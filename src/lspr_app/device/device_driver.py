"""Re-export shim over `lspr_acq_shell.device_driver` (Phase 1 shell
extraction, 2026-08-08) - kept here so every existing
`from lspr_app.device.device_driver import ...` call site in this app keeps
working unchanged.
"""
from __future__ import annotations

from lspr_acq_shell.device_driver import DeviceDriver, DeviceError, DeviceTimeoutError

__all__ = ["DeviceDriver", "DeviceError", "DeviceTimeoutError"]
