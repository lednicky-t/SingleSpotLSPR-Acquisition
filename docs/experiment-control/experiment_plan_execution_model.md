# Flow Plan Execution Model

Status: living draft

This document captures the runtime behavior of experiment control and recording. It is meant to stay with the project as a design reference for future work, UI changes, and control-state refactors.

## Purpose

The experiment plan table and the experiment-plan bar are the designed recipe. They define the intended sequence of steps.

The actual run is the measured execution. It is built live while the experiment runs and can differ from the planned sequence because of:

- holding the plan
- pausing device motion
- skipping steps
- manual intervention
- hardware limitations or failures

The key design principle is:

- the table and the timeline describe the plan
- the runtime state describes what is actually happening now
- recording follows the runtime state, not just the planned state

## Runtime States

The experiment control runtime should expose these states:

- `RUN`
- `HOLD`
- `PAUSE`
- `STOP`

### RUN

This is the normal active state.

Behavior:

- experiment-plan time advances
- the timeline cursor moves
- the current step progresses normally
- hardware keeps running according to the active step
- measurement recording continues if enabled

### HOLD

This is the state that freezes the plan clock without applying the synthetic pause row.

Behavior:

- the system stays on the current step and current step progress
- hardware settings remain unchanged
- the elapsed time / countdown for the next step is frozen
- the timeline cursor stops moving
- measurement recording continues

This is a time hold, not a hardware stop.

The plan can later be resumed from the same point.

### PAUSE

This is the device-control state driven by the synthetic pause row.

Behavior:

- the flow execution remains logically paused
- the current step can be replaced by the synthetic pause step from the app-defined pause row
- selected devices may be stopped or put into a safe idle mode
- measurement recording continues
- when pause is canceled, the original flow step can resume from the paused position

Suggested naming:

- keep `HOLD` for the time-freeze state
- use a different name for this device-stop state if `PAUSE` is too ambiguous

Candidate names:

- `DEVICE_PAUSE`
- `DEV_STOP`
- `SAFE_PAUSE`
- `HARD_PAUSE`

The exact name should reflect that this state is not the same as holding the timer.

### Synthetic Pause Row

The flow table may reserve a special `0` row as a pause template, but it should not be treated as a normal authored plan step.

Behavior:

- it is skipped during the normal authored plan sequence
- it is entered only when the user explicitly requests a pause-like device state
- it can apply its own hardware parameters while the current timed step is suspended
- if its duration is `0`, it may remain active indefinitely until canceled
- the original step progress is preserved so the run can resume from the interrupted point

This makes the pause action a runtime interrupt, not a permanent change to the designed recipe.

Persistence rules:

- the synthetic `0` row is defined only inside the app
- it is not part of normal experiment-plan import or export
- it is not saved as part of the authored plan file
- it is saved only in the measured experiment-control runtime record
- readers should treat it as runtime context, not as authored recipe data

Visibility rules:

- in basic table mode, the `0` row stays hidden
- in advanced table mode, where all channels, directions, and tube size are shown, the `0` row is visible and editable
- the row visibility is controlled by the app view mode, not by the imported experiment plan
- the row should persist across sessions as an app setting or UI state
- importing a experiment plan must not overwrite or remove the app-defined `0` row

### STOP

This is the full termination state.

Behavior:

- all devices stop
- pump motion stops
- flow execution ends
- measurement recording ends
- the HDF5 file is finalized and closed

## Runtime Controls

The control strip below the timeline should expose the runtime actions as separate buttons:

- `Play`
  - starts a stopped plan
  - resumes a plan from `HOLD` or `PAUSE`
- `Hold`
  - freezes the current plan clock and automatic step progression
  - does not apply the synthetic pause row
- `Pause`
  - applies the synthetic pause row
  - pauses the execution while preserving the interrupted step for later resume
  - can be entered directly from `RUN`, from `HOLD`, or from `STOP`
  - when entered from `STOP`, the plan starts recording and applies the pause state on the selected step; `Play` then resumes that selected step afterward
- `Stop`
  - cancels every runtime state
  - stops hardware and ends recording

The `Pause` button edits and applies the runtime pause template, while the pause template itself remains a separate app-defined row in the experiment-control table.

## Plan Vs Runtime

The plan is a design artifact.

It should contain:

- step order
- step duration
- per-channel flow settings
- valve state
- switch position
- color / annotation
- comments

The runtime is the executed sequence.

It should contain:

- the actual state transitions
- the currently active step
- hold and resume events
- pause and device-stop events
- skipped steps
- device state snapshots
- recording state changes

