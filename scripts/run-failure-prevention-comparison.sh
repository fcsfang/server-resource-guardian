#!/usr/bin/env bash

# Run one side of the real failure-prevention comparison inside the isolated
# Multipass Ubuntu lab. The baseline is intentionally unprotected; the
# Guardian side stops the same disposable leak target before host OOM.
# Required opt-in: GUARDIAN_FAILURE_COMPARISON=1
# Required mode: GUARDIAN_FAILURE_MODE=baseline|guardian

set -Eeuo pipefail

if [[ "${GUARDIAN_FAILURE_COMPARISON:-}" != "1" ]]; then
  echo "Refusing to run: set GUARDIAN_FAILURE_COMPARISON=1." >&2
  exit 2
fi

MODE="${GUARDIAN_FAILURE_MODE:-}"
if [[ "$MODE" != "baseline" && "$MODE" != "guardian" ]]; then
  echo "Refusing to run: set GUARDIAN_FAILURE_MODE=baseline or guardian." >&2
  exit 2
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

IMAGE="${GUARDIAN_TEST_IMAGE:-guardian-test-base:local}"
RUN_ID="${GUARDIAN_FAILURE_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_DIR="${GUARDIAN_FAILURE_OUT:-/tmp/guardian-exp021-${MODE}-${RUN_ID}}"
NAME="guardian-exp021-leaker"
PORT="${GUARDIAN_FAILURE_PORT:-18080}"
SHM_SIZE="${GUARDIAN_FAILURE_SHM_SIZE:-3800m}"
WARNING_AVAILABLE="${GUARDIAN_FAILURE_WARNING_AVAILABLE:-70}"
CRITICAL_AVAILABLE="${GUARDIAN_FAILURE_CRITICAL_AVAILABLE:-60}"

mkdir -p "$OUT_DIR/www"
declare -a CREATED=()
VPID=""

