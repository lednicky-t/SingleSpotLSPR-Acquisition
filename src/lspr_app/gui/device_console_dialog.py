from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer, QRunnable, QObject, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QStackedWidget,
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
from lspr_app.device.serial_controllers import SerialController, capabilities_for_controller_type, controller_port_priority
from lspr_app.device.simulated import SimulatedSpectrometer
from lspr_app.domain.pump_plan import ACTIVE_PUMP_CHANNELS, VALID_ROLLER_COUNTS
from lspr_app.gui.device_lifecycle_task import DeviceConnectTask, DeviceDisconnectTask, device_io_pool
from lspr_app.gui.panel_help import make_help_button
from lspr_app.gui.pump_calibration_panel import PumpCalibrationDialog
from lspr_app.gui.ui_helpers import create_status_dot_icon, make_compact_spinbox
from lspr_app.storage.app_config import load_app_setting, save_app_setting
from lspr_app.storage.device_manager_settings import DeviceManagerSettings

_SPECTROMETER = "spectrometer"
_DEVICE_PAGE_ORDER = (_SPECTROMETER, PUMP, SWITCH, SELECTOR)

_STATUS_COLOR_CONNECTED = QColor("#22c55e")
_STATUS_COLOR_DISCONNECTED = QColor("#94a3b8")
_STATUS_COLOR_ERROR = QColor("#ef4444")
# Distinct from _STATUS_COLOR_CONNECTED so the spectrometer row can't be
# mistaken for a real hardware connection while running on the simulated
# backend (no spectrometer actually plugged in) - see _refresh_spectrometer_page.
_STATUS_COLOR_SIMULATED = QColor("#f59e0b")

_DEEP_DEBUG_SETTING_KEY = "device_manager_deep_debug"

# Placeholder soft-warning threshold for the "time since last calibration"
# indicator below - the manual gives NO recommended recalibration interval
# anywhere, so this is our own guess, not an Ismatec spec. It is also in
# operating/run hours, not calendar time: the pump has no real-time clock
# (no command anywhere in the protocol sets a date), so the "xX" command it's
# based on is an elapsed-time counter, not a wall-clock date - see
# RegloICCClient.get_time_since_last_calibration_s and
# _decode_time_type_seconds for a further unit-ambiguity note. Revisit once
# we have our own calibration-history log to base this on.
PUMP_CALIBRATION_WARNING_HOURS = 100.0

_ENABLE_HELP_TOOLTIP = "Enable or disable this device type."
_ENABLE_HELP_TITLE = "Hardware devices"
_ENABLE_HELP_BODY = (
    "Choose which device types this app manages. Unchecked devices are\n"
    "skipped during hardware scans and hidden from the titlebar status strip.\n"
    "Turning a device off disconnects it immediately."
)

# Short controller-name suffix shown in the Switch row's settings popup title,
# so the maintainer can tell at a glance which firmware they're driving -
# see docs/hardware/arduino_valve_controller_protocol.md. Only "arduino-valve"
# supports the temperature/humidity sensors used below.
_SWITCH_CONTROLLER_SHORT_NAMES = {
    "arduino-valve": "Arduino",
    "itsybitsy-32u4-valve": "ItsyBitsy",
    "legacy-valve": "Legacy",
}


def _section_label(text: str) -> QLabel:
    """A small bold heading used to divide a device detail page into
    Stats/Capabilities/Defaults & limits sections without a full GroupBox."""
    return QLabel(f"<b>{text}</b>")


