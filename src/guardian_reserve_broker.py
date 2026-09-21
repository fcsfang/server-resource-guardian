"""Minimal privileged boundary for releasing Guardian's own disk reserve.

This boundary is separate from the Docker action Broker.  It is disabled by
default, accepts exactly one fixed action and path, runs as a root-owned
systemd service with no Docker access, and records a durable incident claim
before invoking the fixed root helper.  An in-flight or failed incident is
never replayed automatically.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import pwd
import signal
import socket
import stat
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from .guardian_actions import Authorization
from .guardian_config import ConfigError, load_config
from .guardian_reserve_recovery import RESERVE_ACTION, RESERVE_ROOT, ReserveRecoveryPolicy
from .guardian_state import GuardianStateStore, StateStoreError


RESERVE_BROKER_REQUEST_SCHEMA = "guardian.reserve_broker.request.v1"
RESERVE_BROKER_RESPONSE_SCHEMA = "guardian.reserve_broker.response.v1"
MAX_REQUEST_BYTES = 16 * 1024
MAX_RESPONSE_BYTES = 16 * 1024
MAX_TEXT = 256
FIXED_HELPER = "/usr/local/sbin/guardian-release-emergency-space"
_AUTH_KEYS = {"approval_id", "environment", "target_id", "action", "expires_at"}
_REQUEST_KEYS = {
    "schema",
    "version",
    "mode",
    "incident_id",
    "idempotency_key",
    "issued_at",
    "expires_at",
    "config_digest",
    "action",
    "target",
    "authorization",
}
_TARGET_KEYS = {"root", "mount_point"}


class ReserveBrokerProtocolError(ValueError):
    """Raised when a reserve request must not reach the root helper."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
        raise ReserveBrokerProtocolError(f"{field}_invalid")
    return value


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReserveBrokerProtocolError(f"{field}_invalid")
    result = float(value)
    if not math.isfinite(result):
        raise ReserveBrokerProtocolError(f"{field}_invalid")
    return result


def _bounded_json(value: Mapping[str, Any], maximum: int) -> bytes:
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > maximum:
        raise ReserveBrokerProtocolError("reserve_broker_payload_too_large")
    return encoded


def _authorization(value: Any) -> Authorization:
    if not isinstance(value, Mapping) or set(value) != _AUTH_KEYS:
        raise ReserveBrokerProtocolError("authorization_invalid")
    return Authorization(
        approval_id=_text(value.get("approval_id"), "approval_id"),
        environment=_text(value.get("environment"), "environment"),
        target_id=_text(value.get("target_id"), "authorization_target_id"),
        action=_text(value.get("action"), "authorization_action"),
        expires_at=_number(value.get("expires_at"), "authorization_expires_at"),
    )


