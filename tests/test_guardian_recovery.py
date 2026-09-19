import unittest

from src.guardian_recovery import (
    CooldownLedger,
    RecoveryObservation,
    RecoveryPolicy,
    assess_recovery,
)


class GuardianRecoveryTests(unittest.TestCase):
    def test_stop_is_recovered_when_target_is_no_longer_running(self):
        result = assess_recovery(
            RecoveryPolicy("graceful_stop"),
            RecoveryObservation("abcdef123456", True, False, "exited", "normal", 2.0),
        )
        self.assertEqual(result.state, "recovered")
        self.assertTrue(result.recovered)

    def test_restart_requires_running_healthy_target_and_normal_risk(self):
        pending = assess_recovery(
            RecoveryPolicy("restart"),
            RecoveryObservation("abcdef123456", True, True, "starting", "critical", 2.0),
        )
        self.assertEqual(pending.state, "pending")

        recovered = assess_recovery(
            RecoveryPolicy("restart"),
            RecoveryObservation("abcdef123456", True, True, "healthy", "normal", 3.0),
        )
        self.assertEqual(recovered.state, "recovered")

    def test_recovery_window_and_unknown_action_fail_closed(self):
        expired = assess_recovery(
            RecoveryPolicy("restart", max_wait_seconds=5.0),
            RecoveryObservation("abcdef123456", True, True, "starting", "critical", 6.0),
        )
        self.assertEqual(expired.reason_codes, ("recovery_window_expired",))

        unknown = assess_recovery(
            RecoveryPolicy("notify"),
            RecoveryObservation("abcdef123456", True, True, "healthy", "normal", 1.0),
        )
        self.assertEqual(unknown.reason_codes, ("unsupported_recovery_action",))

    def test_cooldown_and_action_limit_fail_closed(self):
        ledger = CooldownLedger()
        self.assertEqual(ledger.allow(0.0, 10.0, 2, 3600.0), (True, "allowed"))
        ledger.record(0.0, True)
        self.assertEqual(ledger.allow(5.0, 10.0, 2, 3600.0), (False, "cooldown_active"))
        self.assertEqual(ledger.allow(11.0, 10.0, 1, 3600.0), (False, "action_limit_reached"))
        ledger.record(11.0, False)
        ledger.record(22.0, False)
        self.assertTrue(ledger.tripped())


if __name__ == "__main__":
    unittest.main()
