import json
import tempfile
import unittest
from pathlib import Path

from src.guardian_config import ConfigError, SCHEMA, load_config, safe_defaults, validate_config


def config_data():
    return {
        "schema": SCHEMA,
        "version": 1,
        "agent": {
            "mode": "observe",
            "interval_seconds": 5,
            "snapshot_directory": None,
            "snapshot_max_total_bytes": 256 * 1024 * 1024,
        },
        "risk": {
            "memory": {
                "warning_available_percent": 15,
                "critical_available_percent": 10,
                "warning_for_seconds": 180,
                "critical_for_seconds": 30,
            },
            "composite": {
                "required_samples": 2,
                "max_sample_age_seconds": 15,
                "trend_warning_bytes_per_second": 1048576,
                "trend_critical_bytes_per_second": 16777216,
                "psi_memory_some_warning_avg10": 1,
                "psi_memory_full_critical_avg10": 0.5,
                "swap_used_warning_percent": 25,
                "swap_used_critical_percent": 50,
            },
        },
        "actions": {
            "enabled": False,
            "require_approval": True,
            "authorization_file": None,
            "graceful_timeout_seconds": 30,
            "cooldown_seconds": 600,
            "max_actions_per_host_per_hour": 0,
            "allow": [],
        },
        "protection": {"systemd_units": [], "executable_paths": [], "container_labels": []},
        "recovery": {"max_wait_seconds": 30, "poll_interval_seconds": 1, "require_business_health": True},
        "audit": {"local_buffer_enabled": True, "remote_export_enabled": False, "max_total_bytes": 100 * 1024 * 1024},
    }


