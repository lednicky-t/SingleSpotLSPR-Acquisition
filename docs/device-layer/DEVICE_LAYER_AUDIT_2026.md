# Device Layer Audit — singleLSPR Acquisition

**Date:** 2026-06-25  
**Scope:** `apps/sLSPR/acq/src/lspr_app/device/` and related GUI initializer  
**Status:** Fixes in progress

---

## Background

The device layer was audited after reports of intermittent discovery failures — not all devices
being found or connected properly on startup. The layer has a solid structural foundation
(connection registry, serial controller abstraction, profile persistence, V51 numbered labels)
but contains several concrete bugs that directly cause the failures.

---

## Issues — Red (fix now, active failures)

### R1 — `probe_endpoint()` outer claim blocks all inner clients
**File:** `device_manager.py:115`  
**Status:** ✅ fixed

The method wraps everything in `claim_port_context(endpoint, "device_comm:probe")` before calling
inner probe functions. Each inner client (`RegloICCClient`, valve controllers) calls
`try_claim_port(port, <its-own-owner>)`. The registry logic:

```python
if owners and owner_name not in owners:
    return False   # "device_comm:probe" already holds it → inner client gets False
```

Every probe inside `probe_endpoint` fails with "Port is busy" before touching the hardware.
**The method never successfully detects any device type.**

**Fix:** Remove the outer `claim_port_context` from `probe_endpoint`. Inner clients manage
their own claim/release. If concurrent-probe protection is needed, use a separate mutex
keyed on `endpoint` that does not interact with the owner registry.

---

### R2 — `ArduinoValveController.connect()` does not flush input buffer after bootloader sleep
**File:** `valve_controllers.py:43`  
**Status:** ✅ fixed

Opening the serial port asserts DTR, which resets the Arduino bootloader. The 2-second sleep
waits for the sketch to start. `ItsyBitsy32U4ValveController` correctly calls
`self._serial.reset_input_buffer()` after its 3-second sleep. The base
`ArduinoValveController` does not.

Arduino sketches typically print a startup banner during boot. That text sits in the buffer,
so the first `get_probe()` call reads the banner text as the response to `"asn"`, returning
garbage as `protocol_version`.

**Fix:** Add `self._serial.reset_input_buffer()` immediately after `sleep(2.0)`.

---

### R3 — `detect_controller()` ignores `is_probable_port`, tries ItsyBitsy on every valve connect
**File:** `serial_controllers.py:136` — called from `device_manager.py:219`  
**Status:** ✅ fixed

`device_manager.connect()` calls `detect_valve_controller(endpoint)` → `detect_controller(port)`
which iterates ALL registered controllers sorted by priority, regardless of port VID/PID:

```
ItsyBitsy (priority 30) → sleep(3s) → probe fails for non-ItsyBitsy
Arduino   (priority 20) → sleep(2s) → probe succeeds
```

Connecting a known CH340 Arduino valve takes **5+ seconds every time** because ItsyBitsy is
always tried first. The inventory scanner (`_probe_controller_device`) is already smarter —
it uses `_matching_controller_classes()` which respects `is_probable_port()`. The connect
path should do the same.

**Fix:** Filter `registered_controllers()` by `is_probable_port()` inside `detect_controller`.
Fall back to all controllers only if no candidates match (unknown/generic port).

---

### R4 — `ArduinoValveController` has no `is_probable_port` override
**File:** `valve_controllers.py:19`  
**Status:** ✅ fixed

`ItsyBitsy32U4ValveController` correctly narrows to `"ITSYBITSY" / "ADAFRUIT" / "239A"`.
`ArduinoValveController` inherits the base class broad matcher (fires on any USB-serial port).
`LegacyValveController` has an identical broad match.

For any CH340/Arduino port, both `ArduinoValveController` (priority 20) and `LegacyValveController`
(priority 10) match. This means every non-ItsyBitsy USB-serial port is probed by two controllers
on each discovery scan, wasting 2-3 seconds unnecessarily.

