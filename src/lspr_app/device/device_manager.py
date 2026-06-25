from __future__ import annotations

from dataclasses import asdict, replace
from collections import deque
from time import perf_counter

from lspr_app.device.amf_mswitch import AMFSwitchController, amf_tools_available, detect_amf_mswitch_devices, detect_amf_selector_devices
from lspr_app.device.communication_models import (
    DeviceCommand,
    DeviceCommandResult,
    DeviceEvent,
    DeviceProfile,
    DeviceStatus,
    PortDescriptor,
    PortRefreshData,
    ProbeResult,
    next_device_label,
    new_device_profile,
)
from lspr_app.device.connection_registry import release_port, snapshot_port_ownership
from lspr_app.device.port_assignments import device_assignment_label, get_port_assignment, set_port_assignment
from lspr_app.device.reglo_icc import RegloICCClient
from lspr_app.device.serial_controllers import ControllerError, SerialController
from lspr_app.device.valve_controllers import detect_valve_controller
from lspr_app.storage.app_config import load_app_setting, save_app_setting


_DEVICE_PROFILE_SETTING_KEY = "device_profiles"
_DEVICE_COMM_SERVICE: "DeviceCommunicationService" | None = None


def extract_usb_fingerprint(hwid: str) -> str:
    """Extract a stable device fingerprint from a USB port HWID string.

    Looks for SER= (USB serial number) first, then falls back to VID:PID.
    Returns an empty string if no useful identifier can be extracted.
    """
    for part in str(hwid or "").split():
        if part.upper().startswith("SER="):
            serial = part[4:].strip()
            if serial and serial.upper() not in {"0", "000000", "NONE", "NULL", "N/A", ""}:
                return f"usb-ser:{serial}"
    return ""

_LEGACY_LABEL_MIGRATIONS: dict[str, tuple[str, str, str | None, str | None]] = {
    "pump_main": ("pump_1", "Main Pump", "sample_pump", "pump_main"),
    "pump_aux": ("pump_2", "Aux Pump", "aux_pump", "pump_aux"),
    "pump_waste": ("pump_3", "Waste Pump", "waste_pump", "pump_waste"),
    "valve_inlet": ("valve_1", "Inlet Valve", "inlet_valve", "valve_inlet"),
    "valve_outlet": ("valve_2", "Outlet Valve", "outlet_valve", "valve_outlet"),
    "switch_main": ("selector_1", "Main Selector", "main_selector", "switch_main"),
    "switch_1": ("selector_1", "Main Selector", "main_selector", "switch_1"),
}


