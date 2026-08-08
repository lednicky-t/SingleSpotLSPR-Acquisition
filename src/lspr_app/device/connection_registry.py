"""Re-export shim over `lspr_acq_shell.connection_registry` (Phase 1 shell
extraction, 2026-08-08) - kept here so every existing
`from lspr_app.device.connection_registry import ...` call site in this app
keeps working unchanged.
"""
from __future__ import annotations

from lspr_acq_shell.connection_registry import (
    PortBusyError,
    claim_port,
    claim_port_context,
    port_owners,
    release_port,
    snapshot_port_ownership,
    try_claim_port,
)

__all__ = [
    "PortBusyError",
    "claim_port",
    "claim_port_context",
    "port_owners",
    "release_port",
    "snapshot_port_ownership",
    "try_claim_port",
]
