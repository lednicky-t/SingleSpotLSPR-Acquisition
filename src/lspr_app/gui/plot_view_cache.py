from __future__ import annotations

from dataclasses import dataclass, field
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from time import perf_counter

import numpy as np


@dataclass(slots=True)
class MetricDisplayCache:
    source_revision: int = 0
    source_len: int = 0
    target_points: int = 0
    stride: int = 1
    raw_block_size: int = 128
    combine_factor: int = 4
    x_display: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float64))
    y_display: np.ndarray = field(default_factory=lambda: np.empty(0, dtype=np.float64))
    levels: list[list["_EnvelopeBlock"]] = field(default_factory=list)
    display_revision: int = 0
    display_output_revision: int = 0
    last_display_signature: tuple[object, ...] | None = None
    rebuild_count: int = 0
    incremental_count: int = 0
    hit_count: int = 0
    last_mode: str = "empty"
    last_new_points_processed: int = 0
    last_base_blocks: int = 0
    last_levels: int = 0
    last_tail_groups_updated: int = 0
    last_display_level: int = -1
    last_display_blocks: int = 0
    last_append_ms: float | None = None
    last_assemble_ms: float | None = None
    last_full_rebuild_ms: float | None = None
    last_source_used: str = "empty"
    last_invalidation_reason: str = "unknown"
    archive_read_count: int = 0
    last_archive_points: int = 0


@dataclass(slots=True)
class _EnvelopeBlock:
    first_x: float
    last_x: float
    y_min: float
    y_max: float
    x_at_y_min: float
    x_at_y_max: float
    count: int


def _make_envelope_block(x: np.ndarray, y: np.ndarray) -> _EnvelopeBlock | None:
    if len(x) == 0 or len(y) == 0:
        return None
    finite = np.isfinite(x) & np.isfinite(y)
    if not np.any(finite):
        return None
    xf = np.asarray(x[finite], dtype=np.float64)
    yf = np.asarray(y[finite], dtype=np.float64)
    if len(xf) == 0:
        return None
    min_i = int(np.argmin(yf))
    max_i = int(np.argmax(yf))
    return _EnvelopeBlock(
        first_x=float(xf[0]),
        last_x=float(xf[-1]),
        y_min=float(yf[min_i]),
        y_max=float(yf[max_i]),
        x_at_y_min=float(xf[min_i]),
        x_at_y_max=float(xf[max_i]),
        count=int(len(xf)),
    )


def _combine_envelope_blocks(blocks: list[_EnvelopeBlock]) -> _EnvelopeBlock | None:
    if not blocks:
        return None
    first_x = float(blocks[0].first_x)
    last_x = float(blocks[-1].last_x)
    count = int(sum(block.count for block in blocks))
    min_block = min(blocks, key=lambda block: block.y_min)
    max_block = max(blocks, key=lambda block: block.y_max)
    return _EnvelopeBlock(
        first_x=first_x,
        last_x=last_x,
        y_min=float(min_block.y_min),
        y_max=float(max_block.y_max),
        x_at_y_min=float(min_block.x_at_y_min),
        x_at_y_max=float(max_block.x_at_y_max),
        count=count,
    )


def _blocks_to_display_arrays(blocks: list[_EnvelopeBlock]) -> tuple[np.ndarray, np.ndarray]:
    if not blocks:
        empty = np.empty(0, dtype=np.float64)
        return empty, empty
    xs: list[float] = []
    ys: list[float] = []
    for block in blocks:
        if block.x_at_y_min <= block.x_at_y_max:
            xs.extend([block.x_at_y_min, block.x_at_y_max])
            ys.extend([block.y_min, block.y_max])
        else:
            xs.extend([block.x_at_y_max, block.x_at_y_min])
            ys.extend([block.y_max, block.y_min])
    return np.asarray(xs, dtype=np.float64), np.asarray(ys, dtype=np.float64)


def _build_envelope_levels(x: np.ndarray, y: np.ndarray, *, raw_block_size: int, combine_factor: int) -> list[list[_EnvelopeBlock]]:
    blocks: list[_EnvelopeBlock] = []
    block_size = max(int(raw_block_size), 1)
    for start in range(0, len(x), block_size):
        stop = min(start + block_size, len(x))
        block = _make_envelope_block(x[start:stop], y[start:stop])
        if block is not None:
            blocks.append(block)
    levels: list[list[_EnvelopeBlock]] = [blocks]
    factor = max(int(combine_factor), 2)
    while len(levels[-1]) > factor:
        previous = levels[-1]
        next_level: list[_EnvelopeBlock] = []
        for start in range(0, len(previous), factor):
            combined = _combine_envelope_blocks(previous[start : start + factor])
            if combined is not None:
                next_level.append(combined)
        if not next_level:
            break
        levels.append(next_level)
    return levels


def _append_envelope_levels(
    levels: list[list[_EnvelopeBlock]],
    x_new: np.ndarray,
    y_new: np.ndarray,
    *,
    raw_block_size: int,
    combine_factor: int,
) -> list[list[_EnvelopeBlock]]:
    if not levels:
        levels = [[]]
    block_size = max(int(raw_block_size), 1)
    for start in range(0, len(x_new), block_size):
        stop = min(start + block_size, len(x_new))
        block = _make_envelope_block(x_new[start:stop], y_new[start:stop])
        if block is not None:
            levels[0].append(block)
    factor = max(int(combine_factor), 2)
    current = levels[0]
    rebuilt_levels: list[list[_EnvelopeBlock]] = [current]
    while len(rebuilt_levels[-1]) > factor:
        previous = rebuilt_levels[-1]
        next_level: list[_EnvelopeBlock] = []
        for start in range(0, len(previous), factor):
            combined = _combine_envelope_blocks(previous[start : start + factor])
            if combined is not None:
                next_level.append(combined)
        if not next_level:
            break
        rebuilt_levels.append(next_level)
    return rebuilt_levels


def _select_envelope_blocks(levels: list[list[_EnvelopeBlock]], target_points: int) -> list[_EnvelopeBlock]:
    if not levels:
        return []
    target_bins = max(int(np.ceil(float(max(target_points, 1)) / 2.0)), 1)
    chosen = levels[0]
    for level in levels:
        chosen = level
        if len(level) <= target_bins:
            break
    return chosen


def _display_signature(x: np.ndarray, y: np.ndarray) -> tuple[object, ...]:
    if len(x) == 0 or len(y) == 0:
        return (0,)
    return (
        int(len(x)),
        float(x[0]),
        float(x[-1]),
        float(np.nanmin(y)) if len(y) else float("nan"),
        float(np.nanmax(y)) if len(y) else float("nan"),
    )


