#!/usr/bin/env bash

# Run a bounded, local-only production-like Guardian benchmark inside the
# Multipass Ubuntu lab. This script never discovers or contacts production.
# Required opt-in: GUARDIAN_PRODUCTION_LIKE=1

set -Eeuo pipefail

if [[ "${GUARDIAN_PRODUCTION_LIKE:-}" != "1" ]]; then
  echo "Refusing to run: set GUARDIAN_PRODUCTION_LIKE=1 for the disposable lab." >&2
  exit 2
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${GUARDIAN_TEST_IMAGE:-guardian-test-base:local}"
RUN_ID="${GUARDIAN_BENCHMARK_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_DIR="${GUARDIAN_BENCHMARK_OUT:-/tmp/guardian-exp020-${RUN_ID}}"
PREFIX="guardian-exp020-${RUN_ID}"
cd "$PROJECT_ROOT"
mkdir -p "$OUT_DIR"

declare -a CREATED=()

cleanup() {
  local name
  for name in "${CREATED[@]}"; do
    docker rm -f "$name" >/dev/null 2>&1 || true
  done
}
trap cleanup EXIT INT TERM

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "Refusing to run: local image not found: $IMAGE" >&2
  exit 3
fi

if [[ "$(docker ps -q | wc -l | tr -d ' ')" != "0" ]]; then
  echo "Refusing to run: existing running containers detected; benchmark requires an isolated lab." >&2
  exit 4
fi

now_ms() {
  date +%s%3N
}

snapshot() {
  local label="$1"
  free -b > "$OUT_DIR/${label}-free.txt"
  uptime > "$OUT_DIR/${label}-uptime.txt"
  cat /proc/pressure/cpu > "$OUT_DIR/${label}-psi-cpu.txt"
  cat /proc/pressure/memory > "$OUT_DIR/${label}-psi-memory.txt"
  cat /proc/pressure/io > "$OUT_DIR/${label}-psi-io.txt"
  docker ps --format '{{.ID}},{{.Names}},{{.Status}}' > "$OUT_DIR/${label}-containers.csv"
  docker stats --no-stream --format '{{.ID}},{{.Name}},{{.CPUPerc}},{{.MemUsage}},{{.MemPerc}},{{.PIDs}}' \
    > "$OUT_DIR/${label}-docker-stats.csv" || true
}

run_idle_fleet() {
  local count="$1"
  local index name
  for index in $(seq 1 "$count"); do
    name="${PREFIX}-idle-${index}"
    docker run -d --label "guardian.exp=020" --label "guardian.role=idle" \
      --name "$name" "$IMAGE" /bin/busybox sleep 300 >/dev/null
    CREATED+=("$name")
  done
}

run_memory_target() {
  local name="$1"
  docker run -d --label "guardian.exp=020" --label "guardian.role=memory-target" \
    --name "$name" --memory=900m --shm-size=1g "$IMAGE" /bin/busybox sh -c \
    'dd if=/dev/zero of=/dev/shm/guardian-memory-pressure bs=1M count=700 status=none; trap "exit 0" TERM INT; while :; do sleep 1; done' >/dev/null
  CREATED+=("$name")
}

run_cpu_target() {
  local name="$1"
  docker run -d --label "guardian.exp=020" --label "guardian.role=cpu-target" \
    --cpus=1 --name "$name" "$IMAGE" /bin/busybox sh -c 'while :; do :; done' >/dev/null
  CREATED+=("$name")
}

run_io_target() {
  local name="$1"
  docker run -d --label "guardian.exp=020" --label "guardian.role=io-target" \
    --cpus=0.5 --name "$name" "$IMAGE" /bin/busybox sh -c \
    'for i in 1 2 3 4 5 6 7 8; do dd if=/dev/zero of=/tmp/guardian-io bs=1M count=16 conv=fsync status=none; rm -f /tmp/guardian-io; done; sleep 300' >/dev/null
  CREATED+=("$name")
}

run_pid_target() {
  local name="$1"
  docker run -d --label "guardian.exp=020" --label "guardian.role=pid-target" \
    --pids-limit=200 --name "$name" "$IMAGE" /bin/busybox sh -c \
    'i=0; while [ "$i" -lt 100 ]; do sleep 90 & i=$((i + 1)); done; sleep 90' >/dev/null
  CREATED+=("$name")
}

run_observe() {
  local label="$1"
  shift
  local started ended
  started="$(now_ms)"
  python3 -m src.guardian_observer --once "$@" > "$OUT_DIR/${label}-event.json"
  ended="$(now_ms)"
  printf '%s,%s,%s\n' "$label" "$started" "$ended" >> "$OUT_DIR/timings.csv"
}

printf 'scenario,start_ms,end_ms\n' > "$OUT_DIR/timings.csv"
printf 'scenario,state,action,execution,reasons,candidate_count\n' > "$OUT_DIR/summary.csv"

snapshot baseline
run_observe baseline --mode observe
python3 - "$OUT_DIR/baseline-event.json" >> "$OUT_DIR/summary.csv" <<'PY'
import json
import sys

event = json.load(open(sys.argv[1], encoding="utf-8"))
print("baseline,{},{},{},{},{}".format(
    event["state"], event["decision"]["action"], event["decision"]["execution"],
    "+".join(event["decision"]["reason_codes"]), len(event["object_candidates"]),
))
PY

# A production-like benign fleet: enough objects to exercise Docker stats and
# ambiguity handling, but no unbounded load.
run_idle_fleet 12
sleep 2
snapshot fleet-normal
run_observe fleet-normal --mode observe
python3 - "$OUT_DIR/fleet-normal-event.json" >> "$OUT_DIR/summary.csv" <<'PY'
import json
import sys

