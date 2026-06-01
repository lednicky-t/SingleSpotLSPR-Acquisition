from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import numpy as np

from lspr_app.diagnostics import DiagnosticsConfig
from lspr_app.gui.runtime_probe import build_runtime_drift_lines_for


def _timing_plain_text(value: float | int | None) -> str:
    if value is None:
        return "-"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "-"
    if not np.isfinite(numeric):
        return "-"
    return f"{numeric:.1f} ms"


def _queue_depth_text(window: Any, attr_name: str) -> str:
    queue_obj = getattr(window, attr_name, None)
    if queue_obj is None:
        return "-"
    try:
        size = queue_obj.qsize()
    except (AttributeError, NotImplementedError, OSError):
        return "-"
    try:
        return str(max(int(size), 0))
    except (TypeError, ValueError):
        return "-"


def _queue_depth_max_text(value: object) -> str:
    if value is None:
        return "-"
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return "-"
    return str(max(numeric, 0))


def _format_rate(value: float | int | None) -> str:
    if value is None:
        return "-"
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return "-"
    if not np.isfinite(numeric):
        return "-"
    return f"{numeric:.2f} Hz"


def _measurement_runtime_text(window: Any) -> tuple[str, str]:
    started_at = getattr(window, "_measurement_started_at", None)
    if started_at is None:
        return "-", "-"
    elapsed_s = max((datetime.now(timezone.utc) - started_at).total_seconds(), 0.0)
    return f"{elapsed_s:.1f} s", f"{elapsed_s:.1f} s"


def _timing_line(label: str, value: float | int | None) -> str:
    return f"  {label}: {_timing_plain_text(value)}"


