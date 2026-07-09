"""Hardware discovery and connection steps for the startup init pipeline.

Called by ``start_hardware_initialization_for`` (in ``main_window_lifecycle``)
via ``window._hardware_init_steps()``.  Each scan function runs on a worker
thread inside a :class:`~lspr_app.gui.hardware_initializer.HardwareInitTask`
and must return a :class:`~lspr_app.gui.hardware_initializer.HardwareInitStepResult`.

Public API
----------
``hardware_init_steps_for(window)``
    Build the ordered list of :class:`~lspr_app.gui.hardware_initializer.HardwareInitStep`
    objects for the current device-profile configuration.

Private helpers (module-internal)
----------------------------------
``_spectrometer_init_step``
    Reports whether the spectrometer backend is a real device or a simulation.
``_profile_device_init_step``
    Connects a device that already has a saved :class:`~lspr_app.device.communication_models.DeviceProfile`.
``_pump_scan_step``
    Scans serial ports for a Reglo ICC pump controller.
``_selector_scan_step``
    Scans USB endpoints for an AMF M-switch selector.
``_valve_scan_step``
    Scans serial ports for a valve controller.
"""

from __future__ import annotations

from time import perf_counter

from lspr_app.device.amf_mswitch import detect_amf_selector_devices
from lspr_app.device.communication_models import DeviceProfile
from lspr_app.device.device_manager import DeviceCommunicationService, extract_usb_fingerprint
from lspr_app.device.reglo_icc import PumpProbe, RegloICCClient, is_probable_reglo_port
from lspr_app.device.port_assignments import get_port_assignment, should_probe_port_for_role
from lspr_app.device.probe_diagnostics import record_port_probe_event
from lspr_app.device.serial_controllers import ControllerProbe, SerialController, controller_port_priority
from lspr_app.gui.hardware_initializer import HardwareInitStep, HardwareInitStepResult


