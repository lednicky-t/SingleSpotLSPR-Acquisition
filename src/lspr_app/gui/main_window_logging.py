from __future__ import annotations

import re
import logging
from datetime import datetime, timezone
from pathlib import Path
from html import escape, unescape
from time import perf_counter

import numpy as np
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QApplication

from lspr_app.gui.logging_utils import SUCCESS_LOG_LEVEL

_HTML_TAG_RE = re.compile(r"<[^>]+>")

_LOG_VIEW_ALL = "all"
_LOG_VIEW_GUI = "gui"
_LOG_VIEW_DEVICES = "devices"

_DEVICE_KEYWORDS = (
    "pump",
    "valve",
    "m-switch",
    "mswitch",
    "spectrometer",
    "seabreeze",
    "ocean",
    "reglo",
    "itsybitsy",
    "serial controller",
    "controller",
    "port ",
    "connected",
    "disconnected",
    "discovered",
)

_GUI_KEYWORDS = (
    "startup",
    "ui",
    "gui",
    "experiment control",
    "experiment_control",
    "plot",
    "processing",
    "session",
    "acquisition",
    "window",
)


def _plain_text(value: object) -> str:
    text = unescape(_HTML_TAG_RE.sub("", str(value)))
    return " ".join(text.split()).strip()


def _timing_share_text(value_ms: float | None, total_ms: float | None) -> str:
    if value_ms is None:
        return "-"
    try:
        value = float(value_ms)
        total = float(total_ms) if total_ms is not None else None
    except (TypeError, ValueError):
        return "-"
    if total is None or not np.isfinite(value) or not np.isfinite(total) or total <= 0:
        return f"{value:.1f} ms"
    return f"{value:.1f} ms ({value / total * 100.0:.1f}%)"


def _timing_plain_text(value_ms: float | None) -> str:
    if value_ms is None:
        return "-"
    try:
        value = float(value_ms)
    except (TypeError, ValueError):
        return "-"
    if not np.isfinite(value):
        return "-"
    return f"{value:.1f} ms"


def _timing_value_ms(value: float | int | None) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(numeric):
        return None
    return numeric


def _frame_gap_reference_ms(window) -> float | None:
    spacing_ms = _timing_value_ms(getattr(window, "_last_spacing_ms", None))
    if spacing_ms is not None and spacing_ms > 0:
        return spacing_ms
    elapsed_ms = _timing_value_ms(getattr(window, "_last_elapsed_ms", None))
    if elapsed_ms is not None and elapsed_ms > 0:
        return elapsed_ms
    return None


def _queue_depth_text(window, attr_name: str) -> str:
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


def _safe_path_component(value: object, *, fallback: str = "experiment") -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        return fallback
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", text)
    text = text.strip(" ._")
    return text or fallback


def _recording_experiment_base_dir_for(window) -> Path:
    project_destination = ""
    if hasattr(window, "recording_project_destination"):
        project_destination = str(window.recording_project_destination() or "").strip()
    base_dir = Path(project_destination).expanduser() if project_destination else Path.cwd() / "data"
    experiment_name = ""
    if hasattr(window, "recording_experiment_name"):
        experiment_name = str(window.recording_experiment_name() or "").strip()
    if experiment_name:
        base_dir = base_dir / _safe_path_component(experiment_name)
    return base_dir


def build_recording_experiment_base_dir(
    project_destination: str,
    experiment_name: str,
    *,
    fallback_base: Path | None = None,
) -> Path:
    base_dir = Path(project_destination).expanduser() if str(project_destination or "").strip() else (fallback_base or (Path.cwd() / "data"))
    if str(experiment_name or "").strip():
        base_dir = base_dir / _safe_path_component(experiment_name)
    return base_dir


def build_recording_experiment_log_path_for(
    window,
    *,
    prefix: str = "app_log",
    suffix: str = ".log",
    timestamp: datetime | None = None,
) -> Path:
    base_dir = _recording_experiment_base_dir_for(window)
    started_at = timestamp or getattr(window, "_startup_started_at", None) or datetime.now(timezone.utc)
    started_local = started_at.astimezone()
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / f"{prefix}_{started_local.strftime('%Y%m%d_%H%M%S')}{suffix}"


def build_recording_experiment_log_path(
    project_destination: str,
    experiment_name: str,
    *,
    prefix: str = "app_log",
    suffix: str = ".log",
    timestamp: datetime | None = None,
) -> Path:
    base_dir = build_recording_experiment_base_dir(project_destination, experiment_name)
    started_at = timestamp or datetime.now(timezone.utc)
    started_local = started_at.astimezone()
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / f"{prefix}_{started_local.strftime('%Y%m%d_%H%M%S')}{suffix}"


