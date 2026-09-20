import os
import re
import tempfile
import unittest
from pathlib import Path

from src.guardian_runtime import notify_ready, send_systemd_notification, systemd_notification_status


class FakeSocket:
    instances = []

    def __init__(self, *_args):
        self.connected = None
        self.messages = []
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def connect(self, address):
        self.connected = address

    def sendall(self, message):
        self.messages.append(message)


class GuardianRuntimeTests(unittest.TestCase):
    def setUp(self):
        FakeSocket.instances = []

    def test_systemd_notification_is_noop_without_socket(self):
        self.assertFalse(send_systemd_notification("READY=1", notify_socket="", socket_factory=FakeSocket))

    def test_notification_supports_abstract_socket_and_payload(self):
        self.assertTrue(send_systemd_notification("READY=1", notify_socket="@guardian", socket_factory=FakeSocket))
        self.assertEqual(FakeSocket.instances[0].connected, "\0guardian")
        self.assertEqual(FakeSocket.instances[0].messages, [b"READY=1"])

    def test_notification_status_distinguishes_unconfigured_sent_and_failed(self):
        self.assertEqual(systemd_notification_status(False, notify_socket=""), "not_configured")
        self.assertEqual(systemd_notification_status(True, notify_socket="@guardian"), "sent")
        self.assertEqual(systemd_notification_status(False, notify_socket="@guardian"), "failed")

    def test_ready_writes_atomic_readiness_file_and_notifies(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "run" / "ready")
            old = os.environ.get("GUARDIAN_READY_FILE")
            os.environ["GUARDIAN_READY_FILE"] = path
            try:
                self.assertTrue(notify_ready("observe:normal"))
            finally:
                if old is None:
                    os.environ.pop("GUARDIAN_READY_FILE", None)
                else:
                    os.environ["GUARDIAN_READY_FILE"] = old
            self.assertEqual(Path(path).read_text(encoding="utf-8"), "observe:normal\n")

    def test_unit_and_slice_define_bounded_read_only_service(self):
        repo_root = Path(__file__).resolve().parents[1]
        deploy_root = repo_root / "deploy" / "guardian"
        # P0-07 VM checks copy the two unit templates beside this test into a
        # disposable directory, so the same test remains portable there.
        if not deploy_root.exists():
            deploy_root = repo_root
        unit = (deploy_root / "guardian-observer.service").read_text(encoding="utf-8")
        slice_file = (deploy_root / "guardian-observer.slice").read_text(encoding="utf-8")
        for required in (
            "Type=notify",
            "WatchdogSec=90s",
            "Restart=on-failure",
            "User=guardian",
            "SupplementaryGroups=docker",
            "Slice=guardian-observer.slice",
            "LogRateLimitBurst=200",
            "GUARDIAN_READY_FILE=/run/guardian/ready",
        ):
            self.assertIn(required, unit)
        for required in ("MemoryMin=16M", "MemoryLow=32M", "MemoryHigh=192M", "TasksMax=128"):
            self.assertIn(required, slice_file)
        watchdog_match = re.search(r"^WatchdogSec=(\d+)s$", unit, flags=re.MULTILINE)
        self.assertIsNotNone(watchdog_match)
        self.assertGreater(int(watchdog_match.group(1)), 60)
        self.assertNotIn("docker stop", unit)
        self.assertNotIn("docker restart", unit)
        self.assertNotIn("docker kill", unit)


if __name__ == "__main__":
    unittest.main()
