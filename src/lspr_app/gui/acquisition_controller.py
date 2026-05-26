from __future__ import annotations

import multiprocessing as mp
import queue
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import perf_counter

import numpy as np
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QFileDialog, QInputDialog

from lspr_app.domain.models import Spectrum
from lspr_app.domain.session import MeasurementError
from lspr_app.gui.icon_helpers import flow_tabler_icon, math_function_tab_icon, prism_tab_icon, tint_tabler_icon, transport_icon
from lspr_app.gui.history_utils import trim_history_tail_in_place
from lspr_app.gui.main_window_headers import update_source_link_buttons
from lspr_app.gui.trace_history_buffer import TraceHistoryBuffer
from lspr_app.gui.workers import (
    AcquisitionRequest,
    AcquisitionResult,
    AcquisitionTask,
    LiveAcquisitionEvent,
    LiveAcquisitionWorker,
    LiveProcessedEvent,
    LiveProcessingWorker,
    MeasurementCompressionResult,
    MeasurementCompressionTask,
)
from lspr_app.storage.hdf5_export import AsyncHDF5MeasurementWriter


def _flush_live_processing_logs(window) -> None:
    log_queue = getattr(window, "_live_processing_log_queue", None)
    if log_queue is None:
        return
    while True:
        try:
            levelno, source, message = log_queue.get_nowait()
        except queue.Empty:
            return
        except Exception:
            return
        try:
            window._log_event(int(levelno), str(message), source=str(source))
        except Exception:
            continue


def _start_measurement_file_compression(window, path: Path) -> None:
    if hasattr(window, "_set_measurement_compression_busy_indicator"):
        window._set_measurement_compression_busy_indicator(True)
    task = MeasurementCompressionTask(path)
    task.signals.finished.connect(window._handle_measurement_file_compression_finished)
    task.signals.failed.connect(window._handle_measurement_file_compression_failed)
    window._measurement_compression_task = task
    window._thread_pool.start(task)


def _handle_measurement_file_compression_finished(window, result: object) -> None:
    window._measurement_compression_task = None
    if hasattr(window, "_set_measurement_compression_busy_indicator"):
        window._set_measurement_compression_busy_indicator(False)
    if not isinstance(result, MeasurementCompressionResult):
        window._measurement_path = None
        window.status_label.setText("Measurement stopped.")
        window._log_warning("Measurement file compression finished with an unexpected result payload.")
        window._log_success("Measurement recording stopped.")
        window._refresh_plot()
        window._refresh_trace_plot("Peak position (nm)")
        window._request_trace_autoscale()
        window._refresh_session_summary(force=True)
        window._update_window_mode_label()
        return
    window._measurement_path = None
    window.status_label.setText("Measurement stopped.")
    window._log_success("Measurement file compression finished.")
    window._log_success("Measurement recording stopped.")
    window._refresh_plot()
    window._refresh_trace_plot("Peak position (nm)")
    window._request_trace_autoscale()
    window._refresh_session_summary(force=True)
    window._update_window_mode_label()


def _handle_measurement_file_compression_failed(window, message: str) -> None:
    window._measurement_compression_task = None
    if hasattr(window, "_set_measurement_compression_busy_indicator"):
        window._set_measurement_compression_busy_indicator(False)
    window._measurement_path = None
    window.status_label.setText("Measurement stopped.")
    window._log_warning(f"Measurement file compression failed: {message}")
    window._log_success("Measurement recording stopped.")
    window._refresh_plot()
    window._refresh_trace_plot("Peak position (nm)")
    window._request_trace_autoscale()
    window._refresh_session_summary(force=True)
    window._update_window_mode_label()


def _archive_live_sample_if_needed(window, spectrum: Spectrum) -> None:
    if not window._measurement_active or window._measurement_writer is None:
        return
    values = np.asarray(spectrum.values, dtype=np.float64)
    wavelengths = np.asarray(spectrum.wavelengths_nm, dtype=np.float64)
    peak_nm = float("nan")
    if len(values) > 0 and len(wavelengths) > 0:
        finite = np.isfinite(values)
        if np.any(finite):
            safe_values = np.where(finite, values, -np.inf)
            peak_index = int(np.argmax(safe_values))
            if 0 <= peak_index < len(wavelengths):
                peak_nm = float(wavelengths[peak_index])
    elapsed_s = 0.0
    if window._measurement_started_at is not None:
        elapsed_s = (spectrum.acquired_at - window._measurement_started_at).total_seconds()
    try:
        window._measurement_writer.append_batch([spectrum], [elapsed_s], [peak_nm])
    except Exception:
        logging.getLogger("lspr_app.storage").exception("Failed to enqueue measurement frame for storage.")


