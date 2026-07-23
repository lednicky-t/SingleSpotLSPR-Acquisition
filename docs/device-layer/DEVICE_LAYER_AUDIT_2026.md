# Device Layer Audit — singleLSPR Acquisition

**Date:** 2026-06-25 (follow-up pass: 2026-07-20; full rewrite: 2026-07-20; Device Manager
migration + simulated-device discussion + orphaned-widget deletion: 2026-07-21; whole-subsystem
audit: 2026-07-21; B4 lock split: 2026-07-22)
**Scope:** `apps/sLSPR/acq/src/lspr_app/device/` and related GUI initializer
**Status:** Discovery/connect architecture rewritten — see "2026-07-20: full device
lifecycle rewrite" below. `DeviceManagerDialog`'s manual Connect/Disconnect for the
canonical pump/valve/selector now also routes through the same owner (2026-07-21).
The orphaned, never-laid-out connect widgets in `experiment_control_window.py` have been
deleted (2026-07-21). A whole-subsystem audit (2026-07-21, see below) found and fixed a real
unlocked-hardware-I/O gap in `probe_endpoint()` and a port-claim identity bug shared by the
AMF and generic serial-controller drivers. A follow-up report ("selector homes but doesn't
react during a running plan") traced to `_apply_step_to_pump_async` dispatching real device
commands, including `switch.move_to`, on the general-purpose `QThreadPool` instead of
`device_io_pool()` — fixed 2026-07-21. A further report ("UI freezes during device
initialization") traced to GUI-thread status-display code sharing a lock with the slow real
connect/probe calls happening on `device_io_pool` — fixed 2026-07-21 with a non-blocking
cached-status read, see below. **B4 (below) implemented 2026-07-22** — `DeviceCommunicationService`
now has a separate fast state lock for `is_connected`/`status`/`list_statuses`/`connection`/
`list_profiles`/`get_profile`/`list_events`, so those never block behind a slow connect/
disconnect/command/probe/discover on any device; the non-blocking cached-status read from the
prior fix stays in place as a separate, still-useful optimization (it avoids taking any lock at
all, not just the slow one). Remaining open items: `DeviceManagerDialog`'s
"Scan && connect" button (still a separate discovery/connect path), B2 (standalone device
window - `hardware_inventory.py` appears to be pre-built, unwired scaffolding for this),
B3 (fast reconnect, low priority). Simulated pump/valve/selector devices ("Part B")
were discussed and explicitly dropped — see below.

---

## Background

The device layer was audited after reports of intermittent discovery failures — not all devices
being found or connected properly on startup. The layer has a solid structural foundation
(connection registry, serial controller abstraction, profile persistence, V51 numbered labels)
but contains several concrete bugs that directly cause the failures.

**2026-07-20 follow-up:** a real startup log showed pump and valve failing to connect
("Port COM12 is busy.", a PermissionError on the wrong port) while spectrometer and selector
connected fine, plus a multi-second GUI freeze at startup. See R5, R6, O4 below — root-caused
from that log and fixed the same day.

**2026-07-20, later the same day:** R5/R6/R7/R8/O4 were each individually real fixes, but they
were all symptoms of one underlying structural problem — see the rewrite summary below, which
replaced the two-system architecture these items were built around.

---

## 2026-07-20: full device lifecycle rewrite

Every fix in this document up to R8 was a patch on a seam between **two independent systems**
that both touched the same physical devices without a shared model: a background discovery scan
(`hardware_initializer.py` + `main_window_hardware_scan.py`) and `experiment_control_window.py`'s
own port scan + startup auto-connect state machine + connect/disconnect handlers. That duplication
was the actual root cause behind R5 (double-connect races), R7 (an AMF vendor-SDK thread-safety
hang), and R8 (stale port pins) — and it caused a fourth incident the same day: the selector would
connect and home successfully, then silently stop responding to move commands, because a proactive
health check (`synchronize_device_connections`, gated only during the startup scan) kept running
for the rest of the session and could silently disconnect a perfectly healthy selector on a flaky
port-enumeration mismatch.

Rather than patch a fifth instance of the same bug class, the discovery/connect architecture was
rewritten so there is exactly one owner: **`device/device_lifecycle.py`'s `DeviceLifecycleController`**
(pure Python, no Qt, fully unit-tested) plus a thin Qt wrapper (`gui/device_lifecycle_task.py`,
a single-lane `QThreadPool` so no two device operations can ever run concurrently). It owns
discovery, connect, disconnect, and post-connect setup (the selector's homing, now a pluggable
hook rather than a hardcoded special case) for spectrometer/pump/valve/selector — for both the
automatic startup cycle and the manual Connect/Disconnect/Home buttons, which now call the exact
same code instead of three near-duplicated per-device implementations.

**Deleted entirely** (their function is now owned by the controller): `gui/hardware_initializer.py`,
`gui/main_window_hardware_scan.py`, `gui/experiment_control_connection.py`, the startup
auto-connect state machine and three dead `_auto_connect_*` functions in
`experiment_control_window.py`, and `synchronize_device_connections` (the selector-bug root cause
— replaced with nothing, since the reactive behavior it existed for already happens for free when
a real command to a genuinely-gone device fails).

**Superseded-by-design** (the fix still stands, but the code it fixed no longer exists in its
original form): R1, R5, R6, R7, R8, O4, B1. Their write-ups above are kept for incident history —
each one is a real bug that really happened and explains *why* the rewrite has the shape it has
(e.g. the single-lane thread pool exists specifically because of R7; the "connect attempts must
not mark_manual" rule from R8 is now enforced structurally through `ensure_device_profile()`, the
one function every connect path calls).

**Also fixed as part of the rewrite, found by testing on real hardware, not by inspection:**
`ExperimentControlWindow.__init__` (and one bootstrap-finalize method) still called the
just-deleted state-machine entry point, which crashed panel construction on every launch until
caught by actually running the app rather than trusting the test suite alone.

Not fully re-verified against real hardware end-to-end (pump/valve connected correctly on this
machine's real devices during testing; the selector's homing failure seen during testing was a
genuine hardware "missing main reference" magnet/motor issue, unrelated to this rewrite) — real
button clicks (Connect/Disconnect/Home) still need first-hand confirmation, since there's no way
to drive a live Qt GUI from this environment.

---

## 2026-07-21: Device Manager migration, orphaned widgets found, simulated devices dropped

**`experiment_control_window.py`'s own pump/valve/selector connect widgets were dead code —
now deleted (2026-07-21).** While trying to point the maintainer at the Connect/Disconnect/Home
buttons for real-hardware click-testing, a full grep of this file's entire git history turned up
zero `addWidget`/`addRow` calls ever placing `port_combo`, `connection_toggle_button`,
`mswitch_home_button`, etc. into a layout. They were constructed with `self` as parent and
visibility-toggled by the `devices_enabled` capability, but never actually laid out — Qt just left
them unpositioned and invisible behind other widgets. This predated the full rewrite (confirmed via
`git log -S` across the file's whole history) — the rewrite touched connect *logic*, not
`_build_ui`'s layout code, so nothing broke there. The maintainer asked for this dead code to be
resolved; since `DeviceManagerDialog` now fully covers manual connect/disconnect (see below), it
was deleted rather than wired up. See the "orphaned connect widgets deleted" entry below for what
was actually removed and the two live pieces of logic that turned out to be tangled in with it.

**The real manual-connect UI is `DeviceManagerDialog`** (Hardware menu → "Device Manager…",
`device_console_dialog.py`), and until today it called `DeviceCommunicationService.connect`/
`disconnect` directly — a *third* independent system touching the same devices, bypassing
`DeviceLifecycleController` entirely (no post-connect hook, so selector homing wouldn't fire on a
manual reconnect there; no single-lane pool protection, reopening the AMF thread-safety risk R7
fixed, just via a different door). Fixed: `_connect_selected_profile`/`_disconnect_selected_profile`
(Connected devices tab) and their Profiles-tab equivalents now check whether the selected label is
one of the three canonical labels (`pump_1`/`switch_1`/`selector_1`, from `device_label_for()`); if
so, they dispatch `DeviceConnectTask`/`DeviceDisconnectTask` on `device_io_pool()` — the same path
the startup cycle uses — instead of calling the service directly. Non-canonical profiles (ad hoc
ones from the Probe/assign tab) keep the old direct-service behavior unchanged; that generic
multi-profile diagnostic tooling is legitimately outside the single-owner model. **Not migrated**:
the same dialog's "Scan && connect" button (`_DiscoverTask`) still discovers and connects
independently, and registers its own profile labels rather than the canonical ones — a real,
still-open gap, just not tackled yet.

**Simulated pump/valve/selector devices ("Part B") — discussed, dropped.** The original ask was to
virtualize serial ports so device communication could be tested without real hardware. Ruled out:
Windows has no built-in virtual COM-port pairing (`com0com` needs an unsigned kernel driver +
admin rights), and the AMF selector bypasses pyserial entirely (talks straight to the vendor SDK),
so no port-level virtualization could cover it anyway. The fallback design considered — object-level
fake drivers mirroring `SimulatedSpectrometer`, implementing the same `DeviceDriver` ABC — was
rejected once discussed: it would only exercise `DeviceLifecycleController`'s orchestration (already
unit-tested), not the actual command-encoding/response-parsing code in `RegloICCClient`/
`SerialController`, which is what the maintainer actually wanted verified. A narrower alternative
(a `FakeSerial` test double at the `pyserial` boundary, so pytest could drive the *real* encode/parse
code without a real port — still selector-incompatible, same SDK reason) was offered but also
declined; the maintainer judged it unnecessary now that the actual selector bug turned out to be
architectural (the deleted health-check race), not a protocol bug. No simulated-device work is
planned; do not re-propose this without the maintainer raising it again.

---

## 2026-07-21: orphaned connect widgets deleted from experiment_control_window.py

Deleted, since `DeviceManagerDialog` (see above) is now the only reachable manual connect UI:
the pump/valve/selector port combo boxes, Connect/Disconnect/Refresh/Home/Move/"i" buttons and
their status-dot/detail-label widgets; the generic `_toggle_device_connection`/`_connect_device`/
`_handle_device_connect_finished`/`_disconnect_device` dispatch built in Phase 3 (never reachable
from any of those widgets); the per-device `_toggle_*`/`_disconnect_*`/`_set_*_connection_visual`
wrappers; `_populate_ports`/`_populate_valve_ports`/`_populate_mswitch_ports` and the port-refresh
task machinery that fed them (`_start_port_refresh`, `_handle_port_refresh_finished/_failed`,
`_refresh_ports`/`_refresh_valve_ports`/`_refresh_mswitch_ports` and the `_port_refresh_*` state);
`_ensure_device_profile`'s window-level wrapper (the real implementation in `device_lifecycle.py`
is untouched); `_apply_probe`/`_clear_probe_labels`; the "remember last selected port" persistence
in `save_ui_state`/`_restore_ui_state` (meaningless without a combo to pre-select). Roughly 500
lines removed. `tests/unit/test_device_connections.py` was cut down to just the two still-live
helpers it covered (`_device_label_for`, `_service_device_connected`) — 15 tests that covered the
deleted dispatch logic were removed with it, all now-redundant with `test_device_lifecycle.py` and
`DeviceManagerDialog`'s own migration tests.

**Two pieces of genuinely live logic were tangled in with the dead widgets and had to be
untangled rather than deleted along with them** — both found only by grepping for every remaining
reference after the first pass, not by inspection:
1. `connection_status_label` is not a "pump status" label — reading `_set_status_message`'s ~40
   call sites (plan run/pause/stop/step-select/import/export feedback, throughout the file) showed
   it's the *entire panel's* shared status line, just never laid out either. Kept, along with
   `_set_status_message`/`_refresh_status_line`/`_status_message_base`; only the truly pump-specific
   half of `_set_connection_visual` (the dead status-dot and dead Connect/Disconnect button text)
   was trimmed out — it still updates the real status line on every pump connect/disconnect.
2. `_update_mswitch_state_from_probe` looked purely decorative (it fed the deleted
   `mswitch_target_spin`/`mswitch_current_value` widgets) and was deleted in the first pass — but it
   is also called from `_apply_step_to_pump`/`_on_step_apply_async_done`, the real experiment-plan
   step-execution path, after every switch-move command during a running plan. Restored with the
   widget-writes stripped, keeping the live `switch.get_position` verification query and its
   warning-on-failure log. Caught by an exhaustive dangling-reference grep before running tests —
   the unit test suite would not have caught this (it mocks `self`, so a real `AttributeError`
   several calls deep in `_apply_step_to_pump` was invisible to it), same lesson as the Phase 3
   `__init__` crash noted above. A real Qt construction + a real offscreen app launch
   (`LSPR_ACQ_LAUNCH_PROFILE=control_editor`) were both run clean afterward as an additional check.

Also fixed as part of the same pass: `_build_native_experiment_plan_document` read the pump's model
name from `self.model_value.text()` (the now-deleted label) for the exported plan file's device
metadata; repointed to `self._probe.model` directly, which is the actual live source that label was
mirroring.

---

## 2026-07-21: whole-subsystem audit (device/ package end to end)

Requested explicitly: "check every device part as the whole" rather than just the day's diff.
Went through every file in `device/`, tracing interactions between the port-claim registry,
each driver, and `DeviceCommunicationService`. Two real, previously-unnoticed bugs found and
fixed; two dead-code surfaces found and left alone (flagged, not deleted, since unlike the
orphaned widgets these look like intentional unwired scaffolding, not abandoned work).

**`probe_endpoint()` had no lock — a live, GUI-reachable instance of exactly the bug class this
whole rewrite exists to prevent.** Every other public method that touches real hardware
(`connect`, `disconnect`, `send_command`, `refresh_device_ports`) is wrapped in
`self._device_lock(...)`; `probe_endpoint()` was not, despite doing real serial/AMF-SDK I/O
(`RegloICCClient.probe_port`, `detect_valve_controller`, `AMFSwitchController.connect` via
`_probe_selector`). It's called synchronously from the GUI thread by Device Manager's
"Probe / assign" tab — so clicking "Probe" while `DeviceLifecycleController`'s startup cycle (or
any device_io_pool task) is still running could touch the AMF SDK from two threads at once,
unsynchronized. `refresh_device_ports`'s own R7/R8 fix comment (2026-07-20) claimed it was "the
one method missing the lock" - that claim was incomplete. Fixed: renamed the body to
`_probe_endpoint_impl`, added a `probe_endpoint` wrapper that acquires `self._device_lock`, same
pattern as `connect`/`_connect_impl`. This also retroactively closes part of the "Scan && connect"
gap (`_DiscoverTask` in `device_console_dialog.py`, still on the general-purpose thread pool, not
`device_io_pool()`) — its `probe_endpoint` calls are now correctly serialized against everything
else even though the task itself still isn't migrated.

