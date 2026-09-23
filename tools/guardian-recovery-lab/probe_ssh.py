#!/usr/bin/env python3
"""Bounded external SSH probe that records rescue-chain failures."""

from __future__ import annotations

import argparse
import base64
import json
import re
import shlex
import subprocess
import time
from pathlib import Path


STAGE_MARKER = re.compile(
    r"__GUARDIAN_STAGE__:([a-zA-Z0-9_.-]+):EXIT:(\d+)(?::OUTPUT_B64:([A-Za-z0-9+/=]*))?__"
)
STAGE_NAME = re.compile(r"^[a-zA-Z0-9_.-]{1,48}$")
REMOTE_OUTPUT_LIMIT = 4096


def build_remote_command(checks: list[tuple[str, str]]) -> str:
    lines = ["overall=0", "set +e"]
    for name, command in [("shell", "true"), *checks]:
        if not STAGE_NAME.fullmatch(name):
            raise ValueError(f"invalid stage name: {name}")
        lines.extend(
            [
                # Capture child output before printing the marker. SSH can
                # otherwise interleave a child process's pipe output with the
                # parent shell's marker, making evidence appear under the
                # wrong stage.
                f"stage_output=$( ( set -o pipefail; {command} ) 2>&1 )",
                "stage_rc=$?",
                f"if [ \"${{#stage_output}}\" -gt {REMOTE_OUTPUT_LIMIT} ]; then stage_output=\"...${{stage_output: -{REMOTE_OUTPUT_LIMIT}}}\"; fi",
                'stage_output_b64=$(printf %s "$stage_output" | base64 -w0)',
                f"printf '__GUARDIAN_STAGE__:{name}:EXIT:%s:OUTPUT_B64:%s__\\n' \"$stage_rc\" \"$stage_output_b64\"",
                'if [ "$stage_rc" -ne 0 ]; then overall=1; fi',
            ]
        )
    lines.append('exit "$overall"')
    return "bash -lc " + shlex.quote("\n".join(lines))


def parse_stage_results(stdout: str) -> list[dict[str, int | str]]:
    return [{"name": name, "returncode": int(code)} for name, code, _ in STAGE_MARKER.findall(stdout)]


def parse_stage_evidence(stdout: str) -> list[dict[str, int | str]]:
    evidence: list[dict[str, int | str]] = []
    for name, code, encoded in STAGE_MARKER.findall(stdout):
        try:
            output = base64.b64decode(encoded or "").decode("utf-8", errors="replace")
        except (ValueError, base64.binascii.Error):
            output = "<invalid stage output encoding>"
        evidence.append({"name": name, "returncode": int(code), "output": output})
    return evidence


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="probe an SSH rescue command")
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--check", action="append", default=[], metavar="NAME=COMMAND")
    parser.add_argument("--command", help="legacy single check; prefer --check for stage-level evidence")
    parser.add_argument("--duration", type=float, default=30.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if not 1.0 <= args.duration <= 3600.0 or not 0.1 <= args.interval <= 60.0 or not 0.5 <= args.timeout <= 60.0:
        parser.error("duration, interval or timeout out of bounds")
    checks: list[tuple[str, str]] = []
    for value in args.check:
        name, separator, command_text = value.partition("=")
        if not separator or not command_text.strip():
            parser.error("--check must use NAME=COMMAND")
        if not STAGE_NAME.fullmatch(name):
            parser.error(f"invalid --check stage name: {name}")
        checks.append((name, command_text))
    if args.command:
        checks.append(("command", args.command))
    if len({name for name, _ in checks}) != len(checks) or any(name == "shell" for name, _ in checks):
        parser.error("check names must be unique; shell is reserved")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    end_at = time.monotonic() + args.duration
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        f"ConnectTimeout={max(1, int(args.timeout))}",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "ServerAliveInterval=1",
        "-o",
        "ServerAliveCountMax=1",
        "-i",
        str(args.identity),
        f"{args.user}@{args.host}",
        build_remote_command(checks),
    ]
    expected_stages = ["shell", *(name for name, _ in checks)]
    with args.output.open("w", encoding="utf-8") as stream:
        while time.monotonic() < end_at:
            started = time.monotonic()
            status = "ok"
            returncode: int | None = None
            error = ""
            stdout = ""
            stages: list[dict[str, int | str]] = []
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout, check=False)
                returncode = result.returncode
                stdout = result.stdout[-4096:]
                stages = parse_stage_evidence(result.stdout)
                if result.returncode != 0:
                    status = "failed"
                    error = (result.stderr or result.stdout or "ssh_command_failed").strip()[:512]
                if [stage["name"] for stage in stages] != expected_stages:
                    status = "failed"
                    error = "stage_evidence_missing"
                elif any(stage["returncode"] != 0 for stage in stages):
                    status = "failed"
                    error = "one_or_more_stages_failed"
            except subprocess.TimeoutExpired as exc:
                status = "timeout"
                error = str(exc)[:512]
            elapsed_ms = round((time.monotonic() - started) * 1000, 3)
            stream.write(json.dumps({"status": status, "returncode": returncode, "elapsed_ms": elapsed_ms, "checks": stages, "stdout": stdout, "error": error}) + "\n")
            stream.flush()
            time.sleep(max(0.0, args.interval - (time.monotonic() - started)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
