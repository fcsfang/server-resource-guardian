#!/usr/bin/env bash

# guardian-host-setup.sh: one command for the host-level defense stack and
# the Feishu alert gateway, on top of (or before) install-guardian-local.sh.
#
# What it installs (all idempotent; re-running converges to the same state):
#   1. earlyoom with the validated trigger (-m 10 -s 100, control plane in
#      --avoid) - including the packaged-unit ExecStart override that makes
#      /etc/default/earlyoom actually take effect
#   2. the transport-defense sysctl file (admin_reserve, swappiness)
#   3. oom_score shields: sshd -1000, guardian-collector/feishu-gateway -800
#   4. the user@.service OOM balance drop-in (WSL default adj=100 kills the
#      session manager before the hogs; harmless on stock Ubuntu)
#   5. the Feishu gateway unit + systemd-journal membership for its user
#
# What it never does: restart sshd (the ssh drop-in takes effect at the next
# natural ssh restart or reboot; remote scripts must not restart sshd), touch
# /etc/guardian/guardian.json, enable any broker, or read credentials beyond
# checking the file exists.
#
# Usage (as root, on the disposable host):
#   bash scripts/guardian-host-setup.sh --apply --environment local-disposable
#   bash scripts/guardian-host-setup.sh            # plan only, changes nothing
# Skips (idempotent partial runs):
#   bash scripts/guardian-host-setup.sh --apply --environment local-disposable --skip earlyoom,sysctl
#
# Prerequisites: install-guardian-local.sh --apply has run (creates the
# guardian user and /opt/server-resource-guardian). Feishu credentials at
# /etc/guardian/feishu.json (mode 0640 root:guardian-shared) - see
# config/feishu.example.json; if absent the gateway unit is installed but
# not started.

set -Eeuo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPOSITORY=$(CDPATH= cd -- "${SCRIPT_DIR}/.." && pwd)
DEPLOY_DIR="${REPOSITORY}/deploy/guardian"

environment=""
apply=false
install_root=/opt/server-resource-guardian
skip=""

usage() {
  cat <<'EOF'
Usage:
  guardian-host-setup.sh [--apply --environment local-disposable] [--skip LIST]

The default command prints the plan and changes nothing. Apply installs the
host defense stack (earlyoom, sysctl, oom_score shields, user session
balance) and the Feishu gateway unit. Every step is idempotent.
--skip takes a comma list: earlyoom,sysctl,oom-shields,user-balance,gateway
EOF
}

