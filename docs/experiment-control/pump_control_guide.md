# Pump Control Guide for SeaSharp

This document explains how the pump control code in LSPR is structured and how to extend or reimplement it safely.

It is intended for both humans and AI agents that need to build or maintain pump control logic.

## Scope

- Pump controller: Ismatec Reglo ICC family
- Main implementation file: [`src/lspr_app/device/reglo_icc.py`](../../src/lspr_app/device/reglo_icc.py)
- GUI integration: [`src/lspr_app/gui/experiment_control_window.py`](../../src/lspr_app/gui/experiment_control_window.py)
- Startup detection: [`src/lspr_app/gui/workers.py`](../../src/lspr_app/gui/workers.py)

## High-Level Design

The pump control stack has three layers:

1. Device layer
   - Owns the serial protocol.
   - Opens/closes the port.
   - Sends and receives commands.
   - Converts high-level actions into controller commands.

2. Experiment-control layer
   - Reads the current plan table.
   - Applies a plan step to the pump.
   - Stops, starts, and configures channels.
   - Skips the pump if it is not connected.

3. Startup/discovery layer
   - Scans serial ports for a likely pump controller.
   - Probes the controller with a short identification sequence.
   - Connects the live pump controller only after discovery.

## Core Data Models

The pump device layer uses these structures:

- `PumpPort`
  - `device`
  - `description`
  - `hwid`

- `PumpProbe`
  - `port`
  - `protocol_version`
  - `serial_number`
  - `channel_count`
  - `model`

The experiment plan uses these structures:

- `PumpChannelStep`
  - `flow_ul_min`
  - `direction`

