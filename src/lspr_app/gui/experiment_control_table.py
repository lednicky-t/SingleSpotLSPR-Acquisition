"""Re-export shim over `lspr_acq_shell.experiment_control_table` (Phase 2,
Tier 3a - full experiment-control panel consolidation, 2026-08-09) - kept here
so every existing `from lspr_app.gui.experiment_control_table import ...`
call site keeps working unchanged.
"""

from __future__ import annotations

from lspr_acq_shell.experiment_control_table import (
    configure_experiment_control_plan_table,
    configure_experiment_control_plan_preview,
    configure_experiment_control_table_columns,
    fit_plan_table_columns_to_viewport,
    sync_experiment_control_tube_columns,
    update_plan_detail_toggle_icon,
    update_plan_table_height,
)

__all__ = [
    "configure_experiment_control_plan_table",
    "configure_experiment_control_plan_preview",
    "configure_experiment_control_table_columns",
    "fit_plan_table_columns_to_viewport",
    "sync_experiment_control_tube_columns",
    "update_plan_detail_toggle_icon",
    "update_plan_table_height",
]