**Port-claim identity: `AMFSwitchController` and `SerialController` used a class-level owner
string, not a per-instance one.** `RegloICCClient` correctly claims its port under
`f"reglo-icc:{id(self)}"` (per instance). `AMFSwitchController`/`SerialController` (the shared
base class behind every valve/switch driver) claimed under the bare `self.controller_type`
string (e.g. `"amf-mswitch"`) - shared by *every instance* of the class. `connection_registry.
try_claim_port` treats a second claim under an already-registered owner name as a no-op
re-claim, not a conflict, so two different controller instances could both "successfully" claim
the same port. Currently unexploitable through the app's real call paths (the single-lane
`device_io_pool` plus the service's own lock mean connects never actually overlap in time) but a
real defense-in-depth gap in the exact class of driver (AMF) this rewrite is built around, and
inconsistent with the Reglo driver's pattern for no good reason.

Tracing it further turned up a second, real (not just latent) consequence: `device_manager.py`'s
`_connect_impl` keeps its own `_connection_owners[label]` string for use by `_disconnect_impl`'s
`release_port(endpoint, owner)` call. For the pump this already read the driver's own
`client._claim_owner` directly (correct). For the selector it independently *reconstructed* the
same-looking string (`f"amf-mswitch:{id(controller)}"`) rather than reading it from the
controller - which happened to produce an identical value only because both formulas used
`controller.controller_type` and `id(controller)`. For switch/valve it stored the bare
`controller.controller_type` (no id), which *did* match what the driver claimed under
pre-fix. Fixed both drivers to claim under `self._claim_owner = f"{controller_type}:{id(self)}"`
(mirroring Reglo), and updated `device_manager.py` to read `controller._claim_owner`/
`client._claim_owner` directly in all three branches (pump/selector/switch) instead of
re-deriving or using the bare class attribute, so there is one source of truth for what a driver
claimed a port as, not two formulas that need to be kept in sync by hand.

