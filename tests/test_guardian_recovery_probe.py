from __future__ import annotations

import importlib.util
import subprocess
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools/guardian-recovery-lab/probe_ssh.py"
SPEC = importlib.util.spec_from_file_location("guardian_recovery_probe", MODULE_PATH)
assert SPEC and SPEC.loader
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


class GuardianRecoveryProbeTests(unittest.TestCase):
    def test_remote_command_marks_shell_and_each_requested_stage(self) -> None:
        command = probe.build_remote_command([("resources", "free -m"), ("guardian", "guardian-rescue status")])

        self.assertIn("__GUARDIAN_STAGE__:shell:EXIT:%s:OUTPUT_B64:%s__", command)
        self.assertIn("__GUARDIAN_STAGE__:resources:EXIT:%s:OUTPUT_B64:%s__", command)
        self.assertIn("__GUARDIAN_STAGE__:guardian:EXIT:%s:OUTPUT_B64:%s__", command)
        self.assertIn('exit "$overall"', command)

    def test_stage_results_preserve_intermediate_failure(self) -> None:
        results = probe.parse_stage_results(
            "__GUARDIAN_STAGE__:shell:EXIT:0__\n"
            "__GUARDIAN_STAGE__:resources:EXIT:1__\n"
            "__GUARDIAN_STAGE__:guardian:EXIT:0__\n"
        )

        self.assertEqual(
            results,
            [
                {"name": "shell", "returncode": 0},
                {"name": "resources", "returncode": 1},
                {"name": "guardian", "returncode": 0},
            ],
        )
        self.assertTrue(any(stage["returncode"] != 0 for stage in results))

    def test_missing_stage_marker_is_not_a_complete_probe(self) -> None:
        results = probe.parse_stage_results("__GUARDIAN_STAGE__:shell:EXIT:0__\n")
        expected = ["shell", "resources"]

        self.assertNotEqual([stage["name"] for stage in results], expected)

    def test_remote_wrapper_returns_failure_but_collects_later_stages(self) -> None:
        remote_command = probe.build_remote_command(
            [("broken", "false"), ("later", "printf later-stage-ran")]
        )
        result = subprocess.run(["bash", "-lc", remote_command], capture_output=True, text=True, check=False)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            probe.parse_stage_results(result.stdout),
            [
                {"name": "shell", "returncode": 0},
                {"name": "broken", "returncode": 1},
                {"name": "later", "returncode": 0},
            ],
        )
        evidence = probe.parse_stage_evidence(result.stdout)
        self.assertEqual(evidence[-1]["output"], "later-stage-ran")

    def test_invalid_stage_name_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            probe.build_remote_command([("bad:name", "true")])


if __name__ == "__main__":
    unittest.main()
