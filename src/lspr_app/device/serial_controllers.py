from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from time import monotonic, sleep
from typing import ClassVar

import serial
from serial.tools import list_ports

from lspr_app.device.communication_models import DeviceCommand
from lspr_app.device.connection_registry import release_port, try_claim_port
from lspr_app.device.device_driver import DeviceDriver, DeviceError

@dataclass(slots=True)
class ControllerPort:
    device: str
    description: str
    hwid: str


@dataclass(slots=True)
class ControllerProbe:
    port: str
    controller_type: str
    model: str
    serial_number: str | None = None
    protocol_version: str | None = None


class ControllerError(DeviceError):
    pass


_REGISTERED_CONTROLLERS: list[type["SerialController"]] = []


def register_controller(cls: type["SerialController"]) -> type["SerialController"]:
    if cls not in _REGISTERED_CONTROLLERS:
        _REGISTERED_CONTROLLERS.append(cls)
    return cls


def registered_controllers() -> tuple[type["SerialController"], ...]:
    return tuple(_REGISTERED_CONTROLLERS)


def controller_port_priority(port: ControllerPort) -> int:
    priorities = [
        controller_cls.priority
        for controller_cls in registered_controllers()
        if controller_cls.is_probable_port(port)
    ]
    return max(priorities, default=0)


class SerialController(DeviceDriver):
    controller_type: ClassVar[str] = "controller"
    priority: ClassVar[int] = 0

    # Subclasses set these to configure the shared connect() implementation.
    _BAUD_RATE: ClassVar[int] = 115200
    _TIMEOUT: ClassVar[float] = 0.5
    # Opening a serial port resets many Arduino-family boards via the DTR line,
    # triggering the bootloader. _BOOTLOADER_WAIT_S is how long to wait for the
    # sketch to start before sending any commands. Set to 0 for devices that do
    # not need this (e.g. FTDI-based boards with DTR ignored).
    _BOOTLOADER_WAIT_S: ClassVar[float] = 0.0

    def __init__(self) -> None:
        self._serial: serial.Serial | None = None
        self.port: str | None = None

    @staticmethod
    def list_ports() -> list[ControllerPort]:
        return [
            ControllerPort(device=port.device, description=port.description, hwid=port.hwid)
            for port in list_ports.comports()
            if str(getattr(port, "device", "") or "").strip()
        ]

    @classmethod
    def is_probable_port(cls, port: ControllerPort) -> bool:
        description = port.description.upper()
        hwid = port.hwid.upper()
        return (
            "USB SERIAL" in description
            or "ARDUINO" in description
            or "CH340" in description
            or "FTDI" in description
            or "ATMEGA" in description
            or "2341" in hwid
            or "1A86" in hwid
            or "0403" in hwid
            or "067B" in hwid
        )

    @classmethod
    def probe_port(cls, port: str) -> ControllerProbe:
        controller = cls()
        try:
            controller.connect(port)
            return controller.get_probe()
        finally:
            controller.close()

    def connect(self, port: str) -> None:
        self.close()
        if not try_claim_port(port, self.controller_type):
            raise ControllerError(f"Port {port} is busy.")
        try:
            self._serial = serial.Serial(
                port=port,
                baudrate=self._BAUD_RATE,
                bytesize=8,
                parity="N",
                stopbits=1,
                timeout=self._TIMEOUT,
                write_timeout=self._TIMEOUT,
            )
        except Exception:
            release_port(port, self.controller_type)
            raise
        self.port = port
        if self._BOOTLOADER_WAIT_S:
            sleep(self._BOOTLOADER_WAIT_S)
        self._serial.reset_input_buffer()
        self._post_connect()

    def _post_connect(self) -> None:
        """Called after the port is open and the bootloader wait has elapsed.

        Override in subclasses that need a post-connect handshake.
        """

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                if self.port is not None:
                    release_port(self.port, self.controller_type)
                self._serial = None
                self.port = None

    def is_connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @abstractmethod
    def get_probe(self) -> ControllerProbe:
        raise NotImplementedError

    def execute_command(self, command: DeviceCommand) -> object | None:
        command_type = str(command.command_type or "").strip().casefold()
        payload = dict(command.payload or {})
        if command_type in {"switch.set_position", "valve.set_position"}:
            self.set_position(str(payload.get("position", "")))
            return None
        if command_type in {"switch.stop", "valve.stop"}:
            stop = getattr(self, "stop", None)
            if callable(stop):
                stop()
            return None
        if command_type == "raw.query":
            return self.query(str(payload.get("command", "")))
        raise ControllerError(f"Unsupported command type {command.command_type!r} for {type(self).__name__}.")

    def set_position(self, position: str) -> None:
        raise ControllerError(f"{self.controller_type} does not support position commands.")

    def _write(self, command: str) -> None:
        if self._serial is None:
            raise ControllerError(f"{self.controller_type} is not connected.")
        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
        self._serial.write((command + "\n").encode("ascii"))
        self._serial.flush()

    def _read_line(self, max_wait_s: float = 0.75) -> str:
        if self._serial is None:
            raise ControllerError(f"{self.controller_type} is not connected.")
        started = monotonic()
        while monotonic() - started < max_wait_s:
            chunk = self._serial.readline()
            if chunk:
                return chunk.decode("ascii", errors="replace").strip()
        raise ControllerError(f"No response from {self.controller_type}.")

    def query(self, command: str, max_wait_s: float = 0.75) -> str:
        self._write(command)
        return self._read_line(max_wait_s=max_wait_s)


def detect_controller(port: str) -> tuple[SerialController, ControllerProbe]:
    port_info = next((p for p in SerialController.list_ports() if p.device == port), None)
    all_controllers = sorted(registered_controllers(), key=lambda cls: cls.priority, reverse=True)
    if port_info is not None:
        candidates = [cls for cls in all_controllers if cls.is_probable_port(port_info)]
        if not candidates:
            candidates = all_controllers
    else:
        candidates = all_controllers

    errors: list[str] = []
    for controller_cls in candidates:
        controller = controller_cls()
        try:
            controller.connect(port)
            probe = controller.get_probe()
            return controller, probe
        except Exception as exc:
            errors.append(f"{controller_cls.controller_type}: {exc}")
            controller.close()
    raise ControllerError("; ".join(errors) if errors else f"No registered controller matched {port}.")


def auto_connect_best_port() -> tuple[SerialController | None, ControllerProbe | None]:
    ports = SerialController.list_ports()
    probable_ports = [
        port
        for port in ports
        if controller_port_priority(port) > 0
    ]
    candidate_ports = sorted(probable_ports, key=controller_port_priority, reverse=True) if probable_ports else ports
    for port in candidate_ports:
        try:
            return detect_controller(port.device)
        except ControllerError:
            continue
    return None, None
