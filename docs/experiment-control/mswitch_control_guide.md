# Switch Rotary Valve Control Guide for SeaSharp

This document explains how the switch rotary valve control code in LSPR is structured and how to extend or reimplement it safely.

It is intended for both humans and AI agents that need to build or maintain switch rotary valve control logic.

## Scope

- Device family: AMF RVM Switch Rotary Valve. Code, file, and method names still say
  "M-Switch"/"mswitch"/"selector" (not renamed yet - see the naming note at the end of this
  section); this guide uses "switch rotary valve" as the unified user-facing term.
- Main implementation file: [`src/lspr_app/device/amf_mswitch.py`](../../src/lspr_app/device/amf_mswitch.py)
- GUI integration: [`src/lspr_app/gui/experiment_control_window.py`](../../src/lspr_app/gui/experiment_control_window.py)
- Startup orchestration: [`src/lspr_app/gui/main_window.py`](../../src/lspr_app/gui/main_window.py)

**Naming note (2026-07-23):** user-facing text (status messages, tooltips, the titlebar status
chip) was unified to say "Switch Rotary Valve" instead of the previous "M-Switch". Internal Python
identifiers (`amf_mswitch.py`, `AMFSwitchController`, `SELECTOR`, `_connect_selected_mswitch_port()`,
etc.) were deliberately left unchanged for now - renaming those is a much bigger, riskier change
for no functional benefit on its own, and it will likely need to happen again anyway once the
switch/distribution split below is actually implemented. **TODO: this still needs real work**, not
just naming - see "Hardware Topology" immediately below for what's actually missing.

## Hardware Topology: AMF Valve Types (Distribution vs. On/off vs. Switch)

**This section documents a real gap between the current software model and at least some AMF
valve hardware. Read this before assuming "port count" and "logical position count" are the same
number.**