event = json.load(open(sys.argv[1], encoding="utf-8"))
print("fleet-normal,{},{},{},{},{}".format(
    event["state"], event["decision"]["action"], event["decision"]["execution"],
    "+".join(event["decision"]["reason_codes"]), len(event["object_candidates"]),
))
PY

# CPU and IO are intentionally bounded. The current first-version risk
# evaluator should not promote normal CPU/IO pressure to a memory action.
run_cpu_target "${PREFIX}-cpu"
run_io_target "${PREFIX}-io"
run_pid_target "${PREFIX}-pid"
sleep 5
snapshot normal-pressure
run_observe normal-pressure --mode observe
python3 - "$OUT_DIR/normal-pressure-event.json" >> "$OUT_DIR/summary.csv" <<'PY'
import json
import sys

event = json.load(open(sys.argv[1], encoding="utf-8"))
print("normal-pressure,{},{},{},{},{}".format(
    event["state"], event["decision"]["action"], event["decision"]["execution"],
    "+".join(event["decision"]["reason_codes"]), len(event["object_candidates"]),
))
PY

# Short-lived containers emulate ordinary deploy/restart churn. They are
# auto-removed by Docker and remain bounded to ten lightweight objects.
for index in $(seq 1 10); do
  name="${PREFIX}-churn-${index}"
  docker run -d --rm --label "guardian.exp=020" --label "guardian.role=churn" \
    --name "$name" "$IMAGE" /bin/busybox sh -c 'sleep 2' >/dev/null
  CREATED+=("$name")
done
sleep 0.5
snapshot churn
run_observe churn --mode observe
python3 - "$OUT_DIR/churn-event.json" >> "$OUT_DIR/summary.csv" <<'PY'
import json
import sys

event = json.load(open(sys.argv[1], encoding="utf-8"))
print("churn,{},{},{},{},{}".format(
    event["state"], event["decision"]["action"], event["decision"]["execution"],
    "+".join(event["decision"]["reason_codes"]), len(event["object_candidates"]),
))
PY

# Remove only the benchmark fleet before the focused single-target comparison.
cleanup
CREATED=()

# Control run: a disposable memory target is observed without Guardian action.
run_memory_target "${PREFIX}-control-memory"
sleep 5
snapshot control-memory
target_id="$(docker inspect --format '{{.Id}}' "${PREFIX}-control-memory" | cut -c1-12)"
running="$(docker inspect --format '{{.State.Running}}' "${PREFIX}-control-memory")"
printf 'control_memory_target,%s,%s\n' "$target_id" "$running" > "$OUT_DIR/control-memory-result.csv"
docker rm -f "${PREFIX}-control-memory" >/dev/null
CREATED=()

# Guardian run: the same bounded target is exposed to an explicit local
# synthetic critical threshold, then passed through observe -> enforce.
run_memory_target "${PREFIX}-guardian-memory"
sleep 5
target_id="$(docker inspect --format '{{.Id}}' "${PREFIX}-guardian-memory" | cut -c1-12)"
run_observe guardian-memory \
  --mode enforce --warning-available 100 --critical-available 99 \
  --warning-for 0 --critical-for 0 --allow-unprotected --allow-action graceful_stop

python3 - "$OUT_DIR/guardian-memory-event.json" "$target_id" <<'PY'
import json
import sys

path, target_id = sys.argv[1:]
event = json.load(open(path, encoding="utf-8"))
if len(event["object_candidates"]) != 1:
    raise SystemExit("focused Guardian run expected exactly one candidate")
if event["object_candidates"][0]["id"] != target_id:
    raise SystemExit("observer target ID mismatch")
if event["decision"]["action"] != "graceful_stop":
    raise SystemExit("focused Guardian run did not produce graceful_stop plan")
PY

expires_at="$(( $(date +%s) + 600 ))"
jq -n --arg target "$target_id" --argjson expires "$expires_at" \
  '{approval_id:"exp020-local-disposable",environment:"local-disposable",target_id:$target,action:"graceful_stop",expires_at:$expires}' \
  > "$OUT_DIR/guardian-authorization.json"

started="$(now_ms)"
python3 -m src.guardian_enforce \
  --event-file "$OUT_DIR/guardian-memory-event.json" \
  --authorization-file "$OUT_DIR/guardian-authorization.json" \
  --allow-action graceful_stop --executor docker --confirm-local-disposable \
  --ledger-file "$OUT_DIR/guardian-ledger.json" --recovery-wait 15 --recovery-poll 0.5 \
  --output "$OUT_DIR/guardian-audit.json" > "$OUT_DIR/guardian-audit.stdout.json"
ended="$(now_ms)"
printf 'guardian_enforce,%s,%s\n' "$started" "$ended" >> "$OUT_DIR/timings.csv"

python3 - "$OUT_DIR/guardian-audit.json" "$OUT_DIR/guardian-memory-result.csv" <<'PY'
import json
import sys

audit_path, result_path = sys.argv[1:]
value = json.load(open(audit_path, encoding="utf-8"))
result = value["result"]
recovery = result.get("recovery") or {}
with open(result_path, "w", encoding="utf-8") as stream:
    stream.write("state,action_result_returncode,recovery_state,recovery_reasons,breaker\n")
    stream.write("{},{},{},{},{}\n".format(
        result["state"],
        (result.get("action_result") or {}).get("returncode"),
        recovery.get("state"),
        "+".join(recovery.get("reason_codes") or []),
        result["failure_breaker_tripped"],
    ))
PY

snapshot final
printf 'output_dir=%s\n' "$OUT_DIR"
cat "$OUT_DIR/summary.csv"
cat "$OUT_DIR/control-memory-result.csv"
cat "$OUT_DIR/guardian-memory-result.csv"
