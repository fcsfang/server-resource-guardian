#!/usr/bin/env python3
"""Read-only, fixed-scope status view for the human maintenance account."""

from __future__ import annotations

import subprocess

RESOURCE_DOMAINS = ("rescue.slice", "workload.slice", "guardian-observer.slice")


def run(*args: str) -> str:
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    output = (result.stdout or result.stderr).strip()
    return output if output else f"exit={result.returncode}"


def main() -> int:
    print("Guardian maintenance status (read-only)")
    print("\n[Guardian]")
    print(run("/usr/local/bin/guardian-status"))
    print("\n[Services]")
    for unit in ("guardian-runtime.service", "guardian-collector.service", "ssh.service", "systemd-logind.service", "docker.service", "containerd.service"):
        print(f"{unit}: {run('/usr/bin/systemctl', 'is-active', unit)} / {run('/usr/bin/systemctl', 'is-enabled', unit)}")
    print("\n[Resource domains]")
    for unit in RESOURCE_DOMAINS:
        print(f"{unit}: {run('/usr/bin/systemctl', 'show', unit, '-p', 'Slice', '-p', 'AllowedCPUs', '-p', 'EffectiveCPUs', '-p', 'MemoryMin', '-p', 'MemoryLow', '-p', 'MemoryHigh', '-p', 'MemoryMax', '-p', 'MemorySwapMax', '-p', 'TasksMax')}")
    print("\n[Maintenance shell]")
    print("The SSH session must additionally inspect its own user-UID slice.")
    print(run("/usr/bin/systemctl", "show", "user.slice", "-p", "AllowedCPUs", "-p", "MemoryMin", "-p", "MemoryLow", "-p", "TasksMax"))
    print("\n[Docker read-only]")
    print(run("/usr/bin/docker", "ps", "--format", "{{.Names}}\\t{{.Status}}"))
    print("\n[Network and disk]")
    print(run("/usr/bin/getent", "hosts", "localhost"))
    print(run("/usr/bin/df", "-h", "/"))
    print("\n[Broker boundary]")
    print("marker:", run("/usr/bin/test", "-e", "/etc/guardian/broker.enabled"))
    print("socket:", run("/usr/bin/test", "-S", "/run/guardian-broker/broker.sock"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
