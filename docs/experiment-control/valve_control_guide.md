# Valve Control Guide for SeaSharp

This document explains how the valve control code in LSPR is structured and how to extend or reimplement it safely.

It is intended for both humans and AI agents that need to build or maintain valve control logic.

## Scope

- Valve controllers: Arduino-compatible serial valve, ItsyBitsy 32u4 valve, and legacy multi-channel valve controller
- Main implementation file: [`src/lspr_app/device/valve_controllers.py`](../../src/lspr_app/device/valve_controllers.py)
- GUI integration: [`src/lspr_app/gui/experiment_control_window.py`](../../src/lspr_app/gui/experiment_control_window.py)
- Startup detection: [`src/lspr_app/gui/main_window.py`](../../src/lspr_app/gui/main_window.py)

## High-Level Design

The valve control stack has three layers:

1. Device layer
   - Owns the serial protocol or vendor API.
   - Opens and closes the controller.
   - Sends open/close commands.
   - Probes the device identity and model.

2. Experiment-control layer
   - Reads the current experiment-plan step.
   - Applies the requested valve state only when the step changes.
   - Skips the valve entirely if it is not connected.

3. Startup/discovery layer
   - Scans serial ports for a likely valve controller.
   - Probes the controller with identity commands.
   - Connects the live valve controller only after discovery.

## Core Data Model

Valve discovery and probing use `ControllerProbe` from the shared controller layer.

Useful fields:

- `port`
- `controller_type`
- `model`
- `serial_number`
- `protocol_version`

## Controller Types

### `ArduinoValveController`

This is the generic serial valve controller.

Serial settings:

- baudrate: `115200`
- bytes: `8`
- parity: `N`
- stop bits: `1`
- timeout: `0.35 s`

Startup behavior:

- The port is opened directly with `serial.Serial(...)`.
- The controller sleeps for `2.0 s` after connect.
- The valve state is not changed automatically beyond the explicit command that is sent.

Probe commands:

- `asn`
- `mod`

Position commands:

- open / left -> `vl`
- close / right -> `vr`

### `ItsyBitsy32U4ValveController`

This is the preferred modern ItsyBitsy 32u4 valve controller.

Serial settings:

- baudrate: `115200`
- bytes: `8`
- parity: `N`
- stop bits: `1`
- timeout: `1.0 s`

Startup behavior:

- The port is opened directly with `serial.Serial(...)`.
- The controller sleeps for `3.0 s` after connect.
- The input buffer is reset after the wait.

Probe commands:

- `asn`
- `mod`

Position commands:

- open / left -> `vl`
- close / right -> `vr`

Important note:

- The hardware sketch should agree with the active valve polarity.
- In the current app, the valve command mapping is the protocol layer, not the GUI.

### `LegacyValveController`

This is the older multi-channel valve controller path.

Serial settings:

- baudrate: `9600`
- bytes: `8`
- parity: `N`
- stop bits: `1`
- timeout: `1.0 s`

Startup behavior:

- The port is opened with legacy serial settings.
- The controller sleeps for `0.5 s`.
- The controller is probed with `vi`.

Probe commands:

- `vi`

Position commands:

- open -> enable all channels
- close -> disable all channels

Low-level channel commands:

- `ve{channel}` -> enable channel
- `va{channel}` -> disable channel
- `va0` -> stop/off for the legacy controller family

## Discovery Flow

Valve discovery is implemented in:

- `detect_valve_controller(port)` in [`src/lspr_app/device/valve_controllers.py`](../../src/lspr_app/device/valve_controllers.py)

The discovery path generally does this:

1. Enumerate serial ports.
2. Rank ports using the controller registry.
3. Open the most likely controller type.
4. Probe it with `asn`, `mod`, or legacy `vi`.
5. Return the connected controller and a `ControllerProbe`.

## Command Model

The valve is driven with short commands.

### Current protocol

- `vl`
  - open / left
- `vr`
  - close / right
- `va0`
  - off / stop for the legacy controller family

### Supported aliases

The high-level API accepts several aliases:

- open: `open`, `o`, `left`, `l`
- close: `close`, `c`, `right`, `r`

The legacy controller also accepts:

- on / off
- true / false
- 1 / 0

## Important Safety Rules

These are the rules the app currently follows and new code should preserve them:

- Never send a valve command unless the valve client is connected.
- Do not treat disconnect as a valve movement command.
- Keep the current valve position on shutdown unless a specific safe position is required.
- If the valve is already connected, a re-initialization pass should not reconnect it unnecessarily.
- The experiment plan should skip the valve independently if it is disconnected.

## Current UI / Flow Integration

Valve control is not isolated in a separate GUI module yet.

The experiment-control window is responsible for:

- connecting the valve client
- applying step-based valve changes
- skipping the valve when it is offline
- leaving the valve in place during shutdown

Key methods in:

- [`src/lspr_app/gui/experiment_control_window.py`](../../src/lspr_app/gui/experiment_control_window.py)

Important methods:

- `connect_best_valve_controller()`
- `_connect_selected_valve_port()`
- `_disconnect_valve()`
- `_apply_step_to_pump_async()`
- `shutdown_devices()`

## Flow-Plan Execution Logic

When a flow step is applied, the code should:

1. Read the selected or active step.
2. Compare the requested valve state with the previously applied state.
3. Send the valve command only when the state actually changes.
4. Skip the valve entirely if it is disconnected.

This means the valve is step-driven, not continuously re-sent every cycle.

## Startup Behavior

The app uses a staged startup flow:

1. Discover hardware in a worker.
2. Create the flow panel.
3. Connect the valve controller in the startup path.
4. Keep the splash screen open until startup is ready.

Manual re-initialization from the HW menu remains separate and should be treated as a user-triggered rescan/reconnect.

## Recommended Implementation Pattern

If you are writing new valve-control code, prefer this pattern:

```python
client, probe = detect_valve_controller("COM14")
if client.is_connected():
    client.set_position("open")

# Later
client.set_position("close")
client.close()
```

For experiment-plan execution, use the shared step-application logic instead of sending raw commands from the table editor.

## Troubleshooting Checklist

If the valve does not move:

- Confirm the port is correct.
- Confirm the valve is connected in the live client, not just detected.
- Confirm the app is not trying to open the same COM port from another worker.
- Confirm the step requests a different valve state than the previous one.
- Confirm the firmware accepts the same command mapping as the app.

If re-initialization causes problems:

- Make sure the code does not reconnect an already connected valve.
- Make sure the old serial port is closed before opening a new one.
- Make sure startup and manual HW init are not running at the same time.

## Files to Read Next

- [`src/lspr_app/device/valve_controllers.py`](../../src/lspr_app/device/valve_controllers.py)
- [`src/lspr_app/gui/experiment_control_window.py`](../../src/lspr_app/gui/experiment_control_window.py)
- [`src/lspr_app/gui/main_window.py`](../../src/lspr_app/gui/main_window.py)
