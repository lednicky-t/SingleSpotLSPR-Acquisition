# Codex experiment-control reuse split V49

This document defines the next architecture split for the experiment-control
module in singleLSPR acquisition and the shared visualization path that should
also be reused by singleLSPR evaluation and LSPR imaging evaluation.

This is a planning and documentation file only.
Do not treat it as an implementation patch.

## Goal

Turn the current experiment-control window into a reusable module with two
deployment modes:

1. Acquisition mode
2. Evaluation mode

The same visual structure should be shared across apps, but the runtime
capabilities must differ by host app.

## Target module split

### 1. Shared visualization module

Create a module that owns the reusable UI:

- experiment-control plan table
- timeline
- step editor
- plan import/export controls
- layout state
- theme application
- panel visibility handling

This module should be usable in:

- singleLSPR acquisition
- singleLSPR evaluation
- LSPR imaging evaluation

### 2. Runtime/controller module

Create a controller that owns the experiment plan state machine:

- run
- hold
- pause
- stop
- previous/next step navigation
- step timing
- step transitions
- plan state persistence hooks

The controller should expose a small public API and should not depend on the
main window.

### 3. Device/backend module

Create an acquisition-only backend for hardware control:

- device discovery
- device connection
- device status
- command dispatch
- port ownership
- pump/valve/M-switch integration
- event notifications to the controller

This backend is not required in evaluation mode.

### 4. IO module

Keep plan loading and export separate from the widget:

- import plan
- export plan
- validate plan content
- map UI state to persisted plan state

## Required capability split

The reusable panel must be driven by capabilities rather than app-specific
conditionals spread through the UI.

Recommended capability flags:

- `devices_enabled`
- `runtime_control_enabled`
- `plan_import_export_enabled`
- `show_runtime_buttons`
- `show_device_columns`
- `show_device_status_strip`
- `show_step_navigation_controls`

## Host app behavior

### Acquisition apps

Use the full module stack.

Requirements:

- show all runtime controls
- connect to live devices when available
- ignore unsupported device types cleanly
- show only active device columns where practical
- emit events that the main window can listen to
- support running steps, timing, and command triggering
- support device setting changes and communication

### Evaluation apps

Use the shared visualization, but with a restricted capability set.

Requirements:

- keep the same visual appearance and panel layout where possible
- keep plan loading and exporting
- hide the runtime control icons
- hide device communication and device status UI
- keep timeline, plan editing, and visualization usable
- do not require live device dependencies

## Proposed public interface

The shared module should avoid private main-window reach-through.

Suggested surface:

- `set_capabilities(capabilities)`
- `load_plan(...)`
- `save_plan(...)`
- `set_runtime_state(...)`
- `set_connected_devices(...)`
- `request_run()`
- `request_stop()`
- `request_pause()`
- `request_hold()`
- `request_step_next()`
- `request_step_previous()`
- `request_step_jump(row)`

## Proposed file split

Suggested destination files:

- `gui/experiment_control_panel.py`
- `gui/experiment_control_view.py`
- `gui/experiment_control_controller.py`
- `gui/experiment_control_backend.py`
- `gui/experiment_control_io.py`
- `gui/experiment_control_capabilities.py`

The existing `experiment_control_window.py` should become either:

- a compatibility wrapper around the new panel, or
- a thin app-specific composition layer

## Migration tasks

1. Extract the reusable UI widgets and layout into the shared panel module.
2. Move plan load/save logic into the IO module.
3. Move runtime state machine logic into the controller.
4. Move device comms into the acquisition backend.
5. Add capability flags so evaluation mode can hide unsupported controls.
6. Replace main-window private method reach-through with a public API or
   signals.
7. Wire singleLSPR evaluation to the shared visualization module without live
   device dependencies.
8. Wire LSPR imaging evaluation to the same shared module with its own
   capability set.
9. Keep acquisition mode fully functional after the split.

## Acceptance criteria

The split is acceptable only if all of the following remain true:

- acquisition still runs the full device-enabled experiment control
- evaluation apps can reuse the same visualization without device code
- plan import/export still works
- main-window coupling is reduced
- the panel can be instantiated without live hardware
- capability-based hiding works cleanly
- no app-specific behavior is hard-coded into the shared UI

## Version split rule

This proposal is versioned as `V49`.

Use the next codex task file version for the implementation phase after this
document is committed.
