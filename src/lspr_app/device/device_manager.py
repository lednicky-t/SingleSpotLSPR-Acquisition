"""Re-export shim over `lspr_acq_shell.device_manager` (Phase 1 shell
extraction, 2026-08-08) - kept here so every existing
`from lspr_app.device.device_manager import ...` call site in this app keeps
working unchanged.
"""
from __future__ import annotations

from lspr_acq_shell.device_manager import DeviceCommunicationService, extract_usb_fingerprint

__all__ = ["DeviceCommunicationService", "extract_usb_fingerprint"]
