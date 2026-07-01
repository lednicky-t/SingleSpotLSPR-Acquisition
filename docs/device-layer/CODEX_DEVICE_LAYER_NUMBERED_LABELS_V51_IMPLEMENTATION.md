# Device layer numbered-label implementation notes

This note tracks the local implementation of the V51 device-layer rewrite.

Implemented so far:

- Added `communication_models.py` with pure dataclasses for:
  - `DeviceProfile`
  - `DeviceStatus`
  - `DeviceCommand`
  - `DeviceCommandResult`
  - `ProbeResult`
  - `PortDescriptor`
  - `PortRefreshData`
  - `DeviceEvent`
- Added `new_device_profile()` and `next_device_label()`.
- Migrated legacy profile labels to numbered labels:
  - `pump_main -> pump_1`
  - `pump_aux -> pump_2`
  - `pump_waste -> pump_3`
  - `valve_inlet -> valve_1`
  - `valve_outlet -> valve_2`
  - `switch_main -> switch_1`
- Updated `DeviceCommunicationService` to expose the V51-style API:
  - `list_profiles()`
  - `get_profile()`
  - `save_profile()`
  - `delete_profile()`
  - `scan_passive()`
  - `probe_endpoint()`
  - `connect()`
  - `disconnect()`
  - `status()`
  - `list_statuses()`
  - `send_command()`
- Kept backward-compatible aliases for older call sites:
  - `register_profile()`
  - `profile()`
  - `list_devices()`
  - `connect_device()`
  - `disconnect_device()`
- Made connect ownership persistent with `connection_registry` claims and release on disconnect.
- Reduced the hardware inventory dialog to passive COM listing only.
- Updated the device console connected-device view to include display name and last error.
- Converted the device console tab pages to `QWidget` pages instead of embedded dialogs.
- Added an editable Profiles tab and renamed the probe log tab to Event Log to better match the V51 console model.
- Updated Experiment Control fallback labels to numbered device labels.
- Removed a few remaining disconnected-driver fallbacks in Experiment Control so the window no longer fabricates pump clients when no connection is present.

Notes:

- Legacy labels are still accepted as aliases during migration.
- The service currently remains in-process, as requested by V51.
- The diagnostics/event-log split is represented in the model layer, but the full event router/store is still a future cleanup step.
