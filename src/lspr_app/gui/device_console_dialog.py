from __future__ import annotations

from dataclasses import asdict

from PyQt6.QtCore import Qt, QTimer, QRunnable, QObject, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QWidget,
    QVBoxLayout,
)

from lspr_app.device.amf_mswitch import amf_tools_available, detect_amf_selector_devices
from lspr_app.device.communication_models import DeviceCommand, DeviceEvent, DeviceProfile, next_device_label, new_device_profile
from lspr_app.device.device_lifecycle import DeviceLifecycleController, DeviceLifecycleEvent, device_label_for
from lspr_app.device.device_manager import DeviceCommunicationService, extract_usb_fingerprint
from lspr_app.device.device_types import PUMP, SELECTOR, SWITCH
from lspr_app.device.port_assignments import device_assignment_label, set_port_assignment
from lspr_app.device.probe_diagnostics import snapshot_port_probe_events
from lspr_app.device.reglo_icc import RegloICCClient, is_probable_reglo_port
from lspr_app.device.serial_controllers import SerialController, controller_port_priority
from lspr_app.gui.device_lifecycle_task import DeviceConnectTask, DeviceDisconnectTask, device_io_pool

# Connect/Disconnect for these three labels must go through DeviceLifecycleController,
# not DeviceCommunicationService directly - it is the single owner of connect/disconnect
# for the app's canonical pump/valve/selector (post-connect hooks like selector homing,
# and the single-lane device I/O pool, only run through it). Other, non-canonical
# profiles (e.g. ones created ad hoc via Probe/assign) keep using the service directly -
# this dialog's generic multi-profile management is legitimately out of that model's scope.
_CANONICAL_LABEL_TO_DEVICE_KEY = {device_label_for(key): key for key in (PUMP, SWITCH, SELECTOR)}


class _TaskSignals(QObject):
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(str)


class _RefreshTask(QRunnable):
    def __init__(self, func) -> None:
        super().__init__()
        self._func = func
        self.signals = _TaskSignals()

    def run(self) -> None:
        try:
            result = self._func()
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(result)


class _DiscoverTask(QRunnable):
    """Scan all serial ports, register new devices via find_or_create_profile, and connect."""

    def __init__(self, service: DeviceCommunicationService) -> None:
        super().__init__()
        self._service = service
        self.signals = _TaskSignals()

    def run(self) -> None:
        found: list[str] = []
        configured_endpoints = {p.endpoint for p in self._service.list_profiles() if p.endpoint}

        self.signals.progress.emit("Scanning for pumps…")
        for port in RegloICCClient.list_ports():
            if not is_probable_reglo_port(port) or port.device in configured_endpoints:
                continue
            try:
                probe = RegloICCClient.probe_port(port.device)
            except Exception:
                continue
            sn = str(probe.serial_number or "").strip()
            fingerprint = f"reglo-icc:{sn}" if sn and sn.upper() not in {"#", "*", ""} else extract_usb_fingerprint(port.hwid)
            profile = self._service.find_or_create_profile(
                device_type="pump",
                fingerprint=fingerprint,
                endpoint=port.device,
                identity={
                    "model": probe.model,
                    "serial_number": probe.serial_number,
                    "protocol_version": probe.protocol_version,
                    "channel_count": str(probe.channel_count),
                },
                driver="reglo_icc",
            )
            try:
                self._service.connect(profile.label)
                found.append(profile.label)
            except Exception:
                pass

        self.signals.progress.emit("Scanning for switch controllers…")
        for port in SerialController.list_ports():
            if controller_port_priority(port) <= 0 or port.device in configured_endpoints:
                continue
            try:
                result = self._service.probe_endpoint(port.device, "switch")
            except Exception:
                continue
            if not result.success:
                continue
            fingerprint = extract_usb_fingerprint(port.hwid)
            profile = self._service.find_or_create_profile(
                device_type="switch",
                fingerprint=fingerprint,
                endpoint=port.device,
                identity={k: result.identity.get(k, "") for k in ("model", "serial_number", "protocol_version", "controller_type")},
                driver=result.driver or "auto",
            )
            try:
                self._service.connect(profile.label)
                found.append(profile.label)
            except Exception:
                pass

        if amf_tools_available():
            self.signals.progress.emit("Scanning for selectors…")
            for device in detect_amf_selector_devices():
                port_name = getattr(device, "port", "")
                if port_name in configured_endpoints:
                    continue
                sn = getattr(device, "serial_number", None) or ""
                fingerprint = f"amf-selector:{sn}" if sn else extract_usb_fingerprint(getattr(device, "hwid", ""))
                profile = self._service.find_or_create_profile(
                    device_type="selector",
                    fingerprint=fingerprint,
                    endpoint=port_name,
                    identity={
                        "model": getattr(device, "model", ""),
                        "serial_number": sn,
                        "protocol_version": getattr(device, "protocol_version", "") or "",
                        "controller_type": getattr(device, "controller_type", "amf-mswitch"),
                    },
                    driver="amf-mswitch",
                )
                try:
                    self._service.connect(profile.label)
                    found.append(profile.label)
                except Exception:
                    pass

        self.signals.finished.emit(found)


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