def build_pipeline_timing_breakdown_for(window) -> dict[str, float | None]:
    reference_ms = _frame_gap_reference_ms(window)
    acquisition_ms = _timing_value_ms(getattr(window, "_last_elapsed_ms", None))
    live_result_delay_ms = _timing_value_ms(getattr(window, "_last_live_result_timer_delay_ms", None))
    live_acquisition_flush_ms = _timing_value_ms(getattr(window, "_last_live_acquisition_flush_ms", None))
    live_processed_delay_ms = _timing_value_ms(getattr(window, "_last_live_processed_timer_delay_ms", None))
    live_processed_flush_ms = _timing_value_ms(getattr(window, "_last_live_processed_flush_ms", None))
    stats_refresh_delay_ms = _timing_value_ms(getattr(window, "_last_stats_refresh_delay_ms", None))
    summary_refresh_ms = _timing_value_ms(getattr(window, "_last_summary_refresh_ms", None))
    session_stats_refresh_ms = _timing_value_ms(getattr(window, "_last_session_stats_refresh_ms", None))
    log_buffer_delay_ms = _timing_value_ms(getattr(window, "_last_log_buffer_delay_ms", None))
    log_buffer_flush_ms = _timing_value_ms(getattr(window, "_last_log_buffer_flush_ms", None))
    processing_wait_ms = _timing_value_ms(getattr(window, "_last_processing_queue_wait_ms", None))
    processing_ms = _timing_value_ms(getattr(window, "_last_processing_ms", None))
    plot_refresh_delay_ms = _timing_value_ms(getattr(window, "_last_plot_refresh_delay_ms", None))
    plot_render_ms = _timing_value_ms(getattr(window, "_last_plot_refresh_ms", None))
    sensorgram_render_ms = _timing_value_ms(getattr(window, "_last_sensorgram_render_ms", None))
    sensorgram_heatmap_render_ms = _timing_value_ms(getattr(window, "_last_sensorgram_heatmap_render_ms", None))
    deferred_ui_ms = _timing_value_ms(getattr(window, "_last_deferred_ui_refresh_ms", None))
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
    idle_ms = None
    if reference_ms is not None:
        idle_ms = max(reference_ms - known_total_ms, 0.0)
    return {
        "reference_ms": reference_ms,
        "acquisition_ms": acquisition_ms,
        "live_result_delay_ms": live_result_delay_ms,
        "live_acquisition_flush_ms": live_acquisition_flush_ms,
        "live_processed_delay_ms": live_processed_delay_ms,
        "live_processed_flush_ms": live_processed_flush_ms,
        "stats_refresh_delay_ms": stats_refresh_delay_ms,
        "summary_refresh_ms": summary_refresh_ms,
        "session_stats_refresh_ms": session_stats_refresh_ms,
        "log_buffer_delay_ms": log_buffer_delay_ms,
        "log_buffer_flush_ms": log_buffer_flush_ms,
        "processing_wait_ms": processing_wait_ms,
        "processing_ms": processing_ms,
        "plot_refresh_delay_ms": plot_refresh_delay_ms,
        "plot_render_ms": plot_render_ms,
        "sensorgram_render_ms": sensorgram_render_ms,
        "sensorgram_heatmap_render_ms": sensorgram_heatmap_render_ms,
        "deferred_ui_ms": deferred_ui_ms,
        "idle_ms": idle_ms,
    }


def _pipeline_timing_line(label: str, value_ms: float | None) -> str:
    return f"  {label}: {_timing_plain_text(value_ms)}"

def set_log_following(window, enabled: bool) -> None:
    window._log_follow_enabled = bool(enabled)


def clear_log_terminal(window) -> None:
    if hasattr(window, "log_terminal"):
        window.log_terminal.clear()
    history = getattr(window, "_log_history", None)
    if history is not None:
        history.clear()
    buffer = getattr(window, "_log_buffer", None)
    if buffer is not None:
        buffer.clear()
    window._log_buffer_requested_at = None
    buffer_timer = getattr(window, "_log_buffer_timer", None)
    if buffer_timer is not None:
        buffer_timer.stop()


def copy_log_terminal(window) -> None:
    if not hasattr(window, "log_terminal"):
        return
    QApplication.clipboard().setText(window.log_terminal.toPlainText())


def _normalize_log_source(source: str) -> str:
    return str(source or "").strip().casefold()


def _normalize_log_text(text: str) -> str:
    return " ".join(str(text or "").strip().casefold().split())


