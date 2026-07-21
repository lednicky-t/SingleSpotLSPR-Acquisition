from __future__ import annotations

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QMenuBar, QWidget

from lspr_core import DEFAULT_LAUNCH_PROFILE, launch_profile_spec
from lspr_app.device.device_lifecycle import DeviceLifecycleController
from lspr_app.device.device_types import PUMP, SELECTOR, SWITCH
from lspr_app.gui.icon_helpers import device_status_icon

# Titlebar status-strip keys ("pump"/"valve"/"mswitch") predate and differ
# from the device-layer keys (PUMP/SWITCH/SELECTOR) - map between them here
# rather than changing the widget keys, which are also used as dict keys
# elsewhere (_device_activity_text) and as the literal strings baked into
# build_title_bar's widget-creation loop below.
_TITLEBAR_KEY_TO_DEVICE_KEY = {"pump": PUMP, "valve": SWITCH, "mswitch": SELECTOR}


def device_status_state(connected: bool, discovered: bool) -> str:
    if connected:
        return "connected"
    if discovered:
        return "discovered"
    return "disconnected"


def _device_status_entry(connected: bool, discovered: bool, detail: str = "") -> tuple[str, str]:
    return device_status_state(connected, discovered), detail


def device_status_tooltip(label_text: str, state: str, *, port_name: str = "", detail: str = "") -> str:
    normalized = device_status_state(state == "connected", state == "discovered")
    tooltip = f"{label_text}: {normalized}."
    if port_name:
        tooltip = f"{label_text}: {normalized} on {port_name}."
    if detail:
        suffix = str(detail).strip()
        if suffix and not suffix.startswith("("):
            suffix = f"({suffix})"
        tooltip = f"{tooltip} {suffix}".strip()
    return tooltip