class DeviceCommunicationService:
    def __init__(self) -> None:
        self._profiles: dict[str, DeviceProfile] = {}
        self._connections: dict[str, object] = {}
        self._connection_owners: dict[str, str] = {}
        self._last_errors: dict[str, str] = {}
        self._events: deque[DeviceEvent] = deque(maxlen=1000)
        self.load_profiles()

    @classmethod
    def shared(cls) -> "DeviceCommunicationService":
        global _DEVICE_COMM_SERVICE
        if _DEVICE_COMM_SERVICE is None:
            _DEVICE_COMM_SERVICE = cls()
            _DEVICE_COMM_SERVICE.ensure_default_profiles()
        return _DEVICE_COMM_SERVICE

    def list_profiles(self) -> list[DeviceProfile]:
        return list(self._profiles.values())

    def get_profile(self, label: str) -> DeviceProfile | None:
        return self._profiles.get(self._canonical_label(label))

    def save_profile(self, profile: DeviceProfile) -> None:
        original_label = str(profile.label or "").strip()
        normalized = self._normalize_profile(profile)
        if original_label and original_label != normalized.label:
            self._profiles.pop(original_label, None)
        self._profiles[normalized.label] = normalized
        self.save_profiles()

    def delete_profile(self, label: str) -> None:
        normalized_label = self._canonical_label(label)
        if not normalized_label:
            return
        self.disconnect(normalized_label)
        self._profiles.pop(normalized_label, None)
        self.save_profiles()

    # Backward-compatible names used by the older codebase.
    def register_profile(self, profile: DeviceProfile) -> None:
        self.save_profile(profile)

    def profile(self, label: str) -> DeviceProfile | None:
        return self.get_profile(label)

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
            result = ProbeResult(endpoint="", detected_type=None, driver=None, identity={}, success=False, error="No endpoint selected.", duration_ms=0.0)
            self._record_event(label=None, endpoint=None, owner="device_comm:probe", action="probe", command=None, result="fail", duration_ms=0.0, message=result.error or "")
            return result

        expected = str(expected_type or "auto").strip().casefold()
        pump_error = None
        valve_error = None
        switch_error = None
        owner = "device_comm:probe"

        try:
            if expected in {"pump", "reglo", "reglo-icc", "auto"}:
                pump = RegloICCClient.probe_port(endpoint)
                identity = {
                    "model": pump.model,
                    "serial_number": pump.serial_number,
                    "protocol_version": pump.protocol_version,
                    "channel_count": str(pump.channel_count),
                }
                result = ProbeResult(endpoint, "pump", "reglo_icc", identity, True, None, (perf_counter() - started) * 1000.0)
                self._record_event(label="pump", endpoint=endpoint, owner=owner, action="probe", command=None, result="success", duration_ms=result.duration_ms, message="REGLO ICC probe")
                return result
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
                    result = ProbeResult(endpoint, "valve", probe.controller_type, identity, True, None, (perf_counter() - started) * 1000.0)
                    self._record_event(label="valve", endpoint=endpoint, owner=owner, action="probe", command=None, result="success", duration_ms=result.duration_ms, message=probe.model)
                    return result
                finally:
                    controller.close()
        except Exception as valve_exc:
            valve_error = str(valve_exc)

        try:
            if expected in {"selector", "auto"} and amf_tools_available():
                controller, probe = self._probe_selector(endpoint)
                try:
                    identity = {
                        "model": probe.model,
                        "serial_number": probe.serial_number or "",
                        "protocol_version": probe.protocol_version or "",
                        "controller_type": probe.controller_type,
                    }
                    result = ProbeResult(endpoint, "selector", probe.controller_type, identity, True, None, (perf_counter() - started) * 1000.0)
                    self._record_event(label="selector", endpoint=endpoint, owner=owner, action="probe", command=None, result="success", duration_ms=result.duration_ms, message=probe.model)
                    return result
                finally:
                    controller.close()
        except Exception as switch_exc:
            switch_error = str(switch_exc)

        error = pump_error or valve_error or switch_error or "No matching device detected."
        result = ProbeResult(endpoint, None, None, {}, False, error, (perf_counter() - started) * 1000.0)
        self._record_event(label=None, endpoint=endpoint, owner=owner, action="probe", command=None, result="fail", duration_ms=result.duration_ms, message=error)
        return result

    def connect(self, label: str) -> DeviceStatus:
        profile = self._require_profile(label)
        self.disconnect(label)
        endpoint = str(profile.endpoint or "").strip()
        if not endpoint:
            raise ControllerError(f"Device profile {label!r} has no endpoint.")

        try:
            if profile.driver == "reglo_icc" or profile.type == "pump":
                client = RegloICCClient()
                client.connect(endpoint)
                probe = client.get_probe()
                self._connections[profile.label] = client
                self._connection_owners[profile.label] = client._claim_owner
                self._last_errors.pop(profile.label, None)
                status = self._make_status(profile.label, profile, True, None, {
                    "model": probe.model,
                    "serial_number": probe.serial_number,
                    "protocol_version": probe.protocol_version,
                    "channel_count": str(probe.channel_count),
                })
                self._record_event(label=profile.label, endpoint=endpoint, owner=client._claim_owner, action="connect", command=None, result="success", duration_ms=0.0, message=probe.model)
                return status

            if profile.driver == "amf-mswitch" or profile.type == "selector":
                controller = AMFSwitchController()
                controller.connect(endpoint)
                probe = controller.get_probe()
                self._connections[profile.label] = controller
                self._connection_owners[profile.label] = f"amf-mswitch:{id(controller)}"
                self._last_errors.pop(profile.label, None)
                status = self._make_status(profile.label, profile, True, None, {
                    "model": probe.model,
                    "serial_number": probe.serial_number or "",
                    "protocol_version": probe.protocol_version or "",
                    "controller_type": probe.controller_type,
                })
                self._record_event(label=profile.label, endpoint=endpoint, owner=self._connection_owners[profile.label], action="connect", command=None, result="success", duration_ms=0.0, message=probe.model)
                return status

            if profile.type in {"valve"} or profile.driver not in {"auto", "unknown", ""}:
                controller, probe = detect_valve_controller(endpoint)
                self._connections[profile.label] = controller
                self._connection_owners[profile.label] = controller.controller_type
                self._last_errors.pop(profile.label, None)
                status = self._make_status(profile.label, profile, True, None, {
                    "model": probe.model,
                    "serial_number": probe.serial_number or "",
                    "protocol_version": probe.protocol_version or "",
                    "controller_type": probe.controller_type,
                })
                self._record_event(label=profile.label, endpoint=endpoint, owner=controller.controller_type, action="connect", command=None, result="success", duration_ms=0.0, message=probe.model)
                return status

            raise ControllerError(
                f"Cannot connect {label!r}: device type {profile.type!r} / driver {profile.driver!r} is unresolved. "
                "Probe the port first or assign a device type."
            )
        except Exception:
            self._record_event(label=profile.label, endpoint=endpoint, owner=f"device_comm:{profile.label}", action="connect", command=None, result="fail", duration_ms=0.0, message="connection failed")
            raise

    def disconnect(self, label: str) -> DeviceStatus:
        normalized = str(label or "").strip()
        profile = self._profiles.get(normalized)
        connection = self._connections.pop(normalized, None)
        owner = self._connection_owners.pop(normalized, None)
        endpoint = getattr(connection, "port", None) if connection is not None else (profile.endpoint if profile is not None else None)
        if endpoint and owner:
            release_port(endpoint, owner)
        if connection is not None:
            try:
                close = getattr(connection, "close", None)
                if callable(close):
                    close()
            finally:
                self._last_errors.pop(normalized, None)
        if profile is None and connection is None:
            raise ControllerError(f"Unknown device label {label!r}.")
        if profile is None:
            profile = new_device_profile(label=normalized, type="unknown", driver=type(connection).__name__, endpoint=getattr(connection, "port", None))
        self._record_event(label=normalized, endpoint=str(endpoint or "") or None, owner=owner or "", action="disconnect", command=None, result="success", duration_ms=0.0, message="")
        return self._make_status(normalized, profile, False, self._last_errors.get(normalized), {})

    def disconnect_device(self, label: str) -> None:
        self.disconnect(label)

    def send_command(self, label: str, command: DeviceCommand) -> DeviceCommandResult:
        started = perf_counter()
        normalized = str(label or "").strip()
        connection = self._connections.get(normalized)
        if connection is None:
            return DeviceCommandResult(normalized, command.command_type, False, None, "Device is not connected.", 0.0)

        try:
            response = self._dispatch_command(connection, command)
            self._last_errors.pop(normalized, None)
            result = DeviceCommandResult(normalized, command.command_type, True, response, None, (perf_counter() - started) * 1000.0)
            self._record_event(label=normalized, endpoint=str(getattr(connection, "port", None) or ""), owner=self._connection_owners.get(normalized, ""), action="command", command=command.command_type, result="success", duration_ms=result.duration_ms, message="")
            return result
        except Exception as exc:
            self._last_errors[normalized] = str(exc)
            result = DeviceCommandResult(normalized, command.command_type, False, None, str(exc), (perf_counter() - started) * 1000.0)
            self._record_event(label=normalized, endpoint=str(getattr(connection, "port", None) or ""), owner=self._connection_owners.get(normalized, ""), action="command", command=command.command_type, result="fail", duration_ms=result.duration_ms, message=str(exc))
            return result

    def status(self, label: str) -> DeviceStatus:
        normalized = self._canonical_label(label)
        profile = self._profiles.get(normalized)
        if profile is None:
            connection = self._connections.get(normalized)
            if connection is None:
                raise ControllerError(f"Unknown device label {label!r}.")
            profile = new_device_profile(
                label=normalized,
                type="unknown",
                driver=type(connection).__name__,
                endpoint=getattr(connection, "port", None),
            )
        connection = self._connections.get(normalized)
        connected = bool(connection is not None and getattr(connection, "is_connected", lambda: False)())
        return self._make_status(normalized, profile, connected, self._last_errors.get(normalized), dict(profile.identity))

    def list_statuses(self) -> list[DeviceStatus]:
        labels = list(self._profiles)
        for label in self._connections:
            if label not in self._profiles:
                labels.append(label)
        return [self.status(label) for label in labels]

    # Backward-compatible name used by older UI code.
    def list_devices(self) -> list[DeviceStatus]:
        return self.list_statuses()

    def connection(self, label: str) -> object | None:
        return self._connections.get(self._canonical_label(label))

    def is_connected(self, label: str) -> bool:
        connection = self._connections.get(self._canonical_label(label))
        return bool(connection is not None and getattr(connection, "is_connected", lambda: False)())

    def adopt_connection(self, label: str, connection: object) -> None:
        normalized = self._canonical_label(label)
        if not normalized:
            raise ControllerError("Device label is required.")
        profile = self._require_profile(normalized)
        self._connections[normalized] = connection
        self._connection_owners[normalized] = f"device_comm:adopt:{normalized}"
        self._last_errors.pop(normalized, None)
        if profile.endpoint is None:
            self._profiles[normalized] = replace(profile, endpoint=getattr(connection, "port", None))
            self.save_profiles()

    def ensure_default_profiles(self) -> None:
        _default = {"source": "default"}
        defaults = (
            new_device_profile(label="pump_1", type="pump", driver="reglo_icc", role="sample_pump", display_name="Main Pump", metadata=_default),
            new_device_profile(label="pump_2", type="pump", driver="reglo_icc", role="aux_pump", display_name="Aux Pump", metadata=_default),
            new_device_profile(label="pump_3", type="pump", driver="reglo_icc", role="waste_pump", display_name="Waste Pump", metadata=_default),
            new_device_profile(label="valve_1", type="valve", driver="auto", role="inlet_valve", display_name="Inlet Valve", metadata=_default),
            new_device_profile(label="valve_2", type="valve", driver="auto", role="outlet_valve", display_name="Outlet Valve", metadata=_default),
            new_device_profile(label="selector_1", type="selector", driver="amf-mswitch", role="main_selector", display_name="Main Selector", metadata=_default),
        )
        if not self._profiles:
            for profile in defaults:
                self._profiles[profile.label] = profile
        else:
            for profile in defaults:
                self._profiles.setdefault(profile.label, profile)
        self.save_profiles()

    def register_endpoint_assignment(self, label: str, endpoint: str, device_type: str = "auto", driver: str = "auto", role: str | None = None) -> DeviceProfile:
        normalized_label = str(label or "").strip()
        if not normalized_label:
            normalized_label = next_device_label(set(self._profiles), device_type if device_type != "auto" else "device")
        profile = new_device_profile(label=normalized_label, type=device_type, driver=driver, endpoint=endpoint, role=role)
        self._profiles[normalized_label] = profile
        set_port_assignment(endpoint, device_assignment_label(device_type))
        self.save_profiles()
        return profile

    def find_or_create_profile(
        self,
        *,
        device_type: str,
        fingerprint: str,
        endpoint: str,
        identity: dict[str, str],
        driver: str = "auto",
        display_name: str | None = None,
        role: str | None = None,
    ) -> DeviceProfile:
        """Return an existing profile that matches (type, fingerprint) or create a new one.

        If a matching profile is found on a different endpoint (COM port changed), the
        endpoint is updated in place and persisted. On creation, the next available label
        for *device_type* is auto-assigned (pump_1, pump_2, ...).
        """
        if fingerprint:
            for profile in self._profiles.values():
                if profile.type == device_type and profile.fingerprint == fingerprint:
                    if profile.endpoint != endpoint:
                        updated = replace(profile, endpoint=endpoint, identity=dict(identity))
                        self._profiles[profile.label] = updated
                        self.save_profiles()
                        return updated
                    return profile

        label = next_device_label(set(self._profiles), device_type)
        profile = new_device_profile(
            label=label,
            type=device_type,
            driver=driver,
            endpoint=endpoint,
            role=role,
            display_name=display_name,
            fingerprint=fingerprint,
        )
        profile = replace(profile, identity=dict(identity))
        self._profiles[label] = profile
        self.save_profiles()
        return profile

    def connect_enabled_profiles(self) -> list[DeviceStatus]:
        statuses: list[DeviceStatus] = []
        for profile in self._profiles.values():
            if not profile.enabled or profile.endpoint is None:
                continue
            try:
                statuses.append(self.connect(profile.label))
            except Exception as exc:
                self._last_errors[profile.label] = str(exc)
                statuses.append(self.status(profile.label))
        return statuses

    def safe_stop_all(self) -> list[DeviceCommandResult]:
        results: list[DeviceCommandResult] = []
        for profile in self._profiles.values():
            if profile.type == "pump" and profile.label in self._connections:
                results.append(self.send_command(profile.label, DeviceCommand("pump.stop_all", {"channel_count": 4})))
        return results

    def list_events(self) -> list[DeviceEvent]:
        return list(self._events)

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
            profile = self._profile_from_payload(item)
            if profile is None:
                continue
            self._profiles[profile.label] = profile

    def save_profiles(self) -> None:
        payload = {"devices": [asdict(profile) for profile in self._profiles.values()]}
        save_app_setting(_DEVICE_PROFILE_SETTING_KEY, payload)

    def _profile_from_payload(self, item: dict[str, object]) -> DeviceProfile | None:
        raw_label = str(item.get("label", "")).strip()
        if not raw_label:
            return None
        migrated = _LEGACY_LABEL_MIGRATIONS.get(raw_label)
        if migrated is not None:
            raw_label, display_name, role, legacy_label = migrated
            metadata = dict(item.get("metadata", {}) or {})
            metadata.setdefault("legacy_label", legacy_label or str(item.get("label", "")).strip())
        else:
            metadata = {str(k): v for k, v in dict(item.get("metadata", {}) or {}).items()}
            display_name = str(item.get("display_name", "")).strip() or None
            role = str(item.get("role", "")).strip() or None

        uuid = str(item.get("uuid", "")).strip() or new_device_profile(label=raw_label, type=str(item.get("type", "unknown") or "unknown"), driver=str(item.get("driver", "auto") or "auto")).uuid
        profile = DeviceProfile(
            uuid=uuid,
            label=raw_label,
            type=str(item.get("type", "unknown") or "unknown"),
            driver=str(item.get("driver", "auto") or "auto"),
            endpoint=(str(item.get("endpoint", "")).strip() or None),
            role=role,
            display_name=display_name,
            identity={str(k): str(v) for k, v in dict(item.get("identity", {}) or {}).items()},
            enabled=bool(item.get("enabled", True)),
            metadata=metadata,
            fingerprint=str(item.get("fingerprint", "") or "").strip(),
        )
        return self._normalize_profile(profile)

    def _record_event(
        self,
        *,
        label: str | None,
        endpoint: str | None,
        owner: str,
        action: str,
        command: str | None,
        result: str,
        duration_ms: float,
        message: str,
    ) -> None:
        self._events.append(
            DeviceEvent(
                timestamp_s=perf_counter(),
                label=label,
                endpoint=endpoint,
                owner=owner,
                action=action,
                command=command,
                result=result,
                duration_ms=max(float(duration_ms), 0.0),
                message=message,
            )
        )

    def _normalize_profile(self, profile: DeviceProfile) -> DeviceProfile:
        label = str(profile.label or "").strip()
        if not label:
            raise ControllerError("Device label is required.")
        display_name = profile.display_name or None
        metadata = dict(profile.metadata or {})
        if label in _LEGACY_LABEL_MIGRATIONS:
            migrated_label, migrated_display_name, migrated_role, legacy_label = _LEGACY_LABEL_MIGRATIONS[label]
            metadata.setdefault("legacy_label", legacy_label or label)
            label = migrated_label
            display_name = display_name or migrated_display_name
            role = profile.role or migrated_role
        else:
            role = profile.role
        return DeviceProfile(
            uuid=str(profile.uuid or "").strip() or new_device_profile(label=label, type=profile.type, driver=profile.driver).uuid,
            label=label,
            type=str(profile.type or "unknown"),
            driver=str(profile.driver or "auto"),
            endpoint=(str(profile.endpoint or "").strip() or None),
            role=role,
            display_name=(str(display_name).strip() or None) if display_name is not None else None,
            identity={str(k): str(v) for k, v in dict(profile.identity or {}).items()},
            enabled=bool(profile.enabled),
            metadata=metadata,
            fingerprint=str(profile.fingerprint or "").strip(),
        )

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
        normalized = self._canonical_label(label)
        profile = self._profiles.get(normalized)
        if profile is None:
            raise ControllerError(f"Unknown device label {label!r}.")
        return profile

    def _canonical_label(self, label: str) -> str:
        normalized = str(label or "").strip()
        migrated = _LEGACY_LABEL_MIGRATIONS.get(normalized)
        if migrated is not None:
            return migrated[0]
        return normalized

    def _make_status(self, label: str, profile: DeviceProfile, connected: bool, last_error: str | None, identity: dict[str, str]) -> DeviceStatus:
        return DeviceStatus(
            uuid=profile.uuid,
            label=label,
            type=profile.type,
            driver=profile.driver,
            endpoint=profile.endpoint,
            connected=connected,
            state="connected" if connected else "disconnected",
            last_error=last_error,
            identity=identity,
            display_name=profile.display_name,
            role=profile.role,
        )

    def _probe_selector(self, endpoint: str):
        controller = AMFSwitchController()
        controller.connect(endpoint)
        return controller, controller.get_probe()

    def refresh_device_ports(self, generation: int) -> object:
        pump_ports = RegloICCClient.list_ports()
        valve_ports = SerialController.list_ports()
        selector_devices = detect_amf_selector_devices() if amf_tools_available() else []
        return PortRefreshData(
            generation=generation,
            pump_ports=list(pump_ports),
            valve_ports=list(valve_ports),
            selector_devices=list(selector_devices),
            amf_tools_available=amf_tools_available(),
        )

    def probe_pump_port(self, port: str):
        return RegloICCClient.probe_port(port)

    def connect_valve_port(self, port: str):
        client, probe = detect_valve_controller(port)
        return client, probe

    def connect_selector_port(self, port: str):
        client = AMFSwitchController()
        client.connect(port)
        return client, client.get_probe()

    # Backward-compatible alias
    connect_mswitch_port = connect_selector_port
