import unittest

from src.guardian_multirisk import build_joint_decision


def resource(kind, state, *, target_id=None, attribution_state=None, quality_status="ok"):
    attribution_state = attribution_state or ("TARGET_CONFIRMED" if target_id else "NO_TARGET")
    attribution = {"state": attribution_state, "quality_flags": []}
    if target_id:
        attribution["target"] = {"id": target_id, "name": target_id}
    return {
        "risk": {
            "resource_kind": kind,
            "state": state,
            "quality_status": quality_status,
            "quality_flags": [],
        },
        "attribution": attribution,
    }


class GuardianMultiRiskTests(unittest.TestCase):
    def test_same_stable_target_can_create_one_simulate_plan(self):
        result = build_joint_decision(
            {
                "memory": resource("memory", "critical", target_id="writer-a"),
                "cpu": resource("cpu", "warning", target_id="writer-a"),
                "io": resource("io", "normal"),
            },
            mode="simulate",
            simulate_action="graceful_stop",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["state"], "critical")
        self.assertEqual(result["target_state"], "TARGET_CONFIRMED")
        self.assertEqual(result["decision"]["action"], "graceful_stop")
        self.assertEqual(result["decision"]["execution"], "not_executed")
        self.assertEqual(result["decision"]["target_id"], "writer-a")

    def test_different_targets_are_ambiguous_and_action_zero(self):
        result = build_joint_decision(
            {
                "memory": resource("memory", "critical", target_id="memory-a"),
                "io": resource("io", "critical", target_id="io-b"),
            },
            mode="simulate",
            simulate_action="graceful_stop",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["target_state"], "MULTI_RESOURCE_AMBIGUOUS")
        self.assertEqual(result["decision"]["action"], "escalate")
        self.assertEqual(result["decision"]["execution"], "not_executed")
        self.assertIn("multi_resource_ambiguous", result["decision"]["reason_codes"])

    def test_active_resource_without_target_cannot_join_confirmed_target(self):
        result = build_joint_decision(
            {
                "memory": resource("memory", "critical", target_id="writer-a"),
                "io": resource("io", "critical", attribution_state="DEGRADED_OBSERVABILITY"),
            },
            mode="simulate",
            simulate_action="graceful_stop",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["target_state"], "MULTI_RESOURCE_AMBIGUOUS")
        self.assertEqual(result["decision"]["action"], "escalate")

    def test_degraded_channel_escalates_joint_event(self):
        result = build_joint_decision(
            {
                "memory": resource("memory", "critical", target_id="writer-a"),
                "cpu": resource("cpu", "degraded_observability", quality_status="degraded"),
            },
            mode="simulate",
            simulate_action="graceful_stop",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["state"], "escalated")
        self.assertEqual(result["target_state"], "DEGRADED_OBSERVABILITY")
        self.assertEqual(result["decision"]["action"], "escalate")

    def test_observe_never_creates_action_even_with_confirmed_target(self):
        result = build_joint_decision(
            {"cpu": resource("cpu", "critical", target_id="writer-a")},
            mode="observe",
            simulate_action="graceful_stop",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["decision"]["action"], "none")
        self.assertEqual(result["decision"]["execution"], "not_applicable")

    def test_recovery_has_no_action_and_no_active_target(self):
        result = build_joint_decision(
            {"memory": resource("memory", "recovered")},
            mode="simulate",
            simulate_action="graceful_stop",
            protected=False,
            allowed_actions=["graceful_stop"],
        )
        self.assertEqual(result["state"], "recovered")
        self.assertEqual(result["decision"]["action"], "none")
        self.assertEqual(result["decision"]["execution"], "not_executed")


if __name__ == "__main__":
    unittest.main()