def _log_record_matches_view(levelno: int, source: str, text: str, view_mode: str) -> bool:
    _ = levelno
    mode = _normalize_log_source(view_mode)
    if mode in {"", _LOG_VIEW_ALL}:
        return True
    source_text = _normalize_log_source(source)
    text_text = _normalize_log_text(text)
    if mode == _LOG_VIEW_DEVICES:
        return any(keyword in source_text or keyword in text_text for keyword in _DEVICE_KEYWORDS)
    if mode == _LOG_VIEW_GUI:
        if any(keyword in source_text or keyword in text_text for keyword in _DEVICE_KEYWORDS):
            return False
        return source_text in {"main", "gui", "plot", "processing", "session", "acquisition", "hardware_init", "startup", "experiment_control"} or any(
            keyword in source_text or keyword in text_text for keyword in _GUI_KEYWORDS
        )
    return True


def _append_log_history(window, levelno: int, source: str, text: str) -> None:
    history = getattr(window, "_log_history", None)
    if history is None:
        return
    history.append((int(levelno), str(source), str(text)))
    max_entries = int(getattr(window, "_log_history_max_entries", 2000))
    if len(history) > max_entries:
        del history[: len(history) - max_entries]


def _refresh_log_terminal_view(window) -> None:
    if not hasattr(window, "log_terminal"):
        return
    cursor = window.log_terminal.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.beginEditBlock()
    try:
        window.log_terminal.clear()
        history = getattr(window, "_log_history", None) or []
        view_mode = getattr(window, "_log_view_mode", _LOG_VIEW_ALL)
        for levelno, source, text in history:
            if _log_record_matches_view(levelno, source, text, view_mode):
                insert_log_record(window, cursor, levelno, source, text)
    finally:
        cursor.endEditBlock()
        window.log_terminal.setTextCursor(cursor)


def set_log_view_mode(window, mode: str, *, refresh: bool = True) -> None:
    window._log_view_mode = _normalize_log_source(mode) or _LOG_VIEW_ALL
    if refresh:
        _refresh_log_terminal_view(window)


def copy_session_stats_log_for(window) -> None:
    lines = getattr(window, "_session_stats_log", None)
    if not lines:
        current = build_session_panel_plain_text_for(window)
        QApplication.clipboard().setText(current)
        return
    QApplication.clipboard().setText("\n\n".join(str(line) for line in lines))


def copy_session_stats_snapshot_for(window) -> None:
    QApplication.clipboard().setText(build_session_panel_plain_text_for(window))


def save_session_stats_log_for(window) -> Path | None:
    lines = getattr(window, "_session_stats_log", None)
    text = "\n\n".join(str(line) for line in lines) if lines else build_session_panel_plain_text_for(window)
    if not text.strip():
        return None

    base_dir = _recording_experiment_base_dir_for(window)
    started_at = getattr(window, "_session_stats_recording_started_at", None)
    if started_at is None:
        started_at = getattr(window, "_measurement_started_at", None)
    if started_at is None:
        started_at = datetime.now(timezone.utc)
    started_local = started_at.astimezone()
    duration_s = getattr(window, "_session_stats_recording_duration_s", None)
    if duration_s is None:
        current_started_at = getattr(window, "_session_stats_recording_started_at", None)
        if current_started_at is not None:
            duration_s = max((datetime.now(timezone.utc) - current_started_at).total_seconds(), 0.0)
        else:
            duration_s = 0.0
    timestamp = started_local.strftime("%Y%m%d_%H%M%S")
    duration_suffix = f"{int(round(float(duration_s)))}s"
    base_dir.mkdir(parents=True, exist_ok=True)
    destination = base_dir / f"session_stats_{timestamp}_{duration_suffix}.txt"
    if destination.exists():
        stem = destination.stem
        suffix = destination.suffix
        for index in range(1, 100):
            candidate = destination.with_name(f"{stem}_{index:02d}{suffix}")
            if not candidate.exists():
                destination = candidate
                break
    destination.write_text(text, encoding="utf-8")
    return destination


