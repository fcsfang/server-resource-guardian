#!/usr/bin/env python3
"""Start one bounded, fixed-scope pressure scenario in workload.slice.

This helper is intended for a local disposable VM only.  It accepts no
arbitrary command or path, and every scenario has a short automatic timeout.
"""

from __future__ import annotations

import argparse
import shlex
import subprocess
import time


MAX_SECONDS = 60


def start(unit: str, command: list[str]) -> None:
    subprocess.run(
        [
            "/usr/bin/systemd-run",
            "--quiet",
            "--no-block",
            "--collect",
            f"--unit={unit}",
            "--slice=workload.slice",
            *command,
        ],
        check=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Start one bounded local pressure scenario")
    parser.add_argument("scenario", choices=("cpu", "memory", "disk", "mixed"))
    parser.add_argument("--seconds", type=int, default=30)
    args = parser.parse_args()
    if not 10 <= args.seconds <= MAX_SECONDS:
        raise SystemExit(f"--seconds must be between 10 and {MAX_SECONDS}")
    unit = f"guardian-pressure-{args.scenario}-{int(time.time())}"
    if args.scenario == "cpu":
        command = ["/usr/bin/stress-ng", "--cpu", "8", "--timeout", f"{args.seconds}s"]
    elif args.scenario == "memory":
        command = ["/usr/bin/stress-ng", "--vm", "1", "--vm-bytes", "400M", "--vm-keep", "--timeout", f"{args.seconds}s"]
    elif args.scenario == "mixed":
        command = ["/usr/bin/stress-ng", "--cpu", "8", "--vm", "1", "--vm-bytes", "400M", "--vm-keep", "--timeout", f"{args.seconds}s"]
    else:
        pressure = (
            "set -eu; "
            "path=/var/tmp/guardian-maintenance-disk-pressure.bin; "
            "free=$(df -B1 --output=avail / | tail -n 1); "
            "min_remaining=1073741824; "
            "bytes=$((free * 90 / 100)); "
            "if [ $((free - bytes)) -lt $min_remaining ]; then bytes=$((free - min_remaining)); fi; "
            "if [ $bytes -lt 1073741824 ]; then echo 'not enough disposable free space for bounded disk scenario' >&2; exit 2; fi; "
            "trap 'rm -f \"$path\"' EXIT INT TERM; "
            "fallocate -l \"$bytes\" \"$path\"; "
            f"sleep {args.seconds}"
        )
        command = ["/bin/sh", "-c", pressure]
    print(f"started {unit} in workload.slice: {shlex.join(command)}")
    start(unit, command)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
