from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter

import pyqtgraph as pg
from pyqtgraph import exporters as _pg_exporters

from lspr_app.gui.main_window_startup_diagnostics import notify_startup_data_rendered_for as _notify_startup_data_rendered

from PyQt6.QtWidgets import QFileDialog, QMessageBox, QVBoxLayout, QWidget

from lspr_app.storage.csv_export import export_spectrum_to_csv as _export_spectrum_to_csv

import numpy as np

from lspr_app.domain.models import ProcessingSettings, Spectrum
from lspr_app.domain.processing import fit_processed_spectrum, processing_debug_mode_enabled
from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QColor

from lspr_app.gui.icon_helpers import crosshair_cursor_icon, stats_hidden_icon
from lspr_app.gui.plot_controller import (
    apply_processing_range_to_spectrum_plot as _apply_processing_range_to_spectrum_plot,
    flush_deferred_display_refreshes as _flush_deferred_display_refreshes,
    flush_deferred_metric_refreshes as _flush_deferred_metric_refreshes,
    flush_deferred_stats_refreshes as _flush_deferred_stats_refreshes,
    flush_deferred_ui_refreshes as _flush_deferred_ui_refreshes,
    refresh_plot as _refresh_plot,
)
from lspr_app.gui.spectrum_plot_controller import (
    autoscale_residual_axis as _autoscale_residual_axis,
    update_residual_axis_visibility as _update_residual_axis_visibility,
    update_residual_view_geometry as _update_residual_view_geometry,
)
from lspr_app.gui.plot_controller import (
    autoscale_spectrum_plot as _autoscale_spectrum_plot,
    clip_series_to_window as _clip_series_to_window,
    downsample_spectrum_series_for_view as _downsample_spectrum_series_for_view,
    handle_spectrum_mouse_moved as _handle_spectrum_mouse_moved,
    spectrum_render_cache_key as _spectrum_render_cache_key,
    update_spectrum_stats as _update_spectrum_stats,
)
from lspr_app.gui.trace_plot_controller import (
    autoscale_metric_plot as _autoscale_metric_plot,
    handle_metric_mouse_moved as _handle_metric_mouse_moved,
    refresh_metric_plot as _refresh_metric_plot,
    render_metric_series as _render_metric_series,
    request_metric_autoscale as _request_metric_autoscale,
    update_metric_stats as _update_metric_stats,
)
from lspr_app.gui.processing_helpers import (
    analysis_cache_token as _analysis_cache_token,
    analysis_metrics_cache_token as _analysis_metrics_cache_token,
    compute_centroid_nm as _compute_centroid_nm,
    compute_metric_nm as _compute_metric_nm,
    compute_peak_metric_nm as _compute_peak_metric_nm,
    compute_trace_metrics as _compute_trace_metrics,
    get_analysis_metrics as _get_analysis_metrics,
    get_dense_analysis_curve as _get_dense_analysis_curve,
    get_processed_spectrum as _get_processed_spectrum,
    needs_gaussian_metric as _needs_gaussian_metric,
    processing_cache_token as _processing_cache_token,
)
from lspr_app.gui.main_window_logging import build_pipeline_timing_breakdown_for, _timing_plain_text
from lspr_app.gui.runtime_diagnostics import format_rate_for_window
from lspr_app.gui.main_window_plot_widgets import _spline_render_series
from lspr_app.gui.workers import ProcessingRequest, ProcessingResult, ProcessingTask

def get_analysis_processed_spectrum_for(window, signal: Spectrum | None) -> tuple[Spectrum | None, Spectrum | None]:
    if signal is None:
        return None, None
    if window._last_processed_plot is not None and window._last_processed_plot.acquired_at == signal.acquired_at:
        return window._last_processed_plot, window._last_fit_plot
    return _get_processed_spectrum(signal, window._current_processing_settings())


def get_processed_spectrum_for(
    window,
    spectrum: Spectrum | None,
    settings: ProcessingSettings,
) -> tuple[Spectrum | None, Spectrum | None]:
    token = _processing_cache_token(spectrum, settings)
    if token is not None and token == window._processed_cache_key:
        return window._processed_cache_result
    result = _get_processed_spectrum(spectrum, settings)
    window._processed_cache_key = token
    window._processed_cache_result = result
    return result


def compute_peak_metric_nm_for(window, processed: Spectrum, fit: Spectrum | None) -> float:
    return _compute_peak_metric_nm(processed, fit, window._current_processing_settings())


def compute_trace_metrics_for(window, processed: Spectrum, fit: Spectrum | None) -> dict[str, float]:
    return _compute_trace_metrics(processed, fit, window._current_processing_settings(), window._selected_trace_metrics())


def compute_metric_nm_for(window, mode: str, processed: Spectrum, fit: Spectrum | None) -> float:
    return _compute_metric_nm(mode, processed, fit, window._current_processing_settings())


def processing_cache_token_for(window, spectrum: Spectrum | None, settings: ProcessingSettings) -> tuple[object, ...] | None:
    return _processing_cache_token(spectrum, settings)


def analysis_cache_token_for(window, processed: Spectrum | None, fit: Spectrum | None, settings: ProcessingSettings) -> tuple[object, ...] | None:
    return _analysis_cache_token(processed, fit, settings)


def analysis_metrics_cache_token_for(window, processed: Spectrum | None, fit: Spectrum | None, settings: ProcessingSettings) -> tuple[object, ...] | None:
    return _analysis_metrics_cache_token(processed, fit, settings)


def needs_gaussian_metric_for(window, settings: ProcessingSettings | None = None) -> bool:
    return _needs_gaussian_metric(settings or window._current_processing_settings())


def get_dense_analysis_curve_for(
    window,
    processed: Spectrum | None,
    fit: Spectrum | None,
    settings: ProcessingSettings,
) -> tuple[np.ndarray, np.ndarray, dict[str, float | str]]:
    token = _analysis_cache_token(processed, fit, settings)
    if token is not None and token == window._analysis_cache_key:
        return window._analysis_cache_result
    result = _get_dense_analysis_curve(processed, fit, settings)
    window._analysis_cache_key = token
    window._analysis_cache_result = result
    return result


def get_analysis_metrics_for(
    window,
    processed: Spectrum | None,
    fit: Spectrum | None,
    settings: ProcessingSettings,
) -> dict[str, float | str]:
    token = _analysis_metrics_cache_token(processed, fit, settings)
    if token is not None and token == window._analysis_metrics_cache_key:
        return window._analysis_metrics_cache_result
    result = _get_analysis_metrics(processed, fit, settings)
    window._analysis_metrics_cache_key = token
    window._analysis_metrics_cache_result = result
    return result


def temporal_history_token_for(window, processed: Spectrum | None) -> tuple[object, ...] | None:
    if processed is None:
        return None
    settings = window._current_processing_settings()
    return (
        window.PLOT_MODES[window.plot_selector.currentText()],
        window._source_mode,
        len(processed.wavelengths_nm),
        float(processed.wavelengths_nm[0]) if len(processed.wavelengths_nm) else None,
        float(processed.wavelengths_nm[-1]) if len(processed.wavelengths_nm) else None,
        settings.temporal_smoothing,
        settings.crop_method,
        settings.crop_fraction,
        settings.fit_method,
        settings.fit_window_width_nm,
    )


def apply_temporal_smoothing_for(window, processed: Spectrum | None) -> Spectrum | None:
    if processed is None:
        window._temporal_processed_history.clear()
        window._temporal_history_key = None
        return None

    settings = window._current_processing_settings()
    window_size = max(int(getattr(settings, "temporal_smoothing", 1)), 1)
    key = temporal_history_token_for(window, processed)
    if key != window._temporal_history_key:
        window._temporal_processed_history.clear()
        window._temporal_history_key = key

    window._temporal_processed_history.append(processed)
    if len(window._temporal_processed_history) > window_size:
        window._temporal_processed_history = window._temporal_processed_history[-window_size:]
    if window_size <= 1 or len(window._temporal_processed_history) == 1:
        return processed

    stack = np.vstack([item.values for item in window._temporal_processed_history])
    averaged_values = np.nanmean(stack, axis=0)
    return Spectrum(
        wavelengths_nm=processed.wavelengths_nm.copy(),
        values=averaged_values,
        y_label=processed.y_label,
        acquired_at=processed.acquired_at,
        metadata={
            **processed.metadata,
            "temporal_smoothing": window_size,
            "temporal_average_count": len(window._temporal_processed_history),
        },
    )


def enqueue_plot_processing_for(window) -> None:
    if window._plots_frozen:
        return
    plot_mode = window.PLOT_MODES[window.plot_selector.currentText()]
    raw_spectrum = window._session.get_plot_data(plot_mode)
    settings = window._current_processing_settings()
    window._plot_processing_epoch += 1
    request = ProcessingRequest(spectrum=raw_spectrum, settings=settings, epoch=window._plot_processing_epoch)
    if window._plot_processing_running:
        window._pending_plot_request = request
        return
    start_plot_processing_task_for(window, request)


