#!/bin/bash
# triple extreme: CPU all cores + 3.2GiB memory + direct-IO writer (dsync).
# held open; stop via 50_cleanup.sh or docker stop each.
set -e
docker rm -f guardian-accept-cpu guardian-accept-memory guardian-accept-io >/dev/null 2>&1 || true
docker run -d --name guardian-accept-cpu --label guardian.acceptance=true \
  --memory 128m --pids-limit 128 alpine:3.20 \
  sh -c 'for i in 1 2 3 4 5 6 7 8; do while :; do :; done & done; wait' >/dev/null
docker run -d --name guardian-accept-memory --label guardian.acceptance=true \
  --memory 3500m --pids-limit 64 \
  -v /root/hog_hold.py:/hog.py:ro \
  python:3.12-alpine python3 /hog.py 3200 900 >/dev/null
docker run -d --name guardian-accept-io --label guardian.acceptance=true \
  --cpus 0.5 --memory 256m --pids-limit 64 \
  -v /mnt/guardian-acceptance:/acceptance alpine:3.20 \
  sh -c 'dd if=/dev/zero of=/acceptance/guardian-io.bin bs=1M oflag=dsync count=6000 2>&1 | tail -1; rm -f /acceptance/guardian-io.bin; sleep 900' >/dev/null
echo "triple extreme started at $(date +%T)"
sleep 30
uptime; free -m | sed -n 2,3p
