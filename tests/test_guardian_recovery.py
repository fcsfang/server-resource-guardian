import tempfile
import unittest
from pathlib import Path

from src.guardian_recovery import (
    CooldownLedger,
    BusinessRecoveryObservation,
    BusinessRecoveryPolicy,
    HostRecoveryObservation,
    HostRecoveryPolicy,
    RecoveryObservation,
    RecoveryPolicy,
    assess_business_recovery,
    assess_host_mitigation,
    assess_recovery,
    assess_two_layer_recovery,
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

    def test_graceful_stop_does_not_treat_forced_kill_as_recovered(self):
        forced = assess_recovery(
            RecoveryPolicy("graceful_stop"),
            RecoveryObservation("abcdef123456", True, False, "exited", "normal", 5.0, exit_code=137),
        )
        self.assertEqual(forced.state, "failed")
        self.assertEqual(forced.reason_codes, ("target_force_killed",))

        term = assess_recovery(
            RecoveryPolicy("graceful_stop"),
            RecoveryObservation("abcdef123456", True, False, "exited", "normal", 2.0, exit_code=143),
        )
        self.assertEqual(term.state, "recovered")
        self.assertEqual(term.reason_codes, ("target_stopped_sigterm",))

    def test_cooldown_and_action_limit_fail_closed(self):
        ledger = CooldownLedger()
        self.assertEqual(ledger.allow(0.0, 10.0, 2, 3600.0), (True, "allowed"))
        ledger.record(0.0, True)
        self.assertEqual(ledger.allow(5.0, 10.0, 2, 3600.0), (False, "cooldown_active"))
        self.assertEqual(ledger.allow(11.0, 10.0, 1, 3600.0), (False, "action_limit_reached"))
        ledger.record(11.0, False)
        ledger.record(22.0, False)
        self.assertTrue(ledger.tripped())

    def test_cooldown_ledger_round_trips_to_json(self):
        ledger = CooldownLedger(last_action_at=12.5, action_times=[1.0, 12.5], consecutive_failures=1)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ledger.json"
            ledger.save(path)
            restored = CooldownLedger.load(path)
        self.assertEqual(restored.to_dict(), ledger.to_dict())

    def host_observation(self, **overrides):
        values = {
            "before_available_percent": 5.0,
            "after_available_percent": 25.0,
            "before_psi_full_avg10": 1.0,
            "after_psi_full_avg10": 0.0,
            "before_oom_events": 0,
            "after_oom_events": 0,
            "after_risk_state": "recovered",
            "observed_after_seconds": 5.0,
        }
        values.update(overrides)
        return HostRecoveryObservation(**values)

    def business_observation(self, **overrides):
        values = {
            "target_id": "abcdef123456",
            "target_present": True,
            "target_running": True,
            "health_status": "healthy",
            "probe_ok": True,
            "observed_after_seconds": 5.0,
        }
        values.update(overrides)
        return BusinessRecoveryObservation(**values)

    def test_host_mitigation_requires_improvement_and_no_new_oom(self):
        result = assess_host_mitigation(HostRecoveryPolicy(), self.host_observation())
        self.assertEqual(result.state, "MITIGATED")
        self.assertTrue(result.recovered)

        new_oom = assess_host_mitigation(
            HostRecoveryPolicy(), self.host_observation(after_oom_events=1)
        )
        self.assertEqual(new_oom.reason_codes, ("new_oom_after_action",))

    def test_host_mitigation_fails_closed_on_missing_or_still_critical_evidence(self):
        missing = assess_host_mitigation(
            HostRecoveryPolicy(), self.host_observation(after_psi_full_avg10=None)
        )
        self.assertEqual(missing.reason_codes, ("host_recovery_observation_incomplete",))
        critical = assess_host_mitigation(
            HostRecoveryPolicy(), self.host_observation(after_risk_state="critical")
        )
        self.assertEqual(critical.reason_codes, ("host_risk_still_actionable",))

    def test_business_recovery_requires_health_and_probe(self):
        recovered = assess_business_recovery(
            BusinessRecoveryPolicy(), self.business_observation(), expected_target_id="abcdef123456"
        )
        self.assertEqual(recovered.state, "BUSINESS_RECOVERED")
        unhealthy = assess_business_recovery(
            BusinessRecoveryPolicy(), self.business_observation(health_status="unhealthy"), expected_target_id="abcdef123456"
        )
        self.assertEqual(unhealthy.state, "BUSINESS_DEGRADED")
        missing_probe = assess_business_recovery(
            BusinessRecoveryPolicy(), self.business_observation(probe_ok=None), expected_target_id="abcdef123456"
        )
        self.assertEqual(missing_probe.reason_codes, ("business_probe_not_healthy",))

    def test_two_layer_result_does_not_equate_stopped_target_with_business_recovery(self):
        result = assess_two_layer_recovery(
            HostRecoveryPolicy(),
            self.host_observation(),
            BusinessRecoveryPolicy(),
            self.business_observation(target_running=False, health_status="exited", probe_ok=False),
            expected_target_id="abcdef123456",
        )
        self.assertEqual(result.host_state, "MITIGATED")
        self.assertEqual(result.business_state, "BUSINESS_DEGRADED")
        self.assertEqual(result.overall_state, "MITIGATED")
        self.assertTrue(result.host_mitigated)
        self.assertFalse(result.business_recovered)

    def test_two_layer_result_is_business_recovered_only_when_both_layers_pass(self):
        result = assess_two_layer_recovery(
            HostRecoveryPolicy(),
            self.host_observation(),
            BusinessRecoveryPolicy(),
            self.business_observation(),
            expected_target_id="abcdef123456",
        )
        self.assertEqual(result.overall_state, "BUSINESS_RECOVERED")
        self.assertTrue(result.host_mitigated)
        self.assertTrue(result.business_recovered)

    def test_cpu_stop_recovery_separates_host_mitigation_from_business_health(self):
        host = self.host_observation(
            before_available_percent=None,
            after_available_percent=None,
            before_psi_full_avg10=None,
            after_psi_full_avg10=None,
            before_oom_events=None,
            after_oom_events=None,
            resource_kind="cpu",
            before_resource_state="critical",
            after_resource_state="normal",
        )
        business = self.business_observation(target_running=False, health_status="exited", probe_ok=None)
        result = assess_two_layer_recovery(
            HostRecoveryPolicy(),
            host,
            BusinessRecoveryPolicy(action="graceful_stop"),
            business,
            expected_target_id="abcdef123456",
        )
        self.assertEqual(result.overall_state, "MITIGATED")
        self.assertTrue(result.host_mitigated)
        self.assertFalse(result.business_recovered)
        self.assertIn("business_health_check_not_configured", result.reason_codes)

    def test_stopped_target_can_only_claim_business_recovery_with_explicit_probe(self):
        business = self.business_observation(target_running=False, health_status="exited", probe_ok=True)
        result = assess_business_recovery(
            BusinessRecoveryPolicy(action="graceful_stop"),
            business,
            expected_target_id="abcdef123456",
        )
        self.assertEqual(result.state, "BUSINESS_RECOVERED")
        self.assertEqual(result.reason_codes, ("business_health_confirmed",))


if __name__ == "__main__":
    unittest.main()
