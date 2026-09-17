#!/usr/bin/env bash

# Idempotent setup for the disposable Ubuntu WSL2 lab.
# Safety guard: refuses to run on a native Linux/production host.

set -euo pipefail

if ! grep -qi 'microsoft.*WSL2' /proc/sys/kernel/osrelease; then
  printf 'Refusing to run: this script is only for a WSL2 test instance.\n' >&2
  exit 1
fi

if [ ! -r /etc/os-release ]; then
  printf 'Cannot identify the Linux distribution.\n' >&2
  exit 1
fi

# shellcheck disable=SC1091
. /etc/os-release
if [ "${ID:-}" != "ubuntu" ]; then
  printf 'Expected Ubuntu, found %s %s.\n' "${ID:-unknown}" "${VERSION_ID:-unknown}" >&2
  exit 1
fi

case "${VERSION_ID:-}" in
  22.04)
    printf 'Using Ubuntu 22.04, matching the production user space baseline.\n'
    ;;
  26.04)
    printf '%s\n' \
      'WARNING: Ubuntu 26.04 is suitable for the local functional PoC only.' \
      'Production compatibility must be rechecked on Ubuntu 22.04 / systemd 249.' >&2
    ;;
  *)
    printf 'Supported lab releases are Ubuntu 22.04 and 26.04; found %s.\n' \
      "${VERSION_ID:-unknown}" >&2
    exit 1
    ;;
esac

if [ "$(id -u)" -ne 0 ]; then
  printf 'Run this script with sudo inside the WSL2 Ubuntu instance.\n' >&2
  exit 1
fi

lab_user="${SUDO_USER:-}"
if [ -z "$lab_user" ] || [ "$lab_user" = "root" ]; then
  printf 'SUDO_USER is missing. Run with: sudo bash setup-wsl-ubuntu.sh\n' >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update

packages=(
  ca-certificates
  containerd
  curl
  docker.io
  iproute2
  jq
  procps
  runc
  stress-ng
  sysstat
  systemd-oomd
)

if apt-cache show docker-compose-v2 >/dev/null 2>&1; then
  packages+=(docker-compose-v2)
elif apt-cache show docker-compose-plugin >/dev/null 2>&1; then
  packages+=(docker-compose-plugin)
else
  printf 'WARNING: Docker Compose v2 is unavailable in the configured repositories.\n' >&2
fi

apt-get install -y "${packages[@]}"

install -d -m 0755 /etc/docker
if [ ! -e /etc/docker/daemon.json ]; then
  printf '%s\n' '{' '  "exec-opts": ["native.cgroupdriver=systemd"]' '}' > /etc/docker/daemon.json
else
  printf 'Preserving existing /etc/docker/daemon.json; verify the cgroup driver manually.\n'
fi

usermod -aG docker "$lab_user"
systemctl enable --now docker.service
systemctl enable --now systemd-oomd.service

printf '\n===== Lab verification =====\n'
printf 'OS=%s %s\n' "${ID}" "${VERSION_ID}"
systemctl --version | head -n 1
findmnt -T /sys/fs/cgroup -o TARGET,FSTYPE,OPTIONS
docker version
docker info --format 'CgroupDriver={{.CgroupDriver}} CgroupVersion={{.CgroupVersion}} StorageDriver={{.Driver}} ServerVersion={{.ServerVersion}}'
if docker compose version >/dev/null 2>&1; then
  docker compose version
fi
systemctl is-active docker.service systemd-oomd.service

printf '\nWSL2 lab base setup completed. Restart the WSL instance so docker group membership is refreshed.\n'