Source: [AMF technical note - "What is a Microfluidic Switch Rotary Valve?"](https://amf.ch/technical-note/what-is-a-microfluidic-switch-rotary-valve-key-features-and-benefits/)
(accessed 2026-07-23).

### AMF valve part-number nomenclature

AMF valve model codes follow the pattern `V-A-B-C-D-E-F`, e.g. `V-S-1-4-050-C-P`:

| Field | Meaning | Values |
|-------|---------|--------|
| `V` | Valve (fixed) | - |
| `A` | Type | `D` = Distribution, `O` = On/off, `S` = Switch |
| `B` | Number of stages | integer |
| `C` | Number of radial ports | integer |
| `D` | Channel diameter | e.g. `050` = 0.5 mm, `100` = 1 mm |
| `E` | Stator material | `C` = PCTFE, `K` = PEEK |
| `F` | Rotor material | `P` = PTFE, `U` = UHMW-PE |

AMF's own published examples: `V-S-1-4-050-C-P` (4-port switch valve) and `V-S-1-6-050-C-P`
(6-port switch valve).

**The `A` field is the important one for control-code purposes** - it tells you which topology a
given physical unit actually implements. This app's code and this guide's "Position and Homing
Rules" section below were written assuming type `D` (Distribution/selector). Type `S` (Switch)
behaves differently - see next section.

### Distribution ("selector") valves vs. switch valves

- **Distribution (`D`) / selector valve**: one common port, individually routed to any one of `C`
  radial ports. Each of the `C` ports is its own independent, directly addressable logical
  position. This is the model `amf_mswitch.py`, the experiment-control GUI, and the plan/HDF5
  schema (`switch_position`, clamped to 1-12 in multiple places, e.g.
  [`src/lspr_app/domain/pump_plan.py`](../../src/lspr_app/domain/pump_plan.py)) all currently
  assume.
- **Switch (`S`) valve**: per AMF, "the design includes 4 or 6 (or even more) radial ports that
  are connected 2 by 2." A 4-port switch valve has "4 inputs and 2 interchangeable
  configurations"; a 6-port switch valve has "6 inputs and 3 interchangeable configurations." In
  other words: **the number of usable logical positions on a switch valve is half its physical
  port count**, because ports are wired in pairs and the rotor only ever selects which *pairing*
  is active, not an arbitrary single port. A 12-port switch valve (if such a thing exists in this
  product line) would have 6 logical positions, not 12.

### What this means for this codebase

If your physical switch rotary valve is a type `S` (switch) valve rather than type `D` (distribution), the
current software is wrong in a specific, quantifiable way: it lets you pick any of up to 12 raw
port numbers as if each were an independent state (`_switch_solution_labels`, one independently
nameable label per number, in
[`src/lspr_app/gui/experiment_control_window.py`](../../src/lspr_app/gui/experiment_control_window.py)),
when the hardware may only support half that many real, distinct flow configurations. Nothing in
the code currently reads the valve's type code or reconciles the port-pair structure - `move_to()`
always sends a raw 1-based port number straight to `valveShortestPath()`.

To find out which type your unit is: check the model/part number printed on the device or in its
AMF paperwork against the `V-A-B-C-D-E-F` pattern above (the `A` field is what matters), or query
`get_port_count()` (already implemented but currently unused by the app - see
[`tools/amf_manual_move.py`](../../tools/amf_manual_move.py) for a script that calls it) and check
whether the reported count matches what's printed on the unit or divide-by-two if it doesn't.

## High-Level Design

The switch rotary valve control stack has three layers:

1. Device layer
   - Talks to the proprietary AMFTools API.
   - Opens and closes the switch controller.
   - Reads position, port count, and homing state.
   - Moves the valve to a target port.

2. Experiment-control layer
   - Reads the current experiment-plan step.
   - Applies the requested switch position when the step changes.
   - Skips the switch rotary valve entirely if it is not connected.

3. Startup/discovery layer
   - Scans AMFTools product listings.
   - Filters for RVM-family devices.
   - Connects the live controller only after discovery.

## Dependency Model

The switch rotary valve code depends on the optional proprietary `amfTools` package.

Install it in the active virtual environment before using switch rotary valve control:

```powershell
python -m pip install AMFTools
```

If `amfTools` is not installed:

- discovery returns no devices
- connect raises `ControllerError("AMFTools library is not installed.")`

This makes the dependency optional at import time but required for actual use.

## Core Data Model

Switch rotary valve discovery and probing use `ControllerProbe` from the shared controller layer.

Useful fields:

- `port`
- `controller_type`
- `model`
- `serial_number`
- `protocol_version`

## Discovery Flow

Switch rotary valve discovery is implemented in:

- `detect_amf_mswitch_devices()` in [`src/lspr_app/device/amf_mswitch.py`](../../src/lspr_app/device/amf_mswitch.py)

The discovery path generally does this:

1. Call `amfTools.util.getProductList()`
2. Filter products whose family or type contains `RVM`
3. Extract the COM port
4. Build a `ControllerProbe` for each unique port

Important detail:

- The scanner suppresses stdout and stderr while calling AMFTools, so vendor noise does not flood the GUI log.

## Connection Flow

`AMFSwitchController.connect(port)` does the following:

1. Confirms `amfTools` is installed.
2. Closes any prior controller instance.
3. Creates `amfTools.AMF(port)`.
4. Reads back the resolved serial port.
5. Raises `ControllerError` if the vendor API fails.

This is not a serial-protocol wrapper in the same way as the pump or valve.
It is a vendor API wrapper around the switch rotary valve hardware.

## Device Query Methods

`AMFSwitchController` exposes these runtime methods:

- `get_probe()`
  - reads device information from AMFTools
- `get_position()`
  - returns the current valve position
- `get_port_count()`
  - returns the number of available ports
- `is_homed()`
  - returns whether the switch reports it is homed
- `home(block=True)`
  - homes the switch
- `move_to(target, block=True)`
  - moves the switch to a target port

## Position and Homing Rules

The most important runtime rule for switch rotary valve control is homing.

If the device is not homed:

- moves can fail
- startup can look connected but still be unusable
- the experiment plan may report `Device not initialized` or `Not homed`

New code should therefore preserve these rules:

- check the homing state before the first move
- home only when the controller is being initialized
- do not home repeatedly during every step if the device is already ready

## Command / Operation Model

The switch rotary valve does not use short ASCII commands like the pump and valve.
Instead, it uses the AMFTools API methods.

Typical operations:

- `home(block=True)`
- `move_to(port, block=True)`

The code should treat the target port as a 1-based position.

## Important Safety Rules

These are the rules the app currently follows and new code should preserve them:

- Never send a switch rotary valve move unless the controller is connected.
- Do not treat disconnect as a switch rotary valve motion command.
- Check whether the controller is homed before the first move.
- If the switch rotary valve is already connected, a re-initialization pass should not reconnect it unnecessarily.
- The experiment plan should skip the switch rotary valve independently if it is disconnected.

## Current UI / Flow Integration

Switch rotary valve control is not isolated in a separate GUI module yet.

The experiment-control window is responsible for:

- connecting the switch rotary valve client
- applying step-based port changes
- checking homing state during initialization
- skipping the switch rotary valve when it is offline

Key methods in:

- [`src/lspr_app/gui/experiment_control_window.py`](../../src/lspr_app/gui/experiment_control_window.py)

Important methods:

- `connect_best_mswitch_controller()`
- `_connect_selected_mswitch_port()`
- `_disconnect_mswitch()`
- `_ensure_mswitch_homed()`
- `_apply_step_to_pump_async()`
- `shutdown_devices()`

## Flow-Plan Execution Logic

When a flow step is applied, the code should:

1. Read the selected or active step.
2. Determine the requested switch rotary valve port.
3. Send the move command only if the switch rotary valve is connected.
4. Ensure homing has already happened during initialization.
5. Skip the switch rotary valve entirely if it is disconnected.

The plan runner should not block the other devices just because the switch rotary valve is missing.

## Startup Behavior

The app uses a staged startup flow:

1. Discover hardware in a worker.
2. Create the flow panel.
3. Connect the switch rotary valve in the startup path.
4. Home it during initialization if required.
5. Keep the splash screen open until startup is ready.

Manual re-initialization from the HW menu remains separate and should be treated as a user-triggered rescan/reconnect.

## Recommended Implementation Pattern

If you are writing new switch rotary valve control code, prefer this pattern:

```python
controller = AMFSwitchController()
controller.connect("COM10")

if not controller.is_homed():
    controller.home(block=True)

controller.move_to(3, block=True)

# Later
controller.close()
```

For experiment-plan execution, use the shared step-application logic instead of moving the switch directly from the table editor.

## Troubleshooting Checklist

If the switch rotary valve does not move:

- Confirm `amfTools` is installed and importable.
- Confirm the port is correct.
- Confirm the controller is connected in the live client, not just detected.
- Confirm the device is homed.
- Confirm the target port is within the device port range.
- Confirm the app is not trying to open the same port from another worker.

If startup detection is strange:

- Make sure startup connect is not racing a manual HW initialization.
- Make sure the startup path is not reconnecting an already connected controller.
- Make sure the cached probe state is not being mistaken for live connection state.

If you see `Device not initialized` or `Not homed`:

- run the homing step during initialization
- then retry the move

## Files to Read Next

- [`src/lspr_app/device/amf_mswitch.py`](../../src/lspr_app/device/amf_mswitch.py)
- [`src/lspr_app/gui/experiment_control_window.py`](../../src/lspr_app/gui/experiment_control_window.py)
- [`src/lspr_app/gui/main_window.py`](../../src/lspr_app/gui/main_window.py)
