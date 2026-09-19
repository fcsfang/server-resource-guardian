"""Guarded action adapter for the Guardian prototype.

This module contains the enforcement boundary, not an automatic policy. Every
real action requires an explicit, short-lived local authorization matching the
target and action. Unit tests inject a fake runner; no real container action is
performed by the test suite.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
CONTAINER_ID = re.compile(r"^[0-9a-f]{12,64}$")
SUPPORTED_ACTIONS = {"graceful_stop", "restart", "terminate"}


class ActionDenied(Exception):
    """Raised when the action safety contract is not satisfied."""


@dataclass(frozen=True)
class Authorization:
    approval_id: str
    environment: str
    target_id: str
    action: str
    expires_at: float


@dataclass(frozen=True)
class ActionRequest:
    event_id: str
    target_id: str
    action: str
    protected: bool
    allowed_actions: frozenset[str] = field(default_factory=frozenset)
    authorization: Authorization | None = None
    timeout_seconds: int = 30


@dataclass(frozen=True)
class ActionResult:
    executed: bool
    action: str
    target_id: str
    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    reason: str = ""


def validate_request(request: ActionRequest, now: float | None = None) -> None:
    """Validate every invariant before a mutation-capable runner is called."""

    if request.action not in SUPPORTED_ACTIONS:
        raise ActionDenied("unsupported_action")
    if not CONTAINER_ID.fullmatch(request.target_id):
        raise ActionDenied("unstable_or_invalid_container_id")
    if request.protected:
        raise ActionDenied("protected_object")
    if request.action not in request.allowed_actions:
        raise ActionDenied("action_not_allowlisted")
    approval = request.authorization
    if approval is None:
        raise ActionDenied("explicit_authorization_required")
    if not approval.approval_id:
        raise ActionDenied("approval_id_missing")
    if approval.environment != "local-disposable":
        raise ActionDenied("environment_not_local_disposable")
    if approval.target_id != request.target_id:
        raise ActionDenied("authorization_target_mismatch")
    if approval.action != request.action:
        raise ActionDenied("authorization_action_mismatch")
    timestamp = time.time() if now is None else now
    if approval.expires_at <= timestamp:
        raise ActionDenied("authorization_expired")
    if not 1 <= request.timeout_seconds <= 120:
        raise ActionDenied("invalid_timeout")


def docker_command(request: ActionRequest) -> list[str]:
    """Build an argument vector; never interpolate a target into a shell."""

    if request.action == "graceful_stop":
        return ["docker", "stop", "--time", str(request.timeout_seconds), request.target_id]
    if request.action == "restart":
        return ["docker", "restart", "--time", str(request.timeout_seconds), request.target_id]
    if request.action == "terminate":
        return ["docker", "kill", request.target_id]
    raise ActionDenied("unsupported_action")


class MockActionExecutor:
    """Record planned actions without invoking a runtime or changing state."""

    def __init__(self) -> None:
        self.requests: list[ActionRequest] = []

    def execute(self, request: ActionRequest) -> ActionResult:
        validate_request(request, now=0.0)
        self.requests.append(request)
        return ActionResult(
            executed=False,
            action=request.action,
            target_id=request.target_id,
            returncode=None,
            reason="mock_only_not_executed",
        )


class DockerActionAdapter:
    """Execute one already-authorized Docker action through an argument list."""

    def __init__(self, runner: CommandRunner = subprocess.run) -> None:
        self.runner = runner

    def execute(self, request: ActionRequest, now: float | None = None) -> ActionResult:
        validate_request(request, now=now)
        command = docker_command(request)
        result = self.runner(command, capture_output=True, text=True, timeout=request.timeout_seconds + 5, check=False)
        return ActionResult(
            executed=True,
            action=request.action,
            target_id=request.target_id,
            returncode=result.returncode,
            stdout=(result.stdout or "").strip(),
            stderr=(result.stderr or "").strip(),
            reason="authorized_runtime_call",
        )


def allowed_actions(values: Iterable[str]) -> frozenset[str]:
    """Normalize an allowlist and reject unknown action names early."""

    normalized = frozenset(values)
    unknown = normalized - SUPPORTED_ACTIONS
    if unknown:
        raise ActionDenied("unknown_allowlist_action:" + ",".join(sorted(unknown)))
    return normalized
