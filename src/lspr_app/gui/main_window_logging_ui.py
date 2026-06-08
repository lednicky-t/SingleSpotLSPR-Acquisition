from __future__ import annotations

from collections import deque
import logging

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QPushButton, QTextEdit, QToolButton

from lspr_app.diagnostics import DiagnosticsConfig
from lspr_app.gui.logging_utils import GuiLogBridge, GuiLogHandler, SUCCESS_LOG_LEVEL


class LogTerminalTextEdit(QTextEdit):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._min_font_size = 7.0
        self._max_font_size = 16.0

    def wheelEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta_y = event.angleDelta().y()
            if delta_y == 0:
                event.ignore()
                return
            factor = 1.1 if delta_y > 0 else 1 / 1.1
            current_font = QFont(self.font())
            current_size = float(current_font.pointSizeF())
            if current_size <= 0:
                current_size = float(current_font.pointSize()) if current_font.pointSize() > 0 else 9.0
            if current_size <= 0:
                current_size = 9.0
            font = QFont(current_font)
            new_size = max(self._min_font_size, min(current_size * factor, self._max_font_size))
            if new_size <= 0:
                new_size = self._min_font_size
            font.setPointSizeF(new_size)
            self.setFont(font)
            self.document().setDefaultFont(font)
            event.accept()
            return
        super().wheelEvent(event)


def initialize_logging_ui_for(window) -> None:
    if hasattr(window, "log_terminal"):
        return
    window.log_terminal = LogTerminalTextEdit()
    window.log_terminal.setObjectName("logTerminal")
    window.log_terminal.setReadOnly(True)
    window.log_terminal.setAcceptRichText(True)
    window.log_terminal.setUndoRedoEnabled(False)
    window.log_terminal.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
    window.log_terminal.setMaximumHeight(190)
    window.log_terminal.setMinimumHeight(150)
    log_font = QFont("Consolas", 9)
    window.log_terminal.setFont(log_font)
    window.log_terminal.document().setDefaultFont(log_font)
    window.log_terminal.document().setMaximumBlockCount(300)
    window.log_terminal.setToolTip("Live event log for acquisition, processing, and controller activity.")

    window._log_history: list[tuple[int, str, str]] = []
    window._log_history_max_entries = 1000
    window._log_view_mode = "all"
    window.log_view_all_button = QToolButton()
    window.log_view_all_button.setObjectName("logViewButton")
    window.log_view_all_button.setText("All")
    window.log_view_all_button.setCheckable(True)
    window.log_view_all_button.setAutoRaise(False)
    window.log_view_all_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    window.log_view_all_button.setFixedHeight(22)
    window.log_view_all_button.setToolTip("Show all log entries.")
    window.log_view_all_button.clicked.connect(lambda *_args: window._set_log_view_mode("all"))
    window.log_view_gui_button = QToolButton()
    window.log_view_gui_button.setObjectName("logViewButton")
    window.log_view_gui_button.setText("GUI")
    window.log_view_gui_button.setCheckable(True)
    window.log_view_gui_button.setAutoRaise(False)
    window.log_view_gui_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    window.log_view_gui_button.setFixedHeight(22)
    window.log_view_gui_button.setToolTip("Show GUI, processing, and analysis messages.")
    window.log_view_gui_button.clicked.connect(lambda *_args: window._set_log_view_mode("gui"))
    window.log_view_devices_button = QToolButton()
    window.log_view_devices_button.setObjectName("logViewButton")
    window.log_view_devices_button.setText("Devices")
    window.log_view_devices_button.setCheckable(True)
    window.log_view_devices_button.setAutoRaise(False)
    window.log_view_devices_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    window.log_view_devices_button.setFixedHeight(22)
    window.log_view_devices_button.setToolTip("Show spectrometer, pump, valve, and switch messages.")
    window.log_view_devices_button.clicked.connect(lambda *_args: window._set_log_view_mode("devices"))
    window._set_log_view_mode("all", refresh=False)

    window.log_clear_button = QPushButton("Clear")
    window.log_clear_button.setToolTip("Clear the visible log view.")
    window.log_clear_button.setFixedHeight(24)
    window.log_follow_button = QPushButton("Follow")
    window.log_follow_button.setCheckable(True)
    window.log_follow_button.setChecked(True)
    window.log_follow_button.setToolTip("Keep the log scrolled to the newest entry.")
    window.log_follow_button.setFixedHeight(24)
    window.log_copy_button = QPushButton("Copy")
    window.log_copy_button.setToolTip("Copy the visible log text to the clipboard.")
    window.log_copy_button.setFixedHeight(24)
    window._ui_logger = logging.getLogger("lspr_app")
    window._ui_logger.setLevel(logging.INFO)
    window._ui_logger.propagate = True
    window._diagnostics = DiagnosticsConfig.from_env()
    window._quiet_diagnostics_mode = window._diagnostics.quiet_mode
    window._suppress_diagnostic_info_logs = window._diagnostics.suppress_info_logs
    window._export_diagnostic_events = window._diagnostics.export_diagnostic_events
    window._log_follow_enabled = not window._quiet_diagnostics_mode
    window._ui_logger.info(
        "Startup diagnostics flags resolved | quiet=%s | file_info=%s | diag_export=%s",
        "on" if window._quiet_diagnostics_mode else "off",
        "off" if window._suppress_diagnostic_info_logs else "on",
        "on" if window._export_diagnostic_events else "off",
    )
    window._log_bridge = None
    window._log_handler = None
    if not window._quiet_diagnostics_mode:
        window._log_bridge = GuiLogBridge()
        window._log_bridge.record_received.connect(window._append_log_record)
        window._log_handler = GuiLogHandler(window._log_bridge)
        window._log_handler.setFormatter(logging.Formatter("%(message)s"))
        window._ui_logger.addHandler(window._log_handler)
    window._log_buffer: list[tuple[int, str, str]] = []
    window._log_buffer_timer = QTimer(window)
    window._log_buffer_timer.setInterval(250)
    window._log_buffer_timer.timeout.connect(window._flush_log_buffer)
    window._last_log_buffer_flush_ms: float | None = None
    window._last_log_buffer_delay_ms: float | None = None
    window._last_log_buffer_total_ms: float | None = None
    window._log_buffer_requested_at: float | None = None
    window._diagnostic_export_buffer: list[dict[str, object]] = []
    window._diagnostic_export_path = None
    window._last_diagnostic_export_flush_ms: float | None = None
    window._diagnostic_snapshot_export_events = deque(maxlen=4000)
    window._diagnostic_snapshot_export_last_ts = 0.0
    window._diagnostic_snapshot_export_interval_s = 2.5
    window._last_diagnostic_snapshot_export_ms: float | None = None
    window._log_throttle_state: dict[str, tuple[float, str]] = {}
    window._log_emit_levels = {
        logging.INFO,
        SUCCESS_LOG_LEVEL,
        logging.WARNING,
        logging.ERROR,
        logging.CRITICAL,
    }
    if window._quiet_diagnostics_mode:
        window._log_buffering_enabled = False
        window._log_emit_levels = {logging.WARNING, logging.ERROR, logging.CRITICAL}
    window._ui_startup_ready = False
    window._ui_heartbeat_expected_at = window._startup_t0 + window._ui_heartbeat_interval_ms / 1000.0
    window._ui_heartbeat_timer.start()
