from __future__ import annotations

import multiprocessing as mp
import queue
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any

import numpy as np
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from lspr_app.app import create_spectrometer
from lspr_app.device.communication_models import device_inventory_rows
from lspr_app.domain.models import Spectrum
from lspr_core import DEFAULT_LAUNCH_PROFILE, launch_profile_spec
from lspr_app.domain.session import MeasurementError
from lspr_app.device.simulated import SimulatedSpectrometer, SimulationParameters
from lspr_app.gui.icon_helpers import flow_tabler_icon, math_function_tab_icon, prism_tab_icon, tint_tabler_icon, transport_icon
from lspr_app.gui.experiment_control_runtime import (
    experiment_runtime_label,
    experiment_runtime_state_name,
)
from lspr_app.gui.main_window_headers import update_source_link_buttons
from lspr_app.gui.main_window_plotting import record_plot_refresh_rate_sample
from lspr_app.gui.main_window_state import apply_source_mode_for
from lspr_app.gui.runtime_diagnostics import format_rate_for_window
from lspr_app.gui.sensorgram_secondary_axis import (
    SECONDARY_AXIS_SLOTS,
    normalize_secondary_axis_mode,
    secondary_axis_metric_for,
    secondary_axis_slot_active,
)
from lspr_app.gui.sensorgram_time_anchor import (
    display_time_anchor,
    measurement_to_session_offset_s,
    session_time_anchor,
)
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
    MeasurementWriterErrorSignals,
)
from lspr_app.storage.app_config import save_dark_reference_cache
from lspr_app.storage.hdf5_export import AsyncHDF5MeasurementWriter
from lspr_app.storage.measurement_archive import close_temp_measurement_writer


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


def _queue_size_safe(queue_obj) -> int | None:
    try:
        size = queue_obj.qsize()
    except (AttributeError, NotImplementedError, OSError):
        return None
    try:
        return max(int(size), 0)
    except (TypeError, ValueError):
        return None


def _log_live_timing(window, key: str, message: str) -> None:
    if not bool(getattr(window, "_deep_timing_enabled", False)):
        return
    window._log_throttled(key, message, level=logging.INFO, min_interval=0.5)


def _request_live_acquisition_poll_for(window, delay_ms: float) -> None:
    request = getattr(window, "_request_live_acquisition_poll", None)
    if callable(request):
        request(delay_ms)


def _update_live_source_rate_from_event(window, event: LiveAcquisitionEvent, now: float) -> None:
    previous_finish = getattr(window, "_raw_last_finish_ts", None)
    previous_sample_index = getattr(window, "_raw_last_sample_index", None)
    current_sample_index = getattr(event, "source_sample_index", None)
    window._raw_last_finish_ts = now
    if (
        previous_finish is not None
        and previous_sample_index is not None
        and current_sample_index is not None
    ):
        try:
            delta_samples = int(current_sample_index) - int(previous_sample_index)
        except (TypeError, ValueError):
            delta_samples = 0
        if delta_samples > 0:
            spacing_ms = max((now - previous_finish) * 1000.0 / float(delta_samples), 0.0)
            window._last_spacing_ms = spacing_ms
            if spacing_ms > 0:
                window._effective_raw_rate_hz = 1000.0 / spacing_ms
            window._raw_last_sample_index = int(current_sample_index)
            return
    window._last_spacing_ms = None if previous_finish is None else (now - previous_finish) * 1000.0
    if window._last_spacing_ms and window._last_spacing_ms > 0:
        window._effective_raw_rate_hz = 1000.0 / window._last_spacing_ms
    if current_sample_index is not None:
        try:
            window._raw_last_sample_index = int(current_sample_index)
        except (TypeError, ValueError):
            pass


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
    if getattr(window, "_closing", False):
        return
    if not isinstance(result, MeasurementCompressionResult):
        window._measurement_path = None
        window.status_label.setText("Measurement stopped.")
        window._log_warning("Measurement file compression finished with an unexpected result payload.")
        window._refresh_plot()
        window._refresh_trace_plot("Metric position (nm)")
        window._request_trace_autoscale()
        window._refresh_session_summary(force=True)
        window._update_window_mode_label()
        return
    window._measurement_path = None
    window.status_label.setText("Measurement stopped.")
    window._log_success("Measurement file compression finished.")
    window._refresh_plot()
    window._refresh_trace_plot("Metric position (nm)")
    window._request_trace_autoscale()
    window._refresh_session_summary(force=True)
    window._update_window_mode_label()


def _handle_measurement_file_compression_failed(window, message: str) -> None:
    window._measurement_compression_task = None
    if hasattr(window, "_set_measurement_compression_busy_indicator"):
        window._set_measurement_compression_busy_indicator(False)
    if getattr(window, "_closing", False):
        return
    window._measurement_path = None
    window.status_label.setText("Measurement stopped.")
    window._log_warning(f"Measurement file compression failed: {message}")
    window._refresh_plot()
    window._refresh_trace_plot("Metric position (nm)")
    window._request_trace_autoscale()
    window._refresh_session_summary(force=True)
    window._update_window_mode_label()


def _handle_measurement_writer_failed(window, message: str) -> None:
    """The background HDF5 writer thread died mid-recording (e.g. disk full,
    permission error). Recording has already silently stopped on the writer
    side by this point - this just makes that visible instead of leaving the
    UI showing an active recording that isn't actually being written anymore.
    """
    if not window._measurement_active:
        return
    if window._live_worker is not None and window._live_worker.is_alive():
        window._live_worker.update_archive_context(None, False, None)
    # The writer thread is already gone (that's why we're here) - don't call
    # .close() on it again, just drop the reference.
    window._measurement_writer = None
    window._measurement_writer_error_signals = None
    window._measurement_active = False
    window._measurement_paused = False
    window._measurement_started_at = None
    window._measurement_axis_lock = None
    window._measurement_path = None
    set_measurement_ui_locked(window, False)
    if hasattr(window, "_set_recording_blink_indicator"):
        window._set_recording_blink_indicator(False)
    window.status_label.setText("Measurement recording stopped: a write error occurred.")
    window._log_error(f"Measurement recording stopped unexpectedly: {message}")
    set_measurement_buttons_enabled(window, True)
    window._refresh_session_summary(force=True)
    window._update_window_mode_label()


def _peak_nm_from_spectrum(spectrum: Spectrum) -> float:
    values = np.asarray(spectrum.values, dtype=np.float64)
    wavelengths = np.asarray(spectrum.wavelengths_nm, dtype=np.float64)
    if len(values) > 0 and len(wavelengths) > 0:
        finite = np.isfinite(values)
        if np.any(finite):
            safe_values = np.where(finite, values, -np.inf)
            peak_index = int(np.argmax(safe_values))
            if 0 <= peak_index < len(wavelengths):
                return float(wavelengths[peak_index])
    return float("nan")


def _archive_to_session_writer_if_available(window, spectrum: Spectrum) -> None:
    """Write a raw sample spectrum to the always-on session file."""
    from lspr_app.storage.measurement_archive import ensure_session_writer
    writer = ensure_session_writer(window, spectrum)
    if writer is None:
        return
    # Skip if the session writer IS the measurement writer (only during recording when
    # they're not separate objects — they're always separate objects here, but be safe).
    if writer is window._measurement_writer:
        return
    # Always session-relative regardless of whether a measurement happens to
    # be recording right now - see gui/sensorgram_time_anchor.py and
    # docs/sensorgram_improvements.md (C6).
    session_started_at = session_time_anchor(window)
    if session_started_at is not None:
        elapsed_s = max((spectrum.acquired_at - session_started_at).total_seconds(), 0.0)
    else:
        elapsed_s = 0.0
    try:
        writer.append_batch([spectrum], [elapsed_s], [_peak_nm_from_spectrum(spectrum)])
    except Exception:
        pass


def _archive_live_sample_if_needed(window, spectrum: Spectrum) -> bool:
    _archive_to_session_writer_if_available(window, spectrum)
    if window._measurement_writer is None:
        return False
    elapsed_s = 0.0
    if window._measurement_started_at is not None:
        elapsed_s = (spectrum.acquired_at - window._measurement_started_at).total_seconds()
    try:
        window._measurement_writer.append_batch([spectrum], [elapsed_s], [_peak_nm_from_spectrum(spectrum)])
    except Exception:
        logging.getLogger("lspr_app.storage").exception("Failed to enqueue measurement frame for storage.")
        return False
    return True


def poll_environment_sensors(window) -> None:
    """Periodic tick (see MainWindow._environment_poll_timer): dispatch a
    background read of the Switch device's ambient temperature/humidity
    sensor, if one is connected. Only ArduinoValveController actually
    supports this - see docs/hardware/arduino_valve_controller_protocol.md.
    Cheap no-op (no device I/O at all) when nothing is connected, via the
    same non-blocking cached connection check used for the titlebar status
    strip.
    """
    from lspr_app.device.device_lifecycle import DeviceLifecycleController
    from lspr_app.device.device_types import SWITCH
    from lspr_app.gui.device_lifecycle_task import DeviceEnvironmentReadTask, device_io_pool

    if not DeviceLifecycleController.shared().is_connected_cached(SWITCH):
        return
    task = DeviceEnvironmentReadTask()
    task.signals.finished.connect(
        lambda temperature_c, humidity_percent, w=window: handle_environment_reading(w, temperature_c, humidity_percent)
    )
    device_io_pool().start(task)


