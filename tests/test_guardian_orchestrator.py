import tempfile
import time
import unittest
from pathlib import Path

from src.guardian_config import safe_defaults, validate_config
from src.guardian_actions import Authorization
from src.guardian_observer import ObserverSampler
from src.guardian_orchestrator import RuntimeOrchestrator


def quiet_observation() -> dict:
    return {
        "observed_at": "2026-09-21T00:00:00Z",
        "observed_monotonic_ns": time.monotonic_ns(),
        "memory": {
            "available_ratio_percent": 90.0,
            "available_bytes": 9000,
            "total_bytes": 10000,
            "swap_total_bytes": 0,
            "swap_free_bytes": 0,
        },
        "psi": {"memory": {"some": {"avg10": 0.0}, "full": {"avg10": 0.0}}},
        "cgroup": {"memory_events": {}},
        "docker": {"available": True, "containers": []},
        "object_registry": {"objects": []},
        "cpu": {},
        "disk": {},
        "quality": {"status": "ok", "flags": []},
    }


class RecordingCoordinator:
    def __init__(self, *, delay: float = 0.0, reconciliation_required=()):
        self.delay = delay
        self.reconciliation_required = tuple(reconciliation_required)
        self.events = []
        self.drained = 0

    def process(self, event, *, mode, now_monotonic_ns):
        del now_monotonic_ns
        if self.delay:
            time.sleep(self.delay)
        self.events.append((event["event_id"], mode))
        return {"state": "observed", "event_id": event["event_id"], "mode": mode}

    def drain_notifications(self, *, now_monotonic_ns, max_items):
        del now_monotonic_ns, max_items
        self.drained += 1
        return []


class CapabilityRecordingCoordinator(RecordingCoordinator):
    def __init__(self):
        super().__init__()
        self.registered = []

    def register_capability(self, authorization):
        self.registered.append(authorization)

    def process(self, event, *, mode, now_monotonic_ns, authorization=None, verification_provider=None):
        del now_monotonic_ns, authorization, verification_provider
        self.events.append((event["event_id"], mode))
        return {"state": "observed", "event_id": event["event_id"], "mode": mode}


