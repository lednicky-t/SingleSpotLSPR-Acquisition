"""Re-export shim over `lspr_acq_shell.serial_controllers` (Phase 1 shell
extraction, 2026-08-08) - kept here so every existing
`from lspr_app.device.serial_controllers import ...` call site in this app
keeps working unchanged.
"""
from __future__ import annotations

from lspr_acq_shell.serial_controllers import (
    ControllerCapabilities,
    ControllerError,
    ControllerPort,
    ControllerProbe,
    SerialController,
    auto_connect_best_port,
    capabilities_for_controller_type,
    controller_port_priority,
    detect_controller,
    register_controller,
    registered_controllers,
)

__all__ = [
    "ControllerCapabilities",
    "ControllerError",
    "ControllerPort",
    "ControllerProbe",
    "SerialController",
    "auto_connect_best_port",
    "capabilities_for_controller_type",
    "controller_port_priority",
    "detect_controller",
    "register_controller",
    "registered_controllers",
]
