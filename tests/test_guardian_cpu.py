import tempfile
import unittest
from pathlib import Path

from src.guardian_cpu import (
    CpuAttributionEvaluator,
    CpuAttributionPolicy,
    CpuPolicy,
    CpuRiskEvaluator,
    collect_cpu_sample,
    collect_scheduler_probe,
    parse_cpu_max,
    parse_cpu_psi,
    parse_cpu_stat,
    parse_loadavg,
    parse_proc_stat,
)


def cpu_sample(
    *,
    total_ticks: int,
    idle_ticks: int,
    observed_ns: int,
    psi_some: float = 0.0,
    psi_full: float = 0.0,
    usage_usec: int = 0,
    objects=None,
    quality_flags=None,
):
    return {
        "observed_monotonic_ns": observed_ns,
        "host": {
            "stat": {"aggregate": {"total_ticks": total_ticks, "idle_ticks": idle_ticks}},
            "loadavg": {"load1": 0.0},
            "logical_cpus": 2,
        },
        "psi": {"some": {"avg10": psi_some}, "full": {"avg10": psi_full}},
        "cgroup": {"cpu_stat": {"usage_usec": usage_usec, "nr_throttled": 0}},
        "objects": objects or [],
        "quality": {"status": "ok" if not quality_flags else "degraded", "flags": quality_flags or []},
    }


def cpu_object(object_id: str, path: str, usage_usec: int, *, confidence: str = "high", errors=None):
    return {
        "kind": "container",
        "id": object_id,
        "name": object_id,
        "cgroup_path": path,
        "cpu_stat": {"usage_usec": usage_usec},
        "mapping_confidence": confidence,
        "mapping_errors": errors or [],
    }


class CpuParserTests(unittest.TestCase):
    def test_scheduler_probe_is_bounded_and_read_only(self):
        result = collect_scheduler_probe(0.1)
        self.assertTrue(result["valid"])
        self.assertGreaterEqual(result["elapsed_ms"], 0.1)
        self.assertFalse(collect_scheduler_probe(0)["valid"])

    def test_parse_proc_stat_and_loadavg(self):
        parsed = parse_proc_stat(
            "cpu  10 1 20 100 2 3 4 5 6 7\n"
            "cpu0 5 0 10 50 1 1 2 2 3 3\n"
            "cpu1 5 1 10 50 1 2 2 3 3 4\n"
            "intr 1\n"
        )
        self.assertTrue(parsed["valid"])
        self.assertEqual(parsed["logical_cpus"], 2)
        self.assertEqual(parsed["aggregate"]["total_ticks"], 158)
        self.assertEqual(parsed["aggregate"]["idle_ticks"], 102)
        self.assertEqual(parse_loadavg("1.50 2.00 3.00 4/128 99\n")["runnable_tasks"], 4)

    def test_parse_cpu_cgroup_files(self):
        self.assertEqual(parse_cpu_stat("usage_usec 123\nnr_periods 7\n"), {"usage_usec": 123, "nr_periods": 7})
        self.assertEqual(parse_cpu_max("50000 100000"), {"quota_us": 50000, "period_us": 100000, "valid": True})
        self.assertEqual(parse_cpu_max("max 100000")["quota_us"], None)
        self.assertEqual(parse_cpu_psi("some avg10=1.25 total=4\n")["some"]["avg10"], 1.25)

    def test_collect_cpu_sample_reads_host_and_object_cgroups(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "pressure").mkdir()
            (root / "stat").write_text("cpu 10 0 20 100 0 0 0 0 0 0\ncpu0 5 0 10 50\n")
            (root / "loadavg").write_text("0.50 0.40 0.30 2/100 44\n")
            (root / "pressure" / "cpu").write_text("some avg10=0.20 avg60=0.10\nfull avg10=0.01 avg60=0.00\n")
            host_cgroup = root / "host-cgroup"
            host_cgroup.mkdir()
            (host_cgroup / "cpu.stat").write_text("usage_usec 900\nnr_throttled 1\n")
            (host_cgroup / "cpu.max").write_text("max 100000\n")
            (host_cgroup / "cpu.pressure").write_text("some avg10=0.10\nfull avg10=0.00\n")
            object_cgroup = root / "object-cgroup"
            object_cgroup.mkdir()
            (object_cgroup / "cpu.stat").write_text("usage_usec 300\n")
            (object_cgroup / "cpu.max").write_text("50000 100000\n")
            (object_cgroup / "cpu.pressure").write_text("some avg10=0.30\nfull avg10=0.02\n")

            sample = collect_cpu_sample(
                proc_root=root,
                cgroup_root=host_cgroup,
                object_registry=[
                    {"id": "obj-1", "cgroup_path": str(object_cgroup), "mapping_confidence": "high"}
                ],
            )
            self.assertTrue(sample["host"]["stat"]["valid"])
            self.assertEqual(sample["host"]["logical_cpus"], 1)
            self.assertEqual(sample["cgroup"]["cpu_stat"]["usage_usec"], 900)
            self.assertEqual(sample["objects"][0]["cpu_stat"]["usage_usec"], 300)
            self.assertEqual(sample["quality"]["status"], "ok")


