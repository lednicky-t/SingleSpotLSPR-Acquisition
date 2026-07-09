from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from lspr_app.gui.experiment_control_capabilities import ExperimentControlCapabilities


@dataclass(frozen=True, slots=True)
class ExperimentControlDeviceState:
    key: str
    connected: bool
    label: str = ""
    detail: str = ""


@runtime_checkable
class ExperimentControlBackend(Protocol):
    def capabilities(self) -> ExperimentControlCapabilities:
        ...

    def device_states(self) -> list[ExperimentControlDeviceState]:
        ...

    def is_device_connected(self, device_key: str) -> bool:
        ...

    def refresh_devices(self) -> bool:
        ...

    def send_command(self, device_key: str, command_type: str, payload: dict[str, object] | None = None) -> bool:
        ...

    def connect_device(self, device_key: str) -> bool:
        ...

    def disconnect_device(self, device_key: str) -> bool:
        ...


class NullExperimentControlBackend:
    def __init__(self, capabilities: ExperimentControlCapabilities | None = None) -> None:
        self._capabilities = capabilities or ExperimentControlCapabilities.evaluation()

    def capabilities(self) -> ExperimentControlCapabilities:
        return self._capabilities

    def device_states(self) -> list[ExperimentControlDeviceState]:
        return []

    def is_device_connected(self, device_key: str) -> bool:
        _ = device_key
        return False

    def refresh_devices(self) -> bool:
        return False

    def send_command(self, device_key: str, command_type: str, payload: dict[str, object] | None = None) -> bool:
        _ = (device_key, command_type, payload)
        return False

    def connect_device(self, device_key: str) -> bool:
        _ = device_key
        return False

    def disconnect_device(self, device_key: str) -> bool:
        _ = device_key
        return False


class AcquisitionExperimentControlBackend:
    """Concrete backend that wraps an ExperimentControlWindow for acquisition mode.

    Delegates device queries and commands to the window's existing device-service
    helpers so the controller no longer needs to reach into window private methods.
    The window's lifecycle operations (shutdown_devices, port-scan UI) are not
    delegated here; they remain window-owned until the full V49 split lands.
    """

    def __init__(self, window) -> None:
        self._window = window

    def capabilities(self) -> ExperimentControlCapabilities:
        return self._window._capabilities  # type: ignore[attr-defined]

    def device_states(self) -> list[ExperimentControlDeviceState]:
        states: list[ExperimentControlDeviceState] = []
        for key in ("pump", "valve", "mswitch"):
            connected = self._window._service_device_connected(key)  # type: ignore[attr-defined]
            ctrl_type, port = self._window._service_connection_detail(key)  # type: ignore[attr-defined]
            if ctrl_type and port:
                detail = f"{ctrl_type} @ {port}"
            else:
                detail = port or ctrl_type or ""
            label = self._window._device_label_for(key)  # type: ignore[attr-defined]
            states.append(ExperimentControlDeviceState(key=key, connected=connected, label=label, detail=detail))
        return states

    def is_device_connected(self, device_key: str) -> bool:
        return self._window._service_device_connected(device_key)  # type: ignore[attr-defined]

    def refresh_devices(self) -> bool:
        return bool(self._window.refresh_device_ports())  # type: ignore[attr-defined]

    def send_command(self, device_key: str, command_type: str, payload: dict[str, object] | None = None) -> bool:
        return self._window._send_device_command(device_key, command_type, payload)  # type: ignore[attr-defined]

    def connect_device(self, device_key: str) -> bool:
        _ = device_key
        return False  # connection flow goes through the window's port-scan UI

    def disconnect_device(self, device_key: str) -> bool:
        _ = device_key
        return False  # disconnection goes through the window's shutdown_devices lifecycle
