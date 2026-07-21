"""Single owner of the device (spectrometer/pump/valve/selector) lifecycle.

This module replaces two previously-independent systems that both used to
touch the same physical devices without a shared model: the startup
discovery scan (``gui/hardware_initializer.py`` + ``gui/main_window_hardware_scan.py``)
and ``ExperimentControlWindow``'s own port scan + startup auto-connect state
machine + manual connect/disconnect handlers. See
``docs/device-layer/DEVICE_LAYER_AUDIT_2026.md`` for the incident history
(R5/R6/R7/R8) that motivated this rewrite.

Pure Python, no Qt import - this module is fully unit-testable without a
``QApplication`` or real hardware. The Qt-facing wrapper that runs this on a
background thread lives in ``gui/device_lifecycle_task.py``.

Public API
----------
``DeviceLifecycleController``
    The single owner of device discovery/connect/disconnect. Use
    ``DeviceLifecycleController.shared()`` to get the process-wide instance.
``DeviceLifecycleEvent`` / ``DeviceLifecycleReport`` / ``PostConnectOutcome``
    Data types describing lifecycle progress and outcomes.
``register_post_connect_hook(device_key, hook)``
    Register a callable that runs immediately after a successful connect
    for a given device type (used for the selector's homing step).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from time import perf_counter
from typing import Callable, Sequence

from lspr_app.device.communication_models import DeviceCommand, PortRefreshData
from lspr_app.device.device_manager import DeviceCommunicationService, extract_usb_fingerprint
from lspr_app.device.device_types import PUMP, SELECTOR, SWITCH
from lspr_app.device.port_assignments import get_port_assignment, should_probe_port_for_role
from lspr_app.device.probe_diagnostics import record_port_probe_event
from lspr_app.device.reglo_icc import PumpProbe, RegloICCClient, is_probable_reglo_port
from lspr_app.device.serial_controllers import ControllerProbe, controller_port_priority
from lspr_app.domain.pump_plan import ACTIVE_PUMP_CHANNELS

_LOGGER = logging.getLogger("lspr_app.hardware_init")

# ── Device order and stage constants ────────────────────────────────────────────

DEVICE_ORDER: tuple[str, ...] = (PUMP, SWITCH, SELECTOR)

STAGE_DISCOVERING = "discovering"
STAGE_CONNECTING = "connecting"
STAGE_POST_CONNECT = "post_connect"
STAGE_READY = "ready"
STAGE_MISSING = "missing"
STAGE_FAILED = "failed"
STAGE_DISCONNECTED = "disconnected"
STAGE_SIMULATION = "simulation"

TERMINAL_STAGES = {STAGE_READY, STAGE_MISSING, STAGE_FAILED, STAGE_DISCONNECTED, STAGE_SIMULATION}

_DEVICE_DRIVER = {PUMP: "reglo_icc", SWITCH: "auto", SELECTOR: "amf-mswitch"}
_DEVICE_ROLE = {PUMP: "sample_pump", SWITCH: "inlet_switch", SELECTOR: "main_selector"}
_DEVICE_LABEL = {PUMP: "pump_1", SWITCH: "switch_1", SELECTOR: "selector_1"}


# ── Data types ───────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class DeviceLifecycleEvent:
    """One stage transition for one device, emitted during discovery/connect/setup."""

    device_key: str
    stage: str
    message: str
    connected: bool = False
    probe: object | None = None
    error: str | None = None

    @property
    def terminal(self) -> bool:
        return self.stage in TERMINAL_STAGES


@dataclass(slots=True)
class DeviceLifecycleReport:
    """The full result of one ``run_full_cycle()`` call."""

    events: list[DeviceLifecycleEvent] = field(default_factory=list)
    by_device: dict[str, DeviceLifecycleEvent] = field(default_factory=dict)
    spectrometer: object | None = None


@dataclass(slots=True)
class PostConnectOutcome:
    success: bool
    message: str


EmitFn = Callable[[DeviceLifecycleEvent], None]


def _noop_emit(_event: DeviceLifecycleEvent) -> None:
    return None


# ── Moved helpers (logic relocated verbatim from experiment_control_window.py) ──

def device_label_for(device_key: str) -> str:
    """Map a device-type key to its canonical DeviceCommunicationService label."""
    return _DEVICE_LABEL.get(device_key, f"{device_key}_main")


def ensure_device_profile(service: DeviceCommunicationService, device_key: str, port: str, *, driver: str) -> str:
    """Register/update *device_key*'s profile at an explicit *port*.

    Used by the manual-connect path (the user already picked a port; no
    discovery/probing precedes this call). ``mark_manual=False`` is
    deliberate: this fires on every connect attempt, automatic or manual,
    success or failure - pinning the port as a permanent manual assignment
    here would let a stale or wrong guess outrank future live scans. See R8
    in docs/device-layer/DEVICE_LAYER_AUDIT_2026.md.
    """
    label = device_label_for(device_key)
    service.register_endpoint_assignment(
        label,
        port,
        device_type=device_key,
        driver=driver,
        role=_DEVICE_ROLE.get(device_key),
        mark_manual=False,
    )
    return label


# ── Port ranking (unifies logic previously duplicated in experiment_control_window's
#    _populate_*_ports and main_window_hardware_scan's *_scan_step functions) ────

def ranked_pump_ports(ports: Sequence[object]) -> list[object]:
    """Candidate pump ports in priority order: manually-assigned first, then
    probable-looking ports (each group preserving scan order). Empty if none.
    A port assigned to a *different* role is never suggested here."""
    preferred = [
        p for p in ports
        if get_port_assignment(p.device) == "pump" and should_probe_port_for_role(p.device, "pump")
    ]
    probable = [
        p for p in ports
        if get_port_assignment(p.device) == "auto"
        and is_probable_reglo_port(p)
        and should_probe_port_for_role(p.device, "pump")
    ]
    return preferred + [p for p in probable if p not in preferred]


def best_pump_port(ports: Sequence[object]) -> object | None:
    candidates = ranked_pump_ports(ports)
    return candidates[0] if candidates else None


def ranked_valve_ports(ports: Sequence[object]) -> list[object]:
    """Candidate valve/switch ports in priority order: manually-assigned
    first, then live-scan-ranked by ``controller_port_priority`` (highest
    first). Empty if none."""
    preferred = [
        p for p in ports
        if get_port_assignment(p.device) == "switch" and should_probe_port_for_role(p.device, "valve")
    ]
    likely = sorted(
        (
            p for p in ports
            if get_port_assignment(p.device) == "auto"
            and controller_port_priority(p) > 0
            and should_probe_port_for_role(p.device, "valve")
        ),
        key=controller_port_priority,
        reverse=True,
    )
    return preferred + [p for p in likely if p not in preferred]


def best_valve_port(ports: Sequence[object]) -> object | None:
    candidates = ranked_valve_ports(ports)
    return candidates[0] if candidates else None


def best_selector_port(devices: Sequence[object]) -> object | None:
    """Selector devices are already fully-identified by the AMF vendor SDK
    scan (unlike raw serial ports for pump/valve) - the first discovered
    device is the best (and usually only) candidate."""
    return devices[0] if devices else None


# ── Post-connect hook registry (generalizes the selector's homing step) ─────────

PostConnectHook = Callable[[DeviceCommunicationService, str, str], PostConnectOutcome]

_POST_CONNECT_HOOKS: dict[str, PostConnectHook] = {}


def register_post_connect_hook(device_key: str, hook: PostConnectHook) -> None:
    _POST_CONNECT_HOOKS[device_key] = hook


def _home_selector_hook(service: DeviceCommunicationService, label: str, device_key: str) -> PostConnectOutcome:
    """Home the M-Switch if it isn't already homed. Moved from
    ExperimentControlWindow._ensure_mswitch_homed, with one deliberate fix:
    the original never checked whether the home command itself succeeded
    before reporting "homed" - this version does."""
    connection = service.connection(label)
    try:
        if connection is not None and hasattr(connection, "is_homed") and connection.is_homed():
            return PostConnectOutcome(True, "Already homed.")
    except Exception as exc:
        return PostConnectOutcome(False, f"Could not read M-Switch homing state: {exc}")
    result = service.send_command(label, DeviceCommand("switch.home", {"block": True}))
    if not result.success:
        return PostConnectOutcome(False, f"M-Switch home failed: {result.error}")
    return PostConnectOutcome(True, "M-Switch homed.")


register_post_connect_hook(SELECTOR, _home_selector_hook)


# ── Probe construction from a DeviceStatus (mirrors the old
#    _profile_device_init_step's per-type probe reconstruction) ─────────────────

def _probe_from_status(device_key: str, status: object) -> object | None:
    identity = getattr(status, "identity", None) or {}
    endpoint = getattr(status, "endpoint", None) or ""
    if device_key == PUMP:
        try:
            return PumpProbe(
                port=endpoint,
                protocol_version=identity.get("protocol_version", ""),
                serial_number=identity.get("serial_number", ""),
                channel_count=int(identity.get("channel_count", "0") or "0"),
                model=identity.get("model", ""),
            )
        except Exception:
            return None
    try:
        return ControllerProbe(
            port=endpoint,
            controller_type=identity.get("controller_type", getattr(status, "driver", "") or ""),
            model=identity.get("model", ""),
            serial_number=identity.get("serial_number") or None,
            protocol_version=identity.get("protocol_version") or None,
        )
    except Exception:
        return None


# ── The single owner ─────────────────────────────────────────────────────────────

_SHARED_CONTROLLER: "DeviceLifecycleController | None" = None


class DeviceLifecycleController:
    """Owns discovery, connect, disconnect, and post-connect setup for every
    device. This is the *only* code path that should ever call
    ``DeviceCommunicationService.connect``/``disconnect`` for pump/valve/
    selector - manual buttons and the startup sequence both go through here,
    so there is exactly one real connect implementation and devices are
    never touched by more than one concurrent operation."""

    def __init__(self, service: DeviceCommunicationService | None = None) -> None:
        self._service = service or DeviceCommunicationService.shared()
        self._busy: set[str] = set()
        self._last_event: dict[str, DeviceLifecycleEvent] = {}

    @classmethod
    def shared(cls) -> "DeviceLifecycleController":
        global _SHARED_CONTROLLER
        if _SHARED_CONTROLLER is None:
            _SHARED_CONTROLLER = cls()
        return _SHARED_CONTROLLER

    # -- Status queries -----------------------------------------------------

    def is_busy(self, device_key: str) -> bool:
        return device_key in self._busy

    def last_event(self, device_key: str) -> DeviceLifecycleEvent | None:
        return self._last_event.get(device_key)

    def is_connected(self, device_key: str) -> bool:
        return bool(self._service.is_connected(device_label_for(device_key)))

    def probe_for(self, device_key: str) -> object | None:
        event = self._last_event.get(device_key)
        return event.probe if event is not None else None

    # -- Spectrometer ---------------------------------------------------------

    def run_spectrometer_stage(self, emit: EmitFn = _noop_emit) -> tuple[DeviceLifecycleEvent, object | None]:
        try:
            from lspr_app.device.ocean import OceanSpectrometer
            spectrometer = OceanSpectrometer()
            event = DeviceLifecycleEvent(
                device_key="spectrometer",
                stage=STAGE_READY,
                message=f"Spectrometer backend active: {spectrometer.device_name()}.",
                connected=True,
                # Reusing .probe to carry the live instance (not an identity
                # dataclass, unlike other devices) so the GUI-thread listener
                # can swap it in as soon as this one event arrives, rather
                # than waiting for the whole cycle to finish.
                probe=spectrometer,
            )
            emit(event)
            self._last_event["spectrometer"] = event
            return event, spectrometer
        except Exception:
            event = DeviceLifecycleEvent(
                device_key="spectrometer",
                stage=STAGE_SIMULATION,
                message="No spectrometer detected. Using simulation backend.",
                connected=False,
            )
            emit(event)
            self._last_event["spectrometer"] = event
            return event, None

    # -- Port enumeration -----------------------------------------------------

    def refresh_ports(self) -> PortRefreshData:
        return self._service.refresh_device_ports(0)

    # -- Full startup cycle -----------------------------------------------------

    def run_full_cycle(self, emit: EmitFn = _noop_emit) -> DeviceLifecycleReport:
        _LOGGER.info("Device lifecycle cycle started: spectrometer, %s.", ", ".join(DEVICE_ORDER))
        events: list[DeviceLifecycleEvent] = []
        by_device: dict[str, DeviceLifecycleEvent] = {}

        def _emit(event: DeviceLifecycleEvent) -> None:
            events.append(event)
            by_device[event.device_key] = event
            self._last_event[event.device_key] = event
            if event.terminal:
                _LOGGER.info("Device lifecycle | %s -> %s | %s", event.device_key, event.stage, event.message)
            emit(event)

        _, spectrometer_instance = self.run_spectrometer_stage(_emit)
        ports = self.refresh_ports()
        for device_key in DEVICE_ORDER:
            self._discover_and_connect(device_key, ports, _emit)
        _LOGGER.info("Device lifecycle cycle finished.")

        return DeviceLifecycleReport(events=events, by_device=by_device, spectrometer=spectrometer_instance)

    def _discover_and_connect(self, device_key: str, ports: PortRefreshData, emit: EmitFn) -> DeviceLifecycleEvent:
        if device_key == PUMP:
            return self._discover_and_connect_pump(ports.pump_ports, emit)
        if device_key == SWITCH:
            return self._discover_and_connect_valve(ports.valve_ports, emit)
        return self._discover_and_connect_selector(ports.selector_devices, emit)

    def _discover_and_connect_pump(self, ports: Sequence[object], emit: EmitFn) -> DeviceLifecycleEvent:
        candidates = ranked_pump_ports(ports)
        if not candidates:
            event = DeviceLifecycleEvent(PUMP, STAGE_MISSING, "No pump controller discovered.")
            emit(event)
            return event
        emit(DeviceLifecycleEvent(PUMP, STAGE_DISCOVERING, f"Probing {len(candidates)} candidate port(s) for a pump..."))
        last_error: str | None = None
        for port in candidates:
            assignment = get_port_assignment(port.device)
            started = perf_counter()
            try:
                probe = RegloICCClient.probe_port(port.device)
            except Exception as exc:
                last_error = str(exc)
                record_port_probe_event(
                    phase="startup", role="pump", port=port.device, assignment=assignment,
                    action="probe", result="fail", message=str(exc), owner="startup:pump",
                    command="0x!", duration_ms=(perf_counter() - started) * 1000.0,
                )
                continue
            record_port_probe_event(
                phase="startup", role="pump", port=port.device, assignment=assignment,
                action="probe", result="success", message=probe.model or "Pump controller",
                owner="startup:pump", command="0x!", duration_ms=(perf_counter() - started) * 1000.0,
            )
            sn = str(probe.serial_number or "").strip()
            fingerprint = f"reglo-icc:{sn}" if sn and sn.upper() not in {"#", "*", ""} else extract_usb_fingerprint(port.hwid)
            profile = self._service.find_or_create_profile(
                device_type="pump",
                fingerprint=fingerprint,
                endpoint=port.device,
                identity={
                    "model": probe.model,
                    "serial_number": probe.serial_number,
                    "protocol_version": probe.protocol_version,
                    "channel_count": str(probe.channel_count),
                },
                driver="reglo_icc",
            )
            return self._connect_and_setup(PUMP, profile.label, port.device, emit, cached_pump_probe=probe)
        event = DeviceLifecycleEvent(PUMP, STAGE_FAILED, last_error or "Pump scan completed with no usable device.", error=last_error)
        emit(event)
        return event

    def _discover_and_connect_valve(self, ports: Sequence[object], emit: EmitFn) -> DeviceLifecycleEvent:
        candidates = ranked_valve_ports(ports)
        if not candidates:
            event = DeviceLifecycleEvent(SWITCH, STAGE_MISSING, "No valve controller discovered.")
            emit(event)
            return event
        emit(DeviceLifecycleEvent(SWITCH, STAGE_DISCOVERING, f"Probing {len(candidates)} candidate port(s) for a valve controller..."))
        last_error: str | None = None
        for port in candidates:
            assignment = get_port_assignment(port.device)
            started = perf_counter()
            try:
                probe_result = self._service.probe_endpoint(port.device, "valve")
            except Exception as exc:
                last_error = str(exc)
                record_port_probe_event(
                    phase="startup", role="valve", port=port.device, assignment=assignment,
                    action="probe", result="fail", message=str(exc), owner="startup:valve",
                    duration_ms=(perf_counter() - started) * 1000.0,
                )
                continue
            if not probe_result.success:
                last_error = probe_result.error or "Valve probe failed."
                record_port_probe_event(
                    phase="startup", role="valve", port=port.device, assignment=assignment,
                    action="probe", result="fail", message=probe_result.error or "Valve probe failed.",
                    owner="startup:valve", duration_ms=(perf_counter() - started) * 1000.0,
                )
                continue
            record_port_probe_event(
                phase="startup", role="valve", port=port.device, assignment=assignment,
                action="probe", result="success", message=probe_result.identity.get("model", "") or "Valve controller",
                owner="startup:valve", duration_ms=(perf_counter() - started) * 1000.0,
            )
            fingerprint = extract_usb_fingerprint(port.hwid)
            profile = self._service.find_or_create_profile(
                device_type="valve",
                fingerprint=fingerprint,
                endpoint=port.device,
                identity={
                    "model": probe_result.identity.get("model", ""),
                    "serial_number": probe_result.identity.get("serial_number", ""),
                    "protocol_version": probe_result.identity.get("protocol_version", ""),
                    "controller_type": probe_result.detected_type or "valve",
                },
                driver=probe_result.detected_type or "valve",
            )
            return self._connect_and_setup(SWITCH, profile.label, port.device, emit)
        event = DeviceLifecycleEvent(SWITCH, STAGE_FAILED, last_error or "Valve scan completed with no usable device.", error=last_error)
        emit(event)
        return event

    def _discover_and_connect_selector(self, devices: Sequence[object], emit: EmitFn) -> DeviceLifecycleEvent:
        device = best_selector_port(devices)
        if device is None:
            event = DeviceLifecycleEvent(SELECTOR, STAGE_MISSING, "Selector not discovered at startup.")
            emit(event)
            return event
        port_name = str(getattr(device, "port", "") or "")
        sn = getattr(device, "serial_number", None) or ""
        fingerprint = f"amf-selector:{sn}" if sn else extract_usb_fingerprint(getattr(device, "hwid", ""))
        profile = self._service.find_or_create_profile(
            device_type="selector",
            fingerprint=fingerprint,
            endpoint=port_name,
            identity={
                "model": getattr(device, "model", ""),
                "serial_number": sn,
                "protocol_version": getattr(device, "protocol_version", "") or "",
                "controller_type": getattr(device, "controller_type", "amf-mswitch"),
            },
            driver="amf-mswitch",
        )
        return self._connect_and_setup(SELECTOR, profile.label, port_name, emit)

    # -- The one real connect implementation -----------------------------------

    def _connect_and_setup(
        self,
        device_key: str,
        label: str,
        port: str,
        emit: EmitFn,
        *,
        cached_pump_probe: PumpProbe | None = None,
    ) -> DeviceLifecycleEvent:
        emit(DeviceLifecycleEvent(device_key, STAGE_CONNECTING, f"Connecting on {port}..."))
        try:
            status = self._service.connect(label, cached_pump_probe=cached_pump_probe)
        except Exception as exc:
            event = DeviceLifecycleEvent(device_key, STAGE_FAILED, f"Connect failed on {port}: {exc}", error=str(exc))
            emit(event)
            return event

        probe = _probe_from_status(device_key, status)
        hook = _POST_CONNECT_HOOKS.get(device_key)
        if hook is not None:
            emit(DeviceLifecycleEvent(device_key, STAGE_POST_CONNECT, "Running post-connect setup...", connected=True, probe=probe))
            outcome = hook(self._service, label, device_key)
            if not outcome.success:
                event = DeviceLifecycleEvent(device_key, STAGE_FAILED, outcome.message, connected=True, probe=probe, error=outcome.message)
                emit(event)
                return event

        event = DeviceLifecycleEvent(device_key, STAGE_READY, f"Connected on {status.endpoint}.", connected=True, probe=probe)
        emit(event)
        return event

    # -- Manual / on-demand operations ------------------------------------------

    def request_connect(self, device_key: str, port: str, emit: EmitFn = _noop_emit) -> bool:
        """Connect to an explicit *port* (the manual "Connect" button path -
        no discovery/probing precedes this, the caller already chose the
        port). Single-flighted: returns False without doing anything if a
        connect/disconnect for this device is already in progress."""
        if self.is_busy(device_key):
            return False
        self._busy.add(device_key)
        try:
            label = ensure_device_profile(self._service, device_key, port, driver=_DEVICE_DRIVER.get(device_key, "auto"))
            self._connect_and_setup(device_key, label, port, emit)
            return True
        finally:
            self._busy.discard(device_key)

    def request_disconnect(self, device_key: str, emit: EmitFn = _noop_emit) -> None:
        if self.is_busy(device_key):
            return
        self._busy.add(device_key)
        try:
            label = device_label_for(device_key)
            self._service.disconnect(label)
            event = DeviceLifecycleEvent(device_key, STAGE_DISCONNECTED, "Disconnected.", connected=False)
            self._last_event[device_key] = event
            emit(event)
        finally:
            self._busy.discard(device_key)

    def rerun_post_connect(self, device_key: str, emit: EmitFn = _noop_emit) -> bool:
        """Re-run the post-connect hook on demand (the manual "Home" button
        for the selector). No-op (returns False) if not connected or if
        there's no hook registered for this device type."""
        if not self.is_connected(device_key):
            return False
        hook = _POST_CONNECT_HOOKS.get(device_key)
        if hook is None:
            return False
        label = device_label_for(device_key)
        probe = self.probe_for(device_key)
        emit(DeviceLifecycleEvent(device_key, STAGE_POST_CONNECT, "Running post-connect setup...", connected=True, probe=probe))
        outcome = hook(self._service, label, device_key)
        event = DeviceLifecycleEvent(
            device_key,
            STAGE_READY if outcome.success else STAGE_FAILED,
            outcome.message,
            connected=True,
            probe=probe,
            error=None if outcome.success else outcome.message,
        )
        self._last_event[device_key] = event
        emit(event)
        return outcome.success

    def shutdown_all(self, emit: EmitFn = _noop_emit) -> None:
        """Stop the pump, then disconnect pump/switch/selector. The selector
        is left in its current position (not re-homed or moved) before
        disconnect - moved verbatim from ExperimentControlWindow.shutdown_devices."""
        if self.is_connected(PUMP):
            try:
                self._service.send_command(
                    device_label_for(PUMP),
                    DeviceCommand("pump.stop_all", {"channel_count": ACTIVE_PUMP_CHANNELS}),
                )
            except Exception:
                pass
        for device_key in (SWITCH, SELECTOR, PUMP):
            if self.is_connected(device_key):
                self.request_disconnect(device_key, emit)
