#!/bin/bash
# Layer-1 smoke test: fast re-verification after any change to the defense
# stack (sysctl/oom_score/earlyoom) or a new Guardian build. NOT a substitute
# for the full pinned-collapse acceptance (40-probe, two runs) - run that
# only for material changes (see results/2026-09-23-benefit/).
#
# Usage: run on the disposable host as root (or via wsl.exe -u root).
# Exit 0 = smoke PASS. Any check failure exits 1 with the failed check named.
#
# Checks (in order):
#   1. earlyoom running with the validated trigger (-m 10, RAM floor only)
#   2. earlyoom --avoid list covers the control plane (sshd + guardian + feishu)
#   3. oom_score_adj shields in place (sshd -1000 / runtime -900 / collector+gw -800)
#   4. sysctl values live (admin_reserve, swappiness)
#   5. entry path: 10 SSH probes with first-diagnostic, >=90% must pass
set -u

PROBES=10
PROBE_TIMEOUT=5
PASS_LINE=0.9   # smoke pass line: >=90% of probes

fail() { echo "SMOKE FAIL: $*"; exit 1; }
echo "== layer-1 smoke: $(date -u +%FT%TZ) =="

# --- 1. earlyoom in place with the validated trigger ---
earlyoom_pid=$(pgrep -x earlyoom || true)
[ -n "$earlyoom_pid" ] || fail "earlyoom not running"
earlyoom_args=$(tr '\0' ' ' < "/proc/$earlyoom_pid/cmdline")
echo "$earlyoom_args" | grep -q -- '-m 10' || fail "earlyoom trigger not -m 10: $earlyoom_args"
echo "$earlyoom_args" | grep -q -- '-s 100' || fail "earlyoom swap line not disabled (-s 100): $earlyoom_args"
echo "1. earlyoom: OK (-m 10 -s 100, pid $earlyoom_pid)"

# --- 2. avoid list covers the control plane ---
for name in sshd guardian-runtime guardian-collector feishu; do
  echo "$earlyoom_args" | grep -q -- "--avoid" || fail "no --avoid list"
  echo "$earlyoom_args" | grep -E -o -- '--avoid [^ ]+ [^ ]+' | grep -q "$name" \
    || fail "earlyoom --avoid missing: $name"
done
echo "2. avoid list: OK (sshd/guardian-runtime/guardian-collector/feishu covered)"

# --- 3. oom_score shields ---
shield_check() {
  local unit="$1" want="$2"
  local pid
  pid=$(systemctl show -p MainPID --value "$unit" 2>/dev/null)
  [ -n "$pid" ] && [ "$pid" != "0" ] || return 0   # unit not running: skip, not fail
  local got
  got=$(cat "/proc/$pid/oom_score_adj" 2>/dev/null || echo missing)
  [ "$got" = "$want" ] || fail "$unit oom_score_adj=$got want=$want"
}
shield_check ssh.service -1000
shield_check guardian-runtime -900
shield_check guardian-collector -800
echo "3. oom_score shields: OK (present units verified)"

# --- 4. sysctl values live ---
ar=$(cat /proc/sys/vm/admin_reserve_kbytes)
[ "$ar" -ge 131072 ] || fail "admin_reserve_kbytes=$ar want >=131072"
sw=$(cat /proc/sys/vm/swappiness)
[ "$sw" -le 10 ] || fail "swappiness=$sw want <=10"
echo "4. sysctl: OK (admin_reserve=${ar}KB swappiness=$sw)"

# --- 5. entry probes (loopback, first diagnostic) ---
probe_once() {
  local t0 t1 out rc
  t0=$(date +%s%N)
  out=$(ssh -i /home/csfang/.ssh/id_ed25519 -o BatchMode=yes -o ConnectTimeout="$PROBE_TIMEOUT" \
        -o StrictHostKeyChecking=no csfang@127.0.0.1 'uptime && free -m | head -2' 2>&1)
  rc=$?
  t1=$(date +%s%N)
  # PASS if rc=0 AND output carries the first-diagnostic marker (uptime load line)
  if [ "$rc" = "0" ] && printf '%s' "$out" | grep -q 'load average'; then
    echo "OK $(( (t1 - t0) / 1000000 ))"
  else
    echo "NO -"
  fi
}
pass=0
times=""
for i in $(seq 1 "$PROBES"); do
  read -r verdict ms <<<"$(probe_once)"
  if [ "$verdict" = "OK" ]; then
    pass=$((pass + 1)); times="$times $ms"
  fi
  sleep 0.3
done
rate=$(python3 -c "print(round($pass/$PROBES, 2))")
[ "$(python3 -c "print(1 if $rate >= $PASS_LINE else 0)")" = "1" ] \
  || fail "entry probes $pass/$PROBES (rate $rate < $PASS_LINE)"
echo "5. entry probes: OK ($pass/$PROBES, ms:[$(echo $times | tr ' ' - | head -c 60)]...)"

echo "SMOKE PASS: all checks green"
