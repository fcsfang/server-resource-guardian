#!/usr/bin/env python3
"""Run a bounded local-only Beszel/Guardian memory-alert comparison.

This script is intentionally limited to one disposable in-VM memory worker.
It invokes Guardian's read-only observation path and never calls Docker or
systemd mutation APIs. The worker exits by deadline; the parent does not kill
it.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import sys
import time
from pathlib import Path
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.guardian_observer import RiskEvaluator, build_event, collect_observation


MEMORY_BYTES = 256 * 1024 * 1024
MAX_DURATION_SECONDS = 75.0
SAMPLE_SECONDS = 2.0
WARNING_AVAILABLE_PERCENT = 85.0
CRITICAL_AVAILABLE_PERCENT = 75.0


def memory_worker(deadline: float, memory_bytes: int) -> None:
    """Hold and touch a bounded buffer until the parent-set deadline."""

    buffer = bytearray(memory_bytes)
    for offset in range(0, len(buffer), 4096):
        buffer[offset] = 1
    while time.monotonic() < deadline:
        time.sleep(0.2)


def sample(evaluator: RiskEvaluator, phase: str, started_at: float | None) -> dict[str, object]:
    observation = collect_observation()
    event = build_event(
        observation,
        warning_available=WARNING_AVAILABLE_PERCENT,
        critical_available=CRITICAL_AVAILABLE_PERCENT,
        mode="observe",
        evaluator=evaluator,
    )
    memory = observation.get("memory", {})
    elapsed = None if started_at is None else round(time.monotonic() - started_at, 3)
    try:
        with urlopen("http://127.0.0.1:8090/api/health", timeout=2) as response:
            hub_health: object = {"status": response.status}
    except Exception as exc:
        hub_health = {"error": type(exc).__name__}
    return {
        "phase": phase,
        "elapsed_seconds_from_pressure_start": elapsed,
        "event_id": event["event_id"],
        "observed_at": event["observed_at"],
        "guardian_state": event["state"],
        "guardian_candidate_state": event["risk"]["candidate_state"],
        "guardian_reasons": event["risk"]["reasons"],
        "memory_available_ratio_percent": memory.get("available_ratio_percent"),
        "memory_psi": observation.get("psi", {}).get("memory", {}),
        "hub_health": hub_health,
        "docker_read_only_available": observation.get("docker", {}).get("available"),
        "docker_container_count": len(observation.get("docker", {}).get("containers", [])),
    }


def run(output: Path) -> None:
    started_wall = time.time()
    evaluator = RiskEvaluator(warning_for=0.0, critical_for=0.0)
    records: list[dict[str, object]] = []
    worker: mp.Process | None = None
    pressure_started = None
    stop_reason = "deadline"
    try:
        records.append(sample(evaluator, "before", None))
        pressure_started = time.monotonic()
        deadline = pressure_started + MAX_DURATION_SECONDS
        context = mp.get_context("fork")
        worker = context.Process(target=memory_worker, args=(deadline, MEMORY_BYTES))
        worker.start()
        while time.monotonic() < deadline:
            records.append(sample(evaluator, "during", pressure_started))
            current = records[-1]
            psi_memory = current.get("memory_psi") or {}
            full = psi_memory.get("full", {}) if isinstance(psi_memory, dict) else {}
            full_avg10 = full.get("avg10") if isinstance(full, dict) else None
            if isinstance(full_avg10, (int, float)) and full_avg10 >= 5.0:
                stop_reason = "memory_psi_full_avg10_guardrail"
                break
            hub_health = current.get("hub_health")
            if not isinstance(hub_health, dict) or hub_health.get("status") != 200:
                stop_reason = "beszel_hub_health_failed"
                break
            time.sleep(SAMPLE_SECONDS)
    finally:
        if worker is not None:
            worker.join(timeout=10)
            records.append(sample(evaluator, "after", pressure_started))
    output.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "experiment_id": "EXP-028",
        "scope": "local_multipass_only",
        "started_at_unix": started_wall,
        "memory_bytes": MEMORY_BYTES,
        "max_duration_seconds": MAX_DURATION_SECONDS,
        "sample_interval_seconds": SAMPLE_SECONDS,
        "guardian_thresholds": {
            "warning_available_percent": WARNING_AVAILABLE_PERCENT,
            "critical_available_percent": CRITICAL_AVAILABLE_PERCENT,
            "warning_for_seconds": 0,
            "critical_for_seconds": 0,
        },
        "stop_reason": stop_reason,
        "worker_exit_code": None if worker is None else worker.exitcode,
        "worker_exited_naturally": worker is not None and worker.exitcode == 0,
        "records": records,
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.output)


if __name__ == "__main__":
    main()
