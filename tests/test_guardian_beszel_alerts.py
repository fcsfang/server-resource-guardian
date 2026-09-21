import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from src.guardian_beszel_alerts import (
    BeszelAlertConfigError,
    BeszelAlertRule,
    LocalAlertRecordWindow,
    build_native_alert_plan,
    load_alert_plan_config,
    normalize_native_alert_record,
    parse_rule,
)


UTC = dt.timezone.utc


class GuardianBeszelAlertsTests(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(Path("config/beszel-alerts.example.json").read_text())

    def test_plan_maps_exactly_cpu_memory_disk_and_records_capability_gaps(self):
        plan = load_alert_plan_config(self.config)
        self.assertEqual(plan["schema"], "guardian.beszel.alerts.v1")
        self.assertEqual([item["resource"] for item in plan["rules"]], ["cpu", "memory", "disk"])
        self.assertEqual(plan["requests"], [])
        self.assertTrue(plan["requires_system_selection"])
        gap_ids = {item["id"] for item in plan["capability_gaps"]}
        self.assertEqual(gap_ids, {"no_dual_severity", "no_disk_io_alert"})
        self.assertFalse(plan["safety"]["broker_called"])

    def test_plan_with_system_id_emits_native_request_bodies_only(self):
        rules = [parse_rule(item) for item in self.config["rules"]]
        plan = build_native_alert_plan(["system-1"], rules)
        self.assertEqual([request["body"]["name"] for request in plan["requests"]], ["CPU", "Memory", "Disk"])
        self.assertEqual(plan["requests"][0]["body"]["systems"], ["system-1"])
        self.assertEqual(plan["requests"][0]["path"], "/api/beszel/user-alerts")
        self.assertTrue(plan["safety"]["configuration_only"])

    def test_rejects_disk_io_and_double_rules(self):
        with self.assertRaisesRegex(BeszelAlertConfigError, "unsupported_native_resource"):
            BeszelAlertRule("io", 80, 1)
        rules = [parse_rule(item) for item in self.config["rules"]]
        with self.assertRaisesRegex(BeszelAlertConfigError, "one_rule_per_resource"):
            build_native_alert_plan(["system-1"], [rules[0], rules[0], rules[0]])

    def test_native_record_preserves_single_threshold_without_inventing_severity(self):
        record = normalize_native_alert_record(
            {
                "id": "alert-1",
                "name": "Memory",
                "value": 85,
                "min": 1,
                "created": "2026-09-21T08:00:00Z",
                "resolved": "2026-09-21T08:01:30Z",
                "system": "system-1",
                "expand": {"system": {"name": "guardian-ubuntu"}},
            },
            received_at=dt.datetime(2026, 9, 21, 8, 1, 32, tzinfo=UTC),
        )
        self.assertEqual(record["resource"], "memory")
        self.assertEqual(record["state"], "recovered")
        self.assertIsNone(record["severity"])
        self.assertEqual(record["severity_model"], "single_threshold")
        self.assertEqual(record["duration_seconds"], 90.0)
        self.assertFalse(record["action_authorized"])
        self.assertNotIn("token", json.dumps(record))

    def test_local_window_suppresses_duplicate_but_keeps_recovery(self):
        window = LocalAlertRecordWindow(max_records=2)
        triggered = {"record_id": "a", "state": "triggered"}
        recovered = {"record_id": "a", "state": "recovered"}
        self.assertTrue(window.accept(triggered))
        self.assertFalse(window.accept(triggered))
        self.assertTrue(window.accept(recovered))

    def test_fixture_config_has_no_real_notification_channel(self):
        self.assertEqual(self.config["notification"]["mode"], "local_ui_history")
        self.assertFalse(self.config["notification"]["real_channels_enabled"])

    def test_jsonl_writer_appends_redacted_records(self):
        from src.guardian_beszel_alerts import write_local_records

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "records.jsonl"
            count = write_local_records(path, [{"resource": "cpu", "state": "triggered"}])
            self.assertEqual(count, 1)
            self.assertEqual(len(path.read_text().splitlines()), 1)


if __name__ == "__main__":
    unittest.main()