This means the runtime record should be append-only and reflect what really happened.

## Pause Row Strategy

If the GUI uses a special `0` row, it should be treated as a synthetic runtime template with these rules:

- it is not part of the normal step progression
- it is app-defined only, not imported from or exported with the authored plan
- it can be selected manually or invoked by a pause button
- it can temporarily override hardware parameters
- it does not replace the authored step in the saved plan
- it is persisted only in the measured experiment-control record
- it is hidden in basic mode and shown in advanced mode
- it survives session restore independently of plan import
- the measured runtime record should show both the interruption and the resumed step

## Recording Rules

Recording is separate from flow execution, but it follows the runtime.

Recording should:

- start when the user begins a measurement
- continue through `RUN`
- continue through `HOLD`
- continue through `PAUSE`
- end only on `STOP`

Flow-plan hold does not stop recording.

Device pause does not stop recording.

Only stop closes the file and finalizes the measurement session.

## Timeline And Cursor Rules

The timeline bar should represent the measured flow execution, not only the authored plan.

Rules:

- in `RUN`, the cursor advances normally
- in `HOLD`, the cursor freezes at the current position
- in `PAUSE`, the cursor may stop at the pause event and resume after the pause is released
- in `STOP`, the cursor resets or finalizes according to the session end behavior

The timeline can therefore diverge from the original designed plan if the user holds, pauses, or skips steps.

The experiment-control table should keep two separate cursors while a measurement is active:

- the editor cursor follows the user’s table navigation and cell edits
- the runtime cursor follows the actual step that is currently driving hardware

The runtime cursor must not overwrite the editor cursor just because the measurement advances.
This lets the user inspect and edit other rows while the experiment continues.
If the user edits the currently active runtime row during a running measurement, the hardware should re-apply the changed step immediately and append a runtime event that records what changed.

## Timing Contract

The GUI and runtime controller should use three distinct timing concepts:

- `step runtime`: wall-clock time since the current step started, including hold time
- `step ETA`: remaining active step time, excluding hold time
- `total runtime`: wall-clock time since measurement recording started, including hold time and step changes

Rules:

- when the user presses play, the selected step starts at runtime `0`
- step runtime and total runtime reset to `0` for a new measurement run
- hold freezes step ETA and automatic step progression
- hold does not stop the wall-clock runtime counters
- step skipping or step switching must not reset total runtime
- step skipping or step switching does reset the current step runtime to `0`
- the next step starts only when the active step timer reaches its duration

Implementation note:

- automatic step switching should use elapsed active time only
- timeline cursor movement should reflect active step progress
- displayed runtime counters should continue to show wall-clock elapsed time while the plan is running or held
- when measurement recording stops, the displayed values should freeze until the next play event

## Useful Implementation Separation

Recommended separation for the codebase:

- plan model: the authored step table
- runtime controller: actual flow progression and state transitions
- recording controller: measurement file lifecycle
- timeline view: visual cursor and progress rendering
- hardware adapters: pump, valve, switch, and spectrometer control

This separation makes the system easier to expand later if more device classes or more runtime states are added.

## Open Questions

- Should the device-stop state be called `PAUSE`, `DEV_STOP`, or something else?
- Should `PAUSE` create an explicit artificial step in the runtime record?
- Should recording store both the authored step and the executed runtime event for each transition?
- Should the timeline display the planned step, the executed step, or both?
- Should a paused device state be resumable from the same exact point for every device type?

## Proposed Answers

These are the most practical choices for the current codebase:

- Keep `HOLD` as the time-freeze state.
- Use a device-oriented name such as `DEV_STOP` or `SAFE_PAUSE` for the state that actually stops hardware.
- Do not create a fake experiment-plan step in the authored plan table.
- If a special `0` pause row exists in the UI, treat it as a synthetic runtime template, not a normal authored step.
- Keep that row hidden in basic mode and visible only in advanced mode.
- Do create an explicit runtime event record for every hold, resume, device pause, resume, skip, and stop transition.
- Show both the planned step and the executed step in the runtime record, even if they diverge.
- Make resumability device-specific if needed, but prefer a common high-level control model.

Implementation preference:

- the authored plan stays simple
- the runtime log becomes the source of truth for what happened during the experiment
- the recording layer appends device/runtime events, not just step labels

## Current Interpretation

For now, the intended meaning is:

- `HOLD` equals time freeze on the current step without changing the hardware state
- `PAUSE` equals temporary device stop or safe idle, driven by the synthetic pause row
- `STOP` equals full termination

The table and bar remain plan selectors, while the recorded runtime sequence is built live from the actual experiment.

