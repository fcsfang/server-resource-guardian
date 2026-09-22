#!/bin/bash
# usage: 15_oom_swarm.sh <start|topup|stop> [n] [mib_each]
# a swarm of holders with restart=on-failure: kernel kills some, docker
# replaces them -> system pinned at the OOM churn edge (EXP-088 scenario 7).
# demand should exceed RAM+swap by ~10-15% for sustained churn.
# NOTE: docker restart backoff decays the active count over time; use `topup`.
set -e
CMD=${1:-start}; N=${2:-24}; MIB=${3:-280}
case "$CMD" in
  start)
    docker rm -f $(docker ps -aq --filter name=guardian-oom-swarm-) >/dev/null 2>&1 || true
    for i in $(seq 1 "$N"); do
      docker run -d --name "guardian-oom-swarm-$i" --label guardian.acceptance=true \
        --pids-limit 32 --restart on-failure:100 \
        -v /root/hog_hold.py:/hog.py:ro \
        python:3.12-alpine python3 /hog.py "$MIB" 900 >/dev/null &
    done
    wait
    echo "swarm of $N x ${MIB}MiB (demand $((N*MIB))MiB) started at $(date +%T)" ;;
  topup)
    START=$((N + 1)); END=$((N + 8))
    for i in $(seq "$START" "$END"); do
      docker rm -f "guardian-oom-fill-$i" >/dev/null 2>&1 || true
      docker run -d --name "guardian-oom-fill-$i" --label guardian.acceptance=true \
        --pids-limit 32 --restart on-failure:100 \
        -v /root/hog_hold.py:/hog.py:ro \
        python:3.12-alpine python3 /hog.py "$MIB" 900 >/dev/null &
    done
    wait
    echo "$((END-START+1)) fillers started at $(date +%T)" ;;
  stop)
    docker rm -f $(docker ps -aq --filter name=guardian-oom-) >/dev/null 2>&1 || true
    echo "swarm stopped at $(date +%T)" ;;
  *) echo "cmd must be start|topup|stop"; exit 2 ;;
esac
sleep 30
free -m | sed -n 2,3p
echo "oom-kills: $(dmesg --ctime | grep -c 'Killed process')"
