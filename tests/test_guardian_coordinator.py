from copy import deepcopy
import tempfile
import unittest
from pathlib import Path
from typing import Any

from src.guardian_actions import Authorization
from src.guardian_coordinator import (
    ActionIntentError,
    FakeActionAdapter,
    GuardianCoordinator,
    action_intent_from_event,
)
from src.guardian_emergency_shedding import (
    SUPPORTED_RESOURCES,
    EmergencySheddingPolicy,
    build_emergency_shedding_decision,
    emergency_policy_digest,
)
from src.guardian_notification_outbox import DurableNotificationOutbox, NotificationOutboxError
from src.guardian_notifications import FakeNotificationSink
from src.guardian_revalidation import revalidate_observer_event
from src.guardian_state import GuardianStateStore


TARGET = "a" * 64
CONFIG_DIGEST = "c" * 64


def policy() -> EmergencySheddingPolicy:
    return EmergencySheddingPolicy(
        enabled=True,
        window_seconds=15.0,
        required_samples=2,
        min_host_contribution_percent=20.0,
        resource_priority=SUPPORTED_RESOURCES,
        protected_set=(),
        actionable_set=(
            {
                "stable_id": TARGET,
                "owner": "fixture",
                "environment": "local-disposable",
                "allowed_resources": ["memory"],
                "action": "graceful_stop",
                "expires_at": "2099-01-01T00:00:00Z",
            },
        ),
    )


def event() -> dict:
    selected_policy = policy()
    identity = {
        "kind": "container",
        "id": TARGET,
        "name": "fixture-target",
        "status": "running",
        "running": True,
        "created_at": "2026-09-21T00:00:00Z",
        "cgroup_path": f"/sys/fs/cgroup/docker/{TARGET}",
        "cgroup_inode": 101,
        "labels": {},
        "mapping_confidence": "high",
        "mapping_errors": [],
    }
    candidate = {
        **identity,
        "resource_contributions": {"memory": 90.0},
    }
    shedding = build_emergency_shedding_decision(
        {
            "memory": {
                "state": "CRITICAL_CONFIRMED",
                "sample_complete": True,
                "sample_count": 3,
                "quality_status": "ok",
                "quality_flags": [],
            }
        },
        [candidate],
        registry=[identity],
        actionable_set=selected_policy.actionable_set,
        mode="simulate",
        policy=selected_policy,
        allowed_actions=["graceful_stop"],
        event_id="event-coordinator-1",
        sample_id="sample-coordinator-1",
        config_digest=CONFIG_DIGEST,
        now_epoch_s=1000.0,
    )
    return {
        "schema": "guardian.risk.event.v1",
        "event_id": "event-coordinator-1",
        "sample_id": "sample-coordinator-1",
        "observed_at": "2026-09-21T00:00:00Z",
        "observed_monotonic_ns": 1000,
        "host_id": "fixture-host",
        "state": "critical",
        "signals": {
            "observed_monotonic_ns": 1000,
            "object_registry": {"status": "ok", "objects": [identity]},
        },
        "resource_evaluations": {
            "memory": {
                "risk": {
                    "state": "CRITICAL_CONFIRMED",
                    "danger_confirmed": True,
                    "sample_complete": True,
                    "sample_count": 3,
                    "quality_status": "ok",
                    "quality_flags": [],
                }
            }
        },
        "decision": dict(shedding["decision"]),
        "emergency_shedding": shedding,
        "evidence": {
            "config_digest": CONFIG_DIGEST,
            "policy_digest": emergency_policy_digest(selected_policy),
        },
    }


