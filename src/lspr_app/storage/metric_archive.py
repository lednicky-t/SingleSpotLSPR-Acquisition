from __future__ import annotations

from pathlib import Path
from typing import Any

import h5py
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
    if not path.exists():
        return {}

    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        raw_rows: list[tuple[float, dict[str, Any]]] = []
        with path.open("r", encoding="utf-8") as handle:
            for fallback_index, line in enumerate(handle):
                line = line.strip()
                if not line:
                    continue
                try:
                    import json

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

    if suffix not in {".h5", ".hdf5"}:
        return {}

    series: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    with h5py.File(path, "r") as handle:
        processed = handle.get("processed")
        if processed is None:
            return {}
        metrics = processed.get("metrics")
        if metrics is None:
            return {}
        time_ds = metrics.get("acquired_at_unix_ms")
        if time_ds is None:
            return {}
        unix_ms = np.asarray(time_ds[...], dtype=float)
        # Defensive: see the matching comment in
        # storage/hdf5_export.py:load_processed_metric_history - same fix,
        # same reason (a non-monotonic timestamp column breaks the
        # searchsorted slicing below and makes pyqtgraph draw backward over
        # history).
        sort_order = np.argsort(unix_ms, kind="stable")
        needs_reorder = not np.array_equal(sort_order, np.arange(len(unix_ms)))
        if needs_reorder:
            unix_ms = unix_ms[sort_order]

        # Convert absolute Unix-ms to a relative, plot-ready seconds axis,
        # anchored to this file's own first sample - computed before any
        # time_range_s slicing so the anchor doesn't shift with the query.
        times_s = (unix_ms - unix_ms[0]) / 1000.0 if len(unix_ms) > 0 else unix_ms

        start_index = 0
        end_index = len(times_s)
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
                    start_index = int(np.searchsorted(times_s, start_s, side="left"))
                    end_index = int(np.searchsorted(times_s, end_s, side="right"))
                    times_s = times_s[start_index:end_index]
        for metric_name, dataset in metrics.items():
            if metric_name in {"acquired_at_unix_ms", "sample_index"}:
                continue
            if metric_names is not None and metric_name not in metric_names:
                continue
            if needs_reorder:
                values = np.asarray(dataset[...], dtype=float)[sort_order]
                if time_range_s is not None:
                    values = values[start_index:end_index]
            elif time_range_s is not None:
                values = np.asarray(dataset[start_index:end_index], dtype=float)
            else:
                values = np.asarray(dataset[...], dtype=float)
            series[metric_name] = (times_s.copy(), values)
    return series
