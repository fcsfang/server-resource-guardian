"""Pure recovery and cooldown decisions for the Guardian prototype.

The module consumes observations produced by an adapter; it does not query or
mutate Docker/systemd itself. That separation keeps recovery verification safe
to test with fixtures before any real enforce experiment is authorized.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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
