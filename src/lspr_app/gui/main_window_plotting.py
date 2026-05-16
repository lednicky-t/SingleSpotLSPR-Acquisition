from __future__ import annotations

import logging
from time import perf_counter

import numpy as np

from lspr_app.domain.models import ProcessingSettings, Spectrum
from lspr_app.domain.processing import fit_processed_spectrum
from lspr_app.gui.icon_helpers import math_function_tab_icon, prism_tab_icon
from lspr_app.gui.plot_controller import (
    autoscale_residual_axis as _autoscale_residual_axis,
    autoscale_spectrum_plot as _autoscale_spectrum_plot,
    autoscale_trace_plot as _autoscale_trace_plot,
    flush_deferred_ui_refreshes as _flush_deferred_ui_refreshes,
    flush_plot_refreshes as _flush_plot_refreshes,
    handle_spectrum_mouse_moved as _handle_spectrum_mouse_moved,
    handle_trace_mouse_moved as _handle_trace_mouse_moved,
    refresh_plot as _refresh_plot,
    refresh_trace_plot as _refresh_trace_plot,
    render_trace_series as _render_trace_series,
    request_trace_autoscale as _request_trace_autoscale,
    update_residual_axis_visibility as _update_residual_axis_visibility,
    update_residual_view_geometry as _update_residual_view_geometry,
    update_spectrum_stats as _update_spectrum_stats,
    update_trace_stats as _update_trace_stats,
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
    window._update_poly_warning_indicator(fit)
    window._plot_render_dirty = True
    if not window._plot_refresh_timer.isActive():
        window._plot_refresh_timer.start()
    window._log_throttled(
        "plot_refresh",
        f"Plot refreshed | mode={window.plot_selector.currentText().lower()} | fit={'on' if fit is not None else 'off'}",
        level=logging.DEBUG,
        min_interval=0.75,
    )
    window._request_deferred_ui_refresh(trace_plot=True, summary=True, stats=True, trace_label="Peak position (nm)")
    if window._pending_plot_request is not None:
        pending = window._pending_plot_request
        window._pending_plot_request = None
        start_plot_processing_task_for(window, pending)


def flush_deferred_ui_refreshes_for(window) -> None:
    _flush_deferred_ui_refreshes(window)


def flush_plot_refreshes_for(window) -> None:
    _flush_plot_refreshes(window)


def refresh_plot_for(window) -> None:
    _refresh_plot(window)


def handle_residual_toggle_for(window, visible: bool) -> None:
    window._update_residual_axis_visibility(visible)
    window._update_residual_button_icon()
    window._refresh_plot()
    window._schedule_acquisition_state_persist()


def update_poly_warning_indicator_for(window, fit: Spectrum | None) -> None:
    if fit is None:
        window.poly_warning_label.hide()
        return
    reduced = bool(fit.metadata.get("fit_order_reduced"))
    requested = fit.metadata.get("requested_polynomial_order")
    used = fit.metadata.get("polynomial_order")
    if not reduced or requested is None or used is None:
        window.poly_warning_label.hide()
        return
    window.poly_warning_label.setToolTip(
        f"Requested polynomial order {requested} was reduced to {used} for numerical stability."
    )
    window.poly_warning_label.show()


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


def clear_trace_history_for(window) -> None:
    window._peak_history.clear()
    signal = window._session.state.absorbance or window._session.state.sample
    if signal is not None:
        processed, _ = window._get_analysis_processed_spectrum(signal)
        window._peak_reference_processed = (
            processed.with_metadata(role="peak_reference_reset") if processed is not None else None
        )
    else:
        window._peak_reference_processed = None
    window._refresh_trace_plot("Peak position (nm)")
    window._update_trace_stats()
    window.status_label.setText("Trace history cleared.")


def compute_centroid_nm_for(window, processed: Spectrum, fit: Spectrum | None) -> float:
    return _compute_centroid_nm(processed, fit, window._current_processing_settings())


def reference_peak_nm_for_shift_for(window) -> float | None:
    reference = window._peak_reference_processed
    if reference is None:
        return None
    return float(reference.wavelengths_nm[int(np.nanargmax(reference.values))])


def build_summary_text_for(window) -> str:
    state = window._session.state
    current = window._current_settings()
    processing = window._current_processing_settings()
    return "\n".join(
        [
            f"Source: {'Spectrometer' if window._source_mode == 'spectrometer' else 'Simulation'}",
            f"Backend: {window._spectrometer.device_name()}",
            (
                "Current acquisition: "
                f"integration {current.integration_time_ms:.3f} ms | "
                f"accumulation {current.averages} raw_spectra"
            ),
            (
                "Current processing: "
                f"range {processing.wavelength_min_nm:.2f}-{processing.wavelength_max_nm:.2f} nm | "
                f"baseline {processing.baseline_method} | "
                f"spectral smoothing {processing.smoothing_method} ({processing.smoothing_window}) | "
                f"temporal smoothing {getattr(processing, 'temporal_smoothing', 1)} | "
                f"crop {getattr(processing, 'crop_method', 'fixed_width')} ({getattr(processing, 'crop_fraction', 0.7):.2f}) | "
                f"fit {getattr(processing, 'fit_method', 'none')} | "
                f"poly order {processing.polynomial_order} | "
                f"fit width {processing.fit_window_width_nm:.0f} nm | "
                f"analysis step {getattr(processing, 'analysis_resolution_nm', 0.001):.4f} nm | "
                f"noise window {processing.trace_noise_window_s:.1f} s | "
                f"peak trace {processing.peak_tracking_mode} | "
                f"trace metrics {', '.join(processing.trace_metrics)}"
            ),
            f"Dark: {window._describe_spectrum(state.dark)}",
            f"Reference: {window._describe_spectrum(state.reference)}",
            f"Sample: {window._describe_spectrum(state.sample)}",
            f"Absorbance: {window._describe_spectrum(state.absorbance)}",
        ]
    )


def update_live_estimate_for(window) -> None:
    skipped_rate_hz = live_skip_rate_hz_for(window)
    proc_text = "-" if window._last_processing_ms is None else f"{window._last_processing_ms:.1f} ms"
    headroom_text = headroom_value_text_for(window._processing_headroom_ratio)
    source_rate_text = "-" if window._effective_raw_rate_hz is None else f"{window._effective_raw_rate_hz:.1f} Hz"
    window.live_estimate.setText(
        f"src {source_rate_text} | disp {window.live_rate_spin.value():.2f} Hz | "
        f"proc {proc_text} | head {headroom_text} | skip {skipped_rate_hz:.1f} Hz"
    )
    window._ui_live_estimate_dirty = False


def refresh_telemetry_for(window) -> None:
    if window._last_elapsed_ms is None:
        window.telemetry_label.setText("waiting for first spectrum")
        window._ui_telemetry_dirty = False
        return

    spacing = "-" if window._last_spacing_ms is None else f"{window._last_spacing_ms:.1f} ms"
    overhead = "-" if window._last_overhead_ms is None else f"{window._last_overhead_ms:.1f} ms"
    displayed = "-"
    if window._last_display_average_count is not None and window._last_display_period_ms is not None:
        displayed = f"{window._last_display_average_count}/{window._last_display_period_ms:.0f}"
    window.telemetry_label.setText(
        f"acq {window._last_elapsed_ms:.1f} ms | int {spacing} | "
        f"ovh {overhead} | show {displayed}"
    )
    window._ui_telemetry_dirty = False


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
    color = "#6c7783"
    if headroom_ratio >= 2.0:
        color = "#2e8b57"
    elif headroom_ratio >= 1.0:
        color = "#b8860b"
    else:
        color = "#b44a4a"
    return f"<span style='color:{color}; font-weight:700;'>{headroom_ratio:.2f}x</span>"


def handle_live_setting_change_for(window) -> None:
    window._request_deferred_ui_refresh(live_estimate=True)
    if window._live_active:
        window._display_window_ms = 1000.0 / max(window.live_rate_spin.value(), 1e-9)
        window._live_ui_refresh_delay_ms = max(int(round(window._display_window_ms)), 16)
        window._reset_live_accumulator()
        window._live_display_dropped_frames = 0
        if window._live_worker is not None:
            window._live_worker.update_settings(window._current_settings())
        if window._live_processing_worker is not None:
            window._live_processing_worker.update_settings(window._current_processing_settings())
        window._stats_refresh_timer.stop()
        window._stats_refresh_timer.start(window._live_ui_refresh_delay_ms)
        window._request_deferred_ui_refresh(trace_plot=True, live_estimate=True)
        window._request_trace_autoscale()
        window.status_label.setText("Live display window reset after settings change.")
        window._log_debug("Live display reset after settings change.")
    elif window._source_mode == "simulation":
        window._session.set_sample(window._build_simulation_spectrum("sample"))
        window._schedule_processing_refresh()
        window._request_trace_autoscale()
        window._log_debug("Simulation spectrum refreshed after settings change.")
    window._schedule_acquisition_state_persist()


def handle_simulation_output_rate_change_for(window) -> None:
    if window._live_active and window._source_mode == "simulation" and window._live_worker is not None:
        window._live_worker.update_cycle_period(1.0 / max(window.sim_output_rate_spin.value(), 1e-9))
        window._stats_refresh_timer.stop()
        window._stats_refresh_timer.start(window._live_ui_refresh_delay_ms)
        window._request_deferred_ui_refresh(trace_plot=True, live_estimate=True)
        window._request_trace_autoscale()
    window._schedule_acquisition_state_persist()
    window._log_throttled(
        "simulation_output_rate",
        f"Simulation output rate set to {window.sim_output_rate_spin.value():.2f} Hz.",
        level=logging.DEBUG,
        min_interval=1.0,
    )


def update_window_mode_label_for(window) -> None:
    if not hasattr(window, "_window_mode_label"):
        return
    source_name = "Spectrometer" if window._source_mode == "spectrometer" else "Simulation"
    if window._measurement_active:
        text = f"Measurement | {source_name}"
        tooltip = "A measurement is currently active."
    elif window._live_active:
        text = f"Live mode | {source_name}"
        tooltip = "Live acquisition is running."
    else:
        text = f"Free mode | {source_name}"
        tooltip = "The app is open but no measurement is running."
    window._window_mode_label.setText(text)
    window._window_mode_label.setToolTip(tooltip)
    if hasattr(window, "_window_mode_icon_label"):
        source_icon = prism_tab_icon() if window._source_mode == "spectrometer" else math_function_tab_icon()
        window._window_mode_icon_label.setPixmap(source_icon.pixmap(16, 16))
        window._window_mode_icon_label.setToolTip(f"{source_name} source")


# Convenience aliases for window delegation.
update_spectrum_stats_for = _update_spectrum_stats
update_trace_stats_for = _update_trace_stats
refresh_trace_plot_for = _refresh_trace_plot
render_trace_series_for = _render_trace_series
autoscale_spectrum_plot_for = _autoscale_spectrum_plot
autoscale_trace_plot_for = _autoscale_trace_plot
autoscale_residual_axis_for = _autoscale_residual_axis
update_residual_view_geometry_for = _update_residual_view_geometry
update_residual_axis_visibility_for = _update_residual_axis_visibility
request_trace_autoscale_for = _request_trace_autoscale
handle_spectrum_mouse_moved_for = _handle_spectrum_mouse_moved
handle_trace_mouse_moved_for = _handle_trace_mouse_moved