def request_manual_acquisition(window, kind: str) -> None:
    window._log_info(f"Manual {kind} acquisition requested.")
    if window._live_active:
        current_sample = window._session.state.sample
        if current_sample is None:
            window._log_warning(f"No live sample available to cache as {kind}.")
            window.status_label.setText(f"No live sample available for {kind} capture.")
            return
        if kind == "dark":
            window._session.set_dark(current_sample)
        else:
            window._session.set_reference(current_sample)
        if window._measurement_writer is not None:
            window._measurement_writer.update_baselines(
                window._session.state.dark,
                window._session.state.reference,
            )
        target_plot = "Dark" if kind == "dark" else "Reference"
        if window.plot_selector.currentText() != target_plot:
            window.plot_selector.blockSignals(True)
            window.plot_selector.setCurrentText(target_plot)
            window.plot_selector.blockSignals(False)
        window._refresh_spectrum_plot(window._session.get_plot_data(window.PLOT_MODES[target_plot]), None)
        window._update_dark_reference_button_icons()
        window._request_deferred_ui_refresh(summary=True, telemetry=True)
        window._schedule_acquisition_state_persist()
        window._log_success(f"{kind.capitalize()} spectrum cached from live sample without pausing acquisition.")
        window.status_label.setText(f"{kind.capitalize()} cached from live sample.")
        return
    if window._live_worker is not None and window._live_worker.is_alive():
        window._pending_manual_kind = kind
        window.status_label.setText(f"{kind.capitalize()} acquisition queued while live worker stops...")
        window._log_info(f"Queued {kind} acquisition until live worker stops.")
        return
    if window._busy:
        window._pending_manual_kind = kind
        window.status_label.setText(f"{kind.capitalize()} acquisition queued...")
        window._log_info(f"Queued {kind} acquisition while busy.")
        return
    window.status_label.setText(f"Acquiring {kind} spectrum...")
    window._start_acquisition(kind)


def handle_acquisition_success(window, kind: str, result: AcquisitionResult) -> None:
    if window._closing:
        window._busy = False
        return
    if result.source_epoch != window._source_epoch:
        window._busy = False
        return
    spectrum = result.spectrum
    should_refresh_plot = kind != "sample" or not window._live_active
    now = perf_counter()
    previous_finish = window._raw_last_finish_ts
    window._raw_last_finish_ts = now
    window._last_elapsed_ms = result.elapsed_ms
    window._last_spacing_ms = None if previous_finish is None else (now - previous_finish) * 1000.0
    expected_budget_ms = spectrum.metadata.get("integration_time_ms", 0.0) * spectrum.metadata.get("averages", 1)
    if isinstance(expected_budget_ms, (int, float)):
        window._last_overhead_ms = result.elapsed_ms - float(expected_budget_ms)
    else:
        window._last_overhead_ms = None
    if window._last_spacing_ms and window._last_spacing_ms > 0:
        window._effective_raw_rate_hz = 1000.0 / window._last_spacing_ms

    try:
        if kind == "dark":
            window._session.set_dark(spectrum)
            if window._measurement_writer is not None:
                window._measurement_writer.update_baselines(window._session.state.dark, window._session.state.reference)
        elif kind == "reference":
            window._session.set_reference(spectrum)
            if window._measurement_writer is not None:
                window._measurement_writer.update_baselines(window._session.state.dark, window._session.state.reference)
        elif kind == "sample" and window._live_active:
            should_refresh_plot = False
        elif kind == "sample":
            window._session.set_sample(spectrum)
        else:
            raise ValueError(f"Unsupported acquisition kind: {kind}")
    except MeasurementError as exc:
        window._show_error(str(exc))
        return
    finally:
        window._busy = False
        window._set_measurement_buttons_enabled(True)

    window._request_deferred_ui_refresh(telemetry=True)
    if kind in {"dark", "reference"}:
        target_plot = "Dark" if kind == "dark" else "Reference"
        if window.plot_selector.currentText() != target_plot:
            window.plot_selector.blockSignals(True)
            window.plot_selector.setCurrentText(target_plot)
            window.plot_selector.blockSignals(False)
        selected_plot_mode = window.PLOT_MODES[window.plot_selector.currentText()]
        selected_plot = window._session.get_plot_data(selected_plot_mode)
        if selected_plot is not None:
            window._refresh_spectrum_plot(selected_plot, None)
        window._update_dark_reference_button_icons()
        window._log_debug(f"Plot selector switched to {target_plot} after {kind} acquisition.")
        window._log_success(f"{kind.capitalize()} spectrum acquired in {result.elapsed_ms:.1f} ms.")
    elif kind == "sample" and not window._live_active:
        window._log_success(f"Sample spectrum acquired in {result.elapsed_ms:.1f} ms.")
    elif kind == "sample":
        window._log_throttled(
            "live_sample",
            f"Live sample updated in {result.elapsed_ms:.1f} ms.",
            level=logging.DEBUG,
            min_interval=1.0,
        )

    if window._pending_auto_integration:
        window._pending_auto_integration = False
        QTimer.singleShot(0, window._auto_set_integration_time)
        return

    if window._pending_source_mode is not None:
        pending_mode = window._pending_source_mode
        restart_live = window._resume_live_after_source_switch
        window._pending_source_mode = None
        window._resume_live_after_source_switch = False
        window.source_tabs.blockSignals(True)
        window.source_tabs.setCurrentIndex(0 if pending_mode == "spectrometer" else 1)
        window.source_tabs.blockSignals(False)
        window._apply_source_mode(pending_mode, restart_live=restart_live)
        return

    if window._live_active and kind == "sample":
        pass
    else:
        window.status_label.setText(f"{kind.capitalize()} spectrum acquired successfully.")
        window._log_debug(f"{kind.capitalize()} acquisition completed.")
        if window._pending_manual_kind is not None:
            pending = window._pending_manual_kind
            window._pending_manual_kind = None
            QTimer.singleShot(0, lambda kind=pending: window._start_acquisition(kind))
        elif window._resume_live_after_manual and kind in {"dark", "reference"}:
            window._resume_live_after_manual = False
            QTimer.singleShot(0, window._start_live_acquisition)
    if should_refresh_plot:
        window._refresh_plot()