## Known Issues Fixed

### 2026-07-23 — step navigation via Next/Previous didn't reset the per-step elapsed-time clock

Found while investigating a related sensorgram time-anchor bug (see
`../sensorgram_improvements.md`, C1-C8) - the maintainer noticed a plan step's elapsed/ETA display
kept counting up from wherever the *previous* step had left off after pressing Next, instead of
restarting at 0 for the new step.

`gui/experiment_control_window.py`'s `_move_to_relative_experiment_control_step` (the Next/Previous
buttons) jumps to a new step but was missing the `_plan_elapsed_s` / `_plan_resume_elapsed_s` /
`_plan_started_monotonic` reset that its sibling function, `_jump_to_experiment_control_step`
(clicking a step directly), already performed correctly. Fixed by adding the same reset to both
functions so every "jump to step N while running" path behaves identically.

### 2026-07-23 — "Step left" / "Plan left" status-line ETA was wrong from the second step onward

Independent of the navigation bug above (present even via normal auto-advance, on any plan with 2+
steps) - `_refresh_status_line`'s ETA math subtracted the step-relative `_plan_elapsed_s` (resets to
0 at every step transition) directly from `step.end_s`/`total_end_s`, which are plan-cumulative
positions. This only produced a correct result on the first step (where the two happen to coincide,
since `step.start_s == 0`); from the second step onward "Plan left" barely decreased and "Step left"
was overestimated. Fixed by computing the plan-cumulative elapsed position as
`step.start_s + _plan_elapsed_s` first - the same combination `_timeline_progress_for_display`
already used correctly - before comparing against the cumulative totals.

### 2026-07-23 — pressing Next on the last step replayed it instead of finishing the plan

`_move_to_relative_experiment_control_step` clamps its target row to `len(steps) - 1` - correct for
staying in range, but when already on the last step and running/holding/paused, `row + 1` clamps
right back to the *same* row. Combined with the elapsed-time-reset fix above (which now
unconditionally restarts the target step's clock), pressing Next on the last step reset and
replayed it from 0 instead of finishing the plan - there is no next step to go to, so nothing should
restart. `_advance_experiment_control_progress` (auto-advance) already handles reaching the end
correctly (calls `_stop_experiment_control()` and reports "Experiment plan finished."). Fixed by
checking `row + delta >= len(steps)` *before* clamping, while running/holding/paused, and taking the
same finish path auto-advance already does instead of falling through to the jump/reset logic.
Pressing Next while idle (not running/holding/paused) past the last step is unaffected - there's
nothing to finish, so it keeps the existing clamp-and-select behavior.

## Extended Mode Plan-Table Interactions

The plan table's per-channel "extended" view (toggled via `plan_detail_toggle`, showing the
direction and tube-diameter columns alongside the flow-rate column for every channel) supports the
same hover-and-scroll editing style for all three per-channel fields, plus click-to-toggle for
direction:

- **Flow rate**: scroll over the cell to change it. Default step is 5 per wheel notch; hold Ctrl
  while scrolling for a finer step of 1. Value is clamped at 0.
- **Direction**: click the cell (or press Space/Enter while it has focus) to toggle CW/CCW, or
  scroll over it - any wheel notch in either direction flips it, since it's a two-state value. The
  cell paints as a rotation-arrow glyph (↻ CW / ↺ CCW, via `direction_glyph` in
  `experiment_control_builders.py`) instead of plain text, matching the manual-control panel's Dir
  button. `ExperimentPlanDirectionDelegate` in `gui/flow_plan_model.py` implements the click/paint
  half; `_cycle_plan_table_cell_by_wheel` in `gui/experiment_control_window.py` implements the
  scroll half.
- **Tube diameter**: scroll over the cell to change it by the shared spinbox's single step (0.01
  mm). Tube diameter is **not** a per-step value - it's a per-channel constant that backs
  `manual_tube_spins[channel_index]` in the manual-control panel, shared across every row for that
  channel. Scrolling over any row's tube cell for a channel moves that channel's spinbox directly
  (`spin.setValue(...)`), which then propagates to every row via the spinbox's existing
  `valueChanged` -> `_sync_experiment_control_tube_columns` wiring - the same thing editing the
  manual-control spinbox itself already does. There is deliberately no click-to-edit for tube (only
  scroll), since the field only ever needs to move by a fixed physical increment.

All three wheel-scroll behaviors, and the direction delegate's click/paint, are covered by
`tests/unit/test_experiment_control_plan_table_extended_wheel.py` in the umbrella repo.