def start_plot_processing_task_for(window, request: ProcessingRequest) -> None:
    window._plot_processing_running = True
    window._active_plot_processing_epoch = request.epoch
    task = ProcessingTask(request)
    task.signals.finished.connect(window._handle_plot_processing_result)
    window._thread_pool.start(task)


def handle_plot_processing_result_for(window, result: ProcessingResult) -> None:
    if window._closing:
        window._plot_processing_running = False
        window._pending_plot_request = None
        return

    window._plot_processing_running = False
    if result.epoch != window._active_plot_processing_epoch:
        if window._pending_plot_request is not None:
            pending = window._pending_plot_request
            window._pending_plot_request = None
            start_plot_processing_task_for(window, pending)
        return

    processed = apply_temporal_smoothing_for(window, result.processed)
    fit = result.fit
    if processed is not None and fit is not None and processed is not result.processed:
        fit = fit_processed_spectrum(processed, window._current_processing_settings())
    window._last_processing_ms = result.processing_ms
    if result.processing_ms > 0:
        window._processing_rate_hz = 1000.0 / result.processing_ms
        display_period_ms = max(1000.0 / max(window.live_rate_spin.value(), 1e-9), 1.0)
        window._processing_headroom_ratio = display_period_ms / result.processing_ms
    else:
        window._processing_rate_hz = None
        window._processing_headroom_ratio = None
    window._last_processed_plot = processed
    window._last_fit_plot = fit
    window._processed_cache_key = None
    window._processed_cache_result = (processed, fit)
    window._analysis_cache_key = None
    window._analysis_cache_result = (np.empty(0, dtype=np.float64), np.empty(0, dtype=np.float64), {})
    window._analysis_metrics_cache_key = None
    window._analysis_metrics_cache_result = {}
    try:
        refresh_spectrum_plot_for(window, processed, fit)
        window._autoscale_spectrum_plot()
        # The "sample" (raw) live path (acquisition_controller.py,
        # handle_live_processing_result) updates spectrum_stats_label and the
        # sensorgram trace itself for that mode. This is the equivalent
        # completion point for Dark/Reference/Absorbance - without this,
        # spectrum_stats_label stayed frozen on whatever it last showed in
        # Sample mode, and the sensorgram trace/"Start Tracking" button never
        # saw Absorbance data at all (it only ever received the raw
        # processed/fit - see the comment removed from that call site).
        # Withheld the same way refresh_spectrum_plot_for withholds the curve
        # while device discovery hasn't finished yet - no spectra means no
        # derived stats either.
        if getattr(window, "_device_discovery_complete", True):
            window._update_spectrum_stats(processed, fit)
        else:
            window._update_spectrum_stats(None, None)
    except Exception as exc:
        window._log_error(f"Spectrum refresh failed: {exc}")
    window._update_poly_warning_indicator(fit)
    plot_mode = window.PLOT_MODES.get(window.plot_selector.currentText())
    if plot_mode == "absorbance":
        # Dark/Reference are static snapshots - excluded from the trace/
        # tracking on purpose, same as the raw live path already does.
        window._append_processed_trace_history(processed, fit)
    window._request_deferred_ui_refresh(trace_plot=True, live_estimate=True, telemetry=True, trace_label="Metric position (nm)")
    if window._pending_plot_request is not None:
        pending = window._pending_plot_request
        window._pending_plot_request = None
        start_plot_processing_task_for(window, pending)


def _metric_nm_from_analysis(mode: str, analysis_metrics: dict, fallback_nm: float) -> float:
    """Read one metric's wavelength out of an already-computed analysis-
    metrics dict, with the same fallback chain compute_metric_nm/
    compute_centroid_nm use (finite metric value -> dense_max_nm -> the raw
    peak). Exists so refresh_spectrum_plot_for's marker block can reuse one
    cached window._get_analysis_metrics() call for poly_max/gaussian_center/
    centroid instead of each going through its own uncached
    compute_metric_nm/compute_centroid_nm call - each of which independently
    rebuilds the full dense analysis curve (a real cost, not free) even
    though get_analysis_metrics_for already caches exactly this per
    processed/fit pair. See _analysis_metrics_cache_key.
    """
    value = analysis_metrics.get(mode)
    if isinstance(value, (int, float)) and np.isfinite(float(value)):
        return float(value)
    dense_max = analysis_metrics.get("dense_max_nm")
    if isinstance(dense_max, (int, float)) and np.isfinite(float(dense_max)):
        return float(dense_max)
    return float(fallback_nm)


_RAW_COUNTS_Y_LABEL = "Intensity (counts)"
# Fallback only - used if window._auto_exposure_settings isn't available for
# some reason. Normally the line's position tracks
# AutoExposureSettings.saturation_fraction directly (see below), so the two
# never drift out of sync the way they used to.
DEFAULT_SATURATION_WARNING_FRACTION = 0.95


def _update_saturation_line_for(window, processed) -> None:
    """Position the red saturation-warning line at the same fraction of the
    detector's full scale as auto-exposure's own "too bright" threshold
    (AutoExposureSettings.saturation_fraction - see
    gui/acquisition_controller.py's auto_exposure_handle_live_frame_for), so
    the line always shows where auto-exposure would actually back off,
    rather than an independent hardcoded value that can silently disagree
    with it.

    Only meaningful for raw hardware counts (not simulation "a.u." values or the
    derived Absorbance plot), since those are not bounded by the spectrometer's
    ADC bit depth.
    """
    saturation_line = getattr(window, "saturation_line", None)
    if saturation_line is None:
        return
    if processed is None or processed.y_label != _RAW_COUNTS_Y_LABEL:
        saturation_line.setVisible(False)
        return
    max_intensity_fn = getattr(window._spectrometer, "max_intensity", None)
    if not callable(max_intensity_fn):
        saturation_line.setVisible(False)
        return
    try:
        max_intensity = float(max_intensity_fn())
    except Exception:
        saturation_line.setVisible(False)
        return
    if not np.isfinite(max_intensity) or max_intensity <= 0:
        saturation_line.setVisible(False)
        return
    auto_exposure_settings = getattr(window, "_auto_exposure_settings", None)
    saturation_fraction = getattr(auto_exposure_settings, "saturation_fraction", DEFAULT_SATURATION_WARNING_FRACTION)
    saturation_line.setPos(max_intensity * saturation_fraction)
    saturation_line.setVisible(True)