def flush_log_buffer(window) -> None:
    started = perf_counter()
    requested_at = getattr(window, "_log_buffer_requested_at", None)
    if requested_at is not None:
        try:
            window._last_log_buffer_delay_ms = max((started - float(requested_at)) * 1000.0, 0.0)
        except (TypeError, ValueError):
            window._last_log_buffer_delay_ms = None
    flush_budget_ms = 12.0
    max_records_per_flush = 25
    if not getattr(window, "_log_buffering_enabled", True):
        buffer = getattr(window, "_log_buffer", None)
        if buffer:
            batch = list(buffer)
            buffer.clear()
            for levelno, source, text in collapse_log_batch(batch):
                append_log_record_now(window, levelno, source, text)
        buffer_timer = getattr(window, "_log_buffer_timer", None)
        if buffer_timer is not None:
            buffer_timer.stop()
        window._last_log_buffer_flush_ms = (perf_counter() - started) * 1000.0
        window._log_buffer_requested_at = None
        return
    buffer = getattr(window, "_log_buffer", None)
    if not buffer:
        buffer_timer = getattr(window, "_log_buffer_timer", None)
        if buffer_timer is not None:
            buffer_timer.stop()
        window._last_log_buffer_flush_ms = (perf_counter() - started) * 1000.0
        window._log_buffer_requested_at = None
        return
    batch = list(buffer[:max_records_per_flush])
    del buffer[: len(batch)]
    cursor = window.log_terminal.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.beginEditBlock()
    try:
        for levelno, source, text in collapse_log_batch(batch):
            _append_log_history(window, levelno, source, text)
            insert_log_record(window, cursor, levelno, source, text)
    finally:
        cursor.endEditBlock()
        window.log_terminal.setTextCursor(cursor)
    buffer_timer = getattr(window, "_log_buffer_timer", None)
    if buffer_timer is not None and not buffer:
        buffer_timer.stop()
    if buffer:
        window._log_buffer_requested_at = perf_counter()
    else:
        window._log_buffer_requested_at = None
    window._last_log_buffer_flush_ms = (perf_counter() - started) * 1000.0
    if (perf_counter() - started) * 1000.0 > flush_budget_ms and buffer:
        # Leave remaining records for the next housekeeping pass so the GUI stays responsive.
        return


def collapse_log_batch(batch: list[tuple[int, str, str]]) -> list[tuple[int, str, str]]:
    if not batch:
        return []
    collapsed: list[tuple[int, str, str]] = []
    previous: tuple[int, str, str] | None = None
    repeat_count = 0
    for item in batch:
        if previous is not None and item == previous:
            repeat_count += 1
            continue
        if previous is not None:
            levelno, source, text = previous
            if repeat_count:
                text = f"{text} (x{repeat_count + 1})"
            collapsed.append((levelno, source, text))
        previous = item
        repeat_count = 0
    if previous is not None:
        levelno, source, text = previous
        if repeat_count:
            text = f"{text} (x{repeat_count + 1})"
        collapsed.append((levelno, source, text))
    return collapsed


def append_log_record(window, levelno: int, source: str, text: str) -> None:
    line = str(text).strip()
    if not line or not hasattr(window, "log_terminal"):
        return
    if int(levelno) not in window._log_emit_levels:
        return
    if int(levelno) >= logging.WARNING:
        append_log_record_now(window, levelno, source, line)
        return
    if not getattr(window, "_log_buffering_enabled", True):
        append_log_record_now(window, levelno, source, line)
        return
    buffer = getattr(window, "_log_buffer", None)
    buffer_timer = getattr(window, "_log_buffer_timer", None)
    if buffer is not None and buffer_timer is not None:
        buffer.append((int(levelno), str(source), line))
        if window._log_buffer_requested_at is None:
            window._log_buffer_requested_at = perf_counter()
        return
    append_log_record_now(window, levelno, source, line)


def insert_log_record(window, cursor: QTextCursor, levelno: int, source: str, text: str) -> None:
    timestamp = datetime.now().strftime("%H:%M:%S")
    color_map = {
        logging.DEBUG: "#5fa8ff",
        logging.INFO: "#c7d2e0",
        SUCCESS_LOG_LEVEL: "#44d07b",
        logging.WARNING: "#f4b23d",
        logging.ERROR: "#ff6b6b",
        logging.CRITICAL: "#f35f8d",
    }
    level_label_map = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        SUCCESS_LOG_LEVEL: "SUCCESS",
        logging.WARNING: "WARN",
        logging.ERROR: "ERROR",
        logging.CRITICAL: "CRIT",
    }
    level_color = color_map.get(int(levelno), "#c7d2e0")
    level_label = level_label_map.get(int(levelno), "INFO")
    source_label = str(source).split(".")[-1] or "app"
    escaped = str(text).replace("\n", "<br>")
    html = (
        f"<div style='white-space:pre-wrap; margin:0;'>"
        f"<span style='color:#738193;'>{timestamp}</span> "
        f"<span style='color:{level_color}; font-weight:600;'>[{level_label}]</span> "
        f"<span style='color:#94a3b8;'>{source_label}</span> "
        f"<span style='color:#e5edf7;'>{escaped}</span>"
        f"</div>"
    )
    cursor.insertHtml(html)
    cursor.insertHtml("<br>")
    if window._log_follow_enabled:
        scrollbar = window.log_terminal.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())


