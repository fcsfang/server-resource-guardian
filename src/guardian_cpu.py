"""Read-only CPU sampling, persistence and cgroup attribution.

CPU utilization alone is not an incident: a batch can use all CPUs while the
host remains responsive.  The evaluator therefore keeps utilization, CPU PSI,
load and cgroup throttling as separate explainable signals, applies a dwell
window, and never produces an executable action.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


_CPU_FIELDS = (
    "user",
    "nice",
    "system",
    "idle",
    "iowait",
    "irq",
    "softirq",
    "steal",
    "guest",
    "guest_nice",
)


def collect_scheduler_probe(requested_sleep_ms: float = 1.0) -> dict[str, Any]:
    """Measure a tiny scheduler hand-off without invoking a shell command."""

    if requested_sleep_ms <= 0:
        return {"valid": False, "error": "requested_sleep_must_be_positive"}
    started_ns = time.monotonic_ns()
    try:
        time.sleep(requested_sleep_ms / 1000.0)
    except (OSError, OverflowError, ValueError) as exc:
        return {"valid": False, "error": str(exc)}
    elapsed_ms = (time.monotonic_ns() - started_ns) / 1_000_000
    return {
        "valid": True,
        "requested_sleep_ms": requested_sleep_ms,
        "elapsed_ms": round(elapsed_ms, 3),
        "overshoot_ms": round(max(elapsed_ms - requested_sleep_ms, 0.0), 3),
    }


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, PermissionError, OSError):
        return None


def _read_int(path: Path) -> int | None:
    raw = _read(path)
    if raw is None:
        return None
    try:
        return int(raw.strip())
    except ValueError:
        return None


def parse_proc_stat(text: str) -> dict[str, Any]:
    """Parse aggregate and per-CPU counters from ``/proc/stat``."""

    cpus: dict[str, dict[str, int]] = {}
    for line in text.splitlines():
        fields = line.split()
        if not fields or (fields[0] != "cpu" and not fields[0].startswith("cpu")):
            continue
        if fields[0] != "cpu" and not fields[0][3:].isdigit():
            continue
        values: dict[str, int] = {}
        for name, raw in zip(_CPU_FIELDS, fields[1:]):
            try:
                values[name] = int(raw)
            except ValueError:
                values[name] = 0
        if not values:
            continue
        values["total_ticks"] = sum(values.values())
        values["idle_ticks"] = values.get("idle", 0) + values.get("iowait", 0)
        cpus[fields[0]] = values
    aggregate = cpus.get("cpu", {})
    logical_cpus = len([name for name in cpus if name != "cpu"])
    return {
        "aggregate": aggregate,
        "logical_cpus": logical_cpus,
        "valid": bool(aggregate),
    }


def parse_loadavg(text: str) -> dict[str, Any]:
    fields = text.split()
    load1: float | None = None
    runnable: int | None = None
    total_tasks: int | None = None
    last_pid: int | None = None
    if fields:
        try:
            load1 = float(fields[0])
        except ValueError:
            pass
    if len(fields) > 3 and "/" in fields[3]:
        left, right = fields[3].split("/", 1)
        try:
            runnable = int(left)
            total_tasks = int(right)
        except ValueError:
            pass
    if len(fields) > 4:
        try:
            last_pid = int(fields[4])
        except ValueError:
            pass
    return {
        "load1": load1,
        "runnable_tasks": runnable,
        "total_tasks": total_tasks,
        "last_pid": last_pid,
        "valid": load1 is not None,
    }


def parse_cpu_psi(text: str) -> dict[str, dict[str, float]]:
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


def parse_cpu_stat(text: str) -> dict[str, int]:
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


def parse_cpu_max(text: str) -> dict[str, int | None]:
    fields = text.split()
    if len(fields) != 2:
        return {"quota_us": None, "period_us": None, "valid": False}
    quota: int | None
    try:
        quota = None if fields[0] == "max" else int(fields[0])
        period = int(fields[1])
    except ValueError:
        return {"quota_us": None, "period_us": None, "valid": False}
    return {"quota_us": quota, "period_us": period, "valid": period > 0}


def _cgroup_cpu(path: Path) -> dict[str, Any]:
    stat_raw = _read(path / "cpu.stat")
    max_raw = _read(path / "cpu.max")
    psi_raw = _read(path / "cpu.pressure")
    flags: list[str] = []
    optional_flags: list[str] = []
    if stat_raw is None:
        flags.append("cpu_stat_missing")
    if max_raw is None:
        # A leaf cgroup may not expose an explicit quota even though CPU
        # accounting and PSI are available. This is evidence about limits,
        # not by itself an observation failure.
        optional_flags.append("cpu_max_missing")
    if psi_raw is None:
        flags.append("cpu_pressure_missing")
    return {
        "path": str(path),
        "cpu_stat": parse_cpu_stat(stat_raw or ""),
        "cpu_max": parse_cpu_max(max_raw or ""),
        "psi": parse_cpu_psi(psi_raw or ""),
        "quality": {
            "status": "ok" if not flags else "degraded",
            "flags": flags,
            "optional_flags": optional_flags,
        },
    }


def collect_cpu_sample(
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
    object_registry: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Collect one read-only CPU sample and per-object cgroup counters."""

    flags: list[str] = []
    stat_raw = _read(proc_root / "stat")
    loadavg_raw = _read(proc_root / "loadavg")
    pressure_raw = _read(proc_root / "pressure" / "cpu")
    stat = parse_proc_stat(stat_raw or "")
    loadavg = parse_loadavg(loadavg_raw or "")
    pressure = parse_cpu_psi(pressure_raw or "")
    if not stat["valid"]:
        flags.append("proc_stat_missing")
    if not loadavg["valid"]:
        flags.append("loadavg_missing")
    if not pressure:
        flags.append("cpu_pressure_missing")

    objects: list[dict[str, Any]] = []
    for item in object_registry:
        if not isinstance(item, Mapping):
            flags.append("cpu_object_record_invalid")
            continue
        raw_path = item.get("cgroup_path")
        object_flags: list[str] = []
        if not isinstance(raw_path, str) or not raw_path:
            object_flags.append("object_cgroup_path_missing")
            cgroup = {
                "path": None,
                "cpu_stat": {},
                "cpu_max": {"quota_us": None, "period_us": None, "valid": False},
                "psi": {},
            }
        else:
            cgroup = _cgroup_cpu(Path(raw_path))
            object_flags.extend(cgroup["quality"]["flags"])
        objects.append(
            {
                "kind": item.get("kind", "container"),
                "id": item.get("id"),
                "name": item.get("name"),
                "cgroup_path": cgroup.get("path"),
                "mapping_confidence": item.get("mapping_confidence", "low"),
                "mapping_errors": list(item.get("mapping_errors", [])) if isinstance(item.get("mapping_errors"), list) else [],
                "cpu_stat": cgroup["cpu_stat"],
                "cpu_max": cgroup["cpu_max"],
                "psi": cgroup["psi"],
                "quality": {"status": "ok" if not object_flags else "degraded", "flags": object_flags},
            }
        )

    cgroup = _cgroup_cpu(cgroup_root)
    flags.extend(cgroup["quality"]["flags"])
    scheduler_probe = collect_scheduler_probe()
    if not scheduler_probe.get("valid"):
        flags.append("scheduler_probe_failed")
    return {
        "observed_monotonic_ns": time.monotonic_ns(),
        "host": {
            "stat": stat,
            "loadavg": loadavg,
            "logical_cpus": stat["logical_cpus"] or os.cpu_count() or 1,
        },
        "psi": pressure,
        "cgroup": cgroup,
        "scheduler_probe": scheduler_probe,
        "objects": objects,
        "quality": {"status": "ok" if not flags else "degraded", "flags": sorted(set(flags))},
    }


