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

    def test_maintenance_memory_protection_is_allocated_through_user_slice_ancestors(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('user_slice_parent_dropin_dir="/etc/systemd/system/user.slice.d"', source)
        self.assertIn('MemoryMin=128M\nMemoryLow=256M', source)
        self.assertIn('backup_if_present "${user_slice_parent_dropin_dir}/guardian-maintenance-user-slice.conf"', source)
        self.assertIn('backup_if_present "${user_slice_dropin_dir}/guardian-maintenance.conf"', source)

    def test_script_actually_installs_the_unit_files_it_verifies(self):
        # Regression for the first real-server drill (2026-09-24): the script
        # verified, enabled, and restarted 8 unit files but never copied them
        # to /etc/systemd/system - every host without a pre-existing manual
        # deployment failed with "Unit guardian-collector.service not found".
        # Each managed unit must have an install line from deploy/guardian/.
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn(
            'install -o root -g root -m 0644 "${repository}/deploy/guardian/${unit}" "/etc/systemd/system/${unit}"',
            source,
        )
        # ... and the loop must cover the units it later enables
        self.assertIn('for unit in "${managed_units[@]}"; do', source)


if __name__ == "__main__":
    unittest.main()
