"""Independent local Action Broker service.

Only this process imports ``DockerActionAdapter``.  The service is disabled by
default, accepts one bounded request at a time over a Unix socket, checks the
connecting process identity, revalidates the durable intent/capability and
Docker identity, and then performs at most the allowlisted graceful stop.

The module is intentionally not started by the repository or by the Runtime;
the systemd unit is also disabled until an operator explicitly opens the
execution marker and supplies a reviewed service-account mapping.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import grp
import pwd
import re
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

from .guardian_actions import ActionDenied, ActionRequest, Authorization, DockerActionAdapter, validate_request
from .guardian_attribution import collect_object_registry
from .guardian_broker_client import (
    BROKER_REQUEST_SCHEMA,
    BROKER_RESPONSE_SCHEMA,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
)
from .guardian_config import ConfigError, load_config, safe_defaults
from .guardian_emergency_shedding import EmergencySheddingPolicy, emergency_policy_digest, emergency_shedding_policy_from_config
from .guardian_state import GuardianStateStore, StateStoreError


_FULL_ID = re.compile(r"^[0-9a-f]{64}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_MAX_TEXT = 512
_REQUEST_KEYS = {
    "schema",
    "version",
    "mode",
    "intent_id",
    "event_id",
    "sample_id",
    "host_id",
    "resource_kind",
    "idempotency_key",
    "issued_at",
    "expires_at",
    "policy_digest",
    "config_digest",
    "action",
    "timeout_seconds",
    "target",
    "authorization",
}
_TARGET_KEYS = {"kind", "id", "created_at", "cgroup_path", "cgroup_inode", "labels"}
_AUTH_KEYS = {"approval_id", "environment", "target_id", "action", "expires_at"}


class BrokerProtocolError(ValueError):
    """Raised for a request that must never reach Docker."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > _MAX_TEXT:
        raise BrokerProtocolError(f"{field}_invalid")
    return value


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BrokerProtocolError(f"{field}_invalid")
    result = float(value)
    if not math.isfinite(result):
        raise BrokerProtocolError(f"{field}_invalid")
    return result


def _labels(value: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, Mapping):
        raise BrokerProtocolError("target_labels_invalid")
    pairs: list[tuple[str, str]] = []
    for key, raw in value.items():
        if not isinstance(key, str) or not isinstance(raw, str) or not key or len(key) > 256 or len(raw) > 1024:
            raise BrokerProtocolError("target_labels_invalid")
        pairs.append((key, raw))
    return tuple(sorted(pairs))


def _json_line(value: Mapping[str, Any], maximum: int) -> bytes:
    encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > maximum:
        raise BrokerProtocolError("broker_payload_too_large")
    return encoded


def _authorization(value: Any) -> Authorization:
    if not isinstance(value, Mapping) or set(value) != _AUTH_KEYS:
        raise BrokerProtocolError("authorization_invalid")
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
        newline = b"\n" in chunk
        if total > MAX_REQUEST_BYTES:
            raise BrokerProtocolError("request_too_large")
        if newline:
            break
    raw = b"".join(chunks)
    if len(raw) > MAX_REQUEST_BYTES or b"\n" not in raw:
        raise BrokerProtocolError("request_framing_invalid")
    return raw.split(b"\n", 1)[0]


def _peer_credentials(channel: socket.socket) -> tuple[int, int, int]:
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        raise BrokerProtocolError("peer_identity_unsupported")
    try:
        raw = channel.getsockopt(socket.SOL_SOCKET, option, struct.calcsize("3i"))
        pid, uid, gid = struct.unpack("3i", raw)
    except (OSError, struct.error) as exc:
        raise BrokerProtocolError("peer_identity_unavailable") from exc
    return pid, uid, gid


