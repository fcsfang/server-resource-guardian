import json
import tempfile
import time
import unittest
from pathlib import Path

from scripts.guardian_reserve_space import create
from src.guardian_reserve_recovery import RESERVE_ACTION, ReserveRecoveryController, ReserveRecoveryPolicy


def critical_event(event_id="incident-1"):
    return {
        "event_id": event_id,
        "incident_id": event_id,
        "resource_evaluations": {
            "disk_capacity": {"risk": {"state": "critical_confirmed"}},
        },
    }


class GuardianReserveRecoveryTests(unittest.TestCase):
    def test_disabled_and_simulate_never_delete(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create(root, 1024 * 1024)
            disabled = ReserveRecoveryController(ReserveRecoveryPolicy(root=root))
            self.assertEqual(disabled.handle(critical_event(), mode="enforce")["state"], "disabled")
            auth = root / "authorization.json"
            auth.write_text(
                json.dumps(
                    {
                        "approval_id": "approval-1",
                        "environment": "local-disposable",
                        "target_id": str(root),
                        "action": RESERVE_ACTION,
                        "expires_at": time.time() + 60,
                    }
                ),
                encoding="utf-8",
            )
            policy = ReserveRecoveryPolicy(enabled=True, root=root, authorization_file=auth)
            controller = ReserveRecoveryController(policy)
            result = controller.handle(critical_event(), mode="simulate")
            self.assertEqual(result["state"], "simulated")
            self.assertTrue((root / "emergency-space.bin").exists())

    def test_enforce_releases_only_manifest_owned_file_and_verifies_space(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create(root, 1024 * 1024)
            auth = root / "authorization.json"
            auth.write_text(
                json.dumps(
                    {
                        "approval_id": "approval-1",
                        "environment": "local-disposable",
                        "target_id": str(root),
                        "action": RESERVE_ACTION,
                        "expires_at": time.time() + 60,
                    }
                ),
                encoding="utf-8",
            )
            result = ReserveRecoveryController(
                ReserveRecoveryPolicy(enabled=True, root=root, authorization_file=auth)
            ).handle(critical_event(), mode="enforce")
            self.assertEqual(result["state"], "released")
            self.assertEqual(result["execution"], "executed")
            self.assertGreater(result["after_free_bytes"], result["before_free_bytes"])
            self.assertFalse((root / "emergency-space.bin").exists())
            self.assertFalse((root / "manifest.json").exists())

    def test_manifest_mismatch_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            create(root, 1024 * 1024)
            manifest = root / "manifest.json"
            value = json.loads(manifest.read_text(encoding="utf-8"))
            value["path"] = "/not/guardian/reserve"
            manifest.write_text(json.dumps(value), encoding="utf-8")
            auth = root / "authorization.json"
            auth.write_text(
                json.dumps(
                    {
                        "approval_id": "approval-1",
                        "environment": "local-disposable",
                        "target_id": str(root),
                        "action": RESERVE_ACTION,
                        "expires_at": time.time() + 60,
                    }
                ),
                encoding="utf-8",
            )
            result = ReserveRecoveryController(
                ReserveRecoveryPolicy(enabled=True, root=root, authorization_file=auth)
            ).handle(critical_event(), mode="enforce")
            self.assertEqual(result["state"], "blocked")
            self.assertIn("reserve_manifest_mismatch", result["reason_codes"])
            self.assertTrue((root / "emergency-space.bin").exists())


if __name__ == "__main__":
    unittest.main()