- `PumpPlanStep`
  - `step`
  - `duration_s`
  - `start_s`
  - `end_s`
  - `valve`
  - `switch_position`
  - `description`
  - `channels`

  Whether to send `description` to the pump's display, and whether to live-highlight the
  16-character limit in the plan table, are **not** `PumpPlanStep` fields - they're global
  `ExperimentControlWindow` settings that apply to every step. See
  [Pump Display](#pump-display) below.

## Connection Flow

### Port discovery

The pump scanner checks all serial ports and keeps those that look like a Reglo-compatible device.

Heuristics are implemented in:

- `is_probable_reglo_port(port)`

The current checks look for:

- `265C:0001` in the hardware ID
- `ISMATEC` in description or hardware ID
- `REGLO` in description or hardware ID

### Probe sequence

When the code thinks a port is a pump, it probes it by:

1. Opening the serial port
2. Sending identification commands
3. Reading the replies
4. Closing the port

This is implemented in:

- `RegloICCClient.probe_port(port)`

Probe commands:

- `0x!` -> protocol version
- `0xS` -> serial number
- `0xA` -> channel count
- `0#` -> model string

### Serial settings

The current pump connection uses:

- baudrate: `9600`
- bytes: `8`
- parity: `N`
- stop bits: `1`
- default timeout: `0.35 s`

## Command Model

The pump controller is driven with short ASCII commands terminated by `\r`.

### Start/stop

- `start_channel(channel)`
  - sends `{channel}H`

- `stop_channel(channel)`
  - sends `{channel}I`

- `stop_all(channel_count=4)`
  - stops channels `1..channel_count`

### Channel configuration

`configure_channel(channel, flow_ul_min, direction, tube_mm)` performs:

1. Tube configuration
   - command: `{channel}+{tube_code}`
2. Direction setup, only if flow is active
   - `CW` -> `{channel}J`
   - `CCW` -> `{channel}K`
3. Pump mode setup
   - `{channel}M`
4. Flow rate
   - `{channel}f{encoded_flow}`

### Apply channel

`apply_channel(...)` is the higher-level helper.

Behavior:

- Always configures the channel first.
- If flow is `0` or direction is `OFF`, it stops the channel.
- If `start=True`, it starts the channel after configuring it.

This is the safest entry point for experiment-plan execution because it keeps configuration and start/stop logic together.

## Encoding Rules

### Tube diameter

Tube diameter is encoded as hundredths of a millimeter:

- `0.25 mm` -> `0025`

Implemented by:

- `_encode_tube_mm(tube_mm) -> str`

### Flow rate

Flow rate is encoded in a type-2 volume format.

The helper:

- `_encode_volume_type2(value_ml) -> str`

Converts:

- `uL/min` -> `mL/min`
- then into the controller-specific mantissa/exponent string

If you change this encoding, verify it against the pump manual and a real controller before deploying.

## Pump Display

**Status: implemented per the manual, but NOT confirmed to actually show text on a real
pump's screen yet. Treat this feature as unverified until someone checks it against
hardware and this note is updated or removed.**

**History**: originally implemented with the `DA` command (see "What's NOT confirmed"
below for why that was the first guess); a real hardware test showed the command sends
and gets acknowledged (`*`) but nothing appears on the screen. Confirmed with the
maintainer that the pump already switches to its own "Remote Mode" screen automatically
as soon as it's connected over USB (manual section 6.6, "Information Screens") -
regardless of any command this app sends - so the `A`/`B` front-panel-lock commands
(section 6.17/6.18) that were the next suspect are **not** the missing piece; a
short-lived toggle for them was built, found to make no observable difference, and
removed again. Currently implemented with `xN` instead - see below.

This is a **global** setting, not a per-step field: `ExperimentControlWindow._pump_display_enabled`
(a plain instance attribute, not part of `PumpPlanStep` or the exported plan/HDF5 data - it's
pure app/session UI state, saved and restored the same way as other window preferences via
`save_ui_state()`/`_restore_experiment_control_state()`). When it's on, *every* step, as it's
applied to the pump (channel start/stop/configure - see "Apply channel" above), sends its
`description` to the pump's own display. When it's off, the display is explicitly cleared
(empty string sent) rather than left showing the previous step's text. There is no per-step
override - flipping the setting affects the whole plan.

It was originally a per-step field; it was changed to global because that's what actually
matched the intended workflow (one on/off choice for the whole run, not toggled step by step),
and because it also removed a fragile write-back path (the popup had to reach into whichever
row was currently selected and mutate that row's step object directly - easy to get wrong, and
the source of an earlier "toggling this silently does nothing" bug). A global boolean has
nothing to write back to.

- `RegloICCClient.set_display_text(text)` sends `0xN{text}\r` - the manual's `xN`
  ("Set pump's temporary display name") command, addressed to pump 0 since this is a
  per-pump, not per-channel, parameter.
- `sanitize_pump_display_text(text, max_length=16)` (module-level function in
  `reglo_icc.py`) filters to printable ASCII (0x20-0x7E) and truncates to 16 characters
  first, per the manual's `String` data type rules (section 14.6.13: printable ASCII only,
  no embedded `[CR]`) and the physical display's own line width (the general `String` type
  itself allows up to 64 characters - `xN`'s own table entry doesn't restate a shorter
  limit the way `D`/`DA`'s entries do, but 16 chars is what actually fits the LCD in every
  screenshot in the manual, so the truncation stays at 16 either way). Reused by the GUI's
  live preview so the two can't drift apart.
- Dispatch happens in `experiment_control_window.py`'s `_plan_step_commands` (via a
  `pump.set_display` command) for normal step transitions, and in
  `_push_step_pump_display_now` for an immediate push when the setting is toggled while a
  step is currently applied to hardware (added specifically so toggling it gives instant
  feedback instead of silently waiting for the next step transition).
- The settings popup (gear icon next to the step editor row's Comment field, opened via
  `_edit_pump_display_settings` -> `ExperimentControlDialogs.edit_pump_display_settings`)
  looks like it's per-step because of where it's anchored in the UI, but it edits the two
  global booleans regardless of which row is selected - its title and checkbox wording say
  "all steps" / "each step" to make that explicit.