class ActionBrokerServer:
    """Bounded Unix-socket broker; disabled unless explicitly enabled."""

    def __init__(
        self,
        socket_path: str | Path,
        *,
        state_store: GuardianStateStore,
        adapter: DockerActionAdapter | None = None,
        enabled: bool = False,
        allowed_uid: int | str | None = None,
        allowed_gid: int | str | None = None,
        expected_policy_digest: str | None = None,
        expected_config_digest: str | None = None,
        policy: EmergencySheddingPolicy | None = None,
        config_path: Path | None = None,
        cooldown_seconds: float = 30.0,
        max_actions: int = 1,
        action_window_seconds: float = 300.0,
        max_consecutive_failures: int = 2,
        runner: Any = subprocess.run,
        proc_root: Path = Path("/proc"),
        cgroup_root: Path = Path("/sys/fs/cgroup"),
        request_timeout_seconds: float = 10.0,
    ) -> None:
        if enabled and allowed_uid is None:
            raise ValueError("allowed_runtime_uid_required")
        self.socket_path = Path(socket_path)
        self.state_store = state_store
        self.adapter = adapter or DockerActionAdapter()
        self.enabled = bool(enabled)
        self.allowed_uid = self._resolve_identity(allowed_uid, "uid") if allowed_uid is not None else None
        self.allowed_gid = self._resolve_identity(allowed_gid, "gid") if allowed_gid is not None else None
        self.expected_policy_digest = expected_policy_digest
        self.expected_config_digest = expected_config_digest
        self.policy = policy
        self.config_path = Path(config_path) if config_path is not None else None
        self.cooldown_seconds = float(cooldown_seconds)
        self.max_actions = max_actions
        self.action_window_seconds = float(action_window_seconds)
        self.max_consecutive_failures = max_consecutive_failures
        if not 0 <= self.cooldown_seconds <= 86_400 or not 0 < self.action_window_seconds <= 86_400:
            raise ValueError("broker_cooldown_bounds_invalid")
        if not isinstance(max_actions, int) or isinstance(max_actions, bool) or not 1 <= max_actions <= 10:
            raise ValueError("broker_max_actions_invalid")
        if not isinstance(max_consecutive_failures, int) or isinstance(max_consecutive_failures, bool) or not 1 <= max_consecutive_failures <= 10:
            raise ValueError("broker_breaker_bounds_invalid")
        self.runner = runner
        self.proc_root = proc_root
        self.cgroup_root = cgroup_root
        self.request_timeout_seconds = float(request_timeout_seconds)
        if not math.isfinite(self.request_timeout_seconds) or not 0.1 <= self.request_timeout_seconds <= 120:
            raise ValueError("request_timeout_out_of_bounds")
        self._stop = threading.Event()
        self._active_lock = threading.Lock()
        self._active = 0
        self._server_socket: socket.socket | None = None

    @staticmethod
    def _resolve_identity(value: int | str, field: str) -> int:
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        if isinstance(value, str) and value:
            try:
                return int(value)
            except ValueError:
                try:
                    return pwd.getpwnam(value).pw_uid if field == "uid" else grp.getgrnam(value).gr_gid
                except KeyError as exc:
                    raise ValueError(f"{field}_invalid") from exc
        raise ValueError(f"{field}_invalid")

    def stop(self) -> None:
        self._stop.set()
        server_socket = self._server_socket
        if server_socket is not None:
            try:
                server_socket.close()
            except OSError:
                pass

    def _respond(self, channel: socket.socket, status: str, reasons: tuple[str, ...], result: Mapping[str, Any] | None = None) -> None:
        payload = {
            "schema": BROKER_RESPONSE_SCHEMA,
            "version": 1,
            "status": status,
            "reason_codes": list(dict.fromkeys(reasons))[:16],
            "result": dict(result) if result is not None else None,
        }
        try:
            channel.sendall(_json_line(payload, MAX_RESPONSE_BYTES))
        except (OSError, BrokerProtocolError):
            pass

    def _validate_request(self, request: Mapping[str, Any], *, now: float) -> tuple[Authorization, ActionRequest, dict[str, Any]]:
        if set(request) != _REQUEST_KEYS:
            raise BrokerProtocolError("request_schema_fields_invalid")
        if request.get("schema") != BROKER_REQUEST_SCHEMA or request.get("version") != 1:
            raise BrokerProtocolError("request_schema_invalid")
        if request.get("mode") != "enforce":
            raise BrokerProtocolError("broker_mode_invalid")
        intent_id = _text(request.get("intent_id"), "intent_id")
        event_id = _text(request.get("event_id"), "event_id")
        _text(request.get("sample_id"), "sample_id")
        host_id = _text(request.get("host_id"), "host_id")
        resource_kind = _text(request.get("resource_kind"), "resource_kind")
        idempotency_key = _text(request.get("idempotency_key"), "idempotency_key")
        if not re.fullmatch(r"[0-9a-f]{64}", idempotency_key):
            raise BrokerProtocolError("idempotency_key_invalid")
        issued_at = _number(request.get("issued_at"), "issued_at")
        expires_at = _number(request.get("expires_at"), "expires_at")
        if expires_at <= now or issued_at > now or expires_at <= issued_at or expires_at - issued_at > 300:
            raise BrokerProtocolError("request_expired_or_ttl_invalid")
        policy_digest = _text(request.get("policy_digest"), "policy_digest")
        config_digest = _text(request.get("config_digest"), "config_digest")
        if not _DIGEST.fullmatch(policy_digest) or not _DIGEST.fullmatch(config_digest):
            raise BrokerProtocolError("request_digest_invalid")
        if self.expected_policy_digest is None or self.expected_config_digest is None:
            raise BrokerProtocolError("broker_digest_policy_unavailable")
        if policy_digest != self.expected_policy_digest or config_digest != self.expected_config_digest:
            raise BrokerProtocolError("broker_digest_mismatch")
        action = _text(request.get("action"), "action")
        if action != "graceful_stop":
            raise BrokerProtocolError("action_not_allowlisted")
        timeout_seconds = request.get("timeout_seconds")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or not 1 <= timeout_seconds <= 120:
            raise BrokerProtocolError("timeout_invalid")
        target = request.get("target")
        if not isinstance(target, Mapping) or set(target) != _TARGET_KEYS:
            raise BrokerProtocolError("target_schema_invalid")
        target_id = _text(target.get("id"), "target_id")
        if target.get("kind") != "container" or not _FULL_ID.fullmatch(target_id):
            raise BrokerProtocolError("target_full_id_required")
        created_at = _text(target.get("created_at"), "target_created_at")
        cgroup_path = _text(target.get("cgroup_path"), "target_cgroup_path")
        if not cgroup_path.startswith("/"):
            raise BrokerProtocolError("target_cgroup_path_invalid")
        cgroup_inode = target.get("cgroup_inode")
        if isinstance(cgroup_inode, bool) or not isinstance(cgroup_inode, int) or cgroup_inode <= 0:
            raise BrokerProtocolError("target_cgroup_inode_invalid")
        labels = _labels(target.get("labels"))
        authorization = _authorization(request.get("authorization"))
        if self.policy is None:
            raise BrokerProtocolError("broker_policy_unavailable")
        protected = {
            item.get("stable_id") or item.get("container_id") or item.get("id")
            for item in self.policy.protected_set
            if isinstance(item, Mapping)
        }
        actionable = {
            (item.get("stable_id") or item.get("container_id") or item.get("id")): item
            for item in self.policy.actionable_set
            if isinstance(item, Mapping)
        }
        if target_id in protected:
            raise BrokerProtocolError("protected_object")
        entry = actionable.get(target_id)
        if entry is None:
            raise BrokerProtocolError("target_not_actionable")
        allowed_resources = entry.get("allowed_resources")
        aliases = {"capacity": "disk_capacity", "disk": "disk_capacity", "disk_capacity": "disk_capacity", "memory": "memory", "cpu": "cpu", "io": "io"}
        normalized_resources = {
            aliases.get(str(value).strip().lower(), "")
            for value in allowed_resources
            if isinstance(value, str)
        } if isinstance(allowed_resources, list) else set()
        if resource_kind not in normalized_resources or entry.get("action") != "graceful_stop" or entry.get("environment") != "local-disposable":
            raise BrokerProtocolError("actionable_entry_invalid")
        raw_expiry = entry.get("expires_at")
        if not isinstance(raw_expiry, str):
            raise BrokerProtocolError("actionable_entry_expiry_invalid")
        try:
            normalized_expiry = raw_expiry[:-1] + "+00:00" if raw_expiry.endswith("Z") else raw_expiry
            entry_expiry = dt.datetime.fromisoformat(normalized_expiry).timestamp()
        except (TypeError, ValueError, OverflowError):
            raise BrokerProtocolError("actionable_entry_expiry_invalid")
        if entry_expiry <= now:
            raise BrokerProtocolError("actionable_entry_expired")
        action_request = ActionRequest(
            event_id=event_id,
            target_id=target_id,
            action=action,
            protected=False,
            allowed_actions=frozenset({"graceful_stop"}),
            authorization=authorization,
            timeout_seconds=timeout_seconds,
        )
        validate_request(action_request, now=now)
        return authorization, action_request, {
            "intent_id": intent_id,
            "event_id": event_id,
            "host_id": host_id,
            "resource_kind": resource_kind,
            "idempotency_key": idempotency_key,
            "target_id": target_id,
            "created_at": created_at,
            "cgroup_path": cgroup_path,
            "cgroup_inode": cgroup_inode,
            "labels": labels,
        }

    def _verify_runtime_guards(self, details: Mapping[str, Any], *, now: float) -> None:
        if self.state_store.failure_breaker(
            str(details["host_id"]),
            "__guardian_global__",
            "graceful_stop",
            self.max_consecutive_failures,
        ):
            raise BrokerProtocolError("failure_breaker_tripped")
        allowed, reason = self.state_store.allow_action(
            str(details["host_id"]),
            "__guardian_global__",
            "graceful_stop",
            now,
            self.cooldown_seconds,
            self.max_actions,
            self.action_window_seconds,
        )
        if not allowed:
            raise BrokerProtocolError(reason)

    def _verify_live_config(self) -> None:
        if self.config_path is None or not self.config_path.is_file():
            raise BrokerProtocolError("broker_config_unavailable")
        try:
            config = load_config(self.config_path)
            policy = emergency_shedding_policy_from_config(config)
        except (ConfigError, OSError, ValueError) as exc:
            raise BrokerProtocolError("broker_config_invalid") from exc
        if (
            config.config_digest != self.expected_config_digest
            or emergency_policy_digest(policy) != self.expected_policy_digest
        ):
            raise BrokerProtocolError("broker_config_changed")

    def _verify_durable_intent(self, details: Mapping[str, Any]) -> None:
        record = self.state_store.get_intent(str(details["intent_id"]))
        if record is None:
            raise BrokerProtocolError("intent_not_found")
        if (
            record.get("state") != "EXECUTION_STARTED"
            or record.get("event_id") != details["event_id"]
            or record.get("object_id") != details["target_id"]
            or record.get("action") != "graceful_stop"
            or record.get("idempotency_key") != details["idempotency_key"]
        ):
            raise BrokerProtocolError("intent_not_executable")

    def _verify_live_identity(self, details: Mapping[str, Any]) -> None:
        registry = collect_object_registry(
            {"available": True, "containers": [{"ID": details["target_id"]}]},
            proc_root=self.proc_root,
            cgroup_root=self.cgroup_root,
            runner=self.runner,
        )
        objects = registry.get("objects") if isinstance(registry, Mapping) else None
        if registry.get("status") != "ok" or not isinstance(objects, list):
            raise BrokerProtocolError("broker_live_identity_unavailable")
        matches = [item for item in objects if isinstance(item, Mapping) and item.get("id") == details["target_id"]]
        if len(matches) != 1:
            raise BrokerProtocolError("broker_live_identity_changed")
        identity = matches[0]
        actual_labels = _labels(identity.get("labels"))
        if (
            identity.get("created_at") != details["created_at"]
            or identity.get("cgroup_path") != details["cgroup_path"]
            or identity.get("cgroup_inode") != details["cgroup_inode"]
            or actual_labels != details["labels"]
            or identity.get("mapping_confidence") != "high"
            or identity.get("mapping_errors") != []
            or str(identity.get("status") or "").lower() not in {"running", "restarting"}
        ):
            raise BrokerProtocolError("broker_live_identity_changed")

    def _handle(self, channel: socket.socket) -> None:
        channel.settimeout(self.request_timeout_seconds)
        try:
            _, uid, gid = _peer_credentials(channel)
            if self.allowed_uid != uid or (self.allowed_gid is not None and self.allowed_gid != gid):
                self._respond(channel, "DENIED", ("peer_identity_denied",))
                return
            if not self.enabled:
                self._respond(channel, "DENIED", ("broker_disabled",))
                return
            raw = _read_line(channel)
            try:
                request = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise BrokerProtocolError("request_json_invalid") from exc
            if not isinstance(request, Mapping):
                raise BrokerProtocolError("request_mapping_required")
            now = time.time()
            self._verify_live_config()
            authorization, action_request, details = self._validate_request(request, now=now)
            self._verify_durable_intent(details)
            self._verify_runtime_guards(details, now=now)
            self._verify_live_identity(details)
            capability = self.state_store.consume_capability(
                authorization,
                event_id=str(details["event_id"]),
                now=now,
            )
            if not capability.allowed:
                self._respond(channel, "DENIED", (capability.reason,))
                return
            try:
                result = self.adapter.execute(action_request, now=now)
            except ActionDenied as exc:
                self._respond(channel, "DENIED", (str(exc),))
                return
            except Exception:
                # The adapter may have crossed the Docker boundary.  The
                # Runtime must not retry an unknown result.
                self._respond(channel, "UNKNOWN", ("broker_adapter_outcome_unknown",))
                return
            if result.target_id != action_request.target_id or result.action != action_request.action:
                self._respond(channel, "UNKNOWN", ("broker_result_identity_mismatch",))
                return
            self._respond(channel, "EXECUTED", ("broker_action_completed",), {
                "executed": result.executed,
                "action": result.action,
                "target_id": result.target_id,
                "returncode": result.returncode,
                "stdout": result.stdout[:4096],
                "stderr": result.stderr[:4096],
                "reason": result.reason[:256],
            })
        except (BrokerProtocolError, StateStoreError) as exc:
            self._respond(channel, "DENIED", (str(exc)[:128],))
        except socket.timeout:
            self._respond(channel, "DENIED", ("broker_request_timeout",))
        finally:
            try:
                channel.close()
            except OSError:
                pass
            with self._active_lock:
                self._active = max(0, self._active - 1)

    def serve_forever(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.is_symlink():
            raise OSError("broker_socket_path_symlink")
        if self.socket_path.exists():
            mode = self.socket_path.stat().st_mode
            if not stat.S_ISSOCK(mode):
                raise OSError("broker_socket_path_not_socket")
            self.socket_path.unlink()
        channel = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_socket = channel
        try:
            try:
                os.chmod(self.socket_path.parent, 0o750)
            except OSError:
                pass
            channel.bind(str(self.socket_path))
            os.chmod(self.socket_path, 0o660)
            channel.listen(8)
            channel.settimeout(1.0)
            while not self._stop.is_set():
                try:
                    client, _ = channel.accept()
                except socket.timeout:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                with self._active_lock:
                    if self._active >= 1:
                        self._respond(client, "DENIED", ("concurrency_limit",))
                        client.close()
                        continue
                    self._active += 1
                threading.Thread(target=self._handle, args=(client,), name="guardian-broker-request", daemon=True).start()
        finally:
            try:
                channel.close()
            except OSError:
                pass
            self._server_socket = None
            try:
                if self.socket_path.exists() and stat.S_ISSOCK(self.socket_path.stat().st_mode):
                    self.socket_path.unlink()
            except OSError:
                pass


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guardian independent Unix-socket Action Broker")
    parser.add_argument("--socket", type=Path, default=Path("/run/guardian-broker/broker.sock"))
    parser.add_argument("--state-db", type=Path, default=Path("/var/lib/guardian/shared/state.db"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--enable", action="store_true", help="explicitly open the enforce execution boundary")
    parser.add_argument("--allowed-uid", required=True)
    parser.add_argument("--allowed-gid")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config) if args.config else safe_defaults()
        policy = emergency_shedding_policy_from_config(config)
        server = ActionBrokerServer(
            args.socket,
            state_store=GuardianStateStore(args.state_db, file_mode=0o660),
            enabled=args.enable,
            allowed_uid=args.allowed_uid,
            allowed_gid=args.allowed_gid,
            expected_policy_digest=emergency_policy_digest(policy),
            expected_config_digest=config.config_digest,
            policy=policy,
            config_path=args.config,
            cooldown_seconds=config.cooldown_seconds,
            max_actions=max(1, config.max_actions_per_host_per_hour),
            action_window_seconds=3600.0,
        )
    except (ConfigError, OSError, ValueError, StateStoreError) as exc:
        print(json.dumps({"schema": BROKER_RESPONSE_SCHEMA, "status": "FAILED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    def stop_handler(_signum: int, _frame: Any) -> None:
        server.stop()

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    try:
        server.serve_forever()
    except (OSError, ValueError) as exc:
        print(json.dumps({"schema": BROKER_RESPONSE_SCHEMA, "status": "FAILED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = ["ActionBrokerServer", "BrokerProtocolError"]
