import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.guardian_rescue_plan import render_plan
from scripts.guardian_rescue_probe import dry_run_plan, run_experiment, summarize
from scripts.guardian_rescue_matrix import evaluate_matrix


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy" / "guardian"


def section_values(path: Path, section: str) -> dict[str, str]:
    values: dict[str, str] = {}
    active = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("["):
            active = line == f"[{section}]"
            continue
        if active and "=" in line and not line.startswith("#"):
            key, value = line.split("=", 1)
            values[key] = value
    return values


class GuardianRescueDeploymentTests(unittest.TestCase):
    def test_resource_domains_are_explicit_and_ordered(self):
        rescue = section_values(DEPLOY / "rescue.slice", "Slice")
        workload = section_values(DEPLOY / "workload.slice", "Slice")
        self.assertEqual(rescue["CPUWeight"], "1000")
        self.assertEqual(workload["CPUWeight"], "100")
        self.assertGreater(int(rescue["CPUWeight"]), int(workload["CPUWeight"]))
        self.assertGreater(int(rescue["IOWeight"]), int(workload["IOWeight"]))
        self.assertEqual(rescue["MemoryMin"], "128M")
        self.assertEqual(rescue["MemoryLow"], "512M")
        self.assertEqual(rescue["TasksMax"], "512")
        self.assertEqual(workload["TasksMax"], "4096")
        self.assertNotIn("MemoryMax", rescue)
        self.assertNotIn("MemoryMax", workload)

    def test_runtime_is_non_root_and_does_not_use_docker_group(self):
        source = (DEPLOY / "guardian-runtime.service").read_text(encoding="utf-8")
        self.assertIn("User=guardian", source)
        self.assertIn("SupplementaryGroups=guardian-shared guardian-broker", source)
        self.assertNotIn("SupplementaryGroups=docker", source)
        self.assertIn("Slice=guardian-runtime.slice", source)
        self.assertIn("systemd-logind.service", source)
        self.assertIn("systemd-journald.service", source)
        self.assertIn("docker.service", source)
        self.assertIn("containerd.service", source)
        self.assertIn("RestrictAddressFamilies=AF_UNIX", source)
        runtime_slice = section_values(DEPLOY / "guardian-runtime.slice", "Slice")
        self.assertEqual(runtime_slice["CPUWeight"], "500")
        self.assertEqual(runtime_slice["TasksMax"], "256")

    def test_tmpfiles_preserves_shared_state_boundary(self):
        source = (DEPLOY / "guardian.tmpfiles").read_text(encoding="utf-8")
        self.assertIn("d /var/lib/guardian/shared 2770 root guardian-shared -", source)
        self.assertIn("d /var/lib/guardian/runtime 0700 guardian guardian -", source)
        self.assertIn("d /run/guardian-runtime 0750 guardian guardian -", source)

    def test_probe_cli_defaults_to_dry_run_and_has_no_mutation_words(self):
        source = (ROOT / "scripts" / "guardian_rescue_probe.py").read_text(encoding="utf-8")
        self.assertIn("--run-read-only", source)
        self.assertNotIn("docker stop", source)
        self.assertNotIn("docker restart", source)
        self.assertNotIn("docker kill", source)
        self.assertNotIn("systemctl stop", source)
        self.assertNotIn("systemctl restart", source)
        self.assertNotIn("systemctl kill", source)
        self.assertNotIn("multipass delete", source)
        plan = dry_run_plan(vm="fixture", repo="/tmp/repo", audit="/tmp/audit", count=1, timeout=1, label="fixture")
        self.assertEqual(plan["mode"], "dry-run")
        self.assertFalse(plan["multipass_invoked"])
        self.assertFalse(plan["changes_applied"])

    def test_plan_script_covers_four_actions_without_execution(self):
        source = (ROOT / "scripts" / "guardian_rescue_plan.py").read_text(encoding="utf-8")
        self.assertNotIn("import subprocess", source)
        self.assertNotIn("os.system", source)
        for action in ("install", "diagnose", "disable", "rollback"):
            plan = render_plan(action)
            self.assertEqual(plan["mode"], "dry-run")
            self.assertFalse(plan["changes_applied"])
            self.assertTrue(plan["requires_operator_review"])
            self.assertFalse(plan["systemd_mutation_invoked"])
            self.assertFalse(plan["docker_mutation_invoked"])
            self.assertFalse(plan["multipass_mutation_invoked"])

    def test_probe_summary_and_read_only_result(self):
        result = summarize(
            [
                {"name": "ssh", "elapsed_ms": 10, "ok": True},
                {"name": "ssh", "elapsed_ms": 30, "ok": False},
            ]
        )
        self.assertEqual(result["ssh"]["count"], 2)
        self.assertEqual(result["ssh"]["successes"], 1)
        self.assertEqual(result["ssh"]["failures"], 1)
        self.assertEqual(result["ssh"]["p95_ms"], 30)

        fake = type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})()
        with tempfile.TemporaryDirectory() as temp, patch(
            "scripts.guardian_rescue_probe.subprocess.run", return_value=fake
        ):
            probe = run_experiment(
                vm="fixture",
                repo="/tmp/repo",
                audit="/tmp/audit",
                count=1,
                timeout_seconds=1,
                label="fixture",
            )
            output = Path(temp) / "result.json"
            output.write_text(json.dumps(probe), encoding="utf-8")
            saved = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(saved["safety"]["read_only_control_probes"])
            self.assertTrue(saved["safety"]["temporary_audit_write_only"])
            self.assertFalse(saved["safety"]["docker_mutation_invoked"])
            self.assertEqual(len(saved["records"]), 4)

    def test_merged_matrix_fails_closed_on_missing_domain_or_evidence(self):
        probes = {
            name: {"attempts": 20, "successes": 20, "p95_ms": 100.0}
            for name in (
                "ssh_managed_session",
                "diagnostic_read_only",
                "docker_list_read_only",
                "guardian_observe_once",
            )
        }
        records = {scenario: dict(probes) for scenario in ("cpu", "memory", "io", "pid")}
        result = evaluate_matrix(
            records,
            capacity_inode_evidence=False,
            persistence_evidence=True,
            rollback_evidence=False,
        )
        self.assertEqual(result["status"], "INCONCLUSIVE")
        self.assertIn("scenario_missing:capacity_inode", result["reason_codes"])
        self.assertIn("capacity_inode_evidence_missing", result["reason_codes"])
        self.assertIn("rollback_evidence_missing", result["reason_codes"])
        self.assertTrue(result["read_only"])
        self.assertFalse(result["safety"]["new_threshold_experiment_created"])


if __name__ == "__main__":
    unittest.main()
