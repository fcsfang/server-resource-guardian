"""Durable local notification outbox.

The outbox is intentionally a notification-only boundary.  It persists a
redacted ``guardian.notification.v1`` payload before delivery, keeps dedup /
dead-letter state in SQLite WAL, and never imports an action adapter.  A
crash after a sink call but before the result commit is treated as ambiguous:
the row becomes a durable dead letter and requires explicit redrive instead of
an automatic retry that could duplicate a notification.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .guardian_notifications import (
    NOTIFIABLE_STATES,
    NOTIFICATION_SCHEMA,
    NotificationSinkUnavailable,
    _bounded_int,
    _duration_to_ns,
    build_notification_event,
)


OUTBOX_SCHEMA = "guardian.notification.outbox.v1"
_ACTIVE_STATUSES = ("PENDING", "IN_FLIGHT")
_FINAL_STATUSES = ("DELIVERED", "DEAD_LETTER")


class NotificationOutboxError(RuntimeError):
    """Raised when durable outbox state cannot be read or written."""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise NotificationOutboxError("payload_not_serializable") from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


class DurableNotificationOutbox:
    """SQLite-backed, bounded outbox around an injected local sink."""

    def __init__(
        self,
        path: Path,
        sink: Any,
        *,
        max_attempts: int = 3,
        max_age_seconds: float = 300.0,
        rate_limit_count: int = 10,
        rate_limit_window_seconds: float = 60.0,
        max_pending: int = 1000,
        max_payload_bytes: int = 1_048_576,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        max_attempts = _bounded_int(max_attempts, minimum=1, maximum=5, error="max_attempts_out_of_bounds")
        max_age_ns = _duration_to_ns(max_age_seconds, error="notification_window_must_be_positive")
        rate_limit_count = _bounded_int(rate_limit_count, minimum=1, maximum=100, error="rate_limit_count_out_of_bounds")
        rate_limit_window_ns = _duration_to_ns(
            rate_limit_window_seconds,
            error="notification_window_must_be_positive",
        )
        max_pending = _bounded_int(max_pending, minimum=1, maximum=100_000, error="max_pending_out_of_bounds")
        max_payload_bytes = _bounded_int(
            max_payload_bytes,
            minimum=1024,
            maximum=16 * 1024 * 1024,
            error="max_payload_bytes_out_of_bounds",
        )
        if not callable(getattr(sink, "send", None)):
            raise ValueError("notification_sink_send_required")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.sink = sink
        self.max_attempts = max_attempts
        self.max_age_ns = max_age_ns
        self.rate_limit_count = rate_limit_count
        self.rate_limit_window_ns = rate_limit_window_ns
        self.max_pending = max_pending
        self.max_payload_bytes = max_payload_bytes
        self.sleep = sleep or (lambda _seconds: None)
        self._initialize()
        self.reconcile_in_flight()

    def _connect(self) -> sqlite3.Connection:
        try:
            connection = sqlite3.connect(str(self.path), timeout=5.0, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("PRAGMA journal_mode=WAL")
            return connection
        except sqlite3.Error as exc:
            raise NotificationOutboxError(f"outbox_open_failed:{exc}") from exc

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS notification_outbox (
                    notification_id TEXT PRIMARY KEY,
                    dedup_key TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    observed_monotonic_ns INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS notification_outbox_status_idx
                    ON notification_outbox(status, created_at);
                CREATE INDEX IF NOT EXISTS notification_outbox_dedup_idx
                    ON notification_outbox(dedup_key, severity, status);
                CREATE TABLE IF NOT EXISTS notification_dedup_state (
                    dedup_key TEXT PRIMARY KEY,
                    last_observed_monotonic_ns INTEGER NOT NULL,
                    delivered_severities_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notification_attempts (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempted_monotonic_ns INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS notification_attempts_time_idx
                    ON notification_attempts(attempted_monotonic_ns);
                CREATE TABLE IF NOT EXISTS notification_audits (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    notification_id TEXT,
                    dedup_key TEXT,
                    event_id TEXT,
                    severity TEXT,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL,
                    reason TEXT,
                    payload_digest TEXT,
                    action_authorization TEXT NOT NULL,
                    recorded_at REAL NOT NULL,
                    previous_digest TEXT,
                    record_digest TEXT NOT NULL
                );
                """
            )
        except sqlite3.Error as exc:
            raise NotificationOutboxError(f"outbox_initialize_failed:{exc}") from exc
        finally:
            connection.close()

    @staticmethod
    def _result(
        payload: Mapping[str, Any] | None,
        *,
        status: str,
        attempts: int = 0,
        reason: str | None = None,
        outbox_status: str | None = None,
    ) -> dict[str, Any]:
        return {
            "schema": OUTBOX_SCHEMA,
            "status": status,
            "outbox_status": outbox_status,
            "notification_id": payload.get("notification_id") if isinstance(payload, Mapping) else None,
            "dedup_key": payload.get("dedup_key") if isinstance(payload, Mapping) else None,
            "attempts": attempts,
            "reason": reason,
            "execution": "not_executed",
            "action_authorization": "unchanged",
        }

    def _validate_payload(
        self,
        payload: Mapping[str, Any],
        *,
        now_monotonic_ns: int,
    ) -> str | None:
        if payload.get("schema") != NOTIFICATION_SCHEMA:
            return "schema_invalid"
        for field in ("notification_id", "dedup_key"):
            if not isinstance(payload.get(field), str) or not payload[field]:
                return "payload_invalid"
        if payload.get("severity") not in NOTIFIABLE_STATES:
            return "payload_invalid"
        observed = payload.get("observed_monotonic_ns")
        if isinstance(observed, bool) or not isinstance(observed, int) or observed <= 0:
            return "timestamp_invalid"
        if now_monotonic_ns < observed:
            return "clock_invalid"
        if now_monotonic_ns - observed > self.max_age_ns:
            return "expired"
        source = _mapping(payload.get("source"))
        if not isinstance(source.get("event_id"), str) or not source["event_id"]:
            return "payload_invalid"
        action_context = _mapping(payload.get("action_context"))
        if action_context.get("execution") != "not_executed" or action_context.get("authorization") != "unchanged":
            return "action_boundary_invalid"
        security = _mapping(payload.get("security"))
        if (
            security.get("credentials_included") is not False
            or security.get("raw_signals_included") is not False
            or security.get("action_authorization_changed") is not False
        ):
            return "security_boundary_invalid"
        try:
            encoded = _canonical(payload).encode("utf-8")
        except NotificationOutboxError:
            return "payload_not_serializable"
        if len(encoded) > self.max_payload_bytes:
            return "payload_too_large"
        return None

    def _append_audit(
        self,
        connection: sqlite3.Connection,
        payload: Mapping[str, Any] | None,
        *,
        status: str,
        attempts: int,
        reason: str | None,
        recorded_at: float,
    ) -> None:
        payload_map = _mapping(payload)
        source = _mapping(payload_map.get("source"))
        action_authorization = "unchanged"
        try:
            payload_digest = _digest(dict(payload_map)) if payload_map else None
        except NotificationOutboxError:
            # Rejection audits must remain writable even when the rejected
            # caller object itself is not JSON serializable.
            payload_digest = None
        previous = connection.execute(
            "SELECT record_digest FROM notification_audits ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        previous_digest = previous[0] if previous is not None else None
        body = {
            "notification_id": payload_map.get("notification_id") if isinstance(payload_map.get("notification_id"), str) else None,
            "dedup_key": payload_map.get("dedup_key") if isinstance(payload_map.get("dedup_key"), str) else None,
            "event_id": source.get("event_id") if isinstance(source.get("event_id"), str) else None,
            "severity": payload_map.get("severity") if isinstance(payload_map.get("severity"), str) else None,
            "status": status,
            "attempts": attempts,
            "reason": reason,
            "payload_digest": payload_digest,
            "action_authorization": action_authorization,
            "recorded_at": recorded_at,
            "previous_digest": previous_digest,
        }
        record_digest = _digest(body)
        try:
            connection.execute(
                "INSERT INTO notification_audits(notification_id,dedup_key,event_id,severity,status,attempts,reason,payload_digest,action_authorization,recorded_at,previous_digest,record_digest) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    body["notification_id"],
                    body["dedup_key"],
                    body["event_id"],
                    body["severity"],
                    body["status"],
                    body["attempts"],
                    body["reason"],
                    body["payload_digest"],
                    body["action_authorization"],
                    body["recorded_at"],
                    body["previous_digest"],
                    record_digest,
                ),
            )
        except sqlite3.Error as exc:
            raise NotificationOutboxError(f"audit_write_failed:{exc}") from exc

    def _record_rejection(
        self,
        payload: Mapping[str, Any] | None,
        *,
        reason: str,
    ) -> dict[str, Any]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._append_audit(
                connection,
                payload,
                status="REJECTED",
                attempts=0,
                reason=reason,
                recorded_at=time.time(),
            )
            connection.commit()
        except NotificationOutboxError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise NotificationOutboxError(f"rejection_audit_failed:{exc}") from exc
        finally:
            connection.close()
        return self._result(payload, status="rejected", reason=reason)

    def enqueue(
        self,
        payload: Mapping[str, Any],
        *,
        now_monotonic_ns: int | None = None,
    ) -> dict[str, Any]:
        """Persist one payload before any sink call is attempted."""

        if not isinstance(payload, Mapping):
            return self._record_rejection(None, reason="payload_mapping_required")
        now = time.monotonic_ns() if now_monotonic_ns is None else now_monotonic_ns
        reason = self._validate_payload(payload, now_monotonic_ns=now)
        if reason:
            return self._record_rejection(payload, reason=reason)
        source = _mapping(payload["source"])
        event_id = str(source["event_id"])
        notification_id = str(payload["notification_id"])
        dedup_key = str(payload["dedup_key"])
        severity = str(payload["severity"])
        observed = int(payload["observed_monotonic_ns"])
        encoded = _canonical(dict(payload))
        timestamp = time.time()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT status,attempts FROM notification_outbox WHERE notification_id=?",
                (notification_id,),
            ).fetchone()
            if existing is not None:
                result = self._result(payload, status="duplicate_suppressed", reason="notification_id_seen", outbox_status=existing[0])
                self._append_audit(connection, payload, status="DUPLICATE_SUPPRESSED", attempts=int(existing[1]), reason="notification_id_seen", recorded_at=timestamp)
                connection.commit()
                return result
            existing_event = connection.execute(
                "SELECT status,attempts FROM notification_outbox WHERE event_id=? LIMIT 1",
                (event_id,),
            ).fetchone()
            if existing_event is not None:
                result = self._result(payload, status="duplicate_suppressed", reason="event_id_seen", outbox_status=existing_event[0])
                self._append_audit(connection, payload, status="DUPLICATE_SUPPRESSED", attempts=int(existing_event[1]), reason="event_id_seen", recorded_at=timestamp)
                connection.commit()
                return result
            state = connection.execute(
                "SELECT last_observed_monotonic_ns,delivered_severities_json FROM notification_dedup_state WHERE dedup_key=?",
                (dedup_key,),
            ).fetchone()
            delivered: set[str] = set()
            last_observed: int | None = None
            if state is not None:
                last_observed = int(state[0])
                try:
                    delivered = {str(item) for item in json.loads(state[1]) if isinstance(item, str)}
                except (TypeError, ValueError, json.JSONDecodeError):
                    raise NotificationOutboxError("dedup_state_corrupt")
            if last_observed is not None and observed < last_observed:
                result = self._result(payload, status="rejected", reason="out_of_order")
                self._append_audit(connection, payload, status="REJECTED", attempts=0, reason="out_of_order", recorded_at=timestamp)
                connection.commit()
                return result
            if severity in delivered:
                result = self._result(payload, status="duplicate_suppressed", reason="severity_already_delivered")
                self._append_audit(connection, payload, status="DUPLICATE_SUPPRESSED", attempts=0, reason="severity_already_delivered", recorded_at=timestamp)
                connection.commit()
                return result
            pending_same_severity = connection.execute(
                "SELECT status,attempts FROM notification_outbox WHERE dedup_key=? AND severity=? AND status IN ('PENDING','IN_FLIGHT') LIMIT 1",
                (dedup_key, severity),
            ).fetchone()
            if pending_same_severity is not None:
                result = self._result(payload, status="duplicate_suppressed", reason="severity_pending", outbox_status=pending_same_severity[0])
                self._append_audit(connection, payload, status="DUPLICATE_SUPPRESSED", attempts=int(pending_same_severity[1]), reason="severity_pending", recorded_at=timestamp)
                connection.commit()
                return result
            active_count = connection.execute(
                "SELECT COUNT(*) FROM notification_outbox WHERE status IN ('PENDING','IN_FLIGHT')"
            ).fetchone()[0]
            if int(active_count) >= self.max_pending:
                result = self._result(payload, status="rejected", reason="outbox_capacity_reached")
                self._append_audit(connection, payload, status="REJECTED", attempts=0, reason="outbox_capacity_reached", recorded_at=timestamp)
                connection.commit()
                return result
            connection.execute(
                "INSERT INTO notification_outbox(notification_id,dedup_key,event_id,severity,observed_monotonic_ns,payload_json,status,attempts,last_error,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (notification_id, dedup_key, event_id, severity, observed, encoded, "PENDING", 0, None, timestamp, timestamp),
            )
            connection.execute(
                "INSERT INTO notification_dedup_state(dedup_key,last_observed_monotonic_ns,delivered_severities_json) VALUES (?,?,?) ON CONFLICT(dedup_key) DO UPDATE SET last_observed_monotonic_ns=excluded.last_observed_monotonic_ns",
                (dedup_key, max(last_observed or observed, observed), json.dumps(sorted(delivered))),
            )
            self._append_audit(connection, payload, status="ENQUEUED", attempts=0, reason=None, recorded_at=timestamp)
            connection.commit()
            return self._result(payload, status="queued", outbox_status="PENDING")
        except NotificationOutboxError:
            connection.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            connection.rollback()
            raise NotificationOutboxError(f"outbox_enqueue_conflict:{exc}") from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise NotificationOutboxError(f"outbox_enqueue_failed:{exc}") from exc
        finally:
            connection.close()

    def enqueue_event(
        self,
        source_event: Mapping[str, Any],
        *,
        incident_key: str | None = None,
        now_monotonic_ns: int | None = None,
    ) -> dict[str, Any]:
        """Build the redacted v1 event, then persist it before delivery."""

        payload = build_notification_event(source_event, incident_key=incident_key)
        return self.enqueue(payload, now_monotonic_ns=now_monotonic_ns)

    def _claim_one(self) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM notification_outbox WHERE status='PENDING' ORDER BY created_at,notification_id LIMIT 1"
            ).fetchone()
            if row is None:
                connection.rollback()
                return None
            payload = json.loads(row["payload_json"])
            timestamp = time.time()
            connection.execute(
                "UPDATE notification_outbox SET status='IN_FLIGHT',updated_at=? WHERE notification_id=? AND status='PENDING'",
                (timestamp, row["notification_id"]),
            )
            self._append_audit(connection, payload, status="DELIVERY_STARTED", attempts=int(row["attempts"]), reason=None, recorded_at=timestamp)
            connection.commit()
            result = dict(row)
            result["status"] = "IN_FLIGHT"
            result["payload"] = payload
            return result
        except (NotificationOutboxError, json.JSONDecodeError) as exc:
            connection.rollback()
            if isinstance(exc, NotificationOutboxError):
                raise
            raise NotificationOutboxError("outbox_payload_corrupt") from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise NotificationOutboxError(f"outbox_claim_failed:{exc}") from exc
        finally:
            connection.close()

    def _allow_attempt(self, connection: sqlite3.Connection, now_monotonic_ns: int) -> bool:
        cutoff = now_monotonic_ns - self.rate_limit_window_ns
        connection.execute("DELETE FROM notification_attempts WHERE attempted_monotonic_ns<=?", (cutoff,))
        count = connection.execute("SELECT COUNT(*) FROM notification_attempts").fetchone()[0]
        if int(count) >= self.rate_limit_count:
            return False
        connection.execute(
            "INSERT INTO notification_attempts(attempted_monotonic_ns) VALUES (?)",
            (now_monotonic_ns,),
        )
        return True

    def _mark_final(
        self,
        row: Mapping[str, Any],
        *,
        status: str,
        attempts: int,
        reason: str | None,
    ) -> dict[str, Any]:
        payload = _mapping(row.get("payload"))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            timestamp = time.time()
            connection.execute(
                "UPDATE notification_outbox SET status=?,attempts=?,last_error=?,updated_at=? WHERE notification_id=? AND status='IN_FLIGHT'",
                (status, attempts, reason, timestamp, row["notification_id"]),
            )
            if status == "DELIVERED":
                dedup_key = str(row["dedup_key"])
                severity = str(row["severity"])
                state = connection.execute(
                    "SELECT last_observed_monotonic_ns,delivered_severities_json FROM notification_dedup_state WHERE dedup_key=?",
                    (dedup_key,),
                ).fetchone()
                last_observed = int(state[0]) if state is not None else int(row["observed_monotonic_ns"])
                delivered: set[str] = set()
                if state is not None:
                    try:
                        delivered = {str(item) for item in json.loads(state[1]) if isinstance(item, str)}
                    except (TypeError, ValueError, json.JSONDecodeError) as exc:
                        raise NotificationOutboxError("dedup_state_corrupt") from exc
                if severity == "recovered":
                    delivered.clear()
                delivered.add(severity)
                connection.execute(
                    "INSERT INTO notification_dedup_state(dedup_key,last_observed_monotonic_ns,delivered_severities_json) VALUES (?,?,?) ON CONFLICT(dedup_key) DO UPDATE SET delivered_severities_json=excluded.delivered_severities_json,last_observed_monotonic_ns=MAX(notification_dedup_state.last_observed_monotonic_ns,excluded.last_observed_monotonic_ns)",
                    (dedup_key, last_observed, json.dumps(sorted(delivered))),
                )
            self._append_audit(
                connection,
                payload,
                status=status,
                attempts=attempts,
                reason=reason,
                recorded_at=timestamp,
            )
            connection.commit()
        except NotificationOutboxError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise NotificationOutboxError(f"outbox_finalize_failed:{exc}") from exc
        finally:
            connection.close()
        result_status = "delivered" if status == "DELIVERED" else "dead_letter"
        return self._result(payload, status=result_status, attempts=attempts, reason=reason, outbox_status=status)

    def _rate_limited(self, row: Mapping[str, Any]) -> dict[str, Any]:
        payload = _mapping(row.get("payload"))
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            timestamp = time.time()
            connection.execute(
                "UPDATE notification_outbox SET status='DEAD_LETTER',last_error='rate_limited',updated_at=? WHERE notification_id=? AND status='IN_FLIGHT'",
                (timestamp, row["notification_id"]),
            )
            self._append_audit(connection, payload, status="DEAD_LETTER", attempts=int(row["attempts"]), reason="rate_limited", recorded_at=timestamp)
            connection.commit()
        except NotificationOutboxError:
            connection.rollback()
            raise
        except sqlite3.Error as exc:
            connection.rollback()
            raise NotificationOutboxError(f"rate_limit_finalize_failed:{exc}") from exc
        finally:
            connection.close()
        return self._result(payload, status="rate_limited", reason="rate_limited", outbox_status="DEAD_LETTER")

    def _deliver_row(self, row: Mapping[str, Any], *, now_monotonic_ns: int) -> dict[str, Any]:
        payload = _mapping(row.get("payload"))
        observed = int(row["observed_monotonic_ns"])
        if now_monotonic_ns < observed:
            return self._mark_final(row, status="DEAD_LETTER", attempts=int(row["attempts"]), reason="clock_invalid")
        if now_monotonic_ns - observed > self.max_age_ns:
            return self._mark_final(row, status="DEAD_LETTER", attempts=int(row["attempts"]), reason="expired")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            allowed = self._allow_attempt(connection, now_monotonic_ns)
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise NotificationOutboxError(f"attempt_gate_failed:{exc}") from exc
        finally:
            connection.close()
        if not allowed:
            return self._rate_limited(row)

        attempts = int(row["attempts"])
        error_code: str | None = None
        for _ in range(self.max_attempts):
            attempts += 1
            try:
                self.sink.send(payload)
            except NotificationSinkUnavailable:
                error_code = "sink_unavailable"
            except Exception:
                error_code = "sink_error"
            else:
                return self._mark_final(row, status="DELIVERED", attempts=attempts, reason=None)
            if attempts < self.max_attempts:
                self.sleep(min(2.0, 0.25 * (2 ** (attempts - 1))))
        return self._mark_final(row, status="DEAD_LETTER", attempts=attempts, reason=error_code or "sink_error")

    def drain_pending(
        self,
        *,
        now_monotonic_ns: int,
        max_items: int = 100,
    ) -> list[dict[str, Any]]:
        """Deliver a bounded batch; a sink failure never grants action rights."""

        max_items = _bounded_int(max_items, minimum=1, maximum=1000, error="max_items_out_of_bounds")
        results: list[dict[str, Any]] = []
        for _ in range(max_items):
            row = self._claim_one()
            if row is None:
                break
            results.append(self._deliver_row(row, now_monotonic_ns=now_monotonic_ns))
        return results

    def reconcile_in_flight(self) -> int:
        """Convert ambiguous post-crash deliveries to explicit dead letters."""

        connection = self._connect()
        recovered = 0
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM notification_outbox WHERE status='IN_FLIGHT' ORDER BY created_at"
            ).fetchall()
            timestamp = time.time()
            for row in rows:
                payload = json.loads(row["payload_json"])
                connection.execute(
                    "UPDATE notification_outbox SET status='DEAD_LETTER',last_error='delivery_interrupted',updated_at=? WHERE notification_id=? AND status='IN_FLIGHT'",
                    (timestamp, row["notification_id"]),
                )
                self._append_audit(
                    connection,
                    payload,
                    status="RECONCILIATION_REQUIRED",
                    attempts=int(row["attempts"]),
                    reason="delivery_interrupted",
                    recorded_at=timestamp,
                )
                recovered += 1
            connection.commit()
            return recovered
        except (NotificationOutboxError, json.JSONDecodeError) as exc:
            connection.rollback()
            if isinstance(exc, NotificationOutboxError):
                raise
            raise NotificationOutboxError("outbox_payload_corrupt") from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise NotificationOutboxError(f"reconciliation_failed:{exc}") from exc
        finally:
            connection.close()

    def redrive_dead_letters(
        self,
        *,
        now_monotonic_ns: int,
        max_items: int = 100,
    ) -> list[dict[str, Any]]:
        """Explicitly requeue durable dead letters, then drain them once."""

        max_items = _bounded_int(max_items, minimum=1, maximum=1000, error="max_items_out_of_bounds")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                "SELECT * FROM notification_outbox WHERE status='DEAD_LETTER' ORDER BY updated_at,notification_id LIMIT ?",
                (max_items,),
            ).fetchall()
            timestamp = time.time()
            for row in rows:
                payload = json.loads(row["payload_json"])
                connection.execute(
                    "UPDATE notification_outbox SET status='PENDING',last_error=NULL,updated_at=? WHERE notification_id=? AND status='DEAD_LETTER'",
                    (timestamp, row["notification_id"]),
                )
                self._append_audit(
                    connection,
                    payload,
                    status="REDRIVE_QUEUED",
                    attempts=int(row["attempts"]),
                    reason="explicit_redrive",
                    recorded_at=timestamp,
                )
            connection.commit()
        except (NotificationOutboxError, json.JSONDecodeError) as exc:
            connection.rollback()
            if isinstance(exc, NotificationOutboxError):
                raise
            raise NotificationOutboxError("outbox_payload_corrupt") from exc
        except sqlite3.Error as exc:
            connection.rollback()
            raise NotificationOutboxError(f"redrive_queue_failed:{exc}") from exc
        finally:
            connection.close()
        return self.drain_pending(now_monotonic_ns=now_monotonic_ns, max_items=max_items)

    def get_notification(self, notification_id: str) -> dict[str, Any] | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM notification_outbox WHERE notification_id=?",
                (notification_id,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["payload"] = json.loads(result.pop("payload_json"))
            return result
        except (sqlite3.Error, json.JSONDecodeError) as exc:
            raise NotificationOutboxError(f"notification_read_failed:{exc}") from exc
        finally:
            connection.close()

    def status_counts(self) -> dict[str, int]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT status,COUNT(*) FROM notification_outbox GROUP BY status"
            ).fetchall()
            result = {str(row[0]): int(row[1]) for row in rows}
            return {status: result.get(status, 0) for status in (*_ACTIVE_STATUSES, *_FINAL_STATUSES)}
        except sqlite3.Error as exc:
            raise NotificationOutboxError(f"status_read_failed:{exc}") from exc
        finally:
            connection.close()

    def dead_letter_count(self) -> int:
        return self.status_counts()["DEAD_LETTER"]

    def verify_audit(self) -> dict[str, Any]:
        connection = self._connect()
        errors: list[str] = []
        try:
            rows = connection.execute(
                "SELECT * FROM notification_audits ORDER BY sequence"
            ).fetchall()
            previous: str | None = None
            for row in rows:
                body = {
                    "notification_id": row["notification_id"],
                    "dedup_key": row["dedup_key"],
                    "event_id": row["event_id"],
                    "severity": row["severity"],
                    "status": row["status"],
                    "attempts": int(row["attempts"]),
                    "reason": row["reason"],
                    "payload_digest": row["payload_digest"],
                    "action_authorization": row["action_authorization"],
                    "recorded_at": row["recorded_at"],
                    "previous_digest": row["previous_digest"],
                }
                if body["previous_digest"] != previous:
                    errors.append(f"previous_digest_mismatch:{row['sequence']}")
                if body["action_authorization"] != "unchanged":
                    errors.append(f"action_authorization_changed:{row['sequence']}")
                if _digest(body) != row["record_digest"]:
                    errors.append(f"record_digest_mismatch:{row['sequence']}")
                previous = row["record_digest"]
            return {"valid": not errors, "records": len(rows), "errors": errors}
        except sqlite3.Error as exc:
            raise NotificationOutboxError(f"audit_read_failed:{exc}") from exc
        finally:
            connection.close()


__all__ = ["DurableNotificationOutbox", "NotificationOutboxError", "OUTBOX_SCHEMA"]
