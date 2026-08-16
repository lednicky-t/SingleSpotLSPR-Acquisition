"""Re-export shim over `lspr_acq_shell.experiment_control_backend` (Phase 1
shell extraction, 2026-08-08) for the `ExperimentControlBackend` Protocol,
`ExperimentControlDeviceState`, and `NullExperimentControlBackend` - kept
here so every existing `from lspr_app.gui.experiment_control_backend import
...` call site in this app keeps working unchanged.

`AcquisitionExperimentControlBackend` stays defined here, not in the shell -
it's the concrete sLSPR-specific implementation (wraps an
`ExperimentControlWindow`, reaches into several of its private methods), not
part of the reusable seam. LSPRi acq will write its own concrete class
against the shared Protocol later.
"""
from __future__ import annotations

from lspr_acq_shell.experiment_control_backend import (
    ExperimentControlBackend,
    ExperimentControlDeviceState,
    NullExperimentControlBackend,
)
from lspr_app.device.device_types import PUMP, SELECTOR, SWITCH
from lspr_app.gui.experiment_control_capabilities import ExperimentControlCapabilities

__all__ = [
    "ExperimentControlBackend",
    "ExperimentControlDeviceState",
    "NullExperimentControlBackend",
    "AcquisitionExperimentControlBackend",
]


class AcquisitionExperimentControlBackend:
    """Concrete backend that wraps an ExperimentControlWindow for acquisition mode.

    Delegates device queries and commands to the window's existing device-service
    helpers so the controller no longer needs to reach into window private methods.
    The window's lifecycle operations (shutdown_devices, port-scan UI) are not
    delegated here; they remain window-owned until the full V49 split lands.
    """

    def __init__(self, window) -> None:
        self._window = window

    def capabilities(self) -> ExperimentControlCapabilities:
        return self._window._capabilities  # type: ignore[attr-defined]

    def device_states(self) -> list[ExperimentControlDeviceState]:
        # PUMP/SWITCH/SELECTOR - the canonical device-family keys (post-V51
        # registry generalization), not the old "pump"/"valve"/"mswitch"
        # literals this used to iterate. device_label_for() (device_lifecycle.py)
        # looks families up by these exact keys with no legacy-alias
        # normalization of its own (unlike _normalize_device_type() in
        # device_manager.py, a different function for a different purpose) -
        # "valve"/"mswitch" would silently miss the registered family and
        # fall back to a fabricated f"{key}_main" label that never matches a
        # real connection. Flagged in the architecture plan (§4.3 item 5) as
        # a pre-existing inconsistency to resolve during this extraction
        # rather than carry forward; verified this method currently has no
        # live callers (device_states() is anticipatory V49 infrastructure,
        # not yet wired to any UI), so this was a latent bug, not a visible
        # one - still worth fixing now rather than moving it into the shared
        # package unexamined.
        states: list[ExperimentControlDeviceState] = []
        for key in (PUMP, SWITCH, SELECTOR):
            connected = self._window._service_device_connected(key)  # type: ignore[attr-defined]
            ctrl_type, port = self._window._service_connection_detail(key)  # type: ignore[attr-defined]
            if ctrl_type and port:
                detail = f"{ctrl_type} @ {port}"
            else:
                detail = port or ctrl_type or ""
            label = self._window._device_label_for(key)  # type: ignore[attr-defined]
            states.append(ExperimentControlDeviceState(key=key, connected=connected, label=label, detail=detail))
        return states

    def is_device_connected(self, device_key: str) -> bool:
        return self._window._service_device_connected(device_key)  # type: ignore[attr-defined]

    def refresh_devices(self) -> bool:
        return bool(self._window.refresh_device_ports())  # type: ignore[attr-defined]

    def send_command(self, device_key: str, command_type: str, payload: dict[str, object] | None = None) -> bool:
        return self._window._send_device_command(device_key, command_type, payload)  # type: ignore[attr-defined]

    def connect_device(self, device_key: str) -> bool:
        _ = device_key
        return False  # connection flow goes through Device Manager / DeviceLifecycleController

    def disconnect_device(self, device_key: str) -> bool:
        _ = device_key
        return False  # disconnection goes through the window's shutdown_devices lifecycle
