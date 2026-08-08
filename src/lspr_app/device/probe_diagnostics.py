"""Re-export shim over `lspr_acq_shell.probe_diagnostics` (Phase 1 shell
extraction, 2026-08-08) - kept here so every existing
`from lspr_app.device.probe_diagnostics import ...` call site in this app
keeps working unchanged.
"""
from __future__ import annotations

from lspr_acq_shell.probe_diagnostics import (
    PortProbeEvent,
    record_port_probe_event,
    snapshot_port_probe_events,
)

__all__ = ["PortProbeEvent", "record_port_probe_event", "snapshot_port_probe_events"]