### 16-character limit highlight (table-cell preview)

`ExperimentControlWindow._pump_display_highlight_enabled` is a second, GUI-only global toggle
nested under `_pump_display_enabled` in the pump-display settings popup. It does not affect
what gets sent to hardware at all - it only controls how comments are *drawn*:

- **Committed cells:** when on (and `_pump_display_enabled` is also on),
  `ExperimentPlanCommentDelegate.paint()` in `gui/flow_plan_model.py` splits every row's
  Comment cell text at the 16th character and paints the overflow in `#c97a7a` (the same
  reddish color and split point as the popup's own live preview).
- **While actively typing:** the inline cell editor (`_HighlightingCommentLineEdit`, also in
  `gui/flow_plan_model.py`) does the same split live, character by character, via a custom
  `paintEvent` - but only while the full text still fits the field without needing horizontal
  scroll. `QLineEdit`'s internal scroll offset isn't public API, so this doesn't attempt to
  reproduce it; once a comment is long enough to need scrolling, the editor falls back to
  plain native single-color rendering until the edit is committed, at which point the
  cell's own painting (which has no such limitation) takes back over. Both paint paths share
  the actual split/color logic via `_draw_split_comment_text()` so they can't drift apart.
- Turning `_pump_display_enabled` off always forces this back off too - checked in the popup
  (`experiment_control_dialogs.py`), and both flags are session UI state only (see above),
  not exported plan/HDF5 data.
- The split is done on the raw comment text as typed (character count), not on the
  ASCII-sanitized text used for the actual hardware send - so if a comment contains
  non-printable-ASCII characters, the on-screen split point and the pump's actual truncation
  point can differ slightly. This only matters for non-ASCII comments, which are already
  outside what the pump display itself supports (see `sanitize_pump_display_text` above).

**What's confirmed:** the full software round-trip (checkbox -> global window state -> command
dispatch -> `0xN<text>\r` sent over the serial port) works, and the exact command sent has a
unit test (`tests/unit/test_pump_display.py`) checking it against a fake serial port. The
Comment-cell and inline-editor coloring is covered by
`tests/integration/test_pump_display_global_highlight.py` (pixel-level checks against a
rendered `QPixmap`). Also confirmed: the pump does *not* need to be told anything (via `A`/`B`
or otherwise) to be "under remote control" - it enters that mode by itself as soon as it's
connected over USB, and address `0` is documented as ignored-but-required for any
per-pump (non-per-channel) command sent over USB (section 14.4), so address is not a
suspect either.

