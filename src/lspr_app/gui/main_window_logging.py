from __future__ import annotations

import logging
from datetime import datetime
from time import perf_counter

from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import QApplication

from lspr_app.gui.logging_utils import SUCCESS_LOG_LEVEL

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
    if window.session_summary is None:
        return
    now = perf_counter()
    if not force and (now - window._last_summary_refresh_ts) < 1.0:
        return
    scrollbar = window.session_summary.verticalScrollBar()
    old_value = scrollbar.value()
    old_max = max(scrollbar.maximum(), 1)
    stay_at_bottom = old_value >= max(old_max - 2, 0)
    text = window._build_summary_text()
    if force or text != window._last_summary_text:
        window.session_summary.setPlainText(text)
        window._last_summary_text = text
        scrollbar = window.session_summary.verticalScrollBar()
        if stay_at_bottom:
            scrollbar.setValue(scrollbar.maximum())
        else:
            target = int(round((old_value / old_max) * max(scrollbar.maximum(), 0)))
            scrollbar.setValue(max(0, min(target, scrollbar.maximum())))
    window._last_summary_refresh_ts = now


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
