import unittest
from pathlib import Path

from scripts.guardian_maintenance_status import RESOURCE_DOMAINS


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
        self.assertEqual(workload["CPUWeight"], "1")
        self.assertGreater(int(rescue["CPUWeight"]), int(workload["CPUWeight"]))
        self.assertGreater(int(rescue["IOWeight"]), int(workload["IOWeight"]))
        self.assertEqual(rescue["AllowedCPUs"], "0")
        self.assertEqual(workload["AllowedCPUs"], "1")
        self.assertEqual(rescue["MemoryMin"], "512M")
        self.assertEqual(rescue["MemoryLow"], "768M")
        self.assertEqual(rescue["TasksMax"], "1024")
        self.assertEqual(workload["MemoryMax"], "512M")
        self.assertEqual(workload["MemorySwapMax"], "0")
        self.assertEqual(workload["TasksMax"], "256")

    def test_runtime_is_non_root_and_does_not_use_docker_group(self):
        source = (DEPLOY / "guardian-runtime.service").read_text(encoding="utf-8")
        self.assertIn("User=guardian", source)
        self.assertIn("SupplementaryGroups=guardian-shared guardian-broker", source)
        self.assertNotIn("SupplementaryGroups=docker", source)
        self.assertIn("Slice=rescue.slice", source)
        self.assertIn("systemd-logind.service", source)
        self.assertIn("systemd-journald.service", source)
        self.assertIn("docker.service", source)
        self.assertIn("containerd.service", source)
        self.assertIn("RestrictAddressFamilies=AF_UNIX", source)
        x86_deploy = ROOT / "deploy" / "guardian-x86"
        x86_runtime = (x86_deploy / "guardian-runtime.service").read_text(encoding="utf-8")
        self.assertIn("Slice=guardian-runtime.slice", x86_runtime)
        runtime_slice = section_values(x86_deploy / "guardian-runtime.slice", "Slice")
        self.assertEqual(runtime_slice["CPUWeight"], "500")
        self.assertEqual(runtime_slice["TasksMax"], "256")

    def test_maintenance_helpers_are_fixed_scope(self):
        installer = (ROOT / "scripts" / "install-guardian-local.sh").read_text(encoding="utf-8")
        self.assertIn("guardian-maintenance-pressure", installer)
        self.assertIn("guardian-release-emergency-space", installer)
        pressure = (ROOT / "scripts" / "guardian_maintenance_pressure.py").read_text(encoding="utf-8")
        self.assertIn('choices=("cpu", "memory", "disk", "mixed")', pressure)
        reserve = (ROOT / "scripts" / "guardian_reserve_space.py").read_text(encoding="utf-8")
        self.assertIn("refusing any deletion", reserve)
        self.assertEqual(
            RESOURCE_DOMAINS,
            ("rescue.slice", "workload.slice", "guardian-observer.slice"),
        )

    def test_tmpfiles_preserves_shared_state_boundary(self):
        source = (DEPLOY / "guardian.tmpfiles").read_text(encoding="utf-8")
        self.assertIn("d /var/lib/guardian/shared 2770 root guardian-shared -", source)
        self.assertIn("d /var/lib/guardian/runtime 0700 guardian guardian -", source)
        self.assertIn("d /run/guardian-runtime 0750 guardian guardian -", source)

    def test_journald_and_audit_retention_boundaries_are_explicit(self):
        journald = (DEPLOY / "guardian-journald.conf").read_text(encoding="utf-8")
        self.assertIn("SystemMaxUse=200M", journald)
        self.assertIn("RuntimeMaxUse=64M", journald)
        self.assertIn("RateLimitBurst=200", journald)
        logrotate = (DEPLOY / "guardian-audit.logrotate").read_text(encoding="utf-8")
        self.assertIn("size 50M", logrotate)
        self.assertIn("rotate 7", logrotate)

if __name__ == "__main__":
    unittest.main()
