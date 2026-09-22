"""Read-only administrator status for the installed Guardian runtime."""

from __future__ import annotations

import json
import subprocess
from collections import deque
from pathlib import Path
from typing import Any, Callable, Mapping


STATUS_SCHEMA = "guardian.status.v1"
ALERT_STATES = {
    "warning",
    "critical",
    "critical_confirmed",
    "escalated",
    "degraded_observability",
}
DEFAULT_UNIT = "guardian-runtime.service"
DEFAULT_CONFIG = Path("/etc/guardian/guardian.json")
DEFAULT_READINESS = Path("/run/guardian-runtime/ready")
DEFAULT_AUDIT = Path("/var/lib/guardian/runtime/audit/events.jsonl")
DEFAULT_BROKER_MARKER = Path("/etc/guardian/broker.enabled")
DEFAULT_BROKER_SOCKET = Path("/run/guardian-broker/broker.sock")
DEFAULT_RESERVE_BROKER_MARKER = Path("/etc/guardian/reserve-broker.enabled")
DEFAULT_RESERVE_BROKER_SOCKET = Path("/run/guardian-reserve-broker/reserve.sock")
DEFAULT_COLLECTOR_SOCKET = Path("/run/guardian-collector/collector.sock")


def _systemctl(operation: str, unit: str, runner: Callable[..., Any]) -> str:
    try:
        result = runner(
            ["systemctl", operation, unit],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unavailable:{type(exc).__name__}"
    value = (result.stdout or "").strip()
    if result.returncode == 0 and value:
        return value
    if operation == "is-active" and result.returncode != 0:
        return value or "inactive"
    if operation == "is-enabled" and result.returncode != 0:
        return value or "disabled"
    return value or f"error:{result.returncode}"


def _read_json(path: Path) -> tuple[Mapping[str, Any] | None, str | None]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"config_unavailable:{type(exc).__name__}"
    if not isinstance(value, Mapping):
        return None, "config_invalid:root_not_object"
    return value, None


def _tail_jsonl(path: Path, *, limit: int = 200) -> tuple[list[Mapping[str, Any]], str | None]:
    records: deque[Mapping[str, Any]] = deque(maxlen=limit)
    try:
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, Mapping):
                    records.append(value)
    except (OSError, UnicodeDecodeError) as exc:
        return [], f"audit_unavailable:{type(exc).__name__}"
    return list(records), None


