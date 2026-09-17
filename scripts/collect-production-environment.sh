#!/usr/bin/env bash

# Read-only environment inventory for planning a matching WSL2 test system.
# The script does not collect environment variables, Beszel keys/tokens,
# full container configuration, application logs, or file contents outside the
# explicitly listed kernel and OS information files.

set -u
export LC_ALL=C

section() {
  printf '\n\n===== %s =====\n' "$1"
}

run() {
  printf '\n$'
  printf ' %q' "$@"
  printf '\n'
  "$@" 2>&1 || printf '[command exited with status %s]\n' "$?"
}

run_shell() {
  printf '\n$ %s\n' "$1"
  sh -c "$1" 2>&1 || printf '[command exited with status %s]\n' "$?"
}

has() {
  command -v "$1" >/dev/null 2>&1
}

section "Collection metadata"
run date --iso-8601=seconds
run id
printf '\nThis report intentionally excludes secrets, environment variables, application logs, and full container inspection.\n'

section "Operating system and virtualization"
run cat /etc/os-release
run uname -a
if has hostnamectl; then
  run hostnamectl
fi
if has systemd-detect-virt; then
  run systemd-detect-virt
fi
if has systemd; then
  run systemd --version
fi
if has systemctl; then
  run systemctl is-system-running
fi
run cat /proc/cmdline

section "CPU and memory capacity"
if has lscpu; then
  run_shell "lscpu | grep -E '^(Architecture|CPU\\(s\\)|On-line CPU|Model name|Thread|Core|Socket|Virtualization|Hypervisor vendor)'"
fi
run getconf PAGE_SIZE
run getconf _NPROCESSORS_ONLN
run free -h
if has swapon; then
  run swapon --show --bytes
fi
run_shell "grep -E '^(MemTotal|MemAvailable|SwapTotal|SwapFree|HugePages_Total|Hugepagesize):' /proc/meminfo"

section "Storage"
run df -hT
if has lsblk; then
  run lsblk -o NAME,TYPE,FSTYPE,SIZE,MOUNTPOINTS
fi

section "Cgroup and pressure support"
if has findmnt; then
  run findmnt -T /sys/fs/cgroup -o TARGET,FSTYPE,OPTIONS
fi
run stat -fc %T /sys/fs/cgroup
if [ -r /sys/fs/cgroup/cgroup.controllers ]; then
  run cat /sys/fs/cgroup/cgroup.controllers
else
  printf '\n/sys/fs/cgroup/cgroup.controllers is absent (usually cgroup v1 or unavailable).\n'
fi
for pressure_file in /proc/pressure/cpu /proc/pressure/memory /proc/pressure/io; do
  if [ -r "$pressure_file" ]; then
    run cat "$pressure_file"
  else
    printf '\n%s is unavailable.\n' "$pressure_file"
  fi
done
run_shell "grep -E 'cgroup|cgroup2' /proc/filesystems"
run_shell "mount | grep -E 'type cgroup|type cgroup2'"

section "Relevant kernel settings"
if has sysctl; then
  for key in \
    vm.swappiness \
    vm.overcommit_memory \
    vm.overcommit_ratio \
    vm.min_free_kbytes \
    kernel.pid_max \
    fs.file-max; do
    run sysctl "$key"
  done
fi
run_shell "ulimit -a"

section "Container runtime"
if has docker; then
  run docker version
  run docker info
  run docker compose version
  run docker ps --no-trunc --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}'
  run docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}\t{{.PIDs}}'
  printf '\n$ docker inspect <each container> (selected resource fields only)\n'
  while IFS= read -r container_id; do
    [ -n "$container_id" ] || continue
    docker inspect --format 'Name={{.Name}} Image={{.Config.Image}} Memory={{.HostConfig.Memory}} MemoryReservation={{.HostConfig.MemoryReservation}} MemorySwap={{.HostConfig.MemorySwap}} NanoCpus={{.HostConfig.NanoCpus}} CpuQuota={{.HostConfig.CpuQuota}} CpuShares={{.HostConfig.CpuShares}} OomKillDisable={{.HostConfig.OomKillDisable}} PidsLimit={{.HostConfig.PidsLimit}} Restart={{.HostConfig.RestartPolicy.Name}}' "$container_id" 2>&1 || true
  done < <(docker ps -aq 2>/dev/null)
