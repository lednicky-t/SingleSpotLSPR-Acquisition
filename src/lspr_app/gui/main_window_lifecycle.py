from __future__ import annotations

import traceback

from time import perf_counter

from PyQt6.QtCore import QEvent, Qt, QTimer

from lspr_app.device.simulated import SimulatedSpectrometer
from lspr_app.gui.hardware_initializer import HardwareInitResult, HardwareInitStepResult, HardwareInitTask
from lspr_app.gui.main_window_state import (
    acquisition_state_payload,
    collapsible_section_state,
    launch_profile_settings,
    persist_acquisition_state,
    ensure_visible_top_content_splitter,
    restore_collapsible_section_state,
    restore_ui_state,
    save_ui_state,
    schedule_acquisition_state_persist,
)
from lspr_app.gui.main_window_titlebar import refresh_hw_device_status_strip
from lspr_app.storage.app_config import save_app_setting


def restore_ui_state_for(window) -> None:
    setattr(window, "_restoring_ui_state", True)
    try:
        restore_ui_state(window)
    finally:
        setattr(window, "_restoring_ui_state", False)


def save_ui_state_for(window) -> None:
    started = perf_counter()
    requested_at = getattr(window, "_ui_state_requested_at", None)
    if requested_at is not None:
        try:
            window._last_ui_state_delay_ms = max((started - float(requested_at)) * 1000.0, 0.0)
        except (TypeError, ValueError):
            window._last_ui_state_delay_ms = None

    def _callback() -> None:
        save_ui_state(window)

    window._run_gui_callback_timed("ui_state_save", _callback)
    window._last_ui_state_save_ms = (perf_counter() - started) * 1000.0
    window._ui_state_requested_at = None


def schedule_ui_state_persist_for(window) -> None:
    if getattr(window, "_restoring_ui_state", False):
        return
    if not getattr(window, "_ui_state_persistence_enabled", True):
        window._ui_state_requested_at = None
        timer = getattr(window, "_ui_state_timer", None)
        if timer is not None:
            timer.stop()
        return
    window._ui_state_requested_at = perf_counter()
    timer = getattr(window, "_ui_state_timer", None)
    if timer is not None:
        timer.start()


def collapsible_section_state_for(window) -> dict[str, bool]:
    return collapsible_section_state(window)


def restore_collapsible_section_state_for(window) -> None:
    restore_collapsible_section_state(window)


def acquisition_state_payload_for(window) -> dict[str, object]:
    return acquisition_state_payload(window)


def persist_acquisition_state_for(window) -> None:
    started = perf_counter()
    requested_at = getattr(window, "_acquisition_state_requested_at", None)
    if requested_at is not None:
        try:
            window._last_acquisition_state_delay_ms = max((started - float(requested_at)) * 1000.0, 0.0)
        except (TypeError, ValueError):
            window._last_acquisition_state_delay_ms = None

    def _callback() -> None:
        persist_acquisition_state(window)

    window._run_gui_callback_timed("acquisition_state_save", _callback)
    window._last_acquisition_state_save_ms = (perf_counter() - started) * 1000.0
    window._acquisition_state_requested_at = None


def schedule_acquisition_state_persist_for(window) -> None:
    schedule_acquisition_state_persist(window)


def set_ui_state_autosave_enabled_for(window, enabled: bool) -> None:
    window._ui_state_persistence_enabled = bool(enabled)
    window._ui_state_autosave_enabled = bool(enabled)
    save_app_setting("ui_state_autosave_enabled", window._ui_state_persistence_enabled)
    timer = getattr(window, "_ui_state_timer", None)
    if not window._ui_state_persistence_enabled and timer is not None:
        timer.stop()
        window._ui_state_requested_at = None
    state_text = "enabled" if window._ui_state_persistence_enabled else "disabled"
    window._log_info(f"UI layout persistence {state_text}.")


def set_acquisition_state_autosave_enabled_for(window, enabled: bool) -> None:
    window._acquisition_state_autosave_enabled = bool(enabled)
    save_app_setting("acquisition_state_autosave_enabled", window._acquisition_state_autosave_enabled)
    timer = getattr(window, "_acquisition_state_timer", None)
    if not window._acquisition_state_autosave_enabled and timer is not None:
        timer.stop()
        window._acquisition_state_requested_at = None
    state_text = "enabled" if window._acquisition_state_autosave_enabled else "disabled"
    window._log_info(f"Acquisition state autosave {state_text}.")


