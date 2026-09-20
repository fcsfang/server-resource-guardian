import json
import tempfile
import unittest
from pathlib import Path

from src.guardian_attribution import (
    AttributionPolicy,
    ObjectAttributionEvaluator,
    collect_object_registry,
)


def host(available, monotonic_ns):
    return {
        "memory": {"available_bytes": available},
        "observed_monotonic_ns": monotonic_ns,
    }


def registry(*objects, status="ok", errors=None):
    return {"status": status, "objects": list(objects), "errors": errors or []}


def object_sample(stable_id, *, name=None, current=100, path=None, oom=0, errors=None):
    return {
        "kind": "container",
        "id": stable_id,
        "name": name or stable_id,
        "mapping_confidence": "high" if path else "low",
        "cgroup_path": path,
        "memory_current_bytes": current,
        "memory_events": {"oom": oom, "oom_kill": 0},
        "mapping_errors": errors or [],
    }


class GuardianAttributionTests(unittest.TestCase):
    def test_single_object_is_confirmed_only_after_growth_sample(self):
        evaluator = ObjectAttributionEvaluator(
            AttributionPolicy(min_host_contribution_percent=20, min_lead_margin=0.15)
        )
        first = registry(object_sample("a", name="leak", current=100, path="/docker-a.scope"))
        baseline = evaluator.evaluate(host(1000, 1_000_000_000), first, now=0.0)
        self.assertEqual(baseline["state"], "DEGRADED_OBSERVABILITY")
        second = registry(object_sample("a", name="leak", current=500, path="/docker-a.scope"))
        result = evaluator.evaluate(host(500, 2_000_000_000), second, now=1.0)
        self.assertEqual(result["state"], "TARGET_CONFIRMED")
        self.assertEqual(result["target"]["id"], "a")
        self.assertEqual(result["target"]["host_contribution_percent"], 80.0)

    def test_close_candidates_are_ambiguous(self):
        evaluator = ObjectAttributionEvaluator(AttributionPolicy(min_host_contribution_percent=20, min_lead_margin=0.15))
        first = registry(
            object_sample("a", current=100, path="/docker-a.scope"),
            object_sample("b", current=100, path="/docker-b.scope"),
        )
        evaluator.evaluate(host(1000, 1_000_000_000), first, now=0.0)
        second = registry(
            object_sample("a", current=300, path="/docker-a.scope"),
            object_sample("b", current=290, path="/docker-b.scope"),
        )
        result = evaluator.evaluate(host(500, 2_000_000_000), second, now=1.0)
        self.assertEqual(result["state"], "AMBIGUOUS_TARGET")
        self.assertIn("candidate_lead_margin_too_small", result["reason_codes"])

    def test_missing_object_signal_degrades_without_target(self):
        evaluator = ObjectAttributionEvaluator()
        first = registry(object_sample("a", current=None, path=None), status="degraded")
        result = evaluator.evaluate(host(1000, 1_000_000_000), first, now=0.0)
        self.assertEqual(result["state"], "DEGRADED_OBSERVABILITY")
        self.assertIsNone(result["target"])
        self.assertIn("object_registry_degraded", result["reason_codes"])

    def test_container_recreation_same_name_abandons_target(self):
        evaluator = ObjectAttributionEvaluator()
        evaluator.evaluate(
            host(1000, 1_000_000_000),
            registry(object_sample("old", name="app", current=100, path="/docker-old.scope")),
            now=0.0,
        )
        result = evaluator.evaluate(
            host(500, 2_000_000_000),
            registry(object_sample("new", name="app", current=500, path="/docker-new.scope")),
            now=1.0,
        )
        self.assertEqual(result["state"], "AMBIGUOUS_TARGET")
        self.assertIn("container_recreated", result["reason_codes"])
        self.assertIsNone(result["target"])

    def test_id_and_cgroup_mismatch_never_confirms_target(self):
        evaluator = ObjectAttributionEvaluator()
        result = evaluator.evaluate(
            host(500, 2_000_000_000),
            registry(
                object_sample(
                    "a",
                    current=500,
                    path="/docker-b.scope",
                    errors=["container_id_cgroup_mismatch"],
                ),
                status="degraded",
            ),
            now=1.0,
        )
        self.assertEqual(result["state"], "DEGRADED_OBSERVABILITY")
        self.assertIsNone(result["target"])

    def test_collect_registry_maps_inspect_pid_to_cgroup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            proc = root / "proc"
            cgroup = root / "cgroup"
            (proc / "123").mkdir(parents=True)
            (proc / "123" / "cgroup").write_text("0::/system.slice/docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope\n")
            object_cgroup = cgroup / "system.slice" / "docker-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.scope"
            object_cgroup.mkdir(parents=True)
            (object_cgroup / "memory.current").write_text("2048\n")
            (object_cgroup / "memory.max").write_text("max\n")
            (object_cgroup / "memory.events").write_text("oom 0\noom_kill 0\n")

            def runner(command, **_kwargs):
                self.assertEqual(command[:4], ["docker", "inspect", "--format", "{{json .}}"])
                payload = {
                    "Id": "a" * 32,
                    "Name": "/discardable",
                    "Created": "2026-09-20T00:00:00Z",
                    "State": {"Pid": 123, "Status": "running"},
                    "Config": {"Image": "local:test", "Labels": {"role": "test"}},
                }
                return type("Result", (), {"returncode": 0, "stdout": json.dumps(payload) + "\n", "stderr": ""})()

            result = collect_object_registry(
                {"available": True, "containers": [{"Container": "a" * 32, "Name": "discardable"}]},
                proc_root=proc,
                cgroup_root=cgroup,
                runner=runner,
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["objects"][0]["mapping_confidence"], "high")
            self.assertEqual(result["objects"][0]["memory_current_bytes"], 2048)
            self.assertEqual(result["objects"][0]["cgroup_path"], str(object_cgroup))


if __name__ == "__main__":
    unittest.main()