def _audit_summary(path: Path) -> dict[str, Any]:
    records, error = _tail_jsonl(path)
    alerts: list[dict[str, Any]] = []
    latest_state: str | None = None
    latest_observed_at: str | None = None

    def base_record(record: Mapping[str, Any]) -> Mapping[str, Any]:
        nested = record.get("event")
        return nested if record.get("schema") == "guardian.runtime.result.v1" and isinstance(nested, Mapping) else record

    for record in records:
        observed = base_record(record)
        is_result_record = record.get("schema") == "guardian.runtime.result.v1"
        risk = observed.get("risk")
        risk_map = risk if isinstance(risk, Mapping) else {}
        state = risk_map.get("state") or observed.get("state")
        if isinstance(state, str):
            latest_state = state
            observed_at = observed.get("observed_at")
            latest_observed_at = observed_at if isinstance(observed_at, str) else latest_observed_at
            if state in ALERT_STATES and not is_result_record:
                alerts.append({"state": state, "observed_at": observed_at, "event_id": observed.get("event_id")})
    if error:
        return {
            "status": "unavailable",
            "path": str(path),
            "recent_alerts": [],
            "latest_state": None,
            "latest_observed_at": None,
            "error": error,
        }
    latest_record = records[-1] if records else {}
    latest = base_record(latest_record)
    presentation = latest.get("presentation") if isinstance(latest.get("presentation"), Mapping) else {}
    presented_ranking = presentation.get("candidate_ranking") if isinstance(presentation, Mapping) else {}
    presented_simulation = presentation.get("simulation") if isinstance(presentation, Mapping) else {}
    presented_protection = presentation.get("protection") if isinstance(presentation, Mapping) else {}
    raw_candidates = latest.get("object_candidates")
    candidates = raw_candidates if isinstance(raw_candidates, list) else []
    candidate_summary = []
    for item in candidates[:10]:
        if not isinstance(item, Mapping):
            continue
        candidate_summary.append(
            {
                "name": item.get("name"),
                "score": item.get("score"),
                "confidence": item.get("confidence"),
                "mapping_errors": list(item.get("mapping_errors") or []),
            }
        )
    decision = latest.get("decision") if isinstance(latest.get("decision"), Mapping) else {}
    if isinstance(presented_ranking, Mapping) and isinstance(presented_ranking.get("candidates"), list):
        candidate_summary = [item for item in presented_ranking["candidates"] if isinstance(item, Mapping)][:20]
    if isinstance(presented_simulation, Mapping) and presented_simulation:
        decision = presented_simulation
    runtime_result = latest_record.get("runtime_result") if isinstance(latest_record, Mapping) else None
    action_summary: dict[str, Any] = {
        "state": None,
        "action": None,
        "execution_semantics": None,
        "semantic_state": None,
        "recovery": None,
        "reserve_recovery": None,
        "reason_codes": [],
    }
    if isinstance(runtime_result, Mapping):
        action_summary["state"] = runtime_result.get("state")
        action_summary["execution_semantics"] = runtime_result.get("execution_semantics")
        action_summary["semantic_state"] = runtime_result.get("semantic_state")
        action_summary["reason_codes"] = list(runtime_result.get("reason_codes") or [])
        broker = runtime_result.get("broker")
        if isinstance(broker, Mapping):
            action_result = broker.get("action_result")
            if isinstance(action_result, Mapping):
                action_summary["action"] = action_result.get("action")
            verification = broker.get("verification")
            if isinstance(verification, Mapping):
                action_summary["recovery"] = verification
        reserve_recovery = runtime_result.get("reserve_recovery")
        if isinstance(reserve_recovery, Mapping):
            action_summary["reserve_recovery"] = {
                "action": reserve_recovery.get("action"),
                "state": reserve_recovery.get("state"),
                "execution": reserve_recovery.get("execution"),
                "reason_codes": list(reserve_recovery.get("reason_codes") or []),
                "host_state": runtime_result.get("host_state"),
            }
    return {
        "status": "ok",
        "path": str(path),
        "recent_alerts": alerts[-10:],
        "recent_alert_count": len(alerts),
        "latest_state": latest_state,
        "latest_observed_at": latest_observed_at,
        "candidate_ranking": {
            "status": presented_ranking.get("status") if isinstance(presented_ranking, Mapping) else "ok",
            "resource_kind": presented_ranking.get("resource_kind") if isinstance(presented_ranking, Mapping) else None,
            "count": presented_ranking.get("count", len(candidates)) if isinstance(presented_ranking, Mapping) else len(candidates),
            "top": presented_ranking.get("top") if isinstance(presented_ranking, Mapping) else (candidate_summary[0] if candidate_summary else None),
            "candidates": candidate_summary,
        },
        "simulation": {
            "mode": decision.get("mode"),
            "action": decision.get("action"),
            "execution": decision.get("execution"),
            "reason_codes": list(decision.get("reason_codes") or []),
        },
        "action": action_summary,
        "protection": {
            "protected_candidates": list(presented_protection.get("protected_candidates") or []) if isinstance(presented_protection, Mapping) else [],
            "configured_systemd_units": presented_protection.get("configured_systemd_units") if isinstance(presented_protection, Mapping) else None,
            "configured_container_labels": presented_protection.get("configured_container_labels") if isinstance(presented_protection, Mapping) else None,
        },
    }


