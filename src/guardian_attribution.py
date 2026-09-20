"""Read-only Docker/cgroup object registry and risk attribution.

The module deliberately stops at evidence and target selection.  It never
authorizes or executes an action.  A target is confirmed only when a stable
Docker identity is mapped to a readable cgroup and its memory contribution is
large enough and clearly ahead of the next candidate.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
_DOCKER_SCOPE = re.compile(r"(?:^|/)docker-([0-9a-f]{12,64})\.scope$")


def _read_optional(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _read_int(path: Path) -> int | None:
    value = _read_optional(path)
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _read_scalar(path: Path) -> int | str | None:
    value = _read_optional(path)
    if value is None:
        return None
    value = value.strip()
    try:
        return int(value)
    except ValueError:
        return value


def _parse_counters(text: str | None) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in (text or "").splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            result[fields[0]] = int(fields[1])
        except ValueError:
            continue
    return result


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _container_id(container: Mapping[str, Any]) -> str | None:
    for key in ("Container", "ID", "Id", "id"):
        value = container.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _inspect_records(result: subprocess.CompletedProcess[str] | None) -> tuple[dict[str, Mapping[str, Any]], list[str]]:
    if result is None:
        return {}, ["docker_inspect_not_run"]
    if result.returncode != 0:
        error = (result.stderr or "docker inspect failed").strip()
        return {}, [f"docker_inspect_failed:{error}"]
    records: dict[str, Mapping[str, Any]] = {}
    errors: list[str] = []
    for line in result.stdout.splitlines():
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            errors.append("docker_inspect_invalid_json")
            continue
        if not isinstance(raw, Mapping):
            errors.append("docker_inspect_record_not_object")
            continue
        stable_id = raw.get("Id")
        if isinstance(stable_id, str) and stable_id:
            records[stable_id] = raw
    return records, errors


def _find_inspect_record(records: Mapping[str, Mapping[str, Any]], stable_id: str | None) -> Mapping[str, Any]:
    if not stable_id:
        return {}
    if stable_id in records:
        return records[stable_id]
    matches = [record for key, record in records.items() if key.startswith(stable_id) or stable_id.startswith(key)]
    return matches[0] if len(matches) == 1 else {}


def _process_cgroup_path(proc_root: Path, cgroup_root: Path, pid: int | None) -> tuple[Path | None, list[str]]:
    if pid is None or pid <= 0:
        return None, ["container_pid_missing"]
    content = _read_optional(proc_root / str(pid) / "cgroup")
    if content is None:
        return None, ["container_cgroup_file_missing"]
    for line in content.splitlines():
        hierarchy, _, relative = line.partition("::")
        if hierarchy == "0" and relative:
            path = cgroup_root / relative.lstrip("/")
            if path.is_dir():
                return path, []
            return path, ["container_cgroup_path_missing"]
    return None, ["container_cgroup_v2_path_missing"]


def _identity_flags(stable_id: str | None, cgroup_path: Path | None) -> list[str]:
    if not stable_id or cgroup_path is None:
        return []
    match = _DOCKER_SCOPE.search(str(cgroup_path))
    if match and not (match.group(1) == stable_id or match.group(1).startswith(stable_id) or stable_id.startswith(match.group(1))):
        return ["container_id_cgroup_mismatch"]
    return []


def collect_object_registry(
    docker: Mapping[str, Any],
    *,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
    runner: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    """Collect stable Docker identities and their cgroup memory evidence."""

    if docker.get("available") is False:
        return {"status": "unavailable", "objects": [], "errors": ["docker_stats_unavailable"]}
    raw_containers = docker.get("containers")
    if not isinstance(raw_containers, list):
        return {"status": "degraded", "objects": [], "errors": ["docker_container_list_missing"]}

    identifiers = [
        identifier
        for container in raw_containers
        if isinstance(container, Mapping)
        for identifier in [_container_id(container)]
        if identifier
    ]
    inspect_records: dict[str, Mapping[str, Any]] = {}
    errors: list[str] = []
    if identifiers:
        command = ["docker", "inspect", "--format", "{{json .}}", *identifiers]
        try:
            result = runner(command, capture_output=True, text=True, timeout=5, check=False)
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            result = None
            errors.append(f"docker_inspect_unavailable:{exc}")
        inspect_records, inspect_errors = _inspect_records(result)
        errors.extend(inspect_errors)

    objects: list[dict[str, Any]] = []
    for container in raw_containers:
        if not isinstance(container, Mapping):
            errors.append("docker_container_record_not_object")
            continue
        stats_id = _container_id(container)
        inspect = _find_inspect_record(inspect_records, stats_id)
        stable_id = inspect.get("Id") if isinstance(inspect.get("Id"), str) else stats_id
        state = _mapping(inspect.get("State"))
        config = _mapping(inspect.get("Config"))
        pid = state.get("Pid")
        if not isinstance(pid, int) or isinstance(pid, bool):
            pid = None
        cgroup_path, mapping_errors = _process_cgroup_path(proc_root, cgroup_root, pid)
        identity_flags = _identity_flags(stable_id, cgroup_path)
        object_errors = list(mapping_errors) + identity_flags
        if not inspect:
            object_errors.append("docker_inspect_identity_missing")
        current = _read_int(cgroup_path / "memory.current") if cgroup_path else None
        events = _parse_counters(_read_optional(cgroup_path / "memory.events")) if cgroup_path else {}
        if cgroup_path is not None and current is None:
            object_errors.append("container_memory_current_missing")
        if cgroup_path is not None and not {"oom", "oom_kill"}.issubset(events):
            object_errors.append("container_oom_counters_missing")
        health = _mapping(state.get("Health"))
        name = inspect.get("Name") or container.get("Name")
        if isinstance(name, str):
            name = name.lstrip("/")
        labels = config.get("Labels") if isinstance(config.get("Labels"), Mapping) else {}
        mapping_confidence = "high" if inspect and cgroup_path and not identity_flags else "medium" if stable_id else "low"
        if object_errors:
            mapping_confidence = "low" if identity_flags else "medium"
        objects.append(
            {
                "kind": "container",
                "id": stable_id,
                "name": name,
                "image": config.get("Image") or container.get("Image"),
                "created_at": inspect.get("Created"),
                "status": state.get("Status") or "unknown",
                "health": health.get("Status") or "unknown",
                "labels": dict(labels),
                "pid": pid,
                "cgroup_path": str(cgroup_path) if cgroup_path else None,
                "mapping_confidence": mapping_confidence,
                "mapping_errors": sorted(set(object_errors)),
                "memory_current_bytes": current,
                "memory_max": _read_scalar(cgroup_path / "memory.max") if cgroup_path else None,
                "memory_events": events,
            }
        )

    status = "ok" if not errors and all(not item["mapping_errors"] for item in objects) else "degraded"
    return {"status": status, "objects": objects, "errors": sorted(set(errors))}


@dataclass(frozen=True)
class AttributionPolicy:
    """Local MVP policy; values are calibration defaults, not production SLA."""

    min_host_contribution_percent: float = 20.0
    min_lead_margin: float = 0.15
    max_sample_age_seconds: float = 15.0


def _counter_delta(current: Mapping[str, Any], previous: Mapping[str, Any]) -> tuple[int | None, bool]:
    current_values = {key: value for key, value in current.items() if key in {"oom", "oom_kill"} and isinstance(value, int) and value >= 0}
    previous_values = {key: value for key, value in previous.items() if key in {"oom", "oom_kill"} and isinstance(value, int) and value >= 0}
    if not {"oom", "oom_kill"}.issubset(current_values) or not {"oom", "oom_kill"}.issubset(previous_values):
        return None, True
    reset = any(current_values[key] < previous_values[key] for key in current_values)
    return sum(max(current_values[key] - previous_values[key], 0) for key in current_values), reset


class ObjectAttributionEvaluator:
    """Attribute host memory pressure to containers using two samples."""

    def __init__(self, policy: AttributionPolicy | None = None) -> None:
        self.policy = policy or AttributionPolicy()
        self._previous_objects: dict[str, Mapping[str, Any]] = {}
        self._previous_names: dict[str, str] = {}
        self._previous_host_available: int | None = None
        self._previous_timestamp: float | None = None

    def evaluate(
        self,
        host_observation: Mapping[str, Any],
        registry: Mapping[str, Any],
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        timestamp = time.monotonic() if now is None else now
        memory = _mapping(host_observation.get("memory"))
        available = memory.get("available_bytes")
        available = available if isinstance(available, int) and not isinstance(available, bool) else None
        observed_ns = host_observation.get("observed_monotonic_ns")
        quality_flags: list[str] = []
        if not isinstance(observed_ns, int) or isinstance(observed_ns, bool):
            quality_flags.append("host_monotonic_timestamp_missing")
        elif self._previous_host_available is not None and self._previous_timestamp is not None:
            if timestamp <= self._previous_timestamp:
                quality_flags.append("attribution_clock_not_increasing")
            elif timestamp - self._previous_timestamp > self.policy.max_sample_age_seconds:
                quality_flags.append("attribution_sample_gap_too_large")

        raw_objects = registry.get("objects")
        objects = [item for item in raw_objects if isinstance(item, Mapping)] if isinstance(raw_objects, list) else []
        registry_status = registry.get("status")
        reasons: list[str] = []
        if registry_status != "ok":
            reasons.extend(str(error) for error in registry.get("errors", []) if error)
            reasons.append("object_registry_degraded")
        if not objects:
            reasons.append("object_registry_empty")

        elapsed = None
        if self._previous_timestamp is not None:
            elapsed = timestamp - self._previous_timestamp
        host_decline_rate = None
        if elapsed is not None and elapsed > 0 and self._previous_host_available is not None and available is not None:
            host_decline_rate = max((self._previous_host_available - available) / elapsed, 0.0)

        candidates: list[dict[str, Any]] = []
        identity_blockers: list[str] = []
        positive_growth: list[float] = []
        for item in objects:
            stable_id = item.get("id")
            name = item.get("name")
            if not isinstance(stable_id, str) or not stable_id:
                reasons.append("stable_object_identity_missing")
                continue
            previous = self._previous_objects.get(stable_id)
            previous_name_id = self._previous_names.get(name) if isinstance(name, str) else None
            if previous_name_id and previous_name_id != stable_id:
                identity_blockers.append("container_recreated")
            previous_path = previous.get("cgroup_path") if previous else None
            current_path = item.get("cgroup_path")
            if previous_path and current_path and previous_path != current_path:
                identity_blockers.append("container_cgroup_path_changed")
            mapping_errors = item.get("mapping_errors")
            if isinstance(mapping_errors, list):
                identity_blockers.extend(str(error) for error in mapping_errors if error in {"container_id_cgroup_mismatch"})
            current = item.get("memory_current_bytes")
            previous_current = previous.get("memory_current_bytes") if previous else None
            growth_rate = None
            if elapsed is not None and elapsed > 0 and isinstance(current, int) and isinstance(previous_current, int):
                growth_rate = max((current - previous_current) / elapsed, 0.0)
                if growth_rate > 0:
                    positive_growth.append(growth_rate)
            oom_delta = None
            oom_reset = False
            if previous:
                oom_delta, oom_reset = _counter_delta(
                    _mapping(item.get("memory_events")),
                    _mapping(previous.get("memory_events")),
                )
            if oom_reset:
                quality_flags.append("object_oom_counter_reset")
            growth_share = None
            if growth_rate is not None and positive_growth:
                growth_share = growth_rate / max(sum(positive_growth), 1.0) * 100
            host_contribution = None
            if growth_rate is not None and host_decline_rate and host_decline_rate > 0:
                host_contribution = min(growth_rate / host_decline_rate * 100, 100.0)
            confidence = "high" if item.get("mapping_confidence") == "high" and current_path and isinstance(current, int) else "medium" if stable_id and current_path else "low"
            if mapping_errors:
                confidence = "low" if "container_id_cgroup_mismatch" in mapping_errors else confidence
            score = 0.0
            if host_contribution is not None:
                score = host_contribution / 100
            elif growth_share is not None:
                score = growth_share / 100
            if oom_delta and oom_delta > 0:
                score += 0.25
            candidates.append(
                {
                    "kind": "container",
                    "id": stable_id,
                    "name": name,
                    "confidence": confidence,
                    "cgroup_path": current_path,
                    "memory_current_bytes": current,
                    "memory_growth_bytes_per_second": growth_rate,
                    "memory_growth_share_percent": round(growth_share, 3) if growth_share is not None else None,
                    "host_contribution_percent": round(host_contribution, 3) if host_contribution is not None else None,
                    "oom_events_delta": oom_delta,
                    "score": round(min(score, 1.0), 6),
                    "mapping_errors": list(mapping_errors) if isinstance(mapping_errors, list) else [],
                }
            )

        # Recompute growth shares after all objects are known.
        total_growth = sum(
            item["memory_growth_bytes_per_second"]
            for item in candidates
            if isinstance(item.get("memory_growth_bytes_per_second"), (int, float))
        )
        for item in candidates:
            rate = item.get("memory_growth_bytes_per_second")
            if isinstance(rate, (int, float)) and total_growth > 0:
                item["memory_growth_share_percent"] = round(rate / total_growth * 100, 3)
                if item["host_contribution_percent"] is None:
                    item["score"] = round(min(rate / total_growth + (0.25 if (item.get("oom_events_delta") or 0) > 0 else 0), 1.0), 6)

        candidates.sort(key=lambda item: (-item["score"], str(item.get("id"))))
        if self._previous_timestamp is None:
            reasons.append("object_baseline_established")
        elif elapsed is None or elapsed <= 0:
            quality_flags.append("attribution_elapsed_missing")
        if not self._previous_objects:
            reasons.append("insufficient_object_history")
        if identity_blockers:
            reasons.extend(identity_blockers)
        valid = [
            item
            for item in candidates
            if item["confidence"] == "high"
            and isinstance(item.get("host_contribution_percent"), (int, float))
            and item["host_contribution_percent"] >= self.policy.min_host_contribution_percent
        ]
        top = valid[0] if valid else None
        second = valid[1] if len(valid) > 1 else None
        lead_margin = (top["score"] - second["score"]) if top and second else top["score"] if top else None

        state = "NO_TARGET"
        target = None
        if quality_flags or registry_status != "ok" or not self._previous_objects:
            state = "DEGRADED_OBSERVABILITY"
        elif identity_blockers:
            state = "AMBIGUOUS_TARGET"
            reasons.append("target_identity_not_stable")
        elif not valid:
            state = "NO_TARGET"
            reasons.append("no_candidate_meets_confidence_or_contribution")
        elif second and lead_margin is not None and lead_margin < self.policy.min_lead_margin:
            state = "AMBIGUOUS_TARGET"
            reasons.append("candidate_lead_margin_too_small")
        else:
            state = "TARGET_CONFIRMED"
            target = top
            reasons.append("single_target_exceeds_contribution_and_margin")

        self._previous_objects = {str(item.get("id")): item for item in objects if item.get("id")}
        self._previous_names = {
            str(item.get("name")): str(item.get("id"))
            for item in objects
            if item.get("name") and item.get("id")
        }
        self._previous_host_available = available
        self._previous_timestamp = timestamp

        return {
            "state": state,
            "target": target,
            "candidates": candidates,
            "reason_codes": sorted(set(reasons)),
            "quality_flags": sorted(set(quality_flags)),
            "host_decline_bytes_per_second": host_decline_rate,
            "lead_margin": lead_margin,
            "sample_elapsed_seconds": elapsed,
        }


__all__ = [
    "AttributionPolicy",
    "ObjectAttributionEvaluator",
    "collect_object_registry",
]
