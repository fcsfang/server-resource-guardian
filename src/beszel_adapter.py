"""Read-only Beszel input adapter for the Guardian integration boundary.

The adapter only fetches JSON over HTTP GET and normalizes a whitelisted subset
of fields into the guardian.beszel.v1 event contract. It never calls Docker,
systemd, or any mutating endpoint, and it never copies unknown payload fields
into the normalized event.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping


SCHEMA = "guardian.beszel.v1"
ALLOWED_OBJECT_KINDS = {"container", "systemd_unit", "host"}
ALLOWED_RESOURCES = {
    "cpu",
    "memory",
    "swap",
    "disk",
    "io",
    "network",
    "load",
    "status",
}
ALLOWED_SEVERITIES = {"normal", "warning", "critical", "recovered"}


class AdapterError(ValueError):
    """Raised when a Beszel payload cannot be safely normalized."""


class AdapterTransportError(AdapterError):
    """Raised when a read-only Beszel HTTP request cannot complete."""


def utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_time(value: Any, field: str) -> dt.datetime:
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc)
    if not isinstance(value, str) or not value.strip():
        raise AdapterError(f"{field}_required")
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise AdapterError(f"{field}_invalid") from exc
    if parsed.tzinfo is None:
        raise AdapterError(f"{field}_timezone_required")
    return parsed.astimezone(dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _safe_source_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return None
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def normalize_beszel_event(
    payload: Mapping[str, Any],
    *,
    received_at: dt.datetime | None = None,
    now: dt.datetime | None = None,
    ttl_seconds: int = 30,
    source_kind: str = "hub_alert",
) -> dict[str, Any]:
    """Normalize one whitelisted Beszel payload into guardian.beszel.v1.

    The function is intentionally tolerant of common envelope fields such as
    data and record, but it fails closed for missing timestamps, signals, or
    invalid object kinds.
    """

    if not isinstance(payload, Mapping):
        raise AdapterError("payload_object_required")
    if ttl_seconds <= 0:
        raise AdapterError("ttl_must_be_positive")
    root = _mapping(payload.get("data")) or _mapping(payload.get("record")) or payload
    source = _mapping(root.get("source"))
    system = _mapping(root.get("system"))
    obj = _mapping(root.get("object")) or _mapping(root.get("container"))
    signal = _mapping(root.get("signal"))

    observed = _parse_time(
        _first(root, "observed_at", "timestamp", "time"),
        "observed_at",
    )
    received = received_at or utc_now()
    if received.tzinfo is None:
        raise AdapterError("received_at_timezone_required")
    received = received.astimezone(dt.timezone.utc)
    if received < observed:
        raise AdapterError("received_before_observed")
    current = (now or received).astimezone(dt.timezone.utc)
    expires = received + dt.timedelta(seconds=ttl_seconds)

    kind = _first(obj, "kind") or _first(root, "object_kind") or "host"
    if kind not in ALLOWED_OBJECT_KINDS:
        raise AdapterError("unsupported_object_kind")
    stable_id = _first(obj, "stable_id", "container_id", "id", "ID")
    if stable_id is not None and not isinstance(stable_id, str):
        stable_id = str(stable_id)
    name = _first(obj, "name", "Name")
    identity_source = _first(obj, "identity_source") or (
        "docker_id" if kind == "container" and stable_id else "source_payload"
    )
    identity_confidence = _first(obj, "identity_confidence") or (
        "high" if stable_id else "low"
    )
    if identity_confidence not in {"high", "medium", "low", "unknown"}:
        identity_confidence = "unknown"

    resource = _first(signal, "resource") or _first(root, "resource") or "status"
    if resource not in ALLOWED_RESOURCES:
        raise AdapterError("unsupported_resource")
    severity = _first(signal, "severity") or _first(root, "severity")
    if severity not in ALLOWED_SEVERITIES:
        raise AdapterError("invalid_severity")
    reason_codes = _first(signal, "reason_codes") or _first(root, "reason_codes") or []
    if not isinstance(reason_codes, list):
        reason_codes = [str(reason_codes)]
    reason_codes = [str(reason) for reason in reason_codes if reason is not None]

    source_record_id = _first(source, "record_id", "id") or _first(
        root, "source_record_id", "record_id", "id"
    )
    if source_record_id is not None:
        source_record_id = str(source_record_id)
    metric = _first(signal, "metric") or _first(root, "metric") or resource
    value = _first(signal, "value") if "value" in signal else _first(root, "value")
    unit = _first(signal, "unit") or _first(root, "unit")
    event_id = _first(root, "event_id")
    if not event_id:
        event_id = "beszel:" + hashlib.sha256(
            "|".join(
                [
                    source_record_id or "unknown-source",
                    _iso(observed),
                    str(metric),
                    str(stable_id or name or "host"),
                ]
            ).encode("utf-8")
        ).hexdigest()[:24]

    normalized: dict[str, Any] = {
        "schema": SCHEMA,
        "event_id": str(event_id),
        "source": {
            "kind": source_kind,
            "hub_id": str(_first(source, "hub_id") or _first(root, "hub_id") or "unknown"),
            "system_id": str(
                _first(source, "system_id") or _first(root, "system_id") or "unknown"
            ),
            "record_id": source_record_id,
        },
        "observed_at": _iso(observed),
        "received_at": _iso(received),
        "expires_at": _iso(expires),
        "system": {
            "name": _first(system, "name") or _first(root, "system_name") or "unknown",
            "host": _first(system, "host") or _first(root, "host") or "unknown",
            "architecture": _first(system, "architecture") or "unknown",
        },
        "object": {
            "kind": kind,
            "stable_id": stable_id,
            "name": name,
            "identity_source": identity_source,
            "identity_confidence": identity_confidence,
        },
        "signal": {
            "resource": resource,
            "metric": str(metric),
            "value": value,
            "unit": unit,
            "severity": severity,
            "reason_codes": reason_codes,
        },
        "evidence": {
            "source_url": _safe_source_url(
                _first(root, "source_url") or _first(source, "source_url")
            ),
            "snapshot_ref": _first(root, "snapshot_ref"),
        },
    }
    normalized["integrity"] = {
        "stale": current > expires,
        "raw_payload_digest": _digest(normalized),
        "redacted": True,
    }
    return normalized


def is_actionable_observation(event: Mapping[str, Any]) -> bool:
    """Return whether an event may enter Guardian re-evaluation.

    This is not action authorization. The result only says that the event is
    sufficiently fresh and identifiable for a later local policy check.
    """

    source = _mapping(event.get("source"))
    obj = _mapping(event.get("object"))
    signal = _mapping(event.get("signal"))
    integrity = _mapping(event.get("integrity"))
    return (
        event.get("schema") == SCHEMA
        and bool(event.get("event_id"))
        and source.get("kind") in {"hub_snapshot", "hub_alert", "agent_probe"}
        and not bool(integrity.get("stale"))
        and integrity.get("redacted") is True
        and obj.get("kind") in ALLOWED_OBJECT_KINDS
        and obj.get("identity_confidence") == "high"
        and signal.get("severity") in {"warning", "critical"}
    )


@dataclass
class BeszelEventWindow:
    """Bounded duplicate and ordering guard for normalized events.

    The window only decides whether an observation may be forwarded for a
    later local Guardian evaluation. It never authorizes an action. Rejected
    observations are represented by stable reason codes so callers can write
    audit records without copying the raw payload.
    """

    dedupe_seconds: int = 300

    def __post_init__(self) -> None:
        if self.dedupe_seconds <= 0:
            raise AdapterError("dedupe_seconds_must_be_positive")
        self._seen: dict[str, dt.datetime] = {}
        self._latest_by_signal: dict[str, dt.datetime] = {}

    @staticmethod
    def _signal_key(event: Mapping[str, Any]) -> str:
        source = _mapping(event.get("source"))
        obj = _mapping(event.get("object"))
        signal = _mapping(event.get("signal"))
        return "|".join(
            [
                str(source.get("system_id") or "unknown-system"),
                str(obj.get("kind") or "unknown-object"),
                str(obj.get("stable_id") or obj.get("name") or "unknown-object"),
                str(signal.get("resource") or "unknown-resource"),
                str(signal.get("metric") or "unknown-metric"),
            ]
        )

    def classify(
        self,
        event: Mapping[str, Any],
        *,
        now: dt.datetime | None = None,
    ) -> str:
        """Return accepted or a fail-closed observation reason.

        Accepted means only that the normalized input can continue to a fresh
        local policy check. It does not mean that an action is allowed.
        """

        if event.get("schema") != SCHEMA or not event.get("event_id"):
            return "invalid_event"
        current = (now or utc_now()).astimezone(dt.timezone.utc)
        event_id = str(event["event_id"])
        observed = _parse_time(event.get("observed_at"), "observed_at")
        expires = _parse_time(event.get("expires_at"), "expires_at")
        if current > expires or bool(_mapping(event.get("integrity")).get("stale")):
            return "stale_event"
        previous = self._seen.get(event_id)
        if previous is not None and current <= previous + dt.timedelta(
            seconds=self.dedupe_seconds
        ):
            return "duplicate_event"
        key = self._signal_key(event)
        latest = self._latest_by_signal.get(key)
        if latest is not None and observed < latest:
            return "out_of_order_event"
        self._seen[event_id] = current
        self._latest_by_signal[key] = observed
        return "accepted"


@dataclass(frozen=True)
class BeszelHttpClient:
    """Minimal GET-only Beszel API client.

    The token is accepted only at runtime and is never included in exceptions
    or logs. The client intentionally has no post, patch, delete, or command
    execution method.
    """

    base_url: str
    token: str | None = None
    timeout_seconds: float = 5.0

    def get_json(self, path: str, query: Mapping[str, Any] | None = None) -> Any:
        if not path.startswith("/api/"):
            raise AdapterError("api_path_required")
        parsed_base = urllib.parse.urlsplit(self.base_url)
        if parsed_base.scheme not in {"http", "https"} or not parsed_base.netloc:
            raise AdapterError("base_url_invalid")
        url = urllib.parse.urljoin(self.base_url.rstrip("/") + "/", path.lstrip("/"))
        if query:
            encoded = urllib.parse.urlencode(
                [(key, value) for key, value in query.items() if value is not None],
                doseq=True,
            )
            if encoded:
                url += "?" + encoded
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AdapterTransportError("beszel_get_failed") from exc
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdapterTransportError("beszel_json_invalid") from exc
