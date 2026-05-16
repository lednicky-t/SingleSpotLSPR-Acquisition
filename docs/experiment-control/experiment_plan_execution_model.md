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

This is the state currently closest to the existing plan pause behavior.

Behavior:

- the system stays on the current step and current step progress
- hardware settings remain unchanged
- the elapsed time / countdown for the next step is frozen
- the timeline cursor stops moving
- measurement recording continues

This is a time hold, not a hardware stop.

The plan can later be resumed from the same point.

### PAUSE

This is a new device-control state.

Behavior:

- the flow execution remains logically paused
- the current step can be replaced by an artificial pause step if needed
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

- `HOLD` equals time freeze on the current step
- `PAUSE` equals temporary device stop or safe idle, potentially driven by a synthetic `0` row
- `STOP` equals full termination

The table and bar remain plan selectors, while the recorded runtime sequence is built live from the actual experiment.

