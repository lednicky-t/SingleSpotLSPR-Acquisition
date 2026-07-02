from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class DeviceLifecycleState(str, Enum):
    """All valid lifecycle states for a hardware device connection.

    Inherits from ``str`` so that enum values compare equal to the plain
    string literals already used throughout the GUI
    (e.g. ``status.state == "connected"`` still works without change).

    Transitions:
        DISCONNECTED → CONNECTED  : successful connect()
        CONNECTED    → DISCONNECTED: successful disconnect()
        *            → ERROR      : connect() raised an exception
        ERROR        → DISCONNECTED: explicit disconnect() clears the error
        ERROR        → CONNECTED  : successful reconnect()
        DISCONNECTED → DISCOVERED : device found by scan but not yet connected
        DISCOVERED   → CONNECTED  : user-triggered connect after scan
    """

    DISCONNECTED = "disconnected"
    CONNECTED = "connected"
    ERROR = "error"
    DISCOVERED = "discovered"


@dataclass(frozen=True, slots=True)
class PortDescriptor:
    port: str
    description: str
    hwid: str
    assignment: str = "auto"
    owner: str = ""
    last_probe: str = ""


@dataclass(frozen=True, slots=True)
class PortRefreshData:
    generation: int
    pump_ports: list[object]
    valve_ports: list[object]
    selector_devices: list[object]
    amf_tools_available: bool


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    uuid: str
    label: str
    type: str
    driver: str
    endpoint: str | None = None
    role: str | None = None
    display_name: str | None = None
    identity: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""


def new_device_profile(
    *,
    label: str,
    type: str,
    driver: str,
    endpoint: str | None = None,
    role: str | None = None,
    display_name: str | None = None,
    metadata: dict[str, Any] | None = None,
    fingerprint: str = "",
) -> DeviceProfile:
    return DeviceProfile(
        uuid=str(uuid4()),
        label=label,
        type=type,
        driver=driver,
        endpoint=endpoint,
        role=role,
        display_name=display_name,
        metadata=dict(metadata) if metadata else {},
        fingerprint=str(fingerprint or "").strip(),
    )


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    uuid: str
    label: str
    type: str
    driver: str
    endpoint: str | None
    connected: bool
    state: str
    last_error: str | None = None
    identity: dict[str, str] = field(default_factory=dict)
    display_name: str | None = None
    role: str | None = None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    endpoint: str
    detected_type: str | None
    driver: str | None
    identity: dict[str, str]
    success: bool
    error: str | None
    duration_ms: float


@dataclass(frozen=True, slots=True)
class DeviceCommand:
    command_type: str
    payload: dict[str, object] = field(default_factory=dict)
    timeout_s: float = 2.0


@dataclass(frozen=True, slots=True)
class DeviceCommandResult:
    label: str
    command_type: str
    success: bool
    response: object | None
    error: str | None
    duration_ms: float


@dataclass(frozen=True, slots=True)
class DeviceEvent:
    timestamp_s: float
    label: str | None
    endpoint: str | None
    owner: str
    action: str
    command: str | None
    result: str
    duration_ms: float
    message: str


def next_device_label(existing_labels: set[str], device_type: str) -> str:
    prefix = f"{device_type}_"
    numbers: list[int] = []
    for label in existing_labels:
        if label.startswith(prefix):
            suffix = label[len(prefix):]
            if suffix.isdigit():
                numbers.append(int(suffix))
    next_number = 1
    while next_number in numbers:
        next_number += 1
    return f"{prefix}{next_number}"
