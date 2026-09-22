#!/bin/bash
# six bounded control-plane apps (the "other applications" survival check)
# plus pressure images and the hog helpers
set -e
for n in 1 2 3 4 5 6; do
  docker rm -f "guardian-accept-app-${n}" >/dev/null 2>&1 || true
  docker run -d --name "guardian-accept-app-${n}" --label guardian.acceptance=true \
    --cgroup-parent workload.slice \
    --cpus 0.20 --memory 96m --pids-limit 64 nginx:alpine >/dev/null
done
docker ps --filter label=guardian.acceptance=true --format '{{.Names}} {{.Status}}'
echo "cgroup-parent: $(docker inspect guardian-accept-app-1 --format '{{.HostConfig.CgroupParent}}')"
docker pull -q alpine:3.20 >/dev/null && echo "alpine:3.20 ready"
docker pull -q python:3.12-alpine >/dev/null && echo "python:3.12-alpine ready"
LIB="$(cd "$(dirname "$0")" && pwd)/lib"
install -m 644 "$LIB/hog_hold.py" /root/hog_hold.py
install -m 644 "$LIB/hog_growth.py" /root/hog_growth.py
echo "hog helpers installed to /root"
