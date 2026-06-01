from __future__ import annotations

import logging
from datetime import datetime
from time import perf_counter

from PyQt6.QtCore import QRectF

import numpy as np
import pyqtgraph as pg

from lspr_app.domain.models import Spectrum
from lspr_app.domain.processing import processing_debug_mode_enabled
from lspr_app.gui.plot_view_cache import (
    build_heatmap_arrays,
    build_heatmap_history_token,
    build_metric_series_token,
    derive_heatmap_levels_from_matrix,
    downsample_metric_series_for_view as _downsample_metric_series_for_view,
    expand_heatmap_levels,
    sample_absolute_heatmap_rows_for_view,
    sample_absolute_metric_series_for_view,
    select_heatmap_rows_for_view,
)


def _current_metric_view_state(window) -> tuple[float | None, float | None, float | None]:
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


def _curve_data_arrays(curve) -> tuple[np.ndarray, np.ndarray]:
    x_values = getattr(curve, "xData", None)
    y_values = getattr(curve, "yData", None)
    if x_values is None or y_values is None:
        data = getattr(curve, "data", None)
        if isinstance(data, tuple) and len(data) == 2:
            x_values, y_values = data
    if x_values is None or y_values is None:
        get_data = getattr(curve, "getData", None)
        if callable(get_data):
            try:
                x_values, y_values = get_data()
            except Exception:
                return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
    if x_values is None or y_values is None:
        return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
    x = np.asarray(x_values, dtype=np.float64)
    y = np.asarray(y_values, dtype=np.float64)
    if len(x) == 0 or len(y) == 0:
        return x[:0], y[:0]
    finite = np.isfinite(x) & np.isfinite(y)
    if not np.any(finite):
        return x[:0], y[:0]
    return x[finite], y[finite]


