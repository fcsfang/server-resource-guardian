#!/usr/bin/env python3
"""Read-only admission check for an approved x86_64 non-production host."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Any, Mapping


MIN_MEMORY_BYTES = 2 * 1024**3
MIN_DISK_BYTES = 2 * 1024**3


def _os_release(path: Path = Path("/etc/os-release")) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return None


def _command_ok(command: list[str], timeout: float = 5.0) -> bool:
    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def evaluate(facts: Mapping[str, Any]) -> dict[str, Any]:
    checks = {
        "linux": facts.get("system") == "Linux",
        "x86_64": str(facts.get("machine", "")).lower() in {"x86_64", "amd64"},
        "ubuntu_22_04": facts.get("os_id") == "ubuntu" and str(facts.get("os_version", "")).startswith("22.04"),
        "systemd_pid1": facts.get("pid1") == "systemd",
        "cgroup_v2": facts.get("cgroup_v2") is True,
        "systemctl_available": facts.get("systemctl_available") is True,
        "docker_readable": facts.get("docker_readable") is True,
        "cpu_minimum": isinstance(facts.get("cpu_count"), int) and facts["cpu_count"] >= 2,
        "memory_minimum": isinstance(facts.get("memory_bytes"), int) and facts["memory_bytes"] >= MIN_MEMORY_BYTES,
        "disk_minimum": isinstance(facts.get("disk_free_bytes"), int) and facts["disk_free_bytes"] >= MIN_DISK_BYTES,
        "broker_marker_absent": facts.get("broker_marker_present") is False,
        "reserve_broker_marker_absent": facts.get("reserve_broker_marker_present") is False,
    }
    blockers = [name for name, passed in checks.items() if not passed]
    return {
        "schema": "guardian.nonproduction-preflight.v1",
        "ready_for_observe_install": not blockers,
        "checks": checks,
        "blockers": blockers,
        "facts": dict(facts),
        "safety": {
            "read_only": True,
            "credentials_read": False,
            "services_changed": False,
            "containers_changed": False,
            "production_authorized": False,
            "automatic_actions_authorized": False,
        },
    }


def collect() -> dict[str, Any]:
    release = _os_release()
    memory_bytes: int | None = None
    meminfo = _read(Path("/proc/meminfo"))
    if meminfo:
        for line in meminfo.splitlines():
            if line.startswith("MemTotal:"):
                try:
                    memory_bytes = int(line.split()[1]) * 1024
                except (IndexError, ValueError):
                    memory_bytes = None
                break
    try:
        disk_free = shutil.disk_usage("/").free
    except OSError:
        disk_free = None
    return {
        "system": platform.system(),
        "machine": platform.machine(),
        "os_id": release.get("ID"),
        "os_version": release.get("VERSION_ID"),
        "kernel": platform.release(),
        "pid1": _read(Path("/proc/1/comm")),
        "cgroup_v2": Path("/sys/fs/cgroup/cgroup.controllers").is_file(),
        "systemctl_available": shutil.which("systemctl") is not None,
        "docker_readable": shutil.which("docker") is not None and _command_ok(["docker", "info", "--format", "{{json .ServerVersion}}"]),
        "cpu_count": os.cpu_count(),
        "memory_bytes": memory_bytes,
        "disk_free_bytes": disk_free,
        "broker_marker_present": Path("/etc/guardian/broker.enabled").exists(),
        "reserve_broker_marker_present": Path("/etc/guardian/reserve-broker.enabled").exists(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Guardian x86_64 non-production preflight")
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    args = parser.parse_args()
    report = evaluate(collect())
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if report["ready_for_observe_install"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
