#!/usr/bin/env python3
"""Bounded disposable workload fixture for EXP-058.

This fixture is intentionally dumb: it has no network access, no process
selection logic and no cleanup of paths outside the caller-provided fixture
directory.  The experiment harness owns the timeout and cleanup.
"""

from __future__ import annotations

import argparse
import multiprocessing as mp
import os
import time
from pathlib import Path


def _cpu_worker(deadline: float) -> None:
    value = 0
    while time.monotonic() < deadline:
        value = (value + 1) % 1_000_003
    if value == -1:  # keep the loop result observable to static review
        print(value)


def _touch_memory(size_mib: int) -> bytearray:
    if size_mib <= 0 or size_mib > 512:
        raise ValueError("memory_mib_out_of_bounded_range")
    payload = bytearray(size_mib * 1024 * 1024)
    page = 4096
    for offset in range(0, len(payload), page):
        payload[offset] = 1
    return payload


def _grow_memory(payload: bytearray, target_mib: int) -> bytearray:
    """Grow in bounded chunks so attribution has a measurable delta."""

    if target_mib <= 0 or target_mib > 512:
        raise ValueError("memory_mib_out_of_bounded_range")
    chunk = min(8 * 1024 * 1024, target_mib * 1024 * 1024 - len(payload))
    if chunk > 0:
        payload.extend(b"\x01" * chunk)
        for offset in range(len(payload) - chunk, len(payload), 4096):
            payload[offset] = 1
    return payload


def _write_io(directory: Path, deadline: float, size_mib: int) -> None:
    if size_mib <= 0 or size_mib > 256:
        raise ValueError("io_mib_out_of_bounded_range")
    directory.mkdir(parents=True, exist_ok=True)
    payload = b"guardian-p014-io" * 256
    limit = size_mib * 1024 * 1024
    written = 0
    file_number = 0
    while time.monotonic() < deadline and written < limit:
        path = directory / f"io-{file_number:04d}.bin"
        with path.open("wb") as stream:
            while written < limit and time.monotonic() < deadline:
                chunk = payload[: min(len(payload), limit - written)]
                stream.write(chunk)
                written += len(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        file_number += 1


def _create_inodes(directory: Path, count: int) -> None:
    if count <= 0 or count > 50_000:
        raise ValueError("inode_count_out_of_bounded_range")
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        (directory / f"inode-{index:05d}").write_bytes(b"p014")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=("cpu", "memory", "capacity", "io", "mixed"))
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--memory-mib", type=int, default=128)
    parser.add_argument("--io-mib", type=int, default=64)
    parser.add_argument("--inode-count", type=int, default=4_000)
    parser.add_argument("--path", type=Path, default=Path("/tmp/guardian-p014-fixture"))
    args = parser.parse_args()
    if not 1 <= args.workers <= 4 or not 1 <= args.seconds <= 60:
        raise SystemExit("workers/seconds_out_of_bounded_range")
    deadline = time.monotonic() + args.seconds

    held_memory: bytearray | None = None
    workers: list[mp.Process] = []
    if args.scenario in {"cpu", "mixed"}:
        workers = [mp.Process(target=_cpu_worker, args=(deadline,)) for _ in range(args.workers)]
        for worker in workers:
            worker.start()
    if args.scenario in {"memory", "mixed"}:
        # Start small and grow at bounded intervals. This keeps the fixture
        # useful for attribution without exceeding the caller's cap.
        held_memory = _touch_memory(min(8, args.memory_mib))
    if args.scenario == "capacity":
        _create_inodes(args.path, args.inode_count)
        (args.path / "payload.bin").write_bytes(b"p014-capacity" * (args.memory_mib * 1024 * 1024 // 13))
    if args.scenario == "io":
        _write_io(args.path, deadline, args.io_mib)
    next_memory_growth = time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(0.1)
        if held_memory is not None:
            held_memory[0] = (held_memory[0] + 1) % 255
            if len(held_memory) < args.memory_mib * 1024 * 1024 and time.monotonic() >= next_memory_growth:
                held_memory = _grow_memory(held_memory, args.memory_mib)
                next_memory_growth = time.monotonic() + 0.25
    for worker in workers:
        worker.join(timeout=2)
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=1)


if __name__ == "__main__":
    main()
