#!/usr/bin/env bash
set -euo pipefail

# Local-only, bounded Beszel source probe.
# It creates no container and performs no Docker/systemd mutation. The probe
# runs one CPU worker plus a 128 MiB disposable memory worker for 12 seconds,
# then exits naturally. It is intended for guardian-ubuntu only.

python3 - <<'PY'
import json
import multiprocessing as mp
import os
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

DURATION_SECONDS = 12
MEMORY_BYTES = 128 * 1024 * 1024
SAMPLE_SECONDS = 2


def cpu_worker(deadline):
    value = 1
    while time.monotonic() < deadline:
        value = (value * 1103515245 + 12345) & 0x7FFFFFFF


def memory_worker(deadline):
    buffer = bytearray(MEMORY_BYTES)
    for offset in range(0, len(buffer), 4096):
        buffer[offset] = 1
    while time.monotonic() < deadline:
        time.sleep(0.2)


def read_mem_available():
    for line in Path("/proc/meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            return int(line.split()[1]) * 1024
    return None


def read_psi(path):
    result = {}
    for line in Path(path).read_text().splitlines():
        parts = line.split()
        if not parts:
            continue
        values = {}
        for item in parts[1:]:
            key, value = item.split("=", 1)
            values[key] = float(value) if "." in value else int(value)
        result[parts[0]] = values
    return result


def beszel_health():
    try:
        with urlopen("http://127.0.0.1:8090/api/health", timeout=2) as response:
            return {"status": response.status, "body": response.read(160).decode("utf-8", "replace")}
    except Exception as exc:
        return {"error": type(exc).__name__}


def docker_stats():
    try:
        completed = subprocess.run(
            [
                "docker",
                "stats",
                "--no-stream",
                "--format",
                "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}",
                "beszel-poc",
                "beszel-agent-poc",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        return completed.stdout.strip().splitlines()
    except Exception as exc:
        return [type(exc).__name__]


def sample(label):
    return {
        "label": label,
        "monotonic_seconds": round(time.monotonic(), 3),
        "load_average_1m": os.getloadavg()[0],
        "mem_available_bytes": read_mem_available(),
        "psi_cpu": read_psi("/proc/pressure/cpu"),
        "psi_memory": read_psi("/proc/pressure/memory"),
        "psi_io": read_psi("/proc/pressure/io"),
        "beszel_health": beszel_health(),
        "beszel_docker_stats": docker_stats(),
    }


print(json.dumps({"kind": "probe_start", "duration_seconds": DURATION_SECONDS, "memory_bytes": MEMORY_BYTES}))
print(json.dumps(sample("before")))
deadline = time.monotonic() + DURATION_SECONDS
context = mp.get_context("fork")
workers = [
    context.Process(target=cpu_worker, args=(deadline,)),
    context.Process(target=memory_worker, args=(deadline,)),
]
for worker in workers:
    worker.start()
while time.monotonic() < deadline:
    print(json.dumps(sample("during")))
    time.sleep(SAMPLE_SECONDS)
for worker in workers:
    worker.join(timeout=5)
print(json.dumps(sample("after")))
print(json.dumps({"kind": "probe_end", "worker_exit_codes": [worker.exitcode for worker in workers]}))
PY