def set_log_buffering_enabled_for(window, enabled: bool) -> None:
    window._log_buffering_enabled = bool(enabled)
    save_app_setting("log_buffering_enabled", window._log_buffering_enabled)
    buffer_timer = getattr(window, "_log_buffer_timer", None)
    if not window._log_buffering_enabled and buffer_timer is not None:
        if buffer_timer.isActive() or getattr(window, "_log_buffer", None):
            window._flush_log_buffer()
        buffer_timer.stop()
        window._log_buffer_requested_at = None
    state_text = "enabled" if window._log_buffering_enabled else "disabled"
    window._log_info(f"Log buffering {state_text}.")


def ensure_experiment_control_panel_for(window) -> None:
    try:
        if window._experiment_control_window is None:
            from lspr_app.gui.experiment_control_window import ExperimentControlWindow

            profile = launch_profile_settings(window)
            window._experiment_control_window = ExperimentControlWindow(
                window._experiment_control_window_ui_state,
                known_probe=window._discovered_pump_probe,
                theme_mode=window._theme_mode,
                initial_mswitch_devices=[probe for probe in window._initial_mswitch_devices if probe is not None],
                auto_connect_devices=profile.scan_devices,
                show_runtime_controls=profile.show_runtime_controls,
                parent=getattr(window, "_top_content_stack", window),
            )
            window._experiment_control_window.setVisible(False)
            window._experiment_control_window.availability_changed.connect(window._handle_flow_availability_changed)
            window._experiment_control_window.valve_availability_changed.connect(window._handle_valve_availability_changed)
            window._experiment_control_window.mswitch_availability_changed.connect(window._handle_mswitch_availability_changed)
            window._experiment_control_window.recording_control_requested.connect(window._handle_flow_recording_control)
            window._experiment_control_window.experimental_control_state_recorded.connect(window._handle_experimental_control_state_recorded)
            window._experiment_control_window.recording_controller = window
            window._experiment_control_window.theme_changed.connect(window.set_theme)
            window._experiment_control_window.set_theme(str(getattr(window, "_theme_mode", "dark")))
            if hasattr(window._experiment_control_window, "_set_record_with_flow_recording_active"):
                window._experiment_control_window._set_record_with_flow_recording_active(bool(window._measurement_active))
            window._experiment_control_window._ui_startup_ready = bool(getattr(window, "_ui_startup_ready", False))
            header_label = getattr(window._experiment_control_window, "_experiment_control_header_label", None)
            if header_label is not None:
                header_label.installEventFilter(window)
            sync_experiment_control_startup_ports_for(window)
            if hasattr(window, "_top_content_stack"):
                placeholder = getattr(window, "_experiment_control_panel_placeholder", getattr(window, "_flow_panel_placeholder", None))
                if placeholder is not None:
                    index = window._top_content_stack.indexOf(placeholder)
                    if index >= 0:
                        placeholder.hide()
                        window._top_content_stack.removeWidget(placeholder)
                        placeholder.setParent(None)
                        window._top_content_stack.insertWidget(index, window._experiment_control_window)
                    else:
                        window._top_content_stack.addWidget(window._experiment_control_window)
                ensure_visible_top_content_splitter(window, mode=getattr(window, "_top_view_mode", "spectra"))
            pending_layout_preset = str(getattr(window, "_pending_layout_preset_selected", "") or "").strip().lower()
            if pending_layout_preset == "measurement":
                from lspr_app.gui.main_window_state import apply_layout_preset

                def _replay_selected_layout_preset() -> None:
                    try:
                        if hasattr(window, "_log_info"):
                            window._log_info("Replaying restored measurement preset after experiment control panel creation.")
                        apply_layout_preset(window, "measurement", save=False)
                    except Exception as exc:
                        if hasattr(window, "_log_warning"):
                            window._log_warning(f"Deferred measurement preset replay failed: {exc}")

                QTimer.singleShot(0, _replay_selected_layout_preset)
            window._log_info("Experiment control panel created.")
    except Exception as exc:
        window._log_error(f"Experiment control panel creation failed: {exc}")
        window._log_error(traceback.format_exc().rstrip())


def ensure_flow_panel_for(window) -> None:
    ensure_experiment_control_panel_for(window)