def append_log_record_now(window, levelno: int, source: str, text: str) -> None:
    if not hasattr(window, "log_terminal"):
        return
    _append_log_history(window, levelno, source, text)
    cursor = window.log_terminal.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.beginEditBlock()
    try:
        insert_log_record(window, cursor, levelno, source, text)
    finally:
        cursor.endEditBlock()
        window.log_terminal.setTextCursor(cursor)


def log_event(window, levelno: int, message: str, source: str = "main") -> None:
    logger = getattr(window, "_ui_logger", None)
    if logger is None:
        logging.getLogger("lspr_app.bootstrap").log(int(levelno), str(message))
        return
    logger = logger.getChild(str(source)) if source else logger
    logger.log(int(levelno), str(message))


def log_debug(window, message: str, source: str = "main") -> None:
    log_event(window, logging.DEBUG, message, source=source)


def log_info(window, message: str, source: str = "main") -> None:
    log_event(window, logging.INFO, message, source=source)


def log_success(window, message: str, source: str = "main") -> None:
    log_event(window, SUCCESS_LOG_LEVEL, message, source=source)


def log_warning(window, message: str, source: str = "main") -> None:
    log_event(window, logging.WARNING, message, source=source)


def log_error(window, message: str, source: str = "main") -> None:
    log_event(window, logging.ERROR, message, source=source)


def log_throttled(window, key: str, message: str, *, level: int = logging.DEBUG, min_interval: float = 1.5) -> None:
    line = str(message).strip()
    if not line:
        return
    now = perf_counter()
    state = window._log_throttle_state
    previous = state.get(str(key))
    if previous is not None:
        previous_at, previous_line = previous
        if previous_line == line and (now - previous_at) < float(min_interval):
            return
    state[str(key)] = (now, line)
    log_event(window, level, line)


def refresh_session_summary_for(window, force: bool = False) -> None:
    target = getattr(window, "session_summary", None) or getattr(window, "session_settings_text", None)
    if target is None:
        return
    now = perf_counter()
    if not force and (now - window._last_summary_refresh_ts) < 1.0:
        return
    scrollbar = target.verticalScrollBar()
    old_value = scrollbar.value()
    old_max = max(scrollbar.maximum(), 1)
    stay_at_bottom = old_value >= max(old_max - 2, 0)
    started = perf_counter()
    text = build_session_panel_html_for(window)
    if force or text != window._last_summary_text:
        target.setHtml(text)
        window._last_summary_text = text
        scrollbar = target.verticalScrollBar()
        if stay_at_bottom:
            scrollbar.setValue(scrollbar.maximum())
        else:
            target_value = int(round((old_value / old_max) * max(scrollbar.maximum(), 0)))
            scrollbar.setValue(max(0, min(target_value, scrollbar.maximum())))
    window._last_summary_refresh_ms = (perf_counter() - started) * 1000.0
    window._last_summary_refresh_ts = now


def refresh_session_statistics_for(window, force: bool = False) -> None:
    target = getattr(window, "session_summary", None) or getattr(window, "session_statistics_text", None)
    if target is None:
        return
    now = perf_counter()
    if not force and (now - window._last_session_stats_refresh_ts) < 0.25:
        return
    scrollbar = target.verticalScrollBar()
    old_value = scrollbar.value()
    old_max = max(scrollbar.maximum(), 1)
    stay_at_bottom = old_value >= max(old_max - 2, 0)
    started = perf_counter()
    text = build_session_panel_html_for(window)
    if force or text != window._last_session_stats_text:
        target.setHtml(text)
        window._last_session_stats_text = text
        scrollbar = target.verticalScrollBar()
        if stay_at_bottom:
            scrollbar.setValue(scrollbar.maximum())
        else:
            target_value = int(round((old_value / old_max) * max(scrollbar.maximum(), 0)))
            scrollbar.setValue(max(0, min(target_value, scrollbar.maximum())))
        append_session_stats_log_snapshot_for(window, text)
    window._last_session_stats_refresh_ms = (perf_counter() - started) * 1000.0
    window._last_session_stats_refresh_ts = now


def build_session_panel_plain_text_for(window) -> str:
    summary_text = getattr(window, "_build_summary_text", lambda: "")().strip()
    stats_text = build_session_statistics_text_for(window).strip()
    if summary_text and stats_text:
        return f"{summary_text}\n{stats_text}"
    return summary_text or stats_text


