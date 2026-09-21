"""Fresh action-time revalidation for the Guardian execution boundary.

The Observer event that produced an intent is immutable evidence, not a current
permission.  This module accepts a separately collected event and verifies the
minimum facts required immediately before an adapter can be called.  It does
not execute Docker commands and deliberately fails closed on incomplete
collector output.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from .guardian_emergency_shedding import EmergencySheddingPolicy, emergency_policy_digest


FRESH_REVALIDATION_SCHEMA = "guardian.fresh_revalidation.v1"
_FULL_ID = re.compile(r"^[0-9a-f]{64}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class FreshRevalidationProvider(Protocol):
    def __call__(self, intent: Any, event: Mapping[str, Any]) -> "FreshRevalidation":
        """Collect and validate an independent action-time observation."""


@dataclass(frozen=True)
class FreshRevalidation:
    """Bounded result of one independent action-time observation."""

    accepted: bool
    reason_codes: tuple[str, ...]
    fresh_event_id: str | None = None
    fresh_sample_id: str | None = None
    fresh_observed_monotonic_ns: int | None = None
    registry: tuple[Mapping[str, Any], ...] = ()
    event: Mapping[str, Any] = field(default_factory=dict, repr=False, compare=False)
    schema: str = FRESH_REVALIDATION_SCHEMA

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "accepted": self.accepted,
            "reason_codes": list(self.reason_codes),
            "fresh_event_id": self.fresh_event_id,
            "fresh_sample_id": self.fresh_sample_id,
            "fresh_observed_monotonic_ns": self.fresh_observed_monotonic_ns,
            "registry": [dict(item) for item in self.registry],
        }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _labels(value: Any) -> tuple[tuple[str, str], ...] | None:
    if not isinstance(value, Mapping):
        return None
    pairs: list[tuple[str, str]] = []
    for key, raw in value.items():
        if not isinstance(key, str) or not isinstance(raw, str):
            return None
        if not key or len(key) > 256 or len(raw) > 1024:
            return None
        pairs.append((key, raw))
    return tuple(sorted(pairs))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _failure(
    *reasons: str,
    event_id: str | None = None,
    sample_id: str | None = None,
    observed_monotonic_ns: int | None = None,
    event: Mapping[str, Any] | None = None,
) -> FreshRevalidation:
    return FreshRevalidation(
        False,
        tuple(dict.fromkeys(reason for reason in reasons if reason)),
        event_id,
        sample_id,
        observed_monotonic_ns,
        (),
        event or {},
    )


def revalidate_observer_event(
    intent: Any,
    original_event: Mapping[str, Any],
    fresh_event: Mapping[str, Any],
    *,
    policy: EmergencySheddingPolicy,
    expected_config_digest: str | None = None,
    now_epoch_s: float | None = None,
    now_monotonic_ns: int | None = None,
) -> FreshRevalidation:
    """Validate fresh host/object facts without reusing the cached event.

    The function accepts ``Any`` for ``intent`` to keep this module independent
    from the Coordinator dataclass and avoid an import cycle.  The required
    attributes are checked explicitly and a malformed contract is a refusal.
    """

    if not isinstance(original_event, Mapping) or not isinstance(fresh_event, Mapping):
        return _failure("fresh_event_mapping_required", event=fresh_event if isinstance(fresh_event, Mapping) else {})

    original_monotonic = getattr(intent, "observed_monotonic_ns", None)
    target_id = getattr(intent, "target_id", None)
    target_created_at = getattr(intent, "target_created_at", None)
    target_cgroup_path = getattr(intent, "target_cgroup_path", None)
    target_cgroup_inode = getattr(intent, "target_cgroup_inode", None)
    target_labels = getattr(intent, "target_labels", ())
    intent_event_id = getattr(intent, "event_id", None)
    intent_sample_id = getattr(intent, "sample_id", None)
    intent_host_id = getattr(intent, "host_id", None)
    intent_policy_digest = getattr(intent, "policy_digest", None)
    intent_config_digest = getattr(intent, "config_digest", None)
    intent_expires_at = _number(getattr(intent, "expires_at", None))

    if isinstance(original_monotonic, bool) or not isinstance(original_monotonic, int) or original_monotonic <= 0:
        return _failure("original_monotonic_timestamp_missing", event=fresh_event)
    if not isinstance(target_id, str) or not _FULL_ID.fullmatch(target_id):
        return _failure("target_full_id_required", event=fresh_event)
    if not isinstance(target_labels, tuple):
        return _failure("target_labels_invalid", event=fresh_event)
    if intent_expires_at is None:
        return _failure("intent_expiry_invalid", event=fresh_event)

    epoch = time.time() if now_epoch_s is None else _number(now_epoch_s)
    monotonic = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
    if epoch is None or isinstance(monotonic, bool) or not isinstance(monotonic, int) or monotonic <= 0:
        return _failure("revalidation_clock_invalid", event=fresh_event)
    if epoch >= intent_expires_at:
        return _failure("intent_expired", event=fresh_event)

    fresh_event_id = fresh_event.get("event_id")
    fresh_sample_id = fresh_event.get("sample_id")
    fresh_monotonic = fresh_event.get("observed_monotonic_ns")
    if isinstance(fresh_monotonic, bool) or not isinstance(fresh_monotonic, int) or fresh_monotonic <= 0:
        fresh_monotonic = _mapping(fresh_event.get("signals")).get("observed_monotonic_ns")
    if not isinstance(fresh_event_id, str) or not fresh_event_id:
        return _failure("fresh_event_id_missing", event=fresh_event)
    if fresh_event.get("host_id") != intent_host_id:
        return _failure("fresh_host_identity_changed", event_id=fresh_event_id, event=fresh_event)
    if not isinstance(fresh_sample_id, str) or not fresh_sample_id:
        fresh_sample_id = _mapping(fresh_event.get("signals")).get("sample_id")
    if not isinstance(fresh_sample_id, str) or not fresh_sample_id:
        return _failure("fresh_sample_id_missing", event_id=fresh_event_id, event=fresh_event)
    if fresh_event_id == intent_event_id or fresh_sample_id == intent_sample_id:
        return _failure(
            "fresh_sample_reused",
            event_id=fresh_event_id,
            sample_id=fresh_sample_id,
            event=fresh_event,
        )
    if isinstance(fresh_monotonic, bool) or not isinstance(fresh_monotonic, int) or fresh_monotonic <= original_monotonic:
        return _failure(
            "fresh_monotonic_timestamp_stale",
            event_id=fresh_event_id,
            sample_id=fresh_sample_id,
            observed_monotonic_ns=fresh_monotonic if isinstance(fresh_monotonic, int) else None,
            event=fresh_event,
        )
    if fresh_monotonic > monotonic:
        return _failure(
            "fresh_monotonic_timestamp_in_future",
            event_id=fresh_event_id,
            sample_id=fresh_sample_id,
            observed_monotonic_ns=fresh_monotonic,
            event=fresh_event,
        )

    evidence = _mapping(fresh_event.get("evidence"))
    runtime_gate = _mapping(evidence.get("runtime_gate"))
    if runtime_gate.get("execution_allowed") is False:
        return _failure("fresh_runtime_gate_closed", event_id=fresh_event_id, sample_id=fresh_sample_id, observed_monotonic_ns=fresh_monotonic, event=fresh_event)
    fresh_config_digest = evidence.get("config_digest")
    fresh_policy_digest = evidence.get("policy_digest")
    required_config_digest = expected_config_digest or intent_config_digest
    expected_policy = emergency_policy_digest(policy)
    if (
        not isinstance(fresh_config_digest, str)
        or not _DIGEST.fullmatch(fresh_config_digest)
        or not isinstance(required_config_digest, str)
        or fresh_config_digest != required_config_digest
        or fresh_config_digest != intent_config_digest
    ):
        return _failure("fresh_config_digest_mismatch", event_id=fresh_event_id, sample_id=fresh_sample_id, observed_monotonic_ns=fresh_monotonic, event=fresh_event)
    if (
        not isinstance(fresh_policy_digest, str)
        or not _DIGEST.fullmatch(fresh_policy_digest)
        or fresh_policy_digest != intent_policy_digest
        or fresh_policy_digest != expected_policy
    ):
        return _failure("fresh_policy_digest_mismatch", event_id=fresh_event_id, sample_id=fresh_sample_id, observed_monotonic_ns=fresh_monotonic, event=fresh_event)

    resource_kind = getattr(intent, "resource_kind", None)
    evaluations = _mapping(fresh_event.get("resource_evaluations"))
    evaluation = _mapping(evaluations.get(resource_kind))
    risk = _mapping(evaluation.get("risk"))
    risk_state = str(risk.get("state") or "").upper().replace("-", "_")
    quality_flags = risk.get("quality_flags")
    if (
        risk_state != "CRITICAL_CONFIRMED"
        or risk.get("danger_confirmed") is not True
        or risk.get("sample_complete") is not True
        or risk.get("quality_status") != "ok"
        or not isinstance(quality_flags, list)
        or quality_flags
    ):
        return _failure("fresh_risk_not_confirmed", event_id=fresh_event_id, sample_id=fresh_sample_id, observed_monotonic_ns=fresh_monotonic, event=fresh_event)

    emergency = _mapping(fresh_event.get("emergency_shedding"))
    decision = _mapping(emergency.get("decision"))
    fresh_action = decision.get("action")
    if fresh_action != "graceful_stop":
        return _failure("fresh_action_not_allowed", event_id=fresh_event_id, sample_id=fresh_sample_id, observed_monotonic_ns=fresh_monotonic, event=fresh_event)

    signals = _mapping(fresh_event.get("signals"))
    registry_block = signals.get("object_registry")
    if not isinstance(registry_block, Mapping):
        registry_block = fresh_event.get("object_registry")
    if not isinstance(registry_block, Mapping) or registry_block.get("status") != "ok":
        return _failure("fresh_object_registry_unavailable", event_id=fresh_event_id, sample_id=fresh_sample_id, observed_monotonic_ns=fresh_monotonic, event=fresh_event)
    raw_objects = registry_block.get("objects")
    if not isinstance(raw_objects, list) or not raw_objects:
        return _failure("fresh_object_registry_incomplete", event_id=fresh_event_id, sample_id=fresh_sample_id, observed_monotonic_ns=fresh_monotonic, event=fresh_event)
    registry = tuple(item for item in raw_objects if isinstance(item, Mapping))
    matches = [item for item in registry if item.get("id") == target_id]
    if len(matches) != 1:
        return _failure("fresh_target_identity_changed", event_id=fresh_event_id, sample_id=fresh_sample_id, observed_monotonic_ns=fresh_monotonic, event=fresh_event)
    identity = matches[0]
    labels = _labels(identity.get("labels"))
    mapping_errors = identity.get("mapping_errors")
    if (
        identity.get("created_at") != target_created_at
        or identity.get("cgroup_path") != target_cgroup_path
        or identity.get("cgroup_inode") != target_cgroup_inode
        or labels is None
        or labels != target_labels
        or identity.get("mapping_confidence") != "high"
        or not isinstance(mapping_errors, list)
        or mapping_errors
    ):
        return _failure("fresh_target_identity_changed", event_id=fresh_event_id, sample_id=fresh_sample_id, observed_monotonic_ns=fresh_monotonic, event=fresh_event)
    if not isinstance(identity.get("created_at"), str) or not identity.get("created_at"):
        return _failure("fresh_created_at_missing", event_id=fresh_event_id, sample_id=fresh_sample_id, observed_monotonic_ns=fresh_monotonic, event=fresh_event)
    if not isinstance(identity.get("cgroup_path"), str) or not identity["cgroup_path"].startswith("/"):
        return _failure("fresh_cgroup_path_missing", event_id=fresh_event_id, sample_id=fresh_sample_id, observed_monotonic_ns=fresh_monotonic, event=fresh_event)
    if isinstance(identity.get("cgroup_inode"), bool) or not isinstance(identity.get("cgroup_inode"), int) or identity["cgroup_inode"] <= 0:
        return _failure("fresh_cgroup_inode_missing", event_id=fresh_event_id, sample_id=fresh_sample_id, observed_monotonic_ns=fresh_monotonic, event=fresh_event)
    if str(identity.get("status") or "").lower() not in {"running", "restarting"} and identity.get("running") is not True:
        return _failure("fresh_target_not_running", event_id=fresh_event_id, sample_id=fresh_sample_id, observed_monotonic_ns=fresh_monotonic, event=fresh_event)

    return FreshRevalidation(
        True,
        ("fresh_revalidation_passed",),
        fresh_event_id,
        fresh_sample_id,
        fresh_monotonic,
        registry,
        fresh_event,
    )


__all__ = [
    "FRESH_REVALIDATION_SCHEMA",
    "FreshRevalidation",
    "FreshRevalidationProvider",
    "revalidate_observer_event",
]