cleanup() {
  local name
  for name in "${CREATED[@]}"; do
    docker rm -f "$name" >/dev/null 2>&1 || true
  done
  if [[ -n "$VPID" ]]; then
    kill "$VPID" >/dev/null 2>&1 || true
    wait "$VPID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Refusing to run: local image not found: $IMAGE" >&2
  exit 3
fi
if [[ "$(docker ps -q | wc -l | tr -d ' ')" != "0" ]]; then
  echo "Refusing to run: existing running containers detected." >&2
  exit 4
fi

printf 'health-ok' > "$OUT_DIR/www/health"
printf 'mode,run_id,warning_available_percent,critical_available_percent,shm_size\n%s,%s,%s,%s,%s\n' \
  "$MODE" "$RUN_ID" "$WARNING_AVAILABLE" "$CRITICAL_AVAILABLE" "$SHM_SIZE" \
  > "$OUT_DIR/metadata.csv"
printf 'unix_time,iteration,mem_available_kb,service_alive,health_response,target_state\n' \
  > "$OUT_DIR/observations.csv"

python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$OUT_DIR/www" \
  > "$OUT_DIR/victim.log" 2>&1 &
VPID=$!
sleep 1
echo 1000 > "/proc/$VPID/oom_score_adj"
printf 'server_pid=%s\nserver_oom_score_adj=%s\n' "$VPID" "$(cat "/proc/$VPID/oom_score_adj")" \
  > "$OUT_DIR/victim-metadata.txt"

# No memory limit is configured for the leak target. --shm-size only gives the
# disposable target enough tmpfs space to reproduce unbounded growth; it is
# not a product resource policy or the Guardian mechanism.
docker run -d \
  --name "$NAME" \
  --shm-size="$SHM_SIZE" \
  --oom-score-adj=-500 \
  "$IMAGE" /bin/busybox sh -c \
  'trap "exit 0" TERM INT; i=0; while [ "$i" -lt 60 ]; do dd if=/dev/zero of=/dev/shm/leak bs=1M count=64 status=none conv=notrunc seek="$((i * 64))"; i=$((i + 1)); sleep 0.25; done; sleep 30' \
  > "$OUT_DIR/container-id.txt" 2> "$OUT_DIR/docker-run.err"
CREATED+=("$NAME")

if [[ "$MODE" == "guardian" ]]; then
  : > "$OUT_DIR/guardian-events.jsonl"
fi

enforce_once() {
  local event_file="$1"
  local target_id="$2"
  local expires_at="$3"
  local auth_file="$OUT_DIR/authorization.json"
  local enforce_rc

  python3 - "$target_id" "$expires_at" "$auth_file" <<'PY'
import json
import sys

target_id, expires_at, output = sys.argv[1:]
with open(output, "w", encoding="utf-8") as stream:
    json.dump(
        {
            "approval_id": "exp021-local-disposable",
            "environment": "local-disposable",
            "target_id": target_id,
            "action": "graceful_stop",
            "expires_at": int(expires_at),
        },
        stream,
    )
    stream.write("\n")
PY

  set +e
  python3 -m src.guardian_enforce \
    --event-file "$event_file" \
    --authorization-file "$auth_file" \
    --allow-action graceful_stop \
    --executor docker \
    --confirm-local-disposable \
    --ledger-file "$OUT_DIR/ledger.json" \
    --recovery-wait 15 \
    --recovery-poll 0.5 \
    --output "$OUT_DIR/audit.json" \
    > "$OUT_DIR/audit.stdout.json" 2> "$OUT_DIR/audit.err"
  enforce_rc=$?
  set -e
  printf '%s\n' "$enforce_rc" > "$OUT_DIR/enforce-returncode.txt"
  return "$enforce_rc"
}

for iteration in $(seq 1 100); do
  now="$(date +%s)"
  service_alive=0
  kill -0 "$VPID" >/dev/null 2>&1 && service_alive=1
  health_response="$(curl -fsS --max-time 1 "http://127.0.0.1:${PORT}/health" 2>/dev/null || echo HEALTH_FAIL)"
  target_state="$(docker inspect --format '{{.State.Status}}/{{.State.ExitCode}}' "$NAME" 2>/dev/null || echo ABSENT)"
  mem_available_kb="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
  printf '%s,%s,%s,%s,%s,%s\n' \
    "$now" "$iteration" "$mem_available_kb" "$service_alive" "$health_response" "$target_state" \
    >> "$OUT_DIR/observations.csv"

  if [[ "$MODE" == "guardian" ]]; then
    event_file="$OUT_DIR/event-${iteration}.json"
    set +e
    python3 -m src.guardian_observer \
      --once \
      --mode enforce \
      --warning-available "$WARNING_AVAILABLE" \
      --critical-available "$CRITICAL_AVAILABLE" \
      --warning-for 0 \
      --critical-for 0 \
      --allow-unprotected \
      --allow-action graceful_stop \
      > "$event_file" 2> "$OUT_DIR/event-${iteration}.err"
    observer_rc=$?
    set -e
    cat "$event_file" >> "$OUT_DIR/guardian-events.jsonl"
    if [[ "$observer_rc" != "0" ]]; then
      printf 'observer_failed_iteration=%s\n' "$iteration" > "$OUT_DIR/failure.txt"
      break
    fi
    guardian_state="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["state"])' "$event_file")"
    guardian_action="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["decision"]["action"])' "$event_file")"
    if [[ "$guardian_state" == "critical" && "$guardian_action" == "graceful_stop" ]]; then
      target_id="$(docker inspect --format '{{.Id}}' "$NAME" | cut -c1-12)"
      expires_at="$(( $(date +%s) + 600 ))"
      set +e
      enforce_once "$event_file" "$target_id" "$expires_at"
      enforce_rc=$?
      set -e
      sleep 2
      post_alive=0
      kill -0 "$VPID" >/dev/null 2>&1 && post_alive=1
      post_health="$(curl -fsS --max-time 1 "http://127.0.0.1:${PORT}/health" 2>/dev/null || echo HEALTH_FAIL)"
      post_state="$(docker inspect --format '{{.State.Status}}/{{.State.ExitCode}}' "$NAME" 2>/dev/null || echo ABSENT)"
      post_available_kb="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
      printf 'enforce_iteration=%s\nenforce_returncode=%s\npost_mem_available_kb=%s\npost_service_alive=%s\npost_health_response=%s\npost_target_state=%s\n' \
        "$iteration" "$enforce_rc" "$post_available_kb" "$post_alive" "$post_health" "$post_state" \
        > "$OUT_DIR/after-action.txt"
      break
    fi
  fi

  if [[ "$service_alive" == "0" || "$target_state" == ABSENT ]]; then
    break
  fi
  sleep 0.5
done

printf 'running_containers=%s\n' "$(docker ps -q | wc -l | tr -d ' ')" > "$OUT_DIR/final.txt"
free -m >> "$OUT_DIR/final.txt"
if command -v journalctl >/dev/null 2>&1; then
  journalctl -k -b --no-pager 2>/dev/null \
    | grep -Ei 'out of memory|oom|killed process' \
    > "$OUT_DIR/kernel-oom.log" || true
fi
printf 'output_dir=%s\n' "$OUT_DIR"
cat "$OUT_DIR/final.txt"
