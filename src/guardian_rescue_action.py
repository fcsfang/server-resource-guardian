"""Bounded manual rescue action and verification boundary.

This module is called only by the root-owned ``guardian-rescue-action``
helper.  It accepts one explicitly authorized target, never selects a target,
never escalates TERM to KILL, and records enough evidence for a later verify.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import signal
import subprocess
import time
import uuid
import math
from pathlib import Path
from typing import Any, Mapping

from .guardian_actions import Authorization, CONTAINER_ID, DockerActionAdapter
from .guardian_config import ConfigError, load_config
from .guardian_emergency_shedding import emergency_shedding_policy_from_config
from .guardian_state import GuardianStateStore, StateStoreError


FULL_ID = re.compile(r"^[0-9a-f]{64}$")
DEFAULT_CONFIG = Path("/etc/guardian/guardian.json")
DEFAULT_CGROUP = Path("/sys/fs/cgroup")
DEFAULT_IDENTITY_CACHE = Path("/var/lib/guardian/shared/rescue-identities.json")
DEFAULT_STATE = Path("/var/lib/guardian/shared/state.db")
DEFAULT_AUDIT = Path("/var/lib/guardian/runtime/audit/rescue-actions.jsonl")
MAX_AUTH_BYTES = 16 * 1024


class RescueActionError(RuntimeError):
    """Raised when a rescue request must fail closed."""


def _json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RescueActionError("json_unavailable") from exc
    if not isinstance(value, Mapping):
        raise RescueActionError("json_mapping_required")
    return value


def _authorization(path: Path, *, target_id: str, now: float) -> Authorization:
    try:
        stat = path.stat()
    except OSError as exc:
        raise RescueActionError("authorization_file_unavailable") from exc
    if stat.st_uid != 0 or stat.st_mode & 0o077:
        raise RescueActionError("authorization_file_permissions_invalid")
    if stat.st_size > MAX_AUTH_BYTES:
        raise RescueActionError("authorization_file_too_large")
    value = _json(path)
    try:
        authorization = Authorization(
            approval_id=str(value["approval_id"]),
            environment=str(value["environment"]),
            target_id=str(value["target_id"]),
            action=str(value["action"]),
            expires_at=float(value["expires_at"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RescueActionError("authorization_invalid") from exc
    if authorization.environment != "local-disposable" or authorization.action != "graceful_stop":
        raise RescueActionError("authorization_scope_invalid")
    if authorization.target_id != target_id or authorization.expires_at <= now:
        raise RescueActionError("authorization_target_or_expiry_invalid")
    return authorization


def _host_snapshot(proc_root: Path) -> dict[str, int | float | None]:
    available = None
    try:
        meminfo = (proc_root / "meminfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        meminfo = ""
    for line in meminfo.splitlines():
        if line.startswith("MemAvailable:"):
            try:
                available = int(line.split()[1]) * 1024
            except (IndexError, ValueError):
                pass
            break
    psi = None
    try:
        for line in (proc_root / "pressure" / "memory").read_text(encoding="utf-8").splitlines():
            if line.startswith("full "):
                for field in line.split()[1:]:
                    if field.startswith("avg10="):
                        psi = float(field.split("=", 1)[1])
                        break
    except (OSError, ValueError):
        pass
    return {"available_bytes": available, "memory_full_avg10": psi}


def _identity(cache_path: Path, target_id: str, now: float, cgroup_root: Path) -> Mapping[str, Any]:
    value = _json(cache_path)
    if value.get("schema") != "guardian.rescue-identities.v1":
        raise RescueActionError("identity_cache_schema_invalid")
    entries = value.get("entries")
    if not isinstance(entries, list):
        raise RescueActionError("identity_cache_entries_invalid")
    matches = [item for item in entries if isinstance(item, Mapping) and item.get("stable_id") == target_id]
    if len(matches) != 1:
        raise RescueActionError("target_identity_missing_or_ambiguous")
    item = matches[0]
    observed = item.get("observed_at")
    if not isinstance(observed, (int, float)) or now - float(observed) > 60 or now < float(observed):
        raise RescueActionError("target_identity_stale")
    path = item.get("cgroup_path")
    expected_prefix = str(cgroup_root / "workload.slice") + "/"
    if not isinstance(path, str) or not path.startswith(expected_prefix):
        raise RescueActionError("target_outside_workload_slice")
    return item


def _pids(cgroup_path: Path) -> list[int]:
    try:
        values = cgroup_path.joinpath("cgroup.procs").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RescueActionError("target_cgroup_unavailable") from exc
    result = []
    for value in values:
        try:
            pid = int(value)
        except ValueError:
            continue
        if pid > 1:
            result.append(pid)
    return result[:4096]


def _cgroup_memory_snapshot(cgroup_path: Path) -> dict[str, Any]:
    """Read bounded cgroup memory evidence without changing the cgroup."""

    result: dict[str, Any] = {}
    for name in ("memory.current", "memory.high", "memory.max"):
        try:
            raw = cgroup_path.joinpath(name).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if raw == "max":
            result[name.replace("memory.", "")] = "max"
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if value >= 0:
            result[name.replace("memory.", "")] = value
    try:
        events: dict[str, int] = {}
        for line in cgroup_path.joinpath("memory.events").read_text(encoding="utf-8").splitlines():
            key, separator, raw = line.partition(" ")
            if separator:
                try:
                    value = int(raw.strip())
                except ValueError:
                    continue
                if value >= 0:
                    events[key] = value
        result["events"] = events
    except OSError:
        pass
    current = result.get("current")
    high = result.get("high")
    result["above_high"] = isinstance(current, int) and isinstance(high, int) and current >= high
    return result


def _wait_for_exit(
    cgroup_path: Path,
    *,
    wait_seconds: float,
    sleep: Any = time.sleep,
) -> tuple[list[int], float]:
    """Wait only for natural exit; never sends another signal."""

    if not math.isfinite(wait_seconds) or not 0 <= wait_seconds <= 120:
        raise RescueActionError("verify_wait_seconds_out_of_bounds")
    started = time.monotonic()
    while True:
        if not cgroup_path.is_dir():
            return [], time.monotonic() - started
        pids = _pids(cgroup_path)
        if not pids:
            return [], time.monotonic() - started
        elapsed = time.monotonic() - started
        if elapsed >= wait_seconds:
            return pids, elapsed
        sleep(min(0.2, wait_seconds - elapsed))


def _append_audit(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _policy_entry(config: Any, target_id: str) -> Mapping[str, Any]:
    policy = emergency_shedding_policy_from_config(config)
    if not config.actions_enabled or "graceful_stop" not in config.allowed_actions:
        raise RescueActionError("manual_actions_disabled")
    for item in policy.actionable_set:
        identifier = item.get("stable_id") or item.get("container_id") or item.get("id")
        if identifier == target_id:
            if item.get("environment") != "local-disposable" or item.get("action") != "graceful_stop":
                break
            raw_expiry = item.get("expires_at")
            try:
                expiry = dt.datetime.fromisoformat(str(raw_expiry).replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError, OverflowError):
                raise RescueActionError("target_allowlist_expiry_invalid")
            if expiry <= time.time():
                raise RescueActionError("target_allowlist_expired")
            return item
    raise RescueActionError("target_not_allowlisted")


def stop(
    *,
    target_id: str,
    authorization_file: Path,
    confirm: bool,
    config_path: Path = DEFAULT_CONFIG,
    cgroup_root: Path = DEFAULT_CGROUP,
    identity_cache: Path = DEFAULT_IDENTITY_CACHE,
    state_path: Path = DEFAULT_STATE,
    audit_path: Path = DEFAULT_AUDIT,
    runner: Any = subprocess.run,
    now: float | None = None,
) -> dict[str, Any]:
    if not confirm:
        raise RescueActionError("explicit_confirmation_required")
    if not FULL_ID.fullmatch(target_id):
        raise RescueActionError("target_full_id_required")
    timestamp = time.time() if now is None else float(now)
    try:
        config = load_config(config_path)
    except (ConfigError, OSError) as exc:
        raise RescueActionError("config_invalid") from exc
    _policy_entry(config, target_id)
    authorization = _authorization(authorization_file, target_id=target_id, now=timestamp)
    identity = _identity(identity_cache, target_id, timestamp, cgroup_root)
    cgroup_path = Path(str(identity["cgroup_path"]))
    if not cgroup_path.is_dir() or not str(cgroup_path).startswith(str(cgroup_root / "workload.slice")):
        raise RescueActionError("target_cgroup_invalid")
    expected_inode = identity.get("cgroup_inode")
    if not isinstance(expected_inode, int) or cgroup_path.stat().st_ino != expected_inode:
        raise RescueActionError("target_cgroup_changed")
    pids = _pids(cgroup_path)
    if not pids:
        raise RescueActionError("target_not_running")
    state = GuardianStateStore(state_path, file_mode=0o660)
    try:
        state.register_capability(authorization)
        consumed = state.consume_capability(authorization, event_id=f"rescue-{uuid.uuid4()}", now=timestamp)
    except StateStoreError as exc:
        raise RescueActionError("authorization_state_unavailable") from exc
    if not consumed.allowed:
        raise RescueActionError(consumed.reason)
    before = _host_snapshot(Path("/proc"))
    try:
        docker_result = runner(["docker", "kill", "--signal", "TERM", target_id], capture_output=True, text=True, timeout=10, check=False)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        docker_result = None
    method = "docker"
    if docker_result is None or docker_result.returncode != 0:
        try:
            if cgroup_path.stat().st_ino != expected_inode:
                raise RescueActionError("target_cgroup_changed")
        except OSError as exc:
            raise RescueActionError("target_cgroup_unavailable") from exc
        method = "cgroup"
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                continue
            except PermissionError as exc:
                raise RescueActionError("target_signal_denied") from exc
    record = {
        "schema": "guardian.rescue.action.v1",
        "incident_id": f"rescue-{uuid.uuid4()}",
        "target_id": target_id,
        "action": "graceful_stop",
        "method": method,
        "target_cgroup_path": str(cgroup_path),
        "target_cgroup_inode": cgroup_path.stat().st_ino,
        "before": before,
        "requested_at": timestamp,
        "authorization": authorization.approval_id,
    }
    _append_audit(audit_path, record)
    return {**record, "execution": "term_sent", "pids_signaled": len(pids) if method == "cgroup" else None}


def verify(
    *,
    target_id: str,
    audit_path: Path = DEFAULT_AUDIT,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = DEFAULT_CGROUP,
    wait_seconds: float = 0.0,
    sleep: Any = time.sleep,
) -> dict[str, Any]:
    if not FULL_ID.fullmatch(target_id):
        raise RescueActionError("target_full_id_required")
    records = []
    try:
        for line in audit_path.read_text(encoding="utf-8").splitlines():
            value = json.loads(line)
            if isinstance(value, Mapping) and value.get("target_id") == target_id and value.get("action") == "graceful_stop":
                records.append(value)
    except (OSError, ValueError) as exc:
        raise RescueActionError("rescue_audit_unavailable") from exc
    if not records:
        raise RescueActionError("rescue_action_not_found")
    latest = records[-1]
    cgroup_path = Path(str(latest.get("target_cgroup_path") or ""))
    pids, waited_seconds = _wait_for_exit(cgroup_path, wait_seconds=wait_seconds, sleep=sleep)
    cgroup_memory = _cgroup_memory_snapshot(cgroup_path) if cgroup_path.is_dir() else {"absent": True}
    after = _host_snapshot(proc_root)
    before = latest.get("before") if isinstance(latest.get("before"), Mapping) else {}
    available_improved = isinstance(before.get("available_bytes"), int) and isinstance(after.get("available_bytes"), int) and after["available_bytes"] > before["available_bytes"]
    psi_improved = isinstance(before.get("memory_full_avg10"), (int, float)) and isinstance(after.get("memory_full_avg10"), (int, float)) and after["memory_full_avg10"] < before["memory_full_avg10"]
    recovered = not pids and (available_improved or psi_improved)
    reason_codes = []
    if not pids and (available_improved or psi_improved):
        reason_codes.append("target_stopped_and_memory_improved")
    else:
        reason_codes.append("target_or_host_recovery_pending")
    if cgroup_memory.get("above_high"):
        reason_codes.append("target_above_memory_high")
    if pids and wait_seconds:
        reason_codes.append("term_wait_timeout_no_escalation")
    result = {
        "schema": "guardian.rescue.verify.v1",
        "target_id": target_id,
        "state": "recovered" if recovered else "pending",
        "target_running": bool(pids),
        "before": dict(before),
        "after": after,
        "target_cgroup_memory": cgroup_memory,
        "cleanup": {
            "wait_seconds": wait_seconds,
            "waited_seconds": round(waited_seconds, 3),
            "remaining_pids": pids,
            "escalation": "none",
        },
        "reason_codes": reason_codes,
    }
    _append_audit(audit_path, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guardian bounded manual rescue action")
    parser.add_argument("command", choices=("stop", "verify"))
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--authorization-file")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--wait-seconds", type=float, default=0.0)
    args = parser.parse_args(argv)
    try:
        if args.command == "stop":
            if not args.authorization_file:
                raise RescueActionError("authorization_file_required")
            result = stop(target_id=args.target_id, authorization_file=Path(args.authorization_file), confirm=args.confirm)
        else:
            result = verify(target_id=args.target_id, wait_seconds=args.wait_seconds)
    except RescueActionError as exc:
        print(json.dumps({"schema": "guardian.rescue.action.v1", "state": "blocked", "reason_codes": [str(exc)]}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("state") in {"recovered", "pending"} or result.get("execution") == "term_sent" else 2


__all__ = ["RescueActionError", "main", "stop", "verify"]
