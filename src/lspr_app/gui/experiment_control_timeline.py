"""Re-export shim over `lspr_acq_shell.experiment_control_timeline` (Phase 2,
LSPRi acq experiment-control reuse - Tier 1 extraction, 2026-08-09) - kept
here so every existing `from lspr_app.gui.experiment_control_timeline import
...` call site in this app keeps working unchanged.
"""

from __future__ import annotations

from lspr_acq_shell.experiment_control_timeline import PumpPlanTimelineWidget

__all__ = ["PumpPlanTimelineWidget"]
