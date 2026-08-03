"""Per-app-user Device Manager defaults & limits (spectrometer/pump/switch/selector).

Deliberately separate from the per-device *Enabled* toggle
(``device.device_lifecycle.DeviceLifecycleController.enabled_devices``), which describes which
hardware is physically present on this rig and is stored globally on purpose - see that module's
``_ENABLED_DEVICES_SETTING_KEY`` comment. The settings here describe how a given app-user likes to
run the instrument (integration-time limits, tube diameter, poll interval), so they are saved via
``storage.app_config.save_app_setting``/``load_app_setting``, which resolves to the active
app-user's own settings file (see ``storage.user_profile.current_config_path``).

All four device settings are stored under one namespaced key (``_SETTING_KEY``) instead of one
flat ``save_app_setting`` key per field, to avoid adding to the ad hoc, ungrouped key sprawl already
present in ``storage/app_config.py``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from lspr_app.domain.models import AutoExposureSettings
from lspr_app.domain.pump_plan import DEFAULT_ROLLER_COUNT, DEFAULT_TUBE_MM, VALID_ROLLER_COUNTS
from lspr_app.storage.app_config import load_app_setting, save_app_setting

_SETTING_KEY = "device_manager_settings"

# Legacy flat key this module absorbs on first load - see
# load_device_manager_settings() below. Was previously read/written directly
# by gui/device_console_dialog.py's Switch settings popup.
_LEGACY_ENVIRONMENT_POLL_INTERVAL_KEY = "environment_poll_interval_s"
_DEFAULT_ENVIRONMENT_POLL_INTERVAL_S = 5.0


@dataclass(slots=True)
class PumpDefaults:
    tube_mm: float = DEFAULT_TUBE_MM
    # Roller backsteps for drip-free dispensing, sent to the pump via the
    # Reglo ICC's "%" command on every channel configure (manual sec. 6.4.3 /
    # 16.2 ref 4.3-4.4). Range 0-100; 0 is the pump's own factory default.
    backsteps: int = 0
    # Soft cap on the per-channel flow rate spinbox in the Experiment Control
    # panel (see experiment_control_window.py's manual_flow_spins) - not a
    # pump hardware limit (the Reglo ICC itself supports up to 35 mL/min on
    # the largest tubing, see domain/pump_plan.TUBE_DIAMETER_OPTIONS), just a
    # sane default for this app's typical low-flow LSPR usage that used to be
    # hardcoded to 100.0.
    max_flow_ul_min: float = 100.0
    # Rollers on the installed cassette head - must match the physical head
    # (6/8/12, see RegloICCClient.set_roller_count) or the pump's mL/min
    # flow-rate conversion will be skewed. Sent on every channel configure.
    roller_count: int = DEFAULT_ROLLER_COUNT


@dataclass(slots=True)
class SwitchDefaults:
    environment_poll_interval_s: float = _DEFAULT_ENVIRONMENT_POLL_INTERVAL_S


@dataclass(slots=True)
class SelectorDefaults:
    """No selector-specific settings exist yet.

    Kept as an explicit (empty) dataclass - rather than omitting a selector
    field from DeviceManagerSettings entirely - so the settings model and the
    Device Manager UI both already have a named slot to add fields into later
    without a structural change.
    """


@dataclass(slots=True)
class DeviceManagerSettings:
    spectrometer: AutoExposureSettings = field(default_factory=AutoExposureSettings)
    pump: PumpDefaults = field(default_factory=PumpDefaults)
    switch: SwitchDefaults = field(default_factory=SwitchDefaults)
    selector: SelectorDefaults = field(default_factory=SelectorDefaults)


def _coerce_settings(raw: object, path: Path | None) -> DeviceManagerSettings:
    payload = raw if isinstance(raw, dict) else {}

    spectrometer_defaults = asdict(AutoExposureSettings())
    spectrometer_raw = payload.get("spectrometer")
    if isinstance(spectrometer_raw, dict):
        spectrometer_defaults.update(
            {key: value for key, value in spectrometer_raw.items() if key in spectrometer_defaults}
        )
    spectrometer = AutoExposureSettings(**spectrometer_defaults)

    pump_defaults = asdict(PumpDefaults())
    pump_raw = payload.get("pump")
    if isinstance(pump_raw, dict):
        pump_defaults.update({key: value for key, value in pump_raw.items() if key in pump_defaults})
    if pump_defaults.get("roller_count") not in VALID_ROLLER_COUNTS:
        pump_defaults["roller_count"] = DEFAULT_ROLLER_COUNT
    pump = PumpDefaults(**pump_defaults)

    switch_defaults = asdict(SwitchDefaults())
    switch_raw = payload.get("switch")
    if isinstance(switch_raw, dict):
        switch_defaults.update({key: value for key, value in switch_raw.items() if key in switch_defaults})
    elif "switch" not in payload:
        # First load after this module was introduced: absorb the legacy flat
        # key so nobody's existing poll-interval setting is silently reset.
        legacy_interval = load_app_setting(_LEGACY_ENVIRONMENT_POLL_INTERVAL_KEY, None, path)
        if isinstance(legacy_interval, (int, float)):
            switch_defaults["environment_poll_interval_s"] = float(legacy_interval)
    switch = SwitchDefaults(**switch_defaults)

    return DeviceManagerSettings(
        spectrometer=spectrometer,
        pump=pump,
        switch=switch,
        selector=SelectorDefaults(),
    )


def load_device_manager_settings(path: Path | None = None) -> DeviceManagerSettings:
    raw = load_app_setting(_SETTING_KEY, None, path)
    return _coerce_settings(raw, path)


def save_device_manager_settings(settings: DeviceManagerSettings, path: Path | None = None) -> None:
    save_app_setting(
        _SETTING_KEY,
        {
            "spectrometer": asdict(settings.spectrometer),
            "pump": asdict(settings.pump),
            "switch": asdict(settings.switch),
            "selector": asdict(settings.selector),
        },
        path,
    )
