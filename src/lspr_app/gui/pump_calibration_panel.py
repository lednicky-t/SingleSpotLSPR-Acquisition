"""The pump's own multi-channel calibration workflow: a pop-out window
opened from Device Manager's Pump page ("Open Pump Calibration..." button,
under the "time since last calibration" readout - see
device_console_dialog.py's _open_pump_calibration_window).

Distinct from device_console_dialog.py's deep-debug-gated "Pump Cal. (raw)"
tab, which drives the Reglo ICC manual's own one-channel-at-a-time
interactive procedure (manual sec. 6.4.4/18.5) directly. This runs every
active channel together instead:

1. Fill tubes - primes all 4 channels' tubing at a computed flow rate for
   one minute (see fill_flow_rate_ul_min).
2. Disperse liquid - runs the control row's actual configured
   direction/tube/flow per channel for the shared Duration, then stops.
3. Enter the actually-measured volume per channel and confirm - computes
   and writes a corrected roller-step-volume constant per channel.

Correction math: if the pump was told to dispense a target volume V_target
but a channel actually delivered V_measured, the true roller-step volume
differs from what's currently configured (V_rsv_old) by the same ratio:

    V_rsv_new = V_rsv_old * (V_measured / V_target)

e.g. target 200 mL, measured 202 mL -> ratio 1.01 -> the corrected
roller-step volume is 1% larger than what the pump currently has configured.
"""

from __future__ import annotations

import math
from time import monotonic

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from lspr_app.device.communication_models import DeviceCommand
from lspr_app.device.device_lifecycle import device_label_for
from lspr_app.device.device_manager import DeviceCommunicationService
from lspr_app.device.device_types import PUMP
from lspr_app.domain.pump_plan import ACTIVE_PUMP_CHANNELS
from lspr_app.gui.experiment_control_widgets import TubeDiameterComboBox
from lspr_app.gui.flow_plan_model import display_value_to_seconds, seconds_to_display_value
from lspr_app.gui.ui_helpers import make_compact_spinbox
from lspr_app.storage.device_manager_settings import DeviceManagerSettings

# Time-unit display range/precision per mode - matches
# ExperimentControlWindow._update_time_unit_ui/_duration_display_decimals
# exactly, so cycling behaves the same way here as in the real plan editor.
_TIME_UNIT_ORDER = ("s", "min", "h")
_TIME_UNIT_SPIN_CONFIG = {
    "s": {"decimals": 0, "max": 86400.0, "step": 1.0},
    "min": {"decimals": 1, "max": 1440.0, "step": 0.1},
    "h": {"decimals": 2, "max": 24.0, "step": 0.01},
}

DEFAULT_TUBE_LENGTH_MM = 450.0

# Margin added on top of the tube's raw internal volume before computing the
# one-minute fill rate (step 1) - not a hardware limit, just a safety
# cushion so the tubing reliably ends up fully wetted rather than falling
# just short.
_FILL_VOLUME_MARGIN = 1.2

# Volume spinboxes (Set Volume / Measured Volume) display/edit in uL, but
# the correction math (corrected_roller_step_volume_ml, deviation_pct) and
# the pump protocol's own Volume Type fields work in mL - converted at the
# widget boundary only.
_UL_PER_ML = 1000.0
_VOLUME_UL_MAX = 10_000_000.0

_COLOR_GOOD = "#22c55e"
_COLOR_CAUTION = "#eab308"
_COLOR_WARNING = "#f59e0b"
_COLOR_BAD = "#ef4444"
_COLOR_MUTED = "#94a3b8"

# Deviation thresholds (percent magnitude) for the confirm step's per-channel
# color coding - our own choice, not an Ismatec spec.
_DEVIATION_GOOD_MAX_PCT = 2.0
_DEVIATION_CAUTION_MAX_PCT = 5.0
_DEVIATION_WARNING_MAX_PCT = 10.0
_DEVIATION_REMEASURE_THRESHOLD_PCT = 5.0


def volume_ml_from_flow_and_duration(flow_ul_min: float, duration_s: float) -> float:
    """uL/min over a duration in seconds -> dispensed volume in mL."""
    return max(float(flow_ul_min), 0.0) * max(float(duration_s), 0.0) / 60.0 / 1000.0


def duration_s_from_flow_and_volume(flow_ul_min: float, volume_ml: float) -> float:
    """Inverse of volume_ml_from_flow_and_duration - holds flow rate fixed
    and solves for the duration needed to reach *volume_ml*. Returns 0.0 if
    flow rate is 0 (undefined - avoids a ZeroDivisionError from a stray UI
    signal during setup, e.g. before a rate has been entered)."""
    flow_ul_min = max(float(flow_ul_min), 0.0)
    if flow_ul_min <= 0.0:
        return 0.0
    return max(float(volume_ml), 0.0) * 1000.0 * 60.0 / flow_ul_min


def fill_flow_rate_ul_min(tube_mm: float, tube_length_mm: float) -> float:
    """Flow rate (uL/min) needed to fill *tube_length_mm* of tubing (inner
    diameter *tube_mm*) plus a 20% margin, within one minute.

    1 mm^3 = 1 uL exactly, so the tube's internal volume in uL is simply
    pi * r^2 * length with r/length in mm - no unit conversion needed.
    Rounded up (ceil) so the one-minute fill always has margin to spare
    rather than possibly falling just short of fully wetting the tubing.
    """
    radius_mm = max(float(tube_mm), 0.0) / 2.0
    volume_ul = math.pi * radius_mm**2 * max(float(tube_length_mm), 0.0)
    return float(math.ceil(volume_ul * _FILL_VOLUME_MARGIN))


