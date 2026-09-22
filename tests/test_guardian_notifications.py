import copy
import math
import unittest

from src.guardian_notifications import (
    DisabledNotificationSink,
    FakeNotificationSink,
    NotificationDispatcher,
    NotificationError,
    build_notification_event,
)


TARGET = "a" * 64


def guardian_event(
    state="critical",
    *,
    event_id="event-1",
    observed_ns=1_000_000_000,
    incident_key="incident-a",
):
    source = {
        "event_id": event_id,
        "observed_at": "2026-09-20T15:00:00Z",
        "observed_monotonic_ns": observed_ns,
        "host_id": "host-local",
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
        "target_attribution": {"target": {"id": TARGET}},
        "decision": {
            "mode": "simulate",
            "action": "graceful_stop" if state != "recovered" else "none",
            "execution": "not_executed",
            "target_id": TARGET if state != "recovered" else None,
            "target_state": "TARGET_CONFIRMED" if state != "recovered" else "NO_TARGET",
            "reason_codes": ["multi_resource_simulate_only"],
            "quality_flags": [],
        },
        "evidence": {"config_digest": "digest-local"},
        "signals": {"raw_secret": "must-not-leak"},
    }
    return build_notification_event(source, incident_key=incident_key)


class GuardianNotificationTests(unittest.TestCase):
    def test_critical_host_risk_without_target_still_builds_notification(self):
        source = {
            "event_id": "host-risk-no-target",
            "observed_at": "2026-09-22T00:00:00Z",
            "observed_monotonic_ns": 1_000_000_000,
            "host_id": "host-local",
            "state": "critical",
            "resource_evaluations": {
                "memory": {"risk": {"state": "critical"}},
            },
            "joint_evaluation": {
                "active_resources": ["memory"],
                "target_state": "NO_TARGET",
                "target": None,
            },
            "decision": {
                "mode": "simulate",
                "action": "escalate",
                "execution": "not_executed",
                "target_state": "NO_TARGET",
                "reason_codes": ["target_attribution_not_confirmed"],
                "quality_flags": [],
            },
            "evidence": {"config_digest": "digest-local"},
        }
        payload = build_notification_event(source)
        self.assertEqual(payload["severity"], "critical")
        self.assertFalse(payload["target"]["present"])
        self.assertEqual(payload["action_context"]["planned_action"], "escalate")

    def test_disabled_channel_is_explicitly_fail_closed(self):
        dispatcher = NotificationDispatcher(DisabledNotificationSink(), max_attempts=2)
        result = dispatcher.dispatch(guardian_event(), now_monotonic_ns=1_000_000_000)
        self.assertEqual(result["status"], "dead_letter")
        self.assertEqual(result["reason"], "sink_unavailable")
        self.assertEqual(dispatcher.dead_letters[0]["reason"], "sink_unavailable")
        self.assertEqual(result["execution"], "not_executed")
        self.assertEqual(result["action_authorization"], "unchanged")

    def test_builds_four_resource_mixed_redacted_contract(self):
        payload = guardian_event()
        self.assertEqual(payload["schema"], "guardian.notification.v1")
        self.assertEqual(payload["severity"], "critical")
        self.assertEqual(payload["active_resources"], ["cpu", "memory"])
        self.assertEqual(set(payload["resources"]), {"cpu", "memory", "disk_capacity", "io"})
        self.assertNotIn("signals", payload)
        self.assertNotIn("raw_secret", str(payload))
        self.assertNotIn(TARGET, str(payload))
        self.assertFalse(payload["security"]["credentials_included"])
        self.assertFalse(payload["security"]["action_authorization_changed"])

    def test_duplicate_states_once_and_recovery_reuses_incident_key(self):
        sink = FakeNotificationSink()
        dispatcher = NotificationDispatcher(sink)
        warning = guardian_event("warning", event_id="warning", observed_ns=1_000_000_000)
        critical = guardian_event("critical", event_id="critical", observed_ns=2_000_000_000)
        recovered = guardian_event("recovered", event_id="recovered", observed_ns=3_000_000_000)
        self.assertEqual(dispatcher.dispatch(warning, now_monotonic_ns=1_000_000_000)["status"], "delivered")
        self.assertEqual(dispatcher.dispatch(copy.deepcopy(warning), now_monotonic_ns=1_000_000_001)["status"], "duplicate_suppressed")
        self.assertEqual(dispatcher.dispatch(critical, now_monotonic_ns=2_000_000_000)["status"], "delivered")
        self.assertEqual(dispatcher.dispatch(recovered, now_monotonic_ns=3_000_000_000)["status"], "delivered")
        self.assertEqual(len(sink.delivered), 3)
        self.assertEqual(len({item["dedup_key"] for item in sink.delivered}), 1)

    def test_out_of_order_and_expired_events_are_rejected(self):
        dispatcher = NotificationDispatcher(FakeNotificationSink(), max_age_seconds=1)
        current = guardian_event("critical", event_id="current", observed_ns=10_000_000_000)
        older = guardian_event("warning", event_id="older", observed_ns=9_000_000_000)
        expired = guardian_event("escalated", event_id="expired", observed_ns=1_000_000_000)
        self.assertEqual(dispatcher.dispatch(current, now_monotonic_ns=10_000_000_000)["status"], "delivered")
        self.assertEqual(dispatcher.dispatch(older, now_monotonic_ns=10_000_000_000)["reason"], "out_of_order")
        self.assertEqual(dispatcher.dispatch(expired, now_monotonic_ns=10_000_000_002)["reason"], "expired")
        self.assertEqual(len(dispatcher.sink.delivered), 1)

    def test_sink_failures_retry_then_dead_letter_and_redrive_without_action(self):
        sink = FakeNotificationSink(failures_before_success=5)
        dispatcher = NotificationDispatcher(sink, max_attempts=3)
        payload = guardian_event()
        failed = dispatcher.dispatch(payload, now_monotonic_ns=1_000_000_000)
        self.assertEqual(failed["status"], "dead_letter")
        self.assertEqual(failed["attempts"], 3)
        self.assertEqual(len(dispatcher.dead_letters), 1)
        sink.failures_before_success = 0
        replay = dispatcher.redrive_dead_letters(now_monotonic_ns=1_000_000_001)
        self.assertEqual(replay[0]["status"], "delivered")
        self.assertEqual(len(sink.delivered), 1)
        self.assertEqual(replay[0]["execution"], "not_executed")
        self.assertEqual(replay[0]["action_authorization"], "unchanged")
        self.assertEqual(dispatcher.audits[-1]["status"], "delivered")

    def test_rate_limit_retains_notification_for_later_redrive(self):
        sink = FakeNotificationSink()
        dispatcher = NotificationDispatcher(
            sink,
            rate_limit_count=1,
            rate_limit_window_seconds=10,
        )
        first = guardian_event("warning", event_id="first", observed_ns=1_000_000_000)
        second = guardian_event("critical", event_id="second", observed_ns=2_000_000_000)
        self.assertEqual(dispatcher.dispatch(first, now_monotonic_ns=1_000_000_000)["status"], "delivered")
        self.assertEqual(dispatcher.dispatch(second, now_monotonic_ns=2_000_000_000)["status"], "rate_limited")
        self.assertEqual(len(dispatcher.dead_letters), 1)
        replay = dispatcher.redrive_dead_letters(now_monotonic_ns=20_000_000_000)
        self.assertEqual(replay[0]["status"], "delivered")
        self.assertEqual(len(sink.delivered), 2)

    def test_non_notifiable_source_state_is_rejected_before_sink(self):
        source = {
            "state": "normal",
            "event_id": "normal",
            "observed_at": "2026-09-20T15:00:00Z",
            "observed_monotonic_ns": 1,
        }
        with self.assertRaisesRegex(NotificationError, "state_not_notifiable"):
            build_notification_event(source)

    def test_dispatcher_numeric_limits_fail_closed(self):
        cases = (
            ({"max_attempts": True}, "max_attempts_out_of_bounds"),
            ({"max_attempts": 2.5}, "max_attempts_out_of_bounds"),
            ({"max_age_seconds": math.nan}, "notification_window_must_be_positive"),
            ({"max_age_seconds": math.inf}, "notification_window_must_be_positive"),
            ({"rate_limit_count": True}, "rate_limit_count_out_of_bounds"),
            ({"rate_limit_count": 1.5}, "rate_limit_count_out_of_bounds"),
            ({"rate_limit_window_seconds": math.inf}, "notification_window_must_be_positive"),
        )
        for kwargs, error in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaisesRegex(ValueError, error):
                    NotificationDispatcher(FakeNotificationSink(), **kwargs)


if __name__ == "__main__":
    unittest.main()
