import json
import tempfile
import unittest
from pathlib import Path

from src.guardian_observer import (
    build_event,
    collect_observation,
    parse_meminfo,
    parse_psi,
)


class GuardianObserverTests(unittest.TestCase):
    def test_parse_meminfo_converts_kib_to_bytes(self):
        parsed = parse_meminfo("MemTotal:       1024 kB\nMemAvailable:    512 kB\n")
        self.assertEqual(parsed["MemTotal"], 1024 * 1024)
        self.assertEqual(parsed["MemAvailable"], 512 * 1024)

    def test_parse_psi(self):
        parsed = parse_psi("some avg10=1.25 avg60=0.50 avg300=0.10 total=42\n")
        self.assertEqual(parsed["some"]["avg10"], 1.25)
        self.assertEqual(parsed["some"]["total"], 42.0)

    def test_collect_observation_is_read_only_and_fixture_driven(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pressure").mkdir()
            (root / "meminfo").write_text(
                "MemTotal:       100000 kB\n"
                "MemAvailable:    12000 kB\n"
                "SwapTotal:       20000 kB\n"
                "SwapFree:         5000 kB\n"
            )
            (root / "pressure" / "memory").write_text(
                "some avg10=2.00 avg60=1.00 avg300=0.50 total=100\n"
            )
            cgroup = root / "cgroup"
            cgroup.mkdir()
            (cgroup / "memory.current").write_text("1234\n")
            (cgroup / "memory.max").write_text("max\n")
            (cgroup / "memory.events").write_text("oom 1\noom_kill 1\n")
            (cgroup / "pids.current").write_text("3\n")
            (cgroup / "pids.max").write_text("max\n")

            def fake_runner(*_args, **_kwargs):
                return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

            observation = collect_observation(root, cgroup, fake_runner)
            self.assertEqual(observation["cgroup"]["memory_events"]["oom_kill"], 1)
            self.assertTrue(observation["psi"]["memory"]["some"])
            event = build_event(observation)
            self.assertEqual(event["state"], "critical")
            self.assertEqual(event["decision"]["action"], "none")

    def test_snapshot_can_be_written_without_actions(self):
        observation = {
            "observed_at": "2026-09-19T00:00:00Z",
            "memory": {"available_ratio_percent": 90.0},
            "psi": {},
            "cgroup": {"memory_events": {}},
            "docker": {"containers": []},
        }
        event = build_event(observation)
        with tempfile.TemporaryDirectory() as temp:
            from src.guardian_observer import write_snapshot

            path = Path(write_snapshot(event, Path(temp)))
            self.assertTrue(path.exists())
            saved = json.loads(path.read_text())
            self.assertEqual(saved["decision"]["mode"], "observe")
            self.assertEqual(saved["decision"]["action"], "none")


if __name__ == "__main__":
    unittest.main()
