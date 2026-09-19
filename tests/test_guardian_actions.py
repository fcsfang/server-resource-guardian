import time
import unittest

from src.guardian_actions import (
    ActionDenied,
    ActionRequest,
    Authorization,
    DockerActionAdapter,
    MockActionExecutor,
    allowed_actions,
    validate_request,
)


class GuardianActionTests(unittest.TestCase):
    def authorization(self, action="graceful_stop", target="abcdef123456"):
        return Authorization(
            approval_id="local-test-approval",
            environment="local-disposable",
            target_id=target,
            action=action,
            expires_at=time.time() + 300,
        )

    def request(self, action="graceful_stop", protected=False, target="abcdef123456", authorization=None):
        return ActionRequest(
            event_id="event-1",
            target_id=target,
            action=action,
            protected=protected,
            allowed_actions=allowed_actions([action]),
            authorization=authorization if authorization is not None else self.authorization(action, target),
        )

    def test_protected_object_is_denied_before_runner(self):
        request = self.request(protected=True)
        with self.assertRaisesRegex(ActionDenied, "protected_object"):
            validate_request(request, now=1000.0)

    def test_missing_or_expired_authorization_is_denied(self):
        missing = ActionRequest(
            event_id="event-1",
            target_id="abcdef123456",
            action="graceful_stop",
            protected=False,
            allowed_actions=frozenset({"graceful_stop"}),
            authorization=None,
        )
        with self.assertRaisesRegex(ActionDenied, "explicit_authorization_required"):
            validate_request(missing, now=1000.0)

        expired = self.authorization()
        expired = Authorization(**{**expired.__dict__, "expires_at": 999.0})
        with self.assertRaisesRegex(ActionDenied, "authorization_expired"):
            validate_request(self.request(authorization=expired), now=1000.0)

    def test_mock_executor_records_but_never_executes(self):
        executor = MockActionExecutor()
        result = executor.execute(self.request())
        self.assertFalse(result.executed)
        self.assertEqual(result.reason, "mock_only_not_executed")
        self.assertEqual(len(executor.requests), 1)

    def test_docker_adapter_uses_argument_vector_after_authorization(self):
        calls = []

        def fake_runner(command, **kwargs):
            calls.append((command, kwargs))
            return type("Result", (), {"returncode": 0, "stdout": "stopped", "stderr": ""})()

        request = self.request()
        result = DockerActionAdapter(fake_runner).execute(request, now=1000.0)
        self.assertTrue(result.executed)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(calls[0][0], ["docker", "stop", "--timeout", "30", "abcdef123456"])
        self.assertFalse(calls[0][1].get("shell", False))

    def test_invalid_target_and_unallowlisted_action_are_denied(self):
        with self.assertRaisesRegex(ActionDenied, "unstable_or_invalid_container_id"):
            validate_request(self.request(target="name-not-id"), now=1000.0)

        request = ActionRequest(
            event_id="event-1",
            target_id="abcdef123456",
            action="restart",
            protected=False,
            allowed_actions=frozenset({"graceful_stop"}),
            authorization=self.authorization("restart"),
        )
        with self.assertRaisesRegex(ActionDenied, "action_not_allowlisted"):
            validate_request(request, now=1000.0)


if __name__ == "__main__":
    unittest.main()
