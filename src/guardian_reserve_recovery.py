"""Fail-closed Runtime planning for Guardian's own disk reserve.

This is a host-safety action, not a container action. The unprivileged Runtime
validates the short-lived authorization and asks the fixed-scope reserve
Broker to release Guardian's own reserve. The Broker verifies the manifest,
checks that free space increased, and records the durable result. This module
never accepts an operator-supplied deletion path and never touches logs,
Docker data, images, volumes, or business files.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .guardian_actions import Authorization


RESERVE_SCHEMA = "guardian.emergency_reserve.v1"
RESERVE_CREATED_BY = "server-resource-guardian/maintenance-reserve-v1"
RESERVE_ROOT = Path("/var/lib/guardian/reserve")
RESERVE_NAME = "emergency-space.bin"
MANIFEST_NAME = "manifest.json"
RESERVE_ACTION = "release_emergency_reserve"


@dataclass(frozen=True)
class ReserveRecoveryPolicy:
    enabled: bool = False
    root: Path = RESERVE_ROOT
    mount_point: str = "/"
    max_releases_per_incident: int = 1
    authorization_file: Path | None = None

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any] | None) -> "ReserveRecoveryPolicy":
        raw = value if isinstance(value, Mapping) else {}
        authorization = raw.get("authorization_file")
        return cls(
            enabled=raw.get("enabled") is True,
            root=Path(str(raw.get("root") or RESERVE_ROOT)),
            mount_point=str(raw.get("mount_point") or "/"),
            max_releases_per_incident=int(raw.get("max_releases_per_incident") or 1),
            authorization_file=Path(authorization) if isinstance(authorization, str) and authorization else None,
        )


def _paths(root: Path) -> tuple[Path, Path]:
    return root / RESERVE_NAME, root / MANIFEST_NAME


def _read_manifest(path: Path) -> Mapping[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, Mapping) else None


def reserve_status(root: Path = RESERVE_ROOT) -> dict[str, Any]:
    reserve, manifest_path = _paths(root)
    manifest = _read_manifest(manifest_path)
    try:
        reserve_is_file = reserve.is_file()
        reserve_size = reserve.stat().st_size if reserve_is_file else None
    except OSError:
        return {"state": "unavailable", "path": str(reserve), "reason_codes": ["reserve_access_unavailable"]}
    if not reserve_is_file or manifest is None:
        return {"state": "unavailable", "path": str(reserve), "reason_codes": ["reserve_manifest_missing"]}
    expected_path = str(reserve)
    valid = (
        manifest.get("schema") == RESERVE_SCHEMA
        and manifest.get("created_by") == RESERVE_CREATED_BY
        and manifest.get("path") == expected_path
        and isinstance(manifest.get("size_bytes"), int)
        and manifest.get("size_bytes") == reserve_size
    )
    return {
        "state": "ready" if valid else "invalid",
        "path": expected_path,
        "size_bytes": reserve_size,
        "reason_codes": [] if valid else ["reserve_manifest_mismatch"],
    }


class ReserveRecoveryController:
    """One bounded reserve-release decision/execution boundary."""

    def __init__(
        self,
        policy: ReserveRecoveryPolicy | None = None,
        *,
        broker_socket: str | Path = "/run/guardian-reserve-broker/reserve.sock",
        config_digest: str = "",
    ) -> None:
        from .guardian_reserve_broker import ReserveRecoveryBrokerClient

        self.policy = policy or ReserveRecoveryPolicy()
        self.broker = ReserveRecoveryBrokerClient(broker_socket)
        self.config_digest = config_digest

    @staticmethod
    def _disk_risk(event: Mapping[str, Any]) -> Mapping[str, Any]:
        evaluations = event.get("resource_evaluations")
        if not isinstance(evaluations, Mapping):
            return {}
        disk = evaluations.get("disk_capacity")
        if not isinstance(disk, Mapping):
            return {}
        risk = disk.get("risk")
        return risk if isinstance(risk, Mapping) else {}

    def _authorized(self, incident_id: str, now: float) -> tuple[bool, str, Authorization | None]:
        path = self.policy.authorization_file
        if path is None:
            return False, "reserve_authorization_missing", None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False, "reserve_authorization_invalid", None
        if not isinstance(value, Mapping):
            return False, "reserve_authorization_invalid", None
        if value.get("environment") != "local-disposable" or value.get("action") != RESERVE_ACTION:
            return False, "reserve_authorization_scope_invalid", None
        if value.get("target_id") != str(self.policy.root):
            return False, "reserve_authorization_target_mismatch", None
        try:
            expires_at = float(value["expires_at"])
        except (KeyError, TypeError, ValueError):
            return False, "reserve_authorization_expiry_invalid", None
        if expires_at <= now:
            return False, "reserve_authorization_expired", None
        if not value.get("approval_id") or not incident_id:
            return False, "reserve_authorization_identity_missing", None
        return True, "reserve_authorized", Authorization(
            approval_id=str(value["approval_id"]),
            environment=str(value["environment"]),
            target_id=str(value["target_id"]),
            action=str(value["action"]),
            expires_at=expires_at,
        )

    def handle(self, event: Mapping[str, Any], *, mode: str, now: float | None = None) -> dict[str, Any]:
        timestamp = time.time() if now is None else now
        if not self.policy.enabled:
            return {"action": "none", "execution": "not_applicable", "state": "disabled", "reason_codes": ["RESERVE_RECOVERY_DISABLED"]}
        risk = self._disk_risk(event)
        if str(risk.get("state") or "").lower() not in {"critical", "critical_confirmed"}:
            return {"action": "none", "execution": "not_applicable", "state": "not_eligible", "reason_codes": ["DISK_CAPACITY_NOT_CRITICAL"]}
        incident_id = str(event.get("incident_id") or event.get("event_id") or "")
        status = reserve_status(self.policy.root)
        base = {
            "action": RESERVE_ACTION,
            "resource_kind": "disk_capacity",
            "incident_id": incident_id,
            "reserve": status,
        }
        if mode == "observe":
            return {**base, "execution": "not_applicable", "state": "observed", "reason_codes": ["OBSERVE_ONLY"]}
        if mode == "simulate":
            return {**base, "execution": "not_executed", "state": "simulated", "reason_codes": ["SIMULATE_ONLY"]}
        if mode != "enforce":
            return {**base, "execution": "not_executed", "state": "blocked", "reason_codes": ["MODE_NOT_ACTIONABLE"]}
        authorized, reason, authorization = self._authorized(incident_id, timestamp)
        if not authorized:
            return {**base, "execution": "not_executed", "state": "blocked", "reason_codes": [reason]}
        if not self.config_digest or authorization is None:
            return {**base, "execution": "not_executed", "state": "blocked", "reason_codes": ["reserve_config_digest_missing"]}
        result = self.broker.release(
            incident_id=incident_id,
            authorization=authorization,
            config_digest=self.config_digest,
            mount_point=self.policy.mount_point,
            now=timestamp,
        )
        return {**base, **result}


__all__ = [
    "RESERVE_ACTION",
    "RESERVE_ROOT",
    "ReserveRecoveryController",
    "ReserveRecoveryPolicy",
    "reserve_status",
]
