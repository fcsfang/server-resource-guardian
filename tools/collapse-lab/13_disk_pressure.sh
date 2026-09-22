#!/bin/bash
# usage: 13_disk_pressure.sh <setup|fill|recover|teardown>
# 4GiB loop-backed disk; NEVER touch the root fs. fill writes ~100% (leave 20MiB
# for dd itself). Writer container needs >=1Gi memory limit: cgroup v2 charges
# page cache to the writer and 128m self-destructs (EXP-088 E3).
set -e
CMD=${1:-setup}
IMG=/var/tmp/guardian-acceptance.img
MNT=/mnt/guardian-acceptance
case "$CMD" in
  setup)
    if ! findmnt -n "$MNT" >/dev/null 2>&1; then
      rm -f "$IMG"
      dd if=/dev/zero of="$IMG" bs=1M count=4096 status=none
      mkfs.ext4 -q -F "$IMG"; mkdir -p "$MNT"; mount -o loop "$IMG" "$MNT"
    fi
    echo "test disk: $(findmnt -no SOURCE,SIZE,AVAIL "$MNT")"
    python3 - <<'EOF'
import json
p = '/etc/guardian/guardian.json'
cfg = json.load(open(p))
dc = cfg.setdefault('risk', {}).setdefault('disk_capacity', {})
mp = dc.get('mount_points', ['/'])
if '/mnt/guardian-acceptance' not in mp:
    mp.append('/mnt/guardian-acceptance')
dc['mount_points'] = mp
json.dump(cfg, open(p, 'w'), indent=2, ensure_ascii=False)
print('guardian mount_points:', mp)
EOF
    systemctl restart guardian-runtime.service 2>/dev/null || true
    sleep 6; echo "runtime: $(systemctl is-active guardian-runtime.service)" ;;
  fill)
    docker rm -f guardian-accept-disk >/dev/null 2>&1 || true
    AVAIL_KIB=$(df --output=avail "$MNT" | tail -1)
    docker run -d --name guardian-accept-disk --label guardian.acceptance=true \
      --cpus 0.25 --memory 1024m --pids-limit 64 \
      -v "$MNT:/acceptance" alpine:3.20 \
      sh -c "dd if=/dev/zero of=/acceptance/guardian-pressure.bin bs=1M count=$(( (AVAIL_KIB-20480)/1024 )) 2>/dev/null; sleep 900" >/dev/null
    echo "filling ~$(( (AVAIL_KIB-20480)/1024 ))MiB at $(date +%T) (~4 min at loop speed)"
    sleep 45; df -h "$MNT" | tail -1 ;;
  recover)
    docker stop --timeout 20 guardian-accept-disk 2>/dev/null || true
    rm -f "$MNT"/guardian-pressure*.bin; sync; sleep 5
    df -h "$MNT" | tail -1 ;;
  teardown)
    docker rm -f guardian-accept-disk >/dev/null 2>&1 || true
    umount "$MNT" 2>/dev/null || true; rm -f "$IMG"
    echo "test disk torn down" ;;
  *) echo "cmd must be setup|fill|recover|teardown"; exit 2 ;;
esac