def sync_experiment_control_startup_ports_for(window) -> None:
    try:
        hardware_init_ready = bool(object.__getattribute__(window, "_hardware_init_ready_emitted"))
    except Exception:
        hardware_init_ready = False
    if not hardware_init_ready:
        return
    profile = launch_profile_settings(window)
    if not bool(getattr(profile, "scan_devices", False)):
        return
    experiment_control_window = getattr(window, "_experiment_control_window", None)
    if experiment_control_window is None:
        return
    if hasattr(experiment_control_window, "enable_startup_device_auto_connect"):
        try:
            experiment_control_window.enable_startup_device_auto_connect()
        except Exception as exc:
            window._log_warning(f"Could not enable experiment-control startup auto-connect: {exc}")
    if not hasattr(experiment_control_window, "refresh_device_ports"):
        return
    try:
        refreshed = bool(experiment_control_window.refresh_device_ports())
    except Exception as exc:
        window._log_warning(f"Could not refresh experiment-control ports after initialization: {exc}")
    else:
        if refreshed:
            window._log_info("Refreshing experiment-control ports after initialization.")


def disconnect_all_devices_for(window) -> None:
    experiment_control_window = getattr(window, "_experiment_control_window", None)
    if experiment_control_window is None or not hasattr(experiment_control_window, "shutdown_devices"):
        window._log_info("No hardware controller window is available to disconnect.")
        return
    window._log_info("Disconnecting all hardware devices.")
    try:
        experiment_control_window.shutdown_devices()
    except Exception as exc:
        window._log_warning(f"Disconnect all devices failed: {exc}")
        return
    refresh_hw_device_status_strip(window)
    window._log_info("All hardware devices disconnected.")


def sync_hardware_menu_actions_for(window) -> None:
    action = getattr(window, "_hw_disconnect_all_action", None)
    if action is None:
        return
    action.setEnabled(window._hardware_init_task is None)


def finish_hardware_initialization_for(window, text: str = "Hardware initialization scan finished.") -> None:
    window.status_label.setText(text)
    if window._hardware_init_ready_emitted:
        return
    window._hardware_init_ready_emitted = True
    window._hardware_status_overrides.clear()
    refresh_hw_device_status_strip(window)
    window._sync_hardware_menu_actions()
    window._emit_hardware_init_progress(100, text)
    window._set_startup_loading_indicator(False)
    window.hardware_init_finished.emit()


def start_hardware_initialization_for(window) -> None:
    if window._hardware_init_task is not None:
        return
    experiment_control_window = getattr(window, "_experiment_control_window", None)
    if experiment_control_window is not None and hasattr(experiment_control_window, "shutdown_devices"):
        window._log_info("Resetting live hardware connections before reinitialization.")
        try:
            experiment_control_window.shutdown_devices()
        except Exception as exc:
            window._log_warning(f"Could not fully reset live hardware connections: {exc}")
    window._emit_hardware_init_progress(12, "Scanning connected devices...")
    window._sync_hardware_menu_actions()
    task = HardwareInitTask(window._hardware_init_steps())
    task.signals.progress.connect(window._emit_hardware_init_progress)
    task.signals.step.connect(window._handle_hardware_init_step)
    task.signals.finished.connect(window._handle_hardware_init_finished)
    window._hardware_init_task = task
    window._thread_pool.start(task)


def handle_hardware_init_step_for(window, result: object) -> None:
    if not isinstance(result, HardwareInitStepResult):
        return
    window._hardware_status_overrides[result.key] = (bool(result.connected), result.message)
    window.status_label.setText(result.message)
    key = result.key
    if key == "spectrometer":
        payload = result.payload
        if payload is not None and not isinstance(payload, SimulatedSpectrometer):
            # Real spectrometer discovered on background thread — swap it in now,
            # before live acquisition starts, so no restart is needed.
            window._spectrometer = payload
            window._capabilities = payload.capabilities()
        window._hardware_available = not isinstance(window._spectrometer, SimulatedSpectrometer)
    elif key.startswith("pump"):
        if result.probe is not None:
            window._discovered_pump_probe = result.probe
            window._update_pump_status(result.probe)
    elif key.startswith("selector") or key == "mswitch":
        window._initial_mswitch_devices = list(result.payload or [])
        window._mswitch_probe = result.probe if result.probe is not None else None
    elif key.startswith("valve"):
        if result.probe is not None:
            window._discovered_valve_probe = result.probe
    refresh_hw_device_status_strip(window)


