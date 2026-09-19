"""Safe orchestration boundary between Guardian events and action adapters.

The controller is deliberately dependency-injected: tests use a mock/fake
executor, while a real Docker adapter is only useful after an explicit
``local-disposable`` authorization has been created by the caller. This module
does not discover targets, connect to production, or manufacture authorization.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from .guardian_actions import (
    ActionDenied,
    ActionRequest,
    ActionResult,
    Authorization,
    allowed_actions,
    validate_request,
)
from .guardian_recovery import (
    CooldownLedger,
    RecoveryObservation,
    RecoveryPolicy,
    RecoveryResult,
    assess_recovery,
)


class ActionExecutor(Protocol):
    def execute(self, request: ActionRequest, now: float | None = None) -> ActionResult:
        """Execute or record one already-authorized action request."""


RecoveryProbe = Callable[[ActionRequest, ActionResult], RecoveryObservation | None]


@dataclass(frozen=True)
class ControllerResult:
    """Auditable outcome of one controller decision."""

    state: str
    event_id: str
    action_result: ActionResult | None
    recovery: RecoveryResult | None
    reason_codes: tuple[str, ...]
    cooldown_state: str
    failure_breaker_tripped: bool


class GuardianController:
    """Apply event, policy, authorization and recovery gates in order."""

    def __init__(self, executor: ActionExecutor, ledger: CooldownLedger | None = None) -> None:
        self.executor = executor
        self.ledger = ledger if ledger is not None else CooldownLedger()

    def enforce(
        self,
        event: dict[str, Any],
        authorization: Authorization | None,
        allowed: set[str] | frozenset[str] | list[str] | tuple[str, ...],
        *,
        now: float | None = None,
        recovery_observation: RecoveryObservation | None = None,
        recovery_policy: RecoveryPolicy | None = None,
        recovery_probe: RecoveryProbe | None = None,
        cooldown_seconds: float = 30.0,
        max_actions: int = 1,
        window_seconds: float = 300.0,
        max_consecutive_failures: int = 2,
    ) -> ControllerResult:
        """Attempt one guarded action, returning a fail-closed audit result.

        The method only accepts an event explicitly marked ``enforce``. A
        missing/invalid event, ambiguous object identity, protected object,
        cooldown hit, absent authorization, or adapter rejection never calls
        the injected executor.
        """

        timestamp = time.time() if now is None else now
        event_id = str(event.get("event_id", "")) if isinstance(event, dict) else ""
        decision = event.get("decision", {}) if isinstance(event, dict) else {}
        if not isinstance(decision, dict):
            decision = {}

        def denied(reason: str, state: str = "denied") -> ControllerResult:
            return ControllerResult(
                state=state,
                event_id=event_id,
                action_result=None,
                recovery=None,
                reason_codes=(reason,),
                cooldown_state="not_checked",
                failure_breaker_tripped=self.ledger.tripped(max_consecutive_failures),
            )

        if not isinstance(event, dict) or decision.get("mode") != "enforce":
            return denied("enforce_mode_required")
        if event.get("state") not in {"warning", "critical"}:
            return denied("risk_not_actionable")

        candidates = event.get("object_candidates", [])
        if not isinstance(candidates, list) or len(candidates) != 1:
            return denied("ambiguous_object_identity")
        candidate = candidates[0]
        if not isinstance(candidate, dict) or candidate.get("kind") != "container":
            return denied("unsupported_object_kind")
        target_id = candidate.get("id")
        if not isinstance(target_id, str) or not target_id:
            return denied("no_stable_object_identity")

        action = decision.get("action")
        if not isinstance(action, str) or action in {"none", "escalate"}:
            return denied("no_action_plan")
        try:
            normalized_allowed = allowed_actions(allowed)
        except ActionDenied as exc:
            return denied(str(exc))

        if self.ledger.tripped(max_consecutive_failures):
            return denied("failure_breaker_tripped", state="escalated")
        can_run, cooldown_state = self.ledger.allow(
            timestamp,
            cooldown_seconds,
            max_actions,
            window_seconds,
        )
        if not can_run:
            return ControllerResult(
                state="escalated",
                event_id=event_id,
                action_result=None,
                recovery=None,
                reason_codes=(cooldown_state,),
                cooldown_state=cooldown_state,
                failure_breaker_tripped=self.ledger.tripped(max_consecutive_failures),
            )

        request = ActionRequest(
            event_id=event_id,
            target_id=target_id,
            action=action,
            protected=bool(decision.get("protected", True)),
            allowed_actions=normalized_allowed,
            authorization=authorization,
            timeout_seconds=int(decision.get("timeout_seconds", 30)),
        )
        try:
            validate_request(request, now=timestamp)
            action_result = self.executor.execute(request, now=timestamp)
        except ActionDenied as exc:
            return ControllerResult(
                state="denied",
                event_id=event_id,
                action_result=None,
                recovery=None,
                reason_codes=(str(exc),),
                cooldown_state="adapter_rejected",
                failure_breaker_tripped=self.ledger.tripped(max_consecutive_failures),
            )

        recovery: RecoveryResult | None = None
        if recovery_observation is not None:
            if recovery_observation.target_id != target_id:
                recovery = RecoveryResult("failed", False, ("recovery_target_mismatch",))
            elif recovery_policy is None:
                recovery = RecoveryResult("failed", False, ("recovery_policy_missing",))
            else:
                recovery = assess_recovery(recovery_policy, recovery_observation)
        elif recovery_probe is not None and recovery_policy is not None and action_result.executed:
            try:
                probed_observation = recovery_probe(request, action_result)
            except (OSError, TimeoutError, ValueError) as exc:
                recovery = RecoveryResult("failed", False, ("recovery_probe_error", str(exc)))
            else:
                if probed_observation is None:
                    recovery = RecoveryResult("failed", False, ("recovery_observation_missing",))
                elif probed_observation.target_id != target_id:
                    recovery = RecoveryResult("failed", False, ("recovery_target_mismatch",))
                else:
                    recovery = assess_recovery(recovery_policy, probed_observation)

        if not action_result.executed:
            # A mock/planning adapter does not mutate runtime state and must
            # not consume the real-action cooldown or count as a failure.
            return ControllerResult(
                state="planned",
                event_id=event_id,
                action_result=action_result,
                recovery=recovery,
                reason_codes=(action_result.reason or "action_not_executed",),
                cooldown_state="not_recorded",
                failure_breaker_tripped=self.ledger.tripped(max_consecutive_failures),
            )

        successful_action = action_result.executed and action_result.returncode == 0
        successful_recovery = recovery is None or recovery.recovered
        success = successful_action and successful_recovery
        self.ledger.record(timestamp, success)
        breaker_tripped = self.ledger.tripped(max_consecutive_failures)

        if not successful_action:
            state = "failed"
            reasons = ("action_failed",)
        elif recovery is not None and not recovery.recovered:
            state = "escalated" if breaker_tripped else recovery.state
            reasons = recovery.reason_codes
        else:
            state = "recovered" if recovery is not None else "executed"
            reasons = ("action_succeeded",)

        return ControllerResult(
            state=state,
            event_id=event_id,
            action_result=action_result,
            recovery=recovery,
            reason_codes=reasons,
            cooldown_state="recorded",
            failure_breaker_tripped=breaker_tripped,
        )
