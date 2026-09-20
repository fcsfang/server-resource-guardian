#!/usr/bin/env python3
"""Bounded, read-only process resource sampler for Guardian experiments.

This helper never signals or modifies the target process. It stops when the
target disappears, the duration expires, or the bounded TSV output reaches its
byte cap. It is an experiment aid, not part of Guardian's decision path.
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any


def parse_proc_status(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        key, separator, raw = line.partition(":")
        if not separator:
            continue
        fields = raw.split()
        if not fields:
            continue
        try:
            values[key] = int(fields[0])
        except ValueError:
            continue
    return values


def parse_proc_stat(text: str) -> dict[str, int]:
    """Read utime/stime from /proc/<pid>/stat despite spaces in comm."""

    closing_paren = text.rfind(")")
    if closing_paren < 0:
        raise ValueError("proc_stat_comm_missing")
    fields = text[closing_paren + 2 :].split()
    if len(fields) <= 19:
        raise ValueError("proc_stat_fields_missing")
    return {
        "utime_ticks": int(fields[11]),
        "stime_ticks": int(fields[12]),
        "starttime_ticks": int(fields[19]),
    }


def read_system_cpu_ticks(proc_root: Path = Path("/proc")) -> int:
    for line in (proc_root / "stat").read_text(encoding="utf-8").splitlines():
        if line.startswith("cpu "):
            return sum(int(value) for value in line.split()[1:])
    raise ValueError("system_cpu_line_missing")


def read_process_sample(pid: int, proc_root: Path = Path("/proc")) -> dict[str, Any]:
    process_root = proc_root / str(pid)
    status = parse_proc_status((process_root / "status").read_text(encoding="utf-8"))
    stat = parse_proc_stat((process_root / "stat").read_text(encoding="utf-8"))
    fd_count = len(list((process_root / "fd").iterdir()))
    return {
        "rss_kib": status.get("VmRSS"),
        "threads": status.get("Threads"),
        "fds": fd_count,
        **stat,
        "system_ticks": read_system_cpu_ticks(proc_root),
    }


def _cpu_percent(previous: dict[str, Any] | None, current: dict[str, Any]) -> float | None:
    if previous is None:
        return None
    process_delta = (
        current["utime_ticks"]
        + current["stime_ticks"]
        - previous["utime_ticks"]
        - previous["stime_ticks"]
    )
    system_delta = current["system_ticks"] - previous["system_ticks"]
    if system_delta <= 0:
        return None
    return round(process_delta / system_delta * 100 * (os.cpu_count() or 1), 3)


def sample_process(
    pid: int,
    output: Path,
    *,
    interval_seconds: float = 10.0,
    duration_seconds: float = 300.0,
    max_bytes: int = 10 * 1024 * 1024,
    proc_root: Path = Path("/proc"),
    clock: Any = time.monotonic,
    sleep: Any = time.sleep,
) -> int:
    if pid <= 0 or interval_seconds <= 0 or duration_seconds <= 0 or max_bytes <= 0:
        raise ValueError("sampling_arguments_invalid")
    output.parent.mkdir(parents=True, exist_ok=True)
    header = "epoch_s\trss_kib\tcpu_percent\tfds\tthreads\n"
    with output.open("w", encoding="utf-8") as stream:
        stream.write(header)
        stream.flush()
        started = clock()
        previous: dict[str, Any] | None = None
        while clock() - started < duration_seconds:
            try:
                current = read_process_sample(pid, proc_root)
            except (FileNotFoundError, NotADirectoryError, PermissionError, OSError, ValueError):
                break
            cpu_percent = _cpu_percent(previous, current)
            row = (
                f"{time.time():.3f}\t{current.get('rss_kib', '')}\t"
                f"{'' if cpu_percent is None else cpu_percent}\t{current.get('fds', '')}\t"
                f"{current.get('threads', '')}\n"
            )
            if stream.tell() + len(row.encode("utf-8")) > max_bytes:
                break
            stream.write(row)
            stream.flush()
            previous = current
            remaining = duration_seconds - (clock() - started)
            if remaining <= 0:
                break
            sleep(min(interval_seconds, remaining))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Bounded read-only process resource sampler")
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval", type=float, default=10.0)
    parser.add_argument("--duration", type=float, default=300.0)
    parser.add_argument("--max-bytes", type=int, default=10 * 1024 * 1024)
    args = parser.parse_args()
    sample_process(
        args.pid,
        args.output,
        interval_seconds=args.interval,
        duration_seconds=args.duration,
        max_bytes=args.max_bytes,
    )


if __name__ == "__main__":
    main()
