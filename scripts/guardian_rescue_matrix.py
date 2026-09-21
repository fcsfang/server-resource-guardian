#!/usr/bin/env python3
"""Evaluate one merged, read-only Rescue Plane maintenance matrix.

The evaluator consumes already-recorded evidence.  It never starts pressure,
calls Multipass, changes systemd/Docker state, or deletes files.  A matrix is
only ``PASSED`` when every required pressure domain and every maintenance
probe has complete evidence; missing Docker, capacity/inode, persistence, or
rollback evidence stays visible as a non-passing result.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "guardian.rescue.maintenance-matrix.v1"
REQUIRED_SCENARIOS = ("cpu", "memory", "io", "capacity_inode", "pid")
REQUIRED_PROBES = (
    "ssh_managed_session",
    "diagnostic_read_only",
    "docker_list_read_only",
    "guardian_observe_once",
)
DEFAULT_P95_LIMITS_MS = {
    "ssh_managed_session": 3000.0,
    "diagnostic_read_only": 5000.0,
    "docker_list_read_only": 5000.0,
    "guardian_observe_once": 10000.0,
}
SCENARIO_ALIASES = {
    "cpu-workload-slice": "cpu",
    "memory-workload-slice": "memory",
    "io-workload-slice": "io",
    "tasks-workload-slice": "pid",
}


def _bool(value: Any) -> bool:
    return type(value) is bool


def _positive_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def evaluate_matrix(
    records: Mapping[str, Mapping[str, Mapping[str, Any]]],
    *,
    capacity_inode_evidence: bool,
    persistence_evidence: bool,
    rollback_evidence: bool,
    p95_limits_ms: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Evaluate supplied evidence without inferring missing facts."""

    if not isinstance(records, Mapping):
        raise ValueError("records_mapping_required")
    limits = dict(DEFAULT_P95_LIMITS_MS)
    if p95_limits_ms is not None:
        for probe, limit in p95_limits_ms.items():
            if probe not in REQUIRED_PROBES or not _positive_number(limit):
                raise ValueError("invalid_p95_limit")
            limits[probe] = float(limit)

    reasons: list[str] = []
    scenario_results: dict[str, Any] = {}
    for scenario in REQUIRED_SCENARIOS:
        raw_probes = records.get(scenario)
        if not isinstance(raw_probes, Mapping):
            scenario_results[scenario] = {"status": "missing", "probes": {}}
            reasons.append(f"scenario_missing:{scenario}")
            continue
        probe_results: dict[str, Any] = {}
        scenario_ok = True
        for probe in REQUIRED_PROBES:
            raw = raw_probes.get(probe)
            if not isinstance(raw, Mapping):
                probe_results[probe] = {"status": "missing"}
                reasons.append(f"probe_missing:{scenario}:{probe}")
                scenario_ok = False
                continue
            attempts = raw.get("attempts")
            successes = raw.get("successes")
            p95_ms = raw.get("p95_ms")
            valid_counts = (
                type(attempts) is int
                and type(successes) is int
                and attempts > 0
                and 0 <= successes <= attempts
            )
            valid_latency = _positive_number(p95_ms)
            passed = valid_counts and successes == attempts and valid_latency and p95_ms <= limits[probe]
            status = "passed" if passed else "failed"
            probe_results[probe] = {
                "status": status,
                "attempts": attempts,
                "successes": successes,
                "p95_ms": p95_ms,
                "p95_limit_ms": limits[probe],
            }
            if not passed:
                reasons.append(f"probe_gate_failed:{scenario}:{probe}")
                scenario_ok = False
        scenario_results[scenario] = {
            "status": "passed" if scenario_ok else "failed",
            "probes": probe_results,
        }

    if not _bool(capacity_inode_evidence) or not capacity_inode_evidence:
        reasons.append("capacity_inode_evidence_missing")
    if not _bool(persistence_evidence) or not persistence_evidence:
        reasons.append("persistence_evidence_missing")
    if not _bool(rollback_evidence) or not rollback_evidence:
        reasons.append("rollback_evidence_missing")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return {
        "schema": SCHEMA,
        "status": "PASSED" if not unique_reasons else "INCONCLUSIVE",
        "read_only": True,
        "required_scenarios": list(REQUIRED_SCENARIOS),
        "required_probes": list(REQUIRED_PROBES),
        "scenarios": scenario_results,
        "evidence": {
            "capacity_inode": capacity_inode_evidence,
            "persistence": persistence_evidence,
            "rollback": rollback_evidence,
        },
        "reason_codes": list(unique_reasons),
        "safety": {
            "multipass_invoked": False,
            "systemd_mutation_invoked": False,
            "docker_mutation_invoked": False,
            "production_connected": False,
            "new_threshold_experiment_created": False,
        },
    }


def records_from_summary_csv(path: Path) -> dict[str, dict[str, dict[str, Any]]]:
    records: dict[str, dict[str, dict[str, Any]]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            scenario = SCENARIO_ALIASES.get(row["scenario"])
            if scenario is None:
                continue
            records.setdefault(scenario, {})[row["probe"]] = {
                "attempts": int(row["count"]),
                "successes": int(row["successes"]),
                "p95_ms": float(row["p95_ms"]),
            }
    return records


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a read-only merged Rescue Plane matrix")
    parser.add_argument("--summary-csv", type=Path, required=True)
    parser.add_argument("--capacity-inode", action="store_true")
    parser.add_argument("--persistence", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = evaluate_matrix(
        records_from_summary_csv(args.summary_csv),
        capacity_inode_evidence=args.capacity_inode,
        persistence_evidence=args.persistence,
        rollback_evidence=args.rollback,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "reason_codes": result["reason_codes"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