def refresh_spectrum_plot_for(window, processed, fit) -> None:
    """Update the spectrum plot curves, markers, and residual display.

    Skips the update entirely if the plot is frozen or if the combined render
    key (data + view range) has not changed since the last call.
    """
    if window._plots_frozen:
        return
    if not getattr(window, "_device_discovery_complete", True):
        window.spectrum_curve.setData([], [])
        window.fit_curve.setData([], [])
        window._clear_residual_display()
        window.fit_region_item.hide()
        window.max_marker.setData([], [])
        window.poly_marker.setData([], [])
        window.gaussian_marker.setData([], [])
        window.centroid_marker.setData([], [])
        window.spectrum_plot.setLabel("left", "Signal")
        placeholder = getattr(window, "_discovery_placeholder_text", None)
        if placeholder is not None:
            placeholder.setText("Scanning for devices...")
            placeholder.show()
            window._reposition_discovery_placeholder()
        return
    placeholder = getattr(window, "_discovery_placeholder_text", None)
    if placeholder is not None:
        placeholder.hide()
    view_x_min = view_x_max = view_width_px = None
    try:
        view_box = window.spectrum_plot.getPlotItem().vb
        view_range = view_box.viewRange()
        x_range = view_range[0]
        view_x_min = float(x_range[0])
        view_x_max = float(x_range[1])
        scene_rect = view_box.sceneBoundingRect()
        if scene_rect is not None:
            view_width_px = float(scene_rect.width())
            if not np.isfinite(view_width_px):
                view_width_px = None
    except Exception:
        view_x_min = view_x_max = view_width_px = None

    residual_visible = bool(fit is not None and window.show_residual_button.isChecked())
    show_gaussian = window._needs_gaussian_metric()
    render_key = _spectrum_render_cache_key(
        processed,
        fit,
        view_x_min=view_x_min,
        view_x_max=view_x_max,
        view_width_px=view_width_px,
        residual_visible=residual_visible,
        show_gaussian=show_gaussian,
        spectrum_tracking_mode=window._current_processing_settings().spectrum_tracking_mode,
    )
    if render_key == window._spectrum_render_cache_key:
        return
    window._spectrum_render_cache_key = render_key
    window._visible_processed_plot = processed
    window._visible_fit_plot = fit
    if processed is None:
        window.spectrum_curve.setData([], [])
        window.fit_curve.setData([], [])
        window._clear_residual_display()
        window.fit_region_item.hide()
        window.max_marker.setData([], [])
        window.poly_marker.setData([], [])
        window.gaussian_marker.setData([], [])
        window.centroid_marker.setData([], [])
        window.spectrum_plot.setLabel("left", "Signal")
        window._update_residual_axis_visibility(False)
        window._spectrum_processing_region_bounds = None
        window._spectrum_fit_region_bounds = None
        window._last_spectrum_curve_update_ms = None
        window._last_spectrum_fit_update_ms = None
        window._last_spectrum_marker_update_ms = None
        window._last_spectrum_residual_update_ms = None
        _update_saturation_line_for(window, None)
        return

    low = float(np.min(processed.wavelengths_nm))
    high = float(np.max(processed.wavelengths_nm))
    processing_bounds = (low, high)
    if processing_bounds != window._spectrum_processing_region_bounds:
        window.processing_region_item.setRegion(processing_bounds)
        window._spectrum_processing_region_bounds = processing_bounds

    display_x, display_y = _downsample_spectrum_series_for_view(
        np.asarray(processed.wavelengths_nm, dtype=np.float64),
        np.asarray(processed.values, dtype=np.float64),
        view_x_min=view_x_min,
        view_x_max=view_x_max,
        view_width_px=view_width_px,
    )
    line_step_mode = getattr(window, "_sensorgram_line_step_mode", None)
    curve_started = perf_counter()
    if line_step_mode == "spline":
        spline_x, spline_y = _spline_render_series(display_x, display_y)
        window.spectrum_curve.setData(spline_x, spline_y, skipFiniteCheck=True, stepMode=False)
    elif line_step_mode is None:
        window.spectrum_curve.setData(display_x, display_y, skipFiniteCheck=True, stepMode=False)
    else:
        window.spectrum_curve.setData(display_x, display_y, skipFiniteCheck=True, stepMode=line_step_mode)
    window._last_spectrum_curve_update_ms = (perf_counter() - curve_started) * 1000.0
    if fit is not None:
        fit_values = np.asarray(fit.values, dtype=np.float64)
        fit_low = float(fit.metadata.get("fit_window_min_nm", np.min(fit.wavelengths_nm)))
        fit_high = float(fit.metadata.get("fit_window_max_nm", np.max(fit.wavelengths_nm)))
        display_fit_x, _ = _clip_series_to_window(
            display_x,
            display_y,
            window_min=fit_low,
            window_max=fit_high,
        )
        if len(display_fit_x) > 0 and len(fit.wavelengths_nm) > 0:
            display_fit_y = np.interp(
                display_fit_x,
                np.asarray(fit.wavelengths_nm, dtype=np.float64),
                fit_values,
            )
        else:
            display_fit_x = np.empty(0, dtype=np.float64)
            display_fit_y = fit_values[:0]
        fit_started = perf_counter()
        if line_step_mode == "spline":
            fit_curve_x, fit_curve_y = _spline_render_series(display_fit_x, display_fit_y)
            window.fit_curve.setData(fit_curve_x, fit_curve_y, skipFiniteCheck=True, stepMode=False)
        elif line_step_mode is None:
            window.fit_curve.setData(display_fit_x, display_fit_y, skipFiniteCheck=True, stepMode=False)
        else:
            window.fit_curve.setData(display_fit_x, display_fit_y, skipFiniteCheck=True, stepMode=line_step_mode)
        window._last_spectrum_fit_update_ms = (perf_counter() - fit_started) * 1000.0
        window.fit_region_item.show()
        residual_started = perf_counter()
        if residual_visible:
            residual_base = np.interp(
                display_fit_x,
                np.asarray(processed.wavelengths_nm, dtype=np.float64),
                np.asarray(processed.values, dtype=np.float64),
            )
            with np.errstate(divide="ignore", invalid="ignore"):
                residual_values = np.where(
                    np.abs(display_fit_y) > 1e-12,
                    ((residual_base - display_fit_y) / display_fit_y) * 100.0,
                    np.nan,
                )
            window._render_residual_display(display_fit_x, residual_values)
            if not getattr(window, "_residual_axis_autoscaled", False):
                window._autoscale_residual_axis()
        else:
            window._clear_residual_display()
        window._last_spectrum_residual_update_ms = (perf_counter() - residual_started) * 1000.0
        fit_bounds = (fit_low, fit_high)
        if fit_bounds != window._spectrum_fit_region_bounds:
            window.fit_region_item.setRegion(fit_bounds)
            window._spectrum_fit_region_bounds = fit_bounds
    else:
        fit_started = perf_counter()
        window.fit_curve.setData([], [])
        window._last_spectrum_fit_update_ms = (perf_counter() - fit_started) * 1000.0
        window.fit_region_item.hide()
        residual_started = perf_counter()
        window._clear_residual_display()
        window._last_spectrum_residual_update_ms = (perf_counter() - residual_started) * 1000.0
        fit_bounds = (low, low)
        if fit_bounds != window._spectrum_fit_region_bounds:
            window.fit_region_item.setRegion(fit_bounds)
            window._spectrum_fit_region_bounds = fit_bounds
    window._update_residual_axis_visibility(residual_visible)
    window.spectrum_plot.setLabel("left", processed.y_label)
    _update_saturation_line_for(window, processed)
    marker_started = perf_counter()
    finite_indices = np.flatnonzero(np.isfinite(processed.values))
    if len(finite_indices) == 0:
        window.max_marker.setData([], [])
        window.poly_marker.setData([], [])
        window.centroid_marker.setData([], [])
        window.gaussian_marker.setData([], [])
        window._last_spectrum_marker_update_ms = (perf_counter() - marker_started) * 1000.0
        return
    max_index = int(finite_indices[int(np.argmax(np.asarray(processed.values, dtype=np.float64)[finite_indices]))])
    max_x = float(processed.wavelengths_nm[max_index])
    max_y = float(processed.values[max_index])
    # One cached analysis-metrics lookup instead of three separate uncached
    # compute_metric_nm/compute_centroid_nm calls below, each of which used
    # to independently rebuild the full dense analysis curve per call -
    # see _metric_nm_from_analysis's docstring.
    analysis_metrics = window._get_analysis_metrics(processed, fit)
    poly_x = _metric_nm_from_analysis("poly_max", analysis_metrics, max_x)
    if fit is not None and len(fit.wavelengths_nm) > 0:
        poly_y = float(np.interp(poly_x, fit.wavelengths_nm, fit.values))
    else:
        poly_y = float(np.interp(poly_x, processed.wavelengths_nm, processed.values))
    show_gaussian = window._needs_gaussian_metric()
    if show_gaussian:
        gaussian_x = _metric_nm_from_analysis("gaussian_center", analysis_metrics, max_x)
        if fit is not None and fit.metadata.get("fit_method") == "gaussian" and len(fit.wavelengths_nm) > 0:
            gaussian_y = float(np.interp(gaussian_x, fit.wavelengths_nm, fit.values))
        else:
            gaussian_y = float(np.interp(gaussian_x, processed.wavelengths_nm, processed.values))
        if np.isfinite(gaussian_x) and np.isfinite(gaussian_y):
            window.gaussian_marker.setData([{"pos": (float(gaussian_x), float(gaussian_y)), "data": "gaussian"}])
            window.gaussian_marker.show()
        else:
            window.gaussian_marker.setData([], [])
            window.gaussian_marker.hide()
    else:
        window.gaussian_marker.setData([], [])
        window.gaussian_marker.hide()
    centroid_x = _metric_nm_from_analysis("centroid", analysis_metrics, max_x)
    centroid_y = float(np.interp(centroid_x, processed.wavelengths_nm, processed.values))
    window._set_scatter_marker(window.max_marker, max_x, max_y, "max")
    window._set_scatter_marker(window.poly_marker, poly_x, poly_y, "poly")
    window._set_scatter_marker(window.centroid_marker, centroid_x, centroid_y, "centroid")
    window._last_spectrum_marker_update_ms = (perf_counter() - marker_started) * 1000.0
    _notify_startup_data_rendered(window, "spectrum")


def flush_deferred_ui_refreshes_for(window) -> None:
    started = perf_counter()
    _flush_deferred_ui_refreshes(window)
    elapsed_ms = (perf_counter() - started) * 1000.0
    window._last_deferred_ui_refresh_ms = elapsed_ms
    if bool(getattr(window, "_deep_timing_enabled", False)):
        if elapsed_ms >= 2.0:
            window._log_throttled(
                "gui_deferred_refresh",
                f"GUI deferred refresh: {elapsed_ms:.2f} ms",
                level=logging.INFO,
                min_interval=0.5,
            )


def flush_deferred_display_refreshes_for(window) -> None:
    started = perf_counter()
    requested_at = getattr(window, "_display_refresh_requested_at", None)
    if requested_at is not None:
        try:
            window._last_display_refresh_delay_ms = max((started - float(requested_at)) * 1000.0, 0.0)
        except (TypeError, ValueError):
            window._last_display_refresh_delay_ms = None
    refresh_state = getattr(window, "_ui_refresh_state", None)
    summary_dirty = bool(getattr(refresh_state, "summary_dirty", False))
    telemetry_dirty = bool(getattr(refresh_state, "telemetry_dirty", False))
    live_estimate_dirty = bool(getattr(refresh_state, "live_estimate_dirty", False))
    metric_dirty = bool(getattr(refresh_state, "metric_plot_dirty", False))
    _flush_deferred_display_refreshes(window)
    elapsed_ms = (perf_counter() - started) * 1000.0
    window._last_deferred_display_refresh_ms = elapsed_ms
    if bool(getattr(window, "_live_active", False)):
        window._live_mode_deferred_display_flush_count = int(getattr(window, "_live_mode_deferred_display_flush_count", 0)) + 1
    if bool(getattr(window, "_deep_timing_enabled", False)):
        if elapsed_ms >= 2.0:
            window._log_throttled(
                "gui_deferred_display_refresh",
                (
                    f"GUI deferred display refresh: {elapsed_ms:.2f} ms | "
                    f"summary={int(summary_dirty)} telemetry={int(telemetry_dirty)} "
                    f"live_estimate={int(live_estimate_dirty)} trace={int(metric_dirty)}"
                ),
                level=logging.INFO,
                min_interval=0.5,
            )


