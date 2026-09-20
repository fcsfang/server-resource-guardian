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
    BusinessRecoveryObservation,
    BusinessRecoveryPolicy,
    CooldownLedger,
    HostRecoveryObservation,
    HostRecoveryPolicy,
    RecoveryObservation,
    RecoveryPolicy,
    RecoveryResult,
    TwoLayerRecoveryResult,
    assess_recovery,
    assess_two_layer_recovery,
)
from .guardian_state import GuardianStateStore, StateStoreError


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
    intent_id: str | None = None
    recovery_layers: TwoLayerRecoveryResult | None = None


class GuardianController:
    """Apply event, policy, authorization and recovery gates in order."""

    def __init__(
        self,
        executor: ActionExecutor,
        ledger: CooldownLedger | None = None,
        state_store: GuardianStateStore | None = None,
    ) -> None:
        self.executor = executor
        self.ledger = ledger if ledger is not None else CooldownLedger()
        self.state_store = state_store

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
        host_recovery_observation: HostRecoveryObservation | None = None,
        host_recovery_policy: HostRecoveryPolicy | None = None,
        business_recovery_observation: BusinessRecoveryObservation | None = None,
        business_recovery_policy: BusinessRecoveryPolicy | None = None,
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
        host_id = str(event.get("host_id") or "unknown-host") if isinstance(event, dict) else "unknown-host"
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
                failure_breaker_tripped=(
                    self.ledger.tripped(max_consecutive_failures)
                    if self.state_store is None
                    else False
                ),
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

        try:
            breaker_tripped = (
                self.state_store.failure_breaker(host_id, target_id, action, max_consecutive_failures)
                if self.state_store is not None
                else self.ledger.tripped(max_consecutive_failures)
            )
            can_run, cooldown_state = (
                self.state_store.allow_action(
                    host_id,
                    target_id,
                    action,
                    timestamp,
                    cooldown_seconds,
                    max_actions,
                    window_seconds,
                )
                if self.state_store is not None
                else self.ledger.allow(timestamp, cooldown_seconds, max_actions, window_seconds)
            )
        except StateStoreError:
            return denied("state_store_read_failed", state="escalated")
        if breaker_tripped:
            return denied("failure_breaker_tripped", state="escalated")
        if not can_run:
            return ControllerResult(
                state="escalated",
                event_id=event_id,
                action_result=None,
                recovery=None,
                reason_codes=(cooldown_state,),
                cooldown_state=cooldown_state,
                failure_breaker_tripped=breaker_tripped,
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
        except ActionDenied as exc:
            return ControllerResult(
                state="denied",
                event_id=event_id,
                action_result=None,
                recovery=None,
                reason_codes=(str(exc),),
                cooldown_state="adapter_rejected",
                failure_breaker_tripped=breaker_tripped,
            )

        intent_id: str | None = None
        if self.state_store is not None:
            try:
                if authorization is None:
                    return denied("explicit_authorization_required")
                self.state_store.register_capability(authorization)
                capability = self.state_store.consume_capability(
                    authorization,
                    event_id=event_id,
                    now=timestamp,
                )
                if not capability.allowed:
                    return denied(capability.reason)
                claim = self.state_store.claim_intent(
                    host_id=host_id,
                    object_id=target_id,
                    action=action,
                    event_id=event_id,
                    audit_payload=event,
                    now=timestamp,
                )
                if not claim.claimed:
                    state = "escalated" if claim.reason == "active_intent_exists" else "denied"
                    return ControllerResult(
                        state=state,
                        event_id=event_id,
                        action_result=None,
                        recovery=None,
                        reason_codes=(claim.reason,),
                        cooldown_state=claim.reason,
                        failure_breaker_tripped=breaker_tripped,
                        intent_id=claim.intent_id,
                    )
                intent_id = claim.intent_id
                if not self.state_store.mark_execution_started(intent_id, now=timestamp):
                    return ControllerResult(
                        state="escalated",
                        event_id=event_id,
                        action_result=None,
                        recovery=None,
                        reason_codes=("intent_state_not_executable",),
                        cooldown_state="intent_state_not_executable",
                        failure_breaker_tripped=breaker_tripped,
                        intent_id=intent_id,
                    )
            except StateStoreError as exc:
                return denied(str(exc), state="escalated")

        try:
            action_result = self.executor.execute(request, now=timestamp)
        except ActionDenied as exc:
            if self.state_store is not None and intent_id is not None:
                try:
                    self.state_store.record_result(
                        intent_id,
                        {"reason": str(exc), "executed": False},
                        success=False,
                        executed=False,
                        now=timestamp,
                    )
                except StateStoreError:
                    return ControllerResult(
                        state="escalated",
                        event_id=event_id,
                        action_result=None,
                        recovery=None,
                        reason_codes=("audit_persistence_failed",),
                        cooldown_state="audit_persistence_failed",
                        failure_breaker_tripped=breaker_tripped,
                        intent_id=intent_id,
                    )
            return ControllerResult(
                state="denied",
                event_id=event_id,
                action_result=None,
                recovery=None,
                reason_codes=(str(exc),),
                cooldown_state="adapter_rejected",
                failure_breaker_tripped=breaker_tripped,
                intent_id=intent_id,
            )

        recovery: RecoveryResult | None = None
        if recovery_observation is not None:
            if recovery_observation.target_id != target_id:
                recovery = RecoveryResult("failed", False, ("recovery_target_mismatch",))
            elif recovery_policy is None:
                recovery = RecoveryResult("failed", False, ("recovery_policy_missing",))
            else:
                recovery = assess_recovery(recovery_policy, recovery_observation)
        elif (
            recovery_probe is not None
            and recovery_policy is not None
            and action_result.executed
            and action_result.returncode == 0
        ):
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

        recovery_layers: TwoLayerRecoveryResult | None = None
        if host_recovery_observation is not None:
            recovery_layers = assess_two_layer_recovery(
                host_recovery_policy or HostRecoveryPolicy(),
                host_recovery_observation,
                business_recovery_policy,
                business_recovery_observation,
                expected_target_id=target_id,
            )

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
                failure_breaker_tripped=breaker_tripped,
                intent_id=intent_id,
                recovery_layers=recovery_layers,
            )

        successful_action = action_result.executed and action_result.returncode == 0
        successful_recovery = recovery is None or recovery.recovered
        if recovery_layers is not None:
            successful_recovery = successful_recovery and recovery_layers.host_mitigated
        success = successful_action and successful_recovery
        try:
            if self.state_store is not None and intent_id is not None:
                self.state_store.record_result(
                    intent_id,
                    {
                        "action": action_result.action,
                        "target_id": action_result.target_id,
                        "returncode": action_result.returncode,
                        "reason": action_result.reason,
                        "stderr": action_result.stderr,
                        "recovery_layers": (
                            {
                                "host_state": recovery_layers.host_state,
                                "business_state": recovery_layers.business_state,
                                "overall_state": recovery_layers.overall_state,
                            }
                            if recovery_layers is not None
                            else None
                        ),
                    },
                    success=success,
                    executed=action_result.executed,
                    now=timestamp,
                )
                self.state_store.record_action(host_id, target_id, action, timestamp, success)
                breaker_tripped = self.state_store.failure_breaker(
                    host_id, target_id, action, max_consecutive_failures
                )
            else:
                self.ledger.record(timestamp, success)
                breaker_tripped = self.ledger.tripped(max_consecutive_failures)
        except StateStoreError:
            return ControllerResult(
                state="escalated",
                event_id=event_id,
                action_result=action_result,
                recovery=recovery,
                reason_codes=("audit_persistence_failed",),
                cooldown_state="audit_persistence_failed",
                failure_breaker_tripped=breaker_tripped,
                intent_id=intent_id,
            )

        if not successful_action:
            state = "failed"
            reasons = (
                "action_timeout"
                if action_result.reason == "executor_timeout"
                else "action_failed",
            )
        elif recovery is not None and not recovery.recovered:
            state = "escalated" if breaker_tripped else recovery.state
            reasons = recovery.reason_codes
        elif recovery_layers is not None and not recovery_layers.host_mitigated:
            state = "escalated" if breaker_tripped else "failed"
            reasons = recovery_layers.reason_codes
        elif recovery_layers is not None and recovery_layers.business_recovered:
            state = "recovered"
            reasons = recovery_layers.reason_codes
        elif recovery_layers is not None:
            state = "mitigated"
            reasons = recovery_layers.reason_codes
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
            intent_id=intent_id,
            recovery_layers=recovery_layers,
        )
