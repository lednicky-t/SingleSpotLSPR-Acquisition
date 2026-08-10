"""Re-export shim over `lspr_acq_shell.experiment_control_plan_view` (Phase 2,
Tier 3a - full experiment-control panel consolidation, 2026-08-09) - kept here
so every existing `from lspr_app.gui.experiment_control_plan_view import ...`
call site keeps working unchanged.
"""

from __future__ import annotations

from lspr_acq_shell.experiment_control_plan_view import (
    build_experiment_control_headers,
    build_experiment_control_pause_model,
    configure_experiment_control_plan_preview,
    configure_experiment_control_plan_view,
    match_experiment_control_plan_preview_geometry,
    read_experiment_control_pause_step,
    restore_experiment_control_pause_dialog_state,
    save_experiment_control_pause_dialog_state,
)

__all__ = [
    "build_experiment_control_headers",
    "build_experiment_control_pause_model",
    "configure_experiment_control_plan_preview",
    "configure_experiment_control_plan_view",
    "match_experiment_control_plan_preview_geometry",
    "read_experiment_control_pause_step",
    "restore_experiment_control_pause_dialog_state",
    "save_experiment_control_pause_dialog_state",
]
