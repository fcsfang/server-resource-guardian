#!/bin/bash
# usage: guardian_ctl.sh <on|off>   (A-B-A: simulate a host without Guardian)
set -e
if [ "$1" = off ]; then
  systemctl stop guardian-runtime.service guardian-collector.service feishu-gateway.service 2>/dev/null || true
  echo "OFF $(date +%T): $(systemctl is-active guardian-runtime guardian-collector feishu-gateway | tr '\n' ' ')"
elif [ "$1" = on ]; then
  systemctl start guardian-collector.service guardian-runtime.service feishu-gateway.service 2>/dev/null || true
  sleep 6
  echo "ON $(date +%T): $(systemctl is-active guardian-runtime guardian-collector feishu-gateway | tr '\n' ' ') ready=$(cat /run/guardian-runtime/ready 2>/dev/null)"
else
  echo "usage: $0 on|off"; exit 2
fi
