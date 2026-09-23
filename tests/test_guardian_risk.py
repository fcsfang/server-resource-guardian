import copy
import unittest

from src.guardian_config import safe_defaults, validate_config
from src.guardian_observer import collect_observation
from src.guardian_risk import CompositeRiskEvaluator


def build_test_config(*, required_samples=1, critical_for=1.0, trend_warning=1048576, trend_critical=16777216):
    value = safe_defaults().as_dict()
    value["risk"]["memory"]["critical_for_seconds"] = critical_for
    value["risk"]["memory"]["warning_for_seconds"] = critical_for
    value["risk"]["composite"]["required_samples"] = required_samples
    value["risk"]["composite"]["trend_warning_bytes_per_second"] = trend_warning
    value["risk"]["composite"]["trend_critical_bytes_per_second"] = trend_critical
    return validate_config(value, source="<test>")


def observation(
    *,
    available_ratio=50.0,
    available_bytes=1000,
    oom=0,
    oom_kill=0,
    psi_some=0.0,
    psi_full=0.0,
    swap_total=0,
    swap_ratio=None,
    monotonic_ns=1_000_000_000,
    quality=None,
):
    return {
        "observed_at": "2026-09-20T00:00:00Z",
        "observed_monotonic_ns": monotonic_ns,
        "memory": {
            "available_ratio_percent": available_ratio,
            "available_bytes": available_bytes,
            "swap_total_bytes": swap_total,
            "swap_used_ratio_percent": swap_ratio,
        },
        "psi": {"memory": {"some": {"avg10": psi_some}, "full": {"avg10": psi_full}}},
        "cgroup": {"memory_events": {"oom": oom, "oom_kill": oom_kill}},
        "docker": {"available": True, "containers": []},
        "quality": quality if quality is not None else {"status": "ok", "flags": []},
    }


class CompositeRiskEvaluatorTests(unittest.TestCase):
    def test_counter_baseline_does_not_create_oom_risk(self):
        evaluator = CompositeRiskEvaluator(build_test_config())
        result = evaluator.evaluate(observation(oom=3), now=0.0)
        self.assertEqual(result["candidate_state"], "normal")
        self.assertEqual(result["oom_events_delta"], None)
        self.assertNotIn("new_cgroup_memory_oom_event", result["reasons"])

    def test_new_oom_counter_delta_is_critical(self):
        evaluator = CompositeRiskEvaluator(build_test_config())
        evaluator.evaluate(observation(oom=3), now=0.0)
        result = evaluator.evaluate(observation(oom=4, monotonic_ns=2_000_000_000), now=1.0)
        self.assertEqual(result["candidate_state"], "critical")
        self.assertEqual(result["oom_events_delta"], 1)
        self.assertIn("new_cgroup_memory_oom_event", result["reasons"])

    def test_non_oom_cgroup_counter_is_not_oom_event(self):
        evaluator = CompositeRiskEvaluator(build_test_config())
        first = observation()
        first["cgroup"]["memory_events"] = {"high": 1, "max": 2}
        second = copy.deepcopy(first)
        second["cgroup"]["memory_events"] = {"high": 2, "max": 3}
        second["observed_monotonic_ns"] = 2_000_000_000
        evaluator.evaluate(first, now=0.0)
        result = evaluator.evaluate(second, now=1.0)
        self.assertEqual(result["candidate_state"], "normal")
        self.assertEqual(result["oom_events_delta"], 0)

    def test_two_independent_pressure_signals_raise_critical_candidate(self):
        evaluator = CompositeRiskEvaluator(
            build_test_config(trend_warning=100, trend_critical=200)
        )
        evaluator.evaluate(observation(available_bytes=1000), now=0.0)
        result = evaluator.evaluate(
            observation(
                available_bytes=700,
                psi_full=1.0,
                monotonic_ns=2_000_000_000,
            ),
            now=1.0,
        )
        self.assertEqual(result["candidate_state"], "critical")
        self.assertIn("memory_growth_critical", result["signal_summary"]["critical_support"])
        self.assertIn("memory_psi_full_critical", result["signal_summary"]["critical_support"])

    def test_low_headroom_without_support_is_critical_host_risk(self):
        evaluator = CompositeRiskEvaluator(build_test_config())
        result = evaluator.evaluate(observation(available_ratio=5.0), now=0.0)
        self.assertEqual(result["candidate_state"], "critical")
        self.assertNotIn("memory_psi_full_critical", result["signal_summary"]["critical_support"])

    def test_missing_docker_attribution_does_not_mask_host_memory_risk(self):
        evaluator = CompositeRiskEvaluator(build_test_config(critical_for=0.1))
        first = observation(available_ratio=5.0)
        first["docker"]["available"] = False
        first["quality"] = {"status": "degraded", "flags": ["docker_observation_unavailable"]}
        evaluator.evaluate(first, now=0.0)
        second = observation(available_ratio=5.0, monotonic_ns=2_000_000_000)
        second["docker"]["available"] = False
        second["quality"] = {
            "status": "degraded",
            "flags": ["docker_observation_unavailable", "container_collector_degraded"],
        }
        result = evaluator.evaluate(second, now=0.2)
        self.assertEqual(result["state"], "critical")
        self.assertNotIn("docker_observation_unavailable", result["quality_flags"])

    def test_counter_reset_fails_closed(self):
        evaluator = CompositeRiskEvaluator(build_test_config())
        evaluator.evaluate(observation(oom=4), now=0.0)
        result = evaluator.evaluate(observation(oom=1, monotonic_ns=2_000_000_000), now=1.0)
        self.assertEqual(result["state"], "degraded_observability")
        self.assertIn("oom_counter_reset", result["quality_flags"])

    def test_quality_and_sample_gap_fail_closed(self):
        evaluator = CompositeRiskEvaluator(build_test_config())
        evaluator.evaluate(observation(), now=0.0)
        result = evaluator.evaluate(
            observation(monotonic_ns=20_000_000_000),
            now=1.0,
        )
        self.assertEqual(result["state"], "degraded_observability")
        self.assertIn("sample_gap_too_large", result["quality_flags"])

    def test_missing_quality_metadata_fails_closed(self):
        evaluator = CompositeRiskEvaluator(build_test_config())
        sample = observation()
        sample.pop("quality")
        result = evaluator.evaluate(sample, now=0.0)
        self.assertEqual(result["state"], "degraded_observability")
        self.assertIn("observation_quality_missing", result["quality_flags"])

    def test_collect_observation_exposes_monotonic_time_and_quality(self):
        observation_value = collect_observation()
        self.assertIsInstance(observation_value["observed_monotonic_ns"], int)
        self.assertIn(observation_value["quality"]["status"], {"ok", "degraded"})
        self.assertIsInstance(observation_value["quality"]["flags"], list)


if __name__ == "__main__":
    unittest.main()
