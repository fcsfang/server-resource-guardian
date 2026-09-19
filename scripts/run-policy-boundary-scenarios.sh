#!/usr/bin/env bash

# Run bounded observe/simulate-only policy boundary scenarios in the local
# Multipass Ubuntu lab. No mutation-capable executor is invoked here.
# Required opt-in: GUARDIAN_POLICY_BOUNDARY=1

set -Eeuo pipefail

if [[ "${GUARDIAN_POLICY_BOUNDARY:-}" != "1" ]]; then
  echo "Refusing to run: set GUARDIAN_POLICY_BOUNDARY=1." >&2
  exit 2
fi

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"
IMAGE="${GUARDIAN_TEST_IMAGE:-guardian-test-base:local}"
RUN_ID="${GUARDIAN_POLICY_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_DIR="${GUARDIAN_POLICY_OUT:-/tmp/guardian-exp022-${RUN_ID}}"
PREFIX="guardian-exp022-${RUN_ID}"
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
  echo "Refusing to run: existing running containers detected." >&2
  exit 4
fi

run_memory_fixture() {
  local name="$1"
  docker run -d \
    --name "$name" \
    --label guardian.exp=022 \
    --label guardian.role=bounded-memory-fixture \
    --shm-size=384m \
    "$IMAGE" /bin/busybox sh -c \
    'dd if=/dev/zero of=/dev/shm/fixture bs=1M count=160 status=none; trap "exit 0" TERM INT; while :; do sleep 60; done' \
    >/dev/null
  CREATED+=("$name")
}

run_cpu_fixture() {
  local name="$1"
  docker run -d \
    --name "$name" \
    --label guardian.exp=022 \
    --label guardian.role=bounded-cpu-fixture \
    --cpus=0.5 \
    "$IMAGE" /bin/busybox sh -c 'while :; do :; done' \
    >/dev/null
  CREATED+=("$name")
}

run_io_fixture() {
  local name="$1"
  docker run -d \
    --name "$name" \
    --label guardian.exp=022 \
    --label guardian.role=bounded-io-fixture \
    --cpus=0.5 \
    "$IMAGE" /bin/busybox sh -c \
    'dd if=/dev/zero of=/tmp/fixture bs=1M count=16 conv=fsync status=none; rm -f /tmp/fixture; sleep 60' \
    >/dev/null
  CREATED+=("$name")
}

run_event() {
  local label="$1"
  shift
  python3 -m src.guardian_observer --once "$@" > "$OUT_DIR/${label}.json"
}

validate_event() {
  local file="$1"
  local expected_state="$2"
  local expected_action="$3"
  local expected_reason="$4"
  python3 - "$file" "$expected_state" "$expected_action" "$expected_reason" <<'PY'
import json
import sys

path, expected_state, expected_action, expected_reason = sys.argv[1:]
event = json.loads(open(path, encoding="utf-8").read())
decision = event["decision"]
reasons = decision["reason_codes"]
if event["state"] != expected_state:
    raise SystemExit(f"{path}: state={event['state']!r}, expected {expected_state!r}")
if decision["action"] != expected_action:
    raise SystemExit(f"{path}: action={decision['action']!r}, expected {expected_action!r}")
if expected_reason not in {"", "none"} and expected_reason not in reasons:
    raise SystemExit(f"{path}: missing reason {expected_reason!r}: {reasons!r}")
if decision["execution"] not in {"not_executed", "not_applicable"}:
    raise SystemExit(f"{path}: mutation unexpectedly planned: {decision['execution']!r}")
print(json.dumps({
    "state": event["state"],
    "action": decision["action"],
    "execution": decision["execution"],
    "reasons": reasons,
    "candidate_count": len(event["object_candidates"]),
}, ensure_ascii=False))
PY
}

# Two simultaneous bounded memory fixtures: simulate must refuse to choose
# between them, and must not call an executor.
run_memory_fixture "${PREFIX}-memory-a"
run_memory_fixture "${PREFIX}-memory-b"
sleep 2
run_event multi-object-simulate \
  --mode simulate \
  --warning-available 100 \
  --critical-available 99 \
  --warning-for 0 \
  --critical-for 0 \
  --allow-unprotected \
  --allow-action graceful_stop
