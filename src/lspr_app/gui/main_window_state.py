from __future__ import annotations

from PyQt6.QtCore import Qt, QRect
from PyQt6.QtWidgets import QApplication
from time import perf_counter

from lspr_app.domain.pump_plan import to_core_experiment_plan
from lspr_app.storage.app_config import save_acquisition_state, save_window_ui_state
from lspr_core import LAUNCH_PROFILE_CONTROL_EDITOR, LAUNCH_PROFILE_FULL, LAUNCH_PROFILE_SIMULATION


def restore_ui_state(window) -> None:
    ui_state = window._ui_state
    if not ui_state:
        return

    width = ui_state.get("width")
    height = ui_state.get("height")
    x_pos = ui_state.get("x")
    y_pos = ui_state.get("y")
    splitter_sizes = ui_state.get("splitter_sizes")
    plot_splitter_sizes = ui_state.get("plot_splitter_sizes")
    sensorgram_header_splitter_sizes = ui_state.get("sensorgram_header_splitter_sizes")
    session_stats_splitter_sizes = ui_state.get("session_stats_splitter_sizes")
    maximized = ui_state.get("maximized")
    top_view_mode = ui_state.get("top_view_mode")
    sensorgram_view_mode = ui_state.get("sensorgram_view_mode")
    sensorgram_content_mode = ui_state.get("sensorgram_content_mode")
    sensorgram_time_axis_mode = ui_state.get("sensorgram_time_axis_mode")
    trace_display_window_s = ui_state.get("trace_display_window_s")
    metric_display_points = ui_state.get("metric_display_points")
    sensorgram_compression_recent_tail_points = ui_state.get("sensorgram_compression_recent_tail_points")
    metric_autoscale_follow_latest_buffer_fraction = ui_state.get("metric_autoscale_follow_latest_buffer_fraction")
    metric_autoscale_min_interval_s = ui_state.get("metric_autoscale_min_interval_s")
    metric_autoscale_throttle_mode = ui_state.get("metric_autoscale_throttle_mode")
    metric_autoscale_skip_tiny_changes_enabled = ui_state.get("metric_autoscale_skip_tiny_changes_enabled")
    sensorgram_metric_envelope_overlay_enabled = ui_state.get("sensorgram_metric_envelope_overlay_enabled")
    sensorgram_metric_envelope_overlay_alpha = ui_state.get("sensorgram_metric_envelope_overlay_alpha")
    sensorgram_line_mode = ui_state.get("sensorgram_line_mode")
    plot_antialias_enabled = ui_state.get("plot_antialias_enabled")
    sensorgram_heatmap_history_max_rows = ui_state.get("sensorgram_heatmap_history_max_rows")
    sensorgram_frozen = ui_state.get("sensorgram_frozen")
    left_controls_visible = ui_state.get("left_controls_visible")
    sensorgram_visible = ui_state.get("sensorgram_visible")
    trace_stats_metric_name = ui_state.get("trace_stats_metric_name")
    residual_y_range = ui_state.get("residual_y_range")
    residual_visible = bool(ui_state.get("show_residual", False))

    if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
        app = QApplication.instance()
        if app:
            screen_geometry = app.primaryScreen().availableGeometry()
            margin = 12
            width = min(width, max(screen_geometry.width() - margin * 2, 640))
            height = min(height, max(screen_geometry.height() - margin * 2, 480))
        window.resize(width, height)
    if isinstance(x_pos, int) and isinstance(y_pos, int):
        app = QApplication.instance()
        if app:
            screen_geometry = app.primaryScreen().availableGeometry()
            margin = 12
            window_rect = QRect(x_pos, y_pos, window.width(), window.height())
            if not screen_geometry.intersects(window_rect):
                x_pos = screen_geometry.x() + margin
                y_pos = screen_geometry.y() + margin
            else:
                x_pos = min(
                    max(x_pos, screen_geometry.x() + margin),
                    screen_geometry.x() + screen_geometry.width() - window.width() - margin,
                )
                y_pos = min(
                    max(y_pos, screen_geometry.y() + margin),
                    screen_geometry.y() + screen_geometry.height() - window.height() - margin,
                )
        window.move(x_pos, y_pos)
    if (
        isinstance(splitter_sizes, list)
        and len(splitter_sizes) == 2
        and all(isinstance(item, int) and item > 0 for item in splitter_sizes)
    ):
        window.left_right_splitter.setSizes(splitter_sizes)
    if (
        isinstance(plot_splitter_sizes, list)
        and len(plot_splitter_sizes) == 2
        and all(isinstance(item, int) and item > 0 for item in plot_splitter_sizes)
    ):
        window.plot_splitter.setSizes(plot_splitter_sizes)
    if (
        isinstance(sensorgram_header_splitter_sizes, list)
        and len(sensorgram_header_splitter_sizes) == 2
        and all(isinstance(item, int) and item > 0 for item in sensorgram_header_splitter_sizes)
    ):
        window.sensorgram_header_splitter.setSizes(sensorgram_header_splitter_sizes)
    if (
        isinstance(session_stats_splitter_sizes, list)
        and len(session_stats_splitter_sizes) == 2
        and all(isinstance(item, int) and item > 0 for item in session_stats_splitter_sizes)
        and getattr(window, "session_stats_splitter", None) is not None
    ):
        window.session_stats_splitter.setSizes(session_stats_splitter_sizes)
    if isinstance(top_view_mode, str) and top_view_mode in {"spectra", "flow"}:
        if top_view_mode == "flow":
            window._activate_flow_view()
        else:
            window._activate_spectra_view()
    if isinstance(left_controls_visible, bool):
        window._left_controls_scroll.setVisible(left_controls_visible)
    if isinstance(sensorgram_visible, bool):
        window._sensorgram_block.setVisible(sensorgram_visible)
    if isinstance(trace_stats_metric_name, str) and trace_stats_metric_name:
        window._trace_stats_metric_name = trace_stats_metric_name
    if isinstance(sensorgram_view_mode, str):
        window._sensorgram_view_mode = window._normalize_sensorgram_view_mode(sensorgram_view_mode)
        window._update_sensorgram_view_mode_button()
    if isinstance(sensorgram_content_mode, str):
        window._sensorgram_content_mode = window._normalize_sensorgram_content_mode(sensorgram_content_mode)
        window._apply_sensorgram_content_mode(save=False)
    if isinstance(sensorgram_time_axis_mode, str):
        normalized_time_axis_mode = str(sensorgram_time_axis_mode).strip().lower()
        if normalized_time_axis_mode not in {"elapsed", "clock"}:
            normalized_time_axis_mode = "elapsed"
        window._sensorgram_time_axis_mode = normalized_time_axis_mode
        if hasattr(window, "_apply_sensorgram_time_axis_mode"):
            window._apply_sensorgram_time_axis_mode(redraw=False)
    if isinstance(trace_display_window_s, (int, float)) and float(trace_display_window_s) > 0:
        window._trace_display_window_s = window._normalize_sensorgram_display_window_s(trace_display_window_s)
    if isinstance(metric_display_points, (int, float)) and int(metric_display_points) > 0:
        window._plot_display_points = max(int(metric_display_points), 1)
    if isinstance(sensorgram_compression_recent_tail_points, (int, float)) and int(sensorgram_compression_recent_tail_points) >= 0:
        window._sensorgram_compression_recent_tail_points = int(sensorgram_compression_recent_tail_points)
    if isinstance(metric_autoscale_follow_latest_buffer_fraction, (int, float)):
        window._metric_autoscale_follow_latest_buffer_fraction = max(float(metric_autoscale_follow_latest_buffer_fraction), 0.0)
    if isinstance(metric_autoscale_min_interval_s, (int, float)):
        window._metric_autoscale_min_interval_s = max(float(metric_autoscale_min_interval_s), 0.0)
    if isinstance(metric_autoscale_throttle_mode, str) and metric_autoscale_throttle_mode:
        window._metric_autoscale_throttle_mode = metric_autoscale_throttle_mode
    if isinstance(metric_autoscale_skip_tiny_changes_enabled, (bool, str)):
        if isinstance(metric_autoscale_skip_tiny_changes_enabled, bool):
            window._metric_autoscale_skip_tiny_changes_enabled = bool(metric_autoscale_skip_tiny_changes_enabled)
        else:
            window._metric_autoscale_skip_tiny_changes_enabled = str(metric_autoscale_skip_tiny_changes_enabled).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
    if isinstance(sensorgram_metric_envelope_overlay_enabled, (bool, str)):
        if isinstance(sensorgram_metric_envelope_overlay_enabled, bool):
            window._sensorgram_metric_envelope_overlay_enabled = bool(sensorgram_metric_envelope_overlay_enabled)
        else:
            window._sensorgram_metric_envelope_overlay_enabled = str(sensorgram_metric_envelope_overlay_enabled).strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
        envelope_bands = getattr(window, "trace_metric_envelope_bands", None)
        if isinstance(envelope_bands, dict):
            for band in envelope_bands.values():
                if hasattr(band, "setVisible"):
                    band.setVisible(bool(window._sensorgram_metric_envelope_overlay_enabled))
    if isinstance(sensorgram_metric_envelope_overlay_alpha, (int, float)):
        window._sensorgram_metric_envelope_overlay_alpha = max(min(int(sensorgram_metric_envelope_overlay_alpha), 100), 0)
    if isinstance(sensorgram_line_mode, str):
        window._sensorgram_line_step_mode = window._normalize_sensorgram_line_mode(sensorgram_line_mode)
    if isinstance(plot_antialias_enabled, (bool, str)):
        if isinstance(plot_antialias_enabled, bool):
            window._plot_antialias_enabled = bool(plot_antialias_enabled)
        else:
            window._plot_antialias_enabled = str(plot_antialias_enabled).strip().lower() in {"1", "true", "yes", "on"}
        import pyqtgraph as pg

        pg.setConfigOptions(antialias=window._plot_antialias_enabled)
    if isinstance(sensorgram_heatmap_history_max_rows, (int, float)) and float(sensorgram_heatmap_history_max_rows) > 0:
        window._sensorgram_heatmap_history_max_rows = int(max(int(sensorgram_heatmap_history_max_rows), 16))
    if isinstance(sensorgram_frozen, bool):
        window._sensorgram_frozen = bool(sensorgram_frozen)
        window._update_sensorgram_freeze_button_icon()
    if (
        isinstance(residual_y_range, list)
        and len(residual_y_range) == 2
        and all(isinstance(item, (int, float)) for item in residual_y_range)
    ):
        y_min = float(residual_y_range[0])
        y_max = float(residual_y_range[1])
        if y_max > y_min:
            window._residual_y_range = [y_min, y_max]
            if residual_visible and hasattr(window, "residual_view"):
                window.residual_view.setYRange(y_min, y_max, padding=0.0)
                window._residual_axis_autoscaled = True
    window._start_maximized = bool(maximized)
    window._sync_view_actions()


