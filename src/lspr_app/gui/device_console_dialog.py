from __future__ import annotations

from dataclasses import asdict

from PyQt6.QtCore import Qt, QThreadPool, QRunnable, QObject, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from lspr_app.device.communication_models import DeviceCommand, DeviceProfile
from lspr_app.device.device_manager import DeviceCommunicationService
from lspr_app.device.port_assignments import device_assignment_label, set_port_assignment
from lspr_app.device.probe_diagnostics import snapshot_port_probe_events
from lspr_app.device.hardware_inventory import scan_connected_serial_devices


class _RefreshSignals(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)


class _RefreshTask(QRunnable):
    def __init__(self, func) -> None:
        super().__init__()
        self._func = func
        self.signals = _RefreshSignals()

    def run(self) -> None:
        try:
            result = self._func()
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(result)


def _format_probe_log() -> str:
    events = snapshot_port_probe_events()
    if not events:
        return "No USB probe events recorded yet."
    lines: list[str] = []
    for event in events:
        duration = f"{event.duration_ms:.1f} ms" if event.duration_ms else "-"
        lines.append(
            f"{event.phase} | {event.role} | {event.port} | assignment={event.assignment} | "
            f"{event.action} | {event.result} | {duration} | cmd={event.command or '-'} | owner={event.owner} | {event.message}"
        )
    return "\n".join(lines)


class DeviceConsoleDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Device Console")
        self.setObjectName("deviceConsoleDialog")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(1180, 760)

        self._thread_pool = QThreadPool.globalInstance()
        self._service = getattr(parent, "_device_comm_service", None)
        if not isinstance(self._service, DeviceCommunicationService):
            self._service = DeviceCommunicationService.shared()
            if parent is not None:
                parent._device_comm_service = self._service

        self._task: _RefreshTask | None = None

        self._tabs = QTabWidget(self)
        self._port_list_table = QTableWidget(0, 6, self)
        self._connected_table = QTableWidget(0, 6, self)
        self._probe_output = QPlainTextEdit(self)
        self._probe_output.setReadOnly(True)
        self._probe_output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._log_output = QPlainTextEdit(self)
        self._log_output.setReadOnly(True)
        self._log_output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        self._build_port_list_tab()
        self._build_probe_assign_tab()
        self._build_connected_tab()
        self._build_command_tab()
        self._build_log_tab()

        self._refresh_button = QPushButton("Refresh all")
        self._refresh_button.clicked.connect(self.refresh_all)
        self._close_button = QPushButton("Close")
        self._close_button.clicked.connect(self.close)

        button_row = QHBoxLayout()
        button_row.addWidget(self._refresh_button)
        button_row.addStretch(1)
        button_row.addWidget(self._close_button)

        layout = QVBoxLayout()
        layout.addWidget(self._tabs, 1)
        layout.addLayout(button_row)
        self.setLayout(layout)

        self.refresh_all()

    def _build_port_list_tab(self) -> None:
        self._port_list_table.setHorizontalHeaderLabels(["Port", "Description", "HWID", "Assignment", "Owner", "Last probe"])
        self._port_list_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._port_list_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._port_list_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)

        refresh_button = QPushButton("Refresh ports")
        refresh_button.clicked.connect(self.refresh_port_list)
        inventory_button = QPushButton("Open inventory")
        inventory_button.clicked.connect(self._open_inventory_dialog)

        row = QHBoxLayout()
        row.addWidget(refresh_button)
        row.addWidget(inventory_button)
        row.addStretch(1)

        layout = QVBoxLayout()
        layout.addLayout(row)
        layout.addWidget(self._port_list_table, 1)
        page = self._wrap_layout(layout)
        self._tabs.addTab(page, "Port list")

    def _build_probe_assign_tab(self) -> None:
        self._probe_port_combo = QComboBox(self)
        self._probe_type_combo = QComboBox(self)
        for label, value in (
            ("Auto", "auto"),
            ("Pump", "pump"),
            ("Valve", "valve"),
            ("M-Switch", "mswitch"),
        ):
            self._probe_type_combo.addItem(label, value)
        self._probe_label_edit = QLineEdit(self)
        self._probe_label_edit.setPlaceholderText("label, e.g. pump_main")
        self._probe_result = QPlainTextEdit(self)
        self._probe_result.setReadOnly(True)
        self._probe_result.setPlaceholderText("Probe result appears here.")

        self._probe_button = QPushButton("Probe")
        self._probe_button.clicked.connect(self._probe_selected_endpoint)
        self._assign_button = QPushButton("Assign")
        self._assign_button.clicked.connect(self._assign_selected_endpoint)

        form = QFormLayout()
        form.addRow("Endpoint", self._probe_port_combo)
        form.addRow("Expected type", self._probe_type_combo)
        form.addRow("Profile label", self._probe_label_edit)

        row = QHBoxLayout()
        row.addWidget(self._probe_button)
        row.addWidget(self._assign_button)
        row.addStretch(1)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addLayout(row)
        layout.addWidget(self._probe_result, 1)
        page = self._wrap_layout(layout)
        self._tabs.addTab(page, "Probe / assign")

    def _build_connected_tab(self) -> None:
        self._connected_table.setHorizontalHeaderLabels(["Label", "Type", "Driver", "Endpoint", "Connected", "State"])
        self._connected_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._connected_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._connected_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)

        self._connect_button = QPushButton("Connect")
        self._connect_button.clicked.connect(self._connect_selected_profile)
        self._disconnect_button = QPushButton("Disconnect")
        self._disconnect_button.clicked.connect(self._disconnect_selected_profile)
        self._test_button = QPushButton("Test")
        self._test_button.clicked.connect(self._test_selected_profile)

        row = QHBoxLayout()
        row.addWidget(self._connect_button)
        row.addWidget(self._disconnect_button)
        row.addWidget(self._test_button)
        row.addStretch(1)

        layout = QVBoxLayout()
        layout.addWidget(self._connected_table, 1)
        layout.addLayout(row)
        page = self._wrap_layout(layout)
        self._tabs.addTab(page, "Connected devices")

    def _build_command_tab(self) -> None:
        self._command_label_combo = QComboBox(self)
        self._command_type_combo = QComboBox(self)
        self._command_payload = QLineEdit(self)
        self._command_payload.setPlaceholderText("optional payload or JSON-like text")
        self._command_result = QPlainTextEdit(self)
        self._command_result.setReadOnly(True)
        self._command_result.setPlaceholderText("Command result appears here.")
        self._raw_command = QLineEdit(self)
        self._raw_command.setPlaceholderText("Raw command requires debug mode")
        self._raw_command.setEnabled(False)

        for label, value in (
            ("Pump stop all", "pump.stop_all"),
            ("Pump start", "pump.start"),
            ("Pump stop", "pump.stop"),
            ("Pump set flow", "pump.set_flow"),
            ("Valve set left", "valve.set_position:left"),
            ("Valve set right", "valve.set_position:right"),
            ("Valve stop", "valve.stop"),
            ("Switch home", "switch.home"),
            ("Switch move 1", "switch.move_to:1"),
        ):
            self._command_type_combo.addItem(label, value)

        self._send_command_button = QPushButton("Send command")
        self._send_command_button.clicked.connect(self._send_command)

        form = QFormLayout()
        form.addRow("Device label", self._command_label_combo)
        form.addRow("Preset command", self._command_type_combo)
        form.addRow("Extra payload", self._command_payload)
        form.addRow("Raw command", self._raw_command)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self._send_command_button)
        layout.addWidget(self._command_result, 1)
        page = self._wrap_layout(layout)
        self._tabs.addTab(page, "Command console")

    def _build_log_tab(self) -> None:
        self._log_refresh_button = QPushButton("Refresh log")
        self._log_refresh_button.clicked.connect(self.refresh_probe_log)
        self._log_copy_button = QPushButton("Copy log")
        self._log_copy_button.clicked.connect(self._copy_probe_log)

        row = QHBoxLayout()
        row.addWidget(self._log_refresh_button)
        row.addWidget(self._log_copy_button)
        row.addStretch(1)

        layout = QVBoxLayout()
        layout.addLayout(row)
        layout.addWidget(self._log_output, 1)
        page = self._wrap_layout(layout)
        self._tabs.addTab(page, "Probe log")

    def _wrap_layout(self, layout) -> QDialog:
        page = QDialog(self)
        page.setLayout(layout)
        return page

    def refresh_all(self) -> None:
        self.refresh_port_list()
        self.refresh_connected_devices()
        self.refresh_probe_log()

    def refresh_port_list(self) -> None:
        ports = self._service.scan_passive()
        inventory = scan_connected_serial_devices(passive=True)
        self._port_list_table.setRowCount(len(ports))
        self._probe_port_combo.blockSignals(True)
        self._probe_port_combo.clear()
        self._command_label_combo.blockSignals(True)
        self._command_label_combo.clear()
        for row, port in enumerate(ports):
            self._probe_port_combo.addItem(port.port, port.port)
            values = (
                port.port,
                port.description,
                port.hwid,
                device_assignment_label(port.assignment),
                port.owner,
                port.last_probe,
            )
            for column, value in enumerate(values):
                self._port_list_table.setItem(row, column, QTableWidgetItem(str(value)))
        for status in self._service.list_devices():
            self._command_label_combo.addItem(status.label, status.label)
        self._probe_port_combo.blockSignals(False)
        self._command_label_combo.blockSignals(False)
        if inventory:
            self._port_list_table.resizeColumnsToContents()

    def refresh_connected_devices(self) -> None:
        devices = self._service.list_devices()
        self._connected_table.setRowCount(len(devices))
        for row, status in enumerate(devices):
            values = (
                status.label,
                status.type,
                status.driver,
                status.endpoint or "",
                "yes" if status.connected else "no",
                status.state,
            )
            for column, value in enumerate(values):
                self._connected_table.setItem(row, column, QTableWidgetItem(str(value)))
        self._connected_table.resizeColumnsToContents()

    def refresh_probe_log(self) -> None:
        self._log_output.setPlainText(_format_probe_log())
        self._log_output.moveCursor(self._log_output.textCursor().MoveOperation.End)

    def _copy_probe_log(self) -> None:
        self._log_output.selectAll()
        self._log_output.copy()
        self._log_output.moveCursor(self._log_output.textCursor().MoveOperation.End)

    def _selected_port(self) -> str | None:
        index = self._probe_port_combo.currentIndex()
        if index < 0:
            return None
        return str(self._probe_port_combo.currentData() or "").strip() or None

    def _selected_label(self) -> str | None:
        index = self._command_label_combo.currentIndex()
        if index < 0:
            return None
        return str(self._command_label_combo.currentData() or "").strip() or None

    def _probe_selected_endpoint(self) -> None:
        endpoint = self._selected_port()
        if not endpoint:
            QMessageBox.information(self, "Probe", "Select an endpoint first.")
            return
        expected = str(self._probe_type_combo.currentData() or "auto")
        result = self._service.probe_endpoint(endpoint, expected)
        lines = [
            f"endpoint: {result.endpoint}",
            f"detected_type: {result.detected_type or '-'}",
            f"driver: {result.driver or '-'}",
            f"success: {result.success}",
            f"duration_ms: {result.duration_ms:.1f}",
            f"error: {result.error or '-'}",
        ]
        for key, value in result.identity.items():
            lines.append(f"{key}: {value}")
        self._probe_result.setPlainText("\n".join(lines))
        self.refresh_probe_log()

    def _assign_selected_endpoint(self) -> None:
        endpoint = self._selected_port()
        label = self._probe_label_edit.text().strip()
        if not endpoint or not label:
            QMessageBox.information(self, "Assign", "Select an endpoint and type a label.")
            return
        device_type = str(self._probe_type_combo.currentData() or "auto")
        self._service.register_endpoint_assignment(label, endpoint, device_type=device_type, driver=device_type)
        set_port_assignment(endpoint, device_assignment_label(device_type))
        self._command_label_combo.addItem(label, label)
        self.refresh_port_list()
        self.refresh_connected_devices()
        self._probe_result.appendPlainText(f"\nAssigned {endpoint} to {label}.")

    def _connect_selected_profile(self) -> None:
        label = self._selected_label()
        if not label:
            QMessageBox.information(self, "Connect", "Select a device label first.")
            return
        try:
            status = self._service.connect_device(label)
        except Exception as exc:
            QMessageBox.warning(self, "Connect failed", str(exc))
            return
        self._command_result.setPlainText(str(asdict(status)))
        self.refresh_connected_devices()

    def _disconnect_selected_profile(self) -> None:
        label = self._selected_label()
        if not label:
            QMessageBox.information(self, "Disconnect", "Select a device label first.")
            return
        self._service.disconnect_device(label)
        self.refresh_connected_devices()

    def _test_selected_profile(self) -> None:
        label = self._selected_label()
        if not label:
            QMessageBox.information(self, "Test", "Select a device label first.")
            return
        status = self._service.status(label)
        self._command_result.setPlainText(str(asdict(status)))

    def _send_command(self) -> None:
        label = self._selected_label()
        if not label:
            QMessageBox.information(self, "Command", "Select a device label first.")
            return
        command_value = str(self._command_type_combo.currentData() or "")
        command_type, _, preset_arg = command_value.partition(":")
        payload: dict[str, object] = {}
        if preset_arg:
            if command_type == "valve.set_position":
                payload["position"] = preset_arg
            elif command_type == "switch.move_to":
                payload["position"] = int(preset_arg)
        extra = self._command_payload.text().strip()
        if extra:
            payload["extra"] = extra
        if command_type == "pump.set_flow":
            payload.setdefault("channel", 1)
            payload.setdefault("flow_ul_min", 50.0)
            payload.setdefault("direction", "CW")
            payload.setdefault("tube_mm", 1.0)
        result = self._service.send_command(label, DeviceCommand(command_type, payload))
        self._command_result.setPlainText(
            "\n".join(
                [
                    f"label: {result.label}",
                    f"command: {result.command_type}",
                    f"success: {result.success}",
                    f"duration_ms: {result.duration_ms:.1f}",
                    f"response: {result.response!r}",
                    f"error: {result.error or '-'}",
                ]
            )
        )
        self.refresh_connected_devices()

    def _open_inventory_dialog(self) -> None:
        parent = self.parent()
        if parent is None:
            return
        from lspr_app.gui.hardware_inventory_dialog import show_hardware_inventory_dialog

        show_hardware_inventory_dialog(parent)


def show_device_console_dialog(window) -> DeviceConsoleDialog:
    dialog = getattr(window, "_device_console_dialog", None)
    if not isinstance(dialog, DeviceConsoleDialog):
        dialog = DeviceConsoleDialog(window)
        window._device_console_dialog = dialog
    else:
        dialog.refresh_all()
    if dialog.isVisible():
        dialog.raise_()
        dialog.activateWindow()
    else:
        dialog.show()
    return dialog
