"""Unprivileged client for the independent Guardian Action Broker.

The Runtime imports this module only.  It sends a bounded, versioned request
over a local Unix socket and has no Docker command builder or Docker adapter.
Socket failure before a request is accepted is a denial; a timeout after send
is intentionally an unknown outcome so callers cannot replay the action.
"""

from __future__ import annotations

import json
import math
import socket
from pathlib import Path
from typing import Any, Mapping

from .guardian_actions import ActionDenied, ActionRequest, ActionResult, Authorization


BROKER_REQUEST_SCHEMA = "guardian.action_broker.request.v1"
BROKER_RESPONSE_SCHEMA = "guardian.action_broker.response.v1"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 32 * 1024
MAX_SOCKET_TIMEOUT_SECONDS = 120.0


def _authorization_dict(value: Authorization | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "approval_id": value.approval_id,
        "environment": value.environment,
        "target_id": value.target_id,
        "action": value.action,
        "expires_at": value.expires_at,
    }


def _bounded_json(value: Mapping[str, Any], maximum: int) -> bytes:
    try:
        encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("broker_payload_not_serializable") from exc
    if len(encoded) > maximum:
        raise ValueError("broker_payload_too_large")
    return encoded


def build_broker_request(
    intent: Any,
    request: ActionRequest,
    *,
    intent_id: str,
    mode: str = "enforce",
) -> dict[str, Any]:
    """Build the complete request the independent service must re-check."""

    return {
        "schema": BROKER_REQUEST_SCHEMA,
        "version": 1,
        "mode": mode,
        "intent_id": intent_id,
        "event_id": intent.event_id,
        "sample_id": intent.sample_id,
        "host_id": intent.host_id,
        "resource_kind": intent.resource_kind,
        "idempotency_key": intent.idempotency_key,
        "issued_at": intent.issued_at,
        "expires_at": intent.expires_at,
        "policy_digest": intent.policy_digest,
        "config_digest": intent.config_digest,
        "action": request.action,
        "timeout_seconds": request.timeout_seconds,
        "target": {
            "kind": "container",
            "id": intent.target_id,
            "created_at": intent.target_created_at,
            "cgroup_path": intent.target_cgroup_path,
            "cgroup_inode": intent.target_cgroup_inode,
            "labels": {key: value for key, value in intent.target_labels},
        },
        "authorization": _authorization_dict(request.authorization),
    }


class UnixSocketActionBrokerAdapter:
    """ActionAdapter facade that can only call the independent broker."""

    broker_managed_capability = True

    def __init__(self, socket_path: str | Path, *, timeout_seconds: float = 10.0) -> None:
        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("broker_timeout_invalid") from exc
        if not math.isfinite(timeout) or not 0.1 <= timeout <= MAX_SOCKET_TIMEOUT_SECONDS:
            raise ValueError("broker_timeout_out_of_bounds")
        self.socket_path = str(socket_path)
        self.timeout_seconds = timeout

    def execute(self, request: ActionRequest, now: float | None = None) -> ActionResult:
        del request, now
        raise ActionDenied("broker_context_required")

    def execute_intent(
        self,
        intent: Any,
        request: ActionRequest,
        *,
        intent_id: str,
        now: float | None = None,
    ) -> ActionResult:
        del now
        payload = build_broker_request(intent, request, intent_id=intent_id)
        encoded = _bounded_json(payload, MAX_REQUEST_BYTES)
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
                channel.settimeout(self.timeout_seconds)
                channel.connect(self.socket_path)
                channel.sendall(encoded)
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = channel.recv(min(4096, MAX_RESPONSE_BYTES + 1 - total))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES or b"\n" in chunk:
                        break
        except FileNotFoundError as exc:
            raise ActionDenied("broker_socket_unavailable") from exc
        except ConnectionRefusedError as exc:
            raise ActionDenied("broker_unavailable") from exc
        except socket.timeout as exc:
            raise RuntimeError("broker_outcome_unknown") from exc
        except OSError as exc:
            raise ActionDenied("broker_connection_failed") from exc

        raw = b"".join(chunks)
        if len(raw) > MAX_RESPONSE_BYTES or b"\n" not in raw:
            raise RuntimeError("broker_response_invalid")
        try:
            response = json.loads(raw.split(b"\n", 1)[0].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("broker_response_invalid") from exc
        if not isinstance(response, Mapping) or response.get("schema") != BROKER_RESPONSE_SCHEMA:
            raise RuntimeError("broker_response_schema_invalid")
        status = response.get("status")
        reasons = response.get("reason_codes")
        reason = reasons[0] if isinstance(reasons, list) and reasons and isinstance(reasons[0], str) else "broker_denied"
        if status == "DENIED":
            raise ActionDenied(reason)
        if status == "UNKNOWN":
            raise RuntimeError(reason)
        if status != "EXECUTED":
            raise RuntimeError("broker_response_status_invalid")
        result = response.get("result")
        if not isinstance(result, Mapping):
            raise RuntimeError("broker_result_missing")
        target_id = result.get("target_id")
        action = result.get("action")
        returncode = result.get("returncode")
        if target_id != request.target_id or action != request.action:
            raise RuntimeError("broker_result_identity_mismatch")
        if returncode is not None and (isinstance(returncode, bool) or not isinstance(returncode, int)):
            raise RuntimeError("broker_result_returncode_invalid")
        return ActionResult(
            executed=result.get("executed") is True,
            action=action,
            target_id=target_id,
            returncode=returncode,
            stdout=str(result.get("stdout") or "")[:4096],
            stderr=str(result.get("stderr") or "")[:4096],
            reason=str(result.get("reason") or "broker_executed")[:256],
        )


__all__ = [
    "BROKER_REQUEST_SCHEMA",
    "BROKER_RESPONSE_SCHEMA",
    "MAX_REQUEST_BYTES",
    "MAX_RESPONSE_BYTES",
    "UnixSocketActionBrokerAdapter",
    "build_broker_request",
]
