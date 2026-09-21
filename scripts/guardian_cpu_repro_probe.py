#!/usr/bin/env python3
"""Run bounded host-side probes for the disposable CPU reproduction.

The SSH probes intentionally use the host's OpenSSH client.  This keeps the
experiment separate from Multipass' management channel and measures the path
that an operator would use to enter the VM.
"""

from __future__ import annotations

import argparse
import json
import math
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 3)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        result = ordered[lower]
    else:
        result = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(result, 3)


def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [record["elapsed_ms"] for record in records if record.get("elapsed_ms") is not None]
    failures = [record for record in records if not record.get("ok", False)]
    return {
        "samples": len(records),
        "successes": len(records) - len(failures),
        "failures": len(failures),
        "failure_rate": round(len(failures) / len(records), 4) if records else None,
        "latency_ms": {
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
            "max": round(max(latencies), 3) if latencies else None,
        },
        "timeouts": sum(1 for record in records if record.get("timed_out")),
    }


def _ssh_base(identity: str, host: str, user: str, *, force_tty: bool = False) -> list[str]:
    return [
        "/usr/bin/ssh",
        "-i",
        identity,
        "-o",
        "BatchMode=yes",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        "ConnectTimeout=2",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "ServerAliveInterval=1",
        "-o",
        "ServerAliveCountMax=1",
        *(["-tt"] if force_tty else []),
        f"{user}@{host}",
    ]


def _run_ssh(label: str, command: list[str], timeout_seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {
        "label": label,
        "ok": False,
        "timed_out": False,
        "returncode": None,
        "elapsed_ms": None,
        "stderr": None,
    }
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        result["returncode"] = completed.returncode
        result["ok"] = completed.returncode == 0
        stderr = completed.stderr.strip()
        if stderr:
            result["stderr"] = stderr[-240:]
    except subprocess.TimeoutExpired as exc:
        result["timed_out"] = True
        stderr = (exc.stderr or "")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        if stderr.strip():
            result["stderr"] = stderr.strip()[-240:]
    except OSError as exc:
        result["stderr"] = str(exc)[-240:]
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
    return result


def _run_ssh_interactive(command: list[str], timeout_seconds: float) -> dict[str, Any]:
    """Exercise a real forced-TTY shell and a short sequence of safe commands."""

    started = time.monotonic()
    result: dict[str, Any] = {
        "label": "ssh_interactive_ops",
        "ok": False,
        "timed_out": False,
        "returncode": None,
        "elapsed_ms": None,
        "stderr": None,
        "stdout_tail": None,
    }
    input_text = (
        "printf 'ready\\n'\n"
        "/usr/bin/uptime\n"
        "/usr/bin/ps -e --no-headers | /usr/bin/wc -l\n"
        "/usr/bin/systemctl is-active guardian-cpu-repro-http.service\n"
        "/bin/ls -la /home/ubuntu/guardian-cpu-repro >/dev/null\n"
        "printf 'done\\n'\n"
        "exit\n"
    )
    try:
        completed = subprocess.run(
            command,
            input=input_text,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        result["returncode"] = completed.returncode
        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        result["stdout_tail"] = stdout[-240:] if stdout else None
        result["stderr"] = stderr[-240:] if stderr else None
        result["ok"] = completed.returncode == 0 and "done" in stdout
    except subprocess.TimeoutExpired as exc:
        result["timed_out"] = True
        stderr = exc.stderr or ""
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        if stderr.strip():
            result["stderr"] = stderr.strip()[-240:]
    except OSError as exc:
        result["stderr"] = str(exc)[-240:]
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
    return result


def _run_http(url: str, timeout_seconds: float) -> dict[str, Any]:
    started = time.monotonic()
    result: dict[str, Any] = {
        "label": "http_external",
        "ok": False,
        "timed_out": False,
        "status": None,
        "elapsed_ms": None,
        "error": None,
    }
    try:
        request = urllib.request.Request(url, headers={"Connection": "close"})
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read(128)
            result["status"] = response.status
            result["ok"] = response.status == 200 and body.strip() == b"ok"
            if not result["ok"]:
                result["error"] = "unexpected_health_response"
    except (TimeoutError, socket.timeout):
        result["timed_out"] = True
        result["error"] = "timeout"
    except urllib.error.HTTPError as exc:
        result["status"] = exc.code
        result["error"] = f"http_{exc.code}"
    except (urllib.error.URLError, OSError) as exc:
        result["error"] = str(getattr(exc, "reason", exc))[-240:]
    result["elapsed_ms"] = round((time.monotonic() - started) * 1000, 3)
    return result


def _probe_once(args: argparse.Namespace, index: int) -> dict[str, Any]:
    target = f"{args.user}@{args.host}"
    ssh_base = _ssh_base(args.identity, args.host, args.user)
    interactive_base = _ssh_base(args.identity, args.host, args.user, force_tty=True)
    records = [
        _run_ssh("ssh_connect", [*ssh_base, "true"], args.ssh_timeout),
        _run_ssh(
            "ssh_health",
            [
                *ssh_base,
                "curl",
                "-fsS",
                "--connect-timeout",
                "1",
                "--max-time",
                "2",
                "http://127.0.0.1:18080/health",
            ],
            args.ssh_timeout,
        ),
        _run_ssh_interactive([*interactive_base, "bash", "--noprofile", "--norc", "-i"], args.ssh_timeout),
        _run_http(f"http://{args.host}:{args.port}/health", args.http_timeout),
    ]
    return {
        "index": index,
        "monotonic_seconds": round(time.monotonic(), 6),
        "target": target,
        "probes": records,
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.duration <= 0 or args.interval <= 0:
        raise ValueError("duration and interval must be positive")
    started = time.time()
    deadline = time.monotonic() + args.duration
    samples: list[dict[str, Any]] = []
    next_sample = time.monotonic()
    index = 0
    while time.monotonic() < deadline or not samples:
        remaining = next_sample - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        if time.monotonic() > deadline and samples:
            break
        samples.append(_probe_once(args, index))
        index += 1
        next_sample += args.interval

    by_probe: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        for probe in sample["probes"]:
            by_probe.setdefault(probe["label"], []).append(probe)
    return {
        "schema": "guardian.cpu_repro_external.v1",
        "label": args.label,
        "started_epoch_seconds": started,
        "duration_seconds": args.duration,
        "interval_seconds": args.interval,
        "target": {
            "host": args.host,
            "user": args.user,
            "http_port": args.port,
        },
        "samples": samples,
        "summary": {label: _summary(records) for label, records in sorted(by_probe.items())},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--user", default="ubuntu")
    parser.add_argument("--identity", required=True, help="ephemeral private SSH key path")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--duration", type=float, default=20.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--ssh-timeout", type=float, default=5.0)
    parser.add_argument("--http-timeout", type=float, default=2.0)
    parser.add_argument("--label", default="probe")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except (OSError, ValueError) as exc:
        print(f"probe failed: {exc}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
