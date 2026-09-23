#!/bin/bash
# emergency cleanup: kill all hogs, remove pressure containers, restore services.
# This is the ONLY exit from a collapse run - run it before any diagnosis,
# and any time the experiment must be aborted. Safe to run repeatedly.
docker rm -f $(docker ps -aq --filter label=guardian.acceptance=true) >/dev/null 2>&1
pkill -f 'python3 /hog.py' 2>/dev/null || true
pkill -f 'ben_b_seq' 2>/dev/null || true
pkill -f 'ben_a_run' 2>/dev/null || true
sleep 8
echo "hogs left: $(pgrep -fc 'python3 /hog.py' || echo 0)"
free -m | sed -n 2,3p
systemctl start guardian-collector.service guardian-runtime.service feishu-gateway.service 2>/dev/null || true
sleep 6
echo "guardian: $(systemctl is-active guardian-runtime guardian-collector feishu-gateway | tr '\n' ' ')"
echo "ready: $(cat /run/guardian-runtime/ready 2>/dev/null || echo missing)"
