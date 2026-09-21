"""Local functional convergence for the Guardian incident path.

This module is the only local bridge between an Observer/Emergency Shedding
event and an action adapter.  It intentionally keeps the boundaries explicit:

``event -> plan -> notification outbox -> ActionIntent -> capability/broker
-> adapter -> verification -> cooldown/manual handoff``

The default adapter is a fake adapter.  No Docker or systemd command is
discovered or invoked here.  A real adapter can only be supplied by a caller
that has already crossed the existing action authorization boundary, and
``enforce`` additionally requires a capability previously registered in the
durable state store.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol

from .guardian_actions import (
    ActionDenied,
    ActionRequest,
    ActionResult,
    Authorization,
    CONTAINER_ID,
    validate_request,
)
from .guardian_emergency_shedding import (
    EMERGENCY_SHEDDING_SCHEMA,
    EmergencySheddingPolicy,
    SUPPORTED_RESOURCES,
    emergency_policy_digest,
)
from .guardian_notification_outbox import DurableNotificationOutbox, NotificationOutboxError
from .guardian_notifications import FakeNotificationSink, NOTIFIABLE_STATES
from .guardian_recovery import (
    BusinessRecoveryObservation,
    BusinessRecoveryPolicy,
    HostRecoveryObservation,
    HostRecoveryPolicy,
    TwoLayerRecoveryResult,
    assess_two_layer_recovery,
)
from .guardian_revalidation import FreshRevalidation, FreshRevalidationProvider
from .guardian_revalidation import revalidate_observer_event
from .guardian_state import GuardianStateStore, StateStoreError


ACTION_INTENT_SCHEMA = "guardian.action_intent.v1"
COORDINATOR_SCHEMA = "guardian.coordinator.result.v1"
GLOBAL_COOLDOWN_OBJECT = "__guardian_global__"
SUPPORTED_MODES = ("observe", "simulate", "enforce")
_FULL_ID = re.compile(r"^[0-9a-f]{64}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_TEXT_LENGTH = 512
_MAX_REASON_CODES = 32
_MAX_REASON_LENGTH = 128

# The lowercase ``state`` field remains a compatibility projection for
# existing callers.  ``semantic_state`` is the canonical incident outcome.
INCIDENT_STATES = (
    "NORMAL",
    "SUSPECTED",
    "CRITICAL_CONFIRMED",
    "PLANNED",
    "AUTHORIZED",
    "EXECUTING",
    "VERIFYING",
    "MITIGATED",
    "NOT_MITIGATED",
    "MANUAL_HANDOFF",
    "SIMULATED_PLAN_COMPLETE",
)


class ActionIntentError(ValueError):
    """Raised when a plan cannot be converted without losing safety fields."""


class CoordinatorError(RuntimeError):
    """Raised for durable coordinator failures that must fail closed."""


def _event_semantic_state(event: Mapping[str, Any]) -> str:
    state = str(event.get("state") or "").strip().lower()
    if state in {"normal", "recovered"}:
        return "NORMAL"
    if state in {"critical_confirmed", "critical-confirmed"}:
        return "CRITICAL_CONFIRMED"
    if state in {"warning", "critical", "suspected"}:
        return "SUSPECTED"
    return "MANUAL_HANDOFF"


def _required_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActionIntentError(f"{field}_required")
    normalized = value.strip()
    if len(normalized) > _MAX_TEXT_LENGTH:
        raise ActionIntentError(f"{field}_too_long")
    return normalized


def _positive_int(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ActionIntentError(f"{field}_invalid")
    return value


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ActionIntentError(f"{field}_invalid")
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ActionIntentError(f"{field}_invalid") from exc
    if not math.isfinite(result):
        raise ActionIntentError(f"{field}_invalid")
    return result


def _parse_expiry(value: Any, field: str) -> float:
    raw = _required_string(value, field)
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        parsed = dt.datetime.fromisoformat(normalized)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ActionIntentError(f"{field}_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ActionIntentError(f"{field}_timezone_required")
    return parsed.timestamp()


def _idempotency_key(host_id: str, target_id: str, action: str, event_id: str) -> str:
    raw = "\x00".join((host_id, target_id, action, event_id)).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _labels(value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping):
        return ()
    pairs: list[tuple[str, str]] = []
    for key, raw in value.items():
        if not isinstance(key, str) or not isinstance(raw, str):
            raise ActionIntentError("target_labels_invalid")
        if not key or len(key) > 256 or len(raw) > 1024:
            raise ActionIntentError("target_labels_invalid")
        pairs.append((key, raw))
    return tuple(sorted(pairs))


def _extract_plan(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    emergency = _mapping(event.get("emergency_shedding"))
    plan = emergency.get("plan")
    if isinstance(plan, Mapping):
        return plan
    decision = _mapping(emergency.get("decision"))
    plan = decision.get("plan")
    if isinstance(plan, Mapping):
        return plan
    plan = _mapping(event.get("decision")).get("plan")
    return plan if isinstance(plan, Mapping) else None


@dataclass(frozen=True)
class ActionIntent:
    """Versioned durable contract between a plan and the action broker."""

    plan_id: str
    event_id: str
    sample_id: str
    observed_monotonic_ns: int
    host_id: str
    incident_id: str
    resource_kind: str
    target_id: str
    target_created_at: str
    target_cgroup_path: str
    target_cgroup_inode: int
    target_labels: tuple[tuple[str, str], ...]
    action: str
    reason_codes: tuple[str, ...]
    policy_digest: str
    config_digest: str
    idempotency_key: str
    issued_at: float
    expires_at: float
    timeout_seconds: int = 30
    schema: str = ACTION_INTENT_SCHEMA

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "plan_id": self.plan_id,
            "event_id": self.event_id,
            "sample_id": self.sample_id,
            "observed_monotonic_ns": self.observed_monotonic_ns,
            "host_id": self.host_id,
            "incident_id": self.incident_id,
            "resource_kind": self.resource_kind,
            "target": {
                "kind": "container",
                "id": self.target_id,
                "created_at": self.target_created_at,
                "cgroup_path": self.target_cgroup_path,
                "cgroup_inode": self.target_cgroup_inode,
                "labels": {key: value for key, value in self.target_labels},
            },
            "action": self.action,
            "reason_codes": list(self.reason_codes),
            "policy_digest": self.policy_digest,
            "config_digest": self.config_digest,
            "idempotency_key": self.idempotency_key,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "timeout_seconds": self.timeout_seconds,
        }


def action_intent_from_event(
    event: Mapping[str, Any],
    *,
    expected_policy_digest: str | None = None,
    now_epoch_s: float | None = None,
    plan_ttl_seconds: float = 30.0,
) -> ActionIntent:
    """Convert exactly one Emergency Shedding plan into a strict intent.

    The conversion is deliberately independent from the executor.  A missing
    sample, identity field, digest, or expiry is a refusal rather than a best-
    effort default.
    """

    if not isinstance(event, Mapping):
        raise ActionIntentError("event_mapping_required")
    plan = _extract_plan(event)
    if plan is None:
        raise ActionIntentError("plan_required")
    if plan.get("schema") != EMERGENCY_SHEDDING_SCHEMA:
        raise ActionIntentError("plan_schema_invalid")

    event_id = _required_string(event.get("event_id"), "event_id")
    event_state = _required_string(event.get("state"), "event_state").lower()
    if event_state not in {"warning", "critical", "critical_confirmed"}:
        raise ActionIntentError("event_not_actionable")
    sample_id = event.get("sample_id")
    if not isinstance(sample_id, str) or not sample_id:
        sample_id = _mapping(event.get("signals")).get("sample_id")
    sample_id = _required_string(sample_id, "sample_id")
    host_id = _required_string(event.get("host_id"), "host_id")
    incident_id = _required_string(event.get("incident_id") or event_id, "incident_id")

    observed_monotonic_ns = event.get("observed_monotonic_ns")
    if isinstance(observed_monotonic_ns, bool) or not isinstance(observed_monotonic_ns, int) or observed_monotonic_ns <= 0:
        observed_monotonic_ns = _mapping(event.get("signals")).get("observed_monotonic_ns")
    _positive_int(observed_monotonic_ns, "observed_monotonic_ns")

    plan_id = _required_string(plan.get("plan_id"), "plan_id")
    plan_event_id = plan.get("event_id")
    if plan_event_id and plan_event_id != event_id:
        raise ActionIntentError("plan_event_id_mismatch")
    plan_sample_id = plan.get("sample_id")
    if plan_sample_id and plan_sample_id != sample_id:
        raise ActionIntentError("plan_sample_id_mismatch")

    resource_kind = str(plan.get("resource_kind") or "")
    if resource_kind not in SUPPORTED_RESOURCES:
        raise ActionIntentError("resource_kind_invalid")
    action = _required_string(plan.get("action"), "action")
    if action != "graceful_stop":
        raise ActionIntentError("only_graceful_stop_is_supported")
    if plan.get("root_cause_claimed") is not False:
        raise ActionIntentError("root_cause_claim_not_allowed")

    target = _mapping(plan.get("target"))
    target_id = _required_string(plan.get("target_id"), "plan_target_id")
    if target.get("id") != target_id or not _FULL_ID.fullmatch(target_id):
        raise ActionIntentError("target_full_id_required")
    target_created_at = _required_string(target.get("created_at"), "target_created_at")
    target_cgroup_path = _required_string(target.get("cgroup_path"), "target_cgroup_path")
    if not target_cgroup_path.startswith("/"):
        raise ActionIntentError("target_cgroup_path_invalid")
    target_cgroup_inode = _positive_int(target.get("cgroup_inode"), "target_cgroup_inode")
    target_labels = _labels(target.get("labels", {}))

    evidence = _mapping(event.get("evidence"))
    config_digest = plan.get("config_digest") or evidence.get("config_digest")
    config_digest = _required_string(config_digest, "config_digest")
    if not _DIGEST.fullmatch(config_digest):
        raise ActionIntentError("config_digest_invalid")
    policy_digest = plan.get("policy_digest") or evidence.get("policy_digest")
    policy_digest = _required_string(policy_digest, "policy_digest")
    if not _DIGEST.fullmatch(policy_digest):
        raise ActionIntentError("policy_digest_invalid")
    if expected_policy_digest is not None and policy_digest != expected_policy_digest:
        raise ActionIntentError("policy_digest_mismatch")

    raw_reasons = _mapping(event.get("decision")).get("reason_codes")
    if not isinstance(raw_reasons, list):
        raw_reasons = _mapping(_mapping(event.get("emergency_shedding")).get("decision")).get("reason_codes")
    if (
        not isinstance(raw_reasons, list)
        or not raw_reasons
        or len(raw_reasons) > _MAX_REASON_CODES
        or any(
            not isinstance(item, str)
            or not item
            or len(item.strip()) > _MAX_REASON_LENGTH
            for item in raw_reasons
        )
    ):
        raise ActionIntentError("reason_codes_required")
    reason_codes = tuple(dict.fromkeys(raw_reasons))

    issued_at = time.time() if now_epoch_s is None else _finite_number(now_epoch_s, "issued_at")
    ttl = _finite_number(plan_ttl_seconds, "plan_ttl_seconds")
    if not 0 < ttl <= 300:
        raise ActionIntentError("plan_ttl_out_of_bounds")
    policy_expires_at = _parse_expiry(plan.get("expires_at"), "plan_expires_at")
    if policy_expires_at <= issued_at:
        raise ActionIntentError("plan_expired")
    expires_at = min(policy_expires_at, issued_at + ttl)
    if expires_at <= issued_at:
        raise ActionIntentError("intent_expiry_invalid")

    timeout_value = plan.get("timeout_seconds", 30)
    if isinstance(timeout_value, bool) or not isinstance(timeout_value, int) or not 1 <= timeout_value <= 120:
        timeout_value = 30
    idempotency_key = _idempotency_key(host_id, target_id, action, event_id)
    supplied_key = plan.get("idempotency_key")
    if supplied_key is not None and supplied_key != idempotency_key:
        raise ActionIntentError("idempotency_key_mismatch")
    return ActionIntent(
        plan_id=plan_id,
        event_id=event_id,
        sample_id=sample_id,
        observed_monotonic_ns=observed_monotonic_ns,
        host_id=host_id,
        incident_id=incident_id,
        resource_kind=resource_kind,
        target_id=target_id,
        target_created_at=target_created_at,
        target_cgroup_path=target_cgroup_path,
        target_cgroup_inode=target_cgroup_inode,
        target_labels=target_labels,
        action=action,
        reason_codes=reason_codes,
        policy_digest=policy_digest,
        config_digest=config_digest,
        idempotency_key=idempotency_key,
        issued_at=issued_at,
        expires_at=expires_at,
        timeout_seconds=timeout_value,
    )


class ActionAdapter(Protocol):
    def execute(self, request: ActionRequest, now: float | None = None) -> ActionResult:
        """Execute one already-brokered request."""


class VerificationProvider(Protocol):
    def __call__(self, intent: ActionIntent, result: ActionResult) -> TwoLayerRecoveryResult:
        """Classify host mitigation and business recovery."""


class FakeActionAdapter:
    """Deterministic local adapter used by simulate and development tests."""

    OUTCOMES = {"success", "failure", "timeout", "rejected", "not_executed"}

    def __init__(self, outcome: str = "success") -> None:
        if outcome not in self.OUTCOMES:
            raise ValueError("fake_adapter_outcome_invalid")
        self.outcome = outcome
        self.requests: list[ActionRequest] = []

    def execute(self, request: ActionRequest, now: float | None = None) -> ActionResult:
        self.requests.append(request)
        if self.outcome == "rejected":
            raise ActionDenied("fake_adapter_rejected")
        if self.outcome == "not_executed":
            return ActionResult(False, request.action, request.target_id, None, reason="fake_not_executed")
        if self.outcome == "failure":
            return ActionResult(True, request.action, request.target_id, 1, stderr="fake_failure", reason="fake_failure")
        if self.outcome == "timeout":
            return ActionResult(True, request.action, request.target_id, None, stderr="executor_timeout", reason="executor_timeout")
        return ActionResult(True, request.action, request.target_id, 0, stdout="fake_success", reason="fake_success")


def default_verification(intent: ActionIntent, result: ActionResult) -> TwoLayerRecoveryResult:
    """Use deterministic local evidence for the fake adapter only."""

    successful_stop = result.executed and result.returncode == 0
    host = HostRecoveryObservation(
        before_available_percent=1.0,
        after_available_percent=5.0 if successful_stop else 1.0,
        before_psi_full_avg10=1.0,
        after_psi_full_avg10=0.0 if successful_stop else 1.0,
        before_oom_events=0,
        after_oom_events=0,
        after_risk_state="recovered" if successful_stop else "critical",
        observed_after_seconds=1.0,
    )
    business = BusinessRecoveryObservation(
        target_id=intent.target_id,
        target_present=False,
        target_running=False,
        health_status="exited",
        probe_ok=False,
        observed_after_seconds=1.0,
    )
    return assess_two_layer_recovery(
        HostRecoveryPolicy(),
        host,
        BusinessRecoveryPolicy(action=intent.action),
        business,
        expected_target_id=intent.target_id,
    )


def simulation_verification() -> TwoLayerRecoveryResult:
    """Represent a fake plan exercise without claiming host or business recovery."""

    return TwoLayerRecoveryResult(
        host_state="NOT_APPLICABLE",
        business_state="NOT_APPLICABLE",
        overall_state="SIMULATED_PLAN",
        host_mitigated=False,
        business_recovered=False,
        reason_codes=("simulation_no_real_execution",),
    )


def unknown_verification() -> TwoLayerRecoveryResult:
    """Keep a real action truthful until independent post-action evidence exists."""

    return TwoLayerRecoveryResult(
        host_state="UNKNOWN",
        business_state="UNKNOWN",
        overall_state="VERIFICATION_UNKNOWN",
        host_mitigated=False,
        business_recovered=False,
        reason_codes=("post_action_verification_unavailable",),
    )


@dataclass(frozen=True)
class BrokerResult:
    state: str
    reason_codes: tuple[str, ...]
    action_result: ActionResult | None = None
    verification: TwoLayerRecoveryResult | None = None
    capability_reason: str = "not_required"
    cooldown_state: str = "not_checked"
    adapter_called: bool = False
    requires_reconciliation: bool = False
    semantic_state: str = "PLANNED"
    execution_semantics: str = "NOT_EXECUTED"
    revalidation: FreshRevalidation | None = None
    state_trace: tuple[str, ...] = ()


class GuardianActionBroker:
    """Independent pre-action boundary for one durable ActionIntent."""

    def __init__(
        self,
        state_store: GuardianStateStore,
        policy: EmergencySheddingPolicy,
        adapter: ActionAdapter,
        *,
        cooldown_seconds: float = 30.0,
        max_actions: int = 1,
        action_window_seconds: float = 300.0,
        max_consecutive_failures: int = 2,
        fresh_revalidation_provider: FreshRevalidationProvider | None = None,
        config_digest: str | None = None,
    ) -> None:
        if not 0 <= cooldown_seconds <= 86_400:
            raise ValueError("cooldown_seconds_out_of_bounds")
        if not isinstance(max_actions, int) or isinstance(max_actions, bool) or not 1 <= max_actions <= 10:
            raise ValueError("max_actions_out_of_bounds")
        if not 0 < action_window_seconds <= 86_400:
            raise ValueError("action_window_seconds_out_of_bounds")
        if not isinstance(max_consecutive_failures, int) or isinstance(max_consecutive_failures, bool) or not 1 <= max_consecutive_failures <= 10:
            raise ValueError("max_consecutive_failures_out_of_bounds")
        self.state_store = state_store
        self.policy = policy
        self.adapter = adapter
        self.cooldown_seconds = float(cooldown_seconds)
        self.max_actions = max_actions
        self.action_window_seconds = float(action_window_seconds)
        self.max_consecutive_failures = max_consecutive_failures
        self.fresh_revalidation_provider = fresh_revalidation_provider
        self.config_digest = config_digest

    @staticmethod
    def _policy_entries(policy: EmergencySheddingPolicy) -> tuple[dict[str, Mapping[str, Any]], set[str]]:
        protected: dict[str, Mapping[str, Any]] = {}
        actionable: dict[str, Mapping[str, Any]] = {}
        for item in policy.protected_set:
            identifier = item.get("stable_id") or item.get("container_id") or item.get("id")
            if isinstance(identifier, str):
                protected[identifier] = item
        for item in policy.actionable_set:
            identifier = item.get("stable_id") or item.get("container_id") or item.get("id")
            if isinstance(identifier, str):
                actionable[identifier] = item
        return protected, set(actionable)

    @staticmethod
    def _registry(event: Mapping[str, Any], current_registry: Iterable[Mapping[str, Any]] | None) -> list[Mapping[str, Any]]:
        if current_registry is not None:
            try:
                return [item for item in current_registry if isinstance(item, Mapping)]
            except Exception:
                return []
        raw = _mapping(_mapping(event.get("signals")).get("object_registry")).get("objects")
        return [item for item in raw if isinstance(item, Mapping)] if isinstance(raw, list) else []

    def _preflight(
        self,
        intent: ActionIntent,
        event: Mapping[str, Any],
        *,
        mode: str,
        now_epoch_s: float,
        current_registry: Iterable[Mapping[str, Any]] | None,
        risk_event: Mapping[str, Any] | None = None,
    ) -> tuple[bool, tuple[str, ...]]:
        if mode not in {"simulate", "enforce"}:
            return False, ("mode_not_actionable",)
        if intent.action != "graceful_stop":
            return False, ("action_not_allowlisted",)
        if now_epoch_s >= intent.expires_at:
            return False, ("intent_expired",)
        if intent.policy_digest != emergency_policy_digest(self.policy):
            return False, ("policy_digest_mismatch",)

        emergency = _mapping(event.get("emergency_shedding"))
        decision = _mapping(emergency.get("decision"))
        plan = _extract_plan(event)
        if not isinstance(plan, Mapping) or plan.get("plan_id") != intent.plan_id:
            return False, ("plan_mismatch",)
        if decision.get("resource_kind") not in {None, intent.resource_kind}:
            return False, ("plan_resource_mismatch",)

        risk_source = risk_event if risk_event is not None else event
        evaluations = _mapping(risk_source.get("resource_evaluations"))
        evaluation = _mapping(evaluations.get(intent.resource_kind))
        risk = _mapping(evaluation.get("risk"))
        risk_state = str(risk.get("state") or "").lower()
        fresh_emergency = _mapping(risk_source.get("emergency_shedding"))
        active_resources = fresh_emergency.get("active_resources") or emergency.get("active_resources")
        danger_confirmed = (
            risk.get("danger_confirmed") is True
            or risk_state in {"critical_confirmed", "critical-confirmed"}
            or (isinstance(active_resources, list) and intent.resource_kind in active_resources and decision.get("action") == "graceful_stop")
        )
        if not danger_confirmed:
            return False, ("risk_no_longer_confirmed",)

        protected, actionable = self._policy_entries(self.policy)
        if intent.target_id in protected:
            return False, ("protected_object",)
        entry = next((item for item in self.policy.actionable_set if (item.get("stable_id") or item.get("container_id") or item.get("id")) == intent.target_id), None)
        if intent.target_id not in actionable or entry is None:
            return False, ("target_not_actionable",)
        allowed_resources = entry.get("allowed_resources")
        aliases = {"capacity": "disk_capacity", "disk": "disk_capacity", "disk_capacity": "disk_capacity", "memory": "memory", "cpu": "cpu", "io": "io"}
        normalized_resources = {
            aliases.get(str(value).strip().lower(), "")
            for value in allowed_resources
            if isinstance(value, str)
        } if isinstance(allowed_resources, list) else set()
        if intent.resource_kind not in normalized_resources:
            return False, ("resource_not_allowed",)
        if entry.get("action") != "graceful_stop" or entry.get("environment") != "local-disposable":
            return False, ("actionable_entry_invalid",)
        try:
            if _parse_expiry(entry.get("expires_at"), "actionable_expires_at") <= now_epoch_s:
                return False, ("actionable_entry_expired",)
        except ActionIntentError:
            return False, ("actionable_entry_invalid",)

        registry = self._registry(event, current_registry)
        matching = [item for item in registry if item.get("id") == intent.target_id]
        if len(matching) != 1:
            return False, ("target_identity_changed",)
        identity = matching[0]
        if (
            identity.get("created_at") != intent.target_created_at
            or identity.get("cgroup_path") != intent.target_cgroup_path
            or identity.get("cgroup_inode") != intent.target_cgroup_inode
            or _labels(identity.get("labels")) != intent.target_labels
        ):
            return False, ("target_identity_changed",)
        if str(identity.get("status") or "").lower() not in {"running", "restarting"} and identity.get("running") is not True:
            return False, ("target_not_running",)
        return True, ()

    def execute(
        self,
        intent: ActionIntent,
        event: Mapping[str, Any],
        *,
        intent_id: str,
        mode: str,
        authorization: Authorization | None = None,
        current_registry: Iterable[Mapping[str, Any]] | None = None,
        now_epoch_s: float | None = None,
        verification_provider: VerificationProvider = default_verification,
    ) -> BrokerResult:
        timestamp = time.time() if now_epoch_s is None else _finite_number(now_epoch_s, "now_epoch_s")
        revalidation: FreshRevalidation | None = None
        fresh_event: Mapping[str, Any] | None = None
        fresh_registry: Iterable[Mapping[str, Any]] | None = current_registry
        if mode in {"simulate", "enforce"}:
            if self.fresh_revalidation_provider is None:
                return BrokerResult(
                    "denied",
                    ("fresh_revalidation_unavailable",),
                    semantic_state="MANUAL_HANDOFF",
                    execution_semantics="NOT_EXECUTED",
                )
            try:
                revalidation = self.fresh_revalidation_provider(intent, event)
            except Exception:
                return BrokerResult(
                    "denied",
                    ("fresh_revalidation_failed",),
                    semantic_state="MANUAL_HANDOFF",
                    execution_semantics="NOT_EXECUTED",
                )
            if not isinstance(revalidation, FreshRevalidation):
                return BrokerResult(
                    "denied",
                    ("fresh_revalidation_result_invalid",),
                    semantic_state="MANUAL_HANDOFF",
                    execution_semantics="NOT_EXECUTED",
                )
            if not revalidation.accepted:
                return BrokerResult(
                    "denied",
                    revalidation.reason_codes or ("fresh_revalidation_denied",),
                    semantic_state="MANUAL_HANDOFF",
                    execution_semantics="NOT_EXECUTED",
                    revalidation=revalidation,
                )
            fresh_event = revalidation.event
            fresh_registry = revalidation.registry
        try:
            allowed, reasons = self._preflight(
                intent,
                event,
                mode=mode,
                now_epoch_s=timestamp,
                current_registry=fresh_registry,
                risk_event=fresh_event,
            )
        except Exception:
            return BrokerResult(
                "denied",
                ("broker_preflight_failed",),
                semantic_state="MANUAL_HANDOFF",
                execution_semantics="NOT_EXECUTED",
                revalidation=revalidation,
            )
        if not allowed:
            return BrokerResult(
                "denied",
                reasons,
                semantic_state="MANUAL_HANDOFF",
                execution_semantics="NOT_EXECUTED",
                revalidation=revalidation,
            )

        try:
            breaker = self.state_store.failure_breaker(
                intent.host_id,
                GLOBAL_COOLDOWN_OBJECT,
                intent.action,
                self.max_consecutive_failures,
            )
            if breaker:
                return BrokerResult("escalated", ("failure_breaker_tripped",), cooldown_state="breaker_open")
            can_run, cooldown_state = self.state_store.allow_action(
                intent.host_id,
                GLOBAL_COOLDOWN_OBJECT,
                intent.action,
                timestamp,
                self.cooldown_seconds,
                self.max_actions,
                self.action_window_seconds,
            )
        except StateStoreError:
            return BrokerResult("escalated", ("cooldown_state_unavailable",))
        if not can_run:
            return BrokerResult("escalated", (cooldown_state,), cooldown_state=cooldown_state)

        request = ActionRequest(
            event_id=intent.event_id,
            target_id=intent.target_id,
            action=intent.action,
            protected=False,
            allowed_actions=frozenset({"graceful_stop"}),
            authorization=authorization,
            timeout_seconds=intent.timeout_seconds,
        )
        capability_reason = "not_required_for_simulate"
        if mode == "enforce":
            if authorization is None:
                return BrokerResult("denied", ("capability_required",), capability_reason="capability_missing", cooldown_state=cooldown_state)
            try:
                validate_request(request, now=timestamp)
            except ActionDenied as exc:
                return BrokerResult("denied", (str(exc),), capability_reason="capability_invalid", cooldown_state=cooldown_state)
        if mode == "simulate":
            if not CONTAINER_ID.fullmatch(request.target_id) or request.action != "graceful_stop":
                return BrokerResult("denied", ("simulation_request_invalid",), cooldown_state=cooldown_state)
            if not isinstance(self.adapter, FakeActionAdapter):
                return BrokerResult("denied", ("simulate_requires_fake_adapter",), cooldown_state=cooldown_state)

        try:
            if not self.state_store.claim_action_slot(
                intent.host_id,
                intent.action,
                intent_id,
                now=timestamp,
            ):
                return BrokerResult("escalated", ("action_slot_busy",), cooldown_state="action_slot_busy")
        except StateStoreError:
            return BrokerResult("escalated", ("action_slot_unavailable",), cooldown_state="action_slot_unavailable")

        def release_slot() -> str | None:
            try:
                if self.state_store.release_action_slot(
                    intent.host_id,
                    intent.action,
                    intent_id,
                    now=timestamp,
                ):
                    return None
                return "action_slot_not_owned"
            except StateStoreError:
                return "action_slot_release_failed"

        if mode == "enforce":
            if getattr(self.adapter, "broker_managed_capability", False):
                capability_reason = "broker_managed_capability"
            else:
                try:
                    capability = self.state_store.consume_capability(
                        authorization,
                        event_id=intent.event_id,
                        now=timestamp,
                    )
                except StateStoreError:
                    release_reason = release_slot()
                    if release_reason:
                        return BrokerResult(
                            "manual_reconciliation_required",
                            ("capability_state_unavailable", release_reason),
                            cooldown_state="manual_reconciliation_required",
                            requires_reconciliation=True,
                        )
                    return BrokerResult("escalated", ("capability_state_unavailable",), cooldown_state=cooldown_state)
                if not capability.allowed:
                    release_reason = release_slot()
                    if release_reason:
                        return BrokerResult(
                            "manual_reconciliation_required",
                            (capability.reason, release_reason),
                            capability_reason=capability.reason,
                            cooldown_state="manual_reconciliation_required",
                            requires_reconciliation=True,
                        )
                    return BrokerResult("denied", (capability.reason,), capability_reason=capability.reason, cooldown_state=cooldown_state)
                capability_reason = capability.reason

        try:
            if not self.state_store.mark_execution_started(intent_id, now=timestamp):
                release_reason = release_slot()
                if release_reason:
                    return BrokerResult(
                        "manual_reconciliation_required",
                        ("intent_state_not_executable", release_reason),
                        cooldown_state="manual_reconciliation_required",
                        requires_reconciliation=True,
                    )
                return BrokerResult("escalated", ("intent_state_not_executable",), cooldown_state=cooldown_state)
            execute_intent = getattr(self.adapter, "execute_intent", None)
            if callable(execute_intent):
                action_result = execute_intent(
                    intent,
                    request,
                    intent_id=intent_id,
                    now=timestamp,
                )
            else:
                action_result = self.adapter.execute(request, now=timestamp)
        except ActionDenied as exc:
            release_reason = release_slot()
            if release_reason:
                return BrokerResult(
                    "manual_reconciliation_required",
                    (str(exc), release_reason),
                    capability_reason=capability_reason,
                    cooldown_state="manual_reconciliation_required",
                    adapter_called=True,
                    requires_reconciliation=True,
                )
            return BrokerResult(
                "denied",
                (str(exc),),
                capability_reason=capability_reason,
                cooldown_state="adapter_rejected",
                adapter_called=True,
            )
        except Exception:
            # The adapter may have crossed its own boundary before raising.  Do
            # not guess whether it mutated the target and do not replay it.
            return BrokerResult(
                "manual_reconciliation_required",
                ("adapter_outcome_unknown",),
                capability_reason=capability_reason,
                cooldown_state="manual_reconciliation_required",
                adapter_called=True,
                requires_reconciliation=True,
            )
        if not isinstance(action_result, ActionResult):
            return BrokerResult(
                "manual_reconciliation_required",
                ("adapter_result_invalid",),
                capability_reason=capability_reason,
                cooldown_state="manual_reconciliation_required",
                adapter_called=True,
                requires_reconciliation=True,
            )
        if not action_result.executed:
            release_reason = release_slot()
            if release_reason:
                return BrokerResult(
                    "manual_reconciliation_required",
                    (action_result.reason or "action_not_executed", release_reason),
                    action_result=action_result,
                    capability_reason=capability_reason,
                    cooldown_state="manual_reconciliation_required",
                    adapter_called=True,
                    requires_reconciliation=True,
                )
            return BrokerResult(
                "planned",
                (action_result.reason or "action_not_executed",),
                action_result=action_result,
                capability_reason=capability_reason,
                cooldown_state="not_recorded",
                adapter_called=True,
                semantic_state="PLANNED",
                execution_semantics="NOT_EXECUTED",
                revalidation=revalidation,
                state_trace=("CRITICAL_CONFIRMED", "PLANNED"),
            )
        if mode == "simulate":
            # A fake adapter call is a plan-path exercise only.  It must never
            # produce host mitigation, business recovery or a real cooldown.
            return BrokerResult(
                "simulated_plan_complete",
                ("simulation_plan_complete",),
                action_result=action_result,
                verification=simulation_verification(),
                capability_reason=capability_reason,
                cooldown_state="simulation_not_recorded",
                adapter_called=True,
                semantic_state="SIMULATED_PLAN_COMPLETE",
                execution_semantics="SIMULATED",
                revalidation=revalidation,
                state_trace=("CRITICAL_CONFIRMED", "PLANNED", "AUTHORIZED", "EXECUTING", "SIMULATED_PLAN_COMPLETE"),
            )
        if mode == "enforce" and verification_provider is default_verification:
            verification = unknown_verification()
        else:
            try:
                verification = verification_provider(intent, action_result)
            except Exception:
                verification = None
        if verification is None:
            return BrokerResult(
                "manual_reconciliation_required",
                ("verification_unavailable",),
                action_result=action_result,
                capability_reason=capability_reason,
                cooldown_state="verification_failed",
                adapter_called=True,
                requires_reconciliation=True,
                semantic_state="MANUAL_HANDOFF",
                execution_semantics="UNKNOWN",
                revalidation=revalidation,
            )
        semantic_state = (
            "MANUAL_HANDOFF"
            if verification.overall_state == "VERIFICATION_UNKNOWN"
            else "BUSINESS_RECOVERED"
            if verification.host_mitigated and verification.business_recovered
            else "MITIGATED"
            if verification.host_mitigated
            else "NOT_MITIGATED"
        )
        return BrokerResult(
            "executed",
            ("action_completed",),
            action_result=action_result,
            verification=verification,
            capability_reason=capability_reason,
            cooldown_state=cooldown_state,
            adapter_called=True,
            semantic_state=semantic_state,
            execution_semantics="REAL",
            revalidation=revalidation,
            state_trace=("CRITICAL_CONFIRMED", "PLANNED", "AUTHORIZED", "EXECUTING", "VERIFYING", semantic_state),
        )


@dataclass(frozen=True)
class CoordinatorResult:
    mode: str
    state: str
    event_id: str
    reason_codes: tuple[str, ...]
    notification: Mapping[str, Any] | None = None
    intent: ActionIntent | None = None
    intent_id: str | None = None
    broker: BrokerResult | None = None
    manual_handoff: bool = False
    semantic_state: str = "MANUAL_HANDOFF"
    execution_semantics: str = "NOT_EXECUTED"
    host_state: str | None = None
    business_state: str | None = None
    state_trace: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": COORDINATOR_SCHEMA,
            "mode": self.mode,
            "state": self.state,
            "event_id": self.event_id,
            "reason_codes": list(self.reason_codes),
            "notification": dict(self.notification) if self.notification is not None else None,
            "intent": self.intent.as_dict() if self.intent is not None else None,
            "intent_id": self.intent_id,
            "broker": (
                {
                    "state": self.broker.state,
                    "reason_codes": list(self.broker.reason_codes),
                    "action_result": asdict(self.broker.action_result) if self.broker.action_result else None,
                    "verification": asdict(self.broker.verification) if self.broker.verification else None,
                    "capability_reason": self.broker.capability_reason,
                    "cooldown_state": self.broker.cooldown_state,
                    "adapter_called": self.broker.adapter_called,
                    "requires_reconciliation": self.broker.requires_reconciliation,
                    "semantic_state": self.broker.semantic_state,
                    "execution_semantics": self.broker.execution_semantics,
                    "revalidation": self.broker.revalidation.as_dict() if self.broker.revalidation else None,
                    "state_trace": list(self.broker.state_trace),
                }
                if self.broker is not None
                else None
            ),
            "manual_handoff": self.manual_handoff,
            "semantic_state": self.semantic_state,
            "execution_semantics": self.execution_semantics,
            "host_state": self.host_state,
            "business_state": self.business_state,
            "state_trace": list(self.state_trace),
        }


class GuardianCoordinator:
    """Connect the local event path without granting implicit permissions."""

    def __init__(
        self,
        state_store: GuardianStateStore,
        notification_outbox: DurableNotificationOutbox,
        *,
        policy: EmergencySheddingPolicy,
        adapter: ActionAdapter | None = None,
        default_mode: str = "observe",
        plan_ttl_seconds: float = 30.0,
        cooldown_seconds: float = 30.0,
        max_actions: int = 1,
        action_window_seconds: float = 300.0,
        max_consecutive_failures: int = 2,
        fresh_revalidation_provider: FreshRevalidationProvider | None = None,
        config_digest: str | None = None,
    ) -> None:
        if default_mode not in SUPPORTED_MODES:
            raise ValueError("default_mode_invalid")
        if not isinstance(policy, EmergencySheddingPolicy):
            raise ValueError("emergency_policy_required")
        self.state_store = state_store
        self.notification_outbox = notification_outbox
        self.policy = policy
        self.default_mode = default_mode
        self.plan_ttl_seconds = plan_ttl_seconds
        self.adapter = adapter or FakeActionAdapter()
        self.broker = GuardianActionBroker(
            state_store,
            policy,
            self.adapter,
            cooldown_seconds=cooldown_seconds,
            max_actions=max_actions,
            action_window_seconds=action_window_seconds,
            max_consecutive_failures=max_consecutive_failures,
            fresh_revalidation_provider=fresh_revalidation_provider,
            config_digest=config_digest,
        )
        self.reconciliation_required = tuple(state_store.reconcile_pending())

    def register_capability(self, authorization: Authorization) -> None:
        """Register an externally issued capability; never manufacture one."""

        self.state_store.register_capability(authorization)

    def enqueue_notification_event(
        self,
        event: Mapping[str, Any],
        *,
        incident_key: str | None = None,
        now_monotonic_ns: int | None = None,
    ) -> dict[str, Any]:
        """Persist a notification before a caller crosses an action boundary."""

        return self.notification_outbox.enqueue_event(
            event,
            incident_key=incident_key,
            now_monotonic_ns=now_monotonic_ns,
        )

    @staticmethod
    def _intent_audit_payload(event: Mapping[str, Any], intent: ActionIntent) -> dict[str, Any]:
        return {
            "schema": "guardian.incident.audit.v1",
            "stage": "intent",
            "event": {
                "event_id": intent.event_id,
                "sample_id": intent.sample_id,
                "state": event.get("state"),
                "observed_monotonic_ns": event.get("observed_monotonic_ns") or _mapping(event.get("signals")).get("observed_monotonic_ns"),
            },
            "plan": intent.as_dict(),
        }

    def process(
        self,
        event: Mapping[str, Any],
        *,
        mode: str | None = None,
        authorization: Authorization | None = None,
        current_registry: Iterable[Mapping[str, Any]] | None = None,
        now_epoch_s: float | None = None,
        now_monotonic_ns: int | None = None,
        verification_provider: VerificationProvider = default_verification,
    ) -> CoordinatorResult:
        if not isinstance(event, Mapping):
            return CoordinatorResult("observe", "denied", "", ("event_mapping_required",))
        event_id = str(event.get("event_id") or "")
        # The coordinator's default is an explicit safety setting.  A mode
        # embedded in an event is evidence, not permission to switch into
        # simulate/enforce after a restart or a caller omission.
        effective_mode = mode if mode is not None else self.default_mode
        if effective_mode not in SUPPORTED_MODES:
            return CoordinatorResult(str(effective_mode), "denied", event_id, ("mode_invalid",))
        try:
            state = event.get("state")
            notification = (
                self.notification_outbox.enqueue_event(event, now_monotonic_ns=now_monotonic_ns)
                if state in NOTIFIABLE_STATES
                else None
            )
        except (NotificationOutboxError, ValueError, TypeError) as exc:
            return CoordinatorResult(effective_mode, "escalated", event_id, ("notification_persistence_failed", str(exc)))
        if notification is not None and notification.get("status") != "queued":
            return CoordinatorResult(
                effective_mode,
                "escalated",
                event_id,
                ("notification_not_durable", str(notification.get("reason") or notification.get("status"))),
                notification=notification,
            )
        if effective_mode == "observe":
            return CoordinatorResult(
                effective_mode,
                "observed",
                event_id,
                ("observe_only",),
                notification=notification,
                semantic_state=_event_semantic_state(event),
                execution_semantics="NOT_APPLICABLE",
            )
        runtime_gate = _mapping(_mapping(event.get("evidence")).get("runtime_gate"))
        if runtime_gate.get("execution_allowed") is False:
            reasons = tuple(
                dict.fromkeys(
                    [
                        "runtime_execution_gate_closed",
                        *(
                            str(reason)
                            for reason in runtime_gate.get("reason_codes", [])
                            if isinstance(reason, str) and reason
                        ),
                    ]
                )
            )
            return CoordinatorResult(
                effective_mode,
                "denied",
                event_id,
                reasons,
                notification=notification,
            )
        if self.reconciliation_required:
            return CoordinatorResult(
                effective_mode,
                "manual_reconciliation_required",
                event_id,
                ("startup_reconciliation_required",),
                notification=notification,
                manual_handoff=True,
            )

        try:
            intent = action_intent_from_event(
                event,
                expected_policy_digest=emergency_policy_digest(self.policy),
                now_epoch_s=now_epoch_s,
                plan_ttl_seconds=self.plan_ttl_seconds,
            )
        except ActionIntentError as exc:
            return CoordinatorResult(effective_mode, "denied", event_id, (str(exc),), notification=notification)

        timestamp = time.time() if now_epoch_s is None else now_epoch_s
        try:
            claim = self.state_store.claim_intent(
                host_id=intent.host_id,
                object_id=intent.target_id,
                action=intent.action,
                event_id=intent.event_id,
                audit_payload=self._intent_audit_payload(event, intent),
                now=timestamp,
            )
        except StateStoreError as exc:
            return CoordinatorResult(effective_mode, "escalated", event_id, ("intent_persistence_failed", str(exc)), notification=notification, intent=intent)
        if not claim.claimed:
            state = "escalated" if claim.reason in {"active_intent_exists", "idempotent_intent_reused"} else "denied"
            return CoordinatorResult(
                effective_mode,
                state,
                event_id,
                (claim.reason,),
                notification=notification,
                intent=intent,
                intent_id=claim.intent_id,
                manual_handoff=claim.reason == "active_intent_exists",
            )
        if claim.idempotency_key != intent.idempotency_key:
            try:
                self.state_store.record_result(
                    claim.intent_id,
                    {"stage": "intent", "reason": "idempotency_key_mismatch", "executed": False},
                    success=False,
                    executed=False,
                    now=timestamp,
                )
            except StateStoreError:
                pass
            return CoordinatorResult(
                effective_mode,
                "manual_reconciliation_required",
                event_id,
                ("idempotency_key_mismatch",),
                notification=notification,
                intent=intent,
                intent_id=claim.intent_id,
                manual_handoff=True,
            )

        broker_result = self.broker.execute(
            intent,
            event,
            intent_id=claim.intent_id,
            mode=effective_mode,
            authorization=authorization,
            current_registry=current_registry,
            now_epoch_s=timestamp,
            verification_provider=verification_provider,
        )
        if broker_result.requires_reconciliation:
            self.reconciliation_required = (*self.reconciliation_required, {"intent_id": claim.intent_id, "event_id": event_id})
            return CoordinatorResult(
                effective_mode,
                "manual_reconciliation_required",
                event_id,
                broker_result.reason_codes,
                notification=notification,
                intent=intent,
                intent_id=claim.intent_id,
                broker=broker_result,
                manual_handoff=True,
                semantic_state="MANUAL_HANDOFF",
                execution_semantics=broker_result.execution_semantics,
            )

        action_result = broker_result.action_result
        if effective_mode == "simulate" and action_result is not None:
            simulation_payload = {
                "stage": "simulation",
                "action": asdict(action_result),
                "verification": asdict(broker_result.verification) if broker_result.verification else None,
                "semantic_state": "SIMULATED_PLAN_COMPLETE",
                "execution_semantics": "SIMULATED",
                "executed": False,
            }
            try:
                self.state_store.record_result(
                    claim.intent_id,
                    simulation_payload,
                    success=False,
                    executed=False,
                    now=timestamp,
                )
                if not self.state_store.release_action_slot(
                    intent.host_id,
                    intent.action,
                    claim.intent_id,
                    now=timestamp,
                ):
                    return CoordinatorResult(
                        effective_mode,
                        "manual_reconciliation_required",
                        event_id,
                        ("action_slot_release_failed",),
                        notification=notification,
                        intent=intent,
                        intent_id=claim.intent_id,
                        broker=broker_result,
                        manual_handoff=True,
                        semantic_state="MANUAL_HANDOFF",
                        execution_semantics="SIMULATED",
                        state_trace=("CRITICAL_CONFIRMED", "PLANNED", "AUTHORIZED", "EXECUTING", "MANUAL_HANDOFF"),
                    )
            except StateStoreError as exc:
                return CoordinatorResult(
                    effective_mode,
                    "manual_reconciliation_required",
                    event_id,
                    ("simulation_result_persistence_failed", str(exc)),
                    notification=notification,
                    intent=intent,
                    intent_id=claim.intent_id,
                    broker=broker_result,
                    manual_handoff=True,
                    semantic_state="MANUAL_HANDOFF",
                    execution_semantics="SIMULATED",
                    state_trace=("CRITICAL_CONFIRMED", "PLANNED", "AUTHORIZED", "EXECUTING", "MANUAL_HANDOFF"),
                )
            return CoordinatorResult(
                effective_mode,
                "simulated_plan_complete",
                event_id,
                broker_result.reason_codes,
                notification=notification,
                intent=intent,
                intent_id=claim.intent_id,
                broker=broker_result,
                manual_handoff=False,
                semantic_state="SIMULATED_PLAN_COMPLETE",
                execution_semantics="SIMULATED",
                host_state="NOT_APPLICABLE",
                business_state="NOT_APPLICABLE",
                state_trace=broker_result.state_trace,
            )
        if action_result is None or not action_result.executed:
            try:
                self.state_store.record_result(
                    claim.intent_id,
                    {
                        "stage": "broker",
                        "reason_codes": list(broker_result.reason_codes),
                        "capability_reason": broker_result.capability_reason,
                        "cooldown_state": broker_result.cooldown_state,
                        "semantic_state": broker_result.semantic_state,
                        "execution_semantics": broker_result.execution_semantics,
                        "revalidation": broker_result.revalidation.as_dict() if broker_result.revalidation else None,
                        "executed": False,
                    },
                    success=False,
                    executed=False,
                    now=timestamp,
                )
            except StateStoreError as exc:
                return CoordinatorResult(effective_mode, "manual_reconciliation_required", event_id, ("result_persistence_failed", str(exc)), notification=notification, intent=intent, intent_id=claim.intent_id, broker=broker_result, manual_handoff=True)
            return CoordinatorResult(
                effective_mode,
                broker_result.state,
                event_id,
                broker_result.reason_codes,
                notification=notification,
                intent=intent,
                intent_id=claim.intent_id,
                broker=broker_result,
                manual_handoff=broker_result.state in {"denied", "escalated"},
                semantic_state=broker_result.semantic_state,
                execution_semantics=broker_result.execution_semantics,
                state_trace=broker_result.state_trace,
            )

        verification = broker_result.verification
        host_mitigated = verification.host_mitigated if verification is not None else False
        action_success = action_result.returncode == 0 and host_mitigated
        result_payload = {
            "stage": "verification",
            "action": asdict(action_result),
            "verification": asdict(verification) if verification is not None else None,
            "manual_handoff": True,
            "semantic_state": broker_result.semantic_state,
            "execution_semantics": broker_result.execution_semantics,
            "revalidation": broker_result.revalidation.as_dict() if broker_result.revalidation else None,
        }
        try:
            self.state_store.record_result(
                claim.intent_id,
                result_payload,
                success=action_success,
                executed=True,
                now=timestamp,
            )
            self.state_store.record_action(
                intent.host_id,
                GLOBAL_COOLDOWN_OBJECT,
                intent.action,
                timestamp,
                action_success,
            )
            breaker_tripped = self.state_store.failure_breaker(
                intent.host_id,
                GLOBAL_COOLDOWN_OBJECT,
                intent.action,
                self.broker.max_consecutive_failures,
            )
            if not self.state_store.release_action_slot(
                intent.host_id,
                intent.action,
                claim.intent_id,
                now=timestamp,
            ):
                return CoordinatorResult(
                    effective_mode,
                    "manual_reconciliation_required",
                    event_id,
                    ("action_slot_release_failed",),
                    notification=notification,
                    intent=intent,
                    intent_id=claim.intent_id,
                    broker=broker_result,
                    manual_handoff=True,
                )
        except StateStoreError as exc:
            return CoordinatorResult(effective_mode, "manual_reconciliation_required", event_id, ("result_persistence_failed", str(exc)), notification=notification, intent=intent, intent_id=claim.intent_id, broker=broker_result, manual_handoff=True)

        if verification is not None and verification.overall_state == "VERIFICATION_UNKNOWN":
            state = "manual_handoff"
            reasons = verification.reason_codes
        elif action_success and verification is not None:
            state = "business_recovered" if verification.business_recovered else "mitigated"
            reasons = verification.reason_codes
        elif breaker_tripped:
            state = "escalated"
            reasons = ("failure_breaker_tripped",) + (verification.reason_codes if verification is not None else ())
        else:
            state = "not_mitigated"
            reasons = ("action_timeout" if action_result.reason == "executor_timeout" else "action_failed",) + (verification.reason_codes if verification is not None else ())
        return CoordinatorResult(
            effective_mode,
            state,
            event_id,
            tuple(dict.fromkeys(reasons)),
            notification=notification,
            intent=intent,
            intent_id=claim.intent_id,
            broker=broker_result,
            manual_handoff=state in {"manual_handoff", "not_mitigated", "mitigated", "escalated"},
            semantic_state=broker_result.semantic_state,
            execution_semantics=broker_result.execution_semantics,
            host_state=verification.host_state if verification is not None else None,
            business_state=verification.business_state if verification is not None else None,
            state_trace=broker_result.state_trace,
        )

    def drain_notifications(self, *, now_monotonic_ns: int, max_items: int = 100) -> list[dict[str, Any]]:
        return self.notification_outbox.drain_pending(now_monotonic_ns=now_monotonic_ns, max_items=max_items)


def _load_authorization(path: Path) -> Authorization:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("authorization_mapping_required")
    return Authorization(
        approval_id=str(value["approval_id"]),
        environment=str(value["environment"]),
        target_id=str(value["target_id"]),
        action=str(value["action"]),
        expires_at=float(value["expires_at"]),
    )


def main() -> None:
    """Run one local event through the unified, fake-safe coordinator."""

    parser = argparse.ArgumentParser(description="Run one Guardian event through the local coordinator")
    parser.add_argument("--event-file", type=Path, required=True)
    parser.add_argument("--state-db", type=Path, required=True)
    parser.add_argument("--outbox-db", type=Path, required=True)
    parser.add_argument(
        "--config",
        type=Path,
        help="validated Guardian JSON config; omitted means safe observe-only defaults",
    )
    parser.add_argument("--mode", choices=SUPPORTED_MODES, default="observe")
    parser.add_argument("--adapter-outcome", choices=sorted(FakeActionAdapter.OUTCOMES), default="success")
    parser.add_argument("--authorization-file", type=Path)
    parser.add_argument(
        "--fresh-event-file",
        type=Path,
        help="independent action-time event required for simulate/enforce; omission stays fail-closed",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    from .guardian_config import load_config, safe_defaults
    from .guardian_emergency_shedding import emergency_shedding_policy_from_config

    event = json.loads(args.event_file.read_text(encoding="utf-8"))
    config = load_config(args.config) if args.config else safe_defaults()
    policy = emergency_shedding_policy_from_config(config)
    fresh_provider = None
    if args.fresh_event_file:
        fresh_event = json.loads(args.fresh_event_file.read_text(encoding="utf-8"))

        def fresh_provider(intent: ActionIntent, original_event: Mapping[str, Any]):
            return revalidate_observer_event(
                intent,
                original_event,
                fresh_event,
                policy=policy,
                expected_config_digest=config.config_digest,
            )

    sink = FakeNotificationSink()
    outbox = DurableNotificationOutbox(args.outbox_db, sink)
    store = GuardianStateStore(args.state_db)
    coordinator = GuardianCoordinator(
        store,
        outbox,
        policy=policy,
        adapter=FakeActionAdapter(args.adapter_outcome),
        default_mode="observe",
        fresh_revalidation_provider=fresh_provider,
        config_digest=config.config_digest,
    )
    authorization = _load_authorization(args.authorization_file) if args.authorization_file else None
    result = coordinator.process(event, mode=args.mode, authorization=authorization)
    payload = json.dumps(result.as_dict(), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()


__all__ = [
    "ACTION_INTENT_SCHEMA",
    "ActionIntent",
    "ActionIntentError",
    "BrokerResult",
    "COORDINATOR_SCHEMA",
    "FakeActionAdapter",
    "GuardianActionBroker",
    "GuardianCoordinator",
    "CoordinatorError",
    "INCIDENT_STATES",
    "action_intent_from_event",
    "default_verification",
    "simulation_verification",
    "unknown_verification",
]
