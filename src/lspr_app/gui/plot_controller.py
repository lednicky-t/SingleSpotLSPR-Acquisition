from __future__ import annotations

import logging
from datetime import datetime

import numpy as np
import pyqtgraph as pg

from lspr_app.domain.models import Spectrum


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
    if window._ui_trace_plot_dirty:
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
    window_span = max(float(window._trace_display_window_s), x_span)
    x_min = max(float(np.min(x)), latest_x - window_span)
    x_pad = max(window_span * 0.03, 1.0 if window.trace_time_axis._mode == "clock" else 1e-3)
    y_pad = max(y_span * 0.12, 1e-6)
    window.trace_plot.setXRange(x_min, latest_x + x_pad, padding=0.0)
    window.trace_plot.setYRange(float(np.min(y)) - y_pad, float(np.max(y)) + y_pad, padding=0.0)


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
    if window._plots_frozen:
        return
    window._trace_autoscale_timer.start()


def refresh_trace_plot(window, trace_label: str) -> None:
    if window._plots_frozen:
        return
    window.trace_plot.setLabel("left", trace_label)
    if window._peak_history:
        window._visible_trace_mode = "elapsed"
        window.trace_time_axis.set_mode("elapsed")
        window.trace_plot.setLabel("bottom", "Time (s)")
        render_trace_series(window, window._peak_history, clock_mode=False)
        request_trace_autoscale(window)
        return

    window.trace_time_axis.set_mode("elapsed")
    window.trace_plot.setLabel("bottom", "Time")
    for curve in window.trace_curves.values():
        curve.setData([], [])
    window._visible_trace_x = None
    window._visible_trace_y = None
    window._visible_trace_mode = "elapsed"
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
    active_series = {}
    for metric_name, curve in window.trace_curves.items():
        series = history.get(metric_name, [])
        if metric_name not in window._selected_trace_metrics() or not series:
            curve.setData([], [])
            continue
        if clock_mode:
            x = np.asarray([item[0].timestamp() for item in series], dtype=np.float64)
        else:
            x = np.asarray([item[0] for item in series], dtype=np.float64)
        y = np.asarray([item[1] for item in series], dtype=np.float64)
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
    clock_mode = not bool(window._peak_history)

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
        index = int(np.argmin(np.abs(wavelengths - x)))
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

    best_match: tuple[float, float, float] | None = None
    for x_values, y_values in window._active_trace_series().values():
        if len(x_values) == 0:
            continue
        index = int(np.argmin(np.abs(x_values - mouse_x)))
        candidate_x = float(x_values[index])
        candidate_y = float(y_values[index])
        distance_sq = (candidate_x - mouse_x) ** 2 + (candidate_y - mouse_y) ** 2
        if best_match is None or distance_sq < best_match[2]:
            best_match = (candidate_x, candidate_y, distance_sq)

    if best_match is not None:
        x, y, _ = best_match
    window.trace_vline.setPos(x)
    window.trace_hline.setPos(y)
    window._trace_cursor_text = f"cursor: {x:.3f} s, {y:.3f} nm"
    window.trace_cursor_label.setText(window._trace_cursor_text)