validate_event "$OUT_DIR/multi-object-simulate.json" critical escalate ambiguous_object_identity \
  > "$OUT_DIR/multi-object-summary.json"
cleanup
CREATED=()

# One critical fixture without an unprotected-object policy: simulate must
# refuse the protected object and remain non-mutating.
run_memory_fixture "${PREFIX}-protected"
sleep 2
run_event protected-object-simulate \
  --mode simulate \
  --warning-available 100 \
  --critical-available 99 \
  --warning-for 0 \
  --critical-for 0 \
  --allow-action graceful_stop
validate_event "$OUT_DIR/protected-object-simulate.json" critical escalate protected_object \
  > "$OUT_DIR/protected-object-summary.json"
cleanup
CREATED=()

# Bounded CPU/IO activity without memory pressure: observe must not create a
# destructive action merely because the host is busy.
run_cpu_fixture "${PREFIX}-cpu"
run_io_fixture "${PREFIX}-io"
sleep 2
run_event normal-pressure-observe --mode observe
validate_event "$OUT_DIR/normal-pressure-observe.json" normal none none \
  > "$OUT_DIR/normal-pressure-summary.json"
cleanup
CREATED=()

# Recovery-failure and failure-breaker behavior is tested with a pure fixture;
# no Docker mutation is called. This supplements the previous EXP-017 record.
python3 - "$OUT_DIR/recovery-failure-fixture.json" <<'PY'
import json
import sys
from dataclasses import asdict

from src.guardian_actions import ActionResult, Authorization
from src.guardian_controller import GuardianController
from src.guardian_recovery import RecoveryObservation, RecoveryPolicy


class SuccessfulRuntimeFixture:
    def execute(self, request, now=None):
        return ActionResult(True, request.action, request.target_id, 0, stdout="fixture")


target = "abcdef123456"
event = {
    "event_id": "exp022-recovery-failure",
    "state": "critical",
    "object_candidates": [{"kind": "container", "id": target}],
    "decision": {
        "mode": "enforce",
        "action": "graceful_stop",
        "protected": False,
        "timeout_seconds": 5,
    },
}
authorization = Authorization(
    approval_id="exp022-fixture",
    environment="local-disposable",
    target_id=target,
    action="graceful_stop",
    expires_at=9999999999,
)
observation = RecoveryObservation(target, True, True, "unhealthy", "critical", 2.0, exit_code=1)
controller = GuardianController(SuccessfulRuntimeFixture())
results = []
for now in (100.0, 101.0, 102.0):
    result = controller.enforce(
        event,
        authorization,
        ["graceful_stop"],
        now=now,
        recovery_observation=observation,
        recovery_policy=RecoveryPolicy("graceful_stop"),
        cooldown_seconds=0,
        max_actions=10,
    )
    results.append({
        "state": result.state,
        "recovery": asdict(result.recovery) if result.recovery else None,
        "reason_codes": list(result.reason_codes),
        "failure_breaker_tripped": result.failure_breaker_tripped,
    })
if results[-1]["state"] != "escalated":
    raise SystemExit(results)
json.dump({"scenario": "recovery_failure_and_breaker", "results": results}, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY

printf 'scenario,state,action,execution,reason_or_result,candidate_count\n' > "$OUT_DIR/summary.csv"
python3 - "$OUT_DIR" >> "$OUT_DIR/summary.csv" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
for label in ("multi-object-simulate", "protected-object-simulate", "normal-pressure-observe"):
    event = json.loads((root / f"{label}.json").read_text(encoding="utf-8"))
    decision = event["decision"]
    print("{},{},{},{},{},{}".format(
        label,
        event["state"],
        decision["action"],
        decision["execution"],
        "+".join(decision["reason_codes"]),
        len(event["object_candidates"]),
    ))
fixture = json.loads((root / "recovery-failure-fixture.json").read_text(encoding="utf-8"))
print("recovery-failure-and-breaker,fixture,not_applicable,not_executed," + "+".join(fixture["results"][-1]["reason_codes"]) + ",0")
PY

printf 'output_dir=%s\n' "$OUT_DIR"
cat "$OUT_DIR/summary.csv"