**Fix:** Add `is_probable_port` to `ArduinoValveController` targeting standard Arduino/CH340/FTDI
VIDs, explicitly excluding ItsyBitsy (VID `239A`):
```python
@classmethod
def is_probable_port(cls, port: ControllerPort) -> bool:
    description = port.description.upper()
    hwid = port.hwid.upper()
    return (
        "ARDUINO" in description or "CH340" in description or "ATMEGA" in description
        or "2341" in hwid or "1A86" in hwid
    ) and "239A" not in hwid
```

---

## Issues — Orange (fix soon, logic errors)

### O1 — `RegloICCClient.get_probe()` calls `int()` without error handling
**File:** `reglo_icc.py:103`  
**Status:** ✅ fixed

```python
channel_count = int(self.query("0xA"))
```

The pump can respond with `"*"` or `"#"` (status symbols) if the command is rejected or the
pump is in a wrong state. `int("*")` raises `ValueError`, failing the entire probe even though
the three preceding queries already succeeded and identified the device.

Also: `query()` has a dead if/else — both branches return `response` identically.

**Fix:** Wrap in try/except, default to `0` on failure. Remove the dead if branch from `query()`.

---

### O2 — Unknown driver type silently falls through to valve detection
**File:** `device_manager.py:219`  
**Status:** ✅ fixed

`connect()` has three branches: pump, mswitch, and then an unconditional fallback to
`detect_valve_controller(endpoint)`. Any profile with `driver="auto"` or `type="unknown"`
(i.e., all freshly created default profiles) falls through to valve detection. A pump port
probed as a valve hangs for 5+ seconds then either misidentifies or times out.

**Fix:** Raise `ControllerError` explicitly for unknown/unresolvable driver+type combinations
instead of silently falling through.

---

### O3 — Same `_claim_owner` string for all `RegloICCClient` instances
**File:** `reglo_icc.py:49`  
**Status:** ✅ fixed

```python
self._claim_owner = "reglo-icc"
```

`try_claim_port` allows an owner to reclaim their own port. Since all pump clients use the
same owner string, two distinct `RegloICCClient` instances can both claim the same COM port
simultaneously — defeating the registry's exclusive-access guarantee.

**Fix:** Instance-unique owner: `self._claim_owner = f"reglo-icc:{id(self)}"`.

---

## Issues — Yellow (next sprint, performance/design)

### Y1 — `port_assignments.py` reads JSON from disk on every call
**File:** `port_assignments.py:55`  
**Status:** ⬜ pending

`get_port_assignment(port)` calls `_load_port_assignments()` → `load_app_setting()` →
reads and JSON-parses the settings file from disk. During `scan_connected_serial_devices()`,
this is called at least twice per port. With 5 ports that is 10 file reads per scan.

**Fix:** Module-level cache dict invalidated only by `set_port_assignment()`.

---

### Y2 — `ensure_default_profiles()` always creates 6 profiles on every startup
**File:** `device_manager.py:327`  
**Status:** ⬜ pending

Every startup ensures pump_1/pump_2/pump_3 + valve_1/valve_2 + switch_1 exist, all with
`endpoint=None`. Users with only 1 pump and 1 valve see 6 device slots in the UI, making it
hard to distinguish "configured" from "placeholder" devices.

**Fix:** Add a `source="default"` metadata flag to auto-created profiles so the UI can
visually distinguish placeholders from user-configured devices. Or: stop creating pump_2,
pump_3, valve_2 automatically and let the user add them via the device UI.

---

### Y3 — `arduino_valve.py` is orphaned dead code
**File:** `arduino_valve.py`  
**Status:** ⬜ pending

`ArduinoValveClient` predates the `SerialController` framework. It has no connection registry
integration, no `is_probable_port()` logic, and is imported nowhere in the current code path.
The canonical Arduino valve implementation is `valve_controllers.py:ArduinoValveController`.

