import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "guardian-x86-preflight.py"
SPEC = importlib.util.spec_from_file_location("guardian_x86_preflight", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class GuardianX86PreflightTests(unittest.TestCase):
    def good_facts(self):
        return {
            "system": "Linux",
            "machine": "x86_64",
            "os_id": "ubuntu",
            "os_version": "22.04",
            "kernel": "5.15.0",
            "pid1": "systemd",
            "cgroup_v2": True,
            "systemctl_available": True,
            "docker_readable": True,
            "cpu_count": 2,
            "memory_bytes": 4 * 1024**3,
            "disk_free_bytes": 20 * 1024**3,
            "broker_marker_present": False,
            "reserve_broker_marker_present": False,
        }

    def test_approved_shape_is_ready_for_observe_only_install(self):
        report = MODULE.evaluate(self.good_facts())
        self.assertTrue(report["ready_for_observe_install"])
        self.assertEqual(report["blockers"], [])
        self.assertTrue(report["safety"]["read_only"])
        self.assertFalse(report["safety"]["automatic_actions_authorized"])

    def test_wrong_architecture_or_open_broker_blocks_admission(self):
        facts = self.good_facts()
        facts["machine"] = "aarch64"
        facts["broker_marker_present"] = True
        report = MODULE.evaluate(facts)
        self.assertFalse(report["ready_for_observe_install"])
        self.assertEqual(report["blockers"], ["x86_64", "broker_marker_absent"])

    def test_missing_resources_and_docker_fail_closed(self):
        facts = self.good_facts()
        facts.update({"docker_readable": False, "cpu_count": 1, "memory_bytes": None, "disk_free_bytes": 1})
        report = MODULE.evaluate(facts)
        self.assertFalse(report["ready_for_observe_install"])
        self.assertEqual(
            report["blockers"],
            ["docker_readable", "cpu_minimum", "memory_minimum", "disk_minimum"],
        )


if __name__ == "__main__":
    unittest.main()
