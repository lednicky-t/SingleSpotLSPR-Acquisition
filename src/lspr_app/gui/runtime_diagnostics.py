from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
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


def _metric_absolute_cache_modes_text(window: Any) -> str:
    cache = getattr(window, "_plot_view_cache", None)
    if cache is None:
        return "-"
    snapshot = getattr(cache, "metric_cache_debug_snapshot", None)
    if not callable(snapshot):
        return "-"
    try:
        cache_info = snapshot()
    except Exception:
        return "-"
    if not isinstance(cache_info, dict) or not cache_info:
        return "-"
    total_hit = 0
    total_incremental = 0
    total_rebuild = 0
    for entry in cache_info.values():
        if not isinstance(entry, dict):
            continue
        total_hit += int(entry.get("hits", 0) or 0)
        total_incremental += int(entry.get("incremental", 0) or 0)
        total_rebuild += int(entry.get("rebuilds", 0) or 0)
    return f"hit={total_hit} incremental={total_incremental} rebuild={total_rebuild}"


def _scheduler_visual_coalescing_text(window: Any) -> str:
    scheduler = getattr(window, "_ui_task_scheduler", None)
    if scheduler is None:
        return "-"
    mode = getattr(scheduler, "visual_coalescing_mode_text", None)
    return str(mode) if mode is not None else "-"


def _paint_summary_text(window: Any, prefix: str) -> str:
    count = int(getattr(window, f"_{prefix}_paint_count", 0) or 0)
    total_ms = float(getattr(window, f"_{prefix}_paint_total_ms", 0.0) or 0.0)
    max_ms = float(getattr(window, f"_{prefix}_paint_max_ms", 0.0) or 0.0)
    avg_ms = total_ms / count if count > 0 else 0.0
    return f"count={count} total={total_ms:.1f} ms max={max_ms:.1f} ms avg={avg_ms:.2f} ms"


def _recent_paint_summary_text(window: Any, prefix: str, seconds: float = 10.0) -> str:
    events = getattr(window, "_plot_paint_events", None)
    if not events:
        return "-"
    now = perf_counter()
    try:
        values = [elapsed for ts, name, elapsed in events if name == prefix and (now - float(ts)) <= float(seconds)]
    except Exception:
        return "-"
    if not values:
        return "count=0"
    total_ms = float(sum(values))
    count = len(values)
    return f"count={count} total={total_ms:.1f} ms max={max(values):.1f} ms avg={total_ms / count:.2f} ms"


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


