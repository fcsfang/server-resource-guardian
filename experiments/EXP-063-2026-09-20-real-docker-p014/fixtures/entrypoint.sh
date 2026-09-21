#!/bin/sh
set -eu

scenario="${1:?scenario_required}"
seconds="${2:-16}"
capacity_mb="${GUARDIAN_P014_CAPACITY_MB:-64}"
memory_mb="${GUARDIAN_P014_MEMORY_MB:-256}"
memory_start_delay_seconds="${GUARDIAN_P014_MEMORY_START_DELAY_SECONDS:-0}"
cpu_workers="${GUARDIAN_P014_CPU_WORKERS:-1}"

case "$memory_mb" in
  ''|*[!0-9]*) echo "memory_mb_must_be_integer" >&2; exit 2 ;;
esac
if [ "$memory_mb" -lt 32 ] || [ "$memory_mb" -gt 1536 ]; then
  echo "memory_mb_out_of_bounds" >&2
  exit 2
fi
case "$memory_start_delay_seconds" in
  ''|*[!0-9]*) echo "memory_start_delay_seconds_must_be_integer" >&2; exit 2 ;;
esac
if [ "$memory_start_delay_seconds" -gt 10 ]; then
  echo "memory_start_delay_seconds_out_of_bounds" >&2
  exit 2
fi
case "$cpu_workers" in
  ''|*[!0-9]*) echo "cpu_workers_must_be_integer" >&2; exit 2 ;;
esac
if [ "$cpu_workers" -lt 1 ] || [ "$cpu_workers" -gt 8 ]; then
  echo "cpu_workers_out_of_bounds" >&2
  exit 2
fi

case "$scenario" in
  cpu|memory|capacity|io|mixed|idle) ;;
  *) echo "unknown_scenario" >&2; exit 2 ;;
esac

mkdir -p /www /work /dev/shm/guardian-p014
printf 'healthy\n' > /www/health

# The HTTP endpoint is a small business-health stand-in. It is deliberately
# independent from the resource worker so the observer can distinguish a
# healthy workload from a stopped workload.
busybox httpd -f -p 8080 -h /www &

deadline=$(( $(date +%s) + seconds ))

case "$scenario" in
  cpu)
    # Use bounded, in-container CPU workers. The harness never sends a signal
    # to or stops the Docker container; timeout is part of each fixture process
    # tree and lets the workload exit on its own deadline.
    worker=1
    pids=""
    while [ "$worker" -le "$cpu_workers" ]; do
      busybox timeout "$seconds" busybox yes >/dev/null &
      pids="$pids $!"
      worker=$((worker + 1))
    done
    for pid in $pids; do
      wait "$pid" || true
    done
    ;;
  memory)
    if [ "$memory_start_delay_seconds" -gt 0 ]; then
      sleep "$memory_start_delay_seconds"
    fi
    allocated_mb=0
    chunk_index=0
    while [ "$allocated_mb" -lt "$memory_mb" ]; do
      remaining_mb=$((memory_mb - allocated_mb))
      chunk_mb=64
      if [ "$remaining_mb" -lt "$chunk_mb" ]; then
        chunk_mb="$remaining_mb"
      fi
      dd if=/dev/zero of="/dev/shm/guardian-p014/memory-${chunk_index}.bin" \
        bs=1M count="$chunk_mb" conv=fsync status=none
      allocated_mb=$((allocated_mb + chunk_mb))
      chunk_index=$((chunk_index + 1))
      sleep 0.5
    done
    while [ "$(date +%s)" -lt "$deadline" ]; do sleep 1; done
    ;;
  capacity)
    dd if=/dev/zero of=/work/capacity.bin bs=1M count="$capacity_mb" conv=fsync status=none
    while [ "$(date +%s)" -lt "$deadline" ]; do sleep 1; done
    ;;
  io)
    index=0
    # Keep the real block-I/O signal bounded. Without this cap a fast
    # disposable container can fill the VM root filesystem before Observer
    # recovery samples are collected.
    while [ "$(date +%s)" -lt "$deadline" ] && [ "$index" -lt 8 ]; do
      dd if=/dev/zero of="/work/io-${index}.bin" bs=1M count=4 conv=fsync status=none
      index=$((index + 1))
    done
    while [ "$(date +%s)" -lt "$deadline" ]; do sleep 1; done
    ;;
  mixed)
    dd if=/dev/zero of=/dev/shm/guardian-p014/memory.bin bs=1M count=192 conv=fsync status=none
    while [ "$(date +%s)" -lt "$deadline" ]; do :; done
    ;;
  idle)
    while [ "$(date +%s)" -lt "$deadline" ]; do sleep 1; done
    ;;
esac
