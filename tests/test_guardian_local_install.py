import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-guardian-local.sh"


class GuardianLocalInstallTests(unittest.TestCase):
    def test_default_command_is_a_non_mutating_plan(self):
        result = subprocess.run(
            [str(SCRIPT), "--repository", str(ROOT)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("mutations: no", result.stdout)
        self.assertIn("local-disposable", result.stdout)
        self.assertIn("guardian-runtime.service", result.stdout)

    def test_apply_requires_explicit_local_disposable_marker(self):
        result = subprocess.run(
            [str(SCRIPT), "--apply"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--apply requires --environment local-disposable", result.stderr)

    def test_script_keeps_runtime_observe_only_and_broker_closed(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("mode != \"observe\"", source)
        self.assertIn("actions_enabled is not False", source)
        self.assertIn("broker.enabled", source)
        self.assertIn("/run/guardian-broker/broker.sock", source)
        self.assertIn("guardian-reserve-broker.service", source)
        self.assertIn("/run/guardian-reserve-broker/reserve.sock", source)
        self.assertIn("guardian-collector.service", source)
        self.assertIn("guardian-collector", source)
        self.assertIn("systemctl enable guardian-runtime.service", source)
        self.assertIn("systemctl enable guardian-collector.service", source)
        self.assertNotIn("systemctl enable guardian-broker.service", source)
        self.assertNotIn("systemctl enable guardian-reserve-broker.service", source)


if __name__ == "__main__":
    unittest.main()