def build_session_panel_html_for(window) -> str:
    summary_text = getattr(window, "_build_summary_text", lambda: "")().strip()
    stats_text = build_session_statistics_text_for(window).strip()
    sections: list[str] = []
    if summary_text:
        sections.append(
            "<div style='margin:0 0 6px 0;'>"
            "<div style='font-weight:700; margin:0 0 2px 0;'>Session settings</div>"
            f"<div style='white-space:pre-wrap; margin:0;'>{escape(summary_text).replace(chr(10), '<br>')}</div>"
            "</div>"
        )
    if stats_text:
        sections.append(
            "<div style='margin:0;'>"
            "<div style='font-weight:700; margin:0 0 2px 0;'>Session statistics</div>"
            f"<div style='white-space:pre-wrap; margin:0;'>{escape(stats_text).replace(chr(10), '<br>')}</div>"
            "</div>"
        )
    return "".join(sections) if sections else "<div></div>"


def build_session_statistics_text_for(window) -> str:
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
        actual_rate_text = f"{float(window._actual_plot_refresh_rate_hz):.2f} Hz"
    processing_text = "-" if getattr(window, "_last_processing_ms", None) is None else f"{window._last_processing_ms:.1f} ms"
    headroom_value = getattr(window, "_processing_headroom_ratio", None)
    headroom_text = "-" if headroom_value is None else f"{float(headroom_value):.2f}x"
    wait_text = "-"
    if getattr(window, "_last_processing_queue_wait_ms", None) is not None:
        wait_text = f"{window._last_processing_queue_wait_ms:.1f} ms"
    acquisition_latency_text = "-"
    if getattr(window, "_last_elapsed_ms", None) is not None:
        acquisition_latency_text = f"{window._last_elapsed_ms:.1f} ms"
    acquisition_overhead_text = "-"
    if getattr(window, "_last_overhead_ms", None) is not None:
        acquisition_overhead_text = f"{window._last_overhead_ms:.1f} ms"
    frame_spacing_text = "-"
    if getattr(window, "_last_spacing_ms", None) is not None:
        frame_spacing_text = f"{window._last_spacing_ms:.1f} ms"
    acquisition_latency_text = _timing_plain_text(getattr(window, "_last_elapsed_ms", None))
    acquisition_overhead_text = _timing_plain_text(getattr(window, "_last_overhead_ms", None))
    frame_spacing_text = _timing_plain_text(getattr(window, "_last_spacing_ms", None))
    breakdown = build_pipeline_timing_breakdown_for(window)
    source_rate_text = "-"
    if getattr(window, "_effective_raw_rate_hz", None) is not None:
        source_rate_text = f"{window._effective_raw_rate_hz:.2f} Hz"
    ui_state_delay_text = _timing_plain_text(getattr(window, "_last_ui_state_delay_ms", None))
    ui_state_save_text = _timing_plain_text(getattr(window, "_last_ui_state_save_ms", None))
    ui_state_total_text = _timing_plain_text(getattr(window, "_last_ui_state_total_ms", None))
    acquisition_state_delay_text = _timing_plain_text(getattr(window, "_last_acquisition_state_delay_ms", None))
    acquisition_state_save_text = _timing_plain_text(getattr(window, "_last_acquisition_state_save_ms", None))
    acquisition_state_total_text = _timing_plain_text(getattr(window, "_last_acquisition_state_total_ms", None))
    session_stats_recording_delay_text = _timing_plain_text(getattr(window, "_last_session_stats_recording_delay_ms", None))
    session_stats_recording_snapshot_text = _timing_plain_text(getattr(window, "_last_session_stats_recording_snapshot_ms", None))
    session_stats_recording_total_text = _timing_plain_text(getattr(window, "_last_session_stats_recording_total_ms", None))
    measurement_state_text = "recording" if getattr(window, "_measurement_active", False) else "idle"
    current_measurement_runtime_text = "-"
    total_measurement_runtime_text = "-"
    started_at = getattr(window, "_measurement_started_at", None)
    if started_at is not None:
        elapsed_s = max((datetime.now(timezone.utc) - started_at).total_seconds(), 0.0)
        current_measurement_runtime_text = f"{elapsed_s:.1f} s"
        total_measurement_runtime_text = f"{elapsed_s:.1f} s"
    ui_heartbeat_delay_text = _timing_plain_text(getattr(window, "_last_ui_heartbeat_delay_ms", None))
    ui_heartbeat_max_delay_text = _timing_plain_text(getattr(window, "_ui_heartbeat_max_delay_ms", None))
    ui_heartbeat_total_text = _timing_plain_text(getattr(window, "_last_ui_heartbeat_total_ms", None))
    log_buffer_total_text = _timing_plain_text(getattr(window, "_last_log_buffer_total_ms", None))
    gui_housekeeping_total_text = _timing_plain_text(getattr(window, "_last_gui_housekeeping_total_ms", None))
    plot_refresh_total_text = _timing_plain_text(getattr(window, "_last_plot_refresh_total_ms", None))
    deferred_ui_total_text = _timing_plain_text(getattr(window, "_last_deferred_ui_refresh_total_ms", None))
    deferred_ui_live_estimate_text = _timing_plain_text(getattr(window, "_last_deferred_ui_live_estimate_ms", None))
    deferred_ui_telemetry_text = _timing_plain_text(getattr(window, "_last_deferred_ui_telemetry_ms", None))
    deferred_ui_trace_plot_text = _timing_plain_text(getattr(window, "_last_deferred_ui_trace_plot_ms", None))
    deferred_ui_summary_text = _timing_plain_text(getattr(window, "_last_deferred_ui_summary_ms", None))
    deferred_ui_stats_text = _timing_plain_text(getattr(window, "_last_deferred_ui_stats_ms", None))
    session_summary_total_text = _timing_plain_text(getattr(window, "_last_session_summary_refresh_total_ms", None))
    session_stats_total_text = _timing_plain_text(getattr(window, "_last_session_stats_refresh_total_ms", None))
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
    dropped_frames_value = getattr(window, "_live_display_dropped_frames", None)
    dropped_frames_text = "-" if dropped_frames_value is None else str(max(int(dropped_frames_value), 0))
    live_result_queue_text = _queue_depth_text(window, "_live_result_queue")
    live_result_queue_max_text = _queue_depth_max_text(getattr(window, "_live_result_queue_max_depth", None))
    live_processed_queue_text = _queue_depth_text(window, "_live_processed_queue")
    live_processed_queue_max_text = _queue_depth_max_text(getattr(window, "_live_processed_queue_max_depth", None))
    return "\n".join(
        [
            "App",
            f"  Refresh rate: {display_rate_text}",
            f"  Simulation output rate: {simulation_rate_text}",
            f"  Actual refresh: {actual_rate_text}",
            f"  Frame skip rate: {skip_rate:.1f} Hz",
            f"  State: {measurement_state_text}",
            f"  Runtime: {current_measurement_runtime_text}",
            f"  Total runtime: {total_measurement_runtime_text}",
            "",
            "UI event loop heartbeat",
            f"  Current delay: {ui_heartbeat_delay_text}",
            f"  Max delay: {ui_heartbeat_max_delay_text}",
            f"  Callback time: {ui_heartbeat_total_text}",
            "",
            "Periodic callbacks",
            f"  UI state save delay: {ui_state_delay_text}",
            f"  UI state save time: {ui_state_save_text}",
            f"  UI state save total: {ui_state_total_text}",
            f"  Acquisition state delay: {acquisition_state_delay_text}",
            f"  Acquisition state save time: {acquisition_state_save_text}",
            f"  Acquisition state save total: {acquisition_state_total_text}",
            f"  Session stats snapshot delay: {session_stats_recording_delay_text}",
            f"  Session stats snapshot time: {session_stats_recording_snapshot_text}",
            f"  Session stats snapshot total: {session_stats_recording_total_text}",
            "",
            "GUI callback wall time",
            f"  Plot refresh total: {plot_refresh_total_text}",
            f"  Deferred UI total: {deferred_ui_total_text}",
            f"  Deferred UI live estimate: {deferred_ui_live_estimate_text}",
            f"  Deferred UI telemetry: {deferred_ui_telemetry_text}",
            f"  Deferred UI metric plot: {deferred_ui_trace_plot_text}",
            f"  Deferred UI summary: {deferred_ui_summary_text}",
            f"  Deferred UI stats: {deferred_ui_stats_text}",
            f"  Session summary total: {session_summary_total_text}",
            f"  Session stats total: {session_stats_total_text}",
            f"  Log buffer total: {log_buffer_total_text}",
            f"  GUI housekeeping total: {gui_housekeeping_total_text}",
            f"  Metric history points: {trace_points_text}",
            f"  Metric display buffer points: {trace_buffer_points_text}",
            f"  Heatmap rows: {heatmap_rows_text}",
            f"  Live result queue: {live_result_queue_text} | max: {live_result_queue_max_text}",
            f"  Live processed queue: {live_processed_queue_text} | max: {live_processed_queue_max_text}",
            "",
            "Processing",
            f"  Time per spectrum: {processing_text}",
            f"  Queue wait: {wait_text}",
            f"  Headroom: {headroom_text}",
            "",
            "Pipeline gap breakdown",
            _pipeline_timing_line("Acquisition latency", getattr(window, "_last_elapsed_ms", None)),
            _pipeline_timing_line("Live acquisition timer delay", breakdown["live_result_delay_ms"]),
            _pipeline_timing_line("Live acquisition flush", getattr(window, "_last_live_acquisition_flush_ms", None)),
            _pipeline_timing_line("Live processing timer delay", breakdown["live_processed_delay_ms"]),
            _pipeline_timing_line("Live processing flush", getattr(window, "_last_live_processed_flush_ms", None)),
            _pipeline_timing_line("Stats refresh timer delay", breakdown["stats_refresh_delay_ms"]),
            _pipeline_timing_line("Session summary refresh", getattr(window, "_last_summary_refresh_ms", None)),
            _pipeline_timing_line("Session stats refresh", getattr(window, "_last_session_stats_refresh_ms", None)),
            _pipeline_timing_line("Log buffer timer delay", breakdown["log_buffer_delay_ms"]),
            _pipeline_timing_line("Log buffer flush", getattr(window, "_last_log_buffer_flush_ms", None)),
            _pipeline_timing_line("Processing queue wait", getattr(window, "_last_processing_queue_wait_ms", None)),
            _pipeline_timing_line("Processing compute", getattr(window, "_last_processing_ms", None)),
            _pipeline_timing_line("Plot refresh timer delay", breakdown["plot_refresh_delay_ms"]),
            _pipeline_timing_line("Plot render", getattr(window, "_last_plot_refresh_ms", None)),
            _pipeline_timing_line("Sensorgram render", getattr(window, "_last_sensorgram_render_ms", None)),
            _pipeline_timing_line("Sensorgram heatmap render", getattr(window, "_last_sensorgram_heatmap_render_ms", None)),
            _pipeline_timing_line("Deferred UI flush", getattr(window, "_last_deferred_ui_refresh_ms", None)),
            _pipeline_timing_line("Unattributed / idle", breakdown["idle_ms"]),
            "",
            "Spectrum redraw breakdown",
            _pipeline_timing_line("Curve update", getattr(window, "_last_spectrum_curve_update_ms", None)),
            _pipeline_timing_line("Fit update", getattr(window, "_last_spectrum_fit_update_ms", None)),
            _pipeline_timing_line("Marker update", getattr(window, "_last_spectrum_marker_update_ms", None)),
            _pipeline_timing_line("Residual update", getattr(window, "_last_spectrum_residual_update_ms", None)),
            "",
            "Device acquisition",
            f"  Acquisition latency: {acquisition_latency_text}",
            f"  Acquisition overhead: {acquisition_overhead_text}",
            f"  Frame spacing: {frame_spacing_text}",
            f"  Effective source rate: {source_rate_text}",
            f"  Dropped frames: {dropped_frames_text}",
        ]
    )


