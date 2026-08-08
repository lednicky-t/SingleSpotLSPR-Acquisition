"""Re-export shim over `lspr_acq_shell.reglo_icc` (Phase 1 shell extraction,
2026-08-08) - kept here so every existing
`from lspr_app.device.reglo_icc import ...` call site in this app keeps
working unchanged.

`ACTIVE_PUMP_CHANNELS`/`VALID_ROLLER_COUNTS`/`DEFAULT_ROLLER_COUNT` moved to
`lspr_acq_shell.reglo_icc` too (previously defined in `domain/pump_plan.py`)
- that module now imports them back from there; re-exported here as well
for symmetry with the rest of this shim's surface, though nothing in this
app currently imports them from this particular path.
"""
from __future__ import annotations

from lspr_acq_shell.reglo_icc import (
    ACTIVE_PUMP_CHANNELS,
    DEFAULT_ROLLER_COUNT,
    PUMP_DISPLAY_MAX_LENGTH,
    VALID_ROLLER_COUNTS,
    PumpPort,
    PumpProbe,
    RegloICCClient,
    RegloICCError,
    is_probable_reglo_port,
    sanitize_pump_display_text,
)

__all__ = [
    "ACTIVE_PUMP_CHANNELS",
    "DEFAULT_ROLLER_COUNT",
    "PUMP_DISPLAY_MAX_LENGTH",
    "VALID_ROLLER_COUNTS",
    "PumpPort",
    "PumpProbe",
    "RegloICCClient",
    "RegloICCError",
    "is_probable_reglo_port",
    "sanitize_pump_display_text",
]