**Fix:** Remove the file.

---

### Y4 — Duplicate `claim_port` call pattern (redundant, harmless)
**Files:** `reglo_icc.py:85`, `valve_controllers.py:42`  
**Status:** ✅ fixed

Both `connect()` methods call `try_claim_port()` (which already adds the owner to the set)
and then call `claim_port()` again after `serial.Serial()` opens. Since the backing store is
a Python `set`, the duplicate add is idempotent and causes no bug. It is confusing to read.

**Fix:** Remove the second `claim_port()` call from each `connect()` method.

---

## Issues — Blue (roadmap, structural)

### B1 — `HardwareInitResult` assumes one device per type
**File:** `hardware_initializer.py:23`  
**Status:** ⬜ pending

`HardwareInitResult` has `pump_probe: object | None` and `valve_probe: object | None`.
With multiple pumps (pump_1, pump_2, pump_3), the step-result accumulation loop overwrites
the previous probe result each time a second pump step completes. Doesn't fail today because
only one pump step is registered, but will break when multi-pump init steps are added.

**Fix:** Change to `pump_probes: list[object]` / `valve_probes: list[object]`, accumulated
by key prefix rather than exact key match.

---

### B2 — No standalone device test window
**Status:** ⬜ pending (feature)

Discovery and connection currently only work inside the full main-window startup flow.
The architecture supports standalone operation (the device layer has no Qt dependencies aside
from `hardware_initializer.py`), but there is no entry point to open a device manager
panel independently.

**Goal:** A small `DeviceManagerWindow` (or a panel within `hardware_inventory_dialog.py`)
that can:
- Scan for connected devices
- Show port → device type → recognition state
- Assign labels (pump_1, pump_2, valve_1)
- Test communication (send a status query, show raw response)
- Connect / disconnect individual devices
- All without requiring the main acquisition window to be open

This directly enables the user story: reprogramming the ItsyBitsy and verifying its new
commands without running an experiment.

---

## ItsyBitsy firmware upgrade recommendations

The ItsyBitsy 32u4 valve controller currently supports:

| Command | Response | Meaning |
|---------|----------|---------|
| `asn`   | `<string>` | Protocol version |
| `mod`   | `<string>` | Model name |
| `vl`    | (none) | Valve → left/open |
| `vr`    | (none) | Valve → right/close |

Suggested additions for the next firmware revision:

| Command | Response | Benefit |
|---------|----------|---------|
| `uid`   | `<hex-id>` | Persistent board-unique ID. Distinguishes two identical boards; right now both return the same `protocol_version` and `model`. |
| `sta`   | `L` or `R` | Current valve position. The software currently has no feedback on whether the valve actually moved. |
| `ver`   | `<semver>` | Firmware semantic version (separate from protocol revision). |
| `err`   | `<code>` | Last error code for structured diagnostics. |

Separating `asn` (protocol revision) from `ver` (firmware build) is the most important
change: `serial_number` currently duplicates `protocol_version`, which wastes the field.

---

## Fix sequence

1. R2 — Input buffer flush (1 line, zero risk)
2. R4 — `ArduinoValveController.is_probable_port` override (~8 lines)
3. R3 — `detect_controller` port filtering (~10 lines)
4. R1 — Remove outer claim from `probe_endpoint` (~5 lines, highest impact)
5. O1 — Guard `int()` in `get_probe`, remove dead `query()` branch
6. O2 — Unknown driver explicit error
7. O3 — Instance-unique `_claim_owner`
8. Y4 — Remove redundant `claim_port` calls
9. Y1 — Port assignments disk-read cache
10. Y3 — Delete `arduino_valve.py`
11. Y2 — Default profile metadata flag
12. B1 — Multi-device init result
13. B2 — Standalone device window (feature sprint)
