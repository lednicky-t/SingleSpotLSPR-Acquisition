from __future__ import annotations

import traceback

from functools import partial

from time import perf_counter

from PyQt6.QtCore import QEvent, Qt, QTimer
from PyQt6.QtWidgets import QApplication

from lspr_app.device.device_lifecycle import (
    DEVICE_ORDER,
    DeviceLifecycleController,
    DeviceLifecycleEvent,
    DeviceLifecycleReport,
)
from lspr_app.device.device_types import PUMP, SELECTOR, SWITCH
from lspr_app.device.simulated import SimulatedSpectrometer
from lspr_app.gui.device_lifecycle_task import DeviceDisconnectTask, DeviceLifecycleCycleTask, device_io_pool
from lspr_app.gui.main_window_state import (
    acquisition_state_payload,
    apply_source_mode_for,
    collapsible_section_state,
    launch_profile_settings,
    persist_acquisition_state,
    ensure_visible_top_content_splitter,
    restore_collapsible_section_state,
    restore_ui_state,
    save_ui_state,
    schedule_acquisition_state_persist,
)
from lspr_app.gui.main_window_headers import update_source_link_buttons
from lspr_app.gui.main_window_titlebar import refresh_hw_device_status_strip
from lspr_app.storage.app_config import save_app_setting


def restore_ui_state_for(window) -> None:
    window._restoring_ui_state = True
    try:
        restore_ui_state(window)
    finally:
        window._restoring_ui_state = False


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
                undo_stack=getattr(window, "undo_stack", None),
                device_manager_settings=getattr(window, "_device_manager_settings", None),
            )
            window._experiment_control_window.setVisible(False)
            window._experiment_control_window.availability_changed.connect(window._handle_flow_availability_changed)
            window._experiment_control_window.valve_availability_changed.connect(window._handle_valve_availability_changed)
            window._experiment_control_window.mswitch_availability_changed.connect(window._handle_mswitch_availability_changed)
            window._experiment_control_window.recording_control_requested.connect(window._handle_flow_recording_control)
            window._experiment_control_window.experimental_control_state_recorded.connect(window._handle_experimental_control_state_recorded)
            # partial(), not a lambda: disconnect() below needs the exact same
            # callable object back, which a fresh lambda can't provide.
            window._hw_status_refresh_slot = partial(refresh_hw_device_status_strip, window)
            window._experiment_control_window.hw_status_refresh_requested.connect(window._hw_status_refresh_slot)
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
    """Mirror DeviceLifecycleController's already-finished connection state
    into the experiment-control panel's internal probe/client state and its
    availability_changed signals.

    Pure UI/state sync - by the time this runs (after _hardware_init_ready_emitted
    is set), the controller has already discovered/connected/homed every
    device; this does no device I/O of its own and never re-triggers a
    connect. Called both from handle_hardware_init_finished_for (the normal
    startup path) and from ensure_experiment_control_panel_for (in case the
    panel is constructed after hardware-init has already finished).
    """
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
    if not hasattr(experiment_control_window, "sync_from_lifecycle_controller"):
        return
    try:
        experiment_control_window.sync_from_lifecycle_controller()
    except Exception as exc:
        window._log_warning(f"Could not sync experiment-control panel from device lifecycle state: {exc}")


def disconnect_all_devices_for(window) -> None:
    window._log_info("Disconnecting all hardware devices.")
    try:
        DeviceLifecycleController.shared().shutdown_all()
    except Exception as exc:
        window._log_warning(f"Disconnect all devices failed: {exc}")
        return
    experiment_control_window = getattr(window, "_experiment_control_window", None)
    if experiment_control_window is not None and hasattr(experiment_control_window, "sync_from_lifecycle_controller"):
        try:
            experiment_control_window.sync_from_lifecycle_controller()
        except Exception as exc:
            window._log_warning(f"Could not sync experiment-control panel after disconnect: {exc}")
    refresh_hw_device_status_strip(window)
    window._log_info("All hardware devices disconnected.")


def apply_device_enablement_for(window, enabled: dict[str, bool]) -> None:
    """Apply a new pump/switch/selector enabled-set from the "Hardware devices..."
    dialog. Persists immediately, hides/shows the titlebar items right away
    (no device I/O needed for that), disconnects anything newly turned off,
    and re-runs the normal hardware scan if anything was newly turned on so
    it can be picked up without an app restart."""
    controller = DeviceLifecycleController.shared()
    previous = controller.enabled_devices()
    controller.set_enabled_devices(enabled)
    refresh_hw_device_status_strip(window)
    # Unconditional, not just as a side effect of the disconnect task below:
    # a device can be disabled while not currently connected at all (never
    # plugged in, or the scan just hasn't reached it yet), in which case no
    # disconnect task ever runs - but the Experiment Control panel's
    # device-specific controls (e.g. the Switch column) must still hide
    # based on the enabled/disabled *setting* itself, independent of
    # whether anything was ever connected.
    _sync_experiment_control_panel_after_enablement_change(window)

    newly_disabled = [
        key for key in DEVICE_ORDER
        if previous.get(key, True) and not controller.is_device_type_enabled(key) and controller.is_connected(key)
    ]
    newly_enabled = [
        key for key in DEVICE_ORDER
        if not previous.get(key, True) and controller.is_device_type_enabled(key)
    ]

    for device_key in newly_disabled:
        task = DeviceDisconnectTask(device_key)
        task.signals.finished.connect(lambda _event, w=window: _after_device_enablement_disconnect(w))
        device_io_pool().start(task)

    if newly_enabled:
        window._start_hardware_initialization()


