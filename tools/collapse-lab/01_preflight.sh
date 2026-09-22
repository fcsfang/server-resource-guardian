#!/bin/bash
# spec / services / loop-mount / prerequisites survey
echo "cpus: $(nproc)"
free -h | sed -n 2,3p
grep PRETTY /etc/os-release
df -h / | tail -1
echo "sshd:   $(systemctl is-active ssh 2>/dev/null || echo inactive)"
which sshd >/dev/null || echo "WARN: openssh-server not installed"
echo "docker: $(docker info --format 'driver={{.CgroupDriver}} ver={{.ServerVersion}}' 2>/dev/null || echo NOT-READABLE)"
for s in guardian-runtime guardian-collector feishu-gateway; do
  echo "$s: $(systemctl is-active $s 2>/dev/null || echo absent)"
done
echo "ready:  $(cat /run/guardian-runtime/ready 2>/dev/null || echo none)"
python3 -c "
import json
c=json.load(open('/etc/guardian/guardian.json'))
print('guardian mode:', c['agent']['mode'], '| actions.enabled:', c['actions']['enabled'])
" 2>/dev/null || echo "guardian config: absent"
dd if=/dev/zero of=/var/tmp/loop-test.img bs=1M count=8 status=none \
  && mkfs.ext4 -q -F /var/tmp/loop-test.img && mkdir -p /mnt/loop-test \
  && mount -o loop /var/tmp/loop-test.img /mnt/loop-test \
  && echo "loop mount: OK" && umount /mnt/loop-test && rm -f /var/tmp/loop-test.img \
  || echo "loop mount: FAILED"