def build_status(
    *,
    config_path: Path = DEFAULT_CONFIG,
    readiness_path: Path = DEFAULT_READINESS,
    audit_path: Path = DEFAULT_AUDIT,
    broker_marker: Path = DEFAULT_BROKER_MARKER,
    broker_socket: Path = DEFAULT_BROKER_SOCKET,
    reserve_broker_marker: Path = DEFAULT_RESERVE_BROKER_MARKER,
    reserve_broker_socket: Path = DEFAULT_RESERVE_BROKER_SOCKET,
    collector_socket: Path = DEFAULT_COLLECTOR_SOCKET,
    unit: str = DEFAULT_UNIT,
    runner: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    config, config_error = _read_json(config_path)
    mode = config.get("agent", {}).get("mode") if config else None
    actions_enabled = config.get("actions", {}).get("enabled") if config else None
    runtime_active = _systemctl("is-active", unit, runner)
    runtime_enabled = _systemctl("is-enabled", unit, runner)
    broker_active = _systemctl("is-active", "guardian-broker.service", runner)
    broker_enabled = _systemctl("is-enabled", "guardian-broker.service", runner)
    reserve_broker_active = _systemctl("is-active", "guardian-reserve-broker.service", runner)
    reserve_broker_enabled = _systemctl("is-enabled", "guardian-reserve-broker.service", runner)
    collector_active = _systemctl("is-active", "guardian-collector.service", runner)
    collector_enabled = _systemctl("is-enabled", "guardian-collector.service", runner)
    try:
        readiness = readiness_path.read_text(encoding="utf-8").strip() or "empty"
    except (OSError, UnicodeDecodeError):
        readiness = "missing"
    audit = _audit_summary(audit_path)
    protection = config.get("protection", {}) if isinstance(config, Mapping) else {}
    protected_units = protection.get("systemd_units", []) if isinstance(protection, Mapping) else []
    protected_labels = protection.get("container_labels", []) if isinstance(protection, Mapping) else []
    automatic_actions = "disabled" if mode == "observe" and actions_enabled is False else "enabled_or_unknown"
    broker_closed = broker_active == "inactive" and not broker_marker.exists() and not broker_socket.exists()
    reserve_broker_closed = reserve_broker_active == "inactive" and not reserve_broker_marker.exists() and not reserve_broker_socket.exists()
    collector_socket_present = collector_socket.exists()
    collector_online = collector_active == "active" and collector_socket_present
    ready = runtime_active == "active" and readiness == "runtime:ready:observe"
    overall = "healthy" if ready and automatic_actions == "disabled" and broker_closed and reserve_broker_closed and collector_online else "degraded"
    if config_error or mode is None:
        overall = "unknown"
    return {
        "schema": STATUS_SCHEMA,
        "overall": overall,
        "runtime": {
            "unit": unit,
            "active": runtime_active,
            "enabled": runtime_enabled,
            "readiness": readiness,
        },
        "mode": mode or "unknown",
        "automatic_actions": automatic_actions,
        "recent_alerts": audit,
        "broker": {
            "active": broker_active,
            "enabled": broker_enabled,
            "marker_present": broker_marker.exists(),
            "socket_present": broker_socket.exists(),
            "closed": broker_closed,
        },
        "reserve_broker": {
            "active": reserve_broker_active,
            "enabled": reserve_broker_enabled,
            "marker_present": reserve_broker_marker.exists(),
            "socket_present": reserve_broker_socket.exists(),
            "closed": reserve_broker_closed,
        },
        "collector": {
            "active": collector_active,
            "enabled": collector_enabled,
            "socket_present": collector_socket_present,
            "online": collector_online,
        },
        "config": {"path": str(config_path), "error": config_error},
        "protection": {
            "systemd_units": len(protected_units) if isinstance(protected_units, list) else 0,
            "container_labels": len(protected_labels) if isinstance(protected_labels, list) else 0,
        },
    }


def format_status(value: Mapping[str, Any]) -> str:
    runtime = value["runtime"]
    alerts = value["recent_alerts"]
    latest = alerts.get("latest_state") or "none"
    ranking = alerts.get("candidate_ranking") or {}
    top = ranking.get("top") or {}
    simulation = alerts.get("simulation") or {}
    protection = alerts.get("protection") or {}
    top_name = top.get("name") or "none"
    protected_candidates = protection.get("protected_candidates") or []
    protected_text = "none"
    if protected_candidates:
        protected_text = ", ".join(
            f"{item.get('name', 'unknown')}({';'.join(item.get('reasons') or ['protected'])})"
            for item in protected_candidates[:5]
            if isinstance(item, Mapping)
        ) or "none"
    return "\n".join(
        (
            f"Guardian: {value['overall']}",
            f"Runtime: {runtime['active']} ({runtime['enabled']}), ready={runtime['readiness']}",
            f"Mode: {value['mode']} (read-only when observe)",
            f"Automatic actions: {value['automatic_actions']} (no container will be stopped when disabled)",
            f"Recent local risk: {latest} (alerts={alerts.get('recent_alert_count', 0)})",
            f"Candidates: {ranking.get('count', 0)}, highest={top_name}",
            f"Simulation: action={simulation.get('action') or 'none'}, execution={simulation.get('execution') or 'unknown'}, target={simulation.get('target_name') or 'none'}",
            f"Action result: state={alerts.get('action', {}).get('state') or 'none'}, action={alerts.get('action', {}).get('action') or 'none'}, recovery={alerts.get('action', {}).get('recovery', {}).get('overall_state') if isinstance(alerts.get('action', {}).get('recovery'), dict) else 'none'} (business health is separate)",
            f"Reserve recovery: state={alerts.get('action', {}).get('reserve_recovery', {}).get('state') if isinstance(alerts.get('action', {}).get('reserve_recovery'), dict) else 'none'}, execution={alerts.get('action', {}).get('reserve_recovery', {}).get('execution') if isinstance(alerts.get('action', {}).get('reserve_recovery'), dict) else 'none'}",
            f"Protection: units={value['protection']['systemd_units']}, labels={value['protection']['container_labels']}, protected candidates={len(protected_candidates)}",
            f"Protected candidates: {protected_text}",
            f"Collector: {'online (read-only container data)' if value['collector']['online'] else 'review_required'}",
            f"Broker: {'closed (no automatic action path)' if value['broker']['closed'] else 'review_required'}",
            f"Reserve recovery boundary: {'closed (no automatic reserve release)' if value['reserve_broker']['closed'] else 'review_required'}",
        )
    )


def exit_code(value: Mapping[str, Any]) -> int:
    return 0 if value.get("overall") == "healthy" else 1 if value.get("overall") == "degraded" else 2


__all__ = ["STATUS_SCHEMA", "build_status", "exit_code", "format_status"]