def _sync_experiment_control_panel_after_enablement_change(window) -> None:
    experiment_control_window = getattr(window, "_experiment_control_window", None)
    if experiment_control_window is not None and hasattr(experiment_control_window, "sync_from_lifecycle_controller"):
        try:
            experiment_control_window.sync_from_lifecycle_controller()
        except Exception as exc:
            window._log_warning(f"Could not sync experiment-control panel after device enablement change: {exc}")


def _after_device_enablement_disconnect(window) -> None:
    refresh_hw_device_status_strip(window)
    _sync_experiment_control_panel_after_enablement_change(window)


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
    refresh_hw_device_status_strip(window)
    window._sync_hardware_menu_actions()
    window._emit_hardware_init_progress(100, text)
    window._set_startup_loading_indicator(False)
    window.hardware_init_finished.emit()


def start_hardware_initialization_for(window) -> None:
    if window._hardware_init_task is not None:
        return
    window._log_info("Resetting live hardware connections before reinitialization.")
    try:
        DeviceLifecycleController.shared().shutdown_all()
    except Exception as exc:
        window._log_warning(f"Could not fully reset live hardware connections: {exc}")
    window._emit_hardware_init_progress(12, "Scanning connected devices...")
    window._sync_hardware_menu_actions()
    task = DeviceLifecycleCycleTask(DeviceLifecycleController.shared)
    task.signals.device_event.connect(window._handle_hardware_init_step)
    task.signals.cycle_finished.connect(window._handle_hardware_init_finished)
    window._hardware_init_task = task
    device_io_pool().start(task)


def handle_hardware_init_step_for(window, event: object) -> None:
    if not isinstance(event, DeviceLifecycleEvent):
        return
    window.status_label.setText(event.message)
    if not event.terminal:
        # Non-terminal stages ("connecting on COM4...", "homing...") only
        # update the status text - final state (probes, status overrides)
        # is applied once the terminal event for this device arrives. Also
        # record it as this device's "what's happening right now" text so
        # the titlebar status strip can show it next to the relevant dot.
        window._device_activity_text[event.device_key] = event.message
        refresh_hw_device_status_strip(window)
        return
    window._device_activity_text.pop(event.device_key, None)
    key = event.device_key
    if key == "spectrometer":
        # .probe carries the live instance here (see device_lifecycle.py's
        # run_spectrometer_stage), not an identity dataclass like the other
        # devices - swap it in now, before live acquisition starts, so no
        # restart is needed.
        payload = event.probe
        found_real_device = payload is not None and not isinstance(payload, SimulatedSpectrometer)
        if found_real_device:
            window._spectrometer = payload
            window._capabilities = payload.capabilities()
        window._hardware_available = not isinstance(window._spectrometer, SimulatedSpectrometer)
        # Repaint the Spectrometer/Simulation tab header link icons now - this
        # used to only happen incidentally (e.g. locking/unlocking the UI for
        # a measurement), so the icon stayed on its stale startup state
        # (gray) until something else happened to trigger a repaint, even
        # though _hardware_available had already flipped to True here.
        update_source_link_buttons(window)
        # Auto-connect to real hardware. The maintainer's policy: no
        # spectrometer at launch -> stay on simulation (already the case -
        # see main_window.py's construction-time fallback); spectrometer
        # found during the initial startup scan (see MainWindow._init_runtime_state's
        # _initial_hardware_scan_pending and handle_hardware_init_finished_for
        # below, which clears it once that first scan completes) -> always
        # switch to it automatically; spectrometer connected later (e.g. via
        # "Reinitialize hardware" after already running a while) -> only
        # switch automatically if the tool panel (source tabs) is currently
        # visible - a reasonable proxy for "the user is actively looking at
        # source selection right now", vs. a silent background surprise
        # while they're deliberately using simulation with the panel tucked
        # away.
        initial_scan = getattr(window, "_initial_hardware_scan_pending", False)
        left_controls = getattr(window, "_left_controls_scroll", None)
        tool_panel_visible = bool(left_controls is not None and left_controls.isVisible())
        if (
            found_real_device
            and window._source_mode != "spectrometer"
            and (initial_scan or tool_panel_visible)
        ):
            apply_source_mode_for(window, "spectrometer", restart_live=True)
    elif key == PUMP:
        if event.probe is not None:
            window._discovered_pump_probe = event.probe
            window._update_pump_status(event.probe)
    elif key == SELECTOR:
        window._initial_mswitch_devices = [event.probe] if event.probe is not None else []
        window._mswitch_probe = event.probe
    elif key == SWITCH:
        if event.probe is not None:
            window._discovered_valve_probe = event.probe
    refresh_hw_device_status_strip(window)