def corrected_roller_step_volume_ml(old_rsv_ml: float, target_volume_ml: float, measured_volume_ml: float) -> float:
    """The corrected roller-step-volume constant to write back to the pump.

    See this module's docstring for the derivation. Returns old_rsv_ml
    unchanged if target_volume_ml is 0 (ratio undefined).
    """
    if target_volume_ml <= 0.0:
        return old_rsv_ml
    ratio = measured_volume_ml / target_volume_ml
    return old_rsv_ml * ratio


def deviation_pct(target_volume_ml: float, measured_volume_ml: float) -> float:
    """Percent deviation of measured vs. target volume - e.g. target 200,
    measured 202 -> +1.0 (not +2.0; matches "correction ratio 1.01 -> +1%
    deviation", the pump dispensed 1% more than asked, not 2%)."""
    if target_volume_ml <= 0.0:
        return 0.0
    return (measured_volume_ml / target_volume_ml - 1.0) * 100.0


def deviation_color(deviation_percent: float) -> str:
    """Our own placeholder color bands for the confirm step - not an
    Ismatec spec. <2% good, 2-5% caution, 5-10% warning, >=10% bad."""
    magnitude = abs(deviation_percent)
    if magnitude < _DEVIATION_GOOD_MAX_PCT:
        return _COLOR_GOOD
    if magnitude < _DEVIATION_CAUTION_MAX_PCT:
        return _COLOR_CAUTION
    if magnitude < _DEVIATION_WARNING_MAX_PCT:
        return _COLOR_WARNING
    return _COLOR_BAD


def deviation_tooltip(deviation_percent: float) -> str:
    if abs(deviation_percent) >= _DEVIATION_REMEASURE_THRESHOLD_PCT:
        return f"Deviation exceeds {_DEVIATION_REMEASURE_THRESHOLD_PCT:g}% - consider remeasuring this channel."
    return ""


def _auto_time_unit_for_seconds(seconds: float) -> str:
    """Whichever of s/min/h keeps *seconds* a sensible-looking displayed
    number - used when a Volume edit derives a new Duration, since that can
    easily land far outside whatever unit the user happened to have
    selected (unlike a direct Duration edit, where they already picked a
    unit that works for them)."""
    if seconds >= 3600.0:
        return "h"
    if seconds >= 60.0:
        return "min"
    return "s"


def format_mm_ss(seconds: float) -> str:
    """Whole seconds -> "M:SS" (no leading-zero on minutes, matching a
    typical media-player countdown)."""
    total_seconds = max(int(round(seconds)), 0)
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes}:{secs:02d}"


def _direction_of(button: QPushButton) -> str:
    return "CCW" if button.isChecked() else "CW"


def _make_direction_toggle_button() -> QPushButton:
    button = QPushButton("CW ↻")
    button.setCheckable(True)
    button.setFixedWidth(72)
    button.setAutoDefault(False)  # never trigger from an Enter keypress elsewhere in the dialog
    button.setToolTip("Pump rotation direction. Click to toggle CW/CCW.")

    def _update_text(checked: bool) -> None:
        button.setText("CCW ↺" if checked else "CW ↻")

    button.toggled.connect(_update_text)
    return button


