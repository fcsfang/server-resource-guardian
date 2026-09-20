"""Integrity checks for the bounded Guardian JSONL audit stream."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def verify_audit_file(path: Path, *, max_bytes: int | None = None) -> dict[str, Any]:
    """Validate JSONL records, event identity uniqueness, and capacity.

    The verifier is deliberately read-only. It never rewrites, truncates, or
    deletes an audit file, and it returns a structured summary suitable for an
    experiment record or a later systemd health check.
    """

    result: dict[str, Any] = {
        "status": "invalid",
        "path": str(path),
        "lines": 0,
        "valid_json_records": 0,
        "invalid_json_records": 0,
        "missing_event_ids": 0,
        "duplicate_event_ids": 0,
        "bytes": 0,
        "max_bytes": max_bytes,
        "capacity_ok": True,
    }
    try:
        file_size = path.stat().st_size
    except (FileNotFoundError, OSError) as exc:
        result["error"] = f"audit_file_unavailable:{type(exc).__name__}"
        return result

    result["bytes"] = file_size
    result["capacity_ok"] = max_bytes is None or file_size <= max_bytes
    seen_ids: set[str] = set()
    try:
        with path.open("rb") as stream:
            for line_number, raw_line in enumerate(stream, start=1):
                result["lines"] += 1
                try:
                    record = json.loads(raw_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    result["invalid_json_records"] += 1
                    continue
                if not isinstance(record, dict):
                    result["invalid_json_records"] += 1
                    continue
                result["valid_json_records"] += 1
                event_id = record.get("event_id")
                if not isinstance(event_id, str) or not event_id:
                    result["missing_event_ids"] += 1
                elif event_id in seen_ids:
                    result["duplicate_event_ids"] += 1
                else:
                    seen_ids.add(event_id)
    except (OSError, ValueError) as exc:
        result["error"] = f"audit_file_read_failed:{type(exc).__name__}"
        return result

    if (
        result["capacity_ok"]
        and result["invalid_json_records"] == 0
        and result["missing_event_ids"] == 0
        and result["duplicate_event_ids"] == 0
    ):
        result["status"] = "valid"
    return result


__all__ = ["verify_audit_file"]
