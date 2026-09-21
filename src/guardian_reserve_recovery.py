"""Fail-closed recovery for Guardian's own disk reserve.

This is a host-safety action, not a container action.  It can only release
the fixed Guardian reserve after validating its manifest, and it verifies
that free space on the configured filesystem increased.  It never accepts an
operator-supplied deletion path and never touches logs, Docker data, images,
volumes, business files, or audit files.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


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
    if not reserve.is_file() or manifest is None:
        return {"state": "unavailable", "path": str(reserve), "reason_codes": ["reserve_manifest_missing"]}
    expected_path = str(reserve)
    valid = (
        manifest.get("schema") == RESERVE_SCHEMA
        and manifest.get("created_by") == RESERVE_CREATED_BY
        and manifest.get("path") == expected_path
        and isinstance(manifest.get("size_bytes"), int)
        and manifest.get("size_bytes") == reserve.stat().st_size
    )
    return {
        "state": "ready" if valid else "invalid",
        "path": expected_path,
        "size_bytes": reserve.stat().st_size,
        "reason_codes": [] if valid else ["reserve_manifest_mismatch"],
    }


def _free_bytes(path: str) -> int | None:
    try:
        stats = os.statvfs(path)
        return int(stats.f_bavail * stats.f_frsize)
    except (OSError, ValueError):
        return None


class ReserveRecoveryController:
    """One bounded reserve-release decision/execution boundary."""

    def __init__(self, policy: ReserveRecoveryPolicy | None = None) -> None:
        self.policy = policy or ReserveRecoveryPolicy()
        self._released_incidents: set[str] = set()

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

    def _authorized(self, incident_id: str, now: float) -> tuple[bool, str]:
        path = self.policy.authorization_file
        if path is None:
            return False, "reserve_authorization_missing"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False, "reserve_authorization_invalid"
        if not isinstance(value, Mapping):
            return False, "reserve_authorization_invalid"
        if value.get("environment") != "local-disposable" or value.get("action") != RESERVE_ACTION:
            return False, "reserve_authorization_scope_invalid"
        if value.get("target_id") != str(self.policy.root):
            return False, "reserve_authorization_target_mismatch"
        try:
            expires_at = float(value["expires_at"])
        except (KeyError, TypeError, ValueError):
            return False, "reserve_authorization_expiry_invalid"
        if expires_at <= now:
            return False, "reserve_authorization_expired"
        if not value.get("approval_id") or not incident_id:
            return False, "reserve_authorization_identity_missing"
        return True, "reserve_authorized"

    def _release(self, incident_id: str, now: float) -> dict[str, Any]:
        if incident_id in self._released_incidents:
            return {"state": "blocked", "execution": "not_executed", "reason_codes": ["reserve_release_already_used"]}
        if self.policy.max_releases_per_incident != 1:
            return {"state": "blocked", "execution": "not_executed", "reason_codes": ["reserve_release_limit_invalid"]}
        before = _free_bytes(self.policy.mount_point)
        status = reserve_status(self.policy.root)
        if status["state"] != "ready":
            return {"state": "blocked", "execution": "not_executed", "reason_codes": status["reason_codes"]}
        reserve, manifest_path = _paths(self.policy.root)
        try:
            reserve.unlink()
            manifest_path.unlink()
        except OSError:
            return {"state": "failed", "execution": "executed", "reason_codes": ["reserve_release_failed"]}
        after = _free_bytes(self.policy.mount_point)
        if before is None or after is None or after <= before:
            return {
                "state": "failed",
                "execution": "executed",
                "before_free_bytes": before,
                "after_free_bytes": after,
                "reason_codes": ["reserve_space_increase_unverified"],
            }
        self._released_incidents.add(incident_id)
        return {
            "state": "released",
            "execution": "executed",
            "before_free_bytes": before,
            "after_free_bytes": after,
            "released_path": str(reserve),
            "reason_codes": ["guardian_reserve_released", "writer_source_requires_manual_handling"],
        }

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
        authorized, reason = self._authorized(incident_id, timestamp)
        if not authorized:
            return {**base, "execution": "not_executed", "state": "blocked", "reason_codes": [reason]}
        return {**base, **self._release(incident_id, timestamp)}


__all__ = [
    "RESERVE_ACTION",
    "RESERVE_ROOT",
    "ReserveRecoveryController",
    "ReserveRecoveryPolicy",
    "reserve_status",
]
