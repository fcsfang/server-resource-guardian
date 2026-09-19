"""Safe bridge from normalized Beszel events to Guardian observe/simulate.

The bridge treats Beszel as an external observation source. It validates
freshness and ordering, then asks the local Guardian observer to make the
actual risk and policy decision. A Beszel alert can never select a Docker
target or authorize an action by itself; rejected or low-confidence inputs
are retained only as read-only evidence.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Iterable, Mapping

from .beszel_adapter import (
    BeszelEventWindow,
    is_actionable_observation,
    utc_now,
)
from .guardian_observer import build_event


BRIDGE_SCHEMA = "guardian.beszel.bridge.v1"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_external_summary(event: Mapping[str, Any]) -> dict[str, Any]:
    """Copy only normalized, non-secret fields into the local audit event."""

    source = _mapping(event.get("source"))
    obj = _mapping(event.get("object"))
    signal = _mapping(event.get("signal"))
    integrity = _mapping(event.get("integrity"))
    return {
        "event_id": event.get("event_id"),
        "observed_at": event.get("observed_at"),
        "received_at": event.get("received_at"),
        "expires_at": event.get("expires_at"),
        "source": {
            "kind": source.get("kind"),
            "hub_id": source.get("hub_id"),
            "system_id": source.get("system_id"),
            "record_id": source.get("record_id"),
        },
        "object": {
            "kind": obj.get("kind"),
            "stable_id": obj.get("stable_id"),
            "name": obj.get("name"),
            "identity_confidence": obj.get("identity_confidence"),
        },
        "signal": {
            "resource": signal.get("resource"),
            "metric": signal.get("metric"),
            "value": signal.get("value"),
            "unit": signal.get("unit"),
            "severity": signal.get("severity"),
            "reason_codes": list(signal.get("reason_codes") or []),
        },
        "integrity": {
            "stale": bool(integrity.get("stale")),
            "redacted": integrity.get("redacted") is True,
        },
    }


def _read_only_guardian_event(
    local_observation: Mapping[str, Any],
    *,
    reason_codes: Iterable[str],
) -> dict[str, Any]:
    """Emit a local observation event with no action plan."""

    event = build_event(dict(local_observation), mode="observe")
    event["decision"]["reason_codes"] = list(event["decision"].get("reason_codes", []))
    event["decision"]["reason_codes"].extend(str(code) for code in reason_codes)
    event["decision"]["action"] = "none"
    event["decision"]["execution"] = "not_applicable"
    return event


def bridge_beszel_event(
    event: Mapping[str, Any],
    local_observation: Mapping[str, Any],
    *,
    window: BeszelEventWindow | None = None,
    now: dt.datetime | None = None,
    mode: str = "observe",
    simulate_action: str = "graceful_stop",
    protected: bool = True,
    allowed_actions: Iterable[str] = (),
) -> dict[str, Any]:
    """Bridge one normalized Beszel event into local observe/simulate.

    ``mode=simulate`` only creates a plan. The local observer still decides
    whether a risk is actionable, and its Docker candidates remain the source
    of object identity. The Beszel object is evidence, never an action target.
    """

    if not isinstance(event, Mapping):
        raise ValueError("beszel_event_object_required")
    if not isinstance(local_observation, Mapping):
        raise ValueError("local_observation_object_required")
    if mode not in {"observe", "simulate"}:
        raise ValueError("bridge_mode_must_be_observe_or_simulate")

    event_window = window or BeszelEventWindow()
    classification = event_window.classify(event, now=now)
    external = _safe_external_summary(event)

    if classification != "accepted":
        guardian_event = _read_only_guardian_event(
            local_observation,
            reason_codes=(classification,),
        )
        return {
            "schema": BRIDGE_SCHEMA,
            "bridge": {
                "status": "rejected",
                "classification": classification,
                "action_authorized": False,
            },
            "beszel_event": external,
            "guardian_event": guardian_event,
        }

    if not is_actionable_observation(event):
        guardian_event = _read_only_guardian_event(
            local_observation,
            reason_codes=("beszel_observation_only",),
        )
        return {
            "schema": BRIDGE_SCHEMA,
            "bridge": {
                "status": "observation_only",
                "classification": classification,
                "action_authorized": False,
            },
            "beszel_event": external,
            "guardian_event": guardian_event,
        }

    guardian_event = build_event(
        dict(local_observation),
        mode=mode,
        simulate_action=simulate_action,
        protected=protected,
        allowed_actions=allowed_actions,
    )
    guardian_event["evidence"]["beszel_event_id"] = event.get("event_id")
    guardian_event["evidence"]["beszel_signal"] = external["signal"]
    guardian_event["decision"]["reason_codes"] = list(
        guardian_event["decision"].get("reason_codes", [])
    )
    guardian_event["decision"]["reason_codes"].append("beszel_event_accepted")

    return {
        "schema": BRIDGE_SCHEMA,
        "bridge": {
            "status": "accepted",
            "classification": classification,
            "action_authorized": False,
            "local_confirmation_required": True,
        },
        "beszel_event": external,
        "guardian_event": guardian_event,
    }


def bridge_beszel_events(
    events: Iterable[Mapping[str, Any]],
    local_observation: Mapping[str, Any],
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """Bridge a replayable page of normalized events with one local sample."""

    window = kwargs.pop("window", None) or BeszelEventWindow()
    return [
        bridge_beszel_event(
            event,
            local_observation,
            window=window,
            **kwargs,
        )
        for event in events
    ]


__all__ = [
    "BRIDGE_SCHEMA",
    "bridge_beszel_event",
    "bridge_beszel_events",
]
