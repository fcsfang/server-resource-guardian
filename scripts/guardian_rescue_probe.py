#!/usr/bin/env python3
"""Plan or run bounded, read-only Rescue Plane probes through Multipass.

The default CLI mode is a dry-run plan. ``--run-read-only`` is the only mode
that invokes Multipass, and its guest commands are fixed read-only probes plus
the requested temporary Guardian audit write. This helper never invokes
Docker/systemd mutation commands and writes only the requested local JSON.
"""

from __future__ import annotations

import argparse
import json
import math
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any


READ_ONLY_PROBES: tuple[tuple[str, str], ...] = (
    ("ssh_managed_session", "true"),
    (
        "diagnostic_read_only",
        "uptime; free -m; cat /proc/pressure/memory; cat /proc/pressure/io",
    ),
    ("docker_list_read_only", "docker ps --format '{{.ID}}\\t{{.Names}}\\t{{.Status}}'"),
    (
        "guardian_observe_once",
        "mkdir -p \"$(dirname -- {audit})\" && cd {repo} && "
        "python3 -m src.guardian_observer --once "
        "--config config/guardian.example.json "
        "--audit-file {audit}",
    ),
)


def percentile(values: list[float], percentage: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil((percentage / 100) * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def run_probe(
    vm: str,
    name: str,
    command: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        result = subprocess.run(
            ["multipass", "exec", vm, "--", "sh", "-lc", command],
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        return {
            "name": name,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            "returncode": result.returncode,
            "ok": result.returncode == 0,
            "stdout_bytes": len(result.stdout.encode("utf-8")),
            "stderr": result.stderr.strip()[:300],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": name,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            "returncode": None,
            "ok": False,
            "stdout_bytes": len((exc.stdout or "").encode("utf-8"))
            if isinstance(exc.stdout, str)
            else 0,
            "stderr": "probe_timeout",
        }
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "name": name,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            "returncode": None,
            "ok": False,
            "stdout_bytes": 0,
            "stderr": f"probe_error:{exc}",
        }


def summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in sorted({str(record["name"]) for record in records}):
        selected = [record for record in records if record["name"] == name]
        elapsed = [float(record["elapsed_ms"]) for record in selected]
        result[name] = {
            "count": len(selected),
            "successes": sum(1 for record in selected if record["ok"]),
            "failures": sum(1 for record in selected if not record["ok"]),
            "p50_ms": percentile(elapsed, 50),
            "p95_ms": percentile(elapsed, 95),
            "max_ms": max(elapsed) if elapsed else None,
        }
    return result


def run_experiment(
    *,
    vm: str,
    repo: str,
    audit: str,
    count: int,
    timeout_seconds: float,
    label: str,
) -> dict[str, Any]:
    if count <= 0 or timeout_seconds <= 0:
        raise ValueError("count_and_timeout_must_be_positive")
    records: list[dict[str, Any]] = []
    started = time.time()
    for round_number in range(1, count + 1):
        for name, template in READ_ONLY_PROBES:
            command = template.format(repo=shlex.quote(repo), audit=shlex.quote(audit))
            record = run_probe(vm, name, command, timeout_seconds=timeout_seconds)
            record["round"] = round_number
            record["label"] = label
            records.append(record)
    return {
        "schema": "guardian.rescue_probe.v1",
        "vm": vm,
        "label": label,
        "count_per_probe": count,
        "timeout_seconds": timeout_seconds,
        "started_epoch_s": started,
        "finished_epoch_s": time.time(),
        "records": records,
        "summary": summarize(records),
        "safety": {
            "read_only_control_probes": True,
            "temporary_audit_write_only": True,
            "docker_mutation_invoked": False,
            "systemd_mutation_invoked": False,
            "production_connected": False,
        },
    }


def dry_run_plan(*, vm: str, repo: str, audit: str, count: int, timeout: float, label: str) -> dict[str, Any]:
    return {
        "schema": "guardian.rescue_probe.plan.v1",
        "mode": "dry-run",
        "vm": vm,
        "repo": repo,
        "audit": audit,
        "count_per_probe": count,
        "timeout_seconds": timeout,
        "label": label,
        "probes": [name for name, _ in READ_ONLY_PROBES],
        "changes_applied": False,
        "multipass_invoked": False,
        "docker_mutation_invoked": False,
        "systemd_mutation_invoked": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan or run bounded read-only Rescue Plane probes")
    parser.add_argument("--vm", default="guardian-ubuntu")
    parser.add_argument("--repo", default="/home/ubuntu/server-resource-guardian")
    parser.add_argument("--audit", default="/tmp/guardian-rescue-probe/audit.jsonl")
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--label", default="baseline")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--run-read-only",
        action="store_true",
        help="explicitly run the fixed read-only Multipass probes; never performs mutations",
    )
    args = parser.parse_args()
    if args.count <= 0 or args.timeout <= 0:
        parser.error("--count and --timeout must be positive")
    result = (
        run_experiment(
            vm=args.vm,
            repo=args.repo,
            audit=args.audit,
            count=args.count,
            timeout_seconds=args.timeout,
            label=args.label,
        )
        if args.run_read_only
        else dry_run_plan(
            vm=args.vm,
            repo=args.repo,
            audit=args.audit,
            count=args.count,
            timeout=args.timeout,
            label=args.label,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result.get("summary", result), ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