class CpuRiskEvaluatorTests(unittest.TestCase):
    def test_high_cpu_requires_samples_and_dwell(self):
        evaluator = CpuRiskEvaluator(
            CpuPolicy(warning_for_seconds=2, critical_for_seconds=1, required_samples=2)
        )
        first = evaluator.evaluate(cpu_sample(total_ticks=1000, idle_ticks=100, observed_ns=0), now=0)
        self.assertEqual(first["state"], "degraded_observability")
        second = evaluator.evaluate(
            cpu_sample(total_ticks=1200, idle_ticks=120, observed_ns=1_000_000_000, psi_some=2.0),
            now=1,
        )
        self.assertEqual(second["candidate_state"], "warning")
        self.assertEqual(second["state"], "normal")
        third = evaluator.evaluate(
            cpu_sample(total_ticks=1400, idle_ticks=140, observed_ns=3_000_000_000, psi_some=2.0),
            now=3,
        )
        self.assertEqual(third["state"], "warning")

    def test_short_cpu_burst_recovers_without_alert(self):
        evaluator = CpuRiskEvaluator(CpuPolicy(warning_for_seconds=5, required_samples=1))
        evaluator.evaluate(cpu_sample(total_ticks=1000, idle_ticks=100, observed_ns=0), now=0)
        burst = evaluator.evaluate(
            cpu_sample(total_ticks=1200, idle_ticks=120, observed_ns=1_000_000_000, psi_some=2.0),
            now=1,
        )
        self.assertEqual(burst["candidate_state"], "warning")
        self.assertEqual(burst["state"], "normal")
        recovered = evaluator.evaluate(
            cpu_sample(total_ticks=1400, idle_ticks=400, observed_ns=1_500_000_000),
            now=1.5,
        )
        self.assertEqual(recovered["state"], "normal")

    def test_missing_cpu_signal_is_degraded_and_never_critical(self):
        evaluator = CpuRiskEvaluator(CpuPolicy(required_samples=1))
        result = evaluator.evaluate(
            cpu_sample(
                total_ticks=1200,
                idle_ticks=100,
                observed_ns=1_000_000_000,
                quality_flags=["cpu_pressure_missing"],
            ),
            now=1,
        )
        self.assertEqual(result["state"], "degraded_observability")
        self.assertIn("cpu_pressure_missing", result["quality_flags"])


class CpuAttributionEvaluatorTests(unittest.TestCase):
    def test_stable_leading_object_is_confirmed(self):
        evaluator = CpuAttributionEvaluator(CpuAttributionPolicy(min_host_contribution_percent=20, min_lead_margin=0.15))
        objects = [cpu_object("a", "/a", 100), cpu_object("b", "/b", 100)]
        evaluator.evaluate(cpu_sample(total_ticks=1, idle_ticks=1, observed_ns=0, objects=objects))
        result = evaluator.evaluate(
            cpu_sample(
                total_ticks=2,
                idle_ticks=1,
                observed_ns=1_000_000_000,
                objects=[cpu_object("a", "/a", 900), cpu_object("b", "/b", 300)],
            )
        )
        self.assertEqual(result["state"], "TARGET_CONFIRMED")
        self.assertEqual(result["target"]["id"], "a")
        self.assertGreater(result["target"]["lead_margin_percent"], 15)

    def test_close_candidates_are_ambiguous(self):
        evaluator = CpuAttributionEvaluator()
        evaluator.evaluate(
            cpu_sample(
                total_ticks=1,
                idle_ticks=1,
                observed_ns=0,
                objects=[cpu_object("a", "/a", 100), cpu_object("b", "/b", 100)],
            )
        )
        result = evaluator.evaluate(
            cpu_sample(
                total_ticks=2,
                idle_ticks=1,
                observed_ns=1_000_000_000,
                objects=[cpu_object("a", "/a", 650), cpu_object("b", "/b", 550)],
            )
        )
        self.assertEqual(result["state"], "AMBIGUOUS_TARGET")
        self.assertIn("cpu_candidate_lead_margin_too_small", result["reason_codes"])

    def test_mapping_change_or_missing_identity_degrades(self):
        evaluator = CpuAttributionEvaluator()
        evaluator.evaluate(
            cpu_sample(
                total_ticks=1,
                idle_ticks=1,
                observed_ns=0,
                objects=[cpu_object("a", "/a", 100)],
            )
        )
        changed = evaluator.evaluate(
            cpu_sample(
                total_ticks=2,
                idle_ticks=1,
                observed_ns=1_000_000_000,
                objects=[cpu_object("a", "/recreated", 500)],
            )
        )
        self.assertEqual(changed["state"], "AMBIGUOUS_TARGET")

        missing = CpuAttributionEvaluator().evaluate(
            cpu_sample(
                total_ticks=2,
                idle_ticks=1,
                observed_ns=1_000_000_000,
                objects=[cpu_object("a", "/a", 500, confidence="low", errors=["container_cgroup_path_missing"])],
            )
        )
        self.assertEqual(missing["state"], "DEGRADED_OBSERVABILITY")


if __name__ == "__main__":
    unittest.main()
