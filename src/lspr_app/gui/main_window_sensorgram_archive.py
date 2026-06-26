from __future__ import annotations

from pathlib import Path

import numpy as np
import pyqtgraph as pg
from PyQt6.QtGui import QColor
from pyqtgraph import exporters as pg_exporters

from lspr_app.gui.workers import (
    MetricArchiveReloadRequest,
    MetricArchiveReloadResult,
    MetricArchiveReloadTask,
)
from lspr_app.gui.sensorgram_control_step_overlay import (
    normalize_sensorgram_control_step_overlay_color,
    normalize_sensorgram_control_step_overlay_label,
    resolve_sensorgram_control_step_overlay_palette_label,
)


def apply_sensorgram_display_style(window) -> None:
    for metric_name, curve in window.trace_curves.items():
        curve.setPen(pg.mkPen(window.SENSORGRAM_TIME_PLOT_COLORS.get(metric_name, "#1F77B4"), width=2.2))
    for metric_name, band in getattr(window, "trace_metric_envelope_bands", {}).items():
        base_color = QColor(window.SENSORGRAM_TIME_PLOT_COLORS.get(metric_name, "#1F77B4"))
        band_color = QColor(base_color)
        band_alpha = int(round(max(min(float(getattr(window, "_sensorgram_metric_envelope_overlay_alpha", 16)), 100.0), 0.0) * 2.55))
        band_color.setAlpha(max(min(band_alpha, 255), 0))
        band.setBrush(pg.mkBrush(band_color))


def sensorgram_metric_archive_reload_request_signature(window) -> tuple[object, ...] | None:
    archive_path = getattr(window, "_metric_archive_path", None)
    if archive_path is None:
        return None
    try:
        archive_path = str(Path(archive_path).expanduser())
    except Exception:
        archive_path = str(archive_path)
    selected_metrics = tuple(sorted(str(name) for name in window._selected_trace_metrics()))
    return (
        archive_path,
        int(getattr(window, "_source_epoch", 0)),
        selected_metrics,
        int(getattr(window, "_plot_display_points", 0)),
    )


def start_sensorgram_metric_archive_reload_task(window, request_token: tuple[object, ...]) -> None:
    archive_path = getattr(window, "_metric_archive_path", None)
    if archive_path is None:
        return
    archive_path = Path(archive_path).expanduser()
    window._sensorgram_metric_archive_reload_request_token = request_token
    window._sensorgram_metric_archive_reload_loading = True
    window._sensorgram_metric_archive_reload_pending_token = None
    task = MetricArchiveReloadTask(
        MetricArchiveReloadRequest(
            path=archive_path,
            source_epoch=int(getattr(window, "_source_epoch", 0)),
            request_token=request_token,
            metric_names=tuple(window._selected_trace_metrics()),
        )
    )
    window._sensorgram_metric_archive_reload_task = task
    task.signals.finished.connect(window._handle_sensorgram_metric_archive_reload_result)
    task.signals.failed.connect(window._handle_sensorgram_metric_archive_reload_failed)
    window._thread_pool.start(task)


def request_absolute_sensorgram_metric_archive_reload(window) -> None:
    if window._closing:
        return
    request_token = sensorgram_metric_archive_reload_request_signature(window)
    if request_token is None:
        window.status_label.setText("No saved measurement file is available to reload.")
        return
    archive_path = Path(request_token[0])
    if not archive_path.exists():
        window.status_label.setText("Sensorgram measurement file is missing.")
        window._log_warning(f"Sensorgram measurement file not found: {archive_path}")
        return
    window._sensorgram_metric_archive_reload_pending_token = request_token
    if window._sensorgram_metric_archive_reload_loading:
        return
    start_sensorgram_metric_archive_reload_task(window, request_token)


def request_sensorgram_metric_archive_reload(window) -> None:
    request_absolute_sensorgram_metric_archive_reload(window)


def handle_absolute_sensorgram_metric_archive_reload_result(window, result: MetricArchiveReloadResult) -> None:
    if int(result.point_count) <= 0:
        window.status_label.setText("Reloaded sensorgram measurement file returned no data; keeping the current absolute cache.")
        window._log_warning(
            f"Sensorgram measurement file reload returned no data | purpose=absolute_reload | path={result.path}"
        )
        return
    plot_view_cache = getattr(window, "_plot_view_cache", None)
    display_points = max(int(getattr(window, "_plot_display_points", 512)), 1)
    if plot_view_cache is not None and hasattr(plot_view_cache, "clear_live_absolute_metric_cache"):
        try:
            plot_view_cache.clear_live_absolute_metric_cache()
        except Exception:
            pass
    if plot_view_cache is not None and hasattr(plot_view_cache, "clear_active_trace_series_cache"):
        try:
            plot_view_cache.clear_active_trace_series_cache()
        except Exception:
            pass
    total_points = 0
    for metric_name, (times, values) in result.series.items():
        x_values = np.asarray(times, dtype=np.float64)
        y_values = np.asarray(values, dtype=np.float64)
        if len(x_values) == 0 or len(y_values) == 0:
            continue
        total_points += int(min(len(x_values), len(y_values)))
        if plot_view_cache is not None and hasattr(plot_view_cache, "seed_live_absolute_metric_cache"):
            try:
                plot_view_cache.seed_live_absolute_metric_cache(
                    metric_name,
                    x_values,
                    y_values,
                    target_points=display_points,
                    recent_tail_points=max(int(getattr(window, "_sensorgram_compression_recent_tail_points", 300)), 0),
                )
            except Exception:
                pass
    if hasattr(window, "_metric_render_display_cache"):
        window._metric_render_display_cache.clear()
    if hasattr(window, "_metric_render_state_cache"):
        window._metric_render_state_cache.clear()
    window._trace_view_locked = False
    window._last_metric_absolute_source_text = "saved_file_reload"
    window._last_metric_absolute_cache_invalidation_text = "reload"
    window._last_metric_absolute_cache_rebuild_count_text = str(int(result.metric_count))
    window._last_metric_absolute_hdf5_read_count_text = "1"
    window._last_metric_absolute_archive_points_text = str(int(result.point_count))
    window._last_metric_absolute_display_points_text = str(int(total_points))
    window._last_metric_absolute_view_prep_text = f"{float(result.load_ms):.2f} ms"
    window._last_metric_absolute_append_text = "-"
    window.status_label.setText(
        f"Reloaded sensorgram measurement file ({result.metric_count} metrics, {result.point_count} points)."
    )
    window._log_success(
        f"Sensorgram measurement file reloaded | purpose=absolute_reload | metrics={result.metric_count} | points={result.point_count} | load_ms={float(result.load_ms):.2f}"
    )
    window._refresh_trace_plot("Metric position (nm)")
    window._request_trace_autoscale()
    window._update_trace_stats()
