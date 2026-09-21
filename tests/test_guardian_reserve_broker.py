import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from src.guardian_actions import Authorization
from src.guardian_config import safe_defaults
from src.guardian_reserve_broker import (
    RESERVE_BROKER_RESPONSE_SCHEMA,
    ReserveRecoveryBrokerClient,
    ReserveRecoveryBrokerServer,
)
from src.guardian_reserve_recovery import RESERVE_ACTION
from src.guardian_reserve_state import ReserveBrokerStateStore


class FakeRunner:
    def __init__(self):
        self.commands = []

    def __call__(self, command, **_kwargs):
        self.commands.append(command)
        return type(
            "Completed",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {
                        "status": "released",
                        "path": "/var/lib/guardian/reserve/emergency-space.bin",
                        "before_free_bytes": 100,
                        "after_free_bytes": 200,
                    }
                ),
                "stderr": "",
            },
        )()


def local_config(path: Path, authorization_file: Path) -> None:
    value = safe_defaults().as_dict()
    value["risk"]["disk_reserve_recovery"] = {
        "enabled": True,
        "root": "/var/lib/guardian/reserve",
        "mount_point": "/",
        "max_releases_per_incident": 1,
        "authorization_file": str(authorization_file),
    }
    path.write_text(json.dumps(value), encoding="utf-8")


class GuardianReserveBrokerTests(unittest.TestCase):
    def start_server(self, root: Path):
        socket_path = root / "reserve.sock"
        state = ReserveBrokerStateStore(root / "state" / "state.db")
        authorization_file = root / "authorization.json"
        authorization = {
            "approval_id": "reserve-approval",
            "environment": "local-disposable",
            "target_id": "/var/lib/guardian/reserve",
            "action": RESERVE_ACTION,
            "expires_at": time.time() + 60,
        }
        authorization_file.write_text(json.dumps(authorization), encoding="utf-8")
        config_path = root / "guardian.json"
        local_config(config_path, authorization_file)
        runner = FakeRunner()
        server = ReserveRecoveryBrokerServer(
            socket_path,
            state_store=state,
            config_path=config_path,
            enabled=True,
            allowed_uid=os.getuid(),
            runner=runner,
            peer_uid_reader=lambda _channel: os.getuid(),
        )
        thread = threading.Thread(target=server.serve_forever)
        thread.start()
        for _ in range(100):
            if socket_path.exists():
                break
            time.sleep(0.01)
        auth = Authorization(**authorization)
        client = ReserveRecoveryBrokerClient(socket_path, timeout_seconds=2)
        return server, thread, client, auth, config_path, runner, state

    def test_runtime_boundary_releases_once_and_persists_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            server, thread, client, auth, config_path, runner, state = self.start_server(root)
            try:
                from src.guardian_config import load_config

                digest = load_config(config_path).config_digest
                first = client.release(
                    incident_id="incident-reserve-1",
                    authorization=auth,
                    config_digest=digest,
                    mount_point="/",
                )
                second = client.release(
                    incident_id="incident-reserve-2",
                    authorization=auth,
                    config_digest=digest,
                    mount_point="/",
                )
            finally:
                server.stop()
                thread.join(timeout=2)
            self.assertEqual(first["state"], "released")
            self.assertEqual(first["execution"], "executed")
            self.assertEqual(runner.commands, [["/usr/local/sbin/guardian-release-emergency-space"]])
            self.assertEqual(second["state"], "blocked")
            self.assertIn("reserve_approval_already_consumed", second["reason_codes"])
            connection = state._connect()
            try:
                records = connection.execute("SELECT kind FROM reserve_audits ORDER BY sequence").fetchall()
            finally:
                connection.close()
            self.assertEqual(
                [row[0] for row in records],
                [
                    "reserve_capability_registered",
                    "reserve_capability_consumed",
                    "reserve_execution_started",
                    "reserve_execution_finished",
                ],
            )

    def test_disabled_boundary_rejects_without_helper(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            socket_path = root / "reserve.sock"
            server = ReserveRecoveryBrokerServer(
                socket_path,
                state_store=ReserveBrokerStateStore(root / "state" / "state.db"),
                config_path=root / "missing.json",
                enabled=False,
                allowed_uid=os.getuid(),
                peer_uid_reader=lambda _channel: os.getuid(),
            )
            thread = threading.Thread(target=server.serve_forever)
            thread.start()
            try:
                for _ in range(100):
                    if socket_path.exists():
                        break
                    time.sleep(0.01)
                auth = Authorization(
                    approval_id="approval",
                    environment="local-disposable",
                    target_id="/var/lib/guardian/reserve",
                    action=RESERVE_ACTION,
                    expires_at=time.time() + 60,
                )
                response = ReserveRecoveryBrokerClient(socket_path).release(
                    incident_id="disabled-incident",
                    authorization=auth,
                    config_digest="unused",
                    mount_point="/",
                )
            finally:
                server.stop()
                thread.join(timeout=2)
            self.assertEqual(response["state"], "blocked")
            self.assertIn("reserve_broker_disabled", response["reason_codes"])

    def test_unknown_broker_result_remains_unknown_and_is_not_replayed(self):
        with tempfile.TemporaryDirectory() as temp:
            socket_path = Path(temp) / "unknown.sock"
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            server.bind(str(socket_path))
            server.listen(1)

            def reply_once():
                channel, _ = server.accept()
                try:
                    channel.recv(16 * 1024)
                    channel.sendall(
                        (
                            json.dumps(
                                {
                                    "schema": RESERVE_BROKER_RESPONSE_SCHEMA,
                                    "version": 1,
                                    "status": "UNKNOWN",
                                    "reason_codes": ["reserve_helper_outcome_unknown"],
                                    "result": None,
                                }
                            )
                            + "\n"
                        ).encode()
                    )
                finally:
                    channel.close()

            thread = threading.Thread(target=reply_once)
            thread.start()
            try:
                result = ReserveRecoveryBrokerClient(socket_path).release(
                    incident_id="unknown-incident",
                    authorization=Authorization(
                        approval_id="approval",
                        environment="local-disposable",
                        target_id="/var/lib/guardian/reserve",
                        action=RESERVE_ACTION,
                        expires_at=time.time() + 60,
                    ),
                    config_digest="config-digest",
                    mount_point="/",
                )
            finally:
                thread.join(timeout=2)
                server.close()
            self.assertEqual(result["state"], "unknown")
            self.assertEqual(result["execution"], "unknown")
            self.assertIn("reserve_helper_outcome_unknown", result["reason_codes"])


if __name__ == "__main__":
    unittest.main()
