import json
import tempfile
import unittest
from pathlib import Path

from src.guardian_actions import ActionDenied, Authorization
from src.guardian_enforce import (
    load_authorization,
    probe_container_recovery,
    run_enforce,
    serialize_audit_record,
    serialize_result,
)
from src.guardian_recovery import CooldownLedger, RecoveryPolicy, assess_recovery


def event():
    return {
        "event_id": "event-enforce-1",
        "state": "critical",
        "object_candidates": [{"kind": "container", "id": "abcdef123456", "name": "discardable"}],
        "decision": {
            "mode": "enforce",
            "action": "graceful_stop",
            "protected": False,
            "timeout_seconds": 5,
        },
    }


def authorization():
    return Authorization(
        approval_id="explicit-local-test",
        environment="local-disposable",
        target_id="abcdef123456",
        action="graceful_stop",
        expires_at=2000.0,
    )


class GuardianEnforceTests(unittest.TestCase):
    def test_mock_bridge_is_planned_and_serializable(self):
        result = run_enforce(event(), authorization(), ["graceful_stop"], executor_kind="mock", now=1000.0)
        self.assertEqual(result.state, "planned")
        self.assertFalse(result.action_result.executed)
        output = serialize_result(result)
        self.assertEqual(output["action_result"]["reason"], "mock_only_not_executed")

    def test_audit_record_contains_event_and_result(self):
        result = run_enforce(event(), authorization(), ["graceful_stop"], executor_kind="mock", now=1000.0)
        record = serialize_audit_record(event(), result)
        self.assertEqual(record["schema"], "guardian.enforce.v1")
        self.assertEqual(record["event"]["event_id"], "event-enforce-1")
        self.assertEqual(record["result"]["state"], "planned")
        self.assertTrue(record["recorded_at"].endswith("Z"))

    def test_docker_executor_requires_explicit_local_confirmation(self):
        with self.assertRaisesRegex(ActionDenied, "local_disposable_confirmation_required"):
            run_enforce(
                event(), authorization(), ["graceful_stop"], executor_kind="docker", now=1000.0
            )

        with self.assertRaisesRegex(ActionDenied, "persistent_ledger_required"):
            run_enforce(
                event(), authorization(), ["graceful_stop"], executor_kind="docker",
                confirm_local_disposable=True, now=1000.0,
            )

    def test_real_adapter_path_uses_fake_runner_and_read_only_recovery_probe(self):
        calls = []

        def fake_runner(command, **kwargs):
            calls.append((command, kwargs))
            if command[:2] == ["docker", "stop"]:
                return type("Result", (), {"returncode": 0, "stdout": "stopped", "stderr": ""})()
            return type(
                "Result",
                (),
                {"returncode": 0, "stdout": json.dumps({"Running": False, "Status": "exited", "ExitCode": 0}), "stderr": ""},
            )()

        result = run_enforce(
            event(),
            authorization(),
            ["graceful_stop"],
            executor_kind="docker",
            confirm_local_disposable=True,
            ledger=CooldownLedger(),
            runner=fake_runner,
            now=1000.0,
            recovery_wait_seconds=1.0,
            recovery_poll_seconds=0.0,
        )
        self.assertEqual(result.state, "recovered")
        self.assertTrue(result.action_result.executed)
        self.assertEqual(result.recovery.reason_codes, ("target_stopped",))
        self.assertEqual(calls[0][0], ["docker", "stop", "--timeout", "5", "abcdef123456"])
        self.assertEqual(calls[1][0][:3], ["docker", "inspect", "--format"])
        self.assertFalse(calls[0][1].get("shell", False))

    def test_recovery_probe_stops_polling_after_recovered_state(self):
        def fake_runner(command, **kwargs):
            return type(
                "Result",
                (),
                {"returncode": 0, "stdout": json.dumps({"Running": False, "Status": "exited", "ExitCode": 0}), "stderr": ""},
            )()

        observation = probe_container_recovery(
            type("Request", (), {"target_id": "abcdef123456", "action": "graceful_stop"})(),
            fake_runner,
            max_wait_seconds=1.0,
            poll_interval_seconds=0.0,
            clock=iter([10.0, 10.0]).__next__,
            sleep=lambda _seconds: None,
        )
        self.assertFalse(observation.target_running)
        self.assertEqual(observation.health_status, "exited")
        self.assertEqual(observation.exit_code, 0)

    def test_recovery_probe_reports_window_expiry_when_target_stays_running(self):
        def fake_runner(command, **kwargs):
            return type(
                "Result",
                (),
                {"returncode": 0, "stdout": json.dumps({"Running": True, "Status": "running"}), "stderr": ""},
            )()

        observation = probe_container_recovery(
            type("Request", (), {"target_id": "abcdef123456", "action": "graceful_stop"})(),
            fake_runner,
            max_wait_seconds=1.0,
            poll_interval_seconds=0.0,
            clock=iter([10.0, 10.0, 12.0]).__next__,
            sleep=lambda _seconds: None,
        )
        recovery = assess_recovery(RecoveryPolicy("graceful_stop", max_wait_seconds=1.0), observation)
        self.assertEqual(recovery.state, "failed")
        self.assertEqual(recovery.reason_codes, ("recovery_window_expired",))

    def test_executor_timeout_is_recorded_and_repeated_timeout_trips_breaker(self):
        import subprocess

        def timeout_runner(_command, **_kwargs):
            raise subprocess.TimeoutExpired(["docker", "stop"], 35)

        ledger = CooldownLedger()
        first = run_enforce(
            event(), authorization(), ["graceful_stop"],
            executor_kind="docker", confirm_local_disposable=True, ledger=ledger,
            runner=timeout_runner, now=1000.0, cooldown_seconds=0.0, max_actions=10,
        )
        second = run_enforce(
            event(), authorization(), ["graceful_stop"],
            executor_kind="docker", confirm_local_disposable=True, ledger=ledger,
            runner=timeout_runner, now=1001.0, cooldown_seconds=0.0, max_actions=10,
        )
        self.assertEqual(first.state, "failed")
        self.assertEqual(first.reason_codes, ("action_timeout",))
        self.assertEqual(second.state, "failed")
        self.assertTrue(second.failure_breaker_tripped)
        self.assertEqual(ledger.consecutive_failures, 2)

    def test_authorization_file_is_parsed_without_extra_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "authorization.json"
            path.write_text(json.dumps({
                "approval_id": "a-1",
                "environment": "local-disposable",
                "target_id": "abcdef123456",
                "action": "graceful_stop",
                "expires_at": 2000,
                "ignored": "not used",
            }), encoding="utf-8")
            parsed = load_authorization(path)
        self.assertEqual(parsed.approval_id, "a-1")
        self.assertEqual(parsed.expires_at, 2000.0)


if __name__ == "__main__":
    unittest.main()