def fresh_event(source: dict | None = None, *, cgroup_inode: int | None = None) -> dict:
    """Build a distinct action-time sample from the event under test."""

    value = deepcopy(source or event())
    value["event_id"] = "event-coordinator-fresh-1"
    value["sample_id"] = "sample-coordinator-fresh-1"
    original_monotonic = int(value.get("observed_monotonic_ns") or 1000)
    value["observed_monotonic_ns"] = original_monotonic + 1000
    value["observed_at"] = "2026-09-21T00:00:01Z"
    signals = value.setdefault("signals", {})
    signals["observed_monotonic_ns"] = original_monotonic + 1000
    registry = signals.setdefault("object_registry", {})
    registry["status"] = "ok"
    objects = registry.get("objects")
    identity = deepcopy(objects[0]) if isinstance(objects, list) and objects else {}
    if cgroup_inode is not None:
        identity["cgroup_inode"] = cgroup_inode
    identity.update(
        {
            "status": "running",
            "running": True,
            "labels": identity.get("labels") if isinstance(identity.get("labels"), dict) else {},
            "mapping_confidence": "high",
            "mapping_errors": [],
        }
    )
    registry["objects"] = [identity]
    risk = value.setdefault("resource_evaluations", {}).setdefault("memory", {}).setdefault("risk", {})
    risk.update(
        {
            "state": "CRITICAL_CONFIRMED",
            "danger_confirmed": True,
            "sample_complete": True,
            "quality_status": "ok",
            "quality_flags": [],
        }
    )
    evidence = value.setdefault("evidence", {})
    evidence.update(
        {
            "config_digest": CONFIG_DIGEST,
            "policy_digest": emergency_policy_digest(policy()),
            "runtime_gate": {"execution_allowed": True, "reason_codes": []},
        }
    )
    for branch_name in ("decision", "emergency_shedding"):
        branch = value.get(branch_name)
        if isinstance(branch, dict):
            decision = branch.get("decision") if branch_name == "emergency_shedding" else branch
            if isinstance(decision, dict):
                decision["action"] = "graceful_stop"
    return value


def fresh_provider_for(*, inode_override: int | None = None):
    """Provide the independent sample required by simulate/enforce tests."""

    selected_policy = policy()

    def provider(intent, observed):
        candidate = fresh_event(observed, cgroup_inode=inode_override)
        return revalidate_observer_event(
            intent,
            observed,
            candidate,
            policy=selected_policy,
            expected_config_digest=CONFIG_DIGEST,
            now_epoch_s=intent.issued_at + 0.5,
            now_monotonic_ns=intent.observed_monotonic_ns + 2000,
        )

    return provider


def build_coordinator(
    temp: str,
    *,
    adapter: Any | None = None,
    fresh_provider: Any | None = None,
) -> GuardianCoordinator:
    sink = FakeNotificationSink()
    outbox = DurableNotificationOutbox(Path(temp) / "outbox.db", sink)
    store = GuardianStateStore(Path(temp) / "state.db")
    return GuardianCoordinator(
        store,
        outbox,
        policy=policy(),
        adapter=adapter or FakeActionAdapter(),
        default_mode="observe",
        fresh_revalidation_provider=fresh_provider or fresh_provider_for(),
        config_digest=CONFIG_DIGEST,
    )


