"""Re-export shim over `lspr_acq_shell.experiment_control_dialogs` (Phase 2,
Tier 3a - full experiment-control panel consolidation, 2026-08-09) - kept here
so every existing `from lspr_app.gui.experiment_control_dialogs import ...`
call site keeps working unchanged.
"""

from __future__ import annotations

from lspr_acq_shell.experiment_control_dialogs import ExperimentControlDialogs

__all__ = ["ExperimentControlDialogs"]
