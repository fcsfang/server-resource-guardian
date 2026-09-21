import copy
import json
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path

from src.guardian_notification_outbox import DurableNotificationOutbox
from src.guardian_notifications import FakeNotificationSink, build_notification_event


TARGET = "a" * 64


def notification(
    state="critical",
    *,
    event_id="event-1",
    observed_ns=1_000_000_000,
    incident_key="incident-a",
):
    source = {
        "event_id": event_id,
        "observed_at": "2026-09-21T00:00:00Z",
        "observed_monotonic_ns": observed_ns,
        "host_id": "local-host",
        "state": state,
        "resource_evaluations": {
            "cpu": {"risk": {"state": "warning"}},
            "memory": {"risk": {"state": state if state != "recovered" else "recovered"}},
            "disk_capacity": {"risk": {"state": "normal"}},
            "io": {"risk": {"state": "normal"}},
        },
        "joint_evaluation": {
            "active_resources": ["cpu", "memory"] if state != "recovered" else [],
            "target_state": "TARGET_CONFIRMED" if state != "recovered" else "NO_TARGET",
            "target": {"id": TARGET} if state != "recovered" else None,
        },
        "target_attribution": {"target": {"id": TARGET}} if state != "recovered" else {},
        "decision": {
            "mode": "simulate",
            "action": "graceful_stop" if state != "recovered" else "none",
            "execution": "not_executed",
            "target_id": TARGET if state != "recovered" else None,
            "target_state": "TARGET_CONFIRMED" if state != "recovered" else "NO_TARGET",
            "reason_codes": ["outbox-test"],
            "quality_flags": [],
        },
        "evidence": {"config_digest": "outbox-test-digest"},
    }
    return build_notification_event(source, incident_key=incident_key)


