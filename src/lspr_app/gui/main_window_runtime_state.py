from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class UiRefreshState:
    summary_dirty: bool = False
    telemetry_dirty: bool = False
    live_estimate_dirty: bool = False
    stats_dirty: bool = False
    trace_plot_dirty: bool = False
    session_stats_dirty: bool = False
    plot_render_dirty: bool = False
    pending_trace_label: str | None = None