def handle_hardware_init_finished_for(window, result: object) -> None:
    window._hardware_init_task = None
    if getattr(window, "_closing", False):
        return
    if not isinstance(result, HardwareInitResult):
        window._log_warning("Hardware initialization finished with an unexpected result payload.")
        finish_hardware_initialization_for(window, "Hardware initialization finished.")
        return
    if result.pump_probe is not None:
        window._discovered_pump_probe = result.pump_probe
        window._update_pump_status(result.pump_probe)
        window._log_info(f"Pump controller discovered on {result.pump_probe.port}.")
    elif result.pump_error:
        window._log_warning(f"Pump controller scan completed with no usable device ({result.pump_error}).")
    else:
        window._log_warning("No pump controller discovered.")

    if result.spectrometer_name:
        if isinstance(window._spectrometer, SimulatedSpectrometer):
            window._log_info(result.spectrometer_name)
        else:
            window._log_success(result.spectrometer_name)
    else:
        window._log_warning("Spectrometer initialization produced no name.")

    window._initial_mswitch_devices = list(result.selector_devices)
    window._mswitch_probe = result.selector_devices[0] if result.selector_devices else None
    refresh_hw_device_status_strip(window)
    if window._mswitch_probe is not None:
        window._log_info(f"Selector discovered on {window._mswitch_probe.port}.")
    elif result.selector_error:
        window._log_warning(f"Selector scan failed: {result.selector_error}")
    else:
        window._log_warning("Selector not discovered at startup.")

    if result.valve_probe is not None:
        window._log_info(f"Valve controller discovered on {result.valve_probe.port}.")
    elif result.valve_error:
        window._log_warning(f"Valve controller scan failed: {result.valve_error}")
    else:
        window._log_warning("No valve controller discovered.")

    window._emit_hardware_init_progress(100, "Hardware initialization complete.")
    finish_hardware_initialization_for(window, "Hardware initialization complete.")
    sync_experiment_control_startup_ports_for(window)


