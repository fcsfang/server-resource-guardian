import json
import socket
import tempfile
import threading
import unittest
from pathlib import Path

from src.guardian_collector_client import UnixSocketCollectorClient
from src.guardian_collector_service import CollectorServer, ReadOnlyCollector
from src.guardian_pressure_gate import PressureGate


ROOT = Path(__file__).resolve().parents[1]


class Result:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class GuardianCollectorTests(unittest.TestCase):
    def test_snapshot_uses_only_fixed_read_commands_and_returns_identity_facts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            proc = root / "proc"
            cgroup = root / "cgroup"
            (proc / "123").mkdir(parents=True)
            (proc / "123" / "cgroup").write_text("0::/system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope\n")
            object_cgroup = cgroup / "system.slice" / "docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"
            object_cgroup.mkdir(parents=True)
            (object_cgroup / "memory.current").write_text("4096\n")
            (object_cgroup / "memory.max").write_text("max\n")
            (object_cgroup / "memory.events").write_text("oom 0\noom_kill 0\n")
            commands = []

            def runner(command, **_kwargs):
                commands.append(command)
                if command[:3] == ["docker", "stats", "--no-stream"]:
                    return Result(json.dumps({"Container": "a" * 32, "Name": "discardable", "CPUPerc": "1%"}) + "\n")
                self.assertEqual(command[:4], ["docker", "inspect", "--format", "{{json .}}"])
                return Result(json.dumps({
                    "Id": "a" * 32,
                    "Name": "/discardable",
                    "Created": "2026-09-21T00:00:00Z",
                    "State": {"Pid": 123, "Status": "running"},
                    "Config": {"Image": "local:test", "Labels": {"role": "test"}},
                }) + "\n")

            result = ReadOnlyCollector(runner=runner, proc_root=proc, cgroup_root=cgroup).snapshot()
            self.assertEqual(result["status"], "ok")
            self.assertTrue(result["read_only"])
            self.assertEqual(result["mutation_commands"], [])
            self.assertEqual(result["data"]["object_registry"]["objects"][0]["id"], "a" * 32)
            self.assertEqual([command[:2] for command in commands], [["docker", "stats"], ["docker", "inspect"]])
            self.assertNotIn("stop", json.dumps(commands))
            self.assertNotIn("restart", json.dumps(commands))
            self.assertNotIn("stop", [command[1] for command in commands])
            self.assertNotIn("restart", [command[1] for command in commands])
            self.assertNotIn("rm", [command[1] for command in commands])

    def test_protocol_rejects_arbitrary_operation(self):
        with self.assertRaises(ValueError):
            CollectorServer._request(
                json.dumps({
                    "schema": "guardian.container_collector.request.v1",
                    "version": 1,
                    "operation": "docker-stop",
                }).encode()
                + b"\n"
            )

    def test_pressure_gate_skips_docker_queries_under_critical_memory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            proc = root / "proc"
            (proc / "pressure").mkdir(parents=True)
            (proc / "meminfo").write_text("MemTotal: 4096000 kB\nMemAvailable: 50000 kB\n", encoding="utf-8")
            (proc / "pressure" / "memory").write_text(
                "some avg10=0.00 avg60=0.00 avg300=0.00 total=0\n"
                "full avg10=30.00 avg60=0.00 avg300=0.00 total=0\n",
                encoding="utf-8",
            )
            calls = []

            def runner(command, **_kwargs):
                calls.append(command)
                raise AssertionError("docker must not be queried while pressure is suspended")

            collector = ReadOnlyCollector(
                runner=runner,
                proc_root=proc,
                pressure_gate=PressureGate(proc_root=proc),
            )
            result = collector.snapshot()
            self.assertEqual(result["status"], "degraded")
            self.assertEqual(calls, [])
            self.assertEqual(result["data"]["docker"]["error"], "pressure_gate_suspended")
            self.assertEqual(result["data"]["collector"]["pressure_gate"]["state"], "suspended")

    def test_client_reads_one_snapshot_from_unix_socket(self):
        with tempfile.TemporaryDirectory() as temp:
            socket_path = Path(temp) / "collector.sock"

            class FakeCollector:
                def snapshot(self):
                    return {
                        "schema": "guardian.container_collector.response.v1",
                        "version": 1,
                        "status": "ok",
                        "data": {"docker": {"available": True, "containers": []}, "object_registry": {"status": "ok", "objects": [], "errors": []}},
                        "errors": [],
                        "read_only": True,
                        "mutation_commands": [],
                    }

            server = CollectorServer(socket_path, collector=FakeCollector())
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            for _ in range(100):
                if socket_path.exists():
                    break
                threading.Event().wait(0.01)
            try:
                value = UnixSocketCollectorClient(socket_path).snapshot()
            finally:
                server.close()
                thread.join(timeout=1)
            self.assertTrue(value["collector"]["read_only"])
            self.assertEqual(value["docker"]["containers"], [])

    def test_disconnected_client_does_not_crash_collector_send_path(self):
        class ClosedChannel:
            def sendall(self, _payload):
                raise BrokenPipeError("client closed")

        CollectorServer._send(
            ClosedChannel(),
            {"schema": "guardian.container_collector.response.v1", "status": "ok"},
        )

    def test_systemd_boundary_keeps_docker_access_in_collector_only(self):
        collector = (ROOT / "deploy" / "guardian" / "guardian-collector.service").read_text(encoding="utf-8")
        runtime = (ROOT / "deploy" / "guardian" / "guardian-runtime.service").read_text(encoding="utf-8")
        self.assertIn("User=guardian-collector", collector)
        self.assertIn("SupplementaryGroups=docker", collector)
        self.assertIn("ExecStart=/usr/bin/python3 -m src.guardian_collector_service", collector)
        self.assertIn("ReadWritePaths=/run/guardian-collector", collector)
        self.assertNotIn("SupplementaryGroups=docker", runtime)
        self.assertIn("--collector-socket /run/guardian-collector/collector.sock", runtime)
        for mutation in ("docker stop", "docker restart", "docker rm", "docker kill"):
            self.assertNotIn(mutation, collector)


if __name__ == "__main__":
    unittest.main()