def save_ui_state(window) -> None:
    if window.isMaximized():
        geometry = window.normalGeometry()
        width = geometry.width()
        height = geometry.height()
        x_pos = geometry.x()
        y_pos = geometry.y()
    else:
        width = window.width()
        height = window.height()
        x_pos = window.x()
        y_pos = window.y()

    residual_y_range: list[float] = []
    if hasattr(window, "residual_view") and window.residual_view.isVisible():
        try:
            current_range = window.residual_view.viewRange()[1]
            y_min = float(current_range[0])
            y_max = float(current_range[1])
            if y_max > y_min:
                residual_y_range = [y_min, y_max]
        except Exception:
            residual_y_range = []
    elif isinstance(getattr(window, "_residual_y_range", None), list):
        saved_range = window._residual_y_range
        if len(saved_range) == 2 and all(isinstance(item, (int, float)) for item in saved_range):
            residual_y_range = [float(saved_range[0]), float(saved_range[1])]

    save_window_ui_state(
        "main_window",
        {
            "x": int(x_pos),
            "y": int(y_pos),
            "width": int(width),
            "height": int(height),
            "maximized": bool(window.isMaximized()),
            "splitter_sizes": [int(size) for size in window.left_right_splitter.sizes()],
            "plot_splitter_sizes": [int(size) for size in window.plot_splitter.sizes()],
            "sensorgram_header_splitter_sizes": [int(size) for size in window.sensorgram_header_splitter.sizes()],
            "session_stats_splitter_sizes": [int(size) for size in window.session_stats_splitter.sizes()]
            if hasattr(window, "session_stats_splitter") and window.session_stats_splitter is not None
            else [],
            "top_view_mode": window._top_view_mode,
            "sensorgram_view_mode": window._sensorgram_view_mode,
            "sensorgram_content_mode": window._sensorgram_content_mode,
            "sensorgram_time_axis_mode": str(getattr(window, "_sensorgram_time_axis_mode", "elapsed")),
            "trace_display_window_s": float(window._trace_display_window_s),
            "metric_display_points": int(getattr(window, "_plot_display_points", 512)),
            "sensorgram_compression_recent_tail_points": int(
                getattr(window, "_sensorgram_compression_recent_tail_points", 300)
            ),
            "metric_autoscale_follow_latest_buffer_fraction": float(
                getattr(window, "_metric_autoscale_follow_latest_buffer_fraction", 0.05)
            ),
            "metric_autoscale_min_interval_s": float(getattr(window, "_metric_autoscale_min_interval_s", 1.0)),
            "metric_autoscale_throttle_mode": str(getattr(window, "_metric_autoscale_throttle_mode", "Medium")),
            "metric_autoscale_skip_tiny_changes_enabled": bool(
                getattr(window, "_metric_autoscale_skip_tiny_changes_enabled", True)
            ),
            "sensorgram_metric_envelope_overlay_enabled": bool(
                getattr(window, "_sensorgram_metric_envelope_overlay_enabled", False)
            ),
            "sensorgram_metric_envelope_overlay_alpha": int(
                getattr(window, "_sensorgram_metric_envelope_overlay_alpha", 16)
            ),
            "sensorgram_line_mode": "linear" if getattr(window, "_sensorgram_line_step_mode", None) is None else str(window._sensorgram_line_step_mode),
            "plot_antialias_enabled": bool(getattr(window, "_plot_antialias_enabled", False)),
            "sensorgram_heatmap_history_max_rows": int(getattr(window, "_sensorgram_heatmap_history_max_rows", 800)),
            "sensorgram_frozen": bool(getattr(window, "_sensorgram_frozen", False)),
            "left_controls_visible": window._left_controls_scroll.isVisible(),
            "sensorgram_visible": window._sensorgram_block.isVisible(),
            "trace_stats_metric_name": window._trace_stats_metric_name,
            "residual_y_range": residual_y_range,
            "collapsible_sections": collapsible_section_state(window),
        },
    )


