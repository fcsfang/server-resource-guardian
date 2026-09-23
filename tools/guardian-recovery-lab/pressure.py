#!/usr/bin/env python3
"""Bounded native pressure generator for a disposable Guardian lab VM."""

from __future__ import annotations

import argparse
import multiprocessing as mp
import signal
import time


def _memory_worker(mib: int, hold_seconds: float) -> None:
    block = bytearray(mib * 1024 * 1024)
    for offset in range(0, len(block), 4096):
        block[offset] = 1
    time.sleep(hold_seconds)


def _cpu_worker(hold_seconds: float) -> None:
    deadline = time.monotonic() + hold_seconds
    value = 1
    while time.monotonic() < deadline:
        value = (value * 1103515245 + 12345) & 0x7FFFFFFF
    if value == -1:
        print(value)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="bounded Guardian recovery lab pressure")
    parser.add_argument("--memory-mib", type=int, default=0)
    parser.add_argument("--memory-workers", type=int, default=4)
    parser.add_argument("--cpu-workers", type=int, default=0)
    parser.add_argument("--duration", type=float, default=60.0)
    args = parser.parse_args(argv)
    if args.memory_mib < 0 or not 0 <= args.memory_workers <= 64 or not 0 <= args.cpu_workers <= 256:
        parser.error("pressure worker counts and memory must be bounded")
    if not 1.0 <= args.duration <= 3600.0:
        parser.error("duration must be between 1 and 3600 seconds")
    if args.memory_mib and not args.memory_workers:
        parser.error("memory-workers must be positive when memory-mib is set")
    if not args.memory_mib and not args.cpu_workers:
        parser.error("at least one pressure worker is required")

    children: list[mp.Process] = []
    for _ in range(args.memory_workers):
        if args.memory_mib:
            children.append(mp.Process(target=_memory_worker, args=(args.memory_mib, args.duration), daemon=False))
    for _ in range(args.cpu_workers):
        children.append(mp.Process(target=_cpu_worker, args=(args.duration,), daemon=False))
    for child in children:
        child.start()

    def stop(_signum: int, _frame: object) -> None:
        for child in children:
            if child.is_alive():
                child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    for child in children:
        child.join()
    return 0 if all(child.exitcode in (0, -signal.SIGTERM, -signal.SIGINT) for child in children) else 1


if __name__ == "__main__":
    raise SystemExit(main())
