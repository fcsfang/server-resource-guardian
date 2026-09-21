#!/usr/bin/env bash

# Install the observe-only Guardian runtime on a disposable local Ubuntu host.
# The default is a non-mutating plan. Applying changes requires an explicit
# local-disposable environment marker so this entry point cannot be mistaken
# for a production installer.

set -Eeuo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DEFAULT_REPOSITORY=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)

repository=${DEFAULT_REPOSITORY}
environment=""
apply=false
install_root=/opt/server-resource-guardian
backup_root=/var/backups/guardian-local-installer

usage() {
  cat <<'EOF'
Usage:
  install-guardian-local.sh [--repository PATH]
  install-guardian-local.sh --apply --environment local-disposable [--repository PATH]

The default command only prints the installation plan. The --apply form is
deliberately restricted to a disposable local Ubuntu/systemd test host.
EOF
}

die() {
  printf 'guardian-local-install: %s\n' "$*" >&2
  exit 1
}

while (($# > 0)); do
  case "$1" in
    --apply)
      apply=true
      shift
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
    --install-root)
      (($# >= 2)) || die "--install-root requires a path"
      install_root=$2
      shift 2
      ;;
    --backup-root)
      (($# >= 2)) || die "--backup-root requires a path"
      backup_root=$2
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

required_files=(
  "src/guardian_orchestrator.py"
  "src/guardian_preflight.py"
  "src/guardian_collector_client.py"
  "src/guardian_collector_service.py"
  "scripts/guardian_status.py"
  "config/guardian.example.json"
  "deploy/guardian/guardian-runtime.service"
  "deploy/guardian/guardian-runtime.slice"
  "deploy/guardian/guardian-collector.service"
  "deploy/guardian/guardian-collector.slice"
  "deploy/guardian/guardian-broker.slice"
  "deploy/guardian/guardian-broker.service"
  "deploy/guardian/rescue.slice"
  "deploy/guardian/workload.slice"
  "deploy/guardian/guardian.tmpfiles"
  "deploy/guardian/guardian-journald.conf"
)

for relative_path in "${required_files[@]}"; do
  [[ -f "${repository}/${relative_path}" ]] || die "required repository file missing: ${relative_path}"
done

managed_units=(
  rescue.slice
  workload.slice
  guardian-runtime.slice
  guardian-collector.slice
  guardian-broker.slice
  guardian-collector.service
  guardian-runtime.service
  guardian-broker.service
)

print_plan() {
  cat <<EOF
Guardian local installation plan
  repository: ${repository}
  install root: ${install_root}
  target: local-disposable Ubuntu with systemd
  runtime mode: observe only
  broker: installed as a static template, disabled and unauthorized
  mutations: no (use --apply --environment local-disposable to apply)

Would create or verify:
  accounts: guardian, guardian-broker, guardian-shared
  state: /etc/guardian and /var/lib/guardian
  units: ${managed_units[*]}
  services: guardian-collector.service and guardian-runtime.service enabled and started
EOF
}

if [[ "$apply" != true ]]; then
  [[ -z "$environment" || "$environment" == "local-disposable" ]] || die "only local-disposable is supported"
  print_plan
  exit 0
fi

[[ "$environment" == "local-disposable" ]] || die "--apply requires --environment local-disposable"
[[ "$(uname -s)" == "Linux" ]] || die "apply is supported only on Linux"
[[ "$(id -u)" -eq 0 ]] || die "run the applying command with sudo"
[[ -r /etc/os-release ]] || die "cannot identify the operating system"
# shellcheck disable=SC1091
. /etc/os-release
[[ "${ID:-}" == "ubuntu" ]] || die "apply is restricted to Ubuntu; found ${ID:-unknown}"
command -v systemctl >/dev/null 2>&1 || die "systemctl is required"
command -v systemd-analyze >/dev/null 2>&1 || die "systemd-analyze is required"
command -v systemd-tmpfiles >/dev/null 2>&1 || die "systemd-tmpfiles is required"

if [[ -e /etc/guardian/broker.enabled ]]; then
  die "refusing to install over an explicitly enabled Broker: /etc/guardian/broker.enabled"
fi
if systemctl is-active --quiet guardian-broker.service 2>/dev/null; then
  die "refusing to install while guardian-broker.service is active"
fi
broker_enable_state=$(systemctl is-enabled guardian-broker.service 2>/dev/null || true)
if [[ "$broker_enable_state" == "enabled" ]]; then
  die "refusing to install while guardian-broker.service is enabled"
fi

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

ensure_group guardian
ensure_group guardian-shared
ensure_group guardian-broker
ensure_group guardian-collector
ensure_user guardian guardian
ensure_user guardian-broker guardian-broker
ensure_user guardian-collector guardian-collector
usermod --append --groups guardian-shared,guardian-broker guardian
usermod --append --groups guardian-shared guardian-broker
getent group docker >/dev/null 2>&1 || die "docker group is required for the read-only Collector"
usermod --append --groups docker,guardian-shared guardian-collector

config_path=/etc/guardian/guardian.json
if [[ ! -e "$config_path" ]]; then
  install -d -o root -g guardian-shared -m 0750 /etc/guardian
  install -o root -g guardian-shared -m 0640 "${repository}/config/guardian.example.json" "$config_path"
fi

python3 - "$config_path" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as stream:
    value = json.load(stream)
mode = value.get("agent", {}).get("mode")
actions_enabled = value.get("actions", {}).get("enabled")
if mode != "observe" or actions_enabled is not False:
    raise SystemExit("guardian config must remain observe-only with actions disabled")
PY

mkdir -p "$install_root"
if [[ "$repository" != "$install_root" ]]; then
  tar --exclude=.git --exclude=__pycache__ --exclude='*.pyc' -C "$repository" -cf - . | tar -C "$install_root" -xf -
fi
chown -R root:root "$install_root"
install -o root -g root -m 0755 "${repository}/scripts/guardian_status.py" /usr/local/bin/guardian-status

timestamp=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir="${backup_root}/${timestamp}"
backup_created=false
backup_if_present() {
  local path=$1
  [[ -e "$path" ]] || return 0
  mkdir -p "$backup_dir"
  backup_created=true
  cp -a "$path" "$backup_dir/"
}

for unit in "${managed_units[@]}"; do
  backup_if_present "/etc/systemd/system/${unit}"
done
backup_if_present /etc/tmpfiles.d/guardian.conf
backup_if_present /etc/systemd/journald.conf.d/guardian.conf

install -d -o root -g root -m 0755 /etc/systemd/system
for unit in "${managed_units[@]}"; do
  install -o root -g root -m 0644 "${repository}/deploy/guardian/${unit}" "/etc/systemd/system/${unit}"
done
install -d -o root -g root -m 0755 /etc/tmpfiles.d
install -o root -g root -m 0644 "${repository}/deploy/guardian/guardian.tmpfiles" /etc/tmpfiles.d/guardian.conf
install -d -o root -g root -m 0755 /etc/systemd/journald.conf.d
install -o root -g root -m 0644 "${repository}/deploy/guardian/guardian-journald.conf" /etc/systemd/journald.conf.d/guardian.conf

systemd-analyze verify \
  /etc/systemd/system/rescue.slice \
  /etc/systemd/system/workload.slice \
  /etc/systemd/system/guardian-runtime.slice \
  /etc/systemd/system/guardian-collector.slice \
  /etc/systemd/system/guardian-broker.slice \
  /etc/systemd/system/guardian-collector.service \
  /etc/systemd/system/guardian-runtime.service \
  /etc/systemd/system/guardian-broker.service
systemd-tmpfiles --create /etc/tmpfiles.d/guardian.conf
systemctl daemon-reload
# Some Ubuntu systemd builds expose journald without a standalone reload
# operation. Apply the drop-in without failing the whole install in that case.
systemctl reload systemd-journald 2>/dev/null || systemctl reload-or-restart systemd-journald
systemctl enable guardian-collector.service
systemctl restart guardian-collector.service 2>/dev/null || systemctl start guardian-collector.service
systemctl enable guardian-runtime.service
systemctl restart guardian-runtime.service 2>/dev/null || systemctl start guardian-runtime.service

if ! systemctl is-active --quiet guardian-collector.service || ! systemctl is-active --quiet guardian-runtime.service; then
  printf 'guardian-local-install: runtime failed to start; backup: %s\n' "${backup_created:+$backup_dir}" >&2
  systemctl --no-pager --full status guardian-runtime.service >&2 || true
  exit 1
fi

[[ ! -e /etc/guardian/broker.enabled ]] || die "Broker marker appeared during installation"
if systemctl is-active --quiet guardian-broker.service 2>/dev/null; then
  die "Broker became active during installation"
fi
[[ ! -e /run/guardian-broker/broker.sock ]] || die "Broker socket appeared during installation"
[[ -S /run/guardian-collector/collector.sock ]] || die "Collector socket is missing"
[[ -s /run/guardian-runtime/ready ]] || die "runtime readiness marker is missing"

printf 'Guardian local installation completed\n'
printf '  runtime: %s\n' "$(systemctl is-active guardian-runtime.service)"
printf '  enabled: %s\n' "$(systemctl is-enabled guardian-runtime.service)"
printf '  collector: %s (%s)\n' "$(systemctl is-active guardian-collector.service)" "$(systemctl is-enabled guardian-collector.service)"
printf '  readiness: %s\n' "$(tr -d '\n' < /run/guardian-runtime/ready)"
printf '  broker: disabled (no marker, no socket)\n'
if [[ "$backup_created" == true ]]; then
  printf '  backup: %s\n' "$backup_dir"
fi
