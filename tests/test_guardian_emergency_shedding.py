import unittest

from src.guardian_emergency_shedding import (
    SUPPORTED_RESOURCES,
    EmergencySheddingPolicy,
    build_emergency_shedding_decision,
    policy_from_mapping,
)
from src.guardian_observer import _emergency_candidates, _emergency_host_risks


TARGET_A = "a" * 64
TARGET_B = "b" * 64
TARGET_C = "c" * 64


def candidate(identifier, *, contributions, created_at="2026-09-21T00:00:00Z", inode=101, **extra):
    value = {
        "kind": "container",
        "id": identifier,
        "name": identifier[:12],
        "status": "running",
        "created_at": created_at,
        "cgroup_path": f"/sys/fs/cgroup/docker-{identifier}.scope",
        "cgroup_inode": inode,
        "resource_contributions": dict(contributions),
    }
    value.update(extra)
    return value


def registry_for(*values):
    return [
        {
            key: value[key]
            for key in ("kind", "id", "name", "status", "created_at", "cgroup_path", "cgroup_inode")
        }
        for value in values
    ]


def policy(**overrides):
    values = {
        "enabled": True,
        "window_seconds": 15,
        "required_samples": 2,
        "min_host_contribution_percent": 20,
        "resource_priority": SUPPORTED_RESOURCES,
        "action": "graceful_stop",
    }
    values.update(overrides)
    return EmergencySheddingPolicy(**values)


def actionable(identifier, resources=SUPPORTED_RESOURCES):
    return {
        "stable_id": identifier,
        "owner": "test-owner",
        "environment": "local-disposable",
        "allowed_resources": list(resources),
        "action": "graceful_stop",
        "expires_at": "2026-12-31T00:00:00Z",
    }


def confirmed(*resources):
    return {
        resource: {
            "state": "CRITICAL_CONFIRMED",
            "sample_complete": True,
            "sample_count": 3,
            "quality_status": "ok",
        }
        for resource in resources
    }