def _scheduler_task_breakdown_lines(window: Any, *, limit: int = 5) -> list[str]:
    scheduler = getattr(window, "_ui_task_scheduler", None)
    if scheduler is None or not hasattr(scheduler, "task_dispatch_stats"):
        return ["  No scheduler task samples."]
    try:
        task_stats = scheduler.task_dispatch_stats()
    except Exception:
        return ["  No scheduler task samples."]
    if not task_stats:
        return ["  No scheduler task samples."]
    items = sorted(
        task_stats.items(),
        key=lambda item: (
            -float(item[1].last_lag_ms or 0.0),
            -float(item[1].max_duration_ms or 0.0),
            item[0],
        ),
    )
    lines: list[str] = []
    for task_name, stats in items[: max(int(limit), 1)]:
        lines.append(
            "  "
            f"{task_name}: count={stats.count} "
            f"last_lag={_timing_plain_text(stats.last_lag_ms)} "
            f"last_dur={_timing_plain_text(stats.last_duration_ms)} "
            f"max_lag={_timing_plain_text(stats.max_lag_ms)} "
            f"max_dur={_timing_plain_text(stats.max_duration_ms)}"
        )
    return lines


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
    deferred_display_total_text: str
    deferred_stats_total_text: str
    deferred_ui_live_estimate_text: str
    deferred_ui_telemetry_text: str
    deferred_ui_trace_plot_text: str
    deferred_ui_summary_text: str
    deferred_ui_stats_text: str
    session_summary_total_text: str
    session_stats_total_text: str
    gui_housekeeping_enabled_text: str
    housekeeping_log_buffer_text: str
    housekeeping_ui_state_text: str
    housekeeping_acquisition_state_text: str
    housekeeping_session_stats_snapshot_text: str
    housekeeping_session_stats_refresh_text: str
    scheduler_lag_text: str
    scheduler_duration_text: str
    scheduler_task_count_text: str
    scheduler_pending_text: str
    scheduler_task_breakdown_lines: list[str]
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
    raw_acquired_text: str
    raw_recording_enqueued_text: str
    raw_recording_written_text: str
    raw_recording_backpressure_text: str
    ui_preview_replaced_text: str
    ui_preview_displayed_text: str
    trace_points_text: str
    trace_buffer_points_text: str
    trace_raw_points_text: str
    trace_display_points_text: str
    trace_throttle_text: str
    metric_render_series_text: str
    metric_render_view_text: str
    metric_render_setdata_text: str
    metric_plot_disabled_fast_path_text: str
    metric_setdata_calls_text: str
    metric_setdata_skips_text: str
    metric_absolute_cache_modes_text: str
    scheduler_visual_coalescing_text: str
    metric_autoscale_text: str
    spectrum_plot_paint_summary_text: str
    trace_plot_paint_summary_text: str
    spectrum_plot_paint_recent_text: str
    trace_plot_paint_recent_text: str
    sensorgram_heatmap_arrays_text: str
    sensorgram_heatmap_image_text: str
    sensorgram_heatmap_axes_text: str
    heatmap_rows_text: str
    live_result_queue_text: str
    live_result_queue_max_text: str
    live_processed_queue_text: str
    live_processed_queue_max_text: str
    live_recording_queue_text: str
    live_recording_queue_max_text: str
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
        scheduler_task_breakdown_lines = _scheduler_task_breakdown_lines(window)
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
        trace_raw_points_text = "-"
        trace_display_points_text = "-"
        trace_throttle_text = "-"
        metric_render_series_text = "-"
        metric_render_view_text = "-"
        metric_render_setdata_text = "-"
        metric_autoscale_text = "-"
        sensorgram_heatmap_arrays_text = "-"
        sensorgram_heatmap_image_text = "-"
        sensorgram_heatmap_axes_text = "-"
        raw_acquired_text = str(max(int(getattr(window, "_raw_acquired_count", 0)), 0))
        raw_recording_enqueued_text = str(max(int(getattr(window, "_raw_recording_enqueued_count", 0)), 0))
        raw_recording_written_text = str(max(int(getattr(window, "_raw_recording_written_count", 0)), 0))
        raw_recording_backpressure_text = str(max(int(getattr(window, "_raw_recording_backpressure_count", 0)), 0))
        ui_preview_replaced_text = str(max(int(getattr(window, "_ui_preview_replaced_count", 0)), 0))
        ui_preview_displayed_text = str(max(int(getattr(window, "_ui_preview_displayed_count", 0)), 0))
        active_series = getattr(window, "_active_trace_series", None)
        if callable(active_series):
            try:
                series = active_series()
            except Exception:
                series = {}
            if isinstance(series, dict) and series:
                try:
                    trace_raw_points_text = str(
                        sum(len(np.asarray(values[0], dtype=np.float64)) for values in series.values())
                    )
                except Exception:
                    trace_raw_points_text = "-"
        trace_curves = getattr(window, "trace_curves", None)
        visible_points = 0
        if isinstance(trace_curves, dict):
            for curve in trace_curves.values():
                if hasattr(curve, "isVisible") and not bool(curve.isVisible()):
                    continue
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
                            x_values = y_values = None
                if x_values is None or y_values is None:
                    continue
                try:
                    visible_points += int(len(x_values))
                except Exception:
                    continue
        if visible_points > 0:
            trace_display_points_text = str(visible_points)
        else:
            visible_trace_x = getattr(window, "_visible_trace_x", None)
            if visible_trace_x is not None:
                try:
                    trace_display_points_text = str(len(visible_trace_x))
                except Exception:
                    trace_display_points_text = "-"
        last_metric_refresh_at = getattr(window, "_last_metric_plot_refresh_at", None)
        if last_metric_refresh_at is not None:
            try:
                elapsed_ms = max((perf_counter() - float(last_metric_refresh_at)) * 1000.0, 0.0)
            except (TypeError, ValueError):
                elapsed_ms = None
            if elapsed_ms is not None:
                min_interval_ms = float(getattr(window, "_metric_plot_refresh_min_interval_ms", 250.0))
                if bool(getattr(window, "_live_active", False)):
                    min_interval_ms = max(min_interval_ms, 500.0)
                remaining_ms = max(min_interval_ms - elapsed_ms, 0.0)
                trace_throttle_text = f"{remaining_ms:.1f} ms"
        metric_render_series_text = _timing_plain_text(
            getattr(window, "_last_metric_render_series_export_ms", getattr(window, "_last_metric_render_series_extract_ms", None))
        )
        metric_render_view_text = _timing_plain_text(getattr(window, "_last_metric_render_view_prep_ms", None))
        metric_render_setdata_text = _timing_plain_text(getattr(window, "_last_metric_render_setdata_ms", None))
        metric_plot_disabled_fast_path_text = "yes" if bool(getattr(window, "_last_metric_plot_disabled_fast_path", False)) else "no"
        metric_setdata_calls_text = _queue_depth_max_text(getattr(window, "_last_metric_render_setdata_calls", None))
        metric_setdata_skips_text = _queue_depth_max_text(getattr(window, "_last_metric_render_setdata_skips", None))
        metric_absolute_cache_modes_text = _metric_absolute_cache_modes_text(window)
        scheduler_visual_coalescing_text = _scheduler_visual_coalescing_text(window)
        metric_autoscale_text = _timing_plain_text(getattr(window, "_last_metric_autoscale_ms", None))
        spectrum_plot_paint_summary_text = _paint_summary_text(window, "spectrum_plot")
        trace_plot_paint_summary_text = _paint_summary_text(window, "trace_plot")
        spectrum_plot_paint_recent_text = _recent_paint_summary_text(window, "spectrum_plot")
        trace_plot_paint_recent_text = _recent_paint_summary_text(window, "trace_plot")
        sensorgram_heatmap_arrays_text = _timing_plain_text(getattr(window, "_last_sensorgram_heatmap_arrays_ms", None))
        sensorgram_heatmap_image_text = _timing_plain_text(getattr(window, "_last_sensorgram_heatmap_image_ms", None))
        sensorgram_heatmap_axes_text = _timing_plain_text(getattr(window, "_last_sensorgram_heatmap_axes_ms", None))
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
        display_refresh_delay_ms = getattr(window, "_last_display_refresh_delay_ms", None)
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
                display_refresh_delay_ms,
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
            _timing_line("Display refresh timer delay", display_refresh_delay_ms),
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
            deferred_display_total_text=_timing_plain_text(getattr(window, "_last_deferred_display_refresh_ms", None)),
            deferred_stats_total_text=_timing_plain_text(getattr(window, "_last_deferred_stats_refresh_ms", None)),
            deferred_ui_live_estimate_text=_timing_plain_text(getattr(window, "_last_deferred_ui_live_estimate_ms", None)),
            deferred_ui_telemetry_text=_timing_plain_text(getattr(window, "_last_deferred_ui_telemetry_ms", None)),
            deferred_ui_trace_plot_text=_timing_plain_text(getattr(window, "_last_deferred_ui_trace_plot_ms", None)),
            deferred_ui_summary_text=_timing_plain_text(getattr(window, "_last_deferred_ui_summary_ms", None)),
            deferred_ui_stats_text=_timing_plain_text(getattr(window, "_last_deferred_ui_stats_ms", None)),
            session_summary_total_text=_timing_plain_text(getattr(window, "_last_session_summary_refresh_total_ms", None)),
            session_stats_total_text=_timing_plain_text(getattr(window, "_last_session_stats_refresh_total_ms", None)),
            gui_housekeeping_enabled_text="enabled" if bool(getattr(window, "_gui_housekeeping_enabled", True)) else "disabled",
            housekeeping_log_buffer_text=_timing_plain_text(getattr(window, "_last_gui_housekeeping_log_buffer_ms", None)),
            housekeeping_ui_state_text=_timing_plain_text(getattr(window, "_last_gui_housekeeping_ui_state_ms", None)),
            housekeeping_acquisition_state_text=_timing_plain_text(getattr(window, "_last_gui_housekeeping_acquisition_state_ms", None)),
            housekeeping_session_stats_snapshot_text=_timing_plain_text(getattr(window, "_last_gui_housekeeping_session_stats_snapshot_ms", None)),
            housekeeping_session_stats_refresh_text=_timing_plain_text(getattr(window, "_last_gui_housekeeping_session_stats_refresh_ms", None)),
            scheduler_lag_text=scheduler_lag_text,
            scheduler_duration_text=scheduler_duration_text,
            scheduler_task_count_text=scheduler_task_count_text,
            scheduler_pending_text=scheduler_pending_text,
            scheduler_task_breakdown_lines=scheduler_task_breakdown_lines,
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
            raw_acquired_text=raw_acquired_text,
            raw_recording_enqueued_text=raw_recording_enqueued_text,
            raw_recording_written_text=raw_recording_written_text,
            raw_recording_backpressure_text=raw_recording_backpressure_text,
            ui_preview_replaced_text=ui_preview_replaced_text,
            ui_preview_displayed_text=ui_preview_displayed_text,
            trace_points_text=trace_points_text,
            trace_buffer_points_text=trace_buffer_points_text,
            trace_raw_points_text=trace_raw_points_text,
            trace_display_points_text=trace_display_points_text,
            trace_throttle_text=trace_throttle_text,
            metric_render_series_text=metric_render_series_text,
            metric_render_view_text=metric_render_view_text,
            metric_render_setdata_text=metric_render_setdata_text,
            metric_plot_disabled_fast_path_text=metric_plot_disabled_fast_path_text,
            metric_setdata_calls_text=metric_setdata_calls_text,
            metric_setdata_skips_text=metric_setdata_skips_text,
            metric_absolute_cache_modes_text=metric_absolute_cache_modes_text,
            scheduler_visual_coalescing_text=scheduler_visual_coalescing_text,
            metric_autoscale_text=metric_autoscale_text,
            spectrum_plot_paint_summary_text=spectrum_plot_paint_summary_text,
            trace_plot_paint_summary_text=trace_plot_paint_summary_text,
            spectrum_plot_paint_recent_text=spectrum_plot_paint_recent_text,
            trace_plot_paint_recent_text=trace_plot_paint_recent_text,
            sensorgram_heatmap_arrays_text=sensorgram_heatmap_arrays_text,
            sensorgram_heatmap_image_text=sensorgram_heatmap_image_text,
            sensorgram_heatmap_axes_text=sensorgram_heatmap_axes_text,
            heatmap_rows_text=heatmap_rows_text,
            live_result_queue_text=_queue_depth_text(window, "_live_result_queue"),
            live_result_queue_max_text=_queue_depth_max_text(getattr(window, "_live_result_queue_max_depth", None)),
            live_processed_queue_text=_queue_depth_text(window, "_live_processed_queue"),
            live_processed_queue_max_text=_queue_depth_max_text(getattr(window, "_live_processed_queue_max_depth", None)),
            live_recording_queue_text=_queue_depth_text(window, "_live_recording_queue"),
            live_recording_queue_max_text=_queue_depth_max_text(getattr(window, "_live_recording_queue_max_depth", None)),
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
        f"  Deferred display total: {snapshot.deferred_display_total_text}",
        f"  Deferred stats total: {snapshot.deferred_stats_total_text}",
        f"  Deferred UI live estimate: {snapshot.deferred_ui_live_estimate_text}",
        f"  Deferred UI telemetry: {snapshot.deferred_ui_telemetry_text}",
        f"  Deferred UI metric plot: {snapshot.deferred_ui_trace_plot_text}",
        f"  Deferred UI summary: {snapshot.deferred_ui_summary_text}",
        f"  Deferred UI stats: {snapshot.deferred_ui_stats_text}",
        f"  Session summary total: {snapshot.session_summary_total_text}",
        f"  Session stats total: {snapshot.session_stats_total_text}",
        f"  GUI housekeeping switch: {snapshot.gui_housekeeping_enabled_text}",
        f"  Scheduler dispatch lag: {snapshot.scheduler_lag_text}",
        f"  Scheduler dispatch time: {snapshot.scheduler_duration_text}",
        f"  Scheduler dispatch tasks: {snapshot.scheduler_task_count_text}",
        f"  Scheduler pending tasks: {snapshot.scheduler_pending_text}",
        f"  Log buffer total: {snapshot.log_buffer_total_text}",
        f"  GUI housekeeping total: {snapshot.gui_housekeeping_total_text}",
        "",
        "Scheduler task breakdown",
        *snapshot.scheduler_task_breakdown_lines,
        "",
        "GUI housekeeping breakdown",
        f"  Log buffer: {snapshot.housekeeping_log_buffer_text}",
        f"  UI state save: {snapshot.housekeeping_ui_state_text}",
        f"  Acquisition state save: {snapshot.housekeeping_acquisition_state_text}",
        f"  Session stats snapshot: {snapshot.housekeeping_session_stats_snapshot_text}",
        f"  Session stats refresh: {snapshot.housekeeping_session_stats_refresh_text}",
        f"  Raw acquired: {snapshot.raw_acquired_text}",
        f"  Raw recording enqueued/written: {snapshot.raw_recording_enqueued_text}/{snapshot.raw_recording_written_text}",
        f"  Raw recording backpressure: {snapshot.raw_recording_backpressure_text}",
        f"  UI preview replaced: {snapshot.ui_preview_replaced_text}",
        f"  UI preview displayed: {snapshot.ui_preview_displayed_text}",
        f"  Metric history points: {snapshot.trace_points_text}",
        f"  Metric display buffer points: {snapshot.trace_buffer_points_text}",
        f"  Metric raw points: {snapshot.trace_raw_points_text}",
        f"  Metric display points: {snapshot.trace_display_points_text}",
        f"  Metric plot throttle remaining: {snapshot.trace_throttle_text}",
        f"  Metric render series export: {snapshot.metric_render_series_text}",
        f"  Metric render view prep: {snapshot.metric_render_view_text}",
        f"  Metric render setData: {snapshot.metric_render_setdata_text}",
        f"  Metric plot disabled fast path: {snapshot.metric_plot_disabled_fast_path_text}",
        f"  Metric setData calls/skips: {snapshot.metric_setdata_calls_text}/{snapshot.metric_setdata_skips_text}",
        f"  Metric absolute cache modes: {snapshot.metric_absolute_cache_modes_text}",
        f"  Scheduler visual coalescing: {snapshot.scheduler_visual_coalescing_text}",
        f"  Metric autoscale: {snapshot.metric_autoscale_text}",
        f"  Spectrum paint: {snapshot.spectrum_plot_paint_summary_text}",
        f"  Trace paint: {snapshot.trace_plot_paint_summary_text}",
        f"  Spectrum paint recent 10s: {snapshot.spectrum_plot_paint_recent_text}",
        f"  Trace paint recent 10s: {snapshot.trace_plot_paint_recent_text}",
        f"  Sensorgram heatmap arrays: {snapshot.sensorgram_heatmap_arrays_text}",
        f"  Sensorgram heatmap image: {snapshot.sensorgram_heatmap_image_text}",
        f"  Sensorgram heatmap axes: {snapshot.sensorgram_heatmap_axes_text}",
        f"  Heatmap rows: {snapshot.heatmap_rows_text}",
        f"  Live result queue: {snapshot.live_result_queue_text} | max: {snapshot.live_result_queue_max_text}",
        f"  Live processed queue: {snapshot.live_processed_queue_text} | max: {snapshot.live_processed_queue_max_text}",
        f"  Live recording queue: {snapshot.live_recording_queue_text} | max: {snapshot.live_recording_queue_max_text}",
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
