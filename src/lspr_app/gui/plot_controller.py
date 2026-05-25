from __future__ import annotations

import logging
from datetime import datetime
from typing import TypeVar

from PyQt6.QtCore import QRectF

import numpy as np
import pyqtgraph as pg

from lspr_app.domain.models import Spectrum

T = TypeVar("T")


def trim_history_tail_in_place(history: list[T], max_len: int) -> None:
    if max_len <= 0:
        history.clear()
        return
    excess = len(history) - max_len
    if excess > 0:
        del history[:excess]


def _current_trace_view_state(window) -> tuple[float | None, float | None, float | None]:
    trace_plot = getattr(window, "trace_plot", None)
    if trace_plot is None:
        return None, None, None
    try:
        plot_item = trace_plot.getPlotItem()
        view_box = plot_item.vb
        view_range = view_box.viewRange()
        x_range = view_range[0]
        view_x_min = float(x_range[0])
        view_x_max = float(x_range[1])
        if not np.isfinite(view_x_min) or not np.isfinite(view_x_max):
            return None, None, None
        scene_rect = view_box.sceneBoundingRect()
        view_width_px = float(scene_rect.width()) if scene_rect is not None else None
        if view_width_px is not None and not np.isfinite(view_width_px):
            view_width_px = None
        return view_x_min, view_x_max, view_width_px
    except Exception:
        return None, None, None


def _nearest_sorted_index(values: np.ndarray, target: float) -> int:
    if len(values) <= 1:
        return 0
    index = int(np.searchsorted(values, target))
    if index <= 0:
        return 0
    if index >= len(values):
        return len(values) - 1
    left_index = index - 1
    right_index = index
    left_distance = abs(float(values[left_index]) - target)
    right_distance = abs(float(values[right_index]) - target)
    return right_index if right_distance < left_distance else left_index


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