def collapsible_section_state(window) -> dict[str, bool]:
    sections: dict[str, bool] = {}
    for key, attr in (
        ("source", "_source_section"),
        ("processing", "_processing_section"),
        ("session", "_session_section"),
        ("log", "_log_section"),
    ):
        section = getattr(window, attr, None)
        if section is not None:
            sections[key] = bool(section.is_expanded())
    return sections


def restore_collapsible_section_state(window) -> None:
    state = window._ui_state if isinstance(window._ui_state, dict) else {}
    saved = state.get("collapsible_sections")
    if not isinstance(saved, dict):
        return
    for key, attr in (
        ("source", "_source_section"),
        ("processing", "_processing_section"),
        ("session", "_session_section"),
        ("log", "_log_section"),
    ):
        section = getattr(window, attr, None)
        value = saved.get(key)
        if section is not None and isinstance(value, bool):
            section.set_expanded(value)


def acquisition_state_payload(window) -> dict[str, object]:
    acquisition = window._current_settings()
    simulation = window._simulation_backend.simulation_parameters()
    experiment_control_payload = (
        window._experiment_control_window.switch_solution_hdf5_payload()
        if window._experiment_control_window is not None
        else {
            "switch_solution_mode": False,
            "switch_solution_labels": [f"Solution {index}" for index in range(1, 13)],
            "switch_solution_rows": [[str(index), f"Solution {index}"] for index in range(1, 13)],
            "valve_state_labels": {"Open": "Open", "Close": "Close"},
            "valve_state_colors": {"Open": "#4E79A7", "Close": "#B44A4A"},
        }
    )
    if window._experiment_control_window is not None:
        try:
            experiment_control_payload["plan_rows"] = window._experiment_control_window.current_pump_plan_hdf5_rows()
            experiment_control_payload["selected_plan_row"] = window._experiment_control_window._selected_experiment_control_row()
        except Exception:
            pass
    return {
        "source_mode": window._source_mode,
        "plot_mode": window.plot_selector.currentText(),
        "live_rate_hz": float(window.live_rate_spin.value()),
        "show_residual": bool(window.show_residual_button.isChecked()),
        "freeze_plots": bool(window.freeze_plots_button.isChecked()),
        "acquisition": {
            "integration_time_ms": float(acquisition.integration_time_ms),
            "averages": int(acquisition.averages),
            "correct_dark_counts": bool(window.correct_dark_check.isChecked()),
            "correct_nonlinearity": bool(window.correct_nonlinearity_check.isChecked()),
        },
        "simulation": {
            "peak_center_nm": float(simulation.peak_center_nm),
            "peak_width_nm": float(simulation.peak_width_nm),
            "peak_height": float(simulation.peak_height),
            "secondary_peak_offset_nm": float(simulation.secondary_peak_offset_nm),
            "secondary_peak_height_percent": float(simulation.secondary_peak_height_percent),
            "secondary_peak_width_percent": float(simulation.secondary_peak_width_percent),
            "baseline": float(simulation.baseline),
            "slope": float(simulation.slope * 100.0),
            "noise": float(simulation.noise),
            "wavelength_resolution_nm": float(simulation.wavelength_resolution_nm),
            "output_rate_hz": float(window.sim_output_rate_spin.value()),
        },
        "experiment_control": experiment_control_payload,
    }