def flush_live_acquisition_results(window) -> None:
    _flush_live_processing_logs(window)
    latest_event: LiveAcquisitionEvent | None = None
    dropped_events = 0
    while True:
        try:
            event = window._live_result_queue.get_nowait()
        except queue.Empty:
            break
        if latest_event is not None:
            dropped_events += 1
        latest_event = event

    if latest_event is None:
        if window._live_worker is not None and not window._live_worker.is_alive():
            window._live_worker = None
            window._live_result_timer.stop()
            if window._pending_manual_kind is not None:
                pending = window._pending_manual_kind
                window._pending_manual_kind = None
                QTimer.singleShot(0, lambda kind=pending: window._start_acquisition(kind))
        return

    if latest_event.error is not None:
        if latest_event.source_epoch != window._source_epoch:
            return
        window._live_active = False
        window._live_worker = None
        window._live_result_timer.stop()
        window._handle_acquisition_error(latest_event.source_epoch, latest_event.error)
        return

    if latest_event.result is not None:
        if latest_event.source_epoch != window._source_epoch:
            return
        result = latest_event.result
        spectrum = result.spectrum
        _archive_live_sample_if_needed(window, spectrum)
        now = latest_event.produced_at_perf if latest_event.produced_at_perf is not None else perf_counter()
        previous_finish = window._raw_last_finish_ts
        window._raw_last_finish_ts = now
        window._last_elapsed_ms = result.elapsed_ms
        window._last_spacing_ms = None if previous_finish is None else (now - previous_finish) * 1000.0
        expected_budget_ms = spectrum.metadata.get("integration_time_ms", 0.0) * spectrum.metadata.get("averages", 1)
        if isinstance(expected_budget_ms, (int, float)):
            window._last_overhead_ms = result.elapsed_ms - float(expected_budget_ms)
        else:
            window._last_overhead_ms = None
        if window._last_spacing_ms and window._last_spacing_ms > 0:
            window._effective_raw_rate_hz = 1000.0 / window._last_spacing_ms
            window._log_throttled(
                "raw_rate",
                f"Raw source rate {window._effective_raw_rate_hz:.2f} Hz | display_rate={window.live_rate_spin.value():.2f} Hz",
                level=logging.DEBUG,
                min_interval=1.0,
            )
        window._request_deferred_ui_refresh(telemetry=True, live_estimate=True)
        if window._live_active and not window._live_processed_timer.isActive():
            window._live_processed_timer.start(window._live_ui_refresh_delay_ms)

    if dropped_events > 0:
        window._live_display_dropped_frames += dropped_events

    if window._live_worker is not None and not window._live_worker.is_alive():
        window._live_worker = None
        window._live_result_timer.stop()
        if window._pending_manual_kind is not None and not window._live_active and not window._busy:
            pending = window._pending_manual_kind
            window._pending_manual_kind = None
            QTimer.singleShot(0, lambda kind=pending: window._start_acquisition(kind))


def flush_live_processed_results(window) -> None:
    _flush_live_processing_logs(window)
    latest_event: LiveProcessedEvent | None = None
    dropped_events = 0
    while True:
        try:
            event = window._live_processed_queue.get_nowait()
        except queue.Empty:
            break
        if latest_event is not None:
            dropped_events += 1
        latest_event = event

    if latest_event is None:
        if window._live_processing_worker is not None and window._live_processing_worker.is_alive() and window._live_active:
            window._live_processed_timer.start(window._live_ui_refresh_delay_ms)
        return

    if latest_event.error is not None:
        if latest_event.source_epoch != window._source_epoch:
            return
        window._live_active = False
        window._live_processing_worker = None
        window._live_processed_timer.stop()
        window._handle_acquisition_error(latest_event.source_epoch, latest_event.error)
        return

    if latest_event.result is None or latest_event.result.processed is None:
        if window._live_processing_worker is not None and window._live_processing_worker.is_alive() and window._live_active:
            window._live_processed_timer.start(window._live_ui_refresh_delay_ms)
        return

    if latest_event.source_epoch != window._source_epoch:
        return

    result = latest_event.result
    processed = result.processed
    fit = result.fit
    if processed is None:
        return

    if window._source_mode == "simulation":
        display_window_ms = max(window._display_window_ms, window._current_simulation_interval_ms())
    else:
        display_window_ms = window._display_window_ms
    window._session.set_sample(
        processed.with_metadata(
            display_average_count=1,
            display_window_ms=display_window_ms if display_window_ms > 0 else 0.0,
            display_refresh_hz=window.live_rate_spin.value(),
        )
    )
    window._last_display_average_count = 1
    window._last_display_period_ms = display_window_ms
    window._last_processing_ms = result.processing_ms
    window._last_processing_queue_wait_ms = result.queue_wait_ms
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
    window._analysis_cache_result = (
        np.empty(0, dtype=np.float64),
        np.empty(0, dtype=np.float64),
        {},
    )
    window._analysis_metrics_cache_key = None
    window._analysis_metrics_cache_result = {}
    window._update_poly_warning_indicator(fit)
    window._live_plot_update_counter = int(getattr(window, "_live_plot_update_counter", 0)) + 1
    refresh_live_plot = window._live_plot_update_counter % max(int(getattr(window, "_live_plot_refresh_stride", 2)), 1) == 0
    if refresh_live_plot:
        window._ui_refresh_state.plot_render_dirty = True
        if not window._plot_refresh_timer.isActive():
            window._plot_refresh_timer.start()
        window._request_deferred_ui_refresh(trace_plot=True, live_estimate=True, telemetry=True, trace_label="Peak position (nm)")
        window._request_trace_autoscale()
    else:
        window._request_deferred_ui_refresh(live_estimate=True, telemetry=True)
    window._append_processed_trace_history(processed, fit)
    window._append_sensorgram_heatmap_history(processed)
    window._log_throttled(
        "live_display",
        f"Live display updated | dropped={dropped_events}",
        level=logging.DEBUG,
        min_interval=1.0,
    )
    if window._live_processing_worker is not None and window._live_processing_worker.is_alive() and window._live_active:
        window._live_processed_timer.start(window._live_ui_refresh_delay_ms)


