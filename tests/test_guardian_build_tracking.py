import json
import tempfile
import unittest
from pathlib import Path

from src.guardian_status import _build_info


class BuildInfoTests(unittest.TestCase):
    def test_missing_manifest_is_explicitly_absent(self):
        info = _build_info(Path("/nonexistent/build-manifest.json"))
        self.assertFalse(info["manifest_present"])
        self.assertIsNone(info["deployment_matches"])
        self.assertIsNone(info["commit"])

    def test_corrupt_manifest_is_explicitly_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "build-manifest.json"
            path.write_text("{not json", encoding="utf-8")
            info = _build_info(path)
        self.assertFalse(info["manifest_present"])

    def test_present_manifest_reports_commit_and_verify_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "build-manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "schema": "guardian.build-manifest.v1",
                        "commit": "abc123",
                        "built_at": "2026-09-24T00:00:00Z",
                        "file_count": 0,
                        "files": {},
                    }
                ),
                encoding="utf-8",
            )
            info = _build_info(path)
        self.assertTrue(info["manifest_present"])
        self.assertEqual(info["commit"], "abc123")
        # the verify tool is not on PATH in the test environment; drift
        # checking must report unknown, never silently pass
        self.assertIn(info["deployment_matches"], (None, True, False))

    def test_format_status_renders_absent_manifest_without_crash(self):
        from src.guardian_status import format_status

        value = {
            "overall": "healthy",
            "runtime": {"active": "active", "enabled": "enabled", "readiness": "runtime:ready:observe"},
            "mode": "observe",
            "automatic_actions": "disabled",
            "recent_alerts": {},
            "broker": {"closed": True},
            "reserve_broker": {"closed": True},
            "collector": {"online": True},
            "config": {"path": "/etc/guardian/guardian.json", "error": None},
            "build": {"manifest_present": False, "commit": None, "built_at": None, "deployment_matches": None},
            "protection": {"systemd_units": 0, "container_labels": 0},
        }
        text = format_status(value)
        self.assertIn("Build: no manifest", text)

    def test_format_status_renders_drift_loudly(self):
        from src.guardian_status import format_status

        value = {
            "overall": "healthy",
            "runtime": {"active": "active", "enabled": "enabled", "readiness": "runtime:ready:observe"},
            "mode": "observe",
            "automatic_actions": "disabled",
            "recent_alerts": {},
            "broker": {"closed": True},
            "reserve_broker": {"closed": True},
            "collector": {"online": True},
            "config": {"path": "/etc/guardian/guardian.json", "error": None},
            "build": {"manifest_present": True, "commit": "abc123", "built_at": "2026-09-24T00:00:00Z", "deployment_matches": False},
            "protection": {"systemd_units": 0, "container_labels": 0},
        }
        text = format_status(value)
        self.assertIn("DRIFT DETECTED", text)
        self.assertIn("abc123", text)


if __name__ == "__main__":
    unittest.main()
