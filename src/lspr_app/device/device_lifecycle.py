"""Re-export shim over `lspr_acq_shell.device_lifecycle` (Phase 1 shell
extraction, 2026-08-08) - kept here so every existing
`from lspr_app.device.device_lifecycle import ...` /
`from lspr_app.device import device_lifecycle` call site in this app keeps
working unchanged.

Also registers this app's spectrometer as the shared controller's
"primary detector" stage, at import time - generalized out of the shared
module (which used to hardcode `OceanSpectrometer` construction directly)
since a shared package can't depend on this app's spectrometer driver. See
`lspr_acq_shell.device_lifecycle`'s module docstring for the full design;
behavior for this app (spectrometer stage runs first in run_full_cycle(),
before port refresh, ungated by enabled_devices, its instance surfacing via
`DeviceLifecycleReport.spectrometer`) is unchanged from before this move.
"""
from __future__ import annotations

from lspr_acq_shell.device_lifecycle import (
    DEVICE_ORDER,
    STAGE_CONNECTING,
    STAGE_DISABLED,
    STAGE_DISCONNECTED,
    STAGE_DISCOVERING,
    STAGE_FAILED,
    STAGE_MISSING,
    STAGE_POST_CONNECT,
    STAGE_READY,
    STAGE_SIMULATION,
    TERMINAL_STAGES,
    DeviceFamily,
    DeviceLifecycleController,
    DeviceLifecycleEvent,
    DeviceLifecycleReport,
    DiscoverAndConnectFn,
    EmitFn,
    PostConnectHook,
    PostConnectOutcome,
    PrimaryDetectorStageFn,
    best_pump_port,
    best_selector_port,
    best_valve_port,
    device_family_order,
    device_label_for,
    ensure_device_profile,
    load_enabled_devices,
    ranked_pump_ports,
    ranked_valve_ports,
    register_device_family,
    register_post_connect_hook,
    register_primary_detector_stage,
    save_enabled_devices,
)

__all__ = [
    "DEVICE_ORDER",
    "STAGE_CONNECTING",
    "STAGE_DISABLED",
    "STAGE_DISCONNECTED",
    "STAGE_DISCOVERING",
    "STAGE_FAILED",
    "STAGE_MISSING",
    "STAGE_POST_CONNECT",
    "STAGE_READY",
    "STAGE_SIMULATION",
    "TERMINAL_STAGES",
    "DeviceFamily",
    "DeviceLifecycleController",
    "DeviceLifecycleEvent",
    "DeviceLifecycleReport",
    "DiscoverAndConnectFn",
    "EmitFn",
    "PostConnectHook",
    "PostConnectOutcome",
    "PrimaryDetectorStageFn",
    "best_pump_port",
    "best_selector_port",
    "best_valve_port",
    "device_family_order",
    "device_label_for",
    "ensure_device_profile",
    "load_enabled_devices",
    "ranked_pump_ports",
    "ranked_valve_ports",
    "register_device_family",
    "register_post_connect_hook",
    "register_primary_detector_stage",
    "save_enabled_devices",
]


def _run_spectrometer_stage(
    controller: DeviceLifecycleController, emit: EmitFn
) -> tuple[DeviceLifecycleEvent, object | None]:
    """sLSPR acq's primary-detector stage, registered below. Unchanged
    behavior from the original hardcoded `run_spectrometer_stage()` method
    that used to live directly on `DeviceLifecycleController`."""
    _ = controller
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
        return event, spectrometer
    except Exception:
        event = DeviceLifecycleEvent(
            device_key="spectrometer",
            stage=STAGE_SIMULATION,
            message="No spectrometer detected. Using simulation backend.",
            connected=False,
        )
        emit(event)
        return event, None


register_primary_detector_stage("spectrometer", _run_spectrometer_stage)
