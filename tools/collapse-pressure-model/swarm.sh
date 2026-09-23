#!/bin/bash
# usage: swarm.sh <start|topup|stop> [n] [mib_each]
# Verified pinned-collapse form: 24 x 280MiB with restart=on-failure:100.
# Kernel OOM kills holders; docker replaces them -> host stays pinned at the
# churn edge (RAM <70Mi, swap 100%). Demand should exceed RAM+swap by ~10-15%.
# NOTE: docker restart backoff decays the active count over time (24 -> 14);
# use `topup 8` to re-pin during long windows.
set -e
CMD=${1:-start}; N=${2:-24}; MIB=${3:-280}
PREFIX=${PREFIX:-ben-swarm-}
case "$CMD" in
  start)
    docker rm -f $(docker ps -aq --filter name="$PREFIX") >/dev/null 2>&1 || true
    for i in $(seq 1 "$N"); do
      docker run -d --name "${PREFIX}$i" --label guardian.acceptance=true \
        --pids-limit 32 --restart on-failure:100 \
        -v /root/hog_hold.py:/hog.py:ro \
        python:3.12-alpine python3 /hog.py "$MIB" 900 >/dev/null &
    done
    wait
    echo "swarm $N x ${MIB}MiB (demand $((N*MIB))MiB) started $(date +%T)" ;;
  topup)
    START=$((N + 1)); END=$((N + 8))
    for i in $(seq "$START" "$END"); do
      docker rm -f "${PREFIX}$i" >/dev/null 2>&1 || true
      docker run -d --name "${PREFIX}$i" --label guardian.acceptance=true \
        --pids-limit 32 --restart on-failure:100 \
        -v /root/hog_hold.py:/hog.py:ro \
        python:3.12-alpine python3 /hog.py "$MIB" 900 >/dev/null &
    done
    wait
    echo "$((END-START+1)) fillers started $(date +%T)" ;;
  stop)
    docker rm -f $(docker ps -aq --filter name="$PREFIX") >/dev/null 2>&1 || true
    pkill -f 'python3 /hog.py' 2>/dev/null || true
    echo "swarm stopped $(date +%T)" ;;
  *) echo "cmd must be start|topup|stop"; exit 2 ;;
esac
sleep 25
free -m | sed -n 2,3p
echo "oom-kills: $(dmesg --ctime | grep -c 'Killed process')"
echo "residual hog procs: $(pgrep -fc 'python3 /hog.py' || echo 0)"
