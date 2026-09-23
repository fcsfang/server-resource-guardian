"""Unprivileged client for the independent read-only container Collector."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any, Mapping


COLLECTOR_REQUEST_SCHEMA = "guardian.container_collector.request.v1"
COLLECTOR_RESPONSE_SCHEMA = "guardian.container_collector.response.v1"
MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 1024 * 1024


class CollectorUnavailable(RuntimeError):
    """Raised when the read-only Collector cannot provide a snapshot."""


def _read_line(channel: socket.socket) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = channel.recv(min(4096, MAX_RESPONSE_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES or b"\n" in chunk:
            break
    raw = b"".join(chunks)
    if len(raw) > MAX_RESPONSE_BYTES or b"\n" not in raw:
        raise CollectorUnavailable("collector_response_framing_invalid")
    return raw.split(b"\n", 1)[0]


class UnixSocketCollectorClient:
    """Request one bounded, read-only container snapshot."""

    def __init__(self, socket_path: str | Path, *, timeout_seconds: float = 5.0) -> None:
        if not 0.1 <= float(timeout_seconds) <= 30.0:
            raise ValueError("collector_timeout_out_of_bounds")
        self.socket_path = str(socket_path)
        self.timeout_seconds = float(timeout_seconds)

    def snapshot(self) -> dict[str, Any]:
        request = {
            "schema": COLLECTOR_REQUEST_SCHEMA,
            "version": 1,
            "operation": "snapshot",
        }
        encoded = (json.dumps(request, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_REQUEST_BYTES:
            raise CollectorUnavailable("collector_request_too_large")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
                channel.settimeout(self.timeout_seconds)
                channel.connect(self.socket_path)
                channel.sendall(encoded)
                raw = _read_line(channel)
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout, OSError) as exc:
            raise CollectorUnavailable("collector_socket_unavailable") from exc
        try:
            response = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CollectorUnavailable("collector_response_invalid_json") from exc
        if not isinstance(response, Mapping) or response.get("schema") != COLLECTOR_RESPONSE_SCHEMA:
            raise CollectorUnavailable("collector_response_schema_invalid")
        if response.get("status") not in {"ok", "degraded"}:
            raise CollectorUnavailable(str(response.get("error") or "collector_unavailable"))
        data = response.get("data")
        if not isinstance(data, Mapping):
            raise CollectorUnavailable("collector_data_missing")
        result = dict(data)
        collector_meta = data.get("collector") if isinstance(data.get("collector"), Mapping) else {}
        collector_meta = dict(collector_meta)
        collector_meta.update({
            "status": response.get("status"),
            "errors": list(response.get("errors") or []),
            "read_only": response.get("read_only") is True,
            "mutation_commands": list(response.get("mutation_commands") or []),
        })
        result["collector"] = collector_meta
        return result


__all__ = [
    "COLLECTOR_REQUEST_SCHEMA",
    "COLLECTOR_RESPONSE_SCHEMA",
    "CollectorUnavailable",
    "UnixSocketCollectorClient",
]