def extract_series_arrays(series: object) -> tuple[np.ndarray, np.ndarray]:
    if series is None:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
    if hasattr(series, "to_arrays"):
        arrays = series.to_arrays()  # type: ignore[no-any-return]
        return np.asarray(arrays[0], dtype=np.float64), np.asarray(arrays[1], dtype=np.float64)
    if isinstance(series, tuple) and len(series) == 2:
        return np.asarray(series[0], dtype=np.float64), np.asarray(series[1], dtype=np.float64)
    try:
        if len(series) == 0:  # type: ignore[arg-type]
            return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
    except TypeError:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
    return (
        np.asarray([float(item[0]) for item in series], dtype=np.float64),
        np.asarray([float(item[1]) for item in series], dtype=np.float64),
    )


def build_active_trace_series_token(window) -> tuple[object, ...]:
    selected_metrics = frozenset(getattr(window, "_selected_trace_metrics", lambda: [])())
    view_mode = getattr(window, "_normalize_sensorgram_view_mode", lambda value: value)(
        getattr(window, "_sensorgram_view_mode", "absolute")
    )

    def _source_entries(source: object) -> tuple[tuple[str, int, int, int], ...]:
        if not isinstance(source, dict):
            return ()
        entries: list[tuple[str, int, int, int]] = []
        for metric_name, series in source.items():
            if metric_name not in selected_metrics:
                continue
            if len(series) <= 0:
                continue
            entries.append(
                (
                    str(metric_name),
                    id(series),
                    int(getattr(series, "revision", 0)),
                    int(len(series)),
                )
            )
        return tuple(entries)

    metric_history_buffers = getattr(window, "_metric_history_buffers", None)
    archive_path = getattr(window, "_metric_archive_path", None)
    archive_path = Path(archive_path).expanduser() if archive_path else None
    plot_view_cache = getattr(window, "_plot_view_cache", None)
    if view_mode == "absolute" and plot_view_cache is not None:
        try:
            live_states = tuple(
                state
                for metric_name in sorted(selected_metrics)
                if (state := plot_view_cache.live_absolute_metric_state(metric_name)) is not None
            )
        except Exception:
            live_states = ()
        if live_states:
            return ("live_absolute", view_mode, live_states, tuple(sorted(selected_metrics)))
    if view_mode == "absolute" and bool(getattr(window, "_live_active", False)):
        return ("live_absolute_empty", view_mode, tuple(sorted(selected_metrics)))
    if isinstance(metric_history_buffers, dict) and metric_history_buffers:
        source = _source_entries(metric_history_buffers)
        return ("rolling", source)
    if view_mode == "absolute" and archive_path is not None and archive_path.exists():
        try:
            mtime_ns = archive_path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        return ("archive", str(archive_path), int(mtime_ns), view_mode, tuple(sorted(selected_metrics)))
    if not bool(getattr(window, "_live_active", False)) and archive_path is not None and archive_path.exists():
        try:
            mtime_ns = archive_path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        return ("archive", str(archive_path), int(mtime_ns), view_mode, tuple(sorted(selected_metrics)))
    return ("empty", ())


def build_heatmap_history_token(window) -> tuple[object, ...]:
    history = getattr(window, "_sensorgram_heatmap_history", None)
    wavelengths = getattr(window, "_sensorgram_heatmap_wavelengths", None)
    axis_key = getattr(window, "_sensorgram_heatmap_axis_key", None)
    return (
        id(history),
        int(getattr(window, "_sensorgram_heatmap_history_revision", 0)),
        len(history) if isinstance(history, list) else 0,
        axis_key,
        id(wavelengths),
        len(wavelengths) if isinstance(wavelengths, np.ndarray) else 0,
    )


def build_metric_series_token(window, metric_name: str) -> tuple[object, ...]:
    view_mode = getattr(window, "_normalize_sensorgram_view_mode", lambda value: value)(
        getattr(window, "_sensorgram_view_mode", "absolute")
    )
    archive_path = getattr(window, "_metric_archive_path", None)
    archive_path = Path(archive_path).expanduser() if archive_path else None
    if view_mode == "absolute" and archive_path is not None and archive_path.exists():
        try:
            mtime_ns = archive_path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        return (str(metric_name), "archive", str(archive_path), int(mtime_ns))
    if not bool(getattr(window, "_live_active", False)) and archive_path is not None and archive_path.exists():
        try:
            mtime_ns = archive_path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        return (str(metric_name), "archive", str(archive_path), int(mtime_ns))
    metric_history_buffers = getattr(window, "_metric_history_buffers", None)
    if isinstance(metric_history_buffers, dict):
        series = metric_history_buffers.get(metric_name)
        if series is not None:
            return (
                str(metric_name),
                "rolling",
                id(series),
                int(getattr(series, "revision", 0)),
                int(len(series)),
            )
    return (str(metric_name), "empty", 0, 0, 0)


def build_heatmap_arrays(history: list[tuple[float, np.ndarray]]) -> tuple[np.ndarray, np.ndarray]:
    if not history:
        return np.empty(0, dtype=np.float64), np.empty((0, 0), dtype=np.float64)
    times = np.asarray([float(item[0]) for item in history], dtype=np.float64)
    rows = [np.asarray(item[1], dtype=np.float64) for item in history]
    if not rows:
        return np.empty(0, dtype=np.float64), np.empty((0, 0), dtype=np.float64)
    return times, np.vstack(rows)


def derive_heatmap_levels_from_matrix(matrix: np.ndarray, *, pad_fraction: float = 0.05) -> tuple[float, float]:
    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.size == 0:
        return 0.0, 1.0
    finite = matrix[np.isfinite(matrix)]
    if finite.size == 0:
        return 0.0, 1.0
    low = float(np.min(finite))
    high = float(np.max(finite))
    if not np.isfinite(low) or not np.isfinite(high):
        return 0.0, 1.0
    if high <= low:
        high = low + 1e-6
    span = max(high - low, 1e-6)
    pad = max(span * float(pad_fraction), 1e-6)
    return low - pad, high + pad


def expand_heatmap_levels(
    current_levels: tuple[float, float] | None,
    row_values: np.ndarray,
    *,
    pad_fraction: float = 0.05,
) -> tuple[float, float]:
    row = np.asarray(row_values, dtype=np.float64)
    finite = row[np.isfinite(row)]
    if finite.size == 0:
        if current_levels is not None and len(current_levels) == 2:
            low, high = float(current_levels[0]), float(current_levels[1])
            if np.isfinite(low) and np.isfinite(high) and high > low:
                return low, high
        return 0.0, 1.0
    row_low = float(np.min(finite))
    row_high = float(np.max(finite))
    if current_levels is None or len(current_levels) != 2:
        return derive_heatmap_levels_from_matrix(finite, pad_fraction=pad_fraction)
    low = float(current_levels[0])
    high = float(current_levels[1])
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        low, high = row_low, row_high
    else:
        low = min(low, row_low)
        high = max(high, row_high)
    if high <= low:
        high = low + 1e-6
    span = max(high - low, 1e-6)
    pad = max(span * float(pad_fraction), 1e-6)
    return low - pad, high + pad


