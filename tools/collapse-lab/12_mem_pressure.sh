#!/bin/bash
# usage: 12_mem_pressure.sh <slice|bare|unbounded> <MiB> <hold_s>
# slice:     inside workload.slice (512M cap on the lab layout) -> in-domain PSI
# bare:      no container limit beyond --memory guard, host-level squeeze
# unbounded: NO container memory limit at all -> global-OOM territory
set -e
MODE=${1:-bare}; MIB=${2:-2300}; HOLD=${3:-600}
NAME=guardian-accept-memory
docker rm -f $NAME >/dev/null 2>&1 || true
case "$MODE" in
  slice)
    docker run -d --name $NAME --label guardian.acceptance=true \
      --cgroup-parent workload.slice \
      --memory "${MIB}m" --memory-swap "${MIB}m" --pids-limit 64 \
      -v /root/hog_hold.py:/hog.py:ro \
      python:3.12-alpine python3 /hog.py "$MIB" "$HOLD" >/dev/null ;;
  bare)
    docker run -d --name $NAME --label guardian.acceptance=true \
      --memory 3500m --pids-limit 64 \
      -v /root/hog_hold.py:/hog.py:ro \
      python:3.12-alpine python3 /hog.py "$MIB" "$HOLD" >/dev/null ;;
  unbounded)
    docker run -d --name $NAME --label guardian.acceptance=true \
      --pids-limit 64 \
      -v /root/hog_growth.py:/hog.py:ro \
      python:3.12-alpine python3 /hog.py "${MIB:-256}" "${HOLD:-3}" >/dev/null ;;
  *) echo "mode must be slice|bare|unbounded"; exit 2 ;;
esac
echo "started $NAME ($MODE, arg=${MIB}MiB) at $(date +%T)"; sleep 25
free -h | sed -n 2,3p
cat /proc/pressure/memory 2>/dev/null || true
docker inspect $NAME --format 'oom_killed={{.State.OOMKilled}} status={{.State.Status}}' 2>/dev/null || echo "container gone (likely OOM-killed)"
dmesg --ctime | grep -i "Killed process" | tail -3 || echo "(no OOM kills yet)"
