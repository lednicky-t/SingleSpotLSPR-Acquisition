"""Re-export shim over `lspr_acq_shell.amf_mswitch` (Phase 1 shell
extraction, 2026-08-08) - kept here so every existing
`from lspr_app.device.amf_mswitch import ...` call site in this app keeps
working unchanged.
"""
from __future__ import annotations

from lspr_acq_shell.amf_mswitch import (
    AMFSwitchController,
    amf_tools_available,
    detect_amf_mswitch_devices,
    detect_amf_selector_devices,
)

__all__ = [
    "AMFSwitchController",
    "amf_tools_available",
    "detect_amf_mswitch_devices",
    "detect_amf_selector_devices",
]