def append_session_stats_log_snapshot_for(window, text: str | None = None) -> None:
    if not getattr(window, "_measurement_active", False) and not getattr(window, "_session_stats_recording_active", False):
        return
    log = getattr(window, "_session_stats_log", None)
    if log is None:
        return
    now = perf_counter()
    last_at = float(getattr(window, "_session_stats_log_last_capture_ts", 0.0) or 0.0)
    if text is None:
        text = build_session_panel_plain_text_for(window)
    if text == getattr(window, "_session_stats_log_last_text", None) and (now - last_at) < 1.0:
        return
    timestamp = datetime.now().strftime("%H:%M:%S")
    log.append(f"[{timestamp}] {text}")
    window._session_stats_log_last_text = text
    window._session_stats_log_last_capture_ts = now


def describe_spectrum_for(window, spectrum) -> str:
    if spectrum is None:
        return "not acquired"

    integration_time = spectrum.metadata.get("integration_time_ms", "-")
    averages = spectrum.metadata.get("averages", "-")
    correct_dark = "on" if spectrum.metadata.get("electric_dark_pixel_correction") else "off"
    dark_pixel_count = spectrum.metadata.get("electric_dark_pixel_count", "-")
    correct_nonlinearity = "on" if spectrum.metadata.get("correct_nonlinearity") else "off"
    display_count = spectrum.metadata.get("display_average_count")
    display_window_ms = spectrum.metadata.get("display_window_ms")
    display_text = ""
    if display_count is not None and display_window_ms is not None:
        display_text = f" | displayed {display_count} raw_spectra / {float(display_window_ms):.1f} ms"
    return (
        f"{spectrum.acquired_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')} | "
        f"{len(spectrum.wavelengths_nm)} points | "
        f"integration {integration_time} ms | "
        f"accumulation {averages} raw_spectra | "
        f"elec dark corr {correct_dark} ({dark_pixel_count} px) | "
        f"nonlinear corr {correct_nonlinearity}"
        f"{display_text}"
    )
