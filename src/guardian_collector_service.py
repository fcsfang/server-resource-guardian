"""Independent read-only container Collector service.

The service exposes one fixed operation over a local Unix socket. It builds
only the allowlisted ``docker stats`` and ``docker inspect`` read commands;
clients cannot submit Docker command text and the response contains facts
only, never an action adapter or mutation capability.
"""

from __future__ import annotations

import argparse
import grp
import json
import os
import signal
import socket
import stat
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from .guardian_attribution import collect_object_registry
from .guardian_collector_client import (
    COLLECTOR_REQUEST_SCHEMA,
    COLLECTOR_RESPONSE_SCHEMA,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
)
from .guardian_observer import collect_docker_stats
from .guardian_pressure_gate import SUSPENDED, PressureGate


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _read_line(channel: socket.socket) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = channel.recv(min(4096, MAX_REQUEST_BYTES + 1 - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if total > MAX_REQUEST_BYTES or b"\n" in chunk:
            break
    raw = b"".join(chunks)
    if len(raw) > MAX_REQUEST_BYTES or b"\n" not in raw:
        raise ValueError("collector_request_framing_invalid")
    return raw.split(b"\n", 1)[0]


def _response(
    status: str,
    *,
    data: Mapping[str, Any] | None = None,
    errors: list[str] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": COLLECTOR_RESPONSE_SCHEMA,
        "version": 1,
        "status": status,
        "data": dict(data or {}),
        "errors": list(errors or []),
        "error": error,
        "read_only": True,
        "mutation_commands": [],
    }


class ReadOnlyCollector:
    """Collect and return a single container snapshot."""

    def __init__(
        self,
        *,
        runner: CommandRunner = subprocess.run,
        proc_root: Path = Path("/proc"),
        cgroup_root: Path = Path("/sys/fs/cgroup"),
        identity_cache_path: Path | None = None,
        pressure_gate: PressureGate | None = None,
    ) -> None:
        self.runner = runner
        self.proc_root = proc_root
        self.cgroup_root = cgroup_root
        self.identity_cache_path = identity_cache_path or Path(
            os.environ.get("GUARDIAN_RESCUE_IDENTITY_CACHE", "/var/lib/guardian/shared/rescue-identities.json")
        )
        self.pressure_gate = pressure_gate or PressureGate(proc_root=proc_root)
        self._last_collection_monotonic: float | None = None

    def _write_identity_cache(self, objects: list[Mapping[str, Any]]) -> None:
        entries = []
        observed_at = time.time()
        for item in objects[:256]:
            stable_id = item.get("id")
            cgroup_path = item.get("cgroup_path")
            if not isinstance(stable_id, str) or not stable_id or not isinstance(cgroup_path, str) or not cgroup_path.startswith("/"):
                continue
            name = item.get("name")
            entries.append(
                {
                    "stable_id": stable_id,
                    "name": name if isinstance(name, str) and name else None,
                    "cgroup_path": cgroup_path,
                    "cgroup_inode": item.get("cgroup_inode"),
                    "observed_at": observed_at,
                }
            )
        payload = {
            "schema": "guardian.rescue-identities.v1",
            "version": 1,
            "observed_at": observed_at,
            "entries": entries,
        }
        path = self.identity_cache_path
        temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            os.chmod(temporary, 0o640)
            os.replace(temporary, path)
        except (OSError, ValueError):
            try:
                temporary.unlink()
            except OSError:
                pass

    def snapshot(self) -> dict[str, Any]:
        decision = self.pressure_gate.evaluate()
        now = time.monotonic()
        if decision.state == SUSPENDED or not self.pressure_gate.should_collect(
            now=now,
            last_collection=self._last_collection_monotonic,
            interval_seconds=1.0,
        ):
            reason = "pressure_gate_suspended" if decision.state == SUSPENDED else "pressure_gate_degraded"
            return _response(
                "degraded",
                data={
                    "docker": {"available": False, "error": reason, "containers": []},
                    "object_registry": {"status": "unavailable", "objects": [], "errors": [reason]},
                    "collector": {"pressure_gate": decision.as_dict(), "stale": True},
                },
                errors=[reason],
                error=reason,
            )
        docker = collect_docker_stats(self.runner)
        self._last_collection_monotonic = now
        if docker.get("available") is False:
            return _response(
                "unavailable",
                errors=["docker_observation_unavailable"],
                error=str(docker.get("error") or "docker_unavailable"),
            )
        registry = collect_object_registry(
            docker,
            proc_root=self.proc_root,
            cgroup_root=self.cgroup_root,
            runner=self.runner,
        )
        objects = registry.get("objects")
        if isinstance(objects, list):
            self._write_identity_cache([item for item in objects if isinstance(item, Mapping)])
        errors = list(registry.get("errors") or [])
        status = "ok" if registry.get("status") == "ok" else "degraded"
        return _response(
            status,
            data={
                "docker": docker,
                "object_registry": registry,
                "collector": {"pressure_gate": decision.as_dict(), "stale": False},
            },
            errors=errors,
        )


class CollectorServer:
    """Bounded Unix-socket server for the fixed Collector protocol."""

    def __init__(self, socket_path: str | Path, collector: ReadOnlyCollector | None = None) -> None:
        self.socket_path = Path(socket_path)
        self.collector = collector or ReadOnlyCollector()
        self._server: socket.socket | None = None
        self._stopping = False

    def _bind(self) -> socket.socket:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            if not stat.S_ISSOCK(self.socket_path.stat().st_mode):
                raise RuntimeError("collector_socket_path_not_socket")
            self.socket_path.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_path))
        try:
            shared_gid = grp.getgrnam("guardian-shared").gr_gid
            os.chown(self.socket_path, -1, shared_gid)
        except (KeyError, OSError):
            pass
        os.chmod(self.socket_path, 0o660)
        server.listen(8)
        server.settimeout(1.0)
        self._server = server
        return server

    @staticmethod
    def _request(raw: bytes) -> dict[str, Any]:
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("collector_request_invalid_json") from exc
        if not isinstance(value, Mapping) or set(value) != {"schema", "version", "operation"}:
            raise ValueError("collector_request_keys_invalid")
        if value.get("schema") != COLLECTOR_REQUEST_SCHEMA or value.get("version") != 1:
            raise ValueError("collector_request_schema_invalid")
        if value.get("operation") != "snapshot":
            raise ValueError("collector_operation_not_supported")
        return dict(value)

    @staticmethod
    def _send(channel: socket.socket, value: Mapping[str, Any]) -> None:
        encoded = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_RESPONSE_BYTES:
            encoded = json.dumps(_response("unavailable", error="collector_response_too_large"), separators=(",", ":")).encode("utf-8") + b"\n"
        try:
            channel.sendall(encoded)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # A bounded client may time out while Docker is still unwinding.
            # The disconnected client must not take down the Collector loop.
            return

    def serve_forever(self) -> None:
        server = self._bind()
        while not self._stopping:
            try:
                channel, _ = server.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stopping:
                    break
                raise
            with channel:
                try:
                    self._request(_read_line(channel))
                    self._send(channel, self.collector.snapshot())
                except (OSError, ValueError, RuntimeError) as exc:
                    self._send(channel, _response("unavailable", error=str(exc)))
        self.close()

    def close(self) -> None:
        self._stopping = True
        if self._server is not None:
            self._server.close()
            self._server = None
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guardian read-only container Collector")
    parser.add_argument("--socket", type=Path, default=Path("/run/guardian-collector/collector.sock"))
    args = parser.parse_args(argv)
    server = CollectorServer(args.socket)
    signal.signal(signal.SIGTERM, lambda *_args: server.close())
    signal.signal(signal.SIGINT, lambda *_args: server.close())
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["CollectorServer", "ReadOnlyCollector", "main"]
