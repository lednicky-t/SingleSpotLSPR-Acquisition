"""Canonical device type identifiers.

These string constants are the single source of truth for device type names used
across the GUI and the device service layer.

To rename a device category in the future:
  1. Change the constant value here.
  2. Add the old value as a recognized alias in ``_normalize_device_type()`` in
     ``device_manager.py``.
  3. Add a migration entry in ``_LEGACY_LABEL_MIGRATIONS`` in ``device_manager.py``
     so that profiles saved under the old label are transparently upgraded on load.
  4. Update display strings in ``experiment_control_window.py``.
"""

from __future__ import annotations

PUMP = "pump"
SWITCH = "switch"       # injection valve / flow switch (formerly "valve")
SELECTOR = "selector"   # AMF M-Switch channel selector
