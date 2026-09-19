"""Pure, redacted view-model assembly for the Beszel × Guardian UI design.

This module does not serve HTTP, call Beszel, or execute actions. It converts a
bridge result into the documented ``guardian.ui.v1`` shape while preserving
fail-closed states for rejected events and ambiguous local objects.
"""

from __future__ import annotations

from typing import Any, Mapping


UI_SCHEMA = "guardian.ui.v1"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_candidate(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": candidate.get("kind"),
        "stable_id": candidate.get("id"),
        "display_name": candidate.get("name"),
        "confidence": candidate.get("confidence"),
    }


def _safe_signal(source: str, signal: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "source": source,
        "resource": signal.get("resource"),
        "metric": signal.get("metric"),
        "value": signal.get("value"),
        "unit": signal.get("unit"),
        "severity": signal.get("severity"),
        "window_seconds": signal.get("window_seconds"),
    }


def build_ui_view_model(
    bridge_result: Mapping[str, Any],
    *,
    policy_version: str = "local-poc-1",
    allowed_actions: list[str] | tuple[str, ...] = (),
    result: Mapping[str, Any] | None = None,
    recovery: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a redacted UI model without inferring or authorizing actions."""

    if not isinstance(bridge_result, Mapping):
        raise ValueError("bridge_result_object_required")
    bridge = _mapping(bridge_result.get("bridge"))
    beszel = _mapping(bridge_result.get("beszel_event"))
    guardian = _mapping(bridge_result.get("guardian_event"))
    if not guardian:
        raise ValueError("guardian_event_required")

    guardian_signal = _mapping(guardian.get("risk"))
    guardian_signals = _mapping(guardian.get("signals"))
    local_memory = _mapping(guardian_signals.get("memory"))
    local_memory_value = local_memory.get("available_ratio_percent")
    local_signal = {
        "resource": "memory",
        "metric": "available_ratio_percent",
        "value": local_memory_value,
        "unit": "%",
        "severity": guardian.get("state"),
        "window_seconds": guardian_signal.get("candidate_for_seconds"),
    }
    external_signal = _mapping(beszel.get("signal"))

    raw_candidates = guardian.get("object_candidates", [])
    candidates = [
        _safe_candidate(candidate)
        for candidate in raw_candidates
        if isinstance(candidate, Mapping)
    ]
    stable = [candidate for candidate in candidates if candidate.get("stable_id")]
    decision = _mapping(guardian.get("decision"))
    action = decision.get("action") if bridge.get("status") == "accepted" else "none"
    if len(stable) != 1:
        action = "escalate" if len(stable) > 1 else "none"

    if bridge.get("status") != "accepted":
        policy_decision = "observation_only"
    elif action == "escalate":
        policy_decision = "escalated"
    elif action == "none":
        policy_decision = "observation_only"
    else:
        policy_decision = "plan_generated"

    reason_codes = [str(code) for code in decision.get("reason_codes", [])]
    if bridge.get("classification") and bridge.get("classification") != "accepted":
        reason_codes.append(str(bridge["classification"]))
    if len(stable) != 1 and "ambiguous_object_identity" not in reason_codes and len(stable) > 1:
        reason_codes.append("ambiguous_object_identity")

    system = _mapping(beszel.get("system"))
    mode = decision.get("mode", "observe")
    execution = decision.get("execution", "not_applicable")
    if bridge.get("status") != "accepted":
        execution = "not_applicable"

    model = {
        "schema": UI_SCHEMA,
        "system": {
            "id": _mapping(beszel.get("source")).get("system_id"),
            "name": system.get("name") or "unknown",
            "status": "unknown",
        },
        "risk": {
            "state": guardian.get("state", "normal"),
            "confidence": "high" if bridge.get("status") == "accepted" else "low",
            "entered_at": guardian.get("observed_at"),
            "expires_at": beszel.get("expires_at"),
            "signals": [
                _safe_signal("guardian", local_signal),
                _safe_signal("beszel", external_signal),
            ],
        },
        "object": {
            "kind": stable[0]["kind"] if len(stable) == 1 else "unknown",
            "stable_id": stable[0]["stable_id"] if len(stable) == 1 else None,
            "display_name": stable[0]["display_name"] if len(stable) == 1 else None,
            "identity_confidence": stable[0]["confidence"] if len(stable) == 1 else "low",
            "protected": bool(decision.get("protected", True)),
            "candidates": len(candidates),
        },
        "policy": {
            "mode": mode,
            "decision": policy_decision,
            "reason_codes": reason_codes,
            "allowed_actions": list(allowed_actions) if policy_decision == "plan_generated" else [],
            "policy_version": policy_version,
        },
        "plan": {
            "action": action,
            "execution": execution,
            "confirmation": (
                "not_available"
                if mode == "observe" or policy_decision != "plan_generated"
                else "not_required_for_simulate"
            ),
            "created_at": guardian.get("observed_at"),
        },
        "result": dict(result) if isinstance(result, Mapping) else None,
        "recovery": dict(recovery) if isinstance(recovery, Mapping) else {"state": "pending", "checks": [], "cooldown_until": None},
    }
    return model


__all__ = ["UI_SCHEMA", "build_ui_view_model"]
