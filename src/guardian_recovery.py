"""Pure recovery and cooldown decisions for the Guardian prototype.

The module consumes observations produced by an adapter; it does not query or
mutate Docker/systemd itself. That separation keeps recovery verification safe
to test with fixtures before any real enforce experiment is authorized.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class RecoveryPolicy:
    action: str
    max_wait_seconds: float = 30.0
    healthy_statuses: frozenset[str] = field(default_factory=lambda: frozenset({"healthy", "running"}))


@dataclass(frozen=True)
class RecoveryObservation:
    target_id: str
    target_present: bool
    target_running: bool
    health_status: str | None
    risk_state: str
    observed_after_seconds: float
    exit_code: int | None = None


@dataclass(frozen=True)
class RecoveryResult:
    state: str
    recovered: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class HostRecoveryObservation:
    """Before/after host evidence used to assess mitigation only."""

    before_available_percent: float | None
    after_available_percent: float | None
    before_psi_full_avg10: float | None
    after_psi_full_avg10: float | None
    before_oom_events: int | None
    after_oom_events: int | None
    after_risk_state: str
    observed_after_seconds: float
    resource_kind: str = "memory"
    before_resource_state: str | None = None
    after_resource_state: str | None = None


@dataclass(frozen=True)
class HostRecoveryPolicy:
    max_wait_seconds: float = 30.0
    minimum_available_improvement_percent: float = 0.1
    minimum_psi_improvement_avg10: float = 0.1


@dataclass(frozen=True)
class BusinessRecoveryObservation:
    target_id: str
    target_present: bool
    target_running: bool
    health_status: str | None
    probe_ok: bool | None
    observed_after_seconds: float


@dataclass(frozen=True)
class BusinessRecoveryPolicy:
    max_wait_seconds: float = 30.0
    healthy_statuses: frozenset[str] = field(default_factory=lambda: frozenset({"healthy", "running"}))
    require_probe: bool = True
    action: str = "restart"


@dataclass(frozen=True)
class TwoLayerRecoveryResult:
    host_state: str
    business_state: str
    overall_state: str
    host_mitigated: bool
    business_recovered: bool
    reason_codes: tuple[str, ...]


def assess_recovery(policy: RecoveryPolicy, observation: RecoveryObservation) -> RecoveryResult:
    """Return recovered/pending/failed without performing a follow-up action."""

    if observation.observed_after_seconds < 0:
        return RecoveryResult("failed", False, ("invalid_observation_time",))
    if observation.observed_after_seconds > policy.max_wait_seconds:
        return RecoveryResult("failed", False, ("recovery_window_expired",))

    if policy.action in {"graceful_stop", "terminate"}:
        recovered = not observation.target_present or not observation.target_running
        if recovered and policy.action == "graceful_stop" and observation.exit_code is not None:
            if observation.exit_code == 137:
                return RecoveryResult("failed", False, ("target_force_killed",))
            if observation.exit_code not in {0, 143}:
                return RecoveryResult("failed", False, ("target_stopped_nonzero_exit",))
        if recovered:
            reasons = (
                "target_stopped_sigterm"
                if policy.action == "graceful_stop" and observation.exit_code == 143
                else "target_stopped",
            )
        else:
            reasons = ("target_still_running",)
    elif policy.action == "restart":
        recovered = (
            observation.target_present
            and observation.target_running
            and observation.health_status in policy.healthy_statuses
            and observation.risk_state in {"normal", "recovered"}
        )
        reasons = ("target_healthy",) if recovered else ("target_not_healthy",)
    else:
        return RecoveryResult("failed", False, ("unsupported_recovery_action",))

    if recovered:
        return RecoveryResult("recovered", True, reasons)
    return RecoveryResult("pending", False, reasons)


def assess_host_mitigation(
    policy: HostRecoveryPolicy,
    observation: HostRecoveryObservation,
) -> RecoveryResult:
    """Assess whether the host risk improved after an action.

    A stopped container is not enough.  Host mitigation requires a normal or
    recovered risk state, no new OOM event, and evidence that available memory
    or full PSI improved from the pre-action sample.
    """

    if observation.observed_after_seconds < 0:
        return RecoveryResult("failed", False, ("invalid_observation_time",))
    if observation.observed_after_seconds > policy.max_wait_seconds:
        return RecoveryResult("failed", False, ("host_recovery_window_expired",))
    if observation.resource_kind != "memory":
        before_state = observation.before_resource_state
        after_state = observation.after_resource_state
        if before_state not in {"warning", "critical", "critical_confirmed", "CRITICAL_CONFIRMED"}:
            return RecoveryResult("failed", False, ("resource_recovery_before_state_not_actionable",))
        if after_state not in {"normal", "recovered"}:
            return RecoveryResult("failed", False, ("resource_risk_still_actionable",))
        return RecoveryResult("MITIGATED", True, ("resource_risk_mitigated",))

    required = (
        observation.before_available_percent,
        observation.after_available_percent,
        observation.before_psi_full_avg10,
        observation.after_psi_full_avg10,
        observation.before_oom_events,
        observation.after_oom_events,
    )
    if any(value is None for value in required):
        return RecoveryResult("failed", False, ("host_recovery_observation_incomplete",))
    if observation.after_risk_state not in {"normal", "recovered"}:
        return RecoveryResult("failed", False, ("host_risk_still_actionable",))
    if observation.after_oom_events > observation.before_oom_events:
        return RecoveryResult("failed", False, ("new_oom_after_action",))
    available_improvement = observation.after_available_percent - observation.before_available_percent
    psi_improvement = observation.before_psi_full_avg10 - observation.after_psi_full_avg10
    if (
        available_improvement >= policy.minimum_available_improvement_percent
        or psi_improvement >= policy.minimum_psi_improvement_avg10
    ):
        return RecoveryResult("MITIGATED", True, ("host_risk_mitigated",))
    return RecoveryResult("pending", False, ("host_pressure_not_improved",))


def assess_business_recovery(
    policy: BusinessRecoveryPolicy,
    observation: BusinessRecoveryObservation,
    *,
    expected_target_id: str | None = None,
) -> RecoveryResult:
    """Assess service health independently from host mitigation."""

    if expected_target_id is not None and observation.target_id != expected_target_id:
        return RecoveryResult("BUSINESS_DEGRADED", False, ("business_target_mismatch",))
    if observation.observed_after_seconds < 0:
        return RecoveryResult("BUSINESS_DEGRADED", False, ("invalid_observation_time",))
    if observation.observed_after_seconds > policy.max_wait_seconds:
        return RecoveryResult("BUSINESS_DEGRADED", False, ("business_recovery_window_expired",))
    if policy.action in {"graceful_stop", "terminate"}:
        if observation.target_present and observation.target_running:
            return RecoveryResult("BUSINESS_DEGRADED", False, ("business_target_still_running",))
        # Stopping a disposable or failed target proves containment, not that
        # the surrounding business service is healthy.  A separate, real
        # health check must explicitly confirm the business layer.
        if policy.require_probe and observation.probe_ok is not True:
            return RecoveryResult("BUSINESS_DEGRADED", False, ("business_health_check_not_configured",))
        return RecoveryResult("BUSINESS_RECOVERED", True, ("business_health_confirmed",))
    if not observation.target_present or not observation.target_running:
        return RecoveryResult("BUSINESS_DEGRADED", False, ("business_target_not_running",))
    if observation.health_status not in policy.healthy_statuses:
        return RecoveryResult("BUSINESS_DEGRADED", False, ("business_health_not_healthy",))
    if policy.require_probe and observation.probe_ok is not True:
        return RecoveryResult("BUSINESS_DEGRADED", False, ("business_probe_not_healthy",))
    return RecoveryResult("BUSINESS_RECOVERED", True, ("business_health_recovered",))


def assess_two_layer_recovery(
    host_policy: HostRecoveryPolicy,
    host_observation: HostRecoveryObservation,
    business_policy: BusinessRecoveryPolicy | None = None,
    business_observation: BusinessRecoveryObservation | None = None,
    *,
    expected_target_id: str | None = None,
) -> TwoLayerRecoveryResult:
    """Return host mitigation and business recovery as separate states."""

    host = assess_host_mitigation(host_policy, host_observation)
    if business_policy is None or business_observation is None:
        business = RecoveryResult("BUSINESS_DEGRADED", False, ("business_probe_not_configured",))
    else:
        business = assess_business_recovery(
            business_policy,
            business_observation,
            expected_target_id=expected_target_id,
        )
    if host.recovered and business.recovered:
        overall = "BUSINESS_RECOVERED"
    elif host.recovered:
        overall = "MITIGATED"
    elif host.state == "pending" or business.state == "pending":
        overall = "RECOVERY_PENDING"
    else:
        overall = "RECOVERY_FAILED"
    return TwoLayerRecoveryResult(
        host_state=host.state,
        business_state=business.state,
        overall_state=overall,
        host_mitigated=host.recovered,
        business_recovered=business.recovered,
        reason_codes=tuple(dict.fromkeys((*host.reason_codes, *business.reason_codes))),
    )


@dataclass
class CooldownLedger:
    """Small in-memory guard; persistent storage belongs to the audit layer."""

    last_action_at: float | None = None
    action_times: list[float] = field(default_factory=list)
    consecutive_failures: int = 0

    def allow(self, now: float, cooldown_seconds: float, max_actions: int, window_seconds: float) -> tuple[bool, str]:
        self.action_times = [timestamp for timestamp in self.action_times if now - timestamp <= window_seconds]
        if self.last_action_at is not None and now - self.last_action_at < cooldown_seconds:
            return False, "cooldown_active"
        if len(self.action_times) >= max_actions:
            return False, "action_limit_reached"
        return True, "allowed"

    def record(self, now: float, success: bool) -> None:
        self.last_action_at = now
        self.action_times.append(now)
        self.consecutive_failures = 0 if success else self.consecutive_failures + 1

    def tripped(self, max_consecutive_failures: int = 2) -> bool:
        return self.consecutive_failures >= max_consecutive_failures

    def to_dict(self) -> dict[str, object]:
        return {
            "last_action_at": self.last_action_at,
            "action_times": list(self.action_times),
            "consecutive_failures": self.consecutive_failures,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "CooldownLedger":
        raw_times = data.get("action_times", [])
        action_times = [float(value) for value in raw_times] if isinstance(raw_times, list) else []
        raw_last = data.get("last_action_at")
        return cls(
            last_action_at=float(raw_last) if raw_last is not None else None,
            action_times=action_times,
            consecutive_failures=int(data.get("consecutive_failures", 0)),
        )

    @classmethod
    def load(cls, path: Path) -> "CooldownLedger":
        if not path.exists():
            return cls()
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("cooldown ledger must be a JSON object")
        return cls.from_dict(value)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