def handle_acquisition_error(window, source_epoch: int, message: str) -> None:
    if window._closing:
        window._busy = False
        return
    if source_epoch != window._source_epoch:
        window._busy = False
        return
    window._busy = False
    window._set_measurement_buttons_enabled(True)
    if window._pending_source_mode is not None:
        pending_mode = window._pending_source_mode
        restart_live = window._resume_live_after_source_switch
        window._pending_source_mode = None
        window._resume_live_after_source_switch = False
        window.source_tabs.blockSignals(True)
        window.source_tabs.setCurrentIndex(0 if pending_mode == "spectrometer" else 1)
        window.source_tabs.blockSignals(False)
        window._apply_source_mode(pending_mode, restart_live=restart_live)
        return
    if window._live_active:
        window._stop_live_acquisition(f"Live acquisition stopped: {message}")
        return
    window._pending_manual_kind = None
    window._show_error(message)


def start_live_acquisition(window) -> None:
    if window._busy or window._live_active:
        return
    if window._live_worker is not None and window._live_worker.is_alive():
        return

    window._live_active = True
    window._display_window_ms = 1000.0 / max(window.live_rate_spin.value(), 1e-9)
    window._live_ui_refresh_delay_ms = max(int(round(window._display_window_ms)), 16)
    window._raw_last_finish_ts = None
    window._last_display_average_count = None
    window._last_display_period_ms = None
    window._last_processing_ms = None
    window._last_processing_queue_wait_ms = None
    window._processing_rate_hz = None
    window._processing_headroom_ratio = None
    window._live_plot_update_counter = 0
    window._live_display_dropped_frames = 0
    window._live_display_started_at = perf_counter()
    window._live_trace_started_at = None
    window._last_live_processing_perf = None
    window._peak_history.clear()
    if hasattr(window, "_peak_history_buffers"):
        window._peak_history_buffers.clear()
    window._reset_live_accumulator()
    ctx = mp.get_context("spawn")
    window._live_stop_event = ctx.Event()
    window._live_result_queue = ctx.Queue(maxsize=4)
    window._live_processing_input_queue = ctx.Queue(maxsize=16)
    window._live_processed_queue = ctx.Queue(maxsize=4)
    window._live_processing_log_queue = ctx.Queue(maxsize=32)
    simulation_parameters = None
    if window._source_mode == "simulation":
        simulation_parameters = window._simulation_backend.simulation_parameters()
        window._log_info("Continuous simulation acquisition started.")
    else:
        window._log_info("Continuous spectrometer acquisition started.")
    window._set_measurement_buttons_enabled(False)
    window._set_manual_acquisition_buttons_enabled(True)
    window._request_deferred_ui_refresh(live_estimate=True)
    request = AcquisitionRequest(
        kind="sample",
        settings=window._current_settings(),
        source_epoch=window._source_epoch,
        archive_writer=window._measurement_writer,
        archive_enabled=window._measurement_active,
        measurement_started_at=window._measurement_started_at,
    )
    window._live_worker = LiveAcquisitionWorker(
        request,
        window._live_result_queue,
        window._live_processing_input_queue,
        window._live_stop_event,
        source_mode=window._source_mode,
        simulation_parameters=simulation_parameters,
        debug_mode_enabled=window._processing_debug_mode_enabled,
        log_queue=window._live_processing_log_queue,
    )
    window._live_processing_worker = LiveProcessingWorker(
        window._live_processed_queue,
        window._live_processing_input_queue,
        window._live_stop_event,
        window._current_processing_settings(),
        debug_mode_enabled=window._processing_debug_mode_enabled,
        log_queue=window._live_processing_log_queue,
    )
    if window._source_mode == "simulation":
        window._live_worker.update_cycle_period(1.0 / max(window.sim_output_rate_spin.value(), 1e-9))
    window._live_worker.start()
    window._live_processing_worker.start()
    window._live_result_timer.start()
    window._live_processed_timer.start(0)
    window._update_window_mode_label()