def handle_environment_reading(window, temperature_c: float | None, humidity_percent: float | None) -> None:
    """Record one temperature/humidity reading to whichever HDF5 writer(s)
    are currently active - the always-on session archive and, if a named
    recording is in progress, the measurement file too. Mirrors the "write
    to both if present" pattern _archive_to_session_writer_if_available /
    _archive_live_sample_if_needed already use for raw spectra.
    """
    if temperature_c is None and humidity_percent is None:
        return  # nothing readable - e.g. a connected ItsyBitsy/Legacy controller
    from lspr_app.gui.main_window_titlebar import update_environment_status_strip
    from lspr_app.storage.measurement_archive import ensure_session_writer

    if temperature_c is not None:
        window._last_temperature_c = float(temperature_c)
    if humidity_percent is not None:
        window._last_humidity_percent = float(humidity_percent)
    update_environment_status_strip(window)

    timestamp_utc_ms = int(round(datetime.now(timezone.utc).timestamp() * 1000.0))
    storage_logger = logging.getLogger("lspr_app.storage")

    session_writer = ensure_session_writer(window)  # no spectrum arg: fetch only, never create
    if session_writer is not None:
        try:
            session_writer.append_environment_reading(timestamp_utc_ms, temperature_c, humidity_percent)
        except Exception:
            storage_logger.exception("Failed to record environment reading to session file.")

    measurement_writer = window._measurement_writer
    if measurement_writer is not None and measurement_writer is not session_writer:
        try:
            measurement_writer.append_environment_reading(timestamp_utc_ms, temperature_c, humidity_percent)
        except Exception:
            storage_logger.exception("Failed to record environment reading to measurement file.")

    # Sensorgram secondary axes (see gui/sensorgram_secondary_axis.py).
    # Unlike FWHM/extinction below, environment readings arrive at a slow
    # poll-timer cadence (not once per spectrum), so there's no meaningful
    # "wasted work" cost to maintaining both temperature's and humidity's
    # cache whenever a reading comes in, regardless of which slot(s) are
    # currently active or which metric they're showing - keeps switching
    # instant, with no reseed lag, at negligible extra cost.
    if normalize_secondary_axis_mode(getattr(window, "_secondary_axis_mode", "xy")) != "xy":
        reading_at = datetime.fromtimestamp(timestamp_utc_ms / 1000.0, tz=timezone.utc)
        started_at = display_time_anchor(window)
        if started_at is not None:
            elapsed_s = max((reading_at - started_at).total_seconds(), 0.0)
            plot_view_cache = getattr(window, "_plot_view_cache", None)
            if plot_view_cache is not None and hasattr(plot_view_cache, "append_live_absolute_metric_point"):
                target_points = max(int(getattr(window, "_plot_display_points", 512)), 1)
                recent_tail_points = max(int(getattr(window, "_sensorgram_compression_recent_tail_points", 300)), 0)
                for metric_name, value in (("temperature", temperature_c), ("humidity", humidity_percent)):
                    if not isinstance(value, (int, float)) or not np.isfinite(float(value)):
                        continue
                    try:
                        plot_view_cache.append_live_absolute_metric_point(
                            metric_name,
                            float(elapsed_s),
                            float(value),
                            target_points=target_points,
                            recent_tail_points=recent_tail_points,
                        )
                    except Exception:
                        pass
            # Route through the same coalesced/throttled refresh path the 1Y
            # metrics use (window._ui_task_scheduler, "earliest"-coalesced),
            # rather than redrawing synchronously here - see the FWHM/
            # extinction block below for why a direct, uncoalesced call is a
            # real performance regression at high rates. Environment readings
            # are low-rate already, but there's no reason to special-case it.
            if hasattr(window, "_request_deferred_ui_refresh"):
                window._request_deferred_ui_refresh(trace_plot=True)


def flush_live_recording_results(window) -> None:
    started = perf_counter()
    queue_obj = getattr(window, "_live_recording_queue", None)
    if queue_obj is None:
        return
    drained_events: list[LiveAcquisitionEvent] = []
    while True:
        try:
            event = queue_obj.get_nowait()
        except queue.Empty:
            break
        drained_events.append(event)

    live_recording_depth = _queue_size_safe(queue_obj)
    if live_recording_depth is not None:
        window._live_recording_queue_max_depth = max(
            int(getattr(window, "_live_recording_queue_max_depth", 0)),
            live_recording_depth,
        )

    if not drained_events:
        window._last_live_recording_flush_ms = (perf_counter() - started) * 1000.0
        return

    window._last_live_recording_flush_ms = (perf_counter() - started) * 1000.0
    valid_events = [
        event
        for event in drained_events
        if event.error is None
        and event.result is not None
        and event.source_epoch == window._source_epoch
    ]
    measurement_started_at = getattr(window, "_measurement_started_at", None)
    if measurement_started_at is not None:
        valid_events = [
            event
            for event in valid_events
            if event.result is not None and event.result.spectrum.acquired_at >= measurement_started_at
        ]

    window._raw_acquired_count = int(getattr(window, "_raw_acquired_count", 0)) + len(valid_events)
    window._raw_recording_enqueued_count = int(getattr(window, "_raw_recording_enqueued_count", 0)) + len(valid_events)

    for event in valid_events:
        if _archive_live_sample_if_needed(window, event.result.spectrum):
            window._raw_recording_written_count = int(getattr(window, "_raw_recording_written_count", 0)) + 1


def _suspend_spectrometer_backend_for_live(window) -> None:
    if getattr(window, "_source_mode", "spectrometer") != "spectrometer":
        return
    if getattr(window, "_hardware_backend_suspended_for_live", False):
        return
    spectrometer = getattr(window, "_spectrometer", None)
    close = getattr(spectrometer, "close", None)
    if callable(close):
        try:
            close()
            window._hardware_backend_suspended_for_live = True
            window._log_info("Spectrometer backend released for live acquisition.")
        except Exception as exc:
            window._log_warning(f"Could not release spectrometer backend for live acquisition: {exc}")


def _restore_spectrometer_backend_after_live(window) -> None:
    if not getattr(window, "_hardware_backend_suspended_for_live", False):
        return
    try:
        spectrometer = create_spectrometer(force_simulator=bool(getattr(window._launch_profile_spec, "force_simulator", False)))
    except Exception as exc:
        window._log_warning(f"Could not restore spectrometer backend after live acquisition: {exc}")
        return
    window._spectrometer = spectrometer
    window._capabilities = spectrometer.capabilities()
    window._hardware_available = not isinstance(spectrometer, SimulatedSpectrometer)
    window._hardware_backend_suspended_for_live = False
    window._log_info("Spectrometer backend restored after live acquisition.")


_PLOT_MODE_REVERT_DELAY_MS = 1000


def _schedule_plot_mode_revert_to_raw(window, shown_plot: str) -> None:
    """After jumping the spectrum plot to Dark/Reference right after
    acquiring it (so the user can glance at what was just captured), snap
    back to Raw a moment later instead of leaving the plot parked on a
    static view - per the maintainer's request. Only reverts if the plot is
    still showing exactly the view this call just switched to; if the user
    (or another dark/reference capture) has since changed it, this is a
    no-op.
    """
    def _revert() -> None:
        if window.plot_selector.currentText() == shown_plot:
            window.plot_selector.setCurrentText("Raw")

    QTimer.singleShot(_PLOT_MODE_REVERT_DELAY_MS, _revert)


