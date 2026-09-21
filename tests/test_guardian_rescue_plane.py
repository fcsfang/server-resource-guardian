import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.guardian_rescue_probe import run_experiment, summarize


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


class GuardianRescuePlaneTests(unittest.TestCase):
    def test_rescue_and_workload_slices_are_explicit_and_ordered(self):
        rescue = section_values(DEPLOY / "rescue.slice", "Slice")
        workload = section_values(DEPLOY / "workload.slice", "Slice")
        self.assertEqual(rescue["CPUWeight"], "1000")
        self.assertEqual(workload["CPUWeight"], "1")
        self.assertGreater(int(rescue["CPUWeight"]), int(workload["CPUWeight"]))
        self.assertGreater(int(rescue["IOWeight"]), int(workload["IOWeight"]))
        self.assertEqual(rescue["AllowedCPUs"], "0")
        self.assertEqual(workload["AllowedCPUs"], "1")
        self.assertEqual(rescue["MemoryMin"], "512M")
        self.assertEqual(rescue["MemoryLow"], "768M")
        self.assertEqual(rescue["TasksMax"], "1024")
        self.assertEqual(workload["MemoryMax"], "512M")
        self.assertEqual(workload["TasksMax"], "256")

    def test_probe_contains_only_fixed_read_only_guest_commands(self):
        source = (ROOT / "scripts" / "guardian_rescue_probe.py").read_text(encoding="utf-8")
        for forbidden in (
            "docker stop",
            "docker restart",
            "docker kill",
            "systemctl stop",
            "systemctl restart",
            "systemctl kill",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("docker ps --format", source)
        self.assertIn("guardian_observe_once", source)

    def test_summary_uses_success_and_latency_fields(self):
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

    def test_experiment_result_declares_no_mutation(self):
        fake = {
            "returncode": 0,
            "stdout": "",
            "stderr": "",
        }
        with tempfile.TemporaryDirectory() as temp, patch("scripts.guardian_rescue_probe.subprocess.run", return_value=type("R", (), fake)()):
            result = run_experiment(
                vm="fixture",
                repo="/tmp/repo",
                audit="/tmp/audit",
                count=1,
                timeout_seconds=1,
                label="fixture",
            )
            output = Path(temp) / "result.json"
            output.write_text(json.dumps(result), encoding="utf-8")
            safety = json.loads(output.read_text(encoding="utf-8"))["safety"]
            self.assertTrue(safety["read_only_control_probes"])
            self.assertTrue(safety["temporary_audit_write_only"])
            self.assertFalse(result["safety"]["docker_mutation_invoked"])
            self.assertEqual(len(result["records"]), 4)


if __name__ == "__main__":
    unittest.main()