def flush_deferred_metric_refreshes_for(window) -> None:
    started = perf_counter()
    requested_at = getattr(window, "_metric_refresh_requested_at", None)
    if requested_at is not None:
        try:
            window._last_metric_refresh_delay_ms = max((started - float(requested_at)) * 1000.0, 0.0)
        except (TypeError, ValueError):
            window._last_metric_refresh_delay_ms = None
    refresh_state = getattr(window, "_ui_refresh_state", None)
    metric_dirty = bool(getattr(refresh_state, "metric_plot_dirty", False))
    pending_label = getattr(refresh_state, "pending_metric_label", None) if refresh_state is not None else None
    if getattr(window, "_metric_flush_in_progress", False):
        window._last_deferred_metric_refresh_ms = (perf_counter() - started) * 1000.0
        window._log_throttled(
            "gui_deferred_metric_refresh_reentry",
            "GUI deferred metric refresh skipped because a previous flush is still in progress",
            level=logging.WARNING,
            min_interval=5.0,
        )
        return
    if metric_dirty and not bool(getattr(window, "_sensorgram_frozen", False)):
        window._metric_flush_in_progress = True
        try:
            _flush_deferred_metric_refreshes(window)
        finally:
            window._metric_flush_in_progress = False
    elapsed_ms = (perf_counter() - started) * 1000.0
    window._last_deferred_metric_refresh_ms = elapsed_ms
    if bool(getattr(window, "_live_active", False)):
        window._live_mode_deferred_metric_flush_count = int(getattr(window, "_live_mode_deferred_metric_flush_count", 0)) + 1
    if bool(getattr(window, "_deep_timing_enabled", False)):
        if elapsed_ms >= 2.0:
            window._log_throttled(
                "gui_deferred_metric_refresh",
                (
                    f"GUI deferred metric refresh: {elapsed_ms:.2f} ms | "
                    f"dirty={int(metric_dirty)} label={pending_label or '-'}"
                ),
                level=logging.INFO,
                min_interval=0.5,
            )


def flush_deferred_stats_refreshes_for(window) -> None:
    started = perf_counter()
    requested_at = getattr(window, "_stats_refresh_requested_at", None)
    if requested_at is not None:
        try:
            window._last_stats_refresh_delay_ms = max((started - float(requested_at)) * 1000.0, 0.0)
        except (TypeError, ValueError):
            window._last_stats_refresh_delay_ms = None
    refresh_state = getattr(window, "_ui_refresh_state", None)
    stats_dirty = bool(getattr(refresh_state, "stats_dirty", False))
    _flush_deferred_stats_refreshes(window)
    elapsed_ms = (perf_counter() - started) * 1000.0
    window._last_deferred_stats_refresh_ms = elapsed_ms
    if bool(getattr(window, "_live_active", False)):
        window._live_mode_deferred_stats_flush_count = int(getattr(window, "_live_mode_deferred_stats_flush_count", 0)) + 1
    if bool(getattr(window, "_deep_timing_enabled", False)):
        if elapsed_ms >= 2.0:
            window._log_throttled(
                "gui_deferred_stats_refresh",
                f"GUI deferred stats refresh: {elapsed_ms:.2f} ms | stats={int(stats_dirty)}",
                level=logging.INFO,
                min_interval=0.5,
            )


def refresh_plot_for(window) -> None:
    started = perf_counter()
    _refresh_plot(window)
    if bool(getattr(window, "_deep_timing_enabled", False)):
        elapsed_ms = (perf_counter() - started) * 1000.0
        if elapsed_ms >= 2.0:
            window._log_throttled(
                "gui_refresh_plot",
                f"GUI refresh_plot: {elapsed_ms:.2f} ms",
                level=logging.INFO,
                min_interval=0.5,
            )


def handle_residual_toggle_for(window, visible: bool) -> None:
    if hasattr(window, "residual_view"):
        if not visible:
            current_range = window.residual_view.viewRange()[1]
            y_min = float(current_range[0])
            y_max = float(current_range[1])
            if np.isfinite(y_min) and np.isfinite(y_max) and y_max > y_min:
                window._residual_y_range = [y_min, y_max]
        window.residual_view._manual_y_zoom = False
    window._update_residual_axis_visibility(visible)
    window._update_residual_button_icon()
    if visible:
        window._update_residual_view_geometry()
        saved_range = getattr(window, "_residual_y_range", None)
        if (
            isinstance(saved_range, list)
            and len(saved_range) == 2
            and all(isinstance(item, (int, float)) for item in saved_range)
        ):
            y_min = float(saved_range[0])
            y_max = float(saved_range[1])
            if y_max > y_min:
                window.residual_view.setYRange(y_min, y_max, padding=0.0)
                window._residual_axis_autoscaled = True
            else:
                window._residual_axis_autoscaled = False
        else:
            window._residual_axis_autoscaled = False
    else:
        window._residual_axis_autoscaled = False
    window._refresh_plot()
    window._schedule_acquisition_state_persist()


def update_poly_warning_indicator_for(window, fit: Spectrum | None) -> None:
    spin = getattr(window, "poly_order_spin", None)
    if spin is None:
        return
    default_tooltip = str(getattr(window, "_poly_order_default_tooltip", spin.toolTip()) or "").strip()
    if fit is None:
        spin.setStyleSheet("")
        if default_tooltip:
            spin.setToolTip(default_tooltip)
        return
    reduced = bool(fit.metadata.get("fit_order_reduced"))
    requested = fit.metadata.get("requested_polynomial_order")
    used = fit.metadata.get("polynomial_order")
    if not reduced or requested is None or used is None:
        spin.setStyleSheet("")
        if default_tooltip:
            spin.setToolTip(default_tooltip)
        return
    spin.setStyleSheet(
        """
        QSpinBox {
            color: #d64545;
            border: 1px solid #d64545;
        }
        QSpinBox::up-button, QSpinBox::down-button {
            border-left: 1px solid #d64545;
        }
        """
    )
    spin.setToolTip(
        f"{default_tooltip}\nWarning: the polynomial order was reduced from {requested} to {used} for numerical stability."
    )


def set_plots_frozen_for(window, frozen: bool) -> None:
    window._plots_frozen = frozen
    if hasattr(window, "freeze_plots_button") and window.freeze_plots_button.isChecked() != frozen:
        window.freeze_plots_button.blockSignals(True)
        window.freeze_plots_button.setChecked(frozen)
        window.freeze_plots_button.blockSignals(False)
    window._update_freeze_button_icon()
    if not frozen:
        window._refresh_plot()
    window._schedule_acquisition_state_persist()


def set_sensorgram_frozen_for(window, frozen: bool) -> None:
    window._sensorgram_frozen = frozen
    if hasattr(window, "sensorgram_freeze_button") and window.sensorgram_freeze_button.isChecked() != frozen:
        window.sensorgram_freeze_button.blockSignals(True)
        window.sensorgram_freeze_button.setChecked(frozen)
        window.sensorgram_freeze_button.blockSignals(False)
    window._update_sensorgram_freeze_button_icon()
    if not frozen:
        window._refresh_trace_plot("Metric position (nm)")
        window._ui_refresh_state.metric_plot_dirty = False
        window._request_trace_autoscale()
    if hasattr(window, "status_label"):
        freeze_state = "On" if frozen else "Off"
        window.status_label.setText(f"Sensorgram freeze {freeze_state}.")
    window._schedule_acquisition_state_persist()


def set_sensorgram_tracking_active_for(window, active: bool) -> None:
    """Toggle whether new spectra feed the sensorgram trace/metrics archive.

    Off by default - append_processed_trace_history (acquisition_controller.py)
    returns immediately while this is False, so nothing is plotted or written
    to either HDF5 file's metrics table until the user explicitly starts
    tracking on whatever spectrum/mode is currently displayed. Pausable: this
    only gates future points, existing history is untouched either way, so
    turning tracking off and back on (e.g. to glance back at Dark/Reference
    mid-run) just leaves a time gap rather than losing anything.
    """
    window._sensorgram_tracking_active = active
    if hasattr(window, "start_tracking_button") and window.start_tracking_button.isChecked() != active:
        window.start_tracking_button.blockSignals(True)
        window.start_tracking_button.setChecked(active)
        window.start_tracking_button.blockSignals(False)
    if hasattr(window, "_refresh_tracking_ready_indicator"):
        # Stops the ready-to-track blink the moment tracking starts, and
        # re-evaluates it (resumes blinking if dark/reference are still
        # captured) when tracking is paused again.
        window._refresh_tracking_ready_indicator()
    elif hasattr(window, "_update_start_tracking_button_icon"):
        window._update_start_tracking_button_icon()
    if hasattr(window, "status_label"):
        tracking_state = "started" if active else "paused"
        window.status_label.setText(f"Sensorgram tracking {tracking_state}.")
    window._schedule_acquisition_state_persist()