**Minor**: `device_lifecycle.py`'s `ranked_valve_ports` called
`should_probe_port_for_role(p.device, "valve")` - but that function only recognizes `"pump"`/
`"switch"` as role names (see `port_assignments.py`), so the call silently no-op'd (always
returned `True`). Currently harmless (redundant with the `get_port_assignment(...) == "switch"`/
`"auto"` check already applied in the same expression) but fixed to pass `"switch"` (the
canonical name - `hardware_inventory.py` already gets this right) for correctness of intent.

**Found, not touched — unreachable, no callers anywhere in `src/`:**
- `hardware_inventory.py` (232 lines, `scan_connected_serial_devices` and helpers) - has its own
  test file and looks like pre-built infrastructure for the already-documented, not-started
  "B2 - standalone device test window" roadmap item, not abandoned work. Left as-is.
- `DeviceCommunicationService.connect_enabled_profiles()`, `.safe_stop_all()`,
  `.probe_pump_port()` - each iterates `self._profiles`/`self._connections` without the lock;
  each individual hardware call inside the loop is still locked, so the only latent issue is the
  dict iteration itself racing a concurrent profile mutation. Not exploitable today since nothing
  calls these methods. Not fixed, since fixing dead code's locking would be busywork without a
  caller to verify it against - worth a look if either ever gets wired up to something.

