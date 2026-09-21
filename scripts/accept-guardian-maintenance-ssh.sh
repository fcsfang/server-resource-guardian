#!/usr/bin/env bash

# One bounded local-disposable acceptance run. Every diagnostic is a fresh
# external SSH connection; pressure is started only through the fixed helper.

set -Eeuo pipefail

key=""
host=""
user=guardian-maint
output=""

usage() {
  cat <<'EOF'
Usage: accept-guardian-maintenance-ssh.sh --host IP --key PRIVATE_KEY [--user NAME] [--output FILE]

Runs baseline, CPU, memory, disk, and mixed bounded pressure checks. It never
stops or restarts a container and never enables the Broker.
EOF
}

die() { printf 'guardian-maintenance-acceptance: %s\n' "$*" >&2; exit 1; }

while (($# > 0)); do
  case "$1" in
    --host) (($# >= 2)) || die "--host requires a value"; host=$2; shift 2 ;;
    --key) (($# >= 2)) || die "--key requires a value"; key=$2; shift 2 ;;
    --user) (($# >= 2)) || die "--user requires a value"; user=$2; shift 2 ;;
    --output) (($# >= 2)) || die "--output requires a value"; output=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done

[[ -n "$host" ]] || die "--host is required"
[[ -r "$key" ]] || die "SSH key is not readable"

ssh_opts=(-o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=5 -i "$key")
diag='set -eu
printf "login=%s uid=%s\\n" "$(id -un)" "$(id -u)"
sudo -n guardian-maintenance-status
printf "session-cgroup\\n"
systemctl show "user-$(id -u).slice" -p AllowedCPUs -p EffectiveCPUs -p MemoryMin -p MemoryLow -p TasksMax
printf "beszel-health\\n"
docker inspect --format "{{.Name}} {{.State.Health.Status}}" beszel-isolated-t12
curl --fail --silent --show-error http://127.0.0.1:8091/ >/dev/null && echo beszel_http=ok
printf "process-cgroup\\n"
ps -o pid,comm,stat -p $$ --no-headers
printf "read-only-docker\\n"
docker ps --format "{{.Names}} {{.Status}}"
printf "broker-marker="; test -e /etc/guardian/broker.enabled && echo present || echo absent
printf "broker-socket="; test -S /run/guardian-broker/broker.sock && echo present || echo absent
'

now_ms() { python3 -c 'import time; print(int(time.time() * 1000))'; }

run_ssh() {
  ssh "${ssh_opts[@]}" "${user}@${host}" "$1"
}

recorded_run() {
  local label=$1
  local command=$2
  local started ended rc
  started=$(now_ms)
  set +e
  printf '\n===== %s =====\n' "$label"
  run_ssh "$command"
  rc=$?
  set -e
  ended=$(now_ms)
  printf 'result=%s elapsed_ms=%s\n' "$([[ $rc -eq 0 ]] && echo PASS || echo FAIL)" "$((ended - started))"
  return "$rc"
}

if [[ -n "$output" ]]; then
  mkdir -p "$(dirname "$output")"
  exec > >(tee "$output") 2>&1
fi

printf 'Guardian local maintenance acceptance\n'
printf 'host=%s user=%s\n' "$host" "$user"
printf 'scope=disposable-vm; no-container-stop; broker-closed\n'
recorded_run baseline "$diag"

for scenario in cpu memory disk mixed; do
  printf '\n----- pressure=%s -----\n' "$scenario"
  run_ssh "sudo -n guardian-maintenance-pressure $scenario --seconds 30"
  sleep 2
  recorded_run "$scenario" "$diag"
  sleep 31
done

printf '\n===== final-cleanup-check =====\n'
recorded_run final "$diag"
printf 'acceptance=PASS\n'