class EmergencySheddingTests(unittest.TestCase):
    def run_decision(self, host_risks, candidates, *, protected=(), actionable=(), selected_policy=None, mode="simulate", allowed=("graceful_stop",)):
        return build_emergency_shedding_decision(
            host_risks,
            candidates,
            registry=registry_for(*candidates),
            protected_set=protected,
            actionable_set=actionable,
            mode=mode,
            policy=selected_policy or policy(),
            allowed_actions=allowed,
            event_id="event-p017-test",
            config_digest="config-test",
        )

    def test_normal_and_short_peak_never_create_plan(self):
        target = candidate(TARGET_A, contributions={"memory": 90})
        normal = self.run_decision(
            {"memory": {"state": "NORMAL", "sample_complete": True}},
            [target],
            actionable=[actionable(TARGET_A)],
        )
        self.assertEqual(normal["decision"]["action"], "none")
        self.assertEqual(normal["decision"]["plans_count"], 0)
        self.assertIn("HOST_RISK_NOT_CONFIRMED", normal["decision"]["reason_codes"])

        short_peak = self.run_decision(
            {"memory": {"state": "CRITICAL", "danger_confirmed": False, "sample_complete": False}},
            [target],
            actionable=[actionable(TARGET_A)],
        )
        self.assertEqual(short_peak["decision"]["action"], "none")
        self.assertIn("INCOMPLETE_SAMPLES", short_peak["decision"]["reason_codes"])

    def test_actionable_resource_top_gets_one_simulate_plan(self):
        top = candidate(TARGET_A, contributions={"memory": 70})
        second = candidate(TARGET_B, contributions={"memory": 30}, inode=102)
        result = self.run_decision(
            confirmed("memory"),
            [top, second],
            actionable=[actionable(TARGET_A)],
        )
        decision = result["decision"]
        self.assertEqual(decision["action"], "graceful_stop")
        self.assertEqual(decision["execution"], "not_executed")
        self.assertEqual(decision["plans_count"], 1)
        self.assertEqual(decision["target_id"], TARGET_A)
        self.assertFalse(decision["plan"]["root_cause_claimed"])
        self.assertEqual(decision["plan"]["max_actions"], 1)

    def test_protected_global_top_blocks_lower_actionable_candidate(self):
        protected_top = candidate(TARGET_A, contributions={"cpu": 80})
        actionable_second = candidate(TARGET_B, contributions={"cpu": 20}, inode=102)
        result = self.run_decision(
            confirmed("cpu"),
            [protected_top, actionable_second],
            protected=[{"stable_id": TARGET_A}],
            actionable=[actionable(TARGET_B)],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("PROTECTED_TOP_CONSUMER", result["decision"]["reason_codes"])
        self.assertEqual(result["decision"]["plans_count"], 0)

    def test_unknown_global_top_blocks_lower_actionable_candidate(self):
        unknown_top = candidate(TARGET_A, contributions={"io": 80})
        actionable_second = candidate(TARGET_B, contributions={"io": 20}, inode=102)
        result = self.run_decision(
            confirmed("io"),
            [unknown_top, actionable_second],
            actionable=[actionable(TARGET_B)],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("UNKNOWN_TOP_CONSUMER", result["decision"]["reason_codes"])

    def test_contribution_below_minimum_fails_closed(self):
        top = candidate(TARGET_A, contributions={"memory": 19.9})
        result = self.run_decision(
            confirmed("memory"),
            [top],
            actionable=[actionable(TARGET_A)],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("CONTRIBUTION_BELOW_MINIMUM", result["decision"]["reason_codes"])

    def test_non_finite_contribution_fails_closed(self):
        top = candidate(TARGET_A, contributions={"memory": float("nan")})
        result = self.run_decision(
            confirmed("memory"),
            [top],
            actionable=[actionable(TARGET_A)],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("INCOMPLETE_SAMPLES", result["decision"]["reason_codes"])

    def test_non_iterable_snapshot_or_clock_fails_closed(self):
        top = candidate(TARGET_A, contributions={"memory": 90})
        result = build_emergency_shedding_decision(
            confirmed("memory"),
            None,
            registry=registry_for(top),
            actionable_set=[actionable(TARGET_A)],
            mode="simulate",
            policy=policy(),
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("DATA_INCOMPLETE", result["decision"]["reason_codes"])

        result = build_emergency_shedding_decision(
            confirmed("memory"),
            [top],
            registry=None,
            actionable_set=[actionable(TARGET_A)],
            mode="simulate",
            policy=policy(),
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("DATA_INCOMPLETE", result["decision"]["reason_codes"])

        result = build_emergency_shedding_decision(
            confirmed("memory"),
            [top],
            registry=registry_for(top),
            actionable_set=[actionable(TARGET_A)],
            mode="simulate",
            policy=policy(),
            allowed_actions=["graceful_stop"],
            now_epoch_s=float("nan"),
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("DATA_INCOMPLETE", result["decision"]["reason_codes"])

    def test_malformed_actionable_entry_cannot_widen_action_surface(self):
        top = candidate(TARGET_A, contributions={"memory": 90})
        for mutation in (
            {"environment": "production"},
            {"action": "terminate"},
            {"allowed_resources": []},
            {"allowed_resources": ["unknown"]},
        ):
            entry = actionable(TARGET_A)
            entry.update(mutation)
            result = self.run_decision(
                confirmed("memory"),
                [top],
                actionable=[entry],
            )
            self.assertEqual(result["decision"]["action"], "none")
            self.assertIn("DATA_INCOMPLETE", result["decision"]["reason_codes"])

    def test_malformed_protection_or_action_allowlist_fails_closed(self):
        top = candidate(TARGET_A, contributions={"memory": 90})
        result = build_emergency_shedding_decision(
            confirmed("memory"),
            [top],
            registry=registry_for(top),
            protected_set=[None],
            actionable_set=[actionable(TARGET_A)],
            mode="simulate",
            policy=policy(),
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("DATA_INCOMPLETE", result["decision"]["reason_codes"])

        result = build_emergency_shedding_decision(
            confirmed("memory"),
            [top],
            registry=registry_for(top),
            actionable_set=[actionable(TARGET_A)],
            mode="simulate",
            policy=policy(),
            allowed_actions=None,
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("DATA_INCOMPLETE", result["decision"]["reason_codes"])

    def test_policy_rejects_non_finite_or_boolean_limits(self):
        with self.assertRaisesRegex(ValueError, "window_seconds_must_be_positive"):
            policy(window_seconds=float("inf"))
        with self.assertRaisesRegex(ValueError, "min_host_contribution_out_of_bounds"):
            policy(min_host_contribution_percent=float("nan"))
        with self.assertRaisesRegex(ValueError, "required_samples_out_of_bounds"):
            policy(required_samples=True)

        with self.assertRaisesRegex(ValueError, "window_seconds_must_be_positive"):
            policy(window_seconds=10**10000)
        with self.assertRaisesRegex(ValueError, "enabled_must_be_boolean"):
            EmergencySheddingPolicy(enabled=1)
        with self.assertRaisesRegex(ValueError, "window_seconds_must_be_positive"):
            policy_from_mapping({"enabled": True, "window_seconds": 10**10000})
        with self.assertRaisesRegex(ValueError, "actionable_set_entries_must_be_mappings"):
            policy_from_mapping({"enabled": True, "actionable_set": [None]})
        with self.assertRaisesRegex(ValueError, "protected_set_must_be_list"):
            policy_from_mapping({"enabled": True, "protected_set": None})
        with self.assertRaisesRegex(ValueError, "schema_unsupported"):
            policy_from_mapping({"schema": "guardian.emergency_shedding.v0"})

    def test_malformed_mode_extreme_number_and_raising_iterable_fail_closed(self):
        top = candidate(TARGET_A, contributions={"memory": 10**10000})
        result = self.run_decision(
            confirmed("memory"),
            [top],
            actionable=[actionable(TARGET_A)],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("INCOMPLETE_SAMPLES", result["decision"]["reason_codes"])

        result = build_emergency_shedding_decision(
            confirmed("memory"),
            [candidate(TARGET_A, contributions={"memory": 90})],
            registry=registry_for(candidate(TARGET_A, contributions={"memory": 90})),
            actionable_set=[actionable(TARGET_A)],
            mode="simulate",
            policy=policy(),
            allowed_actions=["graceful_stop"],
            now_epoch_s=10**10000,
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("DATA_INCOMPLETE", result["decision"]["reason_codes"])

        result = build_emergency_shedding_decision(
            confirmed("memory"),
            [],
            mode=[],
            policy=policy(),
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertEqual(result["decision"]["mode"], "invalid")
        self.assertIn("EMERGENCY_SHEDDING_SIMULATE_ONLY", result["decision"]["reason_codes"])

        def raising_iterable():
            yield top
            raise RuntimeError("fixture_iterator_failed")

        result = build_emergency_shedding_decision(
            confirmed("memory"),
            raising_iterable(),
            registry=registry_for(top),
            actionable_set=[actionable(TARGET_A)],
            mode="simulate",
            policy=policy(),
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("DATA_INCOMPLETE", result["decision"]["reason_codes"])

    def test_expired_actionable_entry_fails_closed(self):
        top = candidate(TARGET_A, contributions={"memory": 90})
        expired = actionable(TARGET_A)
        expired["expires_at"] = "2020-01-01T00:00:00Z"
        result = self.run_decision(
            confirmed("memory"),
            [top],
            actionable=[expired],
            selected_policy=policy(),
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("ACTIONABLE_ENTRY_EXPIRED", result["decision"]["reason_codes"])

    def test_missing_or_invalid_actionable_expiry_fails_closed(self):
        top = candidate(TARGET_A, contributions={"memory": 90})
        for expiry in (None, "not-an-iso-time", "2099-01-01T00:00:00"):
            entry = actionable(TARGET_A)
            if expiry is None:
                entry.pop("expires_at")
            else:
                entry["expires_at"] = expiry
            result = self.run_decision(
                confirmed("memory"),
                [top],
                actionable=[entry],
            )
            self.assertEqual(result["decision"]["action"], "none")
            self.assertIn("DATA_INCOMPLETE", result["decision"]["reason_codes"])

    def test_incomplete_sample_data_produces_zero_action(self):
        top = candidate(TARGET_A, contributions={"memory": 90})
        result = self.run_decision(
            {"memory": {"state": "CRITICAL_CONFIRMED", "sample_complete": False}},
            [top],
            actionable=[actionable(TARGET_A)],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("INCOMPLETE_SAMPLES", result["decision"]["reason_codes"])

        for override in (
            {"quality_flags": ["docker_observation_unavailable"]},
            {"quality_flags": "docker_observation_unavailable"},
            {"quality_flags": None},
            {"required_samples": 0},
            {"required_samples": True},
        ):
            quality_gap = {
                "memory": {
                    "state": "CRITICAL_CONFIRMED",
                    "sample_complete": True,
                    "sample_count": 3,
                    "quality_status": "ok",
                    "quality_flags": [],
                    **override,
                }
            }
            result = self.run_decision(
                quality_gap,
                [top],
                actionable=[actionable(TARGET_A)],
            )
            self.assertEqual(result["decision"]["action"], "none")
            self.assertIn("INCOMPLETE_SAMPLES", result["decision"]["reason_codes"])

    def test_identity_change_blocks_actionable_top(self):
        top = candidate(TARGET_A, contributions={"memory": 90}, identity_changed=True)
        result = self.run_decision(
            confirmed("memory"),
            [top],
            actionable=[actionable(TARGET_A)],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("TARGET_IDENTITY_CHANGED", result["decision"]["reason_codes"])

    def test_non_top_identity_gap_blocks_ranked_plan(self):
        top = candidate(TARGET_A, contributions={"memory": 80})
        incomplete_second = candidate(TARGET_B, contributions={"memory": 20}, inode=None)
        result = self.run_decision(
            confirmed("memory"),
            [top, incomplete_second],
            actionable=[actionable(TARGET_A)],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("DATA_INCOMPLETE", result["decision"]["reason_codes"])
        self.assertEqual(result["decision"]["plans_count"], 0)

    def test_missing_candidate_identity_blocks_ranked_plan(self):
        top = candidate(TARGET_A, contributions={"memory": 80})
        malformed = {
            "kind": "container",
            "status": "running",
            "resource_contributions": {"memory": 20},
        }
        result = build_emergency_shedding_decision(
            confirmed("memory"),
            [top, malformed],
            registry=registry_for(top),
            actionable_set=[actionable(TARGET_A)],
            mode="simulate",
            policy=policy(),
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("DATA_INCOMPLETE", result["decision"]["reason_codes"])
        self.assertEqual(result["decision"]["plans_count"], 0)

    def test_non_mapping_candidate_blocks_ranked_plan(self):
        top = candidate(TARGET_A, contributions={"memory": 80})
        result = build_emergency_shedding_decision(
            confirmed("memory"),
            [top, None],
            registry=registry_for(top),
            actionable_set=[actionable(TARGET_A)],
            mode="simulate",
            policy=policy(),
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("DATA_INCOMPLETE", result["decision"]["reason_codes"])
        self.assertEqual(result["decision"]["plans_count"], 0)

    def test_non_container_snapshot_entry_blocks_ranked_plan(self):
        top = candidate(TARGET_A, contributions={"memory": 80})
        non_container = dict(top)
        non_container["kind"] = "host"
        result = build_emergency_shedding_decision(
            confirmed("memory"),
            [top, non_container],
            registry=registry_for(top),
            actionable_set=[actionable(TARGET_A)],
            mode="simulate",
            policy=policy(),
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("DATA_INCOMPLETE", result["decision"]["reason_codes"])

        malformed_registry = registry_for(top)
        malformed_registry.append(dict(malformed_registry[0], kind="host"))
        result = build_emergency_shedding_decision(
            confirmed("memory"),
            [top],
            registry=malformed_registry,
            actionable_set=[actionable(TARGET_A)],
            mode="simulate",
            policy=policy(),
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("DATA_INCOMPLETE", result["decision"]["reason_codes"])

    def test_duplicate_candidate_identity_blocks_ranked_plan(self):
        top = candidate(TARGET_A, contributions={"memory": 80})
        duplicate = candidate(TARGET_A, contributions={"memory": 20}, inode=102)
        result = build_emergency_shedding_decision(
            confirmed("memory"),
            [top, duplicate],
            registry=registry_for(top, duplicate),
            actionable_set=[actionable(TARGET_A)],
            mode="simulate",
            policy=policy(),
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("DATA_INCOMPLETE", result["decision"]["reason_codes"])
        self.assertEqual(result["decision"]["plans_count"], 0)

    def test_duplicate_registry_identity_blocks_ranked_plan(self):
        top = candidate(TARGET_A, contributions={"memory": 90})
        duplicate_registry = registry_for(top, dict(top, cgroup_inode=102))
        result = build_emergency_shedding_decision(
            confirmed("memory"),
            [top],
            registry=duplicate_registry,
            actionable_set=[actionable(TARGET_A)],
            mode="simulate",
            policy=policy(),
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("DATA_INCOMPLETE", result["decision"]["reason_codes"])
        self.assertEqual(result["decision"]["plans_count"], 0)

    def test_malformed_registry_identity_blocks_ranked_plan(self):
        top = candidate(TARGET_A, contributions={"memory": 90})
        malformed_registry = registry_for(top) + [{"kind": "container", "status": "running"}]
        result = build_emergency_shedding_decision(
            confirmed("memory"),
            [top],
            registry=malformed_registry,
            actionable_set=[actionable(TARGET_A)],
            mode="simulate",
            policy=policy(),
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("DATA_INCOMPLETE", result["decision"]["reason_codes"])
        self.assertEqual(result["decision"]["plans_count"], 0)

    def test_mixed_resources_use_one_configured_priority_and_one_plan(self):
        memory_top = candidate(TARGET_A, contributions={"memory": 70, "cpu": 10})
        cpu_top = candidate(TARGET_B, contributions={"memory": 30, "cpu": 90}, inode=102)
        result = self.run_decision(
            confirmed("memory", "cpu"),
            [memory_top, cpu_top],
            actionable=[actionable(TARGET_A), actionable(TARGET_B)],
            selected_policy=policy(resource_priority=("cpu", "memory", "io", "disk_capacity")),
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertEqual(result["decision"]["resource_kind"], "multi_resource")
        self.assertIn("MULTI_RESOURCE_AMBIGUOUS", result["decision"]["reason_codes"])

    def test_capacity_requires_writer_evidence(self):
        writerless = candidate(TARGET_A, contributions={"disk_capacity": 90})
        result = self.run_decision(
            confirmed("disk_capacity"),
            [writerless],
            actionable=[actionable(TARGET_A)],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("CAPACITY_WRITER_EVIDENCE_MISSING", result["decision"]["reason_codes"])

        writer = candidate(TARGET_A, contributions={"disk_capacity": 90}, writer_evidence=True)
        result = self.run_decision(
            confirmed("disk_capacity"),
            [writer],
            actionable=[actionable(TARGET_A)],
        )
        self.assertEqual(result["decision"]["action"], "graceful_stop")

    def test_mixed_memory_and_disk_without_writer_evidence_blocks_container_action(self):
        memory_top = candidate(TARGET_A, contributions={"memory": 90, "disk_capacity": 80})
        result = self.run_decision(
            confirmed("memory", "disk_capacity"),
            [memory_top],
            actionable=[actionable(TARGET_A)],
            selected_policy=policy(resource_priority=("memory", "cpu", "io", "disk_capacity")),
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertIn("CAPACITY_WRITER_EVIDENCE_MISSING", result["decision"]["reason_codes"])

    def test_observe_stays_read_only_and_enforce_generates_pending_plan(self):
        top = candidate(TARGET_A, contributions={"memory": 90})
        observe = self.run_decision(
            confirmed("memory"), [top], actionable=[actionable(TARGET_A)], mode="observe", allowed=()
        )
        self.assertEqual(observe["decision"]["action"], "none")
        self.assertEqual(observe["decision"]["execution"], "not_applicable")
        self.assertEqual(observe["decision"]["plans_count"], 0)
        self.assertEqual(observe["decision"]["reason_codes"], ["OBSERVE_ONLY"])

        enforce = self.run_decision(
            confirmed("memory"), [top], actionable=[actionable(TARGET_A)], mode="enforce"
        )
        self.assertEqual(enforce["decision"]["action"], "graceful_stop")
        self.assertEqual(enforce["decision"]["execution"], "not_executed")
        self.assertEqual(enforce["decision"]["plans_count"], 1)
        self.assertEqual(enforce["decision"]["plan"]["target_id"], TARGET_A)

    def test_observer_translation_feeds_registry_identity_and_dwell_gate(self):
        top = candidate(TARGET_A, contributions={"memory": 90})
        observation = {
            "object_registry": {"status": "ok", "objects": [dict(top)]},
        }
        resource_evaluations = {
            "memory": {
                "risk": {
                    "state": "critical",
                    "candidate_for_seconds": 31,
                    "required_for_seconds": 30,
                    "sample_count": 3,
                    "quality_status": "ok",
                    "quality_flags": [],
                },
                "attribution": {
                    "state": "TARGET_CONFIRMED",
                    "candidates": [
                        {
                            "id": TARGET_A,
                            "host_contribution_percent": 90,
                            "mapping_errors": [],
                        }
                    ],
                    "reason_codes": [],
                },
            }
        }
        host_risks = _emergency_host_risks(resource_evaluations, required_samples=2)
        candidates = _emergency_candidates(observation, resource_evaluations, window_seconds=15)
        self.assertEqual(host_risks["memory"]["state"], "CRITICAL_CONFIRMED")
        self.assertEqual(candidates[0]["resource_contributions"]["memory"], 90.0)
        result = build_emergency_shedding_decision(
            host_risks,
            candidates,
            registry=observation["object_registry"]["objects"],
            actionable_set=[actionable(TARGET_A)],
            mode="simulate",
            policy=policy(),
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["decision"]["action"], "graceful_stop")
        self.assertEqual(result["decision"]["execution"], "not_executed")


if __name__ == "__main__":
    unittest.main()
