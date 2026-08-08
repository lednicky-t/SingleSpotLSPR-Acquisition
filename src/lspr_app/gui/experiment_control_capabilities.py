"""Re-export shim over `lspr_acq_shell.experiment_control_capabilities`
(Phase 1 shell extraction, 2026-08-08) - `ExperimentControlCapabilities` is a
plain frozen dataclass with no window/Qt coupling, so it moved as-is. Kept
here so every existing `from lspr_app.gui.experiment_control_capabilities
import ExperimentControlCapabilities` call site in this app keeps working
unchanged.
"""
from __future__ import annotations

from lspr_acq_shell.experiment_control_capabilities import ExperimentControlCapabilities

__all__ = ["ExperimentControlCapabilities"]
