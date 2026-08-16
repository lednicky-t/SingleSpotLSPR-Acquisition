"""Re-export shim over `lspr_acq_shell.plot_view_cache` (Phase 1 shell
extraction, 2026-08-07) - the multi-resolution cache/downsampling engine
(`PlotViewCache`, `MetricDisplayCache`, and friends) now lives there since it
has zero Qt/window coupling. Kept here so every existing
`from lspr_app.gui.plot_view_cache import ...` call site in this app keeps
working unchanged.

`build_active_trace_series_token`/`build_metric_series_token` stay defined
here, not in the shell - both read a handful of attributes directly off this
app's main window (`_selected_trace_metrics`, `_sensorgram_display_mode`,
`_metric_archive_path`, `_plot_view_cache`) to build a cache-invalidation
token, which is app-specific glue around the engine, not part of the engine
itself.
"""
from __future__ import annotations

from pathlib import Path

from lspr_acq_shell.plot_view_cache import (
    MetricCompressionBlock,
    MetricDisplayCache,
    PlotViewCache,
    downsample_metric_series_for_view,
    level_raw_weight,
    quantize_view_target_points,
    sample_absolute_metric_series_for_view,
)

__all__ = [
    "MetricCompressionBlock",
    "MetricDisplayCache",
    "PlotViewCache",
    "downsample_metric_series_for_view",
    "level_raw_weight",
    "quantize_view_target_points",
    "sample_absolute_metric_series_for_view",
    "build_active_trace_series_token",
    "build_metric_series_token",
]


def build_active_trace_series_token(window) -> tuple[object, ...]:
    selected_metrics = frozenset(getattr(window, "_selected_trace_metrics", lambda: [])())
    display_mode = str(getattr(window, "_sensorgram_display_mode", "session"))
    archive_path = getattr(window, "_metric_archive_path", None)
    archive_path = Path(archive_path).expanduser() if archive_path else None
    plot_view_cache = getattr(window, "_plot_view_cache", None)
    if plot_view_cache is not None:
        try:
            live_states = tuple(
                state
                for metric_name in sorted(selected_metrics)
                if (state := plot_view_cache.live_absolute_metric_state(metric_name)) is not None
            )
        except Exception:
            live_states = ()
        if live_states:
            return ("live_absolute", display_mode, live_states, tuple(sorted(selected_metrics)))
    if archive_path is not None and archive_path.exists():
        try:
            mtime_ns = archive_path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        return ("archive", display_mode, str(archive_path), int(mtime_ns), tuple(sorted(selected_metrics)))
    return ("empty", display_mode)


def build_metric_series_token(window, metric_name: str) -> tuple[object, ...]:
    """Cache-key + change-detection token for one metric's display view (fed
    into absolute_metric_view / absolute_metric_display_state).

    Mirrors build_active_trace_series_token's own preference order - live
    cache state first, then the archive file's mtime, then a bare "empty"
    marker - so the two token builders describe the same three states the
    same way instead of drifting apart. This one used to skip the live-state
    check entirely and always fall to a token with a constant tail whenever
    there was no archive file yet (only true for the sub-second window
    before a session's first spectrum is processed - see
    storage/measurement_archive.py's ensure_session_writer, which sets
    window._metric_archive_path once and never clears it again). Harmless in
    practice (absolute_metric_view's own len(x) check and
    display_output_revision's content-based signature both already catch
    real changes independently of this token), but inconsistent with the
    sibling for no reason - see docs/sensorgram_improvements.md.

    token[3], when present, is what absolute_metric_view reads as
    source_revision - the live branch below surfaces the live cache's own
    display_output_revision there so it means the same thing (worth
    invalidating the cached view when it changes) it does for the archive
    branch's mtime.
    """
    plot_view_cache = getattr(window, "_plot_view_cache", None)
    if plot_view_cache is not None:
        try:
            live_state = plot_view_cache.live_absolute_metric_state(metric_name)
        except Exception:
            live_state = None
        if live_state is not None:
            revision = live_state[1] if len(live_state) > 1 else 0
            return (str(metric_name), "live_absolute", str(live_state), int(revision))
    archive_path = getattr(window, "_metric_archive_path", None)
    archive_path = Path(archive_path).expanduser() if archive_path else None
    if archive_path is not None and archive_path.exists():
        try:
            mtime_ns = archive_path.stat().st_mtime_ns
        except OSError:
            mtime_ns = 0
        return (str(metric_name), "archive", str(archive_path), int(mtime_ns))
    return (str(metric_name), "empty", None)