@dataclass(frozen=True)
class CpuPolicy:
    warning_utilization_percent: float = 85.0
    critical_utilization_percent: float = 95.0
    warning_psi_some_avg10: float = 1.0
    critical_psi_full_avg10: float = 0.5
    warning_scheduler_delay_ms: float = 250.0
    critical_scheduler_delay_ms: float = 1000.0
    warning_for_seconds: float = 30.0
    critical_for_seconds: float = 10.0
    required_samples: int = 2
    max_sample_age_seconds: float = 15.0
    min_object_contribution_percent: float = 20.0
    min_object_lead_margin: float = 0.15


def cpu_policy_from_config(config: Any) -> CpuPolicy:
    """Read optional CPU policy fields, retaining safe defaults for old configs."""

    values = getattr(config, "cpu_policy", None)
    if not isinstance(values, Mapping):
        return CpuPolicy()
    defaults = CpuPolicy()
    kwargs: dict[str, Any] = {}
    for name in defaults.__dataclass_fields__:
        value = values.get(name)
        kwargs[name] = value if value is not None else getattr(defaults, name)
    return CpuPolicy(**kwargs)


def _psi_value(sample: Mapping[str, Any], stall: str, key: str) -> float | None:
    psi = sample.get("psi") if isinstance(sample.get("psi"), Mapping) else {}
    section = psi.get(stall) if isinstance(psi, Mapping) else None
    return _number(section.get(key)) if isinstance(section, Mapping) else None


