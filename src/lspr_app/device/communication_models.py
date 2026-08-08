"""Re-export shim over `lspr_acq_shell.communication_models` (Phase 1 shell
extraction, 2026-08-08) - kept here so every existing
`from lspr_app.device.communication_models import ...` call site in this app
keeps working unchanged.
"""
from __future__ import annotations

from lspr_acq_shell.communication_models import (
    DeviceCommand,
    DeviceCommandResult,
    DeviceEvent,
    DeviceLifecycleState,
    DeviceProfile,
    DeviceStatus,
    PortDescriptor,
    PortRefreshData,
    ProbeResult,
    device_inventory_rows,
    new_device_profile,
    next_device_label,
)

__all__ = [
    "DeviceCommand",
    "DeviceCommandResult",
    "DeviceEvent",
    "DeviceLifecycleState",
    "DeviceProfile",
    "DeviceStatus",
    "PortDescriptor",
    "PortRefreshData",
    "ProbeResult",
    "device_inventory_rows",
    "new_device_profile",
    "next_device_label",
]
