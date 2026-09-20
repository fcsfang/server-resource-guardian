"""One-shot bridge from an ``enforce`` event snapshot to the controller.

The default executor is mock-only. Real Docker mutation requires both a
short-lived ``local-disposable`` authorization file and the explicit CLI
flags ``--executor docker --confirm-local-disposable``. This module does not
discover production targets or manufacture authorization.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from .guardian_actions import (
    ActionDenied,
    ActionRequest,
    ActionResult,
    Authorization,
    DockerActionAdapter,
    MockActionExecutor,
    SUPPORTED_ACTIONS,
)
from .guardian_controller import ControllerResult, GuardianController
from .guardian_recovery import CooldownLedger, RecoveryObservation, RecoveryPolicy
from .guardian_state import GuardianStateStore


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON object required: {path}")
    return value


def load_authorization(path: Path) -> Authorization:
    data = load_json(path)
    try:
        return Authorization(
            approval_id=str(data["approval_id"]),
            environment=str(data["environment"]),
            target_id=str(data["target_id"]),
            action=str(data["action"]),
            expires_at=float(data["expires_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"invalid authorization file: {path}") from exc


def _state_from_inspect(result: subprocess.CompletedProcess[str]) -> tuple[bool, str | None, str, int | None]:
    if result.returncode != 0:
        return False, None, "absent", None
    try:
        state = json.loads(result.stdout)
    except (TypeError, json.JSONDecodeError):
        return False, None, "invalid", None
    if not isinstance(state, dict):
        return False, None, "invalid", None
    running = bool(state.get("Running", False))
    status = str(state.get("Status") or ("running" if running else "exited"))
    raw_exit_code = state.get("ExitCode")
    try:
        exit_code = int(raw_exit_code) if raw_exit_code is not None else None
    except (TypeError, ValueError):
        exit_code = None
    health: str | None = None
    health_data = state.get("Health")
    if isinstance(health_data, dict) and health_data.get("Status") is not None:
        health = str(health_data["Status"])
    if health is None:
        health = "running" if running else status
    return running, health, status, exit_code


def probe_container_recovery(
    request: ActionRequest,
    runner: CommandRunner,
    *,
    max_wait_seconds: float = 30.0,
    poll_interval_seconds: float = 1.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> RecoveryObservation:
    """Read container state until the action's recovery window expires."""

    started = clock()
    while True:
        result = runner(
            ["docker", "inspect", "--format", "{{json .State}}", request.target_id],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        running, health, status, exit_code = _state_from_inspect(result)
        elapsed = max(clock() - started, 0.0)
        present = result.returncode == 0 and status != "absent"
        if request.action in {"graceful_stop", "terminate"}:
            recovered = not present or not running
        else:
            recovered = present and running and health in {"healthy", "running"}
        if recovered or elapsed >= max_wait_seconds:
            risk_state = "normal" if recovered else "critical"
            return RecoveryObservation(
                target_id=request.target_id,
                target_present=present,
                target_running=running,
                health_status=health,
                risk_state=risk_state,
                observed_after_seconds=elapsed,
                exit_code=exit_code,
            )
        sleep(min(max(poll_interval_seconds, 0.0), max_wait_seconds - elapsed))


def serialize_result(result: ControllerResult) -> dict[str, Any]:
    return {
        "state": result.state,
        "event_id": result.event_id,
        "action_result": asdict(result.action_result) if result.action_result else None,
        "recovery": asdict(result.recovery) if result.recovery else None,
        "reason_codes": list(result.reason_codes),
        "cooldown_state": result.cooldown_state,
        "failure_breaker_tripped": result.failure_breaker_tripped,
        "intent_id": result.intent_id,
    }


def serialize_audit_record(event: dict[str, Any], result: ControllerResult) -> dict[str, Any]:
    """Combine the pre-action event and post-action result into one audit record."""

    return {
        "schema": "guardian.enforce.v1",
        "recorded_at": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": event,
        "result": serialize_result(result),
    }


def run_enforce(
    event: dict[str, Any],
    authorization: Authorization,
    allowed_actions: list[str] | tuple[str, ...] | set[str],
    *,
    executor_kind: str = "mock",
    confirm_local_disposable: bool = False,
    runner: CommandRunner = subprocess.run,
    now: float | None = None,
    recovery_wait_seconds: float = 30.0,
    recovery_poll_seconds: float = 1.0,
    ledger: CooldownLedger | None = None,
    state_store: GuardianStateStore | None = None,
    cooldown_seconds: float = 30.0,
    max_actions: int = 1,
    action_window_seconds: float = 300.0,
    max_consecutive_failures: int = 2,
) -> ControllerResult:
    """Execute one event through a mock or explicitly enabled Docker adapter."""

    if executor_kind not in {"mock", "docker"}:
        raise ValueError("executor_kind must be mock or docker")
    if executor_kind == "docker":
        if not confirm_local_disposable:
            raise ActionDenied("local_disposable_confirmation_required")
        if ledger is None:
            raise ActionDenied("persistent_ledger_required")
        if state_store is None:
            raise ActionDenied("persistent_state_store_required")

    if executor_kind == "mock":
        executor = MockActionExecutor()
        probe = None
    else:
        executor = DockerActionAdapter(runner)

        def probe(request: ActionRequest, _result: ActionResult) -> RecoveryObservation:
            return probe_container_recovery(
                request,
                runner,
                max_wait_seconds=recovery_wait_seconds,
                poll_interval_seconds=recovery_poll_seconds,
            )

    action = event.get("decision", {}).get("action") if isinstance(event.get("decision"), dict) else None
    if action not in SUPPORTED_ACTIONS:
        raise ActionDenied("event_action_not_supported")
    result = GuardianController(executor, ledger=ledger, state_store=state_store).enforce(
        event,
        authorization,
        allowed_actions,
        now=now,
        recovery_policy=RecoveryPolicy(action, max_wait_seconds=recovery_wait_seconds),
        recovery_probe=probe,
        cooldown_seconds=cooldown_seconds,
        max_actions=max_actions,
        window_seconds=action_window_seconds,
        max_consecutive_failures=max_consecutive_failures,
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one guarded Guardian enforce event")
    parser.add_argument("--event-file", type=Path, required=True)
    parser.add_argument("--authorization-file", type=Path, required=True)
    parser.add_argument("--allow-action", action="append", choices=sorted(SUPPORTED_ACTIONS), required=True)
    parser.add_argument("--executor", choices=("mock", "docker"), default="mock")
    parser.add_argument(
        "--confirm-local-disposable",
        action="store_true",
        help="required together with --executor docker; confirms the target is disposable",
    )
    parser.add_argument("--recovery-wait", type=float, default=30.0)
    parser.add_argument("--recovery-poll", type=float, default=1.0)
    parser.add_argument("--cooldown-seconds", type=float, default=30.0)
    parser.add_argument("--max-actions", type=int, default=1)
    parser.add_argument("--action-window-seconds", type=float, default=300.0)
    parser.add_argument("--max-consecutive-failures", type=int, default=2)
    parser.add_argument("--ledger-file", type=Path, help="persistent cooldown/failure ledger; required for docker")
    parser.add_argument("--state-db", type=Path, help="SQLite WAL capability/intent/audit state; required for docker")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        ledger = CooldownLedger.load(args.ledger_file) if args.ledger_file else None
        state_store = GuardianStateStore(args.state_db) if args.state_db else None
        event = load_json(args.event_file)
        result = run_enforce(
            event,
            load_authorization(args.authorization_file),
            args.allow_action,
            executor_kind=args.executor,
            confirm_local_disposable=args.confirm_local_disposable,
            recovery_wait_seconds=args.recovery_wait,
            recovery_poll_seconds=args.recovery_poll,
            ledger=ledger,
            state_store=state_store,
            cooldown_seconds=args.cooldown_seconds,
            max_actions=args.max_actions,
            action_window_seconds=args.action_window_seconds,
            max_consecutive_failures=args.max_consecutive_failures,
        )
        if args.executor == "docker" and args.ledger_file is not None and ledger is not None:
            ledger.save(args.ledger_file)
    except (ActionDenied, OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    serialized = json.dumps(serialize_audit_record(event, result), ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized, end="")


if __name__ == "__main__":
    main()