def handle_start_tracking_button_clicked_for(window, checked: bool) -> None:
    """Confirm before pausing an already-active tracking session.

    Starting tracking (checked=True) always proceeds immediately - it's a
    deliberate, low-risk action. Pausing an active session (checked=False)
    breaks the trace's continuity (a time gap, per
    set_sensorgram_tracking_active_for's own docstring), which is easy to
    trigger by an accidental click, so confirm first. If the maintainer backs
    out, the button's check state (already flipped by Qt before this click
    handler runs) is reverted without ever calling
    set_sensorgram_tracking_active_for - tracking keeps running.
    """
    was_active = bool(getattr(window, "_sensorgram_tracking_active", False))
    if not checked and was_active:
        answer = QMessageBox.question(
            window,
            "Pause tracking",
            "Are you sure to pause tracing the spectra?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            button = getattr(window, "start_tracking_button", None)
            if button is not None:
                button.blockSignals(True)
                button.setChecked(True)
                button.blockSignals(False)
            return
    set_sensorgram_tracking_active_for(window, checked)


def clear_trace_history_for(window) -> None:
    if hasattr(window, "_metric_render_display_cache"):
        window._metric_render_display_cache.clear()
    if hasattr(window, "_metric_render_state_cache"):
        window._metric_render_state_cache.clear()
    if hasattr(window, "_plot_view_cache"):
        window._plot_view_cache.clear()
    # Null the reload tokens so any in-flight reload task is discarded when its result arrives.
    if hasattr(window, "_sensorgram_metric_archive_reload_request_token"):
        window._sensorgram_metric_archive_reload_request_token = None
    if hasattr(window, "_sensorgram_metric_archive_reload_pending_token"):
        window._sensorgram_metric_archive_reload_pending_token = None
    if hasattr(window, "_sensorgram_metric_archive_reload_loading"):
        window._sensorgram_metric_archive_reload_loading = False
    if hasattr(window, "_sensorgram_metric_archive_reload_task"):
        window._sensorgram_metric_archive_reload_task = None
    # Reset the autoscale range so the axis re-fits from scratch when the first new
    # point arrives after the clear, rather than keeping the stale zoom from before.
    window._last_metric_autoscale_range = None
    if hasattr(window, "_sensorgram_control_step_events"):
        window._sensorgram_control_step_events.clear()
    if hasattr(window, "_sensorgram_control_step_overlay_items"):
        for item in list(getattr(window, "_sensorgram_control_step_overlay_items", [])):
            try:
                item.setVisible(False)
            except Exception:
                pass
    signal = window._session.state.absorbance or window._session.state.sample
    if signal is not None:
        processed, _ = window._get_analysis_processed_spectrum(signal)
        window._metric_reference_processed = (
            processed.with_metadata(role="metric_reference_reset") if processed is not None else None
        )
    else:
        window._metric_reference_processed = None
    # Schedule a deferred refresh rather than doing it synchronously: any pending
    # deferred timer that was queued before the clear will be coalesced into this one,
    # avoiding a double-render and an axis-reset before new data arrives.
    if hasattr(window, "_request_deferred_ui_refresh"):
        window._request_deferred_ui_refresh(trace_plot=True, trace_label="Metric position (nm)")
    if hasattr(window, "_update_trace_stats"):
        window._update_trace_stats()
    freeze_state = "On" if bool(getattr(window, "_sensorgram_frozen", False)) else "Off"
    window.status_label.setText(f"Metric history cleared. Sensorgram freeze {freeze_state}.")


def compute_centroid_nm_for(window, processed: Spectrum, fit: Spectrum | None) -> float:
    return _compute_centroid_nm(processed, fit, window._current_processing_settings())


def reference_peak_nm_for_shift_for(window) -> float | None:
    reference = window._metric_reference_processed
    if reference is None:
        return None
    finite = np.isfinite(reference.values)
    if not np.any(finite):
        return None
    finite_indices = np.flatnonzero(finite)
    peak_index = int(finite_indices[int(np.argmax(np.asarray(reference.values, dtype=np.float64)[finite_indices]))])
    return float(reference.wavelengths_nm[peak_index])


def build_summary_text_for(window) -> str:
    current = window._current_settings()
    processing = window._current_processing_settings()
    project_destination = "-"
    if hasattr(window, "recording_project_destination"):
        project_destination = str(window.recording_project_destination() or "").strip() or "-"
    experiment_name = "-"
    if hasattr(window, "recording_experiment_name"):
        experiment_name = str(window.recording_experiment_name() or "").strip() or "-"
    compression_enabled = "-"
    if hasattr(window, "measurement_hdf5_compression_enabled"):
        compression_enabled = "enabled" if window.measurement_hdf5_compression_enabled() else "disabled"
    smoothing_method = str(processing.smoothing_method).replace("_", " ")
    crop_method = str(getattr(processing, "crop_method", "fixed_width")).replace("_", " ")
    fit_method = str(getattr(processing, "fit_method", "none")).replace("_", " ")
    spectrum_tracking_mode = str(processing.spectrum_tracking_mode).replace("_", " ")
    return "\n".join(
        [
            "Recording settings",
            f"  Project destination: {project_destination}",
            f"  Experiment name: {experiment_name}",
            f"  HDF5 compression after recording: {compression_enabled}",
            "",
            "Source and acquisition settings",
            f"  Source mode: {'Spectrometer' if window._source_mode == 'spectrometer' else 'Simulation'}",
            f"  Backend: {window._spectrometer.device_name()}",
            "",
            "Acquisition settings",
            f"  Integration time: {current.integration_time_ms:.3f} ms",
            f"  Accumulation: {current.averages} raw_spectra",
            f"  Correct dark counts: {'yes' if current.correct_dark_counts else 'no'}",
            f"  Correct nonlinearity: {'yes' if current.correct_nonlinearity else 'no'}",
            "",
            "Processing settings",
            f"  Range: {processing.wavelength_min_nm:.2f}-{processing.wavelength_max_nm:.2f} nm",
            f"  Baseline removal: {processing.baseline_method}",
            f"  Spectral smoothing: {smoothing_method} ({processing.smoothing_window})",
            f"  Temporal smoothing: {getattr(processing, 'temporal_smoothing', 1)}",
            f"  Crop: {crop_method} ({getattr(processing, 'crop_fraction', 0.7):.2f})",
            f"  Fit: {fit_method}",
            f"  Polynomial order: {processing.polynomial_order}",
            f"  Fit width: {processing.fit_window_width_nm:.0f} nm",
            f"  Analysis resolution: {getattr(processing, 'analysis_resolution_nm', 0.001):.6f} nm",
            f"  Noise window: {processing.trace_noise_window_s:.1f} s",
            f"  Spectrum trace: {spectrum_tracking_mode}",
            f"  Metric traces: {', '.join(processing.trace_metrics)}",
        ]
    )


def update_live_estimate_for(window) -> None:
    skipped_rate_hz = live_skip_rate_hz_for(window)
    proc_text = "-" if window._last_processing_ms is None else f"{window._last_processing_ms:.1f} ms"
    headroom_text = headroom_value_text_for(window._processing_headroom_ratio)
    # auto_seconds_key: in "ms" timing-display mode, a low rate's period can
    # get large enough to be unreadable (e.g. live_rate_spin allows down to
    # 0.1 Hz, a 10 s period) - each gets its own key so switching one to
    # seconds (with hysteresis, see format_ms_auto_unit) doesn't affect the
    # others.
    source_rate_text = format_rate_for_window(window, window._effective_raw_rate_hz, decimals=1, auto_seconds_key="live_estimate_src")
    desired_refresh_text = format_rate_for_window(window, window.live_rate_spin.value(), decimals=1, auto_seconds_key="live_estimate_disp_target")
    actual_refresh_text = format_rate_for_window(window, getattr(window, "_actual_plot_refresh_rate_hz", None), decimals=1, auto_seconds_key="live_estimate_recent_avg")
    wait_text = ""
    if processing_debug_mode_enabled() and window._last_processing_queue_wait_ms is not None:
        wait_text = f" | wait {window._last_processing_queue_wait_ms:.1f} ms"
    window.live_estimate.setText(
        f"src {source_rate_text} | disp target {desired_refresh_text} | recent avg {actual_refresh_text} | "
        f"proc {proc_text}{wait_text} | head {headroom_text} | "
        f"skip {format_rate_for_window(window, skipped_rate_hz, decimals=1, auto_seconds_key='live_estimate_skip')}"
    )
    window._ui_refresh_state.live_estimate_dirty = False


def build_spectrometer_stats_text_for(window) -> str:
    spacing_text = _timing_plain_text(getattr(window, "_last_spacing_ms", None))
    rate_text = "-"
    effective_rate = getattr(window, "_effective_raw_rate_hz", None)
    if effective_rate is not None:
        try:
            rate_text = format_rate_for_window(window, float(effective_rate), decimals=1)
        except (TypeError, ValueError):
            rate_text = "-"
    overhead_text = _timing_plain_text(getattr(window, "_last_overhead_ms", None))
    return f"spacing {spacing_text} | rate {rate_text} | ovh {overhead_text}"


def refresh_spectrometer_stats_for(window) -> None:
    if not hasattr(window, "spectrometer_stats_label"):
        return
    window.spectrometer_stats_label.setText(build_spectrometer_stats_text_for(window))


def _timing_share_text(value_ms: float | None, total_ms: float | None) -> str:
    if value_ms is None:
        return "-"
    try:
        value = float(value_ms)
        total = float(total_ms) if total_ms is not None else None
    except (TypeError, ValueError):
        return "-"
    if total is None or not np.isfinite(value) or not np.isfinite(total) or total <= 0:
        return f"{value:.1f} ms"
    return f"{value:.1f} ms ({value / total * 100.0:.1f}%)"


def refresh_telemetry_for(window) -> None:
    refresh_spectrometer_stats_for(window)
    if window._last_elapsed_ms is None:
        if hasattr(window, "telemetry_label"):
            window.telemetry_label.setStyleSheet("")
        window.telemetry_label.setText("waiting for first spectrum")
        window._ui_refresh_state.telemetry_dirty = False
        return
    if hasattr(window, "telemetry_label"):
        window.telemetry_label.setStyleSheet("")
    window.telemetry_label.setText(build_pipeline_telemetry_text_for(window))
    window._ui_refresh_state.telemetry_dirty = False


def build_pipeline_telemetry_text_for(window) -> str:
    breakdown = build_pipeline_timing_breakdown_for(window)
    # Deliberately plain ms throughout, not auto-unit - see
    # test_build_pipeline_telemetry_text_uses_plain_times: this is a
    # side-by-side performance breakdown, where consistent units across all
    # fields matter more than any single field being "readable" on its own.
    spacing = _timing_plain_text(getattr(window, "_last_spacing_ms", None))
    overhead = _timing_plain_text(getattr(window, "_last_overhead_ms", None))
    latency = _timing_plain_text(getattr(window, "_last_elapsed_ms", None))
    processing_total_ms = None
    if breakdown["processing_wait_ms"] is not None or breakdown["processing_ms"] is not None:
        processing_total_ms = float(breakdown["processing_wait_ms"] or 0.0) + float(breakdown["processing_ms"] or 0.0)
    processing_text = _timing_plain_text(processing_total_ms)
    plot_text = _timing_plain_text(breakdown["plot_render_ms"])
    display_text = _timing_plain_text(breakdown["deferred_display_ms"])
    stats_text = _timing_plain_text(breakdown["deferred_stats_ms"])
    idle_text = _timing_plain_text(breakdown["idle_ms"])
    live_result_queue = getattr(window, "_live_result_queue", None)
    live_processed_queue = getattr(window, "_live_processed_queue", None)
    live_result_queue_text = "-"
    live_processed_queue_text = "-"
    try:
        if live_result_queue is not None:
            live_result_queue_text = str(max(int(live_result_queue.qsize()), 0))
    except (AttributeError, NotImplementedError, OSError, TypeError, ValueError):
        live_result_queue_text = "-"
    try:
        if live_processed_queue is not None:
            live_processed_queue_text = str(max(int(live_processed_queue.qsize()), 0))
    except (AttributeError, NotImplementedError, OSError, TypeError, ValueError):
        live_processed_queue_text = "-"
    displayed = "-"
    if window._last_display_average_count is not None and window._last_display_period_ms is not None:
        displayed = f"{window._last_display_average_count}/{window._last_display_period_ms:.0f}"
    return (
        f"gap {spacing} | acq {latency} | proc {processing_text} | "
        f"plot {plot_text} | disp {display_text} | stats {stats_text} | idle {idle_text} | "
        f"srcq {live_result_queue_text} | procq {live_processed_queue_text} | "
        f"ovh {overhead} | show {displayed}"
    )


def live_skip_rate_hz_for(window) -> float:
    if window._live_display_started_at is None:
        return 0.0
    elapsed_s = perf_counter() - window._live_display_started_at
    if elapsed_s <= 0:
        return 0.0
    return float(window._live_display_dropped_frames) / elapsed_s


def headroom_value_text_for(headroom_ratio: float | None) -> str:
    if headroom_ratio is None or not np.isfinite(float(headroom_ratio)):
        return "-"
    return f"{headroom_ratio:.2f}x"


def handle_live_setting_change_for(window) -> None:
    window._ui_refresh_state.live_estimate_dirty = True
    if window._live_active:
        window._display_window_ms = 1000.0 / max(window.live_rate_spin.value(), 1e-9)
        window._live_ui_refresh_delay_ms = max(int(round(window._display_window_ms)), 16)
        window._reset_live_accumulator()
        window._live_display_dropped_frames = 0
        if window._live_worker is not None:
            window._live_worker.update_settings(window._current_settings())
        if window._live_processing_worker is not None:
            window._live_processing_worker.update_settings(window._current_processing_settings())
        # Re-arm the live polling cadence so the new refresh rate takes effect immediately.
        if hasattr(window, "_ui_task_scheduler"):
            window._ui_task_scheduler.cancel("live_visual_refresh")
            window._ui_task_scheduler.cancel("live_acquisition")
            window._ui_task_scheduler.cancel("live_processed")
        window._ui_refresh_state.metric_plot_dirty = True
        window._ui_refresh_state.telemetry_dirty = True
        window._ui_refresh_state.pending_metric_label = "Metric position (nm)"
        window._request_live_visual_refresh(0)
        window._request_trace_autoscale()
        window.status_label.setText("Live display window reset after settings change.")
    elif window._source_mode == "simulation":
        window._session.set_absorbance_direct(window._build_simulation_spectrum("sample"))
        window._schedule_processing_refresh()
        window._request_trace_autoscale()
    window._schedule_acquisition_state_persist()


def handle_simulation_output_rate_change_for(window) -> None:
    if window._live_active and window._source_mode == "simulation" and window._live_worker is not None:
        window._live_worker.update_cycle_period(1.0 / max(window.sim_output_rate_spin.value(), 1e-9))
        window._request_deferred_ui_refresh(trace_plot=True, live_estimate=True)
        window._request_trace_autoscale()
    window._schedule_acquisition_state_persist()


# Convenience aliases for window delegation.
update_spectrum_stats_for = _update_spectrum_stats
update_metric_stats_for = _update_metric_stats
update_trace_stats_for = _update_metric_stats
autoscale_spectrum_plot_for = _autoscale_spectrum_plot
apply_processing_range_to_spectrum_plot_for = _apply_processing_range_to_spectrum_plot
autoscale_metric_plot_for = _autoscale_metric_plot
autoscale_trace_plot_for = _autoscale_metric_plot
autoscale_residual_axis_for = _autoscale_residual_axis
update_residual_view_geometry_for = _update_residual_view_geometry
update_residual_axis_visibility_for = _update_residual_axis_visibility
request_metric_autoscale_for = _request_metric_autoscale
request_trace_autoscale_for = _request_metric_autoscale
handle_spectrum_mouse_moved_for = _handle_spectrum_mouse_moved
handle_metric_mouse_moved_for = _handle_metric_mouse_moved
handle_trace_mouse_moved_for = _handle_metric_mouse_moved
refresh_metric_plot_for = _refresh_metric_plot
refresh_trace_plot_for = _refresh_metric_plot
render_metric_series_for = _render_metric_series
render_trace_series_for = _render_metric_series


def export_current_plot_for(window) -> None:
    """Export the currently displayed spectrum plot to PNG, SVG, or CSV."""
    plot_mode = window.PLOT_MODES[window.plot_selector.currentText()]
    spectrum = window._last_processed_plot
    if spectrum is None:
        window.status_label.setText("There is no plotted data to export.")
        return

    default_name = f"{plot_mode}_{spectrum.acquired_at.strftime('%Y%m%d_%H%M%S')}.png"
    default_path = Path.cwd() / "data" / default_name
    file_path, _ = QFileDialog.getSaveFileName(
        window,
        "Export graph",
        str(default_path),
        "PNG image (*.png);;SVG vector (*.svg);;CSV data (*.csv)",
    )
    if not file_path:
        window.status_label.setText("Export cancelled.")
        return

    destination = Path(file_path)
    suffix = destination.suffix.lower()
    if suffix == ".csv":
        _export_spectrum_to_csv(destination, spectrum)
        window.status_label.setText(f"Exported {plot_mode} data to {file_path}")
        return

    if suffix not in {".png", ".svg"}:
        destination = destination.with_suffix(".png")
        suffix = ".png"

    if suffix == ".png":
        exporter = _pg_exporters.ImageExporter(window.spectrum_plot.plotItem)
        exporter.parameters()["width"] = 1600
        exporter.export(str(destination))
    else:
        exporter = _pg_exporters.SVGExporter(window.spectrum_plot.plotItem)
        exporter.export(str(destination))
    window.status_label.setText(f"Exported {plot_mode} graph to {destination}")


def apply_metric_color_styles_for(window) -> None:
    """Apply per-metric pen/brush colors to spectrum curves, markers, and envelope bands."""
    colors = getattr(window, "TRACE_METRIC_COLORS", {})
    if not isinstance(colors, dict):
        return
    line_width = max(float(getattr(window, "_sensorgram_line_width_px", 2.2)), 0.5)
    if hasattr(window, "spectrum_curve"):
        window.spectrum_curve.setPen(pg.mkPen("#1f77b4", width=line_width))
    if hasattr(window, "fit_curve"):
        window.fit_curve.setPen(pg.mkPen("#d95f02", width=line_width, style=Qt.PenStyle.DashLine))
    for metric_name, curve in getattr(window, "trace_curves", {}).items():
        if hasattr(curve, "setPen"):
            curve.setPen(pg.mkPen(colors.get(metric_name, "#444444"), width=line_width))
    for metric_name, marker in (
        ("smoothed_max", getattr(window, "max_marker", None)),
        ("centroid", getattr(window, "centroid_marker", None)),
        ("poly_max", getattr(window, "poly_marker", None)),
        ("gaussian_center", getattr(window, "gaussian_marker", None)),
    ):
        if marker is not None and hasattr(marker, "setBrush"):
            marker.setBrush(pg.mkBrush(colors.get(metric_name, "#444444")))
    for metric_name, band in getattr(window, "trace_metric_envelope_bands", {}).items():
        if band is None or not hasattr(band, "setBrush"):
            continue
        base_color = QColor(colors.get(metric_name, "#1F77B4"))
        if not base_color.isValid():
            base_color = QColor("#1F77B4")
        band_alpha = int(round(max(min(float(getattr(window, "_sensorgram_metric_envelope_overlay_alpha", 16)), 100.0), 0.0) * 2.55))
        base_color.setAlpha(band_alpha)
        band.setBrush(pg.mkBrush(base_color))


_SPECTRUM_Y_AXIS_FORMAT_LABELS = {"scientific": "Sci", "si": "SI"}


def _normalize_spectrum_y_axis_format_mode(mode: object) -> str:
    normalized = str(mode or "scientific").strip().lower()
    return normalized if normalized in _SPECTRUM_Y_AXIS_FORMAT_LABELS else "scientific"


def update_spectrum_y_axis_format_button_for(window) -> None:
    button = getattr(window, "spectrum_y_axis_format_button", None)
    if button is None:
        return
    mode = _normalize_spectrum_y_axis_format_mode(getattr(window, "_spectrum_y_axis_format_mode", "scientific"))
    other_mode = "si" if mode == "scientific" else "scientific"
    button.setText(f"[{_SPECTRUM_Y_AXIS_FORMAT_LABELS[mode]}]")
    button.setToolTip(
        f"Y-axis numbers: {_SPECTRUM_Y_AXIS_FORMAT_LABELS[mode]} notation "
        f"(e.g. {'6.55e+04' if mode == 'scientific' else '65.5k'}). "
        f"Click to switch to {_SPECTRUM_Y_AXIS_FORMAT_LABELS[other_mode]}."
    )


def apply_spectrum_y_axis_format_mode_for(window, mode: object) -> None:
    normalized = _normalize_spectrum_y_axis_format_mode(mode)
    window._spectrum_y_axis_format_mode = normalized
    update_spectrum_y_axis_format_button_for(window)
    axis = getattr(window, "spectrum_left_axis", None)
    if axis is not None:
        axis.set_format_mode(normalized)


def cycle_spectrum_y_axis_format_mode_for(window) -> None:
    current = _normalize_spectrum_y_axis_format_mode(getattr(window, "_spectrum_y_axis_format_mode", "scientific"))
    next_mode = "si" if current == "scientific" else "scientific"
    apply_spectrum_y_axis_format_mode_for(window, next_mode)
    if hasattr(window, "_schedule_ui_state_persist"):
        window._schedule_ui_state_persist()


_CORNER_OVERLAY_MARGIN = 6
_CURSOR_OFF_TEXT = "cursor: off (click to enable)"
_STATS_OFF_TEXT = "stats hidden (double-click to show)"
_SPECTRUM_STATS_FALLBACK_TEXT = "peak: -<br>centroid: -<br>FWHM: -<br>MSE: -<br>R: -<br>S/N: -"
_TRACE_STATS_FALLBACK_TEXT = "latest: - | min/max: - | span: - | dt -"


_CORNER_OVERLAY_OBJECT_NAMES = (
    "spectrumStatsOverlay",
    "spectrumCursorOverlay",
    "traceStatsOverlay",
    "traceCursorOverlay",
)


def _style_corner_overlay_container(container: QWidget) -> None:
    container.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    # Deliberately theme-invariant (not %(fg)s/%(bg)s from main_window_style.py):
    # this box needs to stay readable sitting on top of whatever the plot is
    # currently drawing underneath it, in either light or dark app theme, so a
    # fixed dark translucent backdrop + fixed light text is safer than
    # tracking the app theme.
    selectors = ", ".join(f"QWidget#{name}" for name in _CORNER_OVERLAY_OBJECT_NAMES)
    label_selectors = ", ".join(f"QWidget#{name} QLabel" for name in _CORNER_OVERLAY_OBJECT_NAMES)
    container.setStyleSheet(
        f"{selectors} {{ background: rgba(15, 17, 20, 0.72); border-radius: 6px; }}"
        f" {label_selectors} {{ background: transparent; color: #d7dee6; }}"
    )


class _CornerOverlayContainer(QWidget):
    """Corner overlay box that forwards unhandled right-clicks to the plot
    underneath instead of swallowing them.

    WA_TransparentForMouseEvents was tried first and reverted - per Qt's own
    docs that attribute "disables the delivery of mouse events to the widget
    AND ITS CHILDREN", which also silently broke
    spectrum_stats_label/trace_stats_label/spectrum_cursor_label/trace_cursor_label's
    own click-to-cycle/toggle/hide handlers (installEventFilter-based,
    watching for left-button MouseButtonPress and MouseButtonDblClick) - a
    real regression, reported by the maintainer. Overriding contextMenuEvent
    instead only intercepts the
    right-click/context-menu request specifically: a click landing on the
    container's own background/margin generates a QContextMenuEvent that
    reaches this override directly; a click landing on the label itself is
    offered to the label first, and QLabel doesn't handle context menu
    events, so Qt propagates the ignored event up to this parent - either
    way it ends up here, while ordinary mouse press/release events (what the
    label's own toggle logic watches for) are never touched.
    """

    def __init__(self, parent_viewport, plot_widget) -> None:
        super().__init__(parent_viewport)
        self._plot_widget = plot_widget

    def contextMenuEvent(self, event) -> None:
        plot_widget = self._plot_widget
        try:
            view_box = plot_widget.getPlotItem().vb
        except Exception:
            event.ignore()
            return
        if view_box is None or not view_box.menuEnabled():
            event.ignore()
            return
        # QContextMenuEvent.globalPos() returns a plain QPoint (unlike
        # QMouseEvent, it has no globalPosition()/QPointF variant in PyQt6) -
        # wrap it in QPointF since raiseContextMenu calls .toPoint() on
        # whatever screenPos() returns, matching pyqtgraph's own
        # MouseClickEvent.screenPos() contract.
        screen_pos = QPointF(event.globalPos())
        shim = type("_MenuEventShim", (), {"screenPos": lambda self, _pos=screen_pos: _pos})()
        view_box.raiseContextMenu(shim)
        event.accept()


def _make_corner_overlay_container(parent_viewport, object_name: str, label, plot_widget) -> QWidget:
    container = _CornerOverlayContainer(parent_viewport, plot_widget)
    container.setObjectName(object_name)
    layout = QVBoxLayout()
    layout.setContentsMargins(6, 4, 6, 4)
    layout.setSpacing(2)
    layout.addWidget(label)
    container.setLayout(layout)
    _style_corner_overlay_container(container)
    return container


def build_spectrum_corner_overlay_for(window) -> None:
    """Pin spectrum_stats_label to the plot's top-left corner and
    spectrum_cursor_label to its top-right corner, each in its own separate
    box, instead of a shared row above the plot (see main_window_layout.py,
    where that row used to be built) - frees the vertical space that row
    used to take.
    """
    window.spectrum_stats_label.setWordWrap(False)
    window.spectrum_stats_label.setCursor(Qt.CursorShape.PointingHandCursor)
    window.spectrum_stats_label.setToolTip(
        window.spectrum_stats_label.toolTip() + " Double-click to show/hide."
    )
    window.spectrum_stats_label.installEventFilter(window)
    window._spectrum_stats_enabled = True
    window._spectrum_stats_off_icon = stats_hidden_icon()
    window.spectrum_cursor_label.setCursor(Qt.CursorShape.PointingHandCursor)
    window.spectrum_cursor_label.setToolTip(
        "Spectrum cursor readout under the mouse pointer. Click to show/hide (also hides the plot crosshair)."
    )
    window._spectrum_cursor_enabled = True
    window._spectrum_cursor_off_icon = crosshair_cursor_icon()

    viewport = window.spectrum_plot.viewport()
    window.spectrum_stats_overlay = _make_corner_overlay_container(viewport, "spectrumStatsOverlay", window.spectrum_stats_label, window.spectrum_plot)
    window.spectrum_cursor_overlay = _make_corner_overlay_container(viewport, "spectrumCursorOverlay", window.spectrum_cursor_label, window.spectrum_plot)
    window.spectrum_cursor_label.installEventFilter(window)

    window.spectrum_plot.getPlotItem().vb.sigResized.connect(window._reposition_spectrum_corner_overlay)
    window.spectrum_stats_overlay.show()
    window.spectrum_cursor_overlay.show()
    reposition_spectrum_corner_overlay_for(window)


def build_trace_corner_overlay_for(window) -> None:
    """Same idea as build_spectrum_corner_overlay_for, for trace_plot -
    trace_stats_label (top-left) keeps its existing click-to-cycle-metric
    behavior (see main_window_lifecycle.py's event_filter_for);
    trace_cursor_label (top-right) gains the same click-to-show/hide toggle
    as the spectrum plot's cursor label. Both shift down out of the way when
    the experiment-control-step timeline bar is shown at the top of this
    plot - see _sensorgram_overlay_top_margin_px.
    """
    window.trace_stats_label.setToolTip(
        window.trace_stats_label.toolTip() + " Double-click to show/hide."
    )
    window._trace_stats_enabled = True
    window._trace_stats_off_icon = stats_hidden_icon()
    window.trace_cursor_label.setCursor(Qt.CursorShape.PointingHandCursor)
    window.trace_cursor_label.setToolTip(
        "Metric cursor readout under the mouse pointer. Click to show/hide (also hides the plot crosshair)."
    )
    window._trace_cursor_enabled = True
    window._trace_cursor_off_icon = crosshair_cursor_icon()

    viewport = window.trace_plot.viewport()
    window.trace_stats_overlay = _make_corner_overlay_container(viewport, "traceStatsOverlay", window.trace_stats_label, window.trace_plot)
    window.trace_cursor_overlay = _make_corner_overlay_container(viewport, "traceCursorOverlay", window.trace_cursor_label, window.trace_plot)
    window.trace_cursor_label.installEventFilter(window)

    window.trace_plot.getPlotItem().vb.sigResized.connect(window._reposition_trace_corner_overlay)
    window.trace_stats_overlay.show()
    window.trace_cursor_overlay.show()
    reposition_trace_corner_overlay_for(window)


def _reposition_corner_overlay(plot_widget, container, *, align: str, vertical: str = "top") -> None:
    if plot_widget is None or container is None:
        return
    try:
        view_box = plot_widget.getPlotItem().vb
        scene_rect = view_box.sceneBoundingRect()
        # QGraphicsView.mapFromScene() returns viewport-relative coordinates
        # (per Qt docs) - container is parented on plot_widget.viewport(), not
        # plot_widget itself, so this lines up directly with no further offset.
        if vertical == "bottom":
            corner_scene = scene_rect.bottomRight() if align == "right" else scene_rect.bottomLeft()
        else:
            corner_scene = scene_rect.topRight() if align == "right" else scene_rect.topLeft()
        corner = plot_widget.mapFromScene(corner_scene)
    except Exception:
        return
    container.adjustSize()
    if vertical == "bottom":
        top = int(corner.y()) - container.height() - _CORNER_OVERLAY_MARGIN
    else:
        top = int(corner.y()) + _CORNER_OVERLAY_MARGIN
    if align == "right":
        left = int(corner.x()) - container.width() - _CORNER_OVERLAY_MARGIN
    else:
        left = int(corner.x()) + _CORNER_OVERLAY_MARGIN
    container.move(left, top)
    container.raise_()


def _sensorgram_overlay_top_margin_px(window) -> int:
    """Whether trace_plot's corner overlays need to move out of the way of
    the experiment-control-step timeline bar (main_window_sensorgram_overlay.py)
    - nonzero (the configured bar height) whenever that bar is shown at the
    top of the plot, 0 whenever it's disabled or positioned at the bottom
    instead. reposition_trace_corner_overlay_for uses this only as a
    yes/no signal (move the overlays to the bottom corners instead of the
    top) - not as a pixel offset added to the top position, since sitting
    right under the timeline bar would put the stats at an arbitrary height
    unrelated to the plot's own axis.
    """
    enabled = bool(getattr(window, "_sensorgram_control_step_overlay_enabled", True))
    position = str(getattr(window, "_sensorgram_control_step_overlay_position", "top")).strip().lower()
    if not enabled or position != "top":
        return 0
    bar_height_px = max(int(getattr(window, "_sensorgram_control_step_overlay_bar_height_px", 18)), 1)
    return bar_height_px + 6


def reposition_spectrum_corner_overlay_for(window) -> None:
    plot = getattr(window, "spectrum_plot", None)
    _reposition_corner_overlay(plot, getattr(window, "spectrum_stats_overlay", None), align="left")
    _reposition_corner_overlay(plot, getattr(window, "spectrum_cursor_overlay", None), align="right")


def reposition_trace_corner_overlay_for(window) -> None:
    plot = getattr(window, "trace_plot", None)
    # When the control-step timeline bar occupies the top of the plot, move
    # the overlays to the bottom corners (near the plot's own x-axis) instead
    # of nudging them down by the bar's height - keeps them readable next to
    # the axis regardless of where the timeline bar or the data happens to
    # sit, per the maintainer's explicit preference.
    vertical = "bottom" if _sensorgram_overlay_top_margin_px(window) > 0 else "top"
    _reposition_corner_overlay(plot, getattr(window, "trace_stats_overlay", None), align="left", vertical=vertical)
    _reposition_corner_overlay(plot, getattr(window, "trace_cursor_overlay", None), align="right", vertical=vertical)


def _show_off_state(label, off_icon, fallback_text: str) -> None:
    """Collapse a corner-overlay label to a small icon while its content is
    toggled off, instead of a "click to enable"/"double-click to show"
    sentence - takes far less corner space and reads at a glance. Falls back
    to the given placeholder text if no icon was precomputed (e.g. a minimal
    fake window in a pure-logic unit test that never called
    build_spectrum_corner_overlay_for/build_trace_corner_overlay_for).
    """
    if off_icon is None or not hasattr(label, "setPixmap"):
        label.setText(fallback_text)
        return
    size = label.fontMetrics().height()
    label.setPixmap(off_icon.pixmap(size, size))


def toggle_spectrum_cursor_enabled_for(window) -> None:
    window._spectrum_cursor_enabled = not getattr(window, "_spectrum_cursor_enabled", True)
    if window._spectrum_cursor_enabled:
        window.spectrum_cursor_label.setText(getattr(window, "_spectrum_cursor_text", "-"))
    else:
        _show_off_state(window.spectrum_cursor_label, getattr(window, "_spectrum_cursor_off_icon", None), _CURSOR_OFF_TEXT)
    for line_name in ("spectrum_vline", "spectrum_hline"):
        line = getattr(window, line_name, None)
        if line is not None:
            line.setVisible(window._spectrum_cursor_enabled)
    reposition_spectrum_corner_overlay_for(window)


def toggle_trace_cursor_enabled_for(window) -> None:
    window._trace_cursor_enabled = not getattr(window, "_trace_cursor_enabled", True)
    if window._trace_cursor_enabled:
        window.trace_cursor_label.setText(getattr(window, "_trace_cursor_text", "-"))
    else:
        _show_off_state(window.trace_cursor_label, getattr(window, "_trace_cursor_off_icon", None), _CURSOR_OFF_TEXT)
    for line_name in ("trace_vline", "trace_hline"):
        line = getattr(window, line_name, None)
        if line is not None:
            line.setVisible(window._trace_cursor_enabled)
    reposition_trace_corner_overlay_for(window)


def toggle_spectrum_stats_enabled_for(window) -> None:
    window._spectrum_stats_enabled = not getattr(window, "_spectrum_stats_enabled", True)
    if window._spectrum_stats_enabled:
        window.spectrum_stats_label.setText(getattr(window, "_spectrum_stats_html", _SPECTRUM_STATS_FALLBACK_TEXT))
    else:
        _show_off_state(window.spectrum_stats_label, getattr(window, "_spectrum_stats_off_icon", None), _STATS_OFF_TEXT)
    reposition_spectrum_corner_overlay_for(window)


def toggle_trace_stats_enabled_for(window) -> None:
    window._trace_stats_enabled = not getattr(window, "_trace_stats_enabled", True)
    if window._trace_stats_enabled:
        window.trace_stats_label.setText(getattr(window, "_trace_stats_html", _TRACE_STATS_FALLBACK_TEXT))
    else:
        _show_off_state(window.trace_stats_label, getattr(window, "_trace_stats_off_icon", None), _STATS_OFF_TEXT)
    reposition_trace_corner_overlay_for(window)
