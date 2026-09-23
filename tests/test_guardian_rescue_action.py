import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from src.guardian_actions import Authorization
from src.guardian_rescue_action import RescueActionError, stop, verify


TARGET = "a" * 64


class GuardianRescueActionTests(unittest.TestCase):
    def _config(self, root: Path) -> Path:
        value = json.loads((Path("config") / "guardian.example.json").read_text(encoding="utf-8"))
        value["actions"]["enabled"] = True
        value["actions"]["allow"] = ["graceful_stop"]
        value["actions"]["max_actions_per_host_per_hour"] = 1
        value["risk"]["emergency_shedding"]["actionable_set"] = [{
            "stable_id": TARGET,
            "owner": "fixture",
            "environment": "local-disposable",
            "allowed_resources": ["memory"],
            "action": "graceful_stop",
            "grace_timeout_seconds": 30,
            "expires_at": "2099-01-01T00:00:00Z",
            "human_contact": "fixture",
        }]
        path = root / "guardian.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_requires_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(RescueActionError, "explicit_confirmation_required"):
                stop(target_id=TARGET, authorization_file=Path(temp) / "approval.json", confirm=False, config_path=Path(temp) / "missing.json")

    def test_authorized_stop_is_single_use_and_audited(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self._config(root)
            cgroup = root / "cgroup" / "workload.slice" / "fixture.scope"
            cgroup.mkdir(parents=True)
            (cgroup / "cgroup.procs").write_text("999999\n", encoding="utf-8")
            state = root / "state.db"
            audit = root / "audit.jsonl"
            auth = Authorization("approval-1", "local-disposable", TARGET, "graceful_stop", 2000.0)
            runner = lambda *args, **kwargs: CompletedProcess(args[0], 0, "", "")
            with patch("src.guardian_rescue_action._authorization", return_value=auth), patch(
                "src.guardian_rescue_action._identity",
                return_value={"stable_id": TARGET, "cgroup_path": str(cgroup), "cgroup_inode": cgroup.stat().st_ino, "observed_at": 1000.0},
            ):
                result = stop(
                    target_id=TARGET,
                    authorization_file=root / "approval.json",
                    confirm=True,
                    config_path=config,
                    cgroup_root=root / "cgroup",
                    identity_cache=root / "identities.json",
                    state_path=state,
                    audit_path=audit,
                    runner=runner,
                    now=1000.0,
                )
                self.assertEqual(result["execution"], "term_sent")
                with self.assertRaisesRegex(RescueActionError, "capability_already_consumed"):
                    stop(
                        target_id=TARGET,
                        authorization_file=root / "approval.json",
                        confirm=True,
                        config_path=config,
                        cgroup_root=root / "cgroup",
                        identity_cache=root / "identities.json",
                        state_path=state,
                        audit_path=audit,
                        runner=runner,
                        now=1001.0,
                    )
            self.assertIn("target_cgroup_path", audit.read_text(encoding="utf-8"))

    def test_verify_records_memory_high_boundary_without_escalation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = self._config(root)
            cgroup = root / "cgroup" / "workload.slice" / "fixture.scope"
            cgroup.mkdir(parents=True)
            (cgroup / "cgroup.procs").write_text("999999\n", encoding="utf-8")
            (cgroup / "memory.current").write_text("476274688\n", encoding="utf-8")
            (cgroup / "memory.high").write_text("469762048\n", encoding="utf-8")
            (cgroup / "memory.max").write_text("536870912\n", encoding="utf-8")
            (cgroup / "memory.events").write_text("high 31892\nmax 0\noom 0\noom_kill 0\n", encoding="utf-8")
            proc = root / "proc"
            (proc / "pressure").mkdir(parents=True)
            (proc / "meminfo").write_text("MemAvailable: 100 kB\n", encoding="utf-8")
            (proc / "pressure" / "memory").write_text("some avg10=0.0\nfull avg10=0.0\n", encoding="utf-8")
            audit = root / "audit.jsonl"
            auth = Authorization("approval-1", "local-disposable", TARGET, "graceful_stop", 2000.0)
            runner = lambda *args, **kwargs: CompletedProcess(args[0], 0, "", "")
            with patch("src.guardian_rescue_action._authorization", return_value=auth), patch(
                "src.guardian_rescue_action._identity",
                return_value={"stable_id": TARGET, "cgroup_path": str(cgroup), "cgroup_inode": cgroup.stat().st_ino, "observed_at": 1000.0},
            ):
                stop(
                    target_id=TARGET,
                    authorization_file=root / "approval.json",
                    confirm=True,
                    config_path=config,
                    cgroup_root=root / "cgroup",
                    identity_cache=root / "identities.json",
                    state_path=root / "state.db",
                    audit_path=audit,
                    runner=runner,
                    now=1000.0,
                )
            result = verify(target_id=TARGET, audit_path=audit, proc_root=proc, wait_seconds=0)
            self.assertEqual(result["state"], "pending")
            self.assertEqual(result["cleanup"]["escalation"], "none")
            self.assertIn("target_above_memory_high", result["reason_codes"])
            self.assertEqual(result["target_cgroup_memory"]["high"], 469762048)
            self.assertEqual(result["target_cgroup_memory"]["max"], 536870912)


if __name__ == "__main__":
    unittest.main()