def _format_device_events(events: list[DeviceEvent]) -> str:
    if not events:
        return "No device events recorded yet."
    lines: list[str] = []
    for event in events:
        timestamp = f"{event.timestamp_s:.3f}"
        lines.append(
            f"{timestamp} | {event.label or '-'} | {event.endpoint or '-'} | {event.owner} | "
            f"{event.action} | {event.command or '-'} | {event.result} | {event.duration_ms:.1f} ms | {event.message or '-'}"
        )
    return "\n".join(lines)


class DeviceManagerDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Device Manager")
        self.setObjectName("deviceManagerDialog")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(1180, 760)
        self._service = getattr(parent, "_device_comm_service", None)
        if not isinstance(self._service, DeviceCommunicationService):
            self._service = DeviceCommunicationService.shared()
            if parent is not None:
                parent._device_comm_service = self._service

        self._task: _RefreshTask | None = None
        self._discover_task: _DiscoverTask | None = None

        self._tabs = QTabWidget(self)
        self._profiles_table = QTableWidget(0, 8, self)
        self._port_list_table = QTableWidget(0, 6, self)
        self._connected_table = QTableWidget(0, 8, self)
        self._log_output = QPlainTextEdit(self)
        self._log_output.setReadOnly(True)
        self._log_output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        self._build_connected_tab()
        self._build_profiles_tab()
        self._build_probe_assign_tab()
        self._build_command_tab()
        self._build_log_tab()
        self._build_port_list_tab()

        self._connected_tab_index = 0

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(2000)
        self._refresh_timer.timeout.connect(self.refresh_connected_devices)
        self._tabs.currentChanged.connect(self._on_tab_changed)

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

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._tabs.currentIndex() == self._connected_tab_index:
            self._refresh_timer.start()

    def hideEvent(self, event) -> None:
        super().hideEvent(event)
        self._refresh_timer.stop()

    def closeEvent(self, event) -> None:
        self._refresh_timer.stop()
        super().closeEvent(event)

    def _on_tab_changed(self, index: int) -> None:
        if index == self._connected_tab_index:
            self._refresh_timer.start()
        else:
            self._refresh_timer.stop()

    def _build_connected_tab(self) -> None:
        self._connected_table.setHorizontalHeaderLabels(
            ["Label", "Display name", "Type", "Endpoint", "Connected", "State", "Fingerprint", "Last error"]
        )
        self._connected_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._connected_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._connected_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)

        self._discover_button = QPushButton("Scan && connect")
        self._discover_button.setToolTip(
            "Scan all serial ports, register new devices, and connect them.\n"
            "Skips ports that already have a configured profile."
        )
        self._discover_button.clicked.connect(self._run_discover)
        self._discover_status = QLabel("")

        self._connect_button = QPushButton("Connect")
        self._connect_button.clicked.connect(self._connect_selected_profile)
        self._disconnect_button = QPushButton("Disconnect")
        self._disconnect_button.clicked.connect(self._disconnect_selected_profile)
        self._test_button = QPushButton("Test")
        self._test_button.clicked.connect(self._test_selected_profile)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_connected_devices)

        top_row = QHBoxLayout()
        top_row.addWidget(self._discover_button)
        top_row.addWidget(self._discover_status)
        top_row.addStretch(1)

        bottom_row = QHBoxLayout()
        bottom_row.addWidget(self._connect_button)
        bottom_row.addWidget(self._disconnect_button)
        bottom_row.addWidget(self._test_button)
        bottom_row.addWidget(refresh_btn)
        bottom_row.addStretch(1)

        layout = QVBoxLayout()
        layout.addLayout(top_row)
        layout.addWidget(self._connected_table, 1)
        layout.addLayout(bottom_row)
        page = self._wrap_layout(layout)
        self._tabs.addTab(page, "Connected devices")

    def _build_profiles_tab(self) -> None:
        self._profiles_table.setHorizontalHeaderLabels(
            ["Label", "Type", "Driver", "Endpoint", "Role", "Display name", "Enabled", "Fingerprint"]
        )
        self._profiles_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._profiles_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._profiles_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._profiles_table.itemSelectionChanged.connect(self._load_selected_profile_into_editor)

        self._profile_label_edit = QLineEdit(self)
        self._profile_type_edit = QLineEdit(self)
        self._profile_driver_edit = QLineEdit(self)
        self._profile_endpoint_edit = QLineEdit(self)
        self._profile_role_edit = QLineEdit(self)
        self._profile_display_name_edit = QLineEdit(self)
        self._profile_enabled_check = QCheckBox("Enabled", self)
        self._profile_fingerprint_edit = QLineEdit(self)
        self._profile_fingerprint_edit.setReadOnly(True)
        self._profile_fingerprint_edit.setPlaceholderText("auto-assigned on first connect")

        refresh_button = QPushButton("Refresh profiles")
        refresh_button.clicked.connect(self.refresh_profiles)
        new_button = QPushButton("New")
        new_button.clicked.connect(self._new_profile_from_editor)
        save_button = QPushButton("Save")
        save_button.clicked.connect(self._save_profile_from_editor)
        delete_button = QPushButton("Delete")
        delete_button.clicked.connect(self._delete_selected_profile)
        connect_button = QPushButton("Connect")
        connect_button.clicked.connect(self._connect_selected_profile_from_profiles_tab)
        disconnect_button = QPushButton("Disconnect")
        disconnect_button.clicked.connect(self._disconnect_selected_profile_from_profiles_tab)
        test_button = QPushButton("Test")
        test_button.clicked.connect(self._test_selected_profile_from_profiles_tab)

        form = QFormLayout()
        form.addRow("Label", self._profile_label_edit)
        form.addRow("Type", self._profile_type_edit)
        form.addRow("Driver", self._profile_driver_edit)
        form.addRow("Endpoint", self._profile_endpoint_edit)
        form.addRow("Role", self._profile_role_edit)
        form.addRow("Display name", self._profile_display_name_edit)
        form.addRow("", self._profile_enabled_check)
        form.addRow("Fingerprint", self._profile_fingerprint_edit)

        row = QHBoxLayout()
        row.addWidget(refresh_button)
        row.addWidget(new_button)
        row.addWidget(save_button)
        row.addWidget(delete_button)
        row.addWidget(connect_button)
        row.addWidget(disconnect_button)
        row.addWidget(test_button)
        row.addStretch(1)

        layout = QVBoxLayout()
        layout.addLayout(row)
        layout.addLayout(form)
        layout.addWidget(self._profiles_table, 1)
        page = self._wrap_layout(layout)
        self._tabs.addTab(page, "Profiles")

    def _build_port_list_tab(self) -> None:
        self._port_list_table.setHorizontalHeaderLabels(["Port", "Description", "HWID", "Assignment", "Owner", "Last probe"])
        self._port_list_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._port_list_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._port_list_table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)

        refresh_button = QPushButton("Refresh ports")
        refresh_button.clicked.connect(self.refresh_port_list)

        row = QHBoxLayout()
        row.addWidget(refresh_button)
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
            ("Switch", "switch"),
            ("Selector", "selector"),
        ):
            self._probe_type_combo.addItem(label, value)
        self._probe_label_edit = QLineEdit(self)
        self._probe_label_edit.setPlaceholderText("label, e.g. pump_1")
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

    def _build_command_tab(self) -> None:
        self._command_label_combo = QComboBox(self)
        self._command_type_combo = QComboBox(self)
        self._command_payload = QLineEdit(self)
        self._command_payload.setPlaceholderText("optional payload or JSON-like text")
        self._command_result = QPlainTextEdit(self)
        self._command_result.setReadOnly(True)
        self._command_result.setPlaceholderText("Command result appears here.")

        for label, value in (
            ("Pump stop all", "pump.stop_all"),
            ("Pump start", "pump.start"),
            ("Pump stop", "pump.stop"),
            ("Pump set flow", "pump.set_flow"),
            ("Switch set left", "switch.set_position:left"),
            ("Switch set right", "switch.set_position:right"),
            ("Switch stop", "switch.stop"),
            ("Selector home", "switch.home"),
            ("Selector move 1", "switch.move_to:1"),
            ("Selector move 2", "switch.move_to:2"),
            ("Selector get position", "switch.get_position"),
        ):
            self._command_type_combo.addItem(label, value)

        self._send_command_button = QPushButton("Send command")
        self._send_command_button.clicked.connect(self._send_command)

        form = QFormLayout()
        form.addRow("Device label", self._command_label_combo)
        form.addRow("Preset command", self._command_type_combo)
        form.addRow("Extra payload", self._command_payload)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self._send_command_button)
        layout.addWidget(self._command_result, 1)
        page = self._wrap_layout(layout)
        self._tabs.addTab(page, "Commands")

    def _build_log_tab(self) -> None:
        self._log_refresh_button = QPushButton("Refresh")
        self._log_refresh_button.clicked.connect(self.refresh_probe_log)
        self._log_copy_button = QPushButton("Copy")
        self._log_copy_button.clicked.connect(self._copy_probe_log)

        row = QHBoxLayout()
        row.addWidget(self._log_refresh_button)
        row.addWidget(self._log_copy_button)
        row.addStretch(1)

        layout = QVBoxLayout()
        layout.addLayout(row)
        layout.addWidget(self._log_output, 1)
        page = self._wrap_layout(layout)
        self._tabs.addTab(page, "Event log")

    def _wrap_layout(self, layout) -> QWidget:
        page = QWidget(self)
        page.setLayout(layout)
        return page

    # -------------------------------------------------------------------------
    # Discover (Scan & connect)
    # -------------------------------------------------------------------------

    def _run_discover(self) -> None:
        if self._discover_task is not None:
            return
        self._discover_button.setEnabled(False)
        self._discover_status.setText("Scanning…")
        task = _DiscoverTask(self._service)
        task.signals.progress.connect(self._on_discover_progress)
        task.signals.finished.connect(self._on_discover_finished)
        task.signals.failed.connect(self._on_discover_failed)
        self._discover_task = task
        device_io_pool().start(task)

    def _on_discover_progress(self, message: str) -> None:
        self._discover_status.setText(message)

    def _on_discover_finished(self, found: object) -> None:
        self._discover_task = None
        self._discover_button.setEnabled(True)
        labels = list(found) if found else []
        if labels:
            self._discover_status.setText(f"Found: {', '.join(labels)}")
        else:
            self._discover_status.setText("No new devices found.")
        self.refresh_connected_devices()
        self.refresh_profiles()
        self.refresh_port_list()

    def _on_discover_failed(self, message: str) -> None:
        self._discover_task = None
        self._discover_button.setEnabled(True)
        self._discover_status.setText(f"Scan failed: {message}")

    # -------------------------------------------------------------------------
    # Refresh
    # -------------------------------------------------------------------------

    def refresh_all(self) -> None:
        self.refresh_profiles()
        self.refresh_port_list()
        self.refresh_connected_devices()
        self.refresh_probe_log()

    def refresh_profiles(self) -> None:
        profiles = self._service.list_profiles()
        self._profiles_table.setRowCount(len(profiles))
        for row, profile in enumerate(profiles):
            fingerprint = str(getattr(profile, "fingerprint", "") or "")
            values = (
                profile.label,
                profile.type,
                profile.driver,
                profile.endpoint or "",
                profile.role or "",
                profile.display_name or "",
                "yes" if profile.enabled else "no",
                fingerprint[:24] if len(fingerprint) > 24 else fingerprint,
            )
            for column, value in enumerate(values):
                self._profiles_table.setItem(row, column, QTableWidgetItem(str(value)))
        self._profiles_table.resizeColumnsToContents()
        self._load_selected_profile_into_editor()

    def _selected_profile_label(self) -> str | None:
        selected = self._profiles_table.selectedItems()
        if not selected:
            return None
        return str(selected[0].text()).strip() or None

    def _load_selected_profile_into_editor(self) -> None:
        label = self._selected_profile_label()
        profile = self._service.get_profile(label) if label else None
        if profile is None:
            self._profile_label_edit.clear()
            self._profile_type_edit.clear()
            self._profile_driver_edit.clear()
            self._profile_endpoint_edit.clear()
            self._profile_role_edit.clear()
            self._profile_display_name_edit.clear()
            self._profile_enabled_check.setChecked(True)
            self._profile_fingerprint_edit.clear()
            return
        self._profile_label_edit.setText(profile.label)
        self._profile_type_edit.setText(profile.type)
        self._profile_driver_edit.setText(profile.driver)
        self._profile_endpoint_edit.setText(profile.endpoint or "")
        self._profile_role_edit.setText(profile.role or "")
        self._profile_display_name_edit.setText(profile.display_name or "")
        self._profile_enabled_check.setChecked(bool(profile.enabled))
        self._profile_fingerprint_edit.setText(str(getattr(profile, "fingerprint", "") or ""))

    def _profile_from_editor(self) -> DeviceProfile:
        label = self._profile_label_edit.text().strip()
        device_type = self._profile_type_edit.text().strip() or "unknown"
        driver = self._profile_driver_edit.text().strip() or "auto"
        endpoint = self._profile_endpoint_edit.text().strip() or None
        role = self._profile_role_edit.text().strip() or None
        display_name = self._profile_display_name_edit.text().strip() or None
        enabled = self._profile_enabled_check.isChecked()
        profile = self._service.get_profile(label) if label else None
        if profile is None:
            profile = new_device_profile(label=label or "device_1", type=device_type, driver=driver, endpoint=endpoint, role=role, display_name=display_name)
        return DeviceProfile(
            uuid=profile.uuid,
            label=label or profile.label,
            type=device_type,
            driver=driver,
            endpoint=endpoint,
            role=role,
            display_name=display_name,
            identity=dict(profile.identity),
            enabled=enabled,
            metadata=dict(profile.metadata),
            fingerprint=str(getattr(profile, "fingerprint", "") or "").strip(),
        )

    def _save_profile_from_editor(self) -> None:
        profile = self._profile_from_editor()
        self._service.save_profile(profile)
        self.refresh_profiles()
        self.refresh_connected_devices()
        self.refresh_port_list()

    def _new_profile_from_editor(self) -> None:
        existing = {profile.label for profile in self._service.list_profiles()}
        label = next_device_label(existing, "device")
        self._profile_label_edit.setText(label)
        self._profile_type_edit.setText("device")
        self._profile_driver_edit.setText("auto")
        self._profile_endpoint_edit.clear()
        self._profile_role_edit.clear()
        self._profile_display_name_edit.clear()
        self._profile_fingerprint_edit.clear()
        self._profile_enabled_check.setChecked(True)

    def _delete_selected_profile(self) -> None:
        label = self._selected_profile_label()
        if not label:
            return
        self._service.delete_profile(label)
        self.refresh_profiles()
        self.refresh_connected_devices()
        self.refresh_port_list()

    def _connect_selected_profile_from_profiles_tab(self) -> None:
        label = self._selected_profile_label()
        if not label:
            QMessageBox.information(self, "Connect", "Select a profile first.")
            return
        device_key = _CANONICAL_LABEL_TO_DEVICE_KEY.get(label)
        if device_key is not None:
            self._connect_canonical_device(device_key, label)
            return
        self._connect_generic_profile(label)

    def _disconnect_selected_profile_from_profiles_tab(self) -> None:
        label = self._selected_profile_label()
        if not label:
            QMessageBox.information(self, "Disconnect", "Select a profile first.")
            return
        device_key = _CANONICAL_LABEL_TO_DEVICE_KEY.get(label)
        if device_key is not None:
            self._disconnect_canonical_device(device_key)
            return
        self._disconnect_generic_profile(label)

    # -------------------------------------------------------------------------
    # Non-canonical connect/disconnect - any profile other than the single
    # canonical pump/switch/selector (aux/waste pump, second switch, or
    # anything created via Probe/assign). These call DeviceCommunicationService
    # directly rather than DeviceLifecycleController (which only knows about
    # the 3 canonical devices), but must still run off the GUI thread since
    # connect/disconnect do real hardware I/O - reuses the existing, previously
    # unused _RefreshTask + device_io_pool(), the same dedicated single-lane
    # pool the canonical path uses.
    # -------------------------------------------------------------------------

    def _connect_generic_profile(self, label: str) -> None:
        task = _RefreshTask(lambda: self._service.connect(label))
        task.signals.finished.connect(lambda _status: self._on_generic_device_task_done())
        task.signals.failed.connect(lambda message: self._on_generic_device_task_failed("Connect failed", message))
        device_io_pool().start(task)

    def _disconnect_generic_profile(self, label: str) -> None:
        task = _RefreshTask(lambda: self._service.disconnect(label))
        task.signals.finished.connect(lambda _status: self._on_generic_device_task_done())
        task.signals.failed.connect(lambda message: self._on_generic_device_task_failed("Disconnect failed", message))
        device_io_pool().start(task)

    def _on_generic_device_task_done(self) -> None:
        self.refresh_connected_devices()
        self.refresh_profiles()

    def _on_generic_device_task_failed(self, title: str, message: str) -> None:
        QMessageBox.warning(self, title, message)
        self.refresh_connected_devices()
        self.refresh_profiles()

    def _test_selected_profile_from_profiles_tab(self) -> None:
        label = self._selected_profile_label()
        if not label:
            QMessageBox.information(self, "Test", "Select a profile first.")
            return
        try:
            status = self._service.status(label)
        except Exception as exc:
            QMessageBox.warning(self, "Test failed", str(exc))
            return
        QMessageBox.information(self, "Device status", str(asdict(status)))

    def refresh_port_list(self) -> None:
        ports = self._service.scan_passive()
        self._port_list_table.setRowCount(len(ports))
        self._probe_port_combo.blockSignals(True)
        self._probe_port_combo.clear()
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
        self._probe_port_combo.blockSignals(False)
        self._port_list_table.resizeColumnsToContents()

    def refresh_connected_devices(self) -> None:
        devices = self._service.list_devices()
        self._connected_table.setRowCount(len(devices))
        profiles = {p.label: p for p in self._service.list_profiles()}
        for row, status in enumerate(devices):
            profile = profiles.get(status.label)
            fingerprint = str(getattr(profile, "fingerprint", "") or "") if profile else ""
            values = (
                status.label,
                status.display_name or "",
                status.type,
                status.endpoint or "",
                "yes" if status.connected else "no",
                status.state,
                fingerprint[:20] if len(fingerprint) > 20 else fingerprint,
                status.last_error or "",
            )
            for column, value in enumerate(values):
                self._connected_table.setItem(row, column, QTableWidgetItem(str(value)))
        self._connected_table.resizeColumnsToContents()

        # The Commands tab's device dropdown belongs to device state, not the
        # port scan - refreshed here (called after every connect/disconnect,
        # canonical or generic) instead of refresh_port_list() so it never
        # goes stale after a normal Connect/Disconnect click.
        self._command_label_combo.blockSignals(True)
        self._command_label_combo.clear()
        for status in devices:
            self._command_label_combo.addItem(status.label, status.label)
        self._command_label_combo.blockSignals(False)

    def refresh_probe_log(self) -> None:
        sections: list[str] = []
        events: list[DeviceEvent] = []
        if hasattr(self._service, "list_events"):
            try:
                events = list(self._service.list_events())
            except Exception:
                events = []
        sections.append("=== Device events ===")
        sections.append(_format_device_events(events))
        sections.append("")
        sections.append("=== USB probe events ===")
        sections.append(_format_probe_log())
        self._log_output.setPlainText("\n".join(sections))
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

    def _selected_connected_label(self) -> str | None:
        selected = self._connected_table.selectedItems()
        if not selected:
            return None
        return str(selected[0].text()).strip() or None

    def _probe_selected_endpoint(self) -> None:
        endpoint = self._selected_port()
        if not endpoint:
            QMessageBox.information(self, "Probe", "Select an endpoint first.")
            return
        expected = str(self._probe_type_combo.currentData() or "auto")
        self._probe_button.setEnabled(False)
        task = _RefreshTask(lambda: self._service.probe_endpoint(endpoint, expected))
        task.signals.finished.connect(self._on_probe_finished)
        task.signals.failed.connect(self._on_probe_failed)
        device_io_pool().start(task)

    def _on_probe_finished(self, result: object) -> None:
        self._probe_button.setEnabled(True)
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

    def _on_probe_failed(self, message: str) -> None:
        self._probe_button.setEnabled(True)
        self._probe_result.setPlainText(f"Probe failed: {message}")
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
        label = self._selected_connected_label()
        if not label:
            QMessageBox.information(self, "Connect", "Select a device row first.")
            return
        device_key = _CANONICAL_LABEL_TO_DEVICE_KEY.get(label)
        if device_key is not None:
            self._connect_canonical_device(device_key, label)
            return
        self._connect_generic_profile(label)

    def _disconnect_selected_profile(self) -> None:
        label = self._selected_connected_label()
        if not label:
            QMessageBox.information(self, "Disconnect", "Select a device row first.")
            return
        device_key = _CANONICAL_LABEL_TO_DEVICE_KEY.get(label)
        if device_key is not None:
            self._disconnect_canonical_device(device_key)
            return
        self._disconnect_generic_profile(label)

    # -------------------------------------------------------------------------
    # Canonical pump/valve/selector connect+disconnect - routed through
    # DeviceLifecycleController so post-connect hooks (selector homing) and the
    # single-lane device I/O pool apply to manual clicks here exactly like they
    # do to the startup cycle and any other caller.
    # -------------------------------------------------------------------------

    def _connect_canonical_device(self, device_key: str, label: str) -> None:
        controller = DeviceLifecycleController.shared()
        if controller.is_busy(device_key):
            QMessageBox.information(self, "Connect", "This device is already connecting or disconnecting. Please wait.")
            return
        profile = self._service.get_profile(label)
        port = (profile.endpoint or "").strip() if profile is not None else ""
        if not port:
            QMessageBox.information(
                self,
                "Connect",
                "No known port for this device yet. Use \"Scan && connect\" or the Probe / assign tab first.",
            )
            return
        task = DeviceConnectTask(device_key, port)
        task.signals.finished.connect(self._on_canonical_device_event)
        device_io_pool().start(task)

    def _disconnect_canonical_device(self, device_key: str) -> None:
        controller = DeviceLifecycleController.shared()
        if controller.is_busy(device_key):
            QMessageBox.information(self, "Disconnect", "This device is already connecting or disconnecting. Please wait.")
            return
        task = DeviceDisconnectTask(device_key)
        task.signals.finished.connect(self._on_canonical_device_event)
        device_io_pool().start(task)

    def _on_canonical_device_event(self, event: object) -> None:
        self.refresh_connected_devices()
        self.refresh_profiles()
        if isinstance(event, DeviceLifecycleEvent) and event.error:
            QMessageBox.warning(self, "Device connect/disconnect", event.message)

    def _test_selected_profile(self) -> None:
        label = self._selected_connected_label()
        if not label:
            QMessageBox.information(self, "Test", "Select a device row first.")
            return
        try:
            status = self._service.status(label)
        except Exception as exc:
            QMessageBox.warning(self, "Test failed", str(exc))
            return
        QMessageBox.information(self, "Device status", str(asdict(status)))

    def _send_command(self) -> None:
        label = self._selected_label()
        if not label:
            QMessageBox.information(self, "Command", "Select a device label first.")
            return
        command_value = str(self._command_type_combo.currentData() or "")
        command_type, _, preset_arg = command_value.partition(":")
        payload: dict[str, object] = {}
        if preset_arg:
            if command_type == "switch.set_position":
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


def show_device_manager_dialog(window) -> DeviceManagerDialog:
    dialog = getattr(window, "_device_manager_dialog", None)
    if not isinstance(dialog, DeviceManagerDialog):
        dialog = DeviceManagerDialog(window)
        window._device_manager_dialog = dialog
    else:
        dialog.refresh_all()
    if dialog.isVisible():
        dialog.raise_()
        dialog.activateWindow()
    else:
        dialog.show()
    return dialog


# Backward-compatible aliases
DeviceConsoleDialog = DeviceManagerDialog
show_device_console_dialog = show_device_manager_dialog
