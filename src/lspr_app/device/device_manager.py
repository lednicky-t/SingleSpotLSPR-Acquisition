from __future__ import annotations

from dataclasses import asdict
from time import perf_counter

from lspr_app.device.amf_mswitch import AMFSwitchController, amf_tools_available, detect_amf_mswitch_devices
from lspr_app.device.communication_models import (
    DeviceCommand,
    DeviceCommandResult,
    DeviceProfile,
    DeviceStatus,
    PortDescriptor,
    PortRefreshData,
    ProbeResult,
)
from lspr_app.device.connection_registry import snapshot_port_ownership
from lspr_app.device.port_assignments import device_assignment_label, get_port_assignment, set_port_assignment
from lspr_app.device.reglo_icc import RegloICCClient
from lspr_app.device.serial_controllers import ControllerError, SerialController
from lspr_app.device.valve_controllers import detect_valve_controller
from lspr_app.storage.app_config import load_app_setting, save_app_setting


_DEVICE_PROFILE_SETTING_KEY = "device_profiles"
_DEVICE_COMM_SERVICE: "DeviceCommunicationService" | None = None


class DeviceCommunicationService:
    def __init__(self) -> None:
        self._profiles: dict[str, DeviceProfile] = {}
        self._connections: dict[str, object] = {}
        self._last_errors: dict[str, str] = {}
        self.load_profiles()

    @classmethod
    def shared(cls) -> "DeviceCommunicationService":
        global _DEVICE_COMM_SERVICE
        if _DEVICE_COMM_SERVICE is None:
            _DEVICE_COMM_SERVICE = cls()
            _DEVICE_COMM_SERVICE.ensure_default_profiles()
        return _DEVICE_COMM_SERVICE

    def register_profile(self, profile: DeviceProfile) -> None:
        self._profiles[profile.label] = profile
        self.save_profiles()

    def profile(self, label: str) -> DeviceProfile | None:
        return self._profiles.get(str(label or ""))

    def scan_passive(self) -> list[PortDescriptor]:
        ownership = snapshot_port_ownership()
        ports = SerialController.list_ports()
        return [
            PortDescriptor(
                port=port.device,
                description=port.description,
                hwid=port.hwid,
                assignment=get_port_assignment(port.device),
                owner=ownership.get(port.device, ""),
                last_probe="",
            )
            for port in ports
        ]

    def probe_endpoint(self, endpoint: str, expected_type: str | None = None) -> ProbeResult:
        started = perf_counter()
        endpoint = str(endpoint or "").strip()
        if not endpoint:
            return ProbeResult(endpoint="", detected_type=None, driver=None, identity={}, success=False, error="No endpoint selected.", duration_ms=0.0)

        expected = str(expected_type or "auto").strip().casefold()
        pump_error = None
        valve_error = None
        switch_error = None
        try:
            if expected in {"pump", "reglo", "reglo-icc", "auto"}:
                pump = RegloICCClient.probe_port(endpoint)
                identity = {
                    "model": pump.model,
                    "serial_number": pump.serial_number,
                    "protocol_version": pump.protocol_version,
                    "channel_count": str(pump.channel_count),
                }
                return ProbeResult(endpoint, "pump", "reglo_icc", identity, True, None, (perf_counter() - started) * 1000.0)
        except Exception as pump_exc:
            pump_error = str(pump_exc)

        try:
            if expected in {"valve", "auto"}:
                controller, probe = detect_valve_controller(endpoint)
                try:
                    identity = {
                        "model": probe.model,
                        "serial_number": probe.serial_number or "",
                        "protocol_version": probe.protocol_version or "",
                        "controller_type": probe.controller_type,
                    }
                    return ProbeResult(endpoint, "valve", probe.controller_type, identity, True, None, (perf_counter() - started) * 1000.0)
                finally:
                    controller.close()
        except Exception as valve_exc:
            valve_error = str(valve_exc)

        try:
            if expected in {"mswitch", "switch", "auto"} and amf_tools_available():
                controller, probe = self._probe_mswitch(endpoint)
                try:
                    identity = {
                        "model": probe.model,
                        "serial_number": probe.serial_number or "",
                        "protocol_version": probe.protocol_version or "",
                        "controller_type": probe.controller_type,
                    }
                    return ProbeResult(endpoint, "switch", probe.controller_type, identity, True, None, (perf_counter() - started) * 1000.0)
                finally:
                    controller.close()
        except Exception as switch_exc:
            switch_error = str(switch_exc)

        error = pump_error or valve_error or switch_error or "No matching device detected."
        return ProbeResult(endpoint, None, None, {}, False, error, (perf_counter() - started) * 1000.0)

    def connect_device(self, label: str) -> DeviceStatus:
        profile = self._require_profile(label)
        self.disconnect_device(label)
        endpoint = str(profile.endpoint or "").strip()
        if not endpoint:
            raise ControllerError(f"Device profile {label!r} has no endpoint.")

        started = perf_counter()
        if profile.driver == "reglo_icc" or profile.type == "pump":
            client = RegloICCClient()
            client.connect(endpoint)
            probe = client.get_probe()
            self._connections[label] = client
            self._last_errors.pop(label, None)
            return self._make_status(label, profile, True, None, {
                "model": probe.model,
                "serial_number": probe.serial_number,
                "protocol_version": probe.protocol_version,
                "channel_count": str(probe.channel_count),
            })

        if profile.driver == "amf-mswitch" or profile.type in {"switch", "mswitch"}:
            controller = AMFSwitchController()
            controller.connect(endpoint)
            probe = controller.get_probe()
            self._connections[label] = controller
            self._last_errors.pop(label, None)
            return self._make_status(label, profile, True, None, {
                "model": probe.model,
                "serial_number": probe.serial_number or "",
                "protocol_version": probe.protocol_version or "",
                "controller_type": probe.controller_type,
            })

        controller, probe = detect_valve_controller(endpoint)
        self._connections[label] = controller
        self._last_errors.pop(label, None)
        return self._make_status(label, profile, True, None, {
            "model": probe.model,
            "serial_number": probe.serial_number or "",
            "protocol_version": probe.protocol_version or "",
            "controller_type": probe.controller_type,
        })

    def disconnect_device(self, label: str) -> None:
        connection = self._connections.pop(str(label or ""), None)
        if connection is None:
            return
        try:
            close = getattr(connection, "close", None)
            if callable(close):
                close()
        finally:
            self._last_errors.pop(str(label or ""), None)

    def send_command(self, label: str, command: DeviceCommand) -> DeviceCommandResult:
        started = perf_counter()
        connection = self._connections.get(str(label or ""))
        if connection is None:
            return DeviceCommandResult(str(label or ""), command.command_type, False, None, "Device is not connected.", 0.0)

        try:
            response = self._dispatch_command(connection, command)
            self._last_errors.pop(str(label or ""), None)
            return DeviceCommandResult(str(label or ""), command.command_type, True, response, None, (perf_counter() - started) * 1000.0)
        except Exception as exc:
            self._last_errors[str(label or "")] = str(exc)
            return DeviceCommandResult(str(label or ""), command.command_type, False, None, str(exc), (perf_counter() - started) * 1000.0)

    def status(self, label: str) -> DeviceStatus:
        label = str(label or "").strip()
        profile = self._profiles.get(label)
        if profile is None:
            connection = self._connections.get(label)
            if connection is None:
                raise ControllerError(f"Unknown device label {label!r}.")
            profile = DeviceProfile(label=label, type="unknown", driver=type(connection).__name__, endpoint=getattr(connection, "port", None))
        connection = self._connections.get(label)
        connected = bool(connection is not None and getattr(connection, "is_connected", lambda: False)())
        return self._make_status(label, profile, connected, self._last_errors.get(label), {})

    def connection(self, label: str) -> object | None:
        return self._connections.get(str(label or "").strip())

    def is_connected(self, label: str) -> bool:
        connection = self._connections.get(str(label or "").strip())
        return bool(connection is not None and getattr(connection, "is_connected", lambda: False)())

    def adopt_connection(self, label: str, connection: object) -> None:
        label = str(label or "").strip()
        if not label:
            raise ControllerError("Device label is required.")
        profile = self._require_profile(label)
        self._connections[label] = connection
        self._last_errors.pop(label, None)
        if profile.endpoint is None:
            self._profiles[label] = DeviceProfile(
                label=profile.label,
                type=profile.type,
                driver=profile.driver,
                endpoint=getattr(connection, "port", None),
                role=profile.role,
                identity=dict(profile.identity),
                enabled=profile.enabled,
            )
            self.save_profiles()

    def list_devices(self) -> list[DeviceStatus]:
        labels = list(self._profiles)
        for label in self._connections:
            if label not in self._profiles:
                labels.append(label)
        return [self.status(label) for label in labels]

    def ensure_default_profiles(self) -> None:
        defaults = (
            DeviceProfile("pump_main", "pump", "reglo_icc", None, role="sample_pump"),
            DeviceProfile("pump_aux", "pump", "reglo_icc", None, role="aux_pump"),
            DeviceProfile("pump_waste", "pump", "reglo_icc", None, role="waste_pump"),
            DeviceProfile("valve_inlet", "valve", "auto", None, role="inlet_valve"),
            DeviceProfile("valve_outlet", "valve", "auto", None, role="outlet_valve"),
            DeviceProfile("switch_main", "switch", "amf-mswitch", None, role="selector_switch"),
        )
        for profile in defaults:
            self._profiles.setdefault(profile.label, profile)
        if not self._profiles:
            for profile in defaults:
                self._profiles[profile.label] = profile
        self.save_profiles()

    def register_endpoint_assignment(self, label: str, endpoint: str, device_type: str = "auto", driver: str = "auto", role: str | None = None) -> DeviceProfile:
        profile = DeviceProfile(label=label, type=device_type, driver=driver, endpoint=endpoint, role=role)
        self._profiles[label] = profile
        set_port_assignment(endpoint, device_assignment_label(device_type))
        self.save_profiles()
        return profile

    def load_profiles(self) -> None:
        raw = load_app_setting(_DEVICE_PROFILE_SETTING_KEY, [])
        self._profiles.clear()
        if isinstance(raw, dict):
            raw = raw.get("devices", [])
        if not isinstance(raw, list):
            return
        for item in raw:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label", "")).strip()
            if not label:
                continue
            profile = DeviceProfile(
                label=label,
                type=str(item.get("type", "unknown") or "unknown"),
                driver=str(item.get("driver", "auto") or "auto"),
                endpoint=(str(item.get("endpoint", "")).strip() or None),
                role=(str(item.get("role", "")).strip() or None),
                identity={str(k): str(v) for k, v in dict(item.get("identity", {}) or {}).items()},
                enabled=bool(item.get("enabled", True)),
            )
            self._profiles[label] = profile

    def save_profiles(self) -> None:
        payload = {"devices": [asdict(profile) for profile in self._profiles.values()]}
        save_app_setting(_DEVICE_PROFILE_SETTING_KEY, payload)

    def _dispatch_command(self, connection: object, command: DeviceCommand) -> object | None:
        command_type = str(command.command_type or "").strip().casefold()
        payload = dict(command.payload or {})
        if isinstance(connection, RegloICCClient):
            if command_type == "pump.stop_all":
                connection.stop_all(int(payload.get("channel_count", 4)))
                return None
            if command_type == "pump.start":
                connection.start_channel(int(payload.get("channel", 1)))
                return None
            if command_type == "pump.stop":
                connection.stop_channel(int(payload.get("channel", 1)))
                return None
            if command_type == "pump.set_flow":
                connection.apply_channel(
                    int(payload.get("channel", 1)),
                    float(payload.get("flow_ul_min", 0.0)),
                    str(payload.get("direction", "OFF")),
                    float(payload.get("tube_mm", 0.0)),
                    start=bool(payload.get("start", False)),
                )
                return None
            if command_type == "pump.query":
                return connection.query(str(payload.get("command", "")))
        if hasattr(connection, "set_position"):
            if command_type == "valve.set_position":
                connection.set_position(str(payload.get("position", "")))
                return None
            if command_type == "valve.stop" and hasattr(connection, "stop"):
                connection.stop()
                return None
        if isinstance(connection, AMFSwitchController):
            if command_type == "switch.home":
                connection.home(block=bool(payload.get("block", True)))
                return None
            if command_type == "switch.move_to":
                connection.move_to(int(payload.get("position", 1)), block=bool(payload.get("block", True)))
                return None
            if command_type == "switch.get_position":
                return connection.get_position()
        if command_type == "raw.query" and hasattr(connection, "query"):
            return connection.query(str(payload.get("command", "")))
        raise ControllerError(f"Unsupported command type {command.command_type!r} for {type(connection).__name__}.")

    def _require_profile(self, label: str) -> DeviceProfile:
        label = str(label or "").strip()
        profile = self._profiles.get(label)
        if profile is None:
            raise ControllerError(f"Unknown device label {label!r}.")
        return profile

    def _make_status(self, label: str, profile: DeviceProfile, connected: bool, last_error: str | None, identity: dict[str, str]) -> DeviceStatus:
        return DeviceStatus(
            label=label,
            type=profile.type,
            driver=profile.driver,
            endpoint=profile.endpoint,
            connected=connected,
            state="connected" if connected else "disconnected",
            last_error=last_error,
            identity=identity,
        )

    def _probe_mswitch(self, endpoint: str):
        controller = AMFSwitchController()
        controller.connect(endpoint)
        return controller, controller.get_probe()

    def refresh_device_ports(self, generation: int) -> object:
        pump_ports = RegloICCClient.list_ports()
        valve_ports = SerialController.list_ports()
        mswitch_devices = detect_amf_mswitch_devices() if amf_tools_available() else []
        return PortRefreshData(
            generation=generation,
            pump_ports=list(pump_ports),
            valve_ports=list(valve_ports),
            mswitch_devices=list(mswitch_devices),
            amf_tools_available=amf_tools_available(),
        )

    def probe_pump_port(self, port: str):
        return RegloICCClient.probe_port(port)

    def connect_valve_port(self, port: str):
        client, probe = detect_valve_controller(port)
        return client, probe

    def connect_mswitch_port(self, port: str):
        client = AMFSwitchController()
        client.connect(port)
        return client, client.get_probe()
