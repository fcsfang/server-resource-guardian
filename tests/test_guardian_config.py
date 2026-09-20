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
