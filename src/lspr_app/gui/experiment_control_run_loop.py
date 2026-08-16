"""Re-export shim over `lspr_acq_shell.experiment_control_run_loop`
(Phase 2, LSPRi acq experiment-control reuse - Tier 2 extraction,
2026-08-09) - kept here so every existing `from
lspr_app.gui.experiment_control_run_loop import ...` call site in this app
keeps working unchanged.
"""

from __future__ import annotations

from lspr_acq_shell.experiment_control_run_loop import PlanRunLoopMixin

__all__ = ["PlanRunLoopMixin"]
