"""Fail-closed Emergency Shedding v1 policy.

This module is intentionally a pure decision boundary.  It ranks complete,
currently running Docker identities only after the host has entered an
explicit ``CRITICAL_CONFIRMED`` state.  It never imports an action adapter and
never executes a command.  The result is either one immutable
``graceful_stop`` simulate plan or an auditable refusal.

The v1 policy is deliberately less ambitious than causal attribution:
``TOP`` means the largest contributor among the complete registry snapshot,
not the root cause.  A positive ``actionable_set`` is required; being absent
from ``protected_set`` is never enough.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any


EMERGENCY_SHEDDING_SCHEMA = "guardian.emergency_shedding.v1"
SUPPORTED_RESOURCES = ("memory", "cpu", "io", "disk_capacity")
FULL_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")

_RESOURCE_ALIASES = {
    "capacity": "disk_capacity",
    "disk_capacity": "disk_capacity",
    "disk": "disk_capacity",
    "memory": "memory",
    "cpu": "cpu",
    "io": "io",
}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _materialize_iterable(value: Any) -> tuple[list[Any], bool]:
    """Materialize a record/action input and flag non-iterable containers.

    The policy boundary receives data from adapters and configuration.  A
    malformed ``None``/mapping/string must become a refusal, not an uncaught
    ``TypeError`` or a silently filtered record list.
    """

    if value is None or isinstance(value, (Mapping, str, bytes, bytearray)):
        return [], True
    try:
        return list(value), False
    except Exception:
        return [], True


def _resource_kind(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return _RESOURCE_ALIASES.get(value.strip().lower())


def _entry_id(value: Mapping[str, Any]) -> str | None:
    for key in ("stable_id", "container_id", "id"):
        candidate = value.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    selector = _mapping(value.get("selector"))
    for key in ("stable_id", "container_id", "id"):
        candidate = selector.get(key)
        if isinstance(candidate, str) and candidate:
            return candidate
    return None


def _labels(value: Any) -> tuple[tuple[str, str], ...]:
    """Return a bounded, deterministic Docker label identity."""

    if not isinstance(value, Mapping):
        return ()
    pairs: list[tuple[str, str]] = []
    for key, raw in value.items():
        if not isinstance(key, str) or not isinstance(raw, str):
            continue
        if not key or len(key) > 256 or len(raw) > 1024:
            continue
        pairs.append((key, raw))
    return tuple(sorted(pairs))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        numeric = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


@dataclass(frozen=True)
class EmergencySheddingPolicy:
    """Versioned, deterministic v1 ranking policy."""

    enabled: bool = False
    window_seconds: float = 15.0
    required_samples: int = 2
    min_host_contribution_percent: float = 20.0
    resource_priority: tuple[str, ...] = SUPPORTED_RESOURCES
    action: str = "graceful_stop"
    protected_set: tuple[Mapping[str, Any], ...] = ()
    actionable_set: tuple[Mapping[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise ValueError("enabled_must_be_boolean")
        window_seconds = _number(self.window_seconds)
        if window_seconds is None or window_seconds <= 0:
            raise ValueError("window_seconds_must_be_positive")
        if type(self.required_samples) is not int or not 1 <= self.required_samples <= 60:
            raise ValueError("required_samples_out_of_bounds")
        min_host_contribution_percent = _number(self.min_host_contribution_percent)
        if min_host_contribution_percent is None or not 0 <= min_host_contribution_percent <= 100:
            raise ValueError("min_host_contribution_out_of_bounds")
        if (
            not isinstance(self.resource_priority, (tuple, list))
            or any(not isinstance(item, str) for item in self.resource_priority)
            or len(self.resource_priority) != len(SUPPORTED_RESOURCES)
            or set(self.resource_priority) != set(SUPPORTED_RESOURCES)
        ):
            raise ValueError("resource_priority_must_cover_all_resources")
        if self.action != "graceful_stop":
            raise ValueError("only_graceful_stop_is_supported")


@dataclass(frozen=True)
class EmergencySheddingPlan:
    """One immutable, non-executed v1 plan."""

    plan_id: str
    resource_kind: str
    target_id: str
    target_created_at: str
    target_cgroup_path: str
    target_cgroup_inode: int
    target_labels: tuple[tuple[str, str], ...] = ()
    action: str = "graceful_stop"
    execution: str = "not_executed"
    max_actions: int = 1
    root_cause_claimed: bool = False
    schema: str = EMERGENCY_SHEDDING_SCHEMA
    event_id: str = ""
    sample_id: str = ""
    policy_digest: str = ""
    config_digest: str = ""
    expires_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "plan_id": self.plan_id,
            "resource_kind": self.resource_kind,
            "target_id": self.target_id,
            "target": {
                "kind": "container",
                "id": self.target_id,
                "created_at": self.target_created_at,
                "cgroup_path": self.target_cgroup_path,
                "cgroup_inode": self.target_cgroup_inode,
                "labels": {key: value for key, value in self.target_labels},
            },
            "action": self.action,
            "execution": self.execution,
            "max_actions": self.max_actions,
            "root_cause_claimed": self.root_cause_claimed,
            "event_id": self.event_id,
            "sample_id": self.sample_id,
            "policy_digest": self.policy_digest,
            "config_digest": self.config_digest,
            "expires_at": self.expires_at,
        }


def policy_from_mapping(value: Mapping[str, Any] | None) -> EmergencySheddingPolicy:
    """Build a policy from the optional config block.

    An absent block is the safe disabled policy.  Runtime callers should use
    ``guardian_config.validate_config`` first; this function remains strict so
    an invalid ad-hoc mapping cannot silently widen the action surface.
    """

    raw = _mapping(value)
    if not raw:
        return EmergencySheddingPolicy()
    if "schema" in raw and raw.get("schema") != EMERGENCY_SHEDDING_SCHEMA:
        raise ValueError("schema_unsupported")
    enabled = raw.get("enabled", False)
    if type(enabled) is not bool:
        raise ValueError("enabled_must_be_boolean")
    priority_value = raw.get("resource_priority", SUPPORTED_RESOURCES)
    if not isinstance(priority_value, (list, tuple)):
        raise ValueError("resource_priority_must_be_list")
    priority = tuple(priority_value)

    def entry_tuple(key: str) -> tuple[Mapping[str, Any], ...]:
        entries = raw.get(key, [])
        if not isinstance(entries, list):
            raise ValueError(f"{key}_must_be_list")
        if any(not isinstance(item, Mapping) for item in entries):
            raise ValueError(f"{key}_entries_must_be_mappings")
        return tuple(dict(item) for item in entries)

    protected = entry_tuple("protected_set")
    actionable = entry_tuple("actionable_set")
    return EmergencySheddingPolicy(
        enabled=enabled,
        window_seconds=raw.get("window_seconds", 15.0),
        required_samples=raw.get("required_samples", 2),
        min_host_contribution_percent=raw.get("min_host_contribution_percent", 20.0),
        resource_priority=priority,
        action=raw.get("action", "graceful_stop"),
        protected_set=protected,
        actionable_set=actionable,
    )


def emergency_shedding_policy_from_config(config: Any) -> EmergencySheddingPolicy:
    values = getattr(config, "emergency_shedding_policy", None)
    return policy_from_mapping(values if isinstance(values, Mapping) else None)


def emergency_policy_digest(policy: EmergencySheddingPolicy) -> str:
    """Return the stable digest carried by every generated shedding plan."""

    payload = {
        "schema": EMERGENCY_SHEDDING_SCHEMA,
        "enabled": policy.enabled,
        "window_seconds": policy.window_seconds,
        "required_samples": policy.required_samples,
        "min_host_contribution_percent": policy.min_host_contribution_percent,
        "resource_priority": list(policy.resource_priority),
        "action": policy.action,
        "protected_set": [dict(item) for item in policy.protected_set],
        "actionable_set": [dict(item) for item in policy.actionable_set],
    }
    try:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ValueError("policy_not_serializable") from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _risk_mapping(value: Any) -> Mapping[str, Any]:
    value = _mapping(value)
    nested = value.get("risk")
    return _mapping(nested) if isinstance(nested, Mapping) else value


def _risk_state(value: Mapping[str, Any]) -> str:
    return str(value.get("state") or "NORMAL").upper()


def _is_confirmed(value: Mapping[str, Any]) -> bool:
    state = _risk_state(value)
    if state == "CRITICAL_CONFIRMED":
        return True
    # Observer's existing evaluators call the dwell-complete state ``critical``.
    # Only an explicit confirmation marker may translate it into the v1 gate.
    return state == "CRITICAL" and value.get("danger_confirmed") is True


def _sample_complete(value: Mapping[str, Any], policy: EmergencySheddingPolicy) -> bool:
    explicit_complete: bool | None = None
    for key in ("sample_complete", "samples_complete", "sample_window_complete"):
        if key in value:
            explicit_complete = value.get(key) is True
            break
    count = value.get("sample_count")
    required = value.get("required_samples", policy.required_samples)
    quality_flags = value.get("quality_flags", [])
    return (
        (explicit_complete is None or explicit_complete)
        and isinstance(count, int)
        and not isinstance(count, bool)
        and type(required) is int
        and 1 <= required <= 60
        and count >= required
        and value.get("quality_status", "ok") == "ok"
        and isinstance(quality_flags, list)
        and not quality_flags
    )


def _identity_fields(value: Mapping[str, Any]) -> tuple[str | None, str | None, str | None, int | None]:
    stable_id = _entry_id(value)
    created_at = value.get("created_at")
    cgroup_path = value.get("cgroup_path")
    cgroup_inode = value.get("cgroup_inode")
    return (
        stable_id if isinstance(stable_id, str) else None,
        created_at if isinstance(created_at, str) and created_at else None,
        cgroup_path if isinstance(cgroup_path, str) and cgroup_path else None,
        cgroup_inode if isinstance(cgroup_inode, int) and not isinstance(cgroup_inode, bool) and cgroup_inode > 0 else None,
    )


def _identity_reason(candidate: Mapping[str, Any], registry: Mapping[str, Mapping[str, Any]]) -> str | None:
    if candidate.get("identity_changed") is True or candidate.get("registry_identity_changed") is True:
        return "TARGET_IDENTITY_CHANGED"
    stable_id, created_at, cgroup_path, cgroup_inode = _identity_fields(candidate)
    if not stable_id or not FULL_CONTAINER_ID.fullmatch(stable_id):
        return "DATA_INCOMPLETE"
    if not created_at or not cgroup_path or cgroup_inode is None:
        return "DATA_INCOMPLETE"
    observed = registry.get(stable_id)
    if observed is None:
        return "TARGET_IDENTITY_CHANGED"
    observed_id, observed_created, observed_path, observed_inode = _identity_fields(observed)
    if observed_id != stable_id:
        return "TARGET_IDENTITY_CHANGED"
    if any(
        left != right
        for left, right in (
            (created_at, observed_created),
            (cgroup_path, observed_path),
            (cgroup_inode, observed_inode),
        )
    ):
        return "TARGET_IDENTITY_CHANGED"
    return None


def _contribution(candidate: Mapping[str, Any], resource_kind: str) -> float | None:
    resource_values = candidate.get("resource_contributions")
    if not isinstance(resource_values, Mapping):
        resource_values = candidate.get("contributions")
    if isinstance(resource_values, Mapping):
        value = _number(resource_values.get(resource_kind))
        if value is not None:
            return value
    key_map = {
        "memory": ("memory_contribution_percent", "host_contribution_percent", "memory_growth_share_percent"),
        "cpu": ("cpu_contribution_percent", "host_contribution_percent"),
        "io": ("io_contribution_percent", "host_contribution_percent"),
        "disk_capacity": ("capacity_contribution_percent", "capacity_writer_contribution_percent", "host_contribution_percent"),
    }
    for key in key_map[resource_kind]:
        value = _number(candidate.get(key))
        if value is not None:
            return value
    return None


def _actionable_expiry_reason(entry: Mapping[str, Any], *, now_epoch_s: float) -> str | None:
    expires_at = entry.get("expires_at")
    if not isinstance(expires_at, str) or not expires_at:
        return "DATA_INCOMPLETE"
    try:
        normalized = expires_at[:-1] + "+00:00" if expires_at.endswith("Z") else expires_at
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return "DATA_INCOMPLETE"
        expires_epoch_s = parsed.timestamp()
    except (TypeError, ValueError, OverflowError):
        return "DATA_INCOMPLETE"
    return "ACTIONABLE_ENTRY_EXPIRED" if expires_epoch_s <= now_epoch_s else None


def _validated_entry_map(
    entries: Any,
    *,
    actionable: bool,
    now_epoch_s: float,
) -> tuple[dict[str, Mapping[str, Any]], list[int]]:
    """Validate protection/action entries before they can influence ranking."""

    raw_entries, malformed_input = _materialize_iterable(entries)
    if malformed_input:
        return {}, [-1]
    result: dict[str, Mapping[str, Any]] = {}
    malformed_indexes: list[int] = []
    for index, entry in enumerate(raw_entries):
        if not isinstance(entry, Mapping):
            malformed_indexes.append(index)
            continue
        identifier = _entry_id(entry)
        if not isinstance(identifier, str) or not FULL_CONTAINER_ID.fullmatch(identifier):
            malformed_indexes.append(index)
            continue
        if identifier in result:
            malformed_indexes.append(index)
            continue
        if actionable:
            allowed_resources = entry.get("allowed_resources")
            allowed_resource_values = (
                allowed_resources
                if isinstance(allowed_resources, list)
                else []
            )
            expiry_reason = _actionable_expiry_reason(entry, now_epoch_s=now_epoch_s)
            if (
                entry.get("environment") != "local-disposable"
                or entry.get("action") != "graceful_stop"
                or not allowed_resource_values
                or any(_resource_kind(value) is None for value in allowed_resource_values)
                or expiry_reason == "DATA_INCOMPLETE"
            ):
                malformed_indexes.append(index)
                continue
        result[identifier] = entry
    return result, malformed_indexes


def _plan_id(
    event_id: str | None,
    resource_kind: str,
    target_id: str,
    config_digest: str | None,
) -> str:
    canonical = json.dumps(
        {
            "event_id": event_id or "",
            "resource_kind": resource_kind,
            "target_id": target_id,
            "config_digest": config_digest or "",
            "schema": EMERGENCY_SHEDDING_SCHEMA,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "es-v1-" + hashlib.sha256(canonical).hexdigest()[:24]


def _empty_decision(mode: str, execution: str, reasons: Iterable[str]) -> dict[str, Any]:
    return {
        "schema": EMERGENCY_SHEDDING_SCHEMA,
        "mode": mode,
        "action": "none",
        "execution": execution,
        "resource_kind": None,
        "target_id": None,
        "reason_codes": sorted(set(str(reason) for reason in reasons if reason)),
        "plan": None,
        "plans_count": 0,
    }


def build_emergency_shedding_decision(
    host_risks: Mapping[str, Mapping[str, Any]],
    candidates: Iterable[Mapping[str, Any]],
    *,
    registry: Iterable[Mapping[str, Any]] = (),
    protected_set: Iterable[Mapping[str, Any]] = (),
    actionable_set: Iterable[Mapping[str, Any]] = (),
    mode: str = "observe",
    policy: EmergencySheddingPolicy | None = None,
    allowed_actions: Iterable[str] = (),
    event_id: str | None = None,
    sample_id: str | None = None,
    config_digest: str | None = None,
    now_epoch_s: float | None = None,
) -> dict[str, Any]:
    """Rank one resource-specific TOP and return a fail-closed plan/result.

    ``candidates`` must represent the complete current Docker registry, not a
    pre-filtered list.  This is what allows the policy to distinguish a
    protected or unknown global TOP from a valid actionable TOP.
    """

    selected_policy = policy or EmergencySheddingPolicy()
    selected_policy_digest = emergency_policy_digest(selected_policy)
    safe_mode = mode if isinstance(mode, str) else "invalid"
    execution = "not_applicable" if safe_mode == "observe" else "not_executed"
    if not selected_policy.enabled:
        decision = _empty_decision(safe_mode, execution, ("EMERGENCY_SHEDDING_DISABLED",))
        return {"schema": EMERGENCY_SHEDDING_SCHEMA, "decision": decision, "active_resources": [], "rankings": {}}
    if not isinstance(mode, str) or mode not in {"observe", "simulate", "enforce"}:
        decision = _empty_decision(safe_mode, "not_executed", ("EMERGENCY_SHEDDING_SIMULATE_ONLY",))
        return {"schema": EMERGENCY_SHEDDING_SCHEMA, "decision": decision, "active_resources": [], "rankings": {}}
    current_epoch_s = time.time() if now_epoch_s is None else _number(now_epoch_s)
    if current_epoch_s is None:
        decision = _empty_decision(mode, execution, ("DATA_INCOMPLETE", "NO_EFFECTIVE_SHEDDING_TARGET"))
        return {"schema": EMERGENCY_SHEDDING_SCHEMA, "decision": decision, "active_resources": [], "rankings": {}}

    normalized_risks = {
        kind: _risk_mapping(value)
        for raw_kind, value in _mapping(host_risks).items()
        if (kind := _resource_kind(raw_kind)) is not None
    }
    active_resources = [
        resource
        for resource in selected_policy.resource_priority
        if _is_confirmed(normalized_risks.get(resource, {}))
    ]
    incomplete_active = [
        resource
        for resource in selected_policy.resource_priority
        if _risk_state(normalized_risks.get(resource, {})) in {"CRITICAL", "CRITICAL_CONFIRMED"}
        and not _sample_complete(normalized_risks.get(resource, {}), selected_policy)
    ]
    if incomplete_active:
        decision = _empty_decision(mode, execution, ("INCOMPLETE_SAMPLES",))
        decision["resource_kind"] = incomplete_active[0]
        return {
            "schema": EMERGENCY_SHEDDING_SCHEMA,
            "decision": decision,
            "active_resources": active_resources,
            "rankings": {},
        }
    if not active_resources:
        decision = _empty_decision(mode, execution, ("HOST_RISK_NOT_CONFIRMED",))
        return {"schema": EMERGENCY_SHEDDING_SCHEMA, "decision": decision, "active_resources": [], "rankings": {}}

    raw_candidate_values, candidate_input_invalid = _materialize_iterable(candidates)
    if candidate_input_invalid:
        decision = _empty_decision(mode, execution, ("DATA_INCOMPLETE", "NO_EFFECTIVE_SHEDDING_TARGET"))
        decision["malformed_candidate_input"] = True
        return {
            "schema": EMERGENCY_SHEDDING_SCHEMA,
            "decision": decision,
            "active_resources": active_resources,
            "rankings": {},
        }
    raw_candidates = [dict(item) for item in raw_candidate_values if isinstance(item, Mapping)]
    malformed_candidate_indexes = [
        index
        for index, candidate in enumerate(raw_candidate_values)
        if not isinstance(candidate, Mapping)
        or candidate.get("kind") != "container"
        or not isinstance(_entry_id(candidate), str)
    ]
    if malformed_candidate_indexes:
        decision = _empty_decision(mode, execution, ("DATA_INCOMPLETE", "NO_EFFECTIVE_SHEDDING_TARGET"))
        decision["malformed_candidate_indexes"] = malformed_candidate_indexes
        return {
            "schema": EMERGENCY_SHEDDING_SCHEMA,
            "decision": decision,
            "active_resources": active_resources,
            "rankings": {},
        }
    candidate_ids = [_entry_id(candidate) for candidate in raw_candidates if candidate.get("kind", "container") == "container"]
    duplicate_candidate_ids = sorted(
        {identifier for identifier in candidate_ids if identifier is not None and candidate_ids.count(identifier) > 1}
    )
    if duplicate_candidate_ids:
        decision = _empty_decision(mode, execution, ("DATA_INCOMPLETE", "NO_EFFECTIVE_SHEDDING_TARGET"))
        decision["duplicate_candidate_ids"] = duplicate_candidate_ids
        return {
            "schema": EMERGENCY_SHEDDING_SCHEMA,
            "decision": decision,
            "active_resources": active_resources,
            "rankings": {},
        }
    raw_registry, registry_input_invalid = _materialize_iterable(registry)
    if registry_input_invalid:
        decision = _empty_decision(mode, execution, ("DATA_INCOMPLETE", "NO_EFFECTIVE_SHEDDING_TARGET"))
        decision["malformed_registry_input"] = True
        return {
            "schema": EMERGENCY_SHEDDING_SCHEMA,
            "decision": decision,
            "active_resources": active_resources,
            "rankings": {},
        }
    malformed_registry_indexes = [
        index
        for index, item in enumerate(raw_registry)
        if not isinstance(item, Mapping)
        or item.get("kind") != "container"
        or not isinstance(_entry_id(item), str)
    ]
    if malformed_registry_indexes:
        decision = _empty_decision(mode, execution, ("DATA_INCOMPLETE", "NO_EFFECTIVE_SHEDDING_TARGET"))
        decision["malformed_registry_indexes"] = malformed_registry_indexes
        return {
            "schema": EMERGENCY_SHEDDING_SCHEMA,
            "decision": decision,
            "active_resources": active_resources,
            "rankings": {},
        }

    registry_map: dict[str, Mapping[str, Any]] = {}
    duplicate_registry_ids: set[str] = set()
    for item in raw_registry:
        if isinstance(item, Mapping):
            identifier = _entry_id(item)
            if isinstance(identifier, str) and identifier:
                if identifier in registry_map:
                    duplicate_registry_ids.add(identifier)
                registry_map[identifier] = item
    if not registry_map:
        decision = _empty_decision(mode, execution, ("INCOMPLETE_SAMPLES", "NO_EFFECTIVE_SHEDDING_TARGET"))
        return {
            "schema": EMERGENCY_SHEDDING_SCHEMA,
            "decision": decision,
            "active_resources": active_resources,
            "rankings": {},
        }
    if duplicate_registry_ids:
        decision = _empty_decision(mode, execution, ("DATA_INCOMPLETE", "NO_EFFECTIVE_SHEDDING_TARGET"))
        decision["duplicate_registry_ids"] = sorted(duplicate_registry_ids)
        return {
            "schema": EMERGENCY_SHEDDING_SCHEMA,
            "decision": decision,
            "active_resources": active_resources,
            "rankings": {},
        }

    protected, malformed_protected_indexes = _validated_entry_map(
        protected_set,
        actionable=False,
        now_epoch_s=current_epoch_s,
    )
    actionable, malformed_actionable_indexes = _validated_entry_map(
        actionable_set,
        actionable=True,
        now_epoch_s=current_epoch_s,
    )
    if malformed_protected_indexes or malformed_actionable_indexes:
        decision = _empty_decision(mode, execution, ("DATA_INCOMPLETE", "NO_EFFECTIVE_SHEDDING_TARGET"))
        if malformed_protected_indexes:
            decision["malformed_protected_set_indexes"] = malformed_protected_indexes
        if malformed_actionable_indexes:
            decision["malformed_actionable_set_indexes"] = malformed_actionable_indexes
        return {
            "schema": EMERGENCY_SHEDDING_SCHEMA,
            "decision": decision,
            "active_resources": active_resources,
            "rankings": {},
        }
    allowed_action_values, allowed_actions_invalid = _materialize_iterable(allowed_actions)
    if allowed_actions_invalid or any(not isinstance(value, str) for value in allowed_action_values):
        decision = _empty_decision(mode, execution, ("DATA_INCOMPLETE", "ACTION_NOT_ALLOWLISTED"))
        return {
            "schema": EMERGENCY_SHEDDING_SCHEMA,
            "decision": decision,
            "active_resources": active_resources,
            "rankings": {},
        }
    allowed_action_set = set(allowed_action_values)
    rankings: dict[str, list[dict[str, Any]]] = {}
    for resource in active_resources:
        rows: list[dict[str, Any]] = []
        for candidate in raw_candidates:
            identifier = _entry_id(candidate)
            if not isinstance(identifier, str):
                continue
            if candidate.get("kind", "container") != "container":
                continue
            status = str(candidate.get("status") or "unknown").lower()
            running = candidate.get("running") is True or status in {"running", "restarting"}
            if not running:
                continue
            contribution = _contribution(candidate, resource)
            if contribution is None:
                contribution = _number(_mapping(candidate.get("resource_contributions")).get(resource))
            identity_reason = _identity_reason(candidate, registry_map)
            writer_ok = True
            if resource == "disk_capacity":
                writer_ok = candidate.get("writer_evidence") is True or candidate.get("capacity_writer") is True
            rows.append(
                {
                    "id": identifier,
                    "name": candidate.get("name"),
                    "contribution_percent": contribution,
                    "protected": identifier in protected,
                    "actionable": identifier in actionable,
                    "identity_reason": identity_reason,
                    "writer_evidence": writer_ok,
                }
            )
        if any(row["contribution_percent"] is None for row in rows) or not rows:
            decision = _empty_decision(mode, execution, ("INCOMPLETE_SAMPLES", "NO_EFFECTIVE_SHEDDING_TARGET"))
            decision["resource_kind"] = resource
            rankings[resource] = sorted(rows, key=lambda row: str(row["id"]))
            return {
                "schema": EMERGENCY_SHEDDING_SCHEMA,
                "decision": decision,
                "active_resources": active_resources,
                "rankings": rankings,
            }
        rows.sort(key=lambda row: (-float(row["contribution_percent"]), str(row["id"])))
        rankings[resource] = rows

    # A mixed incident gets one decision only.  If active resources point to
    # different objects, or disk capacity has no explicit writer evidence,
    # keep the incident visible but do not let a memory/CPU ranking bypass the
    # unsafe disk channel.  The reserve-recovery path may act independently;
    # container stopping must remain manual in this situation.
    top_by_resource = {resource: rows[0] for resource, rows in rankings.items() if rows}
    top_ids = {row["id"] for row in top_by_resource.values()}
    if len(active_resources) > 1 and len(top_ids) > 1:
        decision = _empty_decision(mode, execution, ("MULTI_RESOURCE_AMBIGUOUS", "NO_EFFECTIVE_SHEDDING_TARGET"))
        decision["resource_kind"] = "multi_resource"
        decision["top_consumers"] = {resource: row["id"] for resource, row in top_by_resource.items()}
        return {"schema": EMERGENCY_SHEDDING_SCHEMA, "decision": decision, "active_resources": active_resources, "rankings": rankings}
    disk_top = top_by_resource.get("disk_capacity")
    if disk_top is not None and not disk_top["writer_evidence"]:
        decision = _empty_decision(mode, execution, ("CAPACITY_WRITER_EVIDENCE_MISSING", "NO_EFFECTIVE_SHEDDING_TARGET"))
        decision["resource_kind"] = "disk_capacity"
        decision["top_consumer"] = disk_top["id"]
        return {"schema": EMERGENCY_SHEDDING_SCHEMA, "decision": decision, "active_resources": active_resources, "rankings": rankings}

    resource = active_resources[0]
    ranking = rankings[resource]
    identity_failures = [row for row in ranking if row["identity_reason"]]
    if identity_failures:
        changed = any(row["identity_reason"] == "TARGET_IDENTITY_CHANGED" for row in identity_failures)
        primary_reason = "TARGET_IDENTITY_CHANGED" if changed else "DATA_INCOMPLETE"
        decision = _empty_decision(mode, execution, (primary_reason, "NO_EFFECTIVE_SHEDDING_TARGET"))
        decision["resource_kind"] = resource
        decision["identity_failures"] = [row["id"] for row in identity_failures]
        return {
            "schema": EMERGENCY_SHEDDING_SCHEMA,
            "decision": decision,
            "active_resources": active_resources,
            "rankings": rankings,
        }
    top = ranking[0]
    second = ranking[1] if len(ranking) > 1 else None
    if second is not None and top["contribution_percent"] == second["contribution_percent"]:
        decision = _empty_decision(mode, execution, ("AMBIGUOUS_TOP_CONSUMER", "NO_EFFECTIVE_SHEDDING_TARGET"))
        decision["resource_kind"] = resource
        return {"schema": EMERGENCY_SHEDDING_SCHEMA, "decision": decision, "active_resources": active_resources, "rankings": rankings}
    if top["protected"]:
        decision = _empty_decision(mode, execution, ("PROTECTED_TOP_CONSUMER", "NO_EFFECTIVE_SHEDDING_TARGET"))
        decision["resource_kind"] = resource
        decision["top_consumer"] = top["id"]
        return {"schema": EMERGENCY_SHEDDING_SCHEMA, "decision": decision, "active_resources": active_resources, "rankings": rankings}
    if not top["actionable"]:
        decision = _empty_decision(mode, execution, ("UNKNOWN_TOP_CONSUMER", "NO_EFFECTIVE_SHEDDING_TARGET"))
        decision["resource_kind"] = resource
        decision["top_consumer"] = top["id"]
        return {"schema": EMERGENCY_SHEDDING_SCHEMA, "decision": decision, "active_resources": active_resources, "rankings": rankings}
    if top["identity_reason"]:
        reason = top["identity_reason"]
        decision = _empty_decision(mode, execution, (reason, "NO_EFFECTIVE_SHEDDING_TARGET"))
        decision["resource_kind"] = resource
        decision["top_consumer"] = top["id"]
        return {"schema": EMERGENCY_SHEDDING_SCHEMA, "decision": decision, "active_resources": active_resources, "rankings": rankings}
    if resource == "disk_capacity" and not top["writer_evidence"]:
        decision = _empty_decision(mode, execution, ("CAPACITY_WRITER_EVIDENCE_MISSING", "NO_EFFECTIVE_SHEDDING_TARGET"))
        decision["resource_kind"] = resource
        return {"schema": EMERGENCY_SHEDDING_SCHEMA, "decision": decision, "active_resources": active_resources, "rankings": rankings}
    if float(top["contribution_percent"]) < selected_policy.min_host_contribution_percent:
        decision = _empty_decision(mode, execution, ("CONTRIBUTION_BELOW_MINIMUM", "NO_EFFECTIVE_SHEDDING_TARGET"))
        decision["resource_kind"] = resource
        decision["top_consumer"] = top["id"]
        return {"schema": EMERGENCY_SHEDDING_SCHEMA, "decision": decision, "active_resources": active_resources, "rankings": rankings}
    if mode == "observe":
        # Observe may expose the ranked candidate, but it must not depend on
        # an action allowlist and must never look like an executable plan.
        decision = _empty_decision(mode, "not_applicable", ("OBSERVE_ONLY",))
        decision["resource_kind"] = resource
        decision["top_consumer"] = top["id"]
        return {
            "schema": EMERGENCY_SHEDDING_SCHEMA,
            "decision": decision,
            "active_resources": active_resources,
            "rankings": rankings,
        }
    entry = actionable[top["id"]]
    expiry_reason = _actionable_expiry_reason(entry, now_epoch_s=current_epoch_s)
    if expiry_reason:
        decision = _empty_decision(mode, execution, (expiry_reason, "NO_EFFECTIVE_SHEDDING_TARGET"))
        decision["resource_kind"] = resource
        decision["top_consumer"] = top["id"]
        return {"schema": EMERGENCY_SHEDDING_SCHEMA, "decision": decision, "active_resources": active_resources, "rankings": rankings}
    allowed_resources = _mapping(entry).get("allowed_resources", SUPPORTED_RESOURCES)
    normalized_allowed_resources = {_resource_kind(value) for value in _list(allowed_resources)}
    if resource not in normalized_allowed_resources:
        decision = _empty_decision(mode, execution, ("RESOURCE_NOT_ALLOWED", "NO_EFFECTIVE_SHEDDING_TARGET"))
        decision["resource_kind"] = resource
        return {"schema": EMERGENCY_SHEDDING_SCHEMA, "decision": decision, "active_resources": active_resources, "rankings": rankings}
    allowed_entry_action = _mapping(entry).get("action", "graceful_stop")
    if allowed_entry_action != "graceful_stop" or "graceful_stop" not in allowed_action_set:
        decision = _empty_decision(mode, execution, ("ACTION_NOT_ALLOWLISTED", "NO_EFFECTIVE_SHEDDING_TARGET"))
        decision["resource_kind"] = resource
        return {"schema": EMERGENCY_SHEDDING_SCHEMA, "decision": decision, "active_resources": active_resources, "rankings": rankings}

    candidate = next(item for item in raw_candidates if _entry_id(item) == top["id"])
    _, created_at, cgroup_path, cgroup_inode = _identity_fields(candidate)
    plan = EmergencySheddingPlan(
        plan_id=_plan_id(event_id, resource, top["id"], config_digest),
        resource_kind=resource,
        target_id=top["id"],
        target_created_at=created_at or "",
        target_cgroup_path=cgroup_path or "",
        target_cgroup_inode=cgroup_inode or 0,
        target_labels=_labels(candidate.get("labels")),
        event_id=event_id or "",
        sample_id=sample_id or "",
        policy_digest=selected_policy_digest,
        config_digest=config_digest or "",
        expires_at=str(entry.get("expires_at") or ""),
    )
    decision = {
        "schema": EMERGENCY_SHEDDING_SCHEMA,
        "mode": mode,
        "action": "graceful_stop" if mode in {"simulate", "enforce"} else "none",
        "execution": "not_executed",
        "resource_kind": resource,
        "target_id": top["id"],
        "top_consumer": top["id"],
        "reason_codes": ["PLAN_GENERATED", "TOP_ACTIONABLE_CONSUMER"],
        "plan": plan.as_dict() if mode in {"simulate", "enforce"} else None,
        "plans_count": 1 if mode in {"simulate", "enforce"} else 0,
    }
    return {
        "schema": EMERGENCY_SHEDDING_SCHEMA,
        "decision": decision,
        "active_resources": active_resources,
        "rankings": rankings,
        "plan": plan.as_dict() if mode in {"simulate", "enforce"} else None,
    }


__all__ = [
    "EMERGENCY_SHEDDING_SCHEMA",
    "EmergencySheddingPlan",
    "EmergencySheddingPolicy",
    "SUPPORTED_RESOURCES",
    "build_emergency_shedding_decision",
    "emergency_policy_digest",
    "emergency_shedding_policy_from_config",
    "policy_from_mapping",
]
