# Experiment Control Table Behavior

This document describes the intended behavior of the experiment control table in `sLSPR Acquisition`.
It uses the word `table` for readability, but it refers to the plan editor used to author experiment steps.

## Purpose

The table is the main editor for the authored control plan.
It represents the step sequence that defines the experimental process and the device parameters used for each step.

The table should be:

- easy to read
- easy to edit
- compact enough for routine use
- compatible with future plan format changes
- consistent with the native control-plan schema

## Core Layout

- The first column is the step number.
- The table fills the full width of the control panel.
- The comment column should be the most expandable column.
- All other columns should be sized to show their contents readably.
- Numeric values should be right-aligned.
- Rows should remain visually distinguishable, with alternating row shading.

## Planned Content

The table should follow the current control plan format.
At minimum, the common columns are:

- Step
- Duration
- Channel flow values
- Valve
- Switch
- Color
- Comment

Advanced mode should reveal additional channel details when available:

- per-channel flow rates
- per-channel directions
- tube sizes

The exact column set may evolve with future plan format versions, but the table should stay data-driven and easy to extend.

## Editing Rules

- Cells are editable only when the step row is selected.
- Hovering and scrolling over editable numeric cells may change the value.
- Valve is a two-state cell and should toggle on click.
- Switch and Color are dropdown-style cells with scrollable option lists.
- The dropdown contents are not editable as text directly in the table.
- Option lists, labels, and palette entries are edited through the popout tool windows above the table.

## Tool-Driven Options

The table should reflect settings controlled elsewhere in the UI:

- valve display labels come from the valve-label editor
- switch port labels or solution names come from the switch editor
- color options come from the palette editor
- time display units come from the time-unit controls

The table should display the current values immediately after those tools change.

## Selection And Navigation

- The selected row must remain visible.
- The table should auto-scroll when the current row changes.
- PageUp and PageDown should move the selected step.
- The table has two distinct cursors when the plan is running:
  - the editor cursor shows where the user is browsing or editing and is highlighted in green
  - the runtime cursor shows which step is currently active in the measurement and is highlighted separately
- The editor cursor should remain user-controlled even while the runtime advances.
- Runtime-driven step changes should not steal the editor cursor.
- In edit mode, multi-cell and multi-row selection should still be possible.
- When the active runtime row is edited during a running measurement, the changed device parameters should take effect immediately and the runtime log should record the change.

## Persistence

The table should preserve session state across restarts:

- column widths
- column order
- selected row
- visible/hidden advanced columns
- time-unit mode
- palette and label settings

## Design Goal

The table should behave like a simple spreadsheet, similar to Microsoft Excel:

- familiar editing
- predictable selection
- compact row layout
- readable values
- stable keyboard and mouse behavior

The implementation should prefer clean model/view separation so future plan format changes can be added without rewriting the whole table again.
