"""Local-only Guardian administrator notification contract.

This module is deliberately a notification boundary, not an action boundary.
It builds a small versioned payload from a Guardian event, sends only to an
injected sink, and records delivery outcomes.  It has no HTTP client, webhook
credential handling, Docker call, systemd call, or action adapter access.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Callable, Iterable, Mapping
from typing import Any


NOTIFICATION_SCHEMA = "guardian.notification.v1"
NOTIFIABLE_STATES = frozenset({"warning", "critical", "recovered", "escalated"})
SAFE_EXECUTION_STATES = frozenset({"not_executed", "not_applicable"})


class NotificationError(ValueError):
    """The source event is not safe or complete enough to notify."""


class NotificationSinkUnavailable(RuntimeError):
    """The injected sink is unavailable; retry/dead-letter is required."""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string(value: Any, *, field: str, required: bool = False) -> str | None:
    if isinstance(value, str) and value and len(value) <= 512:
        return value
    if required:
        raise NotificationError(f"{field}_required")
    return None


def _hash_ref(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _safe_strings(value: Any, *, limit: int = 12) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item][:limit]


def _bounded_int(value: Any, *, minimum: int, maximum: int, error: str) -> int:
    """Accept only real bounded integers; bool and fractional values fail closed."""

    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(error)
    return value


def _duration_to_ns(value: Any, *, error: str) -> int:
    """Convert a positive finite seconds value to nanoseconds safely."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(error)
    try:
        numeric = float(value)
        if not math.isfinite(numeric) or numeric <= 0:
            raise ValueError(error)
        nanoseconds = int(numeric * 1_000_000_000)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(error) from exc
    if nanoseconds <= 0:
        raise ValueError(error)
    return nanoseconds


def _resource_states(source_event: Mapping[str, Any]) -> dict[str, str]:
    evaluations = _mapping(source_event.get("resource_evaluations"))
    result: dict[str, str] = {}
    for kind, raw in evaluations.items():
        if not isinstance(kind, str) or not isinstance(raw, Mapping):
            continue
        risk = _mapping(raw.get("risk"))
        state = risk.get("state") or raw.get("state") or "unknown"
        result[kind] = str(state)
    return dict(sorted(result.items()))


def build_notification_event(
    source_event: Mapping[str, Any],
    *,
    incident_key: str | None = None,
) -> dict[str, Any]:
    """Build a redacted, versioned notification payload from one event.

    The optional ``incident_key`` is the caller-owned stable incident handle;
    it is hashed before leaving this function so it is never sent as raw
    notification content.  Recovery events should reuse the active incident
    key to preserve deduplication across state transitions.
    """

    if not isinstance(source_event, Mapping):
        raise NotificationError("source_event_mapping_required")
    state = _string(source_event.get("state"), field="state", required=True)
    if state not in NOTIFIABLE_STATES:
        raise NotificationError("state_not_notifiable")
    event_id = _string(source_event.get("event_id"), field="event_id", required=True)
    observed_at = _string(source_event.get("observed_at"), field="observed_at", required=True)
    observed_monotonic_ns = source_event.get("observed_monotonic_ns")
    if isinstance(observed_monotonic_ns, bool) or not isinstance(observed_monotonic_ns, int) or observed_monotonic_ns <= 0:
        signals = _mapping(source_event.get("signals"))
        observed_monotonic_ns = signals.get("observed_monotonic_ns")
    if isinstance(observed_monotonic_ns, bool) or not isinstance(observed_monotonic_ns, int) or observed_monotonic_ns <= 0:
        raise NotificationError("observed_monotonic_ns_required")

    host_id = _string(source_event.get("host_id"), field="host_id") or "unknown-host"
    decision = _mapping(source_event.get("decision"))
    joint = _mapping(source_event.get("joint_evaluation"))
    target_attribution = _mapping(source_event.get("target_attribution"))
    target = _mapping(target_attribution.get("target"))
    if not target:
        target = _mapping(joint.get("target"))
    target_id = (
        _string(target.get("id"), field="target_id")
        or _string(decision.get("target_id"), field="target_id")
        or _string(joint.get("target_id"), field="target_id")
    )
    resource_states = _resource_states(source_event)
    active_resources = joint.get("active_resources") or decision.get("active_resources")
    if not isinstance(active_resources, list):
        active_resources = [kind for kind, value in resource_states.items() if value in {"warning", "critical", "critical_confirmed"}]
    active_resources = sorted({str(value) for value in active_resources if isinstance(value, str)})

    stable_incident = incident_key or target_id or ":".join(active_resources) or "host"
    if not isinstance(stable_incident, str) or not stable_incident:
        raise NotificationError("incident_key_required")
    resource_scope = ",".join(sorted(resource_states)) or "host"
    dedup_material = "|".join(
        (NOTIFICATION_SCHEMA, _hash_ref(host_id), _hash_ref(stable_incident), resource_scope)
    )
    dedup_key = f"notification:{hashlib.sha256(dedup_material.encode('utf-8')).hexdigest()}"
    notification_id = hashlib.sha256(
        f"{dedup_key}|{state}|{observed_monotonic_ns}".encode("utf-8")
    ).hexdigest()
    config_digest = _string(_mapping(source_event.get("evidence")).get("config_digest"), field="config_digest")
    planned_action = _string(decision.get("action"), field="action") or "none"
    planned_mode = _string(decision.get("mode"), field="mode") or "observe"
    target_state = _string(decision.get("target_state"), field="target_state") or _string(joint.get("target_state"), field="target_state") or "NO_TARGET"

    payload: dict[str, Any] = {
        "schema": NOTIFICATION_SCHEMA,
        "notification_id": notification_id,
        "dedup_key": dedup_key,
        "severity": state,
        "observed_at": observed_at,
        "observed_monotonic_ns": observed_monotonic_ns,
        "host_ref": _hash_ref(host_id),
        "resources": resource_states,
        "active_resources": active_resources,
        "target": {
            "state": target_state,
            "ref": _hash_ref(target_id) if target_id else None,
            "present": bool(target_id),
        },
        "reason_codes": sorted(set(_safe_strings(decision.get("reason_codes")))),
        "quality_flags": sorted(set(_safe_strings(decision.get("quality_flags")))),
        "action_context": {
            "mode": planned_mode,
            "planned_action": planned_action,
            "execution": "not_executed",
            "authorization": "unchanged",
        },
        "source": {
            "event_id": event_id,
            "config_digest": config_digest,
        },
        "security": {
            "credentials_included": False,
            "raw_signals_included": False,
            "action_authorization_changed": False,
        },
    }
    return payload