def stop_live_acquisition(window, message: str = "Live acquisition stopped.") -> None:
    window._live_active = False
    window._live_stop_event.set()
    window._live_result_timer.stop()
    window._live_processed_timer.stop()
    window._live_display_started_at = None
    window._live_trace_started_at = None
    window._trace_display_cursor_s = 0.0
    window._last_live_processing_perf = None
    window._last_processing_ms = None
    window._last_processing_queue_wait_ms = None
    window._processing_rate_hz = None
    window._processing_headroom_ratio = None
    window._live_plot_update_counter = 0
    window._reset_live_accumulator()
    window._set_measurement_buttons_enabled(True)
    window._set_manual_acquisition_buttons_enabled(True)
    window.status_label.setText(message)
    _flush_live_processing_logs(window)
    window._request_deferred_ui_refresh(telemetry=True)
    window._log_info(message)
    window._update_window_mode_label()
    if window._live_worker is not None and not window._live_worker.is_alive():
        window._live_worker = None
    if window._live_worker is not None:
        try:
            window._live_worker.stop()
            window._live_worker.join(timeout=2.0)
            if window._live_worker.is_alive():
                window._log_warning("Live acquisition worker did not exit cleanly; terminating it.")
                window._live_worker.terminate()
                window._live_worker.join(timeout=1.0)
        except Exception as exc:
            window._log_warning(f"Live acquisition worker shutdown failed: {exc}")
        window._live_worker = None
    if window._live_processing_worker is not None:
        try:
            window._live_processing_worker.join(timeout=0.25)
        except Exception:
            pass
        if not window._live_processing_worker.is_alive():
            window._live_processing_worker = None
    _flush_live_processing_logs(window)


def start_acquisition(window, kind: str) -> None:
    if window._closing:
        return
    if window._busy:
        return
    if window._live_worker is not None and window._live_worker.is_alive():
        return

    if window._source_mode == "simulation":
        if kind != "sample":
            spectrum = window._build_simulation_spectrum(kind)
            window._log_info(f"Generated simulation {kind} spectrum.")
            result = AcquisitionResult(
                spectrum=spectrum,
                elapsed_ms=0.0,
                settings=window._current_settings(),
                source_epoch=window._source_epoch,
            )
            handle_acquisition_success(window, kind, result)
            window._refresh_plot()
            return
        window._live_active = True
        set_measurement_buttons_enabled(window, False)
        window._log_info("Continuous simulation started.")
        window._request_deferred_ui_refresh(live_estimate=True)
        return

    window._busy = True
    if not (window._live_active and kind == "sample"):
        set_measurement_buttons_enabled(window, False)
    window.status_label.setText(f"Acquiring {kind} spectrum...")
    window._log_info(f"Acquiring {kind} spectrum.")

    settings = window._current_settings()
    task = AcquisitionTask(
        window._spectrometer,
        AcquisitionRequest(
            kind=kind,
            settings=settings,
            source_epoch=window._source_epoch,
            archive_writer=window._measurement_writer,
            archive_enabled=window._measurement_active and kind == "sample",
            measurement_started_at=window._measurement_started_at,
        ),
    )
    task.signals.finished.connect(window._handle_acquisition_success)
    task.signals.failed.connect(window._handle_acquisition_error)
    window._thread_pool.start(task)


def set_measurement_buttons_enabled(window, enabled: bool) -> None:
    window.acquire_dark_button.setEnabled(enabled)
    window.acquire_reference_button.setEnabled(enabled)
    window.auto_integration_button.setEnabled(enabled and window._source_mode == "spectrometer")
    window.measurement_toggle_button.setEnabled(enabled)
    window.stop_measurement_button.setEnabled(window._measurement_active)
    window.next_measurement_button.setEnabled(enabled)
    update_measurement_toggle_button(window)


def set_manual_acquisition_buttons_enabled(window, enabled: bool) -> None:
    window.acquire_dark_button.setEnabled(enabled)
    window.acquire_reference_button.setEnabled(enabled)


def set_measurement_ui_locked(window, locked: bool) -> None:
    window.sim_resolution_spin.setEnabled(not locked)
    window.sim_output_rate_spin.setEnabled(not locked and window._source_mode == "simulation")
    if hasattr(window, "_set_recording_context_controls_enabled"):
        window._set_recording_context_controls_enabled(not locked)
    if locked:
        window.source_tabs.setToolTip("Switch tabs without changing the active feed.")
        window.sim_resolution_spin.setToolTip(
            "Locked while measurement is running so the recording wavelength axis stays fixed."
        )
        window.sim_output_rate_spin.setToolTip(
            "Locked while measurement is running so simulation pacing stays consistent."
        )
    else:
        window.source_tabs.setToolTip("Switch tabs without changing the active feed.")
        window.sim_resolution_spin.setToolTip("Resolution of the simulation wavelength axis.")
        window.sim_output_rate_spin.setToolTip("Simulation frame production rate.")
    update_source_link_buttons(window)


