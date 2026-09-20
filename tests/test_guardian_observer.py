import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from src.guardian_observer import (
    RiskEvaluator,
    append_audit,
    build_event,
    collect_docker_stats,
    collect_observation,
    parse_meminfo,
    parse_psi,
    resolve_process_cgroup_root,
    validate_interval_seconds,
    write_snapshot,
)


class GuardianObserverTests(unittest.TestCase):
    def test_interval_validation_accepts_schema_boundaries(self):
        self.assertEqual(validate_interval_seconds(0.1), 0.1)
        self.assertEqual(validate_interval_seconds(60), 60.0)

    def test_interval_validation_rejects_non_finite_or_out_of_range_override(self):
        for invalid in (0, -1, 60.1, float("nan"), float("inf"), float("-inf"), True):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    validate_interval_seconds(invalid)

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

    def test_resolves_current_process_cgroup_v2_path(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "self").mkdir()
            (root / "self" / "cgroup").write_text("0::/user.slice/session.scope\n")
            cgroup = root / "cgroup"
            (cgroup / "user.slice" / "session.scope").mkdir(parents=True)
            resolved = resolve_process_cgroup_root(root, cgroup)
            self.assertEqual(resolved, cgroup / "user.slice" / "session.scope")

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
            path = Path(write_snapshot(event, Path(temp)))
            self.assertTrue(path.exists())
            saved = json.loads(path.read_text())
            self.assertEqual(saved["decision"]["mode"], "observe")
            self.assertEqual(saved["decision"]["action"], "none")

    def test_snapshot_refuses_to_cross_capacity_without_deleting_history(self):
        event = {"event_id": "new", "decision": {"action": "none"}}
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            existing = directory / "existing.json"
            existing.write_text("history\n", encoding="utf-8")
            path = write_snapshot(event, directory, max_total_bytes=existing.stat().st_size)
            self.assertIsNone(path)
            self.assertEqual(existing.read_text(encoding="utf-8"), "history\n")

    def test_audit_refuses_to_cross_capacity_and_returns_failure(self):
        event = {"event_id": "one", "state": "normal"}
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            self.assertTrue(append_audit(event, path, max_total_bytes=1024))
            size = path.stat().st_size
            self.assertFalse(append_audit({"event_id": "two", "payload": "x"}, path, max_total_bytes=size))
            self.assertEqual(path.stat().st_size, size)

    def test_audit_write_error_returns_failure_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "not-a-file"
            directory.mkdir()
            self.assertFalse(append_audit({"event_id": "bad"}, directory))

    def test_snapshot_write_error_returns_no_path_instead_of_raising(self):
        with tempfile.TemporaryDirectory() as temp:
            file_path = Path(temp) / "not-a-directory"
            file_path.write_text("occupied", encoding="utf-8")
            self.assertIsNone(write_snapshot({"event_id": "bad"}, file_path))

    def test_docker_stats_timeout_is_degraded_read_only_observation(self):
        def timeout_runner(*_args, **_kwargs):
            raise subprocess.TimeoutExpired(["docker", "stats"], 3)

        result = collect_docker_stats(timeout_runner)
        self.assertFalse(result["available"])
        self.assertIn("timed out", result["error"])

    def test_risk_evaluator_requires_persistence_window_and_emits_recovery(self):
        observation = {
            "observed_at": "2026-09-19T00:00:00Z",
            "memory": {"available_ratio_percent": 5.0, "available_bytes": 500},
            "cgroup": {"memory_events": {}},
            "psi": {},
            "docker": {"containers": []},
        }
        evaluator = RiskEvaluator(warning_for=2.0, critical_for=4.0)
        first = evaluator.evaluate(observation, 15.0, 10.0, now=0.0)
        self.assertEqual(first["candidate_state"], "critical")
        self.assertEqual(first["state"], "normal")
        second = evaluator.evaluate(observation, 15.0, 10.0, now=4.0)
        self.assertEqual(second["state"], "critical")
        recovered = dict(observation)
        recovered["memory"] = {"available_ratio_percent": 90.0, "available_bytes": 9000}
        third = evaluator.evaluate(recovered, 15.0, 10.0, now=5.0)
        self.assertEqual(third["state"], "recovered")

    def test_simulate_never_executes_and_protects_by_default(self):
        observation = {
            "observed_at": "2026-09-19T00:00:00Z",
            "memory": {"available_ratio_percent": 5.0, "available_bytes": 500},
            "cgroup": {"memory_events": {"oom": 1}},
            "psi": {},
            "docker": {"containers": [{"ID": "abc123", "Name": "discardable"}]},
        }
        event = build_event(
            observation,
            mode="simulate",
            simulate_action="graceful_stop",
            protected=True,
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(event["decision"]["action"], "escalate")
        self.assertEqual(event["decision"]["execution"], "not_executed")
        self.assertIn("protected_object", event["decision"]["reason_codes"])

    def test_simulate_generates_allowlisted_plan_without_execution(self):
        observation = {
            "observed_at": "2026-09-19T00:00:00Z",
            "memory": {"available_ratio_percent": 5.0, "available_bytes": 500},
            "cgroup": {"memory_events": {"oom": 1}},
            "psi": {},
            "docker": {"containers": [{"ID": "abc123", "Name": "discardable"}]},
        }
        event = build_event(
            observation,
            mode="simulate",
            simulate_action="graceful_stop",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(event["decision"]["action"], "graceful_stop")
        self.assertEqual(event["decision"]["execution"], "not_executed")
        self.assertIn("simulate_only", event["decision"]["reason_codes"])

    def test_simulate_escalates_when_multiple_stable_objects_compete(self):
        observation = {
            "observed_at": "2026-09-19T00:00:00Z",
            "memory": {"available_ratio_percent": 5.0, "available_bytes": 500},
            "cgroup": {"memory_events": {"oom": 1}},
            "psi": {},
            "docker": {"containers": [
                {"ID": "abcdef123456", "Name": "discardable-a"},
                {"ID": "fedcba654321", "Name": "discardable-b"},
            ]},
        }
        event = build_event(
            observation,
            mode="simulate",
            simulate_action="graceful_stop",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(event["decision"]["action"], "escalate")
        self.assertIn("ambiguous_object_identity", event["decision"]["reason_codes"])

    def test_simulate_escalates_when_candidate_has_no_stable_id(self):
        observation = {
            "observed_at": "2026-09-19T00:00:00Z",
            "memory": {"available_ratio_percent": 5.0, "available_bytes": 500},
            "cgroup": {"memory_events": {"oom": 1}},
            "psi": {},
            "docker": {"containers": [{"Name": "name-only"}]},
        }
        event = build_event(
            observation,
            mode="simulate",
            simulate_action="graceful_stop",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(event["decision"]["action"], "escalate")
        self.assertIn("no_stable_object_identity", event["decision"]["reason_codes"])

    def test_enforce_plan_requires_controller_and_is_not_runtime_execution(self):
        observation = {
            "observed_at": "2026-09-19T00:00:00Z",
            "memory": {"available_ratio_percent": 5.0, "available_bytes": 500},
            "cgroup": {"memory_events": {"oom": 1}},
            "psi": {},
            "docker": {"containers": [{"ID": "abcdef123456", "Name": "discardable"}]},
        }
        event = build_event(
            observation,
            mode="enforce",
            simulate_action="graceful_stop",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(event["decision"]["action"], "graceful_stop")
        self.assertEqual(event["decision"]["execution"], "pending_controller")

    def test_unconfirmed_object_attribution_cannot_create_simulate_plan(self):
        observation = {
            "observed_at": "2026-09-19T00:00:00Z",
            "memory": {"available_ratio_percent": 5.0, "available_bytes": 500},
            "cgroup": {"memory_events": {"oom": 1}},
            "psi": {},
            "docker": {"containers": [{"ID": "abc123", "Name": "discardable"}]},
        }
        event = build_event(
            observation,
            mode="simulate",
            simulate_action="graceful_stop",
            protected=False,
            allowed_actions=["graceful_stop"],
            object_attribution={
                "state": "AMBIGUOUS_TARGET",
                "candidates": [{"id": "abc123", "name": "discardable", "confidence": "high"}],
            },
        )
        self.assertEqual(event["decision"]["action"], "escalate")
        self.assertIn("object_attribution_not_confirmed", event["decision"]["reason_codes"])


if __name__ == "__main__":
    unittest.main()
