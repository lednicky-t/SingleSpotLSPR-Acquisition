"""Re-export shim over `lspr_acq_shell.pump_plan` (Phase 2, LSPRi acq
experiment-control reuse - Tier 0 extraction, 2026-08-09) - kept here so
every existing `from lspr_app.domain.pump_plan import ...` call site in this
app keeps working unchanged.

`to_core_experiment_plan()` is NOT a plain re-export: the shared version
requires `app_version` explicitly (a shared package can't import
`lspr_app.version.APP_VERSION` - see the shared module's own docstring for
why). This wrapper preserves the exact original signature
(`steps, *, app_name="LSPR Acquisition"`) by supplying `app_version=APP_VERSION`
itself, so none of this app's 5 existing call sites needed to change.
"""

from __future__ import annotations

from lspr_app.version import APP_VERSION
from lspr_acq_shell.pump_plan import (
    ACTIVE_PUMP_CHANNELS,
    DEFAULT_ROLLER_COUNT,
    DEFAULT_TUBE_MM,
    HDF5_PUMP_CHANNELS,
    PLAN_COLOR_OPTIONS,
    TUBE_DIAMETER_OPTIONS,
    VALID_ROLLER_COUNTS,
    ExperimentPlan,
    PumpChannelStep,
    PumpPlanStep,
    TubeDiameterOption,
    duplicate_plan_step,
    from_core_experiment_step,
    from_core_flow_step,
    make_default_pump_plan,
    nearest_tube_diameter_option,
    recompute_plan_timing,
    steps_to_hdf5_rows,
    to_core_experiment_step,
)
from lspr_acq_shell.pump_plan import to_core_experiment_plan as _shared_to_core_experiment_plan

__all__ = [
    "ACTIVE_PUMP_CHANNELS",
    "DEFAULT_ROLLER_COUNT",
    "DEFAULT_TUBE_MM",
    "HDF5_PUMP_CHANNELS",
    "PLAN_COLOR_OPTIONS",
    "TUBE_DIAMETER_OPTIONS",
    "VALID_ROLLER_COUNTS",
    "PumpChannelStep",
    "PumpPlanStep",
    "TubeDiameterOption",
    "duplicate_plan_step",
    "from_core_experiment_step",
    "from_core_flow_step",
    "make_default_pump_plan",
    "nearest_tube_diameter_option",
    "recompute_plan_timing",
    "steps_to_hdf5_rows",
    "to_core_experiment_step",
    "to_core_experiment_plan",
]


def to_core_experiment_plan(steps: list[PumpPlanStep], *, app_name: str = "LSPR Acquisition") -> ExperimentPlan:
    return _shared_to_core_experiment_plan(steps, app_name=app_name, app_version=APP_VERSION)
