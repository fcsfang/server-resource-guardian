"""Small host-pressure gate for optional container collection.

The gate protects the host observation and rescue path from the collector's
Docker queries.  It deliberately uses a few stable host signals and keeps
the policy in-process so a missing or malformed signal fails closed for
container data, never for host observation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


NORMAL = "normal"
DEGRADED = "degraded"
SUSPENDED = "suspended"
STATES = (NORMAL, DEGRADED, SUSPENDED)

DEGRADED_AVAILABLE_RATIO = 0.10
SUSPENDED_AVAILABLE_RATIO = 0.03
DEGRADED_PSI_FULL_AVG10 = 10.0
SUSPENDED_PSI_FULL_AVG10 = 25.0
SUSPENDED_AVAILABLE_BYTES = 128 * 1024 * 1024
NORMAL_SAMPLES_TO_RESUME = 3
DEGRADED_COLLECTION_MULTIPLIER = 3
MIN_DEGRADED_INTERVAL_SECONDS = 15.0


@dataclass(frozen=True)
class PressureDecision:
    state: str
    available_bytes: int | None
    total_bytes: int | None
    available_ratio: float | None
    psi_full_avg10: float | None
    reason_codes: tuple[str, ...]
    transition: str | None = None
    normal_samples: int = 0

    @property
    def allow_container_collection(self) -> bool:
        return self.state != SUSPENDED

    @property
    def collection_interval_multiplier(self) -> int:
        return DEGRADED_COLLECTION_MULTIPLIER if self.state == DEGRADED else 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "available_bytes": self.available_bytes,
            "total_bytes": self.total_bytes,
            "available_ratio": self.available_ratio,
            "psi_full_avg10": self.psi_full_avg10,
            "reason_codes": list(self.reason_codes),
            "transition": self.transition,
            "normal_samples": self.normal_samples,
        }


def _parse_meminfo(text: str | None) -> dict[str, int]:
    values: dict[str, int] = {}
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
        values[key] = value
    return values


def _read_psi_full_avg10(text: str | None) -> float | None:
    for line in (text or "").splitlines():
        fields = line.split()
        if not fields or fields[0] != "full":
            continue
        for field in fields[1:]:
            key, separator, raw = field.partition("=")
            if key == "avg10" and separator:
                try:
                    value = float(raw)
                except ValueError:
                    return None
                return value if math.isfinite(value) and value >= 0 else None
    return None


class PressureGate:
    """Classify host memory pressure and add hysteresis for recovery."""

    def __init__(
        self,
        *,
        proc_root: Path = Path("/proc"),
        read_text: Callable[[Path], str | None] | None = None,
    ) -> None:
        self.proc_root = proc_root
        self._read_text = read_text or self._default_read_text
        self.state = NORMAL
        self._normal_samples = 0
        self._last_transition: str | None = None
        self._last_decision: PressureDecision | None = None

    @staticmethod
    def _default_read_text(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except (FileNotFoundError, PermissionError, OSError):
            return None

    def _signals(self) -> tuple[int | None, int | None, float | None]:
        meminfo = _parse_meminfo(self._read_text(self.proc_root / "meminfo"))
        total = meminfo.get("MemTotal")
        available = meminfo.get("MemAvailable")
        psi = _read_psi_full_avg10(self._read_text(self.proc_root / "pressure" / "memory"))
        return available, total, psi

    @staticmethod
    def _raw_state(available: int | None, total: int | None, psi: float | None) -> tuple[str, tuple[str, ...]]:
        reasons: list[str] = []
        ratio = available / total if available is not None and total and total > 0 else None
        if ratio is not None and ratio <= SUSPENDED_AVAILABLE_RATIO:
            reasons.append("memory_available_critical")
        if available is not None and available <= SUSPENDED_AVAILABLE_BYTES:
            reasons.append("memory_available_bytes_critical")
        if psi is not None and psi >= SUSPENDED_PSI_FULL_AVG10:
            reasons.append("memory_psi_critical")
        if reasons:
            return SUSPENDED, tuple(reasons)
        if (ratio is not None and ratio <= DEGRADED_AVAILABLE_RATIO) or (
            psi is not None and psi >= DEGRADED_PSI_FULL_AVG10
        ):
            if ratio is not None and ratio <= DEGRADED_AVAILABLE_RATIO:
                reasons.append("memory_available_low")
            if psi is not None and psi >= DEGRADED_PSI_FULL_AVG10:
                reasons.append("memory_psi_elevated")
            return DEGRADED, tuple(reasons)
        if available is None or total is None:
            return DEGRADED, ("memory_signal_unavailable",)
        return NORMAL, ()

    def evaluate(self) -> PressureDecision:
        available, total, psi = self._signals()
        ratio = available / total if available is not None and total and total > 0 else None
        raw_state, reasons = self._raw_state(available, total, psi)
        previous = self.state
        if raw_state == NORMAL:
            self._normal_samples += 1
            if self.state != NORMAL and self._normal_samples >= NORMAL_SAMPLES_TO_RESUME:
                self.state = NORMAL
        else:
            self._normal_samples = 0
            if raw_state == SUSPENDED or self.state == NORMAL:
                self.state = raw_state
            elif self.state == SUSPENDED and raw_state == DEGRADED:
                self.state = DEGRADED
        transition = None
        if self.state != previous:
            transition = f"{previous}->{self.state}"
            self._last_transition = transition
        decision = PressureDecision(
            state=self.state,
            available_bytes=available,
            total_bytes=total,
            available_ratio=round(ratio, 6) if ratio is not None else None,
            psi_full_avg10=psi,
            reason_codes=reasons,
            transition=transition,
            normal_samples=self._normal_samples,
        )
        self._last_decision = decision
        return decision

    def should_collect(self, *, now: float, last_collection: float | None, interval_seconds: float) -> bool:
        if self.state != DEGRADED or last_collection is None:
            return True
        try:
            interval = max(float(interval_seconds), MIN_DEGRADED_INTERVAL_SECONDS)
        except (TypeError, ValueError, OverflowError):
            interval = MIN_DEGRADED_INTERVAL_SECONDS
        return now - last_collection >= interval * DEGRADED_COLLECTION_MULTIPLIER


__all__ = [
    "DEGRADED",
    "MIN_DEGRADED_INTERVAL_SECONDS",
    "NORMAL",
    "NORMAL_SAMPLES_TO_RESUME",
    "PressureDecision",
    "PressureGate",
    "SUSPENDED",
]
