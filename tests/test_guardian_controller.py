import time
import unittest

from src.guardian_actions import ActionResult, Authorization, MockActionExecutor
from src.guardian_controller import GuardianController
from src.guardian_recovery import RecoveryObservation, RecoveryPolicy


def enforce_event(action="graceful_stop", protected=False, candidates=None):
    return {
        "event_id": "event-controller-1",
        "state": "critical",
        "object_candidates": candidates if candidates is not None else [
            {"kind": "container", "id": "abcdef123456", "name": "discardable"},
        ],
        "decision": {
            "mode": "enforce",
            "action": action,
            "protected": protected,
            "timeout_seconds": 5,
        },
    }


def authorization(action="graceful_stop", target="abcdef123456"):
    return Authorization(
        approval_id="controller-test-approval",
        environment="local-disposable",
        target_id=target,
        action=action,
        expires_at=time.time() + 300,
    )


class SuccessfulFakeExecutor:
    def __init__(self):
        self.requests = []

    def execute(self, request, now=None):
        self.requests.append(request)
        return ActionResult(True, request.action, request.target_id, 0, stdout="ok")


class FailingFakeExecutor:
    def execute(self, request, now=None):
        return ActionResult(True, request.action, request.target_id, 1, stderr="failed")


class GuardianControllerTests(unittest.TestCase):
    def recovery(self, target="abcdef123456"):
        return RecoveryObservation(target, True, False, "exited", "normal", 2.0)

    def test_mock_controller_runs_full_planning_and_recovery_chain_without_mutation(self):
        executor = MockActionExecutor()
        result = GuardianController(executor).enforce(
            enforce_event(),
            authorization(),
            ["graceful_stop"],
            now=100.0,
            recovery_observation=self.recovery(),
            recovery_policy=RecoveryPolicy("graceful_stop"),
        )
        self.assertEqual(result.state, "planned")
        self.assertEqual(result.recovery.state, "recovered")
        self.assertFalse(result.action_result.executed)
        self.assertEqual(len(executor.requests), 1)

    def test_missing_authorization_is_denied_before_executor(self):
        executor = SuccessfulFakeExecutor()
        result = GuardianController(executor).enforce(
            enforce_event(), None, ["graceful_stop"], now=100.0
        )
        self.assertEqual(result.state, "denied")
        self.assertEqual(result.reason_codes, ("explicit_authorization_required",))
        self.assertEqual(executor.requests, [])

    def test_protected_or_ambiguous_target_is_denied_before_executor(self):
        executor = SuccessfulFakeExecutor()
        protected = GuardianController(executor).enforce(
            enforce_event(protected=True), authorization(), ["graceful_stop"], now=100.0
        )
        self.assertEqual(protected.reason_codes, ("protected_object",))
        ambiguous = GuardianController(executor).enforce(
            enforce_event(candidates=[
                {"kind": "container", "id": "abcdef123456"},
                {"kind": "container", "id": "fedcba654321"},
            ]),
            authorization(),
            ["graceful_stop"],
            now=100.0,
        )
        self.assertEqual(ambiguous.reason_codes, ("ambiguous_object_identity",))
        self.assertEqual(executor.requests, [])

    def test_cooldown_blocks_second_action_and_failure_breaker_escalates(self):
        executor = SuccessfulFakeExecutor()
        controller = GuardianController(executor)
        first = controller.enforce(
            enforce_event(), authorization(), ["graceful_stop"], now=100.0,
            recovery_observation=self.recovery(),
            recovery_policy=RecoveryPolicy("graceful_stop"),
            cooldown_seconds=10.0,
        )
        self.assertEqual(first.state, "recovered")
        second = controller.enforce(
            enforce_event(), authorization(), ["graceful_stop"], now=105.0,
            cooldown_seconds=10.0,
        )
        self.assertEqual(second.reason_codes, ("cooldown_active",))
        self.assertEqual(len(executor.requests), 1)

    def test_non_enforce_event_is_never_forwarded(self):
        executor = SuccessfulFakeExecutor()
        event = enforce_event()
        event["decision"]["mode"] = "simulate"
        result = GuardianController(executor).enforce(
            event, authorization(), ["graceful_stop"], now=100.0
        )
        self.assertEqual(result.reason_codes, ("enforce_mode_required",))
        self.assertEqual(executor.requests, [])

    def test_repeated_runtime_failures_escalate_and_trip_breaker(self):
        controller = GuardianController(FailingFakeExecutor())
        first = controller.enforce(
            enforce_event(), authorization(), ["graceful_stop"], now=100.0,
            cooldown_seconds=0.0, max_actions=10,
        )
        second = controller.enforce(
            enforce_event(), authorization(), ["graceful_stop"], now=101.0,
            cooldown_seconds=0.0, max_actions=10,
        )
        third = controller.enforce(
            enforce_event(), authorization(), ["graceful_stop"], now=102.0,
            cooldown_seconds=0.0, max_actions=10,
        )
        self.assertEqual(first.state, "failed")
        self.assertFalse(first.failure_breaker_tripped)
        self.assertEqual(second.state, "failed")
        self.assertTrue(second.failure_breaker_tripped)
        self.assertEqual(third.reason_codes, ("failure_breaker_tripped",))


if __name__ == "__main__":
    unittest.main()
