"""Strict, fail-closed configuration for the Guardian prototype.

The runtime format is JSON so the first productionization step does not add a
YAML dependency. ``config/guardian.example.yaml`` remains a historical
discussion sample; ``config/guardian.example.json`` is the canonical local
sample for this schema.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "guardian.config.v1"
_ACTIONS = {"graceful_stop", "restart", "terminate"}


class ConfigError(ValueError):
    """Raised when configuration cannot safely be used."""


def _exact_keys(value: Mapping[str, Any], expected: set[str], path: str) -> None:
    actual = set(value)
    missing = expected - actual
    unknown = actual - expected
    if missing:
        raise ConfigError(f"{path}:missing_fields:{','.join(sorted(missing))}")
    if unknown:
        raise ConfigError(f"{path}:unknown_fields:{','.join(sorted(unknown))}")


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{path}:object_required")
    return value


def _bool(value: Any, path: str) -> bool:
    if type(value) is not bool:
        raise ConfigError(f"{path}:boolean_required")
    return value


def _number(value: Any, path: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{path}:number_required")
    return float(value)


def _string(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value):
        raise ConfigError(f"{path}:non_empty_string_required")
    return value


def _strings(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise ConfigError(f"{path}:string_list_required")
    if len(set(value)) != len(value):
        raise ConfigError(f"{path}:duplicates_not_allowed")
    return list(value)


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


class GuardianConfig:
    """Validated immutable-by-convention Guardian configuration."""

    def __init__(self, value: Mapping[str, Any], source: str) -> None:
        self._value = copy.deepcopy(dict(value))
        self.source = source

    @property
    def schema(self) -> str:
        return self._value["schema"]

    @property
    def version(self) -> int:
        return self._value["version"]

    @property
    def mode(self) -> str:
        return self._value["agent"]["mode"]

    @property
    def interval_seconds(self) -> float:
        return self._value["agent"]["interval_seconds"]

    @property
    def snapshot_directory(self) -> str | None:
        return self._value["agent"]["snapshot_directory"]

    @property
    def snapshot_max_total_bytes(self) -> int:
        return self._value["agent"]["snapshot_max_total_bytes"]

    @property
    def warning_available_percent(self) -> float:
        return self._value["risk"]["memory"]["warning_available_percent"]

    @property
    def critical_available_percent(self) -> float:
        return self._value["risk"]["memory"]["critical_available_percent"]

    @property
    def warning_for_seconds(self) -> float:
        return self._value["risk"]["memory"]["warning_for_seconds"]

    @property
    def critical_for_seconds(self) -> float:
        return self._value["risk"]["memory"]["critical_for_seconds"]

    @property
    def required_samples(self) -> int:
        return self._value["risk"]["composite"]["required_samples"]

    @property
    def max_sample_age_seconds(self) -> float:
        return self._value["risk"]["composite"]["max_sample_age_seconds"]

    @property
    def trend_warning_bytes_per_second(self) -> float:
        return self._value["risk"]["composite"]["trend_warning_bytes_per_second"]

    @property
    def trend_critical_bytes_per_second(self) -> float:
        return self._value["risk"]["composite"]["trend_critical_bytes_per_second"]

    @property
    def psi_memory_some_warning_avg10(self) -> float:
        return self._value["risk"]["composite"]["psi_memory_some_warning_avg10"]

    @property
    def psi_memory_full_critical_avg10(self) -> float:
        return self._value["risk"]["composite"]["psi_memory_full_critical_avg10"]

    @property
    def swap_used_warning_percent(self) -> float:
        return self._value["risk"]["composite"]["swap_used_warning_percent"]

    @property
    def swap_used_critical_percent(self) -> float:
        return self._value["risk"]["composite"]["swap_used_critical_percent"]

    @property
    def allowed_actions(self) -> tuple[str, ...]:
        return tuple(self._value["actions"]["allow"])

    @property
    def graceful_timeout_seconds(self) -> int:
        return self._value["actions"]["graceful_timeout_seconds"]

    @property
    def cooldown_seconds(self) -> float:
        return self._value["actions"]["cooldown_seconds"]

    @property
    def max_actions_per_host_per_hour(self) -> int:
        return self._value["actions"]["max_actions_per_host_per_hour"]

    @property
    def recovery_max_wait_seconds(self) -> float:
        return self._value["recovery"]["max_wait_seconds"]

    @property
    def recovery_poll_interval_seconds(self) -> float:
        return self._value["recovery"]["poll_interval_seconds"]

    @property
    def require_business_health(self) -> bool:
        return self._value["recovery"]["require_business_health"]

    @property
    def config_digest(self) -> str:
        return hashlib.sha256(_canonical(self._value)).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._value)


def validate_config(value: Mapping[str, Any], *, source: str = "<mapping>") -> GuardianConfig:
    root = _mapping(value, "config")
    _exact_keys(root, {"schema", "version", "agent", "risk", "actions", "protection", "recovery", "audit"}, "config")
    if root["schema"] != SCHEMA:
        raise ConfigError(f"config.schema:expected:{SCHEMA}")
    if type(root["version"]) is not int or root["version"] != 1:
        raise ConfigError("config.version:unsupported")

    agent = _mapping(root["agent"], "agent")
    _exact_keys(agent, {"mode", "interval_seconds", "snapshot_directory", "snapshot_max_total_bytes"}, "agent")
    if agent["mode"] not in {"observe", "simulate", "enforce"}:
        raise ConfigError("agent.mode:invalid")
    interval = _number(agent["interval_seconds"], "agent.interval_seconds")
    if not 0.1 <= interval <= 60:
        raise ConfigError("agent.interval_seconds:must_be_between_0.1_and_60")
    if agent["snapshot_directory"] is not None:
        _string(agent["snapshot_directory"], "agent.snapshot_directory")
    max_snapshot = agent["snapshot_max_total_bytes"]
    if type(max_snapshot) is not int or not 0 <= max_snapshot <= 10 * 1024 * 1024 * 1024:
        raise ConfigError("agent.snapshot_max_total_bytes:invalid")

    risk = _mapping(root["risk"], "risk")
    _exact_keys(risk, {"memory", "composite"}, "risk")
    memory = _mapping(risk["memory"], "risk.memory")
    _exact_keys(memory, {"warning_available_percent", "critical_available_percent", "warning_for_seconds", "critical_for_seconds"}, "risk.memory")
    warning = _number(memory["warning_available_percent"], "risk.memory.warning_available_percent")
    critical = _number(memory["critical_available_percent"], "risk.memory.critical_available_percent")
    warning_for = _number(memory["warning_for_seconds"], "risk.memory.warning_for_seconds")
    critical_for = _number(memory["critical_for_seconds"], "risk.memory.critical_for_seconds")
    if not 0 < critical <= warning < 100:
        raise ConfigError("risk.memory:critical_must_be_positive_and_not_above_warning")
    if warning_for <= 0 or critical_for <= 0:
        raise ConfigError("risk.memory:dwell_windows_must_be_positive")

    composite = _mapping(risk["composite"], "risk.composite")
    _exact_keys(
        composite,
        {
            "required_samples",
            "max_sample_age_seconds",
            "trend_warning_bytes_per_second",
            "trend_critical_bytes_per_second",
            "psi_memory_some_warning_avg10",
            "psi_memory_full_critical_avg10",
            "swap_used_warning_percent",
            "swap_used_critical_percent",
        },
        "risk.composite",
    )
    required_samples = composite["required_samples"]
    if type(required_samples) is not int or not 1 <= required_samples <= 60:
        raise ConfigError("risk.composite.required_samples:invalid")
    max_age = _number(composite["max_sample_age_seconds"], "risk.composite.max_sample_age_seconds")
    trend_warning = _number(
        composite["trend_warning_bytes_per_second"],
        "risk.composite.trend_warning_bytes_per_second",
    )
    trend_critical = _number(
        composite["trend_critical_bytes_per_second"],
        "risk.composite.trend_critical_bytes_per_second",
    )
    psi_some = _number(composite["psi_memory_some_warning_avg10"], "risk.composite.psi_memory_some_warning_avg10")
    psi_full = _number(composite["psi_memory_full_critical_avg10"], "risk.composite.psi_memory_full_critical_avg10")
    swap_warning = _number(composite["swap_used_warning_percent"], "risk.composite.swap_used_warning_percent")
    swap_critical = _number(composite["swap_used_critical_percent"], "risk.composite.swap_used_critical_percent")
    if max_age <= 0 or max_age > 300:
        raise ConfigError("risk.composite.max_sample_age_seconds:invalid")
    if trend_warning < 0 or trend_critical < trend_warning:
        raise ConfigError("risk.composite:trend_thresholds_invalid")
    if not 0 <= psi_some <= 100 or not 0 <= psi_full <= 100:
        raise ConfigError("risk.composite:psi_thresholds_invalid")
    if not 0 <= swap_warning <= swap_critical <= 100:
        raise ConfigError("risk.composite:swap_thresholds_invalid")

    actions = _mapping(root["actions"], "actions")
    _exact_keys(actions, {"enabled", "require_approval", "graceful_timeout_seconds", "cooldown_seconds", "max_actions_per_host_per_hour", "allow"}, "actions")
    enabled = _bool(actions["enabled"], "actions.enabled")
    require_approval = _bool(actions["require_approval"], "actions.require_approval")
    timeout = actions["graceful_timeout_seconds"]
    if type(timeout) is not int or not 1 <= timeout <= 120:
        raise ConfigError("actions.graceful_timeout_seconds:must_be_between_1_and_120")
    cooldown = _number(actions["cooldown_seconds"], "actions.cooldown_seconds")
    if cooldown < 0:
        raise ConfigError("actions.cooldown_seconds:must_be_non_negative")
    max_actions = actions["max_actions_per_host_per_hour"]
    if type(max_actions) is not int or not 0 <= max_actions <= 100:
        raise ConfigError("actions.max_actions_per_host_per_hour:invalid")
    allow = _strings(actions["allow"], "actions.allow")
    unknown_actions = set(allow) - _ACTIONS
    if unknown_actions:
        raise ConfigError("actions.allow:unknown_action:" + ",".join(sorted(unknown_actions)))
    if agent["mode"] == "enforce":
        if not enabled:
            raise ConfigError("agent.mode:enforce_requires_actions_enabled")
        if not require_approval:
            raise ConfigError("agent.mode:enforce_requires_approval")
        if not allow:
            raise ConfigError("agent.mode:enforce_requires_non_empty_allowlist")
        if "terminate" in allow:
            raise ConfigError("actions.allow:automatic_terminate_forbidden")

    protection = _mapping(root["protection"], "protection")
    _exact_keys(protection, {"systemd_units", "executable_paths", "container_labels"}, "protection")
    for key in ("systemd_units", "executable_paths", "container_labels"):
        _strings(protection[key], f"protection.{key}")

    recovery = _mapping(root["recovery"], "recovery")
    _exact_keys(recovery, {"max_wait_seconds", "poll_interval_seconds", "require_business_health"}, "recovery")
    max_wait = _number(recovery["max_wait_seconds"], "recovery.max_wait_seconds")
    poll = _number(recovery["poll_interval_seconds"], "recovery.poll_interval_seconds")
    if max_wait <= 0 or poll <= 0 or poll > max_wait:
        raise ConfigError("recovery:invalid_poll_window")
    _bool(recovery["require_business_health"], "recovery.require_business_health")

    audit = _mapping(root["audit"], "audit")
    _exact_keys(audit, {"local_buffer_enabled", "remote_export_enabled", "max_total_bytes"}, "audit")
    _bool(audit["local_buffer_enabled"], "audit.local_buffer_enabled")
    if _bool(audit["remote_export_enabled"], "audit.remote_export_enabled"):
        raise ConfigError("audit.remote_export_enabled:not_implemented")
    if type(audit["max_total_bytes"]) is not int or not 0 < audit["max_total_bytes"] <= 10 * 1024 * 1024 * 1024:
        raise ConfigError("audit.max_total_bytes:invalid")

    return GuardianConfig(root, source)


def load_config(path: Path) -> GuardianConfig:
    """Load and validate one canonical JSON configuration file."""

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"config_file_not_found:{path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config_invalid_json:{path}:{exc.lineno}:{exc.colno}") from exc
    return validate_config(raw, source=str(path))


def safe_defaults() -> GuardianConfig:
    """Return the only implicit configuration: observe-only and no actions."""

    return validate_config(
        {
            "schema": SCHEMA,
            "version": 1,
            "agent": {
                "mode": "observe",
                "interval_seconds": 5,
                "snapshot_directory": None,
                "snapshot_max_total_bytes": 256 * 1024 * 1024,
            },
            "risk": {
                "memory": {
                    "warning_available_percent": 15,
                    "critical_available_percent": 10,
                    "warning_for_seconds": 180,
                    "critical_for_seconds": 30,
                },
                "composite": {
                    "required_samples": 2,
                    "max_sample_age_seconds": 15,
                    "trend_warning_bytes_per_second": 1048576,
                    "trend_critical_bytes_per_second": 16777216,
                    "psi_memory_some_warning_avg10": 1,
                    "psi_memory_full_critical_avg10": 0.5,
                    "swap_used_warning_percent": 25,
                    "swap_used_critical_percent": 50,
                },
            },
            "actions": {
                "enabled": False,
                "require_approval": True,
                "graceful_timeout_seconds": 30,
                "cooldown_seconds": 600,
                "max_actions_per_host_per_hour": 0,
                "allow": [],
            },
            "protection": {"systemd_units": [], "executable_paths": [], "container_labels": []},
            "recovery": {"max_wait_seconds": 30, "poll_interval_seconds": 1, "require_business_health": True},
            "audit": {"local_buffer_enabled": True, "remote_export_enabled": False, "max_total_bytes": 100 * 1024 * 1024},
        },
        source="<builtin-safe-defaults>",
    )


__all__ = ["ConfigError", "GuardianConfig", "SCHEMA", "load_config", "safe_defaults", "validate_config"]
