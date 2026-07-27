# Arduino valve controller serial protocol

Applies to the generic Arduino / CH340 controller board driven by
`ArduinoValveController` (`src/lspr_app/device/valve_controllers.py`,
`device_types.SWITCH`, `controller_type = "arduino-valve"`). Shown in the
Hardware devices dialog as "Switch / injection valve (Arduino)".

This board's firmware source is **not** in this repository (unlike the
ItsyBitsy 32u4 controller, whose sketch lives in
`firmware/itsybitsy32u4_valve_controller/`). The commands below were
recovered from a sibling legacy project on this machine,
`LSPR-LCTF_v3.00 - Tomas/main.py`, which talks to the same physical
controller family - confirmed by identical `asn`/`mod` identification
strings and identical `vl`/`vr` valve commands. Treat this file as the
best available protocol reference until the actual firmware source is
located or recovered.

All commands are sent as ASCII text terminated by `\n` at 115200 baud;
the controller responds with a single line terminated by `\n`. This
matches `SerialController.query()`/`_write()` in `serial_controllers.py`.

## Commands wired up in this app's driver

| Command | Response | Driver method |
|---|---|---|
| `asn` | protocol/serial-number string | `get_probe()` |
| `mod` | model string | `get_probe()` |
| `vl` | (none observed) | `set_position("open")` |
| `vr` | (none observed) | `set_position("close")` |
| `at` | ambient temperature, °C, as a plain float string (e.g. `23.4`) | `read_ambient_temperature()` |
| `ah` | ambient humidity, % RH, as a plain float string (e.g. `41.2`) | `read_humidity()` |

`read_ambient_temperature()`/`read_humidity()` are also reachable through
the generic `DeviceCommand` dispatch in `SerialController.execute_command()`
via command types `switch.read_ambient_temperature` /
`valve.read_ambient_temperature` and `switch.read_humidity` /
`valve.read_humidity` (mirrors the existing `switch.set_position` /
`valve.set_position` naming pattern).

Not supported by the ItsyBitsy 32u4 controller (`ItsyBitsy32U4ValveController`)
- its firmware (`firmware/itsybitsy32u4_valve_controller/*.ino`) has no
sensor code at all. Calling either method on that controller raises
`ControllerError` immediately instead of sending a doomed query.

## Automatic polling and storage

`MainWindow._environment_poll_timer` (`gui/main_window.py`) polls both values
every 60 s via `DeviceEnvironmentReadTask` (`gui/device_lifecycle_task.py`),
whenever the Switch device is connected - a no-op, no device I/O at all, if
it isn't. Readings are written to the `/devices/environment` group (see
`docs/measurement_file_format.md`) of whichever HDF5 writer(s) are currently
active (the always-on session file, and the named measurement file too if a
recording is in progress), via `handle_environment_reading()` /
`poll_environment_sensors()` in `gui/acquisition_controller.py`. If the
connected controller doesn't support these commands (ItsyBitsy/Legacy), the
poll simply yields nothing to write - no error, no log spam.

## Commands seen in the legacy app but not yet used here

Recovered from the same `main.py`, for reference if any of this is needed
later. None of these are implemented in `ArduinoValveController` today.

| Command | Response | Purpose (from `main.py`) |
|---|---|---|
| `c{n}o` (e.g. `c1o`) | - | turn channel `n` on |
| `c{n}f` (e.g. `c1f`) | - | turn channel `n` off |
| `ls` | lamp/light-source intensity, plain float string | `Lamp_intensity()` |
| `wf` | filter stability/intensity, plain float string | `Filter_intensity()` |
| `d` (single byte, no `\n`) | - | sent on disconnect, before closing the port |
