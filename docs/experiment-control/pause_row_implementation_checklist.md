# Pause Row Implementation Checklist

Status: living draft

This checklist turns the `0` pause row discussion into implementation work items.

## Scope

The pause row is:

- app-defined only
- visible only in advanced table mode
- hidden in basic table mode
- independent of experiment-plan import/export
- persisted across sessions as UI/app state
- preserved in the measured runtime record

## Must-Have Behavior

- The authored plan must not include the pause row.
- Importing a plan must not add, remove, or overwrite the pause row.
- Exporting a plan must not write the pause row into the authored plan file.
- Advanced mode must show the pause row at table row `0`.
- Basic mode must hide the pause row.
- The pause row should be editable when visible.
- Recording should continue while the pause row is active.
- `STOP` should still be the only state that closes the measurement file.

## Data Model

Recommended pause-row state:

- duration
- color
- valve state
- switch position
- description/comment
- per-channel flow values
- per-channel direction values

Items that do not need separate pause-row storage:

- tube sizes, because they already live in the shared editor/app state

## UI Tasks

1. Add a dedicated pause-template object in the experiment-control window.
2. Restore that pause-template object from UI state on startup.
3. Save that pause-template object back into UI state on close/save.
4. Render the pause row only when advanced details are shown.
5. Hide the pause row when basic mode is active.
6. Keep table plan row numbering and runtime row numbering separate.

## Logic Tasks

1. Exclude the pause row from `_read_experiment_control_steps()`.
2. Exclude the pause row from plan import/export serialization.
3. Allow the pause row to be loaded into the editor panel when selected.
4. Ensure plan stepping, moving, jumping, and timeline activation ignore the pause row.
5. Keep runtime recording aware of pause/hold transitions without converting the pause row into a normal plan step.

## Safety Checks

- Verify the pause row does not change the selected plan step count.
- Verify import and export still match for authored steps.
- Verify session restore brings back the pause row independently of the imported plan.
- Verify basic mode does not show the pause row.
- Verify advanced mode does show it.
