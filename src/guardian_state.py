"""Crash-recoverable local state for Guardian action authorization.

This module is a durable safety boundary, not an action executor.  SQLite WAL
and ``BEGIN IMMEDIATE`` make capability consumption and intent claiming
serialized across processes.  An intent left in an in-flight state after a
crash is reconciled to ``RECONCILIATION_REQUIRED`` and is never retried
automatically.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class StateStoreError(RuntimeError):
    """Raised when durable safety state cannot be read or written."""


@dataclass(frozen=True)
class CapabilityDecision:
    allowed: bool
    reason: str


@dataclass(frozen=True)
class ReserveClaim:
    claimed: bool
    state: str
    reason: str


@dataclass(frozen=True)
class IntentClaim:
    claimed: bool
    intent_id: str
    idempotency_key: str
    state: str
    reason: str


_ACTIVE_INTENT_STATES = (
    "INTENT_RECORDED",
    "EXECUTION_STARTED",
    "RECONCILIATION_REQUIRED",
)
_FINAL_INTENT_STATES = {"PLANNED", "SUCCEEDED", "FAILED"}
_MAX_STATE_JSON_BYTES = 64 * 1024


class GuardianStateStore:
    """SQLite-backed capability, intent, audit and cooldown state."""

    def __init__(self, path: Path, *, file_mode: int = 0o600) -> None:
        self.path = Path(path)
        if file_mode not in {0o600, 0o660}:
            raise ValueError("state_store_file_mode_invalid")
        self.file_mode = file_mode
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        try:
            os.chmod(self.path, self.file_mode)
        except OSError as exc:
            # The shared runtime database is intentionally group-writable by
            # both guardian and guardian-broker. A non-owner process may use
            # that existing mode safely but cannot chmod the file itself.
            try:
                current_mode = stat.S_IMODE(os.stat(self.path).st_mode)
            except OSError as stat_exc:
                raise StateStoreError(f"state_store_permissions_failed:{stat_exc}") from stat_exc
            if current_mode & self.file_mode != self.file_mode:
                raise StateStoreError(f"state_store_permissions_failed:{exc}") from exc

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            return connection
        except sqlite3.Error as exc:
            raise StateStoreError(f"state_store_open_failed:{exc}") from exc

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS capabilities (
                    approval_id TEXT PRIMARY KEY,
                    environment TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    consumed_at REAL,
                    consumed_event_id TEXT
                );
                CREATE TABLE IF NOT EXISTS intents (
                    intent_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    host_id TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    result_json TEXT
                );
                CREATE INDEX IF NOT EXISTS intents_target_idx
                    ON intents(host_id, object_id, action, updated_at);
                CREATE TABLE IF NOT EXISTS audit_records (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    intent_id TEXT,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    recorded_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS action_ledger (
                    host_id TEXT NOT NULL,
                    object_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    last_action_at REAL,
                    action_times_json TEXT NOT NULL,
                    consecutive_failures INTEGER NOT NULL,
                    PRIMARY KEY(host_id, object_id, action)
                );
                CREATE TABLE IF NOT EXISTS action_slots (
                    host_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    intent_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    acquired_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(host_id, action)
                );
                CREATE TABLE IF NOT EXISTS reserve_incidents (
                    incident_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL,
                    target TEXT NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    result_json TEXT
                );
                """
            )
        except sqlite3.Error as exc:
            raise StateStoreError(f"state_store_initialize_failed:{exc}") from exc
        finally:
            connection.close()

    @staticmethod
    def _authorization_fields(authorization: Any) -> tuple[str, str, str, str, float]:
        try:
            approval_id = str(authorization.approval_id)
            environment = str(authorization.environment)
            target_id = str(authorization.target_id)
            action = str(authorization.action)
            expires_at = float(authorization.expires_at)
        except (AttributeError, TypeError, ValueError) as exc:
            raise StateStoreError("authorization_record_invalid") from exc
        if not approval_id:
            raise StateStoreError("approval_id_missing")
        return approval_id, environment, target_id, action, expires_at

    @staticmethod
    def _json(value: Any) -> str:
        try:
            encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise StateStoreError("audit_payload_not_serializable") from exc
        if len(encoded.encode("utf-8")) > _MAX_STATE_JSON_BYTES:
            raise StateStoreError("audit_payload_too_large")
        return encoded

    @staticmethod
    def _idempotency_key(host_id: str, object_id: str, action: str, event_id: str) -> str:
        raw = "\x00".join((host_id, object_id, action, event_id)).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    @staticmethod
    def _audit(
        connection: sqlite3.Connection,
        intent_id: str | None,
        kind: str,
        payload: Any,
        recorded_at: float,
    ) -> None:
        payload_json = GuardianStateStore._json(payload)
        try:
            connection.execute(
                "INSERT INTO audit_records(intent_id,kind,payload_json,recorded_at) VALUES (?,?,?,?)",
                (intent_id, kind, payload_json, recorded_at),
            )
        except sqlite3.Error as exc:
            raise StateStoreError(f"audit_write_failed:{exc}") from exc

    def register_capability(self, authorization: Any) -> None:
        """Register a short-lived capability without replacing an old one."""

        approval_id, environment, target_id, action, expires_at = self._authorization_fields(authorization)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT environment,target_id,action,expires_at FROM capabilities WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
            if existing is not None:
                if tuple(existing) != (environment, target_id, action, expires_at):
                    raise StateStoreError("capability_conflict")
                connection.commit()
                return
            connection.execute(
                "INSERT INTO capabilities(approval_id,environment,target_id,action,expires_at) VALUES (?,?,?,?,?)",
                (approval_id, environment, target_id, action, expires_at),
            )
            connection.commit()
        except StateStoreError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise StateStoreError(f"capability_register_failed:{exc}") from exc
        finally:
            connection.close()

    def consume_capability(
        self,
        authorization: Any,
        *,
        event_id: str,
        now: float | None = None,
    ) -> CapabilityDecision:
        approval_id, environment, target_id, action, expires_at = self._authorization_fields(authorization)
        timestamp = time.time() if now is None else now
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT environment,target_id,action,expires_at,consumed_at FROM capabilities WHERE approval_id=?",
                (approval_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return CapabilityDecision(False, "capability_not_registered")
            if tuple(row[:4]) != (environment, target_id, action, expires_at):
                connection.rollback()
                return CapabilityDecision(False, "capability_record_mismatch")
            if row[4] is not None:
                connection.rollback()
                return CapabilityDecision(False, "capability_already_consumed")
            if expires_at <= timestamp:
                connection.rollback()
                return CapabilityDecision(False, "capability_expired")
            connection.execute(
                "UPDATE capabilities SET consumed_at=?,consumed_event_id=? WHERE approval_id=? AND consumed_at IS NULL",
                (timestamp, event_id, approval_id),
            )
            self._audit(
                connection,
                None,
                "capability_consumed",
                {"approval_id": approval_id, "target_id": target_id, "action": action, "event_id": event_id},
                timestamp,
            )
            connection.commit()
            return CapabilityDecision(True, "capability_consumed")
        except StateStoreError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise StateStoreError(f"capability_consume_failed:{exc}") from exc
        finally:
            connection.close()

    def claim_reserve_incident(
        self,
        *,
        incident_id: str,
        action: str,
        target: str,
        now: float | None = None,
    ) -> ReserveClaim:
        """Claim one reserve recovery incident durably and never replay it."""

        if not incident_id or not action or not target:
            raise StateStoreError("reserve_incident_identity_missing")
        timestamp = time.time() if now is None else now
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT state FROM reserve_incidents WHERE incident_id=?",
                (incident_id,),
            ).fetchone()
            if existing is not None:
                connection.rollback()
                return ReserveClaim(False, str(existing[0]), "reserve_incident_already_claimed")
            connection.execute(
                "INSERT INTO reserve_incidents(incident_id,action,target,state,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (incident_id, action, target, "EXECUTION_STARTED", timestamp, timestamp),
            )
            self._audit(
                connection,
                None,
                "reserve_execution_started",
                {"incident_id": incident_id, "action": action, "target": target},
                timestamp,
            )
            connection.commit()
            return ReserveClaim(True, "EXECUTION_STARTED", "reserve_incident_claimed")
        except StateStoreError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise StateStoreError(f"reserve_incident_claim_failed:{exc}") from exc
        finally:
            connection.close()

    def finish_reserve_incident(
        self,
        *,
        incident_id: str,
        state: str,
        result: Mapping[str, Any],
        now: float | None = None,
    ) -> bool:
        """Persist a terminal reserve result; an in-flight claim is not replayed."""

        if state not in {"RELEASED", "FAILED"}:
            raise StateStoreError("reserve_incident_terminal_state_invalid")
        timestamp = time.time() if now is None else now
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT state FROM reserve_incidents WHERE incident_id=?",
                (incident_id,),
            ).fetchone()
            if current is None or current[0] != "EXECUTION_STARTED":
                connection.rollback()
                return False
            payload = dict(result)
            connection.execute(
                "UPDATE reserve_incidents SET state=?,updated_at=?,result_json=? WHERE incident_id=? AND state='EXECUTION_STARTED'",
                (state, timestamp, self._json(payload), incident_id),
            )
            self._audit(
                connection,
                None,
                "reserve_execution_finished",
                {"incident_id": incident_id, "state": state, "result": payload},
                timestamp,
            )
            connection.commit()
            return True
        except StateStoreError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise StateStoreError(f"reserve_incident_finish_failed:{exc}") from exc
        finally:
            connection.close()

    def claim_intent(
        self,
        *,
        host_id: str,
        object_id: str,
        action: str,
        event_id: str,
        audit_payload: Mapping[str, Any],
        now: float | None = None,
    ) -> IntentClaim:
        timestamp = time.time() if now is None else now
        idempotency_key = self._idempotency_key(host_id, object_id, action, event_id)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT intent_id,state FROM intents WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                connection.rollback()
                return IntentClaim(False, existing[0], idempotency_key, existing[1], "idempotent_intent_reused")
            active = connection.execute(
                "SELECT intent_id,state FROM intents WHERE host_id=? AND object_id=? AND action=? AND state IN (?,?,?) LIMIT 1",
                (host_id, object_id, action, *_ACTIVE_INTENT_STATES),
            ).fetchone()
            if active is not None:
                connection.rollback()
                return IntentClaim(False, active[0], idempotency_key, active[1], "active_intent_exists")
            intent_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO intents(intent_id,idempotency_key,host_id,object_id,action,event_id,state,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (intent_id, idempotency_key, host_id, object_id, action, event_id, "INTENT_RECORDED", timestamp, timestamp),
            )
            self._audit(connection, intent_id, "intent_recorded", dict(audit_payload), timestamp)
            connection.commit()
            return IntentClaim(True, intent_id, idempotency_key, "INTENT_RECORDED", "intent_claimed")
        except StateStoreError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise StateStoreError(f"intent_claim_failed:{exc}") from exc
        finally:
            connection.close()

    def mark_execution_started(self, intent_id: str, *, now: float | None = None) -> bool:
        timestamp = time.time() if now is None else now
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT state FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            if row is None:
                raise StateStoreError("intent_not_found")
            if row[0] != "INTENT_RECORDED":
                connection.rollback()
                return False
            connection.execute(
                "UPDATE intents SET state='EXECUTION_STARTED',updated_at=? WHERE intent_id=? AND state='INTENT_RECORDED'",
                (timestamp, intent_id),
            )
            self._audit(connection, intent_id, "execution_started", {}, timestamp)
            connection.commit()
            return True
        except StateStoreError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise StateStoreError(f"execution_start_failed:{exc}") from exc
        finally:
            connection.close()

    def record_result(
        self,
        intent_id: str,
        result: Mapping[str, Any],
        *,
        success: bool,
        executed: bool,
        now: float | None = None,
    ) -> str:
        timestamp = time.time() if now is None else now
        final_state = "PLANNED" if not executed else "SUCCEEDED" if success else "FAILED"
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT state FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            if row is None:
                raise StateStoreError("intent_not_found")
            if row[0] in _FINAL_INTENT_STATES:
                connection.rollback()
                return row[0]
            connection.execute(
                "UPDATE intents SET state=?,updated_at=?,result_json=? WHERE intent_id=?",
                (final_state, timestamp, self._json(dict(result)), intent_id),
            )
            self._audit(
                connection,
                intent_id,
                "result_recorded",
                {"result": dict(result), "executed": executed, "success": success},
                timestamp,
            )
            connection.commit()
            return final_state
        except StateStoreError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise StateStoreError(f"result_record_failed:{exc}") from exc
        finally:
            connection.close()

    def reconcile_pending(self, *, now: float | None = None) -> list[dict[str, Any]]:
        """Mark incomplete intents as manual-reconciliation required."""

        timestamp = time.time() if now is None else now
        connection = self._connect()
        pending: list[dict[str, Any]] = []
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT intent_id,host_id,object_id,action,event_id,state FROM intents WHERE state IN (?,?,?) ORDER BY created_at",
                _ACTIVE_INTENT_STATES,
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE intents SET state='RECONCILIATION_REQUIRED',updated_at=? WHERE intent_id=?",
                    (timestamp, row[0]),
                )
                self._audit(
                    connection,
                    row[0],
                    "reconciliation_required",
                    {"previous_state": row[5], "event_id": row[4]},
                    timestamp,
                )
                pending.append(dict(row))
            slot_rows = connection.execute(
                "SELECT host_id,action,intent_id,state,acquired_at,updated_at FROM action_slots WHERE state='ACTIVE' ORDER BY acquired_at"
            ).fetchall()
            for row in slot_rows:
                connection.execute(
                    "UPDATE action_slots SET state='RECONCILIATION_REQUIRED',updated_at=? WHERE host_id=? AND action=? AND state='ACTIVE'",
                    (timestamp, row[0], row[1]),
                )
                self._audit(
                    connection,
                    row[2],
                    "action_slot_reconciliation_required",
                    {"host_id": row[0], "action": row[1], "previous_state": row[3]},
                    timestamp,
                )
                pending.append(
                    {
                        "kind": "action_slot",
                        "host_id": row[0],
                        "action": row[1],
                        "intent_id": row[2],
                        "state": "RECONCILIATION_REQUIRED",
                    }
                )
            connection.commit()
            for item in pending:
                item["state"] = "RECONCILIATION_REQUIRED"
            return pending
        except StateStoreError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise StateStoreError(f"reconciliation_failed:{exc}") from exc
        finally:
            connection.close()

    def claim_action_slot(self, host_id: str, action: str, intent_id: str, *, now: float | None = None) -> bool:
        """Reserve one host/action execution slot before calling an adapter.

        The existing cooldown ledger records completed actions.  This separate
        durable lease closes the concurrent gap between the preflight check and
        the adapter call, so two targets cannot both become the first action
        while the ledger is still empty.  A lease left behind by a crash is
        converted to reconciliation-required on the next startup.
        """

        timestamp = time.time() if now is None else now
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT intent_id,state FROM action_slots WHERE host_id=? AND action=?",
                (host_id, action),
            ).fetchone()
            if existing is not None:
                connection.rollback()
                return False
            connection.execute(
                "INSERT INTO action_slots(host_id,action,intent_id,state,acquired_at,updated_at) VALUES (?,?,?,?,?,?)",
                (host_id, action, intent_id, "ACTIVE", timestamp, timestamp),
            )
            self._audit(
                connection,
                intent_id,
                "action_slot_claimed",
                {"host_id": host_id, "action": action},
                timestamp,
            )
            connection.commit()
            return True
        except StateStoreError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise StateStoreError(f"action_slot_claim_failed:{exc}") from exc
        finally:
            connection.close()

    def release_action_slot(self, host_id: str, action: str, intent_id: str, *, now: float | None = None) -> bool:
        """Release a slot only when it still belongs to the same intent."""

        timestamp = time.time() if now is None else now
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT intent_id,state FROM action_slots WHERE host_id=? AND action=?",
                (host_id, action),
            ).fetchone()
            if existing is None or existing[0] != intent_id or existing[1] != "ACTIVE":
                connection.rollback()
                return False
            connection.execute(
                "DELETE FROM action_slots WHERE host_id=? AND action=? AND intent_id=? AND state='ACTIVE'",
                (host_id, action, intent_id),
            )
            self._audit(
                connection,
                intent_id,
                "action_slot_released",
                {"host_id": host_id, "action": action},
                timestamp,
            )
            connection.commit()
            return True
        except StateStoreError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise StateStoreError(f"action_slot_release_failed:{exc}") from exc
        finally:
            connection.close()

    def allow_action(
        self,
        host_id: str,
        object_id: str,
        action: str,
        now: float,
        cooldown_seconds: float,
        max_actions: int,
        window_seconds: float,
    ) -> tuple[bool, str]:
        if max_actions <= 0:
            return False, "action_limit_reached"
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT last_action_at,action_times_json FROM action_ledger WHERE host_id=? AND object_id=? AND action=?",
                (host_id, object_id, action),
            ).fetchone()
            if row is None:
                connection.commit()
                return True, "allowed"
            try:
                action_times = [float(value) for value in json.loads(row[1])]
            except (TypeError, ValueError, json.JSONDecodeError):
                raise StateStoreError("action_ledger_corrupt")
            action_times = [value for value in action_times if now - value <= window_seconds]
            if row[0] is not None and now - float(row[0]) < cooldown_seconds:
                connection.commit()
                return False, "cooldown_active"
            if len(action_times) >= max_actions:
                connection.commit()
                return False, "action_limit_reached"
            connection.execute(
                "UPDATE action_ledger SET action_times_json=? WHERE host_id=? AND object_id=? AND action=?",
                (json.dumps(action_times), host_id, object_id, action),
            )
            connection.commit()
            return True, "allowed"
        except StateStoreError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise StateStoreError(f"cooldown_read_failed:{exc}") from exc
        finally:
            connection.close()

    def record_action(self, host_id: str, object_id: str, action: str, now: float, success: bool) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT action_times_json,consecutive_failures FROM action_ledger WHERE host_id=? AND object_id=? AND action=?",
                (host_id, object_id, action),
            ).fetchone()
            if row is None:
                action_times: list[float] = []
                failures = 0
            else:
                try:
                    action_times = [float(value) for value in json.loads(row[0])]
                except (TypeError, ValueError, json.JSONDecodeError):
                    raise StateStoreError("action_ledger_corrupt")
                failures = int(row[1])
            action_times.append(now)
            failures = 0 if success else failures + 1
            connection.execute(
                "INSERT INTO action_ledger(host_id,object_id,action,last_action_at,action_times_json,consecutive_failures) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(host_id,object_id,action) DO UPDATE SET last_action_at=excluded.last_action_at,action_times_json=excluded.action_times_json,consecutive_failures=excluded.consecutive_failures",
                (host_id, object_id, action, now, json.dumps(action_times), failures),
            )
            connection.commit()
        except StateStoreError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise StateStoreError(f"cooldown_write_failed:{exc}") from exc
        finally:
            connection.close()

    def failure_breaker(self, host_id: str, object_id: str, action: str, max_consecutive_failures: int) -> bool:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT consecutive_failures FROM action_ledger WHERE host_id=? AND object_id=? AND action=?",
                (host_id, object_id, action),
            ).fetchone()
            return row is not None and int(row[0]) >= max_consecutive_failures
        except sqlite3.Error as exc:
            raise StateStoreError(f"cooldown_read_failed:{exc}") from exc
        finally:
            connection.close()

    def get_intent(self, intent_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM intents WHERE intent_id=?", (intent_id,)).fetchone()
            return dict(row) if row is not None else None
        except sqlite3.Error as exc:
            raise StateStoreError(f"intent_read_failed:{exc}") from exc
        finally:
            connection.close()


__all__ = ["CapabilityDecision", "GuardianStateStore", "IntentClaim", "ReserveClaim", "StateStoreError"]
