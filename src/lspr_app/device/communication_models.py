from __future__ import annotations

from dataclasses import dataclass, field


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
    mswitch_devices: list[object]
    amf_tools_available: bool


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    label: str
    type: str
    driver: str
    endpoint: str | None
    role: str | None = None
    identity: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class DeviceStatus:
    label: str
    type: str
    driver: str
    endpoint: str | None
    connected: bool
    state: str
    last_error: str | None
    identity: dict[str, str] = field(default_factory=dict)


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
    payload: dict[str, object]
    timeout_s: float = 2.0


@dataclass(frozen=True, slots=True)
class DeviceCommandResult:
    label: str
    command_type: str
    success: bool
    response: object | None
    error: str | None
    duration_ms: float
