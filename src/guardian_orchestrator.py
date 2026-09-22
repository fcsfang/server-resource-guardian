"""Continuous in-process Guardian runtime.

The runtime is deliberately a small supervisory layer around the existing
Observer and Coordinator.  It owns the bounded hand-off queue, shutdown
semantics, readiness/watchdog signals and durable startup construction.  It
does not contain a second policy engine and it never creates a real action
adapter: the local runtime uses the Coordinator's fake adapter until a later
task supplies an independent broker.
"""

from __future__ import annotations

import argparse
import json
import queue
import signal
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .guardian_actions import Authorization
from .guardian_coordinator import (
    CoordinatorResult,
    FakeActionAdapter,
    GuardianCoordinator,
    SUPPORTED_MODES,
)
from .guardian_config import ConfigError, GuardianConfig, load_config, safe_defaults
from .guardian_broker_client import UnixSocketActionBrokerAdapter
from .guardian_collector_client import UnixSocketCollectorClient
from .guardian_enforce import load_authorization
from .guardian_emergency_shedding import emergency_shedding_policy_from_config
from .guardian_notification_outbox import DurableNotificationOutbox, NotificationOutboxError
from .guardian_notifications import DisabledNotificationSink
from .guardian_observer import ObserverSampler, _finalize_observer_event, append_audit, collect_observation
from .guardian_recovery import (
    BusinessRecoveryObservation,
    BusinessRecoveryPolicy,
    HostRecoveryObservation,
    HostRecoveryPolicy,
    TwoLayerRecoveryResult,
    assess_two_layer_recovery,
)
from .guardian_revalidation import FreshRevalidation, revalidate_observer_event
from .guardian_reserve_recovery import ReserveRecoveryController, ReserveRecoveryPolicy
from .guardian_runtime import notify_ready, notify_status, notify_watchdog
from .guardian_state import GuardianStateStore


ORCHESTRATOR_SCHEMA = "guardian.runtime.orchestrator.v1"
DEFAULT_QUEUE_CAPACITY = 8
DEFAULT_SHUTDOWN_TIMEOUT_SECONDS = 15.0


@dataclass
class RuntimeStats:
    """Bounded, JSON-safe runtime counters for audit and smoke assertions."""

    sampled: int = 0
    enqueued: int = 0
    processed: int = 0
    queue_full: int = 0
    observer_errors: int = 0
    coordinator_errors: int = 0
    notification_errors: int = 0
    result_audit_errors: int = 0
    max_queue_depth: int = 0
    last_event_id: str | None = None
    last_result_state: str | None = None
    readiness: str = "starting"
    startup_reconciliation_count: int = 0
    shutdown_reason: str | None = None
    fatal_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeOutcome:
    """Terminal status returned by a bounded runtime invocation."""

    schema: str
    status: str
    exit_code: int
    stats: Mapping[str, Any]
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "status": self.status,
            "exit_code": self.exit_code,
            "stats": dict(self.stats),
            "error": self.error,
        }


