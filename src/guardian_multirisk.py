"""Joint risk arbitration for the v2 CPU/memory/disk event contract.

Each resource engine keeps its own thresholds, dwell window and attribution.
This module only combines their already-evaluated evidence into one event-level
state and one plan boundary.  It never executes an action.

The important fail-closed rule is that a target confirmed by one resource is
not enough when another active resource points at a different target or has no
trustworthy attribution.  A joint plan is allowed only when every active
resource has the same stable target identity.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


ACTIVE_STATES = frozenset({"warning", "critical", "critical_confirmed"})
DEGRADED_STATES = frozenset({"degraded_observability"})
SEVERITY_ORDER = {"normal": 0, "recovered": 1, "warning": 2, "critical": 3, "critical_confirmed": 4}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _risk(evaluation: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(evaluation.get("risk"))


def _risk_state(evaluation: Mapping[str, Any]) -> str:
    risk = _risk(evaluation)
    state = risk.get("state") or evaluation.get("state") or "normal"
    return str(state)


def _attribution(evaluation: Mapping[str, Any]) -> Mapping[str, Any]:
    return _mapping(evaluation.get("attribution"))


def _target_id(attribution: Mapping[str, Any]) -> str | None:
    if attribution.get("state") != "TARGET_CONFIRMED":
        return None
    target = _mapping(attribution.get("target"))
    for key in ("id", "stable_id", "identity"):
        value = target.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _quality_flags(evaluation: Mapping[str, Any]) -> list[str]:
    flags: list[str] = []
    risk = _risk(evaluation)
    for source in (risk, _attribution(evaluation)):
        raw = source.get("quality_flags", [])
        if isinstance(raw, list):
            flags.extend(str(flag) for flag in raw if flag)
    return sorted(set(flags))


def _event_state(
    active_states: list[str],
    *,
    degraded: bool,
    recovered: bool,
) -> str:
    if active_states:
        # Keep the risk visible even when the joint plan is escalated because
        # attribution or another resource's quality is unsafe.
        return "escalated" if degraded else max(active_states, key=lambda state: SEVERITY_ORDER.get(state, 0))
    if degraded:
        return "degraded_observability"
    return "recovered" if recovered else "normal"


def build_joint_decision(
    resource_evaluations: Mapping[str, Mapping[str, Any]],
    *,
    mode: str,
    simulate_action: str,
    protected: bool,
    allowed_actions: Iterable[str],
) -> dict[str, Any]:
    """Return one authoritative event-level decision without executing it.

    ``resource_evaluations`` values contain ``risk`` and ``attribution``
    mappings.  The function accepts partial data so missing/invalid channels
    are represented as a degraded, non-actionable result instead of raising.
    """

    normalized = {
        str(kind): _mapping(value)
        for kind, value in resource_evaluations.items()
        if isinstance(kind, str)
    }
    active: list[tuple[str, Mapping[str, Any]]] = []
    active_states: list[str] = []
    quality_degraded = False
    recovered = False
    quality_flags: list[str] = []
    for kind, evaluation in normalized.items():
        state = _risk_state(evaluation)
        if state in ACTIVE_STATES:
            active.append((kind, evaluation))
            active_states.append(state)
        elif state in DEGRADED_STATES or _risk(evaluation).get("quality_status") == "degraded":
            quality_degraded = True
        elif state == "recovered":
            recovered = True
        quality_flags.extend(_quality_flags(evaluation))

    confirmed: dict[str, list[str]] = {}
    unconfirmed_active: list[str] = []
    target_by_resource: dict[str, Mapping[str, Any]] = {}
    attribution_states: dict[str, str] = {}
    for kind, evaluation in active:
        attribution = _attribution(evaluation)
        attribution_state = str(attribution.get("state") or "UNKNOWN")
        attribution_states[kind] = attribution_state
        target_id = _target_id(attribution)
        if target_id is None:
            unconfirmed_active.append(kind)
            continue
        confirmed.setdefault(target_id, []).append(kind)
        target = _mapping(attribution.get("target"))
        target_by_resource[kind] = target

    reason_codes: list[str] = []
    target: Mapping[str, Any] | None = None
    target_state = "NO_TARGET"
    if not active:
        reason_codes.append("no_active_resource_risk")
    elif quality_degraded:
        reason_codes.append("multi_resource_observability_degraded")
        target_state = "DEGRADED_OBSERVABILITY"
    elif len(confirmed) > 1:
        reason_codes.append("multi_resource_ambiguous")
        target_state = "MULTI_RESOURCE_AMBIGUOUS"
    elif unconfirmed_active:
        if len(active) > 1 or confirmed:
            reason_codes.append("multi_resource_ambiguous")
            target_state = "MULTI_RESOURCE_AMBIGUOUS"
        else:
            reason_codes.append("target_attribution_not_confirmed")
            target_state = attribution_states.get(unconfirmed_active[0], "NO_TARGET")
    else:
        target_state = "TARGET_CONFIRMED"
        reason_codes.append("multi_resource_target_confirmed")
        target_id = next(iter(confirmed), None)
        if target_id is not None:
            target = target_by_resource.get(confirmed[target_id][0])

    state = _event_state(active_states, degraded=quality_degraded, recovered=recovered)
    execution = "not_applicable" if mode == "observe" else "not_executed" if mode == "simulate" else "pending_controller"
    decision: dict[str, Any] = {
        "mode": mode,
        "resource_kind": "multi_resource",
        "action": "none",
        "execution": execution,
        "reason_codes": sorted(set(reason_codes)),
        "target_state": target_state,
        "target_id": target.get("id") if isinstance(target, Mapping) else None,
        "active_resources": [kind for kind, _ in active],
        "quality_flags": sorted(set(quality_flags)),
    }

    if mode in {"simulate", "enforce"} and active:
        if target_state != "TARGET_CONFIRMED":
            decision["action"] = "escalate"
        elif protected:
            decision["action"] = "escalate"
            decision["reason_codes"].append("protected_object")
        elif simulate_action not in set(allowed_actions):
            decision["action"] = "escalate"
            decision["reason_codes"].append("action_not_allowlisted")
        else:
            decision["action"] = simulate_action
            decision["reason_codes"].append(
                "multi_resource_simulate_only" if mode == "simulate" else "multi_resource_enforce_requires_controller"
            )
    elif mode == "observe":
        decision["reason_codes"].append("observe_only")

    decision["reason_codes"] = sorted(set(decision["reason_codes"]))
    return {
        "state": state,
        "active_resources": [kind for kind, _ in active],
        "target_state": target_state,
        "target": dict(target) if isinstance(target, Mapping) else None,
        "targets_by_resource": {
            kind: dict(value) for kind, value in target_by_resource.items()
        },
        "attribution_states": attribution_states,
        "quality_flags": sorted(set(quality_flags)),
        "decision": decision,
    }


__all__ = ["build_joint_decision"]