class FakeNotificationSink:
    """In-memory sink for local tests; it never opens a network connection."""

    def __init__(self, *, failures_before_success: int = 0) -> None:
        if not isinstance(failures_before_success, int) or failures_before_success < 0 or failures_before_success > 10:
            raise ValueError("failures_before_success_out_of_bounds")
        self.failures_before_success = failures_before_success
        self.delivered: list[dict[str, Any]] = []

    def send(self, payload: Mapping[str, Any]) -> None:
        if self.failures_before_success:
            self.failures_before_success -= 1
            raise NotificationSinkUnavailable("fake_sink_unavailable")
        self.delivered.append(dict(payload))


class DisabledNotificationSink:
    """Explicit fail-closed placeholder until a real channel is authorized."""

    def send(self, payload: Mapping[str, Any]) -> None:
        del payload
        raise NotificationSinkUnavailable("notification_channel_disabled")


class NotificationDispatcher:
    """Bounded deduplicating dispatcher around an injected local sink."""

    def __init__(
        self,
        sink: Any,
        *,
        max_attempts: int = 3,
        max_age_seconds: float = 300.0,
        rate_limit_count: int = 10,
        rate_limit_window_seconds: float = 60.0,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        max_attempts = _bounded_int(max_attempts, minimum=1, maximum=5, error="max_attempts_out_of_bounds")
        max_age_ns = _duration_to_ns(max_age_seconds, error="notification_window_must_be_positive")
        rate_limit_count = _bounded_int(rate_limit_count, minimum=1, maximum=100, error="rate_limit_count_out_of_bounds")
        rate_limit_window_ns = _duration_to_ns(
            rate_limit_window_seconds,
            error="notification_window_must_be_positive",
        )
        if not callable(getattr(sink, "send", None)):
            raise ValueError("notification_sink_send_required")
        self.sink = sink
        self.max_attempts = max_attempts
        self.max_age_ns = max_age_ns
        self.rate_limit_count = rate_limit_count
        self.rate_limit_window_ns = rate_limit_window_ns
        self.sleep = sleep or (lambda _seconds: None)
        self.audits: list[dict[str, Any]] = []
        self.dead_letters: list[dict[str, Any]] = []
        self._seen_event_ids: set[str] = set()
        self._last_observed: dict[str, int] = {}
        self._delivered_severities: dict[str, set[str]] = {}
        self._attempt_timestamps: list[int] = []

    def _audit(
        self,
        payload: Mapping[str, Any] | None,
        *,
        status: str,
        attempts: int,
        reason: str | None = None,
    ) -> dict[str, Any]:
        source = _mapping(payload.get("source")) if isinstance(payload, Mapping) else {}
        audit = {
            "schema": "guardian.notification.delivery-audit.v1",
            "notification_id": payload.get("notification_id") if isinstance(payload, Mapping) else None,
            "dedup_key": payload.get("dedup_key") if isinstance(payload, Mapping) else None,
            "event_id": source.get("event_id"),
            "severity": payload.get("severity") if isinstance(payload, Mapping) else None,
            "status": status,
            "attempts": attempts,
            "reason": reason,
            "action_authorization": "unchanged",
        }
        self.audits.append(audit)
        return audit

    def _result(
        self,
        payload: Mapping[str, Any] | None,
        *,
        status: str,
        attempts: int = 0,
        reason: str | None = None,
    ) -> dict[str, Any]:
        audit = self._audit(payload, status=status, attempts=attempts, reason=reason)
        return {
            "status": status,
            "notification_id": audit["notification_id"],
            "dedup_key": audit["dedup_key"],
            "attempts": attempts,
            "reason": reason,
            "execution": "not_executed",
            "action_authorization": "unchanged",
        }

    def _allow_attempt(self, now_monotonic_ns: int) -> bool:
        cutoff = now_monotonic_ns - self.rate_limit_window_ns
        self._attempt_timestamps = [value for value in self._attempt_timestamps if value > cutoff]
        if len(self._attempt_timestamps) >= self.rate_limit_count:
            return False
        self._attempt_timestamps.append(now_monotonic_ns)
        return True

    def _dead_letter(self, payload: Mapping[str, Any], *, reason: str, attempts: int) -> None:
        self.dead_letters.append(
            {
                "payload": dict(payload),
                "reason": reason,
                "attempts": attempts,
            }
        )

    def _deliver(
        self,
        payload: Mapping[str, Any],
        *,
        now_monotonic_ns: int,
        redrive: bool = False,
    ) -> dict[str, Any]:
        if not self._allow_attempt(now_monotonic_ns):
            self._dead_letter(payload, reason="rate_limited", attempts=0)
            return self._result(payload, status="rate_limited", reason="rate_limited")
        error_code: str | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                self.sink.send(payload)
            except NotificationSinkUnavailable:
                error_code = "sink_unavailable"
            except Exception:
                error_code = "sink_error"
            else:
                key = str(payload["dedup_key"])
                severity = str(payload["severity"])
                delivered = self._delivered_severities.setdefault(key, set())
                if severity == "recovered":
                    delivered.clear()
                delivered.add(severity)
                return self._result(payload, status="delivered", attempts=attempt, reason="redrive" if redrive else None)
            if attempt < self.max_attempts:
                self.sleep(min(2.0, 0.25 * (2 ** (attempt - 1))))
        self._dead_letter(payload, reason=error_code or "sink_error", attempts=self.max_attempts)
        return self._result(payload, status="dead_letter", attempts=self.max_attempts, reason=error_code or "sink_error")

    def dispatch(self, payload: Mapping[str, Any], *, now_monotonic_ns: int | None = None) -> dict[str, Any]:
        """Deliver one built payload while preserving action authorization."""

        if not isinstance(payload, Mapping) or payload.get("schema") != NOTIFICATION_SCHEMA:
            return self._result(payload if isinstance(payload, Mapping) else None, status="rejected", reason="schema_invalid")
        required = ("notification_id", "dedup_key", "severity", "observed_monotonic_ns", "source")
        if any(key not in payload for key in required) or payload.get("severity") not in NOTIFIABLE_STATES:
            return self._result(payload, status="rejected", reason="payload_invalid")
        observed = payload.get("observed_monotonic_ns")
        if isinstance(observed, bool) or not isinstance(observed, int) or observed <= 0:
            return self._result(payload, status="rejected", reason="timestamp_invalid")
        now = observed if now_monotonic_ns is None else now_monotonic_ns
        if isinstance(now, bool) or not isinstance(now, int) or now < observed:
            return self._result(payload, status="rejected", reason="clock_invalid")
        if now - observed > self.max_age_ns:
            return self._result(payload, status="rejected", reason="expired")

        event_id = str(_mapping(payload.get("source")).get("event_id"))
        key = str(payload["dedup_key"])
        severity = str(payload["severity"])
        last = self._last_observed.get(key)
        if event_id in self._seen_event_ids:
            return self._result(payload, status="duplicate_suppressed", reason="event_id_seen")
        self._seen_event_ids.add(event_id)
        if last is not None and observed < last:
            return self._result(payload, status="rejected", reason="out_of_order")
        self._last_observed[key] = max(last or observed, observed)
        if severity in self._delivered_severities.get(key, set()):
            return self._result(payload, status="duplicate_suppressed", reason="severity_already_delivered")
        return self._deliver(payload, now_monotonic_ns=now)

    def redrive_dead_letters(self, *, now_monotonic_ns: int) -> list[dict[str, Any]]:
        """Retry retained dead letters explicitly; no automatic action follows."""

        pending = list(self.dead_letters)
        self.dead_letters.clear()
        results: list[dict[str, Any]] = []
        for item in pending:
            payload = _mapping(item.get("payload"))
            if not payload:
                continue
            result = self._deliver(payload, now_monotonic_ns=now_monotonic_ns, redrive=True)
            results.append(result)
        return results


__all__ = [
    "DisabledNotificationSink",
    "FakeNotificationSink",
    "NOTIFIABLE_STATES",
    "NOTIFICATION_SCHEMA",
    "NotificationDispatcher",
    "NotificationError",
    "NotificationSinkUnavailable",
    "build_notification_event",
]
