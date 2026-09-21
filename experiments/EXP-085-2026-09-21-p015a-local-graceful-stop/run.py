#!/usr/bin/env python3
"""Run the one explicitly authorized local-disposable P0-15A action.

This is an experiment runner, not a production command.  It refuses to reuse
an unknown target, requires the exact full Docker ID, protects all pre-existing
containers in the run snapshot, and permits only one graceful_stop through the
unified coordinator.  It never retries an uncertain adapter result.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.guardian_actions import ActionRequest, Authorization, DockerActionAdapter  # noqa: E402
from src.guardian_coordinator import GuardianCoordinator  # noqa: E402
from src.guardian_config import load_config, validate_config  # noqa: E402
from src.guardian_emergency_shedding import (  # noqa: E402
    SUPPORTED_RESOURCES,
    build_emergency_shedding_decision,
    emergency_policy_digest,
    emergency_shedding_policy_from_config,
)
from src.guardian_enforce import probe_container_recovery  # noqa: E402
from src.guardian_notification_outbox import DurableNotificationOutbox  # noqa: E402
from src.guardian_notifications import FakeNotificationSink  # noqa: E402
from src.guardian_recovery import (  # noqa: E402
    BusinessRecoveryObservation,
    BusinessRecoveryPolicy,
    HostRecoveryObservation,
    HostRecoveryPolicy,
    assess_two_layer_recovery,
)
from src.guardian_state import GuardianStateStore  # noqa: E402


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, check=False)


def docker_json(name: str) -> dict[str, Any]:
    result = _run(["docker", "inspect", "--format", "{{json .}}", name])
    if result.returncode != 0:
        raise RuntimeError(f"docker_inspect_failed:{name}:{result.stderr.strip()}")
    value = json.loads(result.stdout)
    if not isinstance(value, dict):
        raise RuntimeError("docker_inspect_not_object")
    return value


def docker_exec(name: str, command: list[str]) -> str:
    result = _run(["docker", "exec", name, *command])
    if result.returncode != 0:
        raise RuntimeError(f"docker_exec_failed:{name}:{result.stderr.strip()}")
    return result.stdout.strip()


def compact_inspect(value: dict[str, Any]) -> dict[str, Any]:
    state = value.get("State") if isinstance(value.get("State"), dict) else {}
    config = value.get("Config") if isinstance(value.get("Config"), dict) else {}
    host_config = value.get("HostConfig") if isinstance(value.get("HostConfig"), dict) else {}
    return {
        "id": value.get("Id"),
        "name": value.get("Name"),
        "created": value.get("Created"),
        "state": {
            key: state.get(key)
            for key in ("Status", "Running", "ExitCode", "StartedAt", "FinishedAt")
        },
        "image": config.get("Image"),
        "labels": config.get("Labels"),
        "host_config": {
            key: host_config.get(key)
            for key in ("NetworkMode", "ReadonlyRootfs", "Memory", "NanoCpus", "Privileged", "PidsLimit")
        },
    }


def assert_target(target: dict[str, Any], expected_id: str, name: str) -> tuple[str, int]:
    state = target.get("State") if isinstance(target.get("State"), dict) else {}
    config = target.get("Config") if isinstance(target.get("Config"), dict) else {}
    host_config = target.get("HostConfig") if isinstance(target.get("HostConfig"), dict) else {}
    labels = config.get("Labels") if isinstance(config.get("Labels"), dict) else {}
    if target.get("Id") != expected_id:
        raise RuntimeError("target_full_id_mismatch")
    if target.get("Name") != f"/{name}":
        raise RuntimeError("target_name_mismatch")
    if config.get("Image") != "ubuntu:24.04":
        raise RuntimeError("target_image_mismatch")
    if state.get("Status") != "running" or state.get("Running") is not True:
        raise RuntimeError("target_not_running")
    if labels.get("guardian.scope") != "local-disposable":
        raise RuntimeError("target_scope_label_missing")
    if labels.get("guardian.purpose") != "p015a-graceful-stop":
        raise RuntimeError("target_purpose_label_missing")
    if labels.get("guardian.protected") != "false":
        raise RuntimeError("target_protection_label_invalid")
    if labels.get("guardian.action") != "graceful_stop":
        raise RuntimeError("target_action_label_invalid")
    if host_config.get("NetworkMode") != "none":
        raise RuntimeError("target_network_not_isolated")
    if host_config.get("ReadonlyRootfs") is not True:
        raise RuntimeError("target_root_not_read_only")
    if host_config.get("Memory") != 64 * 1024 * 1024:
        raise RuntimeError("target_memory_bound_changed")
    if host_config.get("NanoCpus") != 100_000_000:
        raise RuntimeError("target_cpu_bound_changed")
    if host_config.get("Privileged") is True:
        raise RuntimeError("target_privileged")
    cgroup_lines = docker_exec(name, ["sh", "-c", "cat /proc/1/cgroup"])
    if "0::/" not in cgroup_lines.splitlines():
        raise RuntimeError("target_cgroup_namespace_unexpected")
    cgroup_inode = int(docker_exec(name, ["stat", "-c", "%i", "/sys/fs/cgroup"]))
    if cgroup_inode <= 0:
        raise RuntimeError("target_cgroup_inode_invalid")
    return "/sys/fs/cgroup", cgroup_inode


def pre_existing_container_ids(target_name: str) -> list[tuple[str, str]]:
    result = _run(["docker", "ps", "-a", "--format", "{{.Names}}"])
    if result.returncode != 0:
        raise RuntimeError(f"docker_ps_failed:{result.stderr.strip()}")
    protected: list[tuple[str, str]] = []
    for name in (item.strip() for item in result.stdout.splitlines()):
        if not name or name == target_name:
            continue
        inspected = docker_json(name)
        state = inspected.get("State") if isinstance(inspected.get("State"), dict) else {}
        if state.get("Running") is True:
            raise RuntimeError(f"pre_existing_running_container:{name}")
        identifier = inspected.get("Id")
        if not isinstance(identifier, str) or len(identifier) != 64:
            raise RuntimeError(f"pre_existing_identity_invalid:{name}")
        protected.append((identifier, name))
    return protected


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_runtime_config(config_path: Path, target_id: str, protected: list[tuple[str, str]], expires_at: str):
    raw = json.loads((ROOT / "config/guardian.example.json").read_text(encoding="utf-8"))
    raw["agent"]["mode"] = "enforce"
    raw["risk"]["emergency_shedding"] = {
        "schema": "guardian.emergency_shedding.v1",
        "enabled": True,
        "window_seconds": 15,
        "required_samples": 2,
        "min_host_contribution_percent": 20,
        "resource_priority": list(SUPPORTED_RESOURCES),
        "action": "graceful_stop",
        "protected_set": [
            {
                "stable_id": identifier,
                "owner": "workspace-owner",
                "reason": f"pre-existing container {name}; never touch in P0-15A",
            }
            for identifier, name in protected
        ],
        "actionable_set": [
            {
                "stable_id": target_id,
                "owner": "current-user",
                "environment": "local-disposable",
                "allowed_resources": ["memory"],
                "action": "graceful_stop",
                "grace_timeout_seconds": 30,
                "expires_at": expires_at,
                "human_contact": "current-user",
            }
        ],
    }
    raw["actions"] = {
        "enabled": True,
        "require_approval": True,
        "graceful_timeout_seconds": 30,
        "cooldown_seconds": 600,
        "max_actions_per_host_per_hour": 1,
        "allow": ["graceful_stop"],
    }
    raw["protection"]["container_labels"] = ["guardian.protected=true"]
    raw["recovery"] = {
        "max_wait_seconds": 15,
        "poll_interval_seconds": 0.5,
        "require_business_health": True,
    }
    config = validate_config(raw, source=str(config_path))
    write_json(config_path, config.as_dict())
    return config


def verification_provider_factory(authorization: Authorization):
    def verify(intent, action_result):
        request = ActionRequest(
            event_id=intent.event_id,
            target_id=intent.target_id,
            action=intent.action,
            protected=False,
            allowed_actions=frozenset({"graceful_stop"}),
            authorization=authorization,
            timeout_seconds=intent.timeout_seconds,
        )
        observed = probe_container_recovery(
            request,
            subprocess.run,
            max_wait_seconds=15.0,
            poll_interval_seconds=0.5,
        )
        host = HostRecoveryObservation(
            before_available_percent=None,
            after_available_percent=None,
            before_psi_full_avg10=None,
            after_psi_full_avg10=None,
            before_oom_events=None,
            after_oom_events=None,
            after_risk_state="not_observed",
            observed_after_seconds=observed.observed_after_seconds,
        )
        business = BusinessRecoveryObservation(
            target_id=observed.target_id,
            target_present=observed.target_present,
            target_running=observed.target_running,
            health_status=observed.health_status,
            probe_ok=False,
            observed_after_seconds=observed.observed_after_seconds,
        )
        return assess_two_layer_recovery(
            HostRecoveryPolicy(),
            host,
            BusinessRecoveryPolicy(require_probe=True),
            business,
            expected_target_id=intent.target_id,
        )

    return verify


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-name", required=True)
    parser.add_argument("--expected-target-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--confirm-user-authorized-local-disposable", action="store_true")
    args = parser.parse_args()
    if not args.confirm_user_authorized_local_disposable:
        raise SystemExit("explicit local-disposable confirmation is required")
    if len(args.expected_target_id) != 64 or any(char not in "0123456789abcdef" for char in args.expected_target_id):
        raise SystemExit("expected target ID must be a 64-character lowercase Docker ID")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    state_db = args.output_dir / "state.db"
    outbox_db = args.output_dir / "outbox.db"
    if state_db.exists() or outbox_db.exists():
        raise SystemExit("refusing to reuse prior durable state; choose an empty output directory")

    target_before = docker_json(args.target_name)
    cgroup_path, cgroup_inode = assert_target(target_before, args.expected_target_id, args.target_name)
    protected = pre_existing_container_ids(args.target_name)
    now = time.time()
    now_dt = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    action_expiry = (now_dt + dt.timedelta(minutes=10)).isoformat().replace("+00:00", "Z")
    event_id = f"p015a-{int(now)}-{args.expected_target_id[:12]}"
    sample_id = f"p015a-sample-{int(time.monotonic_ns())}"
    host_id = "orbstack-local-p015a"

    config_path = args.output_dir / "config.json"
    config = build_runtime_config(config_path, args.expected_target_id, protected, action_expiry)
    policy = emergency_shedding_policy_from_config(config)
    identity = {
        "kind": "container",
        "id": args.expected_target_id,
        "name": args.target_name,
        "status": "running",
        "running": True,
        "created_at": target_before["Created"],
        "cgroup_path": cgroup_path,
        "cgroup_inode": cgroup_inode,
    }
    candidate = {
        **identity,
        "resource_contributions": {"memory": 100.0},
    }
    shedding = build_emergency_shedding_decision(
        {
            "memory": {
                "state": "CRITICAL_CONFIRMED",
                "danger_confirmed": True,
                "sample_complete": True,
                "sample_count": 2,
                "required_samples": 2,
                "quality_status": "ok",
                "quality_flags": [],
            }
        },
        [candidate],
        registry=[identity],
        protected_set=policy.protected_set,
        actionable_set=policy.actionable_set,
        mode="simulate",
        policy=policy,
        allowed_actions=["graceful_stop"],
        event_id=event_id,
        sample_id=sample_id,
        config_digest=config.config_digest,
        now_epoch_s=now,
    )
    decision = shedding["decision"]
    if decision.get("action") != "graceful_stop" or not isinstance(decision.get("plan"), dict):
        raise RuntimeError(f"controlled_plan_not_generated:{decision}")
    event = {
        "schema": "guardian.risk.event.v1",
        "event_id": event_id,
        "sample_id": sample_id,
        "incident_id": event_id,
        "observed_at": now_dt.isoformat().replace("+00:00", "Z"),
        "observed_monotonic_ns": time.monotonic_ns(),
        "host_id": host_id,
        "state": "critical",
        "signals": {
            "sample_id": sample_id,
            "observed_monotonic_ns": time.monotonic_ns(),
            "source": "controlled_local_action_gate",
            "object_registry": {"objects": [identity]},
        },
        "resource_evaluations": {
            "memory": {
                "risk": {
                    "state": "CRITICAL_CONFIRMED",
                    "danger_confirmed": True,
                    "sample_count": 2,
                    "required_samples": 2,
                    "quality_status": "ok",
                    "quality_flags": [],
                }
            }
        },
        "object_candidates": [candidate],
        "decision": decision,
        "emergency_shedding": shedding,
        "evidence": {
            "config_digest": config.config_digest,
            "policy_digest": emergency_policy_digest(policy),
            "source": "controlled_local_action_gate",
            "host_pressure_effectiveness": "not_tested",
        },
    }
    authorization = Authorization(
        approval_id=f"user-authorized-p015a-{int(now)}",
        environment="local-disposable",
        target_id=args.expected_target_id,
        action="graceful_stop",
        expires_at=now + 120.0,
    )
    write_json(args.output_dir / "event.json", event)
    write_json(
        args.output_dir / "authorization.json",
        {
            "approval_id": authorization.approval_id,
            "environment": authorization.environment,
            "target_id": authorization.target_id,
            "action": authorization.action,
            "expires_at": authorization.expires_at,
        },
    )
    write_json(args.output_dir / "target-before.json", compact_inspect(target_before))

    sink = FakeNotificationSink()
    outbox = DurableNotificationOutbox(outbox_db, sink)
    store = GuardianStateStore(state_db)
    coordinator = GuardianCoordinator(
        store,
        outbox,
        policy=policy,
        adapter=DockerActionAdapter(),
        default_mode="observe",
        plan_ttl_seconds=30.0,
        cooldown_seconds=600.0,
        max_actions=1,
        action_window_seconds=3600.0,
        max_consecutive_failures=2,
    )
    coordinator.register_capability(authorization)
    result = coordinator.process(
        event,
        mode="enforce",
        authorization=authorization,
        current_registry=[identity],
        now_epoch_s=now,
        now_monotonic_ns=time.monotonic_ns(),
        verification_provider=verification_provider_factory(authorization),
    )
    result_dict = result.as_dict()
    write_json(args.output_dir / "coordinator-result.json", result_dict)

    target_after = docker_json(args.target_name)
    write_json(args.output_dir / "target-after.json", compact_inspect(target_after))
    action_result = result.broker.action_result if result.broker is not None else None
    action_executed = bool(action_result is not None and action_result.executed)
    action_returncode = action_result.returncode if action_result is not None else None
    target_stopped = not bool((target_after.get("State") or {}).get("Running"))

    cleanup = {"attempted": False, "removed": False, "returncode": None, "stderr": ""}
    if action_executed and action_returncode == 0 and target_stopped:
        cleanup["attempted"] = True
        remove_result = _run(["docker", "rm", args.target_name])
        cleanup.update(
            {
                "removed": remove_result.returncode == 0,
                "returncode": remove_result.returncode,
                "stderr": remove_result.stderr.strip(),
            }
        )

    intent_record = store.get_intent(result.intent_id) if result.intent_id else None
    summary = {
        "schema": "guardian.p015a.action.v1",
        "status": "PASS_ACTION" if action_executed and action_returncode == 0 and target_stopped else "FAIL",
        "scope": "local-disposable-only",
        "coordinator_state": result.state,
        "reason_codes": list(result.reason_codes),
        "action_executed": action_executed,
        "action_returncode": action_returncode,
        "target_stopped": target_stopped,
        "cleanup": cleanup,
        "real_docker_actions": 1 if action_executed else 0,
        "real_notification_deliveries": 0,
        "external_connections": 0,
        "host_recovery_verified": False,
        "business_recovered": False,
        "manual_handoff": result.manual_handoff,
        "notification_audit": outbox.verify_audit(),
        "intent_record": intent_record,
        "target_id": args.expected_target_id,
        "target_name": args.target_name,
        "config_digest": config.config_digest,
        "policy_digest": emergency_policy_digest(policy),
    }
    write_json(args.output_dir / "result.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "PASS_ACTION" else 1


if __name__ == "__main__":
    raise SystemExit(main())

