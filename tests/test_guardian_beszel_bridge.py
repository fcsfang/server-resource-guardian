import datetime as dt
import unittest

from src.beszel_adapter import BeszelEventWindow, normalize_beszel_event
from src.guardian_beszel_bridge import bridge_beszel_event, bridge_beszel_events


UTC = dt.timezone.utc
RECEIVED = dt.datetime(2026, 9, 20, 0, 0, 2, tzinfo=UTC)


def local_observation(*, available=5.0, containers=None):
    return {
        "observed_at": "2026-09-20T00:00:00Z",
        "memory": {"available_ratio_percent": available, "available_bytes": 500},
        "psi": {},
        "cgroup": {"memory_events": {}},
        "docker": {"containers": containers if containers is not None else [{"ID": "local-1", "Name": "discardable"}]},
    }


def beszel_event(*, event_id="alert-1", observed="2026-09-20T00:00:00Z", confidence="high", severity="critical"):
    payload = {
        "event_id": event_id,
        "observed_at": observed,
        "source": {"kind": "hub_alert", "hub_id": "local", "system_id": "system-1", "record_id": event_id},
        "system": {"name": "guardian-ubuntu"},
        "object": {"kind": "host", "stable_id": "system-1", "name": "guardian-ubuntu", "identity_confidence": confidence},
        "signal": {"resource": "memory", "metric": "Memory", "value": 92, "unit": "%", "severity": severity, "reason_codes": ["memory_threshold"]},
    }
    return normalize_beszel_event(payload, received_at=RECEIVED, now=RECEIVED)


class GuardianBeszelBridgeTests(unittest.TestCase):
    def test_accepted_alert_enters_simulate_but_never_authorizes_execution(self):
        result = bridge_beszel_event(
            beszel_event(),
            local_observation(),
            now=RECEIVED,
            mode="simulate",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["bridge"]["status"], "accepted")
        self.assertFalse(result["bridge"]["action_authorized"])
        self.assertEqual(result["guardian_event"]["decision"]["action"], "graceful_stop")
        self.assertEqual(result["guardian_event"]["decision"]["execution"], "not_executed")
        self.assertIn("beszel_event_accepted", result["guardian_event"]["decision"]["reason_codes"])
        self.assertEqual(result["guardian_event"]["evidence"]["beszel_event_id"], "alert-1")

    def test_external_alert_does_not_override_normal_local_observation(self):
        result = bridge_beszel_event(
            beszel_event(),
            local_observation(available=90.0),
            now=RECEIVED,
            mode="simulate",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        decision = result["guardian_event"]["decision"]
        self.assertEqual(result["bridge"]["status"], "accepted")
        self.assertEqual(decision["action"], "none")
        self.assertIn("risk_not_actionable", decision["reason_codes"])
        self.assertEqual(decision["execution"], "not_executed")

    def test_duplicate_stale_and_out_of_order_events_are_read_only(self):
        window = BeszelEventWindow(dedupe_seconds=60)
        first = beszel_event()
        accepted = bridge_beszel_event(
            first,
            local_observation(),
            window=window,
            now=RECEIVED,
            mode="simulate",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        duplicate = bridge_beszel_event(
            first,
            local_observation(),
            window=window,
            now=RECEIVED,
            mode="simulate",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        stale = beszel_event(event_id="alert-stale")
        stale["integrity"]["stale"] = True
        stale_result = bridge_beszel_event(stale, local_observation(), window=window, now=RECEIVED)
        older = beszel_event(event_id="alert-older", observed="2026-09-19T23:59:59Z")
        older_result = bridge_beszel_event(older, local_observation(), window=window, now=RECEIVED)

        self.assertEqual(accepted["bridge"]["status"], "accepted")
        self.assertEqual(duplicate["bridge"]["classification"], "duplicate_event")
        self.assertEqual(stale_result["bridge"]["classification"], "stale_event")
        self.assertEqual(older_result["bridge"]["classification"], "out_of_order_event")
        for result in (duplicate, stale_result, older_result):
            self.assertEqual(result["guardian_event"]["decision"]["action"], "none")
            self.assertEqual(result["guardian_event"]["decision"]["execution"], "not_applicable")

    def test_low_confidence_event_cannot_create_simulate_action(self):
        result = bridge_beszel_event(
            beszel_event(event_id="alert-low", confidence="low"),
            local_observation(),
            now=RECEIVED,
            mode="simulate",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["bridge"]["status"], "observation_only")
        self.assertFalse(result["bridge"]["action_authorized"])
        self.assertEqual(result["guardian_event"]["decision"]["action"], "none")
        self.assertIn("beszel_observation_only", result["guardian_event"]["decision"]["reason_codes"])

    def test_recovered_event_is_observation_only(self):
        result = bridge_beszel_event(
            beszel_event(event_id="alert-recovered", severity="recovered"),
            local_observation(),
            now=RECEIVED,
            mode="simulate",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["bridge"]["status"], "observation_only")
        self.assertEqual(result["guardian_event"]["decision"]["action"], "none")

    def test_multiple_local_candidates_remain_ambiguous(self):
        result = bridge_beszel_event(
            beszel_event(),
            local_observation(containers=[{"ID": "a"}, {"ID": "b"}]),
            now=RECEIVED,
            mode="simulate",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        decision = result["guardian_event"]["decision"]
        self.assertEqual(decision["action"], "escalate")
        self.assertIn("ambiguous_object_identity", decision["reason_codes"])
        self.assertEqual(decision["execution"], "not_executed")

    def test_page_bridge_reuses_one_window(self):
        events = [beszel_event(event_id="page-1"), beszel_event(event_id="page-2", observed="2026-09-20T00:00:01Z")]
        results = bridge_beszel_events(events, local_observation(available=90.0), now=RECEIVED)
        self.assertEqual(len(results), 2)
        self.assertEqual([item["bridge"]["status"] for item in results], ["accepted", "accepted"])


if __name__ == "__main__":
    unittest.main()
