"""Tests for the one-command host setup script and smoke tool.

These check the safety contract in the script source (they do not apply
anything): apply is gated on the local-disposable marker, sshd is never
restarted, brokers stay untouched, and the known packaging gotchas (earlyoom
ExecStart override, per-file sysctl apply, journal group) are handled.

No test here may invoke --apply with a valid environment marker: this suite
runs as root on the disposable host, and that combination really mutates.
Gating is therefore asserted against plan-mode output and die() messages.
"""

import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SETUP = ROOT / "scripts" / "guardian-host-setup.sh"
SMOKE = ROOT / "scripts" / "guardian-smoke"


def code_only(source: str) -> str:
    """Strip comment lines so docstring mentions don't trip assertions."""
    return "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )


class GuardianHostSetupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SETUP.read_text(encoding="utf-8")
        cls.code = code_only(cls.source)

    def test_default_command_is_a_non_mutating_plan(self):
        result = subprocess.run(
            [str(SETUP)], check=False, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("mutations: no", result.stdout)

    def test_apply_requires_local_disposable_marker(self):
        result = subprocess.run(
            [str(SETUP), "--apply"], check=False, capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--apply requires --environment local-disposable", result.stderr)

    def test_apply_rejects_other_environments(self):
        result = subprocess.run(
            [str(SETUP), "--apply", "--environment", "production"],
            check=False, capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("local-disposable", result.stderr)

    def test_plan_mode_lists_skipped_steps(self):
        result = subprocess.run(
            [str(SETUP), "--skip", "earlyoom,sysctl"],
            check=False, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("skipped: earlyoom,sysctl", result.stdout)

    def test_script_never_restarts_sshd(self):
        self.assertNotIn("restart ssh", self.code)
        self.assertNotIn("try-restart ssh", self.code)

    def test_script_never_touches_brokers_or_guardian_json(self):
        # guardian.json appears only in the documentation plan-text heredoc
        # ("guardian.json and brokers untouched"); the code must not modify it.
        json_lines = [ln for ln in self.code.splitlines() if "guardian.json" in ln]
        self.assertTrue(json_lines)  # documentation mention is present
        for ln in json_lines:
            self.assertIn("untouched", ln, f"suspicious guardian.json line: {ln}")
        self.assertNotIn("broker.enabled", self.code)
        self.assertNotIn("guardian-broker", self.code)
        self.assertNotIn("reserve-broker", self.code)

    def test_known_gotchas_are_handled(self):
        # earlyoom packaged-unit literal-args gotcha -> override.conf
        self.assertIn("earlyoom.service.d/override.conf", self.code)
        # sysctl --system silently skips files -> per-file apply
        self.assertIn("sysctl -p", self.code)
        # journal membership for the earlyoom kill watcher
        self.assertIn("systemd-journal", self.code)
        # apply refuses to run without the core install
        self.assertIn("install root missing", self.code)

    def test_skip_flag_is_accepted_by_the_parser(self):
        result = subprocess.run(
            [str(SETUP), "--skip", "earlyoom,sysctl"],
            check=False, capture_output=True, text=True,
        )
        self.assertNotIn("unknown argument", result.stderr)

    def test_deployed_templates_exist(self):
        deploy = ROOT / "deploy" / "guardian"
        for name in (
            "earlyoom-default.conf",
            "earlyoom-systemd-override.conf",
            "user-oom-balance.conf",
            "guardian-oom-defense.conf",
            "ssh-oom-defense.conf",
            "transport-defense-sysctl.conf",
        ):
            self.assertTrue((deploy / name).is_file(), f"missing {name}")
        # the earlyoom args template carries the validated trigger on a
        # EARLYOOM_ARGS= line the installer extracts into /etc/default/earlyoom
        args = (deploy / "earlyoom-default.conf").read_text(encoding="utf-8")
        args_line = next(
            ln for ln in args.splitlines() if ln.startswith("EARLYOOM_ARGS=")
        )
        self.assertIn("-m 10", args_line)
        self.assertIn("-s 100", args_line)
        self.assertIn("guardian-runtime", args_line)
        self.assertIn("--avoid", args_line)


class GuardianSmokeTests(unittest.TestCase):
    def test_help_runs(self):
        result = subprocess.run(
            [str(SMOKE), "--help"], check=False, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("Checks", result.stdout)

    def test_unknown_argument_rejected(self):
        result = subprocess.run(
            [str(SMOKE), "--bogus"], check=False, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown argument", result.stderr)

    def test_smoke_never_restarts_anything(self):
        source = SMOKE.read_text(encoding="utf-8")
        self.assertNotIn("systemctl restart", source)
        self.assertNotIn("systemctl start", source)
        self.assertNotIn("systemctl stop", source)


if __name__ == "__main__":
    unittest.main()
