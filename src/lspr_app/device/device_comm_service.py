from __future__ import annotations

from lspr_app.device.device_manager import DeviceCommunicationService
from lspr_app.device.communication_models import PortRefreshData
from lspr_app.device.reglo_icc import PumpProbe
from lspr_app.device.serial_controllers import ControllerProbe


def refresh_device_ports(generation: int) -> PortRefreshData:
    service = DeviceCommunicationService.shared()
    payload = service.refresh_device_ports(generation)
    return PortRefreshData(
        generation=payload.generation,
        pump_ports=list(payload.pump_ports),
        valve_ports=list(payload.valve_ports),
        mswitch_devices=list(payload.mswitch_devices),
        amf_tools_available=bool(payload.amf_tools_available),
    )


def probe_pump_port(port: str) -> PumpProbe:
    return DeviceCommunicationService.shared().probe_pump_port(port)


def connect_valve_port(port: str) -> tuple[SerialController, ControllerProbe]:
    return DeviceCommunicationService.shared().connect_valve_port(port)


def connect_mswitch_port(port: str) -> tuple[AMFSwitchController, ControllerProbe]:
    return DeviceCommunicationService.shared().connect_mswitch_port(port)