def downsample_trace_series_for_view(
    x: np.ndarray,
    y: np.ndarray,
    *,
    view_x_min: float | None = None,
    view_x_max: float | None = None,
    view_width_px: float | None = None,
    enabled: bool = True,
    minimum_points: int = 256,
    oversample: float = 2.0,
    default_points: int = 4096,
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

    if view_width_px is None or view_width_px <= 0:
        target_points = default_points
    else:
        target_points = max(minimum_points, int(view_width_px * oversample))

    if len(x) <= target_points:
        return x, y

    target_bins = max(1, target_points // 2)
    keep = _peak_preserving_downsample_indices(y, target_bins)
    if len(keep) == 0:
        return x, y
    return x[keep], y[keep]


def downsample_spectrum_series_for_view(
    x: np.ndarray,
    y: np.ndarray,
    *,
    view_x_min: float | None = None,
    view_x_max: float | None = None,
    view_width_px: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    return downsample_trace_series_for_view(
        x,
        y,
        view_x_min=view_x_min,
        view_x_max=view_x_max,
        view_width_px=view_width_px,
        minimum_points=192,
        oversample=1.5,
        default_points=2048,
    )


def clip_series_to_window(
    x: np.ndarray,
    y: np.ndarray,
    *,
    window_min: float | None,
    window_max: float | None,
) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(x, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    if len(x) == 0 or len(y) == 0:
        return x[:0], y[:0]
    if window_min is None or window_max is None or not np.isfinite(window_min) or not np.isfinite(window_max):
        return x, y
    low = float(window_min)
    high = float(window_max)
    if high <= low:
        return x[:0], y[:0]
    mask = np.isfinite(x) & np.isfinite(y) & (x >= low) & (x <= high)
    if not np.any(mask):
        return x[:0], y[:0]
    return x[mask], y[mask]


def spectrum_render_cache_key(
    processed: Spectrum | None,
    fit: Spectrum | None,
    *,
    view_x_min: float | None,
    view_x_max: float | None,
    view_width_px: float | None,
    residual_visible: bool,
    show_gaussian: bool,
    peak_tracking_mode: str,
) -> tuple[object, ...]:
    def _spec_key(spectrum: Spectrum | None) -> tuple[object, ...] | None:
        if spectrum is None:
            return None
        wavelengths = np.asarray(spectrum.wavelengths_nm, dtype=np.float64)
        return (
            id(spectrum),
            spectrum.acquired_at,
            len(wavelengths),
            float(wavelengths[0]) if len(wavelengths) else None,
            float(wavelengths[-1]) if len(wavelengths) else None,
            spectrum.y_label,
            spectrum.metadata.get("fit_method"),
            spectrum.metadata.get("fit_window_min_nm"),
            spectrum.metadata.get("fit_window_max_nm"),
        )

    return (
        _spec_key(processed),
        _spec_key(fit),
        round(view_x_min, 6) if view_x_min is not None else None,
        round(view_x_max, 6) if view_x_max is not None else None,
        round(view_width_px, 3) if view_width_px is not None else None,
        bool(residual_visible),
        bool(show_gaussian),
        peak_tracking_mode,
    )


def downsample_sensorgram_history_for_view(
    history: list[tuple[float, np.ndarray]],
    *,
    view_x_min: float | None = None,
    view_x_max: float | None = None,
    max_rows: int = 2000,
    view_height_px: float | None = None,
    oversample: float = 2.0,
    minimum_rows: int = 256,
    enabled: bool = True,
) -> list[tuple[float, np.ndarray]]:
    if not history:
        return history
    if view_x_min is not None and view_x_max is not None and view_x_max > view_x_min:
        visible = [item for item in history if view_x_min <= float(item[0]) <= view_x_max]
        if visible:
            history = visible
    if not enabled:
        return history
    if view_height_px is not None and view_height_px > 0:
        target_rows = max(minimum_rows, int(view_height_px * oversample))
        max_rows = min(max_rows, target_rows)
    if max_rows <= 0 or len(history) <= max_rows:
        return history

    indices = np.linspace(0, len(history) - 1, num=max_rows, dtype=np.int64)
    indices = np.unique(indices)
    return [history[int(index)] for index in indices]


def flush_deferred_ui_refreshes(window) -> None:
    if window._plots_frozen:
        return
    did_work = False
    if window._ui_live_estimate_dirty:
        window._update_live_estimate()
        window._ui_live_estimate_dirty = False
        did_work = True
    if window._ui_telemetry_dirty:
        window._refresh_telemetry()
        window._ui_telemetry_dirty = False
        did_work = True
    if window._ui_trace_plot_dirty and not bool(getattr(window, "_sensorgram_frozen", False)):
        window._refresh_trace_plot(window._pending_trace_label)
        window._ui_trace_plot_dirty = False
        did_work = True
    if window._ui_summary_dirty:
        window._refresh_session_summary()
        window._ui_summary_dirty = False
        did_work = True
    if window._ui_stats_dirty:
        window._update_spectrum_stats(window._last_processed_plot, window._last_fit_plot)
        window._update_trace_stats()
        window._ui_stats_dirty = False
        did_work = True
    if window._ui_session_stats_dirty:
        window._refresh_session_statistics()
        window._ui_session_stats_dirty = False
        did_work = True
    if did_work:
        window._log_throttled(
            "ui_flush",
            f"UI flush | trace_dirty={window._ui_trace_plot_dirty} | stats_dirty={window._ui_stats_dirty} | display_rate={window.live_rate_spin.value():.2f} Hz",
            level=logging.DEBUG,
            min_interval=1.0,
        )


def flush_plot_refreshes(window) -> None:
    if window._plots_frozen:
        window._plot_render_dirty = False
        return
    if window._plot_render_dirty:
        plot_mode = window.PLOT_MODES[window.plot_selector.currentText()]
        plot_spectrum = window._session.get_plot_data(plot_mode)
        plot_fit = window._last_fit_plot if plot_mode in {"sample", "absorbance"} else None
        window._refresh_spectrum_plot(plot_spectrum, plot_fit)
        window._plot_render_dirty = False


def refresh_plot(window) -> None:
    window._enqueue_plot_processing()


def autoscale_spectrum_plot(window) -> None:
    processed = window._last_processed_plot
    if processed is None or len(processed.wavelengths_nm) == 0:
        window.spectrum_plot.enableAutoRange(axis=pg.ViewBox.XYAxes, enable=True)
        window.spectrum_plot.autoRange()
        return
    x = np.asarray(processed.wavelengths_nm, dtype=np.float64)
    y = np.asarray(processed.values, dtype=np.float64)
    finite = np.isfinite(y)
    if not np.any(finite):
        return
    y = y[finite]
    x_finite = x[finite]
    x_pad = max((float(np.max(x_finite)) - float(np.min(x_finite))) * 0.02, 1e-6)
    y_span = float(np.max(y) - np.min(y))
    y_pad = max(y_span * 0.08, 1e-6)
    window.spectrum_plot.setXRange(float(np.min(x_finite)) - x_pad, float(np.max(x_finite)) + x_pad, padding=0.0)
    window.spectrum_plot.setYRange(float(np.min(y)) - y_pad, float(np.max(y)) + y_pad, padding=0.0)
    window._autoscale_residual_axis()


def autoscale_trace_plot(window) -> None:
    content_mode = getattr(window, "_sensorgram_content_mode", "metric")
    if content_mode == "heatmap":
        window._trace_view_autoscaling = True
        try:
            render_sensorgram_heatmap(window, window._sensorgram_heatmap_history, clock_mode=not bool(window._measurement_active))
        finally:
            window._trace_view_autoscaling = False
        return
    series = window._active_trace_series()
    if not series:
        window.trace_plot.enableAutoRange(axis=pg.ViewBox.XYAxes, enable=True)
        window.trace_plot.autoRange()
        return
    x = np.concatenate([item[0] for item in series.values()])
    y = np.concatenate([item[1] for item in series.values()])
    finite = np.isfinite(x) & np.isfinite(y)
    if not np.any(finite):
        return
    x = x[finite]
    y = y[finite]
    latest_x = float(np.max(x))
    x_span = float(np.max(x) - np.min(x))
    y_span = float(np.max(y) - np.min(y))
    view_mode = getattr(window, "_sensorgram_view_mode", "absolute")
    if view_mode == "rolling":
        window_span = max(float(window._trace_display_window_s), 1e-9)
        x_min = max(float(np.min(x)), latest_x - window_span)
        x_max = latest_x
        x_pad = max(window_span * 0.03, 1.0 if window.trace_time_axis._mode == "clock" else 1e-3)
    else:
        x_min = float(np.min(x))
        x_max = latest_x
        x_pad = max(x_span * 0.03, 1.0 if window.trace_time_axis._mode == "clock" else 1e-3)
    y_pad = max(y_span * 0.12, 1e-6)
    if getattr(window, "_trace_view_locked", False):
        return
    window._trace_view_autoscaling = True
    try:
        if view_mode == "rolling":
            window.trace_plot.setXRange(x_min, x_max + x_pad, padding=0.0)
        else:
            window.trace_plot.setXRange(x_min, x_max + x_pad, padding=0.0)
        window.trace_plot.setYRange(float(np.min(y)) - y_pad, float(np.max(y)) + y_pad, padding=0.0)
    finally:
        window._trace_view_autoscaling = False


def update_residual_view_geometry(window) -> None:
    if not hasattr(window, "residual_view"):
        return
    spectrum_vb = window.spectrum_plot.getPlotItem().vb
    window.residual_view.setGeometry(spectrum_vb.sceneBoundingRect())
    window.residual_view.linkedViewChanged(spectrum_vb, pg.ViewBox.XAxis)


def autoscale_residual_axis(window) -> None:
    if not hasattr(window, "residual_view") or not window.show_residual_button.isChecked():
        return
    residual = window.residual_curve.yData
    if residual is None or len(residual) == 0:
        window.residual_view.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
        return
    y = np.asarray(residual, dtype=np.float64)
    finite = np.isfinite(y)
    if not np.any(finite):
        return
    y = y[finite]
    center = float(np.median(y))
    amplitude = float(np.percentile(np.abs(y - center), 95))
    amplitude = max(amplitude, float(np.max(np.abs(y - center))), 1e-9)
    pad = max(amplitude * 0.25, 1e-6)
    if not np.isfinite(pad):
        pad = 1e-6
    window.residual_view.setYRange(center - (amplitude + pad), center + (amplitude + pad), padding=0.0)


def update_residual_axis_visibility(window, visible: bool | None = None) -> None:
    if visible is None:
        visible = window.show_residual_button.isChecked()
    if hasattr(window, "residual_axis"):
        window.residual_axis.setVisible(visible)
    if hasattr(window, "residual_view"):
        window.residual_view.setVisible(visible)


def request_trace_autoscale(window) -> None:
    if window._plots_frozen or bool(getattr(window, "_sensorgram_frozen", False)):
        return
    window._trace_autoscale_timer.start()


def refresh_trace_plot(window, trace_label: str) -> None:
    if window._plots_frozen:
        return
    clock_mode = not bool(window._measurement_active)
    content_mode = getattr(window, "_sensorgram_content_mode", "metric")
    if content_mode == "heatmap":
        window._visible_trace_mode = "clock" if clock_mode else "elapsed"
        window.trace_time_axis.set_mode("clock" if clock_mode else "elapsed")
        window.trace_plot.setLabel("left", "Wavelength (nm)")
        window.trace_plot.setLabel("bottom", "Time (local)" if clock_mode else "Time (s)")
        if window._peak_history:
            render_trace_series(window, window._peak_history, clock_mode=clock_mode)
        else:
            for curve in window.trace_curves.values():
                curve.setData([], [])
                curve.setVisible(False)
        render_sensorgram_heatmap(window, window._sensorgram_heatmap_history, clock_mode=clock_mode)
        request_trace_autoscale(window)
        return
    if window._peak_history:
        window._visible_trace_mode = "clock" if clock_mode else "elapsed"
        window.trace_time_axis.set_mode("clock" if clock_mode else "elapsed")
        window.trace_plot.setLabel("left", trace_label)
        window.trace_plot.setLabel("bottom", "Time (local)" if clock_mode else "Time (s)")
        render_trace_series(window, window._peak_history, clock_mode=clock_mode)
        window.trace_heatmap_image.setVisible(False)
        if hasattr(window, "trace_legend"):
            window.trace_legend.setVisible(True)
        for curve in window.trace_curves.values():
            curve.setVisible(True)
        request_trace_autoscale(window)
        return

    if content_mode == "heatmap":
        window.trace_plot.setLabel("left", "Wavelength (nm)")
    else:
        window.trace_plot.setLabel("left", trace_label)
    window.trace_time_axis.set_mode("clock" if clock_mode else "elapsed")
    window.trace_plot.setLabel("bottom", "Time (local)" if clock_mode else "Time (s)")
    for curve in window.trace_curves.values():
        curve.setData([], [])
        curve.setVisible(content_mode != "heatmap")
    if hasattr(window, "trace_heatmap_image"):
        window.trace_heatmap_image.setVisible(False)
    if hasattr(window, "trace_legend"):
        window.trace_legend.setVisible(content_mode != "heatmap")
    window._visible_trace_x = None
    window._visible_trace_y = None
    window._visible_trace_mode = "clock" if clock_mode else "elapsed"
    request_trace_autoscale(window)
    window._log_throttled(
        "trace_refresh",
        f"Trace updated | {trace_label}",
        level=logging.DEBUG,
        min_interval=1.5,
    )
    request_trace_autoscale(window)


def render_trace_series(
    window,
    history: dict[str, list[tuple[object, float]]],
    clock_mode: bool,
) -> None:
    def _trace_x_value(raw_x: object) -> float:
        if hasattr(raw_x, "timestamp"):
            return float(raw_x.timestamp())
        return float(raw_x)

    view_mode = getattr(window, "_sensorgram_view_mode", "absolute")
    trace_view_locked = bool(getattr(window, "_trace_view_locked", False))
    _, _, view_width_px = _current_trace_view_state(window)
    view_x_min = view_x_max = None
    if trace_view_locked:
        view_x_min, view_x_max, view_width_px = _current_trace_view_state(window)
    elif view_mode == "rolling" and len(history) > 0:
        latest_x = None
        for series in history.values():
            for item in series:
                x_value = _trace_x_value(item[0])
                if latest_x is None or x_value > latest_x:
                    latest_x = x_value
        if latest_x is not None:
            window_span = max(float(getattr(window, "_trace_display_window_s", 60.0)), 1e-9)
            view_x_max = latest_x
            view_x_min = latest_x - window_span
    active_series = {}
    for metric_name, curve in window.trace_curves.items():
        series = history.get(metric_name, [])
        if metric_name not in window._selected_trace_metrics() or not series:
            curve.setData([], [])
            continue
        x = np.asarray([_trace_x_value(item[0]) for item in series], dtype=np.float64)
        y = np.asarray([item[1] for item in series], dtype=np.float64)
        x, y = downsample_trace_series_for_view(
            x,
            y,
            view_x_min=view_x_min,
            view_x_max=view_x_max,
            view_width_px=view_width_px,
            enabled=bool(getattr(window, "_sensorgram_downsampling_enabled", True)),
        )
        curve.setData(x, y)
        active_series[metric_name] = (x, y)

    primary_name = window._primary_trace_metric()
    if primary_name in active_series:
        window._visible_trace_x = active_series[primary_name][0].copy()
        window._visible_trace_y = active_series[primary_name][1].copy()
    elif active_series:
        first_series = next(iter(active_series.values()))
        window._visible_trace_x = first_series[0].copy()
        window._visible_trace_y = first_series[1].copy()
    else:
        window._visible_trace_x = None
        window._visible_trace_y = None


def render_sensorgram_heatmap(
    window,
    history: list[tuple[float, np.ndarray]],
    clock_mode: bool,
) -> None:
    if not hasattr(window, "trace_heatmap_image"):
        return
    if not history or getattr(window, "_sensorgram_heatmap_wavelengths", None) is None:
        window.trace_heatmap_image.setVisible(False)
        for curve in window.trace_curves.values():
            curve.setVisible(False)
        if hasattr(window, "trace_legend"):
            window.trace_legend.setVisible(False)
        return

    view_mode = getattr(window, "_sensorgram_view_mode", "absolute")
    trace_view_locked = bool(getattr(window, "_trace_view_locked", False))
    _, _, _view_width_px = _current_trace_view_state(window)
    view_x_min = view_x_max = None
    if trace_view_locked:
        view_x_min, view_x_max, _view_width_px = _current_trace_view_state(window)
    elif view_mode == "rolling" and len(history) > 0:
        latest_x = float(history[-1][0])
        window_span = max(float(getattr(window, "_trace_display_window_s", 60.0)), 1e-9)
        view_x_max = latest_x
        view_x_min = latest_x - window_span
    requested_view_x_min = view_x_min
    requested_view_x_max = view_x_max
    view_height_px = None
    try:
        scene_rect = window.trace_plot.getPlotItem().vb.sceneBoundingRect()
        if scene_rect is not None:
            view_height_px = float(scene_rect.height())
            if not np.isfinite(view_height_px):
                view_height_px = None
    except Exception:
        view_height_px = None
    history = downsample_sensorgram_history_for_view(
        history,
        view_x_min=view_x_min,
        view_x_max=view_x_max,
        max_rows=int(getattr(window, "_sensorgram_heatmap_history_max_rows", 2000)),
        view_height_px=view_height_px,
        enabled=bool(getattr(window, "_sensorgram_downsampling_enabled", True)),
    )

    times = np.asarray([float(item[0]) for item in history], dtype=np.float64)
    wavelengths = np.asarray(window._sensorgram_heatmap_wavelengths, dtype=np.float64)
    if len(times) == 0 or len(wavelengths) == 0:
        window.trace_heatmap_image.setVisible(False)
        return

    values = [np.asarray(item[1], dtype=np.float64) for item in history]
    expected_length = len(wavelengths)
    if any(len(row) != expected_length for row in values):
        window.trace_heatmap_image.setVisible(False)
        return

    matrix = np.vstack(values)
    finite = np.isfinite(matrix)
    if not np.any(finite):
        window.trace_heatmap_image.setVisible(False)
        return

    image_data = matrix.T
    window.trace_heatmap_image.setImage(image_data, autoLevels=True)
    left = float(times[0])
    right = float(times[-1])
    if right <= left:
        right = left + 1e-6
    bottom = float(np.min(wavelengths))
    top = float(np.max(wavelengths))
    if top <= bottom:
        top = bottom + 1e-6
    window.trace_heatmap_image.setRect(QRectF(left, bottom, right - left, top - bottom))
    if requested_view_x_min is not None and requested_view_x_max is not None and requested_view_x_max > requested_view_x_min:
        view_span = requested_view_x_max - requested_view_x_min
    else:
        view_span = right - left
    x_pad = max(view_span * 0.03, 1.0 if clock_mode else 1e-3)
    if not getattr(window, "_trace_view_locked", False):
        window._trace_view_autoscaling = True
        try:
            if requested_view_x_min is not None and requested_view_x_max is not None and requested_view_x_max > requested_view_x_min:
                window.trace_plot.setXRange(requested_view_x_min, requested_view_x_max + x_pad, padding=0.0)
            else:
                window.trace_plot.setXRange(left, right + x_pad, padding=0.0)
            window.trace_plot.setYRange(bottom, top, padding=0.0)
        finally:
            window._trace_view_autoscaling = False
    window.trace_heatmap_image.setVisible(True)
    for curve in window.trace_curves.values():
        curve.setVisible(False)
    if hasattr(window, "trace_legend"):
        window.trace_legend.setVisible(False)
    window._visible_trace_x = times.copy()
    safe_matrix = np.where(np.isfinite(matrix), matrix, -np.inf)
    row_max = np.max(safe_matrix, axis=1)
    row_max[~np.isfinite(row_max)] = np.nan
    window._visible_trace_y = row_max
    window._visible_trace_mode = "clock" if clock_mode else "elapsed"


def update_spectrum_stats(window, processed: Spectrum | None, fit: Spectrum | None) -> None:
    if processed is None:
        window.spectrum_stats_label.setText("peak: - | centroid: - | FWHM: - | MSE: - | R: - | S/N: -")
        window.spectrum_cursor_label.setText("cursor: -")
        return

    peak_mode = window._current_processing_settings().peak_tracking_mode
    label_map = {
        "smoothed_max": "max",
        "poly_max": "poly",
        "gaussian_center": "gauss",
        "centroid": "centroid",
    }
    analysis = window._get_analysis_metrics(processed, fit)
    primary_peak = analysis.get("primary_peak_nm", float("nan"))
    centroid = analysis.get("centroid_nm", float("nan"))
    fit_r = analysis.get("fit_r")
    mse = analysis.get("mse", "-")
    snr = analysis.get("snr")
    fwhm = analysis.get("fwhm", "-")
    if not isinstance(primary_peak, (int, float)) or not np.isfinite(float(primary_peak)):
        primary_peak = window._compute_peak_metric_nm(processed, fit)
    if not isinstance(centroid, (int, float)) or not np.isfinite(float(centroid)):
        centroid = window._compute_centroid_nm(processed, fit)
    window.spectrum_stats_label.setText(
        f"{label_map.get(peak_mode, 'peak')}: {float(primary_peak):.3f} nm"
        f" | centroid: {float(centroid):.3f} nm"
        f" | FWHM: {fwhm}"
        f" | MSE: {mse}"
        f" | R: {fit_r if fit_r is not None else '-'}"
        f" | S/N: {snr if snr is not None else '-'}"
    )
    window.spectrum_cursor_label.setText(window._spectrum_cursor_text)


def update_trace_stats(window) -> None:
    series = window._active_trace_series()
    metric_name = window._trace_stats_metric()
    metric_label = window.TRACE_METRIC_LABELS.get(metric_name, metric_name)
    metric_color = window.TRACE_METRIC_COLORS.get(metric_name, "#444444")
    if not series:
        window.trace_stats_label.setText(
            f'<span style="color:{metric_color}; font-weight:700;">{metric_label}</span>: -'
            " | min/max: - | span: - | dt -"
        )
        window.trace_noise_summary_label.setText("noise: -")
        window.trace_cursor_label.setText("cursor: -")
        return

    if metric_name in series:
        x_values, y_values = series[metric_name]
    else:
        x_values, y_values = next(iter(series.values()))
        metric_name = next(iter(series.keys()))
        metric_label = window.TRACE_METRIC_LABELS.get(metric_name, metric_name)
        metric_color = window.TRACE_METRIC_COLORS.get(metric_name, "#444444")
    clock_mode = not bool(window._measurement_active)

    if len(x_values) == 0 or len(y_values) == 0:
        window.trace_stats_label.setText("latest: - | min/max: - | span: - | dt -")
        window.trace_noise_summary_label.setText("noise: -")
        window.trace_cursor_label.setText("cursor: -")
        return

    y_min = float(np.min(y_values))
    y_max = float(np.max(y_values))
    latest_y = float(y_values[-1])
    if clock_mode:
        try:
            latest_time_text = datetime.fromtimestamp(float(x_values[-1])).strftime("%H:%M:%S")
        except (OverflowError, OSError, ValueError):
            latest_time_text = "-"
    else:
        latest_time_text = f"{float(x_values[-1]):.2f} s"

    dt_values = np.diff(x_values)
    dt_text = "-" if len(dt_values) == 0 else f"{float(np.nanmean(dt_values)):.2f} s"
    window_s = window.trace_noise_window_spin.value()
    noise_chunks: list[str] = []
    for metric_name, (metric_x, metric_y) in series.items():
        metric_mask = metric_x >= (float(metric_x[-1]) - window_s)
        metric_window = metric_y[metric_mask]
        if len(metric_window) >= 2:
            metric_noise = f"{float(np.nanstd(metric_window)):.4f} nm"
        else:
            metric_noise = "-"
        color = window.TRACE_METRIC_COLORS.get(metric_name, "#444444")
        label = window.TRACE_METRIC_LABELS.get(metric_name, metric_name)
        noise_chunks.append(f"<span style='color:{color};'><b>{label}</b> {metric_noise}</span>")

    window.trace_stats_label.setText(
        f'<span style="color:{metric_color}; font-weight:700;">{metric_label}</span>: {latest_time_text}, {latest_y:.3f} nm'
        f" | min/max: {y_min:.3f} / {y_max:.3f} nm"
        f" | span: {y_max - y_min:.3f} nm"
        f" | dt {dt_text}"
    )
    window.trace_noise_summary_label.setText(" | ".join(noise_chunks))
    window.trace_cursor_label.setText(window._trace_cursor_text)


def handle_spectrum_mouse_moved(window, event) -> None:
    pos = event[0]
    if not window.spectrum_plot.sceneBoundingRect().contains(pos):
        return
    mouse_point = window.spectrum_plot.plotItem.vb.mapSceneToView(pos)
    x = float(mouse_point.x())
    processed = window._visible_processed_plot if window._visible_processed_plot is not None else window._last_processed_plot
    if processed is not None and len(processed.wavelengths_nm) > 0:
        wavelengths = np.asarray(processed.wavelengths_nm, dtype=np.float64)
        values = np.asarray(processed.values, dtype=np.float64)
        index = _nearest_sorted_index(wavelengths, x)
        x = float(wavelengths[index])
        y = float(values[index])
    else:
        y = float(mouse_point.y())
    window.spectrum_vline.setPos(x)
    window.spectrum_hline.setPos(y)
    window._spectrum_cursor_text = f"cursor: {x:.3f} nm, {y:.3f}"
    window.spectrum_cursor_label.setText(window._spectrum_cursor_text)


def handle_trace_mouse_moved(window, event) -> None:
    pos = event[0]
    if not window.trace_plot.sceneBoundingRect().contains(pos):
        return
    mouse_point = window.trace_plot.plotItem.vb.mapSceneToView(pos)
    mouse_x = float(mouse_point.x())
    mouse_y = float(mouse_point.y())
    x = mouse_x
    y = mouse_y
    if getattr(window, "_sensorgram_content_mode", "metric") == "heatmap":
        window.trace_vline.setPos(x)
        window.trace_hline.setPos(y)
        if window._measurement_active:
            cursor_x = f"{x:.3f} s"
        else:
            try:
                cursor_x = datetime.fromtimestamp(float(x)).strftime("%H:%M:%S")
            except (OverflowError, OSError, ValueError):
                cursor_x = f"{x:.3f}"
        window._trace_cursor_text = f"cursor: {cursor_x}, {y:.3f} nm"
        window.trace_cursor_label.setText(window._trace_cursor_text)
        return

    best_match: tuple[float, float, float] | None = None
    for x_values, y_values in window._active_trace_series().values():
        if len(x_values) == 0:
            continue
        x_array = np.asarray(x_values, dtype=np.float64)
        index = _nearest_sorted_index(x_array, mouse_x)
        candidate_x = float(x_values[index])
        candidate_y = float(y_values[index])
        distance_sq = (candidate_x - mouse_x) ** 2 + (candidate_y - mouse_y) ** 2
        if best_match is None or distance_sq < best_match[2]:
            best_match = (candidate_x, candidate_y, distance_sq)

    if best_match is not None:
        x, y, _ = best_match
    window.trace_vline.setPos(x)
    window.trace_hline.setPos(y)
    if window._measurement_active:
        cursor_x = f"{x:.3f} s"
    else:
        try:
            cursor_x = datetime.fromtimestamp(float(x)).strftime("%H:%M:%S")
        except (OverflowError, OSError, ValueError):
            cursor_x = f"{x:.3f}"
    window._trace_cursor_text = f"cursor: {cursor_x}, {y:.3f} nm"
    window.trace_cursor_label.setText(window._trace_cursor_text)