def _series_point_count(series: object) -> int:
    if series is None:
        return 0
    if hasattr(series, "to_arrays"):
        try:
            arrays = series.to_arrays()  # type: ignore[no-any-return]
            return int(len(arrays[0]))
        except Exception:
            return 0
    if isinstance(series, tuple) and len(series) == 2:
        try:
            return int(len(series[0]))
        except Exception:
            return 0
    try:
        return int(len(series))
    except Exception:
        return 0


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
    return _downsample_metric_series_for_view(
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


def downsample_spectrum_series_for_view(
    x: np.ndarray,
    y: np.ndarray,
    *,
    view_x_min: float | None = None,
    view_x_max: float | None = None,
    view_width_px: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    return downsample_metric_series_for_view(
        x,
        y,
        view_x_min=view_x_min,
        view_x_max=view_x_max,
        view_width_px=view_width_px,
        minimum_points=128,
        oversample=1.0,
        default_points=1024,
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
    did_work = False
    refresh_state = getattr(window, "_ui_refresh_state", None)
    if getattr(refresh_state, "live_estimate_dirty", False):
        started = perf_counter()
        window._update_live_estimate()
        window._last_deferred_ui_live_estimate_ms = (perf_counter() - started) * 1000.0
        refresh_state.live_estimate_dirty = False
        did_work = True
    if getattr(refresh_state, "telemetry_dirty", False):
        started = perf_counter()
        window._refresh_telemetry()
        window._last_deferred_ui_telemetry_ms = (perf_counter() - started) * 1000.0
        refresh_state.telemetry_dirty = False
        did_work = True
    if getattr(refresh_state, "metric_plot_dirty", False) and not bool(getattr(window, "_sensorgram_frozen", False)):
        started = perf_counter()
        window._refresh_trace_plot(refresh_state.pending_metric_label or "Peak position (nm)")
        window._last_deferred_ui_trace_plot_ms = (perf_counter() - started) * 1000.0
        refresh_state.metric_plot_dirty = False
        did_work = True
    if getattr(refresh_state, "summary_dirty", False):
        started = perf_counter()
        window._refresh_session_summary()
        window._last_deferred_ui_summary_ms = (perf_counter() - started) * 1000.0
        refresh_state.summary_dirty = False
        did_work = True
    if getattr(refresh_state, "stats_dirty", False):
        started = perf_counter()
        window._update_spectrum_stats(window._last_processed_plot, window._last_fit_plot)
        window._update_trace_stats()
        window._last_deferred_ui_stats_ms = (perf_counter() - started) * 1000.0
        refresh_state.stats_dirty = False
        did_work = True
    if did_work:
        window._log_throttled(
            "ui_flush",
            (
                "UI flush | trace_dirty="
                f"{int(bool(getattr(refresh_state, 'metric_plot_dirty', False)))} | "
                f"stats_dirty={int(bool(getattr(refresh_state, 'stats_dirty', False)))} | "
                f"display_rate={window.live_rate_spin.value():.2f} Hz"
            ),
            level=logging.DEBUG,
            min_interval=1.0,
        )


def flush_plot_refreshes(window) -> None:
    refresh_state = getattr(window, "_ui_refresh_state", None)
    if window._plots_frozen:
        if refresh_state is not None:
            refresh_state.plot_render_dirty = False
        return
    if getattr(refresh_state, "plot_render_dirty", False):
        started = perf_counter()
        previous_finish = getattr(window, "_last_plot_refresh_finished_at", None)
        plot_mode = window.PLOT_MODES[window.plot_selector.currentText()]
        plot_spectrum = window._session.get_plot_data(plot_mode)
        plot_fit = window._last_fit_plot if plot_mode in {"sample", "absorbance"} else None
        window._refresh_spectrum_plot(plot_spectrum, plot_fit)
        finished = perf_counter()
        window._last_plot_refresh_ms = (finished - started) * 1000.0
        window._last_plot_refresh_gap_ms = None if previous_finish is None else (finished - float(previous_finish)) * 1000.0
        window._last_plot_refresh_finished_at = finished
        refresh_timestamps = getattr(window, "_plot_refresh_timestamps", None)
        if refresh_timestamps is None:
            refresh_timestamps = []
            window._plot_refresh_timestamps = refresh_timestamps
        refresh_timestamps.append(finished)
        window._plot_refresh_timestamps = [
            timestamp
            for timestamp in refresh_timestamps
            if (finished - float(timestamp)) <= float(getattr(window, "_plot_refresh_rate_window_s", 5.0))
        ]
        if len(window._plot_refresh_timestamps) >= 2:
            first_timestamp = float(window._plot_refresh_timestamps[0])
            last_timestamp = float(window._plot_refresh_timestamps[-1])
            span_s = last_timestamp - first_timestamp
            if span_s > 0:
                window._actual_plot_refresh_rate_hz = (len(window._plot_refresh_timestamps) - 1) / span_s
            else:
                window._actual_plot_refresh_rate_hz = None
        else:
            window._actual_plot_refresh_rate_hz = None
        if processing_debug_mode_enabled():
            gap_text = "-" if window._last_plot_refresh_gap_ms is None else f"{window._last_plot_refresh_gap_ms:.2f} ms"
            window._plot_refresh_rate_window_s = float(getattr(window, "_plot_refresh_rate_window_s", 5.0))
            refresh_window_text = f"{window._plot_refresh_rate_window_s:.1f} s"
            actual_text = "-" if window._actual_plot_refresh_rate_hz is None else f"{window._actual_plot_refresh_rate_hz:.2f} Hz"
            window._log_throttled(
                "gui_plot_refresh",
                f"GUI plot refresh: {window._last_plot_refresh_ms:.2f} ms | gap={gap_text} | avg={actual_text} | window={refresh_window_text}",
                level=logging.INFO,
                min_interval=0.5,
            )
        refresh_state.plot_render_dirty = False


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
    y_span = float(np.max(y) - np.min(y))
    y_pad = max(y_span * 0.08, 1e-6)
    window.spectrum_plot.setXRange(float(np.min(x_finite)), float(np.max(x_finite)), padding=0.0)
    window.spectrum_plot.setYRange(float(np.min(y)) - y_pad, float(np.max(y)) + y_pad, padding=0.0)
    window._autoscale_residual_axis()


def apply_processing_range_to_spectrum_plot(window) -> None:
    settings = window._current_processing_settings()
    low = float(min(settings.wavelength_min_nm, settings.wavelength_max_nm))
    high = float(max(settings.wavelength_min_nm, settings.wavelength_max_nm))
    if not np.isfinite(low) or not np.isfinite(high):
        return
    if high <= low:
        high = low + 1e-6
    window.spectrum_plot.setXRange(low, high, padding=0.0)
    window._spectrum_render_cache_key = None


def autoscale_metric_plot(window, *, force: bool = True) -> None:
    if not force:
        last_autoscale_at = float(getattr(window, "_last_metric_autoscale_at", 0.0))
        min_interval_s = float(getattr(window, "_metric_autoscale_min_interval_s", 0.25))
        if last_autoscale_at > 0.0 and (perf_counter() - last_autoscale_at) < min_interval_s:
            window._metric_autoscale_pending = False
            return
    started = perf_counter()
    content_mode = getattr(window, "_sensorgram_content_mode", "metric")
    if content_mode == "heatmap":
        window._trace_view_autoscaling = True
        try:
            render_sensorgram_heatmap(window, window._sensorgram_heatmap_history, clock_mode=not bool(window._measurement_active))
        finally:
            window._trace_view_autoscaling = False
            window._last_metric_autoscale_at = perf_counter()
            window._metric_autoscale_pending = False
        return
    visible_series: list[tuple[np.ndarray, np.ndarray]] = []
    trace_curves = getattr(window, "trace_curves", None)
    if isinstance(trace_curves, dict):
        for curve in trace_curves.values():
            if hasattr(curve, "isVisible") and not bool(curve.isVisible()):
                continue
            x_values, y_values = _curve_data_arrays(curve)
            if len(x_values) > 0 and len(y_values) > 0:
                visible_series.append((x_values, y_values))
    if not visible_series:
        series = window._active_trace_series()
        if not series:
            window.trace_plot.enableAutoRange(axis=pg.ViewBox.XYAxes, enable=True)
            window.trace_plot.autoRange()
            window._metric_autoscale_pending = False
            return
        visible_series = [tuple(np.asarray(item, dtype=np.float64) for item in series_values) for series_values in series.values()]
    if not visible_series:
        window.trace_plot.enableAutoRange(axis=pg.ViewBox.XYAxes, enable=True)
        window.trace_plot.autoRange()
        window._metric_autoscale_pending = False
        return
    x = np.concatenate([item[0] for item in visible_series])
    y = np.concatenate([item[1] for item in visible_series])
    finite = np.isfinite(x) & np.isfinite(y)
    if not np.any(finite):
        window._metric_autoscale_pending = False
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
        x_pad = max(window_span * 0.005, 0.25 if window.trace_time_axis._mode == "clock" else 1e-3)
    else:
        x_min = float(np.min(x))
        x_max = latest_x
        x_pad = max(x_span * 0.005, 0.25 if window.trace_time_axis._mode == "clock" else 1e-3)
    y_pad = max(y_span * 0.12, 1e-6)
    if getattr(window, "_trace_view_locked", False):
        window._metric_autoscale_pending = False
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
        window._last_metric_autoscale_at = perf_counter()
        window._metric_autoscale_pending = False
        if processing_debug_mode_enabled():
            elapsed_ms = (perf_counter() - started) * 1000.0
            if elapsed_ms >= 2.0:
                window._log_throttled(
                    "metric_autoscale",
                    f"Metric autoscale: {elapsed_ms:.2f} ms | points={len(x)}",
                    level=logging.INFO,
                    min_interval=0.5,
                )


def update_residual_view_geometry(window) -> None:
    if not hasattr(window, "residual_view"):
        return
    spectrum_vb = window.spectrum_plot.getPlotItem().vb
    window.residual_view.setGeometry(spectrum_vb.sceneBoundingRect())
    window.residual_view.linkedViewChanged(spectrum_vb, pg.ViewBox.XAxis)


def autoscale_residual_axis(window) -> None:
    if not hasattr(window, "residual_view") or not window.show_residual_button.isChecked():
        return
    if bool(getattr(window, "_residual_axis_autoscaled", False)):
        return
    residual = window.residual_curve.yData
    if residual is None or len(residual) == 0:
        window.residual_view.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
        window._residual_axis_autoscaled = True
        return
    y = np.asarray(residual, dtype=np.float64)
    finite = np.isfinite(y)
    if not np.any(finite):
        return
    y = y[finite]
    amplitude = float(np.percentile(np.abs(y), 95))
    amplitude = max(amplitude, float(np.max(np.abs(y))), 1e-9)
    pad = max(amplitude * 0.15, 1e-6)
    if not np.isfinite(pad):
        pad = 1e-6
    half_span = min(amplitude + pad, 100.0)
    window.residual_view.setYRange(-half_span, half_span, padding=0.0)
    window._residual_axis_autoscaled = True


def update_residual_axis_visibility(window, visible: bool | None = None) -> None:
    if visible is None:
        visible = window.show_residual_button.isChecked()
    if hasattr(window, "residual_axis"):
        window.residual_axis.setVisible(visible)
    if hasattr(window, "residual_view"):
        window.residual_view.setVisible(visible)


def request_metric_autoscale(window) -> None:
    if bool(getattr(window, "_sensorgram_frozen", False)):
        return
    window._metric_autoscale_pending = True
    window._trace_autoscale_timer.start()


def refresh_metric_plot(window, trace_label: str) -> None:
    if window._plots_frozen:
        return
    started = perf_counter()
    clock_mode = not bool(window._measurement_active)
    content_mode = getattr(window, "_sensorgram_content_mode", "metric")
    metric_plot_enabled = bool(getattr(window, "_metric_plot_enabled", True))
    if content_mode != "heatmap":
        window._last_sensorgram_heatmap_render_ms = None
    active_series = window._active_trace_series()
    try:
        if content_mode == "heatmap":
            window._visible_trace_mode = "clock" if clock_mode else "elapsed"
            window.trace_time_axis.set_mode("clock" if clock_mode else "elapsed")
            window.trace_plot.setLabel("left", "Wavelength (nm)")
            window.trace_plot.setLabel("bottom", "Time (local)" if clock_mode else "Time (s)")
            if metric_plot_enabled and active_series:
                render_metric_series(window, active_series, clock_mode=clock_mode)
            else:
                for curve in window.trace_curves.values():
                    curve.setData([], [])
                    curve.setVisible(False)
            render_sensorgram_heatmap(window, window._sensorgram_heatmap_history, clock_mode=clock_mode)
            request_metric_autoscale(window)
            return
        if not metric_plot_enabled:
            _show_metric_plot_unavailable(window, clock_mode)
            return
        if active_series:
            window._visible_trace_mode = "clock" if clock_mode else "elapsed"
            window.trace_time_axis.set_mode("clock" if clock_mode else "elapsed")
            window.trace_plot.setLabel("left", trace_label)
            window.trace_plot.setLabel("bottom", "Time (local)" if clock_mode else "Time (s)")
            render_metric_series(window, active_series, clock_mode=clock_mode)
            window.trace_heatmap_image.setVisible(False)
            if hasattr(window, "trace_heatmap_notice_item"):
                window.trace_heatmap_notice_item.setVisible(False)
            if hasattr(window, "trace_legend"):
                window.trace_legend.setVisible(True)
            for curve in window.trace_curves.values():
                curve.setVisible(True)
            request_metric_autoscale(window)
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
        if hasattr(window, "trace_heatmap_notice_item"):
            window.trace_heatmap_notice_item.setVisible(False)
        if hasattr(window, "trace_legend"):
            window.trace_legend.setVisible(content_mode != "heatmap")
        window._visible_trace_x = None
        window._visible_trace_y = None
        window._visible_trace_mode = "clock" if clock_mode else "elapsed"
        request_metric_autoscale(window)
        window._log_throttled(
            "trace_refresh",
            f"Metric updated | {trace_label}",
            level=logging.DEBUG,
            min_interval=1.5,
        )
    finally:
        window._last_sensorgram_render_ms = (perf_counter() - started) * 1000.0


def render_metric_series(
    window,
    history: dict[str, tuple[np.ndarray, np.ndarray] | list[tuple[object, float]]],
    clock_mode: bool,
) -> None:
    started = perf_counter()
    setdata_ms = 0.0
    display_points = 0
    raw_points = 0

    def _trace_x_value(raw_x: object) -> float:
        if hasattr(raw_x, "timestamp"):
            return float(raw_x.timestamp())
        return float(raw_x)

    def _series_to_arrays(series: tuple[np.ndarray, np.ndarray] | list[tuple[object, float]]) -> tuple[np.ndarray, np.ndarray]:
        if isinstance(series, tuple) and len(series) == 2:
            return np.asarray(series[0], dtype=np.float64), np.asarray(series[1], dtype=np.float64)
        if not series:
            return np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64)
        return (
            np.asarray([_trace_x_value(item[0]) for item in series], dtype=np.float64),
            np.asarray([item[1] for item in series], dtype=np.float64),
        )

    view_mode = getattr(window, "_sensorgram_view_mode", "absolute")
    trace_view_locked = bool(getattr(window, "_trace_view_locked", False))
    _, _, view_width_px = _current_metric_view_state(window)
    view_x_min = view_x_max = None
    if trace_view_locked:
        view_x_min, view_x_max, view_width_px = _current_metric_view_state(window)
    elif view_mode == "rolling" and len(history) > 0:
        latest_x = None
        for series in history.values():
            x_values, _ = _series_to_arrays(series)
            if len(x_values) == 0:
                continue
            series_latest = float(x_values[-1])
            if latest_x is None or series_latest > latest_x:
                latest_x = series_latest
        if latest_x is not None:
            window_span = max(float(getattr(window, "_trace_display_window_s", 60.0)), 1e-9)
            view_x_max = latest_x
            view_x_min = latest_x - window_span
    active_series = {}
    render_cache = getattr(window, "_metric_render_display_cache", None)
    if not isinstance(render_cache, dict):
        render_cache = {}
        window._metric_render_display_cache = render_cache
    for metric_name, curve in window.trace_curves.items():
        series = history.get(metric_name, [])
        if metric_name not in window._selected_trace_metrics() or series is None:
            curve.setData([], [])
            continue
        raw_points += _series_point_count(series)
        x, y = _series_to_arrays(series)
        if len(x) == 0 or len(y) == 0:
            curve.setData([], [])
            continue
        cache = getattr(window, "_plot_view_cache", None)
        series_token = build_metric_series_token(window, metric_name)
        if cache is not None:
            if view_mode == "absolute" and not trace_view_locked:
                x, y = cache.absolute_metric_view(
                    series_token,
                    x,
                    y,
                    view_width_px=view_width_px,
                    enabled=bool(getattr(window, "_sensorgram_downsampling_enabled", True)),
                )
            else:
                x, y = cache.metric_view(
                    series_token,
                    x,
                    y,
                    view_x_min=view_x_min,
                    view_x_max=view_x_max,
                    view_width_px=view_width_px,
                    enabled=bool(getattr(window, "_sensorgram_downsampling_enabled", True)),
                )
        else:
            if view_mode == "absolute" and not trace_view_locked:
                x, y = sample_absolute_metric_series_for_view(
                    x,
                    y,
                    view_width_px=view_width_px,
                    enabled=bool(getattr(window, "_sensorgram_downsampling_enabled", True)),
                )
            else:
                x, y = downsample_metric_series_for_view(
                    x,
                    y,
                    view_x_min=view_x_min,
                    view_x_max=view_x_max,
                    view_width_px=view_width_px,
                    enabled=bool(getattr(window, "_sensorgram_downsampling_enabled", True)),
                )
        cached_display = render_cache.get(metric_name)
        if (
            isinstance(cached_display, tuple)
            and len(cached_display) == 2
            and cached_display[0] is x
            and cached_display[1] is y
        ):
            display_points += int(len(x))
            active_series[metric_name] = (x, y)
            continue
        setdata_started = perf_counter()
        curve.setData(x, y)
        setdata_ms += (perf_counter() - setdata_started) * 1000.0
        display_points += int(len(x))
        render_cache[metric_name] = (x, y)
        active_series[metric_name] = (x, y)

    primary_name = window._primary_trace_metric()
    if primary_name in active_series:
        window._visible_trace_x = active_series[primary_name][0]
        window._visible_trace_y = active_series[primary_name][1]
    elif active_series:
        first_series = next(iter(active_series.values()))
        window._visible_trace_x = first_series[0]
        window._visible_trace_y = first_series[1]
    else:
        window._visible_trace_x = None
        window._visible_trace_y = None

    if processing_debug_mode_enabled():
        elapsed_ms = (perf_counter() - started) * 1000.0
        if elapsed_ms >= 2.0:
            window._log_throttled(
                "metric_render",
                (
                    "Metric render: "
                    f"{elapsed_ms:.2f} ms | setData={setdata_ms:.2f} ms | "
                    f"raw_n={raw_points} | display_n={display_points}"
                ),
                level=logging.INFO,
                min_interval=0.5,
            )


def render_sensorgram_heatmap(
    window,
    history: list[tuple[float, np.ndarray]],
    clock_mode: bool,
) -> None:
    if not hasattr(window, "trace_heatmap_image"):
        return
    if not bool(getattr(window, "_sensorgram_heatmap_enabled", True)):
        _show_sensorgram_heatmap_unavailable(window, clock_mode)
        return
    if not history or getattr(window, "_sensorgram_heatmap_wavelengths", None) is None:
        window.trace_heatmap_image.setVisible(False)
        if hasattr(window, "trace_heatmap_notice_item"):
            window.trace_heatmap_notice_item.setVisible(False)
        for curve in window.trace_curves.values():
            curve.setVisible(False)
        if hasattr(window, "trace_legend"):
            window.trace_legend.setVisible(False)
        return

    started = perf_counter()
    view_mode = getattr(window, "_sensorgram_view_mode", "absolute")
    trace_view_locked = bool(getattr(window, "_trace_view_locked", False))
    _, _, _view_width_px = _current_metric_view_state(window)
    view_x_min = view_x_max = None
    if trace_view_locked:
        view_x_min, view_x_max, _view_width_px = _current_metric_view_state(window)
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
    cache = getattr(window, "_plot_view_cache", None)
    heatmap_token = build_heatmap_history_token(window)
    if cache is not None:
        times, matrix = cache.heatmap_arrays_from_history(heatmap_token, history, build_heatmap_arrays)
        if view_mode == "absolute" and not trace_view_locked:
            times, matrix = cache.absolute_heatmap_view(
                heatmap_token,
                times,
                matrix,
                max_rows=int(getattr(window, "_sensorgram_heatmap_history_max_rows", 2000)),
                view_height_px=view_height_px,
                enabled=bool(getattr(window, "_sensorgram_downsampling_enabled", True)),
            )
        else:
            times, matrix = cache.heatmap_view(
                heatmap_token,
                times,
                matrix,
                view_x_min=view_x_min,
                view_x_max=view_x_max,
                max_rows=int(getattr(window, "_sensorgram_heatmap_history_max_rows", 2000)),
                view_height_px=view_height_px,
                enabled=bool(getattr(window, "_sensorgram_downsampling_enabled", True)),
            )
    else:
        times, matrix = build_heatmap_arrays(history)
        if view_mode == "absolute" and not trace_view_locked:
            times, matrix = sample_absolute_heatmap_rows_for_view(
                times,
                matrix,
                max_rows=int(getattr(window, "_sensorgram_heatmap_history_max_rows", 2000)),
                view_height_px=view_height_px,
                enabled=bool(getattr(window, "_sensorgram_downsampling_enabled", True)),
            )
        else:
            times, matrix = select_heatmap_rows_for_view(
                times,
                matrix,
                view_x_min=view_x_min,
                view_x_max=view_x_max,
                max_rows=int(getattr(window, "_sensorgram_heatmap_history_max_rows", 2000)),
                view_height_px=view_height_px,
                enabled=bool(getattr(window, "_sensorgram_downsampling_enabled", True)),
            )
    if view_mode == "rolling" and len(times) > 0:
        times = times - float(times[0])
    wavelengths = np.asarray(window._sensorgram_heatmap_wavelengths, dtype=np.float64)
    if len(times) == 0 or len(wavelengths) == 0:
        window.trace_heatmap_image.setVisible(False)
        if hasattr(window, "trace_heatmap_notice_item"):
            window.trace_heatmap_notice_item.setVisible(False)
        return

    expected_length = len(wavelengths)
    if matrix.shape[1] != expected_length:
        window.trace_heatmap_image.setVisible(False)
        if hasattr(window, "trace_heatmap_notice_item"):
            window.trace_heatmap_notice_item.setVisible(False)
        return

    finite = np.isfinite(matrix)
    if not np.any(finite):
        window.trace_heatmap_image.setVisible(False)
        if hasattr(window, "trace_heatmap_notice_item"):
            window.trace_heatmap_notice_item.setVisible(False)
        return

    image_data = matrix.T
    levels = getattr(window, "_sensorgram_heatmap_levels", None)
    if levels is None:
        levels = derive_heatmap_levels_from_matrix(matrix)
        window._sensorgram_heatmap_levels = levels
    else:
        # Keep the displayed range stable, but expand it if new rows exceed the cached bounds.
        latest_row = matrix[-1] if len(matrix) > 0 else matrix
        updated_levels = expand_heatmap_levels(levels, latest_row)
        if updated_levels != levels:
            levels = updated_levels
            window._sensorgram_heatmap_levels = levels
    window.trace_heatmap_image.setImage(image_data, autoLevels=False)
    if hasattr(window.trace_heatmap_image, "setLevels"):
        window.trace_heatmap_image.setLevels(levels)
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
    x_pad = max(view_span * 0.005, 0.25 if clock_mode else 1e-3)
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
    if hasattr(window, "trace_heatmap_notice_item"):
        window.trace_heatmap_notice_item.setVisible(False)
    for curve in window.trace_curves.values():
        curve.setVisible(False)
    if hasattr(window, "trace_legend"):
        window.trace_legend.setVisible(False)
    window._visible_trace_x = times
    safe_matrix = np.where(np.isfinite(matrix), matrix, -np.inf)
    row_max = np.max(safe_matrix, axis=1)
    row_max[~np.isfinite(row_max)] = np.nan
    window._visible_trace_y = row_max
    window._visible_trace_mode = "clock" if clock_mode else "elapsed"
    elapsed_ms = (perf_counter() - started) * 1000.0
    window._last_sensorgram_heatmap_render_ms = elapsed_ms
    if elapsed_ms >= 2.0:
        window._log_throttled(
            "sensorgram_heatmap_render",
            (
                "Sensorgram heatmap render "
                f"| rows={len(history)} | cols={len(wavelengths)} | "
                f"mode={view_mode} | {elapsed_ms:.2f} ms"
            ),
            level=logging.INFO,
            min_interval=1.0,
        )


def _show_sensorgram_heatmap_unavailable(window, clock_mode: bool) -> None:
    _show_trace_plot_unavailable(
        window,
        "Heatmap unavailable",
        "Enable it in Help > Performance switches.",
        clock_mode=clock_mode,
    )


def _show_metric_plot_unavailable(window, clock_mode: bool) -> None:
    _show_trace_plot_unavailable(
        window,
        "Metric plot unavailable",
        "Enable it in Help > Performance switches.",
        clock_mode=clock_mode,
    )


def _show_trace_plot_unavailable(window, title: str, detail: str, *, clock_mode: bool) -> None:
    if hasattr(window, "trace_heatmap_image"):
        window.trace_heatmap_image.setVisible(False)
    if hasattr(window, "trace_heatmap_notice_item"):
        notice = window.trace_heatmap_notice_item
        if hasattr(notice, "setHtml"):
            notice.setHtml(
                "<div style='text-align:center;'>"
                f"<span style='font-size:12pt; font-weight:600; color:#d8dee9;'>{title}</span><br>"
                f"<span style='font-size:9pt; color:#9aa4b2;'>{detail}</span>"
                "</div>"
            )
        elif hasattr(notice, "setText"):
            notice.setText(f"{title}\n{detail}")
        try:
            plot_item = window.trace_plot.getPlotItem()
            view_box = plot_item.vb
            view_range = view_box.viewRange()
            x_range = view_range[0]
            y_range = view_range[1]
            x_center = float((float(x_range[0]) + float(x_range[1])) / 2.0)
            y_center = float((float(y_range[0]) + float(y_range[1])) / 2.0)
            if np.isfinite(x_center) and np.isfinite(y_center):
                notice.setPos(x_center, y_center)
        except Exception:
            pass
        notice.setVisible(True)
    for curve in window.trace_curves.values():
        curve.setVisible(False)
    if hasattr(window, "trace_legend"):
        window.trace_legend.setVisible(False)


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


def update_metric_stats(window) -> None:
    started = perf_counter()
    series = window._active_trace_series()
    metric_name = window._trace_stats_metric()
    metric_label = window.TRACE_METRIC_LABELS.get(metric_name, metric_name)
    metric_color = window.TRACE_METRIC_COLORS.get(metric_name, "#444444")

    visible_series: list[tuple[np.ndarray, np.ndarray]] = []
    trace_curves = getattr(window, "trace_curves", None)
    if isinstance(trace_curves, dict):
        for curve in trace_curves.values():
            if hasattr(curve, "isVisible") and not bool(curve.isVisible()):
                continue
            x_values, y_values = _curve_data_arrays(curve)
            if len(x_values) > 0 and len(y_values) > 0:
                visible_series.append((x_values, y_values))

    if not visible_series:
        if not series:
            window.trace_stats_label.setText(
                f'<span style="color:{metric_color}; font-weight:700;">{metric_label}</span>: -'
                " | min/max: - | span: - | dt -"
            )
            window.trace_noise_summary_label.setText("noise: -")
            window.trace_cursor_label.setText("cursor: -")
            return
        visible_series = [tuple(np.asarray(item, dtype=np.float64) for item in series_values) for series_values in series.values()]

    if not visible_series:
        window.trace_stats_label.setText(
            f'<span style="color:{metric_color}; font-weight:700;">{metric_label}</span>: -'
            " | min/max: - | span: - | dt -"
        )
        window.trace_noise_summary_label.setText("noise: -")
        window.trace_cursor_label.setText("cursor: -")
        return

    selected_metrics = list(window._selected_trace_metrics())
    if metric_name in selected_metrics:
        matched_curve = None
        if isinstance(trace_curves, dict):
            matched_curve = trace_curves.get(metric_name)
        if matched_curve is not None:
            x_values, y_values = _curve_data_arrays(matched_curve)
        else:
            x_values, y_values = visible_series[0]
    else:
        x_values, y_values = visible_series[0]
        metric_name = selected_metrics[0] if selected_metrics else metric_name
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

    if processing_debug_mode_enabled():
        elapsed_ms = (perf_counter() - started) * 1000.0
        if elapsed_ms >= 2.0:
            visible_points = 0
            for x_values, _y_values in visible_series:
                visible_points += int(len(x_values))
            raw_points = sum(_series_point_count(series) for series in series.values()) if series else 0
            window._log_throttled(
                "metric_stats",
                (
                    "Metric stats: "
                    f"{elapsed_ms:.2f} ms | raw_n={raw_points} | "
                    f"display_n={visible_points}"
                ),
                level=logging.INFO,
                min_interval=0.5,
            )


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


def handle_metric_mouse_moved(window, event) -> None:
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


_current_trace_view_state = _current_metric_view_state
downsample_trace_series_for_view = downsample_metric_series_for_view
autoscale_trace_plot = autoscale_metric_plot
request_trace_autoscale = request_metric_autoscale
refresh_trace_plot = refresh_metric_plot
render_trace_series = render_metric_series
update_trace_stats = update_metric_stats
handle_trace_mouse_moved = handle_metric_mouse_moved
