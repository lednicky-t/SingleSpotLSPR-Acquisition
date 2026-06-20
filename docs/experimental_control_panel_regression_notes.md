# Experimental Control Panel Regression Notes

## Summary

The regression was in the top-content activation and sizing contract for the Experimental Control panel.
The panel was still being created and inserted, but the top splitter could stay collapsed or be restored to a bad size before the Experimental Control view became active.

## What Changed During Cleanup

- The top-content logic was split across `main_window_state.py`, `main_window_lifecycle.py`, and `experiment_control_window.py`.
- The Experimental Control widget is now inserted through a placeholder/stacked-widget flow.
- UI restore now handles `top_view_mode` through normalized view names instead of the older flow-specific path.

## What Was Lost

- The reliable activation sequence that kept the Experimental Control page active after insertion.
- The effective guard that prevented parent splitter sizing from running while the Experimental Control page was inactive.
- The post-activation normalization that prevented a zero or tiny top pane from hiding the panel.

## Current Behavior Before the Fix

- `set_top_content_mode()` could select the stacked page without forcing the top pane back to a usable height.
- `restore_ui_state()` could restore `plot_splitter_sizes` that left the top pane effectively collapsed.
- `ExperimentControlWindow._apply_experiment_control_parent_splitter_sizes()` could run without checking whether the Experimental Control page was actually current.

## Fix Applied

- Added `TOP_CONTENT_TRACE=1` diagnostics for:
  - `restore_ui_state`
  - `showEvent`
  - `ensure_flow_panel_for`
  - `set_top_content_mode`
  - Experimental Control view-mode updates and parent splitter sizing
- Clamped the top splitter to a minimum visible height when activating or restoring top content.
- Guarded Experimental Control parent splitter sizing so it only runs when:
  - the top view mode is `experimental_control`
  - the Experimental Control widget is the current stacked page
- Re-applied Experimental Control layout sizing only after the widget is current.

## Files Touched

- `src/lspr_app/gui/main_window_state.py`
- `src/lspr_app/gui/main_window_lifecycle.py`
- `src/lspr_app/gui/main_window.py`
- `src/lspr_app/gui/experiment_control_window.py`

