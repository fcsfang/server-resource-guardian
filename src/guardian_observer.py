#!/usr/bin/env python3
"""Read-only Guardian observe prototype.

The module deliberately has no mutation-capable code. It reads host metrics,
cgroup v2 counters and docker stats, then emits JSONL events for later policy
and action layers.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from .guardian_attribution import ObjectAttributionEvaluator, collect_object_registry
from .guardian_config import ConfigError, GuardianConfig, load_config, safe_defaults
from .guardian_cpu import (
    CpuAttributionEvaluator,
    CpuAttributionPolicy,
    CpuRiskEvaluator,
    collect_cpu_sample,
    cpu_policy_from_config,
)
from .guardian_disk import (
    CapacityAttributionEvaluator,
    DiskCapacityRiskEvaluator,
    DiskIoAttributionEvaluator,
    IoRiskEvaluator,
    build_disk_simulation_decision,
    collect_disk_sample,
    disk_capacity_policy_from_config,
    disk_mount_points_from_config,
    io_policy_from_config,
)
from .guardian_emergency_shedding import (
    SUPPORTED_RESOURCES,
    build_emergency_shedding_decision,
    emergency_policy_digest,
    emergency_shedding_policy_from_config,
)
from .guardian_multirisk import build_joint_decision
from .guardian_risk import CompositeRiskEvaluator
from .guardian_runtime import notify_ready, notify_watchdog, systemd_notification_status


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]

MIN_INTERVAL_SECONDS = 0.1
MAX_INTERVAL_SECONDS = 60.0


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def validate_interval_seconds(value: float) -> float:
    """Reject CLI interval overrides that could break bounded sampling."""

    if isinstance(value, bool):
        raise ValueError("interval_seconds:finite_number_required")
    try:
        interval = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("interval_seconds:finite_number_required") from exc
    if not math.isfinite(interval) or not MIN_INTERVAL_SECONDS <= interval <= MAX_INTERVAL_SECONDS:
        raise ValueError("interval_seconds:must_be_between_0.1_and_60")
    return interval


def parse_meminfo(text: str) -> dict[str, int]:
    """Parse /proc/meminfo into byte values where a unit is provided."""

    result: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        fields = raw.split()
        if not fields:
            continue
        try:
            value = int(fields[0])
        except ValueError:
            continue
        if len(fields) > 1 and fields[1].lower() == "kb":
            value *= 1024
        result[key] = value
    return result


def parse_psi(text: str) -> dict[str, dict[str, float]]:
    """Parse one Linux pressure stall information file."""

    result: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        values: dict[str, float] = {}
        for field in fields[1:]:
            if "=" not in field:
                continue
            key, raw = field.split("=", 1)
            try:
                values[key] = float(raw)
            except ValueError:
                continue
        result[fields[0]] = values
    return result


def parse_counter_file(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            result[fields[0]] = int(fields[1])
        except ValueError:
            continue
    return result


def memory_signals(meminfo: dict[str, int]) -> dict[str, float | int | None]:
    total = meminfo.get("MemTotal")
    available = meminfo.get("MemAvailable")
    swap_total = meminfo.get("SwapTotal")
    swap_free = meminfo.get("SwapFree")
    available_ratio = None
    swap_used_ratio = None
    if total and available is not None:
        available_ratio = round(available / total * 100, 3)
    if swap_total:
        swap_used_ratio = round((swap_total - (swap_free or 0)) / swap_total * 100, 3)
    return {
        "total_bytes": total,
        "available_bytes": available,
        "available_ratio_percent": available_ratio,
        "swap_total_bytes": swap_total,
        "swap_free_bytes": swap_free,
        "swap_used_ratio_percent": swap_used_ratio,
    }


def read_optional(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return None


def resolve_process_cgroup_root(
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> Path:
    """Resolve the current process cgroup directory on a cgroup v2 host.

    Callers may still pass a fixture directory directly; when the fixture has
    no ``/proc/self/cgroup`` file, the supplied root is preserved.
    """

    content = read_optional(proc_root / "self" / "cgroup")
    if content is None:
        return cgroup_root
    for line in content.splitlines():
        hierarchy, _, relative = line.partition("::")
        if hierarchy == "0" and relative:
            candidate = cgroup_root / relative.lstrip("/")
            if candidate.is_dir():
                return candidate
    return cgroup_root


def collect_docker_stats(runner: CommandRunner = subprocess.run) -> dict[str, Any]:
    """Read docker stats without invoking any mutation-capable command."""

    command = ["docker", "stats", "--no-stream", "--format", "{{json .}}"]
    try:
        result = runner(command, capture_output=True, text=True, timeout=3, check=False)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "error": str(exc), "containers": []}
    if result.returncode != 0:
        return {
            "available": False,
            "error": (result.stderr or "docker stats failed").strip(),
            "containers": [],
        }
    containers: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        try:
            containers.append(json.loads(line))
        except json.JSONDecodeError:
            containers.append({"raw": line})
    return {"available": True, "containers": containers}


def collect_observation(
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
    runner: CommandRunner = subprocess.run,
    disk_mount_points: Iterable[str] = ("/",),
    container_collector: Callable[[], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    observed_monotonic_ns = time.monotonic_ns()
    quality_flags: list[str] = []
    meminfo_text = read_optional(proc_root / "meminfo")
    if meminfo_text is None:
        quality_flags.append("meminfo_missing")
    psi: dict[str, Any] = {}
    for resource in ("cpu", "memory", "io"):
        content = read_optional(proc_root / "pressure" / resource)
        if content is not None:
            psi[resource] = parse_psi(content)
    if "memory" not in psi:
        quality_flags.append("memory_psi_missing")

    process_cgroup_root = resolve_process_cgroup_root(proc_root, cgroup_root)
    events_text = read_optional(process_cgroup_root / "memory.events")
    if events_text is None:
        quality_flags.append("memory_events_missing")
    events = parse_counter_file(events_text or "")
    meminfo = parse_meminfo(meminfo_text or "")
    memory = memory_signals(meminfo)
    if memory.get("total_bytes") is None:
        quality_flags.append("memory_total_missing")
    if memory.get("available_bytes") is None:
        quality_flags.append("memory_available_missing")
    if container_collector is None:
        docker = collect_docker_stats(runner)
        object_registry = collect_object_registry(
            docker,
            proc_root=proc_root,
            cgroup_root=cgroup_root,
            runner=runner,
        )
    else:
        try:
            collected = container_collector()
        except Exception as exc:  # Collector failure must degrade, never guess.
            collected = {
                "docker": {"available": False, "error": f"collector_unavailable:{type(exc).__name__}", "containers": []},
                "object_registry": {"status": "unavailable", "objects": [], "errors": ["collector_unavailable"]},
            }
        docker = collected.get("docker") if isinstance(collected.get("docker"), Mapping) else {
            "available": False,
            "error": "collector_docker_data_missing",
            "containers": [],
        }
        object_registry = collected.get("object_registry") if isinstance(collected.get("object_registry"), Mapping) else {
            "status": "unavailable",
            "objects": [],
            "errors": ["collector_object_registry_missing"],
        }
        collector_meta = collected.get("collector") if isinstance(collected.get("collector"), Mapping) else {}
        if collector_meta.get("read_only") is not True:
            quality_flags.append("collector_read_only_contract_missing")
        if docker.get("available") is False or object_registry.get("status") == "unavailable":
            quality_flags.append("docker_observation_unavailable")
        if collector_meta.get("status") == "degraded" or object_registry.get("status") == "degraded":
            quality_flags.append("container_collector_degraded")
    if docker.get("available") is False:
        quality_flags.append("docker_observation_unavailable")
    cpu = collect_cpu_sample(
        proc_root=proc_root,
        cgroup_root=process_cgroup_root,
        object_registry=object_registry.get("objects", []),
    )
    disk = collect_disk_sample(
        proc_root=proc_root,
        cgroup_root=process_cgroup_root,
        mount_points=disk_mount_points,
        object_registry=object_registry.get("objects", []),
    )
    observation = {
        "observed_at": utc_now(),
        "observed_monotonic_ns": observed_monotonic_ns,
        "memory": memory,
        "psi": psi,
        "cgroup": {
            "memory_current_bytes": _read_int(process_cgroup_root / "memory.current"),
            "memory_max": _read_scalar(process_cgroup_root / "memory.max"),
            "memory_events": events,
            "pids_current": _read_int(process_cgroup_root / "pids.current"),
            "pids_max": _read_scalar(process_cgroup_root / "pids.max"),
            "path": str(process_cgroup_root),
        },
        "docker": docker,
        "object_registry": object_registry,
        "collector": collector_meta if container_collector is not None else {"status": "direct"},
        "cpu": cpu,
        "disk": disk,
        "quality": {
            "status": "ok" if not quality_flags else "degraded",
            "flags": sorted(set(quality_flags)),
        },
    }
    return observation


def _read_int(path: Path) -> int | None:
    raw = read_optional(path)
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _read_scalar(path: Path) -> str | int | None:
    raw = read_optional(path)
    if raw is None:
        return None
    value = raw.strip()
    try:
        return int(value)
    except ValueError:
        return value


def _candidate_state(observation: dict[str, Any], warning_available: float, critical_available: float) -> tuple[str, list[str]]:
    reasons: list[str] = []
    memory = observation["memory"]
    available = memory.get("available_ratio_percent")
    events = observation["cgroup"].get("memory_events", {})
    if isinstance(events, dict) and (events.get("oom") or events.get("oom_kill")):
        reasons.append("cgroup_memory_oom_event")
    if isinstance(available, (int, float)):
        if available <= critical_available:
            reasons.append("host_memory_available_critical")
        elif available <= warning_available:
            reasons.append("host_memory_available_warning")
    if "cgroup_memory_oom_event" in reasons or "host_memory_available_critical" in reasons:
        return "critical", reasons
    if reasons:
        return "warning", reasons
    return "normal", reasons


class RiskEvaluator:
    """Stateful, read-only risk evaluator with de-bounce windows."""

    def __init__(self, warning_for: float = 180.0, critical_for: float = 30.0) -> None:
        self.warning_for = warning_for
        self.critical_for = critical_for
        self._candidate_level = "normal"
        self._candidate_since: float | None = None
        self._last_emitted = "normal"
        self._previous_available: int | None = None
        self._previous_at: float | None = None

    def evaluate(
        self,
        observation: dict[str, Any],
        warning_available: float,
        critical_available: float,
        now: float | None = None,
    ) -> dict[str, Any]:
        timestamp = time.monotonic() if now is None else now
        candidate, reasons = _candidate_state(observation, warning_available, critical_available)
        memory = observation.get("memory", {})
        available = memory.get("available_bytes")
        growth_rate = None
        if (
            isinstance(available, int)
            and self._previous_available is not None
            and self._previous_at is not None
            and timestamp > self._previous_at
        ):
            growth_rate = max((self._previous_available - available) / (timestamp - self._previous_at), 0.0)
        self._previous_available = available if isinstance(available, int) else None
        self._previous_at = timestamp

        if candidate != self._candidate_level:
            self._candidate_level = candidate
            self._candidate_since = timestamp
        if self._candidate_since is None:
            self._candidate_since = timestamp
        candidate_for = max(timestamp - self._candidate_since, 0.0)

        if candidate == "normal":
            state = "recovered" if self._last_emitted in {"warning", "critical"} else "normal"
            required_for = 0.0
        else:
            required_for = self.critical_for if candidate == "critical" else self.warning_for
            state = candidate if candidate_for >= required_for else "normal"

        if state == "recovered":
            self._last_emitted = "normal"
        elif state != "normal":
            self._last_emitted = state

        return {
            "state": state,
            "candidate_state": candidate,
            "candidate_for_seconds": round(candidate_for, 3),
            "required_for_seconds": required_for,
            "reasons": reasons,
            "memory_available_growth_bytes_per_second": growth_rate,
        }


def build_event(
    observation: dict[str, Any],
    warning_available: float = 15.0,
    critical_available: float = 10.0,
    mode: str = "observe",
    evaluator: Any = None,
    simulate_action: str = "graceful_stop",
    protected: bool = True,
    allowed_actions: Iterable[str] = (),
    object_attribution: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if evaluator is None:
        candidate, reasons = _candidate_state(observation, warning_available, critical_available)
        risk = {
            "state": candidate,
            "candidate_state": candidate,
            "candidate_for_seconds": None,
            "required_for_seconds": None,
            "reasons": reasons,
            "memory_available_growth_bytes_per_second": None,
        }
    else:
        risk = evaluator.evaluate(observation, warning_available, critical_available)
        candidate = risk["state"]
        reasons = risk["reasons"]
    candidates = []
    attributed_candidates = object_attribution.get("candidates") if isinstance(object_attribution, Mapping) else None
    if isinstance(attributed_candidates, list):
        candidates = [
            {
                "kind": item.get("kind", "container"),
                "id": item.get("id"),
                "name": item.get("name"),
                "confidence": item.get("confidence", "low"),
                "cgroup_path": item.get("cgroup_path"),
                "score": item.get("score"),
                "host_contribution_percent": item.get("host_contribution_percent"),
                "mapping_errors": item.get("mapping_errors", []),
            }
            for item in attributed_candidates
            if isinstance(item, Mapping)
        ]
    else:
        containers = observation["docker"].get("containers", [])
        for container in containers:
            if not isinstance(container, dict):
                continue
            candidates.append({
                "kind": "container",
                "id": container.get("ID") or container.get("Container"),
                "name": container.get("Name"),
                "raw": container,
                "confidence": "observed" if container.get("ID") or container.get("Container") else "low",
            })
    decision: dict[str, Any] = {
        "mode": mode,
        "action": "none",
        "reason_codes": reasons,
        "protected": protected,
        "execution": (
            "not_applicable"
            if mode == "observe"
            else "not_executed"
            if mode == "simulate"
            else "pending_controller"
        ),
    }
    if mode in {"simulate", "enforce"}:
        allowed = set(allowed_actions)
        stable_candidates = [
            candidate
            for candidate in candidates
            if isinstance(candidate.get("id"), str) and candidate["id"]
        ]
        if candidate not in {"warning", "critical"}:
            decision["reason_codes"].append("risk_not_actionable")
        elif not stable_candidates:
            decision["action"] = "escalate"
            decision["reason_codes"].append("no_stable_object_identity")
        elif len(stable_candidates) != 1:
            decision["action"] = "escalate"
            decision["reason_codes"].append("ambiguous_object_identity")
        elif protected:
            decision["action"] = "escalate"
            decision["reason_codes"].append("protected_object")
        elif simulate_action not in allowed:
            decision["action"] = "escalate"
            decision["reason_codes"].append("action_not_allowlisted")
        else:
            decision["action"] = simulate_action
            decision["reason_codes"].append(
                "simulate_only" if mode == "simulate" else "enforce_requires_controller"
            )
        if (
            isinstance(object_attribution, Mapping)
            and object_attribution.get("state") != "TARGET_CONFIRMED"
            and candidate in {"warning", "critical"}
        ):
            decision["action"] = "escalate"
            decision["reason_codes"].append("object_attribution_not_confirmed")

    event_id = str(uuid.uuid4())
    observed_monotonic_ns = observation.get("observed_monotonic_ns")
    if isinstance(observed_monotonic_ns, bool) or not isinstance(observed_monotonic_ns, int):
        observed_monotonic_ns = None
    supplied_sample_id = observation.get("sample_id")
    if not isinstance(supplied_sample_id, str) or not supplied_sample_id:
        supplied_sample_id = (
            f"sample-{observed_monotonic_ns}"
            if observed_monotonic_ns is not None
            else f"sample-{uuid.uuid4()}"
        )
    return {
        "schema": "guardian.risk.event.v1",
        "event_id": event_id,
        "sample_id": supplied_sample_id,
        "observed_at": observation["observed_at"],
        "observed_monotonic_ns": observed_monotonic_ns,
        "state": candidate,
        "host_id": os.uname().nodename,
        "signals": observation,
        "risk": risk,
        "object_candidates": candidates,
        "object_attribution": object_attribution,
        "decision": decision,
        "evidence": {"snapshot_path": None, "sample_window": None},
    }


def build_cpu_simulation_decision(
    risk: Mapping[str, Any],
    attribution: Mapping[str, Any],
    *,
    mode: str,
    simulate_action: str,
    protected: bool,
    allowed_actions: Iterable[str],
) -> dict[str, Any]:
    """Create a CPU-only plan; this helper never executes an action."""

    decision: dict[str, Any] = {
        "mode": mode,
        "resource_kind": "cpu",
        "action": "none",
        "execution": "not_applicable" if mode == "observe" else "not_executed",
        "reason_codes": [],
    }
    risk_state = risk.get("state")
    if risk_state == "degraded_observability":
        decision["reason_codes"].append("cpu_observability_degraded")
        if mode != "observe":
            decision["action"] = "escalate"
        return decision
    if risk_state not in {"warning", "critical"}:
        decision["reason_codes"].append("cpu_risk_not_actionable")
        return decision
    if mode == "observe":
        decision["reason_codes"].append("observe_only")
        return decision
    if attribution.get("state") != "TARGET_CONFIRMED":
        decision["action"] = "escalate"
        decision["reason_codes"].append("cpu_attribution_not_confirmed")
    elif protected:
        decision["action"] = "escalate"
        decision["reason_codes"].append("protected_object")
    elif simulate_action not in set(allowed_actions):
        decision["action"] = "escalate"
        decision["reason_codes"].append("action_not_allowlisted")
    else:
        decision["action"] = simulate_action
        decision["reason_codes"].append("cpu_simulate_only")
    return decision


def _emergency_host_risks(
    resource_evaluations: Mapping[str, Mapping[str, Any]],
    *,
    required_samples: int,
) -> dict[str, dict[str, Any]]:
    """Translate existing resource evaluators into the v1 danger gate.

    The older evaluators expose a dwell-complete ``critical`` state.  The
    emergency policy uses the more explicit ``CRITICAL_CONFIRMED`` marker so
    a single sample or a short peak cannot accidentally become a plan.
    """

    result: dict[str, dict[str, Any]] = {}
    for resource_kind, evaluation in resource_evaluations.items():
        risk = evaluation.get("risk") if isinstance(evaluation.get("risk"), Mapping) else {}
        normalized = dict(risk)
        sample_count = normalized.get("sample_count")
        quality_status = normalized.get("quality_status", "ok")
        quality_flags = normalized.get("quality_flags", [])
        sample_complete = (
            isinstance(sample_count, int)
            and not isinstance(sample_count, bool)
            and sample_count >= required_samples
            and quality_status == "ok"
            and not quality_flags
        )
        state = str(normalized.get("state") or "normal").lower()
        candidate_for = normalized.get("candidate_for_seconds")
        required_for = normalized.get("required_for_seconds")
        danger_confirmed = (
            state in {"critical", "critical_confirmed"}
            and sample_complete
            and isinstance(candidate_for, (int, float))
            and isinstance(required_for, (int, float))
            and candidate_for >= required_for
        )
        normalized["state"] = "CRITICAL_CONFIRMED" if danger_confirmed else normalized.get("state", "normal")
        normalized["danger_confirmed"] = danger_confirmed
        normalized["sample_complete"] = sample_complete
        result[resource_kind] = normalized
    return result


def _emergency_candidates(
    observation: Mapping[str, Any],
    resource_evaluations: Mapping[str, Mapping[str, Any]],
    *,
    window_seconds: float,
) -> list[dict[str, Any]]:
    """Join registry identity with per-resource short-window contributions."""

    registry = observation.get("object_registry") if isinstance(observation.get("object_registry"), Mapping) else {}
    objects = registry.get("objects") if isinstance(registry.get("objects"), list) else []
    candidates: dict[str, dict[str, Any]] = {}
    for item in objects:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
            continue
        candidate = dict(item)
        candidate["running"] = str(item.get("status") or "unknown").lower() in {"running", "restarting"}
        candidate["resource_contributions"] = {}
        candidate["contribution_window_seconds"] = window_seconds
        candidates[item["id"]] = candidate

    contribution_keys = {
        "memory": ("host_contribution_percent", "memory_growth_share_percent"),
        "cpu": ("cpu_contribution_percent",),
        "io": ("io_contribution_percent",),
        "disk_capacity": ("capacity_contribution_percent",),
    }
    identity_change_reasons = {
        "container_recreated",
        "container_cgroup_path_changed",
        "cpu_cgroup_path_changed",
        "io_cgroup_path_changed",
        "target_identity_changed",
    }
    for resource_kind in SUPPORTED_RESOURCES:
        evaluation = resource_evaluations.get(resource_kind, {})
        attribution = evaluation.get("attribution") if isinstance(evaluation.get("attribution"), Mapping) else {}
        attribution_reasons = {str(value) for value in attribution.get("reason_codes", []) if value}
        for raw in attribution.get("candidates", []):
            if not isinstance(raw, Mapping) or not isinstance(raw.get("id"), str):
                continue
            candidate = candidates.get(raw["id"])
            if candidate is None:
                continue
            contribution = None
            for key in contribution_keys[resource_kind]:
                value = raw.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    contribution = float(value)
                    break
            if contribution is not None:
                candidate["resource_contributions"][resource_kind] = contribution
            if attribution_reasons & identity_change_reasons or any(
                reason in identity_change_reasons for reason in raw.get("mapping_errors", [])
            ):
                candidate["identity_changed"] = True
            if resource_kind == "disk_capacity" and raw.get("writer_evidence") is True:
                candidate["writer_evidence"] = True
    return list(candidates.values())


def _container_label_matches(labels: Any, rules: Iterable[str]) -> list[str]:
    """Return configured container-label protection rules that match labels."""

    if not isinstance(labels, Mapping):
        return []
    matches: list[str] = []
    for rule in rules:
        if not isinstance(rule, str) or not rule:
            continue
        if rule in labels:
            matches.append(rule)
            continue
        if "=" in rule:
            key, value = rule.split("=", 1)
            if labels.get(key) == value:
                matches.append(rule)
    return matches


def _build_runtime_presentation(
    event: Mapping[str, Any],
    observation: Mapping[str, Any],
    emergency: Mapping[str, Any],
    config: GuardianConfig,
) -> dict[str, Any]:
    """Project existing runtime evidence into a bounded administrator view.

    This is deliberately a presentation-only projection. It does not select
    a new target, authorize an action, or expose raw Docker command data.
    """

    registry = observation.get("object_registry")
    registry_objects = registry.get("objects", []) if isinstance(registry, Mapping) else []
    registry_by_id = {
        str(item.get("id")): item
        for item in registry_objects
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    config_value = config.as_dict()
    protection_config = config_value.get("protection", {})
    protection_config = protection_config if isinstance(protection_config, Mapping) else {}
    policy = config.emergency_shedding_policy
    protected_entries = policy.get("protected_set", []) if isinstance(policy, Mapping) else []
    actionable_entries = policy.get("actionable_set", []) if isinstance(policy, Mapping) else []
    protected_by_id = {
        item.get("stable_id"): item
        for item in protected_entries
        if isinstance(item, Mapping) and isinstance(item.get("stable_id"), str)
    }
    actionable_ids = {
        item.get("stable_id")
        for item in actionable_entries
        if isinstance(item, Mapping) and isinstance(item.get("stable_id"), str)
    }
    emergency_rankings = emergency.get("rankings") if isinstance(emergency, Mapping) else {}
    selected_resource = None
    emergency_decision = emergency.get("decision") if isinstance(emergency, Mapping) else {}
    if isinstance(emergency_decision, Mapping):
        selected_resource = emergency_decision.get("resource_kind")
    ranked_rows = emergency_rankings.get(selected_resource, []) if isinstance(emergency_rankings, Mapping) else []
    ranked_by_id = {
        row.get("id"): row
        for row in ranked_rows
        if isinstance(row, Mapping) and isinstance(row.get("id"), str)
    }

    candidates = event.get("object_candidates")
    if not isinstance(candidates, list):
        candidates = []
    candidate_ids = {
        item.get("id")
        for item in candidates
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    for item in registry_objects:
        if isinstance(item, Mapping) and isinstance(item.get("id"), str) and item["id"] not in candidate_ids:
            candidates.append({"id": item["id"], "name": item.get("name"), "confidence": "observed"})

    rows: list[dict[str, Any]] = []
    for item in candidates:
        if not isinstance(item, Mapping) or not isinstance(item.get("id"), str):
            continue
        identifier = item["id"]
        registry_item = registry_by_id.get(identifier, {})
        ranked = ranked_by_id.get(identifier, {})
        protected_entry = protected_by_id.get(identifier)
        label_matches = _container_label_matches(
            registry_item.get("labels"),
            protection_config.get("container_labels", []),
        )
        reasons: list[str] = []
        if protected_entry:
            reason = protected_entry.get("reason")
            reasons.append(f"emergency_protected:{reason}" if reason else "emergency_protected")
        reasons.extend(f"container_label:{rule}" for rule in label_matches)
        if item.get("mapping_errors"):
            reasons.extend(str(error) for error in item.get("mapping_errors", []) if error)
        contribution = ranked.get("contribution_percent")
        if contribution is None:
            contribution = item.get("host_contribution_percent")
        score = contribution if selected_resource and contribution is not None else item.get("score")
        if score is None:
            score = contribution
        rows.append(
            {
                "name": item.get("name") or registry_item.get("name") or identifier[:12],
                "score": score,
                "confidence": item.get("confidence") or registry_item.get("mapping_confidence"),
                "resource_contribution_percent": contribution,
                "protected": bool(protected_entry or label_matches),
                "protection_reasons": reasons,
                "actionable": identifier in actionable_ids,
                "mapping_errors": list(item.get("mapping_errors") or []),
            }
        )
    rows.sort(key=lambda row: (-float(row["score"]) if isinstance(row.get("score"), (int, float)) else 1.0, str(row.get("name"))))
    for index, row in enumerate(rows, start=1):
        row["rank"] = index

    decision = event.get("decision") if isinstance(event.get("decision"), Mapping) else emergency_decision
    simulation = dict(decision) if isinstance(decision, Mapping) else {}
    target_id = simulation.get("target_id") or simulation.get("top_consumer")
    target_name = None
    for item in candidates:
        if isinstance(item, Mapping) and item.get("id") == target_id:
            target_name = item.get("name") or registry_by_id.get(target_id, {}).get("name") or str(target_id)[:12]
            break
    if target_name is not None:
        simulation["target_name"] = target_name
    simulation = {
        "mode": simulation.get("mode"),
        "action": simulation.get("action"),
        "execution": simulation.get("execution"),
        "resource_kind": simulation.get("resource_kind"),
        "target_name": simulation.get("target_name"),
        "reason_codes": list(simulation.get("reason_codes") or []),
    }
    protected_candidates = [
        {"name": row["name"], "reasons": row["protection_reasons"]}
        for row in rows
        if row["protected"]
    ]
    return {
        "schema": "guardian.runtime.presentation.v1",
        "candidate_ranking": {
            "status": "ok" if isinstance(registry, Mapping) and registry.get("status") == "ok" else "degraded",
            "resource_kind": selected_resource,
            "count": len(rows),
            "top": rows[0] if rows else None,
            "candidates": rows[:20],
        },
        "protection": {
            "protected_candidates": protected_candidates[:20],
            "configured_systemd_units": len(protection_config.get("systemd_units", [])),
            "configured_container_labels": len(protection_config.get("container_labels", [])),
        },
        "simulation": simulation,
    }


def record_watchdog_status(
    event: dict[str, Any],
    sent: bool,
    *,
    notify_socket: str | None = None,
) -> str:
    """Persist watchdog delivery state and block execution on a configured failure."""

    status = systemd_notification_status(sent, notify_socket=notify_socket)
    evidence = event.setdefault("evidence", {})
    evidence["watchdog_status"] = status
    if status == "failed":
        decision = event.setdefault("decision", {})
        decision["action"] = "escalate"
        decision["execution"] = "not_executed"
        reason_codes = decision.setdefault("reason_codes", [])
        if "watchdog_notify_failed" not in reason_codes:
            reason_codes.append("watchdog_notify_failed")
    return status


def _regular_file_bytes(directory: Path) -> int:
    try:
        return sum(item.stat().st_size for item in directory.iterdir() if item.is_file())
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return 0


def write_snapshot(
    event: dict[str, Any],
    directory: Path,
    *,
    max_total_bytes: int | None = None,
) -> str | None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{event['event_id']}.json"
        payload = json.dumps(event, ensure_ascii=False, indent=2) + "\n"
        if max_total_bytes is not None and _regular_file_bytes(directory) + len(payload.encode("utf-8")) > max_total_bytes:
            return None
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(path)
    except (OSError, TypeError, ValueError):
        return None
    return str(path)


def append_audit(
    event: dict[str, Any],
    path: Path,
    *,
    max_total_bytes: int | None = None,
) -> bool:
    """Append one audit event and only report success after syncing it.

    A successful ``write`` only means that bytes reached the process/file
    buffers.  The observer uses this result as an action gate, so returning
    success before ``flush``/``fsync`` would leave a crash window in which the
    decision appears accepted but the corresponding audit record is missing.
    A sync failure is deliberately fail-closed: callers must treat the event
    as degraded and must not continue toward an executable action.
    """

    payload = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        current_size = path.stat().st_size if path.exists() else 0
        if max_total_bytes is not None and current_size + len(payload) > max_total_bytes:
            return False
        with path.open("ab") as stream:
            written = stream.write(payload)
            if written != len(payload):
                return False
            stream.flush()
            os.fsync(stream.fileno())
    except (OSError, ValueError):
        return False
    return True


class ObserverSampler:
    """Stateful, read-only event producer for the continuous runtime.

    The former CLI loop owned the evaluator instances, which made it
    impossible for a long-running coordinator to consume the same in-process
    event stream. This class keeps that state alive across samples while
    leaving all mutation-capable work outside the observer.
    """

    def __init__(
        self,
        config: GuardianConfig,
        *,
        mode: str | None = None,
        interval: float | None = None,
        warning_available: float | None = None,
        critical_available: float | None = None,
        warning_for: float | None = None,
        critical_for: float | None = None,
        simulate_action: str = "graceful_stop",
        protected: bool = True,
        allowed_actions: Iterable[str] | None = None,
        collector: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        if not isinstance(config, GuardianConfig):
            raise ValueError("config_required")
        self.config = config
        self.mode = mode or config.mode
        if self.mode not in {"observe", "simulate", "enforce"}:
            raise ValueError("mode_invalid")
        self.interval = validate_interval_seconds(
            config.interval_seconds if interval is None else interval
        )
        self.warning_available = (
            config.warning_available_percent if warning_available is None else warning_available
        )
        self.critical_available = (
            config.critical_available_percent if critical_available is None else critical_available
        )
        self.warning_for = config.warning_for_seconds if warning_for is None else warning_for
        self.critical_for = config.critical_for_seconds if critical_for is None else critical_for
        self.simulate_action = simulate_action
        self.protected = protected
        self.allowed_actions = tuple(
            config.allowed_actions if allowed_actions is None else allowed_actions
        )
        self.collector = collector or collect_observation
        self.evaluator = CompositeRiskEvaluator(
            config,
            warning_for=self.warning_for,
            critical_for=self.critical_for,
        )
        self.attributor = ObjectAttributionEvaluator()
        cpu_policy = cpu_policy_from_config(config)
        self.cpu_evaluator = CpuRiskEvaluator(cpu_policy)
        self.cpu_attributor = CpuAttributionEvaluator(
            CpuAttributionPolicy(
                min_host_contribution_percent=cpu_policy.min_object_contribution_percent,
                min_lead_margin=cpu_policy.min_object_lead_margin,
                max_sample_age_seconds=cpu_policy.max_sample_age_seconds,
            )
        )
        disk_capacity_policy = disk_capacity_policy_from_config(config)
        io_policy = io_policy_from_config(config)
        self.disk_capacity_evaluator = DiskCapacityRiskEvaluator(disk_capacity_policy)
        self.disk_capacity_attributor = CapacityAttributionEvaluator()
        self.io_evaluator = IoRiskEvaluator(io_policy)
        self.io_attributor = DiskIoAttributionEvaluator(io_policy)
        self.disk_mount_points = disk_mount_points_from_config(config)
        self.emergency_policy = emergency_shedding_policy_from_config(config)
        self.sample_count = 0

    def sample(self) -> dict[str, Any]:
        """Collect and evaluate one real observation without executing actions."""

        observation = self.collector(disk_mount_points=self.disk_mount_points)
        object_attribution = self.attributor.evaluate(
            observation,
            observation.get("object_registry", {}),
        )
        cpu_risk = self.cpu_evaluator.evaluate(observation.get("cpu", {}))
        cpu_attribution = self.cpu_attributor.evaluate(observation.get("cpu", {}))
        disk_sample = observation.get("disk", {})
        disk_capacity_risk = self.disk_capacity_evaluator.evaluate(disk_sample)
        disk_capacity_attribution = self.disk_capacity_attributor.evaluate(disk_sample)
        io_risk = self.io_evaluator.evaluate(disk_sample)
        io_attribution = self.io_attributor.evaluate(disk_sample)
        event = build_event(
            observation,
            warning_available=self.warning_available,
            critical_available=self.critical_available,
            mode=self.mode,
            simulate_action=self.simulate_action,
            protected=self.protected,
            allowed_actions=self.allowed_actions,
            evaluator=self.evaluator,
            object_attribution=object_attribution,
        )
        event["resource_evaluations"] = {
            "memory": {
                "risk": event["risk"],
                "attribution": object_attribution,
                "decision": dict(event["decision"]),
            },
            "cpu": {
                "risk": cpu_risk,
                "attribution": cpu_attribution,
                "decision": build_cpu_simulation_decision(
                    cpu_risk,
                    cpu_attribution,
                    mode=self.mode,
                    simulate_action=self.simulate_action,
                    protected=self.protected,
                    allowed_actions=self.allowed_actions,
                ),
            },
            "disk_capacity": {
                "risk": disk_capacity_risk,
                "attribution": disk_capacity_attribution,
                "decision": build_disk_simulation_decision(
                    disk_capacity_risk,
                    disk_capacity_attribution,
                    resource_kind="disk_capacity",
                    mode=self.mode,
                    simulate_action=self.simulate_action,
                    protected=self.protected,
                    allowed_actions=self.allowed_actions,
                ),
            },
            "io": {
                "risk": io_risk,
                "attribution": io_attribution,
                "decision": build_disk_simulation_decision(
                    io_risk,
                    io_attribution,
                    resource_kind="io",
                    mode=self.mode,
                    simulate_action=self.simulate_action,
                    protected=self.protected,
                    allowed_actions=self.allowed_actions,
                ),
            },
        }
        emergency_host_risks = _emergency_host_risks(
            event["resource_evaluations"],
            required_samples=self.emergency_policy.required_samples,
        )
        for resource_kind, risk in emergency_host_risks.items():
            if resource_kind in event["resource_evaluations"]:
                event["resource_evaluations"][resource_kind]["risk"] = dict(risk)
        emergency = build_emergency_shedding_decision(
            emergency_host_risks,
            _emergency_candidates(
                observation,
                event["resource_evaluations"],
                window_seconds=self.emergency_policy.window_seconds,
            ),
            registry=(
                observation.get("object_registry", {}).get("objects", [])
                if isinstance(observation.get("object_registry"), Mapping)
                else []
            ),
            protected_set=self.emergency_policy.protected_set,
            actionable_set=self.emergency_policy.actionable_set,
            mode=self.mode,
            policy=self.emergency_policy,
            allowed_actions=self.allowed_actions,
            event_id=event["event_id"],
            sample_id=event["sample_id"],
            config_digest=self.config.config_digest,
        )
        joint = build_joint_decision(
            event["resource_evaluations"],
            mode=self.mode,
            simulate_action=self.simulate_action,
            protected=self.protected,
            allowed_actions=self.allowed_actions,
        )
        event["joint_evaluation"] = joint
        event["emergency_shedding"] = emergency
        event["target_attribution"] = joint["target"]
        event["state"] = joint["state"]
        event["decision"] = joint["decision"]
        if self.emergency_policy.enabled:
            # Once v1 is enabled it is the authoritative local action
            # boundary. The older causal/joint plan cannot bypass its
            # protected/actionable/identity gates. An enforce plan must also
            # carry an actionable event state into the Coordinator; otherwise
            # the plan would be rejected before authorization is checked.
            event["decision"] = dict(emergency["decision"])
            event["target_attribution"] = None
            if self.mode == "enforce" and isinstance(event["decision"].get("plan"), Mapping):
                event["state"] = "critical_confirmed"
        event["presentation"] = _build_runtime_presentation(
            event,
            observation,
            emergency,
            self.config,
        )
        event["evidence"]["config_digest"] = self.config.config_digest
        event["evidence"]["policy_digest"] = emergency_policy_digest(self.emergency_policy)
        event["evidence"]["sample_id"] = event["sample_id"]
        event["evidence"]["config_source"] = self.config.source
        self.sample_count += 1
        return event


def _finalize_observer_event(
    event: dict[str, Any],
    *,
    config: GuardianConfig,
    snapshot_dir: str | None,
    snapshot_all: bool,
    audit_file: str | None,
) -> dict[str, Any]:
    """Add runtime health and bounded local evidence before coordination."""

    gate = event.setdefault("evidence", {}).setdefault(
        "runtime_gate",
        {"execution_allowed": True, "reason_codes": []},
    )
    watchdog_sent = notify_watchdog(f"observe:{event['state']}")
    watchdog_status = record_watchdog_status(event, watchdog_sent)
    if watchdog_status == "failed":
        gate["execution_allowed"] = False
        gate["reason_codes"].append("watchdog_notify_failed")
    if snapshot_dir and (snapshot_all or event["state"] in {"warning", "critical", "recovered", "escalated"}):
        snapshot_path = write_snapshot(
            event,
            Path(snapshot_dir),
            max_total_bytes=config.snapshot_max_total_bytes,
        )
        event["evidence"]["snapshot_path"] = snapshot_path
        if snapshot_path is None:
            event["evidence"]["snapshot_status"] = "capacity_exhausted"
            event["decision"]["action"] = "escalate"
            event["decision"]["execution"] = "not_executed"
            event["decision"]["reason_codes"].append("snapshot_capacity_exhausted")
            gate["execution_allowed"] = False
            gate["reason_codes"].append("snapshot_capacity_exhausted")
    if audit_file:
        audit_written = append_audit(
            event,
            Path(audit_file),
            max_total_bytes=config.audit_max_total_bytes,
        )
        event["evidence"]["audit_status"] = "written" if audit_written else "degraded"
        if not audit_written:
            event["decision"]["action"] = "escalate"
            event["decision"]["execution"] = "not_executed"
            event["decision"]["reason_codes"].append("audit_write_failed_or_capacity_exhausted")
            gate["execution_allowed"] = False
            gate["reason_codes"].append("audit_write_failed_or_capacity_exhausted")
    gate["reason_codes"] = list(dict.fromkeys(gate["reason_codes"]))
    return event


def run(args: argparse.Namespace) -> None:
    try:
        config: GuardianConfig = load_config(args.config) if args.config else safe_defaults()
    except ConfigError as exc:
        raise SystemExit(f"configuration rejected: {exc}") from exc

    mode = args.mode or config.mode
    try:
        interval = validate_interval_seconds(
            args.interval if args.interval is not None else config.interval_seconds
        )
    except ValueError as exc:
        raise SystemExit(f"configuration rejected: {exc}") from exc
    warning_available = (
        args.warning_available
        if args.warning_available is not None
        else config.warning_available_percent
    )
    critical_available = (
        args.critical_available
        if args.critical_available is not None
        else config.critical_available_percent
    )
    warning_for = args.warning_for if args.warning_for is not None else config.warning_for_seconds
    critical_for = args.critical_for if args.critical_for is not None else config.critical_for_seconds
    snapshot_dir = args.snapshot_dir or config.snapshot_directory
    allowed_actions = args.allow_action if args.allow_action is not None else list(config.allowed_actions)
    simulate_action = args.simulate_action or "graceful_stop"
    evaluator = CompositeRiskEvaluator(
        config,
        warning_for=warning_for,
        critical_for=critical_for,
    )
    attributor = ObjectAttributionEvaluator()
    cpu_policy = cpu_policy_from_config(config)
    cpu_evaluator = CpuRiskEvaluator(cpu_policy)
    cpu_attributor = CpuAttributionEvaluator(
        CpuAttributionPolicy(
            min_host_contribution_percent=cpu_policy.min_object_contribution_percent,
            min_lead_margin=cpu_policy.min_object_lead_margin,
            max_sample_age_seconds=cpu_policy.max_sample_age_seconds,
        )
    )
    disk_capacity_policy = disk_capacity_policy_from_config(config)
    io_policy = io_policy_from_config(config)
    disk_capacity_evaluator = DiskCapacityRiskEvaluator(disk_capacity_policy)
    disk_capacity_attributor = CapacityAttributionEvaluator()
    io_evaluator = IoRiskEvaluator(io_policy)
    io_attributor = DiskIoAttributionEvaluator(io_policy)
    disk_mount_points = disk_mount_points_from_config(config)
    emergency_policy = emergency_shedding_policy_from_config(config)
    ready_notified = False
    sample_count = 0
    while True:
        observation = collect_observation(disk_mount_points=disk_mount_points)
        object_attribution = attributor.evaluate(
            observation,
            observation.get("object_registry", {}),
        )
        cpu_risk = cpu_evaluator.evaluate(observation.get("cpu", {}))
        cpu_attribution = cpu_attributor.evaluate(observation.get("cpu", {}))
        disk_sample = observation.get("disk", {})
        disk_capacity_risk = disk_capacity_evaluator.evaluate(disk_sample)
        disk_capacity_attribution = disk_capacity_attributor.evaluate(disk_sample)
        io_risk = io_evaluator.evaluate(disk_sample)
        io_attribution = io_attributor.evaluate(disk_sample)
        event = build_event(
            observation,
            warning_available=warning_available,
            critical_available=critical_available,
            mode=mode,
            simulate_action=simulate_action,
            protected=not args.allow_unprotected,
            allowed_actions=allowed_actions,
            evaluator=evaluator,
            object_attribution=object_attribution,
        )
        event["resource_evaluations"] = {
            "memory": {
                "risk": event["risk"],
                "attribution": object_attribution,
                "decision": dict(event["decision"]),
            },
            "cpu": {
                "risk": cpu_risk,
                "attribution": cpu_attribution,
                "decision": build_cpu_simulation_decision(
                    cpu_risk,
                    cpu_attribution,
                    mode=mode,
                    simulate_action=simulate_action,
                    protected=not args.allow_unprotected,
                    allowed_actions=allowed_actions,
                ),
            },
            "disk_capacity": {
                "risk": disk_capacity_risk,
                "attribution": disk_capacity_attribution,
                "decision": build_disk_simulation_decision(
                    disk_capacity_risk,
                    disk_capacity_attribution,
                    resource_kind="disk_capacity",
                    mode=mode,
                    simulate_action=simulate_action,
                    protected=not args.allow_unprotected,
                    allowed_actions=allowed_actions,
                ),
            },
            "io": {
                "risk": io_risk,
                "attribution": io_attribution,
                "decision": build_disk_simulation_decision(
                    io_risk,
                    io_attribution,
                    resource_kind="io",
                    mode=mode,
                    simulate_action=simulate_action,
                    protected=not args.allow_unprotected,
                    allowed_actions=allowed_actions,
                ),
            },
        }
        emergency_host_risks = _emergency_host_risks(
            event["resource_evaluations"],
            required_samples=emergency_policy.required_samples,
        )
        for resource_kind, risk in emergency_host_risks.items():
            if resource_kind in event["resource_evaluations"]:
                event["resource_evaluations"][resource_kind]["risk"] = dict(risk)
        emergency = build_emergency_shedding_decision(
            emergency_host_risks,
            _emergency_candidates(
                observation,
                event["resource_evaluations"],
                window_seconds=emergency_policy.window_seconds,
            ),
            registry=(observation.get("object_registry", {}).get("objects", [])
                      if isinstance(observation.get("object_registry"), Mapping)
                      else []),
            protected_set=emergency_policy.protected_set,
            actionable_set=emergency_policy.actionable_set,
            mode=mode,
            policy=emergency_policy,
            allowed_actions=allowed_actions,
            event_id=event["event_id"],
            sample_id=event["sample_id"],
            config_digest=config.config_digest,
        )
        joint = build_joint_decision(
            event["resource_evaluations"],
            mode=mode,
            simulate_action=simulate_action,
            protected=not args.allow_unprotected,
            allowed_actions=allowed_actions,
        )
        event["joint_evaluation"] = joint
        event["emergency_shedding"] = emergency
        event["target_attribution"] = joint["target"]
        event["state"] = joint["state"]
        event["decision"] = joint["decision"]
        if emergency_policy.enabled:
            # Once v1 is enabled it is the authoritative local action
            # boundary. The older causal/joint plan cannot bypass its
            # protected/actionable/identity gates. An enforce plan must also
            # carry an actionable event state into the Coordinator.
            event["decision"] = dict(emergency["decision"])
            event["target_attribution"] = None
            if mode == "enforce" and isinstance(event["decision"].get("plan"), Mapping):
                event["state"] = "critical_confirmed"
        event["presentation"] = _build_runtime_presentation(
            event,
            observation,
            emergency,
            config,
        )
        event["evidence"]["config_digest"] = config.config_digest
        event["evidence"]["policy_digest"] = emergency_policy_digest(emergency_policy)
        event["evidence"]["sample_id"] = event["sample_id"]
        event["evidence"]["config_source"] = config.source
        watchdog_sent = notify_watchdog(f"observe:{event['state']}")
        record_watchdog_status(event, watchdog_sent)
        if snapshot_dir and (args.snapshot_all or event["state"] in {"warning", "critical", "recovered", "escalated"}):
            snapshot_path = write_snapshot(
                event,
                Path(snapshot_dir),
                max_total_bytes=config.snapshot_max_total_bytes,
            )
            event["evidence"]["snapshot_path"] = snapshot_path
            if snapshot_path is None:
                event["evidence"]["snapshot_status"] = "capacity_exhausted"
                event["decision"]["action"] = "escalate"
                event["decision"]["execution"] = "not_executed"
                event["decision"]["reason_codes"].append("snapshot_capacity_exhausted")
        if args.audit_file:
            audit_written = append_audit(
                event,
                Path(args.audit_file),
                max_total_bytes=config.audit_max_total_bytes,
            )
            event["evidence"]["audit_status"] = "written" if audit_written else "degraded"
            if not audit_written:
                event["decision"]["action"] = "escalate"
                event["decision"]["execution"] = "not_executed"
                event["decision"]["reason_codes"].append("audit_write_failed_or_capacity_exhausted")
        if not ready_notified:
            # Readiness means the process has completed its first collection,
            # not that the first sample was normal. Risk state is reported via
            # the watchdog STATUS field and the persisted event.
            ready_notified = notify_ready("observe:ready")
        print(json.dumps(event, ensure_ascii=False), flush=True)
        sample_count += 1
        if args.once or (args.samples is not None and sample_count >= args.samples):
            return
        time.sleep(interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Guardian observe prototype")
    parser.add_argument("--once", action="store_true", help="emit one observation and exit")
    parser.add_argument("--samples", type=int, default=None, help="emit a bounded number of samples and exit")
    parser.add_argument("--config", type=Path, help="strict JSON config; absent means safe observe-only defaults")
    parser.add_argument(
        "--mode",
        choices=("observe", "simulate", "enforce"),
        default=None,
        help="enforce 只生成待控制层接管的计划；真实动作必须另行调用 guardian_enforce",
    )
    parser.add_argument("--interval", type=float, default=None, help="sampling interval in seconds")
    parser.add_argument("--warning-available", type=float, default=None, help="override config warning threshold")
    parser.add_argument("--critical-available", type=float, default=None, help="override config critical threshold")
    parser.add_argument("--warning-for", type=float, default=None, help="override warning persistence window")
    parser.add_argument("--critical-for", type=float, default=None, help="override critical persistence window")
    parser.add_argument("--snapshot-dir", help="optional directory for JSON snapshots")
    parser.add_argument("--snapshot-all", action="store_true", help="snapshot normal observations too")
    parser.add_argument("--audit-file", help="optional JSONL audit file")
    parser.add_argument("--simulate-action", default=None, choices=("notify", "snapshot", "graceful_stop", "restart", "terminate", "escalate"))
    parser.add_argument("--allow-action", action="append", default=None, help="override config action allowlist; repeatable")
    parser.add_argument("--allow-unprotected", action="store_true", help="fixture-only switch to simulate a non-protected target")
    parsed = parser.parse_args()
    if parsed.samples is not None and not 1 <= parsed.samples <= 120:
        raise SystemExit("samples must be between 1 and 120")
    run(parsed)


if __name__ == "__main__":
    main()
