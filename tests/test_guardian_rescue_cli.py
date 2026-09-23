from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.guardian_rescue_cli import status_document, top_document
from src.guardian_collector_service import ReadOnlyCollector


class GuardianRescueCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.proc = self.root / "proc"
        self.cgroup = self.root / "cgroup"
        self.workload = self.cgroup / "workload.slice"
        self.rescue = self.cgroup / "rescue.slice"
        self.workload.mkdir(parents=True)
        self.rescue.mkdir(parents=True)
        (self.proc / "self").mkdir(parents=True)
        (self.proc / "self" / "cgroup").write_text("0::/user.slice/user-1000.slice\n", encoding="utf-8")
        (self.proc / "meminfo").write_text("MemTotal:       4096000 kB\nMemAvailable:   2048000 kB\n", encoding="utf-8")
        (self.proc / "loadavg").write_text("1.25 0.80 0.40 1/100 10\n", encoding="utf-8")
        (self.proc / "stat").write_text("cpu  10 0 20 100 0 0 0 0 0 0\ncpu0 5 0 10 50 0 0 0 0 0 0\ncpu1 5 0 10 50 0 0 0 0 0 0\n", encoding="utf-8")
        self.object = self.workload / "docker-test.scope"
        self.object.mkdir()
        (self.object / "cgroup.procs").write_text("101\n102\n", encoding="utf-8")
        (self.object / "memory.current").write_text("4096\n", encoding="utf-8")
        (self.object / "memory.max").write_text("1048576\n", encoding="utf-8")
        (self.object / "cpu.stat").write_text("usage_usec 100\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    @staticmethod
    def no_systemctl(*_args, **_kwargs):
        raise FileNotFoundError("systemctl")

    def test_status_is_read_only_and_does_not_require_runtime_or_docker(self):
        value = status_document(
            proc_root=self.proc,
            cgroup_root=self.cgroup,
            mount_point=self.root,
            runner=self.no_systemctl,
        )
        self.assertEqual(value["schema"], "guardian.rescue.status.v1")
        self.assertTrue(value["read_only"])
        self.assertEqual(value["dependencies"]["docker"], "not_required")
        self.assertTrue(value["maintenance"]["workload_slice"]["present"])
        self.assertEqual(value["host"]["memory"]["available_bytes"], 2048000 * 1024)

    def test_status_uses_one_bounded_batch_systemctl_query(self):
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return type(
                "Result",
                (),
                {
                    "returncode": 0,
                    "stdout": "\n".join(
                        (
                            "Id=ssh.service",
                            "ActiveState=active",
                            "SubState=running",
                            "Id=guardian-runtime.service",
                            "ActiveState=active",
                            "SubState=running",
                        )
                    )
                    + "\n",
                    "stderr": "",
                },
            )()

        value = status_document(
            proc_root=self.proc,
            cgroup_root=self.cgroup,
            mount_point=self.root,
            runner=runner,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0][0:3], ["/usr/bin/systemctl", "show", "ssh.service"])
        self.assertEqual(calls[0][1]["timeout"], 1.5)
        self.assertEqual(value["services"]["ssh.service"]["active"], "active")
        self.assertEqual(value["services"]["guardian-runtime.service"]["substate"], "running")

    def test_top_uses_cgroup_without_identity_and_never_marks_actionable(self):
        value = top_document(
            proc_root=self.proc,
            cgroup_root=self.cgroup,
            identity_cache=self.root / "missing.json",
            interval_seconds=0,
        )
        self.assertEqual(value["status"], "ok")
        self.assertEqual(value["objects"][0]["name"], "docker-test.scope")
        self.assertEqual(value["objects"][0]["identity_confidence"], "low")
        self.assertFalse(value["objects"][0]["actionable"])
        self.assertIn("identity_cache_missing", value["objects"][0]["reason_codes"])

    def test_fresh_identity_cache_is_used_but_top_remains_read_only(self):
        cache = self.root / "rescue-identities.json"
        cache.write_text(
            json.dumps(
                {
                    "schema": "guardian.rescue-identities.v1",
                    "version": 1,
                    "entries": [
                        {
                            "stable_id": "a" * 64,
                            "name": "test-service",
                            "cgroup_path": str(self.object),
                            "observed_at": time.time(),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        value = top_document(proc_root=self.proc, cgroup_root=self.cgroup, identity_cache=cache, interval_seconds=0)
        item = value["objects"][0]
        self.assertEqual(item["name"], "test-service")
        self.assertEqual(item["stable_id"], "a" * 64)
        self.assertEqual(item["identity_confidence"], "high")
        self.assertFalse(item["actionable"])

    def test_collector_writes_redacted_identity_cache(self):
        cache = self.root / "cache" / "rescue-identities.json"
        stable_id = "abc" + "a" * 61
        (self.proc / "101").mkdir()
        (self.proc / "101" / "cgroup").write_text("0::/workload.slice/docker-test.scope\n", encoding="utf-8")

        def runner(command, **_kwargs):
            self.assertEqual(command[0], "docker")
            if command[1] == "stats":
                return type("Result", (), {"returncode": 0, "stdout": '{"ID":"abc","Name":"test"}\n', "stderr": ""})()
            return type("Result", (), {"returncode": 0, "stdout": json.dumps({"Id": stable_id, "Name": "/test", "State": {"Pid": 101}, "Config": {"Labels": {}}}) + "\n", "stderr": ""})()

        collector = ReadOnlyCollector(runner=runner, proc_root=self.proc, cgroup_root=self.cgroup, identity_cache_path=cache)
        with patch("src.guardian_collector_service.collect_docker_stats", return_value={"available": True, "containers": [{"ID": "abc"}]}):
            collector.snapshot()
        self.assertTrue(cache.exists())
        value = json.loads(cache.read_text(encoding="utf-8"))
        self.assertEqual(value["schema"], "guardian.rescue-identities.v1")
        self.assertEqual(value["entries"][0]["stable_id"], stable_id)
        self.assertNotIn("Config", value["entries"][0])