class RuntimeOrchestrator:
    """Run Observer -> bounded queue -> Coordinator in one process."""

    def __init__(
        self,
        config: GuardianConfig,
        *,
        state_db: Path,
        outbox_db: Path,
        audit_file: Path | None = None,
        snapshot_dir: str | None = None,
        snapshot_all: bool = False,
        mode: str | None = None,
        interval: float | None = None,
        queue_capacity: int = DEFAULT_QUEUE_CAPACITY,
        shutdown_timeout_seconds: float = DEFAULT_SHUTDOWN_TIMEOUT_SECONDS,
        broker_socket: str | Path = "/run/guardian-broker/broker.sock",
        broker_timeout_seconds: float = 10.0,
        reserve_broker_socket: str | Path = "/run/guardian-reserve-broker/reserve.sock",
        collector_socket: str | Path = "/run/guardian-collector/collector.sock",
        authorization_file: str | Path | None = None,
        observer: Any | None = None,
        coordinator: Any | None = None,
        result_callback: Callable[[Mapping[str, Any]], None] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if not isinstance(config, GuardianConfig):
            raise ValueError("config_required")
        if not isinstance(queue_capacity, int) or isinstance(queue_capacity, bool) or not 1 <= queue_capacity <= 1024:
            raise ValueError("queue_capacity_out_of_bounds")
        try:
            shutdown_timeout_seconds = float(shutdown_timeout_seconds)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("shutdown_timeout_seconds_invalid") from exc
        if not 0.1 <= shutdown_timeout_seconds <= 120:
            raise ValueError("shutdown_timeout_seconds_out_of_bounds")
        effective_mode = mode or config.mode
        if effective_mode not in SUPPORTED_MODES:
            raise ValueError("mode_invalid")

        self.config = config
        self.mode = effective_mode
        self.reserve_recovery = ReserveRecoveryController(
            ReserveRecoveryPolicy.from_mapping(config.disk_reserve_recovery_policy),
            broker_socket=reserve_broker_socket,
            config_digest=config.config_digest,
        )
        self.snapshot_dir = snapshot_dir if snapshot_dir is not None else config.snapshot_directory
        self.snapshot_all = bool(snapshot_all)
        self.audit_file = Path(audit_file) if audit_file is not None else None
        self.queue_capacity = queue_capacity
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self.broker_socket = str(broker_socket)
        self.broker_timeout_seconds = float(broker_timeout_seconds)
        self.collector_socket = str(collector_socket)
        self.authorization_file = Path(authorization_file) if authorization_file is not None else (
            Path(config.authorization_file) if config.authorization_file else None
        )
        self.events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=queue_capacity)
        self.stop_event = threading.Event()
        self._fatal_event = threading.Event()
        self._lock = threading.Lock()
        self._observer_lock = threading.Lock()
        self._sleep = sleep or time.sleep
        self._producer: threading.Thread | None = None
        self._consumer: threading.Thread | None = None
        self._started = False
        self._finished = False
        self._stats = RuntimeStats()
        self._error: str | None = None
        self._result_callback = result_callback

        if observer is None:
            collector_client = UnixSocketCollectorClient(self.collector_socket)

            def runtime_collector(**kwargs: Any) -> dict[str, Any]:
                return collect_observation(
                    **kwargs,
                    container_collector=collector_client.snapshot,
                )

            self.observer = ObserverSampler(
                config,
                mode=self.mode,
                interval=interval,
                simulate_action="graceful_stop",
                protected=True,
                collector=runtime_collector,
            )
        else:
            self.observer = observer
        self._owns_coordinator = coordinator is None
        self.state_store: GuardianStateStore | None = None
        self.authorization: Authorization | None = None
        if coordinator is None:
            state_store = GuardianStateStore(Path(state_db), file_mode=0o660)
            self.state_store = state_store
            outbox = DurableNotificationOutbox(Path(outbox_db), DisabledNotificationSink())
            policy = emergency_shedding_policy_from_config(config)

            def fresh_revalidation_provider(intent: Any, original_event: Mapping[str, Any]):
                source = Path(config.source)
                if source.is_file():
                    try:
                        live_config = load_config(source)
                        live_policy = emergency_shedding_policy_from_config(live_config)
                    except (ConfigError, OSError, ValueError):
                        return FreshRevalidation(False, ("live_config_unavailable",))
                    if (
                        live_config.config_digest != config.config_digest
                        or live_policy != policy
                    ):
                        return FreshRevalidation(False, ("live_config_changed",))
                with self._observer_lock:
                    fresh_event = self.observer.sample()
                _finalize_observer_event(
                    fresh_event,
                    config=self.config,
                    snapshot_dir=self.snapshot_dir,
                    snapshot_all=False,
                    audit_file=str(self.audit_file) if self.audit_file is not None else None,
                )
                return revalidate_observer_event(
                    intent,
                    original_event,
                    fresh_event,
                    policy=policy,
                    expected_config_digest=config.config_digest,
                )

            adapter = (
                UnixSocketActionBrokerAdapter(
                    self.broker_socket,
                    timeout_seconds=self.broker_timeout_seconds,
                )
                if self.mode == "enforce"
                else FakeActionAdapter()
            )
            self.coordinator = GuardianCoordinator(
                state_store,
                outbox,
                policy=policy,
                adapter=adapter,
                default_mode=self.mode,
                cooldown_seconds=config.cooldown_seconds,
                max_actions=max(1, config.max_actions_per_host_per_hour),
                action_window_seconds=3600.0,
                fresh_revalidation_provider=fresh_revalidation_provider,
                config_digest=config.config_digest,
            )
        else:
            self.coordinator = coordinator

        if self.mode == "enforce":
            if self.authorization_file is None:
                raise ValueError("enforce_authorization_file_required")
            try:
                self.authorization = load_authorization(self.authorization_file)
            except (OSError, ValueError) as exc:
                raise ValueError(f"authorization_file_invalid:{exc}") from exc
            register_capability = getattr(self.coordinator, "register_capability", None)
            if not callable(register_capability):
                raise ValueError("enforce_capability_registration_unavailable")
            try:
                register_capability(self.authorization)
            except Exception as exc:
                raise ValueError(f"authorization_registration_failed:{exc}") from exc

        pending = getattr(self.coordinator, "reconciliation_required", ())
        self._stats.startup_reconciliation_count = len(tuple(pending or ()))
        if self._stats.startup_reconciliation_count:
            self._stats.readiness = "reconciliation_required"

    @property
    def stats(self) -> RuntimeStats:
        with self._lock:
            return RuntimeStats(**self._stats.as_dict())

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def is_running(self) -> bool:
        return self._started and not self._finished

    def request_stop(self, reason: str = "requested") -> None:
        """Request a bounded graceful stop; queued events are drained."""

        with self._lock:
            if self._stats.shutdown_reason is None:
                self._stats.shutdown_reason = str(reason)[:128]
        self.stop_event.set()
        notify_status(f"runtime:stopping:{str(reason)[:64]}")

    def _fail_closed(self, reason: str, exc: BaseException | None = None) -> None:
        detail = str(exc)[:256] if exc is not None else ""
        error = f"{reason}:{detail}" if detail else reason
        with self._lock:
            self._error = error
            self._stats.fatal_reason = error
            self._stats.readiness = "failed"
        self._fatal_event.set()
        self.stop_event.set()
        self._discard_queued_events()
        notify_status(f"runtime:failed:{reason}")

    def _discard_queued_events(self) -> None:
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                return
            else:
                self.events.task_done()

    def _update_queue_depth(self) -> None:
        with self._lock:
            self._stats.max_queue_depth = max(self._stats.max_queue_depth, self.events.qsize())

    def _enqueue(self, event: dict[str, Any]) -> bool:
        while not self.stop_event.is_set():
            try:
                self.events.put(event, timeout=0.1)
                with self._lock:
                    self._stats.enqueued += 1
                self._update_queue_depth()
                return True
            except queue.Full:
                with self._lock:
                    self._stats.queue_full += 1
                self._fail_closed("event_queue_full")
                return False
        return False

    def _producer_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                with self._observer_lock:
                    event = self.observer.sample()
                if not isinstance(event, dict):
                    raise ValueError("observer_event_mapping_required")
                _finalize_observer_event(
                    event,
                    config=self.config,
                    snapshot_dir=self.snapshot_dir,
                    snapshot_all=self.snapshot_all,
                    audit_file=str(self.audit_file) if self.audit_file is not None else None,
                )
                with self._lock:
                    self._stats.sampled += 1
                    self._stats.last_event_id = str(event.get("event_id") or "")
                if not self._enqueue(event):
                    return
                if getattr(self, "max_samples", None) is not None and self.stats.sampled >= self.max_samples:
                    self.request_stop("sample_limit")
                    return
            except Exception as exc:
                with self._lock:
                    self._stats.observer_errors += 1
                self._fail_closed("observer_failed", exc)
                return
            interval = getattr(self.observer, "interval", self.config.interval_seconds)
            try:
                interval = max(0.1, min(60.0, float(interval)))
            except (TypeError, ValueError, OverflowError):
                self._fail_closed("observer_interval_invalid")
                return
            if self.stop_event.wait(interval):
                return

    def _mark_ready(self) -> None:
        with self._lock:
            if self._stats.readiness != "starting":
                return
            self._stats.readiness = "ready"
        notify_ready(f"runtime:ready:{self.mode}")

    @staticmethod
    def _nested_number(value: Any, *keys: str) -> float | None:
        current = value
        for key in keys:
            if not isinstance(current, Mapping):
                return None
            current = current.get(key)
        return float(current) if isinstance(current, (int, float)) and not isinstance(current, bool) else None

    @staticmethod
    def _oom_total(event: Mapping[str, Any]) -> int | None:
        signals = event.get("signals") if isinstance(event.get("signals"), Mapping) else {}
        cgroup = signals.get("cgroup") if isinstance(signals.get("cgroup"), Mapping) else {}
        counters = cgroup.get("memory_events") if isinstance(cgroup.get("memory_events"), Mapping) else {}
        values = [counters.get(key) for key in ("oom", "oom_kill")]
        if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in values):
            return None
        return sum(values)

    @staticmethod
    def _resource_risk(event: Mapping[str, Any], resource_kind: str) -> Mapping[str, Any]:
        evaluations = event.get("resource_evaluations") if isinstance(event.get("resource_evaluations"), Mapping) else {}
        evaluation = evaluations.get(resource_kind) if isinstance(evaluations.get(resource_kind), Mapping) else {}
        risk = evaluation.get("risk") if isinstance(evaluation.get("risk"), Mapping) else {}
        return risk

    @staticmethod
    def _target_after(event: Mapping[str, Any], target_id: str) -> tuple[bool, bool, str | None]:
        signals = event.get("signals") if isinstance(event.get("signals"), Mapping) else {}
        registry = signals.get("object_registry") if isinstance(signals.get("object_registry"), Mapping) else {}
        objects = registry.get("objects") if isinstance(registry.get("objects"), list) else []
        matches = [item for item in objects if isinstance(item, Mapping) and item.get("id") == target_id]
        if len(matches) != 1:
            return False, False, None
        item = matches[0]
        status = str(item.get("status") or "unknown").lower()
        running = status in {"running", "restarting"}
        health = item.get("health")
        if not isinstance(health, str) or not health:
            health = status
        return True, running, health

    def _verification_provider(self, before_event: Mapping[str, Any]):
        """Build an action-time verifier from the same read-only Collector path."""

        def verify(intent: Any, _action_result: Any) -> TwoLayerRecoveryResult:
            started = time.monotonic()
            latest: TwoLayerRecoveryResult | None = None
            while True:
                with self._observer_lock:
                    after_event = self.observer.sample()
                elapsed = max(time.monotonic() - started, 0.0)
                before_risk = self._resource_risk(before_event, intent.resource_kind)
                after_risk = self._resource_risk(after_event, intent.resource_kind)
                before_signals = before_event.get("signals") if isinstance(before_event.get("signals"), Mapping) else {}
                after_signals = after_event.get("signals") if isinstance(after_event.get("signals"), Mapping) else {}
                before_memory = before_signals.get("memory") if isinstance(before_signals.get("memory"), Mapping) else {}
                after_memory = after_signals.get("memory") if isinstance(after_signals.get("memory"), Mapping) else {}
                before_psi = before_signals.get("psi") if isinstance(before_signals.get("psi"), Mapping) else {}
                after_psi = after_signals.get("psi") if isinstance(after_signals.get("psi"), Mapping) else {}
                target_present, target_running, health = self._target_after(after_event, intent.target_id)
                host = HostRecoveryObservation(
                    before_available_percent=self._nested_number(before_memory, "available_ratio_percent"),
                    after_available_percent=self._nested_number(after_memory, "available_ratio_percent"),
                    before_psi_full_avg10=self._nested_number(before_psi, "memory", "full", "avg10"),
                    after_psi_full_avg10=self._nested_number(after_psi, "memory", "full", "avg10"),
                    before_oom_events=self._oom_total(before_event),
                    after_oom_events=self._oom_total(after_event),
                    after_risk_state=str(after_risk.get("state") or after_event.get("state") or "unknown"),
                    observed_after_seconds=elapsed,
                    resource_kind=intent.resource_kind,
                    before_resource_state=str(before_risk.get("state") or before_event.get("state") or "unknown"),
                    after_resource_state=str(after_risk.get("state") or after_event.get("state") or "unknown"),
                )
                business = BusinessRecoveryObservation(
                    target_id=intent.target_id,
                    target_present=target_present,
                    target_running=target_running,
                    health_status=health,
                    # Stopping the target is not a business health check.  A
                    # real probe must be supplied by a future integration.
                    probe_ok=None,
                    observed_after_seconds=elapsed,
                )
                latest = assess_two_layer_recovery(
                    HostRecoveryPolicy(max_wait_seconds=self.config.recovery_max_wait_seconds),
                    host,
                    BusinessRecoveryPolicy(
                        max_wait_seconds=self.config.recovery_max_wait_seconds,
                        require_probe=self.config.require_business_health,
                        action=intent.action,
                    ),
                    business,
                    expected_target_id=intent.target_id,
                )
                # Host containment and business recovery are independent.  A
                # stopped disposable target can mitigate the resource risk
                # while business health remains unconfirmed because no probe
                # is configured.  Once host mitigation is proven, preserve
                # that result and hand business confirmation to the operator
                # instead of waiting until the host window expires and
                # overwriting MITIGATED with a timeout failure.
                if latest.host_mitigated:
                    return latest
                if elapsed >= self.config.recovery_max_wait_seconds:
                    return latest
                self._sleep(min(self.config.recovery_poll_interval_seconds, self.config.recovery_max_wait_seconds - elapsed))

        return verify

    def _append_result_audit(self, event: Mapping[str, Any], result_payload: Mapping[str, Any]) -> None:
        if self.audit_file is None or not self._owns_coordinator:
            return
        record = dict(event)
        record["schema"] = "guardian.runtime.result.v1"
        record["runtime_result"] = dict(result_payload)
        record["recorded_at"] = time.time()
        if not append_audit(record, self.audit_file, max_total_bytes=self.config.audit_max_total_bytes):
            with self._lock:
                self._stats.result_audit_errors += 1

    def _consume_one(self, event: dict[str, Any]) -> None:
        try:
            reserve_plan = self.reserve_recovery.handle(event, mode=self.mode, execute=False)
        except TypeError as exc:
            # Keep injected legacy test doubles source-compatible while the
            # real controller uses the explicit plan/execute split.
            if "execute" not in str(exc):
                raise
            reserve_plan = self.reserve_recovery.handle(event, mode=self.mode)
        reserve_actionable = reserve_plan.get("action") != "none" and reserve_plan.get("state") in {
            "observed",
            "simulated",
            "authorized",
        }
        reserve_result = reserve_plan
        if reserve_actionable and self.mode in {"simulate", "enforce"}:
            try:
                enqueue_notification = getattr(self.coordinator, "enqueue_notification_event", None)
                if not callable(enqueue_notification):
                    raise NotificationOutboxError("notification_outbox_unavailable")
                else:
                    notification = enqueue_notification(
                        event,
                        incident_key=str(event.get("incident_id") or event.get("event_id") or "reserve"),
                        now_monotonic_ns=time.monotonic_ns(),
                    )
            except (NotificationOutboxError, ValueError, TypeError) as exc:
                notification = {"status": "rejected", "reason": f"notification_persistence_failed:{exc}"}
            if notification.get("status") != "queued":
                reserve_result = {
                    **reserve_plan,
                    "state": "blocked",
                    "execution": "not_executed",
                    "reason_codes": ["notification_persistence_failed", str(notification.get("reason") or notification.get("status"))],
                }
            elif self.mode == "enforce":
                reserve_result = self.reserve_recovery.handle(event, mode=self.mode, execute=True)
                result_state_after_action = "recovered" if reserve_result.get("state") == "released" else "escalated"
                result_event = dict(event)
                result_event["event_id"] = f"{event.get('event_id')}:reserve-result"
                result_event["state"] = result_state_after_action
                result_event["decision"] = {
                    "action": reserve_result.get("action"),
                    "mode": self.mode,
                    "target_state": reserve_result.get("state"),
                    "reason_codes": reserve_result.get("reason_codes", []),
                }
                try:
                    enqueue_notification = getattr(self.coordinator, "enqueue_notification_event", None)
                    if not callable(enqueue_notification):
                        raise NotificationOutboxError("notification_outbox_unavailable")
                    else:
                        result_notification = enqueue_notification(
                            result_event,
                            incident_key=str(event.get("incident_id") or event.get("event_id") or "reserve"),
                            now_monotonic_ns=time.monotonic_ns(),
                        )
                except (NotificationOutboxError, ValueError, TypeError) as exc:
                    result_notification = {"status": "rejected", "reason": f"notification_persistence_failed:{exc}"}
                if result_notification.get("status") != "queued" and reserve_result.get("execution") == "executed":
                    reserve_result = {
                        **reserve_result,
                        "state": "unknown",
                        "execution": "unknown",
                        "reason_codes": [
                            *list(reserve_result.get("reason_codes", [])),
                            "reserve_result_notification_persistence_failed",
                            "manual_reconciliation_required",
                        ],
                    }
            reserve_execution = str(reserve_result.get("execution") or "not_executed")
            reserve_state = str(reserve_result.get("state") or "blocked")
            semantic_state = (
                "SIMULATED_PLAN_COMPLETE"
                if self.mode == "simulate" and reserve_execution == "not_executed"
                else "MITIGATED"
                if reserve_state == "released"
                else "MANUAL_HANDOFF"
            )
            execution_semantics = (
                "SIMULATED"
                if self.mode == "simulate"
                else "REAL"
                if reserve_execution == "executed"
                else "UNKNOWN"
                if reserve_execution == "unknown"
                else "NOT_EXECUTED"
            )
            result_payload: Mapping[str, Any] = {
                "schema": "guardian.coordinator.result.v1",
                "mode": self.mode,
                "state": "simulated_plan_complete" if self.mode == "simulate" and reserve_result.get("execution") == "not_executed" else ("observed" if reserve_result.get("execution") == "not_executed" else ("recovered" if reserve_result.get("state") == "released" else "manual_handoff")),
                "event_id": str(event.get("event_id") or ""),
                "reason_codes": list(reserve_result.get("reason_codes", [])),
                "notification": notification,
                "intent": None,
                "intent_id": None,
                "broker": None,
                "manual_handoff": self.mode == "enforce" and reserve_result.get("state") != "released",
                "semantic_state": semantic_state,
                "execution_semantics": execution_semantics,
                "host_state": "reserve_released" if reserve_result.get("state") == "released" else "pending",
                "business_state": "BUSINESS_DEGRADED",
                "state_trace": ["CRITICAL_CONFIRMED", semantic_state],
            }
            result_state = str(result_payload["state"])
        else:
            process_kwargs: dict[str, Any] = {
                "mode": self.mode,
                "now_monotonic_ns": time.monotonic_ns(),
            }
            if self._owns_coordinator:
                process_kwargs["authorization"] = self.authorization
                if self.mode == "enforce":
                    process_kwargs["verification_provider"] = self._verification_provider(event)
            result = self.coordinator.process(event, **process_kwargs)
            if isinstance(result, CoordinatorResult):
                result_payload = result.as_dict()
                result_state = result.state
            elif callable(getattr(result, "as_dict", None)):
                result_payload = result.as_dict()
                result_state = str(result_payload.get("state") or "unknown")
            elif isinstance(result, Mapping):
                result_payload = result
                result_state = str(result.get("state") or "unknown")
            else:
                raise ValueError("coordinator_result_invalid")
        result_payload = dict(result_payload)
        result_payload["reserve_recovery"] = reserve_result
        with self._lock:
            self._stats.processed += 1
            self._stats.last_result_state = result_state
        self._append_result_audit(event, result_payload)
        if self._result_callback is not None:
            self._result_callback(result_payload)
        self.coordinator.drain_notifications(
            now_monotonic_ns=time.monotonic_ns(),
            max_items=1,
        )
        notify_watchdog(f"runtime:processed:{result_state}")
        if self._stats.startup_reconciliation_count == 0:
            self._mark_ready()

    def _consumer_loop(self) -> None:
        while True:
            if self._fatal_event.is_set():
                self._discard_queued_events()
                return
            try:
                event = self.events.get(timeout=0.1)
            except queue.Empty:
                if self.stop_event.is_set():
                    return
                continue
            try:
                self._consume_one(event)
            except Exception as exc:
                with self._lock:
                    self._stats.coordinator_errors += 1
                self._fail_closed("coordinator_failed", exc)
                return
            finally:
                self.events.task_done()

    def start(self, *, max_samples: int | None = None) -> None:
        if self._started:
            raise RuntimeError("runtime_already_started")
        if max_samples is not None and (
            isinstance(max_samples, bool) or not isinstance(max_samples, int) or not 1 <= max_samples <= 100_000
        ):
            raise ValueError("max_samples_out_of_bounds")
        self.max_samples = max_samples
        self._started = True
        if self._stats.startup_reconciliation_count:
            notify_status("runtime:reconciliation_required")
        else:
            notify_status("runtime:starting")
        self._producer = threading.Thread(target=self._producer_loop, name="guardian-observer", daemon=False)
        self._consumer = threading.Thread(target=self._consumer_loop, name="guardian-coordinator", daemon=False)
        self._consumer.start()
        self._producer.start()

    def join(self) -> RuntimeOutcome:
        if not self._started:
            raise RuntimeError("runtime_not_started")
        assert self._producer is not None
        assert self._consumer is not None
        self._producer.join(timeout=self.shutdown_timeout_seconds)
        if self._producer.is_alive():
            self._fail_closed("producer_shutdown_timeout")
        self._consumer.join(timeout=self.shutdown_timeout_seconds)
        if self._consumer.is_alive():
            self._fail_closed("consumer_shutdown_timeout")
        self._finished = True
        stats = self.stats
        if self.error is not None:
            status = "FAILED"
            exit_code = 1
        else:
            status = "STOPPED"
            exit_code = 0
        return RuntimeOutcome(ORCHESTRATOR_SCHEMA, status, exit_code, stats.as_dict(), self.error)

    def run(self, *, max_samples: int | None = None) -> RuntimeOutcome:
        self.start(max_samples=max_samples)
        try:
            while self._producer is not None and self._consumer is not None and (
                self._producer.is_alive() or self._consumer.is_alive()
            ):
                self._sleep(0.1)
        except KeyboardInterrupt:
            self.request_stop("keyboard_interrupt")
        finally:
            if not self.stop_event.is_set():
                self.request_stop("runtime_completed")
        return self.join()