class GuardianCoordinatorTests(unittest.TestCase):
    def test_plan_converts_to_strict_intent_and_preserves_contract_fields(self):
        source = event()
        intent = action_intent_from_event(
            source,
            expected_policy_digest=emergency_policy_digest(policy()),
            now_epoch_s=1000.0,
        )
        self.assertEqual(intent.event_id, source["event_id"])
        self.assertEqual(intent.sample_id, source["sample_id"])
        self.assertEqual(intent.resource_kind, "memory")
        self.assertEqual(intent.target_id, TARGET)
        self.assertEqual(intent.config_digest, CONFIG_DIGEST)
        self.assertEqual(len(intent.idempotency_key), 64)
        self.assertGreater(intent.expires_at, 1000.0)

        broken = event()
        broken["emergency_shedding"]["plan"]["target"]["cgroup_inode"] = 0
        with self.assertRaisesRegex(ActionIntentError, "target_cgroup_inode_invalid"):
            action_intent_from_event(broken, now_epoch_s=1000.0)

    def test_simulate_runs_one_durable_fake_end_to_end_and_never_needs_capability(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeActionAdapter("success")
            coordinator = build_coordinator(temp, adapter=adapter)
            result = coordinator.process(event(), mode="simulate", now_epoch_s=1000.0, now_monotonic_ns=2000)

            self.assertEqual(result.state, "simulated_plan_complete")
            self.assertEqual(result.reason_codes[0], "simulation_plan_complete")
            self.assertEqual(len(adapter.requests), 1)
            self.assertIsNone(adapter.requests[0].authorization)
            self.assertFalse(result.manual_handoff)
            self.assertEqual(result.semantic_state, "SIMULATED_PLAN_COMPLETE")
            self.assertEqual(result.execution_semantics, "SIMULATED")
            self.assertEqual(result.broker.verification.overall_state, "SIMULATED_PLAN")
            self.assertEqual(coordinator.state_store.get_intent(result.intent_id)["state"], "PLANNED")
            self.assertTrue(coordinator.notification_outbox.verify_audit()["valid"])
            delivered = coordinator.drain_notifications(now_monotonic_ns=2000)
            self.assertEqual(len(delivered), 1)

            replay = coordinator.process(event(), mode="simulate", now_epoch_s=1000.0, now_monotonic_ns=2000)
            self.assertIn(replay.state, {"escalated", "denied"})
            self.assertEqual(len(adapter.requests), 1)

    def test_observe_is_the_default_and_does_not_claim_or_call_an_adapter(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeActionAdapter("success")
            coordinator = build_coordinator(temp, adapter=adapter)
            result = coordinator.process(event(), now_epoch_s=1000.0, now_monotonic_ns=2000)
            self.assertEqual(result.mode, "observe")
            self.assertEqual(result.state, "observed")
            self.assertEqual(adapter.requests, [])
            self.assertIsNone(result.intent_id)
            self.assertEqual(coordinator.state_store.get_intent("missing"), None)

    def test_runtime_execution_gate_blocks_action_intent_after_observer_persistence_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeActionAdapter("success")
            coordinator = build_coordinator(temp, adapter=adapter)
            blocked = event()
            blocked["evidence"]["runtime_gate"] = {
                "execution_allowed": False,
                "reason_codes": ["audit_write_failed_or_capacity_exhausted"],
            }

            result = coordinator.process(blocked, mode="simulate", now_epoch_s=1000.0, now_monotonic_ns=2000)

            self.assertEqual(result.state, "denied")
            self.assertIn("runtime_execution_gate_closed", result.reason_codes)
            self.assertIsNone(result.intent_id)
            self.assertEqual(adapter.requests, [])

    def test_enforce_without_pre_registered_capability_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeActionAdapter("success")
            coordinator = build_coordinator(temp, adapter=adapter)
            result = coordinator.process(event(), mode="enforce", now_epoch_s=1000.0, now_monotonic_ns=2000)
            self.assertEqual(result.state, "denied")
            self.assertIn("capability_missing", result.broker.capability_reason)
            self.assertEqual(adapter.requests, [])
            self.assertEqual(coordinator.state_store.get_intent(result.intent_id)["state"], "PLANNED")

    def test_enforce_consumes_registered_capability_once_and_records_cooldown(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeActionAdapter("success")
            coordinator = build_coordinator(temp, adapter=adapter)
            authorization = Authorization(
                "approval-coordinator-1",
                "local-disposable",
                TARGET,
                "graceful_stop",
                2000.0,
            )
            coordinator.register_capability(authorization)
            result = coordinator.process(
                event(),
                mode="enforce",
                authorization=authorization,
                now_epoch_s=1000.0,
                now_monotonic_ns=2000,
            )
            self.assertEqual(result.state, "manual_handoff")
            self.assertEqual(result.broker.capability_reason, "capability_consumed")
            self.assertEqual(result.broker.semantic_state, "MANUAL_HANDOFF")
            self.assertEqual(len(adapter.requests), 1)

    def test_timeout_is_recorded_and_does_not_automatically_select_another_target(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeActionAdapter("timeout")
            coordinator = build_coordinator(temp, adapter=adapter)
            result = coordinator.process(event(), mode="simulate", now_epoch_s=1000.0, now_monotonic_ns=2000)
            self.assertEqual(result.state, "simulated_plan_complete")
            self.assertEqual(result.broker.action_result.reason, "executor_timeout")
            self.assertEqual(len(adapter.requests), 1)
            self.assertEqual(result.broker.verification.overall_state, "SIMULATED_PLAN")

    def test_failure_is_recorded_as_unmitigated_and_enters_manual_handoff(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeActionAdapter("failure")
            coordinator = build_coordinator(temp, adapter=adapter)
            result = coordinator.process(event(), mode="simulate", now_epoch_s=1000.0, now_monotonic_ns=2000)
            self.assertEqual(result.state, "simulated_plan_complete")
            self.assertEqual(result.broker.action_result.reason, "fake_failure")
            self.assertFalse(result.manual_handoff)
            self.assertEqual(result.broker.verification.overall_state, "SIMULATED_PLAN")

    def test_fake_adapter_rejection_is_recorded_without_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeActionAdapter("rejected")
            coordinator = build_coordinator(temp, adapter=adapter)
            result = coordinator.process(event(), mode="simulate", now_epoch_s=1000.0, now_monotonic_ns=2000)
            self.assertEqual(result.state, "denied")
            self.assertIn("fake_adapter_rejected", result.reason_codes)
            self.assertEqual(len(adapter.requests), 1)
            self.assertEqual(coordinator.state_store.get_intent(result.intent_id)["state"], "PLANNED")

    def test_notification_persistence_failure_blocks_intent_and_adapter(self):
        class FailingOutbox:
            def enqueue_event(self, event, *, now_monotonic_ns=None):
                del event, now_monotonic_ns
                raise NotificationOutboxError("outbox_unavailable")

        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeActionAdapter("success")
            store = GuardianStateStore(Path(temp) / "state.db")
            coordinator = GuardianCoordinator(
                store,
                FailingOutbox(),
                policy=policy(),
                adapter=adapter,
                default_mode="observe",
            )
            result = coordinator.process(event(), mode="simulate", now_epoch_s=1000.0, now_monotonic_ns=2000)
            self.assertEqual(result.state, "escalated")
            self.assertIn("notification_persistence_failed", result.reason_codes)
            self.assertIsNone(result.intent_id)
            self.assertEqual(adapter.requests, [])

    def test_execution_time_identity_recheck_refuses_changed_target(self):
        with tempfile.TemporaryDirectory() as temp:
            adapter = FakeActionAdapter("success")
            coordinator = build_coordinator(temp, adapter=adapter)
            changed = event()
            changed["signals"]["object_registry"]["objects"][0]["cgroup_inode"] = 999
            result = coordinator.process(changed, mode="simulate", now_epoch_s=1000.0, now_monotonic_ns=2000)
            self.assertEqual(result.state, "denied")
            self.assertIn("fresh_target_identity_changed", result.reason_codes)
            self.assertEqual(adapter.requests, [])
            self.assertEqual(coordinator.state_store.get_intent(result.intent_id)["state"], "PLANNED")

    def test_simulate_rejects_non_fake_adapter_before_calling_it(self):
        class UnsafeAdapter:
            def __init__(self):
                self.called = False

            def execute(self, request, now=None):
                del request, now
                self.called = True
                raise AssertionError("simulate must not call a non-fake adapter")

        with tempfile.TemporaryDirectory() as temp:
            adapter = UnsafeAdapter()
            coordinator = build_coordinator(temp, adapter=adapter)
            result = coordinator.process(event(), mode="simulate", now_epoch_s=1000.0, now_monotonic_ns=2000)
            self.assertEqual(result.state, "denied")
            self.assertIn("simulate_requires_fake_adapter", result.reason_codes)
            self.assertFalse(adapter.called)

    def test_restart_reconciles_unknown_intent_and_blocks_any_automatic_replay(self):
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "state.db"
            first_store = GuardianStateStore(state_path)
            intent = action_intent_from_event(event(), now_epoch_s=1000.0)
            claim = first_store.claim_intent(
                host_id=intent.host_id,
                object_id=intent.target_id,
                action=intent.action,
                event_id=intent.event_id,
                audit_payload={"intent": intent.as_dict()},
                now=1000.0,
            )
            self.assertTrue(claim.claimed)
            self.assertTrue(first_store.mark_execution_started(claim.intent_id, now=1001.0))

            adapter = FakeActionAdapter("success")
            coordinator = build_coordinator(temp, adapter=adapter)
            result = coordinator.process(event(), mode="simulate", now_epoch_s=1002.0, now_monotonic_ns=2000)
            self.assertEqual(result.state, "manual_reconciliation_required")
            self.assertIn("startup_reconciliation_required", result.reason_codes)
            self.assertEqual(adapter.requests, [])


if __name__ == "__main__":
    unittest.main()
