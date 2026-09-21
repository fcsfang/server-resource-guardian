import tempfile
import threading
import unittest
import stat
from unittest.mock import patch
from pathlib import Path

from src.guardian_actions import ActionResult, Authorization, MockActionExecutor
from src.guardian_controller import GuardianController
from src.guardian_state import GuardianStateStore, StateStoreError


def auth(approval_id="approval-1", target="abcdef123456", action="graceful_stop", expires_at=2000.0):
    return Authorization(approval_id, "local-disposable", target, action, expires_at)


def enforce_event(event_id="event-1", target="abcdef123456", action="graceful_stop"):
    return {
        "event_id": event_id,
        "host_id": "test-host",
        "state": "critical",
        "object_candidates": [{"kind": "container", "id": target, "confidence": "high"}],
        "decision": {
            "mode": "enforce",
            "action": action,
            "protected": False,
            "timeout_seconds": 5,
        },
    }


class GuardianStateStoreTests(unittest.TestCase):
    def store(self, temp):
        return GuardianStateStore(Path(temp) / "guardian-state.db")

    def test_shared_state_mode_is_explicit_and_bounded(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "shared-state.db"
            GuardianStateStore(path, file_mode=0o660)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o660)
            with self.assertRaises(ValueError):
                GuardianStateStore(Path(temp) / "invalid.db", file_mode=0o666)

    def test_shared_state_owner_can_leave_sufficient_group_mode_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "shared-state.db"
            path.touch(mode=0o660)
            path.chmod(0o660)
            with patch("src.guardian_state.os.chmod", side_effect=PermissionError("not owner")):
                GuardianStateStore(path, file_mode=0o660)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o660)

    def test_capability_is_registered_and_consumed_once(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp)
            capability = auth()
            store.register_capability(capability)
            self.assertTrue(store.consume_capability(capability, event_id="event-1", now=1000).allowed)
            second = store.consume_capability(capability, event_id="event-2", now=1001)
            self.assertFalse(second.allowed)
            self.assertEqual(second.reason, "capability_already_consumed")

    def test_capability_conflict_and_expiry_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp)
            store.register_capability(auth(approval_id="conflict"))
            with self.assertRaisesRegex(StateStoreError, "capability_conflict"):
                store.register_capability(auth(approval_id="conflict", action="restart"))
            expired = auth(approval_id="expired", expires_at=10)
            store.register_capability(expired)
            result = store.consume_capability(expired, event_id="expired-event", now=11)
            self.assertEqual(result.reason, "capability_expired")

    def test_intent_is_idempotent_and_active_target_is_serialized(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp)
            first = store.claim_intent(
                host_id="host", object_id="object", action="graceful_stop", event_id="event-1", audit_payload={"ok": True}, now=100
            )
            replay = store.claim_intent(
                host_id="host", object_id="object", action="graceful_stop", event_id="event-1", audit_payload={"ok": True}, now=101
            )
            blocked = store.claim_intent(
                host_id="host", object_id="object", action="graceful_stop", event_id="event-2", audit_payload={"ok": True}, now=101
            )
            self.assertTrue(first.claimed)
            self.assertEqual(replay.reason, "idempotent_intent_reused")
            self.assertEqual(blocked.reason, "active_intent_exists")
            self.assertEqual(first.intent_id, replay.intent_id)

    def test_audit_serialization_failure_leaves_no_intent(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp)
            with self.assertRaisesRegex(StateStoreError, "audit_payload_not_serializable"):
                store.claim_intent(
                    host_id="host", object_id="object", action="graceful_stop", event_id="event-1", audit_payload={"bad": object()}, now=100
                )
            retry = store.claim_intent(
                host_id="host", object_id="object", action="graceful_stop", event_id="event-1", audit_payload={"ok": True}, now=101
            )
            self.assertTrue(retry.claimed)

    def test_audit_payload_size_is_bounded_before_intent_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp)
            with self.assertRaisesRegex(StateStoreError, "audit_payload_too_large"):
                store.claim_intent(
                    host_id="host",
                    object_id="object",
                    action="graceful_stop",
                    event_id="event-large",
                    audit_payload={"large": "x" * (64 * 1024)},
                    now=100,
                )
            retry = store.claim_intent(
                host_id="host",
                object_id="object",
                action="graceful_stop",
                event_id="event-large",
                audit_payload={"ok": True},
                now=101,
            )
            self.assertTrue(retry.claimed)

    def test_crash_reconciliation_blocks_automatic_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "guardian-state.db"
            first_store = GuardianStateStore(path)
            first = first_store.claim_intent(
                host_id="host", object_id="object", action="graceful_stop", event_id="event-1", audit_payload={"ok": True}, now=100
            )
            self.assertTrue(first_store.mark_execution_started(first.intent_id, now=101))
            restarted = GuardianStateStore(path)
            pending = restarted.reconcile_pending(now=102)
            self.assertEqual(pending[0]["state"], "RECONCILIATION_REQUIRED")
            retry = restarted.claim_intent(
                host_id="host", object_id="object", action="graceful_stop", event_id="event-2", audit_payload={"ok": True}, now=103
            )
            self.assertEqual(retry.reason, "active_intent_exists")

    def test_result_is_durable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp)
            claim = store.claim_intent(
                host_id="host", object_id="object", action="graceful_stop", event_id="event-1", audit_payload={"ok": True}, now=100
            )
            store.mark_execution_started(claim.intent_id, now=101)
            result = store.record_result(
                claim.intent_id, {"returncode": 0}, success=True, executed=True, now=102
            )
            repeated = store.record_result(
                claim.intent_id, {"returncode": 99}, success=False, executed=True, now=103
            )
            self.assertEqual(result, "SUCCEEDED")
            self.assertEqual(repeated, "SUCCEEDED")
            self.assertEqual(store.get_intent(claim.intent_id)["state"], "SUCCEEDED")

    def test_concurrent_claims_have_one_winner(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "guardian-state.db"
            stores = [GuardianStateStore(path), GuardianStateStore(path)]
            barrier = threading.Barrier(2)
            results = []

            def claim(store, event_id):
                barrier.wait()
                results.append(
                    store.claim_intent(
                        host_id="host", object_id="object", action="graceful_stop", event_id=event_id,
                        audit_payload={"event_id": event_id}, now=100,
                    )
                )

            threads = [
                threading.Thread(target=claim, args=(stores[0], "event-a")),
                threading.Thread(target=claim, args=(stores[1], "event-b")),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(sum(item.claimed for item in results), 1)
            self.assertEqual(sum(item.reason == "active_intent_exists" for item in results), 1)

    def test_persistent_cooldown_and_failure_breaker(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp)
            self.assertEqual(store.allow_action("host", "object", "stop", 100, 10, 2, 300), (True, "allowed"))
            store.record_action("host", "object", "stop", 100, success=False)
            self.assertEqual(store.allow_action("host", "object", "stop", 105, 10, 2, 300), (False, "cooldown_active"))
            store.record_action("host", "object", "stop", 111, success=False)
            self.assertTrue(store.failure_breaker("host", "object", "stop", 2))

    def test_action_slot_serializes_concurrent_execution_window(self):
        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp)
            self.assertTrue(store.claim_action_slot("host", "graceful_stop", "intent-1", now=100))
            self.assertFalse(store.claim_action_slot("host", "graceful_stop", "intent-2", now=101))
            self.assertFalse(store.release_action_slot("host", "graceful_stop", "intent-2", now=102))
            self.assertTrue(store.release_action_slot("host", "graceful_stop", "intent-1", now=103))
            self.assertTrue(store.claim_action_slot("host", "graceful_stop", "intent-2", now=104))

    def test_action_slot_restart_requires_manual_reconciliation(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "guardian-state.db"
            first = self.store(temp)
            self.assertTrue(first.claim_action_slot("host", "graceful_stop", "intent-1", now=100))
            restarted = GuardianStateStore(path)
            pending = restarted.reconcile_pending(now=101)
            self.assertTrue(any(item.get("kind") == "action_slot" for item in pending))
            self.assertFalse(restarted.claim_action_slot("host", "graceful_stop", "intent-2", now=102))

    def test_controller_uses_durable_intent_before_executor(self):
        class Executor:
            def __init__(self):
                self.calls = 0

            def execute(self, request, now=None):
                self.calls += 1
                return ActionResult(True, request.action, request.target_id, 0)

        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp)
            executor = Executor()
            controller = GuardianController(executor, state_store=store)
            result = controller.enforce(
                enforce_event(), auth(), ["graceful_stop"], now=1000, cooldown_seconds=0, max_actions=10
            )
            replay = controller.enforce(
                enforce_event(), auth(), ["graceful_stop"], now=1001, cooldown_seconds=0, max_actions=10
            )
            self.assertEqual(result.state, "executed")
            self.assertEqual(replay.reason_codes, ("capability_already_consumed",))
            self.assertEqual(executor.calls, 1)

    def test_controller_rejects_audit_failure_before_executor(self):
        class Executor:
            def __init__(self):
                self.calls = 0

            def execute(self, request, now=None):
                self.calls += 1
                return ActionResult(True, request.action, request.target_id, 0)

        with tempfile.TemporaryDirectory() as temp:
            store = self.store(temp)
            executor = Executor()
            controller = GuardianController(executor, state_store=store)
            broken = enforce_event()
            broken["non_serializable"] = object()
            result = controller.enforce(
                broken,
                auth(approval_id="audit-failure"),
                ["graceful_stop"],
                now=1000,
                cooldown_seconds=0,
                max_actions=10,
            )
            self.assertEqual(result.state, "escalated")
            self.assertIn("audit_payload_not_serializable", result.reason_codes)
            self.assertEqual(executor.calls, 0)


if __name__ == "__main__":
    unittest.main()
