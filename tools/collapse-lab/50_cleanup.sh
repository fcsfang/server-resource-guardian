#!/bin/bash
# full teardown: remove every lab object, verify recovery and control-plane health
docker rm -f $(docker ps -aq --filter label=guardian.acceptance=true) >/dev/null 2>&1 || true
umount /mnt/guardian-acceptance 2>/dev/null || true
rm -f /var/tmp/guardian-acceptance.img
sleep 8
echo "=== memory ==="; free -m | sed -n 2,3p
echo "=== control plane ==="
systemctl is-active guardian-runtime guardian-collector feishu-gateway ssh || true
systemctl show guardian-runtime -p NRestarts --value
echo "=== surviving containers ==="
docker ps --format '{{.Names}} {{.Status}}'
uptime