def update_measurement_toggle_button(window) -> None:
    if window._measurement_active:
        icon = transport_icon(window._theme_mode, "stop")
        tooltip = "Stop measurement"
    else:
        icon = transport_icon(window._theme_mode, "play")
        tooltip = "Start measurement"
    window.measurement_toggle_button.setIcon(icon)
    window.measurement_toggle_button.setToolTip(tooltip)
    trace_button = getattr(window, "trace_record_button", None)
    if trace_button is not None:
        if window._measurement_active:
            record_color = QColor("#d84d4d" if window._theme_mode == "dark" else "#a11d1d")
            record_color.setAlpha(255 if bool(getattr(window, "_recording_blink_visible", True)) else 90)
            trace_icon = tint_tabler_icon(flow_tabler_icon("player_record"), record_color)
        else:
            trace_icon = transport_icon(window._theme_mode, "record")
        trace_tooltip = "Stop sensorgram recording" if window._measurement_active else "Start sensorgram recording"
        trace_button.setIcon(trace_icon)
        trace_button.setToolTip(trace_tooltip)
    experiment_control_window = getattr(window, "_experiment_control_window", None)
    if experiment_control_window is not None and hasattr(experiment_control_window, "_set_record_with_flow_recording_active"):
        experiment_control_window._set_record_with_flow_recording_active(bool(window._measurement_active))


def toggle_measurement_run(window) -> None:
    if window._measurement_active:
        stop_measurement_run(window)
        return
    start_measurement_run(window)