class CpuRiskEvaluator:
    """Evaluate sustained host CPU pressure without authorizing an action."""

    def __init__(self, policy: CpuPolicy | None = None) -> None:
        self.policy = policy or CpuPolicy()
        self._previous_stat: Mapping[str, Any] | None = None
        self._previous_ns: int | None = None
        self._candidate_level = "normal"
        self._candidate_since: float | None = None
        self._last_emitted = "normal"
        self._sample_count = 0
        self._previous_cgroup_stat: Mapping[str, Any] = {}

    def evaluate(self, sample: Mapping[str, Any], *, now: float | None = None) -> dict[str, Any]:
        timestamp = time.monotonic() if now is None else now
        self._sample_count += 1
        reasons: list[str] = []
        quality_flags: list[str] = []
        quality = sample.get("quality")
        if isinstance(quality, Mapping):
            raw_flags = quality.get("flags", [])
            if isinstance(raw_flags, list):
                quality_flags.extend(str(flag) for flag in raw_flags if flag)
            else:
                quality_flags.append("cpu_quality_flags_invalid")
        else:
            quality_flags.append("cpu_quality_missing")

        host = sample.get("host") if isinstance(sample.get("host"), Mapping) else {}
        stat_wrapper = host.get("stat") if isinstance(host, Mapping) else {}
        current_stat = stat_wrapper.get("aggregate") if isinstance(stat_wrapper, Mapping) else {}
        observed_ns = sample.get("observed_monotonic_ns")
        if not isinstance(observed_ns, int) or isinstance(observed_ns, bool):
            quality_flags.append("cpu_monotonic_timestamp_missing")
            observed_ns = None
        elapsed = None
        if self._previous_ns is not None and observed_ns is not None:
            elapsed = (observed_ns - self._previous_ns) / 1_000_000_000
            if elapsed <= 0:
                quality_flags.append("cpu_monotonic_clock_not_increasing")
            elif elapsed > self.policy.max_sample_age_seconds:
                quality_flags.append("cpu_sample_gap_too_large")
        self._previous_ns = observed_ns if observed_ns is not None else self._previous_ns

        utilization: float | None = None
        previous = self._previous_stat
        if previous is None:
            reasons.append("cpu_counter_baseline_established")
        elif elapsed is None or elapsed <= 0:
            quality_flags.append("cpu_elapsed_missing")
        else:
            total_delta = int(current_stat.get("total_ticks", 0)) - int(previous.get("total_ticks", 0))
            idle_delta = int(current_stat.get("idle_ticks", 0)) - int(previous.get("idle_ticks", 0))
            if total_delta <= 0 or idle_delta < 0:
                quality_flags.append("cpu_counter_reset_or_invalid")
            else:
                utilization = round(max(min((total_delta - idle_delta) / total_delta * 100, 100.0), 0.0), 3)
        self._previous_stat = current_stat if isinstance(current_stat, Mapping) else None

        logical_cpus = host.get("logical_cpus")
        if not isinstance(logical_cpus, int) or logical_cpus <= 0:
            quality_flags.append("logical_cpu_count_missing")
            logical_cpus = 1
        loadavg = host.get("loadavg") if isinstance(host.get("loadavg"), Mapping) else {}
        load1 = _number(loadavg.get("load1"))
        if load1 is None:
            quality_flags.append("load1_missing")
        psi_some = _psi_value(sample, "some", "avg10")
        psi_full = _psi_value(sample, "full", "avg10")
        if psi_some is None or psi_full is None:
            quality_flags.append("cpu_psi_avg10_missing")

        warning_signals: list[str] = []
        critical_support: list[str] = []
        if utilization is not None:
            if utilization >= self.policy.critical_utilization_percent:
                critical_support.append("cpu_utilization_critical")
                reasons.append("cpu_utilization_critical")
            elif utilization >= self.policy.warning_utilization_percent:
                warning_signals.append("cpu_utilization_warning")
                reasons.append("cpu_utilization_warning")
        if psi_some is not None and psi_some >= self.policy.warning_psi_some_avg10:
            warning_signals.append("cpu_psi_some_warning")
            reasons.append("cpu_psi_some_warning")
        if psi_full is not None and psi_full >= self.policy.critical_psi_full_avg10:
            critical_support.append("cpu_psi_full_critical")
            reasons.append("cpu_psi_full_critical")
        scheduler = sample.get("scheduler_probe") if isinstance(sample.get("scheduler_probe"), Mapping) else {}
        scheduler_delay = _number(scheduler.get("elapsed_ms"))
        if scheduler_delay is not None:
            if scheduler_delay >= self.policy.critical_scheduler_delay_ms:
                critical_support.append("scheduler_delay_critical")
                reasons.append("scheduler_delay_critical")
            elif scheduler_delay >= self.policy.warning_scheduler_delay_ms:
                warning_signals.append("scheduler_delay_warning")
                reasons.append("scheduler_delay_warning")
        if load1 is not None and load1 >= logical_cpus * 2:
            critical_support.append("cpu_load_critical")
            reasons.append("cpu_load_critical")
        elif load1 is not None and load1 >= logical_cpus * 1.5:
            warning_signals.append("cpu_load_warning")
            reasons.append("cpu_load_warning")

        cgroup = sample.get("cgroup") if isinstance(sample.get("cgroup"), Mapping) else {}
        cgroup_stat = cgroup.get("cpu_stat") if isinstance(cgroup.get("cpu_stat"), Mapping) else {}
        throttled_delta: int | None = None
        if previous is not None:
            # The current process cgroup is a signal, not a host-wide verdict.
            previous_cgroup = getattr(self, "_previous_cgroup_stat", {})
            current_throttled = cgroup_stat.get("nr_throttled")
            previous_throttled = previous_cgroup.get("nr_throttled")
            if isinstance(current_throttled, int) and isinstance(previous_throttled, int):
                if current_throttled < previous_throttled:
                    quality_flags.append("cgroup_throttle_counter_reset")
                else:
                    throttled_delta = current_throttled - previous_throttled
                    if throttled_delta > 0:
                        critical_support.append("cgroup_cpu_throttled")
                        reasons.append("cgroup_cpu_throttled")
        self._previous_cgroup_stat = dict(cgroup_stat)

        candidate = "normal"
        if len(critical_support) >= 2 or "cpu_psi_full_critical" in critical_support:
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
        candidate_for = max(timestamp - self._candidate_since, 0.0)
        required_for = 0.0
        if quality_flags:
            state = "degraded_observability"
        elif candidate == "normal":
            state = "recovered" if self._last_emitted in {"warning", "critical"} else "normal"
        else:
            required_for = self.policy.critical_for_seconds if candidate == "critical" else self.policy.warning_for_seconds
            state = candidate if candidate_for >= required_for else "normal"
        if state == "recovered":
            self._last_emitted = "normal"
        elif state in {"warning", "critical"}:
            self._last_emitted = state
        return {
            "resource_kind": "cpu",
            "state": state,
            "candidate_state": candidate,
            "candidate_for_seconds": round(candidate_for, 3),
            "required_for_seconds": required_for,
            "reasons": sorted(set(reasons)),
            "quality_flags": sorted(set(quality_flags)),
            "quality_status": "ok" if not quality_flags else "degraded",
            "sample_count": self._sample_count,
            "cpu_utilization_percent": utilization,
            "load1": load1,
            "logical_cpus": logical_cpus,
            "cpu_psi_some_avg10": psi_some,
            "cpu_psi_full_avg10": psi_full,
            "scheduler_delay_ms": scheduler_delay,
            "cgroup_throttled_delta": throttled_delta,
            "warning_signals": sorted(set(warning_signals)),
            "critical_support": sorted(set(critical_support)),
        }


