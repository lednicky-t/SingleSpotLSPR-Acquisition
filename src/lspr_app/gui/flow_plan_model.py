"""Re-export shim over `lspr_acq_shell.experiment_plan_table_model` (Phase 2,
Tier 3a - full experiment-control panel consolidation, 2026-08-09) - kept here
so every existing `from lspr_app.gui.flow_plan_model import ...` call site in
this app (and in the umbrella test suite) keeps working unchanged.

The real model/delegates now live in the shared package so LSPRi acq (and any
future app) uses the exact same implementation, not a separate lean copy -
see docs/architecture/general/lspri_acq_build_log.md's 2026-08-09 entries for
the full design discussion.
"""

from __future__ import annotations

from lspr_acq_shell.experiment_plan_table_model import (
    ExperimentPlanColorDelegate,
    ExperimentPlanCommentDelegate,
    ExperimentPlanDirectionDelegate,
    ExperimentPlanDurationDelegate,
    ExperimentPlanFlowDelegate,
    ExperimentPlanSwitchDelegate,
    ExperimentPlanTableModel,
    ExperimentPlanValveDelegate,
    _BaseFlowDelegate,
    _ColorPopupDelegate,
    _HighlightingCommentLineEdit,
    _contrast_text_color,
    _draw_split_comment_text,
    clamped_flow_ul_min,
    display_value_to_seconds,
    safe_color_name,
    seconds_to_display_value,
)
from lspr_acq_shell.pump_plan import (
    clamped_switch_position,
    normalized_pump_direction,
    normalized_valve_state,
)

__all__ = [
    "ExperimentPlanColorDelegate",
    "ExperimentPlanCommentDelegate",
    "ExperimentPlanDirectionDelegate",
    "ExperimentPlanDurationDelegate",
    "ExperimentPlanFlowDelegate",
    "ExperimentPlanSwitchDelegate",
    "ExperimentPlanTableModel",
    "ExperimentPlanValveDelegate",
    "_BaseFlowDelegate",
    "_ColorPopupDelegate",
    "_HighlightingCommentLineEdit",
    "_contrast_text_color",
    "_draw_split_comment_text",
    "clamped_flow_ul_min",
    "clamped_switch_position",
    "display_value_to_seconds",
    "normalized_pump_direction",
    "normalized_valve_state",
    "safe_color_name",
    "seconds_to_display_value",
]
