"""Composite, fail-closed risk evaluation for the Guardian MVP."""

from __future__ import annotations

import time
from typing import Any, Mapping

from .guardian_config import GuardianConfig, safe_defaults


ATTRIBUTION_ONLY_QUALITY_FLAGS = {
    "container_collector_degraded",
    "docker_observation_unavailable",
}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _nested_number(value: Any, *keys: str) -> float | None:
    current = value
    for key in keys:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return _number(current)


class CompositeRiskEvaluator:
    """Combine memory headroom, trend, PSI, swap and event deltas.

    This evaluator never authorizes an action. It produces an explainable risk
    result; the existing policy/controller layer remains the action gate.
    """

    def __init__(
        self,
        config: GuardianConfig | None = None,
        *,
        warning_for: float | None = None,
        critical_for: float | None = None,
    ) -> None:
        self.config = config or safe_defaults()
        self.warning_for = (
            self.config.warning_for_seconds if warning_for is None else float(warning_for)
        )
        self.critical_for = (
            self.config.critical_for_seconds if critical_for is None else float(critical_for)
        )
        self._candidate_level = "normal"
        self._candidate_since: float | None = None
        self._last_emitted = "normal"
        self._previous_available: int | None = None
        self._previous_events: dict[str, int] | None = None
        self._previous_monotonic_ns: int | None = None
        self._previous_timestamp: float | None = None
        self._sample_count = 0

    def _event_delta(self, current: Mapping[str, Any]) -> tuple[int | None, bool, bool]:
        tracked_keys = {"oom", "oom_kill"}
        counters = {
            key: int(value)
            for key, value in current.items()
            if key in tracked_keys
            and isinstance(value, int)
            and not isinstance(value, bool)
            and value >= 0
        }
        if self._previous_events is None:
            self._previous_events = counters
            return None, True, False
        reset = any(value < self._previous_events.get(key, value) for key, value in counters.items())
        delta = sum(max(value - self._previous_events.get(key, value), 0) for key, value in counters.items())
        self._previous_events = counters
        return delta, False, reset

    def evaluate(
        self,
        observation: Mapping[str, Any],
        warning_available: float | None = None,
        critical_available: float | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        timestamp = time.monotonic() if now is None else now
        self._sample_count += 1
        reasons: list[str] = []
        quality_flags: list[str] = []

        warning_threshold = (
            self.config.warning_available_percent
            if warning_available is None
            else float(warning_available)
        )
        critical_threshold = (
            self.config.critical_available_percent
            if critical_available is None
            else float(critical_available)
        )

        quality = observation.get("quality")
        if isinstance(quality, Mapping):
            raw_flags = quality.get("flags", [])
            if isinstance(raw_flags, list):
                quality_flags.extend(
                    str(flag)
                    for flag in raw_flags
                    if flag and str(flag) not in ATTRIBUTION_ONLY_QUALITY_FLAGS
                )
            else:
                quality_flags.append("observation_quality_flags_invalid")
            if quality.get("status") != "ok" and not raw_flags:
                quality_flags.append("observation_quality_degraded_without_flags")
        else:
            quality_flags.append("observation_quality_missing")

        memory = observation.get("memory") if isinstance(observation.get("memory"), Mapping) else {}
        cgroup = observation.get("cgroup") if isinstance(observation.get("cgroup"), Mapping) else {}
        psi = observation.get("psi") if isinstance(observation.get("psi"), Mapping) else {}
        memory_events = cgroup.get("memory_events") if isinstance(cgroup.get("memory_events"), Mapping) else {}
        if not {"oom", "oom_kill"}.issubset(memory_events):
            quality_flags.append("oom_counters_missing")
        available_ratio = _number(memory.get("available_ratio_percent"))
        available_bytes = memory.get("available_bytes")
        if not isinstance(available_bytes, int) or isinstance(available_bytes, bool):
            quality_flags.append("memory_available_missing")
            available_bytes = None

        observed_ns = observation.get("observed_monotonic_ns")
        if observed_ns is None:
            quality_flags.append("monotonic_timestamp_missing")
        elif not isinstance(observed_ns, int) or isinstance(observed_ns, bool):
            quality_flags.append("monotonic_timestamp_invalid")
            observed_ns = None
        if self._previous_monotonic_ns is not None and observed_ns is not None:
            if observed_ns <= self._previous_monotonic_ns:
                quality_flags.append("monotonic_clock_not_increasing")
            elif observed_ns - self._previous_monotonic_ns > self.config.max_sample_age_seconds * 1_000_000_000:
                quality_flags.append("sample_gap_too_large")
        self._previous_monotonic_ns = observed_ns if observed_ns is not None else self._previous_monotonic_ns

        event_delta, baseline, event_reset = self._event_delta(memory_events)
        if baseline:
            reasons.append("oom_counter_baseline_established")
        if event_reset:
            quality_flags.append("oom_counter_reset")
        if event_delta:
            reasons.append("new_cgroup_memory_oom_event")

        growth_rate: float | None = None
        if self._previous_available is not None and available_bytes is not None:
            previous_timestamp = self._previous_timestamp
            elapsed = (
                timestamp - previous_timestamp
                if previous_timestamp is not None
                else 0.0
            )
            if elapsed <= 0:
                quality_flags.append("evaluation_clock_not_increasing")
            else:
                growth_rate = max((self._previous_available - available_bytes) / elapsed, 0.0)
        self._previous_available = available_bytes
        self._previous_timestamp = timestamp

        swap_total = _number(memory.get("swap_total_bytes"))
        swap_ratio = _number(memory.get("swap_used_ratio_percent"))
        if swap_total is None:
            quality_flags.append("swap_signal_missing")
        elif swap_total < 0:
            quality_flags.append("swap_signal_invalid")
        elif swap_total == 0:
            # No swap is a valid host configuration, not zero pressure.
            swap_ratio = None
        elif swap_ratio is None:
            quality_flags.append("swap_signal_missing")

        psi_memory = psi.get("memory") if isinstance(psi.get("memory"), Mapping) else None
        psi_some = _nested_number(psi_memory, "some", "avg10")
        psi_full = _nested_number(psi_memory, "full", "avg10")
        if psi_memory is None or psi_some is None or psi_full is None:
            quality_flags.append("memory_psi_missing")

        warning_signals: list[str] = []
        critical_support: list[str] = []
        if available_ratio is None:
            quality_flags.append("memory_available_ratio_missing")
        elif available_ratio <= critical_threshold:
            reasons.append("host_memory_available_critical")
        elif available_ratio <= warning_threshold:
            warning_signals.append("host_memory_available_warning")
            reasons.append("host_memory_available_warning")

        if growth_rate is not None:
            if growth_rate >= self.config.trend_critical_bytes_per_second:
                critical_support.append("memory_growth_critical")
                reasons.append("memory_growth_critical")
            elif growth_rate >= self.config.trend_warning_bytes_per_second:
                warning_signals.append("memory_growth_warning")
                reasons.append("memory_growth_warning")

        if psi_full is not None and psi_full >= self.config.psi_memory_full_critical_avg10:
            critical_support.append("memory_psi_full_critical")
            reasons.append("memory_psi_full_critical")
        elif psi_some is not None and psi_some >= self.config.psi_memory_some_warning_avg10:
            warning_signals.append("memory_psi_some_warning")
            reasons.append("memory_psi_some_warning")

        if swap_ratio is not None:
            if swap_ratio >= self.config.swap_used_critical_percent:
                critical_support.append("swap_used_critical")
                reasons.append("swap_used_critical")
            elif swap_ratio >= self.config.swap_used_warning_percent:
                warning_signals.append("swap_used_warning")
                reasons.append("swap_used_warning")

        hard_critical = bool(event_delta)
        low_available_critical = available_ratio is not None and available_ratio <= critical_threshold
        candidate = "normal"
        if hard_critical:
            candidate = "critical"
        elif low_available_critical or len(critical_support) >= 2:
            candidate = "critical"
        elif warning_signals or (
            available_ratio is not None and available_ratio <= warning_threshold
        ):
            candidate = "warning"
        elif baseline:
            candidate = "normal"

        if self._sample_count < self.config.required_samples:
            reasons.append("insufficient_samples")
            quality_flags.append("insufficient_samples")

        if candidate != self._candidate_level:
            self._candidate_level = candidate
            self._candidate_since = timestamp
        if self._candidate_since is None:
            self._candidate_since = timestamp
        candidate_for = max(timestamp - self._candidate_since, 0.0)

        state = "normal"
        required_for = 0.0
        if quality_flags:
            state = "degraded_observability"
        elif candidate == "normal":
            state = "recovered" if self._last_emitted in {"warning", "critical"} else "normal"
        else:
            required_for = self.critical_for if candidate == "critical" else self.warning_for
            state = candidate if candidate_for >= required_for else "normal"

        if state == "recovered":
            self._last_emitted = "normal"
        elif state in {"warning", "critical"}:
            self._last_emitted = state

        return {
            "state": state,
            "candidate_state": candidate,
            "candidate_for_seconds": round(candidate_for, 3),
            "required_for_seconds": required_for,
            "reasons": reasons,
            "quality_flags": sorted(set(quality_flags)),
            "sample_count": self._sample_count,
            "oom_events_delta": event_delta,
            "memory_available_growth_bytes_per_second": growth_rate,
            "quality_status": "ok" if not quality_flags else "degraded",
            "signal_summary": {
                "warning_signals": warning_signals,
                "critical_support": critical_support,
                "psi_memory_some_avg10": psi_some,
                "psi_memory_full_avg10": psi_full,
                "swap_used_ratio_percent": swap_ratio,
            },
        }


__all__ = ["CompositeRiskEvaluator"]