def resolve_initial_source_mode(window, requested_source_mode: str) -> str:
    profile = getattr(window, "_launch_profile_spec", None)
    profile_key = str(getattr(profile, "key", "") or "").strip().lower()
    requested = str(requested_source_mode or "").strip().lower()
    hardware_available = bool(getattr(window, "_hardware_available", False))

    if profile_key == LAUNCH_PROFILE_SIMULATION:
        return "simulation"
    if profile_key == LAUNCH_PROFILE_FULL:
        return "spectrometer" if hardware_available else "simulation"
    if profile_key == LAUNCH_PROFILE_CONTROL_EDITOR:
        if requested in {"spectrometer", "simulation"}:
            return requested
        return "spectrometer" if hardware_available else "simulation"

    if requested in {"spectrometer", "simulation"}:
        if requested == "spectrometer" and not hardware_available:
            return "simulation"
        return requested
    return "spectrometer" if hardware_available else "simulation"


def persist_acquisition_state(window) -> None:
    if window._suspend_acquisition_autosave or not getattr(window, "_acquisition_state_autosave_enabled", True):
        return
    payload = acquisition_state_payload(window)
    window._acquisition_state = payload
    save_acquisition_state(payload)
    if window._measurement_writer is not None:
        writer_payload = dict(payload)
        experiment_control = dict(payload.get("experiment_control", {}))
        if window._experiment_control_window is not None:
            try:
                experiment_control["experiment_plan"] = to_core_experiment_plan(
                    window._experiment_control_window._read_experiment_control_steps()
                )
                experiment_control["plan_rows"] = window._experiment_control_window.current_pump_plan_hdf5_rows()
                experiment_control["selected_plan_row"] = window._experiment_control_window._selected_experiment_control_row()
            except Exception:
                pass
        writer_payload["experiment_control"] = experiment_control
        window._measurement_writer.update_acquisition_state(writer_payload)