class DurableNotificationOutboxTests(unittest.TestCase):
    def store(self, directory, sink, **kwargs):
        return DurableNotificationOutbox(Path(directory) / "notifications.db", sink, **kwargs)

    def test_enqueue_is_durable_before_delivery_and_restart_keeps_dedup(self):
        with tempfile.TemporaryDirectory() as temp:
            sink = FakeNotificationSink()
            first = self.store(temp, sink, max_age_seconds=60)
            payload = notification()
            queued = first.enqueue(payload, now_monotonic_ns=1_000_000_000)
            self.assertEqual(queued["status"], "queued")
            self.assertEqual(sink.delivered, [])

            restarted = self.store(temp, sink, max_age_seconds=60)
            self.assertEqual(restarted.status_counts()["PENDING"], 1)
            delivered = restarted.drain_pending(now_monotonic_ns=1_000_000_001)
            self.assertEqual(delivered[0]["status"], "delivered")
            duplicate = restarted.enqueue(copy.deepcopy(payload), now_monotonic_ns=1_000_000_002)
            self.assertEqual(duplicate["status"], "duplicate_suppressed")
            self.assertEqual(len(sink.delivered), 1)
            self.assertTrue(restarted.verify_audit()["valid"])

    def test_dead_letter_survives_restart_and_explicit_redrive(self):
        with tempfile.TemporaryDirectory() as temp:
            failing_sink = FakeNotificationSink(failures_before_success=5)
            first = self.store(temp, failing_sink, max_attempts=2, max_age_seconds=60)
            payload = notification()
            first.enqueue(payload, now_monotonic_ns=1_000_000_000)
            failed = first.drain_pending(now_monotonic_ns=1_000_000_001)[0]
            self.assertEqual(failed["status"], "dead_letter")
            self.assertEqual(first.status_counts()["DEAD_LETTER"], 1)

            failing_sink.failures_before_success = 0
            restarted = self.store(temp, failing_sink, max_attempts=2, max_age_seconds=60)
            self.assertEqual(restarted.status_counts()["DEAD_LETTER"], 1)
            replay = restarted.redrive_dead_letters(now_monotonic_ns=1_000_000_002)
            self.assertEqual(replay[0]["status"], "delivered")
            self.assertEqual(restarted.status_counts()["DELIVERED"], 1)
            self.assertEqual(len(failing_sink.delivered), 1)
            self.assertTrue(restarted.verify_audit()["valid"])

    def test_inflight_restart_is_reconciliation_required_until_explicit_redrive(self):
        with tempfile.TemporaryDirectory() as temp:
            sink = FakeNotificationSink()
            first = self.store(temp, sink, max_age_seconds=60)
            payload = notification()
            first.enqueue(payload, now_monotonic_ns=1_000_000_000)
            claimed = first._claim_one()
            self.assertIsNotNone(claimed)
            self.assertEqual(sink.delivered, [])

            restarted = self.store(temp, sink, max_age_seconds=60)
            row = restarted.get_notification(payload["notification_id"])
            self.assertEqual(row["status"], "DEAD_LETTER")
            self.assertEqual(row["last_error"], "delivery_interrupted")
            self.assertEqual(len(sink.delivered), 0)
            replay = restarted.redrive_dead_letters(now_monotonic_ns=1_000_000_001)
            self.assertEqual(replay[0]["status"], "delivered")
            self.assertEqual(len(sink.delivered), 1)
            self.assertTrue(restarted.verify_audit()["valid"])

    def test_rate_limit_dead_letter_is_durable_and_redrive_waits_for_window(self):
        with tempfile.TemporaryDirectory() as temp:
            sink = FakeNotificationSink()
            store = self.store(
                temp,
                sink,
                max_age_seconds=60,
                rate_limit_count=1,
                rate_limit_window_seconds=10,
            )
            first = notification(event_id="first", observed_ns=1_000_000_000, incident_key="incident-a")
            second = notification(event_id="second", observed_ns=1_000_000_001, incident_key="incident-b")
            store.enqueue(first, now_monotonic_ns=1_000_000_000)
            store.enqueue(second, now_monotonic_ns=1_000_000_001)
            results = store.drain_pending(now_monotonic_ns=1_000_000_001)
            self.assertEqual([item["status"] for item in results], ["delivered", "rate_limited"])
            self.assertEqual(store.status_counts()["DEAD_LETTER"], 1)
            replay = store.redrive_dead_letters(now_monotonic_ns=11_000_000_002)
            self.assertEqual(replay[0]["status"], "delivered")
            self.assertEqual(len(sink.delivered), 2)

    def test_capacity_and_security_boundary_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            sink = FakeNotificationSink()
            store = self.store(temp, sink, max_age_seconds=60, max_pending=1)
            first = notification()
            second = notification(event_id="second", observed_ns=1_000_000_001, incident_key="incident-b")
            self.assertEqual(store.enqueue(first, now_monotonic_ns=1_000_000_000)["status"], "queued")
            self.assertEqual(store.enqueue(second, now_monotonic_ns=1_000_000_001)["reason"], "outbox_capacity_reached")
            unsafe = copy.deepcopy(notification(event_id="unsafe", observed_ns=1_000_000_002))
            unsafe["action_context"]["execution"] = "executed"
            self.assertEqual(store.enqueue(unsafe, now_monotonic_ns=1_000_000_002)["reason"], "action_boundary_invalid")
            self.assertEqual(store.enqueue({"bad": object()}, now_monotonic_ns=1_000_000_002)["status"], "rejected")
            self.assertEqual(sink.delivered, [])

    def test_enqueue_event_redacts_before_durable_persistence(self):
        with tempfile.TemporaryDirectory() as temp:
            sink = FakeNotificationSink()
            store = self.store(temp, sink, max_age_seconds=60)
            source = {
                "event_id": "raw-event",
                "observed_at": "2026-09-21T00:00:00Z",
                "observed_monotonic_ns": 1_000_000_000,
                "host_id": "local-host",
                "state": "critical",
                "resource_evaluations": {"memory": {"risk": {"state": "critical"}}},
                "joint_evaluation": {
                    "active_resources": ["memory"],
                    "target_state": "TARGET_CONFIRMED",
                    "target": {"id": TARGET},
                },
                "target_attribution": {"target": {"id": TARGET}},
                "decision": {
                    "mode": "simulate",
                    "action": "graceful_stop",
                    "execution": "not_executed",
                    "target_id": TARGET,
                    "reason_codes": ["outbox-test"],
                },
                "signals": {"raw_secret": "must-not-persist"},
            }
            queued = store.enqueue_event(
                source,
                incident_key="incident-raw",
                now_monotonic_ns=1_000_000_000,
            )
            self.assertEqual(queued["status"], "queued")
            row = store.get_notification(queued["notification_id"])
            serialized = json.dumps(row, ensure_ascii=False)
            self.assertNotIn("raw_secret", serialized)
            self.assertNotIn("must-not-persist", serialized)
            self.assertEqual(row["status"], "PENDING")
            self.assertEqual(store.drain_pending(now_monotonic_ns=1_000_000_001)[0]["status"], "delivered")
            self.assertEqual(sink.delivered[0]["security"]["raw_signals_included"], False)

    def test_out_of_order_recovery_and_post_recovery_state_survive_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            sink = FakeNotificationSink()
            store = self.store(temp, sink, max_age_seconds=60)
            critical = notification(event_id="critical", observed_ns=2_000_000_000)
            older = notification("warning", event_id="older", observed_ns=1_000_000_000)
            recovered = notification("recovered", event_id="recovered", observed_ns=3_000_000_000)
            warning_again = notification("warning", event_id="warning-again", observed_ns=4_000_000_000)
            self.assertEqual(store.enqueue(critical, now_monotonic_ns=2_000_000_000)["status"], "queued")
            self.assertEqual(store.drain_pending(now_monotonic_ns=2_000_000_001)[0]["status"], "delivered")
            restarted = self.store(temp, sink, max_age_seconds=60)
            self.assertEqual(restarted.enqueue(older, now_monotonic_ns=2_000_000_002)["reason"], "out_of_order")
            self.assertEqual(restarted.enqueue(recovered, now_monotonic_ns=3_000_000_000)["status"], "queued")
            self.assertEqual(restarted.drain_pending(now_monotonic_ns=3_000_000_001)[0]["status"], "delivered")
            self.assertEqual(restarted.enqueue(warning_again, now_monotonic_ns=4_000_000_000)["status"], "queued")
            self.assertEqual(restarted.drain_pending(now_monotonic_ns=4_000_000_001)[0]["status"], "delivered")
            self.assertEqual(len(sink.delivered), 3)

    def test_audit_hash_chain_detects_tampering(self):
        with tempfile.TemporaryDirectory() as temp:
            sink = FakeNotificationSink()
            store = self.store(temp, sink, max_age_seconds=60)
            payload = notification()
            store.enqueue(payload, now_monotonic_ns=1_000_000_000)
            self.assertTrue(store.verify_audit()["valid"])
            connection = sqlite3.connect(str(Path(temp) / "notifications.db"))
            try:
                connection.execute("UPDATE notification_audits SET record_digest='tampered' WHERE sequence=1")
                connection.commit()
            finally:
                connection.close()
            verification = store.verify_audit()
            self.assertFalse(verification["valid"])
            self.assertTrue(any("record_digest_mismatch" in value for value in verification["errors"]))

    def test_outbox_numeric_limits_and_batch_bounds_fail_closed(self):
        constructor_cases = (
            ({"max_attempts": True}, "max_attempts_out_of_bounds"),
            ({"max_attempts": 2.5}, "max_attempts_out_of_bounds"),
            ({"max_age_seconds": math.nan}, "notification_window_must_be_positive"),
            ({"max_age_seconds": math.inf}, "notification_window_must_be_positive"),
            ({"rate_limit_count": True}, "rate_limit_count_out_of_bounds"),
            ({"rate_limit_count": 1.5}, "rate_limit_count_out_of_bounds"),
            ({"rate_limit_window_seconds": -math.inf}, "notification_window_must_be_positive"),
            ({"max_pending": 1.5}, "max_pending_out_of_bounds"),
            ({"max_payload_bytes": True}, "max_payload_bytes_out_of_bounds"),
            ({"max_payload_bytes": 1024.0}, "max_payload_bytes_out_of_bounds"),
        )
        with tempfile.TemporaryDirectory() as temp:
            sink = FakeNotificationSink()
            for kwargs, error in constructor_cases:
                with self.subTest(kwargs=kwargs):
                    with self.assertRaisesRegex(ValueError, error):
                        self.store(temp, sink, **kwargs)

            store = self.store(temp, sink, max_age_seconds=60)
            for invalid in (True, 1.5, 0, 1001):
                with self.subTest(max_items=invalid):
                    with self.assertRaisesRegex(ValueError, "max_items_out_of_bounds"):
                        store.drain_pending(now_monotonic_ns=1_000_000_000, max_items=invalid)
                    with self.assertRaisesRegex(ValueError, "max_items_out_of_bounds"):
                        store.redrive_dead_letters(now_monotonic_ns=1_000_000_000, max_items=invalid)


if __name__ == "__main__":
    unittest.main()
