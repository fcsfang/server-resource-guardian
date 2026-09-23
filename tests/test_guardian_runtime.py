import argparse
import os
import re
import tempfile
import unittest
from pathlib import Path

from src.guardian_preflight import run as run_preflight
from src.guardian_runtime import notify_ready, notify_status, send_systemd_notification, systemd_notification_status


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

    def test_status_writes_not_ready_marker_without_ready_notification(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "run" / "ready")
            old = os.environ.get("GUARDIAN_READY_FILE")
            os.environ["GUARDIAN_READY_FILE"] = path
            try:
                self.assertTrue(notify_status("runtime:reconciliation_required"))
            finally:
                if old is None:
                    os.environ.pop("GUARDIAN_READY_FILE", None)
                else:
                    os.environ["GUARDIAN_READY_FILE"] = old
            self.assertEqual(Path(path).read_text(encoding="utf-8"), "runtime:reconciliation_required\n")

    def test_runtime_unit_routes_observer_into_coordinator_entrypoint(self):
        repo_root = Path(__file__).resolve().parents[1]
        unit = (repo_root / "deploy" / "guardian" / "guardian-runtime.service").read_text(encoding="utf-8")
        for required in (
            "Type=notify",
            "WatchdogSec=90s",
            "ExecStart=/usr/bin/python3 -m src.guardian_orchestrator",
            "--state-db /var/lib/guardian/shared/state.db",
            "--outbox-db /var/lib/guardian/runtime/outbox.db",
            "--audit-file /var/lib/guardian/runtime/audit/events.jsonl",
            "--reserve-broker-socket /run/guardian-reserve-broker/reserve.sock",
            "Environment=GUARDIAN_READY_FILE=/run/guardian-runtime/ready",
            "Restart=on-failure",
            "NoNewPrivileges=true",
            "SupplementaryGroups=guardian-shared guardian-broker",
        ):
            self.assertIn(required, unit)
        self.assertNotIn("SupplementaryGroups=docker", unit)
        self.assertNotIn("docker stop", unit)
        self.assertNotIn("docker restart", unit)
        self.assertNotIn("docker kill", unit)

    def test_runtime_and_broker_use_separate_accounts_and_directories(self):
        repo_root = Path(__file__).resolve().parents[1]
        deploy_root = repo_root / "deploy" / "guardian"
        runtime = (deploy_root / "guardian-runtime.service").read_text(encoding="utf-8")
        broker = (deploy_root / "guardian-broker.service").read_text(encoding="utf-8")
        tmpfiles = (deploy_root / "guardian.tmpfiles").read_text(encoding="utf-8")
        self.assertIn("User=guardian\n", runtime)
        self.assertIn("User=guardian-broker\n", broker)
        self.assertNotIn("SupplementaryGroups=docker", runtime)
        self.assertIn("SupplementaryGroups=docker guardian-shared", broker)
        self.assertIn("/run/guardian-runtime", runtime)
        self.assertIn("/run/guardian-broker/broker.sock", runtime)
        self.assertIn("/run/guardian-reserve-broker/reserve.sock", runtime)
        self.assertIn("/run/guardian-broker/broker.sock", broker)
        self.assertIn("/var/lib/guardian/shared", runtime)
        self.assertIn("/var/lib/guardian/shared", broker)
        self.assertIn("d /var/lib/guardian 0750 root guardian-shared", tmpfiles)
        self.assertIn("d /var/lib/guardian/shared 2770 root guardian-shared", tmpfiles)
        self.assertIn("d /run/guardian-runtime 0750 guardian guardian", tmpfiles)
        self.assertIn("d /run/guardian-broker 0750 guardian-broker guardian-broker", tmpfiles)
        self.assertIn("d /run/guardian-reserve-broker 0750 root guardian", tmpfiles)

    def test_preflight_requires_role_specific_writable_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shared = root / "shared"
            runtime = root / "runtime"
            audit = runtime / "audit"
            broker_run = root / "broker-run"
            for path in (shared, runtime, audit, broker_run):
                path.mkdir(parents=True, exist_ok=True)
            state_db = shared / "state.db"
            state_db.touch(mode=0o660)
            common = {
                "config": None,
                "state_db": state_db,
                "mode": None,
            }
            runtime_result = run_preflight(argparse.Namespace(
                role="runtime",
                outbox_db=runtime / "outbox.db",
                audit_file=audit / "events.jsonl",
                socket=None,
                **common,
            ))
            broker_result = run_preflight(argparse.Namespace(
                role="broker",
                outbox_db=None,
                audit_file=None,
                socket=broker_run / "broker.sock",
                **common,
            ))
            self.assertEqual(runtime_result["status"], "PASS")
            self.assertEqual(broker_result["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
