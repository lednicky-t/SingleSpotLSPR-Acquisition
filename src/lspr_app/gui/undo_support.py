"""Re-export shim over `lspr_acq_shell.undo_support` (Phase 2, Tier 3a - full
experiment-control panel consolidation, 2026-08-09) - kept here so every
existing `from lspr_app.gui.undo_support import ...` call site keeps working
unchanged.
"""

from __future__ import annotations

from lspr_acq_shell.undo_support import DEFAULT_UNDO_HISTORY_SIZE, SnapshotCommand, push_snapshot

__all__ = ["DEFAULT_UNDO_HISTORY_SIZE", "SnapshotCommand", "push_snapshot"]
