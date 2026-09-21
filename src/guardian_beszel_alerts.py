"""Beszel 0.19.0 native alert contract for milestone one.

This module keeps the alert boundary deliberately small:

* CPU, Memory and Disk are represented using Beszel's native single-threshold
  ``alerts`` records (``name``, ``value``, ``min``).
* configuration is rendered into API-ready request bodies, but this module has
  no POST/DELETE client and never reads credentials or invokes Guardian action
  code;
* active/history records are reduced to a redacted local trigger/recovery
  record with the minimum fields needed for evidence;
* Beszel's lack of a native warning/critical pair and disk-I/O alert is an
  explicit capability gap, not an invitation to fork Beszel.

The existing :mod:`src.beszel_adapter` remains the read-only HTTP boundary.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .beszel_adapter import BeszelHttpClient


SCHEMA = "guardian.beszel.alerts.v1"
BESZEL_VERSION = "0.19.0"
MAX_SYSTEMS = 256
MAX_RULES = 3
MAX_RECORD_BYTES = 64 * 1024


class BeszelAlertConfigError(ValueError):
    """Raised when an alert configuration is unsafe or incomplete."""


@dataclass(frozen=True)
class NativeAlertCapability:
    resource: str
    beszel_name: str
    metric: str
    unit: str
    direction: str
    supports_duration: bool
    scope: str


NATIVE_CAPABILITIES: dict[str, NativeAlertCapability] = {
    "cpu": NativeAlertCapability("cpu", "CPU", "cpu_usage_percent", "%", "above", True, "host"),
    "memory": NativeAlertCapability("memory", "Memory", "memory_usage_percent", "%", "above", True, "host"),
    "disk": NativeAlertCapability("disk", "Disk", "disk_usage_percent", "%", "above", True, "any_filesystem"),
}

CAPABILITY_GAPS: tuple[dict[str, str], ...] = (
    {
        "id": "no_dual_severity",
        "status": "unsupported",
        "description": "Beszel 0.19.0 stores one threshold per resource rule; it has no native warning/critical severity pair.",
        "boundary": "Guardian remains the source of multi-level risk state.",
    },
    {
        "id": "no_disk_io_alert",
        "status": "unsupported",
        "description": "Beszel 0.19.0 exposes disk I/O charts but no native disk I/O threshold alert rule.",
        "boundary": "Disk I/O remains Guardian-local observation only.",
    },
)


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BeszelAlertConfigError(f"{field}:number_required")
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        raise BeszelAlertConfigError(f"{field}:finite_required")
    return number


def _non_empty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise BeszelAlertConfigError(f"{field}:non_empty_string_required")
    return value.strip()


def _parse_time(value: Any, field: str) -> dt.datetime:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return dt.datetime.fromtimestamp(value, tz=dt.timezone.utc)
    if not isinstance(value, str) or not value.strip():
        raise BeszelAlertConfigError(f"{field}:timestamp_required")
    raw = value.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise BeszelAlertConfigError(f"{field}:timestamp_invalid") from exc
    if parsed.tzinfo is None:
        raise BeszelAlertConfigError(f"{field}:timezone_required")
    return parsed.astimezone(dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class BeszelAlertRule:
    """One native Beszel system alert rule."""

    resource: str
    threshold_percent: float
    duration_minutes: int
    enabled: bool = True

    def __post_init__(self) -> None:
        if self.resource not in NATIVE_CAPABILITIES:
            raise BeszelAlertConfigError("resource:unsupported_native_resource")
        threshold = _finite_number(self.threshold_percent, "threshold_percent")
        if not 0 < threshold <= 100:
            raise BeszelAlertConfigError("threshold_percent:must_be_between_0_and_100")
        if type(self.duration_minutes) is not int or not 1 <= self.duration_minutes <= 255:
            raise BeszelAlertConfigError("duration_minutes:must_be_integer_1_to_255")
        if type(self.enabled) is not bool:
            raise BeszelAlertConfigError("enabled:boolean_required")

    @property
    def capability(self) -> NativeAlertCapability:
        return NATIVE_CAPABILITIES[self.resource]

    def to_beszel_body(self, system_ids: Iterable[str], *, overwrite: bool = True) -> dict[str, Any]:
        ids = _system_ids(system_ids)
        if not self.enabled:
            raise BeszelAlertConfigError("disabled_rule_cannot_be_upserted")
        return {
            "name": self.capability.beszel_name,
            "value": self.threshold_percent,
            "min": self.duration_minutes,
            "systems": ids,
            "overwrite": overwrite,
        }


def _system_ids(values: Iterable[str]) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise BeszelAlertConfigError("system_ids:string_list_required")
    ids = [_non_empty_string(value, "system_id") for value in values]
    if not ids:
        raise BeszelAlertConfigError("system_ids:at_least_one_required")
    if len(ids) > MAX_SYSTEMS or len(set(ids)) != len(ids):
        raise BeszelAlertConfigError("system_ids:duplicate_or_limit_exceeded")
    return ids


def parse_rule(value: Mapping[str, Any], *, path: str = "rule") -> BeszelAlertRule:
    if not isinstance(value, Mapping):
        raise BeszelAlertConfigError(f"{path}:object_required")
    allowed = {"resource", "threshold_percent", "duration_minutes", "enabled"}
    unknown = set(value) - allowed
    if unknown:
        raise BeszelAlertConfigError(f"{path}:unknown_fields:{','.join(sorted(unknown))}")
    missing = {"resource", "threshold_percent", "duration_minutes"} - set(value)
    if missing:
        raise BeszelAlertConfigError(f"{path}:missing_fields:{','.join(sorted(missing))}")
    return BeszelAlertRule(
        resource=_non_empty_string(value["resource"], f"{path}.resource"),
        threshold_percent=value["threshold_percent"],
        duration_minutes=value["duration_minutes"],
        enabled=value.get("enabled", True),
    )


def build_native_alert_plan(
    system_ids: Iterable[str],
    rules: Iterable[BeszelAlertRule],
    *,
    overwrite: bool = True,
) -> dict[str, Any]:
    """Build a no-side-effect plan for Beszel's native alert API."""

    ids = _system_ids(system_ids)
    parsed_rules = list(rules)
    if not 1 <= len(parsed_rules) <= MAX_RULES:
        raise BeszelAlertConfigError("rules:must_contain_1_to_3_rules")
    enabled = [rule for rule in parsed_rules if rule.enabled]
    resources = [rule.resource for rule in enabled]
    if len(set(resources)) != len(resources):
        raise BeszelAlertConfigError("rules:one_rule_per_resource")
    requests = [
        {
            "method": "POST",
            "path": "/api/beszel/user-alerts",
            "body": rule.to_beszel_body(ids, overwrite=overwrite),
        }
        for rule in enabled
    ]
    disabled = [rule.resource for rule in parsed_rules if not rule.enabled]
    plan = {
        "schema": SCHEMA,
        "version": 1,
        "beszel_version": BESZEL_VERSION,
        "systems": ids,
        "rules": [
            {
                "resource": rule.resource,
                "beszel_name": rule.capability.beszel_name,
                "metric": rule.capability.metric,
                "threshold_percent": rule.threshold_percent,
                "duration_minutes": rule.duration_minutes,
                "enabled": rule.enabled,
            }
            for rule in parsed_rules
        ],
        "capability_gaps": list(CAPABILITY_GAPS),
        "disabled_rules": disabled,
        "requests": requests,
        "safety": {
            "configuration_only": True,
            "action_adapter_invoked": False,
            "broker_called": False,
            "capability_generated": False,
            "credentials_included": False,
            "real_notification_channel": False,
        },
    }
    plan["plan_digest"] = hashlib.sha256(
        json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return plan


def load_alert_plan_config(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the standalone ``config/beszel-alerts.example.json`` shape."""

    if not isinstance(value, Mapping):
        raise BeszelAlertConfigError("config:object_required")
    expected = {"schema", "version", "beszel_version", "system_ids", "rules", "notification"}
    unknown = set(value) - expected
    missing = expected - set(value)
    if unknown:
        raise BeszelAlertConfigError(f"config:unknown_fields:{','.join(sorted(unknown))}")
    if missing:
        raise BeszelAlertConfigError(f"config:missing_fields:{','.join(sorted(missing))}")
    if value["schema"] != SCHEMA or value["version"] != 1 or value["beszel_version"] != BESZEL_VERSION:
        raise BeszelAlertConfigError("config:unsupported_schema_or_beszel_version")
    raw_ids = value["system_ids"]
    if not isinstance(raw_ids, list):
        raise BeszelAlertConfigError("system_ids:string_list_required")
    ids = [] if not raw_ids else _system_ids(raw_ids)
    raw_rules = value["rules"]
    if not isinstance(raw_rules, list):
        raise BeszelAlertConfigError("rules:list_required")
    rules = [parse_rule(item, path=f"rules[{index}]") for index, item in enumerate(raw_rules)]
    notification = value["notification"]
    if not isinstance(notification, Mapping):
        raise BeszelAlertConfigError("notification:object_required")
    if set(notification) != {"mode", "real_channels_enabled"}:
        raise BeszelAlertConfigError("notification:unexpected_fields")
    if notification["mode"] not in {"local_ui_history", "local_fake_sink"}:
        raise BeszelAlertConfigError("notification.mode:unsupported")
    if notification["real_channels_enabled"] is not False:
        raise BeszelAlertConfigError("notification.real_channels_enabled:must_be_false")
    plan = build_native_alert_plan(ids or ["local-system-id-required"], rules)
    if not ids:
        plan["systems"] = []
        plan["requests"] = []
        plan["requires_system_selection"] = True
        plan["plan_digest"] = hashlib.sha256(
            json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    return plan


def _resource_from_name(name: Any) -> str:
    if not isinstance(name, str):
        raise BeszelAlertConfigError("alert.name_required")
    for resource, capability in NATIVE_CAPABILITIES.items():
        if name == capability.beszel_name:
            return resource
    raise BeszelAlertConfigError("alert.name_not_in_t12_scope")


def normalize_native_alert_record(
    record: Mapping[str, Any],
    *,
    received_at: dt.datetime,
    system_id: str | None = None,
    now: dt.datetime | None = None,
) -> dict[str, Any]:
    """Return one redacted trigger/recovery record from ``alerts`` or history."""

    if not isinstance(record, Mapping):
        raise BeszelAlertConfigError("alert_record:object_required")
    record_id = _non_empty_string(record.get("id"), "alert.id")
    resource = _resource_from_name(record.get("name"))
    created = _parse_time(record.get("created"), "alert.created")
    resolved_raw = record.get("resolved")
    resolved: dt.datetime | None = None
    if resolved_raw not in (None, False):
        resolved = created if resolved_raw is True else _parse_time(resolved_raw, "alert.resolved")
        if resolved < created:
            raise BeszelAlertConfigError("alert.resolved_before_created")
    observed = resolved or created
    if received_at.tzinfo is None:
        raise BeszelAlertConfigError("received_at:timezone_required")
    if now is not None and now.tzinfo is None:
        raise BeszelAlertConfigError("now:timezone_required")
    current = (now or received_at).astimezone(dt.timezone.utc)
    received = received_at.astimezone(dt.timezone.utc)
    if received < observed:
        raise BeszelAlertConfigError("alert.received_before_observed")
    state = "recovered" if resolved is not None else "triggered"
    duration = None if resolved is None else round((resolved - created).total_seconds(), 3)
    expanded = record.get("expand") if isinstance(record.get("expand"), Mapping) else {}
    system = expanded.get("system") if isinstance(expanded.get("system"), Mapping) else {}
    sid = system_id or record.get("system")
    if sid is not None:
        sid = _non_empty_string(str(sid), "system_id")
    system_name = system.get("name") or record.get("system_name")
    threshold = _finite_number(record.get("value"), "alert.value")
    if not 0 < threshold <= 100:
        raise BeszelAlertConfigError("alert.value:must_be_between_0_and_100")
    raw_min = record.get("min", 1)
    if type(raw_min) is not int or not 1 <= raw_min <= 255:
        raise BeszelAlertConfigError("alert.min:must_be_integer_1_to_255")
    result: dict[str, Any] = {
        "schema": "guardian.beszel.native-alert-record.v1",
        "record_id": record_id,
        "system_id": sid,
        "system_name": str(system_name) if system_name else None,
        "resource": resource,
        "beszel_name": NATIVE_CAPABILITIES[resource].beszel_name,
        "threshold_percent": threshold,
        "duration_minutes": raw_min,
        "state": state,
        "severity": None,
        "severity_model": "single_threshold",
        "triggered_at": _iso(created),
        "recovered_at": _iso(resolved) if resolved is not None else None,
        "observed_at": _iso(observed),
        "received_at": _iso(received),
        "duration_seconds": duration,
        "active_at_read": resolved is None,
        "stale_at_read": current > received_at.astimezone(dt.timezone.utc) + dt.timedelta(minutes=10),
        "action_authorized": False,
        "credentials_included": False,
    }
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_RECORD_BYTES:
        raise BeszelAlertConfigError("alert_record:too_large")
    return result


def fetch_native_alert_records(
    client: BeszelHttpClient,
    *,
    history: bool = True,
    page: int = 1,
    per_page: int = 200,
    system_id: str | None = None,
    received_at: dt.datetime,
    now: dt.datetime | None = None,
) -> list[dict[str, Any]]:
    """Read active/history records with GET only and normalize them locally."""

    if page <= 0 or not 1 <= per_page <= 200:
        raise BeszelAlertConfigError("pagination:out_of_bounds")
    collection = "alerts_history" if history else "alerts"
    fields = "id,name,value,min,system,triggered,created,resolved,expand.system.name"
    response = client.get_json(
        f"/api/collections/{collection}/records",
        query={"page": page, "perPage": per_page, "sort": "-created", "expand": "system", "fields": fields},
    )
    if not isinstance(response, Mapping) or not isinstance(response.get("items"), list):
        raise BeszelAlertConfigError("alert_records:items_required")
    return [
        normalize_native_alert_record(
            item,
            received_at=received_at,
            system_id=system_id,
            now=now,
        )
        for item in response["items"]
        if isinstance(item, Mapping)
    ]


class LocalAlertRecordWindow:
    """Bounded readback dedupe for repeated active/history polling."""

    def __init__(self, *, max_records: int = 256) -> None:
        if type(max_records) is not int or not 1 <= max_records <= 10_000:
            raise BeszelAlertConfigError("max_records:out_of_bounds")
        self.max_records = max_records
        self._seen: OrderedDict[tuple[str, str], None] = OrderedDict()

    def accept(self, record: Mapping[str, Any]) -> bool:
        key = (str(record.get("record_id") or ""), str(record.get("state") or ""))
        if not key[0] or not key[1]:
            raise BeszelAlertConfigError("local_record:identity_required")
        if key in self._seen:
            return False
        self._seen[key] = None
        self._seen.move_to_end(key)
        while len(self._seen) > self.max_records:
            self._seen.popitem(last=False)
        return True


def write_local_records(path: Path, records: Iterable[Mapping[str, Any]]) -> int:
    """Write redacted JSONL evidence without overwriting an existing file."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            encoded = json.dumps(dict(record), ensure_ascii=False, sort_keys=True)
            handle.write(encoded + "\n")
            count += 1
    return count


__all__ = [
    "BESZEL_VERSION",
    "CAPABILITY_GAPS",
    "BeszelAlertConfigError",
    "BeszelAlertRule",
    "LocalAlertRecordWindow",
    "NATIVE_CAPABILITIES",
    "build_native_alert_plan",
    "fetch_native_alert_records",
    "load_alert_plan_config",
    "normalize_native_alert_record",
    "parse_rule",
    "write_local_records",
]
