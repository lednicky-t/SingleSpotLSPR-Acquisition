from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from lspr_app.device.device_lifecycle import DeviceLifecycleController
from lspr_app.device.device_types import PUMP, SELECTOR, SWITCH

_DEVICE_CHECKBOX_LABELS = (
    (PUMP, "Pump (Reglo ICC)"),
    (SWITCH, "Switch / injection valve (Arduino)"),
    (SELECTOR, "Selector rotary valve (AMF M-Switch)"),
)


class HardwareDevicesDialog(QDialog):
    """Lets the user turn individual device types off so this app stops
    managing them: they're skipped during hardware scans and their titlebar
    status item disappears. Useful both for hardware that simply isn't part
    of a given setup, and as a fallback to hide a device whose driver isn't
    reliable yet."""

    def __init__(self, window, parent: QWidget | None = None) -> None:
        super().__init__(parent or window)
        self._window = window
        self.setWindowTitle("Hardware devices")
        self.setModal(True)
        self.setMinimumWidth(420)

        self._checks: dict[str, QCheckBox] = {}
        controller = DeviceLifecycleController.shared()
        enabled = controller.enabled_devices()
        for device_key, label in _DEVICE_CHECKBOX_LABELS:
            check = QCheckBox(label)
            check.setChecked(bool(enabled.get(device_key, True)))
            self._checks[device_key] = check

        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        info_label = QLabel(
            "Choose which device types this app manages. Unchecked devices are\n"
            "skipped during hardware scans and hidden from the titlebar status strip.\n"
            "Turning a device off disconnects it immediately."
        )
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        for device_key, _label in _DEVICE_CHECKBOX_LABELS:
            layout.addWidget(self._checks[device_key])

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_ok)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

    def _on_ok(self) -> None:
        enabled = {device_key: check.isChecked() for device_key, check in self._checks.items()}
        if hasattr(self._window, "_apply_device_enablement"):
            self._window._apply_device_enablement(enabled)
        self.accept()


def show_hardware_devices_dialog_for(window) -> None:
    dialog = HardwareDevicesDialog(window)
    dialog.exec()