def device_settings_title(device_key: str) -> str:
    """Friendly device name shown atop its Device Manager settings popup."""
    if device_key == PUMP:
        return "Pump (Reglo ICC)"
    if device_key == SELECTOR:
        return "Selector rotary valve (AMF M-Switch)"
    if device_key == SWITCH:
        probe = DeviceLifecycleController.shared().probe_for(SWITCH)
        controller_type = getattr(probe, "controller_type", "") if probe is not None else ""
        short_name = _SWITCH_CONTROLLER_SHORT_NAMES.get(controller_type)
        return f"Switch valve ({short_name})" if short_name else "Switch valve"
    return device_key

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

        # Falls back to a throwaway instance (never persisted) if this dialog
        # is ever constructed without a real MainWindow parent - mirrors the
        # _service fallback above. Normal use always goes through
        # show_device_manager_dialog(window), which guarantees a real parent.
        self._device_manager_settings = getattr(parent, "_device_manager_settings", None)
        if not isinstance(self._device_manager_settings, DeviceManagerSettings):
            self._device_manager_settings = DeviceManagerSettings()

        self._task: _RefreshTask | None = None
        self._discover_task: _DiscoverTask | None = None

        self._tabs = QTabWidget(self)
        self._profiles_table = QTableWidget(0, 8, self)
        self._port_list_table = QTableWidget(0, 6, self)
        # Per-device-key widgets built once by _build_devices_tab()/the
        # _build_*_detail_page() methods, keyed by _DEVICE_PAGE_ORDER entries -
        # populated with the live status/stats labels refresh_connected_devices()
        # updates on each tick, kept separate from the "Defaults & limits" spin
        # boxes (built once, never overwritten by the periodic refresh - see
        # refresh_connected_devices() for why).
        self._device_pages: dict[str, dict[str, QWidget]] = {}
        self._log_output = QPlainTextEdit(self)
        self._log_output.setReadOnly(True)
        self._log_output.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)

        self._pump_calibration_window: PumpCalibrationDialog | None = None
        self._build_devices_tab()
        self._build_log_tab()

        self._connected_tab_index = 0

        # Advanced/developer tools live behind "Deep debug mode" (off by default) -
        # a raw profile editor, manual port probe/assign, a raw hardware command
        # console, and the full OS serial-port list are not needed for normal
        # connect/disconnect use and are easy to misuse (e.g. Commands sends raw
        # pump/switch commands straight to hardware, bypassing the experiment plan).
        self._debug_tab_specs = [
            self._build_profiles_tab(),
            self._build_probe_assign_tab(),
            self._build_command_tab(),
            self._build_pump_calibration_tab(),
            self._build_port_list_tab(),
        ]
        self._debug_tabs_visible = False

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(2000)
        self._refresh_timer.timeout.connect(self.refresh_connected_devices)
        self._tabs.currentChanged.connect(self._on_tab_changed)

        self._refresh_button = QPushButton("Refresh all")
        self._refresh_button.clicked.connect(self.refresh_all)
        self._deep_debug_check = QCheckBox("Deep debug mode", self)
        self._deep_debug_check.setToolTip(
            "Show advanced/developer tools: raw profile editor, manual port probe/assign,\n"
            "a raw hardware command console, and the full serial port list.\n"
            "Leave this off for normal day-to-day use - these can send commands straight\n"
            "to hardware and bypass the experiment plan."
        )
        self._deep_debug_check.toggled.connect(self._set_deep_debug_enabled)
        self._close_button = QPushButton("Close")
        self._close_button.clicked.connect(self.close)

        button_row = QHBoxLayout()
        button_row.addWidget(self._refresh_button)
        button_row.addWidget(self._deep_debug_check)
        button_row.addStretch(1)
        button_row.addWidget(self._close_button)

        layout = QVBoxLayout()
        layout.addWidget(self._tabs, 1)
        layout.addLayout(button_row)
        self.setLayout(layout)

        self._deep_debug_check.setChecked(bool(load_app_setting(_DEEP_DEBUG_SETTING_KEY, False)))

        self.refresh_all()

    def _set_deep_debug_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._debug_tabs_visible:
            return
        self._debug_tabs_visible = enabled
        if enabled:
            for page, title in self._debug_tab_specs:
                self._tabs.addTab(page, title)
            self.refresh_profiles()
            self.refresh_port_list()
        else:
            for page, _title in self._debug_tab_specs:
                index = self._tabs.indexOf(page)
                if index != -1:
                    self._tabs.removeTab(index)
        save_app_setting(_DEEP_DEBUG_SETTING_KEY, enabled)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._tabs.currentIndex() == self._connected_tab_index:
            self._refresh_timer.start()
        if self._selected_device_key() == PUMP:
            self._refresh_pump_calibration_status()

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

    def _device_display_name(self, device_key: str) -> str:
        if device_key == _SPECTROMETER:
            return "Spectrometer"
        return device_settings_title(device_key)

    def _status_icon_for_state(self, connected: bool, state: str) -> QIcon:
        if state == "error":
            return create_status_dot_icon(_STATUS_COLOR_ERROR)
        if connected:
            return create_status_dot_icon(_STATUS_COLOR_CONNECTED)
        return create_status_dot_icon(_STATUS_COLOR_DISCONNECTED)

    def _build_devices_tab(self) -> None:
        self._discover_button = QPushButton("Scan && connect")
        self._discover_button.setToolTip(
            "Scan all serial ports, register new devices, and connect them.\n"
            "Skips ports that already have a configured profile."
        )
        self._discover_button.clicked.connect(self._run_discover)
        self._discover_status = QLabel("")
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self.refresh_connected_devices)

        top_row = QHBoxLayout()
        top_row.addWidget(self._discover_button)
        top_row.addWidget(self._discover_status)
        top_row.addStretch(1)
        top_row.addWidget(refresh_btn)

        self._device_list = QListWidget(self)
        self._device_list.setFixedWidth(230)
        for device_key in _DEVICE_PAGE_ORDER:
            display_name = self._device_display_name(device_key)
            item = QListWidgetItem(display_name)
            item.setData(Qt.ItemDataRole.UserRole, device_key)
            item.setIcon(create_status_dot_icon(_STATUS_COLOR_DISCONNECTED))
            # Longer names (e.g. the Selector's controller-brand suffix) get
            # elided by the fixed-width list - the full name is still
            # available as a tooltip rather than only in the detail page.
            item.setToolTip(display_name)
            self._device_list.addItem(item)
        self._device_list.currentRowChanged.connect(self._on_device_list_row_changed)

        self._device_stack = QStackedWidget(self)
        self._device_stack.addWidget(self._build_spectrometer_detail_page())
        for device_key in (PUMP, SWITCH, SELECTOR):
            self._device_stack.addWidget(self._build_canonical_detail_page(device_key))

        split_row = QHBoxLayout()
        split_row.addWidget(self._device_list)
        split_row.addWidget(self._device_stack, 1)

        layout = QVBoxLayout()
        layout.addLayout(top_row)
        layout.addLayout(split_row, 1)
        page = self._wrap_layout(layout)
        self._tabs.addTab(page, "Devices")

        self._device_list.setCurrentRow(0)

    def _on_device_list_row_changed(self, row: int) -> None:
        if row < 0:
            return
        self._device_stack.setCurrentIndex(row)
        if self._selected_device_key() == PUMP:
            self._refresh_pump_calibration_status()

    def _selected_device_key(self) -> str | None:
        item = self._device_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _open_pump_calibration_window(self) -> None:
        if self._pump_calibration_window is None:
            self._pump_calibration_window = PumpCalibrationDialog(self._device_manager_settings, self._service, self)
        self._pump_calibration_window.show()
        self._pump_calibration_window.raise_()
        self._pump_calibration_window.activateWindow()

    def _build_spectrometer_detail_page(self) -> QWidget:
        title_label = QLabel(f"<b>{self._device_display_name(_SPECTROMETER)}</b>")
        status_label = QLabel("Unknown")
        header_widget = QWidget()
        header_row = QHBoxLayout(header_widget)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.addWidget(title_label)
        header_row.addStretch(1)
        header_row.addWidget(status_label)

        stats_label = QLabel("-")
        stats_label.setWordWrap(True)

        min_spin = make_compact_spinbox(QSpinBox())
        min_spin.setRange(1, 10_000_000)
        min_spin.setSuffix(" µs")
        min_spin.setValue(int(self._device_manager_settings.spectrometer.min_integration_time_us))
        max_spin = make_compact_spinbox(QSpinBox())
        max_spin.setRange(1, 10_000_000)
        max_spin.setSuffix(" µs")
        max_spin.setValue(int(self._device_manager_settings.spectrometer.max_integration_time_us))
        min_spin.valueChanged.connect(lambda _value: self._on_auto_exposure_limits_changed())
        max_spin.valueChanged.connect(lambda _value: self._on_auto_exposure_limits_changed())

        defaults_form = QFormLayout()
        defaults_form.addRow("Min integration time (auto-exposure search)", min_spin)
        defaults_form.addRow("Max integration time (auto-exposure search)", max_spin)

        layout = QVBoxLayout()
        layout.addWidget(header_widget)
        layout.addWidget(_section_label("Stats"))
        layout.addWidget(stats_label)
        layout.addWidget(_section_label("Defaults & limits"))
        layout.addLayout(defaults_form)
        layout.addStretch(1)

        page = QWidget()
        page.setLayout(layout)

        self._device_pages[_SPECTROMETER] = {
            "status_label": status_label,
            "stats_label": stats_label,
            "min_us_spin": min_spin,
            "max_us_spin": max_spin,
        }
        return page

    def _build_canonical_detail_page(self, device_key: str) -> QWidget:
        title_label = QLabel(f"<b>{self._device_display_name(device_key)}</b>")
        status_label = QLabel("Unknown")
        connect_button = QPushButton("Connect")
        connect_button.clicked.connect(lambda _checked=False, key=device_key: self._connect_device(key))
        disconnect_button = QPushButton("Disconnect")
        disconnect_button.clicked.connect(lambda _checked=False, key=device_key: self._disconnect_device(key))
        test_button = QPushButton("Test")
        test_button.clicked.connect(lambda _checked=False, key=device_key: self._test_device(key))

        header_widget = QWidget()
        header_row = QHBoxLayout(header_widget)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.addWidget(title_label)
        header_row.addStretch(1)
        header_row.addWidget(status_label)
        header_row.addWidget(connect_button)
        header_row.addWidget(disconnect_button)
        header_row.addWidget(test_button)

        enable_widget = QWidget()
        enable_row = QHBoxLayout(enable_widget)
        enable_row.setContentsMargins(0, 0, 0, 0)
        enabled_check = QCheckBox("Enabled", enable_widget)
        enabled_check.toggled.connect(lambda checked, key=device_key: self._on_device_enabled_toggled(key, checked))
        enable_row.addWidget(enabled_check)
        enable_row.addWidget(make_help_button(
            _ENABLE_HELP_TOOLTIP, title=_ENABLE_HELP_TITLE, body=_ENABLE_HELP_BODY, parent=enable_widget,
        ))
        enable_row.addStretch(1)

        stats_form = QFormLayout()
        endpoint_value = QLabel("-")
        fingerprint_value = QLabel("-")
        stats_form.addRow("Endpoint", endpoint_value)
        stats_form.addRow("Fingerprint", fingerprint_value)
        identity_label = QLabel("-")
        identity_label.setWordWrap(True)

        capabilities_label = QLabel("-")

        defaults_form = QFormLayout()
        widgets: dict[str, QWidget] = {}
        if device_key == PUMP:
            tube_spin = make_compact_spinbox(QDoubleSpinBox())
            tube_spin.setRange(0.01, 10.0)
            tube_spin.setDecimals(2)
            tube_spin.setSuffix(" mm")
            tube_spin.setValue(self._device_manager_settings.pump.tube_mm)
            tube_spin.valueChanged.connect(self._on_pump_tube_mm_changed)
            defaults_form.addRow("Default tube diameter", tube_spin)
            widgets["tube_mm_spin"] = tube_spin

            backsteps_spin = make_compact_spinbox(QSpinBox())
            backsteps_spin.setRange(0, 100)
            backsteps_spin.setToolTip(
                "Roller backsteps for drip-free dispensing (pump manual sec. 6.4.3).\n"
                "0 = pump's own factory default (no backstep correction)."
            )
            backsteps_spin.setValue(self._device_manager_settings.pump.backsteps)
            backsteps_spin.valueChanged.connect(self._on_pump_backsteps_changed)
            defaults_form.addRow("Backsteps", backsteps_spin)
            widgets["backsteps_spin"] = backsteps_spin

            max_flow_spin = make_compact_spinbox(QDoubleSpinBox())
            max_flow_spin.setRange(0.01, 35_000.0)
            max_flow_spin.setDecimals(1)
            max_flow_spin.setSuffix(" uL/min")
            max_flow_spin.setToolTip(
                "Soft cap on the flow rate spinboxes in the Experiment Control panel.\n"
                "Not a pump hardware limit - the Reglo ICC itself supports up to\n"
                "35 mL/min on the largest tubing (manual sec. 13)."
            )
            max_flow_spin.setValue(self._device_manager_settings.pump.max_flow_ul_min)
            max_flow_spin.valueChanged.connect(self._on_pump_max_flow_changed)
            defaults_form.addRow("Max flow rate", max_flow_spin)
            widgets["max_flow_spin"] = max_flow_spin

            roller_count_combo = QComboBox()
            roller_count_combo.setToolTip(
                "Rollers on the installed cassette head (pump manual sec. 6.2/6.12-6.13).\n"
                "Must match the physical head, or the pump's mL/min flow-rate\n"
                "conversion for every channel will be skewed."
            )
            for option in VALID_ROLLER_COUNTS:
                roller_count_combo.addItem(f"{option}", option)
            current_roller_count = self._device_manager_settings.pump.roller_count
            index = roller_count_combo.findData(current_roller_count)
            roller_count_combo.setCurrentIndex(index if index >= 0 else 0)
            roller_count_combo.currentIndexChanged.connect(self._on_pump_roller_count_changed)
            defaults_form.addRow("Rollers (cassette head)", roller_count_combo)
            widgets["roller_count_combo"] = roller_count_combo
        elif device_key == SWITCH:
            poll_row_widget = QWidget()
            poll_row_layout = QHBoxLayout(poll_row_widget)
            poll_row_layout.setContentsMargins(0, 0, 0, 0)
            poll_row_layout.addWidget(QLabel("Temp/humidity read interval"))
            poll_spin = make_compact_spinbox(QDoubleSpinBox())
            poll_spin.setRange(1.0, 300.0)
            poll_spin.setDecimals(1)
            poll_spin.setSuffix(" s")
            poll_spin.setValue(self._device_manager_settings.switch.environment_poll_interval_s)
            poll_spin.valueChanged.connect(self._on_environment_poll_interval_changed)
            poll_row_layout.addWidget(poll_spin)
            poll_row_layout.addStretch(1)
            defaults_form.addRow(poll_row_widget)
            widgets["poll_interval_spin"] = poll_spin
            widgets["poll_interval_row"] = poll_row_widget
        elif device_key == SELECTOR:
            placeholder = QLabel("No settings yet.")
            placeholder.setEnabled(False)
            defaults_form.addRow(placeholder)

        calibration_form: QFormLayout | None = None
        calibration_channel_labels: list[QLabel] = []
        open_calibration_button: QPushButton | None = None
        if device_key == PUMP:
            calibration_form = QFormLayout()
            for channel in range(1, ACTIVE_PUMP_CHANNELS + 1):
                channel_label = QLabel("-")
                calibration_form.addRow(f"CH{channel}", channel_label)
                calibration_channel_labels.append(channel_label)
            open_calibration_button = QPushButton("Open Pump Calibration...")
            open_calibration_button.setToolTip(
                "Opens the pump calibration window (run all channels together,\n"
                "measure, and write corrected roller-step-volume constants)."
            )
            open_calibration_button.clicked.connect(self._open_pump_calibration_window)

        layout = QVBoxLayout()
        layout.addWidget(header_widget)
        layout.addWidget(enable_widget)
        layout.addWidget(_section_label("Stats"))
        layout.addLayout(stats_form)
        layout.addWidget(identity_label)
        if device_key == SWITCH:
            layout.addWidget(_section_label("Capabilities"))
            layout.addWidget(capabilities_label)
        layout.addWidget(_section_label("Defaults & limits"))
        layout.addLayout(defaults_form)
        if device_key == PUMP and calibration_form is not None and open_calibration_button is not None:
            layout.addWidget(_section_label("Calibration"))
            layout.addLayout(calibration_form)
            layout.addWidget(open_calibration_button)
        layout.addStretch(1)

        page = QWidget()
        page.setLayout(layout)

        widgets.update({
            "status_label": status_label,
            "connect_button": connect_button,
            "disconnect_button": disconnect_button,
            "test_button": test_button,
            "enabled_check": enabled_check,
            "endpoint_value": endpoint_value,
            "fingerprint_value": fingerprint_value,
            "identity_label": identity_label,
            "capabilities_label": capabilities_label,
        })
        if device_key == PUMP:
            widgets["calibration_channel_labels"] = calibration_channel_labels
            widgets["open_calibration_button"] = open_calibration_button
        self._device_pages[device_key] = widgets
        return page

    def _on_auto_exposure_limits_changed(self) -> None:
        page = self._device_pages.get(_SPECTROMETER, {})
        min_spin = page.get("min_us_spin")
        max_spin = page.get("max_us_spin")
        if min_spin is None or max_spin is None:
            return
        window = self.parent()
        if window is not None and hasattr(window, "_set_auto_exposure_integration_limits_us"):
            window._set_auto_exposure_integration_limits_us(min_spin.value(), max_spin.value())

    def _on_pump_tube_mm_changed(self, value: float) -> None:
        window = self.parent()
        if window is not None and hasattr(window, "_set_pump_default_tube_mm"):
            window._set_pump_default_tube_mm(value)

    def _on_pump_backsteps_changed(self, value: int) -> None:
        window = self.parent()
        if window is not None and hasattr(window, "_set_pump_default_backsteps"):
            window._set_pump_default_backsteps(value)

    def _refresh_pump_calibration_status(self) -> None:
        """Query each pump channel's elapsed time since it was last
        calibrated ("xX", manual sec. 5.8) and show it next to the pump's
        Defaults & limits. Triggered whenever the Pump page becomes the
        visible one (see _on_device_list_row_changed/showEvent) rather than
        a manual button or the automatic 2 s status timer - avoids
        contending with an in-progress experiment for the single-lane
        serial connection on every tick, while still staying current
        whenever someone actually looks at the Pump page.

        PUMP_CALIBRATION_WARNING_HOURS is our own placeholder threshold, not
        an Ismatec recommendation - the manual has none. And this is
        operating/run hours, not calendar time - the pump has no real-time
        clock. See that constant's comment and get_time_since_last_calibration_s's
        docstring for the unit-ambiguity caveat in the manual's own worked
        example for this exact command.
        """
        labels = self._device_pages.get(PUMP, {}).get("calibration_channel_labels", [])
        if not labels:
            return
        label = device_label_for(PUMP)
        try:
            connected = bool(self._service.status(label).connected)
        except Exception:
            connected = False
        if not connected:
            for channel_label in labels:
                channel_label.setText("Pump not connected.")
                channel_label.setStyleSheet("")
            return
        for index, channel_label in enumerate(labels, start=1):
            result = self._service.send_command(
                label, DeviceCommand("pump.calibration.time_since_last_s", {"channel": index}),
            )
            if not result.success:
                channel_label.setText(f"Error: {result.error or 'unknown'}")
                channel_label.setStyleSheet(f"color: {_STATUS_COLOR_ERROR.name()};")
                continue
            seconds = float(result.response or 0.0)
            hours = seconds / 3600.0
            if hours >= PUMP_CALIBRATION_WARNING_HOURS:
                channel_label.setText(f"{hours:.1f} h since last calibration - consider recalibrating")
                channel_label.setStyleSheet(f"color: {_STATUS_COLOR_SIMULATED.name()};")
            else:
                channel_label.setText(f"{hours:.1f} h since last calibration")
                channel_label.setStyleSheet("")

    def _on_pump_max_flow_changed(self, value: float) -> None:
        window = self.parent()
        if window is not None and hasattr(window, "_set_pump_default_max_flow_ul_min"):
            window._set_pump_default_max_flow_ul_min(value)

    def _on_pump_roller_count_changed(self, _index: int) -> None:
        page = self._device_pages.get(PUMP, {})
        combo = page.get("roller_count_combo")
        if combo is None:
            return
        window = self.parent()
        if window is not None and hasattr(window, "_set_pump_default_roller_count"):
            window._set_pump_default_roller_count(int(combo.currentData()))

    def _on_environment_poll_interval_changed(self, value: float) -> None:
        window = self.parent()
        if window is not None and hasattr(window, "_set_environment_poll_interval_s"):
            window._set_environment_poll_interval_s(value)

    def _on_device_enabled_toggled(self, device_key: str, checked: bool) -> None:
        window = self.parent()
        if window is not None and hasattr(window, "_apply_device_enablement"):
            current = DeviceLifecycleController.shared().enabled_devices()
            current[device_key] = bool(checked)
            window._apply_device_enablement(current)

    def _build_profiles_tab(self) -> tuple[QWidget, str]:
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
        return page, "Profiles"

    def _build_port_list_tab(self) -> tuple[QWidget, str]:
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
        return page, "Port list"

    def _build_probe_assign_tab(self) -> tuple[QWidget, str]:
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
        return page, "Probe / assign"

    def _build_command_tab(self) -> tuple[QWidget, str]:
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
        return page, "Commands"

    def _build_pump_calibration_tab(self) -> tuple[QWidget, str]:
        """Raw, one-channel-at-a-time test bench for the pump's calibration
        commands (manual sec. 6.4.4 / 16.2 ref 5.0, 18.5) plus direct
        roller-step-volume access (ref 6.33/6.34) - see RegloICCClient's
        calibration methods. Deep-debug-gated like the Commands tab, since
        "Start calibration" physically dispenses fluid on the selected
        channel.

        Kept deliberately low-level (one channel at a time, raw command
        access) for verifying real pump behavior against the manual - in
        particular the Time Type unit ambiguity flagged in
        RegloICCClient._decode_time_type_seconds/_encode_time_type. For the
        real, everyday calibration workflow (all channels together), see
        gui/pump_calibration_panel.PumpCalibrationDialog, opened via the
        Pump page's "Open Pump Calibration..." button
        (_open_pump_calibration_window).
        """
        self._cal_channel_spin = make_compact_spinbox(QSpinBox())
        self._cal_channel_spin.setRange(1, ACTIVE_PUMP_CHANNELS)
        self._cal_channel_spin.setValue(1)

        self._cal_direction_combo = QComboBox(self)
        self._cal_direction_combo.addItem("CW", "CW")
        self._cal_direction_combo.addItem("CCW", "CCW")

        self._cal_target_volume_spin = make_compact_spinbox(QDoubleSpinBox())
        self._cal_target_volume_spin.setRange(0.001, 1000.0)
        self._cal_target_volume_spin.setDecimals(3)
        self._cal_target_volume_spin.setSuffix(" mL")
        self._cal_target_volume_spin.setValue(10.0)

        self._cal_time_spin = make_compact_spinbox(QDoubleSpinBox())
        self._cal_time_spin.setRange(0.1, 3600.0)
        self._cal_time_spin.setDecimals(1)
        self._cal_time_spin.setSuffix(" s")
        self._cal_time_spin.setValue(60.0)

        apply_button = QPushButton("Apply direction/target/duration to channel")
        apply_button.clicked.connect(self._apply_pump_calibration_settings)

        self._cal_implied_flow_label = QLabel("-")
        self._cal_implied_flow_label.setToolTip(
            "Target volume / duration, as a flow rate - compare against your\n"
            "tubing's achievable range (manual sec. 13's chart / this app's\n"
            "TUBE_DIAMETER_OPTIONS). A target that implies a flow rate the\n"
            "installed tube can't achieve is a likely cause of the pump\n"
            "refusing to start (\"-\" response, cause \"R\" via \"Why?\" below)."
        )
        self._cal_target_volume_spin.valueChanged.connect(self._update_pump_calibration_implied_flow_rate)
        self._cal_time_spin.valueChanged.connect(self._update_pump_calibration_implied_flow_rate)

        form = QFormLayout()
        form.addRow("Channel", self._cal_channel_spin)
        form.addRow("Direction", self._cal_direction_combo)
        form.addRow("Target volume", self._cal_target_volume_spin)
        form.addRow("Duration", self._cal_time_spin)
        form.addRow("Implied flow rate", self._cal_implied_flow_label)

        start_button = QPushButton("Start calibration (dispenses fluid!)")
        start_button.setToolTip('Sends "xY" - physically runs the pump on the selected channel.')
        start_button.clicked.connect(self._start_pump_calibration)
        cancel_button = QPushButton("Cancel calibration")
        cancel_button.clicked.connect(self._cancel_pump_calibration)
        why_button = QPushButton("Why can't it start? (xe)")
        why_button.setToolTip(
            'If Start returns a "-" response, this queries the pump\'s own\n'
            "documented reason (manual sec. 2.7, \"xe\") for the selected channel."
        )
        why_button.clicked.connect(self._diagnose_pump_calibration_start_failure)
        run_row = QHBoxLayout()
        run_row.addWidget(start_button)
        run_row.addWidget(cancel_button)
        run_row.addWidget(why_button)
        run_row.addStretch(1)

        self._cal_measured_volume_spin = make_compact_spinbox(QDoubleSpinBox())
        self._cal_measured_volume_spin.setRange(0.0, 1000.0)
        self._cal_measured_volume_spin.setDecimals(3)
        self._cal_measured_volume_spin.setSuffix(" mL")
        submit_measured_button = QPushButton("Submit measured volume (applies correction)")
        submit_measured_button.setToolTip(
            "Deviation is computed against the Target volume field above -\n"
            "make sure it still matches what you set before starting the run."
        )
        submit_measured_button.clicked.connect(self._submit_pump_calibration_measured_volume)
        measured_row = QHBoxLayout()
        measured_row.addWidget(self._cal_measured_volume_spin)
        measured_row.addWidget(submit_measured_button)

        self._cal_roller_step_volume_label = QLabel("-")
        read_rsv_button = QPushButton("Read")
        read_rsv_button.clicked.connect(self._read_pump_roller_step_volume)
        rsv_read_row = QHBoxLayout()
        rsv_read_row.addWidget(QLabel("Current:"))
        rsv_read_row.addWidget(self._cal_roller_step_volume_label, 1)
        rsv_read_row.addWidget(read_rsv_button)

        self._cal_roller_step_volume_write_spin = make_compact_spinbox(QDoubleSpinBox())
        self._cal_roller_step_volume_write_spin.setRange(0.0, 10.0)
        self._cal_roller_step_volume_write_spin.setDecimals(6)
        self._cal_roller_step_volume_write_spin.setSuffix(" mL/step")
        write_rsv_button = QPushButton("Write (direct, no dispense)")
        write_rsv_button.setToolTip(
            "Directly overwrites this channel's calibrated roller-step volume -\n"
            "the constant used to convert flow-rate/volume targets into an\n"
            "actual roller speed - without running the interactive\n"
            "dispense-then-measure procedure above."
        )
        write_rsv_button.clicked.connect(self._write_pump_roller_step_volume)
        rsv_write_row = QHBoxLayout()
        rsv_write_row.addWidget(self._cal_roller_step_volume_write_spin)
        rsv_write_row.addWidget(write_rsv_button)

        self._cal_time_since_last_label = QLabel("-")
        time_since_button = QPushButton("Refresh")
        time_since_button.clicked.connect(self._refresh_pump_calibration_time_since_last)
        time_since_row = QHBoxLayout()
        time_since_row.addWidget(self._cal_time_since_last_label, 1)
        time_since_row.addWidget(time_since_button)

        self._cal_result = QPlainTextEdit(self)
        self._cal_result.setReadOnly(True)
        self._cal_result.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self._cal_result.setPlaceholderText("Raw pump responses appear here (one line per command).")

        layout = QVBoxLayout()
        layout.addWidget(_section_label("1. Configure channel"))
        layout.addLayout(form)
        layout.addWidget(apply_button)
        layout.addWidget(_section_label("2. Run calibration (physically dispenses fluid)"))
        layout.addLayout(run_row)
        layout.addWidget(_section_label("3. Enter measured volume (applies the correction)"))
        layout.addLayout(measured_row)
        layout.addWidget(_section_label("Roller-step volume - direct read/write, no dispense"))
        layout.addLayout(rsv_read_row)
        layout.addLayout(rsv_write_row)
        layout.addWidget(_section_label("Time since last calibration"))
        layout.addLayout(time_since_row)
        layout.addWidget(_section_label("Raw responses"))
        layout.addWidget(self._cal_result, 1)
        page = self._wrap_layout(layout)
        self._update_pump_calibration_implied_flow_rate()
        return page, "Pump Cal. (raw)"

    def _update_pump_calibration_implied_flow_rate(self) -> None:
        volume_ml = self._cal_target_volume_spin.value()
        duration_s = self._cal_time_spin.value()
        if duration_s <= 0.0:
            self._cal_implied_flow_label.setText("-")
            return
        flow_ml_min = volume_ml / (duration_s / 60.0)
        self._cal_implied_flow_label.setText(f"{flow_ml_min:.4g} mL/min")

    def _diagnose_pump_calibration_start_failure(self) -> None:
        channel = self._cal_channel_spin.value()
        self._run_pump_calibration_command(
            "pump.get_start_failure_reason", {"channel": channel}, f"CH{channel} why can't it start (xe)",
        )

    def _run_pump_calibration_command(self, command_type: str, payload: dict, description: str) -> object | None:
        result = self._service.send_command(device_label_for(PUMP), DeviceCommand(command_type, payload))
        timestamp = datetime.now().strftime("%H:%M:%S")
        if result.success:
            self._cal_result.appendPlainText(f"[{timestamp}] {description}: OK -> {result.response!r}")
            return result.response
        self._cal_result.appendPlainText(f"[{timestamp}] {description}: FAILED -> {result.error}")
        return None

    def _apply_pump_calibration_settings(self) -> None:
        channel = self._cal_channel_spin.value()
        direction = str(self._cal_direction_combo.currentData())
        target_ml = self._cal_target_volume_spin.value()
        duration_s = self._cal_time_spin.value()
        self._run_pump_calibration_command(
            "pump.calibration.set_direction", {"channel": channel, "direction": direction},
            f"CH{channel} set direction={direction}",
        )
        self._run_pump_calibration_command(
            "pump.calibration.set_target_volume_ml", {"channel": channel, "volume_ml": target_ml},
            f"CH{channel} set target volume={target_ml:g} mL",
        )
        self._run_pump_calibration_command(
            "pump.calibration.set_time_s", {"channel": channel, "seconds": duration_s},
            f"CH{channel} set duration={duration_s:g} s",
        )

    def _start_pump_calibration(self) -> None:
        channel = self._cal_channel_spin.value()
        self._run_pump_calibration_command("pump.calibration.start", {"channel": channel}, f"CH{channel} start calibration")

    def _cancel_pump_calibration(self) -> None:
        channel = self._cal_channel_spin.value()
        self._run_pump_calibration_command("pump.calibration.cancel", {"channel": channel}, f"CH{channel} cancel calibration")

    def _submit_pump_calibration_measured_volume(self) -> None:
        channel = self._cal_channel_spin.value()
        measured_ml = self._cal_measured_volume_spin.value()
        target_ml = self._cal_target_volume_spin.value()
        confirmed = self._run_pump_calibration_command(
            "pump.calibration.set_measured_volume_ml", {"channel": channel, "volume_ml": measured_ml},
            f"CH{channel} submit measured volume={measured_ml:g} mL",
        )
        if confirmed is not None and target_ml > 0.0:
            deviation_pct = (float(confirmed) - target_ml) / target_ml * 100.0
            self._cal_result.appendPlainText(
                f"    -> deviation vs target ({target_ml:g} mL): {deviation_pct:+.2f}%"
            )

    def _read_pump_roller_step_volume(self) -> None:
        channel = self._cal_channel_spin.value()
        response = self._run_pump_calibration_command(
            "pump.roller_step_volume.get", {"channel": channel}, f"CH{channel} read roller-step volume",
        )
        if response is not None:
            self._cal_roller_step_volume_label.setText(f"{float(response):.6f} mL/step")

    def _write_pump_roller_step_volume(self) -> None:
        channel = self._cal_channel_spin.value()
        volume_ml = self._cal_roller_step_volume_write_spin.value()
        self._run_pump_calibration_command(
            "pump.roller_step_volume.set", {"channel": channel, "volume_ml": volume_ml},
            f"CH{channel} write roller-step volume={volume_ml:g} mL/step",
        )

    def _refresh_pump_calibration_time_since_last(self) -> None:
        channel = self._cal_channel_spin.value()
        response = self._run_pump_calibration_command(
            "pump.calibration.time_since_last_s", {"channel": channel}, f"CH{channel} time since last calibration",
        )
        if response is not None:
            hours = float(response) / 3600.0
            self._cal_time_since_last_label.setText(f"{hours:.2f} h (raw: {float(response):.1f} s)")

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
        # No parent here on purpose: QTabWidget.addTab() reparents this page
        # into its own internal stack the moment it's actually added. The
        # four "deep debug" tabs (Profiles/Probe-assign/Commands/Port list)
        # are built in __init__ but only added to self._tabs once Deep debug
        # mode is switched on - if this page were parented to self (the
        # dialog) immediately, it would render as a floating orphan on top
        # of the dialog in the meantime (same bug class as the _probe_output
        # issue in DEVICE_LAYER_AUDIT_2026.md item 30, different widget).
        page = QWidget()
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
        profiles = {p.label: p for p in self._service.list_profiles()}
        statuses_by_label = {status.label: status for status in devices}
        enabled = DeviceLifecycleController.shared().enabled_devices()

        for device_key in (PUMP, SWITCH, SELECTOR):
            label = device_label_for(device_key)
            self._refresh_canonical_device_page(
                device_key, statuses_by_label.get(label), profiles.get(label), enabled.get(device_key, True),
            )
        self._refresh_spectrometer_page()

        # The Commands tab's device dropdown belongs to device state, not the
        # port scan - refreshed here (called after every connect/disconnect,
        # canonical or generic) instead of refresh_port_list() so it never
        # goes stale after a normal Connect/Disconnect click.
        self._command_label_combo.blockSignals(True)
        self._command_label_combo.clear()
        for status in devices:
            self._command_label_combo.addItem(status.label, status.label)
        self._command_label_combo.blockSignals(False)

    def _refresh_canonical_device_page(
        self, device_key: str, status: object | None, profile: DeviceProfile | None, enabled: bool,
    ) -> None:
        page = self._device_pages.get(device_key)
        if page is None:
            return
        connected = bool(getattr(status, "connected", False))
        # DeviceStatus.state is typed `str` but device_manager.py's
        # _make_status() actually assigns a DeviceLifecycleState enum member -
        # str() on a (str, Enum) member gives "DeviceLifecycleState.X", not
        # "x", so unwrap .value first (falls back to the raw value if it's
        # ever a plain string instead).
        raw_state = getattr(status, "state", None)
        state = str(getattr(raw_state, "value", raw_state) or ("disconnected" if status is not None else "not registered"))
        page["status_label"].setText(state)

        list_index = _DEVICE_PAGE_ORDER.index(device_key)
        item = self._device_list.item(list_index)
        if item is not None:
            item.setIcon(self._status_icon_for_state(connected, state))

        page["connect_button"].setEnabled(not connected)
        page["disconnect_button"].setEnabled(connected)
        page["test_button"].setEnabled(connected)

        enabled_check = page["enabled_check"]
        enabled_check.blockSignals(True)
        enabled_check.setChecked(bool(enabled))
        enabled_check.blockSignals(False)

        endpoint = getattr(status, "endpoint", None) or (profile.endpoint if profile is not None else None)
        page["endpoint_value"].setText(str(endpoint or "-"))
        fingerprint = str(getattr(profile, "fingerprint", "") or "") if profile is not None else ""
        page["fingerprint_value"].setText(fingerprint or "-")

        identity: dict[str, str] = {}
        status_identity = getattr(status, "identity", None)
        if status_identity:
            identity = dict(status_identity)
        elif profile is not None and profile.identity:
            identity = dict(profile.identity)
        page["identity_label"].setText(
            "\n".join(f"{key}: {value}" for key, value in sorted(identity.items())) or "No identity information yet."
        )

        if device_key == SWITCH:
            capabilities = capabilities_for_controller_type(identity.get("controller_type", ""))
            page["capabilities_label"].setText(
                f"Temperature sensor: {'yes' if capabilities.has_temperature_sensor else 'no'}\n"
                f"Humidity sensor: {'yes' if capabilities.has_humidity_sensor else 'no'}"
            )
            poll_row = page.get("poll_interval_row")
            if poll_row is not None:
                poll_row.setVisible(capabilities.has_temperature_sensor or capabilities.has_humidity_sensor)

    def _refresh_spectrometer_page(self) -> None:
        page = self._device_pages.get(_SPECTROMETER)
        if page is None:
            return
        list_index = _DEVICE_PAGE_ORDER.index(_SPECTROMETER)
        item = self._device_list.item(list_index)
        spectrometer = getattr(self.parent(), "_spectrometer", None)
        if spectrometer is None:
            page["status_label"].setText("Not available")
            page["stats_label"].setText("No spectrometer connected.")
            if item is not None:
                item.setIcon(create_status_dot_icon(_STATUS_COLOR_DISCONNECTED))
                item.setToolTip(self._device_display_name(_SPECTROMETER))
            return

        try:
            device_name = spectrometer.device_name()
        except Exception:
            device_name = "Spectrometer"
        page["status_label"].setText(device_name)
        if item is not None:
            # A SimulatedSpectrometer is a stand-in used when no real hardware
            # is connected (see device/simulated.py) - showing it with the
            # same green dot as a real connection would read as "spectrometer
            # is plugged in" when it isn't. Amber marks it as active-but-fake.
            is_simulated = isinstance(spectrometer, SimulatedSpectrometer)
            color = _STATUS_COLOR_SIMULATED if is_simulated else _STATUS_COLOR_CONNECTED
            item.setIcon(create_status_dot_icon(color))
            tooltip = f"{self._device_display_name(_SPECTROMETER)} (simulated, no hardware)" if is_simulated else self._device_display_name(_SPECTROMETER)
            item.setToolTip(tooltip)

        # No shared public accessor for the wavelength axis exists on the
        # Spectrometer ABC (device/base.py) - OceanSpectrometer exposes it as
        # the private _wavelengths (set once at connect time), while
        # SimulatedSpectrometer computes it on demand via wavelength_axis()
        # and has no _wavelengths attribute at all. Try both rather than
        # assuming one backend's shape.
        wavelength_axis = getattr(spectrometer, "wavelength_axis", None)
        wavelengths = wavelength_axis() if callable(wavelength_axis) else None
        if wavelengths is None:
            wavelengths = getattr(spectrometer, "_wavelengths", None)
        if wavelengths is not None and len(wavelengths) > 0:
            resolution_text = f"{len(wavelengths)} px, {float(wavelengths[0]):.1f}-{float(wavelengths[-1]):.1f} nm"
        else:
            resolution_text = "unavailable"
        try:
            saturation_text = f"{spectrometer.max_intensity():.0f} counts"
        except Exception:
            saturation_text = "unavailable"
        page["stats_label"].setText(f"Resolution: {resolution_text}\nSaturation: {saturation_text}")

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

    def _connect_device(self, device_key: str) -> None:
        self._connect_canonical_device(device_key, device_label_for(device_key))

    def _disconnect_device(self, device_key: str) -> None:
        self._disconnect_canonical_device(device_key)

    def _test_device(self, device_key: str) -> None:
        label = device_label_for(device_key)
        try:
            status = self._service.status(label)
        except Exception as exc:
            QMessageBox.warning(self, "Test failed", str(exc))
            return
        QMessageBox.information(self, "Device status", str(asdict(status)))

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
            payload.setdefault("tube_mm", self._device_manager_settings.pump.tube_mm)
            payload.setdefault("backsteps", self._device_manager_settings.pump.backsteps)
            payload.setdefault("roller_count", self._device_manager_settings.pump.roller_count)
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
