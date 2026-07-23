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

## Files to Read Next

- [`src/lspr_app/device/reglo_icc.py`](../../src/lspr_app/device/reglo_icc.py)
- [`src/lspr_app/domain/pump_plan.py`](../../src/lspr_app/domain/pump_plan.py)
- [`src/lspr_app/gui/experiment_control_window.py`](../../src/lspr_app/gui/experiment_control_window.py)
- [`src/lspr_app/gui/workers.py`](../../src/lspr_app/gui/workers.py)