def build_title_bar(window, menu_bar: QMenuBar, brand_icon_path) -> QWidget:
    profile = getattr(window, "_launch_profile_spec", None)
    if profile is None:
        profile = launch_profile_spec(DEFAULT_LAUNCH_PROFILE)

    title_widget = QWidget(window)
    title_widget.setObjectName("titleBar")

    window._window_mode_label = QLabel(title_widget)
    window._window_mode_label.setObjectName("windowModeLabel")
    window._window_mode_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    window._window_mode_label.setContentsMargins(0, 0, 0, 0)
    window._window_mode_label.setToolTip("Current application mode.")
    window._update_window_mode_label()

    window._window_mode_icon_label = QLabel(title_widget)
    window._window_mode_icon_label.setObjectName("windowModeIconLabel")
    window._window_mode_icon_label.setFixedSize(16, 16)
    window._window_mode_icon_label.setToolTip("Current source icon.")

    center_cluster = QWidget(title_widget)
    center_layout = QHBoxLayout()
    center_layout.setContentsMargins(0, 0, 0, 0)
    center_layout.setSpacing(5)
    center_layout.addWidget(window._window_mode_label)
    center_layout.addWidget(window._window_mode_icon_label)
    center_cluster.setLayout(center_layout)
    window._window_mode_cluster = center_cluster

    status_cluster = QWidget(title_widget)
    status_layout = QHBoxLayout()
    status_layout.setContentsMargins(0, 0, 0, 0)
    status_layout.setSpacing(8)
    window._startup_loading_frames = ["â—", "â—“", "â—‘", "â—’"]
    window._startup_loading_frame_index = 0
    window._startup_loading_label = QLabel("â—", title_widget)
    window._startup_loading_label.setObjectName("startupLoadingLabel")
    window._startup_loading_label.setFixedSize(16, 16)
    window._startup_loading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    window._startup_loading_label.setToolTip("Device initialization is still in progress.")
    window._startup_loading_label.setVisible(False)
    window._startup_loading_timer = QTimer(window)
    window._startup_loading_timer.setInterval(140)
    window._startup_loading_timer.timeout.connect(window._advance_startup_loading_indicator)
    window._recording_blink_visible = True
    window._recording_blink_timer = QTimer(window)
    window._recording_blink_timer.setInterval(650)
    window._recording_blink_timer.timeout.connect(window._advance_recording_blink_indicator)
    window._hw_status_items = []
    for key, label_text in (
        ("spectrometer", "Spectrometer"),
        ("pump", "Pump"),
        ("valve", "Valve"),
        ("mswitch", "M-Switch"),
    ):
        icon_label = QLabel(title_widget)
        icon_label.setFixedSize(16, 16)
        icon_label.setToolTip(f"{label_text} connection status.")
        text_label = QLabel(label_text, title_widget)
        text_label.setToolTip(f"{label_text} connection status.")
        item = QWidget(title_widget)
        item_layout = QHBoxLayout()
        item_layout.setContentsMargins(0, 0, 0, 0)
        item_layout.setSpacing(4)
        item_layout.addWidget(icon_label)
        item_layout.addWidget(text_label)
        item.setLayout(item_layout)
        status_layout.addWidget(item)
        window._hw_status_items.append((key, icon_label, text_label))
    status_cluster.setLayout(status_layout)
    status_cluster.setVisible(bool(profile.show_device_statuses))
    window._titlebar_status_cluster = status_cluster

    left_cluster = QWidget(title_widget)
    left_layout = QHBoxLayout()
    left_layout.setContentsMargins(0, 0, 0, 0)
    left_layout.setSpacing(6)
    left_layout.addWidget(menu_bar)
    left_cluster.setLayout(left_layout)

    right_cluster = QWidget(title_widget)
    right_layout = QHBoxLayout()
    right_layout.setContentsMargins(0, 0, 0, 0)
    right_layout.setSpacing(4)
    right_layout.addWidget(window._startup_loading_label)
    right_layout.addWidget(status_cluster)
    right_cluster.setLayout(right_layout)

    title_layout = QGridLayout()
    title_layout.setContentsMargins(8, 3, 8, 3)
    title_layout.setHorizontalSpacing(8)
    title_layout.setVerticalSpacing(0)
    title_layout.setColumnStretch(0, 1)
    title_layout.setColumnStretch(1, 0)
    title_layout.setColumnStretch(2, 1)
    title_layout.addWidget(left_cluster, 0, 0, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    title_layout.addWidget(center_cluster, 0, 1, alignment=Qt.AlignmentFlag.AlignCenter)
    title_layout.addWidget(right_cluster, 0, 2, alignment=Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    title_widget.setLayout(title_layout)
    title_widget.installEventFilter(window)
    window._update_window_mode_label()
    return title_widget


def refresh_hw_device_status_strip(window) -> None:
    """Render the titlebar's spectrometer/pump/valve/M-switch status dots.

    Reads connection state directly from DeviceLifecycleController.shared() -
    the single owner of device connections - rather than reaching through
    experiment_control_window's own attributes. This also means the strip
    stays accurate even when the panel hasn't been constructed yet (it used
    to show everything as disconnected in that case, even mid-cycle).
    """
    items = getattr(window, "_hw_status_items", None)
    if not items:
        return
    profile = getattr(window, "_launch_profile_spec", None)
    if profile is None:
        profile = launch_profile_spec(DEFAULT_LAUNCH_PROFILE)
    status_cluster = getattr(window, "_titlebar_status_cluster", None)
    if status_cluster is not None:
        status_cluster.setVisible(bool(profile.show_device_statuses))
    if not bool(profile.show_device_statuses):
        return

    controller = DeviceLifecycleController.shared()
    activity_text = getattr(window, "_device_activity_text", {})

    def _port_name(probe: object | None) -> str:
        if probe is None:
            return ""
        port = getattr(probe, "port", None)
        return str(getattr(port, "device", port) or "").strip()

    devices = {"spectrometer": ("connected" if bool(window._hardware_available) else "disconnected", "")}
    for titlebar_key, device_key in _TITLEBAR_KEY_TO_DEVICE_KEY.items():
        connected = controller.is_connected(device_key)
        probe = controller.probe_for(device_key)
        state = device_status_state(connected, probe is not None)
        devices[titlebar_key] = (state, _port_name(probe))

    for key, icon_label, text_label in items:
        state, port_name = devices.get(key, ("disconnected", ""))
        device_key = _TITLEBAR_KEY_TO_DEVICE_KEY.get(key)
        detail = activity_text.get(device_key, "") if device_key is not None else ""
        icon_label.setPixmap(device_status_icon(state).pixmap(16, 16))
        base_text = text_label.text().split(":", 1)[0].strip()
        text_label.setText(base_text if not detail else f"{base_text}: {detail}")
        tooltip = device_status_tooltip(base_text, state, port_name=port_name, detail=detail)
        icon_label.setToolTip(tooltip)
        text_label.setToolTip(tooltip)


def sync_window_control_icons(window) -> None:
    return
