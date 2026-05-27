from __future__ import annotations

from dataclasses import dataclass

from lspr_app.device.port_assignments import get_port_assignment
from lspr_app.device.reglo_icc import PumpPort, RegloICCClient, is_probable_reglo_port
from lspr_app.device.serial_controllers import ControllerPort, SerialController, registered_controllers
from lspr_app.device import valve_controllers  # noqa: F401  # Ensure serial controller registrations are loaded.


@dataclass(slots=True)
class ConnectedSerialDevice:
    port: str
    description: str
    hwid: str
    recognized_device: str
    recognition_state: str
    manual_assignment: str = "auto"
    details: str = ""


def _format_controller_label(controller_type: str | None, model: str | None) -> str:
    kind = str(controller_type or "controller").replace("-", " ").strip()
    if model:
        return f"{kind.title()}: {model}"
    return kind.title()


def _probe_pump_device(port: ControllerPort) -> ConnectedSerialDevice:
    pump_port = PumpPort(device=port.device, description=port.description, hwid=port.hwid)
    if not is_probable_reglo_port(pump_port):
        return ConnectedSerialDevice(
            port=port.device,
            description=port.description,
            hwid=port.hwid,
            recognized_device="Unrecognized",
            recognition_state="unrecognized",
            manual_assignment=get_port_assignment(port.device),
        )

    try:
        probe = RegloICCClient.probe_port(port.device)
    except Exception as exc:
        return ConnectedSerialDevice(
            port=port.device,
            description=port.description,
            hwid=port.hwid,
            recognized_device="Likely pump controller",
            recognition_state="probable",
            manual_assignment=get_port_assignment(port.device),
            details=str(exc),
        )

    details = "; ".join(
        part
        for part in (
            f"serial {probe.serial_number}" if probe.serial_number else "",
            f"protocol {probe.protocol_version}" if probe.protocol_version else "",
            f"{probe.channel_count} channels" if probe.channel_count else "",
        )
        if part
    )
    model = probe.model or "Pump controller"
    return ConnectedSerialDevice(
        port=port.device,
        description=port.description,
        hwid=port.hwid,
        recognized_device=f"Pump controller: {model}",
        recognition_state="confirmed",
        manual_assignment=get_port_assignment(port.device),
        details=details,
    )


def _matching_controller_classes(port: ControllerPort) -> list[type[SerialController]]:
    return sorted(
        [controller_cls for controller_cls in registered_controllers() if controller_cls.is_probable_port(port)],
        key=lambda cls: cls.priority,
        reverse=True,
    )


def _probe_controller_device(port: ControllerPort) -> ConnectedSerialDevice:
    candidate_classes = _matching_controller_classes(port)
    if not candidate_classes:
        return ConnectedSerialDevice(
            port=port.device,
            description=port.description,
            hwid=port.hwid,
            recognized_device="Unrecognized",
            recognition_state="unrecognized",
            manual_assignment=get_port_assignment(port.device),
        )

    errors: list[str] = []
    for controller_cls in candidate_classes:
        controller = controller_cls()
        try:
            controller.connect(port.device)
            probe = controller.get_probe()
            details = "; ".join(
                part
                for part in (
                    f"serial {probe.serial_number}" if probe.serial_number else "",
                    f"protocol {probe.protocol_version}" if probe.protocol_version else "",
                )
                if part
            )
            label = _format_controller_label(probe.controller_type, probe.model)
            return ConnectedSerialDevice(
                port=port.device,
                description=port.description,
                hwid=port.hwid,
                recognized_device=label,
                recognition_state="confirmed",
                manual_assignment=get_port_assignment(port.device),
                details=details,
            )
        except Exception as exc:
            errors.append(f"{controller_cls.controller_type}: {exc}")
        finally:
            try:
                controller.close()
            except Exception:
                pass

    if errors:
        return ConnectedSerialDevice(
            port=port.device,
            description=port.description,
            hwid=port.hwid,
            recognized_device="Likely serial controller",
            recognition_state="probable",
            manual_assignment=get_port_assignment(port.device),
            details="; ".join(errors),
        )
    return ConnectedSerialDevice(
        port=port.device,
        description=port.description,
        hwid=port.hwid,
        recognized_device="Unrecognized",
        recognition_state="unrecognized",
        manual_assignment=get_port_assignment(port.device),
    )


def scan_connected_serial_devices() -> list[ConnectedSerialDevice]:
    ports = SerialController.list_ports()
    inventory: list[ConnectedSerialDevice] = []
    for port in ports:
        record = _probe_pump_device(port)
        if record.recognition_state == "unrecognized":
            record = _probe_controller_device(port)
        inventory.append(record)
    return inventory
