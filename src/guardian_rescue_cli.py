"""Docker-independent, read-only maintenance diagnostics.

The rescue CLI deliberately reads only host procfs, cgroup v2, statvfs and a
small identity cache written by the read-only Collector. It never talks to
Docker, Guardian Runtime, the Collector socket or an action broker.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Mapping


STATUS_SCHEMA = "guardian.rescue.status.v1"
TOP_SCHEMA = "guardian.rescue.top.v1"
IDENTITY_CACHE_SCHEMA = "guardian.rescue-identities.v1"
DEFAULT_PROC_ROOT = Path("/proc")
DEFAULT_CGROUP_ROOT = Path("/sys/fs/cgroup")
DEFAULT_WORKLOAD_SLICE = "workload.slice"
DEFAULT_IDENTITY_CACHE = Path("/var/lib/guardian/shared/rescue-identities.json")
IDENTITY_MAX_AGE_SECONDS = 60.0
MAX_CGROUPS = 4096
MAX_PIDS_PER_CGROUP = 4096

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


def _read_text(path: Path, *, limit: int = 1_048_576) -> str | None:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return stream.read(limit + 1)[:limit]
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return None


def _read_int(path: Path) -> int | None:
    value = _read_text(path)
    if value is None:
        return None
    try:
        parsed = int(value.strip())
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _parse_key_values(text: str | None) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in (text or "").splitlines():
        key, separator, value = line.partition(" ")
        if separator and key:
            result[key] = value.strip()
    return result


def _parse_meminfo(text: str | None) -> dict[str, int]:
    result: dict[str, int] = {}
    for line in (text or "").splitlines():
        key, separator, raw = line.partition(":")
        if not separator:
            continue
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


def _cpu_count(proc_root: Path) -> int:
    text = _read_text(proc_root / "stat")
    count = sum(1 for line in (text or "").splitlines() if line.startswith("cpu") and line[3:4].isdigit())
    return max(count, 1)


def _load_average(proc_root: Path) -> float | None:
    text = _read_text(proc_root / "loadavg")
    if not text:
        return None
    try:
        return float(text.split()[0])
    except (IndexError, ValueError):
        return None


def _current_cgroup(proc_root: Path) -> str | None:
    text = _read_text(proc_root / "self" / "cgroup")
    for line in (text or "").splitlines():
        hierarchy, separator, relative = line.partition("::")
        if hierarchy == "0" and separator:
            return relative or "/"
    return None


def _memory_snapshot(proc_root: Path) -> dict[str, int | None]:
    values = _parse_meminfo(_read_text(proc_root / "meminfo"))
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    used = total - available if total is not None and available is not None else None
    return {"total_bytes": total, "available_bytes": available, "used_bytes": used}


def _disk_snapshot(mount_point: Path) -> dict[str, int | None | str]:
    try:
        stats = os.statvfs(mount_point)
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError) as exc:
        return {"mount_point": str(mount_point), "total_bytes": None, "available_bytes": None, "error": type(exc).__name__}
    total = stats.f_frsize * stats.f_blocks
    available = stats.f_frsize * stats.f_bavail
    return {"mount_point": str(mount_point), "total_bytes": total, "available_bytes": available, "error": None}


def _unit_state(unit: str, runner: CommandRunner = subprocess.run) -> dict[str, str]:
    try:
        result = runner(
            ["/usr/bin/systemctl", "show", unit, "-p", "ActiveState", "-p", "SubState"],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return {"active": "unavailable", "substate": "unavailable"}
    values: dict[str, str] = {"active": "unknown", "substate": "unknown"}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        if key == "ActiveState":
            values["active"] = value or "unknown"
        elif key == "SubState":
            values["substate"] = value or "unknown"
    if result.returncode != 0:
        values["active"] = "unavailable"
        values["substate"] = "unavailable"
    return values


def _service_snapshot(runner: CommandRunner = subprocess.run) -> dict[str, dict[str, str]]:
    units = (
        "ssh.service",
        "systemd-logind.service",
        "systemd-journald.service",
        "docker.service",
        "containerd.service",
        "guardian-runtime.service",
        "guardian-collector.service",
    )
    unavailable = {unit: {"active": "unavailable", "substate": "unavailable"} for unit in units}
    try:
        result = runner(
            [
                "/usr/bin/systemctl",
                "show",
                *units,
                "-p",
                "Id",
                "-p",
                "ActiveState",
                "-p",
                "SubState",
            ],
            capture_output=True,
            text=True,
            timeout=1.5,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return unavailable
    if result.returncode != 0:
        return unavailable
    current: dict[str, str] = {}
    snapshots: dict[str, dict[str, str]] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if key == "Id":
            if current.get("id") in units:
                snapshots[current["id"]] = {
                    "active": current.get("active", "unknown"),
                    "substate": current.get("substate", "unknown"),
                }
            current = {"id": value}
        elif key == "ActiveState":
            current["active"] = value or "unknown"
        elif key == "SubState":
            current["substate"] = value or "unknown"
    if current.get("id") in units:
        snapshots[current["id"]] = {
            "active": current.get("active", "unknown"),
            "substate": current.get("substate", "unknown"),
        }
    return {unit: snapshots.get(unit, unavailable[unit]) for unit in units}


def status_document(
    *,
    proc_root: Path = DEFAULT_PROC_ROOT,
    cgroup_root: Path = DEFAULT_CGROUP_ROOT,
    mount_point: Path = Path("/"),
    runner: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    """Build a read-only host status document without Docker dependencies."""

    memory = _memory_snapshot(proc_root)
    disk = _disk_snapshot(mount_point)
    workload = cgroup_root / DEFAULT_WORKLOAD_SLICE
    rescue = cgroup_root / "rescue.slice"
    missing = []
    if _read_text(proc_root / "meminfo") is None:
        missing.append("proc_meminfo")
    if not workload.is_dir():
        missing.append("workload_slice")
    if not rescue.is_dir():
        missing.append("rescue_slice")
    return {
        "schema": STATUS_SCHEMA,
        "status": "degraded" if missing else "ok",
        "read_only": True,
        "reason_codes": missing,
        "host": {
            "cpu_count": _cpu_count(proc_root),
            "load1": _load_average(proc_root),
            "memory": memory,
            "disk": disk,
        },
        "maintenance": {
            "current_cgroup": _current_cgroup(proc_root),
            "rescue_slice": {"present": rescue.is_dir(), "path": str(rescue)},
            "workload_slice": {"present": workload.is_dir(), "path": str(workload)},
        },
        "services": _service_snapshot(runner),
        "dependencies": {
            "docker": "not_required",
            "collector": "not_required",
            "guardian_runtime": "not_required",
            "action_broker": "not_used",
        },
    }


def _read_cpu_usage(cgroup: Path) -> int | None:
    values = _parse_key_values(_read_text(cgroup / "cpu.stat"))
    raw = values.get("usage_usec")
    try:
        parsed = int(raw) if raw is not None else None
    except ValueError:
        parsed = None
    return parsed if parsed is not None and parsed >= 0 else None


def _read_pids(cgroup: Path) -> list[int]:
    values: list[int] = []
    for raw in (_read_text(cgroup / "cgroup.procs", limit=64 * 1024) or "").splitlines()[:MAX_PIDS_PER_CGROUP]:
        try:
            pid = int(raw.strip())
        except ValueError:
            continue
        if pid > 0:
            values.append(pid)
    return values


def _memory_limit(cgroup: Path) -> int | str | None:
    value = (_read_text(cgroup / "memory.max") or "").strip()
    if not value:
        return None
    if value == "max":
        return "max"
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _load_identity_cache(path: Path, now: float) -> dict[str, dict[str, Any]]:
    raw = _read_text(path, limit=2 * 1024 * 1024)
    if raw is None:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(value, Mapping) or value.get("schema") != IDENTITY_CACHE_SCHEMA:
        return {}
    result: dict[str, dict[str, Any]] = {}
    entries = value.get("entries")
    if not isinstance(entries, list):
        return result
    for entry in entries[:MAX_CGROUPS]:
        if not isinstance(entry, Mapping):
            continue
        cgroup_path = entry.get("cgroup_path")
        if not isinstance(cgroup_path, str) or not cgroup_path.startswith("/"):
            continue
        observed_at = entry.get("observed_at")
        if not isinstance(observed_at, (int, float)) or isinstance(observed_at, bool):
            continue
        age = now - float(observed_at)
        stable_id = entry.get("stable_id")
        name = entry.get("name")
        result[cgroup_path] = {
            "stable_id": stable_id if isinstance(stable_id, str) and stable_id else None,
            "name": name if isinstance(name, str) and name else None,
            "age_seconds": round(age, 3),
            "fresh": 0 <= age <= IDENTITY_MAX_AGE_SECONDS,
        }
    return result


def _iter_cgroups(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    result: list[Path] = []
    try:
        for current, directories, _files in os.walk(root):
            directories.sort()
            for name in directories:
                path = Path(current) / name
                if len(result) >= MAX_CGROUPS:
                    return result
                if (path / "cgroup.procs").is_file():
                    result.append(path)
    except (PermissionError, OSError):
        return result
    if (root / "cgroup.procs").is_file():
        result.insert(0, root)
    return result[:MAX_CGROUPS]


def _snapshot_objects(root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for cgroup in _iter_cgroups(root):
        pids = _read_pids(cgroup)
        if not pids:
            continue
        try:
            inode = cgroup.stat().st_ino
        except OSError:
            inode = None
        result[str(cgroup)] = {
            "cgroup_path": str(cgroup),
            "cgroup_inode": inode,
            "memory_current_bytes": _read_int(cgroup / "memory.current"),
            "memory_max": _memory_limit(cgroup),
            "cpu_usage_usec": _read_cpu_usage(cgroup),
            "pid_count": len(pids),
        }
    return result


def top_document(
    *,
    proc_root: Path = DEFAULT_PROC_ROOT,
    cgroup_root: Path = DEFAULT_CGROUP_ROOT,
    identity_cache: Path = DEFAULT_IDENTITY_CACHE,
    interval_seconds: float = 0.2,
    limit: int = 10,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Return a bounded top list from workload cgroups only."""

    interval = float(interval_seconds)
    if not 0 <= interval <= 2:
        raise ValueError("interval_seconds_out_of_bounds")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit_out_of_bounds")
    workload = cgroup_root / DEFAULT_WORKLOAD_SLICE
    if not workload.is_dir():
        return {
            "schema": TOP_SCHEMA,
            "status": "unavailable",
            "read_only": True,
            "reason_codes": ["workload_slice_missing"],
            "objects": [],
        }
    first_at = clock()
    first = _snapshot_objects(workload)
    if interval:
        sleep(interval)
    second_at = clock()
    second = _snapshot_objects(workload)
    elapsed = max(second_at - first_at, 0.001)
    identities = _load_identity_cache(identity_cache, time.time())
    objects: list[dict[str, Any]] = []
    for path, current in second.items():
        previous = first.get(path, {})
        usage_delta = None
        if isinstance(current.get("cpu_usage_usec"), int) and isinstance(previous.get("cpu_usage_usec"), int):
            usage_delta = max(current["cpu_usage_usec"] - previous["cpu_usage_usec"], 0)
        cpu_percent = None
        if usage_delta is not None:
            cpu_percent = round(usage_delta / (elapsed * 1_000_000 * _cpu_count(proc_root)) * 100, 2)
        identity = identities.get(path, {})
        fresh = bool(identity.get("fresh"))
        name = identity.get("name") or Path(path).name
        confidence = "high" if identity.get("stable_id") and fresh else "low"
        reasons = []
        if not identity:
            reasons.append("identity_cache_missing")
        elif not fresh:
            reasons.append("identity_cache_stale")
        if not identity.get("stable_id"):
            reasons.append("stable_identity_missing")
        objects.append(
            {
                **current,
                "name": name,
                "stable_id": identity.get("stable_id"),
                "identity_confidence": confidence,
                "identity_age_seconds": identity.get("age_seconds"),
                "actionable": False,
                "reason_codes": reasons,
                "cpu_percent": cpu_percent,
            }
        )
    objects.sort(
        key=lambda item: (
            -(item.get("memory_current_bytes") or 0),
            -(item.get("cpu_percent") or 0),
            str(item.get("cgroup_path")),
        )
    )
    return {
        "schema": TOP_SCHEMA,
        "status": "ok" if objects else "empty",
        "read_only": True,
        "reason_codes": [],
        "interval_seconds": round(elapsed, 3),
        "objects": objects[:limit],
    }