def handle_hardware_init_finished_for(window, report: object) -> None:
    window._hardware_init_task = None
    if getattr(window, "_closing", False):
        return

    # Discovery is now fully finished - the spectrum plot showed nothing
    # while scanning (see refresh_spectrum_plot_for's _device_discovery_complete
    # gate); render the real verdict now. Only the *initial* startup scan
    # forces the plot mode - a later "Reinitialize hardware" rescan doesn't
    # (see handle_hardware_init_step_for's own initial_scan-only auto-switch
    # for the matching source-mode rule).
    was_initial_scan = bool(getattr(window, "_initial_hardware_scan_pending", False))
    window._device_discovery_complete = True
    if was_initial_scan and window._hardware_available and hasattr(window, "plot_selector"):
        window.plot_selector.setCurrentText("Raw")
    window._refresh_plot()

    if not isinstance(report, DeviceLifecycleReport):
        window._log_warning("Hardware initialization finished with an unexpected result payload.")
        finish_hardware_initialization_for(window, "Hardware initialization finished.")
        return

    pump_event = report.by_device.get(PUMP)
    if pump_event is not None and pump_event.connected and pump_event.probe is not None:
        window._discovered_pump_probe = pump_event.probe
        window._update_pump_status(pump_event.probe)
        window._log_info(f"Pump controller discovered on {pump_event.probe.port}.")
    elif pump_event is not None and pump_event.error:
        window._log_warning(f"Pump controller scan completed with no usable device ({pump_event.error}).")
    else:
        window._log_warning("No pump controller discovered.")

    spectrometer_event = report.by_device.get("spectrometer")
    if spectrometer_event is not None:
        if isinstance(window._spectrometer, SimulatedSpectrometer):
            window._log_info(spectrometer_event.message)
        else:
            window._log_success(spectrometer_event.message)
    else:
        window._log_warning("Spectrometer initialization produced no result.")

    selector_event = report.by_device.get(SELECTOR)
    if selector_event is not None and selector_event.connected and selector_event.probe is not None:
        window._initial_mswitch_devices = [selector_event.probe]
        window._mswitch_probe = selector_event.probe
    else:
        window._initial_mswitch_devices = []
        window._mswitch_probe = None
    refresh_hw_device_status_strip(window)
    if window._mswitch_probe is not None:
        window._log_info(f"Selector discovered on {window._mswitch_probe.port}.")
    elif selector_event is not None and selector_event.error:
        window._log_warning(f"Selector scan failed: {selector_event.error}")
    else:
        window._log_warning("Selector not discovered at startup.")

    valve_event = report.by_device.get(SWITCH)
    if valve_event is not None and valve_event.connected and valve_event.probe is not None:
        window._log_info(f"Valve controller discovered on {valve_event.probe.port}.")
    elif valve_event is not None and valve_event.error:
        window._log_warning(f"Valve controller scan failed: {valve_event.error}")
    else:
        window._log_warning("No valve controller discovered.")

    # Mirror the panel's connection state directly, here, before calling
    # finish_hardware_initialization_for below. Historically (before the full
    # device lifecycle rewrite) this ordering avoided a stale-read health
    # check (synchronize_device_connections, since deleted) that keyed off the
    # panel's own _probe/_valve_probe/_mswitch_probe attributes. That specific
    # failure mode is gone now that refresh_hw_device_status_strip reads
    # DeviceLifecycleController directly - but the panel's own state
    # (self._probe, availability_changed signals) still needs this call to be
    # current before anything downstream reacts to hardware-init finishing, so
    # the ordering is kept. sync_experiment_control_startup_ports_for (below)
    # redundantly re-syncs once the ready flag is set - harmless (idempotent),
    # not yet worth removing.
    experiment_control_window = getattr(window, "_experiment_control_window", None)
    if experiment_control_window is not None and hasattr(experiment_control_window, "sync_from_lifecycle_controller"):
        try:
            experiment_control_window.sync_from_lifecycle_controller()
        except Exception as exc:
            window._log_warning(f"Could not sync experiment-control panel from device lifecycle state: {exc}")

    window._emit_hardware_init_progress(100, "Hardware initialization complete.")
    finish_hardware_initialization_for(window, "Hardware initialization complete.")
    sync_experiment_control_startup_ports_for(window)
    # The initial-scan window closes here, whether or not a spectrometer was
    # found - any later scan (Reinitialize hardware, or a device-enablement
    # change) must not auto-switch the active source. See
    # handle_hardware_init_step_for's use of this flag.
    window._initial_hardware_scan_pending = False


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
    try:
        DeviceLifecycleController.shared().shutdown_all()
    except Exception as exc:
        window._log_warning(f"Experimental control device shutdown failed: {exc}")
    if window._experiment_control_window is not None:
        window._experiment_control_window.save_ui_state()
        ecw = window._experiment_control_window
        try:
            ecw.availability_changed.disconnect(window._handle_flow_availability_changed)
            ecw.valve_availability_changed.disconnect(window._handle_valve_availability_changed)
            ecw.mswitch_availability_changed.disconnect(window._handle_mswitch_availability_changed)
            ecw.recording_control_requested.disconnect(window._handle_flow_recording_control)
            ecw.experimental_control_state_recorded.disconnect(window._handle_experimental_control_state_recorded)
            hw_status_refresh_slot = getattr(window, "_hw_status_refresh_slot", None)
            if hw_status_refresh_slot is not None:
                ecw.hw_status_refresh_requested.disconnect(hw_status_refresh_slot)
            ecw.theme_changed.disconnect(window.set_theme)
        except RuntimeError:
            pass
        ecw.close()
    window._pending_manual_kind = None
    window._pending_source_mode = None
    window._pending_auto_exposure_start = False
    auto_exposure_state = getattr(window, "_auto_exposure_state", None)
    if auto_exposure_state is not None:
        auto_exposure_state.active = False
    window._resume_live_after_manual = False
    window._resume_live_after_source_switch = False
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
    if obj is getattr(window, "spectrum_stats_label", None):
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
            window._toggle_spectrum_stats_enabled()
            return True
    if obj is getattr(window, "trace_stats_label", None):
        event_type = event.type()
        # trace_stats_label has two independent click behaviors: a single
        # left-click cycles the displayed metric, a double-click hides/shows
        # the whole stats box. Qt delivers a double-click as Press, Release,
        # DblClick, Release - so the first Press always arrives first and
        # would fire the single-click cycle as an unwanted side effect right
        # before the box gets hidden. Debounce it: delay the cycle action by
        # doubleClickInterval() and bump _trace_stats_click_token on every
        # click (single or double) so a stale delayed callback can tell it's
        # been superseded and skip itself - see
        # _handle_trace_stats_delayed_single_click.
        if event_type == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
            window._trace_stats_click_token = getattr(window, "_trace_stats_click_token", 0) + 1
            window._toggle_trace_stats_enabled()
            return True
        if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            window._trace_stats_click_token = getattr(window, "_trace_stats_click_token", 0) + 1
            token = window._trace_stats_click_token
            QTimer.singleShot(
                QApplication.doubleClickInterval(),
                lambda: window._handle_trace_stats_delayed_single_click(token),
            )
            return True
    if obj is getattr(window, "spectrum_cursor_label", None):
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            window._toggle_spectrum_cursor_enabled()
            return True
    if obj is getattr(window, "trace_cursor_label", None):
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            window._toggle_trace_cursor_enabled()
            return True
    if obj is getattr(window, "_processed_spectra_header_label", None):
        event_type = event.type()
        if event_type == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
            window._activate_experiment_control_view()
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
            menu_bar = getattr(window, "_menu_bar", None)
            if menu_bar is not None:
                # Double-clicking File/Edit/View/... (e.g. the second click of
                # a quick click-to-open-then-click-to-close on a menu) can
                # still land as a MouseButtonDblClick on title_widget itself
                # rather than the menu bar - check position, not just obj
                # identity, so only clicks outside the menu bar's own area
                # maximize/restore the window.
                #
                # X-only check, not a full rect containment: menu_bar's own
                # widget height is whatever QMenuBar's natural sizeHint is,
                # which can be a few pixels shorter than the title bar row it
                # sits in (it's vertically centered there, not stretched to
                # fill it - see build_title_bar's left_cluster). A double-
                # click landing within the menu's horizontal span but just
                # above/below its exact rect (easy to do - the whole row
                # reads as "the menu" to the eye) fell through this check
                # entirely and still maximized/restored the window, per the
                # maintainer's report. The menu bar can't usefully be
                # double-clicked at all (it only responds to single
                # press/release to open a menu), so treating its full
                # horizontal span as off-limits for the whole row's height
                # has no downside.
                local_to_menu_bar = menu_bar.mapFromGlobal(event.globalPosition().toPoint())
                if 0 <= local_to_menu_bar.x() <= menu_bar.width():
                    return None
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