Verified after every fix: full test suite (213 tests) green, pyflakes clean across `device/` and
`gui/` (only pre-existing, unrelated warnings remain), and a real offscreen app launch with no
tracebacks.

---

## 2026-07-21: selector homes fine but doesn't react during a running plan

Reported: the M-switch initializes and homes correctly, but once an experiment plan is running,
it doesn't respond to step-driven position changes. Traced the full step-execution wiring
(`_advance_experiment_control_progress` → `_apply_step_to_pump_async` → `_plan_step_commands` →
`_StepApplyRunnable.run()` → `DeviceCommunicationService.send_command`) - the command-building
logic itself (`_plan_step_commands`'s `_switch_cmd()`) is correct and unchanged by this session's
other work.

**Root cause: `_apply_step_to_pump_async` submitted `_StepApplyRunnable` to
`QThreadPool.globalInstance()` instead of `device_io_pool()`.** This is the *normal, steady-state*
path every step transition takes while a plan is running (as opposed to
`_apply_experiment_control_step_to_pump`, the synchronous version used only for the first step of
a run and manual step application). `DeviceCommunicationService.send_command` still takes the
service lock regardless of which pool calls it, so this was never a hard data race - but the AMF
selector was connected and homed on `device_io_pool`'s single persistent worker thread, and this
runnable then sent its `switch.move_to` (and every other) command from an arbitrary thread in the
general-purpose pool instead. Vendor hardware SDKs commonly assume same-thread access to a device
handle (this one already required the dedicated single-lane pool specifically because it isn't
guaranteed thread-safe - see R7/R8 and the 2026-07-21 whole-subsystem audit above); a mismatch
here plausibly causes commands to silently fail or no-op rather than raise a loud Python
exception, which matches "no reaction, no visible error" exactly. Fixed by routing the same
runnable through `device_io_pool()` instead - `device_io_pool` was already imported in this file
for `DevicePortRefreshTask`.

**Follow-up, done 2026-07-23:** `_apply_experiment_control_step_to_pump` (the synchronous path)
called `send_command` directly from whatever thread invoked it - the GUI thread for its real call
sites (starting a run, selecting a timeline row, restoring pause state, resuming after pause/hold,
live-editing the active step's row) - blocking the Qt event loop for however long the slowest
command (the M-switch's real several-second rotation) took, which also froze live sensorgram
plotting (it depends on the GUI event loop being free to process queued signals). Reported
separately as "GUI/sensorgram freezes while the selector is changing port." All 7 of its call
sites now dispatch through `_apply_step_to_pump_async`/`device_io_pool()` instead, matching the
already-correct auto-advance path; `_apply_experiment_control_step_to_pump` itself is deleted
(zero remaining callers). This needed `_apply_step_to_pump_async` to become overlap-safe first
(`on_success` is now carried on each dispatch's own `_StepApplyResult`, not a shared queue, since
multiple GUI-triggered applies can now legitimately be in flight at once) and one deliberate
behavior change: `_resume_experiment_control_after_manual_step_change` no longer hard-blocks the
resume if the hardware apply fails (previously it silently left the plan not-resumed) - a failure
now surfaces via the status bar and, per the same-day durable-logging fix below, the always-on
session HDF5 file, which is a stronger data-integrity guarantee than a synchronous gate that only
helps if someone is watching the screen at that exact moment.

Also same day: devices gained a `BUSY` lifecycle state (`DeviceCommunicationService.send_command`
marks the label busy for the duration of dispatch), surfaced in both Device Manager and the
titlebar status strip, so "the M-switch is mid-move" has one consistent representation instead of
being invisible.

Also same day: `_handle_experimental_control_state_recorded` (`gui/main_window.py`) previously only
wrote device state/failure events to the per-measurement HDF5 file, gated on
`self._measurement_active` - meaning a device failure during setup, between measurements, or while
paused left no durable record anywhere, only the ephemeral in-app log. Now also writes
unconditionally to the always-on session file (`storage/measurement_archive.py`'s
`ensure_session_writer`, same writer class/schema as the measurement file), so device state is
reviewable from the data file itself regardless of whether an official recording happened to be
running at the time.

Verified: full test suite green, pyflakes clean, panel constructs cleanly with the fix applied.

---

## 2026-07-21: UI freezes during device initialization

Reported: the whole app UI freezes/stalls while devices are initializing at startup, and the
request was to make the UI independent of device state - never wait, stay responsive.

**Root cause: GUI-thread status-display code was calling the same locked, live
`DeviceCommunicationService` methods the startup cycle uses for real hardware I/O.**
`connect`/`disconnect`/`send_command`/`probe_endpoint`/`refresh_device_ports` all hold
`self._lock` for their *entire* duration - correct and necessary, that's what serializes
hardware access. But `is_connected`/`connection`/`status` etc. also acquire the *same* lock
(`with self._lock: ...`), and several GUI-thread call sites invoke those during the startup
window, once per device-lifecycle event:
- `main_window_titlebar.py`'s `refresh_hw_device_status_strip` (fired on every `device_event`
  during the cycle - "connecting", "post_connect", "ready", per device)
- `experiment_control_window.py`'s `_sync_pump_from_controller`/`_sync_valve_from_controller`/
  `_sync_mswitch_from_controller` (called from `sync_from_lifecycle_controller`, itself called
  once during panel construction and again when bootstrap finishes - both of which can land
  while the cycle is still running)

`DeviceLifecycleController.run_full_cycle`'s `_emit` closure updates `self._last_event[device_key]`
*before* emitting the Qt signal that triggers these GUI-thread handlers - so a "connecting..."
event fires essentially the same moment `DeviceCommunicationService.connect()` starts and takes
the lock for however long the real connect takes (multiple probe attempts at up to ~0.75 s each
are realistic for the pump). The GUI-thread handler's `is_connected()` call then blocks trying to
acquire that same lock - freezing the *entire* UI (single Qt event loop) for the duration of that
device's connect, repeated for each of pump/valve/selector. Confirmed with a targeted script: a
simulated slow connect holding the lock for 1.5 s made `is_connected()` take 1400 ms; the fix
below took 0.0 ms under the same conditions.

**Fix: added `DeviceLifecycleController.is_connected_cached()`**, which reads only
`self._last_event` (no lock, never blocks) instead of querying the service live. Correct here
because `_last_event` is written on the same thread as the emit, before the cross-thread signal
that triggers the GUI-thread reader - it's never stale relative to what the UI is reacting to.
Switched the GUI-thread display call sites above to use it. **Deliberately not changed**:
`experiment_control_window._service_device_connected` and anything that gates a real device
action (deciding whether to send a command, home, etc.) - those keep using the live, authoritative
`is_connected()`/`DeviceCommunicationService.is_connected()` directly. Using stale cached data to
decide whether to *act* on a device would be a real correctness bug, not just a UI polish issue;
this fix only touches status *display*.

Also found and fixed in the same pass: `_sync_pump_from_controller` and
`ExperimentControlWindow._sync_device_connections_from_service` (called once during panel
construction) both called `DeviceCommunicationService.connection()` - live, locked, same blocking
risk - purely to set `self._client`, which turned out to be write-only: grepped the whole app,
nothing ever reads it. Removed both the dead assignment and the now-pointless
`_sync_device_connections_from_service` method entirely. Also removed `ExperimentControlWindow.
__init__`'s own unconditional `sync_from_lifecycle_controller()` call - its signal emissions
(`availability_changed` etc.) were provably discarded, since the caller (`ensure_experiment_control_
panel_for`) only connects those signals *after* the constructor returns; the real sync already
happens later, in `_finalize_experiment_control_bootstrap_population`, after signals are connected.

**Deliberately not changed**: `DeviceCommunicationService`'s single-lock design itself. Splitting
it into a fast state-lock (for reads) and a slow hardware-I/O lock (for real device operations)
would be the more thorough fix, and would also help the `shutdown_all`/`rerun_post_connect`-style
internal callers - but it means restructuring the locking inside `_connect_impl`/`_disconnect_impl`/
`send_command`, the most safety-critical code in this file, with zero existing test coverage for
`device_manager.py` itself. Per the engineering priority order (correctness/data integrity above
GUI responsiveness), that's a larger, riskier change to make without discussing it first - this
session's fix resolves the reported freeze without touching the hardware-serialization guarantee
at all.

Verified: full test suite (213 tests) green, pyflakes clean, and a standalone script proving the
exact freeze mechanism and confirming the fix (see above).

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

### R5 — Discovery scan connects pump/valve and leaves them claimed, colliding with the real connect
**Files:** `main_window_hardware_scan.py` (`_pump_scan_step`, `_valve_scan_step`, `_profile_device_init_step`)
**Status:** ✅ fixed

`hardware_initializer`'s startup scan is a *discovery* pass — it exists to identify what's
plugged in. But `_pump_scan_step` and `_valve_scan_step` both called `service.connect(...)`
after identifying a device and left it connected under an auto-resolved profile label.
`_profile_device_init_step` (used once a profile is already saved+enabled, i.e. every run
after the first) did the same. Meanwhile `experiment_control_window.py` runs its own
*separate* connect pass for the same physical devices (it owns the real "connect for use").
The second connect attempt collides with the first via the in-process port-claim registry
(`connection_registry.py`), producing exactly `Port COM12 is busy.` — a real, reproducible
error message despite there being no actual OS-level conflict.

`_selector_scan_step` never had this bug — it only identifies the device and reports
`connected=False`, leaving the one real connect to `experiment_control_window`. That's why
the selector connected reliably while pump and valve did not: the difference wasn't the
protocol, it was that only pump/valve scan steps eagerly connected-and-held.

**Fix:** Made `_pump_scan_step` and `_valve_scan_step` probe/register only (`connected=False`,
`state="discovered"`), matching `_selector_scan_step`. Made `_profile_device_init_step`
disconnect after reading identity instead of leaving the connection open. Discovery no longer
holds any device open; `experiment_control_window` is the sole "connect for use" owner.

---

### R6 — Valve/pump port auto-selection could pick a stale remembered port over the live scan result
**File:** `experiment_control_window.py` (`_populate_ports`, `_populate_valve_ports`)
**Status:** ✅ fixed

Both port-selection functions checked `self._last_selected_port`/`self._last_selected_valve_port`
(a value persisted from a previous session in `lspr_settings.json`) *before* considering the
freshly-ranked "most likely" port from the live scan. In one real case this meant a valve
controller freshly discovered on COM11 was ignored in favor of a stale remembered COM4 (which
no longer had anything valid on it), producing a `PermissionError`. There was also a dead
comparison (`get_port_assignment(...) == "valve"`) that could never be true, since that
function only ever returns `"auto"`, `"pump"`, or `"switch"`.

**Fix:** Re-ordered both functions to a consistent priority: explicit manual port assignment
first, then the freshly-ranked/likely live-scan result, and only as a last resort — when the
scan is inconclusive — the remembered port from a previous session. A stale remembered port
can no longer outrank a live result. `_populate_mswitch_ports` (selector) was checked too and
does not have this bug: its candidate list is already fully-identified AMF devices (not raw
serial ports that merely *might* be something), so reusing a remembered selection among them
is safe as-is.

The original intent behind `_last_selected_*_port` was reportedly a "fast reconnect" idea —
skip the full scan on session restore and go straight to the last-known-good port. That
feature was never actually implemented (the scan always runs today; the stale value just won
the selection race), so this fix keeps the always-scan behavior and does not attempt the fast
path. See B3 below.

---

### R7 — Health-check sync could race hardware_init's own connect/disconnect on the same device
**File:** `main_window_titlebar.py` (`refresh_hw_device_status_strip`)
**Status:** ✅ fixed (found the same day, caused by the R5 fix above)

After the R5 fix, `_profile_device_init_step` briefly connects then disconnects a device to
verify it (e.g. the selector) instead of leaving it connected. That created a new window where
`experiment_control_window.synchronize_device_connections()` — which runs on every hardware-init
step callback via `refresh_hw_device_status_strip` and can call `disconnect_device(...)` on a
device it thinks is "stale" — could run **concurrently** with hardware_init's own worker-thread
touch of the *same physical device*. Observed symptom: startup hung indefinitely after
"Selector failed health check; disconnecting stale connection" / "Selector disconnected." with
no further log output and no exception — consistent with the AMF vendor SDK (not guaranteed
thread-safe) blocking forever when hit from two threads at once, while the GUI thread itself
stayed responsive (the busy spinner kept animating) because the hang was confined to whichever
background thread got stuck holding the shared device lock.

**Fix:** `refresh_hw_device_status_strip` already computed `init_active` (hardware-init running)
for display purposes but didn't use it to gate the sync call. Now `synchronize_device_connections()`
is skipped entirely while `init_active` is true — hardware_init owns device connect/disconnect
during its own scan; the health-check sync only runs once the scan has finished.

Could not be verified against real AMF hardware in this environment (no devices attached) — if
a similar stall recurs, check whether it's specifically the AMF/selector step, since that vendor
SDK is the one un-auditable black box in this chain.

---

### R8 — Every connect attempt permanently pinned the port as a manual assignment
**Files:** `device_manager.py` (`register_endpoint_assignment`), `experiment_control_window.py`
(`_ensure_device_profile`), `main_window_hardware_scan.py` (`_valve_scan_step`)
**Status:** ✅ fixed

The R6 fix (above) made port auto-selection correctly prefer manual assignment > live scan
ranking > remembered port. That surfaced a deeper, pre-existing bug: `register_endpoint_assignment`
unconditionally called `set_port_assignment(...)`, permanently pinning whatever port was passed
as a "manual" assignment - on **every** call, not just genuine user pins. `_ensure_device_profile`
(the single choke point behind both the automatic startup auto-connect sequence and the regular
"Connect" button) calls this on every attempt, success or failure, before the connect even runs.

Since Windows COM port numbers shift over sessions as USB devices get replugged, this had
silently accumulated **five stale, partly-conflicting pins** in this installation's settings:
`COM10→switch`, `COM4→switch`, `COM6→switch`, `COM8→pump`, `COM12→pump`. Only one "switch" pin
can ever be current; the rest were leftovers outranking live discovery, which is exactly why the
valve kept connecting on a dead COM4 even after R6 fixed the ranking logic itself - the "manual"
tier that R6 correctly prioritizes was poisoned with stale data.

Also fixed a related dead comparison in `_valve_scan_step`'s `preferred_ports` filter: it checked
`get_port_assignment(port.device) == "valve"`, a value `get_port_assignment` can never return
(only `"auto"` / `"pump"` / `"switch"` - see `port_assignments.py:DeviceAssignment`), so that
filter was always empty. Changed to `"switch"`, matching the constant used everywhere else.

**Fix:** `register_endpoint_assignment` gained a `mark_manual: bool = True` parameter.
`_ensure_device_profile` now passes `mark_manual=False` - connect attempts (automatic or manual
button click) register/update the device profile so `connect()` knows the endpoint, but no
longer pin a permanent assignment as a side effect. The dedicated, genuinely-manual "assign this
port" action in `device_console_dialog.py` (`_assign_selected_endpoint`) is unaffected - it keeps
the default `mark_manual=True` and also explicitly calls `set_port_assignment` itself. The five
stale entries already in this installation's `lspr_settings.json` were cleared (backed up first
to `lspr_settings.json.pre_assignment_cleanup_<timestamp>` in the same folder) so the fix takes
effect immediately rather than only preventing new staleness.

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

### O4 — Startup step-list build ran synchronously on the GUI thread, freezing the app
**Files:** `main_window_hardware_scan.py` (`hardware_init_steps_for`), `hardware_initializer.py`
(`HardwareInitTask`), `main_window_lifecycle.py` (`start_hardware_initialization_for`)
**Status:** ✅ fixed

The per-device *probes* genuinely ran on a background `QThreadPool` worker (the "Hardware init
worker" thread). But the list of steps handed to that worker was built by calling
`hardware_init_steps_for(window)` **before** constructing `HardwareInitTask`, i.e. on the GUI
thread. That function enumerates serial ports and also calls the AMF vendor SDK
(`amfTools.util.getProductList()`) directly — a known multi-second blocking USB enumeration.
So the "background" scan actually began with a synchronous multi-second block on the GUI
thread, which is what produced the multi-second freeze at startup.

**Fix:** `HardwareInitTask` now takes a `steps_factory` callable instead of a pre-built list,
and calls it inside `run()` — which already executes on the worker thread. The call site
changed from `HardwareInitTask(window._hardware_init_steps())` to
`HardwareInitTask(window._hardware_init_steps)` (passing the method, not its result). Verified
with a smoke test that the factory now runs on a worker thread, not `MainThread`.

---

## Issues — Yellow (next sprint, performance/design)

### Y1 — `port_assignments.py` reads JSON from disk on every call
**File:** `port_assignments.py:55`
**Status:** ✅ fixed

`get_port_assignment(port)` calls `_load_port_assignments()` → `load_app_setting()` →
reads and JSON-parses the settings file from disk. During `scan_connected_serial_devices()`,
this is called at least twice per port. With 5 ports that is 10 file reads per scan.

**Fix:** Module-level cache dict invalidated only by `set_port_assignment()`.

---

### Y2 — `ensure_default_profiles()` always creates 6 profiles on every startup
**File:** `device_manager.py:327`
**Status:** ✅ fixed (metadata flag added; UI distinction is B2 scope)

Every startup ensures pump_1/pump_2/pump_3 + valve_1/valve_2 + switch_1 exist, all with
`endpoint=None`. Users with only 1 pump and 1 valve see 6 device slots in the UI, making it
hard to distinguish "configured" from "placeholder" devices.

**Fix:** Add a `source="default"` metadata flag to auto-created profiles so the UI can
visually distinguish placeholders from user-configured devices. Or: stop creating pump_2,
pump_3, valve_2 automatically and let the user add them via the device UI.

---

### Y3 — `arduino_valve.py` is orphaned dead code
**File:** `arduino_valve.py`
**Status:** ✅ fixed (file removed)

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
**Status:** ✅ done

Implemented multi-device init refactor + nomenclature standardization + device fingerprinting:

- `HardwareInitResult`: renamed `mswitch_devices/mswitch_error` → `selector_devices/selector_error`;
  added `by_label: dict[str, HardwareInitStepResult]` populated for every step.
- `HardwareInitTask.run()`: key accumulation now uses prefix matching (`startswith("pump")`,
  `startswith("valve")`, `startswith("selector")`). First probe found per type is kept; second
  pump/valve probe goes into `by_label` keyed by its profile label.
- Nomenclature: "mswitch"/"switch" → "selector" across `communication_models.py`,
  `device_comm_service.py`, `device_manager.py`, `hardware_initializer.py`, `main_window.py`,
  `main_window_lifecycle.py`. Legacy labels `switch_main` and `switch_1` auto-migrate to
  `selector_1` via `_LEGACY_LABEL_MIGRATIONS`.
- `DeviceProfile.fingerprint: str = ""` field added; persisted to / loaded from settings JSON.
  `new_device_profile()` accepts `fingerprint` parameter.
- `extract_usb_fingerprint(hwid)` module-level helper in `device_manager.py` parses `SER=`
  from USB HWID string.
- `DeviceCommunicationService.find_or_create_profile()`: looks up by (type, fingerprint);
  creates with auto-assigned label on first discovery; updates endpoint if COM port changes.
- `service.connect()` fixed: removed outer `try_claim_port` that was preventing inner clients
  from claiming the same port (same double-claim bug as the R1 probe_endpoint fix).
- `_hardware_init_steps()` now profile-driven: generates one connect step per configured
  profile with an endpoint; falls back to scan steps (pump_scan / valve_scan / selector_scan)
  for device types with no configured endpoint. Scan steps register profiles + connect.
- `_profile_device_init_step(profile)` added for profile-driven connects.
- Scan steps renamed: `_pump_scan_step`, `_valve_scan_step`, `_selector_scan_step`.
- Pump scan: registers all found pumps, not just the first; returns summary result.

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

### B3 — Fast reconnect to last-known ports on session restore (low priority)
**Status:** ⬜ pending (low priority, roadmap)

Today every startup always runs a full device scan/re-rank (see R6 fix above — the
alternative of trusting a remembered port without re-verifying it was removed as the source
of a real bug, not replaced with a working fast path). A full scan is safe but not
instant — the AMF/serial enumeration alone can take a few seconds even after the O4 threading
fix moves it off the GUI thread.

**Idea:** if the previous session's last-known port for a device is still present *and* a
lightweight re-probe on that specific port confirms it's still the same device (matching
fingerprint/identity), connect directly instead of waiting for the full ranked scan across
all ports. This must re-verify the device on that port before trusting it — silently reusing
a remembered port without re-checking is exactly what R6 just removed, and re-introducing that
without verification would reintroduce the same bug.

Deliberately deferred: the full-scan behavior is correct and not urgent to optimize.

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
14. R5 — Discovery scan no longer eagerly connects pump/valve (2026-07-20)
15. R6 — Port auto-selection prefers live scan over stale remembered port (2026-07-20)
16. O4 — Step-list build moved off the GUI thread (2026-07-20)
17. R7 — Health-check sync no longer races hardware_init's own device touches (2026-07-20)
18. R8 — Connect attempts no longer permanently pin ports as manual assignments (2026-07-20)
19. Full device lifecycle rewrite — single owner (`DeviceLifecycleController`) replaces the
    two-system architecture behind R1/R5/R6/R7/R8/O4/B1; `synchronize_device_connections`
    (the selector mid-session disconnect bug) deleted; `hardware_initializer.py`/
    `main_window_hardware_scan.py`/`experiment_control_connection.py` deleted (2026-07-20)
20. B3 — Fast reconnect to last-known ports (low priority, not started)
21. B2 — Standalone device test window (not started)
22. `DeviceManagerDialog` Connect/Disconnect for canonical pump/valve/selector migrated to
    `DeviceLifecycleController` (2026-07-21) — "Scan && connect" in the same dialog not migrated
23. Orphaned, never-laid-out connect widgets found and deleted from `experiment_control_window.py`
    (2026-07-21) — ~500 lines removed; DeviceManagerDialog is now the sole manual-connect UI
24. Simulated pump/valve/selector devices ("Part B") — discussed and dropped (2026-07-21)
25. `probe_endpoint()` given the service lock it was missing (2026-07-21) — was reachable
    unsynchronized from the GUI thread (Device Manager's Probe/assign tab)
26. `AMFSwitchController`/`SerialController` port-claim owner made per-instance instead of
    class-level (2026-07-21), matching `RegloICCClient`; fixed the resulting release-mismatch in
    `device_manager.py`'s connect bookkeeping for selector and switch/valve
27. `ranked_valve_ports`'s `should_probe_port_for_role` call fixed to pass `"switch"` instead of
    the unrecognized `"valve"` (2026-07-21) — was a silent no-op, currently-harmless
28. Dead code found, not removed: `hardware_inventory.py` (unwired B2 scaffolding),
    `connect_enabled_profiles`/`safe_stop_all`/`probe_pump_port` on `DeviceCommunicationService`
    (no callers anywhere) (2026-07-21)
29. B4 — **Done (2026-07-22).** Split `DeviceCommunicationService`'s single lock into a fast
    state lock (`self._state_lock`, via a new `_state(operation)` helper — for
    `is_connected`/`connection`/`status`/`list_statuses`/`list_profiles`/`get_profile`/
    `list_events`, plus the brief bookkeeping-dict mutations nested inside the slow methods) and
    the existing hardware-I/O lock (`self._lock`/`_device_lock`, unchanged, still serializes
    `connect`/`disconnect`/`send_command`/`probe_endpoint`/`refresh_device_ports`/profile-mutation
    methods service-wide, exactly as before). One-way nesting only (I/O lock may acquire the
    state lock, never the reverse), so no deadlock is possible between the two. Also fixes a
    pre-existing gap: `save_profiles()` had no lock at all before this. New test file
    `tests/test_device_manager_locking.py` (4 tests, stable across repeated runs) proves a fast
    read doesn't block during a slow connect or disconnect, that disconnect's `self._connections`
    pop happens before the real `close()` I/O (so a concurrent reader never sees "connected" for
    a connection whose teardown is already in progress), that the reentrant connect-over-
    existing-connection path still works, and that concurrent status reads survive connect/
    disconnect churn without hitting a dict-mutated-during-iteration race. Also helps
    `shutdown_all`/`rerun_post_connect`'s internal `is_connected()` calls, which weren't touched
    by the earlier cached-status-read fix. See the design writeup this was implemented from for
    the full lock-ordering/torn-read/reentrancy reasoning (kept in this session's plan history,
    not duplicated here).
30. `gui/device_console_dialog.py` (Device Manager) audit and fix pass, 2026-07-22, prompted by a
    maintainer question ("is it logically ok, does each function need to be there") plus a
    separately-reported UI bug (an orphaned `_probe_output` widget overlapping the tab bar, fixed
    first, same session - never added to any layout, a leftover from before the code moved to
    `_probe_result`). The audit found six real issues, all fixed: (a) non-canonical
    profiles (aux/waste pump, second switch, anything probed/assigned manually) called
    `service.connect()`/`disconnect_device()` synchronously on the GUI thread - only the single
    canonical pump/switch/selector went through `DeviceLifecycleController`/`device_io_pool()`;
    now all of them do, via the previously-defined-but-never-used `_RefreshTask` wrapper; (b)
    "Scan & connect" submitted real hardware I/O (including AMF selector connects) to
    `QThreadPool.globalInstance()` instead of `device_io_pool()` - same AMF thread-safety risk
    class as R7; now uses `device_io_pool()`, `self._thread_pool` removed as dead; (c) the Probe
    button also ran `service.probe_endpoint()` synchronously on the GUI thread - now async via the
    same `_RefreshTask`/`device_io_pool()` pattern; (d) both "Test" buttons wrote their result to
    a *different* tab's text box with no visible feedback on the tab you were looking at - now
    `QMessageBox.information`; (e) the Commands tab's device dropdown was only refreshed inside
    `refresh_port_list()`, so a normal connect/disconnect never updated it - moved to
    `refresh_connected_devices()` (the correct data ownership, and the one called after every
    connect/disconnect); (f) the "Raw command" field was permanently `setEnabled(False)` scaffolding
    for the simulated-device debug mode ("Part B") that was explicitly dropped earlier this session
    - deleted, it could never be enabled by any current code path. Verified via
    `python -m pyflakes`, the full test suite (no regressions; this file has zero dedicated test
    coverage, confirmed), and an offscreen-`QApplication` construction/wiring smoke script (no
    test file exists for this dialog). Remaining, explicitly deferred: the redundancy between the
    Connected Devices tab's and Profiles tab's near-duplicate Connect/Disconnect/Test
    implementations - a maintainer-facing simplification question, not a correctness bug, saved
    for a separate decision.
31. **Selector silently ignored plan-step move commands despite showing connected - fixed
    2026-07-22.** Real incident, traced live: user reported "selector is connected but does not
    move with the experiment running." Log showed `M-Switch connected: RVMFS on COM9` at startup,
    then `M-Switch command skipped | controller not connected | step=1 switch=4` when the plan
    tried to move it. Root cause: `DeviceLifecycleController._discover_and_connect_selector`
    (and the equivalent pump/valve methods) resolved which profile to connect via
    `service.find_or_create_profile()` - a fingerprint/endpoint search across *all* profiles,
    correct for the general multi-profile Device Manager discovery flow but wrong for the fixed
    canonical labels this controller owns. The user's settings had a stale `selector_2` profile
    (from an earlier session, before canonical defaults existed) already holding the real
    selector's fingerprint, so discovery connected the physical device under `selector_2` -
    while every experiment-control connectivity check (`_service_device_connected`, gating
    `_switch_cmd()`'s `mswitch_connected`) looks at the fixed canonical `selector_1`
    (`device_label_for(SELECTOR)`). The device was genuinely connected, just under the wrong
    label, so it looked fine at startup and silently no-opped every move during a plan.
    **Fix:** `_discover_and_connect_pump`/`_discover_and_connect_valve`/`_discover_and_connect_selector`
    now resolve via `ensure_device_profile()` - the exact mechanism the manual "Connect" button
    (`request_connect`) already used correctly - which always targets the fixed canonical label,
    never searches other profiles. A new `DeviceCommunicationService.update_profile_identity(label,
    fingerprint=..., identity=...)` records the discovered fingerprint/identity onto that same
    canonical label afterward (in-place, no search, no new profile), preserving the diagnostic
    info the old fingerprint-search path used to capture for the Device Manager's Profiles table.
    `find_or_create_profile()` itself is untouched and still correctly used by the general
    multi-profile "Scan & connect" flow (`device_console_dialog.py`'s `_DiscoverTask`), which
    legitimately wants fingerprint-based dedup across possibly many devices of the same type -
    only the fixed-canonical-label lifecycle stopped using it. Regression tests added in
    `tests/unit/test_device_lifecycle.py` (`CanonicalLabelResolutionTests`, one per device type,
    asserting `find_or_create_profile` is never called and the canonical label is always used)
    plus an isolated script reproducing the exact reported scenario (a pre-existing stale
    `selector_2` holding the real fingerprint) and confirming the fix. **Not fixed by this
    change:** any *already-corrupted* local settings file still has the stale duplicate profile
    sitting in it - the code fix prevents new occurrences and correctly reuses/updates the
    canonical profile going forward, but an existing stale duplicate should still be deleted via
    Device Manager's Profiles tab (the maintainer did this manually alongside the code fix).