class GuardianOrchestratorTests(unittest.TestCase):
    def build_runtime(self, temp, *, observer=None, coordinator=None, **kwargs):
        config = kwargs.pop("config", None) or safe_defaults()
        return RuntimeOrchestrator(
            config,
            state_db=Path(temp) / "state.db",
            outbox_db=Path(temp) / "outbox.db",
            audit_file=Path(temp) / "audit" / "events.jsonl",
            observer=observer,
            coordinator=coordinator,
            **kwargs,
        )

    def test_observer_sampler_is_stateful_and_produces_runtime_events(self):
        config = safe_defaults()
        sampler = ObserverSampler(config, interval=0.1, collector=lambda **_kwargs: quiet_observation())

        first = sampler.sample()
        second = sampler.sample()

        self.assertEqual(sampler.sample_count, 2)
        self.assertNotEqual(first["event_id"], second["event_id"])
        self.assertEqual(first["schema"], "guardian.risk.event.v1")
        self.assertEqual(first["decision"]["mode"], "observe")
        self.assertEqual(first["decision"]["execution"], "not_applicable")

    def test_real_sampler_events_are_consumed_by_coordinator_in_process(self):
        with tempfile.TemporaryDirectory() as temp:
            sampler = ObserverSampler(
                safe_defaults(),
                interval=0.1,
                collector=lambda **_kwargs: quiet_observation(),
            )
            coordinator = RecordingCoordinator()
            runtime = self.build_runtime(temp, observer=sampler, coordinator=coordinator)

            outcome = runtime.run(max_samples=3)

            self.assertEqual(outcome.status, "STOPPED")
            self.assertEqual(outcome.exit_code, 0)
            self.assertEqual(outcome.stats["sampled"], 3)
            self.assertEqual(outcome.stats["enqueued"], 3)
            self.assertEqual(outcome.stats["processed"], 3)
            self.assertEqual(outcome.stats["readiness"], "ready")
            self.assertEqual(len(coordinator.events), 3)
            self.assertEqual({mode for _event_id, mode in coordinator.events}, {"observe"})
            self.assertEqual(coordinator.drained, 3)
            audit_lines = (Path(temp) / "audit" / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(audit_lines), 3)

    def test_graceful_stop_drains_bounded_queue(self):
        with tempfile.TemporaryDirectory() as temp:
            sampler = ObserverSampler(
                safe_defaults(),
                interval=0.1,
                collector=lambda **_kwargs: quiet_observation(),
            )
            coordinator = RecordingCoordinator(delay=0.05)
            runtime = self.build_runtime(temp, observer=sampler, coordinator=coordinator, queue_capacity=2)
            runtime.start()
            time.sleep(0.25)
            runtime.request_stop("test_sigterm")
            outcome = runtime.join()

            self.assertEqual(outcome.status, "STOPPED")
            self.assertEqual(outcome.exit_code, 0)
            self.assertEqual(outcome.stats["shutdown_reason"], "test_sigterm")
            self.assertLessEqual(outcome.stats["max_queue_depth"], 2)
            self.assertEqual(outcome.stats["enqueued"], outcome.stats["processed"])

    def test_queue_full_is_fail_closed_with_nonzero_outcome(self):
        with tempfile.TemporaryDirectory() as temp:
            sampler = ObserverSampler(
                safe_defaults(),
                interval=0.1,
                collector=lambda **_kwargs: quiet_observation(),
            )
            coordinator = RecordingCoordinator(delay=0.4)
            runtime = self.build_runtime(temp, observer=sampler, coordinator=coordinator, queue_capacity=1)

            outcome = runtime.run(max_samples=20)

            self.assertEqual(outcome.status, "FAILED")
            self.assertEqual(outcome.exit_code, 1)
            self.assertGreaterEqual(outcome.stats["queue_full"], 1)
            self.assertEqual(outcome.stats["fatal_reason"].split(":", 1)[0], "event_queue_full")

    def test_startup_reconciliation_keeps_runtime_not_ready(self):
        with tempfile.TemporaryDirectory() as temp:
            sampler = ObserverSampler(
                safe_defaults(),
                interval=0.1,
                collector=lambda **_kwargs: quiet_observation(),
            )
            coordinator = RecordingCoordinator(
                reconciliation_required=(
                    {"intent_id": "intent-1", "state": "RECONCILIATION_REQUIRED"},
                )
            )
            runtime = self.build_runtime(temp, observer=sampler, coordinator=coordinator)

            outcome = runtime.run(max_samples=1)

            self.assertEqual(outcome.status, "STOPPED")
            self.assertEqual(outcome.stats["startup_reconciliation_count"], 1)
            self.assertEqual(outcome.stats["readiness"], "reconciliation_required")

    def test_enforce_loads_and_registers_one_time_authorization(self):
        with tempfile.TemporaryDirectory() as temp:
            authorization_path = Path(temp) / "authorization.json"
            authorization_path.write_text(
                '{"approval_id":"local-test-1","environment":"local-disposable",'
                '"target_id":"abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",'
                '"action":"graceful_stop","expires_at":4102444800}',
                encoding="utf-8",
            )
            value = safe_defaults().as_dict()
            value["agent"]["mode"] = "enforce"
            value["actions"].update(
                {
                    "enabled": True,
                    "require_approval": True,
                    "authorization_file": str(authorization_path),
                    "allow": ["graceful_stop"],
                }
            )
            config = validate_config(value)
            coordinator = CapabilityRecordingCoordinator()
            runtime = self.build_runtime(
                temp,
                observer=ObserverSampler(
                    config,
                    mode="enforce",
                    interval=0.1,
                    collector=lambda **_kwargs: quiet_observation(),
                ),
                coordinator=coordinator,
                config=config,
                mode="enforce",
            )

            outcome = runtime.run(max_samples=1)

            self.assertEqual(outcome.exit_code, 0)
            self.assertEqual(len(coordinator.registered), 1)
            self.assertIsInstance(coordinator.registered[0], Authorization)
            self.assertEqual(coordinator.registered[0].approval_id, "local-test-1")
            self.assertEqual(coordinator.registered[0].environment, "local-disposable")

    def test_coordinator_failure_stops_runtime_fail_closed(self):
        class FailingCoordinator(RecordingCoordinator):
            def process(self, event, *, mode, now_monotonic_ns):
                del event, mode, now_monotonic_ns
                raise RuntimeError("coordinator fixture failure")

        with tempfile.TemporaryDirectory() as temp:
            sampler = ObserverSampler(
                safe_defaults(),
                interval=0.1,
                collector=lambda **_kwargs: quiet_observation(),
            )
            runtime = self.build_runtime(temp, observer=sampler, coordinator=FailingCoordinator())

            outcome = runtime.run(max_samples=2)

            self.assertEqual(outcome.status, "FAILED")
            self.assertEqual(outcome.exit_code, 1)
            self.assertEqual(outcome.stats["coordinator_errors"], 1)
            self.assertTrue(outcome.stats["fatal_reason"].startswith("coordinator_failed:"))


if __name__ == "__main__":
    unittest.main()
