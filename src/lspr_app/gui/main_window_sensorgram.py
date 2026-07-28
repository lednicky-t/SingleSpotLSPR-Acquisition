from __future__ import annotations

from pathlib import Path
from time import perf_counter

import numpy as np
from lspr_app.gui.main_window_processing import normalize_sensorgram_metric_name, sensorgram_metric_order, sensorgram_metric_archive_names
from lspr_app.gui.plot_view_cache import build_active_trace_series_token
from lspr_app.gui.sensorgram_time_anchor import display_time_anchor
from lspr_app.storage.hdf5_export import load_processed_metric_history


def sensorgram_metric_selection(window) -> tuple[list[str], str]:
    visible = [mode for mode in sensorgram_metric_order(window) if mode in set(getattr(window, "_sensorgram_metric_visible_modes", set()))]
    if not visible:
        visible = [sensorgram_metric_order(window)[0]]
    primary = normalize_sensorgram_metric_name(getattr(window, "_sensorgram_metric_primary_mode", visible[0]))
    if primary not in visible:
        primary = visible[0]
    return visible, primary


def apply_sensorgram_metric_selection(window, visible_modes: list[str] | set[str], primary_mode: str, *, save: bool = False) -> None:
    ordered = [mode for mode in sensorgram_metric_order(window) if mode in {normalize_sensorgram_metric_name(name) for name in visible_modes}]
    if not ordered:
        ordered = [sensorgram_metric_order(window)[0]]
    primary = normalize_sensorgram_metric_name(primary_mode)
    if primary not in ordered:
        primary = ordered[0]
    previous_visible = set(getattr(window, "_sensorgram_metric_visible_modes", set()))
    newly_visible = set(ordered) - previous_visible
    window._sensorgram_metric_visible_modes = set(ordered)
    window._sensorgram_metric_primary_mode = primary
    current_stats = normalize_sensorgram_metric_name(getattr(window, "_trace_stats_metric_name", ordered[0]))
    window._trace_stats_metric_name = current_stats if current_stats in ordered else ordered[0]
    if hasattr(window, "_update_trace_stats"):
        window._update_trace_stats()
    if newly_visible:
        # append_processed_trace_history (acquisition_controller.py) only appends
        # live points into a metric's compression cache while it's selected, so a
        # metric that was hidden has a stale/empty cache. Reload it from the
        # archive file (same mechanism used when the display mode changes) so it
        # doesn't show a gap for the period it was hidden.
        request_reload = getattr(window, "_request_absolute_sensorgram_metric_archive_reload", None)
        if callable(request_reload):
            try:
                request_reload()
            except Exception:
                pass
    if save:
        window._schedule_acquisition_state_persist()
        window._schedule_ui_state_persist()


def primary_trace_metric(window) -> str:
    return sensorgram_metric_selection(window)[1]


def trace_stats_metric(window) -> str:
    selected, primary = sensorgram_metric_selection(window)
    current = getattr(window, "_trace_stats_metric_name", None)
    if current in selected:
        return current
    if selected:
        window._trace_stats_metric_name = selected[0]
        return selected[0]
    window._trace_stats_metric_name = primary
    return primary


def cycle_trace_stats_metric(window) -> None:
    selected, _primary = sensorgram_metric_selection(window)
    if not selected:
        return
    current = normalize_sensorgram_metric_name(getattr(window, "_trace_stats_metric_name", selected[0]))
    try:
        index = selected.index(current)
    except ValueError:
        index = -1
    next_metric = selected[(index + 1) % len(selected)]
    window._trace_stats_metric_name = next_metric
    if hasattr(window, "trace_stats_label"):
        window._update_trace_stats()


def normalize_sensorgram_line_mode(window, value: object | None = None) -> str | None:
    current = getattr(window, "_sensorgram_line_step_mode", None) if value is None else value
    if current is None:
        return None
    if not isinstance(current, str):
        return None
    normalized = current.strip().lower()
    return normalized if normalized in {"left", "right", "center", "spline"} else None