class GuardianConfigTests(unittest.TestCase):
    def test_safe_defaults_are_observe_only(self):
        config = safe_defaults()
        self.assertEqual(config.mode, "observe")
        self.assertEqual(config.allowed_actions, ())
        self.assertEqual(len(config.config_digest), 64)

    def test_example_json_loads_and_has_stable_digest(self):
        path = Path(__file__).parents[1] / "config" / "guardian.example.json"
        first = load_config(path)
        second = load_config(path)
        self.assertEqual(first.config_digest, second.config_digest)
        self.assertEqual(first.schema, SCHEMA)
        self.assertEqual(first.disk_mount_points, ("/",))
        self.assertEqual(first.disk_capacity_policy["critical_free_percent"], 5)
        self.assertEqual(first.io_policy["critical_device_utilization_percent"], 90)
        self.assertFalse(first.emergency_shedding_policy["enabled"])

    def test_emergency_shedding_requires_explicit_local_disposable_sets(self):
        value = config_data()
        value["risk"]["emergency_shedding"] = {
            "schema": "guardian.emergency_shedding.v1",
            "enabled": True,
            "window_seconds": 15,
            "required_samples": 2,
            "min_host_contribution_percent": 20,
            "resource_priority": ["memory", "cpu", "io", "disk_capacity"],
            "action": "graceful_stop",
            "protected_set": [
                {"stable_id": "a" * 64, "owner": "platform", "reason": "control-plane"},
            ],
            "actionable_set": [
                {
                    "stable_id": "b" * 64,
                    "owner": "experiment-owner",
                    "environment": "local-disposable",
                    "allowed_resources": ["memory", "cpu", "io"],
                    "action": "graceful_stop",
                    "grace_timeout_seconds": 30,
                    "expires_at": "2026-12-31T00:00:00Z",
                    "human_contact": "on-call",
                },
            ],
        }
        config = validate_config(value)
        self.assertTrue(config.emergency_shedding_policy["enabled"])
        self.assertEqual(config.emergency_shedding_policy["action"], "graceful_stop")

        invalid = config_data()
        invalid["risk"]["emergency_shedding"] = value["risk"]["emergency_shedding"].copy()
        invalid["risk"]["emergency_shedding"]["actionable_set"] = []
        with self.assertRaisesRegex(ConfigError, "actionable_set:non_empty_when_enabled"):
            validate_config(invalid)

        duplicate = config_data()
        duplicate["risk"]["emergency_shedding"] = value["risk"]["emergency_shedding"].copy()
        duplicate["risk"]["emergency_shedding"]["protected_set"] = [
            value["risk"]["emergency_shedding"]["protected_set"][0],
            value["risk"]["emergency_shedding"]["protected_set"][0].copy(),
        ]
        with self.assertRaisesRegex(ConfigError, "protected_set:duplicate_stable_id"):
            validate_config(duplicate)

        overlap = config_data()
        overlap["risk"]["emergency_shedding"] = value["risk"]["emergency_shedding"].copy()
        overlap["risk"]["emergency_shedding"]["actionable_set"] = [
            {
                **value["risk"]["emergency_shedding"]["actionable_set"][0],
                "stable_id": value["risk"]["emergency_shedding"]["protected_set"][0]["stable_id"],
            }
        ]
        with self.assertRaisesRegex(ConfigError, "protected_actionable_overlap"):
            validate_config(overlap)

    def test_disk_policy_rejects_relative_mount_point(self):
        value = config_data()
        value["risk"]["disk_capacity"] = {
            "mount_points": ["relative"],
            "warning_free_percent": 15,
            "critical_free_percent": 5,
            "warning_inode_free_percent": 10,
            "critical_inode_free_percent": 5,
            "warning_time_to_full_seconds": 86400,
            "critical_time_to_full_seconds": 3600,
            "warning_for_seconds": 180,
            "critical_for_seconds": 30,
            "required_samples": 2,
            "max_sample_age_seconds": 15,
            "min_object_contribution_percent": 20,
            "min_object_lead_margin": 0.15,
        }
        with self.assertRaisesRegex(ConfigError, "absolute_non_empty_required"):
            validate_config(value)

    def test_unknown_top_level_field_is_rejected(self):
        value = config_data()
        value["dangerous_default"] = True
        with self.assertRaisesRegex(ConfigError, "unknown_fields:dangerous_default"):
            validate_config(value)

    def test_threshold_order_and_dwell_windows_are_rejected(self):
        value = config_data()
        value["risk"]["memory"]["critical_available_percent"] = 20
        with self.assertRaisesRegex(ConfigError, "critical_must_be_positive"):
            validate_config(value)
        value = config_data()
        value["risk"]["memory"]["warning_for_seconds"] = 0
        with self.assertRaisesRegex(ConfigError, "dwell_windows_must_be_positive"):
            validate_config(value)

    def test_enforce_requires_explicit_enabled_approval_and_allowlist(self):
        value = config_data()
        value["agent"]["mode"] = "enforce"
        with self.assertRaisesRegex(ConfigError, "enforce_requires_actions_enabled"):
            validate_config(value)
        value["actions"]["enabled"] = True
        with self.assertRaisesRegex(ConfigError, "enforce_requires_non_empty_allowlist"):
            validate_config(value)
        value["actions"]["allow"] = ["terminate"]
        with self.assertRaisesRegex(ConfigError, "automatic_terminate_forbidden"):
            validate_config(value)

    def test_enforce_requires_absolute_authorization_file(self):
        value = config_data()
        value["agent"]["mode"] = "enforce"
        value["actions"].update({
            "enabled": True,
            "allow": ["graceful_stop"],
        })
        with self.assertRaisesRegex(ConfigError, "enforce_requires_authorization_file"):
            validate_config(value)
        value["actions"]["authorization_file"] = "relative/authorization.json"
        with self.assertRaisesRegex(ConfigError, "absolute_path_required"):
            validate_config(value)

    def test_remote_export_is_fail_closed_until_implemented(self):
        value = config_data()
        value["audit"]["remote_export_enabled"] = True
        with self.assertRaisesRegex(ConfigError, "remote_export_enabled:not_implemented"):
            validate_config(value)

    def test_config_file_invalid_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.json"
            path.write_text("{not-json", encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "config_invalid_json"):
                load_config(path)

    def test_digest_changes_when_policy_changes(self):
        first = validate_config(config_data())
        changed = config_data()
        changed["risk"]["memory"]["warning_for_seconds"] = 181
        second = validate_config(changed)
        self.assertNotEqual(first.config_digest, second.config_digest)


if __name__ == "__main__":
    unittest.main()
