"""Re-export shim over `lspr_acq_shell.diagnostics` (Phase 1 shell
extraction, 2026-08-07) - the diagnostics verbosity profile
(off/normal/debug/deep) now lives there since it was already fully
app-agnostic. Kept here so every existing
`from lspr_app.diagnostics import DiagnosticsConfig` (etc.) call site in
this app keeps working unchanged.

Not extracted alongside it: `gui/runtime_diagnostics.py`'s
`SessionDiagnosticsSnapshot` and `gui/main_window_startup_diagnostics.py` -
both are deeply coupled to this app's specific main window (spectrum/trace/
sensorgram plot internals) with no modality-agnostic seam, unlike this
profile/config layer. See the 2026-08-07 build-log entry.
"""
from __future__ import annotations

from lspr_acq_shell.diagnostics import (
    DiagnosticsConfig,
    DiagnosticsProfile,
    apply_diagnostic_info_filter,
)

__all__ = [
    "DiagnosticsConfig",
    "DiagnosticsProfile",
    "apply_diagnostic_info_filter",
]