else
  printf '\nDocker CLI not found.\n'
fi
if has podman; then
  run podman version
  run podman info
fi
if has containerd; then
  run containerd --version
fi
if has runc; then
  run runc --version
fi

section "Beszel deployment (secrets excluded)"
if has beszel-agent; then
  run command -v beszel-agent
  run readlink -f "$(command -v beszel-agent)"
  run sha256sum "$(command -v beszel-agent)"
  run beszel-agent --version
fi
if has systemctl; then
  run systemctl show beszel-agent.service \
    --property=LoadState,ActiveState,SubState,UnitFileState,MainPID,User,Group,Slice,ControlGroup,Restart,CPUAccounting,MemoryAccounting,TasksAccounting,CPUWeight,IOWeight,MemoryCurrent,MemoryMin,MemoryLow,MemoryHigh,MemoryMax,TasksCurrent,TasksMax,OOMPolicy
  run systemctl is-enabled beszel-agent.service
fi
if has docker && docker inspect beszel-agent >/dev/null 2>&1; then
  run docker inspect --format 'Name={{.Name}} Image={{.Config.Image}} ImageID={{.Image}} NetworkMode={{.HostConfig.NetworkMode}} Restart={{.HostConfig.RestartPolicy.Name}} Memory={{.HostConfig.Memory}} MemoryReservation={{.HostConfig.MemoryReservation}} NanoCpus={{.HostConfig.NanoCpus}} CpuQuota={{.HostConfig.CpuQuota}} CpuShares={{.HostConfig.CpuShares}} OomKillDisable={{.HostConfig.OomKillDisable}} PidsLimit={{.HostConfig.PidsLimit}}' beszel-agent
  beszel_image_id="$(docker inspect --format '{{.Image}}' beszel-agent 2>/dev/null || true)"
  if [ -n "$beszel_image_id" ]; then
    run docker image inspect --format 'RepoTags={{json .RepoTags}} RepoDigests={{json .RepoDigests}} Created={{.Created}} Architecture={{.Architecture}} OS={{.Os}}' "$beszel_image_id"
  fi
fi
if has ss; then
  run_shell "ss -lntp | awk 'NR == 1 || /:45876|:8090/'"
fi

section "OOM services and recent boot-level events"
if has systemctl; then
  run systemctl is-enabled systemd-oomd.service
  run systemctl is-active systemd-oomd.service
  run systemctl show systemd-oomd.service --property=LoadState,ActiveState,SubState,UnitFileState
  run systemctl is-enabled earlyoom.service
  run systemctl is-active earlyoom.service
fi
if has oomctl; then
  run oomctl
fi
run_shell "dmesg --level=emerg,alert,crit,err 2>/dev/null | grep -Ei 'out of memory|oom-kill|killed process' | tail -n 20"

section "Package versions relevant to reproduction"
if has dpkg-query; then
  run_shell "dpkg-query -W -f='\${binary:Package}\t\${Version}\n' 2>/dev/null | grep -E '^(docker|containerd|runc|systemd|linux-image|linux-generic|beszel)' | sort"
fi
if has rpm; then
  run_shell "rpm -qa --qf '%{NAME}\t%{VERSION}-%{RELEASE}\n' 2>/dev/null | grep -E '^(docker|containerd|runc|systemd|kernel|beszel)' | sort"
fi

section "Network interfaces (addresses excluded)"
if has ip; then
  run ip -brief link
  run ip route show default
fi

section "Collection complete"
printf '\nReview the report before sharing. Hostname, container names, image names, listening ports, and internal routing may be confidential even though secrets are excluded.\n'