@dataclass(frozen=True)
class CpuAttributionPolicy:
    min_host_contribution_percent: float = 20.0
    min_lead_margin: float = 0.15
    max_sample_age_seconds: float = 15.0


class CpuAttributionEvaluator:
    """Attribute CPU growth to one stable cgroup or explicitly abandon."""

    def __init__(self, policy: CpuAttributionPolicy | None = None) -> None:
        self.policy = policy or CpuAttributionPolicy()
        self._previous_objects: dict[str, Mapping[str, Any]] = {}
        self._previous_ns: int | None = None

    def evaluate(self, sample: Mapping[str, Any]) -> dict[str, Any]:
        observed_ns = sample.get("observed_monotonic_ns")
        quality_flags: list[str] = []
        if not isinstance(observed_ns, int) or isinstance(observed_ns, bool):
            quality_flags.append("cpu_monotonic_timestamp_missing")
            observed_ns = None
        elapsed = None
        if self._previous_ns is not None and observed_ns is not None:
            elapsed = (observed_ns - self._previous_ns) / 1_000_000_000
            if elapsed <= 0 or elapsed > self.policy.max_sample_age_seconds:
                quality_flags.append("cpu_attribution_sample_gap_or_reset")
        current_objects = sample.get("objects")
        objects = [item for item in current_objects if isinstance(item, Mapping)] if isinstance(current_objects, list) else []
        reasons: list[str] = []
        if not objects:
            reasons.append("cpu_object_registry_empty")
        if self._previous_ns is None:
            reasons.append("cpu_object_baseline_established")
        candidates: list[dict[str, Any]] = []
        identity_blockers: list[str] = []
        total_usage = 0
        for item in objects:
            stable_id = item.get("id")
            path = item.get("cgroup_path")
            stat = item.get("cpu_stat") if isinstance(item.get("cpu_stat"), Mapping) else {}
            current_usage = stat.get("usage_usec")
            if not isinstance(stable_id, str) or not stable_id or not isinstance(path, str) or not path:
                reasons.append("cpu_stable_object_identity_missing")
                continue
            previous = self._previous_objects.get(stable_id)
            previous_path = previous.get("cgroup_path") if previous else path
            previous_stat = previous.get("cpu_stat") if isinstance(previous, Mapping) else {}
            previous_usage = previous_stat.get("usage_usec") if isinstance(previous_stat, Mapping) else None
            if previous_path != path:
                identity_blockers.append("cpu_cgroup_path_changed")
            delta = None
            if elapsed is not None and elapsed > 0 and isinstance(current_usage, int) and isinstance(previous_usage, int):
                if current_usage < previous_usage:
                    quality_flags.append("cpu_usage_counter_reset")
                else:
                    delta = current_usage - previous_usage
                    total_usage += delta
            candidates.append(
                {
                    "kind": item.get("kind", "container"),
                    "id": stable_id,
                    "name": item.get("name"),
                    "cgroup_path": path,
                    "mapping_confidence": item.get("mapping_confidence", "low"),
                    "mapping_errors": list(item.get("mapping_errors", [])) if isinstance(item.get("mapping_errors"), list) else [],
                    "cpu_usage_delta_usec": delta,
                    "cpu_contribution_percent": None,
                }
            )
        self._previous_objects = {
            str(item.get("id")): item
            for item in objects
            if isinstance(item.get("id"), str) and item.get("id")
        }
        self._previous_ns = observed_ns if observed_ns is not None else self._previous_ns
        if total_usage > 0:
            for item in candidates:
                delta = item.get("cpu_usage_delta_usec")
                if isinstance(delta, int):
                    item["cpu_contribution_percent"] = round(delta / total_usage * 100, 3)
            candidates.sort(key=lambda item: -(item.get("cpu_contribution_percent") or 0))
        if quality_flags or elapsed is None:
            reasons.extend(identity_blockers)
            return self._result("DEGRADED_OBSERVABILITY", None, candidates, reasons, quality_flags, elapsed)
        if identity_blockers:
            return self._result("AMBIGUOUS_TARGET", None, candidates, reasons + identity_blockers, quality_flags, elapsed)
        if total_usage <= 0:
            return self._result("NO_TARGET", None, candidates, reasons + ["cpu_growth_not_observed"], quality_flags, elapsed)
        top = candidates[0] if candidates else None
        second_share = candidates[1].get("cpu_contribution_percent") if len(candidates) > 1 else 0.0
        top_share = top.get("cpu_contribution_percent") if top else 0.0
        lead = (top_share or 0.0) - (second_share or 0.0)
        if not top or top.get("mapping_errors") or top.get("mapping_confidence") not in {"high", "medium"}:
            return self._result("DEGRADED_OBSERVABILITY", None, candidates, reasons + ["cpu_target_mapping_unconfirmed"], quality_flags, elapsed)
        if (top_share or 0.0) < self.policy.min_host_contribution_percent:
            return self._result("NO_TARGET", None, candidates, reasons + ["cpu_contribution_below_minimum"], quality_flags, elapsed)
        if lead < self.policy.min_lead_margin * 100:
            return self._result("AMBIGUOUS_TARGET", None, candidates, reasons + ["cpu_candidate_lead_margin_too_small"], quality_flags, elapsed)
        target = dict(top)
        target["lead_margin_percent"] = round(lead, 3)
        return self._result("TARGET_CONFIRMED", target, candidates, reasons, quality_flags, elapsed)

    @staticmethod
    def _result(
        state: str,
        target: dict[str, Any] | None,
        candidates: list[dict[str, Any]],
        reasons: list[str],
        quality_flags: list[str],
        elapsed: float | None,
    ) -> dict[str, Any]:
        return {
            "resource_kind": "cpu",
            "state": state,
            "target": target,
            "candidates": candidates,
            "reason_codes": sorted(set(reasons)),
            "quality_flags": sorted(set(quality_flags)),
            "sample_elapsed_seconds": elapsed,
        }


__all__ = [
    "CpuAttributionEvaluator",
    "CpuAttributionPolicy",
    "CpuPolicy",
    "CpuRiskEvaluator",
    "collect_cpu_sample",
    "collect_scheduler_probe",
    "cpu_policy_from_config",
    "parse_cpu_max",
    "parse_cpu_psi",
    "parse_cpu_stat",
    "parse_loadavg",
    "parse_proc_stat",
]
