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
import re
from pathlib import Path
from typing import Any, Mapping


SCHEMA = "guardian.config.v1"

DEFAULT_PROTECTED_SYSTEMD_UNITS = (
    "guardian-runtime.service",
    "guardian-collector.service",
    "guardian-broker.service",
    "beszel.service",
    "beszel-agent.service",
    "ssh.service",
    "sshd.service",
    "systemd-logind.service",
    "systemd-journald.service",
    "docker.service",
    "containerd.service",
)
DEFAULT_PROTECTED_CONTAINER_LABELS = (
    "guardian.role=control-plane",
    "guardian.role=guardian",
    "com.docker.compose.service=beszel",
    "com.docker.compose.service=beszel-agent",
    "com.docker.compose.service=beszel-isolated",
    "com.docker.compose.service=beszel-agent-isolated",
)
_ACTIONS = {"graceful_stop", "restart", "terminate"}
_EMERGENCY_RESOURCES = {"memory", "cpu", "io", "disk_capacity"}
_FULL_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")


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
    def cpu_policy(self) -> Mapping[str, Any]:
        """Return the optional CPU policy block for the read-only observer."""

        value = self._value["risk"].get("cpu", {})
        return value if isinstance(value, Mapping) else {}

    @property
    def disk_capacity_policy(self) -> Mapping[str, Any]:
        """Return the optional filesystem capacity/inode policy block."""

        value = self._value["risk"].get("disk_capacity", {})
        return value if isinstance(value, Mapping) else {}

    @property
    def disk_mount_points(self) -> tuple[str, ...]:
        value = self.disk_capacity_policy.get("mount_points", ["/"])
        return tuple(value) if isinstance(value, list) else ("/",)

    @property
    def io_policy(self) -> Mapping[str, Any]:
        """Return the optional block-I/O policy block."""

        value = self._value["risk"].get("io", {})
        return value if isinstance(value, Mapping) else {}

    @property
    def emergency_shedding_policy(self) -> Mapping[str, Any]:
        """Return the optional, disabled-by-default Emergency Shedding v1 block."""

        value = self._value["risk"].get("emergency_shedding", {})
        return value if isinstance(value, Mapping) else {}

    @property
    def disk_reserve_recovery_policy(self) -> Mapping[str, Any]:
        """Return the disabled-by-default Guardian-owned reserve policy."""

        value = self._value["risk"].get("disk_reserve_recovery", {})
        return value if isinstance(value, Mapping) else {}

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
    def authorization_file(self) -> str | None:
        value = self._value["actions"].get("authorization_file")
        return value if isinstance(value, str) else None

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
    def audit_max_total_bytes(self) -> int:
        return self._value["audit"]["max_total_bytes"]

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
    required_risk_keys = {"memory", "composite"}
    missing_risk = required_risk_keys - set(risk)
    unknown_risk = set(risk) - required_risk_keys - {"cpu", "disk_capacity", "io", "emergency_shedding", "disk_reserve_recovery"}
    if missing_risk:
        raise ConfigError(f"risk:missing_fields:{','.join(sorted(missing_risk))}")
    if unknown_risk:
        raise ConfigError(f"risk:unknown_fields:{','.join(sorted(unknown_risk))}")
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

    cpu = risk.get("cpu")
    if cpu is not None:
        cpu = _mapping(cpu, "risk.cpu")
        _exact_keys(
            cpu,
            {
                "warning_utilization_percent",
                "critical_utilization_percent",
                "warning_psi_some_avg10",
                "critical_psi_full_avg10",
                "warning_scheduler_delay_ms",
                "critical_scheduler_delay_ms",
                "warning_for_seconds",
                "critical_for_seconds",
                "required_samples",
                "max_sample_age_seconds",
                "min_object_contribution_percent",
                "min_object_lead_margin",
            },
            "risk.cpu",
        )
        warning_utilization = _number(cpu["warning_utilization_percent"], "risk.cpu.warning_utilization_percent")
        critical_utilization = _number(cpu["critical_utilization_percent"], "risk.cpu.critical_utilization_percent")
        warning_psi = _number(cpu["warning_psi_some_avg10"], "risk.cpu.warning_psi_some_avg10")
        critical_psi = _number(cpu["critical_psi_full_avg10"], "risk.cpu.critical_psi_full_avg10")
        warning_scheduler_delay = _number(cpu["warning_scheduler_delay_ms"], "risk.cpu.warning_scheduler_delay_ms")
        critical_scheduler_delay = _number(cpu["critical_scheduler_delay_ms"], "risk.cpu.critical_scheduler_delay_ms")
        cpu_warning_for = _number(cpu["warning_for_seconds"], "risk.cpu.warning_for_seconds")
        cpu_critical_for = _number(cpu["critical_for_seconds"], "risk.cpu.critical_for_seconds")
        cpu_required_samples = cpu["required_samples"]
        cpu_max_age = _number(cpu["max_sample_age_seconds"], "risk.cpu.max_sample_age_seconds")
        min_contribution = _number(cpu["min_object_contribution_percent"], "risk.cpu.min_object_contribution_percent")
        lead_margin = _number(cpu["min_object_lead_margin"], "risk.cpu.min_object_lead_margin")
        if not 0 < warning_utilization <= critical_utilization <= 100:
            raise ConfigError("risk.cpu:utilization_thresholds_invalid")
        if not 0 <= warning_psi <= 100 or not 0 <= critical_psi <= 100:
            raise ConfigError("risk.cpu:psi_thresholds_invalid")
        if not 0 < warning_scheduler_delay <= critical_scheduler_delay:
            raise ConfigError("risk.cpu:scheduler_delay_thresholds_invalid")
        if cpu_warning_for <= 0 or cpu_critical_for <= 0:
            raise ConfigError("risk.cpu:dwell_windows_must_be_positive")
        if type(cpu_required_samples) is not int or not 1 <= cpu_required_samples <= 60:
            raise ConfigError("risk.cpu.required_samples:invalid")
        if cpu_max_age <= 0 or cpu_max_age > 300:
            raise ConfigError("risk.cpu.max_sample_age_seconds:invalid")
        if not 0 <= min_contribution <= 100 or not 0 <= lead_margin <= 1:
            raise ConfigError("risk.cpu:attribution_thresholds_invalid")

    disk_capacity = risk.get("disk_capacity")
    if disk_capacity is not None:
        disk_capacity = _mapping(disk_capacity, "risk.disk_capacity")
        _exact_keys(
            disk_capacity,
            {
                "mount_points",
                "warning_free_percent",
                "critical_free_percent",
                "warning_inode_free_percent",
                "critical_inode_free_percent",
                "warning_time_to_full_seconds",
                "critical_time_to_full_seconds",
                "warning_for_seconds",
                "critical_for_seconds",
                "required_samples",
                "max_sample_age_seconds",
                "min_object_contribution_percent",
                "min_object_lead_margin",
            },
            "risk.disk_capacity",
        )
        mount_points = _strings(disk_capacity["mount_points"], "risk.disk_capacity.mount_points")
        if not mount_points or any(not path.startswith("/") for path in mount_points):
            raise ConfigError("risk.disk_capacity.mount_points:absolute_non_empty_required")
        warning_free = _number(disk_capacity["warning_free_percent"], "risk.disk_capacity.warning_free_percent")
        critical_free = _number(disk_capacity["critical_free_percent"], "risk.disk_capacity.critical_free_percent")
        warning_inode = _number(disk_capacity["warning_inode_free_percent"], "risk.disk_capacity.warning_inode_free_percent")
        critical_inode = _number(disk_capacity["critical_inode_free_percent"], "risk.disk_capacity.critical_inode_free_percent")
        warning_ttf = _number(disk_capacity["warning_time_to_full_seconds"], "risk.disk_capacity.warning_time_to_full_seconds")
        critical_ttf = _number(disk_capacity["critical_time_to_full_seconds"], "risk.disk_capacity.critical_time_to_full_seconds")
        capacity_warning_for = _number(disk_capacity["warning_for_seconds"], "risk.disk_capacity.warning_for_seconds")
        capacity_critical_for = _number(disk_capacity["critical_for_seconds"], "risk.disk_capacity.critical_for_seconds")
        capacity_required_samples = disk_capacity["required_samples"]
        capacity_max_age = _number(disk_capacity["max_sample_age_seconds"], "risk.disk_capacity.max_sample_age_seconds")
        capacity_min_contribution = _number(disk_capacity["min_object_contribution_percent"], "risk.disk_capacity.min_object_contribution_percent")
        capacity_lead_margin = _number(disk_capacity["min_object_lead_margin"], "risk.disk_capacity.min_object_lead_margin")
        if not 0 < critical_free <= warning_free <= 100 or not 0 < critical_inode <= warning_inode <= 100:
            raise ConfigError("risk.disk_capacity:free_thresholds_invalid")
        if critical_ttf <= 0 or warning_ttf < critical_ttf:
            raise ConfigError("risk.disk_capacity:time_to_full_thresholds_invalid")
        if capacity_warning_for <= 0 or capacity_critical_for <= 0:
            raise ConfigError("risk.disk_capacity:dwell_windows_must_be_positive")
        if type(capacity_required_samples) is not int or not 1 <= capacity_required_samples <= 60:
            raise ConfigError("risk.disk_capacity.required_samples:invalid")
        if capacity_max_age <= 0 or capacity_max_age > 300:
            raise ConfigError("risk.disk_capacity.max_sample_age_seconds:invalid")
        if not 0 <= capacity_min_contribution <= 100 or not 0 <= capacity_lead_margin <= 1:
            raise ConfigError("risk.disk_capacity:attribution_thresholds_invalid")

    io = risk.get("io")
    if io is not None:
        io = _mapping(io, "risk.io")
        _exact_keys(
            io,
            {
                "warning_psi_some_avg10",
                "critical_psi_full_avg10",
                "warning_device_utilization_percent",
                "critical_device_utilization_percent",
                "warning_average_latency_ms",
                "critical_average_latency_ms",
                "warning_for_seconds",
                "critical_for_seconds",
                "required_samples",
                "max_sample_age_seconds",
                "min_object_contribution_percent",
                "min_object_lead_margin",
            },
            "risk.io",
        )
        io_warning_psi = _number(io["warning_psi_some_avg10"], "risk.io.warning_psi_some_avg10")
        io_critical_psi = _number(io["critical_psi_full_avg10"], "risk.io.critical_psi_full_avg10")
        io_warning_utilization = _number(io["warning_device_utilization_percent"], "risk.io.warning_device_utilization_percent")
        io_critical_utilization = _number(io["critical_device_utilization_percent"], "risk.io.critical_device_utilization_percent")
        io_warning_latency = _number(io["warning_average_latency_ms"], "risk.io.warning_average_latency_ms")
        io_critical_latency = _number(io["critical_average_latency_ms"], "risk.io.critical_average_latency_ms")
        io_warning_for = _number(io["warning_for_seconds"], "risk.io.warning_for_seconds")
        io_critical_for = _number(io["critical_for_seconds"], "risk.io.critical_for_seconds")
        io_required_samples = io["required_samples"]
        io_max_age = _number(io["max_sample_age_seconds"], "risk.io.max_sample_age_seconds")
        io_min_contribution = _number(io["min_object_contribution_percent"], "risk.io.min_object_contribution_percent")
        io_lead_margin = _number(io["min_object_lead_margin"], "risk.io.min_object_lead_margin")
        if not 0 <= io_warning_psi <= 100 or not 0 <= io_critical_psi <= 100:
            raise ConfigError("risk.io:psi_thresholds_invalid")
        if not 0 < io_warning_utilization <= io_critical_utilization <= 100:
            raise ConfigError("risk.io:utilization_thresholds_invalid")
        if io_warning_latency <= 0 or io_warning_latency > io_critical_latency:
            raise ConfigError("risk.io:latency_thresholds_invalid")
        if io_warning_for <= 0 or io_critical_for <= 0:
            raise ConfigError("risk.io:dwell_windows_must_be_positive")
        if type(io_required_samples) is not int or not 1 <= io_required_samples <= 60:
            raise ConfigError("risk.io.required_samples:invalid")
        if io_max_age <= 0 or io_max_age > 300:
            raise ConfigError("risk.io.max_sample_age_seconds:invalid")
        if not 0 <= io_min_contribution <= 100 or not 0 <= io_lead_margin <= 1:
            raise ConfigError("risk.io:attribution_thresholds_invalid")

    emergency_shedding = risk.get("emergency_shedding")
    if emergency_shedding is not None:
        emergency_shedding = _mapping(emergency_shedding, "risk.emergency_shedding")
        _exact_keys(
            emergency_shedding,
            {
                "schema",
                "enabled",
                "window_seconds",
                "required_samples",
                "min_host_contribution_percent",
                "resource_priority",
                "action",
                "protected_set",
                "actionable_set",
            },
            "risk.emergency_shedding",
        )
        if emergency_shedding["schema"] != "guardian.emergency_shedding.v1":
            raise ConfigError("risk.emergency_shedding.schema:unsupported")
        enabled = _bool(emergency_shedding["enabled"], "risk.emergency_shedding.enabled")
        window_seconds = _number(emergency_shedding["window_seconds"], "risk.emergency_shedding.window_seconds")
        required_samples = emergency_shedding["required_samples"]
        contribution = _number(
            emergency_shedding["min_host_contribution_percent"],
            "risk.emergency_shedding.min_host_contribution_percent",
        )
        priority = _strings(emergency_shedding["resource_priority"], "risk.emergency_shedding.resource_priority")
        if not 1 <= window_seconds <= 60:
            raise ConfigError("risk.emergency_shedding.window_seconds:must_be_between_1_and_60")
        if type(required_samples) is not int or not 1 <= required_samples <= 60:
            raise ConfigError("risk.emergency_shedding.required_samples:invalid")
        if not 0 <= contribution <= 100:
            raise ConfigError("risk.emergency_shedding.min_host_contribution_percent:invalid")
        if len(priority) != len(_EMERGENCY_RESOURCES) or set(priority) != _EMERGENCY_RESOURCES:
            raise ConfigError("risk.emergency_shedding.resource_priority:must_cover_all_resources")
        if emergency_shedding["action"] != "graceful_stop":
            raise ConfigError("risk.emergency_shedding.action:only_graceful_stop_supported")

        protected_entries = emergency_shedding["protected_set"]
        actionable_entries = emergency_shedding["actionable_set"]
        if not isinstance(protected_entries, list) or any(not isinstance(item, dict) for item in protected_entries):
            raise ConfigError("risk.emergency_shedding.protected_set:object_list_required")
        if not isinstance(actionable_entries, list) or any(not isinstance(item, dict) for item in actionable_entries):
            raise ConfigError("risk.emergency_shedding.actionable_set:object_list_required")
        protected_ids: list[str] = []
        actionable_ids: list[str] = []
        for index, item in enumerate(protected_entries):
            _exact_keys(
                item,
                {"stable_id", "owner", "reason"},
                f"risk.emergency_shedding.protected_set[{index}]",
            )
            stable_id = _string(item["stable_id"], f"risk.emergency_shedding.protected_set[{index}].stable_id")
            if not _FULL_CONTAINER_ID.fullmatch(stable_id):
                raise ConfigError(f"risk.emergency_shedding.protected_set[{index}].stable_id:full_container_id_required")
            protected_ids.append(stable_id)
            _string(item["owner"], f"risk.emergency_shedding.protected_set[{index}].owner")
            _string(item["reason"], f"risk.emergency_shedding.protected_set[{index}].reason")
        for index, item in enumerate(actionable_entries):
            _exact_keys(
                item,
                {
                    "stable_id",
                    "owner",
                    "environment",
                    "allowed_resources",
                    "action",
                    "grace_timeout_seconds",
                    "expires_at",
                    "human_contact",
                },
                f"risk.emergency_shedding.actionable_set[{index}]",
            )
            stable_id = _string(item["stable_id"], f"risk.emergency_shedding.actionable_set[{index}].stable_id")
            if not _FULL_CONTAINER_ID.fullmatch(stable_id):
                raise ConfigError(f"risk.emergency_shedding.actionable_set[{index}].stable_id:full_container_id_required")
            actionable_ids.append(stable_id)
            _string(item["owner"], f"risk.emergency_shedding.actionable_set[{index}].owner")
            if item["environment"] != "local-disposable":
                raise ConfigError(f"risk.emergency_shedding.actionable_set[{index}].environment:local_disposable_required")
            allowed_resources = _strings(
                item["allowed_resources"],
                f"risk.emergency_shedding.actionable_set[{index}].allowed_resources",
            )
            if not allowed_resources or not set(allowed_resources).issubset(_EMERGENCY_RESOURCES):
                raise ConfigError(f"risk.emergency_shedding.actionable_set[{index}].allowed_resources:invalid")
            if item["action"] != "graceful_stop":
                raise ConfigError(f"risk.emergency_shedding.actionable_set[{index}].action:only_graceful_stop_supported")
            timeout = item["grace_timeout_seconds"]
            if type(timeout) is not int or not 1 <= timeout <= 120:
                raise ConfigError(f"risk.emergency_shedding.actionable_set[{index}].grace_timeout_seconds:invalid")
            _string(item["expires_at"], f"risk.emergency_shedding.actionable_set[{index}].expires_at")
            _string(item["human_contact"], f"risk.emergency_shedding.actionable_set[{index}].human_contact")
        if len(set(protected_ids)) != len(protected_ids):
            raise ConfigError("risk.emergency_shedding.protected_set:duplicate_stable_id")
        if len(set(actionable_ids)) != len(actionable_ids):
            raise ConfigError("risk.emergency_shedding.actionable_set:duplicate_stable_id")
        if set(protected_ids) & set(actionable_ids):
            raise ConfigError("risk.emergency_shedding:protected_actionable_overlap")
        if enabled and not protected_entries:
            raise ConfigError("risk.emergency_shedding.protected_set:non_empty_when_enabled")
        if enabled and not actionable_entries:
            raise ConfigError("risk.emergency_shedding.actionable_set:non_empty_when_enabled")

    reserve_recovery = risk.get("disk_reserve_recovery")
    if reserve_recovery is not None:
        reserve_recovery = _mapping(reserve_recovery, "risk.disk_reserve_recovery")
        _exact_keys(
            reserve_recovery,
            {"enabled", "root", "mount_point", "max_releases_per_incident", "authorization_file"},
            "risk.disk_reserve_recovery",
        )
        enabled = _bool(reserve_recovery["enabled"], "risk.disk_reserve_recovery.enabled")
        root_path = _string(reserve_recovery["root"], "risk.disk_reserve_recovery.root")
        mount_point = _string(reserve_recovery["mount_point"], "risk.disk_reserve_recovery.mount_point")
        if not root_path.startswith("/") or root_path != "/var/lib/guardian/reserve":
            raise ConfigError("risk.disk_reserve_recovery.root:fixed_guardian_reserve_path_required")
        if not mount_point.startswith("/"):
            raise ConfigError("risk.disk_reserve_recovery.mount_point:absolute_path_required")
        max_releases = reserve_recovery["max_releases_per_incident"]
        if type(max_releases) is not int or max_releases != 1:
            raise ConfigError("risk.disk_reserve_recovery.max_releases_per_incident:must_be_one")
        authorization_file = reserve_recovery["authorization_file"]
        if authorization_file is not None:
            authorization_file = _string(authorization_file, "risk.disk_reserve_recovery.authorization_file")
            if not authorization_file.startswith("/"):
                raise ConfigError("risk.disk_reserve_recovery.authorization_file:absolute_path_required")
        if enabled and authorization_file is None:
            raise ConfigError("risk.disk_reserve_recovery:authorization_file_required_when_enabled")

    actions = _mapping(root["actions"], "actions")
    # Keep already-installed observe/simulate configurations readable while
    # requiring the new authorization path before enforce can start. Fresh
    # configs and the JSON schema include this field explicitly.
    if "authorization_file" not in actions and agent["mode"] in {"observe", "simulate"}:
        actions = {**actions, "authorization_file": None}
    _exact_keys(actions, {"enabled", "require_approval", "authorization_file", "graceful_timeout_seconds", "cooldown_seconds", "max_actions_per_host_per_hour", "allow"}, "actions")
    enabled = _bool(actions["enabled"], "actions.enabled")
    require_approval = _bool(actions["require_approval"], "actions.require_approval")
    authorization_file = actions["authorization_file"]
    if authorization_file is not None:
        authorization_file = _string(authorization_file, "actions.authorization_file")
        if not authorization_file.startswith("/"):
            raise ConfigError("actions.authorization_file:absolute_path_required")
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
        if authorization_file is None:
            raise ConfigError("agent.mode:enforce_requires_authorization_file")

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
                "cpu": {
                    "warning_utilization_percent": 85,
                    "critical_utilization_percent": 95,
                    "warning_psi_some_avg10": 1,
                    "critical_psi_full_avg10": 0.5,
                    "warning_scheduler_delay_ms": 250,
                    "critical_scheduler_delay_ms": 1000,
                    "warning_for_seconds": 30,
                    "critical_for_seconds": 10,
                    "required_samples": 2,
                    "max_sample_age_seconds": 15,
                    "min_object_contribution_percent": 20,
                    "min_object_lead_margin": 0.15,
                },
                "disk_capacity": {
                    "mount_points": ["/"],
                    "warning_free_percent": 15,
                    "critical_free_percent": 5,
                    "warning_inode_free_percent": 10,
                    "critical_inode_free_percent": 5,
                    "warning_time_to_full_seconds": 86400,
                    "critical_time_to_full_seconds": 3600,
                    "warning_for_seconds": 180,
                    "critical_for_seconds": 30,
                    "required_samples": 2,
                    "max_sample_age_seconds": 15,
                    "min_object_contribution_percent": 20,
                    "min_object_lead_margin": 0.15,
                },
                "io": {
                    "warning_psi_some_avg10": 1,
                    "critical_psi_full_avg10": 0.5,
                    "warning_device_utilization_percent": 70,
                    "critical_device_utilization_percent": 90,
                    "warning_average_latency_ms": 100,
                    "critical_average_latency_ms": 500,
                    "warning_for_seconds": 30,
                    "critical_for_seconds": 10,
                    "required_samples": 2,
                    "max_sample_age_seconds": 15,
                    "min_object_contribution_percent": 20,
                    "min_object_lead_margin": 0.15,
                },
                "disk_reserve_recovery": {
                    "enabled": False,
                    "root": "/var/lib/guardian/reserve",
                    "mount_point": "/",
                    "max_releases_per_incident": 1,
                    "authorization_file": None,
                },
            },
            "actions": {
                "enabled": False,
                "require_approval": True,
                "authorization_file": None,
                "graceful_timeout_seconds": 30,
                "cooldown_seconds": 600,
                "max_actions_per_host_per_hour": 0,
                "allow": [],
            },
            "protection": {
                "systemd_units": list(DEFAULT_PROTECTED_SYSTEMD_UNITS),
                "executable_paths": [],
                "container_labels": list(DEFAULT_PROTECTED_CONTAINER_LABELS),
            },
            "recovery": {"max_wait_seconds": 30, "poll_interval_seconds": 1, "require_business_health": True},
            "audit": {"local_buffer_enabled": True, "remote_export_enabled": False, "max_total_bytes": 100 * 1024 * 1024},
        },
        source="<builtin-safe-defaults>",
    )


__all__ = ["ConfigError", "GuardianConfig", "SCHEMA", "load_config", "safe_defaults", "validate_config"]
