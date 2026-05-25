from __future__ import annotations

import logging
import re
from datetime import datetime
from html import unescape
from time import perf_counter

from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QApplication

from lspr_app.gui.logging_utils import SUCCESS_LOG_LEVEL

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _plain_text(value: object) -> str:
    text = unescape(_HTML_TAG_RE.sub("", str(value)))
    return " ".join(text.split()).strip()

def set_log_following(window, enabled: bool) -> None:
    window._log_follow_enabled = bool(enabled)


def clear_log_terminal(window) -> None:
    if hasattr(window, "log_terminal"):
        window.log_terminal.clear()
    buffer = getattr(window, "_log_buffer", None)
    if buffer is not None:
        buffer.clear()
    buffer_timer = getattr(window, "_log_buffer_timer", None)
    if buffer_timer is not None:
        buffer_timer.stop()


def copy_log_terminal(window) -> None:
    if not hasattr(window, "log_terminal"):
        return
    QApplication.clipboard().setText(window.log_terminal.toPlainText())


def copy_session_stats_log_for(window) -> None:
    lines = getattr(window, "_session_stats_log", None)
    if not lines:
        current = build_session_statistics_text_for(window)
        QApplication.clipboard().setText(current)
        return
    QApplication.clipboard().setText("\n\n".join(str(line) for line in lines))


def flush_log_buffer(window) -> None:
    buffer = getattr(window, "_log_buffer", None)
    if not buffer:
        buffer_timer = getattr(window, "_log_buffer_timer", None)
        if buffer_timer is not None:
            buffer_timer.stop()
        return
    batch = list(buffer)
    buffer.clear()
    cursor = window.log_terminal.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.beginEditBlock()
    try:
        for levelno, source, text in collapse_log_batch(batch):
            insert_log_record(window, cursor, levelno, source, text)
    finally:
        cursor.endEditBlock()
        window.log_terminal.setTextCursor(cursor)
    buffer_timer = getattr(window, "_log_buffer_timer", None)
    if buffer_timer is not None:
        buffer_timer.stop()


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
    buffer = getattr(window, "_log_buffer", None)
    buffer_timer = getattr(window, "_log_buffer_timer", None)
    if buffer is not None and buffer_timer is not None:
        buffer.append((int(levelno), str(source), line))
        if not buffer_timer.isActive():
            buffer_timer.start()
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
    target = getattr(window, "session_settings_text", None) or getattr(window, "session_summary", None)
    if target is None:
        return
    now = perf_counter()
    if not force and (now - window._last_summary_refresh_ts) < 1.0:
        return
    scrollbar = target.verticalScrollBar()
    old_value = scrollbar.value()
    old_max = max(scrollbar.maximum(), 1)
    stay_at_bottom = old_value >= max(old_max - 2, 0)
    text = window._build_summary_text()
    if force or text != window._last_summary_text:
        target.setPlainText(text)
        window._last_summary_text = text
        scrollbar = target.verticalScrollBar()
        if stay_at_bottom:
            scrollbar.setValue(scrollbar.maximum())
        else:
            target_value = int(round((old_value / old_max) * max(scrollbar.maximum(), 0)))
            scrollbar.setValue(max(0, min(target_value, scrollbar.maximum())))
    window._last_summary_refresh_ts = now


def refresh_session_statistics_for(window, force: bool = False) -> None:
    target = getattr(window, "session_statistics_text", None)
    if target is None:
        return
    now = perf_counter()
    if not force and (now - window._last_session_stats_refresh_ts) < 0.25:
        return
    scrollbar = target.verticalScrollBar()
    old_value = scrollbar.value()
    old_max = max(scrollbar.maximum(), 1)
    stay_at_bottom = old_value >= max(old_max - 2, 0)
    text = build_session_statistics_text_for(window)
    if force or text != window._last_session_stats_text:
        target.setPlainText(text)
        window._last_session_stats_text = text
        scrollbar = target.verticalScrollBar()
        if stay_at_bottom:
            scrollbar.setValue(scrollbar.maximum())
        else:
            target_value = int(round((old_value / old_max) * max(scrollbar.maximum(), 0)))
            scrollbar.setValue(max(0, min(target_value, scrollbar.maximum())))
        append_session_stats_log_snapshot_for(window, text)
    window._last_session_stats_refresh_ts = now


def build_session_statistics_text_for(window) -> str:
    live_estimate = _plain_text(window.live_estimate.text() if hasattr(window, "live_estimate") else "")
    telemetry = _plain_text(window.telemetry_label.text() if hasattr(window, "telemetry_label") else "")
    spectrum_stats = _plain_text(window.spectrum_stats_label.text() if hasattr(window, "spectrum_stats_label") else "")
    spectrum_cursor = _plain_text(window.spectrum_cursor_label.text() if hasattr(window, "spectrum_cursor_label") else "")
    trace_stats = _plain_text(window.trace_stats_label.text() if hasattr(window, "trace_stats_label") else "")
    trace_noise = _plain_text(window.trace_noise_summary_label.text() if hasattr(window, "trace_noise_summary_label") else "")
    trace_cursor = _plain_text(window.trace_cursor_label.text() if hasattr(window, "trace_cursor_label") else "")
    skip_rate = window._live_skip_rate_hz() if hasattr(window, "_live_skip_rate_hz") else 0.0
    display_rate_text = f"{float(window.live_rate_spin.value()):.2f} Hz" if hasattr(window, "live_rate_spin") else "-"
    processing_text = "-" if getattr(window, "_last_processing_ms", None) is None else f"{window._last_processing_ms:.1f} ms"
    headroom_value = getattr(window, "_processing_headroom_ratio", None)
    headroom_text = "-" if headroom_value is None else f"{float(headroom_value):.2f}x"
    wait_text = "-"
    if getattr(window, "_last_processing_queue_wait_ms", None) is not None:
        wait_text = f"{window._last_processing_queue_wait_ms:.1f} ms"
    return "\n".join(
        [
            "GUI",
            f"  Display refresh rate: {display_rate_text}",
            f"  Live estimate: {live_estimate or '-'} | skip {skip_rate:.1f} Hz",
            "",
            "Processing",
            f"  Processing time: {processing_text}",
            f"  Queue wait: {wait_text}",
            f"  Headroom: {headroom_text}",
            "",
            "Acquisition",
            f"  Telemetry: {telemetry or '-'}",
            "",
            "Spectrum",
            f"  Stats: {spectrum_stats or '-'}",
            f"  Cursor: {spectrum_cursor or '-'}",
            "",
            "Trace",
            f"  Stats: {trace_stats or '-'}",
            f"  Noise: {trace_noise or '-'}",
            f"  Cursor: {trace_cursor or '-'}",
        ]
    )


def append_session_stats_log_snapshot_for(window, text: str | None = None) -> None:
    if not getattr(window, "_measurement_active", False):
        return
    log = getattr(window, "_session_stats_log", None)
    if log is None:
        return
    now = perf_counter()
    last_at = float(getattr(window, "_session_stats_log_last_capture_ts", 0.0) or 0.0)
    if text is None:
        text = build_session_statistics_text_for(window)
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
