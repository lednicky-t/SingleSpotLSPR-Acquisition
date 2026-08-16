"""Re-export shim over `lspr_acq_shell.port_assignments` (Phase 1 shell
extraction, 2026-08-08) - kept here so every existing
`from lspr_app.device.port_assignments import ...` call site in this app
keeps working unchanged.
"""
from __future__ import annotations

from lspr_acq_shell.port_assignments import (
    DeviceAssignment,
    assignment_for_port_label,
    clear_port_assignment,
    device_assignment_label,
    get_port_assignment,
    normalize_device_assignment,
    set_port_assignment,
    should_probe_port_for_role,
    snapshot_port_assignments,
)

__all__ = [
    "DeviceAssignment",
    "assignment_for_port_label",
    "clear_port_assignment",
    "device_assignment_label",
    "get_port_assignment",
    "normalize_device_assignment",
    "set_port_assignment",
    "should_probe_port_for_role",
    "snapshot_port_assignments",
]
