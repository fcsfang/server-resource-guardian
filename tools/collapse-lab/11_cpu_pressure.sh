#!/bin/bash
# usage: 11_cpu_pressure.sh <workload|bare>
# workload: pinned to workload.slice (AllowedCPUs=1 on the lab layout) -> 1 core
# bare:     no cgroup-parent, 8 oversubscribed busy loops -> all cores;
#           NOTE docker.service in rescue.slice makes the daemon validate
#           --cpus against 1 core, so bare uses loop count, not --cpus
set -e
MODE=${1:-bare}
NAME=guardian-accept-cpu
docker rm -f $NAME >/dev/null 2>&1 || true
if [ "$MODE" = workload ]; then
  docker run -d --name $NAME --label guardian.acceptance=true \
    --cgroup-parent workload.slice \
    --cpus 1.0 --memory 128m --pids-limit 128 alpine:3.20 \
    sh -c 'for i in 1 2 3 4 5 6 7 8; do while :; do :; done & done; wait' >/dev/null
else
  docker run -d --name $NAME --label guardian.acceptance=true \
    --memory 128m --pids-limit 128 alpine:3.20 \
    sh -c 'for i in 1 2 3 4 5 6 7 8; do while :; do :; done & done; wait' >/dev/null
fi
echo "started $NAME ($MODE) at $(date +%T)"; sleep 20
uptime; docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' $NAME