def select_heatmap_rows_for_view(
    times: np.ndarray,
    matrix: np.ndarray,
    *,
    view_x_min: float | None = None,
    view_x_max: float | None = None,
    max_rows: int = 2000,
    view_height_px: float | None = None,
    oversample: float = 2.0,
    minimum_rows: int = 256,
    enabled: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    times = np.asarray(times, dtype=np.float64)
    matrix = np.asarray(matrix, dtype=np.float64)
    if len(times) == 0 or len(matrix) == 0:
        return times[:0], matrix[:0]
    if matrix.ndim != 2 or matrix.shape[0] != len(times):
        return times[:0], matrix[:0]
    if view_x_min is not None and view_x_max is not None and view_x_max > view_x_min:
        visible = np.flatnonzero((times >= view_x_min) & (times <= view_x_max))
        if len(visible) > 0:
            start = max(int(visible[0]) - 1, 0)
            stop = min(int(visible[-1]) + 2, len(times))
            times = times[start:stop]
            matrix = matrix[start:stop]
    if not enabled:
        return times, matrix
    max_rows = _target_rows_from_height(
        view_height_px,
        enabled=enabled,
        max_rows=max_rows,
        minimum_rows=minimum_rows,
        oversample=oversample,
    )
    if max_rows <= 0 or len(times) <= max_rows:
        return times, matrix
    indices = np.linspace(0, len(times) - 1, num=max_rows, dtype=np.int64)
    indices = np.unique(indices)
    return times[indices], matrix[indices]


def _peak_preserving_downsample_indices(y: np.ndarray, target_bins: int) -> np.ndarray:
    if target_bins <= 0 or len(y) == 0:
        return np.empty(0, dtype=np.int64)
    if len(y) <= target_bins:
        return np.arange(len(y), dtype=np.int64)

    bins = max(int(target_bins), 1)
    edges = np.linspace(0, len(y), num=bins + 1, dtype=np.int64)
    keep: list[int] = []
    for start, stop in zip(edges[:-1], edges[1:]):
        if stop <= start:
            continue
        segment = y[start:stop]
        finite = np.isfinite(segment)
        if not np.any(finite):
            keep.append(start + (stop - start - 1) // 2)
            continue
        finite_segment = segment[finite]
        finite_positions = np.flatnonzero(finite) + start
        keep.append(int(finite_positions[int(np.argmin(finite_segment))]))
        keep.append(int(finite_positions[int(np.argmax(finite_segment))]))
    if not keep:
        return np.arange(len(y), dtype=np.int64)
    return np.unique(np.asarray(keep, dtype=np.int64))


def _minmax_preserving_downsample_indices(y: np.ndarray, target_bins: int) -> np.ndarray:
    if target_bins <= 0 or len(y) == 0:
        return np.empty(0, dtype=np.int64)
    if len(y) <= target_bins * 2:
        return np.arange(len(y), dtype=np.int64)

    bins = max(int(target_bins), 1)
    edges = np.linspace(0, len(y), num=bins + 1, dtype=np.int64)
    keep: list[int] = [0, len(y) - 1]
    for start, stop in zip(edges[:-1], edges[1:]):
        if stop <= start:
            continue
        segment = y[start:stop]
        finite = np.isfinite(segment)
        if not np.any(finite):
            continue
        finite_segment = segment[finite]
        finite_positions = np.flatnonzero(finite) + start
        min_index = int(finite_positions[int(np.argmin(finite_segment))])
        max_index = int(finite_positions[int(np.argmax(finite_segment))])
        keep.append(min_index)
        keep.append(max_index)
    if not keep:
        return np.arange(len(y), dtype=np.int64)
    return np.unique(np.asarray(keep, dtype=np.int64))


def quantize_view_target_points(points: int) -> int:
    points = max(int(points), 1)
    bucket = 1
    while bucket < points:
        bucket <<= 1
    return bucket


def _target_points_from_width(
    view_width_px: float | None,
    *,
    enabled: bool,
    minimum_points: int,
    oversample: float,
    default_points: int,
) -> int:
    max_points = max(int(default_points), 1)
    min_points = max(1, min(int(minimum_points), max_points))
    if not enabled:
        return quantize_view_target_points(max_points)
    if view_width_px is None or view_width_px <= 0:
        target_points = max_points
    else:
        target_points = max(min_points, int(float(view_width_px) * float(oversample)))
        target_points = min(target_points, max_points)
    return quantize_view_target_points(target_points)


def _target_rows_from_height(
    view_height_px: float | None,
    *,
    enabled: bool,
    max_rows: int,
    minimum_rows: int,
    oversample: float,
) -> int:
    if not enabled:
        return quantize_view_target_points(max_rows)
    target_rows = max_rows
    if view_height_px is not None and view_height_px > 0:
        target_rows = min(target_rows, max(minimum_rows, int(view_height_px * oversample)))
    return quantize_view_target_points(target_rows)


def _stride_sample_indices(length: int, stride: int) -> np.ndarray:
    if length <= 0:
        return np.empty(0, dtype=np.int64)
    stride = max(int(stride), 1)
    indices = np.arange(0, length, stride, dtype=np.int64)
    if indices.size == 0 or indices[-1] != length - 1:
        indices = np.append(indices, length - 1)
    return np.unique(indices)


def _absolute_metric_cache_key(token: tuple[object, ...], target_points: int) -> tuple[object, ...]:
    metric_name = token[0] if len(token) > 0 else None
    source_kind = token[1] if len(token) > 1 else None
    source_id = token[2] if len(token) > 2 else None
    return (metric_name, source_kind, source_id, int(target_points))


def _absolute_heatmap_cache_key(token: tuple[object, ...], target_rows: int) -> tuple[object, ...]:
    history_id = token[0] if len(token) > 0 else None
    axis_key = token[3] if len(token) > 3 else None
    wavelengths_id = token[4] if len(token) > 4 else None
    return (history_id, axis_key, wavelengths_id, int(target_rows))


def sample_absolute_metric_series_for_view(
    x: np.ndarray,
    y: np.ndarray,
    *,
    view_width_px: float | None = None,
    enabled: bool = True,
    minimum_points: int = 128,
    oversample: float = 1.0,
    default_points: int = 2048,
) -> tuple[np.ndarray, np.ndarray]:
    if len(x) == 0 or len(y) == 0:
        return x, y
    finite = np.isfinite(x) & np.isfinite(y)
    if not np.any(finite):
        return x[:0], y[:0]
    x = x[finite]
    y = y[finite]
    if len(x) == 0:
        return x, y
    if not enabled:
        return x, y
    target_points = _target_points_from_width(
        view_width_px,
        enabled=enabled,
        minimum_points=minimum_points,
        oversample=oversample,
        default_points=default_points,
    )
    if len(x) <= target_points:
        return x, y
    target_bins = max(int(np.ceil(target_points / 2.0)), 1)
    indices = _minmax_preserving_downsample_indices(y, target_bins)
    return x[indices], y[indices]


def sample_absolute_heatmap_rows_for_view(
    times: np.ndarray,
    matrix: np.ndarray,
    *,
    view_height_px: float | None = None,
    enabled: bool = True,
    max_rows: int = 2000,
    minimum_rows: int = 256,
    oversample: float = 2.0,
) -> tuple[np.ndarray, np.ndarray]:
    times = np.asarray(times, dtype=np.float64)
    matrix = np.asarray(matrix, dtype=np.float64)
    if len(times) == 0 or len(matrix) == 0:
        return times[:0], matrix[:0]
    if matrix.ndim != 2 or matrix.shape[0] != len(times):
        return times[:0], matrix[:0]
    if not enabled:
        return times, matrix
    target_rows = _target_rows_from_height(
        view_height_px,
        enabled=enabled,
        max_rows=max_rows,
        minimum_rows=minimum_rows,
        oversample=oversample,
    )
    if len(times) <= target_rows:
        return times, matrix
    stride = max(int(np.ceil(len(times) / float(target_rows))), 1)
    indices = np.arange(0, len(times), stride, dtype=np.int64)
    if indices[-1] != len(times) - 1:
        indices = np.append(indices, len(times) - 1)
    return times[indices], matrix[indices]


def downsample_metric_series_for_view(
    x: np.ndarray,
    y: np.ndarray,
    *,
    view_x_min: float | None = None,
    view_x_max: float | None = None,
    view_width_px: float | None = None,
    enabled: bool = True,
    minimum_points: int = 128,
    oversample: float = 1.0,
    default_points: int = 2048,
) -> tuple[np.ndarray, np.ndarray]:
    if len(x) == 0 or len(y) == 0:
        return x, y
    finite = np.isfinite(x) & np.isfinite(y)
    if not np.any(finite):
        return x[:0], y[:0]
    x = x[finite]
    y = y[finite]

    if view_x_min is not None and view_x_max is not None and view_x_max > view_x_min:
        visible = np.flatnonzero((x >= view_x_min) & (x <= view_x_max))
        if len(visible) > 0:
            start = max(int(visible[0]) - 1, 0)
            stop = min(int(visible[-1]) + 2, len(x))
            x = x[start:stop]
            y = y[start:stop]

    if len(x) == 0:
        return x, y

    if not enabled:
        return x, y

    target_points = _target_points_from_width(
        view_width_px,
        enabled=enabled,
        minimum_points=minimum_points,
        oversample=oversample,
        default_points=default_points,
    )

    if len(x) <= target_points:
        return x, y

    if view_x_min is not None and view_x_max is not None and view_x_max > view_x_min:
        start = int(np.searchsorted(x, view_x_min, side="left"))
        stop = int(np.searchsorted(x, view_x_max, side="right"))
        if stop > start:
            start = max(start - 1, 0)
            stop = min(stop + 1, len(x))
            x = x[start:stop]
            y = y[start:stop]

    target_bins = max(1, target_points // 2)
    keep = _peak_preserving_downsample_indices(y, target_bins)
    if len(keep) == 0:
        return x, y
    return x[keep], y[keep]


class PlotViewCache:
    def __init__(self, *, max_view_entries: int = 16) -> None:
        self._max_view_entries = max(int(max_view_entries), 1)
        self._active_trace_series_token: tuple[object, ...] | None = None
        self._active_trace_series_result: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._metric_view_cache: OrderedDict[tuple[object, ...], tuple[np.ndarray, np.ndarray]] = OrderedDict()
        self._heatmap_arrays_token: tuple[object, ...] | None = None
        self._heatmap_arrays_result: tuple[np.ndarray, np.ndarray] = (
            np.empty(0, dtype=np.float64),
            np.empty((0, 0), dtype=np.float64),
        )
        self._heatmap_view_cache: OrderedDict[tuple[object, ...], tuple[np.ndarray, np.ndarray]] = OrderedDict()
        self._absolute_metric_view_cache: OrderedDict[tuple[object, ...], MetricDisplayCache] = OrderedDict()
        self._absolute_heatmap_view_cache: OrderedDict[tuple[object, ...], dict[str, object]] = OrderedDict()
        self._live_absolute_metric_cache: dict[str, MetricDisplayCache] = {}
        self._live_absolute_archive_read_count = 0

    def clear(self) -> None:
        self._active_trace_series_token = None
        self._active_trace_series_result = {}
        self._metric_view_cache.clear()
        self._heatmap_arrays_token = None
        self._heatmap_arrays_result = (
            np.empty(0, dtype=np.float64),
            np.empty((0, 0), dtype=np.float64),
        )
        self._heatmap_view_cache.clear()
        self._absolute_metric_view_cache.clear()
        self._absolute_heatmap_view_cache.clear()
        self._live_absolute_metric_cache.clear()

    def cached_active_trace_series(
        self,
        token: tuple[object, ...],
        builder: Callable[[], dict[str, tuple[np.ndarray, np.ndarray]]],
    ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        if token == self._active_trace_series_token:
            return self._active_trace_series_result
        result = builder()
        self._active_trace_series_token = token
        self._active_trace_series_result = result
        return result

    def clear_live_absolute_metric_cache(self) -> None:
        self._live_absolute_metric_cache.clear()

    def live_absolute_metric_series(
        self,
        metric_names: set[str] | frozenset[str],
    ) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        result: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for metric_name in metric_names:
            cache = self._live_absolute_metric_cache.get(metric_name)
            if not isinstance(cache, MetricDisplayCache):
                continue
            if len(cache.x_display) == 0 or len(cache.y_display) == 0:
                continue
            result[str(metric_name)] = (cache.x_display, cache.y_display)
        return result

    def live_absolute_metric_state(self, metric_name: str) -> tuple[object, ...] | None:
        cache = self._live_absolute_metric_cache.get(metric_name)
        if not isinstance(cache, MetricDisplayCache):
            return None
        return (
            str(metric_name),
            int(cache.display_output_revision),
            int(cache.source_len),
            int(cache.target_points),
            int(len(cache.x_display)),
            float(cache.x_display[0]) if len(cache.x_display) else None,
            float(cache.x_display[-1]) if len(cache.x_display) else None,
        )

    def seed_live_absolute_metric_cache(
        self,
        metric_name: str,
        x: np.ndarray,
        y: np.ndarray,
        *,
        target_points: int,
    ) -> None:
        cache = self._live_absolute_metric_cache.get(metric_name)
        if not isinstance(cache, MetricDisplayCache):
            cache = MetricDisplayCache(target_points=int(target_points))
            self._live_absolute_metric_cache[metric_name] = cache
        cache.source_len = 0
        cache.source_revision += 1
        cache.target_points = int(target_points)
        cache.last_invalidation_reason = "seed"
        cache.levels = _build_envelope_levels(
            np.asarray(x, dtype=np.float64),
            np.asarray(y, dtype=np.float64),
            raw_block_size=cache.raw_block_size,
            combine_factor=cache.combine_factor,
        )
        cache.source_len = int(min(len(x), len(y)))
        cache.last_archive_points = int(cache.source_len)
        cache.rebuild_count += 1
        display_level, blocks = self._select_metric_envelope_blocks(cache, target_points=int(target_points))
        raw_len = min(len(x), len(y))
        force_envelope_at = max(2048, int(int(target_points) * 4))
        if raw_len <= force_envelope_at:
            display_x, display_y = np.asarray(x, dtype=np.float64), np.asarray(y, dtype=np.float64)
        else:
            display_x, display_y = _blocks_to_display_arrays(blocks)
        cache.x_display = display_x
        cache.y_display = display_y
        cache.last_display_level = int(display_level)
        cache.last_display_blocks = int(len(blocks))
        cache.last_display_signature = _display_signature(display_x, display_y)
        cache.display_output_revision += 1
        cache.last_mode = "seeded"
        cache.last_source_used = "archive_seed" if cache.last_archive_points > 0 else "seed"

    def append_live_absolute_metric_point(
        self,
        metric_name: str,
        x_value: float,
        y_value: float,
        *,
        target_points: int,
    ) -> tuple[np.ndarray, np.ndarray]:
        cache = self._live_absolute_metric_cache.get(metric_name)
        if not isinstance(cache, MetricDisplayCache):
            cache = MetricDisplayCache(target_points=int(target_points))
            self._live_absolute_metric_cache[metric_name] = cache
        cache.target_points = int(target_points)
        cache.source_len += 1
        cache.source_revision += 1
        cache.last_invalidation_reason = "live_append"
        block_size = max(int(cache.raw_block_size), 1)
        if not cache.levels:
            cache.levels = [[]]
        current_level = cache.levels[0]
        current_level.append(
            _EnvelopeBlock(
                first_x=float(x_value),
                last_x=float(x_value),
                y_min=float(y_value),
                y_max=float(y_value),
                x_at_y_min=float(x_value),
                x_at_y_max=float(x_value),
                count=1,
            )
        )
        rebuilt_levels: list[list[_EnvelopeBlock]] = [current_level]
        factor = max(int(cache.combine_factor), 2)
        while len(rebuilt_levels[-1]) > factor:
            previous = rebuilt_levels[-1]
            next_level: list[_EnvelopeBlock] = []
            for start in range(0, len(previous), factor):
                combined = _combine_envelope_blocks(previous[start : start + factor])
                if combined is not None:
                    next_level.append(combined)
            if not next_level:
                break
            rebuilt_levels.append(next_level)
        cache.levels = rebuilt_levels
        display_level, blocks = self._select_metric_envelope_blocks(cache, target_points=int(target_points))
        force_envelope_at = max(2048, int(int(target_points) * 4))
        if cache.source_len <= force_envelope_at:
            if len(cache.x_display) == 0:
                cache.x_display = np.asarray([float(x_value)], dtype=np.float64)
                cache.y_display = np.asarray([float(y_value)], dtype=np.float64)
            else:
                cache.x_display = np.concatenate((cache.x_display, np.asarray([float(x_value)], dtype=np.float64)))
                cache.y_display = np.concatenate((cache.y_display, np.asarray([float(y_value)], dtype=np.float64)))
        else:
            cache.x_display, cache.y_display = _blocks_to_display_arrays(blocks)
        cache.last_display_level = int(display_level)
        cache.last_display_blocks = int(len(blocks))
        cache.last_display_signature = _display_signature(cache.x_display, cache.y_display)
        cache.display_output_revision += 1
        cache.last_mode = "incremental"
        cache.last_source_used = "cache_incremental"
        return cache.x_display, cache.y_display

    def live_absolute_metric_view(
        self,
        metric_name: str,
        *,
        target_points: int,
    ) -> tuple[np.ndarray, np.ndarray] | None:
        cache = self._live_absolute_metric_cache.get(metric_name)
        if not isinstance(cache, MetricDisplayCache):
            return None
        if cache.target_points != int(target_points):
            cache.target_points = int(target_points)
        raw_len = int(cache.source_len)
        if not cache.levels:
            return None
        force_envelope_at = max(2048, int(int(target_points) * 4))
        if raw_len <= force_envelope_at:
            if len(cache.x_display) == 0 or len(cache.y_display) == 0:
                return None
            cache.last_source_used = "cache"
            return cache.x_display, cache.y_display
        max_detail_points = max(int(len(cache.levels[0])) * 2, 0)
        if int(target_points) > max_detail_points:
            cache.last_invalidation_reason = "insufficient_detail"
            return None
        display_level, blocks = self._select_metric_envelope_blocks(cache, target_points=int(target_points))
        display_x, display_y = _blocks_to_display_arrays(blocks)
        cache.x_display = display_x
        cache.y_display = display_y
        cache.last_display_level = int(display_level)
        cache.last_display_blocks = int(len(blocks))
        signature = _display_signature(display_x, display_y)
        if signature != cache.last_display_signature:
            cache.display_output_revision += 1
            cache.last_display_signature = signature
        cache.last_mode = "live_recompute"
        cache.last_source_used = "cache_recompute"
        cache.last_invalidation_reason = "reuse"
        return display_x, display_y

    def refresh_live_absolute_metric_cache(
        self,
        metric_names: set[str] | frozenset[str],
        *,
        target_points: int,
        archive_path: Path | None = None,
    ) -> bool:
        metric_names = frozenset(str(name) for name in metric_names)
        needs_archive = False
        for metric_name in metric_names:
            cache = self._live_absolute_metric_cache.get(metric_name)
            if not isinstance(cache, MetricDisplayCache):
                needs_archive = True
                self._live_absolute_metric_archive_read_count = getattr(self, "_live_absolute_metric_archive_read_count", 0) + 1
                break
            if not cache.levels:
                needs_archive = True
                self._live_absolute_metric_archive_read_count = getattr(self, "_live_absolute_metric_archive_read_count", 0) + 1
                break
            raw_len = int(cache.source_len)
            force_envelope_at = max(2048, int(int(target_points) * 4))
            if raw_len <= force_envelope_at:
                continue
            max_detail_points = max(int(len(cache.levels[0])) * 2, 0)
            if int(target_points) > max_detail_points:
                needs_archive = True
                self._live_absolute_metric_archive_read_count = getattr(self, "_live_absolute_metric_archive_read_count", 0) + 1
                break

        if needs_archive:
            if archive_path is None:
                return False
            from lspr_app.storage.hdf5_export import load_processed_metric_history

            archive_series = load_processed_metric_history(Path(archive_path), set(metric_names))
            if not archive_series:
                return False
            self._live_absolute_archive_read_count += 1
            for metric_name in metric_names:
                series = archive_series.get(metric_name)
                if series is None:
                    continue
                x, y = series
                cache = self._live_absolute_metric_cache.get(metric_name)
                if not isinstance(cache, MetricDisplayCache):
                    cache = MetricDisplayCache(target_points=int(target_points))
                    self._live_absolute_metric_cache[metric_name] = cache
                cache.archive_read_count = int(self._live_absolute_archive_read_count)
                cache.last_archive_points = int(min(len(x), len(y)))
                cache.last_invalidation_reason = "archive_rebuild"
                self.seed_live_absolute_metric_cache(
                    metric_name,
                    x,
                    y,
                    target_points=int(target_points),
                )
            return True

        refreshed = False
        for metric_name in metric_names:
            cache = self._live_absolute_metric_cache.get(metric_name)
            if isinstance(cache, MetricDisplayCache):
                cache.last_invalidation_reason = "reuse"
            if self.live_absolute_metric_view(metric_name, target_points=int(target_points)) is not None:
                refreshed = True
        return refreshed

    def _rebuild_metric_envelope_cache(
        self,
        cache: MetricDisplayCache,
        x: np.ndarray,
        y: np.ndarray,
        *,
        target_points: int,
    ) -> int:
        started = perf_counter()
        cache.source_len = 0
        cache.target_points = int(target_points)
        cache.levels = _build_envelope_levels(
            np.asarray(x, dtype=np.float64),
            np.asarray(y, dtype=np.float64),
            raw_block_size=cache.raw_block_size,
            combine_factor=cache.combine_factor,
        )
        cache.source_len = int(len(x))
        cache.display_revision += 1
        cache.rebuild_count += 1
        cache.last_mode = "full_rebuild"
        cache.last_new_points_processed = int(len(x))
        cache.last_base_blocks = len(cache.levels[0]) if cache.levels else 0
        cache.last_levels = len(cache.levels)
        cache.last_tail_groups_updated = cache.last_base_blocks
        cache.last_full_rebuild_ms = (perf_counter() - started) * 1000.0
        cache.last_append_ms = None
        return cache.last_base_blocks

    def _append_metric_envelope_cache(
        self,
        cache: MetricDisplayCache,
        x_new: np.ndarray,
        y_new: np.ndarray,
    ) -> int:
        if len(x_new) == 0 or len(y_new) == 0:
            cache.last_new_points_processed = 0
            cache.last_tail_groups_updated = 0
            cache.last_append_ms = 0.0
            return 0
        started = perf_counter()
        block_size = max(int(cache.raw_block_size), 1)
        block_count = int(np.ceil(float(len(x_new)) / float(block_size)))
        cache.levels = _append_envelope_levels(
            cache.levels,
            np.asarray(x_new, dtype=np.float64),
            np.asarray(y_new, dtype=np.float64),
            raw_block_size=cache.raw_block_size,
            combine_factor=cache.combine_factor,
        )
        cache.source_len += int(len(x_new))
        cache.display_revision += 1
        cache.incremental_count += 1
        cache.last_mode = "incremental"
        cache.last_new_points_processed = int(len(x_new))
        cache.last_base_blocks = len(cache.levels[0]) if cache.levels else 0
        cache.last_levels = len(cache.levels)
        cache.last_tail_groups_updated = block_count
        cache.last_append_ms = (perf_counter() - started) * 1000.0
        return block_count

    def _select_metric_envelope_blocks(
        self,
        cache: MetricDisplayCache,
        *,
        target_points: int,
    ) -> tuple[int, list[_EnvelopeBlock]]:
        if not cache.levels:
            return -1, []
        target_bins = max(int(np.ceil(float(max(target_points, 1)) / 2.0)), 1)
        chosen_level = 0
        chosen_blocks = cache.levels[0]
        for level_index, level in enumerate(cache.levels):
            chosen_level = level_index
            chosen_blocks = level
            if len(level) <= target_bins:
                break
        return chosen_level, chosen_blocks

    def metric_view(
        self,
        token: tuple[object, ...],
        x: np.ndarray,
        y: np.ndarray,
        *,
        view_x_min: float | None = None,
        view_x_max: float | None = None,
        view_width_px: float | None = None,
        enabled: bool = True,
        minimum_points: int = 128,
        oversample: float = 1.0,
        default_points: int = 2048,
    ) -> tuple[np.ndarray, np.ndarray]:
        target_points = _target_points_from_width(
            view_width_px,
            enabled=enabled,
            minimum_points=minimum_points,
            oversample=oversample,
            default_points=default_points,
        )
        cache_key = (
            token,
            round(view_x_min, 6) if view_x_min is not None else None,
            round(view_x_max, 6) if view_x_max is not None else None,
            int(target_points),
        )
        cached = self._metric_view_cache.get(cache_key)
        if cached is not None:
            self._metric_view_cache.move_to_end(cache_key)
            return cached
        result = downsample_metric_series_for_view(
            x,
            y,
            view_x_min=view_x_min,
            view_x_max=view_x_max,
            view_width_px=view_width_px,
            enabled=enabled,
            minimum_points=minimum_points,
            oversample=oversample,
            default_points=default_points,
        )
        self._metric_view_cache[cache_key] = result
        self._metric_view_cache.move_to_end(cache_key)
        while len(self._metric_view_cache) > self._max_view_entries:
            self._metric_view_cache.popitem(last=False)
        return result

    def heatmap_arrays(
        self,
        token: tuple[object, ...],
        builder: Callable[[], tuple[np.ndarray, np.ndarray]],
    ) -> tuple[np.ndarray, np.ndarray]:
        if token == self._heatmap_arrays_token:
            return self._heatmap_arrays_result
        result = builder()
        self._heatmap_arrays_token = token
        self._heatmap_arrays_result = result
        return result

    def heatmap_arrays_from_history(
        self,
        token: tuple[object, ...],
        history: list[tuple[float, np.ndarray]],
        builder: Callable[[list[tuple[float, np.ndarray]]], tuple[np.ndarray, np.ndarray]],
    ) -> tuple[np.ndarray, np.ndarray]:
        if token == self._heatmap_arrays_token:
            return self._heatmap_arrays_result
        previous_token = self._heatmap_arrays_token
        previous_times, previous_matrix = self._heatmap_arrays_result
        if (
            isinstance(previous_token, tuple)
            and len(previous_token) >= 4
            and len(token) >= 4
            and previous_token[0] == token[0]
            and previous_token[3] == token[3]
            and len(history) == len(previous_times) + 1
            and len(previous_times) > 0
        ):
            last_time, last_row = history[-1]
            times = np.concatenate((previous_times, np.asarray([float(last_time)], dtype=np.float64)))
            row = np.asarray(last_row, dtype=np.float64)
            if previous_matrix.ndim == 2 and previous_matrix.shape[1] == len(row):
                matrix = np.vstack((previous_matrix, row[np.newaxis, :]))
                self._heatmap_arrays_token = token
                self._heatmap_arrays_result = (times, matrix)
                return self._heatmap_arrays_result
        result = builder(history)
        self._heatmap_arrays_token = token
        self._heatmap_arrays_result = result
        return result

    def absolute_metric_view(
        self,
        token: tuple[object, ...],
        x: np.ndarray,
        y: np.ndarray,
        *,
        view_width_px: float | None = None,
        enabled: bool = True,
        minimum_points: int = 128,
        oversample: float = 1.0,
        default_points: int = 2048,
    ) -> tuple[np.ndarray, np.ndarray]:
        target_points = _target_points_from_width(
            view_width_px,
            enabled=enabled,
            minimum_points=minimum_points,
            oversample=oversample,
            default_points=default_points,
        )
        cache_key = _absolute_metric_cache_key(token, target_points)
        source_revision = token[3] if len(token) > 3 else None
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        if len(x) == 0 or len(y) == 0:
            result = (x[:0], y[:0])
            cache = self._absolute_metric_view_cache.get(cache_key)
            if not isinstance(cache, MetricDisplayCache):
                cache = MetricDisplayCache()
            cache.source_revision = int(source_revision or 0)
            cache.source_len = 0
            cache.target_points = int(target_points)
            cache.x_display = result[0]
            cache.y_display = result[1]
            cache.levels = []
            cache.last_mode = "empty"
            cache.display_output_revision += 1
            cache.last_display_signature = _display_signature(cache.x_display, cache.y_display)
            self._absolute_metric_view_cache[cache_key] = cache
            return result
        if not enabled:
            cache = self._absolute_metric_view_cache.get(cache_key)
            if not isinstance(cache, MetricDisplayCache):
                cache = MetricDisplayCache()
            cache.source_revision = int(source_revision or 0)
            cache.source_len = len(x)
            cache.target_points = int(target_points)
            cache.x_display = x
            cache.y_display = y
            cache.levels = []
            cache.last_mode = "disabled"
            cache.display_output_revision += 1
            cache.last_display_signature = _display_signature(cache.x_display, cache.y_display)
            self._absolute_metric_view_cache[cache_key] = cache
            return x, y

        cached = self._absolute_metric_view_cache.get(cache_key)
        if not isinstance(cached, MetricDisplayCache):
            cached = MetricDisplayCache(target_points=int(target_points))
            self._absolute_metric_view_cache[cache_key] = cached
        if (
            cached.source_len == len(x)
            and cached.target_points == int(target_points)
            and cached.source_revision == int(source_revision or 0)
        ):
            cached.hit_count += 1
            cached.last_mode = "hit"
            self._absolute_metric_view_cache.move_to_end(cache_key)
            return cached.x_display, cached.y_display
        if (
            cached.source_len > len(x)
            or cached.source_len < 0
            or cached.source_revision > int(source_revision or 0)
            or not cached.levels
        ):
            self._rebuild_metric_envelope_cache(cached, x, y, target_points=target_points)
        else:
            old_len = int(cached.source_len)
            self._append_metric_envelope_cache(cached, x[old_len:], y[old_len:])
            cached.target_points = int(target_points)
            cached.source_revision = int(source_revision or 0)
        display_level, blocks = self._select_metric_envelope_blocks(cached, target_points=int(target_points))
        raw_len = min(len(x), len(y))
        force_envelope_at = max(2048, int(int(target_points) * 4))
        assemble_started = perf_counter()
        if raw_len <= force_envelope_at:
            display_x, display_y = x, y
        else:
            display_x, display_y = _blocks_to_display_arrays(blocks)
        cached.last_assemble_ms = (perf_counter() - assemble_started) * 1000.0
        cached.last_display_level = int(display_level)
        cached.last_display_blocks = int(len(blocks))
        signature = _display_signature(display_x, display_y)
        if signature != cached.last_display_signature:
            cached.display_output_revision += 1
            cached.last_display_signature = signature
        cached.x_display = display_x
        cached.y_display = display_y
        cached.target_points = int(target_points)
        cached.source_revision = int(source_revision or 0)
        self._absolute_metric_view_cache.move_to_end(cache_key)
        while len(self._absolute_metric_view_cache) > self._max_view_entries:
            self._absolute_metric_view_cache.popitem(last=False)
        return display_x, display_y

    def absolute_metric_display_state(self, token: tuple[object, ...]) -> tuple[object, ...] | None:
        for cache_key, cached in reversed(list(self._absolute_metric_view_cache.items())):
            if not isinstance(cached, MetricDisplayCache):
                continue
            if len(cache_key) < 3:
                continue
            if cache_key[0] != token[0] or cache_key[1] != token[1] or cache_key[2] != token[2]:
                continue
            return (
                cache_key,
                int(cached.display_output_revision),
                int(len(cached.x_display)),
                float(cached.x_display[0]) if len(cached.x_display) else None,
                float(cached.x_display[-1]) if len(cached.x_display) else None,
            )
        return None

    def metric_cache_debug_snapshot(self) -> dict[str, object]:
        modes: dict[str, object] = {}
        for key, cached in self._absolute_metric_view_cache.items():
            if isinstance(cached, MetricDisplayCache):
                modes[str(key)] = {
                    "source_len": int(cached.source_len),
                    "target_points": int(cached.target_points),
                    "display_len": int(len(cached.x_display)),
                    "last_mode": str(cached.last_mode),
                    "last_source_used": str(cached.last_source_used),
                    "last_invalidation_reason": str(cached.last_invalidation_reason),
                    "archive_read_count": int(cached.archive_read_count),
                    "last_archive_points": int(cached.last_archive_points),
                    "hits": int(cached.hit_count),
                    "incremental": int(cached.incremental_count),
                    "rebuilds": int(cached.rebuild_count),
                    "levels": [len(level) for level in cached.levels],
                    "new_points": int(cached.last_new_points_processed),
                    "base_blocks": int(cached.last_base_blocks),
                    "level_count": int(cached.last_levels),
                    "tail_groups_updated": int(cached.last_tail_groups_updated),
                    "display_level": int(cached.last_display_level),
                    "display_blocks": int(cached.last_display_blocks),
                    "append_ms": cached.last_append_ms,
                    "assemble_ms": cached.last_assemble_ms,
                    "full_rebuild_ms": cached.last_full_rebuild_ms,
                }
        return modes

    def absolute_heatmap_view(
        self,
        token: tuple[object, ...],
        times: np.ndarray,
        matrix: np.ndarray,
        *,
        view_height_px: float | None = None,
        enabled: bool = True,
        max_rows: int = 2000,
        minimum_rows: int = 256,
        oversample: float = 2.0,
    ) -> tuple[np.ndarray, np.ndarray]:
        target_rows = _target_rows_from_height(
            view_height_px,
            enabled=enabled,
            max_rows=max_rows,
            minimum_rows=minimum_rows,
            oversample=oversample,
        )
        cache_key = _absolute_heatmap_cache_key(token, target_rows)
        times = np.asarray(times, dtype=np.float64)
        matrix = np.asarray(matrix, dtype=np.float64)
        if len(times) == 0 or len(matrix) == 0:
            result = (times[:0], matrix[:0])
            self._absolute_heatmap_view_cache[cache_key] = {
                "source_len": 0,
                "stride": 1,
                "result": result,
            }
            return result
        if matrix.ndim != 2 or matrix.shape[0] != len(times):
            result = (times[:0], matrix[:0])
            self._absolute_heatmap_view_cache[cache_key] = {
                "source_len": 0,
                "stride": 1,
                "result": result,
            }
            return result
        if not enabled:
            result = (times, matrix)
            self._absolute_heatmap_view_cache[cache_key] = {
                "source_len": len(times),
                "stride": 1,
                "result": result,
            }
            return result

        desired_stride = max(1, int(np.ceil(len(times) / float(max(target_rows, 1)))))
        cached = self._absolute_heatmap_view_cache.get(cache_key)
        if not isinstance(cached, dict) or int(cached.get("source_len", -1)) > len(times):
            cached = None
        if cached is None or int(cached.get("stride", 0)) != desired_stride:
            indices = _stride_sample_indices(len(times), desired_stride)
            result = (times[indices], matrix[indices])
            self._absolute_heatmap_view_cache[cache_key] = {
                "source_len": len(times),
                "stride": desired_stride,
                "result": result,
            }
            self._absolute_heatmap_view_cache.move_to_end(cache_key)
            while len(self._absolute_heatmap_view_cache) > self._max_view_entries:
                self._absolute_heatmap_view_cache.popitem(last=False)
            return result

        sampled_times, sampled_matrix = cached["result"]  # type: ignore[index]
        sampled_times = np.asarray(sampled_times, dtype=np.float64)
        sampled_matrix = np.asarray(sampled_matrix, dtype=np.float64)
        previous_len = int(cached.get("source_len", len(times)))
        if previous_len == len(times):
            return sampled_times, sampled_matrix
        if previous_len < 0 or previous_len > len(times):
            indices = _stride_sample_indices(len(times), desired_stride)
            result = (times[indices], matrix[indices])
            self._absolute_heatmap_view_cache[cache_key] = {
                "source_len": len(times),
                "stride": desired_stride,
                "result": result,
            }
            return result
        first_new_index = ((previous_len + desired_stride - 1) // desired_stride) * desired_stride
        if first_new_index < len(times):
            new_indices = _stride_sample_indices(len(times) - first_new_index, desired_stride) + first_new_index
            if len(new_indices) > 0:
                sampled_times = np.concatenate((sampled_times, times[new_indices]))
                sampled_matrix = np.vstack((sampled_matrix, matrix[new_indices]))
        result = (sampled_times, sampled_matrix)
        self._absolute_heatmap_view_cache[cache_key] = {
            "source_len": len(times),
            "stride": desired_stride,
            "result": result,
        }
        self._absolute_heatmap_view_cache.move_to_end(cache_key)
        while len(self._absolute_heatmap_view_cache) > self._max_view_entries:
            self._absolute_heatmap_view_cache.popitem(last=False)
        return result

    def refresh_live_absolute_heatmap_cache(
        self,
        token: tuple[object, ...],
        history: list[tuple[float, np.ndarray]],
        *,
        max_rows: int,
        view_height_px: float | None,
    ) -> tuple[np.ndarray, np.ndarray] | None:
        times, matrix = self.heatmap_arrays_from_history(token, history, build_heatmap_arrays)
        if len(times) == 0 or len(matrix) == 0:
            return None
        return self.absolute_heatmap_view(
            token,
            times,
            matrix,
            view_height_px=view_height_px,
            enabled=True,
            max_rows=max_rows,
            minimum_rows=256,
            oversample=2.0,
        )

    def heatmap_view(
        self,
        token: tuple[object, ...],
        times: np.ndarray,
        matrix: np.ndarray,
        *,
        view_x_min: float | None = None,
        view_x_max: float | None = None,
        max_rows: int = 2000,
        view_height_px: float | None = None,
        oversample: float = 2.0,
        minimum_rows: int = 256,
        enabled: bool = True,
    ) -> tuple[np.ndarray, np.ndarray]:
        target_rows = _target_rows_from_height(
            view_height_px,
            enabled=enabled,
            max_rows=max_rows,
            minimum_rows=minimum_rows,
            oversample=oversample,
        )
        cache_key = (
            token,
            round(view_x_min, 6) if view_x_min is not None else None,
            round(view_x_max, 6) if view_x_max is not None else None,
            int(target_rows),
        )
        cached = self._heatmap_view_cache.get(cache_key)
        if cached is not None:
            self._heatmap_view_cache.move_to_end(cache_key)
            return cached
        result = select_heatmap_rows_for_view(
            times,
            matrix,
            view_x_min=view_x_min,
            view_x_max=view_x_max,
            max_rows=target_rows,
            view_height_px=view_height_px,
            oversample=oversample,
            minimum_rows=minimum_rows,
            enabled=enabled,
        )
        self._heatmap_view_cache[cache_key] = result
        self._heatmap_view_cache.move_to_end(cache_key)
        while len(self._heatmap_view_cache) > self._max_view_entries:
            self._heatmap_view_cache.popitem(last=False)
        return result
