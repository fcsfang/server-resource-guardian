import unittest

from src.guardian_rescue_plane import (
    DependencyEdge,
    DependencyGraph,
    DependencySpec,
    DependencyState,
    ResourceProtectionConfig,
    ResourceProtectionObservation,
    RescuePlaneError,
    RollbackPreconditions,
    check_rollback_preconditions,
    diagnose_maintenance,
)


class GuardianRescuePlaneContractTests(unittest.TestCase):
    def setUp(self):
        self.graph = DependencyGraph.minimum()
        self.healthy_states = {
            node.name: DependencyState(present=True, status="ready")
            for node in self.graph.nodes
        }
        self.protection = ResourceProtectionObservation(status="ready", config_matches=True)

    def test_minimum_graph_is_small_and_acyclic(self):
        self.assertEqual(
            {node.name for node in self.graph.nodes},
            {
                "network",
                "dns",
                "ssh",
                "logind",
                "user.slice",
                "guardian-runtime",
                "local-state",
                "journald",
                "docker",
                "containerd",
                "diagnostic-observer",
            },
        )
        self.assertGreaterEqual(len(self.graph.edges), 10)
        self.assertEqual(self.graph.evaluate(self.healthy_states).status, "ready")

    def test_graph_rejects_cycles_and_unknown_edges(self):
        with self.assertRaisesRegex(RescuePlaneError, "cycle_detected"):
            DependencyGraph(
                (DependencySpec("a"), DependencySpec("b")),
                (DependencyEdge("a", "b"), DependencyEdge("b", "a")),
            )
        with self.assertRaisesRegex(RescuePlaneError, "unknown_node"):
            DependencyGraph((DependencySpec("a"),), (DependencyEdge("a", "missing"),))

    def test_missing_or_abnormal_dependency_fails_closed(self):
        states = dict(self.healthy_states)
        states.pop("local-state")
        states["guardian-runtime"] = DependencyState(present=True, status="failed")
        result = self.graph.evaluate(states)
        self.assertEqual(result.status, "blocked")
        self.assertIn("dependency_missing:local-state", result.reason_codes)
        self.assertIn("dependency_status_abnormal:guardian-runtime:failed", result.reason_codes)
        self.assertIn("dependency_prerequisite_unhealthy:guardian-runtime:local-state", result.reason_codes)
        self.assertIn("dependency_prerequisite_unhealthy:diagnostic-observer:guardian-runtime", result.reason_codes)

    def test_resource_protection_is_declarative_and_ordered(self):
        config = ResourceProtectionConfig(
            protected_units=("guardian-observer.service",),
            protected_paths=("/run/guardian",),
            memory_min_bytes=16 * 1024 * 1024,
            memory_low_bytes=32 * 1024 * 1024,
            memory_high_bytes=192 * 1024 * 1024,
            tasks_max=128,
        )
        self.assertEqual(config.mode, "observe")
        bounded = ResourceProtectionConfig(cpu_weight=1000, io_weight=1000, oom_score_adjust=-900)
        self.assertEqual(bounded.oom_score_adjust, -900)
        with self.assertRaisesRegex(RescuePlaneError, "systemd_weight_out_of_range"):
            ResourceProtectionConfig(cpu_weight=10001)
        with self.assertRaisesRegex(RescuePlaneError, "oom_score_adjust:out_of_range"):
            ResourceProtectionConfig(oom_score_adjust=-1001)
        with self.assertRaisesRegex(RescuePlaneError, "memory_low_above_high"):
            ResourceProtectionConfig(memory_low_bytes=10, memory_high_bytes=1)
        with self.assertRaisesRegex(RescuePlaneError, "read_only_mode_required"):
            ResourceProtectionConfig(mode="enforce")

    def test_maintenance_requires_all_evidence_and_remains_read_only(self):
        result = diagnose_maintenance(self.graph, self.healthy_states, self.protection)
        self.assertEqual(result.status, "ready")
        self.assertTrue(result.read_only)
        degraded = diagnose_maintenance(
            self.graph,
            self.healthy_states,
            ResourceProtectionObservation(status="unknown", config_matches=None),
            runtime_status="degraded",
        )
        self.assertEqual(degraded.status, "blocked")
        self.assertIn("runtime_status_abnormal:degraded", degraded.reason_codes)
        self.assertIn("resource_protection_config_unverified", degraded.reason_codes)

    def test_rollback_defaults_to_blocked_without_mutation(self):
        result = check_rollback_preconditions(
            RollbackPreconditions(
                target_id="",
                snapshot_present=None,
                snapshot_integrity_ok=None,
                target_identity_matches=None,
            )
        )
        self.assertFalse(result.ready)
        self.assertEqual(result.status, "blocked")
        self.assertTrue(result.read_only)
        self.assertIn("maintenance_diagnostic_missing", result.reason_codes)

    def test_rollback_requires_authorization_only_for_opted_in_mutation(self):
        maintenance = diagnose_maintenance(self.graph, self.healthy_states, self.protection)
        evidence = dict(
            target_id="snapshot-2026-09-21",
            snapshot_present=True,
            snapshot_integrity_ok=True,
            target_identity_matches=True,
            maintenance=maintenance,
        )
        read_only = check_rollback_preconditions(RollbackPreconditions(**evidence))
        self.assertTrue(read_only.ready)
        self.assertTrue(read_only.read_only)

        denied = check_rollback_preconditions(
            RollbackPreconditions(**evidence, request_mutation=True)
        )
        self.assertFalse(denied.ready)
        self.assertIn("rollback_explicit_authorization_required", denied.reason_codes)

        authorized = check_rollback_preconditions(
            RollbackPreconditions(**evidence, request_mutation=True, explicit_authorization=True)
        )
        self.assertTrue(authorized.ready)
        self.assertFalse(authorized.read_only)


if __name__ == "__main__":
    unittest.main()