def close_event_for(window, event) -> None:  # pragma: no cover - GUI runtime path
    """Perform all cleanup before the window closes.

    The caller (``MainWindow.closeEvent``) is responsible for calling
    ``super().closeEvent(event)`` and ``QApplication.instance().quit()``
    after this function returns.
    """
    if (
        getattr(window, "_measurement_active", False)
        and getattr(window, "_confirm_exit_if_recording", True)
    ):
        from PyQt6.QtWidgets import QMessageBox
        answer = QMessageBox.question(
            window,
            "Recording in progress",
            "A measurement recording is currently active.\nClose the application and stop recording?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            event.ignore()
            return

    window._closing = True
    window._log_info("Closing application.")
    settings_dialog = getattr(window, "_sensorgram_settings_dialog", None)
    if settings_dialog is not None:
        try:
            settings_dialog.close()
        except Exception:
            pass
        window._sensorgram_settings_dialog = None
    window._acquisition_state_timer.stop()
    window._ui_state_timer.stop()
    window._persist_acquisition_state()
    window._save_ui_state()
    if window._experiment_control_window is not None:
        try:
            window._experiment_control_window.shutdown_devices()
        except Exception as exc:
            window._log_warning(f"Experimental control device shutdown failed: {exc}")
        window._experiment_control_window.save_ui_state()
        ecw = window._experiment_control_window
        try:
            ecw.availability_changed.disconnect(window._handle_flow_availability_changed)
            ecw.valve_availability_changed.disconnect(window._handle_valve_availability_changed)
            ecw.mswitch_availability_changed.disconnect(window._handle_mswitch_availability_changed)
            ecw.recording_control_requested.disconnect(window._handle_flow_recording_control)
            ecw.experimental_control_state_recorded.disconnect(window._handle_experimental_control_state_recorded)
            ecw.theme_changed.disconnect(window.set_theme)
        except RuntimeError:
            pass
        ecw.close()
    window._pending_manual_kind = None
    window._pending_source_mode = None
    window._pending_auto_integration = False
    window._resume_live_after_manual = False
    window._resume_live_after_source_switch = False
    window._resume_live_after_auto_integration = False
    window._live_active = False
    window._simulation_refresh_timer.stop()
    window._ui_task_scheduler.clear()
    window._ui_heartbeat_timer.stop()
    window._live_stop_event.set()
    if window._live_worker is not None and window._live_worker.is_alive():
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
            window._live_processing_worker.stop()
            window._live_processing_worker.join(timeout=2.0)
            if window._live_processing_worker.is_alive():
                window._log_warning("Live processing worker did not exit cleanly; terminating it.")
                window._live_processing_worker.terminate()
                window._live_processing_worker.join(timeout=1.0)
        except Exception as exc:
            window._log_warning(f"Live processing worker shutdown failed: {exc}")
    window._live_processing_worker = None
    window._reset_live_accumulator()
    if window._measurement_active:
        window._stop_measurement_run()
    elif window._measurement_writer is not None:
        window._flush_measurement_frames(force=True)
        window._measurement_writer.close()
        window._measurement_writer = None
    from lspr_app.storage.measurement_archive import close_session_writer
    close_session_writer(window)
    window._busy = False
    try:
        window._thread_pool.waitForDone(3000)
    except TypeError:
        window._thread_pool.waitForDone()
    try:
        log_handler = getattr(window, "_log_handler", None)
        if log_handler is not None:
            window._ui_logger.removeHandler(log_handler)
    except Exception:
        pass


def event_filter_for(window, obj, event) -> bool | None:
    """Handle watched-object events for the main window.

    Returns ``True`` to consume the event, or ``None`` to fall through to
    ``super().eventFilter(obj, event)`` (the caller is responsible for that
    call).
    """
    if obj is getattr(window, "project_destination_edit", None):
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            window._choose_recording_project_destination()
            return True
    if obj is getattr(window, "trace_stats_label", None):
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            window._cycle_trace_stats_metric()
            return True
    if obj is getattr(window, "_processed_spectra_header_label", None):
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
            window._activate_experimental_control_view()
            return True
    experiment_header = getattr(getattr(window, "_experiment_control_window", None), "_experiment_control_header_label", None)
    if obj is experiment_header:
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
            window._activate_spectra_view()
            return True
    if event.type() == QEvent.Type.Paint:
        startup_painted = False
        startup_widget_label = None
        if obj is getattr(window, "_main_content_widget", None):
            startup_widget_label = "main_content"
            startup_painted = True
        elif obj is getattr(window, "_spectra_block", None):
            startup_widget_label = "spectra_block"
            startup_painted = True
        elif obj is getattr(window, "_sensorgram_block", None):
            startup_widget_label = "sensorgram_block"
            startup_painted = True
        else:
            spectrum_viewport = getattr(getattr(window, "spectrum_plot", None), "viewport", None)
            if spectrum_viewport is not None and obj is spectrum_viewport():
                startup_widget_label = "spectrum_viewport"
                startup_painted = True
            else:
                trace_viewport = getattr(getattr(window, "trace_plot", None), "viewport", None)
                if trace_viewport is not None and obj is trace_viewport():
                    startup_widget_label = "trace_viewport"
                    startup_painted = True
        if startup_painted:
            startup_show_t0 = getattr(window, "_startup_show_requested_t0", None)
            if startup_show_t0 is not None:
                startup_elapsed_ms = (perf_counter() - startup_show_t0) * 1000.0
                if not getattr(window, "_startup_widget_paint_reported", False):
                    window._startup_widget_paint_reported = set()
                painted = getattr(window, "_startup_widget_paint_reported", set())
                if startup_widget_label not in painted:
                    painted.add(startup_widget_label)
                    window._startup_widget_paint_reported = painted
                    window._log_info(
                        f"Startup +{startup_elapsed_ms:.1f} ms: first paint on {startup_widget_label}"
                    )
    spectrum_viewport = getattr(getattr(window, "spectrum_plot", None), "viewport", None)
    if spectrum_viewport is not None and obj is spectrum_viewport():
        pass  # reserved for future viewport-level event handling
    if obj is window._title_bar_widget:
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
            window._toggle_window_max_restore()
            return True
        if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            window._title_bar_drag_active = True
            window._title_bar_drag_offset = event.globalPosition().toPoint() - window.frameGeometry().topLeft()
            return True
        if event_type == QEvent.Type.MouseMove and window._title_bar_drag_active:
            if not window.isMaximized() and event.buttons() & Qt.MouseButton.LeftButton:
                window.move(event.globalPosition().toPoint() - window._title_bar_drag_offset)
            return True
        if event_type in {QEvent.Type.MouseButtonRelease, QEvent.Type.Leave}:
            window._title_bar_drag_active = False
    return None
