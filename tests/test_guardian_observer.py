import errno
import json
import subprocess
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch

from src.guardian_config import safe_defaults, validate_config
from src.guardian_observer import (
    RiskEvaluator,
    _build_runtime_presentation,
    append_audit,
    build_event,
    build_cpu_simulation_decision,
    collect_docker_stats,
    collect_observation,
    parse_meminfo,
    parse_psi,
    record_watchdog_status,
    resolve_process_cgroup_root,
    run,
    validate_interval_seconds,
    write_snapshot,
)


class GuardianObserverTests(unittest.TestCase):
    def test_runtime_presentation_exposes_ranked_candidate_protection_and_simulation(self):
        config_value = safe_defaults().as_dict()
        config_value["protection"]["container_labels"] = ["guardian.role=control-plane"]
        config = validate_config(config_value, source="<presentation-fixture>")
        candidate_id = "a" * 64
        event = {
            "object_candidates": [
                {
                    "id": candidate_id,
                    "name": "control-plane",
                    "score": 0.9,
                    "confidence": "high",
                    "host_contribution_percent": 90.0,
                    "mapping_errors": [],
                }
            ]
        }
        observation = {
            "object_registry": {
                "status": "ok",
                "objects": [{
                    "id": candidate_id,
                    "name": "control-plane",
                    "labels": {"guardian.role": "control-plane"},
                }],
            }
        }
        emergency = {
            "rankings": {},
            "decision": {
                "mode": "simulate",
                "action": "graceful_stop",
                "execution": "not_executed",
                "resource_kind": "memory",
                "target_id": candidate_id,
                "reason_codes": ["TOP_ACTIONABLE_CONSUMER"],
            },
        }

        value = _build_runtime_presentation(event, observation, emergency, config)

        self.assertEqual(value["candidate_ranking"]["top"]["name"], "control-plane")
        self.assertTrue(value["candidate_ranking"]["top"]["protected"])
        self.assertIn("container_label:guardian.role=control-plane", value["candidate_ranking"]["top"]["protection_reasons"])
        self.assertEqual(value["simulation"]["target_name"], "control-plane")
        self.assertEqual(value["simulation"]["execution"], "not_executed")

    def test_cpu_simulation_is_plan_only_and_fail_closed_on_unknown_target(self):
        risk = {"state": "critical"}
        confirmed = {"state": "TARGET_CONFIRMED"}
        plan = build_cpu_simulation_decision(
            risk,
            confirmed,
            mode="simulate",
            simulate_action="graceful_stop",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(plan["action"], "graceful_stop")
        self.assertEqual(plan["execution"], "not_executed")
        self.assertIn("cpu_simulate_only", plan["reason_codes"])
        rejected = build_cpu_simulation_decision(
            risk,
            {"state": "AMBIGUOUS_TARGET"},
            mode="simulate",
            simulate_action="graceful_stop",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(rejected["action"], "escalate")
        self.assertEqual(rejected["execution"], "not_executed")
        self.assertIn("cpu_attribution_not_confirmed", rejected["reason_codes"])

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
            self.assertIn("disk", observation)
            self.assertIn("capacity_quality", observation["disk"])
            self.assertIn("io", observation["disk"])
            event = build_event(observation)
            self.assertEqual(event["state"], "critical")
            self.assertEqual(event["decision"]["action"], "none")

    def test_collect_observation_uses_external_container_collector(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pressure").mkdir()
            (root / "meminfo").write_text("MemTotal: 100000 kB\nMemAvailable: 90000 kB\n")
            cgroup = root / "cgroup"
            cgroup.mkdir()
            (cgroup / "memory.current").write_text("1234\n")
            (cgroup / "memory.max").write_text("max\n")
            (cgroup / "memory.events").write_text("oom 0\noom_kill 0\n")
            (cgroup / "pids.current").write_text("3\n")
            (cgroup / "pids.max").write_text("max\n")

            def forbidden_runner(*_args, **_kwargs):
                raise AssertionError("runtime must not invoke docker commands")

            observation = collect_observation(
                root,
                cgroup,
                forbidden_runner,
                container_collector=lambda: {
                    "docker": {"available": True, "containers": [{"Container": "abc"}]},
                    "object_registry": {"status": "ok", "objects": [], "errors": []},
                    "collector": {"status": "ok", "read_only": True},
                },
            )
            self.assertEqual(observation["docker"]["containers"][0]["Container"], "abc")
            self.assertEqual(observation["object_registry"]["status"], "ok")
            self.assertNotIn("docker_observation_unavailable", observation["quality"]["flags"])

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

    def test_audit_no_space_error_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            with patch.object(Path, "open", side_effect=OSError(errno.ENOSPC, "No space left on device")):
                self.assertFalse(append_audit({"event_id": "no-space"}, path))
            self.assertFalse(path.exists())

    def test_snapshot_no_space_error_is_fail_closed_without_overwriting_history(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            existing = directory / "history.json"
            existing.write_text("history\n", encoding="utf-8")
            with patch.object(Path, "write_text", side_effect=OSError(errno.ENOSPC, "No space left on device")):
                self.assertIsNone(write_snapshot({"event_id": "no-space"}, directory))
            self.assertEqual(existing.read_text(encoding="utf-8"), "history\n")

    def test_audit_syncs_before_reporting_success(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            with patch("src.guardian_observer.os.fsync") as sync:
                self.assertTrue(append_audit({"event_id": "durable"}, path))
            sync.assert_called_once()
            self.assertIn('"event_id": "durable"', path.read_text(encoding="utf-8"))

    def test_audit_sync_failure_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "events.jsonl"
            with patch("src.guardian_observer.os.fsync", side_effect=OSError("sync failed")):
                self.assertFalse(append_audit({"event_id": "uncertain"}, path))
            self.assertIn('"event_id": "uncertain"', path.read_text(encoding="utf-8"))

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

    def test_configured_watchdog_failure_is_audited_and_blocks_execution(self):
        event = {
            "evidence": {},
            "decision": {
                "action": "graceful_stop",
                "execution": "pending_controller",
                "reason_codes": [],
            },
        }
        status = record_watchdog_status(event, False, notify_socket="@guardian")
        self.assertEqual(status, "failed")
        self.assertEqual(event["evidence"]["watchdog_status"], "failed")
        self.assertEqual(event["decision"]["action"], "escalate")
        self.assertEqual(event["decision"]["execution"], "not_executed")
        self.assertIn("watchdog_notify_failed", event["decision"]["reason_codes"])

    def test_unconfigured_watchdog_is_explicit_without_escalation(self):
        event = {
            "evidence": {},
            "decision": {"action": "none", "execution": "not_applicable", "reason_codes": []},
        }
        status = record_watchdog_status(event, False, notify_socket="")
        self.assertEqual(status, "not_configured")
        self.assertEqual(event["evidence"]["watchdog_status"], "not_configured")
        self.assertEqual(event["decision"]["action"], "none")

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

    def test_run_makes_emergency_decision_authoritative_in_observe_and_simulate(self):
        candidate_id = "a" * 64
        protected_id = "b" * 64
        identity = {
            "kind": "container",
            "id": candidate_id,
            "name": "fixture-shed-target",
            "status": "running",
            "created_at": "2026-09-21T00:00:00Z",
            "cgroup_path": f"/sys/fs/cgroup/docker/{candidate_id}",
            "cgroup_inode": 12345,
        }
        observation = {
            "observed_at": "2026-09-21T00:00:00Z",
            "memory": {"available_ratio_percent": 4.0, "available_bytes": 400},
            "cgroup": {"memory_events": {"oom": 1}},
            "psi": {},
            "docker": {"containers": [{"ID": candidate_id, "Name": identity["name"]}]},
            "object_registry": {"objects": [identity]},
            "cpu": {},
            "disk": {},
        }
        memory_risk = {
            "state": "critical",
            "candidate_state": "critical",
            "candidate_for_seconds": 30.0,
            "required_for_seconds": 30.0,
            "reasons": ["fixture_critical"],
            "sample_count": 3,
            "quality_status": "ok",
            "quality_flags": [],
        }
        object_attribution = {
            "state": "TARGET_CONFIRMED",
            "target": dict(identity),
            "candidates": [
                {
                    "kind": "container",
                    "id": candidate_id,
                    "name": identity["name"],
                    "confidence": "high",
                    "cgroup_path": identity["cgroup_path"],
                    "host_contribution_percent": 90.0,
                    "mapping_errors": [],
                }
            ],
            "reason_codes": [],
            "quality_flags": [],
        }

        def enabled_config(mode):
            value = safe_defaults().as_dict()
            value["agent"]["mode"] = mode
            value["actions"]["allow"] = ["graceful_stop"]
            if mode == "enforce":
                value["actions"]["enabled"] = True
                value["actions"]["require_approval"] = True
                value["actions"]["authorization_file"] = "/fixture/authorization.json"
            value["risk"]["emergency_shedding"] = {
                "schema": "guardian.emergency_shedding.v1",
                "enabled": True,
                "window_seconds": 15,
                "required_samples": 2,
                "min_host_contribution_percent": 20,
                "resource_priority": ["memory", "cpu", "io", "disk_capacity"],
                "action": "graceful_stop",
                "protected_set": [
                    {
                        "stable_id": protected_id,
                        "owner": "platform",
                        "reason": "fixture-protected",
                    }
                ],
                "actionable_set": [
                    {
                        "stable_id": candidate_id,
                        "owner": "fixture",
                        "environment": "local-disposable",
                        "allowed_resources": ["memory"],
                        "action": "graceful_stop",
                        "grace_timeout_seconds": 30,
                        "expires_at": "2099-01-01T00:00:00Z",
                        "human_contact": "fixture@example.invalid",
                    }
                ],
            }
            return validate_config(value, source=f"<observer-{mode}-fixture>")

        configs = [enabled_config("observe"), enabled_config("simulate"), enabled_config("enforce")]
        args = Namespace(
            config=Path("/fixture/guardian.json"),
            mode=None,
            interval=0.1,
            warning_available=None,
            critical_available=None,
            warning_for=None,
            critical_for=None,
            snapshot_dir=None,
            snapshot_all=False,
            audit_file=None,
            simulate_action=None,
            allow_action=None,
            allow_unprotected=True,
            once=True,
            samples=None,
        )

        with (
            patch("src.guardian_observer.load_config", side_effect=configs),
            patch("src.guardian_observer.collect_observation", return_value=observation),
            patch("src.guardian_observer.CompositeRiskEvaluator") as composite_class,
            patch("src.guardian_observer.ObjectAttributionEvaluator") as object_attributor_class,
            patch("src.guardian_observer.CpuRiskEvaluator") as cpu_risk_class,
            patch("src.guardian_observer.CpuAttributionEvaluator") as cpu_attributor_class,
            patch("src.guardian_observer.DiskCapacityRiskEvaluator") as capacity_risk_class,
            patch("src.guardian_observer.CapacityAttributionEvaluator") as capacity_attributor_class,
            patch("src.guardian_observer.IoRiskEvaluator") as io_risk_class,
            patch("src.guardian_observer.DiskIoAttributionEvaluator") as io_attributor_class,
            patch("src.guardian_observer._emergency_host_risks") as emergency_host_risks,
            patch("src.guardian_observer._emergency_candidates") as emergency_candidates,
            patch("src.guardian_observer.notify_watchdog", return_value=False),
            patch("src.guardian_observer.notify_ready", return_value=False),
            patch("builtins.print") as printed,
        ):
            composite_class.return_value.evaluate.return_value = memory_risk
            object_attributor_class.return_value.evaluate.return_value = object_attribution
            cpu_risk_class.return_value.evaluate.return_value = {"state": "normal", "quality_flags": []}
            cpu_attributor_class.return_value.evaluate.return_value = {"state": "NO_TARGET", "quality_flags": []}
            capacity_risk_class.return_value.evaluate.return_value = {"state": "normal", "quality_flags": []}
            capacity_attributor_class.return_value.evaluate.return_value = {"state": "NO_TARGET", "quality_flags": []}
            io_risk_class.return_value.evaluate.return_value = {"state": "normal", "quality_flags": []}
            io_attributor_class.return_value.evaluate.return_value = {"state": "NO_TARGET", "quality_flags": []}
            emergency_host_risks.return_value = {
                "memory": {
                    "state": "CRITICAL_CONFIRMED",
                    "sample_complete": True,
                    "sample_count": 3,
                    "quality_status": "ok",
                    "quality_flags": [],
                }
            }
            emergency_candidates.return_value = [
                {
                    **identity,
                    "running": True,
                    "resource_contributions": {"memory": 90.0},
                    "contribution_window_seconds": 15.0,
                }
            ]

            for _ in configs:
                run(args)
            events = [json.loads(call.args[0]) for call in printed.call_args_list]

        self.assertEqual(len(events), 3)
        observe_event, simulate_event, enforce_event = events
        for event in events:
            self.assertEqual(event["target_attribution"], None)
            self.assertEqual(event["decision"], event["emergency_shedding"]["decision"])
        self.assertEqual(observe_event["decision"]["mode"], "observe")
        self.assertEqual(observe_event["decision"]["action"], "none")
        self.assertEqual(observe_event["decision"]["execution"], "not_applicable")
        self.assertEqual(observe_event["decision"]["plans_count"], 0)
        self.assertEqual(observe_event["decision"]["top_consumer"], candidate_id)
        self.assertIn("OBSERVE_ONLY", observe_event["decision"]["reason_codes"])
        self.assertEqual(simulate_event["decision"]["mode"], "simulate")
        self.assertEqual(simulate_event["decision"]["action"], "graceful_stop")
        self.assertEqual(simulate_event["decision"]["execution"], "not_executed")
        self.assertEqual(simulate_event["decision"]["plans_count"], 1)
        self.assertEqual(simulate_event["decision"]["plan"]["target_id"], candidate_id)
        self.assertEqual(enforce_event["decision"]["mode"], "enforce")
        self.assertEqual(enforce_event["decision"]["action"], "graceful_stop")
        self.assertEqual(enforce_event["decision"]["execution"], "not_executed")
        self.assertEqual(enforce_event["decision"]["plans_count"], 1)
        self.assertEqual(enforce_event["decision"]["plan"]["target_id"], candidate_id)
        self.assertEqual(enforce_event["state"], "critical_confirmed")


if __name__ == "__main__":
    unittest.main()
