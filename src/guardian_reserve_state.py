"""Root-only durable state for the fixed-scope reserve recovery broker.

This database is intentionally independent from the Runtime/Broker shared
state.  The Runtime account must not be able to consume an approval, occupy
the reserve execution slot, or rewrite the reserve audit trail.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class ReserveStateError(RuntimeError):
    """Raised when the root-only reserve state cannot be read or written."""


@dataclass(frozen=True)
class ReserveExecutionClaim:
    claimed: bool
    state: str
    reason: str


class ReserveBrokerStateStore:
    """Small SQLite store owned only by the root reserve service."""

    def __init__(self, path: Path, *, file_mode: int = 0o600) -> None:
        if file_mode != 0o600:
            raise ValueError("reserve_state_file_mode_invalid")
        self.path = Path(path)
        self.file_mode = file_mode
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.path.parent, 0o700)
        except OSError as exc:
            raise ReserveStateError(f"reserve_state_directory_permissions_failed:{exc}") from exc
        self._initialize()
        try:
            os.chmod(self.path, self.file_mode)
        except OSError as exc:
            raise ReserveStateError(f"reserve_state_permissions_failed:{exc}") from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA journal_mode=WAL")
            return connection
        except sqlite3.Error as exc:
            raise ReserveStateError(f"reserve_state_open_failed:{exc}") from exc

    @staticmethod
    def _json(value: Mapping[str, Any]) -> str:
        try:
            encoded = json.dumps(dict(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ReserveStateError("reserve_state_payload_invalid") from exc
        if len(encoded.encode("utf-8")) > 64 * 1024:
            raise ReserveStateError("reserve_state_payload_too_large")
        return encoded

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS reserve_capabilities (
                    approval_id TEXT PRIMARY KEY,
                    environment TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    consumed_at REAL,
                    consumed_incident_id TEXT
                );
                CREATE TABLE IF NOT EXISTS reserve_incidents (
                    incident_id TEXT PRIMARY KEY,
                    approval_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    result_json TEXT
                );
                CREATE TABLE IF NOT EXISTS reserve_slots (
                    slot_key TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    state TEXT NOT NULL,
                    acquired_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS reserve_audits (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    incident_id TEXT,
                    approval_id TEXT,
                    payload_json TEXT NOT NULL,
                    recorded_at REAL NOT NULL
                );
                """
            )
        except sqlite3.Error as exc:
            raise ReserveStateError(f"reserve_state_initialize_failed:{exc}") from exc
        finally:
            connection.close()

    def _audit(
        self,
        connection: sqlite3.Connection,
        *,
        kind: str,
        incident_id: str | None,
        approval_id: str | None,
        payload: Mapping[str, Any],
        recorded_at: float,
    ) -> None:
        connection.execute(
            "INSERT INTO reserve_audits(kind,incident_id,approval_id,payload_json,recorded_at) VALUES (?,?,?,?,?)",
            (kind, incident_id, approval_id, self._json(payload), recorded_at),
        )

    def register_capability(
        self,
        *,
        approval_id: str,
        environment: str,
        target_id: str,
        action: str,
        expires_at: float,
        now: float | None = None,
    ) -> None:
        """Mirror the root-owned configured approval without consuming it."""

        timestamp = time.time() if now is None else float(now)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT environment,target_id,action,expires_at FROM reserve_capabilities WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
            value = (environment, target_id, action, float(expires_at))
            if existing is None:
                connection.execute(
                    "INSERT INTO reserve_capabilities(approval_id,environment,target_id,action,expires_at) VALUES (?,?,?,?,?)",
                    (approval_id, *value),
                )
                self._audit(
                    connection,
                    kind="reserve_capability_registered",
                    incident_id=None,
                    approval_id=approval_id,
                    payload={"approval_id": approval_id, "environment": environment, "target_id": target_id, "action": action, "expires_at": float(expires_at)},
                    recorded_at=timestamp,
                )
            elif tuple(existing) != value:
                raise ReserveStateError("reserve_approval_identity_changed")
            connection.commit()
        except ReserveStateError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise ReserveStateError(f"reserve_capability_register_failed:{exc}") from exc
        finally:
            connection.close()

    def claim_execution(
        self,
        *,
        incident_id: str,
        approval_id: str,
        environment: str,
        target_id: str,
        action: str,
        expires_at: float,
        target: str,
        now: float | None = None,
    ) -> ReserveExecutionClaim:
        """Consume approval, claim the one active slot, and audit atomically."""

        if not all(isinstance(value, str) and value for value in (incident_id, approval_id, environment, target_id, action, target)):
            raise ReserveStateError("reserve_execution_identity_invalid")
        timestamp = time.time() if now is None else float(now)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            capability = connection.execute(
                "SELECT environment,target_id,action,expires_at,consumed_at FROM reserve_capabilities WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
            if capability is None:
                connection.rollback()
                return ReserveExecutionClaim(False, "DENIED", "reserve_approval_not_registered")
            if capability[4] is not None:
                connection.rollback()
                return ReserveExecutionClaim(False, "DENIED", "reserve_approval_already_consumed")
            if float(capability[3]) <= timestamp or float(expires_at) <= timestamp:
                connection.rollback()
                return ReserveExecutionClaim(False, "DENIED", "reserve_approval_expired")
            if tuple(capability[:3]) != (environment, target_id, action):
                connection.rollback()
                return ReserveExecutionClaim(False, "DENIED", "reserve_approval_scope_mismatch")
            existing = connection.execute(
                "SELECT state FROM reserve_incidents WHERE incident_id=?",
                (incident_id,),
            ).fetchone()
            if existing is not None:
                connection.rollback()
                return ReserveExecutionClaim(False, str(existing[0]), "reserve_incident_already_claimed")
            slot = connection.execute(
                "SELECT incident_id,state FROM reserve_slots WHERE slot_key=?",
                ("release_emergency_reserve",),
            ).fetchone()
            if slot is not None:
                connection.rollback()
                return ReserveExecutionClaim(False, str(slot[1]), "reserve_active_slot_occupied")
            connection.execute(
                "INSERT INTO reserve_incidents(incident_id,approval_id,action,target,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                (incident_id, approval_id, action, target, "EXECUTION_STARTED", timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO reserve_slots(slot_key,incident_id,action,target,state,acquired_at,updated_at) VALUES (?,?,?,?,?,?,?)",
                ("release_emergency_reserve", incident_id, action, target, "EXECUTION_STARTED", timestamp, timestamp),
            )
            connection.execute(
                "UPDATE reserve_capabilities SET consumed_at=?,consumed_incident_id=? WHERE approval_id=? AND consumed_at IS NULL",
                (timestamp, incident_id, approval_id),
            )
            payload = {"incident_id": incident_id, "approval_id": approval_id, "action": action, "target": target}
            self._audit(connection, kind="reserve_capability_consumed", incident_id=incident_id, approval_id=approval_id, payload=payload, recorded_at=timestamp)
            self._audit(connection, kind="reserve_execution_started", incident_id=incident_id, approval_id=approval_id, payload=payload, recorded_at=timestamp)
            connection.commit()
            return ReserveExecutionClaim(True, "EXECUTION_STARTED", "reserve_execution_claimed")
        except ReserveStateError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise ReserveStateError(f"reserve_execution_claim_failed:{exc}") from exc
        finally:
            connection.close()

    def finish_execution(
        self,
        *,
        incident_id: str,
        state: str,
        result: Mapping[str, Any],
        now: float | None = None,
    ) -> bool:
        """Finish once; UNKNOWN deliberately keeps the slot occupied."""

        if state not in {"RELEASED", "FAILED", "UNKNOWN", "EXECUTED_UNVERIFIED"}:
            raise ReserveStateError("reserve_execution_terminal_state_invalid")
        timestamp = time.time() if now is None else float(now)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT approval_id,state FROM reserve_incidents WHERE incident_id=?",
                (incident_id,),
            ).fetchone()
            if row is None or row[1] != "EXECUTION_STARTED":
                connection.rollback()
                return False
            payload = dict(result)
            connection.execute(
                "UPDATE reserve_incidents SET state=?,updated_at=?,result_json=? WHERE incident_id=? AND state='EXECUTION_STARTED'",
                (state, timestamp, self._json(payload), incident_id),
            )
            if state != "UNKNOWN" and state != "EXECUTED_UNVERIFIED":
                connection.execute("DELETE FROM reserve_slots WHERE slot_key=? AND incident_id=?", ("release_emergency_reserve", incident_id))
            else:
                connection.execute(
                    "UPDATE reserve_slots SET state=?,updated_at=? WHERE slot_key=? AND incident_id=?",
                    (state, timestamp, "release_emergency_reserve", incident_id),
                )
            self._audit(connection, kind="reserve_execution_finished", incident_id=incident_id, approval_id=str(row[0]), payload={"state": state, "result": payload}, recorded_at=timestamp)
            connection.commit()
            return True
        except ReserveStateError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise ReserveStateError(f"reserve_execution_finish_failed:{exc}") from exc
        finally:
            connection.close()


__all__ = ["ReserveBrokerStateStore", "ReserveExecutionClaim", "ReserveStateError"]
