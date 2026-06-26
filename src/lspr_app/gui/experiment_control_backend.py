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