class PumpCalibrationControlRow(QWidget):
    """The Duration/CHs/Dir/Tube/Length/Flow/Set Volume control row - same
    layout idiom as ExperimentControlWindow's plan-step editor row (matrix
    grid, "="/"≠" toggle swapping a single shared control for per-channel
    ones), minus Valve/Switch/Color/Comment, plus Tube Length and Set
    Volume columns.

    Unlike ExperimentControlWindow's real uniform mode (which only shares
    Direction+Tube, since flow rate legitimately differs per channel/
    reagent in a real experiment), calibration wants every active channel
    run under identical conditions when "=" is on - so here "=" *also*
    links Flow rate (and therefore Set Volume, transitively) using CH1 as
    the source, in addition to Direction/Tube/Length.

    Volume<->Duration<->Flow linking rule: editing Duration or a channel's
    Flow recomputes that channel's Volume; editing a channel's Volume
    solves for a new shared Duration (holding that channel's own Flow rate
    fixed), which then refreshes every other channel's Volume display too,
    since Duration is shared across channels.

    ``grid`` (the QGridLayout backing this widget) is exposed so
    PumpCalibrationDialog can add the Measured Volume/Deviation rows (built
    once step 2 finishes) directly onto it, in the same CH1-4 columns as
    Set Volume - two separate QGridLayout instances have no way to keep
    their column widths in sync with each other, so sharing one grid is
    the only reliable way to keep those columns visually aligned.
    """

    _ROW_HEADER = 0
    _ROW_PRIMARY = 1
    _ROW_DIRECTION = 2
    _ROW_TUBE = 3
    _ROW_LENGTH = 4
    _ROW_SET_VOLUME = 5

    _COL_DURATION = 0
    _COL_EQUAL = 1
    _COL_DIRECTION = 2
    _COL_TUBE = 3
    _COL_LENGTH = 4
    _COL_ROW_LABEL = 5
    _COL_CHANNEL_START = 6

    def __init__(self, device_manager_settings: DeviceManagerSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._device_manager_settings = device_manager_settings
        self._channel_flow_spins: dict[int, QDoubleSpinBox] = {}
        self._channel_volume_spins: dict[int, QDoubleSpinBox] = {}
        self._channel_direction_buttons: dict[int, QPushButton] = {}
        self._channel_tube_combos: dict[int, TubeDiameterComboBox] = {}
        self._channel_length_spins: dict[int, QDoubleSpinBox] = {}
        # Fires whenever anything that feeds fill_flow_rate_ul_min changes
        # (tube diameter/length, shared or per-channel, or the "=" toggle) -
        # PumpCalibrationDialog hooks this to refresh its step-1 label.
        self.tube_geometry_changed: list = []
        self._build_ui()
        self._set_equal_mode(True)

    @staticmethod
    def channel_column(channel: int) -> int:
        """Grid column for *channel*'s cells (1-indexed) - shared with
        PumpCalibrationDialog so the Measured Volume/Deviation rows it adds
        line up under Set Volume exactly."""
        return channel + PumpCalibrationControlRow._COL_CHANNEL_START - 1

    def _build_ui(self) -> None:
        self.grid = QGridLayout(self)
        grid = self.grid
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        duration_label = QLabel("Duration")
        equal_label = QLabel("CHs")
        equal_label.setToolTip(
            "Shared channel mode. '=' keeps direction, tube, length, and flow rate\n"
            "identical for all channels (following CH1). '≠' allows independent\n"
            "per-channel direction/tube/length, and always-independent flow rates."
        )
        dir_label = QLabel("Dir")
        tube_label = QLabel("Tube")
        length_label = QLabel("Length")
        flow_label = QLabel("Flow")
        for label in (equal_label, dir_label, tube_label, length_label):
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(duration_label, self._ROW_HEADER, self._COL_DURATION)
        grid.addWidget(equal_label, self._ROW_HEADER, self._COL_EQUAL)
        grid.addWidget(dir_label, self._ROW_HEADER, self._COL_DIRECTION)
        grid.addWidget(tube_label, self._ROW_HEADER, self._COL_TUBE)
        grid.addWidget(length_label, self._ROW_HEADER, self._COL_LENGTH)
        grid.addWidget(flow_label, self._ROW_HEADER, self._COL_ROW_LABEL)

        self._time_unit_mode = "s"
        self._duration_seconds = 60.0

        self._duration_spin = make_compact_spinbox(QDoubleSpinBox())
        self._duration_spin.valueChanged.connect(self._on_duration_spin_changed)

        self._time_unit_button = QToolButton()
        self._time_unit_button.setMinimumWidth(34)
        self._time_unit_button.setMaximumWidth(42)
        self._time_unit_button.clicked.connect(self._cycle_time_unit_mode)

        duration_widget = QWidget()
        duration_layout = QHBoxLayout(duration_widget)
        duration_layout.setContentsMargins(0, 0, 0, 0)
        duration_layout.setSpacing(3)
        duration_layout.addWidget(self._duration_spin)
        duration_layout.addWidget(self._time_unit_button)
        grid.addWidget(duration_widget, self._ROW_PRIMARY, self._COL_DURATION)
        self._update_time_unit_ui()

        self._equal_button = QPushButton("=")
        self._equal_button.setCheckable(True)
        self._equal_button.setChecked(True)
        self._equal_button.setFixedWidth(36)
        self._equal_button.setAutoDefault(False)
        self._equal_button.toggled.connect(self._set_equal_mode)
        grid.addWidget(self._equal_button, self._ROW_PRIMARY, self._COL_EQUAL)

        self._shared_direction_button = _make_direction_toggle_button()
        self._shared_direction_button.toggled.connect(self._apply_shared_settings)
        grid.addWidget(self._shared_direction_button, self._ROW_PRIMARY, self._COL_DIRECTION)

        self._shared_tube_combo = TubeDiameterComboBox()
        self._shared_tube_combo.setMaximumWidth(96)
        self._shared_tube_combo.setValue(self._device_manager_settings.pump.tube_mm)
        self._shared_tube_combo.valueChanged.connect(self._apply_shared_settings)
        grid.addWidget(self._shared_tube_combo, self._ROW_PRIMARY, self._COL_TUBE)

        self._shared_length_spin = make_compact_spinbox(QDoubleSpinBox())
        self._shared_length_spin.setRange(1.0, 10_000.0)
        self._shared_length_spin.setDecimals(0)
        self._shared_length_spin.setSuffix(" mm")
        self._shared_length_spin.setValue(DEFAULT_TUBE_LENGTH_MM)
        self._shared_length_spin.setMaximumWidth(96)
        self._shared_length_spin.valueChanged.connect(self._apply_shared_settings)
        grid.addWidget(self._shared_length_spin, self._ROW_PRIMARY, self._COL_LENGTH)

        self._dir_row_label = QLabel("Dir")
        self._tube_row_label = QLabel("Tube")
        self._length_row_label = QLabel("Length")
        self._volume_row_label = QLabel("Set Volume")
        grid.addWidget(self._dir_row_label, self._ROW_DIRECTION, self._COL_ROW_LABEL)
        grid.addWidget(self._tube_row_label, self._ROW_TUBE, self._COL_ROW_LABEL)
        grid.addWidget(self._length_row_label, self._ROW_LENGTH, self._COL_ROW_LABEL)
        grid.addWidget(self._volume_row_label, self._ROW_SET_VOLUME, self._COL_ROW_LABEL)

        for channel in range(1, ACTIVE_PUMP_CHANNELS + 1):
            column = self.channel_column(channel)
            channel_header = QLabel(f"CH{channel}")
            channel_header.setAlignment(Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(channel_header, self._ROW_HEADER, column)

            flow_spin = make_compact_spinbox(QDoubleSpinBox())
            flow_spin.setRange(0.0, self._device_manager_settings.pump.max_flow_ul_min)
            flow_spin.setDecimals(0)
            flow_spin.setSuffix(" uL/min")
            flow_spin.setMaximumWidth(96)
            flow_spin.setToolTip(f"Flow rate for CH{channel}.")
            flow_spin.valueChanged.connect(lambda _value, ch=channel: self._on_channel_flow_changed(ch))
            grid.addWidget(flow_spin, self._ROW_PRIMARY, column)
            self._channel_flow_spins[channel] = flow_spin

            direction_button = _make_direction_toggle_button()
            direction_button.setVisible(False)
            grid.addWidget(direction_button, self._ROW_DIRECTION, column)
            self._channel_direction_buttons[channel] = direction_button

            tube_combo = TubeDiameterComboBox()
            tube_combo.setMaximumWidth(96)
            tube_combo.setVisible(False)
            tube_combo.valueChanged.connect(lambda _value: self._notify_tube_geometry_changed())
            grid.addWidget(tube_combo, self._ROW_TUBE, column)
            self._channel_tube_combos[channel] = tube_combo

            length_spin = make_compact_spinbox(QDoubleSpinBox())
            length_spin.setRange(1.0, 10_000.0)
            length_spin.setDecimals(0)
            length_spin.setSuffix(" mm")
            length_spin.setMaximumWidth(96)
            length_spin.setValue(DEFAULT_TUBE_LENGTH_MM)
            length_spin.setVisible(False)
            length_spin.valueChanged.connect(lambda _value: self._notify_tube_geometry_changed())
            grid.addWidget(length_spin, self._ROW_LENGTH, column)
            self._channel_length_spins[channel] = length_spin

            volume_spin = make_compact_spinbox(QDoubleSpinBox())
            volume_spin.setRange(0.0, _VOLUME_UL_MAX)
            volume_spin.setDecimals(0)
            volume_spin.setSuffix(" uL")
            volume_spin.setMaximumWidth(96)
            volume_spin.setToolTip(
                f"Set volume for CH{channel} this run - linked to Duration and this channel's Flow rate.\n"
                "Scroll/spin step matches the current flow rate (one step = about one minute's worth)."
            )
            volume_spin.valueChanged.connect(lambda _value, ch=channel: self._on_channel_volume_changed(ch))
            grid.addWidget(volume_spin, self._ROW_SET_VOLUME, column)
            self._channel_volume_spins[channel] = volume_spin

        self._notify_tube_geometry_changed()

    # ------------------------------------------------------------------
    # Equal / not-equal ("=" / "≠")
    # ------------------------------------------------------------------

    def _set_equal_mode(self, enabled: bool) -> None:
        self._equal_button.setText("=" if enabled else "≠")
        self._equal_button.setToolTip(
            "Shared direction/tube/length/flow for all channels." if enabled
            else "Independent per-channel direction/tube/length settings are visible."
        )
        self._shared_direction_button.setVisible(enabled)
        self._shared_tube_combo.setVisible(enabled)
        self._shared_length_spin.setVisible(enabled)
        self._dir_row_label.setVisible(not enabled)
        self._tube_row_label.setVisible(not enabled)
        self._length_row_label.setVisible(not enabled)
        for button in self._channel_direction_buttons.values():
            button.setVisible(not enabled)
        for combo in self._channel_tube_combos.values():
            combo.setVisible(not enabled)
        for spin in self._channel_length_spins.values():
            spin.setVisible(not enabled)

        # CH1's Flow/Set Volume stay the always-editable "source"; CH2-4
        # follow it (disabled) only in equal mode.
        for channel in range(2, ACTIVE_PUMP_CHANNELS + 1):
            self._channel_flow_spins[channel].setEnabled(not enabled)
            self._channel_volume_spins[channel].setEnabled(not enabled)

        if enabled:
            self._apply_shared_settings()
            self._copy_flow_from_channel_one()
        self._notify_tube_geometry_changed()

    def _apply_shared_settings(self, *_args: object) -> None:
        if not self._equal_button.isChecked():
            return
        direction = self._shared_direction_button.isChecked()
        tube_mm = self._shared_tube_combo.value()
        length_mm = self._shared_length_spin.value()
        for button in self._channel_direction_buttons.values():
            button.blockSignals(True)
            button.setChecked(direction)
            button.blockSignals(False)
        for combo in self._channel_tube_combos.values():
            combo.blockSignals(True)
            combo.setValue(tube_mm)
            combo.blockSignals(False)
        for spin in self._channel_length_spins.values():
            spin.blockSignals(True)
            spin.setValue(length_mm)
            spin.blockSignals(False)
        self._notify_tube_geometry_changed()

    def _copy_flow_from_channel_one(self) -> None:
        if not self._equal_button.isChecked():
            return
        flow_ul_min = self._channel_flow_spins[1].value()
        for channel in range(2, ACTIVE_PUMP_CHANNELS + 1):
            spin = self._channel_flow_spins[channel]
            spin.blockSignals(True)
            spin.setValue(flow_ul_min)
            spin.blockSignals(False)
            self._refresh_channel_volume_display(channel)

    # ------------------------------------------------------------------
    # Time unit (s/min/h) - same behavior as ExperimentControlWindow's
    # step_duration_spin/time_unit_toggle: Duration is always stored in
    # seconds (self._duration_seconds); the spinbox only ever shows/edits
    # the value converted into whichever unit is currently selected.
    # ------------------------------------------------------------------

    def _cycle_time_unit_mode(self) -> None:
        next_index = (_TIME_UNIT_ORDER.index(self._time_unit_mode) + 1) % len(_TIME_UNIT_ORDER)
        self._time_unit_mode = _TIME_UNIT_ORDER[next_index]
        self._update_time_unit_ui()

    def _update_time_unit_ui(self) -> None:
        self._time_unit_button.setText(self._time_unit_mode)
        self._time_unit_button.setToolTip(
            f"Current display unit: {self._time_unit_mode}. Click to cycle between seconds, "
            "minutes, and hours. Internally, Duration always stays in seconds."
        )
        config = _TIME_UNIT_SPIN_CONFIG[self._time_unit_mode]
        self._duration_spin.blockSignals(True)
        self._duration_spin.setDecimals(config["decimals"])
        self._duration_spin.setRange(0.0, config["max"])
        self._duration_spin.setSingleStep(config["step"])
        self._duration_spin.setValue(round(seconds_to_display_value(self._duration_seconds, self._time_unit_mode), config["decimals"]))
        self._duration_spin.blockSignals(False)

    def _set_duration_seconds(self, seconds: float, *, auto_select_unit: bool = False) -> None:
        self._duration_seconds = max(float(seconds), 0.0)
        if auto_select_unit:
            self._time_unit_mode = _auto_time_unit_for_seconds(self._duration_seconds)
        self._update_time_unit_ui()
        for channel in self._channel_flow_spins:
            self._refresh_channel_volume_display(channel)

    # ------------------------------------------------------------------
    # Duration / flow / volume linking
    # ------------------------------------------------------------------

    def _on_duration_spin_changed(self, value: float) -> None:
        self._duration_seconds = max(display_value_to_seconds(value, self._time_unit_mode), 0.0)
        for channel in self._channel_flow_spins:
            self._refresh_channel_volume_display(channel)

    def _on_channel_flow_changed(self, channel: int) -> None:
        self._refresh_channel_volume_display(channel)
        if channel == 1 and self._equal_button.isChecked():
            self._copy_flow_from_channel_one()

    def _on_channel_volume_changed(self, channel: int) -> None:
        flow_ul_min = self._channel_flow_spins[channel].value()
        if flow_ul_min <= 0.0:
            return
        volume_ml = self._channel_volume_spins[channel].value() / _UL_PER_ML
        new_duration_s = duration_s_from_flow_and_volume(flow_ul_min, volume_ml)
        if new_duration_s <= 0.0:
            return
        # Auto-select whichever unit (s/min/h) keeps the displayed number a
        # sensible size - editing Volume can easily push Duration well past
        # what's comfortable to read in seconds (or even past the 24h "s"
        # mode range), unlike a plain Duration edit where the user already
        # picked their preferred unit.
        self._set_duration_seconds(new_duration_s, auto_select_unit=True)
        for other_channel in self._channel_flow_spins:
            if other_channel != channel:
                self._refresh_channel_volume_display(other_channel)

    def _refresh_channel_volume_display(self, channel: int) -> None:
        flow_ul_min = self._channel_flow_spins[channel].value()
        volume_ml = volume_ml_from_flow_and_duration(flow_ul_min, self._duration_seconds)
        volume_spin = self._channel_volume_spins[channel]
        volume_spin.blockSignals(True)
        volume_spin.setValue(volume_ml * _UL_PER_ML)
        # Scroll/spin-arrow step tracks the current flow rate (one step is
        # about one minute's worth of dispensing at that rate) - keeps
        # scrolling meaningful relative to how fast this channel runs.
        volume_spin.setSingleStep(max(flow_ul_min, 1.0))
        volume_spin.blockSignals(False)

    def _notify_tube_geometry_changed(self) -> None:
        for callback in self.tube_geometry_changed:
            callback()

    # ------------------------------------------------------------------
    # Read-only accessors
    # ------------------------------------------------------------------

    def channel_direction(self, channel: int) -> str:
        return _direction_of(self._channel_direction_buttons[channel])

    def channel_tube_mm(self, channel: int) -> float:
        return self._channel_tube_combos[channel].value()

    def channel_tube_length_mm(self, channel: int) -> float:
        return self._channel_length_spins[channel].value()

    def channel_flow_ul_min(self, channel: int) -> float:
        return self._channel_flow_spins[channel].value()

    def channel_volume_ml(self, channel: int) -> float:
        return self._channel_volume_spins[channel].value() / _UL_PER_ML

    def duration_s(self) -> float:
        return self._duration_seconds


class _StepStatusSegment(QWidget):
    """One segment of the merged step/status bar: a header (step label + a
    single Start/Stop toggle button, right-aligned) directly above that
    step's own progress bar - the label and button only span this step's
    fraction of the overall bar, not the whole strip. Several of these
    sitting side by side read as one continuous progress bar."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.label = QLabel()
        self.toggle_button = QPushButton("Start")
        self.toggle_button.setAutoDefault(False)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.addWidget(self.label, 1)
        header_row.addWidget(self.toggle_button)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFixedHeight(22)
        # Explicit text-align/chunk styling - some platform styles otherwise
        # draw the percentage text beside/above the bar rather than
        # centered on top of the fill.
        self.progress_bar.setStyleSheet(
            "QProgressBar {"
            " border: 1px solid rgba(255, 255, 255, 60);"
            " border-radius: 4px;"
            " text-align: center;"
            " background-color: rgba(255, 255, 255, 12);"
            "}"
            "QProgressBar::chunk {"
            " background-color: #3b82f6;"
            " border-radius: 3px;"
            "}"
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(header_row)
        layout.addWidget(self.progress_bar)
        self.reset()

    def set_running(self, running: bool) -> None:
        self.toggle_button.setText("Stop" if running else "Start")

    def update_progress(self, fraction: float, remaining_s: float) -> None:
        percent = int(max(min(fraction, 1.0), 0.0) * 100.0)
        self.progress_bar.setValue(percent)
        self.progress_bar.setFormat(f"{percent}% - {format_mm_ss(remaining_s)} left")

    def reset(self) -> None:
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("0%")


class _StepStatusBar(QWidget):
    """Two (or more) _StepStatusSegments placed side by side, narrower than
    the full row width to leave room for a Calibrate button fixed at the
    right-hand end - together they read as one progress bar divided into
    per-step segments, with the (mostly disabled) Calibrate action always
    in the same place regardless of which step is showing progress."""

    def __init__(self, segment_count: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.segments: list[_StepStatusSegment] = []
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        for _ in range(segment_count):
            segment = _StepStatusSegment()
            self.segments.append(segment)
            layout.addWidget(segment, 1)

        self.calibrate_button = QPushButton("Calibrate")
        self.calibrate_button.setAutoDefault(False)
        self.calibrate_button.setEnabled(False)
        self.calibrate_button.setToolTip(
            "Compute and write the corrected roller-step volume for every channel with a\n"
            "non-zero measured volume. Channels left at 0 (or never measured) are skipped -\n"
            "their existing calibration is not touched."
        )
        layout.addWidget(self.calibrate_button)


class PumpCalibrationDialog(QDialog):
    """Pop-out window: the control row, then a merged step/status bar with
    "1. Fill tubes" / "2. Disperse liquid" (each its own Start/Stop toggle
    button directly above its own progress segment), then - once step 2
    finishes - a "Measured Volume"/"Deviation" row (added to the control
    row's own grid, so its columns line up with Set Volume) and a confirm
    button that writes corrected roller-step-volume constants. Non-modal,
    like DeviceManagerDialog itself, so it can stay open alongside the rest
    of the app."""

    _STEP_FILL_TUBES = 0
    _STEP_DISPERSE = 1
    _FILL_TUBES_DURATION_S = 60.0

    def __init__(
        self,
        device_manager_settings: DeviceManagerSettings,
        service: DeviceCommunicationService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pump Calibration")
        self._device_manager_settings = device_manager_settings
        self._service = service

        self._channel_target_volume_ml: dict[int, float] = {}
        self._channel_old_rsv_ml: dict[int, float] = {}

        self._active_step_index: int | None = None
        self._active_step_channels: list[int] = []
        self._active_step_duration_s = 0.0
        self._active_step_deadline = 0.0

        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(200)
        self._tick_timer.timeout.connect(self._tick_active_step)
        self._auto_stop_timer = QTimer(self)
        self._auto_stop_timer.setSingleShot(True)
        self._auto_stop_timer.timeout.connect(self._on_active_step_elapsed)

        self.control_row = PumpCalibrationControlRow(device_manager_settings, self)
        self.control_row.tube_geometry_changed.append(self._update_fill_tubes_label)

        self._status_bar = _StepStatusBar(2, self)
        fill_segment = self._status_bar.segments[self._STEP_FILL_TUBES]
        fill_segment.toggle_button.setToolTip(
            "Primes all 4 channels' tubing for one minute at a flow rate computed\n"
            "from tube diameter/length plus a 20% margin."
        )
        fill_segment.toggle_button.clicked.connect(self._on_fill_tubes_toggle_clicked)
        disperse_segment = self._status_bar.segments[self._STEP_DISPERSE]
        disperse_segment.label.setText("2. Disperse liquid.")
        disperse_segment.label.setToolTip(
            "Runs the control row's configured direction/tube/flow rate for each\n"
            "active channel (flow rate > 0), for the shared Duration above, then stops.\n"
            "Duration is shared across channels - set it generously relative to\n"
            "whichever active channel has the lowest flow rate, so that channel still\n"
            "dispenses a reasonably measurable volume."
        )
        disperse_segment.toggle_button.clicked.connect(self._on_disperse_toggle_clicked)
        self._status_bar.calibrate_button.clicked.connect(self._apply_corrections)

        self._build_measure_section()
        self._set_measure_section_visible(False)

        layout = QVBoxLayout(self)
        layout.addWidget(self.control_row)
        layout.addWidget(self._status_bar)
        layout.addStretch(1)

        self._update_fill_tubes_label()

    # ------------------------------------------------------------------
    # Step 1: Fill tubes
    # ------------------------------------------------------------------

    def _fill_tubes_rates_ul_min(self) -> dict[int, float]:
        return {
            channel: fill_flow_rate_ul_min(
                self.control_row.channel_tube_mm(channel), self.control_row.channel_tube_length_mm(channel),
            )
            for channel in range(1, ACTIVE_PUMP_CHANNELS + 1)
        }

    def _update_fill_tubes_label(self) -> None:
        rates = self._fill_tubes_rates_ul_min()
        unique_rates = {round(rate, 3) for rate in rates.values()}
        if len(unique_rates) == 1:
            rate_text = f"{next(iter(unique_rates)):.0f} uL/min"
        else:
            rate_text = ", ".join(f"CH{channel}: {rate:.0f}" for channel, rate in rates.items()) + " uL/min"
        self._status_bar.segments[self._STEP_FILL_TUBES].label.setText(f"1. Fill tubes using {rate_text} for one minute")

    def _on_fill_tubes_toggle_clicked(self) -> None:
        if self._active_step_index == self._STEP_FILL_TUBES:
            self._stop_active_step()
        else:
            self._start_fill_tubes()

    def _start_fill_tubes(self) -> None:
        if self._active_step_index is not None:
            return
        rates = self._fill_tubes_rates_ul_min()
        channels = list(range(1, ACTIVE_PUMP_CHANNELS + 1))
        label = device_label_for(PUMP)
        for channel in channels:
            self._service.send_command(
                label,
                DeviceCommand(
                    "pump.set_flow",
                    {
                        "channel": channel,
                        "flow_ul_min": rates[channel],
                        "direction": self.control_row.channel_direction(channel),
                        "tube_mm": self.control_row.channel_tube_mm(channel),
                        "backsteps": self._device_manager_settings.pump.backsteps,
                        "roller_count": self._device_manager_settings.pump.roller_count,
                        "start": True,
                    },
                ),
            )
        self._begin_timed_step(self._STEP_FILL_TUBES, channels, self._FILL_TUBES_DURATION_S, self._on_fill_tubes_finished)

    def _on_fill_tubes_finished(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Step 2: Disperse liquid
    # ------------------------------------------------------------------

    def _active_channels(self) -> list[int]:
        return [
            channel for channel in range(1, ACTIVE_PUMP_CHANNELS + 1)
            if self.control_row.channel_flow_ul_min(channel) > 0.0
        ]

    def _on_disperse_toggle_clicked(self) -> None:
        if self._active_step_index == self._STEP_DISPERSE:
            self._stop_active_step()
        else:
            self._start_disperse()

    def _start_disperse(self) -> None:
        if self._active_step_index is not None:
            return
        channels = self._active_channels()
        if not channels:
            QMessageBox.information(
                self, "Disperse liquid",
                "No channel has a flow rate above 0 - nothing to run.",
            )
            return
        label = device_label_for(PUMP)
        for channel in channels:
            self._channel_target_volume_ml[channel] = self.control_row.channel_volume_ml(channel)
            self._service.send_command(
                label,
                DeviceCommand(
                    "pump.set_flow",
                    {
                        "channel": channel,
                        "flow_ul_min": self.control_row.channel_flow_ul_min(channel),
                        "direction": self.control_row.channel_direction(channel),
                        "tube_mm": self.control_row.channel_tube_mm(channel),
                        "backsteps": self._device_manager_settings.pump.backsteps,
                        "roller_count": self._device_manager_settings.pump.roller_count,
                        "start": True,
                    },
                ),
            )
        self._begin_timed_step(self._STEP_DISPERSE, channels, self.control_row.duration_s(), self._on_disperse_finished)

    def _on_disperse_finished(self) -> None:
        label = device_label_for(PUMP)
        for channel in self._active_step_channels:
            result = self._service.send_command(label, DeviceCommand("pump.roller_step_volume.get", {"channel": channel}))
            if result.success:
                self._channel_old_rsv_ml[channel] = float(result.response)
        self._reveal_measure_section()

    # ------------------------------------------------------------------
    # Timed step machinery shared by fill-tubes/disperse
    # ------------------------------------------------------------------

    def _begin_timed_step(self, step_index: int, channels: list[int], duration_s: float, on_finished) -> None:
        self._active_step_index = step_index
        self._active_step_channels = channels
        self._active_step_duration_s = max(duration_s, 0.001)
        self._active_step_deadline = monotonic() + duration_s
        self._active_step_on_finished = on_finished
        self._status_bar.segments[step_index].reset()
        self._set_run_controls_enabled(step_index)
        self._tick_timer.start()
        self._auto_stop_timer.start(max(int(duration_s * 1000.0), 0))

    def _set_run_controls_enabled(self, running_step_index: int | None) -> None:
        idle = running_step_index is None
        for index, segment in enumerate(self._status_bar.segments):
            segment.set_running(index == running_step_index)
            segment.toggle_button.setEnabled(idle or index == running_step_index)

    def _tick_active_step(self) -> None:
        if self._active_step_index is None:
            return
        remaining_s = max(self._active_step_deadline - monotonic(), 0.0)
        fraction = 1.0 - (remaining_s / self._active_step_duration_s)
        self._status_bar.segments[self._active_step_index].update_progress(fraction, remaining_s)

    def _stop_active_step(self) -> None:
        """Manual Stop - ends the running step immediately rather than
        waiting for its Duration to elapse. Shares the same finishing logic
        as a normal timed completion (stop channels, read roller-step
        volume if it was Disperse, re-enable the Start/Stop buttons)."""
        if self._active_step_index is None:
            return
        self._auto_stop_timer.stop()
        self._on_active_step_elapsed()

    def _on_active_step_elapsed(self) -> None:
        if self._active_step_index is None:
            return
        self._tick_timer.stop()
        label = device_label_for(PUMP)
        for channel in self._active_step_channels:
            self._service.send_command(label, DeviceCommand("pump.stop", {"channel": channel}))
        self._status_bar.segments[self._active_step_index].update_progress(1.0, 0.0)
        finished_callback = self._active_step_on_finished
        self._active_step_index = None
        self._set_run_controls_enabled(None)
        finished_callback()

    # ------------------------------------------------------------------
    # Step 3: Enter measured volume + confirm
    # ------------------------------------------------------------------

    _ROW_MEASURE_TITLE = 6
    _ROW_MEASURED_VOLUME = 7
    _ROW_DEVIATION = 8

    def _build_measure_section(self) -> None:
        grid = self.control_row.grid
        label_col = PumpCalibrationControlRow._COL_ROW_LABEL
        last_col = self.control_row.channel_column(ACTIVE_PUMP_CHANNELS)

        title = QLabel("3. Input measured volume")
        title.setStyleSheet("font-weight: 700;")
        grid.addWidget(title, self._ROW_MEASURE_TITLE, 0, 1, last_col + 1)

        measured_label = QLabel("Measured Volume")
        deviation_label = QLabel("Deviation")
        grid.addWidget(measured_label, self._ROW_MEASURED_VOLUME, label_col)
        grid.addWidget(deviation_label, self._ROW_DEVIATION, label_col)

        self._measured_spins: dict[int, QDoubleSpinBox] = {}
        self._deviation_labels: dict[int, QLabel] = {}
        for channel in range(1, ACTIVE_PUMP_CHANNELS + 1):
            column = self.control_row.channel_column(channel)
            spin = make_compact_spinbox(QDoubleSpinBox())
            spin.setRange(0.0, _VOLUME_UL_MAX)
            spin.setDecimals(0)
            spin.setSuffix(" uL")
            spin.setMaximumWidth(96)
            spin.setToolTip("Leave at 0 to skip this channel - its existing calibration will not be touched.")
            spin.valueChanged.connect(lambda _value: self._update_calibrate_button_enabled())
            grid.addWidget(spin, self._ROW_MEASURED_VOLUME, column)
            self._measured_spins[channel] = spin

            deviation_value_label = QLabel("-")
            grid.addWidget(deviation_value_label, self._ROW_DEVIATION, column)
            self._deviation_labels[channel] = deviation_value_label

        self._measure_widgets: list[QWidget] = [
            title, measured_label, deviation_label,
            *self._measured_spins.values(), *self._deviation_labels.values(),
        ]

    def _set_measure_section_visible(self, visible: bool) -> None:
        for widget in self._measure_widgets:
            widget.setVisible(visible)

    def _reveal_measure_section(self) -> None:
        for channel in range(1, ACTIVE_PUMP_CHANNELS + 1):
            target_ml = self._channel_target_volume_ml.get(channel)
            spin = self._measured_spins[channel]
            deviation_label = self._deviation_labels[channel]
            has_target = target_ml is not None
            spin.setEnabled(has_target)
            deviation_label.setText("-")
            deviation_label.setStyleSheet(f"color: {_COLOR_MUTED};")
            deviation_label.setToolTip("")
            if has_target:
                spin.blockSignals(True)
                spin.setValue(target_ml * _UL_PER_ML)
                spin.blockSignals(False)
        self._set_measure_section_visible(True)
        self._update_calibrate_button_enabled()

    def _update_calibrate_button_enabled(self) -> None:
        ready = any(
            self._channel_old_rsv_ml.get(channel) is not None
            and self._channel_target_volume_ml.get(channel)
            and self._measured_spins[channel].value() != 0.0
            for channel in range(1, ACTIVE_PUMP_CHANNELS + 1)
        )
        self._status_bar.calibrate_button.setEnabled(ready)

    def _apply_corrections(self) -> None:
        label = device_label_for(PUMP)
        applied_any = False
        for channel in range(1, ACTIVE_PUMP_CHANNELS + 1):
            old_rsv_ml = self._channel_old_rsv_ml.get(channel)
            target_ml = self._channel_target_volume_ml.get(channel)
            measured_ml = self._measured_spins[channel].value() / _UL_PER_ML
            # A channel left at 0 (or never measured) is a deliberate skip -
            # don't touch its existing roller-step-volume calibration.
            if old_rsv_ml is None or not target_ml or not measured_ml:
                continue
            applied_any = True
            new_rsv_ml = corrected_roller_step_volume_ml(old_rsv_ml, target_ml, measured_ml)
            self._service.send_command(
                label, DeviceCommand("pump.roller_step_volume.set", {"channel": channel, "volume_ml": new_rsv_ml}),
            )
            pct = deviation_pct(target_ml, measured_ml)
            deviation_label = self._deviation_labels[channel]
            deviation_label.setText(f"{pct:+.2f}%")
            deviation_label.setStyleSheet(f"color: {deviation_color(pct)}; font-weight: 600;")
            deviation_label.setToolTip(deviation_tooltip(pct))
        if not applied_any:
            QMessageBox.information(
                self, "Calibrate",
                "No channels are ready to calibrate - run \"Disperse liquid\" and enter a non-zero "
                "measured volume for at least one channel first.",
            )
        self._update_calibrate_button_enabled()
