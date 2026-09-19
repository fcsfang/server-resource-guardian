import json
import unittest

from src.guardian_beszel_bridge import bridge_beszel_event
from src.guardian_ui_model import UI_SCHEMA, build_ui_view_model
from tests.test_guardian_beszel_bridge import RECEIVED, beszel_event, local_observation


class GuardianUiModelTests(unittest.TestCase):
    def bridge(self, *, available=5.0, containers=None, mode="simulate"):
        return bridge_beszel_event(
            beszel_event(),
            local_observation(available=available, containers=containers),
            now=RECEIVED,
            mode=mode,
            protected=False,
            allowed_actions=["graceful_stop"],
        )

    def test_model_preserves_simulate_boundary_and_redacts_raw_candidates(self):
        model = build_ui_view_model(self.bridge())
        self.assertEqual(model["schema"], UI_SCHEMA)
        self.assertEqual(model["policy"]["decision"], "plan_generated")
        self.assertEqual(model["plan"]["action"], "graceful_stop")
        self.assertEqual(model["plan"]["execution"], "not_executed")
        self.assertEqual(model["object"]["stable_id"], "local-1")
        self.assertNotIn("raw", json.dumps(model))

    def test_model_matches_frozen_contract_and_does_not_authorize_actions(self):
        bridge = self.bridge()
        model = build_ui_view_model(bridge, allowed_actions=["graceful_stop"])

        self.assertEqual(
            set(model),
            {"schema", "system", "risk", "object", "policy", "plan", "result", "recovery"},
        )
        self.assertEqual(
            set(model["risk"]),
            {"state", "confidence", "entered_at", "expires_at", "signals"},
        )
        self.assertEqual(
            set(model["object"]),
            {"kind", "stable_id", "display_name", "identity_confidence", "protected", "candidates"},
        )
        self.assertEqual(
            set(model["policy"]),
            {"mode", "decision", "reason_codes", "allowed_actions", "policy_version"},
        )
        self.assertEqual(
            set(model["plan"]),
            {"action", "execution", "confirmation", "created_at"},
        )
        self.assertEqual(model["policy"]["mode"], "simulate")
        self.assertEqual(model["policy"]["decision"], "plan_generated")
        self.assertEqual(model["policy"]["allowed_actions"], ["graceful_stop"])
        self.assertEqual(model["plan"]["execution"], "not_executed")
        self.assertFalse(bridge["bridge"]["action_authorized"])

    def test_missing_object_identity_is_fail_closed(self):
        model = self.bridge(containers=[{"Name": "no-stable-id"}])
        view = build_ui_view_model(model, allowed_actions=["graceful_stop"])

        self.assertEqual(view["object"]["stable_id"], None)
        self.assertEqual(view["policy"]["decision"], "observation_only")
        self.assertEqual(view["policy"]["allowed_actions"], [])
        self.assertEqual(view["plan"]["action"], "none")
        self.assertEqual(view["plan"]["execution"], "not_executed")
        self.assertIn("no_stable_object_identity", view["policy"]["reason_codes"])

    def test_rejected_event_is_observation_only(self):
        bridge = self.bridge(available=90.0)
        bridge["bridge"]["status"] = "rejected"
        bridge["bridge"]["classification"] = "stale_event"
        model = build_ui_view_model(bridge)
        self.assertEqual(model["policy"]["decision"], "observation_only")
        self.assertEqual(model["plan"]["action"], "none")
        self.assertEqual(model["plan"]["confirmation"], "not_available")
        self.assertIn("stale_event", model["policy"]["reason_codes"])

    def test_ambiguous_objects_escalate_and_hide_actions(self):
        model = build_ui_view_model(
            self.bridge(containers=[{"ID": "a"}, {"ID": "b"}])
        )
        self.assertEqual(model["policy"]["decision"], "escalated")
        self.assertEqual(model["object"]["stable_id"], None)
        self.assertEqual(model["policy"]["allowed_actions"], [])
        self.assertEqual(model["plan"]["action"], "escalate")


if __name__ == "__main__":
    unittest.main()
