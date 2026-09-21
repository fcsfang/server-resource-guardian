#!/usr/bin/env python3
"""Read-only synthetic Docker identity for the disposable P0-14 fixture.

This is deliberately not a Docker replacement. It exposes only the two
read-only commands used by the Observer (``stats`` and ``inspect``), with a
fixed stable ID and the PID of the caller-owned workload cgroup. It must never
be used against a real host.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


STATE_PATH = Path(os.environ.get("P014_FAKE_DOCKER_STATE", "/tmp/guardian-p014-fake-docker-state.json"))
DEFAULT_ID = "a" * 64


def load_state() -> dict[str, object]:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def main() -> int:
    args = sys.argv[1:]
    state = load_state()
    stable_id = str(state.get("id") or DEFAULT_ID)
    name = str(state.get("name") or "/guardian-p014-target")
    pid = state.get("pid")
    if not isinstance(pid, int) or pid <= 0:
        pid = 0

    if args and args[0] == "stats":
        if not pid:
            return 0
        print(json.dumps({
            "ID": stable_id[:12],
            "Name": name,
            "CPUPerc": "95.00%",
            "MemUsage": "128MiB / 3GiB",
            "MemPerc": "4.00%",
        }))
        return 0

    if args and args[0] == "inspect":
        if not pid:
            return 0
        print(json.dumps({
            "Id": stable_id,
            "Name": name,
            "Created": "2026-09-20T00:00:00Z",
            "State": {
                "Status": "running",
                "Running": True,
                "Pid": pid,
                "Health": {"Status": "healthy"},
            },
            "Config": {
                "Image": "guardian-p014-fixture:local",
                "Labels": {"guardian.fixture": "p014"},
            },
        }))
        return 0

    sys.stderr.write("p014_fake_docker: read-only stats/inspect only\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