def _default_path(value: str) -> Path:
    return Path(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Continuous Guardian Observer -> Coordinator runtime")
    parser.add_argument("--config", type=Path, help="strict JSON config; absent means safe observe-only defaults")
    parser.add_argument("--state-db", type=Path, default=Path("/var/lib/guardian/shared/state.db"))
    parser.add_argument("--outbox-db", type=Path, default=Path("/var/lib/guardian/runtime/outbox.db"))
    parser.add_argument("--audit-file", type=Path, default=Path("/var/lib/guardian/runtime/audit/events.jsonl"))
    parser.add_argument("--snapshot-dir", default=None)
    parser.add_argument("--snapshot-all", action="store_true")
    parser.add_argument("--mode", choices=SUPPORTED_MODES, default=None)
    parser.add_argument("--interval", type=float, default=None)
    parser.add_argument("--queue-capacity", type=int, default=DEFAULT_QUEUE_CAPACITY)
    parser.add_argument("--samples", type=int, default=None, help="bounded smoke limit; omit for continuous service")
    parser.add_argument("--shutdown-timeout", type=float, default=DEFAULT_SHUTDOWN_TIMEOUT_SECONDS)
    parser.add_argument("--broker-socket", default="/run/guardian-broker/broker.sock")
    parser.add_argument("--broker-timeout", type=float, default=10.0)
    parser.add_argument("--reserve-broker-socket", default="/run/guardian-reserve-broker/reserve.sock")
    parser.add_argument("--collector-socket", default="/run/guardian-collector/collector.sock")
    parser.add_argument("--authorization-file", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config) if args.config else safe_defaults()
        runtime = RuntimeOrchestrator(
            config,
            state_db=args.state_db,
            outbox_db=args.outbox_db,
            audit_file=args.audit_file,
            snapshot_dir=args.snapshot_dir,
            snapshot_all=args.snapshot_all,
            mode=args.mode,
            interval=args.interval,
            queue_capacity=args.queue_capacity,
            shutdown_timeout_seconds=args.shutdown_timeout,
            broker_socket=args.broker_socket,
            broker_timeout_seconds=args.broker_timeout,
            reserve_broker_socket=args.reserve_broker_socket,
            collector_socket=args.collector_socket,
            authorization_file=args.authorization_file,
        )
    except (ConfigError, OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"schema": ORCHESTRATOR_SCHEMA, "status": "FAILED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    def stop_handler(signum: int, _frame: Any) -> None:
        runtime.request_stop(signal.Signals(signum).name.lower())

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)
    outcome = runtime.run(max_samples=args.samples)
    print(json.dumps(outcome.as_dict(), ensure_ascii=False), flush=True)
    return outcome.exit_code


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_QUEUE_CAPACITY",
    "ORCHESTRATOR_SCHEMA",
    "RuntimeOrchestrator",
    "RuntimeOutcome",
    "RuntimeStats",
    "main",
]
