#!/usr/bin/env python3
"""Small independent workers for the disposable multi-process pressure lab."""

from __future__ import annotations

import argparse
import os
import time


def memory_worker(mib: int) -> None:
    block = bytearray(mib * 1024 * 1024)
    for offset in range(0, len(block), 4096):
        block[offset] = 1
    while True:
        time.sleep(1)


def cpu_worker() -> None:
    value = 1
    while True:
        value = (value * 1103515245 + 12345) & 0x7FFFFFFF
        if value == -1:
            print(value, flush=True)


def io_worker(path: str, mib: int) -> None:
    payload = b"g" * (1024 * 1024)
    with open(path, "wb", buffering=0) as stream:
        for _ in range(mib):
            stream.write(payload)
            os.fsync(stream.fileno())
        while True:
            stream.seek(0)
            stream.write(payload)
            os.fsync(stream.fileno())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("kind", choices=("memory", "cpu", "io"))
    parser.add_argument("--mib", type=int, default=1)
    parser.add_argument("--path", default="/var/tmp/guardian-storm-io.bin")
    args = parser.parse_args()
    if args.mib < 1 or args.mib > 512:
        parser.error("--mib must be between 1 and 512")
    if args.kind == "memory":
        memory_worker(args.mib)
    elif args.kind == "cpu":
        cpu_worker()
    else:
        io_worker(args.path, args.mib)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
