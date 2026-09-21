#!/usr/bin/env python3
"""Run a bounded, read-only CPU observe/simulate window.

The workload, if any, is started by the experiment harness outside this
script. This helper only reads proc/cgroup/Docker observation surfaces and
writes a bounded JSON result; it never invokes a mutation-capable command.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

# Allow direct execution from the repository's ``scripts`` directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.guardian_config import load_config, safe_defaults
from src.guardian_cpu import (
    CpuAttributionEvaluator,
    CpuAttributionPolicy,
    CpuRiskEvaluator,
    cpu_policy_from_config,
    collect_cpu_sample,
)
from src.guardian_observer import collect_observation, build_cpu_simulation_decision, resolve_process_cgroup_root


def run_window(
    *,
    samples: int,
    interval: float,
    config_path: Path | None,
    mode: str,
    object_id: str | None = None,
    object_cgroup_path: str | None = None,
    protected: bool = True,
    allowed_actions: list[str] | None = None,
    simulate_action: str = "graceful_stop",
) -> dict[str, Any]:
    config = load_config(config_path) if config_path else safe_defaults()
    policy = cpu_policy_from_config(config)
    risk_evaluator = CpuRiskEvaluator(policy)
    attribution_evaluator = CpuAttributionEvaluator(
        CpuAttributionPolicy(
            min_host_contribution_percent=policy.min_object_contribution_percent,
            min_lead_margin=policy.min_object_lead_margin,
            max_sample_age_seconds=policy.max_sample_age_seconds,
        )
    )
    effective_actions = allowed_actions if allowed_actions is not None else list(config.allowed_actions)
    records: list[dict[str, Any]] = []
    started = time.time()
    for index in range(samples):
        observation = collect_observation()
        cpu = observation["cpu"]
        if object_id and object_cgroup_path:
            cpu = collect_cpu_sample(
                object_registry=[
                    {
                        "kind": "systemd_unit",
                        "id": object_id,
                        "name": object_id,
                        "cgroup_path": object_cgroup_path,
                        "mapping_confidence": "high",
                        "mapping_errors": [],
                    }
                ],
                cgroup_root=resolve_process_cgroup_root(),
            )
        risk = risk_evaluator.evaluate(cpu)
        attribution = attribution_evaluator.evaluate(cpu)
        decision = build_cpu_simulation_decision(
            risk,
            attribution,
            mode=mode,
            simulate_action=simulate_action,
            protected=protected,
            allowed_actions=effective_actions,
        )
        records.append(
            {
                "sample": index + 1,
                "observed_at": observation["observed_at"],
                "cpu": cpu,
                "risk": risk,
                "attribution": attribution,
                "decision": decision,
                "mode": mode,
            }
        )
        if index + 1 < samples:
            time.sleep(interval)
    return {
        "schema": "guardian.cpu_probe.v1",
        "config_digest": config.config_digest,
        "config_source": config.source,
        "mode": mode,
        "samples": samples,
        "interval_seconds": interval,
        "object": {
            "id": object_id,
            "cgroup_path": object_cgroup_path,
            "protected": protected,
        },
        "allowed_actions": effective_actions,
        "started_epoch_s": started,
        "finished_epoch_s": time.time(),
        "records": records,
        "safety": {
            "read_only_observation": True,
            "mutation_commands_invoked": False,
            "production_connected": False,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded read-only Guardian CPU probe")
    parser.add_argument("--samples", type=int, default=10)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--mode", choices=("observe", "simulate"), default="observe")
    parser.add_argument("--object-id")
    parser.add_argument("--object-cgroup-path")
    parser.add_argument("--allow-action", action="append", default=None)
    parser.add_argument("--simulate-action", default="graceful_stop")
    parser.add_argument("--allow-unprotected", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.samples <= 120:
        raise SystemExit("samples must be between 1 and 120")
    if not 0.1 <= args.interval <= 60:
        raise SystemExit("interval must be between 0.1 and 60 seconds")
    result = run_window(
        samples=args.samples,
        interval=args.interval,
        config_path=args.config,
        mode=args.mode,
        object_id=args.object_id,
        object_cgroup_path=args.object_cgroup_path,
        protected=not args.allow_unprotected,
        allowed_actions=args.allow_action,
        simulate_action=args.simulate_action,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "samples": result["samples"],
        "risk_states": [record["risk"]["state"] for record in result["records"]],
        "candidate_states": [record["risk"]["candidate_state"] for record in result["records"]],
        "attribution_states": [record["attribution"]["state"] for record in result["records"]],
        "decision_actions": [record["decision"]["action"] for record in result["records"]],
    }, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
