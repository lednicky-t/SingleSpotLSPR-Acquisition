# M-Switch Control Guide for SeaSharp

This document explains how the M-switch control code in LSPR is structured and how to extend or reimplement it safely.

It is intended for both humans and AI agents that need to build or maintain M-switch control logic.

## Scope

- Device family: AMF RVM M-switch
- Main implementation file: [`src/lspr_app/device/amf_mswitch.py`](../../src/lspr_app/device/amf_mswitch.py)
- GUI integration: [`src/lspr_app/gui/experiment_control_window.py`](../../src/lspr_app/gui/experiment_control_window.py)
- Startup orchestration: [`src/lspr_app/gui/main_window.py`](../../src/lspr_app/gui/main_window.py)

## High-Level Design

The M-switch control stack has three layers:

1. Device layer
   - Talks to the proprietary AMFTools API.
   - Opens and closes the switch controller.
   - Reads position, port count, and homing state.
   - Moves the valve to a target port.

2. Experiment-control layer
   - Reads the current experiment-plan step.
   - Applies the requested switch position when the step changes.
   - Skips the M-switch entirely if it is not connected.

3. Startup/discovery layer
   - Scans AMFTools product listings.
   - Filters for RVM-family devices.
   - Connects the live controller only after discovery.

## Dependency Model

The M-switch code depends on the optional proprietary `amfTools` package.

Install it in the active virtual environment before using M-Switch control:

```powershell
python -m pip install AMFTools
```

If `amfTools` is not installed:

- discovery returns no devices
- connect raises `ControllerError("AMFTools library is not installed.")`

This makes the dependency optional at import time but required for actual use.

## Core Data Model

M-switch discovery and probing use `ControllerProbe` from the shared controller layer.

Useful fields:

- `port`
- `controller_type`
- `model`
- `serial_number`
- `protocol_version`

## Discovery Flow

M-switch discovery is implemented in:

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
It is a vendor API wrapper around the M-switch hardware.

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

The most important runtime rule for M-switch control is homing.

If the device is not homed:

- moves can fail
- startup can look connected but still be unusable
- the experiment plan may report `Device not initialized` or `Not homed`

New code should therefore preserve these rules:

- check the homing state before the first move
- home only when the controller is being initialized
- do not home repeatedly during every step if the device is already ready

## Command / Operation Model

The M-switch does not use short ASCII commands like the pump and valve.
Instead, it uses the AMFTools API methods.

Typical operations:

- `home(block=True)`
- `move_to(port, block=True)`

The code should treat the target port as a 1-based position.

## Important Safety Rules

These are the rules the app currently follows and new code should preserve them:

- Never send an M-switch move unless the controller is connected.
- Do not treat disconnect as an M-switch motion command.
- Check whether the controller is homed before the first move.
- If the M-switch is already connected, a re-initialization pass should not reconnect it unnecessarily.
- The experiment plan should skip the M-switch independently if it is disconnected.

## Current UI / Flow Integration

M-switch control is not isolated in a separate GUI module yet.

The experiment-control window is responsible for:

- connecting the M-switch client
- applying step-based port changes
- checking homing state during initialization
- skipping the M-switch when it is offline

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
2. Determine the requested M-switch port.
3. Send the move command only if the M-switch is connected.
4. Ensure homing has already happened during initialization.
5. Skip the M-switch entirely if it is disconnected.

The plan runner should not block the other devices just because the M-switch is missing.

## Startup Behavior

The app uses a staged startup flow:

1. Discover hardware in a worker.
2. Create the flow panel.
3. Connect the M-switch in the startup path.
4. Home it during initialization if required.
5. Keep the splash screen open until startup is ready.

Manual re-initialization from the HW menu remains separate and should be treated as a user-triggered rescan/reconnect.

## Recommended Implementation Pattern

If you are writing new M-switch control code, prefer this pattern:

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

If the M-switch does not move:

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
