#!/usr/bin/env bash

# Install or roll back the observe-only Guardian package on Ubuntu 22.04 x86_64.
# The default command is a non-mutating plan. Applying changes requires an
# explicit environment marker and passes the read-only x86 admission check.
# Apply also creates host-sized maintenance and acceptance-workload domains.

set -Eeuo pipefail
umask 0027

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DEFAULT_REPOSITORY=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)

repository=${DEFAULT_REPOSITORY}
environment=""
operation=plan
install_root=/opt/server-resource-guardian
backup_root=/var/backups/guardian-x86-installer
rollback_backup=""
backup_dir=""
preflight_report=""

usage() {
  cat <<'EOF'
Usage:
  install-guardian-x86.sh [--repository PATH]
  install-guardian-x86.sh --apply --environment x86-observe [--repository PATH]
  install-guardian-x86.sh --rollback BACKUP --environment x86-observe

The default command prints a plan and changes nothing. Apply installs only the
observe runtime and collector. It never enables container actions. Rollback
restores files and service states captured by a previous apply.
EOF
}

die() {
  printf 'guardian-x86-install: %s\n' "$*" >&2
  exit 1
}

while (($# > 0)); do
  case "$1" in
    --apply)
      [[ "$operation" == plan ]] || die "choose exactly one operation"
      operation=apply
      shift
      ;;
    --rollback)
      [[ "$operation" == plan ]] || die "choose exactly one operation"
      (($# >= 2)) || die "--rollback requires a backup path"
      operation=rollback
      rollback_backup=$2
      shift 2
      ;;
    --environment)
      (($# >= 2)) || die "--environment requires a value"
      environment=$2
      shift 2
      ;;
    --repository)
      (($# >= 2)) || die "--repository requires a path"
      repository=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "unknown argument: $1"
      ;;
  esac
done

repository=$(CDPATH= cd -- "$repository" 2>/dev/null && pwd) || die "repository does not exist: $repository"

managed_units=(
  rescue.slice
  workload.slice
  guardian-runtime.slice
  guardian-collector.slice
  guardian-collector.service
  guardian-runtime.service
)

managed_paths=(
  "$install_root"
  /usr/local/bin/guardian-status
  /etc/systemd/system/guardian-runtime.slice
  /etc/systemd/system/guardian-collector.slice
  /etc/systemd/system/rescue.slice
  /etc/systemd/system/workload.slice
  /etc/systemd/system/guardian-collector.service
  /etc/systemd/system/guardian-runtime.service
  /etc/tmpfiles.d/guardian.conf
)

print_plan() {
  cat <<EOF
Guardian x86 observe installation plan
  repository: ${repository}
  install root: ${install_root}
  target: Ubuntu 22.04 x86_64 with systemd and cgroup v2
  runtime mode: observe only
  automatic actions: disabled; no Broker service is installed or enabled
  host services: existing SSH, login, logging, networking and Docker control
    units receive a reversible maintenance-resource drop-in
  CPU placement: no fixed CPU; reserve values are calculated from this host
  mutations: no (use --apply --environment x86-observe to apply)

Would install and start:
  guardian-collector.service
  guardian-runtime.service

Would preserve:
  existing Guardian configuration, state, audit and snapshots
  pre-install files and service states in ${backup_root}
EOF
}

protected_units=(
  ssh.service
  sshd.service
  systemd-logind.service
  systemd-journald.service
  docker.service
  containerd.service
  systemd-networkd.service
  systemd-resolved.service
  NetworkManager.service
)
for unit in "${protected_units[@]}"; do
  managed_paths+=("/etc/systemd/system/${unit}.d/guardian-rescue-member.conf")
done
managed_paths+=(
  /etc/systemd/system/user.slice.d/guardian-rescue-member.conf
  /etc/systemd/journald.conf.d/guardian.conf
)

if [[ "$operation" == plan ]]; then
  [[ -z "$environment" || "$environment" == x86-observe ]] || die "only x86-observe is supported"
  print_plan
  exit 0
fi

[[ "$environment" == x86-observe ]] || die "apply and rollback require --environment x86-observe"
[[ "$(uname -s)" == Linux ]] || die "this operation is supported only on Linux"
[[ "$(id -u)" -eq 0 ]] || die "run this operation with sudo"
command -v systemctl >/dev/null 2>&1 || die "systemctl is required"

validate_backup_path() {
  local requested=$1
  local canonical_root canonical_path owner mode
  canonical_root=$(realpath -m "$backup_root")
  canonical_path=$(realpath -m "$requested")
  [[ "$canonical_path" == "$canonical_root"/* ]] || die "rollback backup must be below ${canonical_root}"
  [[ -f "$canonical_path/manifest.tsv" ]] || die "rollback manifest missing: ${canonical_path}/manifest.tsv"
  [[ -f "$canonical_path/services.tsv" ]] || die "rollback service state missing: ${canonical_path}/services.tsv"
  owner=$(stat -c '%u' "$canonical_path" "$canonical_path/manifest.tsv" "$canonical_path/services.tsv" | sort -u)
  [[ "$owner" == 0 ]] || die "rollback metadata must be owned by root"
  for metadata in "$canonical_path" "$canonical_path/manifest.tsv" "$canonical_path/services.tsv"; do
    mode=$(stat -c '%a' "$metadata")
    (( (8#$mode & 0022) == 0 )) || die "rollback metadata must not be group/world writable: ${metadata}"
  done
  printf '%s\n' "$canonical_path"
}

is_managed_path() {
  local candidate=$1
  local expected
  for expected in "${managed_paths[@]}"; do
    [[ "$candidate" == "$expected" ]] && return 0
  done
  return 1
}

if [[ "$operation" == rollback ]]; then
  backup_dir=$(validate_backup_path "$rollback_backup")
  systemctl disable --now guardian-runtime.service guardian-collector.service >/dev/null 2>&1 || true

  while IFS=$'\t' read -r path existed; do
    [[ -n "$path" ]] || continue
    is_managed_path "$path" || die "rollback manifest contains unmanaged path: ${path}"
    [[ "$existed" == yes || "$existed" == no ]] || die "rollback manifest has invalid state for: ${path}"
    if [[ "$existed" == yes ]]; then
      source_path="${backup_dir}/rootfs${path}"
      [[ -e "$source_path" || -L "$source_path" ]] || die "backup payload missing: ${source_path}"
      rm -rf -- "$path"
      mkdir -p "$(dirname -- "$path")"
      cp -a -- "$source_path" "$path"
    else
      rm -rf -- "$path"
    fi
  done < "$backup_dir/manifest.tsv"

  systemctl daemon-reload
  while IFS=$'\t' read -r unit enabled active; do
    [[ -n "$unit" ]] || continue
    if [[ "$enabled" == enabled ]]; then
      systemctl enable "$unit" >/dev/null
    fi
    if [[ "$active" == active ]]; then
      systemctl start "$unit"
    fi
  done < "$backup_dir/services.tsv"

  printf 'Guardian x86 rollback completed\n'
  printf '  restored from: %s\n' "$backup_dir"
  printf '  preserved state: /etc/guardian and /var/lib/guardian\n'
  printf '  reboot required: yes (to restore protected service cgroups)\n'
  exit 0
fi

required_files=(
  scripts/guardian-x86-preflight.py
  scripts/guardian_status.py
  config/guardian.example.json
  deploy/guardian-x86/guardian.tmpfiles
  deploy/guardian-x86/guardian-runtime.slice
  deploy/guardian-x86/guardian-collector.slice
  deploy/guardian-x86/guardian-runtime.service
  deploy/guardian-x86/guardian-collector.service
  src/guardian_orchestrator.py
  src/guardian_collector_service.py
  src/guardian_preflight.py
)
for relative_path in "${required_files[@]}"; do
  [[ -f "${repository}/${relative_path}" ]] || die "required repository file missing: ${relative_path}"
done

[[ ! -e /etc/guardian/broker.enabled ]] || die "refusing to install while the action Broker marker exists"
[[ ! -e /etc/guardian/reserve-broker.enabled ]] || die "refusing to install while the reserve Broker marker exists"
if systemctl is-active --quiet guardian-broker.service 2>/dev/null; then
  die "refusing to install while guardian-broker.service is active"
fi
if systemctl is-active --quiet guardian-reserve-broker.service 2>/dev/null; then
  die "refusing to install while guardian-reserve-broker.service is active"
fi

preflight_report=$(mktemp)
cleanup() {
  local result=$?
  rm -f -- "$preflight_report"
  if (( result != 0 )) && [[ -n "$backup_dir" ]]; then
    printf 'guardian-x86-install: installation did not complete; rollback with:\n' >&2
    printf '  sudo %s --rollback %s --environment x86-observe\n' "$0" "$backup_dir" >&2
  fi
}
trap cleanup EXIT
python3 "${repository}/scripts/guardian-x86-preflight.py" --output "$preflight_report" >/dev/null \
  || { cat "$preflight_report" >&2; die "x86 admission check failed"; }

cpu_count=$(nproc)
mem_kib=$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo)
[[ "$cpu_count" =~ ^[0-9]+$ && "$cpu_count" -ge 2 ]] || die "cannot determine host CPU count"
[[ "$mem_kib" =~ ^[0-9]+$ && "$mem_kib" -gt 0 ]] || die "cannot determine host memory"
mem_mib=$((mem_kib / 1024))
rescue_min_mib=$((mem_mib / 8))
(( rescue_min_mib < 256 )) && rescue_min_mib=256
(( rescue_min_mib > 2048 )) && rescue_min_mib=2048
rescue_low_mib=$((mem_mib / 4))
(( rescue_low_mib < rescue_min_mib )) && rescue_low_mib=$rescue_min_mib
(( rescue_low_mib > 4096 )) && rescue_low_mib=4096
workload_high_mib=$((mem_mib * 3 / 4))
(( workload_high_mib < 256 )) && workload_high_mib=256
printf 'Guardian x86 host policy: cpus=%s memory=%sMiB rescue_min=%sMiB rescue_low=%sMiB workload_high=%sMiB\n' \
  "$cpu_count" "$mem_mib" "$rescue_min_mib" "$rescue_low_mib" "$workload_high_mib"

ensure_group() {
  local name=$1
  getent group "$name" >/dev/null 2>&1 || groupadd --system "$name"
}

ensure_user() {
  local name=$1
  local group=$2
  if ! getent passwd "$name" >/dev/null 2>&1; then
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin --gid "$group" "$name"
  fi
}

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir="${backup_root}/${timestamp}"
mkdir -p "$backup_dir/rootfs"
chmod 0700 "$backup_dir" "$backup_dir/rootfs"
: > "$backup_dir/manifest.tsv"
: > "$backup_dir/services.tsv"

for unit in guardian-collector.service guardian-runtime.service; do
  enabled=$(systemctl is-enabled "$unit" 2>/dev/null || true)
  active=$(systemctl is-active "$unit" 2>/dev/null || true)
  printf '%s\t%s\t%s\n' "$unit" "$enabled" "$active" >> "$backup_dir/services.tsv"
done

for path in "${managed_paths[@]}"; do
  if [[ -e "$path" || -L "$path" ]]; then
    printf '%s\tyes\n' "$path" >> "$backup_dir/manifest.tsv"
    destination="${backup_dir}/rootfs${path}"
    mkdir -p "$(dirname -- "$destination")"
    cp -a -- "$path" "$destination"
  else
    printf '%s\tno\n' "$path" >> "$backup_dir/manifest.tsv"
  fi
done

ensure_group guardian
ensure_group guardian-shared
ensure_group guardian-collector
ensure_user guardian guardian
ensure_user guardian-collector guardian-collector
usermod --append --groups guardian-shared guardian
getent group docker >/dev/null 2>&1 || die "docker group is required for the collector"
usermod --append --groups docker,guardian-shared guardian-collector

config_path=/etc/guardian/guardian.json
if [[ ! -e "$config_path" ]]; then
  install -d -o root -g guardian-shared -m 0750 /etc/guardian
  install -o root -g guardian-shared -m 0640 "${repository}/config/guardian.example.json" "$config_path"
fi

python3 - "$config_path" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as stream:
    value = json.load(stream)
if value.get("agent", {}).get("mode") != "observe":
    raise SystemExit("guardian config must remain in observe mode")
if value.get("actions", {}).get("enabled") is not False:
    raise SystemExit("guardian automatic actions must remain disabled")
if value.get("actions", {}).get("allow"):
    raise SystemExit("guardian action allowlist must remain empty")
PY

rm -rf -- "$install_root"
mkdir -p "$install_root"
tar --exclude=.git --exclude=__pycache__ --exclude='*.pyc' -C "$repository" -cf - . | tar -C "$install_root" -xf -
deployed_version=$(git -C "$repository" rev-parse HEAD 2>/dev/null || printf 'source-archive')
printf '%s\n' "$deployed_version" > "$install_root/DEPLOYED_VERSION"
chown -R root:root "$install_root"
install -o root -g root -m 0755 "${repository}/scripts/guardian_status.py" /usr/local/bin/guardian-status

install -d -o root -g root -m 0755 /etc/systemd/system /etc/tmpfiles.d
for unit in "${managed_units[@]}"; do
  if [[ "$unit" == "rescue.slice" || "$unit" == "workload.slice" ]]; then
    continue
  fi
  install -o root -g root -m 0644 "${repository}/deploy/guardian-x86/${unit}" "/etc/systemd/system/${unit}"
done
install -o root -g root -m 0644 "${repository}/deploy/guardian-x86/guardian.tmpfiles" /etc/tmpfiles.d/guardian.conf

cat > /etc/systemd/system/rescue.slice <<EOF
[Unit]
Description=Guardian x86 maintenance resource domain

[Slice]
CPUAccounting=true
IOAccounting=true
MemoryAccounting=true
TasksAccounting=true
CPUWeight=1000
IOWeight=1000
MemoryMin=${rescue_min_mib}M
MemoryLow=${rescue_low_mib}M
TasksMax=25%
EOF

cat > /etc/systemd/system/workload.slice <<EOF
[Unit]
Description=Guardian x86 bounded acceptance workload domain

[Slice]
CPUAccounting=true
IOAccounting=true
MemoryAccounting=true
TasksAccounting=true
CPUWeight=1
IOWeight=1
MemoryHigh=${workload_high_mib}M
MemorySwapMax=0
TasksMax=75%
EOF

install -d -o root -g root -m 0755 /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/guardian.conf <<'EOF'
[Journal]
SystemMaxUse=200M
RuntimeMaxUse=64M
RateLimitIntervalSec=30s
RateLimitBurst=200
EOF

for unit in "${protected_units[@]}"; do
  if systemctl cat "$unit" >/dev/null 2>&1; then
    install -d -o root -g root -m 0755 "/etc/systemd/system/${unit}.d"
    cat > "/etc/systemd/system/${unit}.d/guardian-rescue-member.conf" <<'EOF'
[Service]
Slice=rescue.slice
CPUWeight=1000
IOWeight=1000
EOF
  fi
done
install -d -o root -g root -m 0755 /etc/systemd/system/user.slice.d
cat > /etc/systemd/system/user.slice.d/guardian-rescue-member.conf <<EOF
[Slice]
CPUWeight=1000
IOWeight=1000
MemoryMin=${rescue_min_mib}M
MemoryLow=${rescue_low_mib}M
TasksMax=25%
EOF

systemd-analyze verify \
  /etc/systemd/system/guardian-runtime.slice \
  /etc/systemd/system/guardian-collector.slice \
  /etc/systemd/system/guardian-collector.service \
  /etc/systemd/system/guardian-runtime.service \
  /etc/systemd/system/rescue.slice \
  /etc/systemd/system/workload.slice
systemd-tmpfiles --create /etc/tmpfiles.d/guardian.conf
systemctl daemon-reload
systemctl reload-or-restart systemd-journald 2>/dev/null || true
systemctl enable guardian-collector.service guardian-runtime.service
systemctl restart guardian-collector.service
systemctl restart guardian-runtime.service

systemctl is-active --quiet guardian-collector.service || die "collector failed to start; rollback with ${backup_dir}"
systemctl is-active --quiet guardian-runtime.service || die "runtime failed to start; rollback with ${backup_dir}"
[[ -S /run/guardian-collector/collector.sock ]] || die "collector socket is missing; rollback with ${backup_dir}"
[[ -s /run/guardian-runtime/ready ]] || die "runtime readiness marker is missing; rollback with ${backup_dir}"
[[ "$(tr -d '\n' < /run/guardian-runtime/ready)" == runtime:ready:observe ]] || die "runtime is not observe-ready"
[[ ! -e /etc/guardian/broker.enabled ]] || die "action Broker marker appeared unexpectedly"
[[ ! -e /etc/guardian/reserve-broker.enabled ]] || die "reserve Broker marker appeared unexpectedly"

printf 'Guardian x86 observe installation completed\n'
printf '  runtime: %s (%s)\n' "$(systemctl is-active guardian-runtime.service)" "$(systemctl is-enabled guardian-runtime.service)"
printf '  collector: %s (%s)\n' "$(systemctl is-active guardian-collector.service)" "$(systemctl is-enabled guardian-collector.service)"
printf '  readiness: %s\n' "$(tr -d '\n' < /run/guardian-runtime/ready)"
printf '  automatic actions: disabled\n'
printf '  maintenance protection: configured for next reboot\n'
printf '  reboot required before acceptance: yes\n'
printf '  version: %s\n' "$deployed_version"
printf '  rollback: sudo %s --rollback %s --environment x86-observe\n' "$0" "$backup_dir"
