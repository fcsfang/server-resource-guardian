import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.guardian_status import build_status, exit_code, format_status


class GuardianStatusTests(unittest.TestCase):
    def test_build_status_reports_healthy_observe_runtime_and_closed_broker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "guardian.json"
            readiness = root / "ready"
            audit = root / "events.jsonl"
            marker = root / "broker.enabled"
            socket = root / "broker.sock"
            collector_socket = root / "collector.sock"
            collector_socket.touch()
            config.write_text(json.dumps({"agent": {"mode": "observe"}, "actions": {"enabled": False}}), encoding="utf-8")
            readiness.write_text("runtime:ready:observe\n", encoding="utf-8")
            audit.write_text(json.dumps({
                "state": "normal",
                "observed_at": "2026-09-21T00:00:00Z",
                "object_candidates": [{"name": "fixture-app", "score": 12.5, "confidence": "high", "mapping_errors": []}],
                "decision": {"mode": "observe", "action": "none", "execution": "not_applicable", "reason_codes": ["observe_only"]},
                "presentation": {
                    "candidate_ranking": {
                        "status": "ok",
                        "resource_kind": "memory",
                        "count": 1,
                        "top": {"name": "fixture-app", "score": 12.5, "protected": True, "protection_reasons": ["fixture-protected"]},
                        "candidates": [{"name": "fixture-app", "score": 12.5, "protected": True, "protection_reasons": ["fixture-protected"]}],
                    },
                    "protection": {
                        "protected_candidates": [{"name": "fixture-app", "reasons": ["fixture-protected"]}],
                        "configured_systemd_units": 1,
                        "configured_container_labels": 0,
                    },
                    "simulation": {
                        "mode": "observe",
                        "action": "none",
                        "execution": "not_applicable",
                        "target_name": None,
                        "reason_codes": ["OBSERVE_ONLY"],
                    },
                },
            }) + "\n", encoding="utf-8")

            def runner(command, **kwargs):
                value = "active" if command[1] == "is-active" and command[2] in {"guardian-runtime.service", "guardian-collector.service"} else "enabled"
                if command[2] in {"guardian-broker.service", "guardian-reserve-broker.service"}:
                    value = "inactive" if command[1] == "is-active" else "static"
                return subprocess.CompletedProcess(command, 0, value + "\n", "")

            value = build_status(
                config_path=config,
                readiness_path=readiness,
                audit_path=audit,
                broker_marker=marker,
                broker_socket=socket,
                collector_socket=collector_socket,
                runner=runner,
            )
            self.assertEqual(value["overall"], "healthy")
            self.assertEqual(value["automatic_actions"], "disabled")
            self.assertTrue(value["broker"]["closed"])
            self.assertTrue(value["reserve_broker"]["closed"])
            self.assertEqual(value["recent_alerts"]["candidate_ranking"]["top"]["name"], "fixture-app")
            self.assertEqual(value["recent_alerts"]["simulation"]["action"], "none")
            self.assertEqual(value["recent_alerts"]["protection"]["protected_candidates"][0]["name"], "fixture-app")
            self.assertEqual(exit_code(value), 0)
            formatted = format_status(value)
            self.assertIn("Guardian: healthy", formatted)
            self.assertIn("Protected candidates: fixture-app(fixture-protected)", formatted)

    def test_status_is_not_healthy_when_runtime_or_config_is_uncertain(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "missing.json"
            readiness = root / "missing.ready"
            audit = root / "missing.jsonl"

            def runner(command, **kwargs):
                value = "inactive" if command[1] == "is-active" else "disabled"
                return subprocess.CompletedProcess(command, 3, "", "")

            value = build_status(config_path=config, readiness_path=readiness, audit_path=audit, runner=runner)
            self.assertEqual(value["overall"], "unknown")
            self.assertEqual(exit_code(value), 2)

    def test_status_surfaces_continuous_action_and_recovery_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "guardian.json"
            readiness = root / "ready"
            audit = root / "events.jsonl"
            collector_socket = root / "collector.sock"
            collector_socket.touch()
            config.write_text(json.dumps({"agent": {"mode": "observe"}, "actions": {"enabled": False}}), encoding="utf-8")
            readiness.write_text("runtime:ready:observe\n", encoding="utf-8")
            event = {
                "schema": "guardian.risk.event.v1",
                "state": "critical",
                "observed_at": "2026-09-21T00:00:00Z",
                "presentation": {"candidate_ranking": {}, "protection": {}, "simulation": {}},
                "runtime_result": {
                    "state": "executed",
                    "semantic_state": "MITIGATED",
                    "execution_semantics": "REAL",
                    "broker": {
                        "action_result": {"action": "graceful_stop"},
                        "verification": {"overall_state": "MITIGATED"},
                    },
                    "reserve_recovery": {
                        "action": "release_emergency_reserve",
                        "state": "released",
                        "execution": "executed",
                        "reason_codes": ["guardian_reserve_released"],
                    },
                },
            }
            event["schema"] = "guardian.runtime.result.v1"
            event["event"] = {"state": "critical", "observed_at": event["observed_at"], "presentation": event["presentation"]}
            audit.write_text(json.dumps(event) + "\n", encoding="utf-8")

            def runner(command, **kwargs):
                value = "active" if command[1] == "is-active" else "enabled"
                if command[2] in {"guardian-broker.service", "guardian-reserve-broker.service"}:
                    value = "inactive" if command[1] == "is-active" else "static"
                return subprocess.CompletedProcess(command, 0, value + "\n", "")

            value = build_status(
                config_path=config,
                readiness_path=readiness,
                audit_path=audit,
                collector_socket=collector_socket,
                runner=runner,
            )
            self.assertEqual(value["recent_alerts"]["action"]["action"], "graceful_stop")
            self.assertEqual(value["recent_alerts"]["action"]["recovery"]["overall_state"], "MITIGATED")
            self.assertEqual(value["recent_alerts"]["action"]["reserve_recovery"]["state"], "released")

    def test_cli_json_is_read_only_and_uses_fixture_paths(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = root / "guardian.json"
            readiness = root / "ready"
            audit = root / "events.jsonl"
            collector_socket = root / "collector.sock"
            collector_socket.touch()
            config.write_text(json.dumps({"agent": {"mode": "observe"}, "actions": {"enabled": False}}), encoding="utf-8")
            readiness.write_text("runtime:ready:observe\n", encoding="utf-8")
            audit.write_text("", encoding="utf-8")
            with patch("scripts.guardian_status._load_status_module") as loader:
                def runner(command, **kwargs):
                    if command[2] in {"guardian-broker.service", "guardian-reserve-broker.service"}:
                        value = "inactive" if command[1] == "is-active" else "static"
                    else:
                        value = "active" if command[1] == "is-active" else "enabled"
                    return subprocess.CompletedProcess(command, 0, value + "\n", "")

                loader.return_value = (
                    lambda **kwargs: build_status(
                        config_path=config,
                        readiness_path=readiness,
                        audit_path=audit,
                        collector_socket=collector_socket,
                        runner=runner,
                    ),
                    exit_code,
                    format_status,
                )
                from scripts.guardian_status import main

                self.assertEqual(main(["--json"]), 0)


if __name__ == "__main__":
    unittest.main()
