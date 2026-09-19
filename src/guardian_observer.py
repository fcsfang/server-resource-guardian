#!/usr/bin/env python3
"""Read-only Guardian observe prototype.

The module deliberately has no mutation-capable code. It reads host metrics,
cgroup v2 counters and docker stats, then emits JSONL events for later policy
and action layers.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_meminfo(text: str) -> dict[str, int]:
    """Parse /proc/meminfo into byte values where a unit is provided."""

    result: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        fields = raw.split()
        if not fields:
            continue
        try:
            value = int(fields[0])
        except ValueError:
            continue
        if len(fields) > 1 and fields[1].lower() == "kb":
            value *= 1024
        result[key] = value
    return result


def parse_psi(text: str) -> dict[str, dict[str, float]]:
    """Parse one Linux pressure stall information file."""

    result: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        values: dict[str, float] = {}
        for field in fields[1:]:
            if "=" not in field:
                continue
            key, raw = field.split("=", 1)
            try:
                values[key] = float(raw)
            except ValueError:
                continue
        result[fields[0]] = values
    return result


def parse_counter_file(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            result[fields[0]] = int(fields[1])
        except ValueError:
            continue
    return result


def memory_signals(meminfo: dict[str, int]) -> dict[str, float | int | None]:
    total = meminfo.get("MemTotal")
    available = meminfo.get("MemAvailable")
    swap_total = meminfo.get("SwapTotal")
    swap_free = meminfo.get("SwapFree")
    available_ratio = None
    swap_used_ratio = None
    if total:
        available_ratio = round((available or 0) / total * 100, 3)
    if swap_total:
        swap_used_ratio = round((swap_total - (swap_free or 0)) / swap_total * 100, 3)
    return {
        "total_bytes": total,
        "available_bytes": available,
        "available_ratio_percent": available_ratio,
        "swap_total_bytes": swap_total,
        "swap_free_bytes": swap_free,
        "swap_used_ratio_percent": swap_used_ratio,
    }


def read_optional(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return None


def collect_docker_stats(runner: CommandRunner = subprocess.run) -> dict[str, Any]:
    """Read docker stats without invoking any mutation-capable command."""

    command = ["docker", "stats", "--no-stream", "--format", "{{json .}}"]
    try:
        result = runner(command, capture_output=True, text=True, timeout=3, check=False)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "error": str(exc), "containers": []}
    if result.returncode != 0:
        return {
            "available": False,
            "error": (result.stderr or "docker stats failed").strip(),
            "containers": [],
        }
    containers: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        try:
            containers.append(json.loads(line))
        except json.JSONDecodeError:
            containers.append({"raw": line})
    return {"available": True, "containers": containers}


def collect_observation(
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
    runner: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    meminfo_text = read_optional(proc_root / "meminfo") or ""
    psi: dict[str, Any] = {}
    for resource in ("cpu", "memory", "io"):
        content = read_optional(proc_root / "pressure" / resource)
        if content is not None:
            psi[resource] = parse_psi(content)

    events = parse_counter_file(read_optional(cgroup_root / "memory.events") or "")
    observation = {
        "observed_at": utc_now(),
        "memory": memory_signals(parse_meminfo(meminfo_text)),
        "psi": psi,
        "cgroup": {
            "memory_current_bytes": _read_int(cgroup_root / "memory.current"),
            "memory_max": _read_scalar(cgroup_root / "memory.max"),
            "memory_events": events,
            "pids_current": _read_int(cgroup_root / "pids.current"),
            "pids_max": _read_scalar(cgroup_root / "pids.max"),
        },
        "docker": collect_docker_stats(runner),
    }
    return observation


def _read_int(path: Path) -> int | None:
    raw = read_optional(path)
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def _read_scalar(path: Path) -> str | int | None:
    raw = read_optional(path)
    if raw is None:
        return None
    value = raw.strip()
    try:
        return int(value)
    except ValueError:
        return value


def _candidate_state(observation: dict[str, Any], warning_available: float, critical_available: float) -> tuple[str, list[str]]:
    reasons: list[str] = []
    memory = observation["memory"]
    available = memory.get("available_ratio_percent")
    events = observation["cgroup"].get("memory_events", {})
    if isinstance(events, dict) and (events.get("oom") or events.get("oom_kill")):
        reasons.append("cgroup_memory_oom_event")
    if isinstance(available, (int, float)):
        if available <= critical_available:
            reasons.append("host_memory_available_critical")
        elif available <= warning_available:
            reasons.append("host_memory_available_warning")
    if "cgroup_memory_oom_event" in reasons or "host_memory_available_critical" in reasons:
        return "critical", reasons
    if reasons:
        return "warning", reasons
    return "normal", reasons


class RiskEvaluator:
    """Stateful, read-only risk evaluator with de-bounce windows."""

    def __init__(self, warning_for: float = 180.0, critical_for: float = 30.0) -> None:
        self.warning_for = warning_for
        self.critical_for = critical_for
        self._candidate_level = "normal"
        self._candidate_since: float | None = None
        self._last_emitted = "normal"
        self._previous_available: int | None = None
        self._previous_at: float | None = None

    def evaluate(
        self,
        observation: dict[str, Any],
        warning_available: float,
        critical_available: float,
        now: float | None = None,
    ) -> dict[str, Any]:
        timestamp = time.monotonic() if now is None else now
        candidate, reasons = _candidate_state(observation, warning_available, critical_available)
        memory = observation.get("memory", {})
        available = memory.get("available_bytes")
        growth_rate = None
        if (
            isinstance(available, int)
            and self._previous_available is not None
            and self._previous_at is not None
            and timestamp > self._previous_at
        ):
            growth_rate = max((self._previous_available - available) / (timestamp - self._previous_at), 0.0)
        self._previous_available = available if isinstance(available, int) else None
        self._previous_at = timestamp

        if candidate != self._candidate_level:
            self._candidate_level = candidate
            self._candidate_since = timestamp
        if self._candidate_since is None:
            self._candidate_since = timestamp
        candidate_for = max(timestamp - self._candidate_since, 0.0)

        if candidate == "normal":
            state = "recovered" if self._last_emitted in {"warning", "critical"} else "normal"
            required_for = 0.0
        else:
            required_for = self.critical_for if candidate == "critical" else self.warning_for
            state = candidate if candidate_for >= required_for else "normal"

        if state == "recovered":
            self._last_emitted = "normal"
        elif state != "normal":
            self._last_emitted = state

        return {
            "state": state,
            "candidate_state": candidate,
            "candidate_for_seconds": round(candidate_for, 3),
            "required_for_seconds": required_for,
            "reasons": reasons,
            "memory_available_growth_bytes_per_second": growth_rate,
        }


def build_event(
    observation: dict[str, Any],
    warning_available: float = 15.0,
    critical_available: float = 10.0,
    mode: str = "observe",
    evaluator: RiskEvaluator | None = None,
    simulate_action: str = "graceful_stop",
    protected: bool = True,
    allowed_actions: Iterable[str] = (),
) -> dict[str, Any]:
    if evaluator is None:
        candidate, reasons = _candidate_state(observation, warning_available, critical_available)
        risk = {
            "state": candidate,
            "candidate_state": candidate,
            "candidate_for_seconds": None,
            "required_for_seconds": None,
            "reasons": reasons,
            "memory_available_growth_bytes_per_second": None,
        }
    else:
        risk = evaluator.evaluate(observation, warning_available, critical_available)
        candidate = risk["state"]
        reasons = risk["reasons"]
    containers = observation["docker"].get("containers", [])
    candidates = []
    for container in containers:
        if not isinstance(container, dict):
            continue
        candidates.append({
            "kind": "container",
            "id": container.get("ID") or container.get("Container"),
            "name": container.get("Name"),
            "raw": container,
            "confidence": "observed" if container.get("ID") or container.get("Container") else "low",
        })
    decision: dict[str, Any] = {
        "mode": mode,
        "action": "none",
        "reason_codes": reasons,
        "protected": protected,
        "execution": (
            "not_applicable"
            if mode == "observe"
            else "not_executed"
            if mode == "simulate"
            else "pending_controller"
        ),
    }
    if mode in {"simulate", "enforce"}:
        allowed = set(allowed_actions)
        stable_candidates = [
            candidate
            for candidate in candidates
            if isinstance(candidate.get("id"), str) and candidate["id"]
        ]
        if candidate not in {"warning", "critical"}:
            decision["reason_codes"].append("risk_not_actionable")
        elif not stable_candidates:
            decision["action"] = "escalate"
            decision["reason_codes"].append("no_stable_object_identity")
        elif len(stable_candidates) != 1:
            decision["action"] = "escalate"
            decision["reason_codes"].append("ambiguous_object_identity")
        elif protected:
            decision["action"] = "escalate"
            decision["reason_codes"].append("protected_object")
        elif simulate_action not in allowed:
            decision["action"] = "escalate"
            decision["reason_codes"].append("action_not_allowlisted")
        else:
            decision["action"] = simulate_action
            decision["reason_codes"].append(
                "simulate_only" if mode == "simulate" else "enforce_requires_controller"
            )

    return {
        "event_id": str(uuid.uuid4()),
        "observed_at": observation["observed_at"],
        "state": candidate,
        "host_id": os.uname().nodename,
        "signals": observation,
        "risk": risk,
        "object_candidates": candidates,
        "decision": decision,
        "evidence": {"snapshot_path": None, "sample_window": None},
    }


def write_snapshot(event: dict[str, Any], directory: Path) -> str:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{event['event_id']}.json"
    path.write_text(json.dumps(event, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return str(path)


def append_audit(event: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False) + "\n")


def run(args: argparse.Namespace) -> None:
    evaluator = RiskEvaluator(warning_for=args.warning_for, critical_for=args.critical_for)
    while True:
        observation = collect_observation()
        event = build_event(
            observation,
            warning_available=args.warning_available,
            critical_available=args.critical_available,
            mode=args.mode,
            simulate_action=args.simulate_action,
            protected=not args.allow_unprotected,
            allowed_actions=args.allow_action,
            evaluator=evaluator,
        )
        if args.snapshot_dir and (args.snapshot_all or event["state"] in {"warning", "critical", "recovered", "escalated"}):
            event["evidence"]["snapshot_path"] = write_snapshot(event, Path(args.snapshot_dir))
        if args.audit_file:
            append_audit(event, Path(args.audit_file))
        print(json.dumps(event, ensure_ascii=False), flush=True)
        if args.once:
            return
        time.sleep(args.interval)


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Guardian observe prototype")
    parser.add_argument("--once", action="store_true", help="emit one observation and exit")
    parser.add_argument(
        "--mode",
        choices=("observe", "simulate", "enforce"),
        default="observe",
        help="enforce 只生成待控制层接管的计划；真实动作必须另行调用 guardian_enforce",
    )
    parser.add_argument("--interval", type=float, default=5.0, help="sampling interval in seconds")
    parser.add_argument("--warning-available", type=float, default=15.0, help="PoC warning threshold")
    parser.add_argument("--critical-available", type=float, default=10.0, help="PoC critical threshold")
    parser.add_argument("--warning-for", type=float, default=180.0, help="warning persistence window in seconds")
    parser.add_argument("--critical-for", type=float, default=30.0, help="critical persistence window in seconds")
    parser.add_argument("--snapshot-dir", help="optional directory for JSON snapshots")
    parser.add_argument("--snapshot-all", action="store_true", help="snapshot normal observations too")
    parser.add_argument("--audit-file", help="optional JSONL audit file")
    parser.add_argument("--simulate-action", default="graceful_stop", choices=("notify", "snapshot", "graceful_stop", "restart", "terminate", "escalate"))
    parser.add_argument("--allow-action", action="append", default=[], help="action allowed in simulate policy; repeatable")
    parser.add_argument("--allow-unprotected", action="store_true", help="fixture-only switch to simulate a non-protected target")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