@dataclass(frozen=True, slots=True)
class SessionDiagnosticsSnapshot:
    diagnostics: DiagnosticsConfig
    display_rate_text: str
    simulation_rate_text: str
    actual_refresh_text: str
    skip_rate_text: str
    measurement_state_text: str
    current_runtime_text: str
    total_runtime_text: str
    ui_heartbeat_delay_text: str
    ui_heartbeat_max_delay_text: str
    ui_heartbeat_total_text: str
    ui_state_delay_text: str
    ui_state_save_text: str
    ui_state_total_text: str
    acquisition_state_delay_text: str
    acquisition_state_save_text: str
    acquisition_state_total_text: str
    session_stats_recording_delay_text: str
    session_stats_recording_snapshot_text: str
    session_stats_recording_total_text: str
    plot_refresh_total_text: str
    deferred_ui_total_text: str
    deferred_ui_live_estimate_text: str
    deferred_ui_telemetry_text: str
    deferred_ui_trace_plot_text: str
    deferred_ui_summary_text: str
    deferred_ui_stats_text: str
    session_summary_total_text: str
    session_stats_total_text: str
    scheduler_lag_text: str
    scheduler_duration_text: str
    scheduler_task_count_text: str
    scheduler_pending_text: str
    log_buffer_total_text: str
    gui_housekeeping_total_text: str
    processing_text: str
    wait_text: str
    headroom_text: str
    acquisition_latency_text: str
    acquisition_overhead_text: str
    frame_spacing_text: str
    source_rate_text: str
    dropped_frames_text: str
    trace_points_text: str
    trace_buffer_points_text: str
    heatmap_rows_text: str
    live_result_queue_text: str
    live_result_queue_max_text: str
    live_processed_queue_text: str
    live_processed_queue_max_text: str
    pipeline_gap_lines: list[str]
    spectrum_redraw_lines: list[str]
    device_acquisition_lines: list[str]
    runtime_drift_lines: list[str]

    @classmethod
    def from_window(cls, window: Any) -> "SessionDiagnosticsSnapshot":
        diagnostics = DiagnosticsConfig.from_window(window)
        skip_rate = window._live_skip_rate_hz() if hasattr(window, "_live_skip_rate_hz") else 0.0
        display_rate_text = f"{float(window.live_rate_spin.value()):.2f} Hz" if hasattr(window, "live_rate_spin") else "-"
        simulation_rate_text = "-"
        if hasattr(window, "sim_output_rate_spin"):
            try:
                simulation_rate_text = f"{float(window.sim_output_rate_spin.value()):.2f} Hz"
            except (TypeError, ValueError):
                simulation_rate_text = "-"
        actual_rate_text = "-"
        if getattr(window, "_actual_plot_refresh_rate_hz", None) is not None:
            window_s = float(getattr(window, "_plot_refresh_rate_window_s", 5.0))
            actual_rate_text = f"{float(window._actual_plot_refresh_rate_hz):.2f} Hz (recent {window_s:.1f}s avg)"
        current_runtime_text, total_runtime_text = _measurement_runtime_text(window)
        scheduler = getattr(window, "_ui_task_scheduler", None)
        scheduler_lag_text = _timing_plain_text(getattr(scheduler, "_last_dispatch_lag_ms", None))
        scheduler_duration_text = _timing_plain_text(getattr(scheduler, "_last_dispatch_duration_ms", None))
        scheduler_task_count_text = "-" if scheduler is None else str(int(getattr(scheduler, "_last_dispatch_task_count", 0)))
        scheduler_pending_text = "-" if scheduler is None else str(int(getattr(scheduler, "pending_count", lambda: 0)()))
        trace_points_text = "-"
        peak_history = getattr(window, "_peak_history", None)
        if peak_history:
            try:
                trace_points_text = str(max(len(buffer) for buffer in peak_history.values()))
            except ValueError:
                trace_points_text = "0"
        trace_buffer_points_text = "-"
        peak_history_buffers = getattr(window, "_peak_history_buffers", None)
        if peak_history_buffers:
            try:
                trace_buffer_points_text = str(max(len(buffer) for buffer in peak_history_buffers.values()))
            except ValueError:
                trace_buffer_points_text = "0"
        heatmap_rows_text = str(len(getattr(window, "_sensorgram_heatmap_history", []) or []))
        reference_ms = None
        spacing_ms = getattr(window, "_last_spacing_ms", None)
        if spacing_ms is not None and spacing_ms > 0:
            reference_ms = float(spacing_ms)
        elif getattr(window, "_last_elapsed_ms", None) is not None and getattr(window, "_last_elapsed_ms", None) > 0:
            reference_ms = float(getattr(window, "_last_elapsed_ms", None))
        acquisition_ms = getattr(window, "_last_elapsed_ms", None)
        live_result_delay_ms = getattr(window, "_last_live_result_poll_delay_ms", None)
        live_acquisition_flush_ms = getattr(window, "_last_live_acquisition_flush_ms", None)
        live_processed_delay_ms = getattr(window, "_last_live_processed_poll_delay_ms", None)
        live_processed_flush_ms = getattr(window, "_last_live_processed_flush_ms", None)
        stats_refresh_delay_ms = getattr(window, "_last_stats_refresh_delay_ms", None)
        summary_refresh_ms = getattr(window, "_last_summary_refresh_ms", None)
        session_stats_refresh_ms = getattr(window, "_last_session_stats_refresh_ms", None)
        log_buffer_delay_ms = getattr(window, "_last_log_buffer_delay_ms", None)
        log_buffer_flush_ms = getattr(window, "_last_log_buffer_flush_ms", None)
        processing_wait_ms = getattr(window, "_last_processing_queue_wait_ms", None)
        processing_ms = getattr(window, "_last_processing_ms", None)
        plot_refresh_delay_ms = getattr(window, "_last_plot_refresh_delay_ms", None)
        plot_render_ms = getattr(window, "_last_plot_refresh_ms", None)
        sensorgram_render_ms = getattr(window, "_last_sensorgram_render_ms", None)
        sensorgram_heatmap_render_ms = getattr(window, "_last_sensorgram_heatmap_render_ms", None)
        deferred_ui_ms = getattr(window, "_last_deferred_ui_refresh_ms", None)
        known_total_ms = sum(
            value
            for value in (
                acquisition_ms,
                live_result_delay_ms,
                live_acquisition_flush_ms,
                live_processed_delay_ms,
                live_processed_flush_ms,
                stats_refresh_delay_ms,
                summary_refresh_ms,
                session_stats_refresh_ms,
                log_buffer_delay_ms,
                log_buffer_flush_ms,
                processing_wait_ms,
                processing_ms,
                plot_refresh_delay_ms,
                plot_render_ms,
                sensorgram_render_ms,
                sensorgram_heatmap_render_ms,
                deferred_ui_ms,
            )
            if value is not None and value > 0
        )
        idle_ms = max(reference_ms - known_total_ms, 0.0) if reference_ms is not None else None
        pipeline_gap_lines = [
            _timing_line("Acquisition latency", acquisition_ms),
            _timing_line("Live acquisition timer delay", live_result_delay_ms),
            _timing_line("Live acquisition flush", live_acquisition_flush_ms),
            _timing_line("Live processing timer delay", live_processed_delay_ms),
            _timing_line("Live processing flush", live_processed_flush_ms),
            _timing_line("Stats refresh timer delay", stats_refresh_delay_ms),
            _timing_line("Session summary refresh", summary_refresh_ms),
            _timing_line("Session stats refresh", session_stats_refresh_ms),
            _timing_line("Log buffer timer delay", log_buffer_delay_ms),
            _timing_line("Log buffer flush", log_buffer_flush_ms),
            _timing_line("Processing queue wait", processing_wait_ms),
            _timing_line("Processing compute", processing_ms),
            _timing_line("Plot refresh timer delay", plot_refresh_delay_ms),
            _timing_line("Plot render", plot_render_ms),
            _timing_line("Sensorgram render", sensorgram_render_ms),
            _timing_line("Sensorgram heatmap render", sensorgram_heatmap_render_ms),
            _timing_line("Deferred UI flush", deferred_ui_ms),
            _timing_line("Unattributed / idle", idle_ms),
        ]
        spectrum_redraw_lines = [
            _timing_line("Curve update", getattr(window, "_last_spectrum_curve_update_ms", None)),
            _timing_line("Fit update", getattr(window, "_last_spectrum_fit_update_ms", None)),
            _timing_line("Marker update", getattr(window, "_last_spectrum_marker_update_ms", None)),
            _timing_line("Residual update", getattr(window, "_last_spectrum_residual_update_ms", None)),
        ]
        device_acquisition_lines = [
            f"  Acquisition latency: {_timing_plain_text(acquisition_ms)}",
            f"  Acquisition overhead: {_timing_plain_text(getattr(window, '_last_overhead_ms', None))}",
            f"  Frame spacing: {_timing_plain_text(getattr(window, '_last_spacing_ms', None))}",
            f"  Effective source rate: {_format_rate(getattr(window, '_effective_raw_rate_hz', None))}",
            f"  Dropped frames: {('-' if getattr(window, '_live_display_dropped_frames', None) is None else str(max(int(getattr(window, '_live_display_dropped_frames')), 0)))}",
        ]
        return cls(
            diagnostics=diagnostics,
            display_rate_text=display_rate_text,
            simulation_rate_text=simulation_rate_text,
            actual_refresh_text=actual_rate_text,
            skip_rate_text=f"{skip_rate:.1f} Hz",
            measurement_state_text="recording" if getattr(window, "_measurement_active", False) else "idle",
            current_runtime_text=current_runtime_text,
            total_runtime_text=total_runtime_text,
            ui_heartbeat_delay_text=_timing_plain_text(getattr(window, "_last_ui_heartbeat_delay_ms", None)),
            ui_heartbeat_max_delay_text=_timing_plain_text(getattr(window, "_ui_heartbeat_max_delay_ms", None)),
            ui_heartbeat_total_text=_timing_plain_text(getattr(window, "_last_ui_heartbeat_total_ms", None)),
            ui_state_delay_text=_timing_plain_text(getattr(window, "_last_ui_state_delay_ms", None)),
            ui_state_save_text=_timing_plain_text(getattr(window, "_last_ui_state_save_ms", None)),
            ui_state_total_text=_timing_plain_text(getattr(window, "_last_ui_state_total_ms", None)),
            acquisition_state_delay_text=_timing_plain_text(getattr(window, "_last_acquisition_state_delay_ms", None)),
            acquisition_state_save_text=_timing_plain_text(getattr(window, "_last_acquisition_state_save_ms", None)),
            acquisition_state_total_text=_timing_plain_text(getattr(window, "_last_acquisition_state_total_ms", None)),
            session_stats_recording_delay_text=_timing_plain_text(getattr(window, "_last_session_stats_recording_delay_ms", None)),
            session_stats_recording_snapshot_text=_timing_plain_text(getattr(window, "_last_session_stats_recording_snapshot_ms", None)),
            session_stats_recording_total_text=_timing_plain_text(getattr(window, "_last_session_stats_recording_total_ms", None)),
            plot_refresh_total_text=_timing_plain_text(getattr(window, "_last_plot_refresh_total_ms", None)),
            deferred_ui_total_text=_timing_plain_text(getattr(window, "_last_deferred_ui_refresh_total_ms", None)),
            deferred_ui_live_estimate_text=_timing_plain_text(getattr(window, "_last_deferred_ui_live_estimate_ms", None)),
            deferred_ui_telemetry_text=_timing_plain_text(getattr(window, "_last_deferred_ui_telemetry_ms", None)),
            deferred_ui_trace_plot_text=_timing_plain_text(getattr(window, "_last_deferred_ui_trace_plot_ms", None)),
            deferred_ui_summary_text=_timing_plain_text(getattr(window, "_last_deferred_ui_summary_ms", None)),
            deferred_ui_stats_text=_timing_plain_text(getattr(window, "_last_deferred_ui_stats_ms", None)),
            session_summary_total_text=_timing_plain_text(getattr(window, "_last_session_summary_refresh_total_ms", None)),
            session_stats_total_text=_timing_plain_text(getattr(window, "_last_session_stats_refresh_total_ms", None)),
            scheduler_lag_text=scheduler_lag_text,
            scheduler_duration_text=scheduler_duration_text,
            scheduler_task_count_text=scheduler_task_count_text,
            scheduler_pending_text=scheduler_pending_text,
            log_buffer_total_text=_timing_plain_text(getattr(window, "_last_log_buffer_total_ms", None)),
            gui_housekeeping_total_text=_timing_plain_text(getattr(window, "_last_gui_housekeeping_total_ms", None)),
            processing_text=_timing_plain_text(getattr(window, "_last_processing_ms", None)),
            wait_text=_timing_plain_text(getattr(window, "_last_processing_queue_wait_ms", None)),
            headroom_text=(
                "-"
                if getattr(window, "_processing_headroom_ratio", None) is None
                else f"{float(getattr(window, '_processing_headroom_ratio')):.2f}x"
            ),
            acquisition_latency_text=_timing_plain_text(getattr(window, "_last_elapsed_ms", None)),
            acquisition_overhead_text=_timing_plain_text(getattr(window, "_last_overhead_ms", None)),
            frame_spacing_text=_timing_plain_text(getattr(window, "_last_spacing_ms", None)),
            source_rate_text=_format_rate(getattr(window, "_effective_raw_rate_hz", None)),
            dropped_frames_text=(
                "-"
                if getattr(window, "_live_display_dropped_frames", None) is None
                else str(max(int(getattr(window, "_live_display_dropped_frames")), 0))
            ),
            trace_points_text=trace_points_text,
            trace_buffer_points_text=trace_buffer_points_text,
            heatmap_rows_text=heatmap_rows_text,
            live_result_queue_text=_queue_depth_text(window, "_live_result_queue"),
            live_result_queue_max_text=_queue_depth_max_text(getattr(window, "_live_result_queue_max_depth", None)),
            live_processed_queue_text=_queue_depth_text(window, "_live_processed_queue"),
            live_processed_queue_max_text=_queue_depth_max_text(getattr(window, "_live_processed_queue_max_depth", None)),
            pipeline_gap_lines=pipeline_gap_lines,
            spectrum_redraw_lines=spectrum_redraw_lines,
            device_acquisition_lines=device_acquisition_lines,
            runtime_drift_lines=build_runtime_drift_lines_for(window),
        )