def start_measurement_run(window) -> None:
    if window._measurement_active:
        return

    signal_mode = window.plot_selector.currentText().lower()
    if signal_mode not in {"sample", "absorbance"}:
        signal_mode = "sample"

    anchor = window._session.get_plot_data(signal_mode)
    if anchor is None:
        anchor = window._session.state.sample or window._session.state.absorbance
    if anchor is None:
        window.status_label.setText("Measurement needs at least one live spectrum before starting.")
        window._log_warning("Measurement start blocked: no live spectrum available yet.")
        return

    project_destination = ""
    if hasattr(window, "recording_project_destination"):
        project_destination = str(window.recording_project_destination() or "").strip()
    if not project_destination:
        experiment_control_window = getattr(window, "_experiment_control_window", None)
        if experiment_control_window is not None and hasattr(experiment_control_window, "recording_project_destination"):
            project_destination = str(experiment_control_window.recording_project_destination() or "").strip()
    experiment_name = ""
    if hasattr(window, "recording_experiment_name"):
        experiment_name = str(window.recording_experiment_name() or "").strip()
    if not experiment_name:
        experiment_control_window = getattr(window, "_experiment_control_window", None)
        if experiment_control_window is not None and hasattr(experiment_control_window, "recording_experiment_name"):
            experiment_name = str(experiment_control_window.recording_experiment_name() or "").strip()
    if not experiment_name:
        experiment_name = str(getattr(window, "_measurement_experiment_name", "") or "").strip()
    if not experiment_name:
        experiment_name, accepted = QInputDialog.getText(
            window,
            "Experiment name",
            "Experiment name:",
            text=datetime.now().strftime("experiment_%Y%m%d"),
        )
        if not accepted:
            window.status_label.setText("Measurement start cancelled.")
            window._log_info("Measurement start cancelled.")
            return
    experiment_name = str(experiment_name).strip() or "experiment"
    file_experiment_name = _sanitize_experiment_name(experiment_name)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_name = f"{timestamp}_{file_experiment_name}.h5"
    if project_destination:
        destination = Path(project_destination) / _safe_folder_name(experiment_name) / default_name
    else:
        default_path = Path.cwd() / "data" / _safe_folder_name(experiment_name) / default_name
        file_path, _ = QFileDialog.getSaveFileName(
            window,
            "Start measurement",
            str(default_path),
            "HDF5 files (*.h5)",
        )
        if not file_path:
            window.status_label.setText("Measurement start cancelled.")
            window._log_info("Measurement start cancelled.")
            return
        destination = Path(file_path)
        if hasattr(window, "project_destination_edit") and not str(window.recording_project_destination() or "").strip():
            window.project_destination_edit.setText(str(destination.parent))
            if hasattr(window, "_remember_recording_project_destination"):
                window._remember_recording_project_destination()
        if hasattr(window, "experiment_name_edit") and not str(window.recording_experiment_name() or "").strip():
            window.experiment_name_edit.setText(experiment_name)
            if hasattr(window, "_remember_recording_experiment_name"):
                window._remember_recording_experiment_name()
    original_destination = destination
    destination = _reserve_unique_measurement_destination(destination)
    if destination != original_destination:
        window._log_warning(
            f"Measurement file already existed; saved as {destination.name} instead of {original_destination.name}."
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(timezone.utc)
    # Recording follows the execution state, but it is not stopped by flow HOLD.
    window._measurement_axis_lock = np.asarray(anchor.wavelengths_nm, dtype=np.float64).copy()
    window._measurement_writer = AsyncHDF5MeasurementWriter(
        destination,
        signal_mode,
        window._measurement_axis_lock,
        window._current_processing_settings(),
        experiment_name=experiment_name,
        started_at_utc=started_at,
        flush_interval_s=window._measurement_flush_interval_s,
    )
    window._measurement_writer.update_acquisition_state(window._acquisition_state_payload())
    window._measurement_writer.update_baselines(window._session.state.dark, window._session.state.reference)
    window._measurement_active = True
    window._measurement_paused = False
    window._measurement_signal_mode = signal_mode
    window._measurement_path = destination
    window._measurement_started_at = started_at
    window._measurement_experiment_name = experiment_name
    window._session_stats_log.clear()
    window._session_stats_log_last_text = ""
    window._session_stats_log_last_capture_ts = 0.0
    if window._live_worker is not None and window._live_worker.is_alive():
        window._live_worker.update_archive_context(window._measurement_writer, True, window._measurement_started_at)
    set_measurement_ui_locked(window, True)
    if hasattr(window, "_set_recording_blink_indicator"):
        window._set_recording_blink_indicator(True)
    if hasattr(window, "_sensorgram_view_mode"):
        window._sensorgram_view_mode = "absolute"
    if hasattr(window, "_apply_sensorgram_view_mode"):
        window._apply_sensorgram_view_mode(save=False)
    if hasattr(window, "_apply_sensorgram_content_mode"):
        window._apply_sensorgram_content_mode(save=False)
    window._peak_history.clear()
    if hasattr(window, "_peak_history_buffers"):
        window._peak_history_buffers.clear()
    if hasattr(window, "_sensorgram_heatmap_history"):
        window._sensorgram_heatmap_history.clear()
    if hasattr(window, "_sensorgram_heatmap_wavelengths"):
        window._sensorgram_heatmap_wavelengths = None
    window._trace_display_cursor_s = 0.0
    window._live_trace_started_at = None
    window._peak_reference_processed = None
    window._refresh_session_statistics(force=True)
    window._refresh_trace_plot("Peak position (nm)")
    window._request_trace_autoscale()
    set_measurement_buttons_enabled(window, True)
    window.status_label.setText(f"Recording to {destination.name}")
    window._log_success(f"Measurement recording started: {destination.name}.")
    window._update_window_mode_label()

    if not window._live_active and not window._busy:
        window._start_live_acquisition()


def pause_measurement_run(window) -> None:
    if not window._measurement_active:
        return
    if window._live_worker is not None and window._live_worker.is_alive():
        window._live_worker.update_archive_context(window._measurement_writer, True, window._measurement_started_at)
    update_measurement_toggle_button(window)
    window._update_window_mode_label()


def stop_measurement_run(window) -> None:
    if not window._measurement_active:
        return
    # STOP is the only state that finalizes the recording file.
    started_at = window._measurement_started_at
    measurement_path = window._measurement_path
    flush_measurement_frames(window, force=True)
    if window._live_worker is not None and window._live_worker.is_alive():
        window._live_worker.update_archive_context(None, False, None)
    if window._measurement_writer is not None:
        window._measurement_writer.close()
    window._measurement_writer = None
    window._measurement_active = False
    window._measurement_paused = False
    window._measurement_started_at = None
    window._measurement_axis_lock = None
    set_measurement_ui_locked(window, False)
    if hasattr(window, "_set_recording_blink_indicator"):
        window._set_recording_blink_indicator(False)
    window._sync_simulation_backend_from_controls()
    if started_at is not None and window._peak_history:
        converted_history: dict[str, list[tuple[float, float]]] = {}
        for metric_name, series in window._peak_history.items():
            converted_series: list[tuple[float, float]] = []
            for elapsed_s, value in series:
                local_timestamp = (started_at + timedelta(seconds=float(elapsed_s))).astimezone().timestamp()
                converted_series.append((float(local_timestamp), float(value)))
            if converted_series:
                converted_history[metric_name] = converted_series
        window._peak_history = converted_history
        peak_history_buffers = getattr(window, "_peak_history_buffers", None)
        if peak_history_buffers:
            converted_buffers: dict[str, TraceHistoryBuffer] = {}
            max_points = int(getattr(window, "_trace_history_max_points", 6000))
            for metric_name, buffer in peak_history_buffers.items():
                times, values = buffer.to_arrays()
                converted_buffer = TraceHistoryBuffer(max_points)
                for elapsed_s, value in zip(times.tolist(), values.tolist(), strict=False):
                    local_timestamp = (started_at + timedelta(seconds=float(elapsed_s))).astimezone().timestamp()
                    converted_buffer.append(float(local_timestamp), float(value))
                if len(converted_buffer) > 0:
                    converted_buffers[metric_name] = converted_buffer
            window._peak_history_buffers = converted_buffers
    if started_at is not None and getattr(window, "_sensorgram_heatmap_history", None):
        converted_heatmap_history: list[tuple[float, np.ndarray]] = []
        for elapsed_s, values in window._sensorgram_heatmap_history:
            local_timestamp = (started_at + timedelta(seconds=float(elapsed_s))).astimezone().timestamp()
            converted_heatmap_history.append((float(local_timestamp), np.asarray(values, dtype=np.float64).copy()))
        window._sensorgram_heatmap_history = converted_heatmap_history
    window._trace_view_locked = False
    window._trace_display_cursor_s = 0.0
    window._live_trace_started_at = None
    window._peak_reference_processed = None
    window._refresh_session_statistics(force=True)
    window._refresh_trace_plot("Peak position (nm)")
    window._request_trace_autoscale()
    set_measurement_buttons_enabled(window, True)
    if measurement_path is not None and bool(getattr(window, "measurement_hdf5_compression_enabled", lambda: True)()):
        window.status_label.setText("Measurement stopped. Compressing file...")
        window._log_success("Measurement recording stopped. Compressing file...")
        if hasattr(window, "_start_measurement_file_compression"):
            window._start_measurement_file_compression(measurement_path)
    else:
        window._measurement_path = None
        window.status_label.setText("Measurement stopped.")
        window._log_success("Measurement recording stopped.")
        window._refresh_plot()
        window._refresh_session_summary(force=True)
        window._update_window_mode_label()


def append_processed_trace_history(window, processed: Spectrum, fit: Spectrum | None) -> None:
    if not window._live_active:
        return

    measurement_active = bool(window._measurement_active)
    if measurement_active:
        started_at = window._measurement_started_at
        if started_at is not None:
            elapsed_s = max((processed.acquired_at - started_at).total_seconds(), 0.0)
        else:
            elapsed_s = 0.0
    else:
        elapsed_s = float(processed.acquired_at.timestamp())

    display_step_s = max(1.0 / max(window.live_rate_spin.value(), 1e-9), 1e-3)
    metrics = window._get_analysis_metrics(processed, fit)
    updated = False
    for metric_name in window.TRACE_METRIC_LABELS:
        value = metrics.get(metric_name)
        if not isinstance(value, (int, float)) or not np.isfinite(float(value)):
            continue
        history = window._peak_history.setdefault(metric_name, [])
        history.append((elapsed_s, float(value)))
        trim_history_tail_in_place(history, int(getattr(window, "_trace_history_max_points", 6000)))
        window._peak_history[metric_name] = history
        peak_history_buffers = getattr(window, "_peak_history_buffers", None)
        if peak_history_buffers is None:
            peak_history_buffers = {}
            window._peak_history_buffers = peak_history_buffers
        buffer = peak_history_buffers.get(metric_name)
        max_points = int(getattr(window, "_trace_history_max_points", 6000))
        if buffer is None or buffer.capacity != max_points:
            buffer = TraceHistoryBuffer(max_points)
            for history_elapsed_s, history_value in history:
                buffer.append(history_elapsed_s, history_value)
            peak_history_buffers[metric_name] = buffer
        else:
            buffer.append(elapsed_s, float(value))
        updated = True

    window._trace_display_cursor_s = elapsed_s + display_step_s

    if updated:
        if window._measurement_writer is not None and measurement_active:
            # Store the derived metric with its absolute UTC timestamp so it can be joined later without ambiguity.
            acquired_at_unix_ms = int(round(processed.acquired_at.astimezone(timezone.utc).timestamp() * 1000.0))
            metric_row = {
                "acquired_at_unix_ms": acquired_at_unix_ms,
                "t_ms": int(round(elapsed_s * 1000.0)),
                "sample_index": -1,
                "centroid_nm": metrics.get("centroid", np.nan),
                "smoothed_max_nm": metrics.get("smoothed_max", np.nan),
                "poly_max_nm": metrics.get("poly_max", np.nan),
                "gaussian_center_nm": metrics.get("gaussian_center", np.nan),
                "fwhm_nm": fit.metadata.get("fwhm_nm", np.nan) if fit is not None else processed.metadata.get("fwhm_nm", np.nan),
                "mse": fit.metadata.get("mse", np.nan) if fit is not None else np.nan,
                "snr": metrics.get("snr", np.nan),
            }
            window._measurement_writer.append_metrics([metric_row])
        window._request_deferred_ui_refresh(trace_plot=True, stats=True, trace_label="Peak position (nm)")
        window._request_trace_autoscale()
        window._log_throttled(
            "trace_append",
            (
                "Trace point appended | points="
                f"{len(next(iter(getattr(window, '_peak_history_buffers', {}).values()), []))} | "
                f"rate={window.live_rate_spin.value():.2f} Hz"
            ),
            level=logging.DEBUG,
            min_interval=1.0,
        )


def flush_measurement_frames(window, force: bool = False) -> None:
    if window._measurement_writer is None:
        return
    window._measurement_writer.flush()
    if window._measurement_path is not None:
        window.status_label.setText(f"Recording to {window._measurement_path.name}")


def update_window_mode_label(window) -> None:
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


def _sanitize_experiment_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value).strip())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned or "experiment"


def _safe_folder_name(value: str) -> str:
    cleaned = "".join("_" if ch in '<>:"/\\|?*' else ch for ch in str(value).strip())
    cleaned = cleaned.rstrip(" .")
    return cleaned or "experiment"


def _reserve_unique_measurement_destination(destination: Path) -> Path:
    destination = Path(destination).expanduser()
    if destination.suffix.lower() != ".h5":
        destination = destination.with_suffix(".h5")
    if not destination.exists():
        return destination
    stem = destination.stem
    suffix = destination.suffix
    for index in range(1, 1000):
        candidate = destination.with_name(f"{stem}_{index:02d}{suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Unable to reserve a unique measurement file name under {destination.parent}")