def host_top_document(
    *,
    proc_root: Path = DEFAULT_PROC_ROOT,
    interval_seconds: float = 0.5,
    limit: int = 10,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Host-level process top: the CPU/memory hogs among ALL processes.

    Rationale (T2 finding on the first dedicated test server): a real CPU
    hog - a runaway process, a business bug - never lives in
    workload.slice, so the cgroup-scoped top cannot name it. This view
    samples /proc directly (read-only, two ticks, per-process CPU delta).
    It complements, never replaces, the workload-cgroup top.
    """

    interval = float(interval_seconds)
    if not 0 <= interval <= 2:
        raise ValueError("interval_seconds_out_of_bounds")
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit_out_of_bounds")

    def sample() -> dict[int, dict[str, Any]]:
        procs: dict[int, dict[str, Any]] = {}
        try:
            entries = sorted(
                (p for p in proc_root.iterdir() if p.name.isdigit()),
                key=lambda p: int(p.name),
            )
        except (PermissionError, OSError):
            return procs
        for proc_dir in entries[:8192]:
            stat_text = _read_text(proc_dir / "stat")
            if stat_text is None:
                continue
            # comm can contain spaces/parens: split after the closing paren
            close = stat_text.rfind(")")
            if close < 0:
                continue
            comm = stat_text[stat_text.find("(") + 1 : close]
            fields = stat_text[close + 2 :].split()
            # fields[11] = utime, fields[12] = stime (0-indexed after state)
            if len(fields) < 14:
                continue
            try:
                utime, stime = int(fields[11]), int(fields[12])
            except ValueError:
                continue
            rss_pages = None
            if len(fields) > 21:
                try:
                    rss_pages = int(fields[21])
                except ValueError:
                    rss_pages = None
            try:
                uid_text = (proc_dir / "").owner if False else None
            except Exception:
                uid_text = None
            procs[int(proc_dir.name)] = {
                "name": comm,
                "cpu_ticks": utime + stime,
                "rss_bytes": rss_pages * 4096 if rss_pages is not None else None,
            }
        return procs

    page_size = 4096
    try:
        import os

        page_size = os.sysconf("SC_PAGE_SIZE")
    except (ValueError, OSError, AttributeError):
        pass

    first_at = clock()
    first = sample()
    if interval:
        sleep(interval)
    second_at = clock()
    second = sample()
    elapsed = max(second_at - first_at, 0.001)
    cpu_count = max(_cpu_count(proc_root), 1)

    boot_ticks = None  # ticks since boot are absolute; delta is what matters
    objects: list[dict[str, Any]] = []
    for pid, current in second.items():
        previous = first.get(pid)
        if previous is None:
            continue  # process started mid-sample: cannot delta it
        usage_delta = max(current["cpu_ticks"] - previous["cpu_ticks"], 0)
        cpu_percent = round(usage_delta / (elapsed * _clock_ticks_per_second() * cpu_count) * 100, 2)
        objects.append(
            {
                "pid": pid,
                "name": current["name"],
                "cpu_percent": cpu_percent,
                "memory_current_bytes": current["rss_bytes"],
                "read_only": True,
            }
        )
    objects.sort(
        key=lambda item: (
            -(item.get("cpu_percent") or 0),
            -(item.get("memory_current_bytes") or 0),
            item.get("pid") or 0,
        )
    )
    return {
        "schema": TOP_SCHEMA,
        "mode": "host",
        "status": "ok" if objects else "empty",
        "read_only": True,
        "reason_codes": [],
        "interval_seconds": round(elapsed, 3),
        "objects": objects[:limit],
    }


def _clock_ticks_per_second() -> float:
    import os

    try:
        return float(os.sysconf("SC_CLK_TCK"))
    except (ValueError, OSError, AttributeError):
        return 100.0


def format_status(value: Mapping[str, Any]) -> str:
    host = value.get("host") if isinstance(value.get("host"), Mapping) else {}
    memory = host.get("memory") if isinstance(host.get("memory"), Mapping) else {}
    disk = host.get("disk") if isinstance(host.get("disk"), Mapping) else {}
    maintenance = value.get("maintenance") if isinstance(value.get("maintenance"), Mapping) else {}
    return "\n".join(
        (
            "Guardian rescue status (read-only)",
            f"Status: {value.get('status', 'unknown')}",
            f"CPU: {host.get('cpu_count', 'unknown')} cores, load1={host.get('load1', 'unknown')}",
            f"Memory: available={memory.get('available_bytes', 'unknown')} / total={memory.get('total_bytes', 'unknown')} bytes",
            f"Disk: available={disk.get('available_bytes', 'unknown')} / total={disk.get('total_bytes', 'unknown')} bytes",
            f"Current cgroup: {maintenance.get('current_cgroup', 'unknown')}",
            f"Rescue slice: {maintenance.get('rescue_slice', {}).get('present', False)}",
            f"Workload slice: {maintenance.get('workload_slice', {}).get('present', False)}",
            "Docker/Collector/Runtime: not required for this view",
            "Actions: none (read-only)",
        )
    )


def format_top(value: Mapping[str, Any]) -> str:
    lines = ["Guardian rescue top (read-only)", f"Status: {value.get('status', 'unknown')}"]
    objects = value.get("objects") if isinstance(value.get("objects"), list) else []
    if not objects:
        lines.append("Objects: none")
        return "\n".join(lines)
    for index, item in enumerate(objects, start=1):
        lines.append(
            f"{index}. {item.get('name', 'unknown')} memory={item.get('memory_current_bytes', 'unknown')} "
            f"cpu={item.get('cpu_percent', 'unknown')}% identity={item.get('identity_confidence', 'low')}"
        )
    lines.append("Actions: none (read-only)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Docker-independent Guardian rescue diagnostics")
    subparsers = parser.add_subparsers(dest="command", required=True)
    status_parser = subparsers.add_parser("status", help="show host and maintenance status")
    status_parser.add_argument("--json", action="store_true")
    top_parser = subparsers.add_parser("top", help="show workload cgroup resource top")
    top_parser.add_argument("--json", action="store_true")
    top_parser.add_argument("--limit", type=int, default=10)
    top_parser.add_argument("--interval", type=float, default=0.2)
    top_parser.add_argument(
        "--host",
        action="store_true",
        help="host-level process view: CPU/memory hogs among ALL processes "
        "(a real hog never lives in workload.slice; T2 finding)",
    )
    stop_parser = subparsers.add_parser("stop", help="request one authorized graceful stop")
    stop_parser.add_argument("--target-id", required=True)
    stop_parser.add_argument("--authorization-file", required=True)
    stop_parser.add_argument("--confirm", action="store_true")
    verify_parser = subparsers.add_parser("verify", help="verify one prior rescue stop")
    verify_parser.add_argument("--target-id", required=True)
    verify_parser.add_argument("--wait-seconds", type=float, default=0.0)
    args = parser.parse_args(argv)
    if args.command in {"stop", "verify"}:
        helper = os.environ.get("GUARDIAN_RESCUE_ACTION", "/usr/local/sbin/guardian-rescue-action")
        command = ["sudo", "-n", helper, args.command, "--target-id", args.target_id]
        if args.command == "stop":
            command.extend(["--authorization-file", args.authorization_file])
            if args.confirm:
                command.append("--confirm")
        elif args.wait_seconds:
            command.extend(["--wait-seconds", str(args.wait_seconds)])
        result = subprocess.run(command, check=False)
        return result.returncode
    if args.command == "status":
        value = status_document()
        print(json.dumps(value, ensure_ascii=False, indent=2) if args.json else format_status(value))
    else:
        if args.host:
            value = host_top_document(interval_seconds=args.interval, limit=args.limit)
        else:
            value = top_document(interval_seconds=args.interval, limit=args.limit)
        print(json.dumps(value, ensure_ascii=False, indent=2) if args.json else format_top(value))
    return 0


__all__ = [
    "format_status",
    "format_top",
    "host_top_document",
    "main",
    "status_document",
    "top_document",
]