**What's NOT confirmed:** whether `xN` is actually the right command for this pump model's
display. First real test attempt used `DA` ("write letters to the pump to display while
under external control", section 6.20) - it seemed like the obvious match by name, sent and
got acknowledged (`*`), but no text appeared on the screen. The tell in hindsight: `DA` (and
its sibling `D`, "write numbers") are the *only* commands in the entire manual's summary
table (section 16.2's list) with no corresponding worked example anywhere in section 18,
even though nearly every other command does - including `xN` ("Set pump's temporary display
name", section 6.5), which has one for exactly this scenario (section 18.6.2: `0xNReagent
A[CR]` -> `*`). Switched to `xN` on that basis, but this is still a documentation-driven
best guess, not a hardware-confirmed fix. Next time hardware is available: enable
diagnostics/log panel, toggle the setting on the currently-applied step, and check the log
for `pump.set_display` - "Step command OK" or "Step command failed | ... | error=...". If it
logs OK but nothing shows even with `xN`, the remaining suspects are: the display needing a
specific pumping *mode* selected (section 6.4.2 lists 7 modes, e.g. Flow Rate vs Volume vs
Disabled - the manual's screenshots suggest the channel number/rate always occupies the
main display area, so a custom name may only be visible on a *different* screen than the
default Status view, e.g. reachable via the right-arrow "Next Screen" icon shown in section
6.2) or, less likely, this being a documented-but-unimplemented command in this pump's
actual firmware revision.

## Important Safety Rules

These are the rules the app currently follows and new code should preserve them:

- Never send a pump command unless the pump client is connected.
- Disconnecting should not be treated as a stop command.
- To make the pump safe on exit, call `stop_all(...)` before closing the port.
- Startup/discovery should not assume a device is connected just because it was seen in the past.
- If the pump is already connected, a re-initialization pass should not reconnect it unnecessarily.

## Current UI/Flow Integration

Pump control is not isolated in a separate GUI module yet.

The experiment-control window is responsible for:

- connecting the pump client
- reading the plan table
- applying a step to the hardware
- stopping all channels at shutdown

Key methods in:

- [`src/lspr_app/gui/experiment_control_window.py`](../../src/lspr_app/gui/experiment_control_window.py)

Important methods:

- `connect_best_pump_controller()`
- `_connect_selected_port()`
- `_disconnect_pump()`
- `_apply_step_to_pump_async()`
- `_stop_all_channels()`
- `shutdown_devices()`

## Flow-Plan Execution Logic

When a flow step is applied, the code should:

1. Read the selected/running step.
2. Determine which pump channels changed.
3. Stop channels that no longer need flow.
4. Configure the active channels.
5. Start the channels that should run.
6. Skip the pump entirely if it is disconnected.

This means pump changes are step-driven, not continuously re-sent every cycle.

## Startup Behavior

The app now uses a staged startup flow:

1. Discover hardware in a worker.
2. Create the flow panel.
3. Connect the pump, valve, and M-switch in the startup path.
4. Keep the splash screen open until startup is ready.

Manual re-initialization from the HW menu remains separate and should be treated as a user-triggered rescan/reconnect.

## Recommended Implementation Pattern

If you are writing new pump-control code, prefer this pattern:

```python
client = RegloICCClient()
client.connect("COM13")
probe = client.get_probe()

for channel in range(1, 5):
    client.configure_channel(channel, flow_ul_min=20.0, direction="CW", tube_mm=0.25)

client.start_channels([1, 2])

# Later
client.stop_all(4)
client.close()
```

For a experiment-plan step, use `apply_channel(...)` instead of hand-building raw commands.

## Troubleshooting Checklist

If the pump does not move:

- Confirm the port is correct.
- Confirm the pump is connected in the live client, not just detected.
- Confirm the app is not holding the port open in another worker.
- Confirm the step has a non-zero flow and a valid direction.
- Confirm the UI is not skipping the pump because it thinks the pump is offline.

If re-initialization causes problems:

- Make sure the code does not reconnect an already connected pump.
- Make sure the old serial port is closed before opening a new one.
- Make sure startup and manual HW init are not running at the same time.

If the pump display does not show the step comment (see "Pump Display" above - this is a
known unverified area, not necessarily a new bug):

- Confirm the toggle is actually set on the row (icon should tint blue) and that row is the
  one currently applied to hardware, or that the plan has advanced to it since.
- Check the log for a `pump.set_display` line - "OK" vs "failed" tells you whether the
  command reached the pump and was acknowledged.
- If it logs OK but the screen doesn't change, the issue is almost certainly which display
  field/command the real hardware expects, not the app's dispatch logic - re-check the `DA`
  command choice and the `A`/`B` (user-interface control) commands against the manual.

## Files to Read Next

- [`src/lspr_app/device/reglo_icc.py`](../../src/lspr_app/device/reglo_icc.py)
- [`src/lspr_app/domain/pump_plan.py`](../../src/lspr_app/domain/pump_plan.py)
- [`src/lspr_app/gui/experiment_control_window.py`](../../src/lspr_app/gui/experiment_control_window.py)
- [`src/lspr_app/gui/workers.py`](../../src/lspr_app/gui/workers.py)