die() {
  printf 'guardian-host-setup: %s\n' "$*" >&2
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
    --install-root)
      (($# >= 2)) || die "--install-root requires a value"
      install_root=$2
      shift 2
      ;;
    --skip)
      (($# >= 2)) || die "--skip requires a value"
      skip=$2
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

want_step() {
  [[ ",$skip," != *",$1,"* ]]
}

print_plan() {
  cat <<EOF
Guardian host setup plan
  repository: ${REPOSITORY}
  install root: ${install_root}
  target: local-disposable Ubuntu with systemd
  steps: earlyoom (-m 10 -s 100, control plane avoided), transport-defense
    sysctl, oom_score shields (sshd -1000 / collector+gateway -800),
    user@.service OOM balance, Feishu gateway unit + journal group
  skipped: ${skip:-none}
  notes: sshd is never restarted; guardian.json and brokers untouched
  mutations: no (add --apply --environment local-disposable to apply)
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
[[ -d "$install_root" ]] || die "install root missing (run install-guardian-local.sh --apply first): $install_root"
[[ -d "$DEPLOY_DIR" ]] || die "repository deploy dir missing: $DEPLOY_DIR"

step() { printf '\n== %s ==\n' "$1"; }

# ---------------------------------------------------------------- earlyoom --
if want_step earlyoom; then
  step "1/5 earlyoom"
  if ! command -v earlyoom >/dev/null 2>&1; then
    if command -v apt-get >/dev/null 2>&1; then
      DEBIAN_FRONTEND=noninteractive apt-get install -y earlyoom
    else
      die "earlyoom missing and apt-get unavailable - install it manually"
    fi
  fi
  install -d -o root -g root -m 0755 /etc/default
  # Deploy the validated args file: copy the EARLYOOM_ARGS='...' line
  # verbatim. /etc/default/earlyoom is a systemd EnvironmentFile and needs
  # the KEY=value form - writing the bare value (no key) silently yields an
  # empty $EARLYOOM_ARGS and earlyoom runs unconfigured.
  args_line=$(grep '^EARLYOOM_ARGS=' "${DEPLOY_DIR}/earlyoom-default.conf") \
    || die "earlyoom-default.conf has no EARLYOOM_ARGS= line"
  printf '%s\n' "$args_line" > /etc/default/earlyoom
  chmod 0644 /etc/default/earlyoom
  # The packaged unit passes $EARLYOOM_ARGS literally; fix with the override.
  install -d -o root -g root -m 0755 /etc/systemd/system/earlyoom.service.d
  install -o root -g root -m 0644 "${DEPLOY_DIR}/earlyoom-systemd-override.conf" \
    /etc/systemd/system/earlyoom.service.d/override.conf
  systemctl daemon-reload
  systemctl enable earlyoom >/dev/null 2>&1 || true
  systemctl restart earlyoom
  sleep 1
  systemctl is-active --quiet earlyoom || die "earlyoom failed to start"
  live_args=$(tr '\0' ' ' < "/proc/$(pgrep -x earlyoom)/cmdline")
  printf '%s' "$live_args" | grep -q -- '-m 10' || die "earlyoom live args lack -m 10: $live_args"
  printf '%s' "$live_args" | grep -q -- '-s 100' || die "earlyoom live args lack -s 100: $live_args"
  echo "earlyoom: active with validated args"
else
  echo "1/5 earlyoom: skipped"
fi

# ----------------------------------------------------------------- sysctl --
if want_step sysctl; then
  step "2/5 transport-defense sysctl"
  install -d -o root -g root -m 0755 /etc/sysctl.d
  install -o root -g root -m 0644 "${DEPLOY_DIR}/transport-defense-sysctl.conf" \
    /etc/sysctl.d/99-guardian-transport-defense.conf
  # Apply directly from the file: `sysctl --system` can skip unqualified
  # files silently; per-file apply prints any offending line.
  sysctl -p /etc/sysctl.d/99-guardian-transport-defense.conf
  ar=$(cat /proc/sys/vm/admin_reserve_kbytes)
  [[ "$ar" -ge 131072 ]] || die "admin_reserve_kbytes=$ar want >=131072"
  sw=$(cat /proc/sys/vm/swappiness)
  [[ "$sw" -le 10 ]] || die "swappiness=$sw want <=10"
  echo "sysctl: applied and live (admin_reserve=${ar}KB swappiness=$sw)"
else
  echo "2/5 sysctl: skipped"
fi

# ------------------------------------------------------------ oom shields --
if want_step oom-shields; then
  step "3/5 oom_score shields"
  install_shield() {
    local unit=$1 want=$2 path
    # $unit already carries the .service suffix: the drop-in dir is
    # <unit>.d/, NOT <unit>.service.d/ (which systemd silently ignores).
    path="/etc/systemd/system/${unit}.d/guardian-oom-defense.conf"
    if ! systemctl cat "$unit" >/dev/null 2>&1; then
      echo "  $unit: not present, skipped"
      return 0
    fi
    install -d -o root -g root -m 0755 "/etc/systemd/system/${unit}.d"
    install -o root -g root -m 0644 "${DEPLOY_DIR}/guardian-oom-defense.conf" "$path"
    local adj
    # match the real directive, not the doc comment (which mentions -900)
    adj=$(grep -v '^\s*#' "${DEPLOY_DIR}/guardian-oom-defense.conf" \
      | grep -oP '^\s*OOMScoreAdjust=\K-?\d+' | tail -1)
    [[ "$adj" == "$want" ]] || die "shields template carries OOMScoreAdjust=$adj want $want for $unit"
    echo "  $unit: drop-in with OOMScoreAdjust=${want} (template -800; sshd uses its own)"
  }
  # sshd gets its own -1000 template; runtime keeps the -900 baked in its unit.
  if systemctl cat ssh.service >/dev/null 2>&1 || systemctl cat sshd.service >/dev/null 2>&1; then
    ssh_unit=$(systemctl cat ssh.service >/dev/null 2>&1 && echo ssh.service || echo sshd.service)
    install -d -o root -g root -m 0755 "/etc/systemd/system/${ssh_unit}.d"
    install -o root -g root -m 0644 "${DEPLOY_DIR}/ssh-oom-defense.conf" \
      "/etc/systemd/system/${ssh_unit}.d/guardian-oom-defense.conf"
    echo "  ${ssh_unit}: OOMScoreAdjust=-1000 (takes effect at next natural restart or reboot; sshd is NOT restarted)"
  else
    echo "  ssh: no ssh unit found, skipped"
  fi
  install_shield guardian-collector.service -800
  if systemctl cat feishu-gateway.service >/dev/null 2>&1; then
    install_shield feishu-gateway.service -800
  else
    echo "  feishu-gateway.service: not installed yet (step 5 will cover it)"
  fi
  systemctl daemon-reload
  # The shields apply at process start; restart our own services now so the
  # live oom_score matches immediately (never sshd - that one waits for the
  # next natural restart or reboot).
  for unit in guardian-collector.service feishu-gateway.service; do
    if systemctl cat "$unit" >/dev/null 2>&1 && systemctl is-active --quiet "$unit"; then
      systemctl try-restart "$unit"
    fi
  done
  echo "oom shields: installed and applied to live collector/gateway (sshd is never restarted)"
else
  echo "3/5 oom shields: skipped"
fi

# --------------------------------------------------------- user balance ----
if want_step user-balance; then
  step "4/5 user@.service OOM balance"
  install -d -o root -g root -m 0755 /etc/systemd/system/user@.service.d
  install -o root -g root -m 0644 "${DEPLOY_DIR}/user-oom-balance.conf" \
    /etc/systemd/system/user@.service.d/guardian-oom-balance.conf
  systemctl daemon-reload
  echo "user balance: drop-in installed (applies at each user session start)"
else
  echo "4/5 user balance: skipped"
fi

# ---------------------------------------------------------------- gateway --
if want_step gateway; then
  step "5/5 Feishu gateway"
  [[ -f "${REPOSITORY}/tools/feishu_alert_gateway.py" ]] || die "gateway script missing in repository"
  [[ -f "${REPOSITORY}/tools/feishu-gateway.service" ]] || die "gateway unit missing in repository"
  # The unit references /opt/.../scripts/feishu_alert_gateway.py; install there.
  install -o root -g root -m 0755 "${REPOSITORY}/tools/feishu_alert_gateway.py" \
    "${install_root}/scripts/feishu_alert_gateway.py"
  install -o root -g root -m 0644 "${REPOSITORY}/tools/feishu-gateway.service" /etc/systemd/system/
  getent passwd guardian >/dev/null 2>&1 || die "guardian user missing - run install-guardian-local.sh --apply first"
  # Journal membership: without it the earlyoom kill watcher silently returns
  # empty summaries (verified 2026-09-24). Not a --skip candidate: the gateway
  # is broken without it, so it is part of the gateway step itself.
  usermod --append --groups systemd-journal guardian
  if [[ -f /etc/guardian/feishu.json ]]; then
    chown root:guardian-shared /etc/guardian/feishu.json
    chmod 0640 /etc/guardian/feishu.json
    systemctl daemon-reload
    systemctl enable feishu-gateway >/dev/null 2>&1 || true
    systemctl restart feishu-gateway
    sleep 1
    systemctl is-active --quiet feishu-gateway || die "feishu-gateway failed to start (check /etc/guardian/feishu.json)"
    echo "gateway: active"
  else
    systemctl daemon-reload
    echo "gateway: unit installed but NOT started - credentials missing"
    echo "  create /etc/guardian/feishu.json (from config/feishu.example.json), then:"
    echo "  chown root:guardian-shared /etc/guardian/feishu.json && chmod 0640 /etc/guardian/feishu.json"
    echo "  systemctl enable --now feishu-gateway"
  fi
else
  echo "5/5 gateway: skipped"
fi

# ------------------------------------------------------------------ smoke --
if [[ "$apply" == true ]]; then
  step "post-setup smoke"
  if [[ -f "${REPOSITORY}/scripts/guardian-smoke" ]]; then
    "${REPOSITORY}/scripts/guardian-smoke" --skip-probes || true
  fi
fi

printf '\nguardian-host-setup: completed\n'
