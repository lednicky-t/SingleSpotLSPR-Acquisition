from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np


def _row_timestamp_s(row: dict[str, Any], fallback_index: int) -> float:
    t_ms = row.get("t_ms")
    if t_ms is not None:
        try:
            return float(t_ms) / 1000.0
        except Exception:
            pass
    acquired_at_unix_ms = row.get("acquired_at_unix_ms")
    if acquired_at_unix_ms is not None:
        try:
            return float(acquired_at_unix_ms) / 1000.0
        except Exception:
            pass
    time_s = row.get("time_s")
    if time_s is not None:
        try:
            return float(time_s)
        except Exception:
            pass
    return float(fallback_index)


def load_metric_archive_history(
    path: Path,
    metric_names: set[str] | None = None,
    *,
    time_range_s: tuple[float, float] | None = None,
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Load metric history from a legacy JSON-lines archive file.

    Only .jsonl is handled here - load_processed_metric_history() in
    hdf5_export.py is the sole HDF5 reader; it only delegates to this
    function for .jsonl paths, never for .h5/.hdf5.
    """
    if not path.exists() or path.suffix.lower() != ".jsonl":
        return {}

    raw_rows: list[tuple[float, dict[str, Any]]] = []
    with path.open("r", encoding="utf-8") as handle:
        for fallback_index, line in enumerate(handle):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if not isinstance(row, dict):
                continue
            raw_rows.append((_row_timestamp_s(row, fallback_index), row))

    series: dict[str, list[tuple[float, float]]] = {}
    if raw_rows:
        # _row_timestamp_s can return an absolute Unix-epoch value (schema
        # 6.0+ rows only have acquired_at_unix_ms, no relative t_ms) -
        # normalize to relative-to-first-row seconds here, once, for the
        # whole file. A no-op for legacy rows that had t_ms (already
        # relative, so the minimum is already ~0). See
        # docs/sensorgram_improvements.md.
        anchor_s = min(t for t, _ in raw_rows)
        for timestamp_s, row in raw_rows:
            relative_s = timestamp_s - anchor_s
            if time_range_s is not None:
                try:
                    start_s = float(time_range_s[0])
                    end_s = float(time_range_s[1])
                except Exception:
                    start_s = end_s = None  # type: ignore[assignment]
                else:
                    if np.isfinite(start_s) and np.isfinite(end_s):
                        if end_s < start_s:
                            start_s, end_s = end_s, start_s
                        if relative_s < start_s or relative_s > end_s:
                            continue
            for key, value in row.items():
                if key in {"t_ms", "acquired_at_unix_ms", "time_s", "sample_index"}:
                    continue
                if metric_names is not None and key not in metric_names:
                    continue
                try:
                    numeric_value = float(value)
                except Exception:
                    continue
                series.setdefault(key, []).append((relative_s, numeric_value))

    result: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for metric_name, points in series.items():
        if not points:
            continue
        # Defensive: sort by timestamp - rows should already be in file
        # order, but an unsorted x-array makes pyqtgraph draw the line
        # backward over already-drawn history. See
        # docs/sensorgram_improvements.md.
        points.sort(key=lambda p: p[0])
        times_s = np.asarray([p[0] for p in points], dtype=float)
        values = np.asarray([p[1] for p in points], dtype=float)
        result[metric_name] = (times_s, values)
    return result
