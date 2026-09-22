#!/bin/bash
# evidence sweep: how badly did the monitoring stack degrade?
# - feishu gateway stale alerts + recoveries in the window
# - beszel agent connection churn
# - guardian runtime crash/restart timeline
WIN_FROM=${1:-"17:00"}
WIN_TO=${2:-"now"}
echo "=== feishu gateway stale/recovery alerts ($WIN_FROM -> $WIN_TO) ==="
journalctl -u feishu-gateway --since "$WIN_FROM" --until "$WIN_TO" --no-pager \
  | grep -E "sent.*(中断|恢复正常)" | tail -30
echo "stale-alert count: $(journalctl -u feishu-gateway --since "$WIN_FROM" --until "$WIN_TO" --no-pager | grep -c '中断')"
echo
echo "=== guardian-runtime crash timeline ==="
journalctl -u guardian-runtime --since "$WIN_FROM" --no-pager \
  | grep -E "Main process exited|Started guardian-runtime" | tail -10
echo "NRestarts=$(systemctl show guardian-runtime -p NRestarts --value)  state=$(systemctl is-active guardian-runtime)"
echo
echo "=== beszel agent connection churn (errors + re-connects) ==="
docker logs beszel-agent-poc --since 120m 2>&1 \
  | grep -ciE "error|fail|timeout" || echo 0
docker logs beszel-agent-poc --since 120m 2>&1 | grep -c "SSH connected" || echo 0
echo
echo "=== kernel OOM victims (unique) ==="
dmesg --ctime | grep "Killed process" | tail -10
