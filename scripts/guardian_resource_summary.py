#!/usr/bin/env python3
"""Summarize bounded Guardian resource-sampler TSV evidence.

This helper is read-only. It reads at most ``--max-bytes`` from an existing
TSV, computes deterministic nearest-rank percentiles, and prints JSON. It
does not signal, inspect, or modify the sampled process. A file that is still
being appended can be summarized safely; an incomplete final row is ignored.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
from pathlib import Path
from typing import Any, Iterable


METRIC_COLUMNS = {
    "rss_kib": "rss_kib",
    "cpu_percent": "cpu_percent",
    "fds": "fds",
    "threads": "threads",
}


def _bounded_text(path: Path, max_bytes: int) -> tuple[str, int, bool]:
    if max_bytes <= 0:
        raise ValueError("max_bytes_invalid")
    with path.open("rb") as stream:
        raw = stream.read(max_bytes + 1)
    truncated = len(raw) > max_bytes
    if truncated:
        raw = raw[:max_bytes]
        # Do not include a potentially partial final record in the summary.
        complete_end = raw.rfind(b"\n")
        raw = raw[: complete_end + 1] if complete_end >= 0 else b""
    return raw.decode("utf-8", errors="replace"), len(raw), truncated


def _number(raw: str | None) -> float | None:
    if raw is None or not raw.strip():
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _percentile(values: Iterable[float], percentile: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _metric_summary(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"samples": 0, "min": None, "p50": None, "p95": None, "p99": None, "max": None, "mean": None}
    return {
        "samples": len(values),
        "min": min(values),
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "p99": _percentile(values, 0.99),
        "max": max(values),
        "mean": round(sum(values) / len(values), 3),
    }


def summarize_tsv(path: Path, *, max_bytes: int = 10 * 1024 * 1024) -> dict[str, Any]:
    text, bytes_considered, truncated = _bounded_text(path, max_bytes)
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    values: dict[str, list[float]] = {name: [] for name in METRIC_COLUMNS}
    epochs: list[float] = []
    invalid_rows = 0
    rows = 0
    for row in reader:
        rows += 1
        epoch = _number(row.get("epoch_s"))
        if epoch is not None:
            epochs.append(epoch)
        cpu_key = "cpu_percent" if "cpu_percent" in row else "pcpu"
        for metric, column in METRIC_COLUMNS.items():
            source_column = cpu_key if metric == "cpu_percent" else column
            value = _number(row.get(source_column))
            if value is not None:
                values[metric].append(value)
        if epoch is None and not any(_number(row.get(column)) is not None for column in METRIC_COLUMNS.values()):
            invalid_rows += 1

    epoch_summary: dict[str, Any] = {
        "samples": len(epochs),
        "first": min(epochs) if epochs else None,
        "last": max(epochs) if epochs else None,
        "duration_seconds": round(max(epochs) - min(epochs), 3) if len(epochs) >= 2 else None,
    }
    return {
        "source": str(path),
        "bytes_considered": bytes_considered,
        "truncated": truncated,
        "rows_read": rows,
        "invalid_rows": invalid_rows,
        "epoch": epoch_summary,
        "metrics": {name: _metric_summary(metric_values) for name, metric_values in values.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize bounded Guardian resource TSV evidence")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--max-bytes", type=int, default=10 * 1024 * 1024)
    args = parser.parse_args()
    print(json.dumps(summarize_tsv(args.input, max_bytes=args.max_bytes), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
