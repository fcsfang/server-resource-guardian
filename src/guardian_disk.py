"""Read-only disk capacity/inode and block-I/O evidence.

Capacity exhaustion and I/O saturation are deliberately separate resource
channels.  This module only reads procfs, statvfs and cgroup v2 counters; it
never deletes files, changes a mount, applies an I/O limit or executes an
action.  Missing mount/device/ownership evidence is represented explicitly
and makes the corresponding evaluator fail closed.
"""

from __future__ import annotations

import math
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping


StatvfsReader = Callable[[str], Any]


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _unescape_mount_path(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        try:
            return chr(int(match.group(1), 8))
        except ValueError:
            return match.group(0)

    return re.sub(r"\\([0-7]{3})", replace, value)


def parse_mountinfo(text: str) -> list[dict[str, Any]]:
    """Parse Linux ``/proc/*/mountinfo`` records into stable mount facts."""

    result: list[dict[str, Any]] = []
    for line in text.splitlines():
        left, separator, right = line.partition(" - ")
        if not separator:
            continue
        left_fields = left.split()
        right_fields = right.split()
        if len(left_fields) < 6 or len(right_fields) < 3:
            continue
        try:
            mount_id = int(left_fields[0])
        except ValueError:
            continue
        device = left_fields[2]
        if not re.fullmatch(r"\d+:\d+", device):
            device = None
        options = left_fields[5].split(",")
        result.append(
            {
                "mount_id": mount_id,
                "parent_id": left_fields[1],
                "device": device,
                "root": _unescape_mount_path(left_fields[3]),
                "mount_point": _unescape_mount_path(left_fields[4]),
                "options": options,
                "read_only": "ro" in options,
                "filesystem_type": right_fields[0],
                "source": _unescape_mount_path(right_fields[1]),
                "super_options": right_fields[2].split(","),
            }
        )
    return result


def parse_diskstats(text: str) -> dict[str, dict[str, int | str]]:
    """Parse the device counters needed for bounded I/O deltas."""

    result: dict[str, dict[str, int | str]] = {}
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 14:
            continue
        try:
            major = int(fields[0])
            minor = int(fields[1])
            values = [int(value) for value in fields[3:14]]
        except ValueError:
            continue
        if len(values) != 11:
            continue
        result[f"{major}:{minor}"] = {
            "name": fields[2],
            "reads_completed": values[0],
            "reads_merged": values[1],
            "sectors_read": values[2],
            "milliseconds_reading": values[3],
            "writes_completed": values[4],
            "writes_merged": values[5],
            "sectors_written": values[6],
            "milliseconds_writing": values[7],
            "ios_in_progress": values[8],
            "io_ticks_ms": values[9],
            "weighted_io_ms": values[10],
        }
    return result


def parse_io_stat(text: str) -> dict[str, dict[str, int]]:
    """Parse cgroup v2 ``io.stat`` counters keyed by major:minor."""

    result: dict[str, dict[str, int]] = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields or not re.fullmatch(r"\d+:\d+", fields[0]):
            continue
        counters: dict[str, int] = {}
        for field in fields[1:]:
            key, separator, raw = field.partition("=")
            if not separator:
                continue
            try:
                counters[key] = int(raw)
            except ValueError:
                continue
        result[fields[0]] = counters
    return result


def _statvfs_record(value: Any) -> dict[str, Any]:
    block_size = getattr(value, "f_frsize", 0) or getattr(value, "f_bsize", 0)
    blocks = getattr(value, "f_blocks", None)
    available_blocks = getattr(value, "f_bavail", None)
    free_blocks = getattr(value, "f_bfree", None)
    total_inodes = getattr(value, "f_files", None)
    free_inodes = getattr(value, "f_ffree", None)
    if not isinstance(block_size, int) or block_size <= 0 or not isinstance(blocks, int) or blocks < 0:
        raise ValueError("statvfs_block_counts_invalid")
    if not isinstance(available_blocks, int) or available_blocks < 0:
        raise ValueError("statvfs_available_blocks_invalid")
    if not isinstance(free_blocks, int) or free_blocks < 0:
        raise ValueError("statvfs_free_blocks_invalid")
    if not isinstance(total_inodes, int) or total_inodes < 0:
        raise ValueError("statvfs_inode_counts_invalid")
    if not isinstance(free_inodes, int) or free_inodes < 0:
        raise ValueError("statvfs_free_inodes_invalid")
    total_bytes = blocks * block_size
    free_bytes = available_blocks * block_size
    total_inode_ratio = free_inodes / total_inodes * 100 if total_inodes else None
    return {
        "block_size_bytes": block_size,
        "total_bytes": total_bytes,
        "free_bytes": free_bytes,
        "free_ratio_percent": round(free_bytes / total_bytes * 100, 3) if total_bytes else None,
        "total_inodes": total_inodes,
        "free_inodes": free_inodes,
        "free_inode_ratio_percent": round(total_inode_ratio, 3) if total_inode_ratio is not None else None,
        "read_only": bool(getattr(value, "f_flag", 0) & getattr(os, "ST_RDONLY", 1)),
    }


def _mount_for_path(mounts: Iterable[Mapping[str, Any]], configured_path: str) -> Mapping[str, Any] | None:
    requested = configured_path.rstrip("/") or "/"
    matches: list[Mapping[str, Any]] = []
    for mount in mounts:
        mount_point = mount.get("mount_point")
        if not isinstance(mount_point, str):
            continue
        normalized = mount_point.rstrip("/") or "/"
        try:
            if os.path.commonpath([requested, normalized]) == normalized:
                matches.append(mount)
        except ValueError:
            continue
    return max(matches, key=lambda item: len(str(item.get("mount_point", "")))) if matches else None


def _cgroup_io(path: Path) -> dict[str, Any]:
    stat_raw = _read(path / "io.stat")
    pressure_raw = _read(path / "io.pressure")
    flags: list[str] = []
    optional_flags: list[str] = []
    if stat_raw is None:
        optional_flags.append("io_stat_missing")
    if pressure_raw is None:
        optional_flags.append("io_pressure_missing")
    return {
        "path": str(path),
        "io_stat": parse_io_stat(stat_raw or ""),
        "pressure": parse_psi(pressure_raw or ""),
        "quality": {
            "status": "ok" if not flags else "degraded",
            "flags": flags,
            "optional_flags": optional_flags,
        },
    }


def parse_psi(text: str) -> dict[str, dict[str, float]]:
    result: dict[str, dict[str, float]] = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields:
            continue
        values: dict[str, float] = {}
        for field in fields[1:]:
            key, separator, raw = field.partition("=")
            if not separator:
                continue
            try:
                values[key] = float(raw)
            except ValueError:
                continue
        result[fields[0]] = values
    return result


def collect_disk_sample(
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
    mount_points: Iterable[str] = ("/",),
    object_registry: Iterable[Mapping[str, Any]] = (),
    statvfs_reader: StatvfsReader = os.statvfs,
) -> dict[str, Any]:
    """Collect mount capacity/inode and host/cgroup I/O counters read-only."""

    capacity_flags: list[str] = []
    mountinfo_raw = _read(proc_root / "self" / "mountinfo")
    if mountinfo_raw is None:
        # Keep fixture compatibility for small synthetic proc roots.
        mountinfo_raw = _read(proc_root / "mountinfo")
    mountinfo = parse_mountinfo(mountinfo_raw or "")
    if mountinfo_raw is None:
        capacity_flags.append("mountinfo_missing")

    mounts: list[dict[str, Any]] = []
    configured = list(mount_points)
    if not configured:
        capacity_flags.append("mount_points_empty")
    for configured_path in configured:
        row: dict[str, Any] = {
            "configured_path": configured_path,
            "mount_point": None,
            "mount_id": None,
            "device": None,
            "filesystem_type": None,
            "source": None,
            "read_only": None,
            "stats": None,
            "device_stats": None,
            "quality": {"status": "ok", "flags": []},
        }
        mount = _mount_for_path(mountinfo, configured_path)
        if mount is None:
            row["quality"] = {"status": "degraded", "flags": ["mount_metadata_missing"]}
            capacity_flags.append("mount_metadata_missing")
        else:
            row.update(
                {
                    "mount_point": mount.get("mount_point"),
                    "mount_id": mount.get("mount_id"),
                    "device": mount.get("device"),
                    "filesystem_type": mount.get("filesystem_type"),
                    "source": mount.get("source"),
                    "read_only": mount.get("read_only"),
                }
            )
        try:
            stats = _statvfs_record(statvfs_reader(configured_path))
            row["stats"] = stats
            if stats.get("read_only"):
                row["read_only"] = True
                row["quality"]["flags"].append("mount_read_only")
        except (OSError, ValueError, TypeError) as exc:
            row["quality"]["status"] = "degraded"
            row["quality"]["flags"].append("statvfs_missing")
            row["quality"]["error"] = str(exc)
            capacity_flags.append("statvfs_missing")
        mounts.append(row)

    diskstats_raw = _read(proc_root / "diskstats")
    devices = parse_diskstats(diskstats_raw or "")
    io_flags: list[str] = []
    if diskstats_raw is None or not devices:
        io_flags.append("diskstats_missing")
    pressure_raw = _read(proc_root / "pressure" / "io")
    pressure = parse_psi(pressure_raw or "")
    if pressure_raw is None or not pressure:
        io_flags.append("io_pressure_missing")

    for row in mounts:
        device = row.get("device")
        if isinstance(device, str):
            row["device_stats"] = devices.get(device)
            if row["device_stats"] is None:
                row["quality"]["flags"].append("device_stats_missing")
        elif row["mount_point"] is not None:
            row["quality"]["flags"].append("mount_device_missing")

    root_cgroup = _cgroup_io(cgroup_root)
    objects: list[dict[str, Any]] = []
    for item in object_registry:
        if not isinstance(item, Mapping):
            continue
        raw_path = item.get("cgroup_path")
        object_quality: list[str] = []
        if not isinstance(raw_path, str) or not raw_path:
            object_quality.append("object_cgroup_path_missing")
            object_cgroup = {"io_stat": {}, "pressure": {}, "path": None}
        else:
            object_cgroup = _cgroup_io(Path(raw_path))
            object_quality.extend(object_cgroup["quality"].get("flags", []))
            object_quality.extend(object_cgroup["quality"].get("optional_flags", []))
        objects.append(
            {
                "kind": item.get("kind", "container"),
                "id": item.get("id"),
                "name": item.get("name"),
                "cgroup_path": object_cgroup.get("path"),
                "mapping_confidence": item.get("mapping_confidence", "low"),
                "mapping_errors": list(item.get("mapping_errors", [])) if isinstance(item.get("mapping_errors"), list) else [],
                "io_stat": object_cgroup.get("io_stat", {}),
                "pressure": object_cgroup.get("pressure", {}),
                "quality": {"status": "ok" if not object_quality else "degraded", "flags": object_quality},
            }
        )

    capacity_status = "ok" if not capacity_flags else "degraded"
    io_status = "ok" if not io_flags else "degraded"
    return {
        "observed_monotonic_ns": time.monotonic_ns(),
        "mounts": mounts,
        "capacity_quality": {"status": capacity_status, "flags": sorted(set(capacity_flags))},
        "io": {
            "psi": pressure,
            "devices": devices,
            "cgroup": root_cgroup,
            "objects": objects,
            "quality": {"status": io_status, "flags": sorted(set(io_flags))},
        },
        "quality": {
            "status": "ok" if capacity_status == "ok" and io_status == "ok" else "degraded",
            "flags": sorted(set(capacity_flags + io_flags)),
        },
    }


@dataclass(frozen=True)
class DiskCapacityPolicy:
    warning_free_percent: float = 15.0
    critical_free_percent: float = 5.0
    warning_inode_free_percent: float = 10.0
    critical_inode_free_percent: float = 5.0
    warning_time_to_full_seconds: float = 86_400.0
    critical_time_to_full_seconds: float = 3_600.0
    warning_for_seconds: float = 180.0
    critical_for_seconds: float = 30.0
    required_samples: int = 2
    max_sample_age_seconds: float = 15.0
    min_object_contribution_percent: float = 20.0
    min_object_lead_margin: float = 0.15


@dataclass(frozen=True)
class IoPolicy:
    warning_psi_some_avg10: float = 1.0
    critical_psi_full_avg10: float = 0.5
    warning_device_utilization_percent: float = 70.0
    critical_device_utilization_percent: float = 90.0
    warning_average_latency_ms: float = 100.0
    critical_average_latency_ms: float = 500.0
    warning_for_seconds: float = 30.0
    critical_for_seconds: float = 10.0
    required_samples: int = 2
    max_sample_age_seconds: float = 15.0
    min_object_contribution_percent: float = 20.0
    min_object_lead_margin: float = 0.15


def disk_capacity_policy_from_config(config: Any) -> DiskCapacityPolicy:
    values = getattr(config, "disk_capacity_policy", None)
    defaults = DiskCapacityPolicy()
    if not isinstance(values, Mapping):
        return defaults
    kwargs: dict[str, Any] = {}
    for name in defaults.__dataclass_fields__:
        value = values.get(name)
        if value is not None:
            kwargs[name] = value
    return DiskCapacityPolicy(**kwargs)


def io_policy_from_config(config: Any) -> IoPolicy:
    values = getattr(config, "io_policy", None)
    defaults = IoPolicy()
    if not isinstance(values, Mapping):
        return defaults
    kwargs: dict[str, Any] = {}
    for name in defaults.__dataclass_fields__:
        value = values.get(name)
        if value is not None:
            kwargs[name] = value
    return IoPolicy(**kwargs)


def disk_mount_points_from_config(config: Any) -> tuple[str, ...]:
    values = getattr(config, "disk_mount_points", None)
    if isinstance(values, (list, tuple)) and values:
        return tuple(str(value) for value in values)
    return ("/",)


def _state_result(
    *,
    candidate: str,
    candidate_since: float | None,
    timestamp: float,
    required_for: float,
    quality_flags: list[str],
    sample_count: int,
    last_emitted: str,
) -> tuple[str, str, float]:
    candidate_for = max(timestamp - (candidate_since if candidate_since is not None else timestamp), 0.0)
    if quality_flags:
        state = "degraded_observability"
    elif candidate == "normal":
        state = "recovered" if last_emitted in {"warning", "critical"} else "normal"
    else:
        state = candidate if candidate_for >= required_for else "normal"
    return state, candidate, candidate_for


def _timestamp_quality(
    sample: Mapping[str, Any],
    previous_ns: int | None,
    max_age_seconds: float,
) -> tuple[int | None, float | None, list[str]]:
    observed_ns = sample.get("observed_monotonic_ns")
    flags: list[str] = []
    if not isinstance(observed_ns, int) or isinstance(observed_ns, bool):
        flags.append("disk_monotonic_timestamp_missing")
        return None, None, flags
    elapsed = None
    if previous_ns is not None:
        elapsed = (observed_ns - previous_ns) / 1_000_000_000
        if elapsed <= 0:
            flags.append("disk_monotonic_clock_not_increasing")
        elif elapsed > max_age_seconds:
            flags.append("disk_sample_gap_too_large")
    return observed_ns, elapsed, flags


class DiskCapacityRiskEvaluator:
    """Evaluate mount free-space/inode headroom with an independent dwell."""

    def __init__(self, policy: DiskCapacityPolicy | None = None) -> None:
        self.policy = policy or DiskCapacityPolicy()
        self._previous_mounts: dict[str, Mapping[str, Any]] = {}
        self._previous_ns: int | None = None
        self._candidate_level = "normal"
        self._candidate_since: float | None = None
        self._last_emitted = "normal"
        self._sample_count = 0

    def evaluate(self, sample: Mapping[str, Any], *, now: float | None = None) -> dict[str, Any]:
        timestamp = time.monotonic() if now is None else now
        self._sample_count += 1
        quality_flags: list[str] = []
        quality = sample.get("capacity_quality") if isinstance(sample.get("capacity_quality"), Mapping) else {}
        quality_flags.extend(str(flag) for flag in quality.get("flags", []) if flag)
        observed_ns, elapsed, timestamp_flags = _timestamp_quality(
            sample,
            self._previous_ns,
            self.policy.max_sample_age_seconds,
        )
        quality_flags.extend(timestamp_flags)
        mounts = sample.get("mounts") if isinstance(sample.get("mounts"), list) else []
        if not mounts:
            quality_flags.append("capacity_mounts_empty")

        mount_results: list[dict[str, Any]] = []
        warning_signals: list[str] = []
        critical_support: list[str] = []
        reasons: list[str] = []
        current_mounts: dict[str, Mapping[str, Any]] = {}
        for mount in mounts:
            if not isinstance(mount, Mapping):
                quality_flags.append("capacity_mount_record_invalid")
                continue
            identity = "|".join(str(mount.get(key) or "") for key in ("mount_id", "device", "mount_point", "configured_path"))
            current_mounts[identity] = mount
            row = dict(mount)
            stats = mount.get("stats") if isinstance(mount.get("stats"), Mapping) else None
            mount_quality = mount.get("quality") if isinstance(mount.get("quality"), Mapping) else {}
            if stats is None:
                quality_flags.append("capacity_mount_statvfs_missing")
                row["free_bytes_per_second"] = None
                row["time_to_full_seconds"] = None
                mount_results.append(row)
                continue
            for key in ("free_ratio_percent", "free_inode_ratio_percent", "free_bytes", "free_inodes"):
                if stats.get(key) is None:
                    quality_flags.append(f"capacity_{key}_missing")
            previous = self._previous_mounts.get(identity)
            free_rate = None
            time_to_full = None
            if previous is not None and elapsed is not None and elapsed > 0:
                previous_stats = previous.get("stats") if isinstance(previous.get("stats"), Mapping) else {}
                previous_free = previous_stats.get("free_bytes")
                current_free = stats.get("free_bytes")
                if isinstance(previous_free, int) and isinstance(current_free, int):
                    free_rate = max((previous_free - current_free) / elapsed, 0.0)
                    if free_rate > 0:
                        time_to_full = current_free / free_rate
            row["free_bytes_per_second"] = round(free_rate, 3) if free_rate is not None else None
            row["time_to_full_seconds"] = round(time_to_full, 3) if time_to_full is not None else None
            if mount_quality.get("flags"):
                quality_flags.extend(str(flag) for flag in mount_quality["flags"] if flag)
            free_ratio = _number(stats.get("free_ratio_percent"))
            inode_ratio = _number(stats.get("free_inode_ratio_percent"))
            if free_ratio is not None:
                if free_ratio <= self.policy.critical_free_percent:
                    critical_support.append("disk_capacity_free_critical")
                    reasons.append("disk_capacity_free_critical")
                elif free_ratio <= self.policy.warning_free_percent:
                    warning_signals.append("disk_capacity_free_warning")
                    reasons.append("disk_capacity_free_warning")
            if inode_ratio is not None:
                if inode_ratio <= self.policy.critical_inode_free_percent:
                    critical_support.append("disk_inode_free_critical")
                    reasons.append("disk_inode_free_critical")
                elif inode_ratio <= self.policy.warning_inode_free_percent:
                    warning_signals.append("disk_inode_free_warning")
                    reasons.append("disk_inode_free_warning")
            if time_to_full is not None:
                if time_to_full <= self.policy.critical_time_to_full_seconds:
                    critical_support.append("disk_capacity_time_to_full_critical")
                    reasons.append("disk_capacity_time_to_full_critical")
                elif time_to_full <= self.policy.warning_time_to_full_seconds:
                    warning_signals.append("disk_capacity_time_to_full_warning")
                    reasons.append("disk_capacity_time_to_full_warning")
            mount_results.append(row)

        candidate = "normal"
        if "disk_capacity_time_to_full_critical" in critical_support or len(critical_support) >= 2:
            candidate = "critical"
        elif warning_signals or critical_support:
            candidate = "warning"
        if self._sample_count < self.policy.required_samples:
            quality_flags.append("insufficient_samples")
            reasons.append("insufficient_samples")
        if candidate != self._candidate_level:
            self._candidate_level = candidate
            self._candidate_since = timestamp
        if self._candidate_since is None:
            self._candidate_since = timestamp
        required_for = self.policy.critical_for_seconds if candidate == "critical" else self.policy.warning_for_seconds if candidate == "warning" else 0.0
        state, _, candidate_for = _state_result(
            candidate=candidate,
            candidate_since=self._candidate_since,
            timestamp=timestamp,
            required_for=required_for,
            quality_flags=quality_flags,
            sample_count=self._sample_count,
            last_emitted=self._last_emitted,
        )
        if state == "recovered":
            self._last_emitted = "normal"
        elif state in {"warning", "critical"}:
            self._last_emitted = state
        self._previous_mounts = current_mounts
        self._previous_ns = observed_ns if observed_ns is not None else self._previous_ns
        return {
            "resource_kind": "disk_capacity",
            "state": state,
            "candidate_state": candidate,
            "candidate_for_seconds": round(candidate_for, 3),
            "required_for_seconds": required_for,
            "reasons": sorted(set(reasons)),
            "quality_flags": sorted(set(quality_flags)),
            "quality_status": "ok" if not quality_flags else "degraded",
            "sample_count": self._sample_count,
            "mounts": mount_results,
            "warning_signals": sorted(set(warning_signals)),
            "critical_support": sorted(set(critical_support)),
        }


def _device_deltas(
    current: Mapping[str, Any],
    previous: Mapping[str, Any],
    elapsed: float | None,
) -> tuple[dict[str, Any], list[str]]:
    flags: list[str] = []
    result = dict(current)
    if elapsed is None or elapsed <= 0:
        return result, ["io_device_elapsed_missing"]
    counter_fields = (
        "reads_completed",
        "writes_completed",
        "sectors_read",
        "sectors_written",
        "io_ticks_ms",
        "weighted_io_ms",
    )
    deltas: dict[str, int] = {}
    for field in counter_fields:
        value = current.get(field)
        old = previous.get(field)
        if not isinstance(value, int) or not isinstance(old, int):
            flags.append(f"io_device_{field}_missing")
            continue
        if value < old:
            flags.append("io_device_counter_reset")
            continue
        deltas[field] = value - old
    result["deltas"] = deltas
    ticks = deltas.get("io_ticks_ms")
    operations = deltas.get("reads_completed", 0) + deltas.get("writes_completed", 0)
    if ticks is not None:
        result["utilization_percent"] = round(min(max(ticks / (elapsed * 1000) * 100, 0.0), 100.0), 3)
    else:
        result["utilization_percent"] = None
    result["throughput_bytes_per_second"] = round(
        (deltas.get("sectors_read", 0) + deltas.get("sectors_written", 0)) * 512 / elapsed,
        3,
    )
    result["average_latency_ms"] = round(deltas.get("weighted_io_ms", 0) / operations, 3) if operations else None
    return result, flags


class IoRiskEvaluator:
    """Evaluate block-I/O pressure separately from filesystem capacity."""

    def __init__(self, policy: IoPolicy | None = None) -> None:
        self.policy = policy or IoPolicy()
        self._previous_devices: Mapping[str, Mapping[str, Any]] = {}
        self._previous_ns: int | None = None
        self._candidate_level = "normal"
        self._candidate_since: float | None = None
        self._last_emitted = "normal"
        self._sample_count = 0

    def evaluate(self, sample: Mapping[str, Any], *, now: float | None = None) -> dict[str, Any]:
        timestamp = time.monotonic() if now is None else now
        self._sample_count += 1
        io = sample.get("io") if isinstance(sample.get("io"), Mapping) else {}
        quality = io.get("quality") if isinstance(io.get("quality"), Mapping) else {}
        quality_flags = [str(flag) for flag in quality.get("flags", []) if flag]
        observed_ns, elapsed, timestamp_flags = _timestamp_quality(sample, self._previous_ns, self.policy.max_sample_age_seconds)
        quality_flags.extend(timestamp_flags)
        devices = io.get("devices") if isinstance(io.get("devices"), Mapping) else {}
        if not devices:
            quality_flags.append("io_devices_empty")
        device_results: dict[str, dict[str, Any]] = {}
        device_flags: list[str] = []
        max_utilization: float | None = None
        max_latency: float | None = None
        for device, current in devices.items():
            if not isinstance(current, Mapping):
                quality_flags.append("io_device_record_invalid")
                continue
            previous = self._previous_devices.get(str(device))
            if previous is None:
                result = dict(current)
                result["utilization_percent"] = None
                result["average_latency_ms"] = None
                result["throughput_bytes_per_second"] = None
                result["deltas"] = {}
            else:
                result, flags = _device_deltas(current, previous, elapsed)
                device_flags.extend(flags)
            device_results[str(device)] = result
            utilization = _number(result.get("utilization_percent"))
            latency = _number(result.get("average_latency_ms"))
            if utilization is not None and (max_utilization is None or utilization > max_utilization):
                max_utilization = utilization
            if latency is not None and (max_latency is None or latency > max_latency):
                max_latency = latency
        quality_flags.extend(device_flags)

        psi = io.get("psi") if isinstance(io.get("psi"), Mapping) else {}
        some = _number(psi.get("some", {}).get("avg10")) if isinstance(psi.get("some"), Mapping) else None
        full = _number(psi.get("full", {}).get("avg10")) if isinstance(psi.get("full"), Mapping) else None
        if some is None or full is None:
            quality_flags.append("io_psi_avg10_missing")
        warning_signals: list[str] = []
        critical_support: list[str] = []
        reasons: list[str] = []
        if some is not None and some >= self.policy.warning_psi_some_avg10:
            warning_signals.append("io_psi_some_warning")
            reasons.append("io_psi_some_warning")
        if full is not None and full >= self.policy.critical_psi_full_avg10:
            critical_support.append("io_psi_full_critical")
            reasons.append("io_psi_full_critical")
        if max_utilization is not None:
            if max_utilization >= self.policy.critical_device_utilization_percent:
                critical_support.append("io_device_utilization_critical")
                reasons.append("io_device_utilization_critical")
            elif max_utilization >= self.policy.warning_device_utilization_percent:
                warning_signals.append("io_device_utilization_warning")
                reasons.append("io_device_utilization_warning")
        if max_latency is not None:
            if max_latency >= self.policy.critical_average_latency_ms:
                critical_support.append("io_average_latency_critical")
                reasons.append("io_average_latency_critical")
            elif max_latency >= self.policy.warning_average_latency_ms:
                warning_signals.append("io_average_latency_warning")
                reasons.append("io_average_latency_warning")

        candidate = "normal"
        if "io_psi_full_critical" in critical_support or len(critical_support) >= 2:
            candidate = "critical"
        elif warning_signals or critical_support:
            candidate = "warning"
        if self._sample_count < self.policy.required_samples:
            quality_flags.append("insufficient_samples")
            reasons.append("insufficient_samples")
        if candidate != self._candidate_level:
            self._candidate_level = candidate
            self._candidate_since = timestamp
        if self._candidate_since is None:
            self._candidate_since = timestamp
        required_for = self.policy.critical_for_seconds if candidate == "critical" else self.policy.warning_for_seconds if candidate == "warning" else 0.0
        state, _, candidate_for = _state_result(
            candidate=candidate,
            candidate_since=self._candidate_since,
            timestamp=timestamp,
            required_for=required_for,
            quality_flags=quality_flags,
            sample_count=self._sample_count,
            last_emitted=self._last_emitted,
        )
        if state == "recovered":
            self._last_emitted = "normal"
        elif state in {"warning", "critical"}:
            self._last_emitted = state
        self._previous_devices = {str(key): value for key, value in devices.items() if isinstance(value, Mapping)}
        self._previous_ns = observed_ns if observed_ns is not None else self._previous_ns
        return {
            "resource_kind": "io",
            "state": state,
            "candidate_state": candidate,
            "candidate_for_seconds": round(candidate_for, 3),
            "required_for_seconds": required_for,
            "reasons": sorted(set(reasons)),
            "quality_flags": sorted(set(quality_flags)),
            "quality_status": "ok" if not quality_flags else "degraded",
            "sample_count": self._sample_count,
            "io_psi_some_avg10": some,
            "io_psi_full_avg10": full,
            "max_device_utilization_percent": max_utilization,
            "max_average_latency_ms": max_latency,
            "devices": device_results,
            "warning_signals": sorted(set(warning_signals)),
            "critical_support": sorted(set(critical_support)),
        }


def _io_bytes(io_stat: Mapping[str, Any]) -> int:
    total = 0
    for device_values in io_stat.values():
        if not isinstance(device_values, Mapping):
            continue
        for key in ("rbytes", "wbytes", "dbytes"):
            value = device_values.get(key)
            if isinstance(value, int) and value >= 0:
                total += value
    return total


class DiskIoAttributionEvaluator:
    """Attribute I/O counter growth to one stable cgroup, or abandon."""

    def __init__(self, policy: IoPolicy | None = None) -> None:
        self.policy = policy or IoPolicy()
        self._previous_objects: dict[str, Mapping[str, Any]] = {}
        self._previous_ns: int | None = None

    def evaluate(self, sample: Mapping[str, Any], *, now: float | None = None) -> dict[str, Any]:
        timestamp = time.monotonic() if now is None else now
        io = sample.get("io") if isinstance(sample.get("io"), Mapping) else {}
        objects = io.get("objects") if isinstance(io.get("objects"), list) else []
        quality_flags: list[str] = []
        reasons: list[str] = []
        observed_ns = sample.get("observed_monotonic_ns")
        if not isinstance(observed_ns, int) or isinstance(observed_ns, bool):
            quality_flags.append("io_attribution_timestamp_missing")
        elapsed = None
        if self._previous_ns is not None and isinstance(observed_ns, int):
            elapsed = (observed_ns - self._previous_ns) / 1_000_000_000
            if elapsed <= 0:
                quality_flags.append("io_attribution_clock_not_increasing")
            elif elapsed > self.policy.max_sample_age_seconds:
                quality_flags.append("io_attribution_sample_gap_too_large")
        if not objects:
            reasons.append("io_object_registry_empty")
        candidates: list[dict[str, Any]] = []
        positive_deltas: list[int] = []
        identity_blockers: list[str] = []
        for item in objects:
            if not isinstance(item, Mapping):
                quality_flags.append("io_object_record_invalid")
                continue
            stable_id = item.get("id")
            path = item.get("cgroup_path")
            if not isinstance(stable_id, str) or not stable_id:
                reasons.append("io_stable_object_identity_missing")
                continue
            previous = self._previous_objects.get(stable_id)
            if previous and previous.get("cgroup_path") and path and previous.get("cgroup_path") != path:
                identity_blockers.append("io_cgroup_path_changed")
            current_bytes = _io_bytes(item.get("io_stat") if isinstance(item.get("io_stat"), Mapping) else {})
            previous_bytes = _io_bytes(previous.get("io_stat") if isinstance(previous, Mapping) and isinstance(previous.get("io_stat"), Mapping) else {}) if previous else None
            delta = None
            if previous_bytes is not None and elapsed is not None and elapsed > 0:
                if current_bytes < previous_bytes:
                    quality_flags.append("io_object_counter_reset")
                else:
                    delta = current_bytes - previous_bytes
                    if delta > 0:
                        positive_deltas.append(delta)
            confidence = "high" if item.get("mapping_confidence") == "high" and isinstance(path, str) and path else "low"
            item_quality = item.get("quality") if isinstance(item.get("quality"), Mapping) else {}
            if item_quality.get("flags"):
                quality_flags.extend(str(flag) for flag in item_quality["flags"] if flag)
                confidence = "low"
            candidates.append(
                {
                    "kind": item.get("kind", "container"),
                    "id": stable_id,
                    "name": item.get("name"),
                    "cgroup_path": path,
                    "io_bytes_delta": delta,
                    "confidence": confidence,
                    "mapping_errors": list(item.get("mapping_errors", [])) if isinstance(item.get("mapping_errors"), list) else [],
                }
            )
        total_delta = sum(positive_deltas)
        for item in candidates:
            delta = item.get("io_bytes_delta")
            if isinstance(delta, int) and total_delta > 0:
                item["io_contribution_percent"] = round(delta / total_delta * 100, 3)
            else:
                item["io_contribution_percent"] = None
        candidates.sort(key=lambda item: -(item.get("io_contribution_percent") or 0))
        valid = [
            item
            for item in candidates
            if item.get("confidence") == "high"
            and isinstance(item.get("io_contribution_percent"), (int, float))
            and item["io_contribution_percent"] >= self.policy.min_object_contribution_percent
        ]
        target = None
        state = "NO_TARGET"
        if quality_flags or not self._previous_objects:
            state = "DEGRADED_OBSERVABILITY"
            if not self._previous_objects:
                reasons.append("io_object_baseline_established")
        elif identity_blockers:
            state = "AMBIGUOUS_TARGET"
            reasons.extend(identity_blockers)
        elif not valid:
            reasons.append("io_no_candidate_meets_confidence_or_contribution")
        elif len(valid) > 1:
            margin = (valid[0].get("io_contribution_percent") or 0) - (valid[1].get("io_contribution_percent") or 0)
            if margin < self.policy.min_object_lead_margin * 100:
                state = "AMBIGUOUS_TARGET"
                reasons.append("io_candidate_lead_margin_too_small")
            else:
                state = "TARGET_CONFIRMED"
                target = valid[0]
                reasons.append("io_single_target_exceeds_contribution_and_margin")
        else:
            state = "TARGET_CONFIRMED"
            target = valid[0]
            reasons.append("io_single_target_exceeds_contribution_and_margin")
        self._previous_objects = {str(item.get("id")): item for item in objects if isinstance(item, Mapping) and item.get("id")}
        self._previous_ns = observed_ns if isinstance(observed_ns, int) else self._previous_ns
        return {
            "resource_kind": "io",
            "state": state,
            "target": target,
            "candidates": candidates,
            "reason_codes": sorted(set(reasons)),
            "quality_flags": sorted(set(quality_flags)),
            "sample_elapsed_seconds": elapsed,
        }


class CapacityAttributionEvaluator:
    """Refuse capacity ownership claims without an explicit writer contract."""

    def evaluate(self, sample: Mapping[str, Any], *, now: float | None = None) -> dict[str, Any]:
        quality = sample.get("capacity_quality") if isinstance(sample.get("capacity_quality"), Mapping) else {}
        flags = [str(flag) for flag in quality.get("flags", []) if flag]
        reasons = ["capacity_writer_ownership_unavailable"]
        state = "DEGRADED_OBSERVABILITY" if not flags else "DEGRADED_OBSERVABILITY"
        return {
            "resource_kind": "disk_capacity",
            "state": state,
            "target": None,
            "candidates": [],
            "reason_codes": sorted(set(reasons)),
            "quality_flags": sorted(set(flags)),
        }


def build_disk_simulation_decision(
    risk: Mapping[str, Any],
    attribution: Mapping[str, Any],
    *,
    resource_kind: str,
    mode: str,
    simulate_action: str,
    protected: bool,
    allowed_actions: Iterable[str],
) -> dict[str, Any]:
    """Create a disk plan only; this helper never performs an action."""

    prefix = resource_kind
    decision: dict[str, Any] = {
        "mode": mode,
        "resource_kind": resource_kind,
        "action": "none",
        "execution": "not_applicable" if mode == "observe" else "not_executed",
        "reason_codes": [],
    }
    risk_state = risk.get("state")
    if risk_state == "degraded_observability":
        decision["reason_codes"].append(f"{prefix}_observability_degraded")
        if mode != "observe":
            decision["action"] = "escalate"
        return decision
    if risk_state not in {"warning", "critical"}:
        decision["reason_codes"].append(f"{prefix}_risk_not_actionable")
        return decision
    if mode == "observe":
        decision["reason_codes"].append("observe_only")
        return decision
    if attribution.get("state") != "TARGET_CONFIRMED":
        decision["action"] = "escalate"
        decision["reason_codes"].append(f"{prefix}_attribution_not_confirmed")
    elif protected:
        decision["action"] = "escalate"
        decision["reason_codes"].append("protected_object")
    elif simulate_action not in set(allowed_actions):
        decision["action"] = "escalate"
        decision["reason_codes"].append("action_not_allowlisted")
    else:
        decision["action"] = simulate_action
        decision["reason_codes"].append(f"{prefix}_simulate_only")
    return decision


__all__ = [
    "CapacityAttributionEvaluator",
    "DiskCapacityPolicy",
    "DiskCapacityRiskEvaluator",
    "DiskIoAttributionEvaluator",
    "IoPolicy",
    "IoRiskEvaluator",
    "build_disk_simulation_decision",
    "collect_disk_sample",
    "disk_capacity_policy_from_config",
    "disk_mount_points_from_config",
    "io_policy_from_config",
    "parse_diskstats",
    "parse_io_stat",
    "parse_mountinfo",
]