def request_manual_acquisition(window, kind: str) -> None:
    # Recapturing Dark/Reference while tracking is active changes what
    # Absorbance means partway through a trace (same continuity concern as
    # pausing tracking itself - see handle_start_tracking_button_clicked_for)
    # - confirm first. Not needed when nothing is being tracked.
    if bool(getattr(window, "_sensorgram_tracking_active", False)):
        answer = QMessageBox.question(
            window,
            f"Capture new {kind}",
            f"Tracking is active. Are you sure you want to capture a new {kind} spectrum?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
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
        dark = window._session.state.dark
        reference = window._session.state.reference
        session_writer = getattr(window, "_session_writer", None)
        if session_writer is not None:
            try:
                session_writer.update_baselines(dark, reference)
            except Exception:
                pass
        if window._measurement_writer is not None:
            window._measurement_writer.update_baselines(dark, reference)
        try:
            save_dark_reference_cache(dark, reference)
        except Exception:
            window._log_warning(f"Could not cache the {kind} spectrum to the user profile.")
        target_plot = "Dark" if kind == "dark" else "Reference"
        if window.plot_selector.currentText() != target_plot:
            window.plot_selector.blockSignals(True)
            window.plot_selector.setCurrentText(target_plot)
            window.plot_selector.blockSignals(False)
        window._refresh_spectrum_plot(window._session.get_plot_data(window.PLOT_MODES[target_plot]), None)
        _schedule_plot_mode_revert_to_raw(window, target_plot)
        window._update_dark_reference_button_icons()
        window._request_deferred_ui_refresh(telemetry=True)
        window._schedule_acquisition_state_persist()
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
    if window._source_mode == "simulation":
        window._last_overhead_ms = None
    else:
        expected_budget_ms = spectrum.metadata.get("integration_time_ms", 0.0) * spectrum.metadata.get("averages", 1)
        if isinstance(expected_budget_ms, (int, float)):
            window._last_overhead_ms = max(result.elapsed_ms - float(expected_budget_ms), 0.0)
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
        try:
            save_dark_reference_cache(window._session.state.dark, window._session.state.reference)
        except Exception:
            window._log_warning(f"Could not cache the {kind} spectrum to the user profile.")
        _schedule_plot_mode_revert_to_raw(window, target_plot)
        window._log_success(f"{kind.capitalize()} spectrum acquired.")
    elif kind == "sample" and not window._live_active:
        window._log_success("Raw spectrum acquired.")
    elif kind == "sample":
        window._log_throttled(
            "live_sample",
            "Live sample updated.",
            level=logging.DEBUG,
            min_interval=1.0,
        )

    if window._pending_source_mode is not None:
        pending_mode = window._pending_source_mode
        restart_live = window._resume_live_after_source_switch
        window._pending_source_mode = None
        window._resume_live_after_source_switch = False
        window.source_tabs.blockSignals(True)
        window.source_tabs.setCurrentIndex(0 if pending_mode == "spectrometer" else 1)
        window.source_tabs.blockSignals(False)
        apply_source_mode_for(window, pending_mode, restart_live=restart_live)
        return

    if window._live_active and kind == "sample":
        pass
    else:
        window.status_label.setText(f"{kind.capitalize()} spectrum acquired successfully.")
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
    started = perf_counter()
    requested_at = getattr(window, "_live_result_requested_at", None)
    if requested_at is not None:
        interval_ms = float(getattr(window, "_live_result_requested_delay_ms", 0.0) or 0.0)
        try:
            window._last_live_result_poll_delay_ms = max((started - float(requested_at)) * 1000.0 - interval_ms, 0.0)
        except (TypeError, ValueError):
            window._last_live_result_poll_delay_ms = None
    window._live_result_requested_at = started
    _flush_live_processing_logs(window)
    flush_live_recording_results(window)
    drained_events: list[LiveAcquisitionEvent] = []
    dropped_events = 0
    while True:
        try:
            event = window._live_result_queue.get_nowait()
        except queue.Empty:
            break
        if drained_events:
            dropped_events += 1
        drained_events.append(event)

    live_result_depth = _queue_size_safe(window._live_result_queue)
    if live_result_depth is not None:
        window._live_result_queue_max_depth = max(int(getattr(window, "_live_result_queue_max_depth", 0)), live_result_depth)

    if not drained_events:
        window._last_live_acquisition_flush_ms = (perf_counter() - started) * 1000.0
        if window._live_worker is not None and not window._live_worker.is_alive():
            window._live_worker = None
            if window._pending_manual_kind is not None:
                pending = window._pending_manual_kind
                window._pending_manual_kind = None
                QTimer.singleShot(0, lambda kind=pending: window._start_acquisition(kind))
        elif window._live_active and window._live_worker is not None and window._live_worker.is_alive():
            _request_live_acquisition_poll_for(window, window._live_ui_refresh_delay_ms)
        return

    drain_ms = (perf_counter() - started) * 1000.0
    window._last_live_acquisition_flush_ms = drain_ms

    window._ui_preview_replaced_count = int(getattr(window, "_ui_preview_replaced_count", 0)) + dropped_events

    recording_backpressure = sum(e.recording_dropped_count for e in drained_events)
    if recording_backpressure > 0:
        window._raw_recording_backpressure_count = int(getattr(window, "_raw_recording_backpressure_count", 0)) + recording_backpressure

    latest_event: LiveAcquisitionEvent | None = None
    for event in reversed(drained_events):
        if event.error is None and event.result is not None:
            latest_event = event
            break

    if latest_event.error is not None:
        if latest_event.source_epoch != window._source_epoch:
            return
        auto_exposure_state = getattr(window, "_auto_exposure_state", None)
        if auto_exposure_state is not None and auto_exposure_state.active:
            _auto_exposure_finish(
                window,
                success=False,
                status_text="Failed",
                tooltip=f"Live acquisition stopped unexpectedly: {latest_event.error}",
            )
        window._live_active = False
        window._live_worker = None
        window._handle_acquisition_error(latest_event.source_epoch, latest_event.error)
        return

    if latest_event.result is not None:
        if latest_event.source_epoch != window._source_epoch:
            return
        result = latest_event.result
        spectrum = result.spectrum
        if getattr(window, "_pending_auto_exposure_start", False):
            window._pending_auto_exposure_start = False
            _auto_exposure_begin(window)
        window._auto_exposure_handle_live_frame(spectrum)
        now = latest_event.produced_at_perf if latest_event.produced_at_perf is not None else perf_counter()
        window._last_elapsed_ms = result.elapsed_ms
        _update_live_source_rate_from_event(window, latest_event, now)
        if window._source_mode == "simulation":
            window._last_overhead_ms = None
        else:
            expected_budget_ms = spectrum.metadata.get("integration_time_ms", 0.0) * spectrum.metadata.get("averages", 1)
            if isinstance(expected_budget_ms, (int, float)):
                window._last_overhead_ms = max(result.elapsed_ms - float(expected_budget_ms), 0.0)
            else:
                window._last_overhead_ms = None
        if window._effective_raw_rate_hz is not None and bool(getattr(window, "_deep_timing_enabled", False)):
            window._log_throttled(
                "raw_rate",
                (
                    "Raw source rate "
                    f"{format_rate_for_window(window, window._effective_raw_rate_hz)} | "
                    f"display_rate={format_rate_for_window(window, window.live_rate_spin.value())} | "
                    f"sample={getattr(latest_event, 'source_sample_index', 0)}"
                ),
                level=logging.DEBUG,
                min_interval=1.0,
            )
        spacing_text = "-" if window._last_spacing_ms is None else f"{window._last_spacing_ms:.1f} ms"
        _log_live_timing(
            window,
            "live_acquisition_queue_drain",
            (
                "Live acquisition queue drain: "
                f"{drain_ms:.2f} ms | acq={result.elapsed_ms:.1f} ms | "
                f"gap={spacing_text} | dropped={dropped_events}"
            ),
        )
        if bool(getattr(window, "_deep_timing_enabled", False)):
            window._log_throttled(
                "live_acq_flush",
                f"Live acquisition flush: {drain_ms:.2f} ms | dropped={dropped_events}",
                level=logging.DEBUG,
                min_interval=1.0,
            )
        refresh_state = window._ui_refresh_state
        refresh_state.telemetry_dirty = True
        refresh_state.live_estimate_dirty = True
        if window._live_active:
            _request_live_acquisition_poll_for(window, window._live_ui_refresh_delay_ms)
        window._ui_preview_displayed_count = int(getattr(window, "_ui_preview_displayed_count", 0)) + 1

    if dropped_events > 0:
        window._live_display_dropped_frames += dropped_events

    if window._live_worker is not None and not window._live_worker.is_alive():
        window._live_worker = None
        if window._pending_manual_kind is not None and not window._live_active and not window._busy:
            pending = window._pending_manual_kind
            window._pending_manual_kind = None
            QTimer.singleShot(0, lambda kind=pending: window._start_acquisition(kind))
    elif window._live_active and window._live_worker is not None and window._live_worker.is_alive():
        _request_live_acquisition_poll_for(window, window._live_ui_refresh_delay_ms)


def flush_live_processed_results(window) -> None:
    started = perf_counter()
    requested_at = getattr(window, "_live_processed_requested_at", None)
    if requested_at is not None:
        try:
            requested_delay_ms = float(getattr(window, "_live_processed_requested_delay_ms", 0.0) or 0.0)
            window._last_live_processed_poll_delay_ms = max((started - float(requested_at)) * 1000.0 - requested_delay_ms, 0.0)
        except (TypeError, ValueError):
            window._last_live_processed_poll_delay_ms = None
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

    live_processed_depth = _queue_size_safe(window._live_processed_queue)
    if live_processed_depth is not None:
        window._live_processed_queue_max_depth = max(int(getattr(window, "_live_processed_queue_max_depth", 0)), live_processed_depth)

    if latest_event is None:
        window._last_live_processed_flush_ms = (perf_counter() - started) * 1000.0
        if window._live_processing_worker is not None and window._live_processing_worker.is_alive() and window._live_active:
            _request_live_acquisition_poll_for(window, window._live_ui_refresh_delay_ms)
        return

    drain_ms = (perf_counter() - started) * 1000.0
    window._last_live_processed_flush_ms = drain_ms

    if latest_event.error is not None:
        if latest_event.source_epoch != window._source_epoch:
            return
        window._live_active = False
        window._live_processing_worker = None
        window._handle_acquisition_error(latest_event.source_epoch, latest_event.error)
        return

    if latest_event.result is None or latest_event.result.processed is None:
        if window._live_processing_worker is not None and window._live_processing_worker.is_alive() and window._live_active:
            _request_live_acquisition_poll_for(window, window._live_ui_refresh_delay_ms)
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
    tagged_processed = processed.with_metadata(
        display_average_count=1,
        display_window_ms=display_window_ms if display_window_ms > 0 else 0.0,
        display_refresh_hz=window.live_rate_spin.value(),
    )
    if window._source_mode == "simulation":
        # Simulation output is wired directly to Absorbance - no dark/
        # reference to subtract (see spectral_processing_pipeline_architecture.md).
        # set_sample is never called in simulation mode, so state.sample stays
        # unset there (nothing depends on it: saturation-line logic already
        # excludes simulation by y_label, auto-exposure is spectrometer-only).
        window._session.set_absorbance_direct(tagged_processed)
    else:
        window._session.set_sample(tagged_processed)
    window._last_display_average_count = 1
    window._last_display_period_ms = display_window_ms
    window._last_processing_ms = result.processing_ms
    window._last_processing_queue_wait_ms = result.queue_wait_ms
    _log_live_timing(
        window,
        "live_processing_queue_drain",
        (
            "Live processing queue drain: "
            f"{drain_ms:.2f} ms | proc={result.processing_ms:.1f} ms | "
                f"wait={result.queue_wait_ms:.1f} ms | dropped={dropped_events}"
        ),
    )
    if bool(getattr(window, "_deep_timing_enabled", False)):
        window._log_throttled(
            "live_processed_flush",
            f"Live processing flush: {drain_ms:.2f} ms | dropped={dropped_events}",
            level=logging.DEBUG,
            min_interval=1.0,
        )
    if result.processing_ms > 0:
        window._processing_rate_hz = 1000.0 / result.processing_ms
        display_period_ms = max(1000.0 / max(window.live_rate_spin.value(), 1e-9), 1.0)
        window._processing_headroom_ratio = display_period_ms / result.processing_ms
    else:
        window._processing_rate_hz = None
        window._processing_headroom_ratio = None
    # This event's processed/fit always come from the live-processing
    # worker's raw-sample transform - it has no idea about dark/reference and
    # never computes absorbance. Rendering it unconditionally here regardless
    # of plot_selector used to make Dark/Reference/Absorbance flash correctly
    # for one frame (whatever enqueue_plot_processing_for had just rendered
    # via the dropdown change), then get immediately overwritten back to raw
    # by the very next live frame, even though the dropdown still correctly
    # showed the other mode.
    plot_mode = window.PLOT_MODES.get(window.plot_selector.currentText(), "sample")
    if plot_mode == "sample":
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
        spectrum_render_started = perf_counter()
        try:
            window._refresh_spectrum_plot(processed, fit)
            window._autoscale_spectrum_plot()
            window._update_spectrum_stats(processed, fit)
            spectrum_render_finished = perf_counter()
            window._last_plot_refresh_ms = (spectrum_render_finished - spectrum_render_started) * 1000.0
            previous_finish = getattr(window, "_last_plot_refresh_finished_at", None)
            window._last_plot_refresh_gap_ms = (
                None if previous_finish is None else (spectrum_render_finished - float(previous_finish)) * 1000.0
            )
            window._last_plot_refresh_finished_at = spectrum_render_finished
            record_plot_refresh_rate_sample(window, spectrum_render_finished)
        except Exception as exc:
            window._log_error(f"Spectrum refresh failed: {exc}")
        # Only the "sample" (raw) branch has processed/fit that actually
        # correspond to what's on screen - see the comment above. Dark/
        # Reference/Absorbance are rendered via enqueue_plot_processing_for
        # below, whose own completion handler (handle_plot_processing_result_for,
        # main_window_plotting.py) is responsible for updating stats/trace for
        # those modes instead. Appending this raw processed/fit here
        # unconditionally used to feed the sensorgram trace (and the "Start
        # Tracking" button) raw-spectrum metrics even while viewing
        # Absorbance - see the maintainer's report of tracking "the wrong
        # plot".
        window._append_processed_trace_history(processed, fit)
    elif plot_mode == "absorbance":
        # Not produced by the live-processing worker - session.set_sample()
        # just above already refreshed session.state.absorbance from this
        # frame, so route through the normal thread-pool processing path
        # (enqueue_plot_processing_for) to actually display it. That path
        # already coalesces correctly if a request from the previous frame is
        # still in flight, same as a dropdown-triggered switch would.
        window._enqueue_plot_processing()
    # "dark"/"reference": intentionally left alone - both are static
    # snapshots that don't change just because a new live sample arrived.
    refresh_state = window._ui_refresh_state
    refresh_state.live_estimate_dirty = True
    refresh_state.telemetry_dirty = True
    if window._live_processing_worker is not None and window._live_processing_worker.is_alive() and window._live_active:
        _request_live_acquisition_poll_for(window, window._live_ui_refresh_delay_ms)


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
        apply_source_mode_for(window, pending_mode, restart_live=restart_live)
        return
    if window._live_active:
        window._stop_live_acquisition(f"Live acquisition stopped: {message}")
        return
    window._pending_manual_kind = None
    window._show_error(message)


def _reset_live_frame_telemetry(window) -> None:
    """Reset the per-frame timing/paint/refresh telemetry shared by live-acquisition start and stop.

    Session-total counters (``_raw_acquired_count``, ``_processing_completed_count``,
    etc.) are deliberately NOT reset here - only ``start_live_acquisition`` zeroes
    those, so they stay visible as a final tally after ``stop_live_acquisition``.
    """
    window._last_plot_refresh_ms = None
    window._last_plot_refresh_gap_ms = None
    window._last_plot_refresh_finished_at = None
    window._actual_plot_refresh_rate_hz = None
    window._plot_refresh_timestamps = []
    window._last_spectrum_curve_update_ms = None
    window._last_spectrum_fit_update_ms = None
    window._last_spectrum_marker_update_ms = None
    window._last_spectrum_residual_update_ms = None
    window._last_sensorgram_render_ms = None
    window._last_metric_autoscale_ms = None
    window._last_metric_plot_disabled_fast_path = False
    window._last_metric_render_series_export_ms = None
    window._last_metric_render_setdata_calls = None
    window._last_metric_render_setdata_skips = None
    window._last_metric_view_cache_modes = {}
    window._plot_display_points = max(int(getattr(window, "_plot_display_points", 512)), 1)
    window._trace_plot_paint_count = 0
    window._trace_plot_paint_total_ms = None
    window._trace_plot_paint_max_ms = None
    window._spectrum_plot_paint_count = 0
    window._spectrum_plot_paint_total_ms = None
    window._spectrum_plot_paint_max_ms = None
    window._last_deferred_ui_refresh_ms = None
    window._last_deferred_ui_refresh_total_ms = None
    window._last_deferred_display_refresh_ms = None
    window._last_deferred_stats_refresh_ms = None
    window._display_refresh_requested_at = None
    window._stats_refresh_requested_at = None
    window._last_deferred_ui_live_estimate_ms = None
    window._last_deferred_ui_telemetry_ms = None
    window._last_deferred_ui_trace_plot_ms = None
    window._last_deferred_ui_summary_ms = None
    window._last_deferred_ui_stats_ms = None
    window._last_stats_refresh_delay_ms = None
    window._last_processing_ms = None
    window._last_processing_queue_wait_ms = None
    window._processing_rate_hz = None
    window._processing_headroom_ratio = None
    window._last_live_acquisition_flush_ms = None
    window._last_live_processed_flush_ms = None
    window._last_live_visual_refresh_ms = None
    window._live_visual_refresh_count = 0
    window._live_visual_refresh_in_progress = False
    window._live_mode_deferred_display_flush_count = 0
    window._live_mode_deferred_metric_flush_count = 0
    window._live_mode_deferred_stats_flush_count = 0
    window._last_live_result_poll_delay_ms = None
    window._last_live_processed_poll_delay_ms = None
    window._last_summary_refresh_ms = None
    window._last_session_summary_refresh_total_ms = None
    window._last_session_stats_refresh_ms = None
    window._last_session_stats_refresh_total_ms = None
    window._last_log_buffer_flush_ms = None
    window._last_log_buffer_delay_ms = None
    window._last_log_buffer_total_ms = None
    window._live_result_requested_at = None
    window._live_result_requested_delay_ms = None
    window._last_ui_heartbeat_delay_ms = None
    window._ui_heartbeat_max_delay_ms = None
    window._last_ui_heartbeat_total_ms = None
    window._live_plot_update_counter = 0
    window._last_live_processing_perf = None


def start_live_acquisition(window) -> None:
    # Session writer is initialized lazily on the first processed spectrum because
    # the wavelength axis is not available yet - no eager creation here.
    if window._busy or window._live_active:
        return
    if window._live_worker is not None and window._live_worker.is_alive():
        return

    window._live_active = True
    window._display_window_ms = 1000.0 / max(window.live_rate_spin.value(), 1e-9)
    window._live_ui_refresh_delay_ms = max(int(round(window._display_window_ms)), 16)
    window._raw_last_finish_ts = None
    window._raw_last_sample_index = None
    window._last_display_average_count = None
    window._last_display_period_ms = None
    window._last_plot_refresh_delay_ms = None
    window._log_buffer_requested_at = None
    window._live_processed_requested_at = None
    window._live_processed_requested_delay_ms = None
    window._live_display_dropped_frames = 0
    window._raw_acquired_count = 0
    window._raw_recording_enqueued_count = 0
    window._raw_recording_written_count = 0
    window._raw_recording_backpressure_count = 0
    window._raw_recording_failed_count = 0
    window._ui_preview_enqueued_count = 0
    window._ui_preview_replaced_count = 0
    window._ui_preview_displayed_count = 0
    window._processing_enqueued_count = 0
    window._processing_replaced_count = 0
    window._processing_completed_count = 0
    window._processing_displayed_count = 0
    _reset_live_frame_telemetry(window)
    window._live_display_started_at = perf_counter()
    window._live_trace_started_at = datetime.now(timezone.utc)
    if hasattr(window, "_plot_view_cache") and hasattr(window._plot_view_cache, "clear_live_absolute_metric_cache"):
        window._plot_view_cache.clear_live_absolute_metric_cache()
    window._reset_live_accumulator()
    ctx = mp.get_context("spawn")
    window._live_stop_event = ctx.Event()
    window._live_result_queue = ctx.Queue(maxsize=4)
    window._live_processing_input_queue = ctx.Queue(maxsize=16)
    window._live_processed_queue = ctx.Queue(maxsize=4)
    window._live_recording_queue = ctx.Queue(maxsize=2048)
    window._live_processing_log_queue = ctx.Queue(maxsize=32)
    window._live_result_queue_max_depth = 0
    window._live_processed_queue_max_depth = 0
    window._live_recording_queue_max_depth = 0
    simulation_parameters = None
    if window._source_mode == "simulation":
        simulation_parameters = window._simulation_backend.simulation_parameters()
        window._log_info("Continuous simulation acquisition started.")
    else:
        window._log_info("Continuous spectrometer acquisition started.")
        _suspend_spectrometer_backend_for_live(window)
    window._set_measurement_buttons_enabled(False)
    window._set_manual_acquisition_buttons_enabled(True)
    refresh_state = window._ui_refresh_state
    refresh_state.live_estimate_dirty = True
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
        window._live_recording_queue,
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
    if hasattr(window, "_live_recording_timer"):
        window._live_recording_timer.start()
    flush_live_recording_results(window)
    _request_live_acquisition_poll_for(window, window._live_ui_refresh_delay_ms)
    live_result_depth = _queue_size_safe(window._live_result_queue)
    live_processed_depth = _queue_size_safe(window._live_processed_queue)
    if live_result_depth is not None:
        window._live_result_queue_max_depth = max(int(getattr(window, "_live_result_queue_max_depth", 0)), live_result_depth)
    if live_processed_depth is not None:
        window._live_processed_queue_max_depth = max(int(getattr(window, "_live_processed_queue_max_depth", 0)), live_processed_depth)
    window._update_window_mode_label()


def stop_live_acquisition(window, message: str = "Live acquisition stopped.") -> None:
    try:
        auto_exposure_state = getattr(window, "_auto_exposure_state", None)
        if auto_exposure_state is not None and auto_exposure_state.active:
            _auto_exposure_finish(
                window,
                success=False,
                status_text="Failed",
                tooltip="Live acquisition was stopped before auto exposure finished.",
            )
        window._pending_auto_exposure_start = False
        window._live_active = False
        window._live_stop_event.set()
        if hasattr(window, "_live_recording_timer"):
            window._live_recording_timer.stop()
        window._ui_task_scheduler.cancel("live_visual_refresh")
        window._ui_task_scheduler.cancel("live_acquisition")
        window._live_display_started_at = None
        window._live_trace_started_at = None
        window._trace_display_cursor_s = 0.0
        _reset_live_frame_telemetry(window)
        window._reset_live_accumulator()
        window._live_result_queue_max_depth = None
        window._live_processed_queue_max_depth = None
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
        flush_live_recording_results(window)
        if window._live_processing_worker is not None:
            try:
                window._live_processing_worker.join(timeout=0.25)
            except Exception:
                pass
            if not window._live_processing_worker.is_alive():
                window._live_processing_worker = None
        _flush_live_processing_logs(window)
        flush_live_recording_results(window)
        window._live_recording_queue = None
        _restore_spectrometer_backend_after_live(window)
    finally:
        if not _is_recording_active(window):
            close_temp_measurement_writer(window)


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
    # Auto-exposure needs live acquisition running (it watches the live stream -
    # see start_auto_exposure_for), so it belongs in the same "still usable
    # during live" bucket as dark/reference, not in set_measurement_buttons_enabled's
    # blanket disable. Without this, start_live_acquisition's
    # set_measurement_buttons_enabled(False) leaves the Auto button permanently
    # disabled for as long as live acquisition runs - i.e. essentially always,
    # once a spectrometer is connected.
    window.auto_integration_button.setEnabled(enabled and window._source_mode == "spectrometer")


def set_measurement_ui_locked(window, locked: bool) -> None:
    window._measurement_ui_locked = locked
    window.sim_resolution_spin.setEnabled(not locked)
    window.sim_output_rate_spin.setEnabled(not locked and window._source_mode == "simulation")
    # Toggling tracking off mid-recording would put a gap in the measurement
    # file's own metrics table (append_processed_trace_history is what's being
    # gated) - disable the control entirely for the duration of the recording
    # rather than rely on the maintainer never pressing it by mistake. Also
    # only enabled while viewing Absorbance with it actually available - see
    # _refresh_start_tracking_button_enabled (only Absorbance is trackable).
    if hasattr(window, "_refresh_start_tracking_button_enabled"):
        window._refresh_start_tracking_button_enabled()
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
            trace_icon = tint_tabler_icon(flow_tabler_icon("player_record_filled"), record_color)
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

    # Derived via PLOT_MODES (display label -> internal kind) rather than a raw
    # .lower() of the label text, so this stays correct regardless of what the
    # dropdown currently displays (e.g. "Raw" instead of the old "Sample").
    signal_mode = window.PLOT_MODES.get(window.plot_selector.currentText(), "sample")
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

    # Restart the session right as recording commits, so the sensorgram's
    # t=0 becomes "the moment Trace was clicked" instead of whenever the
    # session/prep-procedure time happened to start - see reset_session_for's
    # docstring. Dark/reference are always carried over since they're
    # calibration, not the prep-procedure "trash" this is meant to drop; the
    # old session file (with any prep-time data) is left untouched on disk,
    # not deleted. Silent (announce=False): naming the experiment above
    # already served as the "starting for real now" confirmation, and the
    # "Recording to ..." status message fires right below anyway.
    from lspr_app.gui.main_window_new_session import reset_session_for

    reset_session_for(window, keep_dark=True, keep_reference=True, announce=False)

    started_at = datetime.now(timezone.utc)
    # Recording follows the execution state, but it is not stopped by flow HOLD.
    window._measurement_axis_lock = np.asarray(anchor.wavelengths_nm, dtype=np.float64).copy()
    writer_error_signals = MeasurementWriterErrorSignals()
    writer_error_signals.failed.connect(window._handle_measurement_writer_failed)
    # Kept alive on window for as long as the writer runs - a QObject with no
    # surviving Python reference can be garbage-collected even with live
    # connections, which would silently drop this error path.
    window._measurement_writer_error_signals = writer_error_signals
    window._measurement_writer = AsyncHDF5MeasurementWriter(
        destination,
        signal_mode,
        window._measurement_axis_lock,
        window._current_processing_settings(),
        experiment_name=experiment_name,
        started_at_utc=started_at,
        flush_interval_s=window._measurement_flush_interval_s,
        on_error=writer_error_signals.failed.emit,
    )
    window._measurement_writer.update_acquisition_state(window._acquisition_state_payload())
    window._measurement_writer.update_baselines(window._session.state.dark, window._session.state.reference)
    window._measurement_writer.write_device_inventory(device_inventory_rows(window._device_comm_service.list_statuses()))
    window._measurement_active = True
    window._measurement_paused = False
    window._measurement_signal_mode = signal_mode
    window._measurement_path = destination
    window._measurement_started_at = started_at
    window._measurement_experiment_name = experiment_name
    window._session_stats_log.clear()
    window._session_stats_log_last_text = ""
    window._session_stats_log_last_capture_ts = 0.0
    # Deliberately NOT cleared here: the events list accumulates across
    # every measurement in the session (each entry carries its own
    # timestamp_utc_ms), so session view can show step markers from every
    # past recording, not just the most recent one. sync_sensorgram_control_
    # step_overlay filters to just the current measurement while one is
    # active; clear_trace_history_for (main_window_plotting.py) is the
    # actual "reset everything" point, for when the whole session resets.
    if hasattr(window, "_sync_sensorgram_control_step_overlay"):
        window._sync_sensorgram_control_step_overlay()
    if window._live_worker is not None and window._live_worker.is_alive():
        window._live_worker.update_archive_context(window._measurement_writer, True, window._measurement_started_at)
    set_measurement_ui_locked(window, True)
    if hasattr(window, "_set_recording_blink_indicator"):
        window._set_recording_blink_indicator(True)
    if hasattr(window, "_plot_view_cache") and hasattr(window._plot_view_cache, "clear_live_absolute_metric_cache"):
        window._plot_view_cache.clear_live_absolute_metric_cache()
    if hasattr(window, "_plot_view_cache"):
        window._plot_view_cache.clear()
    window._trace_display_cursor_s = 0.0
    window._live_trace_started_at = None
    window._metric_reference_processed = None
    # Auto-switch to measurement view so the plot shows only the current recording,
    # and release any stale pan/zoom lock so the view re-follows once recording
    # starts (see SensorgramDisplayState.begin_measurement).
    window._sensorgram_display.begin_measurement()
    window._last_metric_autoscale_range = None
    window._refresh_session_statistics(force=True)
    window._refresh_trace_plot("Metric position (nm)")
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
    measurement_path = window._measurement_path
    if window._live_worker is not None and window._live_worker.is_alive():
        window._live_worker.update_archive_context(None, False, None)
    flush_live_recording_results(window)
    flush_measurement_frames(window, force=True)
    if window._measurement_writer is not None:
        window._measurement_writer.close()
    window._measurement_writer = None
    window._measurement_writer_error_signals = None
    window._measurement_active = False
    window._measurement_paused = False
    # Sensorgram x-values are relative to measurement start while a
    # measurement is recording, but the session view (which the plot is
    # about to switch back to, below) is relative to session start instead.
    # Rebase any live-cache tail points still on the measurement's scale
    # onto the session's scale before that anchor is cleared - otherwise the
    # post-stop session reload (which reads everything as session-relative)
    # would merge those points in on the wrong scale, splicing a short
    # misplaced line segment into already-drawn session history. This is a
    # pure time shift, not a re-derivation, so point order/pairing can't be
    # scrambled by it. See docs/sensorgram_improvements.md.
    offset_s = measurement_to_session_offset_s(window)
    if offset_s is not None:
        cache = getattr(window, "_plot_view_cache", None)
        if cache is not None and hasattr(cache, "rebase_live_absolute_metric_recent_tail"):
            cache.rebase_live_absolute_metric_recent_tail(offset_s)
    # Close off this measurement's overlay segment with a STOP boundary
    # before its events can be mistaken for still-open by whatever the next
    # measurement records - see close_sensorgram_control_step_overlay_segment.
    if hasattr(window, "_close_sensorgram_control_step_overlay_segment"):
        window._close_sensorgram_control_step_overlay_segment()
    window._measurement_started_at = None
    window._measurement_axis_lock = None
    set_measurement_ui_locked(window, False)
    if hasattr(window, "_set_recording_blink_indicator"):
        window._set_recording_blink_indicator(False)
    sync_simulation_backend_from_controls_for(window)
    window._live_trace_started_at = None
    window._metric_reference_processed = None
    # Auto-switch back to session view so the full session history is visible
    # again, and clear any pan/zoom lock (see SensorgramDisplayState.end_measurement).
    window._sensorgram_display.end_measurement()
    window._last_metric_autoscale_range = None
    # Reload session archive so the cache gets repopulated with the full session.
    try:
        from lspr_app.gui.main_window_sensorgram_archive import request_absolute_sensorgram_metric_archive_reload
        request_absolute_sensorgram_metric_archive_reload(window)
    except Exception:
        pass
    window._refresh_session_statistics(force=True)
    window._refresh_trace_plot("Metric position (nm)")
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


def _is_absorbance_plot_mode(window) -> bool:
    """True while the spectrum plot is showing Absorbance.

    "Extinction" implies an absorbance-like (-log10 transmittance) value;
    processed.values/fit.values hold whatever PLOT_MODES the user currently
    has selected (Dark/Reference/Sample/Absorbance), so a Y-value read
    outside Absorbance mode would be raw counts mislabeled as extinction.
    Per the maintainer's explicit choice, extinction is only ever recorded/
    displayed while genuinely in Absorbance mode - not silently
    recomputed against an absorbance curve while another mode is shown.
    """
    plot_modes = getattr(window, "PLOT_MODES", None)
    plot_selector = getattr(window, "plot_selector", None)
    if not isinstance(plot_modes, dict) or plot_selector is None:
        return False
    return plot_modes.get(plot_selector.currentText()) == "absorbance"


def append_processed_trace_history(window, processed: Spectrum, fit: Spectrum | None) -> None:
    if not window._live_active:
        return
    if not getattr(window, "_sensorgram_tracking_active", False):
        # Tracking hasn't been started (start_tracking_button) - don't plot or
        # archive a point for whatever is currently displayed. Keeps Dark/
        # Reference setup, or any pre-tracking fiddling with Sample/Absorbance,
        # out of the sensorgram and out of both HDF5 metrics tables. See
        # docs/sensorgram_improvements.md and the maintainer's workflow
        # discussion on avoiding calibration-time artefacts in the trace.
        return

    # display_time_anchor is measurement-relative while a measurement is
    # recording and session-relative otherwise (see
    # gui/sensorgram_time_anchor.py) - the same anchor the plot's own axis
    # and the per-step overlay use, so this stays consistent with what's
    # actually drawn regardless of view-mode switches.
    started_at = display_time_anchor(window)
    if started_at is not None:
        elapsed_s = max((processed.acquired_at - started_at).total_seconds(), 0.0)
    else:
        elapsed_s = float(getattr(window, "_trace_display_cursor_s", 0.0) or 0.0)

    display_step_s = max(1.0 / max(window.live_rate_spin.value(), 1e-9), 1e-3)
    metrics = window._get_analysis_metrics(processed, fit)
    selected_metrics = set(window._selected_trace_metrics())
    updated = False
    for metric_name in window.TRACE_METRIC_LABELS:
        value = metrics.get(metric_name)
        if not isinstance(value, (int, float)) or not np.isfinite(float(value)):
            continue
        # Only maintain the compression-pyramid cache for metrics that are actually
        # selected/plotted - render_metric_series() (plot_controller.py) already
        # skips unselected metrics the same way, so rebuilding all four caches
        # (see TRACE_METRIC_LABELS) every live frame regardless of what's on screen
        # was pure wasted work, and it scales with elapsed session time (each
        # rebuild walks the metric's full compression pyramid). See
        # docs/sensorgram_improvements.md P2, and apply_sensorgram_metric_selection
        # in main_window_sensorgram.py, which reseeds a metric's cache from the
        # archive file when it becomes newly selected so switching visibility
        # doesn't leave a gap.
        if metric_name in selected_metrics:
            plot_view_cache = getattr(window, "_plot_view_cache", None)
            if plot_view_cache is not None and hasattr(plot_view_cache, "append_live_absolute_metric_point"):
                try:
                    plot_view_cache.append_live_absolute_metric_point(
                        metric_name,
                        float(elapsed_s),
                        float(value),
                        target_points=max(int(getattr(window, "_plot_display_points", 512)), 1),
                        recent_tail_points=max(int(getattr(window, "_sensorgram_compression_recent_tail_points", 300)), 0),
                    )
                except Exception:
                    pass
        updated = True
        last_metric_points = max(int(getattr(window, "_trace_display_cursor_s", 0.0) / max(display_step_s, 1e-9)), 0)

    window._trace_display_cursor_s = elapsed_s + display_step_s

    # Sensorgram secondary axes (see gui/sensorgram_secondary_axis.py): FWHM
    # and extinction are both high-rate (one value per spectrum, same as the
    # 1Y metrics above), so only maintain caches for metrics some active
    # slot actually wants - same "don't do wasted work for what isn't
    # visible" reasoning as the loop above. In "xy2y" mode both slots could
    # want the same or different high-rate metrics, hence the set union.
    active_secondary_metrics = {
        secondary_axis_metric_for(window, slot)
        for slot in SECONDARY_AXIS_SLOTS
        if secondary_axis_slot_active(window, slot)
    }
    high_rate_metrics = active_secondary_metrics & {"fwhm", "extinction"}
    if high_rate_metrics:
        plot_view_cache = getattr(window, "_plot_view_cache", None)
        target_points = max(int(getattr(window, "_plot_display_points", 512)), 1)
        recent_tail_points = max(int(getattr(window, "_sensorgram_compression_recent_tail_points", 300)), 0)
        for metric_name in high_rate_metrics:
            if metric_name == "fwhm":
                secondary_value = fit.metadata.get("fwhm_nm", np.nan) if fit is not None else processed.metadata.get("fwhm_nm", np.nan)
            else:
                # extinction: only meaningful while genuinely viewing
                # Absorbance - see _is_absorbance_plot_mode. Outside that
                # mode there's simply nothing to plot for this point, same
                # as a metric with no data.
                secondary_value = metrics.get("extinction_value", np.nan) if _is_absorbance_plot_mode(window) else np.nan
            if not isinstance(secondary_value, (int, float)) or not np.isfinite(float(secondary_value)):
                continue
            if plot_view_cache is not None and hasattr(plot_view_cache, "append_live_absolute_metric_point"):
                try:
                    plot_view_cache.append_live_absolute_metric_point(
                        metric_name,
                        float(elapsed_s),
                        float(secondary_value),
                        target_points=target_points,
                        recent_tail_points=recent_tail_points,
                    )
                except Exception:
                    pass
        # No direct redraw call here: window._request_deferred_ui_refresh(...)
        # a few lines below (inside "if updated:", which always runs) already
        # marks the plot dirty and goes through the same coalesced/throttled
        # scheduler the 1Y metrics use - refresh_metric_plot's own tick (see
        # plot_controller.py) is what actually calls update_secondary_axis_
        # display for both slots. Calling it directly here as well used to
        # force a full, uncoalesced curve.setData()+envelope+autoscale on
        # every single spectrum (up to ~100 Hz) instead of once per coalesced
        # UI refresh - the same anti-pattern already identified and fixed for
        # the 1Y metrics in docs/sensorgram_improvements.md (P2).

    if updated:
        # Store the derived metric so the live archive and the recording file share the same format.
        acquired_at_unix_ms = int(round(processed.acquired_at.astimezone(timezone.utc).timestamp() * 1000.0))
        common_fields = {
            "acquired_at_unix_ms": acquired_at_unix_ms,
            "sample_index": -1,
            "centroid_nm": metrics.get("centroid", np.nan),
            "smoothed_max_nm": metrics.get("smoothed_max", np.nan),
            "poly_max_nm": metrics.get("poly_max", np.nan),
            "gaussian_center_nm": metrics.get("gaussian_center", np.nan),
            "fwhm_nm": fit.metadata.get("fwhm_nm", np.nan) if fit is not None else processed.metadata.get("fwhm_nm", np.nan),
            "mse": fit.metadata.get("mse", np.nan) if fit is not None else np.nan,
            "snr": metrics.get("snr", np.nan),
            # Only ever recorded while genuinely in Absorbance mode - see
            # _is_absorbance_plot_mode - so this column is never a raw-counts
            # value silently mislabeled as extinction.
            "extinction_value": metrics.get("extinction_value", np.nan) if _is_absorbance_plot_mode(window) else np.nan,
        }

        # Always write to the session file. acquired_at_unix_ms (absolute,
        # already in common_fields) is the sole per-row timestamp as of
        # schema 6.0 - no relative anchor to compute or keep in sync across
        # live-acquisition/measurement start-stop transitions. See
        # docs/sensorgram_improvements.md, "Correctness fixes" C1/C2, for why
        # the old per-branch relative t_ms was the actual bug source.
        from lspr_app.storage.measurement_archive import ensure_session_writer
        session_writer = ensure_session_writer(window, processed)
        if session_writer is not None:
            try:
                session_writer.append_metrics([common_fields])
            except Exception:
                pass

        # Also write to the measurement file during active recording.
        if window._measurement_active and window._measurement_writer is not None:
            try:
                window._measurement_writer.append_metrics([common_fields])
            except Exception:
                pass
        window._request_deferred_ui_refresh(trace_plot=True, trace_label="Metric position (nm)")
        window._request_trace_autoscale()
        notify_startup_data_rendered = getattr(window, "_notify_startup_data_rendered", None)
        if callable(notify_startup_data_rendered):
            try:
                notify_startup_data_rendered("trace")
            except Exception:
                pass
        window._log_throttled(
            "trace_append",
            (
                "Metric point appended | points="
                f"{last_metric_points if 'last_metric_points' in locals() else 0} | "
                f"rate={format_rate_for_window(window, window.live_rate_spin.value())}"
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


def _experiment_runtime_flags(window) -> tuple[bool, bool, bool]:
    experiment_control_window = getattr(window, "_experiment_control_window", None)
    if experiment_control_window is None:
        return False, False, False
    return (
        bool(getattr(experiment_control_window, "_plan_running", False)),
        bool(getattr(experiment_control_window, "_plan_holding", False)),
        bool(getattr(experiment_control_window, "_plan_paused", False)),
    )


def _experiment_runtime_state(window) -> str:
    running, holding, paused = _experiment_runtime_flags(window)
    return experiment_runtime_state_name(running=running, holding=holding, paused=paused)


def _experiment_runtime_label(window) -> str:
    running, holding, paused = _experiment_runtime_flags(window)
    return experiment_runtime_label(
        running=running,
        holding=holding,
        paused=paused,
        recording=bool(getattr(window, "_measurement_active", False)),
    )


def update_window_mode_label(window) -> None:
    if not hasattr(window, "_window_mode_label"):
        return
    profile = getattr(window, "_launch_profile_spec", None)
    if profile is None:
        profile = launch_profile_spec(DEFAULT_LAUNCH_PROFILE)
    source_name = "Spectrometer" if window._source_mode == "spectrometer" else "Simulation"
    if getattr(profile, "window_mode_label_text", None):
        text = str(profile.window_mode_label_text)
        tooltip = (
            "Control Plan Editor mode: edit experiment plans without hardware discovery, live acquisition, "
            "or recording controls."
        )
        window._window_mode_label.setText(text)
        window._window_mode_label.setToolTip(tooltip)
        window._window_mode_label.setStyleSheet(
            f"color: {str(getattr(profile, 'window_mode_label_color', '') or '#9FD7A6')}; font-weight: 700;"
        )
        if hasattr(window, "_window_mode_icon_label"):
            window._window_mode_icon_label.clear()
            window._window_mode_icon_label.setVisible(False)
        return
    # Primary state: whether the app is actively recording a measurement,
    # just live-previewing, or neither - plus which source is feeding it.
    # This is "the main state" per the maintainer's explicit request; it
    # used to show only the experiment-control plan's own run/hold/pause
    # state ("Experiment: stopped"), which is a different concept entirely
    # (an automated pump/valve program that may or may not be running
    # regardless of whether a measurement is being recorded) and never
    # mentioned the source at all outside the tooltip - so the one thing the
    # maintainer most wanted to see at a glance wasn't there. The plan state
    # is still appended, but only when it's actually doing something, so the
    # common case (no plan running) stays a simple two-part label instead of
    # always saying "stopped".
    if bool(getattr(window, "_measurement_active", False)):
        state_text = "Recording"
        style = "color: #ff5b5b; font-weight: 700;"
    elif bool(getattr(window, "_live_active", False)):
        state_text = "Live mode"
        style = ""
    else:
        state_text = "Free mode"
        style = ""
    text = f"{state_text} | {source_name}"
    tooltip = f"{state_text} on the {source_name.lower()} source."

    running, holding, paused = _experiment_runtime_flags(window)
    plan_state = experiment_runtime_state_name(running=running, holding=holding, paused=paused)
    if plan_state != "stopped":
        text += f" | Plan: {plan_state}"
        tooltip += f" Experiment control plan is {plan_state}."

    window._window_mode_label.setText(text)
    window._window_mode_label.setToolTip(tooltip)
    window._window_mode_label.setStyleSheet(style)
    if hasattr(window, "_window_mode_icon_label"):
        if bool(getattr(profile, "show_source_icon", True)):
            if bool(getattr(window, "_measurement_active", False)):
                # Blinks in step with the measurement-toggle button's own
                # recording blink (_render_recording_blink_indicator calls
                # this function on every blink tick) - a still source icon
                # here looked identical whether or not a recording was
                # actually in progress, easy to miss at a glance.
                if bool(getattr(window, "_recording_blink_visible", True)):
                    record_icon = transport_icon(window._theme_mode, "record")
                    window._window_mode_icon_label.setPixmap(record_icon.pixmap(16, 16))
                else:
                    blank = QPixmap(16, 16)
                    blank.fill(Qt.GlobalColor.transparent)
                    window._window_mode_icon_label.setPixmap(blank)
                window._window_mode_icon_label.setToolTip("Recording is active.")
                window._window_mode_icon_label.setVisible(True)
            else:
                source_icon = prism_tab_icon() if window._source_mode == "spectrometer" else math_function_tab_icon()
                window._window_mode_icon_label.setPixmap(source_icon.pixmap(16, 16))
                window._window_mode_icon_label.setToolTip(f"{source_name} source")
                window._window_mode_icon_label.setVisible(True)
        else:
            window._window_mode_icon_label.clear()
            window._window_mode_icon_label.setToolTip("")
            window._window_mode_icon_label.setVisible(False)


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


def _is_recording_active(window: Any) -> bool:
    return bool(getattr(window, "_measurement_active", False))


AUTO_EXPOSURE_WATCHDOG_MS = 8_000


@dataclass
class _AutoExposureState:
    """Tracks one in-progress auto-exposure run. See start_auto_exposure_for."""

    active: bool = True
    requested_time_us: int = 0
    iteration: int = 0
    min_us: int = 1_000
    max_us: int = 1_000_000
    watchdog_token: int = 0


def _auto_exposure_finish(window, *, success: bool, status_text: str, tooltip: str) -> None:
    state = getattr(window, "_auto_exposure_state", None)
    if state is not None:
        state.active = False
    window.auto_integration_button.setEnabled(window._source_mode == "spectrometer")
    window.integration_spin.setEnabled(True)
    label = window.auto_integration_status_label
    label.setText(status_text)
    label.setToolTip(tooltip)
    label.setStyleSheet(
        "color: #2e7d32; font-weight: 600;" if success else "color: #c62828; font-weight: 600;"
    )
    if success:
        window._log_success(f"Auto exposure finished: {tooltip}")
        window.status_label.setText(tooltip)
    else:
        window._log_warning(f"Auto exposure failed: {tooltip}")
        window.status_label.setText(f"Auto exposure failed: {tooltip}")


def _auto_exposure_arm_watchdog(window) -> None:
    state = window._auto_exposure_state
    state.watchdog_token += 1
    token = state.watchdog_token
    QTimer.singleShot(AUTO_EXPOSURE_WATCHDOG_MS, lambda: _auto_exposure_handle_watchdog(window, token))


def _auto_exposure_handle_watchdog(window, token: int) -> None:
    state = getattr(window, "_auto_exposure_state", None)
    if state is None or not state.active or state.watchdog_token != token:
        return
    _auto_exposure_finish(
        window,
        success=False,
        status_text="Failed",
        tooltip="No new live spectrum arrived in time. Check the live acquisition / spectrometer connection.",
    )


def auto_exposure_handle_live_frame_for(window, spectrum: Spectrum) -> None:
    """Advance an in-progress auto-exposure run using the latest live frame.

    Called for every live frame from flush_live_acquisition_results - a no-op
    unless a run is active. Reads the raw sample peak directly (not the
    processed/cropped plot data) since the goal is the detector's true
    saturation headroom, not whatever wavelength window is being analyzed.
    """
    state = getattr(window, "_auto_exposure_state", None)
    if state is None or not state.active:
        return

    metadata_ms = spectrum.metadata.get("integration_time_ms")
    if not isinstance(metadata_ms, (int, float)) or not np.isfinite(float(metadata_ms)):
        return
    observed_us = int(round(float(metadata_ms) * 1000.0))
    if abs(observed_us - state.requested_time_us) > 2:
        # Stale frame acquired before the live worker picked up our last
        # requested integration time - ignore it and wait for the next one.
        return

    cfg = window._auto_exposure_settings
    max_intensity = float(window._spectrometer.max_intensity())
    values = np.asarray(spectrum.values, dtype=np.float64)
    peak = float(np.max(values)) if values.size else 0.0

    saturation_level = cfg.saturation_fraction * max_intensity
    band_low = cfg.target_low_fraction * max_intensity
    target_level = 0.5 * (cfg.target_low_fraction + cfg.saturation_fraction) * max_intensity

    if band_low <= peak < saturation_level:
        _auto_exposure_finish(
            window,
            success=True,
            status_text="Done",
            tooltip=(
                f"Integration time set to {observed_us / 1000.0:.3f} ms "
                f"(peak {peak / max_intensity * 100:.1f}% of full scale)."
            ),
        )
        return

    current_us = state.requested_time_us
    too_bright = peak >= saturation_level
    if too_bright and current_us <= state.min_us + 1:
        _auto_exposure_finish(
            window,
            success=False,
            status_text="Failed",
            tooltip="Light source too bright. Lower the intensity.",
        )
        return
    if not too_bright and current_us >= state.max_us - 1:
        _auto_exposure_finish(
            window,
            success=False,
            status_text="Failed",
            tooltip="Light source too dark. Check if it is turn on, optic path, etc.",
        )
        return

    state.iteration += 1
    if state.iteration > cfg.max_iterations:
        _auto_exposure_finish(
            window,
            success=False,
            status_text="Failed",
            tooltip="Could not converge on a stable integration time within the allowed attempts.",
        )
        return

    if peak <= max(max_intensity * 1e-6, 1e-9):
        # No detectable signal at all - jump straight to the ceiling instead of
        # a proportional step, since peak/target scaling is meaningless at zero.
        candidate_us = state.max_us
    else:
        candidate_us = int(round(current_us * (target_level / peak)))
    candidate_us = min(max(candidate_us, state.min_us), state.max_us)
    if candidate_us == current_us:
        candidate_us += -1 if too_bright else 1
        candidate_us = min(max(candidate_us, state.min_us), state.max_us)

    state.requested_time_us = candidate_us
    # setValue() synchronously triggers _handle_live_setting_change, which
    # overwrites status_label itself ("Live display window reset after
    # settings change.") - set our own message after, not before, so it's
    # the one actually left showing.
    window.integration_spin.setValue(candidate_us / 1000.0)
    window.status_label.setText(
        f"Auto exposure: trying {candidate_us / 1000.0:.3f} ms "
        f"(attempt {state.iteration}/{cfg.max_iterations})..."
    )
    _auto_exposure_arm_watchdog(window)


def _auto_exposure_begin(window) -> None:
    cfg = window._auto_exposure_settings
    min_us, max_us = cfg.min_integration_time_us, cfg.max_integration_time_us
    hw_limits = window._spectrometer.integration_time_limits_us()
    if hw_limits is not None:
        hw_min_us, hw_max_us = hw_limits
        min_us = max(min_us, int(hw_min_us))
        max_us = min(max_us, int(hw_max_us))

    raw_us = int(round(window.integration_spin.value() * 1000.0))
    current_us = min(max(raw_us, min_us), max_us)

    window._auto_exposure_state = _AutoExposureState(
        requested_time_us=current_us,
        min_us=min_us,
        max_us=max_us,
    )
    window.auto_integration_button.setEnabled(False)
    window.integration_spin.setEnabled(False)
    window.auto_integration_status_label.setText("")
    window.auto_integration_status_label.setToolTip("")
    if current_us != raw_us:
        # The current spin value was outside the effective [min_us, max_us]
        # search range (config bounds intersected with hardware limits) -
        # move it in range before waiting for a matching live frame.
        window.integration_spin.setValue(current_us / 1000.0)
    window.status_label.setText("Auto exposure: evaluating current signal...")
    _auto_exposure_arm_watchdog(window)


def start_auto_exposure_for(window) -> None:
    """Start (or queue the start of) a live auto-exposure run.

    Unlike the old implementation, this never touches window._spectrometer's
    own hardware connection or stops/restarts live acquisition: it simply
    watches the frames the live worker is already producing and nudges the
    integration time (via the normal integration_spin -> live worker settings
    path) toward the target band. See auto_exposure_handle_live_frame_for for
    the per-frame convergence logic.
    """
    if window._source_mode == "simulation":
        window.status_label.setText("Auto exposure is only available for the spectrometer source.")
        window._log_warning("Auto exposure requested while simulation source is active.")
        return

    existing = getattr(window, "_auto_exposure_state", None)
    if existing is not None and existing.active:
        return

    if not window._live_active:
        window.status_label.setText("Auto exposure: starting live acquisition...")
        window._pending_auto_exposure_start = True
        window._start_live_acquisition()
        return

    _auto_exposure_begin(window)


def sync_simulation_backend_from_controls_for(window) -> None:
    """Push current slider/spin values to the simulation backend and live worker."""
    window._simulation_backend.set_simulation_parameters(
        SimulationParameters(
            wavelength_min_nm=400.0,
            wavelength_max_nm=900.0,
            wavelength_resolution_nm=max(window.sim_resolution_spin.value(), 0.001),
            peak_center_nm=float(window.sim_peak_center_slider.value()),
            peak_width_nm=float(max(window.sim_peak_width_slider.value(), 1)),
            # peak_height/baseline/noise sliders are int, milli-AU steps -
            # divide by 1000 to get absorbance units (see SimulationParameters).
            peak_height=float(window.sim_peak_height_slider.value()) / 1000.0,
            secondary_peak_offset_nm=float(window.sim_secondary_peak_offset_slider.value()),
            secondary_peak_height_percent=float(window.sim_secondary_peak_height_slider.value()),
            secondary_peak_width_percent=float(max(window.sim_secondary_peak_width_slider.value(), 1)),
            baseline=float(window.sim_baseline_slider.value()) / 1000.0,
            slope=float(window.sim_slope_slider.value()) / 100.0,
            noise=float(window.sim_noise_slider.value()) / 1000.0,
        )
    )
    if (
        window._live_active
        and window._source_mode == "simulation"
        and window._live_worker is not None
        and window._live_worker.is_alive()
    ):
        window._live_worker.update_simulation_parameters(window._simulation_backend.simulation_parameters())
