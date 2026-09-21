#!/usr/bin/env python3
"""Collect bounded, read-only CPU evidence inside the reproduction VM."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT))

from src.guardian_cpu import CpuPolicy, CpuRiskEvaluator, collect_cpu_sample  # noqa: E402


def _object_registry(args: argparse.Namespace) -> list[dict[str, Any]]:
    registry: list[dict[str, Any]] = []
    if args.stress_cgroup:
        registry.append(
            {
                "kind": "systemd_unit",
                "id": "guardian-cpu-pressure.service",
                "name": "cpu_pressure",
                "cgroup_path": args.stress_cgroup,
                "mapping_confidence": "high",
            }
        )
    if args.service_cgroup:
        registry.append(
            {
                "kind": "systemd_unit",
                "id": "guardian-cpu-repro-http.service",
                "name": "http_health_service",
                "cgroup_path": args.service_cgroup,
                "mapping_confidence": "high",
            }
        )
    return registry


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.duration <= 0 or args.interval <= 0:
        raise ValueError("duration and interval must be positive")
    evaluator = CpuRiskEvaluator(
        CpuPolicy(
            warning_for_seconds=args.warning_for_seconds,
            critical_for_seconds=args.critical_for_seconds,
            required_samples=2,
        )
    )
    started = time.time()
    deadline = time.monotonic() + args.duration
    next_sample = time.monotonic()
    samples: list[dict[str, Any]] = []
    index = 0
    registry = _object_registry(args)
    while time.monotonic() < deadline or not samples:
        remaining = next_sample - time.monotonic()
        if remaining > 0:
            time.sleep(remaining)
        if time.monotonic() > deadline and samples:
            break
        sample = collect_cpu_sample(object_registry=registry)
        evaluation = evaluator.evaluate(sample)
        samples.append({"index": index, "sample": sample, "evaluation": evaluation})
        index += 1
        next_sample += args.interval
    return {
        "schema": "guardian.cpu_repro_guest.v1",
        "started_epoch_seconds": started,
        "duration_seconds": args.duration,
        "interval_seconds": args.interval,
        "objects": registry,
        "samples": samples,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=70.0)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--warning-for-seconds", type=float, default=30.0)
    parser.add_argument("--critical-for-seconds", type=float, default=10.0)
    parser.add_argument("--stress-cgroup")
    parser.add_argument("--service-cgroup")
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except (OSError, ValueError) as exc:
        print(f"guest monitor failed: {exc}", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"samples={len(result['samples'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
