#!/usr/bin/env python3
"""Build or read the local Beszel CPU, memory and disk alert contract.

``plan`` validates configuration and emits request bodies without sending
them. ``fixture`` creates local trigger/recovery examples. ``live-read`` is
GET-only and requires an already authorized client. This command never invokes
Guardian actions or reads credentials from disk.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.beszel_adapter import BeszelHttpClient
from src.guardian_beszel_alerts import (
    BeszelAlertConfigError,
    LocalAlertRecordWindow,
    build_native_alert_plan,
    fetch_native_alert_records,
    load_alert_plan_config,
    normalize_native_alert_record,
    parse_rule,
    write_local_records,
)


UTC = dt.timezone.utc


def _load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise BeszelAlertConfigError("config:object_required")
    return value


def _plan(config: dict[str, Any], system_id: str | None) -> dict[str, Any]:
    validated = load_alert_plan_config(config)
    ids = [system_id] if system_id else config.get("system_ids") or []
    rules = [parse_rule(item, path=f"rules[{index}]") for index, item in enumerate(config["rules"])]
    if not ids:
        return validated
    return build_native_alert_plan(ids, rules)


def _fixture_records() -> list[dict[str, Any]]:
    base = dt.datetime(2026, 9, 21, 8, 0, tzinfo=UTC)
    records: list[dict[str, Any]] = []
    for index, (name, value, duration) in enumerate(
        (("CPU", 86.0, 60), ("Memory", 87.0, 90), ("Disk", 91.0, 120))
    ):
        created = base + dt.timedelta(minutes=index)
        resolved = created + dt.timedelta(seconds=duration)
        common = {
            "id": f"alert-fixture-{name.lower()}",
            "name": name,
            "value": value,
            "min": 1,
            "created": created.isoformat().replace("+00:00", "Z"),
            "system": "local-system-fixture",
            "expand": {"system": {"name": "guardian-alert-fixture"}},
        }
        records.append(
            normalize_native_alert_record(
                common | {"resolved": None},
                received_at=created + dt.timedelta(seconds=2),
                system_id="local-system-fixture",
                now=created + dt.timedelta(seconds=2),
            )
        )
        records.append(
            normalize_native_alert_record(
                common | {"resolved": resolved.isoformat().replace("+00:00", "Z")},
                received_at=resolved + dt.timedelta(seconds=2),
                system_id="local-system-fixture",
                now=resolved + dt.timedelta(seconds=2),
            )
        )
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the local-only Beszel alert contract")
    parser.add_argument("--config", type=Path, default=REPO_ROOT / "config/beszel-alerts.example.json")
    parser.add_argument("--mode", choices=("plan", "fixture", "live-read"), default="plan")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--system-id", help="override system selection; no credential is read")
    parser.add_argument("--base-url", help="Beszel base URL for GET-only live-read")
    parser.add_argument("--history", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    config = _load(args.config)
    plan = _plan(config, args.system_id)
    output: dict[str, Any] = {
        "schema": "guardian.beszel.alerts.run.v1",
        "mode": args.mode,
        "plan": plan,
        "records": [],
        "safety": plan["safety"]
        | {"network_write": False, "production_connected": False, "credentials_read": False},
    }

    if args.mode == "fixture":
        records = _fixture_records()
        window = LocalAlertRecordWindow()
        accepted = [record for record in records if window.accept(record)]
        duplicate_accepted = window.accept(dict(accepted[0]))
        output["records"] = accepted
        output["dedupe"] = {
            "input_count": len(records) + 1,
            "accepted_count": len(accepted),
            "duplicate_suppressed": not duplicate_accepted,
        }
        output["safety"].update({"fixture_only": True, "network_read": False})
    elif args.mode == "live-read":
        if not args.base_url:
            raise BeszelAlertConfigError("base_url:required_for_live_read")
        client = BeszelHttpClient(args.base_url)
        now = dt.datetime.now(UTC)
        output["records"] = fetch_native_alert_records(
            client,
            history=args.history,
            system_id=args.system_id,
            received_at=now,
            now=now,
        )
        output["safety"].update({"network_read": True, "get_only": True})

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.mode == "fixture":
        write_local_records(args.output.with_suffix(".jsonl"), output["records"])
    print(
        json.dumps(
            {
                "mode": args.mode,
                "record_count": len(output["records"]),
                "plan_digest": plan["plan_digest"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BeszelAlertConfigError, OSError, ValueError) as exc:
        print(f"beszel_alert_error:{exc}", file=sys.stderr)
        raise SystemExit(2)