def _valid_port_name(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.casefold() in {"none", "null", "n/a", "-"}:
        return None
    return text


# ── Internal helper ────────────────────────────────────────────────────────────

def _record_startup_port_probe_event(
    *,
    role: str,
    port: str,
    assignment: str,
    action: str,
    result: str,
    message: str,
    owner: str,
    command: str | None = None,
    duration_ms: float | None = None,
) -> None:
    """Shorthand: forward a port-probe event tagged with ``phase='startup'``."""
    record_port_probe_event(
        phase="startup",
        port=port,
        owner=owner,
        role=role,
        assignment=assignment,
        action=action,
        command=command,
        result=result,
        duration_ms=duration_ms,
        message=message,
    )


# ── Spectrometer ───────────────────────────────────────────────────────────────

def _spectrometer_init_step() -> HardwareInitStepResult:
    """Try to open the real spectrometer on this worker thread.

    Returns the live ``OceanSpectrometer`` instance as ``payload`` on success so
    the GUI thread can swap ``window._spectrometer`` without any additional init.
    """
    try:
        from lspr_app.device.ocean import OceanSpectrometer
        spec = OceanSpectrometer()
        return HardwareInitStepResult(
            key="spectrometer",
            label="Spectrometer",
            state="ready",
            message=f"Spectrometer backend active: {spec.device_name()}.",
            connected=True,
            payload=spec,
        )
    except Exception:
        return HardwareInitStepResult(
            key="spectrometer",
            label="Spectrometer",
            state="simulation",
            message="No spectrometer detected. Using simulation backend.",
            connected=False,
            payload=None,
        )


# ── Profile-based device connection ───────────────────────────────────────────

def _profile_device_init_step(
    service: DeviceCommunicationService,
    profile: DeviceProfile,
) -> HardwareInitStepResult:
    """Connect a device that already has a saved profile."""
    display = profile.display_name or profile.label
    try:
        status = service.connect(profile.label)
    except Exception as exc:
        return HardwareInitStepResult(
            key=profile.label,
            label=display,
            state="error",
            message=f"{display}: {exc}",
            connected=False,
            error=str(exc),
        )
    probe: object | None = None
    if profile.type == "pump":
        try:
            probe = PumpProbe(
                port=status.endpoint or "",
                protocol_version=status.identity.get("protocol_version", ""),
                serial_number=status.identity.get("serial_number", ""),
                channel_count=int(status.identity.get("channel_count", "0") or "0"),
                model=status.identity.get("model", ""),
            )
        except Exception:
            probe = None
    elif profile.type == "valve":
        try:
            probe = ControllerProbe(
                port=status.endpoint or "",
                controller_type=status.driver,
                model=status.identity.get("model", ""),
                serial_number=status.identity.get("serial_number") or None,
                protocol_version=status.identity.get("protocol_version") or None,
            )
        except Exception:
            probe = None
    return HardwareInitStepResult(
        key=profile.label,
        label=display,
        state="connected",
        message=f"{display} connected on {status.endpoint}.",
        connected=True,
        probe=probe,
        payload=status,
    )


# ── Pump scan ──────────────────────────────────────────────────────────────────

def _pump_scan_step(service: DeviceCommunicationService) -> HardwareInitStepResult:
    """Scan serial ports for a Reglo ICC pump controller."""
    all_ports = RegloICCClient.list_ports()
    preferred_ports = [
        port
        for port in all_ports
        if _valid_port_name(port.device) and get_port_assignment(port.device) == "pump" and should_probe_port_for_role(port.device, "pump")
    ]
    probable_ports = [
        port
        for port in all_ports
        if _valid_port_name(port.device)
        and get_port_assignment(port.device) == "auto"
        and is_probable_reglo_port(port)
        and should_probe_port_for_role(port.device, "pump")
    ]
    ports = preferred_ports + [port for port in probable_ports if port not in preferred_ports]
    if not ports:
        return HardwareInitStepResult(
            key="pump_scan",
            label="Pump scan",
            state="missing",
            message="No pump controller discovered.",
            connected=False,
            payload=[],
        )
    found_probes: list[PumpProbe] = []
    found_profiles: list[DeviceProfile] = []
    last_error: str | None = None
    assigned_error: str | None = None
    for port in ports:
        port_name = _valid_port_name(port.device)
        if port_name is None:
            continue
        assignment = get_port_assignment(port_name)
        if not should_probe_port_for_role(port_name, "pump"):
            _record_startup_port_probe_event(
                role="pump",
                port=port_name,
                assignment=assignment,
                action="skip",
                result="skipped",
                message=f"Port {port_name} is assigned to {assignment} and is excluded from pump probing.",
                owner="startup:pump",
            )
            continue
        started = perf_counter()
        try:
            probe = RegloICCClient.probe_port(port_name)
        except Exception as exc:
            last_error = str(exc)
            if assignment == "pump" and assigned_error is None:
                assigned_error = f"Assigned pump port {port_name} did not respond as Reglo ICC: {exc}"
            _record_startup_port_probe_event(
                role="pump",
                port=port_name,
                assignment=assignment,
                action="probe",
                result="fail",
                message=str(exc),
                owner="startup:pump",
                command="0x!",
                duration_ms=(perf_counter() - started) * 1000.0,
            )
            continue
        _record_startup_port_probe_event(
            role="pump",
            port=port_name,
            assignment=assignment,
            action="probe",
            result="success",
            message=probe.model or "Pump controller",
            owner="startup:pump",
            command="0x!",
            duration_ms=(perf_counter() - started) * 1000.0,
        )
        sn = str(probe.serial_number or "").strip()
        fingerprint = f"reglo-icc:{sn}" if sn and sn.upper() not in {"#", "*", ""} else extract_usb_fingerprint(port.hwid)
        profile = service.find_or_create_profile(
            device_type="pump",
            fingerprint=fingerprint,
            endpoint=port_name,
            identity={
                "model": probe.model,
                "serial_number": probe.serial_number,
                "protocol_version": probe.protocol_version,
                "channel_count": str(probe.channel_count),
            },
            driver="reglo_icc",
        )
        try:
            service.connect(profile.label, cached_pump_probe=probe)
        except Exception:
            pass
        found_probes.append(probe)
        found_profiles.append(profile)
    if not found_probes:
        if assigned_error is not None:
            last_error = assigned_error if last_error is None else f"{assigned_error}; {last_error}"
        return HardwareInitStepResult(
            key="pump_scan",
            label="Pump scan",
            state="error",
            message=last_error or "Pump scan completed with no usable device.",
            connected=False,
            error=last_error,
            payload=[],
        )
    primary_probe = found_probes[0]
    primary_profile = found_profiles[0]
    names = ", ".join(p.label for p in found_profiles)
    count = len(found_probes)
    return HardwareInitStepResult(
        key=primary_profile.label,
        label="Pump scan",
        state="connected",
        message=f"{count} pump controller{'s' if count > 1 else ''} found: {names}.",
        connected=True,
        probe=primary_probe,
        payload=found_probes,
    )


# ── Selector scan ──────────────────────────────────────────────────────────────

def _selector_scan_step(service: DeviceCommunicationService) -> HardwareInitStepResult:
    """Scan USB endpoints for an AMF M-switch selector."""
    try:
        devices = detect_amf_selector_devices()
    except Exception as exc:
        return HardwareInitStepResult(
            key="selector_scan",
            label="Selector scan",
            state="error",
            message=f"Selector scan failed: {exc}",
            connected=False,
            error=str(exc),
            payload=[],
        )
    if not devices:
        return HardwareInitStepResult(
            key="selector_scan",
            label="Selector scan",
            state="missing",
            message="Selector not discovered at startup.",
            connected=False,
            payload=[],
        )
    found_profiles: list[DeviceProfile] = []
    for device in devices:
        port_name = _valid_port_name(getattr(device, "port", ""))
        if port_name is None:
            continue
        sn = getattr(device, "serial_number", None) or ""
        fingerprint = f"amf-selector:{sn}" if sn else extract_usb_fingerprint(getattr(device, "hwid", ""))
        identity = {
            "model": getattr(device, "model", ""),
            "serial_number": sn,
            "protocol_version": getattr(device, "protocol_version", "") or "",
            "controller_type": getattr(device, "controller_type", "amf-mswitch"),
        }
        profile = service.find_or_create_profile(
            device_type="selector",
            fingerprint=fingerprint,
            endpoint=port_name,
            identity=identity,
            driver="amf-mswitch",
        )
        found_profiles.append(profile)
    first = devices[0]
    port_name = _valid_port_name(getattr(first, "port", "")) or "unknown"
    primary_profile = found_profiles[0] if found_profiles else None
    return HardwareInitStepResult(
        key=primary_profile.label if primary_profile else "selector_scan",
        label="Selector scan",
        state="discovered",
        message=f"Selector discovered on {port_name}.",
        connected=False,
        probe=first,
        payload=devices,
    )


# ── Valve scan ─────────────────────────────────────────────────────────────────

def _valve_scan_step(service: DeviceCommunicationService) -> HardwareInitStepResult:
    """Scan serial ports for a valve controller."""
    all_ports = SerialController.list_ports()
    preferred_ports = [
        port
        for port in all_ports
        if _valid_port_name(port.device) and get_port_assignment(port.device) == "valve" and should_probe_port_for_role(port.device, "valve")
    ]
    likely_ports = [
        port
        for port in all_ports
        if _valid_port_name(port.device)
        and get_port_assignment(port.device) == "auto"
        and controller_port_priority(port) > 0
        and should_probe_port_for_role(port.device, "valve")
    ]
    ports = preferred_ports + [port for port in likely_ports if port not in preferred_ports]
    if not ports:
        return HardwareInitStepResult(
            key="valve_scan",
            label="Valve scan",
            state="missing",
            message="No valve controller discovered.",
            connected=False,
            payload=None,
        )
    ports = sorted(ports, key=controller_port_priority, reverse=True)
    last_error: str | None = None
    assigned_error: str | None = None
    for port in ports:
        port_name = _valid_port_name(port.device)
        if port_name is None:
            continue
        assignment = get_port_assignment(port_name)
        if not should_probe_port_for_role(port_name, "valve"):
            _record_startup_port_probe_event(
                role="valve",
                port=port_name,
                assignment=assignment,
                action="skip",
                result="skipped",
                message=f"Port {port_name} is assigned to {assignment} and is excluded from valve probing.",
                owner="startup:valve",
            )
            continue
        started = perf_counter()
        try:
            probe_result = service.probe_endpoint(port_name, "valve")
        except Exception as exc:
            last_error = str(exc)
            if assignment == "valve" and assigned_error is None:
                assigned_error = f"Assigned valve port {port_name} did not respond as a valve controller: {exc}"
            _record_startup_port_probe_event(
                role="valve",
                port=port_name,
                assignment=assignment,
                action="probe",
                result="fail",
                message=str(exc),
                owner="startup:valve",
                duration_ms=(perf_counter() - started) * 1000.0,
            )
            continue
        if not probe_result.success:
            last_error = probe_result.error or "Valve probe failed."
            if assignment == "valve" and assigned_error is None:
                assigned_error = f"Assigned valve port {port_name} did not respond as a valve controller: {probe_result.error or 'unknown error'}"
            _record_startup_port_probe_event(
                role="valve",
                port=port_name,
                assignment=assignment,
                action="probe",
                result="fail",
                message=probe_result.error or "Valve probe failed.",
                owner="startup:valve",
                duration_ms=(perf_counter() - started) * 1000.0,
            )
            continue
        probe = ControllerProbe(
            port=port_name,
            controller_type=probe_result.detected_type or "valve",
            model=probe_result.identity.get("model", ""),
            serial_number=probe_result.identity.get("serial_number") or None,
            protocol_version=probe_result.identity.get("protocol_version") or None,
        )
        _record_startup_port_probe_event(
            role="valve",
            port=port_name,
            assignment=assignment,
            action="probe",
            result="success",
            message=probe.model or "Valve controller",
            owner="startup:valve",
            duration_ms=(perf_counter() - started) * 1000.0,
        )
        fingerprint = extract_usb_fingerprint(port.hwid)
        profile = service.find_or_create_profile(
            device_type="valve",
            fingerprint=fingerprint,
            endpoint=port_name,
            identity={
                "model": probe.model,
                "serial_number": probe.serial_number or "",
                "protocol_version": probe.protocol_version or "",
                "controller_type": probe.controller_type,
            },
            driver=probe.controller_type,
        )
        try:
            service.connect(profile.label)
        except Exception:
            pass
        return HardwareInitStepResult(
            key=profile.label,
            label="Valve scan",
            state="connected",
            message=f"Valve controller connected on {port_name}.",
            connected=True,
            probe=probe,
            payload=probe,
        )
    return HardwareInitStepResult(
        key="valve_scan",
        label="Valve scan",
        state="error",
        message=assigned_error or last_error or "Valve scan completed with no usable device.",
        connected=False,
        error=assigned_error or last_error,
        payload=None,
    )


# ── Public entry point ─────────────────────────────────────────────────────────

def hardware_init_steps_for(window) -> list[HardwareInitStep]:
    """Build the ordered list of hardware init steps for the current profile config.

    Called by ``window._hardware_init_steps()`` which is invoked by
    ``start_hardware_initialization_for`` in ``main_window_lifecycle``.
    """
    service: DeviceCommunicationService = window._device_comm_service
    available_pump_ports = {
        port.device
        for port in RegloICCClient.list_ports()
        if _valid_port_name(getattr(port, "device", None))
    }
    available_serial_ports = {
        port.device
        for port in SerialController.list_ports()
        if _valid_port_name(getattr(port, "device", None))
    }
    try:
        available_selector_ports = {
            _valid_port_name(getattr(device, "port", None))
            for device in detect_amf_selector_devices()
        }
        available_selector_ports.discard(None)
    except Exception:
        available_selector_ports = set()
    steps: list[HardwareInitStep] = [
        HardwareInitStep("spectrometer", "Spectrometer", _spectrometer_init_step),
    ]
    configured_types: set[str] = set()
    for profile in sorted(service.list_profiles(), key=lambda p: p.label):
        endpoint = _valid_port_name(profile.endpoint)
        if not profile.enabled or not endpoint:
            continue
        profile_type = str(profile.type or "").strip().casefold()
        endpoint_available = True
        if profile_type == "pump":
            endpoint_available = endpoint in available_pump_ports
        elif profile_type == "valve":
            endpoint_available = endpoint in available_serial_ports
        elif profile_type == "selector":
            endpoint_available = endpoint in available_selector_ports if available_selector_ports else False
        if not endpoint_available:
            continue
        configured_types.add(profile.type)
        display = profile.display_name or profile.label
        steps.append(HardwareInitStep(
            profile.label,
            display,
            lambda p=profile: _profile_device_init_step(service, p),
        ))
    if "pump" not in configured_types:
        steps.append(HardwareInitStep("pump_scan", "Pump scan", lambda: _pump_scan_step(service)))
    if "valve" not in configured_types:
        steps.append(HardwareInitStep("valve_scan", "Valve scan", lambda: _valve_scan_step(service)))
    if "selector" not in configured_types:
        steps.append(HardwareInitStep("selector_scan", "Selector scan", lambda: _selector_scan_step(service)))
    return steps