def build_session_statistics_lines(snapshot: SessionDiagnosticsSnapshot) -> list[str]:
    lines = [
        "App",
        f"  Refresh rate: {snapshot.display_rate_text}",
        f"  Simulation output rate: {snapshot.simulation_rate_text}",
        f"  Recent refresh: {snapshot.actual_refresh_text}",
        f"  Frame skip rate: {snapshot.skip_rate_text}",
        f"  State: {snapshot.measurement_state_text}",
        f"  Runtime: {snapshot.current_runtime_text}",
        f"  Total runtime: {snapshot.total_runtime_text}",
        "",
        "UI event loop heartbeat",
        f"  Current delay: {snapshot.ui_heartbeat_delay_text}",
        f"  Max delay: {snapshot.ui_heartbeat_max_delay_text}",
        f"  Callback time: {snapshot.ui_heartbeat_total_text}",
        "",
        "Periodic callbacks",
        f"  UI state save delay: {snapshot.ui_state_delay_text}",
        f"  UI state save time: {snapshot.ui_state_save_text}",
        f"  UI state save total: {snapshot.ui_state_total_text}",
        f"  Acquisition state delay: {snapshot.acquisition_state_delay_text}",
        f"  Acquisition state save time: {snapshot.acquisition_state_save_text}",
        f"  Acquisition state save total: {snapshot.acquisition_state_total_text}",
        f"  Session stats snapshot delay: {snapshot.session_stats_recording_delay_text}",
        f"  Session stats snapshot time: {snapshot.session_stats_recording_snapshot_text}",
        f"  Session stats snapshot total: {snapshot.session_stats_recording_total_text}",
        "",
        "GUI callback wall time",
        f"  Plot refresh total: {snapshot.plot_refresh_total_text}",
        f"  Deferred UI total: {snapshot.deferred_ui_total_text}",
        f"  Deferred UI live estimate: {snapshot.deferred_ui_live_estimate_text}",
        f"  Deferred UI telemetry: {snapshot.deferred_ui_telemetry_text}",
        f"  Deferred UI metric plot: {snapshot.deferred_ui_trace_plot_text}",
        f"  Deferred UI summary: {snapshot.deferred_ui_summary_text}",
        f"  Deferred UI stats: {snapshot.deferred_ui_stats_text}",
        f"  Session summary total: {snapshot.session_summary_total_text}",
        f"  Session stats total: {snapshot.session_stats_total_text}",
        f"  Scheduler dispatch lag: {snapshot.scheduler_lag_text}",
        f"  Scheduler dispatch time: {snapshot.scheduler_duration_text}",
        f"  Scheduler dispatch tasks: {snapshot.scheduler_task_count_text}",
        f"  Scheduler pending tasks: {snapshot.scheduler_pending_text}",
        f"  Log buffer total: {snapshot.log_buffer_total_text}",
        f"  GUI housekeeping total: {snapshot.gui_housekeeping_total_text}",
        f"  Metric history points: {snapshot.trace_points_text}",
        f"  Metric display buffer points: {snapshot.trace_buffer_points_text}",
        f"  Heatmap rows: {snapshot.heatmap_rows_text}",
        f"  Live result queue: {snapshot.live_result_queue_text} | max: {snapshot.live_result_queue_max_text}",
        f"  Live processed queue: {snapshot.live_processed_queue_text} | max: {snapshot.live_processed_queue_max_text}",
        "",
        "Processing",
        f"  Time per spectrum: {snapshot.processing_text}",
        f"  Queue wait: {snapshot.wait_text}",
        f"  Headroom: {snapshot.headroom_text}",
        "",
        "Pipeline gap breakdown",
        *snapshot.pipeline_gap_lines,
        "",
        "Spectrum redraw breakdown",
        *snapshot.spectrum_redraw_lines,
        "",
        "Device acquisition",
        *snapshot.device_acquisition_lines,
        "",
        "Runtime drift probe",
        *snapshot.runtime_drift_lines,
    ]
    lines.extend(f"  {line}" for line in snapshot.diagnostics.summary_lines())
    return lines