def normalize_sensorgram_metric_y_axis_mode(mode: object) -> str:
    normalized = str(mode or "auto").strip().lower().replace(" ", "_").replace("-", "_")
    if normalized in {"auto", "step_2nm", "step_5nm", "step_10nm", "step_50nm"}:
        return normalized
    return "auto"


def sensorgram_metric_y_axis_mode_label(mode: object | None = None) -> str:
    normalized = normalize_sensorgram_metric_y_axis_mode(mode)
    return {
        "auto": "Auto",
        "step_2nm": "2 nm",
        "step_5nm": "5 nm",
        "step_10nm": "10 nm",
        "step_50nm": "50 nm",
    }[normalized]


def sensorgram_metric_y_axis_mode_tooltip(window, mode: object | None = None) -> str:
    normalized = normalize_sensorgram_metric_y_axis_mode(mode or getattr(window, "_sensorgram_metric_y_axis_mode", "auto"))
    if normalized == "auto":
        return (
            "Current display: Auto y-axis. Click to switch to stepped scaling."
            " The sensorgram metric plot will scale itself to the data."
        )
    step_label = sensorgram_metric_y_axis_mode_label(normalized)
    return (
        f"Current display: stepped {step_label} y-axis. Click to switch back to Auto."
        " The sensorgram metric plot will snap its Y range to the selected step size."
    )


def update_sensorgram_metric_y_axis_mode_button(window) -> None:
    button = getattr(window, "sensorgram_metric_y_axis_mode_button", None)
    if button is None:
        return
    normalized = normalize_sensorgram_metric_y_axis_mode(getattr(window, "_sensorgram_metric_y_axis_mode", "auto"))
    button.setText(f"[{sensorgram_metric_y_axis_mode_label(normalized)}]")
    button.setToolTip(sensorgram_metric_y_axis_mode_tooltip(window, normalized))


def apply_sensorgram_metric_y_axis_mode(window, mode: object, *, save: bool = False) -> None:
    window._sensorgram_metric_y_axis_mode = normalize_sensorgram_metric_y_axis_mode(mode)
    update_sensorgram_metric_y_axis_mode_button(window)
    if window._sensorgram_metric_y_axis_mode == "auto":
        if hasattr(window, "_request_trace_autoscale"):
            window._request_trace_autoscale()
    else:
        apply_sensorgram_metric_y_axis_mode_once(window)
    if save:
        window._schedule_ui_state_persist()


def apply_sensorgram_metric_y_axis_mode_once(window) -> None:
    mode = normalize_sensorgram_metric_y_axis_mode(getattr(window, "_sensorgram_metric_y_axis_mode", "auto"))
    step = {
        "step_2nm": 2.0,
        "step_5nm": 5.0,
        "step_10nm": 10.0,
        "step_50nm": 50.0,
    }.get(mode)
    if step is None:
        return

    visible_series: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    series_getter = getattr(window, "_active_trace_series", None)
    if callable(series_getter):
        try:
            visible_series = dict(series_getter() or {})
        except Exception:
            visible_series = {}

    y_arrays: list[np.ndarray] = []
    for series in visible_series.values():
        try:
            y_values = np.asarray(series[1], dtype=np.float64)
        except Exception:
            continue
        finite = y_values[np.isfinite(y_values)]
        if len(finite) > 0:
            y_arrays.append(finite)

    if not y_arrays and hasattr(window, "trace_curves"):
        for curve in getattr(window, "trace_curves", {}).values():
            y_data = getattr(curve, "yData", None)
            if y_data is None:
                continue
            try:
                y_values = np.asarray(y_data, dtype=np.float64)
            except Exception:
                continue
            finite = y_values[np.isfinite(y_values)]
            if len(finite) > 0:
                y_arrays.append(finite)

    if not y_arrays or not hasattr(window, "trace_plot"):
        return

    combined = np.concatenate(y_arrays)
    if len(combined) == 0:
        return

    combined = np.sort(combined)
    trim_count = int(max(len(combined) * 0.1, 0))
    if len(combined) >= 10 and trim_count > 0 and (trim_count * 2) < len(combined):
        trimmed = combined[trim_count:-trim_count]
    else:
        trimmed = combined
    if len(trimmed) == 0:
        trimmed = combined
    center = float(np.mean(trimmed))
    lower = float(np.floor(center - (float(step) * 0.5)))
    upper = lower + float(step)
    if upper <= lower:
        return

    try:
        window._trace_view_autoscaling = True
        window.trace_plot.setYRange(lower, upper, padding=0.0)
        window._last_metric_autoscale_range = (0.0, 0.0, lower, upper)
        window._last_metric_autoscale_at = perf_counter()
        window._last_metric_autoscale_ms = 0.0
    finally:
        window._trace_view_autoscaling = False
        window._metric_autoscale_pending = False