def schedule_acquisition_state_persist(window) -> None:
    if window._suspend_acquisition_autosave or not getattr(window, "_acquisition_state_autosave_enabled", True):
        timer = getattr(window, "_acquisition_state_timer", None)
        if timer is not None:
            timer.stop()
        return
    window._acquisition_state_requested_at = perf_counter()


def apply_acquisition_state_to_widgets(window, state: dict[str, object]) -> None:
    if not state:
        window._update_dark_reference_button_icons()
        window._update_freeze_button_icon()
        window._update_residual_button_icon()
        return

    window._suspend_acquisition_autosave = True
    try:
        plot_mode = str(state.get("plot_mode", "Sample"))
        if plot_mode in window.PLOT_MODES:
            window.plot_selector.blockSignals(True)
            window.plot_selector.setCurrentText(plot_mode)
            window.plot_selector.blockSignals(False)

        live_rate_hz = state.get("live_rate_hz")
        if isinstance(live_rate_hz, (int, float)) and float(live_rate_hz) > 0:
            window.live_rate_spin.setValue(float(live_rate_hz))

        source_mode = resolve_initial_source_mode(window, str(state.get("source_mode", window._source_mode)))

        acquisition = state.get("acquisition", {})
        if isinstance(acquisition, dict):
            integration_time_ms = acquisition.get("integration_time_ms")
            if isinstance(integration_time_ms, (int, float)) and float(integration_time_ms) > 0:
                window.integration_spin.setValue(float(integration_time_ms))
            averages = acquisition.get("averages")
            if isinstance(averages, int) and averages > 0:
                window.averages_spin.setValue(averages)
            if isinstance(acquisition.get("correct_dark_counts"), bool):
                window.correct_dark_check.setChecked(bool(acquisition["correct_dark_counts"]))
            if isinstance(acquisition.get("correct_nonlinearity"), bool):
                window.correct_nonlinearity_check.setChecked(bool(acquisition["correct_nonlinearity"]))

        simulation = state.get("simulation", {})
        if isinstance(simulation, dict):
            peak_center_nm = simulation.get("peak_center_nm")
            if isinstance(peak_center_nm, (int, float)):
                window.sim_peak_center_slider.setValue(int(round(float(peak_center_nm))))
            peak_width_nm = simulation.get("peak_width_nm")
            if isinstance(peak_width_nm, (int, float)):
                window.sim_peak_width_slider.setValue(int(round(float(peak_width_nm))))
            peak_height = simulation.get("peak_height")
            if isinstance(peak_height, (int, float)):
                window.sim_peak_height_slider.setValue(int(round(float(peak_height))))
            secondary_peak_offset_nm = simulation.get("secondary_peak_offset_nm")
            if isinstance(secondary_peak_offset_nm, (int, float)):
                window.sim_secondary_peak_offset_slider.setValue(int(round(float(secondary_peak_offset_nm))))
            secondary_peak_height_percent = simulation.get("secondary_peak_height_percent")
            if isinstance(secondary_peak_height_percent, (int, float)):
                window.sim_secondary_peak_height_slider.setValue(int(round(float(secondary_peak_height_percent))))
            secondary_peak_width_percent = simulation.get("secondary_peak_width_percent")
            if isinstance(secondary_peak_width_percent, (int, float)):
                window.sim_secondary_peak_width_slider.setValue(int(round(float(secondary_peak_width_percent))))
            baseline = simulation.get("baseline")
            if isinstance(baseline, (int, float)):
                window.sim_baseline_slider.setValue(int(round(float(baseline))))
            slope = simulation.get("slope")
            if isinstance(slope, (int, float)):
                window.sim_slope_slider.setValue(int(round(float(slope))))
            noise = simulation.get("noise")
            if isinstance(noise, (int, float)):
                window.sim_noise_slider.setValue(int(round(float(noise))))
            wavelength_resolution_nm = simulation.get("wavelength_resolution_nm")
            if isinstance(wavelength_resolution_nm, (int, float)) and float(wavelength_resolution_nm) > 0:
                window.sim_resolution_spin.setValue(float(wavelength_resolution_nm))
            output_rate_hz = simulation.get("output_rate_hz")
            if isinstance(output_rate_hz, (int, float)) and float(output_rate_hz) > 0:
                window.sim_output_rate_spin.setValue(float(output_rate_hz))

        if source_mode == "simulation":
            window.source_tabs.blockSignals(True)
            window.source_tabs.setCurrentIndex(1)
            window.source_tabs.blockSignals(False)
            window._sync_simulation_backend_from_controls()
        else:
            window.source_tabs.blockSignals(True)
            window.source_tabs.setCurrentIndex(0)
            window.source_tabs.blockSignals(False)
        window._apply_source_mode(source_mode if source_mode in {"spectrometer", "simulation"} else "spectrometer", restart_live=False)

        residual_visible = bool(state.get("show_residual", False))
        window.show_residual_button.blockSignals(True)
        window.show_residual_button.setChecked(residual_visible)
        window.show_residual_button.blockSignals(False)
        window._update_residual_axis_visibility(residual_visible)
        window._update_residual_button_icon()

        frozen = bool(state.get("freeze_plots", False))
        window.freeze_plots_button.blockSignals(True)
        window.freeze_plots_button.setChecked(frozen)
        window.freeze_plots_button.blockSignals(False)
        window._set_plots_frozen(frozen)

        window._update_dark_reference_button_icons()
        window._update_window_mode_label()
    finally:
        window._suspend_acquisition_autosave = False
    schedule_acquisition_state_persist(window)

