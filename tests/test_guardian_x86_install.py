import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-guardian-x86.sh"
DEPLOY = ROOT / "deploy" / "guardian-x86"


class GuardianX86InstallTests(unittest.TestCase):
    def test_default_command_is_a_non_mutating_observe_plan(self):
        result = subprocess.run(
            [str(SCRIPT), "--repository", str(ROOT)],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertIn("mutations: no", result.stdout)
        self.assertIn("runtime mode: observe only", result.stdout)
        self.assertIn("automatic actions: disabled", result.stdout)
        self.assertIn("host services: SSH, Docker, networking and login services are not modified", result.stdout)

    def test_apply_and_rollback_require_explicit_environment(self):
        apply_result = subprocess.run([str(SCRIPT), "--apply"], check=False, capture_output=True, text=True)
        self.assertNotEqual(apply_result.returncode, 0)
        self.assertIn("require --environment x86-observe", apply_result.stderr)

        rollback_result = subprocess.run(
            [str(SCRIPT), "--rollback", "/var/backups/guardian-x86-installer/example"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(rollback_result.returncode, 0)
        self.assertIn("require --environment x86-observe", rollback_result.stderr)

    def test_installer_keeps_actions_closed_and_does_not_reconfigure_host_services(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('get("mode") != "observe"', source)
        self.assertIn('get("enabled") is not False', source)
        self.assertIn('get("allow")', source)
        self.assertIn("guardian-x86-preflight.py", source)
        self.assertNotIn("systemctl enable guardian-broker.service", source)
        self.assertNotIn("systemctl enable guardian-reserve-broker.service", source)
        self.assertNotIn("systemctl try-restart", source)
        self.assertNotIn("systemctl restart ssh", source)
        self.assertNotIn("systemctl restart docker", source)
        self.assertNotIn("guardian-rescue-member.conf", source)
        self.assertIn('is_managed_path "$path"', source)

    def test_x86_units_do_not_pin_fixed_cpu_ids(self):
        for path in DEPLOY.iterdir():
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("AllowedCPUs=", source, path.name)
        runtime = (DEPLOY / "guardian-runtime.service").read_text(encoding="utf-8")
        collector = (DEPLOY / "guardian-collector.service").read_text(encoding="utf-8")
        self.assertIn("Slice=guardian-runtime.slice", runtime)
        self.assertIn("Slice=guardian-collector.slice", collector)
        self.assertNotIn("SupplementaryGroups=guardian-broker", runtime)

    def test_x86_tmpfiles_has_no_action_service_directories(self):
        source = (DEPLOY / "guardian.tmpfiles").read_text(encoding="utf-8")
        self.assertNotIn("guardian-broker", source)
        self.assertNotIn("reserve-broker", source)


if __name__ == "__main__":
    unittest.main()