def cycle_sensorgram_metric_y_axis_mode(window) -> None:
    current = normalize_sensorgram_metric_y_axis_mode(getattr(window, "_sensorgram_metric_y_axis_mode", "auto"))
    order = ["auto", "step_2nm", "step_5nm", "step_10nm", "step_50nm"]
    try:
        index = order.index(current)
    except ValueError:
        index = 0
    next_mode = order[(index + 1) % len(order)]
    apply_sensorgram_metric_y_axis_mode(window, next_mode, save=True)



SENSORGRAM_TIME_AXIS_MODES = ("elapsed", "seconds", "clock")


def normalize_sensorgram_time_axis_mode(mode: object) -> str:
    normalized = str(mode or "elapsed").strip().lower()
    return normalized if normalized in SENSORGRAM_TIME_AXIS_MODES else "elapsed"


def sensorgram_time_axis_label_text(mode: object) -> str:
    """X-axis label for the sensorgram, including units - so the unit is
    visible without having to read individual tick values first."""
    normalized = normalize_sensorgram_time_axis_mode(mode)
    return {
        "elapsed": "Elapsed time (HH:MM:SS)",
        "seconds": "Elapsed time (s)",
        "clock": "Time (local, HH:MM:SS)",
    }[normalized]


def apply_sensorgram_time_axis_mode(window) -> None:
    mode = normalize_sensorgram_time_axis_mode(getattr(window, "_sensorgram_time_axis_mode", "elapsed"))
    window._sensorgram_time_axis_mode = mode
    axis = getattr(window, "trace_time_axis", None)
    if axis is not None and hasattr(axis, "set_time_mode"):
        axis.set_time_mode(mode, start_datetime=display_time_anchor(window))
    if hasattr(window, "trace_plot"):
        window.trace_plot.setLabel("bottom", sensorgram_time_axis_label_text(mode))


def toggle_sensorgram_time_axis_mode(window) -> None:
    current_mode = normalize_sensorgram_time_axis_mode(getattr(window, "_sensorgram_time_axis_mode", "elapsed"))
    next_index = (SENSORGRAM_TIME_AXIS_MODES.index(current_mode) + 1) % len(SENSORGRAM_TIME_AXIS_MODES)
    window._sensorgram_time_axis_mode = SENSORGRAM_TIME_AXIS_MODES[next_index]
    apply_sensorgram_time_axis_mode(window)
    window._schedule_ui_state_persist()


def active_trace_series_for(window) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Return the current active metric series, using the plot-view cache if available."""
    cache = getattr(window, "_plot_view_cache", None)

    def _build_active_series() -> dict[str, tuple[np.ndarray, np.ndarray]]:
        selected_metrics = set(window._selected_trace_metrics())
        if cache is not None:
            active = cache.live_absolute_metric_series(selected_metrics)
            if active:
                return active
        archive_path = getattr(window, "_metric_archive_path", None)
        if archive_path is not None:
            try:
                archive_series = load_processed_metric_history(
                    Path(archive_path),
                    sensorgram_metric_archive_names(selected_metrics),
                    time_range_s=None,
                )
            except Exception:
                archive_series = {}
            if archive_series:
                return archive_series
        return {}

    if cache is None:
        return _build_active_series()
    token = build_active_trace_series_token(window)
    return cache.cached_active_trace_series(token, _build_active_series)