def _read_line(channel: socket.socket) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = channel.recv(min(4096, MAX_REQUEST_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_REQUEST_BYTES:
            raise ReserveBrokerProtocolError("request_too_large")
        if b"\n" in chunk:
            break
    raw = b"".join(chunks)
    if len(raw) > MAX_REQUEST_BYTES or b"\n" not in raw:
        raise ReserveBrokerProtocolError("request_framing_invalid")
    return raw.split(b"\n", 1)[0]


def _peer_uid(channel: socket.socket) -> int:
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        raise ReserveBrokerProtocolError("peer_identity_unsupported")
    try:
        raw = channel.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", raw)
    except (OSError, struct.error) as exc:
        raise ReserveBrokerProtocolError("peer_identity_unavailable") from exc
    return uid


def _authorization_dict(value: Authorization) -> dict[str, Any]:
    return {
        "approval_id": value.approval_id,
        "environment": value.environment,
        "target_id": value.target_id,
        "action": value.action,
        "expires_at": value.expires_at,
    }


def build_reserve_request(
    *,
    incident_id: str,
    idempotency_key: str,
    issued_at: float,
    expires_at: float,
    config_digest: str,
    authorization: Authorization,
    mount_point: str,
) -> dict[str, Any]:
    return {
        "schema": RESERVE_BROKER_REQUEST_SCHEMA,
        "version": 1,
        "mode": "enforce",
        "incident_id": incident_id,
        "idempotency_key": idempotency_key,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "config_digest": config_digest,
        "action": RESERVE_ACTION,
        "target": {"root": str(RESERVE_ROOT), "mount_point": mount_point},
        "authorization": _authorization_dict(authorization),
    }


class ReserveRecoveryBrokerClient:
    """Unprivileged client used by the Guardian Runtime."""

    def __init__(self, socket_path: str | Path = "/run/guardian-reserve-broker/reserve.sock", *, timeout_seconds: float = 10.0) -> None:
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or not 0.1 <= timeout <= 120:
            raise ValueError("reserve_broker_timeout_invalid")
        self.socket_path = str(socket_path)
        self.timeout_seconds = timeout

    def release(
        self,
        *,
        incident_id: str,
        authorization: Authorization,
        config_digest: str,
        mount_point: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        issued_at = time.time() if now is None else float(now)
        expires_at = min(float(authorization.expires_at), issued_at + 300.0)
        payload = build_reserve_request(
            incident_id=incident_id,
            idempotency_key=hashlib.sha256(f"reserve:{incident_id}".encode("utf-8")).hexdigest(),
            issued_at=issued_at,
            expires_at=expires_at,
            config_digest=config_digest,
            authorization=authorization,
            mount_point=mount_point,
        )
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
        except FileNotFoundError:
            return {"action": RESERVE_ACTION, "state": "blocked", "execution": "not_executed", "reason_codes": ["reserve_broker_unavailable"]}
        except socket.timeout:
            return {"action": RESERVE_ACTION, "state": "unknown", "execution": "unknown", "reason_codes": ["reserve_broker_outcome_unknown"]}
        except (ConnectionRefusedError, OSError):
            return {"action": RESERVE_ACTION, "state": "blocked", "execution": "not_executed", "reason_codes": ["reserve_broker_connection_failed"]}
        raw = b"".join(chunks)
        if len(raw) > MAX_RESPONSE_BYTES or b"\n" not in raw:
            return {"action": RESERVE_ACTION, "state": "unknown", "execution": "unknown", "reason_codes": ["reserve_broker_response_invalid"]}
        try:
            response = json.loads(raw.split(b"\n", 1)[0].decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {"action": RESERVE_ACTION, "state": "unknown", "execution": "unknown", "reason_codes": ["reserve_broker_response_invalid"]}
        if not isinstance(response, Mapping) or response.get("schema") != RESERVE_BROKER_RESPONSE_SCHEMA:
            return {"action": RESERVE_ACTION, "state": "unknown", "execution": "unknown", "reason_codes": ["reserve_broker_response_schema_invalid"]}
        reasons = response.get("reason_codes")
        result = response.get("result")
        payload_result = dict(result) if isinstance(result, Mapping) else {}
        status = response.get("status")
        if status == "EXECUTED":
            state = "released"
            execution = "executed"
        elif status == "UNKNOWN":
            state = "unknown"
            execution = "unknown"
        else:
            state = "blocked"
            execution = "not_executed"
        return {
            "action": RESERVE_ACTION,
            "state": state,
            "execution": execution,
            **payload_result,
            "reason_codes": list(reasons) if isinstance(reasons, list) else ["reserve_broker_denied"],
        }


class ReserveRecoveryBrokerServer:
    """Root-owned, fixed-scope reserve release server."""

    def __init__(
        self,
        socket_path: str | Path,
        *,
        state_store: GuardianStateStore,
        config_path: Path,
        enabled: bool = False,
        allowed_uid: int | str = "guardian",
        runner: Any = subprocess.run,
        helper_path: str = FIXED_HELPER,
        peer_uid_reader: Any = _peer_uid,
    ) -> None:
        self.socket_path = Path(socket_path)
        self.state_store = state_store
        self.config_path = Path(config_path)
        self.enabled = bool(enabled)
        self.allowed_uid = self._resolve_uid(allowed_uid)
        self.runner = runner
        self.peer_uid_reader = peer_uid_reader
        if helper_path != FIXED_HELPER:
            raise ValueError("reserve_helper_path_is_fixed")
        self.helper_path = FIXED_HELPER
        self._stop = threading.Event()
        self._server_socket: socket.socket | None = None

    @staticmethod
    def _resolve_uid(value: int | str) -> int:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        if isinstance(value, str) and value:
            try:
                return int(value)
            except ValueError:
                try:
                    return pwd.getpwnam(value).pw_uid
                except KeyError as exc:
                    raise ValueError("reserve_allowed_uid_invalid") from exc
        raise ValueError("reserve_allowed_uid_invalid")

    def stop(self) -> None:
        self._stop.set()
        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except OSError:
                pass

    def _respond(self, channel: socket.socket, status: str, reasons: list[str], result: Mapping[str, Any] | None = None) -> None:
        payload = {
            "schema": RESERVE_BROKER_RESPONSE_SCHEMA,
            "version": 1,
            "status": status,
            "reason_codes": list(dict.fromkeys(reasons))[:16],
            "result": dict(result) if result is not None else None,
        }
        try:
            channel.sendall(_bounded_json(payload, MAX_RESPONSE_BYTES))
        except (OSError, ReserveBrokerProtocolError):
            pass

    def _load_policy(self) -> tuple[ReserveRecoveryPolicy, str]:
        try:
            config = load_config(self.config_path)
        except (ConfigError, OSError) as exc:
            raise ReserveBrokerProtocolError("reserve_config_invalid") from exc
        policy = ReserveRecoveryPolicy.from_mapping(config.disk_reserve_recovery_policy)
        if not policy.enabled or policy.root != RESERVE_ROOT or policy.mount_point != "/" or policy.max_releases_per_incident != 1:
            raise ReserveBrokerProtocolError("reserve_policy_not_enabled_or_fixed")
        return policy, config.config_digest

    def _validate(self, request: Mapping[str, Any], *, now: float) -> tuple[str, Authorization, ReserveRecoveryPolicy]:
        if set(request) != _REQUEST_KEYS or request.get("schema") != RESERVE_BROKER_REQUEST_SCHEMA or request.get("version") != 1:
            raise ReserveBrokerProtocolError("request_schema_invalid")
        if request.get("mode") != "enforce" or request.get("action") != RESERVE_ACTION:
            raise ReserveBrokerProtocolError("reserve_action_not_allowlisted")
        incident_id = _text(request.get("incident_id"), "incident_id")
        idempotency_key = _text(request.get("idempotency_key"), "idempotency_key")
        if len(idempotency_key) != 64 or any(character not in "0123456789abcdef" for character in idempotency_key):
            raise ReserveBrokerProtocolError("idempotency_key_invalid")
        issued_at = _number(request.get("issued_at"), "issued_at")
        expires_at = _number(request.get("expires_at"), "expires_at")
        if expires_at <= now or issued_at > now or expires_at <= issued_at or expires_at - issued_at > 300:
            raise ReserveBrokerProtocolError("request_expired_or_ttl_invalid")
        policy, config_digest = self._load_policy()
        if request.get("config_digest") != config_digest:
            raise ReserveBrokerProtocolError("reserve_config_digest_mismatch")
        target = request.get("target")
        if not isinstance(target, Mapping) or set(target) != _TARGET_KEYS or target.get("root") != str(RESERVE_ROOT) or target.get("mount_point") != policy.mount_point:
            raise ReserveBrokerProtocolError("reserve_target_not_fixed")
        authorization = _authorization(request.get("authorization"))
        if authorization.environment != "local-disposable" or authorization.target_id != str(RESERVE_ROOT) or authorization.action != RESERVE_ACTION or authorization.expires_at <= now:
            raise ReserveBrokerProtocolError("reserve_authorization_invalid")
        configured_auth = policy.authorization_file
        if configured_auth is None:
            raise ReserveBrokerProtocolError("reserve_authorization_file_missing")
        try:
            configured_value = json.loads(configured_auth.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ReserveBrokerProtocolError("reserve_authorization_file_invalid") from exc
        if not isinstance(configured_value, Mapping) or dict(configured_value) != _authorization_dict(authorization):
            raise ReserveBrokerProtocolError("reserve_authorization_file_mismatch")
        return incident_id, authorization, policy

    def _handle(self, channel: socket.socket) -> None:
        try:
            channel.settimeout(10.0)
            if self.peer_uid_reader(channel) != self.allowed_uid:
                self._respond(channel, "DENIED", ["peer_identity_denied"])
                return
            if not self.enabled:
                self._respond(channel, "DENIED", ["reserve_broker_disabled"])
                return
            request = json.loads(_read_line(channel).decode("utf-8"))
            if not isinstance(request, Mapping):
                raise ReserveBrokerProtocolError("request_mapping_required")
            now = time.time()
            incident_id, _authorization_value, policy = self._validate(request, now=now)
            claim = self.state_store.claim_reserve_incident(
                incident_id=incident_id,
                action=RESERVE_ACTION,
                target=str(policy.root),
                now=now,
            )
            if not claim.claimed:
                self._respond(channel, "DENIED", [claim.reason, "reserve_failure_breaker_or_idempotency"])
                return
            try:
                result = self.runner(
                    [self.helper_path],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                payload = {"action": RESERVE_ACTION, "incident_id": incident_id, "reason_codes": ["reserve_helper_outcome_unknown"]}
                self.state_store.finish_reserve_incident(incident_id=incident_id, state="FAILED", result=payload, now=time.time())
                self._respond(channel, "UNKNOWN", ["reserve_helper_outcome_unknown"], payload)
                return
            except OSError:
                payload = {"action": RESERVE_ACTION, "incident_id": incident_id, "reason_codes": ["reserve_helper_unavailable"]}
                self.state_store.finish_reserve_incident(incident_id=incident_id, state="FAILED", result=payload, now=time.time())
                self._respond(channel, "DENIED", ["reserve_helper_unavailable"], payload)
                return
            try:
                helper_result = json.loads((result.stdout or "").strip())
            except (TypeError, json.JSONDecodeError):
                helper_result = None
            valid = (
                result.returncode == 0
                and isinstance(helper_result, Mapping)
                and helper_result.get("status") == "released"
                and helper_result.get("path") == str(policy.root / "emergency-space.bin")
                and isinstance(helper_result.get("before_free_bytes"), int)
                and isinstance(helper_result.get("after_free_bytes"), int)
                and helper_result["after_free_bytes"] > helper_result["before_free_bytes"]
            )
            payload = {
                "action": RESERVE_ACTION,
                "incident_id": incident_id,
                "released_path": str(policy.root / "emergency-space.bin"),
                "before_free_bytes": helper_result.get("before_free_bytes") if isinstance(helper_result, Mapping) else None,
                "after_free_bytes": helper_result.get("after_free_bytes") if isinstance(helper_result, Mapping) else None,
                "reason_codes": ["guardian_reserve_released", "writer_source_requires_manual_handling"] if valid else ["reserve_helper_failed"],
            }
            state = "RELEASED" if valid else "FAILED"
            self.state_store.finish_reserve_incident(incident_id=incident_id, state=state, result=payload, now=time.time())
            self._respond(channel, "EXECUTED" if valid else "DENIED", payload["reason_codes"], payload)
        except (ReserveBrokerProtocolError, StateStoreError, json.JSONDecodeError) as exc:
            self._respond(channel, "DENIED", [str(exc)[:128]])
        finally:
            try:
                channel.close()
            except OSError:
                pass

    def serve_forever(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.is_symlink():
            raise OSError("reserve_broker_socket_symlink")
        if self.socket_path.exists():
            if not stat.S_ISSOCK(self.socket_path.stat().st_mode):
                raise OSError("reserve_broker_socket_not_socket")
            self.socket_path.unlink()
        server_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_socket = server_socket
        try:
            server_socket.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o660)
            server_socket.listen(2)
            server_socket.settimeout(1.0)
            while not self._stop.is_set():
                try:
                    channel, _ = server_socket.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                threading.Thread(target=self._handle, args=(channel,), daemon=True).start()
        finally:
            try:
                server_socket.close()
            except OSError:
                pass
            self._server_socket = None
            try:
                if self.socket_path.exists() and stat.S_ISSOCK(self.socket_path.stat().st_mode):
                    self.socket_path.unlink()
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guardian fixed-scope reserve recovery broker")
    parser.add_argument("--socket", type=Path, default=Path("/run/guardian-reserve-broker/reserve.sock"))
    parser.add_argument("--state-db", type=Path, default=Path("/var/lib/guardian/shared/state.db"))
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--enable", action="store_true")
    parser.add_argument("--allowed-uid", default="guardian")
    args = parser.parse_args(argv)
    try:
        server = ReserveRecoveryBrokerServer(
            args.socket,
            state_store=GuardianStateStore(args.state_db, file_mode=0o660),
            config_path=args.config,
            enabled=args.enable,
            allowed_uid=args.allowed_uid,
        )
    except (ConfigError, OSError, ValueError, StateStoreError) as exc:
        print(json.dumps({"schema": RESERVE_BROKER_RESPONSE_SCHEMA, "status": "FAILED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    def stop_handler(_signum: int, _frame: Any) -> None:
        server.stop()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    try:
        server.serve_forever()
    except OSError as exc:
        print(json.dumps({"schema": RESERVE_BROKER_RESPONSE_SCHEMA, "status": "FAILED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


__all__ = [
    "FIXED_HELPER",
    "RESERVE_BROKER_REQUEST_SCHEMA",
    "RESERVE_BROKER_RESPONSE_SCHEMA",
    "ReserveBrokerProtocolError",
    "ReserveRecoveryBrokerClient",
    "ReserveRecoveryBrokerServer",
    "build_reserve_request",
]


if __name__ == "__main__":
    raise SystemExit(main())
