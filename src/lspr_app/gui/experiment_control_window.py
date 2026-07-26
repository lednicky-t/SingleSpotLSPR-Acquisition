from __future__ import annotations

import logging
import math
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from time import monotonic, perf_counter
from typing import Callable

try:
    import h5py
except ImportError:  # pragma: no cover - optional dependency guard
    h5py = None

try:
    import yaml
except ImportError:  # pragma: no cover - optional dependency guard
    yaml = None

from PyQt6.QtCore import QByteArray, QRect, QSize, QThreadPool, QTimer, Qt, QEvent, QModelIndex, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap, QUndoStack
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QFileDialog,
    QScrollArea,
    QSplitter,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lspr_app.device.device_lifecycle import DeviceLifecycleController, device_label_for
from lspr_app.device.device_manager import DeviceCommunicationService
from lspr_app.device.device_types import PUMP, SWITCH, SELECTOR
from lspr_app.device.communication_models import DeviceCommand
from lspr_app.device.serial_controllers import ControllerProbe
from lspr_app.device.reglo_icc import PumpProbe
from lspr_app import __version__
from lspr_app.gui.experiment_control_runtime import ExperimentRuntimeSnapshot, experiment_runtime_snapshot
from lspr_app.resources import app_icon_path
from lspr_app.domain.pump_plan import (
    ACTIVE_PUMP_CHANNELS,
    DEFAULT_TUBE_MM,
    HDF5_PUMP_CHANNELS,
    PumpChannelStep,
    PumpPlanStep,
    recompute_plan_timing,
    to_core_experiment_plan,
)
from lspr_app.gui.experiment_control_builders import (
    create_direction_button,
    create_flow_step_action_button,
    direction_glyph,
    set_step_valve_button_state_for_button,
)
from lspr_app.gui.experiment_control_table import (
    configure_experiment_control_table_columns,
    configure_experiment_control_plan_table,
    fit_plan_table_columns_to_viewport,
    sync_experiment_control_tube_columns,
    update_plan_detail_toggle_icon,
    update_plan_table_height,
)
from lspr_app.gui.experiment_control_editing import ExperimentControlEditingController
from lspr_app.gui.experiment_control_dialogs import ExperimentControlDialogs
from lspr_app.gui.experiment_control_controller import ExperimentControlController
from lspr_app.gui.experiment_control_backend import AcquisitionExperimentControlBackend, ExperimentControlBackend, NullExperimentControlBackend
from lspr_app.gui.experiment_control_capabilities import ExperimentControlCapabilities
from lspr_app.gui.experiment_control_timeline import PumpPlanTimelineWidget
from lspr_app.gui.experiment_control_import import (
    ExperimentPlanImportData,
    ExperimentPlanImportTask,
    build_experiment_plan_steps_from_import_data,
    _safe_float,
    _safe_int,
)
from lspr_app.gui.experiment_control_export import (
    ExperimentPlanExportData,
    ExperimentPlanExportTask,
)
from lspr_app.gui.device_lifecycle_task import DevicePortRefreshTask, device_io_pool
from lspr_app.gui.experiment_control_widgets import (
    ExperimentControlTableView,
    _NoFocusItemDelegate,
    _make_frameless_icon_button,
)
from lspr_app.gui.experiment_control_step_runner import (
    _PlannedCommand,
    _StepApplyResult,
    _StepApplyRunnable,
)
from lspr_app.gui.icon_helpers import flow_tabler_icon, tint_tabler_icon, transport_icon
from lspr_app.gui.panel_help import make_help_button
from lspr_app.gui.panel_help_text import EXPERIMENT_CONTROL_BODY, EXPERIMENT_CONTROL_TITLE, EXPERIMENT_CONTROL_TOOLTIP
from lspr_app.gui.ui_helpers import make_compact_spinbox
from lspr_app.gui.undo_support import push_snapshot
from lspr_app.storage.app_config import load_app_setting, save_app_setting, save_window_ui_state
from lspr_io import (
    build_legacy_experiment_plan_row_table,
)


_LOGGER = logging.getLogger("lspr_app.experiment_control")


class ExperimentControlWindow(QWidget):
    availability_changed = pyqtSignal(object)
    valve_availability_changed = pyqtSignal(object)
    mswitch_availability_changed = pyqtSignal(object)
    recording_control_requested = pyqtSignal(str)
    experimental_control_state_recorded = pyqtSignal(object)
    flow_state_recorded = experimental_control_state_recorded
    theme_changed = pyqtSignal(str)
    # No payload - just tells the titlebar strip a device's BUSY status may
    # have changed. Fired at both the start and end of a step-apply dispatch
    # (see _apply_step_to_pump_async/_on_step_apply_async_done), since a
    # "step applied" event only fires on completion and the busy dot needs
    # to appear immediately, not just after the command finishes.
    hw_status_refresh_requested = pyqtSignal()
    PLAN_COLOR_OPTIONS = [
        ("Blue", "#4E79A7"),
        ("Green", "#59A14F"),
        ("Red", "#E15759"),
        ("Orange", "#F28E2B"),
        ("Purple", "#B07AA1"),
        ("Teal", "#76B7B2"),
        ("Gold", "#EDC948"),
        ("Gray", "#9C9DA1"),
    ]
    PLAN_COLUMNS = [
        "step",
        "duration_s",
        "start_s",
        "end_s",
        *[
            item
            for channel_index in range(ACTIVE_PUMP_CHANNELS)
            for item in (
                f"ch{channel_index + 1}_flow_ul_min",
                f"ch{channel_index + 1}_direction",
                f"ch{channel_index + 1}_tube_mm",
            )
        ],
        "valve",
        "switch",
        "color",
        "description",
    ]

    def __init__(
        self,
        ui_state: dict[str, object],
        known_probe: PumpProbe | None = None,
        theme_mode: str | None = None,
        initial_mswitch_devices: list[ControllerProbe] | None = None,
        auto_connect_devices: bool = False,
        show_runtime_controls: bool = True,
        capabilities: ExperimentControlCapabilities | None = None,
        backend: ExperimentControlBackend | None = None,
        parent: QWidget | None = None,
        undo_stack: QUndoStack | None = None,
    ) -> None:
        super().__init__(parent)
        # Shared with MainWindow when embedded in the real app (see
        # main_window_lifecycle.py) so Ctrl+Z has one continuous history across
        # the whole app. Falls back to a private stack so this widget still
        # works when constructed standalone (tests, tools).
        self.undo_stack = undo_stack if undo_stack is not None else QUndoStack(self)
        self._bootstrap_t0 = perf_counter()
        self._bootstrap_batches_logged = 0
        self._ui_state = ui_state
        self._capabilities = capabilities or ExperimentControlCapabilities.acquisition()
        self._backend = backend or AcquisitionExperimentControlBackend(self)
        self._experiment_control_controller = ExperimentControlController(self, self._backend, self._capabilities)
        self._device_comm_service = DeviceCommunicationService.shared()
        self._pause_state_dialog_state: dict[str, object] = {}
        self._start_maximized = False
        self._updating_table = False
        self._plan_table_active_editor: tuple[int, int] | None = None
        self._client = None
        self._probe: PumpProbe | None = known_probe
        self._thread_pool = QThreadPool.globalInstance()
        self._valve_probe: ControllerProbe | None = None
        self._mswitch_probe: ControllerProbe | None = None
        self._mswitch_probe_cache: list[ControllerProbe] | None = list(initial_mswitch_devices or [])
        self._auto_connect_devices = bool(auto_connect_devices)
        self._show_runtime_controls = bool(show_runtime_controls and self._capabilities.show_runtime_buttons)
        self._ui_startup_ready = False
        self._plan_running = False
        self._plan_holding = False
        self._plan_paused = False
        self._plan_hold_blink_frame = 0
        self._plan_hold_blink_timer = QTimer(self)
        self._plan_hold_blink_timer.setInterval(110)
        self._plan_hold_blink_timer.timeout.connect(self._advance_plan_hold_blink_indicator)
        self._paused_plan_step: PumpPlanStep | None = None
        self._plan_elapsed_s = 0.0
        self._plan_resume_elapsed_s = 0.0
        self._plan_runtime_s = 0.0
        self._plan_resume_runtime_s = 0.0
        self._plan_started_monotonic: float | None = None
        self._step_started_monotonic: float | None = None
        self._measurement_started_monotonic: float | None = None
        self._pending_experiment_control_start_after_recording: tuple[bool, int | None] | None = None
        self._plan_active_row: int | None = None
        self._applied_plan_step: PumpPlanStep | None = None
        self._status_message_base = "Pump not connected."
        self._show_plan_details = bool(ui_state.get("show_plan_details", False))
        # Global, not per-step: applies to every step's comment when it's applied to
        # hardware. See docs/experiment-control/pump_control_guide.md "Pump Display".
        self._pump_display_enabled = bool(ui_state.get("pump_display_enabled", False))
        self._pump_display_highlight_enabled = self._pump_display_enabled and bool(
            ui_state.get("pump_display_highlight_enabled", False)
        )
        self._editor_duration_seconds = 60.0
        self._suspend_duration_tracking = False
        self._updating_switch_editor = False
        self._experiment_control_steps_cache: list[PumpPlanStep] = []
        self._switch_solution_mode = bool(ui_state.get("switch_solution_mode", False))
        self._wait_for_mswitch_first = bool(ui_state.get("wait_for_mswitch_first", False))
        self._valve_state_labels = self._load_valve_state_labels(ui_state)
        self._valve_state_colors = self._load_valve_state_colors(ui_state)
        self._switch_solution_labels = [
            "empty"
            for index in range(1, 13)
        ]
        self._color_palette_entries = self._load_color_palette_entries(ui_state)
        self._sync_custom_plan_colors_from_palette()
        self._tint_icon = tint_tabler_icon
        self._experiment_plan_import_generation = 0
        self._experiment_plan_import_task: ExperimentPlanImportTask | None = None
        self._experiment_plan_import_in_progress = False
        self._experiment_plan_import_pending_steps: list[PumpPlanStep] = []
        self._experiment_plan_import_pending_payload: ExperimentPlanImportData | None = None
        self._experiment_plan_import_pending_selected_row: int | None = None
        self._experiment_plan_import_pending_step_index = 0
        self._experiment_plan_import_pending_batch_size = 24
        self._experiment_plan_export_generation = 0
        self._experiment_plan_export_task: ExperimentPlanExportTask | None = None
        self._experiment_plan_export_in_progress = False
        self._experiment_control_bootstrap_in_progress = False
        self._experiment_control_bootstrap_started = False
        self._experiment_control_bootstrap_pending_steps: list[PumpPlanStep] = []
        self._experiment_control_bootstrap_pending_row_order: list[int] = []
        self._experiment_control_bootstrap_pending_selected_row: int | None = None
        self._experiment_control_bootstrap_pending_pause_selected = False
        self._experiment_control_bootstrap_pending_state: dict[str, object] | None = None
        self._experiment_control_bootstrap_pending_step_index = 0
        self._experiment_control_bootstrap_batch_size = 24
        self._experiment_control_view_mode_sizes: dict[str, list[int]] = {}
        self._experiment_control_view_mode_panel_sizes: dict[str, list[int]] = {}
        self._experiment_control_view_mode_apply_pending = False
        self._experiment_control_visible_rows_timer = QTimer(self)
        self._experiment_control_visible_rows_timer.setSingleShot(True)
        self._experiment_control_visible_rows_timer.setInterval(0)
        self._experiment_control_visible_rows_timer.timeout.connect(self._load_visible_experiment_control_rows)
        self._experiment_control_loaded_widget_rows: set[int] = set()
        self._experiment_control_pause_template = PumpPlanStep(
            step=0,
            duration_s=0.0,
            color=self._default_experiment_control_color(0),
            valve="Open",
            switch_position=1,
            description="Pause",
            channels=[PumpChannelStep() for _ in range(ACTIVE_PUMP_CHANNELS)],
        )
        self._plan_timer = QTimer(self)
        self._plan_timer.setSingleShot(True)
        self._plan_timer.timeout.connect(self._advance_experiment_control_progress)
        # Count, not a bool: multiple step-applies can legitimately overlap
        # once every GUI trigger dispatches async (not just auto-advance) -
        # e.g. a manual step jump fired while an earlier dispatch's switch
        # rotary valve move is still in flight on device_io_pool(). Each dispatch's own
        # on_success callback is carried on its _StepApplyResult, not a
        # shared queue here, so overlap can't mix up which callback belongs
        # to which completion.
        self._step_apply_inflight = 0
        loaded_theme = str(theme_mode or load_app_setting("theme_mode", "dark"))
        self._theme_mode = "dark" if loaded_theme not in {"light", "dark"} else loaded_theme
        if self._theme_mode != "dark":
            self._theme_mode = "dark"
            save_app_setting("theme_mode", self._theme_mode)
        loaded_time_unit = str(ui_state.get("time_unit_mode", "s"))
        self._time_unit_mode = loaded_time_unit if loaded_time_unit in {"s", "min", "h"} else "s"
        self._experiment_control_view_mode_sizes = self._load_experiment_control_view_mode_sizes(ui_state)
        self._experiment_control_view_mode_panel_sizes = self._load_experiment_control_view_mode_panel_sizes(ui_state)
        self._experiment_control_view_mode = self._normalize_experiment_control_view_mode(
            ui_state.get("experiment_control_view_mode", "full")
        )
        self._experiment_control_timeline_label_mode = self._normalize_experiment_control_timeline_label_mode(
            ui_state.get("timeline_label_mode", "comment")
        )
        legacy_sizes = ui_state.get("experiment_control_editor_splitter_sizes")
        if legacy_sizes is None:
            legacy_sizes = ui_state.get("flow_editor_splitter_sizes")
        if not self._experiment_control_view_mode_sizes and isinstance(legacy_sizes, list):
            parsed_legacy: list[int] = []
            for value in legacy_sizes[:2]:
                try:
                    parsed_legacy.append(max(int(value), 20))
                except (TypeError, ValueError):
                    parsed_legacy = []
                    break
            if len(parsed_legacy) == 2:
                self._experiment_control_view_mode_sizes[self._experiment_control_view_mode] = parsed_legacy
        self._experiment_control_view_mode_apply_pending = True

        _LOGGER.info("Experiment control bootstrap +%.1f ms: init state prepared", (perf_counter() - self._bootstrap_t0) * 1000.0)

        self.setWindowTitle(f"Experiment Control {__version__}")
        self.setWindowIcon(QIcon(str(app_icon_path())))
        self.resize(1220, 860)
        self._apply_style()

        # Shared status line for the whole panel (plan run/pause/stop feedback,
        # import/export progress, etc.) - also carries pump connection messages
        # from sync_from_lifecycle_controller(). See _set_status_message/_refresh_status_line.
        self.connection_status_label = QLabel("Pump not connected.", self)
        self.connection_status_label.setWordWrap(True)

        self.manual_flow_spins: list[QDoubleSpinBox] = []
        self.manual_direction_buttons: list[QToolButton] = []
        self.manual_tube_spins: list[QDoubleSpinBox] = []
        self.shared_direction_button = create_direction_button(self, "CW")
        self.shared_tube_spin = QDoubleSpinBox(self)
        make_compact_spinbox(self.shared_tube_spin)
        self.shared_tube_spin.setRange(0.13, 3.17)
        self.shared_tube_spin.setDecimals(2)
        self.shared_tube_spin.setSingleStep(0.01)
        self.shared_tube_spin.setValue(DEFAULT_TUBE_MM)
        self.shared_tube_spin.setSuffix("")
        self.manual_uniform_button = QToolButton(self)
        self.manual_uniform_button.setCheckable(True)
        self.manual_uniform_button.setChecked(True)
        self.plan_detail_toggle = QToolButton(self)
        self.plan_detail_toggle.setObjectName("flowStepActionButton")
        self.plan_detail_toggle.setCheckable(True)
        self.plan_detail_toggle.setChecked(self._show_plan_details)
        self.plan_detail_toggle.setAutoRaise(True)
        self.plan_detail_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.plan_detail_toggle.setFixedSize(32, 32)
        self.plan_detail_toggle.setIconSize(QSize(24, 24))
        self.plan_detail_toggle.setStyleSheet(
            "QToolButton#flowStepActionButton { background: transparent; border: none; padding: 0px; margin: 0px; }"
            "QToolButton#flowStepActionButton:hover { background: rgba(127, 127, 127, 0.10); border: none; }"
            "QToolButton#flowStepActionButton:pressed { background: rgba(127, 127, 127, 0.18); border: none; }"
        )
        self._update_plan_detail_toggle_icon()
        self.pause_state_button = self._make_icon_button(
            self._pause_state_button_icon(),
            "Edit the synthetic pause state applied when the plan enters pause mode.",
        )
        self.color_comment_button = QToolButton(self)
        self.color_comment_button.setObjectName("flowStepActionButton")
        self.color_comment_button.setAutoRaise(True)
        self.color_comment_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.color_comment_button.setFixedSize(32, 32)
        self.color_comment_button.setIconSize(QSize(24, 24))
        self.color_comment_button.setIcon(tint_tabler_icon(flow_tabler_icon("arrow_move_right"), QColor("#9b7dff")))
        self.color_comment_button.setToolTip("Copy each row color name into the comment field.")
        self.color_comment_button.setStyleSheet(
            "QToolButton#flowStepActionButton { background: transparent; border: none; padding: 0px; margin: 0px; }"
            "QToolButton#flowStepActionButton:hover { background: rgba(127, 127, 127, 0.10); border: none; }"
            "QToolButton#flowStepActionButton:pressed { background: rgba(127, 127, 127, 0.18); border: none; }"
        )
        self.step_duration_spin = QDoubleSpinBox(self)
        make_compact_spinbox(self.step_duration_spin)
        self.step_duration_spin.setRange(0.0, 86400.0)
        self.step_duration_spin.setDecimals(1)
        self.step_duration_spin.setSingleStep(5.0)
        self.step_duration_spin.setValue(60.0)
        self.step_duration_spin.setSuffix(" s")
        self.time_unit_toggle = QToolButton(self)
        self.time_unit_toggle.setMinimumWidth(34)
        self.time_unit_toggle.setMaximumWidth(42)
        self.time_unit_toggle.setToolTip("Cycle time display/editing between seconds, minutes, and hours. Internally and in saved data, times stay in seconds.")
        self.color_palette_button = QToolButton(self)
        self.color_palette_button.setObjectName("flowColorAddButton")
        self.color_palette_button.setAutoRaise(True)
        self.color_palette_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.color_palette_button.setIcon(tint_tabler_icon(flow_tabler_icon("settings"), QColor("#f0f3f7")))
        self.color_palette_button.setIconSize(QSize(21, 21))
        self.color_palette_button.setToolTip("Edit and overwrite the color palette used by the dropdown.")
        self.remove_custom_color_button = QToolButton(self)
        self.remove_custom_color_button.setObjectName("flowColorRemoveButton")
        self.remove_custom_color_button.setAutoRaise(True)
        self.remove_custom_color_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.remove_custom_color_button.setIcon(tint_tabler_icon(flow_tabler_icon("x"), QColor("#b44a4a")))
        self.remove_custom_color_button.setIconSize(QSize(21, 21))
        self.remove_custom_color_button.setToolTip("Remove the selected palette entry.")
        self.remove_custom_color_button.setVisible(False)
        self.step_color_combo = QComboBox(self)
        self.step_color_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self._populate_color_combo(self.step_color_combo)
        self.step_color_combo.setToolTip("Step color used in the plan timeline for quick visual identification.")
        self._install_click_to_open_combo_filter(self.step_color_combo)
        self.step_valve_button = QToolButton(self)
        self.step_valve_button.setToolTip("Valve state to associate with this step. Click to toggle between Open and Close.")
        self.step_valve_button.setCheckable(True)
        self.step_valve_button.setAutoRaise(True)
        self.step_valve_settings_button = QToolButton(self)
        self.step_valve_settings_button.setObjectName("flowValveSettingsButton")
        self.step_valve_settings_button.setAutoRaise(True)
        self.step_valve_settings_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.step_valve_settings_button.setIcon(tint_tabler_icon(flow_tabler_icon("settings"), QColor("#f0f3f7")))
        self.step_valve_settings_button.setIconSize(QSize(20, 20))
        self.step_valve_settings_button.setToolTip("Edit the text labels used for valve states.")
        self._set_step_valve_button_state("Open")
        self.step_valve_button.clicked.connect(self._toggle_step_valve_button)
        self.step_switch_spin = QSpinBox(self)
        make_compact_spinbox(self.step_switch_spin)
        self.step_switch_spin.setRange(1, 12)
        self.step_switch_spin.setValue(1)
        self.step_switch_spin.setFixedWidth(96)
        self.step_switch_spin.setToolTip("AMF switch position for this step. Select a port from 1 to 12.")
        self.step_switch_combo = QComboBox(self)
        self.step_switch_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.step_switch_combo.setMinimumWidth(148)
        self.step_switch_combo.setToolTip("AMF switch position and solution for this step.")
        self.step_switch_combo.currentIndexChanged.connect(self._handle_step_switch_combo_changed)
        self.step_switch_combo.setVisible(False)
        self.step_switch_mode_button = QToolButton(self)
        self.step_switch_mode_button.setObjectName("flowSwitchModeButton")
        self.step_switch_mode_button.setAutoRaise(True)
        self.step_switch_mode_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.step_switch_mode_button.setIconSize(QSize(20, 20))
        self.step_switch_mode_button.toggled.connect(self._set_switch_solution_mode)
        self.step_switch_mode_button.setCheckable(True)
        self.step_switch_mode_button.blockSignals(True)
        self.step_switch_mode_button.setChecked(False)
        self.step_switch_mode_button.blockSignals(False)
        self.step_switch_mode_button.setVisible(False)
        self.step_switch_settings_button = QToolButton(self)
        self.step_switch_settings_button.setObjectName("flowSwitchSettingsButton")
        self.step_switch_settings_button.setAutoRaise(True)
        self.step_switch_settings_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.step_switch_settings_button.setIcon(tint_tabler_icon(flow_tabler_icon("settings"), QColor("#f0f3f7")))
        self.step_switch_settings_button.setIconSize(QSize(20, 20))
        self.step_switch_settings_button.setToolTip("Edit the switch solution labels.")
        self.step_comment_display_button = QToolButton(self)
        self.step_comment_display_button.setObjectName("flowCommentDisplayButton")
        self.step_comment_display_button.setAutoRaise(True)
        self.step_comment_display_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.step_comment_display_button.setIcon(tint_tabler_icon(flow_tabler_icon("settings"), QColor("#f0f3f7")))
        self.step_comment_display_button.setIconSize(QSize(20, 20))
        self.step_comment_display_button.setToolTip(
            "Show all step comments on the pump display, and preview the 16-character limit."
        )
        self.step_comment_edit = QLineEdit(self)
        self.step_comment_edit.setPlaceholderText("Comment")
        self.step_comment_edit.setToolTip("Free-text note for the step. It is shown in the timeline when there is enough space.")
        self.plan_toggle_button = self._make_icon_button(
            transport_icon(self._theme_mode, "play"),
            "Run or resume plan",
        )
        self.plan_toggle_button.setToolTip("Run or resume the plan.")
        self.hold_plan_button = self._make_icon_button(
            transport_icon(self._theme_mode, "hold"),
            "Hold plan",
        )
        self.hold_plan_button.setCheckable(True)
        self.pause_plan_button = self._make_icon_button(
            self._runtime_pause_button_icon(),
            "Pause plan",
        )
        self.pause_plan_button.setCheckable(True)
        self.stop_plan_button = self._make_icon_button(
            transport_icon(self._theme_mode, "stop"),
            "Stop plan",
        )
        self.previous_step_button = self._make_icon_button(
            transport_icon(self._theme_mode, "previous"),
            "Previous step",
        )
        self.next_step_button = self._make_icon_button(
            transport_icon(self._theme_mode, "next"),
            "Next step",
        )

        self.plan_table = ExperimentControlTableView()
        self.plan_table.setObjectName("flowControlTable")
        # The plan table setup is centralized so the view, model, delegates, and layout rules stay in one place.
        configure_experiment_control_plan_table(self)

        self.pause_table = QTableWidget(self)
        self.pause_table.setObjectName("flowControlPauseTable")
        self.pause_table.setColumnCount(len(self.PLAN_COLUMNS))
        self.pause_table.setHorizontalHeaderLabels(self.PLAN_COLUMNS)
        self.pause_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pause_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.pause_table.verticalHeader().setVisible(False)
        self.pause_table.verticalHeader().setDefaultSectionSize(20)
        self.pause_table.horizontalHeader().setMinimumHeight(20)
        self.pause_table.horizontalHeader().setMaximumHeight(20)
        self.pause_table.setAlternatingRowColors(True)
        self.pause_table.horizontalHeader().setStretchLastSection(False)
        self.pause_table.setWordWrap(False)
        self.pause_table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.pause_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pause_table.setItemDelegate(_NoFocusItemDelegate(self.pause_table))
        pause_palette = self.pause_table.palette()
        for group in (
            QPalette.ColorGroup.Active,
            QPalette.ColorGroup.Inactive,
            QPalette.ColorGroup.Disabled,
        ):
            pause_palette.setColor(group, QPalette.ColorRole.Highlight, QColor(0, 0, 0, 0))
            pause_palette.setColor(group, QPalette.ColorRole.HighlightedText, QColor(self._theme_palette()["fg"]))
        self.pause_table.setPalette(pause_palette)
        self.pause_table.viewport().setPalette(pause_palette)
        self.pause_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.pause_table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.pause_table.setMaximumHeight(self.pause_table.verticalHeader().defaultSectionSize() + self.pause_table.horizontalHeader().height() + 6)
        self.pause_table.setFrameShape(QFrame.Shape.NoFrame)
        self.pause_table.setShowGrid(True)
        self.pause_table.setVisible(False)
        self._plan_table_layout_save_timer = QTimer(self)
        self._plan_table_layout_save_timer.setSingleShot(True)
        self._plan_table_layout_save_timer.setInterval(150)
        self._plan_table_layout_save_timer.timeout.connect(self.save_ui_state)
        self._plan_table_fit_timer = QTimer(self)
        self._plan_table_fit_timer.setSingleShot(True)
        self._plan_table_fit_timer.setInterval(0)
        self._plan_table_fit_timer.timeout.connect(self._fit_plan_table_columns_to_viewport)
        self._suppress_plan_table_layout_save = True
        self._plan_table_layout_locked = False
        self._plan_table_initial_fit_pending = True
        self._experiment_control_edit_mode = False
        self._flow_editor_splitter_initialized = False

        self.add_step_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("square_plus"), QColor("#47a861")),
            "Add a step after the selected row.",
        )
        self.duplicate_step_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("copy"), QColor("#4f88ff")),
            "Duplicate the selected step.",
        )
        self.remove_step_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("trash"), QColor("#b44a4a")),
            "Remove the selected step.",
        )
        self.apply_step_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("edit"), QColor("#e8d85f")),
            "Toggle table edit mode.",
        )
        self.apply_step_button.setCheckable(True)
        self.apply_step_button.toggled.connect(lambda checked: self._set_experiment_control_edit_mode_button_icon(bool(checked)))
        self._experiment_control_edit_controller = ExperimentControlEditingController(self, self.plan_table, self.apply_step_button)
        self.import_plan_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("file_import"), QColor("#66d48a")),
            "Import a experiment plan from CSV or TXT.",
        )
        self.export_plan_button = create_flow_step_action_button(
            tint_tabler_icon(flow_tabler_icon("file_export"), QColor("#8fbaff")),
            "Export the current experiment plan to CSV or TXT.",
        )
        self.import_plan_busy_label = QLabel("â—", self)
        self.import_plan_busy_label.setVisible(False)
        self.import_plan_busy_label.setFixedSize(16, 16)
        self.import_plan_busy_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.import_plan_busy_label.setToolTip("Import is running.")
        self._import_plan_busy_frames = ["â—", "â—“", "â—‘", "â—’"]
        self._import_plan_busy_frame_index = 0
        self._import_plan_busy_timer = QTimer(self)
        self._import_plan_busy_timer.setInterval(140)
        self._import_plan_busy_timer.timeout.connect(self._advance_import_plan_busy_indicator)
        self._experiment_plan_import_fill_timer = QTimer(self)
        self._experiment_plan_import_fill_timer.setInterval(0)
        self._experiment_plan_import_fill_timer.timeout.connect(self._advance_experiment_plan_import_population)
        self.record_with_flow_button = QToolButton(self)
        self.record_with_flow_button.setObjectName("flowStepActionButton")
        self.record_with_flow_button.setAutoRaise(True)
        self.record_with_flow_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.record_with_flow_button.setFixedSize(32, 32)
        self.record_with_flow_button.setIconSize(QSize(24, 24))
        self.record_with_flow_button.setToolTip("Record measurement data while the experiment plan runs.")
        self.record_with_flow_button.setStyleSheet(
            "QToolButton#flowStepActionButton { background: transparent; border: none; padding: 0px; margin: 0px; }"
            "QToolButton#flowStepActionButton:hover { background: rgba(127, 127, 127, 0.10); border: none; }"
            "QToolButton#flowStepActionButton:pressed { background: rgba(127, 127, 127, 0.18); border: none; }"
        )
        self.record_with_flow_button.setCheckable(True)
        self.record_with_flow_button.setChecked(True)
        self.record_with_flow_button.toggled.connect(self._handle_record_with_flow_button_toggled)
        self._record_with_flow_recording_active = False
        self._record_with_flow_locked_checked = bool(self.record_with_flow_button.isChecked())
        self._update_record_with_flow_button_icon()
        self.timeline_widget = PumpPlanTimelineWidget()
        self.timeline_widget.set_theme(self._theme_mode)
        self.timeline_widget.set_theme_palette(self._theme_palette())
        self.timeline_widget.set_time_unit_mode(self._time_unit_mode)
        self.timeline_widget.set_color_palette_entries(self._color_palette_entries)
        self.timeline_widget.set_label_mode(self._experiment_control_timeline_label_mode)
        self.timeline_widget.label_mode_toggled.connect(self._cycle_experiment_control_timeline_label_mode)
        self.timeline_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._startup_ui_pending = True
        self._build_ui()
        _LOGGER.info("Experiment control bootstrap +%.1f ms: UI built", (perf_counter() - self._bootstrap_t0) * 1000.0)
        self._refresh_switch_solution_combo(self.step_switch_spin.value())
        self._set_switch_solution_mode(self._switch_solution_mode)
        self._connect_signals()
        self._apply_capabilities_to_ui()
        self._update_time_unit_ui()
        self._experiment_control_edit_controller.set_edit_mode(False)
        self._update_experiment_control_toggle_button()
        self._restore_ui_state()
        self._restore_experiment_control_state()
        _LOGGER.info("Experiment control bootstrap +%.1f ms: state restore queued", (perf_counter() - self._bootstrap_t0) * 1000.0)
        self._set_manual_uniform_mode(self.manual_uniform_button.isChecked())
        self._set_connection_visual(False, "Pump not connected.")
        self._suppress_plan_table_layout_save = False
        # Not calling sync_from_lifecycle_controller() here: availability_changed
        # and friends aren't connected to anything yet at this point in
        # construction (the caller connects them right after this constructor
        # returns), so any emission here would be discarded, and the real sync
        # already happens in _finalize_experiment_control_bootstrap_population
        # once bootstrap finishes - after signals are connected.
        self._startup_ui_pending = False
        self._set_switch_solution_mode(self._switch_solution_mode)
        _LOGGER.info("Experiment control bootstrap +%.1f ms: constructor finished", (perf_counter() - self._bootstrap_t0) * 1000.0)

    def _build_ui(self) -> None:
        palette = self._theme_palette()
        editor_header = QLabel("Experiment control")
        editor_header.setObjectName("flowHeaderLabel")
        editor_header.setStyleSheet(
            "QLabel#flowHeaderLabel {"
            f" color: {palette['title']};"
            " font-size: 12px;"
            " font-weight: 700;"
            "}"
        )
        editor_header.setCursor(Qt.CursorShape.PointingHandCursor)
        editor_header.setToolTip("Double-click to switch to processed spectra.")
        self._experiment_control_header_label = editor_header
        editor_hide_button = _make_frameless_icon_button(
            tint_tabler_icon(flow_tabler_icon("eye_off", "eye-off"), QColor("#8a98a8")),
            "Hide experimental control.",
            size=22,
        )
        editor_hide_button.clicked.connect(lambda _checked=False: self._activate_spectra_view())
        editor_header_row = QWidget(self)
        editor_header_row_layout = QHBoxLayout()
        editor_header_row_layout.setContentsMargins(0, 0, 0, 0)
        editor_header_row_layout.setSpacing(6)
        editor_header_row_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        editor_header_row_layout.addWidget(editor_header)
        self._experiment_control_view_mode_button = QToolButton(self)
        self._experiment_control_view_mode_button.setObjectName("flowViewModeButton")
        self._experiment_control_view_mode_button.setAutoRaise(True)
        self._experiment_control_view_mode_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._experiment_control_view_mode_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._experiment_control_view_mode_button.clicked.connect(self._cycle_experiment_control_view_mode)
        self._update_experiment_control_view_mode_button()
        editor_header_row_layout.addWidget(self._experiment_control_view_mode_button)
        editor_header_row_layout.addStretch(1)
        editor_header_row_layout.addWidget(
            make_help_button(EXPERIMENT_CONTROL_TOOLTIP, title=EXPERIMENT_CONTROL_TITLE, body=EXPERIMENT_CONTROL_BODY)
        )
        editor_header_row_layout.addWidget(editor_hide_button)
        editor_header_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        editor_header_row.setLayout(editor_header_row_layout)
        self._experiment_control_header_row = editor_header_row

        editor_layout = QVBoxLayout()
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(4)
        editor_layout.addWidget(editor_header_row)
        matrix = QGridLayout()
        matrix.setHorizontalSpacing(6)
        matrix.setVerticalSpacing(4)
        time_label = QLabel("Duration")
        time_label.setToolTip("Step duration. Displayed in seconds or minutes according to the unit switch, but stored internally in seconds.")
        equal_label = QLabel("CHs")
        equal_label.setToolTip("Shared channel mode. 'CHs' keeps direction and tube identical for all channels. 'not equal' expands per-channel editing.")
        dir_label = QLabel("Dir")
        dir_label.setToolTip("Pump rotation direction. '\u21bb' means clock-wise (CW), '\u21ba' means counter clock-wise (CCW).")
        tube_label = QLabel("Tube")
        tube_label.setToolTip("Tubing inner diameter in mm.")
        flow_header_label = QLabel("Flow")
        flow_header_label.setToolTip("Channel flow rate in uL/min.")
        color_label = QLabel("Color")
        color_label.setToolTip("Timeline color for this step.")
        valve_label = QLabel("Valve")
        valve_label.setToolTip("Valve state associated with this step.")
        switch_label = QLabel("Switch")
        switch_label.setToolTip("AMF switch position or solution label associated with this step.")
        comment_label = QLabel("Comment")
        comment_label.setToolTip("Short step description shown in the timeline.")

        matrix.addWidget(time_label, 0, 0)
        matrix.addWidget(equal_label, 0, 1)
        matrix.addWidget(dir_label, 0, 2)
        matrix.addWidget(tube_label, 0, 3)
        matrix.addWidget(flow_header_label, 0, 4)
        for channel in range(1, ACTIVE_PUMP_CHANNELS + 1):
            flow_spin = QDoubleSpinBox()
            make_compact_spinbox(flow_spin)
            flow_spin.setRange(0.0, 100.0)
            flow_spin.setDecimals(0)
            flow_spin.setSingleStep(1.0)
            flow_spin.setMaximumWidth(82)
            flow_spin.setToolTip(f"Flow rate for CH{channel} in uL/min.")

            direction_button = create_direction_button(self, "CW")
            direction_button.setMaximumWidth(40)
            direction_button.setToolTip(
                f"Direction for CH{channel}. '\u21bb' means clock-wise (CW), '\u21ba' means counter clock-wise (CCW)."
            )

            tube_spin = QDoubleSpinBox()
            make_compact_spinbox(tube_spin)
            tube_spin.setRange(0.13, 3.17)
            tube_spin.setDecimals(2)
            tube_spin.setSingleStep(0.01)
            tube_spin.setValue(DEFAULT_TUBE_MM)
            tube_spin.setMaximumWidth(74)
            tube_spin.setToolTip(f"Tubing inner diameter for CH{channel} in mm.")

            self.manual_flow_spins.append(flow_spin)
            self.manual_direction_buttons.append(direction_button)
            self.manual_tube_spins.append(tube_spin)
            tube_spin.valueChanged.connect(lambda _value, self=self: self._sync_experiment_control_tube_columns())
            matrix.addWidget(QLabel(f"CH{channel}"), 0, channel + 4)
            matrix.addWidget(flow_spin, 1, channel + 4)
            matrix.addWidget(direction_button, 2, channel + 4)
            matrix.addWidget(tube_spin, 3, channel + 4)

        self.manual_uniform_button.setToolTip("Shared direction and tube for all channels. Click to expand per-channel settings.")
        self.manual_uniform_button.setText("=")
        self.shared_direction_button.setToolTip("Shared direction for all channels when 'CHs' mode is active. '\u21bb' means CW, '\u21ba' means CCW.")
        self.shared_tube_spin.setToolTip("Shared tubing inner diameter in mm when 'CHs' mode is active.")
        self.plan_detail_toggle.setToolTip("Show or hide the per-channel direction and tube columns in the table.")
        self.manual_flow_label = QLabel("Flow")
        self.manual_dir_label = QLabel("Dir")
        self.manual_tube_label = QLabel("Tube")
        self.step_duration_spin.setToolTip("Step duration. Display value follows the selected time unit, but the plan stores seconds.")
        time_widget = QWidget(self)
        time_layout = QHBoxLayout()
        time_layout.setContentsMargins(0, 0, 0, 0)
        time_layout.setSpacing(3)
        time_layout.addWidget(self.step_duration_spin)
        time_layout.addWidget(self.time_unit_toggle)
        time_widget.setLayout(time_layout)
        matrix.addWidget(time_widget, 1, 0)
        matrix.addWidget(self.manual_uniform_button, 1, 1)
        color_header_widget = QWidget(self)
        color_header_layout = QHBoxLayout()
        color_header_layout.setContentsMargins(0, 0, 0, 0)
        color_header_layout.setSpacing(4)
        color_header_layout.addWidget(color_label)
        color_header_layout.addWidget(self.color_palette_button)
        color_header_layout.addStretch(1)
        color_header_widget.setLayout(color_header_layout)
        color_widget = QWidget(self)
        color_layout = QHBoxLayout()
        color_layout.setContentsMargins(0, 0, 0, 0)
        color_layout.setSpacing(4)
        color_layout.addWidget(self.step_color_combo)
        color_layout.addStretch(1)
        color_widget.setLayout(color_layout)
        valve_header_widget = QWidget(self)
        valve_header_layout = QHBoxLayout()
        valve_header_layout.setContentsMargins(0, 0, 0, 0)
        valve_header_layout.setSpacing(2)
        valve_header_layout.addWidget(valve_label)
        valve_header_layout.addWidget(self.step_valve_settings_button)
        valve_header_layout.addStretch(1)
        valve_header_widget.setLayout(valve_header_layout)
        matrix.addWidget(valve_header_widget, 0, ACTIVE_PUMP_CHANNELS + 5)
        valve_widget = QWidget(self)
        valve_layout = QHBoxLayout()
        valve_layout.setContentsMargins(0, 0, 0, 0)
        valve_layout.setSpacing(2)
        valve_layout.addWidget(self.step_valve_button)
        valve_layout.addStretch(1)
        valve_widget.setLayout(valve_layout)
        matrix.addWidget(valve_widget, 1, ACTIVE_PUMP_CHANNELS + 5)
        switch_header_widget = QWidget(self)
        switch_header_layout = QHBoxLayout()
        switch_header_layout.setContentsMargins(0, 0, 0, 0)
        switch_header_layout.setSpacing(2)
        switch_header_layout.addWidget(switch_label)
        switch_header_layout.addWidget(self.step_switch_mode_button)
        switch_header_layout.addWidget(self.step_switch_settings_button)
        switch_header_layout.addStretch(1)
        switch_header_widget.setLayout(switch_header_layout)
        switch_widget = QWidget(self)
        switch_layout = QHBoxLayout()
        switch_layout.setContentsMargins(0, 0, 0, 0)
        switch_layout.setSpacing(4)
        switch_layout.addWidget(self.step_switch_spin)
        switch_layout.addWidget(self.step_switch_combo)
        switch_layout.addStretch(1)
        switch_widget.setLayout(switch_layout)
        matrix.addWidget(switch_header_widget, 0, ACTIVE_PUMP_CHANNELS + 6)
        matrix.addWidget(switch_widget, 1, ACTIVE_PUMP_CHANNELS + 6)
        matrix.addWidget(color_header_widget, 0, ACTIVE_PUMP_CHANNELS + 7)
        matrix.addWidget(color_widget, 1, ACTIVE_PUMP_CHANNELS + 7)
        comment_header_widget = QWidget(self)
        comment_header_layout = QHBoxLayout()
        comment_header_layout.setContentsMargins(0, 0, 0, 0)
        comment_header_layout.setSpacing(2)
        comment_header_layout.addWidget(comment_label)
        comment_header_layout.addWidget(self.step_comment_display_button)
        comment_header_layout.addStretch(1)
        comment_header_widget.setLayout(comment_header_layout)
        matrix.addWidget(comment_header_widget, 0, ACTIVE_PUMP_CHANNELS + 8)
        matrix.addWidget(self.step_comment_edit, 1, ACTIVE_PUMP_CHANNELS + 8)
        self.step_comment_edit.setMinimumWidth(300)

        self.shared_direction_button.setMaximumWidth(40)
        self.shared_tube_spin.setMaximumWidth(82)
        self.shared_direction_row = QWidget(self)
        shared_direction_layout = QHBoxLayout()
        shared_direction_layout.setContentsMargins(0, 0, 0, 0)
        shared_direction_layout.addWidget(self.shared_direction_button)
        shared_direction_layout.addStretch(1)
        self.shared_direction_row.setLayout(shared_direction_layout)
        self.shared_tube_row = QWidget(self)
        shared_tube_layout = QHBoxLayout()
        shared_tube_layout.setContentsMargins(0, 0, 0, 0)
        shared_tube_layout.addWidget(self.shared_tube_spin)
        shared_tube_layout.addStretch(1)
        self.shared_tube_row.setLayout(shared_tube_layout)
        matrix.addWidget(self.shared_direction_row, 1, 2)
        matrix.addWidget(self.shared_tube_row, 1, 3)
        matrix.addWidget(self.manual_dir_label, 2, 4)
        matrix.addWidget(self.manual_tube_label, 3, 4)
        matrix.setColumnStretch(ACTIVE_PUMP_CHANNELS + 8, 2)
        self._experiment_control_matrix_widget = QWidget(self)
        matrix.setContentsMargins(0, 0, 0, 0)
        self._experiment_control_matrix_widget.setLayout(matrix)
        editor_layout.addWidget(self._experiment_control_matrix_widget)

        editor_action_row = QHBoxLayout()
        editor_action_row.setSpacing(3)
        editor_action_row.addWidget(self.add_step_button)
        editor_action_row.addWidget(self.apply_step_button)
        editor_action_row.addWidget(self.duplicate_step_button)
        editor_action_row.addWidget(self.remove_step_button)
        editor_action_row.addWidget(self.plan_detail_toggle)
        editor_action_row.addWidget(self.pause_state_button)
        editor_action_row.addWidget(self.color_comment_button)
        editor_action_row.addStretch(1)
        editor_action_row.addWidget(self.import_plan_busy_label)
        editor_action_row.addWidget(self.import_plan_button)
        editor_action_row.addWidget(self.export_plan_button)
        self._experiment_control_editor_action_row = QWidget(self)
        editor_action_row.setContentsMargins(0, 0, 0, 0)
        self._experiment_control_editor_action_row.setLayout(editor_action_row)
        editor_layout.addWidget(self._experiment_control_editor_action_row)

        table_container = QWidget(self)
        table_layout = QVBoxLayout()
        table_layout.setContentsMargins(0, 0, 0, 0)
        table_layout.setSpacing(0)
        self.plan_table.setMinimumWidth(0)
        table_layout.addWidget(self.plan_table, 1)
        table_container.setLayout(table_layout)
        table_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._experiment_control_table_container = table_container

        self._flow_editor_splitter = QSplitter(Qt.Orientation.Vertical)
        self._flow_editor_splitter.setChildrenCollapsible(False)
        self._flow_editor_splitter.setHandleWidth(6)
        self._flow_editor_splitter.setOpaqueResize(True)
        self._flow_editor_splitter.addWidget(table_container)
        self.timeline_widget.step_activated.connect(self._jump_to_experiment_control_step)
        self.timeline_widget.step_double_activated.connect(self._apply_selected_experiment_control_step)
        self.timeline_widget.setMinimumHeight(48)
        self.timeline_widget.setMaximumHeight(max(self.timeline_widget.minimumHeight(), self.timeline_widget.sizeHint().height()))
        self._flow_editor_splitter.addWidget(self.timeline_widget)
        self._flow_editor_splitter.setStretchFactor(0, 1)
        self._flow_editor_splitter.setStretchFactor(1, 0)
        self._flow_editor_splitter.splitterMoved.connect(self._on_flow_editor_splitter_moved)
        timeline_controls_widget = QWidget(self)
        timeline_controls_layout = QVBoxLayout()
        timeline_controls_layout.setContentsMargins(0, 0, 0, 0)
        timeline_controls_layout.setSpacing(0)
        timeline_controls_widget.setLayout(timeline_controls_layout)
        flow_action_row = QHBoxLayout()
        flow_action_row.setSpacing(4)
        flow_action_row.setContentsMargins(0, 0, 0, 0)
        flow_action_row.addWidget(self.plan_toggle_button)
        flow_action_row.addWidget(self.hold_plan_button)
        flow_action_row.addWidget(self.stop_plan_button)
        flow_action_row.addWidget(self.previous_step_button)
        flow_action_row.addWidget(self.next_step_button)
        flow_action_row.addWidget(self.pause_plan_button)
        flow_action_row.addWidget(self.record_with_flow_button)
        flow_action_row.addStretch(1)
        self._experiment_control_flow_action_row = QWidget(timeline_controls_widget)
        self._experiment_control_flow_action_row.setLayout(flow_action_row)
        self._experiment_control_flow_action_row.setVisible(self._show_runtime_controls)
        timeline_controls_layout.addWidget(self._flow_editor_splitter)
        timeline_controls_layout.addWidget(self._experiment_control_flow_action_row)
        self._experiment_control_timeline_controls_widget = timeline_controls_widget
        editor_layout.addWidget(timeline_controls_widget)
        editor_container = QWidget(self)
        editor_container.setObjectName("flowEditorContainer")
        editor_container.setLayout(editor_layout)

        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(2)
        content_layout.addWidget(editor_container, 1)

        content = QWidget(self)
        content.setObjectName("flowContent")
        content.setLayout(content_layout)

        scroller = QScrollArea(self)
        scroller.setWidgetResizable(True)
        scroller.setFrameShape(QFrame.Shape.NoFrame)
        scroller.setStyleSheet(
            """
            QScrollArea {
                background: %(bg)s;
                border: none;
            }
            QScrollArea > QWidget > QWidget {
                background: %(bg)s;
            }
            """ % palette
        )
        scroller.setWidget(content)
        outer_layout = QVBoxLayout()
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(scroller)
        self.setLayout(outer_layout)

    def _connect_signals(self) -> None:
        self.plan_toggle_button.clicked.connect(self._experiment_control_controller.toggle_run_hold)
        self.hold_plan_button.clicked.connect(self._experiment_control_controller.toggle_hold)
        self.pause_plan_button.clicked.connect(self._experiment_control_controller.toggle_pause)
        self.stop_plan_button.clicked.connect(self._experiment_control_controller.stop)
        self.previous_step_button.clicked.connect(lambda _checked=False: self._experiment_control_controller.move_relative(-1))
        self.next_step_button.clicked.connect(lambda _checked=False: self._experiment_control_controller.move_relative(1))
        self.manual_uniform_button.toggled.connect(self._set_manual_uniform_mode)
        self.shared_direction_button.clicked.connect(
            lambda: self._toggle_direction_button(self.shared_direction_button, self._apply_shared_manual_settings)
        )
        self.shared_tube_spin.valueChanged.connect(self._apply_shared_manual_settings)
        self.plan_detail_toggle.toggled.connect(self._set_experiment_control_details_visible)
        self.time_unit_toggle.clicked.connect(self._cycle_time_unit_mode)
        self.step_duration_spin.valueChanged.connect(self._capture_editor_duration_from_spin)
        self.shared_tube_spin.valueChanged.connect(self._sync_experiment_control_tube_columns)
        self.color_palette_button.clicked.connect(lambda _checked=False, btn=self.color_palette_button: self._edit_color_palette_entries(btn))
        self.remove_custom_color_button.clicked.connect(self._remove_selected_custom_color)
        self.step_valve_settings_button.clicked.connect(lambda _checked=False, btn=self.step_valve_settings_button: self._edit_valve_state_labels(btn))
        self.step_switch_spin.valueChanged.connect(self._handle_step_switch_spin_changed)
        self.step_switch_settings_button.clicked.connect(lambda _checked=False, btn=self.step_switch_settings_button: self._edit_switch_solution_labels(btn))
        self.step_comment_display_button.clicked.connect(lambda _checked=False, btn=self.step_comment_display_button: self._edit_pump_display_settings(btn))
        self._update_step_comment_display_button_icon()
        self.pause_state_button.clicked.connect(lambda _checked=False, btn=self.pause_state_button: self._edit_pause_state(btn))
        self.step_color_combo.currentIndexChanged.connect(
            lambda *_args: self._handle_color_selection_changed()
        )

        self.add_step_button.clicked.connect(self._add_experiment_control_step_from_editor)
        self.duplicate_step_button.clicked.connect(self._experiment_control_edit_controller.duplicate_selected_rows)
        self.remove_step_button.clicked.connect(self._experiment_control_edit_controller.remove_selected_rows)
        self.apply_step_button.toggled.connect(self._experiment_control_edit_controller.toggle_edit_mode)
        self.color_comment_button.clicked.connect(self._copy_color_names_to_comments)
        self.import_plan_button.clicked.connect(self._import_experiment_control_plan_from_file)
        self.export_plan_button.clicked.connect(self._export_experiment_control_plan_placeholder)
        self.plan_table.step_move_requested.connect(self._experiment_control_edit_controller.move_selected_rows)
        self.plan_table.copy_requested.connect(self._experiment_control_edit_controller.copy_selection)
        self.plan_table.paste_requested.connect(self._experiment_control_edit_controller.paste_selection)
        self.plan_table.verticalScrollBar().valueChanged.connect(self._experiment_control_edit_controller.sync_overlay)
        self.plan_table.horizontalScrollBar().valueChanged.connect(self._keep_plan_table_left_aligned)
        self.timeline_widget.step_reordered.connect(self._move_experiment_control_step_to_row)
        self.plan_table.horizontalHeader().sectionResized.connect(self._schedule_plan_table_layout_save)
        self._plan_model.dataChanged.connect(self._handle_experiment_control_model_changed)
        self._plan_model.modelReset.connect(self._handle_experiment_control_model_changed)
        selection_model = self.plan_table.selectionModel()
        if selection_model is not None:
            selection_model.currentChanged.connect(self._handle_experiment_control_current_index_changed)

    def capabilities(self) -> ExperimentControlCapabilities:
        return self._capabilities

    def backend(self) -> ExperimentControlBackend:
        return self._backend

    def set_capabilities(self, capabilities: ExperimentControlCapabilities) -> None:
        self._capabilities = capabilities
        self._show_runtime_controls = bool(self._capabilities.show_runtime_buttons)
        self._experiment_control_controller.set_capabilities(capabilities)
        self._apply_capabilities_to_ui()

    def bind_backend(self, backend: ExperimentControlBackend | None) -> None:
        self._backend = backend or NullExperimentControlBackend(self._capabilities)
        self._experiment_control_controller.bind_backend(self._backend)
        self._apply_capabilities_to_ui()

    def _apply_capabilities_to_ui(self) -> None:
        capabilities = self._capabilities
        runtime_visible = bool(capabilities.show_runtime_buttons and self._show_runtime_controls)
        self._experiment_control_flow_action_row.setVisible(runtime_visible)
        self.previous_step_button.setVisible(bool(capabilities.show_step_navigation_controls))
        self.next_step_button.setVisible(bool(capabilities.show_step_navigation_controls))
        self.plan_toggle_button.setVisible(runtime_visible)
        self.hold_plan_button.setVisible(runtime_visible)
        self.pause_plan_button.setVisible(runtime_visible)
        self.stop_plan_button.setVisible(runtime_visible)
        self.record_with_flow_button.setVisible(runtime_visible)
        self.plan_detail_toggle.setVisible(bool(capabilities.plan_import_export_enabled))
        self.import_plan_button.setVisible(bool(capabilities.plan_import_export_enabled))
        self.export_plan_button.setVisible(bool(capabilities.plan_import_export_enabled))
        self.import_plan_busy_label.setVisible(bool(capabilities.plan_import_export_enabled))

    def _make_icon_button(self, icon: QIcon, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("flowIconButton")
        button.setAutoRaise(True)
        button.setIcon(icon)
        button.setToolTip(tooltip)
        button.setFixedSize(32, 32)
        button.setIconSize(QSize(24, 24))
        return button

    def _pause_state_button_icon(self) -> QIcon:
        accent = QColor("#8a98a8")
        for icon_name in ("settings_pause", "clock_pause", "pause"):
            try:
                return tint_tabler_icon(flow_tabler_icon(icon_name), accent)
            except Exception:
                continue
        return transport_icon(self._theme_mode, "pause")

    def _hold_plan_button_icon(self, *, active: bool) -> QIcon:
        if active:
            alpha_steps = [255, 255, 220, 160, 90, 40, 90, 160]
            frame_index = int(getattr(self, "_plan_hold_blink_frame", 0)) % len(alpha_steps)
            color = QColor("#66a7ff")
            color.setAlpha(alpha_steps[frame_index])
        elif self._plan_running:
            color = QColor("#66a7ff")
        else:
            color = QColor("#8a98a8")
        try:
            return tint_tabler_icon(flow_tabler_icon("clock_stop"), color)
        except Exception:
            return transport_icon(self._theme_mode, "hold")

    def _runtime_pause_button_icon(self, *, active: bool = False) -> QIcon:
        if active:
            alpha_steps = [255, 255, 220, 160, 90, 40, 90, 160]
            frame_index = int(getattr(self, "_plan_hold_blink_frame", 0)) % len(alpha_steps)
            color = QColor("#ffbf3f")
            color.setAlpha(alpha_steps[frame_index])
        else:
            color = QColor("#ffbf3f")
        return tint_tabler_icon(flow_tabler_icon("player_pause", "pause"), color)

    def _switch_solution_label(self, position: int) -> str:
        index = max(min(int(position), 12), 1) - 1
        if 0 <= index < len(self._switch_solution_labels):
            label = str(self._switch_solution_labels[index]).strip()
            if label:
                return label
        return "empty"

    def _switch_display_text(self, position: int) -> str:
        normalized = max(min(int(position), 12), 1)
        return f"{normalized}: {self._switch_solution_label(normalized)}"

    def _populate_switch_solution_combo(
        self,
        combo: QComboBox,
        selected_position: int | None = None,
        *,
        show_labels: bool | None = None,
    ) -> None:
        current_position = max(min(int(selected_position or 1), 12), 1)
        combo.blockSignals(True)
        combo.clear()
        for position in range(1, 13):
            item_text = self._switch_display_text(position)
            combo.addItem(item_text, position)
            combo.setItemData(position - 1, int(Qt.AlignmentFlag.AlignCenter), Qt.ItemDataRole.TextAlignmentRole)
        combo.setCurrentIndex(current_position - 1)
        combo.view().setMinimumWidth(max(self._switch_solution_popup_width(combo), int(combo.width())))
        self._style_combo_popup_view(combo, center_items=True, rounded=False, selection_frame=True)
        combo.blockSignals(False)

    def _refresh_switch_solution_combo(
        self,
        selected_position: int | None = None,
    ) -> None:
        if not hasattr(self, "step_switch_combo"):
            return
        current_position = max(min(int(selected_position or self.step_switch_spin.value()), 12), 1)
        self._populate_switch_solution_combo(
            self.step_switch_combo,
            current_position,
        )
        self.step_switch_combo.view().setMinimumWidth(
            max(self._switch_solution_popup_width(self.step_switch_combo), int(self.step_switch_combo.width()))
        )
        self._style_combo_popup_view(self.step_switch_combo, center_items=True, rounded=False, selection_frame=True)

    def _switch_solution_popup_width(self, combo: QComboBox) -> int:
        metrics = combo.fontMetrics()
        widest = 0
        for index in range(combo.count()):
            widest = max(widest, metrics.horizontalAdvance(combo.itemText(index)))
        return max(140, widest + 40)

    def _color_combo_popup_width(self, combo: QComboBox) -> int:
        metrics = combo.fontMetrics()
        widest = 0
        for index in range(combo.count()):
            widest = max(widest, metrics.horizontalAdvance(combo.itemText(index)))
        cell_width = max(int(combo.width()), 0)
        return max(cell_width, widest + 48)

    def _style_combo_popup_view(
        self,
        combo: QComboBox,
        *,
        center_items: bool = False,
        rounded: bool = True,
        selection_frame: bool = False,
    ) -> None:
        view = combo.view()
        if view is None:
            return
        view.setObjectName("flowComboPopup")
        palette = self._theme_palette()
        align_rule = "text-align: center;" if center_items else ""
        radius_rule = " border-radius: 10px;" if rounded else " border-radius: 0px;"
        item_radius_rule = " border-radius: 8px;" if rounded else " border-radius: 0px;"
        selected_rule = (
            "QListView#flowComboPopup::item:selected {"
            f" background: transparent;"
            f" color: {palette['fg']};"
            f" border: 1px solid {palette['selection']};"
            "}"
            if selection_frame
            else
            "QListView#flowComboPopup::item:selected {"
            f" background: {palette['selection']};"
            f" color: {palette['fg']};"
            "}"
        )
        view.setStyleSheet(
            "QListView#flowComboPopup {"
            f" background: {palette['field']};"
            f" color: {palette['fg']};"
            f" border: 1px solid {palette['border']};"
            f"{radius_rule}"
            " padding: 2px;"
            " outline: none;"
            "}"
            "QListView#flowComboPopup::item {"
            " min-height: 20px;"
            " padding: 2px 8px;"
            f"{item_radius_rule}"
            f" {align_rule}"
            "}"
            f"{selected_rule}"
        )

    def _set_switch_solution_mode(self, enabled: bool) -> None:
        _ = enabled
        self._switch_solution_mode = False
        self.step_switch_mode_button.setVisible(False)
        self.step_switch_spin.setVisible(False)
        self.step_switch_combo.setVisible(not getattr(self, "_startup_ui_pending", False))
        self._refresh_switch_solution_controls()
        self._update_timeline_selection()

    def _refresh_switch_solution_controls(self) -> None:
        current_position = self._current_switch_position_from_editor()
        self._refresh_switch_solution_combo(current_position)
        self._plan_model.set_switch_solution_labels(self._switch_solution_labels)
        self._plan_model.set_theme_palette(self._theme_palette())
        self._plan_model.set_valve_state_colors(self._valve_state_colors)
        self.plan_table.viewport().update()
        self._fit_plan_table_columns_to_viewport()
        self._update_timeline_selection()

    def _handle_step_switch_spin_changed(self, value: int) -> None:
        if self._updating_switch_editor:
            return
        self._updating_switch_editor = True
        try:
            self.step_switch_combo.setCurrentIndex(max(min(int(value), 12), 1) - 1)
        finally:
            self._updating_switch_editor = False

    def _handle_step_switch_combo_changed(self, index: int) -> None:
        if self._updating_switch_editor:
            return
        if index < 0:
            return
        self._updating_switch_editor = True
        try:
            self.step_switch_spin.setValue(index + 1)
        finally:
            self._updating_switch_editor = False

    def _current_switch_position_from_editor(self) -> int:
        if self.step_switch_combo.currentIndex() >= 0:
            data = self.step_switch_combo.currentData()
            if isinstance(data, (int, float)):
                return max(min(int(data), 12), 1)
            return max(min(self.step_switch_combo.currentIndex() + 1, 12), 1)
        return max(min(int(self.step_switch_spin.value()), 12), 1)

    def _switch_position_from_text(self, text: str) -> int:
        cleaned = str(text or "").strip()
        if not cleaned:
            return 1
        head = cleaned.split(":", 1)[0].strip()
        try:
            return max(min(int(float(head)), 12), 1)
        except ValueError:
            pass
        for position in range(1, 13):
            if cleaned.casefold() == self._switch_solution_label(position).casefold():
                return position
        return 1

    def _experiment_plan_import_default_dir(self) -> Path:
        stored = load_app_setting("experiment_plan_import_dir", "")
        if isinstance(stored, str) and stored:
            stored_path = Path(stored)
            if stored_path.exists():
                return stored_path
        examples_dir = Path.cwd().parent / "LSPR_examples" / "pumplans"
        if examples_dir.exists():
            return examples_dir
        return Path.cwd()

    def _set_experiment_plan_import_running(self, running: bool) -> None:
        self._experiment_plan_import_in_progress = running
        self.import_plan_button.setEnabled(not running)
        self.export_plan_button.setEnabled(not running)
        if not running:
            self._experiment_plan_import_fill_timer.stop()
        self._update_experiment_control_busy_indicator()

    def _set_experiment_control_bootstrap_busy(self, running: bool) -> None:
        self._experiment_control_bootstrap_in_progress = running
        if not running:
            self._experiment_plan_import_fill_timer.stop()
        self._update_experiment_control_busy_indicator()

    def _update_experiment_control_busy_indicator(self) -> None:
        busy = self._experiment_plan_import_in_progress or self._experiment_control_bootstrap_in_progress
        self.import_plan_busy_label.setVisible(busy)
        if busy:
            self._import_plan_busy_frame_index = 0
            self._render_import_plan_busy_indicator()
            if not self._import_plan_busy_timer.isActive():
                self._import_plan_busy_timer.start()
        else:
            self._import_plan_busy_timer.stop()
            self.import_plan_busy_label.setVisible(False)

    def _set_experiment_plan_export_running(self, running: bool) -> None:
        self._experiment_plan_export_in_progress = running
        self.import_plan_button.setEnabled(not running)
        self.export_plan_button.setEnabled(not running)

    def _normalize_experiment_plan_header(self, text: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(text or "").casefold())

    def _run_gui_callback_timed(self, label: str, callback, *, warn_ms: float = 20.0) -> None:
        started = perf_counter()
        try:
            callback()
        finally:
            elapsed_ms = (perf_counter() - started) * 1000.0
        if elapsed_ms < warn_ms:
            return
        controller = getattr(self, "recording_controller", None)
        source_mode = str(getattr(controller, "_source_mode", "") or "").strip().lower()
        if source_mode == "simulation":
            if bool(getattr(self, "_debug_timing_enabled", False)) or bool(getattr(self, "_deep_timing_enabled", False)):
                _LOGGER.debug("Experiment control GUI callback %s took %.2f ms", label, elapsed_ms)
            return
        _LOGGER.warning("Experiment control GUI callback %s took %.2f ms", label, elapsed_ms)

    def _advance_import_plan_busy_indicator(self) -> None:
        def _callback() -> None:
            if not self.import_plan_busy_label.isVisible():
                return
            self._import_plan_busy_frame_index = (self._import_plan_busy_frame_index + 1) % len(self._import_plan_busy_frames)
            self._render_import_plan_busy_indicator()

        self._run_gui_callback_timed("import_plan_busy", _callback)

    def _render_import_plan_busy_indicator(self) -> None:
        label = self.import_plan_busy_label
        size = 16
        pixmap = QPixmap(size, size)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            center = size / 2.0
            ring_radius = 5.5
            dot_radius = 1.4
            base_color = QColor("#39c7ba")
            trail_alphas = [255, 220, 185, 150]
            for index, alpha in enumerate(trail_alphas):
                frame = (self._import_plan_busy_frame_index + index) % 12
                angle = frame * 30.0
                radians = angle * math.pi / 180.0
                x = center + ring_radius * math.cos(radians)
                y = center + ring_radius * math.sin(radians)
                color = QColor(base_color)
                color.setAlpha(alpha)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawEllipse(
                    int(round(x - dot_radius)),
                    int(round(y - dot_radius)),
                    int(round(dot_radius * 2.0)),
                    int(round(dot_radius * 2.0)),
                )
        finally:
            painter.end()
        label.setPixmap(pixmap)

    def _detect_experiment_plan_delimiter(self, header_line: str) -> str:
        candidates = [";", ",", "\t", "|"]
        counts = {candidate: header_line.count(candidate) for candidate in candidates}
        delimiter = max(counts, key=counts.get)
        return delimiter if counts[delimiter] > 0 else ";"

    def _experiment_plan_column_map(self, headers: list[str]) -> dict[object, int]:
        mapping: dict[object, int] = {}
        for index, header in enumerate(headers):
            normalized = self._normalize_experiment_plan_header(header)
            if not normalized:
                continue
            if normalized.startswith("step"):
                mapping["step"] = index
                continue
            if normalized.startswith("time"):
                mapping["time"] = index
                continue
            if normalized.startswith("valve"):
                mapping["valve"] = index
                continue
            if normalized.startswith("color"):
                mapping["color"] = index
                continue
            if "solution" in normalized:
                mapping["solution"] = index
                continue
            if "comment" in normalized or "description" in normalized or "descrit" in normalized:
                mapping["description"] = index
                continue
            match = re.match(r"ch(\d+)", normalized)
            if not match:
                continue
            channel = int(match.group(1))
            if "flow" in normalized:
                mapping[("flow", channel)] = index
            elif "direction" in normalized or normalized.endswith("dir"):
                mapping[("direction", channel)] = index
            elif "tube" in normalized:
                mapping[("tube", channel)] = index
        return mapping

    def _experiment_plan_cell(self, row: list[str], index: int | None, default: str = "") -> str:
        if index is None or index < 0 or index >= len(row):
            return default
        return str(row[index]).strip()

    def _experiment_plan_uses_lr_valves(self, rows: list[list[str]], column_map: dict[object, int]) -> bool:
        valve_index = column_map.get("valve")
        if valve_index is None:
            return False
        for row in rows:
            valve = self._experiment_plan_cell(row, valve_index)
            if valve.casefold() in {"l", "r", "left", "right"}:
                return True
        return False

    def _prompt_experiment_plan_l_is_open(self) -> bool:
        saved = load_app_setting("experiment_plan_import_l_is_open", None)
        if isinstance(saved, bool):
            return saved
        prompt = QMessageBox(self)
        prompt.setWindowTitle("Import experiment plan")
        prompt.setIcon(QMessageBox.Icon.Question)
        prompt.setText("The imported file uses L / R valve labels.\nShould L mean Open?")
        prompt.setInformativeText("This choice will be remembered for future imports.")
        left_open_button = prompt.addButton("L = Open", QMessageBox.ButtonRole.YesRole)
        prompt.addButton("L = Close", QMessageBox.ButtonRole.NoRole)
        prompt.setDefaultButton(left_open_button)
        prompt.exec()
        choice = prompt.clickedButton() is left_open_button
        save_app_setting("experiment_plan_import_l_is_open", choice)
        return choice

    def _normalize_experiment_plan_valve(self, raw_valve: str, l_is_open: bool) -> str:
        text = str(raw_valve or "").strip()
        if not text:
            return "Open"
        lowered = text.casefold()
        if lowered in {"open", "close"}:
            return "Close" if lowered == "close" else "Open"
        for internal_state, label in self._valve_state_labels.items():
            if lowered == str(label).strip().casefold():
                return "Close" if str(internal_state).strip().casefold() == "close" else "Open"
        if lowered in {"l", "left"}:
            return "Open" if l_is_open else "Close"
        if lowered in {"r", "right"}:
            return "Close" if l_is_open else "Open"
        return "Close" if lowered.startswith("close") else "Open"

    def _build_experiment_plan_steps_from_import_data(
        self,
        data: ExperimentPlanImportData,
        *,
        l_is_open: bool,
    ) -> list[PumpPlanStep]:
        steps: list[PumpPlanStep] = []
        for row_index, row in enumerate(data.rows, start=1):
            if not any(cell.strip() for cell in row):
                continue

            channels: list[PumpChannelStep] = []
            for channel_index in range(1, ACTIVE_PUMP_CHANNELS + 1):
                flow_text = self._experiment_plan_cell(row, data.column_map.get(("flow", channel_index)), "0")
                direction_text = self._experiment_plan_cell(row, data.column_map.get(("direction", channel_index)), "CW")
                flow_ml_min = max(_safe_float(flow_text), 0.0)
                direction = "CCW" if direction_text.casefold() == "ccw" else "CW"
                channels.append(
                    PumpChannelStep(
                        flow_ul_min=max(round(flow_ml_min * 1000.0), 0),
                        direction=direction,
                    )
                )

            valve = self._normalize_experiment_plan_valve(
                self._experiment_plan_cell(row, data.column_map.get("valve"), "Open"),
                l_is_open,
            )
            raw_color = self._experiment_plan_cell(row, data.column_map.get("color"), "").strip().upper()
            qcolor = QColor(raw_color)
            color = qcolor.name().upper() if qcolor.isValid() else self._default_experiment_control_color(row_index - 1)
            description = self._experiment_plan_cell(row, data.column_map.get("description"), "").strip()
            switch_text = self._experiment_plan_cell(row, data.column_map.get("solution"), "")
            switch_position = self._switch_position_from_text(switch_text) if switch_text else 1
            duration_s = max(_safe_float(self._experiment_plan_cell(row, data.column_map.get("time"), "0")), 0.0)

            steps.append(
                PumpPlanStep(
                    step=row_index,
                    duration_s=duration_s,
                    color=color,
                    valve=valve,
                    switch_position=switch_position,
                    description=description,
                    channels=channels,
                )
            )

        return recompute_plan_timing(steps)

    def _experiment_plan_native_flow_factor(self, document: dict[str, object]) -> float:
        units = document.get("units", {})
        if not isinstance(units, dict):
            return 1.0
        flow_unit = str(units.get("flow", "uL/min") or "uL/min").strip().casefold()
        if flow_unit in {"ml/min", "ml min-1", "ml_per_min"}:
            return 1000.0
        return 1.0

    def _build_experiment_plan_steps_from_native_document(self, document: dict[str, object]) -> list[PumpPlanStep]:
        raw_steps = document.get("steps", [])
        if not isinstance(raw_steps, list):
            return []
        flow_factor = self._experiment_plan_native_flow_factor(document)
        steps: list[PumpPlanStep] = []
        for row_index, raw_step in enumerate(raw_steps, start=1):
            if not isinstance(raw_step, dict):
                continue
            devices = raw_step.get("devices", {})
            devices = devices if isinstance(devices, dict) else {}
            pump = devices.get("pump_1", {})
            pump = pump if isinstance(pump, dict) else {}
            channels: list[PumpChannelStep] = []
            for channel_index in range(1, ACTIVE_PUMP_CHANNELS + 1):
                raw_channel = pump.get(f"ch{channel_index}", {})
                raw_channel = raw_channel if isinstance(raw_channel, dict) else {}
                flow = max(_safe_float(str(raw_channel.get("flow", 0.0) or 0.0)), 0.0) * flow_factor
                direction = str(raw_channel.get("direction", "OFF") or "OFF").upper()
                if direction not in {"CW", "CCW", "OFF"}:
                    direction = "CW"
                channels.append(PumpChannelStep(flow_ul_min=max(round(flow), 0), direction=direction))

            valve_payload = devices.get("valve_1", {})
            valve_payload = valve_payload if isinstance(valve_payload, dict) else {}
            raw_valve = str(valve_payload.get("state", "open") or "open").strip().casefold()
            valve = "Close" if raw_valve in {"close", "closed"} else "Open"

            switch_payload = devices.get("switch_1", {})
            switch_payload = switch_payload if isinstance(switch_payload, dict) else {}
            switch_position = self._switch_position_from_text(str(switch_payload.get("port", 1) or 1))

            qcolor = QColor(str(raw_step.get("color", "") or "").strip())
            color = qcolor.name().upper() if qcolor.isValid() else self._default_experiment_control_color(row_index - 1)
            steps.append(
                PumpPlanStep(
                    step=row_index,
                    duration_s=max(_safe_float(str(raw_step.get("duration_s", 0.0) or 0.0)), 0.0),
                    color=color,
                    valve=valve,
                    switch_position=switch_position,
                    description=str(raw_step.get("comment", raw_step.get("description", "")) or ""),
                    channels=channels,
                )
            )
        return recompute_plan_timing(steps)

    def _native_experiment_plan_unsupported_devices(self, document: dict[str, object]) -> list[str]:
        supported = {"pump_1", "valve_1", "switch_1"}
        found: set[str] = set()
        devices = document.get("devices", {})
        if isinstance(devices, dict):
            for group_name in ("pumps", "valves", "switches"):
                group = devices.get(group_name, {})
                if isinstance(group, dict):
                    found.update(str(key) for key in group)
        raw_steps = document.get("steps", [])
        if isinstance(raw_steps, list):
            for raw_step in raw_steps:
                if not isinstance(raw_step, dict):
                    continue
                step_devices = raw_step.get("devices", {})
                if isinstance(step_devices, dict):
                    found.update(str(key) for key in step_devices)
        return sorted(device for device in found if device not in supported)

    def _apply_native_experiment_plan_device_labels(self, document: dict[str, object]) -> None:
        devices = document.get("devices", {})
        if not isinstance(devices, dict):
            return

        switches = devices.get("switches", {})
        if isinstance(switches, dict):
            switch_1 = switches.get("switch_1", {})
            if isinstance(switch_1, dict):
                ports = switch_1.get("ports", {})
                if isinstance(ports, dict):
                    labels = list(self._switch_solution_labels)
                    while len(labels) < 12:
                        labels.append("empty")
                    for raw_port, raw_label in ports.items():
                        try:
                            port = int(raw_port)
                        except (TypeError, ValueError):
                            continue
                        if 1 <= port <= 12:
                            labels[port - 1] = str(raw_label or "empty").strip() or "empty"
                    self._switch_solution_labels = labels[:12]
                    self._refresh_switch_solution_controls()

        valves = devices.get("valves", {})
        if isinstance(valves, dict):
            valve_1 = valves.get("valve_1", {})
            if isinstance(valve_1, dict):
                display_labels = valve_1.get("display_labels", {})
                if isinstance(display_labels, dict):
                    open_label = str(display_labels.get("open", "Open") or "Open").strip() or "Open"
                    close_label = str(display_labels.get("close", "Close") or "Close").strip() or "Close"
                    self._valve_state_labels = {"Open": open_label, "Close": close_label}
                    set_step_valve_button_state_for_button(
                        self,
                        self.step_valve_button,
                        str(self.step_valve_button.property("valve") or "Open"),
                    )

    def _experiment_plan_export_default_dir(self) -> Path:
        stored = load_app_setting("experiment_plan_export_dir", "")
        if isinstance(stored, str) and stored:
            stored_path = Path(stored)
            if stored_path.exists():
                return stored_path
        import_dir = self._experiment_plan_import_default_dir()
        if import_dir.exists():
            return import_dir
        return Path.cwd()

    def _experiment_plan_export_l_is_open(self) -> bool:
        saved = load_app_setting("experiment_plan_import_l_is_open", True)
        return bool(saved) if isinstance(saved, bool) else True

    def _experiment_plan_export_valve_text(self, valve: str) -> str:
        normalized = "Close" if str(valve or "").strip().lower() == "close" else "Open"
        l_is_open = self._experiment_plan_export_l_is_open()
        if normalized == "Open":
            return "L" if l_is_open else "R"
        return "R" if l_is_open else "L"

    def _build_native_experiment_plan_document(self) -> dict[str, object]:
        steps = recompute_plan_timing(self._read_experiment_control_steps())
        tube_mm_by_channel = self._tube_mm_values()
        pump_label = str(getattr(self._probe, "model", "") or "").strip()
        if not pump_label:
            pump_label = "Pump 1"
        return {
            "format": {
                "name": "LSPR Experiment Plan",
                "version": 1,
            },
            "metadata": {
                "created_by": "LSPR Acquisition",
                "app_version": __version__,
                "exported_at": datetime.now().isoformat(timespec="seconds"),
                "notes": "",
            },
            "units": {
                "flow": "uL/min",
                "time": "s",
                "tube_diameter": "mm",
            },
            "devices": {
                "pumps": {
                    "pump_1": {
                        "label": pump_label,
                        "channels": {
                            f"ch{channel_index}": {
                                "label": f"CH{channel_index}",
                                "tube_mm": float(tube_mm_by_channel[channel_index - 1]),
                            }
                            for channel_index in range(1, ACTIVE_PUMP_CHANNELS + 1)
                        },
                    }
                },
                "valves": {
                    "valve_1": {
                        "labels": {
                            "open": self._experiment_plan_export_valve_text("Open"),
                            "close": self._experiment_plan_export_valve_text("Close"),
                        },
                        "display_labels": {
                            "open": self._valve_state_label("Open"),
                            "close": self._valve_state_label("Close"),
                        },
                    }
                },
                "switches": {
                    "switch_1": {
                        "ports": {
                            position: self._switch_solution_label(position)
                            for position in range(1, 13)
                        }
                    }
                },
            },
            "steps": [
                {
                    "id": step.step,
                    "duration_s": float(step.duration_s),
                    "color": str(step.color or self._default_experiment_control_color(step.step - 1)),
                    "comment": str(step.description or ""),
                    "devices": {
                        "pump_1": {
                            f"ch{channel_index + 1}": {
                                "flow": float(step.channels[channel_index].flow_ul_min),
                                "direction": str(step.channels[channel_index].direction or "OFF"),
                            }
                            for channel_index in range(ACTIVE_PUMP_CHANNELS)
                        },
                        "valve_1": {
                            "state": "close" if str(step.valve or "").strip().lower() == "close" else "open",
                        },
                        "switch_1": {
                            "port": int(max(min(int(step.switch_position), 12), 1)),
                        },
                    },
                }
                for step in steps
            ],
        }

    def _build_experiment_plan_export_payload(self, path: Path) -> ExperimentPlanExportData:
        if path.suffix.casefold() in {".yaml", ".yml"}:
            return ExperimentPlanExportData(path=path, document=self._build_native_experiment_plan_document())

        steps = recompute_plan_timing(self._read_experiment_control_steps())
        header = [
            "Step",
            "Ch-1 Flow [ml/min]",
            "Ch-1 Direction",
            "Ch-1 Tubesize [mm]",
            "Ch-2 Flow [ml/min]",
            "Ch-2 Direction",
            "Ch-2 Tubesize [mm]",
            "Ch-3 Flow [ml/min]",
            "Ch-3 Direction",
            "Ch-3 Tubesize [mm]",
            "Ch-4 Flow [ml/min]",
            "Ch-4 Direction",
            "Ch-4 Tubesize [mm]",
            "Ch-5 Flow [ml/min]",
            "Ch-5 Direction",
            "Ch-5 Tubesize [mm]",
            "Ch-6 Flow [ml/min]",
            "Ch-6 Direction",
            "Ch-6 Tubesize [mm]",
            "Time",
            "Valve",
            "Color",
            "Descritption",
            "",
            "Solution",
            "volume:?L",
        ]
        rows: list[list[str]] = []
        tube_mm_by_channel = self._tube_mm_values()
        for step in steps:
            row = [str(step.step)]
            for channel_index in range(HDF5_PUMP_CHANNELS):
                if channel_index < ACTIVE_PUMP_CHANNELS:
                    channel = step.channels[channel_index]
                    row.extend(
                        [
                            f"{max(float(channel.flow_ul_min), 0.0) / 1000.0:g}",
                            str(channel.direction or "OFF"),
                            f"{float(tube_mm_by_channel[channel_index]):.2f}",
                        ]
                    )
                else:
                    row.extend(["", "", ""])
            row.extend(
                [
                    f"{max(float(step.duration_s), 0.0):g}",
                    self._experiment_plan_export_valve_text(step.valve),
                    str(step.color or self._default_experiment_control_color(step.step - 1)),
                    str(step.description or ""),
                    "",
                    self._switch_solution_label(step.switch_position) if self._switch_solution_label(step.switch_position) != "empty" else "",
                    "",
                ]
            )
            rows.append(row)
        return ExperimentPlanExportData(path=path, header=header, rows=rows)

    def _start_experiment_plan_export(self, path: Path) -> None:
        if self._experiment_plan_export_in_progress:
            return
        if self._experiment_plan_import_in_progress:
            return
        try:
            payload = self._build_experiment_plan_export_payload(path)
            self._experiment_plan_export_generation += 1
            generation = self._experiment_plan_export_generation
            task = ExperimentPlanExportTask(generation, payload)
            self._experiment_plan_export_task = task
            self._set_experiment_plan_export_running(True)
            self._set_status_message(f"Exporting experiment plan to {path.name}...")
            task.signals.finished.connect(self._handle_experiment_plan_export_finished)
            task.signals.failed.connect(self._handle_experiment_plan_export_failed)
            self._thread_pool.start(task)
        except Exception as exc:
            self._experiment_plan_export_task = None
            self._set_experiment_plan_export_running(False)
            QMessageBox.warning(self, "Export experiment plan", f"Could not export experiment plan:\n{exc}")

    def _handle_experiment_plan_export_finished(self, generation: int, payload: object) -> None:
        if generation != self._experiment_plan_export_generation:
            return
        self._experiment_plan_export_task = None
        self._set_experiment_plan_export_running(False)
        if not isinstance(payload, ExperimentPlanExportData):
            self._show_error("Exported experiment plan data had an unexpected format.")
            return
        save_app_setting("experiment_plan_export_dir", str(payload.path.parent))
        self._set_status_message(f"Exported experiment plan to {payload.path.name}.")

    def _handle_experiment_plan_export_failed(self, generation: int, message: str) -> None:
        if generation != self._experiment_plan_export_generation:
            return
        self._experiment_plan_export_task = None
        self._set_experiment_plan_export_running(False)
        self._show_error(f"Could not export experiment plan:\n{message}")

    def _merge_imported_experiment_plan_colors(self, colors: list[str]) -> bool:
        if not colors:
            return False
        existing = {color for _name, color in self._color_palette_entries}
        changed = False
        for color in colors:
            qcolor = QColor(str(color).strip())
            if not qcolor.isValid():
                continue
            normalized = qcolor.name().upper()
            if normalized in existing:
                continue
            self._color_palette_entries.append((normalized, normalized))
            existing.add(normalized)
            changed = True
        return changed

    def _prompt_hdf5_experiment_plan_import_options(self, path: Path) -> tuple[bool, bool] | None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Import HDF5 experiment plan")
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        label = QLabel(f"Import data from:\n{path.name}")
        label.setWordWrap(True)
        layout.addWidget(label)

        plan_checkbox = QCheckBox("Import authored plan (required)", dialog)
        plan_checkbox.setChecked(True)
        plan_checkbox.setEnabled(False)
        runtime_checkbox = QCheckBox("Import runtime state", dialog)
        runtime_checkbox.setChecked(True)
        layout.addWidget(plan_checkbox)
        layout.addWidget(runtime_checkbox)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(dialog.accept)
        button_box.rejected.connect(dialog.reject)
        layout.addWidget(button_box)

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        return bool(plan_checkbox.isChecked()), bool(runtime_checkbox.isChecked())

    def _start_experiment_plan_import(
        self,
        path: Path,
        *,
        preset_hdf5_options: tuple[bool, bool] | None = None,
    ) -> None:
        """Start the plan import pipeline for *path*.

        *preset_hdf5_options*, if given as ``(import_plan, import_runtime)``,
        skips the interactive sub-dialog for HDF5 files.  Pass this from the
        unified "Import from measurement file" dialog so the user is not
        prompted twice.
        """
        if self._experiment_plan_import_in_progress:
            return
        try:
            self._experiment_plan_import_fill_timer.stop()
            self._experiment_plan_import_pending_steps = []
            self._experiment_plan_import_pending_payload = None
            self._experiment_plan_import_pending_selected_row = None
            self._experiment_plan_import_pending_step_index = 0
            self._experiment_plan_import_generation += 1
            generation = self._experiment_plan_import_generation
            import_hdf5_plan = True
            import_hdf5_runtime = True
            if path.suffix.casefold() in {".h5", ".hdf5"}:
                if preset_hdf5_options is not None:
                    import_hdf5_plan, import_hdf5_runtime = preset_hdf5_options
                else:
                    options = self._prompt_hdf5_experiment_plan_import_options(path)
                    if options is None:
                        return
                    import_hdf5_plan, import_hdf5_runtime = options
            task = ExperimentPlanImportTask(
                generation,
                path,
                import_hdf5_plan=import_hdf5_plan,
                import_hdf5_runtime=import_hdf5_runtime,
            )
            self._experiment_plan_import_task = task
            self._set_experiment_plan_import_running(True)
            self._set_status_message(f"Importing experiment plan from {path.name}...")
            task.signals.finished.connect(self._handle_experiment_plan_import_finished)
            task.signals.failed.connect(self._handle_experiment_plan_import_failed)
            self._thread_pool.start(task)
        except Exception as exc:
            self._experiment_plan_import_task = None
            self._set_experiment_plan_import_running(False)
            QMessageBox.warning(self, "Import experiment plan", f"Could not import experiment plan:\n{exc}")

    def _handle_experiment_plan_import_finished(self, generation: int, payload: object) -> None:
        if generation != self._experiment_plan_import_generation:
            return
        self._experiment_plan_import_task = None
        if not isinstance(payload, ExperimentPlanImportData):
            self._set_experiment_plan_import_running(False)
            self._show_error("Imported experiment plan data had an unexpected format.")
            return
        if payload.switch_solution_rows:
            labels = ["empty" for _ in range(12)]
            for row in payload.switch_solution_rows:
                if not isinstance(row, list) or len(row) < 2:
                    continue
                try:
                    port = int(float(str(row[0]).strip()))
                except (TypeError, ValueError):
                    continue
                if 1 <= port <= 12:
                    labels[port - 1] = str(row[1] or "empty").strip() or "empty"
            self._switch_solution_labels = labels
            self._refresh_switch_solution_controls()
        if payload.switch_solution_mode is not None:
            self.step_switch_mode_button.blockSignals(True)
            try:
                self.step_switch_mode_button.setChecked(bool(payload.switch_solution_mode))
            finally:
                self.step_switch_mode_button.blockSignals(False)
            self._set_switch_solution_mode(bool(payload.switch_solution_mode))
        if payload.valve_state_labels is not None:
            self._valve_state_labels = self._load_valve_state_labels({"valve_state_labels": payload.valve_state_labels})
            set_step_valve_button_state_for_button(
                self,
                self.step_valve_button,
                str(self.step_valve_button.property("valve") or "Open"),
            )
        if payload.valve_state_colors is not None:
            self._valve_state_colors = self._load_valve_state_colors({"valve_state_colors": payload.valve_state_colors})
        if payload.color_palette_entries is not None:
            payload_entries: list[tuple[str, str]] = []
            for index, entry in enumerate(payload.color_palette_entries):
                name = str(entry.get("name", "")).strip()
                color = str(entry.get("color", "")).strip().upper()
                normalized = self._normalize_color_entry(name, color, index)
                if normalized is not None:
                    payload_entries.append(normalized)
            if payload_entries:
                self._color_palette_entries = payload_entries
                self._sync_custom_plan_colors_from_palette()
                self._refresh_color_palette_widgets()
                self._update_experiment_control_timeline_label_mode()
        if payload.native_document is not None:
            unsupported_devices = self._native_experiment_plan_unsupported_devices(payload.native_document)
            if unsupported_devices:
                QMessageBox.information(
                    self,
                    "Import experiment control plan",
                    "This experiment control plan contains devices that are not supported by this app version and will be skipped:\n"
                    + ", ".join(unsupported_devices),
                )
            self._apply_native_experiment_plan_device_labels(payload.native_document)
        else:
            pass
        steps = list(payload.steps or [])
        if payload.native_document is None and payload.uses_lr_valves:
            l_is_open = self._prompt_experiment_plan_l_is_open()
            steps = build_experiment_plan_steps_from_import_data(payload, l_is_open=bool(l_is_open))
        if not steps:
            self._set_experiment_plan_import_running(False)
            QMessageBox.warning(self, "Import experiment plan", "The selected file did not contain any flow steps.")
            return
        palette_changed = self._merge_imported_experiment_plan_colors(payload.imported_colors)
        if palette_changed:
            self._save_color_palette_entries()
        for index, tube_mm in enumerate(payload.tube_mm_by_channel[:ACTIVE_PUMP_CHANNELS]):
            self.manual_tube_spins[index].blockSignals(True)
            self.manual_tube_spins[index].setValue(float(tube_mm))
            self.manual_tube_spins[index].blockSignals(False)
        self.save_ui_state()
        self._begin_experiment_plan_import_population(payload, steps)

    def _handle_experiment_plan_import_failed(self, generation: int, message: str) -> None:
        if generation != self._experiment_plan_import_generation:
            return
        self._experiment_plan_import_task = None
        self._set_experiment_plan_import_running(False)
        self._show_error(f"Could not import experiment plan:\n{message}")

    def _begin_experiment_plan_import_population(self, payload: ExperimentPlanImportData, steps: list[PumpPlanStep]) -> None:
        self._experiment_plan_import_pending_payload = payload
        self._experiment_plan_import_pending_steps = list(steps)
        selected_row = payload.selected_row if payload.selected_row is not None else 0
        if steps:
            selected_row = min(max(int(selected_row), 0), len(steps) - 1)
        self._experiment_plan_import_pending_selected_row = selected_row
        self._experiment_plan_import_pending_step_index = 0
        self.plan_table.blockSignals(True)
        self.plan_table.setUpdatesEnabled(False)
        try:
            self._plan_model.set_steps(steps)
            self.plan_table.clearSelection()
        finally:
            self.plan_table.setUpdatesEnabled(True)
            self.plan_table.blockSignals(False)
        self._experiment_plan_import_fill_timer.start()

    def _advance_experiment_plan_import_population(self) -> None:
        def _callback() -> None:
            try:
                if self._experiment_control_bootstrap_pending_state is not None:
                    self._advance_experiment_control_bootstrap_population()
                    return
                steps = self._experiment_plan_import_pending_steps
                payload = self._experiment_plan_import_pending_payload
                if payload is None or not steps:
                    self._experiment_plan_import_fill_timer.stop()
                    self._finalize_experiment_plan_import_population()
                    return
                self._experiment_plan_import_fill_timer.stop()
                self._populate_experiment_control_table(steps, selected_row=self._experiment_plan_import_pending_selected_row)
                self._finalize_experiment_plan_import_population()
            except Exception as exc:
                self._abort_experiment_plan_import_population(str(exc))

        self._run_gui_callback_timed("experiment_plan_import_population", _callback)

    def _finalize_experiment_plan_import_population(self) -> None:
        try:
            payload = self._experiment_plan_import_pending_payload
            steps = self._experiment_plan_import_pending_steps
            if payload is None:
                self._set_experiment_plan_import_running(False)
                return
            if steps:
                self.timeline_widget.set_steps(steps, 0, 0.0)
                self.save_ui_state()
                save_app_setting("experiment_plan_import_dir", str(payload.path.parent))
                self._set_status_message(f"Imported experiment plan from {payload.path.name}.")
        except Exception as exc:
            self._abort_experiment_plan_import_population(str(exc))
            return
        self._experiment_plan_import_pending_payload = None
        self._experiment_plan_import_pending_steps = []
        self._experiment_plan_import_pending_selected_row = None
        self._experiment_plan_import_pending_step_index = 0
        self._set_experiment_plan_import_running(False)

    def _abort_experiment_plan_import_population(self, message: str) -> None:
        self._experiment_plan_import_fill_timer.stop()
        self._experiment_plan_import_pending_payload = None
        self._experiment_plan_import_pending_steps = []
        self._experiment_plan_import_pending_selected_row = None
        self._experiment_plan_import_pending_step_index = 0
        self._set_experiment_plan_import_running(False)
        self._show_error(f"Could not import experiment plan:\n{message}")

    def _import_experiment_control_plan_from_file(self) -> None:
        start_dir = self._experiment_plan_import_default_dir()
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import experiment plan",
            str(start_dir),
            "Experiment plan files (*.flow.yaml *.yaml *.yml *.csv *.txt *.h5 *.hdf5);;Native YAML (*.flow.yaml *.yaml *.yml);;HDF5 measurement (*.h5 *.hdf5);;Compatibility CSV/TXT (*.csv *.txt);;All files (*)",
        )
        if not file_path:
            return

        self._start_experiment_plan_import(Path(file_path))

    def _export_experiment_control_plan_placeholder(self) -> None:
        if self._experiment_plan_export_in_progress or self._experiment_plan_import_in_progress:
            return
        steps = self._read_experiment_control_steps()
        if not steps:
            QMessageBox.warning(self, "Export experiment plan", "There is no experiment plan to export.")
            return
        start_dir = self._experiment_plan_export_default_dir()
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export experiment plan",
            str(start_dir / "experiment_plan.flow.yaml"),
            "Native YAML (*.flow.yaml *.yaml *.yml);;Compatibility CSV (*.csv);;Compatibility TXT (*.txt);;All files (*)",
        )
        if not file_path:
            return
        path = Path(file_path)
        if not path.suffix:
            if "TXT" in selected_filter:
                path = path.with_suffix(".txt")
            elif "CSV" in selected_filter:
                path = path.with_suffix(".csv")
            else:
                path = path.with_suffix(".flow.yaml")
        self._start_experiment_plan_export(path)

    def _edit_switch_solution_labels(self, anchor: QWidget | None = None) -> None:
        dialogs = ExperimentControlDialogs(self, self._theme_palette(), self._contrast_text_color, self._tint_icon)
        updated_labels = dialogs.edit_switch_solution_labels(self._switch_solution_labels, anchor)
        if updated_labels is None:
            return
        self._switch_solution_labels = updated_labels
        self._refresh_switch_solution_controls()
        self._update_timeline_selection()

    def _edit_pause_state(self, anchor: QWidget | None = None) -> None:
        dialogs = ExperimentControlDialogs(self, self._theme_palette(), self._contrast_text_color, self._tint_icon)
        updated_step = dialogs.edit_pause_state(self._pause_row_step(), anchor or self.pause_state_button)
        if updated_step is None:
            return
        self._experiment_control_pause_template = updated_step
        self.save_ui_state()
        self._set_status_message("Pause state updated.")
        _LOGGER.info("Pause state updated.")

    def _apply_pause_state(self) -> None:
        step = self._pause_row_step()
        if step is None:
            return
        # Dispatched async - _apply_step_to_pump_async already catches and
        # logs internally, so no try/except is needed here (unlike the old
        # synchronous call this replaces).
        self._apply_step_to_pump_async(step, start=False)

    def _edit_color_palette_entries(self, anchor: QWidget | None = None) -> None:
        dialogs = ExperimentControlDialogs(self, self._theme_palette(), self._contrast_text_color, self._tint_icon)
        updated_entries = dialogs.edit_color_palette_entries(self._color_palette_entries, anchor)
        if updated_entries is None:
            return
        self._color_palette_entries = updated_entries
        self._sync_custom_plan_colors_from_palette()
        self._save_color_palette_entries()
        self._update_experiment_control_timeline_label_mode()
        self._refresh_experiment_control_view()
        self._update_timeline_selection()
        return

    def _set_step_valve_button_state(self, valve: str) -> None:
        set_step_valve_button_state_for_button(self, self.step_valve_button, valve)

    def _toggle_step_valve_button(self, button: QToolButton | None = None) -> None:
        if not isinstance(button, QToolButton):
            button = self.step_valve_button
        current = str(button.property("valve") or "Open")
        next_state = "Close" if current != "Close" else "Open"
        set_step_valve_button_state_for_button(self, button, next_state)

    def _load_valve_state_labels(self, state: dict[str, object]) -> dict[str, str]:
        labels = {"Open": "Open", "Close": "Close"}
        payload = state.get("valve_state_labels")
        if isinstance(payload, dict):
            open_label = str(payload.get("Open", "Open")).strip() or "Open"
            close_label = str(payload.get("Close", "Close")).strip() or "Close"
            labels["Open"] = open_label
            labels["Close"] = close_label
        return labels

    def _load_valve_state_colors(self, state: dict[str, object]) -> dict[str, str]:
        colors = {"Open": "#4E79A7", "Close": "#B44A4A"}
        payload = state.get("valve_state_colors")
        if isinstance(payload, dict):
            open_color = QColor(str(payload.get("Open", colors["Open"]) or colors["Open"]).strip())
            close_color = QColor(str(payload.get("Close", colors["Close"]) or colors["Close"]).strip())
            if open_color.isValid():
                colors["Open"] = open_color.name().upper()
            if close_color.isValid():
                colors["Close"] = close_color.name().upper()
        return colors

    def _valve_state_label(self, valve: str) -> str:
        normalized = "Close" if str(valve or "").strip().lower() == "close" else "Open"
        label = str(self._valve_state_labels.get(normalized, normalized)).strip()
        return label or normalized

    def _valve_state_color(self, valve: str) -> str:
        normalized = "Close" if str(valve or "").strip().lower() == "close" else "Open"
        color = QColor(str(self._valve_state_colors.get(normalized, "")).strip())
        if color.isValid():
            return color.name().upper()
        return "#4E79A7" if normalized == "Open" else "#B44A4A"

    def _edit_valve_state_labels(self, anchor: QWidget | None = None) -> None:
        dialogs = ExperimentControlDialogs(self, self._theme_palette(), self._contrast_text_color, self._tint_icon)
        updated = dialogs.edit_valve_labels(self._valve_state_labels, self._valve_state_colors, anchor)
        if updated is None:
            return
        updated_labels, updated_colors = updated
        self._valve_state_labels = updated_labels
        self._valve_state_colors = updated_colors
        set_step_valve_button_state_for_button(self, self.step_valve_button, str(self.step_valve_button.property("valve") or "Open"))
        self._refresh_experiment_control_view()
        self.save_ui_state()
        return

    def _edit_pump_display_settings(self, anchor: QWidget | None = None) -> None:
        dialogs = ExperimentControlDialogs(self, self._theme_palette(), self._contrast_text_color, self._tint_icon)
        before = (self._pump_display_enabled, self._pump_display_highlight_enabled)
        updated = dialogs.edit_pump_display_settings(self.step_comment_edit, before[0], before[1], anchor)
        if updated is None:
            return
        push_snapshot(self.undo_stack, "Pump display settings", before, updated, apply=self._apply_pump_display_settings)

    def _apply_pump_display_settings(self, value: tuple[bool, bool]) -> None:
        self._pump_display_enabled, self._pump_display_highlight_enabled = value
        self._update_step_comment_display_button_icon()
        self.save_ui_state()
        # This is a global setting (applies to every step), not a per-row edit, so there's
        # no selected-row write-back - just repaint the table so the Comment column's live
        # highlight reflects the new setting immediately.
        self.plan_table.viewport().update()
        # If a step is currently applied to hardware, push its display text immediately
        # instead of waiting for the next step transition - otherwise toggling this would
        # silently do nothing until the plan happens to advance again.
        if self._applied_plan_step is not None:
            self._push_step_pump_display_now(self._applied_plan_step)

    def _push_step_pump_display_now(self, step: PumpPlanStep) -> None:
        if not self._service_device_connected("pump"):
            return
        pump_label = self._device_label_for("pump")
        text = str(step.description or "").strip() if self._pump_display_enabled else ""
        command = _PlannedCommand(pump_label, "pump.set_display", {"text": text}, f"pump.set_display text={text!r}")
        runnable = _StepApplyRunnable(self._device_comm_service, [command], step, False, [])
        runnable.signals.done.connect(self._on_step_apply_async_done)
        device_io_pool().start(runnable)

    def _update_step_comment_display_button_icon(self) -> None:
        enabled = self._pump_display_enabled
        color = QColor("#5fa8ff") if enabled else QColor("#f0f3f7")
        self.step_comment_display_button.setIcon(tint_tabler_icon(flow_tabler_icon("settings"), color))
        self.step_comment_display_button.setToolTip(
            "Pump display: showing all step comments. Click to configure."
            if enabled
            else "Show all step comments on the pump display, and preview the 16-character limit."
        )

    def _set_direction_button(self, button: QToolButton, direction: str) -> None:
        normalized = "CCW" if str(direction or "").upper() == "CCW" else "CW"
        button.setProperty("direction", normalized)
        button.setText(direction_glyph(normalized))

    def _direction_button_value(self, button: QToolButton) -> str:
        value = button.property("direction")
        return str(value) if value in {"CW", "CCW"} else "CW"

    def _toggle_direction_button(self, button: QToolButton, on_change=None) -> None:
        next_direction = "CCW" if self._direction_button_value(button) == "CW" else "CW"
        self._set_direction_button(button, next_direction)
        if on_change is not None:
            on_change()

    def _update_experiment_control_toggle_button(self) -> None:
        snapshot = self._experiment_runtime_snapshot()
        timer = getattr(self, "_plan_hold_blink_timer", None)
        if snapshot.blink_active:
            if timer is not None and not timer.isActive():
                self._plan_hold_blink_frame = 0
                timer.start()
        else:
            if timer is not None and timer.isActive():
                timer.stop()
            self._plan_hold_blink_frame = 0

        self.plan_toggle_button.setIcon(self._play_plan_button_icon(active=snapshot.play_active))
        if snapshot.running:
            self.plan_toggle_button.setToolTip("Plan running. Click to resume from the current state.")
        elif snapshot.holding or snapshot.paused:
            self.plan_toggle_button.setToolTip("Resume the plan.")
        else:
            self.plan_toggle_button.setToolTip("Start the plan.")

        self.hold_plan_button.setVisible(snapshot.hold_visible)
        self.hold_plan_button.setEnabled(snapshot.hold_enabled)
        self.hold_plan_button.setIcon(self._hold_plan_button_icon(active=snapshot.holding))
        if snapshot.holding:
            self.hold_plan_button.setToolTip("Hold active. Click to resume from hold.")
            self.hold_plan_button.setChecked(True)
        elif snapshot.running:
            self.hold_plan_button.setToolTip("Hold the running plan.")
            self.hold_plan_button.setChecked(False)
        else:
            self.hold_plan_button.setToolTip("Hold is available only while the plan is running.")
            self.hold_plan_button.setChecked(False)

        self.pause_plan_button.setEnabled(snapshot.pause_available)
        self.pause_plan_button.setIcon(self._runtime_pause_button_icon(active=snapshot.paused))
        if snapshot.paused:
            self.pause_plan_button.setToolTip("Pause active. Click to resume from pause.")
            self.pause_plan_button.setChecked(True)
        elif snapshot.running:
            self.pause_plan_button.setToolTip("Apply the pause state.")
            self.pause_plan_button.setChecked(False)
        elif snapshot.holding:
            self.pause_plan_button.setToolTip("Apply the pause state from hold.")
            self.pause_plan_button.setChecked(False)
        elif snapshot.pause_available:
            self.pause_plan_button.setToolTip("Start the plan in pause state.")
            self.pause_plan_button.setChecked(False)
        else:
            self.pause_plan_button.setToolTip("Pause is available only while the plan is running.")
            self.pause_plan_button.setChecked(False)

        self.stop_plan_button.setVisible(snapshot.stop_visible)
        self.stop_plan_button.setEnabled(snapshot.stop_visible)

    def _play_plan_button_icon(self, *, active: bool) -> QIcon:
        if active:
            alpha_steps = [255, 255, 220, 160, 90, 40, 90, 160]
            frame_index = int(getattr(self, "_plan_hold_blink_frame", 0)) % len(alpha_steps)
            color = QColor("#38d862")
            color.setAlpha(alpha_steps[frame_index])
        else:
            color = QColor("#38d862")
        try:
            return tint_tabler_icon(flow_tabler_icon("player_play", "play"), color)
        except Exception:
            return transport_icon(self._theme_mode, "play")

    def _advance_plan_hold_blink_indicator(self) -> None:
        def _callback() -> None:
            timer = getattr(self, "_plan_hold_blink_timer", None)
            if timer is None or not timer.isActive() or not (self._plan_running or self._plan_holding or self._plan_paused):
                return
            self._plan_hold_blink_frame = (int(getattr(self, "_plan_hold_blink_frame", 0)) + 1) % 8
            self._update_experiment_control_toggle_button()

        self._run_gui_callback_timed("plan_hold_blink", _callback)

    def _update_record_with_flow_button_icon(self) -> None:
        if not hasattr(self, "record_with_flow_button"):
            return
        active = self.record_with_flow_button.isChecked()
        recording_active = bool(getattr(self, "_record_with_flow_recording_active", False))
        color = QColor("#47a861" if active else "#8a98a8")
        self.record_with_flow_button.setIcon(tint_tabler_icon(flow_tabler_icon("file_pencil"), color))
        if recording_active:
            self.record_with_flow_button.setToolTip("Sensorgram recording is active. The setting is locked while recording.")
        else:
            self.record_with_flow_button.setToolTip("Record measurement data while the experiment plan runs.")

    def _handle_record_with_flow_button_toggled(self, checked: bool) -> None:
        recording_active = bool(getattr(self, "_record_with_flow_recording_active", False))
        if recording_active:
            locked_checked = bool(getattr(self, "_record_with_flow_locked_checked", True))
            if bool(checked) != locked_checked:
                self.record_with_flow_button.blockSignals(True)
                try:
                    self.record_with_flow_button.setChecked(locked_checked)
                finally:
                    self.record_with_flow_button.blockSignals(False)
            self._update_record_with_flow_button_icon()
            return
        self._record_with_flow_locked_checked = bool(checked)
        self._update_record_with_flow_button_icon()

    def _set_record_with_flow_recording_active(self, active: bool) -> None:
        self._record_with_flow_recording_active = bool(active)
        if active:
            self._record_with_flow_locked_checked = bool(self.record_with_flow_button.isChecked())
            if self._pending_experiment_control_start_after_recording is not None:
                QTimer.singleShot(0, self._run_pending_experiment_control_start_after_recording)
        if not active:
            if self._measurement_started_monotonic is not None:
                self._plan_runtime_s = self._plan_runtime_for_display()
            if self._step_started_monotonic is not None:
                self._plan_resume_runtime_s = self._step_runtime_for_display()
            self._measurement_started_monotonic = None
            self._step_started_monotonic = None
            self._pending_experiment_control_start_after_recording = None
            self._refresh_status_line()
            if self._read_experiment_control_steps():
                self.timeline_widget.set_steps(
                    self._read_experiment_control_steps(),
                    self._experiment_control_timeline_row(),
                    self._timeline_progress_for_display(),
                    self._plan_runtime_for_display(),
                    self._step_runtime_for_display(),
                )
        self._update_record_with_flow_button_icon()

    def _toggle_experiment_control_run_hold(self) -> None:
        self._start_or_resume_experiment_control()

    def _toggle_experiment_control_hold(self) -> None:
        if self._plan_running:
            self._hold_experiment_control()
            return
        if self._plan_holding:
            self._start_or_resume_experiment_control()

    def _toggle_experiment_control_pause(self) -> None:
        if self._plan_running:
            self._pause_experiment_control()
            return
        if self._plan_holding:
            self._pause_experiment_control()
            return
        if self._plan_paused:
            self._start_or_resume_experiment_control()
            return
        self._start_paused_experiment_control()

    def _set_plan_runtime_flags(self, *, running: bool, holding: bool, paused: bool) -> None:
        self._plan_running = bool(running)
        self._plan_holding = bool(holding)
        self._plan_paused = bool(paused)

    def _capture_plan_elapsed_from_clock(self) -> float:
        if self._plan_started_monotonic is None:
            return max(float(self._plan_elapsed_s), 0.0)
        elapsed = self._plan_resume_elapsed_s + max(monotonic() - self._plan_started_monotonic, 0.0)
        self._plan_elapsed_s = elapsed
        self._plan_resume_elapsed_s = elapsed
        return elapsed

    def _reset_plan_runtime_counters(self) -> None:
        self._plan_elapsed_s = 0.0
        self._plan_resume_elapsed_s = 0.0
        self._plan_runtime_s = 0.0
        self._plan_resume_runtime_s = 0.0

    def _ensure_measurement_started(self) -> None:
        if self._measurement_started_monotonic is None:
            self._measurement_started_monotonic = monotonic()

    def _experiment_runtime_snapshot(self) -> ExperimentRuntimeSnapshot:
        return experiment_runtime_snapshot(
            running=self._plan_running,
            holding=self._plan_holding,
            paused=self._plan_paused,
            recording=bool(self.__dict__.get("_measurement_started_monotonic") is not None),
            has_steps=bool(self._read_experiment_control_steps()),
        )

    def _set_experiment_control_runtime_row_property(self, row: int | None) -> None:
        self.plan_table.setProperty("experiment_control_runtime_row", row)
        self.plan_table.viewport().setProperty("experiment_control_runtime_row", row)

    def _ensure_experiment_control_plan_row_visible(self, plan_row: int | None, *, center: bool = False) -> None:
        if plan_row is None:
            return
        table_row = self._table_row_from_plan_row(plan_row)
        if not (0 <= table_row < self.plan_table.rowCount()):
            return
        model = self.plan_table.model()
        if model is None:
            return
        index = model.index(table_row, 0)
        if not index.isValid():
            return
        hint = QAbstractItemView.ScrollHint.PositionAtCenter if center else QAbstractItemView.ScrollHint.EnsureVisible
        self.plan_table.scrollTo(index, hint)

    def _sync_experiment_control_timeline(self, steps: list[PumpPlanStep], plan_row: int | None, *, refresh_status: bool = False) -> None:
        runtime_row = plan_row if (self._plan_running or self._plan_holding or self._plan_paused) else None
        self._set_experiment_control_runtime_row_property(runtime_row)
        self.timeline_widget.set_steps(
            steps,
            plan_row,
            self._timeline_progress_for_display(),
            self._plan_runtime_for_display(),
            self._step_runtime_for_display(),
            # `steps` is always the return value of `_read_experiment_control_steps()`
            # from every caller of this method, which already ran it through
            # `recompute_plan_timing` — skip the widget's own redundant pass so the
            # 150ms (or 50ms) progress tick doesn't deepcopy every step twice.
            already_normalized=True,
        )
        self._ensure_experiment_control_plan_row_visible(
            plan_row if (self._plan_running or self._plan_holding or self._plan_paused) else self._selected_experiment_control_row(),
            center=bool(self._plan_running or self._plan_holding or self._plan_paused),
        )
        controller = getattr(self, "_experiment_control_edit_controller", None)
        if controller is not None:
            controller.sync_overlay()
        if refresh_status:
            self._refresh_status_line()

    def _resume_experiment_plan(
        self,
        *,
        restore_step: PumpPlanStep | None = None,
        status_message: str,
        log_message: str,
        emit_event: str,
        emit_step: PumpPlanStep | None = None,
    ) -> None:
        if restore_step is not None:
            self._apply_step_to_pump_async(restore_step, start=True)
        self._set_plan_runtime_flags(running=True, holding=False, paused=False)
        self._plan_started_monotonic = monotonic()
        self._plan_timer.stop()
        self._schedule_plan_timer()
        self._update_experiment_control_toggle_button()
        self._set_status_message(status_message)
        _LOGGER.info(log_message)
        steps = self._read_experiment_control_steps()
        if self._plan_active_row is not None and 0 <= self._plan_active_row < len(steps):
            self._emit_experimental_control_state(emit_event, emit_step or steps[self._plan_active_row])

    def _begin_experiment_plan_run(self, row: int, steps: list[PumpPlanStep]) -> None:
        self._reset_plan_runtime_counters()
        self._ensure_measurement_started()
        self._step_started_monotonic = monotonic()
        self._set_plan_runtime_flags(running=True, holding=False, paused=False)
        self._plan_active_row = row
        self._plan_started_monotonic = monotonic()
        self._update_experiment_control_toggle_button()
        self._activate_experiment_control_step_for_elapsed(0.0, force=True)
        self._schedule_plan_timer()
        self._set_status_message(f"Running experiment plan from step {self._plan_active_row + 1 if self._plan_active_row is not None else 1}.")
        _LOGGER.info("Experiment plan started | step=%s", self._plan_active_row + 1 if self._plan_active_row is not None else 1)
        if self._plan_active_row is not None and 0 <= self._plan_active_row < len(steps):
            self._emit_experimental_control_state("plan_started", steps[self._plan_active_row])

    def _begin_paused_experiment_plan_run(self, row: int, steps: list[PumpPlanStep]) -> None:
        self._paused_plan_step = deepcopy(steps[row])
        self._plan_active_row = row
        self._plan_timer.stop()
        self._reset_plan_runtime_counters()
        self._ensure_measurement_started()
        self._set_plan_runtime_flags(running=False, holding=False, paused=True)
        self._plan_started_monotonic = None
        self._step_started_monotonic = None
        self._schedule_plan_timer()
        self._apply_pause_state()
        self._update_experiment_control_toggle_button()
        self._set_status_message(
            f"Experiment plan started in pause state on step {self._plan_active_row + 1 if self._plan_active_row is not None else 1}."
        )
        _LOGGER.info(
            "Experiment plan started in pause state | step=%s",
            self._plan_active_row + 1 if self._plan_active_row is not None else 1,
        )
        if self._plan_active_row is not None and 0 <= self._plan_active_row < len(steps):
            self._emit_experimental_control_state("plan_pause", self._applied_plan_step, status="started in pause state")

    def _resume_experiment_control_after_manual_step_change(
        self,
        row: int,
        *,
        status_message: str,
        log_message: str,
        emit_event: str,
    ) -> None:
        steps = self._read_experiment_control_steps()
        if row < 0 or row >= len(steps):
            return
        # Dispatched async and not gated on the result: plan-runtime state
        # always advances here, exactly like every other step-apply trigger.
        # A hardware failure is surfaced via the status bar text a moment
        # later (and durably logged to the session HDF5 file regardless of
        # active recording - see _handle_experimental_control_state_recorded
        # in main_window.py) rather than silently blocking the resume.
        self._apply_step_to_pump_async(steps[row], start=True)
        self._paused_plan_step = None
        self._reset_plan_runtime_counters()
        self._ensure_measurement_started()
        self._set_plan_runtime_flags(running=True, holding=False, paused=False)
        self._plan_active_row = row
        self._plan_elapsed_s = 0.0
        self._plan_resume_elapsed_s = 0.0
        self._plan_started_monotonic = monotonic()
        self._step_started_monotonic = monotonic()
        self._plan_timer.stop()
        self._schedule_plan_timer()
        self._update_experiment_control_toggle_button()
        self._sync_experiment_control_timeline(steps, row, refresh_status=True)
        self._set_status_message(status_message)
        _LOGGER.info(log_message)
        self._emit_experimental_control_state(emit_event, steps[row])

    def _queue_experiment_control_start_after_recording(self, *, paused: bool, row: int | None) -> None:
        self._pending_experiment_control_start_after_recording = (bool(paused), row)

    def _run_pending_experiment_control_start_after_recording(self) -> None:
        pending = self._pending_experiment_control_start_after_recording
        if pending is None:
            return
        self._pending_experiment_control_start_after_recording = None
        paused, row = pending
        steps = self._read_experiment_control_steps()
        if not steps:
            self._set_status_message("Experiment plan is empty.")
            return
        if row is None or not (0 <= int(row) < len(steps)):
            row = self._selected_experiment_control_row()
            if row is None:
                row = 0
                self._select_experiment_control_plan_row(0)
        if paused:
            self._begin_paused_experiment_plan_run(int(row), steps)
        else:
            self._begin_experiment_plan_run(int(row), steps)

    def _enter_hold_state(self) -> None:
        if not self._plan_running:
            return
        # HOLD freezes plan time and cursor position, but does not stop recording.
        self._capture_plan_elapsed_from_clock()
        self._set_plan_runtime_flags(running=False, holding=True, paused=False)
        self._plan_started_monotonic = None
        self._plan_runtime_s = self._step_runtime_for_display()
        self._update_experiment_control_toggle_button()
        self._set_status_message("Experiment plan hold.")
        _LOGGER.info("Experiment plan hold.")
        self._emit_experimental_control_state("plan_hold", self._applied_plan_step)

    def _enter_pause_state(self, *, restore_step: PumpPlanStep | None = None) -> None:
        if not (self._plan_running or self._plan_holding):
            return
        if self._plan_running:
            self._capture_plan_elapsed_from_clock()
        self._paused_plan_step = deepcopy(self._applied_plan_step) if self._applied_plan_step is not None else None
        if restore_step is not None:
            self._paused_plan_step = deepcopy(restore_step)
        self._apply_pause_state()
        self._set_plan_runtime_flags(running=False, holding=False, paused=True)
        self._plan_started_monotonic = None
        self._plan_runtime_s = self._step_runtime_for_display()
        self._step_started_monotonic = None
        self._update_experiment_control_toggle_button()
        self._set_status_message("Experiment plan paused.")
        _LOGGER.info("Experiment plan paused.")
        self._emit_experimental_control_state("plan_pause", self._applied_plan_step)

    def _stop_experiment_plan(self, last_step: PumpPlanStep | None) -> None:
        steps = self._read_experiment_control_steps()
        target_row = self._plan_active_row
        if target_row is None:
            target_row = self._selected_experiment_control_row()
        if target_row is None and steps:
            target_row = 0
        if steps and target_row is not None:
            target_row = min(max(int(target_row), 0), len(steps) - 1)
            self._plan_active_row = target_row
            if not (self._plan_running or self._plan_holding or self._plan_paused):
                self._plan_elapsed_s = 0.0
            self._plan_resume_elapsed_s = self._plan_elapsed_s
            self._sync_experiment_control_timeline(steps, target_row)
        if self._plan_running:
            self._capture_plan_elapsed_from_clock()
        self._set_plan_runtime_flags(running=False, holding=False, paused=False)
        self._plan_started_monotonic = None
        self._step_started_monotonic = None
        self._measurement_started_monotonic = None
        self._plan_runtime_s = self._plan_runtime_for_display()
        self._plan_resume_runtime_s = self._step_runtime_for_display()
        self._applied_plan_step = None
        self._paused_plan_step = None
        self._plan_timer.stop()
        self._update_experiment_control_toggle_button()
        if self._service_device_connected("pump"):
            self._stop_all_channels()
        else:
            self._set_status_message("Experiment plan stopped.")
        _LOGGER.info("Experiment plan stopped.")
        self._emit_experimental_control_state("plan_stopped", last_step)
        self._request_recording_control("stop")

    def _start_paused_experiment_control(self) -> None:
        steps = self._read_experiment_control_steps()
        if not steps:
            self._set_status_message("Experiment plan is empty.")
            return
        recording_active = bool(getattr(getattr(self, "recording_controller", None), "_measurement_active", False))
        if self.record_with_flow_button.isChecked() and not recording_active:
            row = self._selected_experiment_control_row()
            if row is None:
                row = 0
            self._queue_experiment_control_start_after_recording(paused=True, row=row)
        if not self._request_recording_control("start"):
            self._pending_experiment_control_start_after_recording = None
            self._set_status_message("Experiment plan start cancelled because recording was not started.")
            return
        if self._pending_experiment_control_start_after_recording is not None:
            return
        row = self._selected_experiment_control_row()
        if row is None:
            row = 0
            self._select_experiment_control_plan_row(0)
        self._begin_paused_experiment_plan_run(row, steps)

    def _request_recording_control(self, action: str) -> None:
        if not self.record_with_flow_button.isChecked():
            return True
        if str(action or "").strip().lower() == "pause":
            return True
        controller = getattr(self, "recording_controller", None)
        if controller is not None and hasattr(controller, "_handle_flow_recording_control"):
            return bool(controller._handle_flow_recording_control(action))
        self.recording_control_requested.emit(action)
        return True

    def _cycle_time_unit_mode(self) -> None:
        order = ["s", "min", "h"]
        next_index = (order.index(self._time_unit_mode) + 1) % len(order)
        new_mode = order[next_index]
        if new_mode == self._time_unit_mode:
            self._update_time_unit_ui()
            return
        steps = self._read_experiment_control_steps()
        selected_row = self._selected_experiment_control_row()
        self._time_unit_mode = new_mode
        self._update_time_unit_ui(self._editor_duration_seconds)
        self._populate_experiment_control_table(steps)
        if selected_row is not None and 0 <= selected_row < self.plan_table.rowCount():
            self._select_experiment_control_plan_row(selected_row)
        self._refresh_status_line()
        self.save_ui_state()

    def _theme_palette(self) -> dict[str, str]:
        if self._theme_mode == "dark":
            return {
                "bg": "#13161b",
                "fg": "#e6ebf1",
                "muted": "#a8b0ba",
                "field": "#171b21",
                "button": "#20252d",
                "button_hover": "#272d36",
                "button_pressed": "#303640",
                "accent_button": "#5d6876",
                "accent_hover": "#707d8c",
                "title": "#8fbaff",
                "danger_button": "#8f5a61",
                "danger_hover": "#a46a72",
                "border": "#2b3138",
                "border_hover": "#414852",
                "pressed": "#252b33",
                "scroll": "#49505a",
                "scroll_hover": "#5c6470",
                "splitter": "#2b3138",
                "timeline_bg": "#0f1216",
                "header": "#1b2026",
                "selection": "#252b33",
            }
        return {
            "bg": "#f4f6f8",
            "fg": "#1d2733",
            "muted": "#5f7388",
            "field": "#f4f6f8",
            "button": "#eef3f7",
            "button_hover": "#e6edf3",
            "button_pressed": "#dde9f3",
            "accent_button": "#2f80c1",
            "accent_hover": "#3e8dcf",
            "title": "#2f80c1",
            "danger_button": "#d65a63",
            "danger_hover": "#e06a73",
            "border": "#d9e0e7",
            "border_hover": "#9dbbd4",
            "pressed": "#dde9f3",
            "scroll": "#bcc9d5",
            "scroll_hover": "#9fb3c5",
            "splitter": "#dde5ec",
            "timeline_bg": "#f4f6f8",
            "header": "#eef3f7",
            "selection": "#dbeafe",
        }

    def _apply_style(self) -> None:
        palette = self._theme_palette()
        self.setStyleSheet(
            """
            QWidget {
                background: %(bg)s;
                color: %(fg)s;
                font-size: 12px;
            }
            QToolTip {
                background-color: %(bg)s;
                color: %(fg)s;
                border: 1px solid %(border)s;
                padding: 4px 6px;
            }
            QGroupBox {
                background: %(bg)s;
                border: 1px solid %(border)s;
                border-radius: 12px;
                margin-top: 8px;
                padding-top: 10px;
                font-weight: 600;
            }
            QGroupBox::title {
                left: 10px;
                top: 2px;
            }
            QPushButton, QToolButton, QComboBox, QDoubleSpinBox, QLineEdit, QTableWidget {
                background: %(field)s;
                border: 1px solid %(border)s;
                border-radius: 10px;
                padding: 4px 6px;
            }
            QSpinBox, QDoubleSpinBox {
                border-radius: 3px;
                padding: 1px 4px;
            }
            QSpinBox::up-button, QSpinBox::down-button,
            QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
                width: 0px;
                border: none;
                background: transparent;
            }
            QSpinBox::up-arrow, QSpinBox::down-arrow,
            QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow {
                width: 0px;
                height: 0px;
            }
            QPushButton:hover, QToolButton:hover, QComboBox:hover, QDoubleSpinBox:hover, QLineEdit:hover {
                border-color: %(border_hover)s;
                background: %(button_hover)s;
            }
            QPushButton:pressed, QToolButton:pressed {
                background: %(button_pressed)s;
            }
            QPushButton#accentButton {
                background: %(accent_button)s;
                border-color: %(accent_button)s;
            }
            QPushButton#accentButton:hover, QToolButton#accentButton:hover {
                background: %(accent_hover)s;
                border-color: %(accent_hover)s;
            }
            QToolButton#accentButton {
                background: %(accent_button)s;
                border-color: %(accent_button)s;
            }
            QPushButton#dangerButton {
                background: %(danger_button)s;
                border-color: %(danger_button)s;
            }
            QPushButton#dangerButton:hover, QToolButton#dangerButton:hover {
                background: %(danger_hover)s;
                border-color: %(danger_hover)s;
            }
            QToolButton#dangerButton {
                background: %(danger_button)s;
                border-color: %(danger_button)s;
            }
            QToolButton#flowIconButton {
                background: transparent;
                border: none;
                padding: 0px;
            }
            QToolButton#flowIconButton:hover {
                background: rgba(127, 127, 127, 0.10);
                border: none;
            }
            QToolButton#flowIconButton:pressed {
                background: rgba(127, 127, 127, 0.18);
                border: none;
            }
            QToolButton#flowViewModeButton {
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
                color: #e8d85f;
                font-weight: 600;
            }
            QToolButton#flowViewModeButton:hover {
                background: rgba(127, 127, 127, 0.10);
                border: none;
            }
            QToolButton#flowViewModeButton:pressed {
                background: rgba(127, 127, 127, 0.18);
                border: none;
            }
            QToolButton#flowColorAddButton {
                background: transparent;
                border: none;
                padding: 0px;
                min-width: 18px;
                min-height: 18px;
            }
            QToolButton#flowColorAddButton:hover {
                background: rgba(47, 143, 83, 0.10);
            }
            QToolButton#flowColorAddButton:pressed {
                background: rgba(47, 143, 83, 0.18);
            }
            QToolButton#flowColorRemoveButton {
                background: transparent;
                border: none;
                padding: 0px;
                min-width: 18px;
                min-height: 18px;
                color: #b44a4a;
            }
            QToolButton#flowColorRemoveButton:hover {
                background: rgba(180, 74, 74, 0.10);
            }
            QToolButton#flowColorRemoveButton:pressed {
                background: rgba(180, 74, 74, 0.18);
            }
            QToolButton#flowSwitchModeButton,
            QToolButton#flowSwitchSettingsButton,
            QToolButton#flowCommentDisplayButton {
                background: transparent;
                border: none;
                padding: 0px;
                min-width: 18px;
                min-height: 18px;
                color: #f0f3f7;
            }
            QToolButton#flowValveSettingsButton {
                background: transparent;
                border: none;
                padding: 0px;
                min-width: 18px;
                min-height: 18px;
                color: #f0f3f7;
            }
            QToolButton#flowSwitchModeButton:hover,
            QToolButton#flowSwitchSettingsButton:hover,
            QToolButton#flowValveSettingsButton:hover,
            QToolButton#flowCommentDisplayButton:hover {
                background: rgba(127, 127, 127, 0.10);
            }
            QToolButton#flowSwitchModeButton:pressed,
            QToolButton#flowSwitchSettingsButton:pressed,
            QToolButton#flowValveSettingsButton:pressed,
            QToolButton#flowCommentDisplayButton:pressed {
                background: rgba(127, 127, 127, 0.18);
            }
            QLabel#flowHeaderLabel {
                color: %(muted)s;
                font-size: 13px;
                font-weight: 800;
                letter-spacing: 0.8px;
            }
            QWidget#flowContent, QWidget#flowEditorContainer {
                background: %(bg)s;
                border: none;
            }
            QTableView#flowControlTable {
                background: %(bg)s;
                border: none;
                border-radius: 0px;
                gridline-color: %(border)s;
                alternate-background-color: %(button)s;
                selection-background-color: transparent;
                selection-color: %(fg)s;
                font-size: 11px;
            }
            QTableView#flowControlTable::viewport {
                background: %(bg)s;
                border: none;
            }
            QTableView#flowControlTable::item {
                border: none;
                padding: 1px 4px;
            }
            QTableView#flowControlTable QComboBox,
            QTableView#flowControlTable QDoubleSpinBox,
            QTableView#flowControlTable QLineEdit,
            QTableView#flowControlTable QToolButton {
                background: transparent;
                border: none;
                padding: 0px 1px;
                margin: 0px;
            }
            QTableView#flowControlTable QComboBox::drop-down {
                border: none;
                background: transparent;
                width: 0px;
            }
            QTableView#flowControlTable QComboBox::down-arrow {
                width: 0px;
                height: 0px;
            }
            QTableView#flowControlTable QComboBox::item {
                padding: 0px 4px;
            }
            QTableView#flowControlTable QDoubleSpinBox::up-button,
            QTableView#flowControlTable QDoubleSpinBox::down-button {
                width: 0px;
                border: none;
                background: transparent;
            }
            QTableView#flowControlTable QDoubleSpinBox::up-arrow,
            QTableView#flowControlTable QDoubleSpinBox::down-arrow {
                width: 0px;
                height: 0px;
            }
            QTableView#flowControlTable::item:selected {
                background: transparent;
                background-color: transparent;
            }
            QTableView#flowControlTable::item:selected:active,
            QTableView#flowControlTable::item:selected:!active {
                background: transparent;
                background-color: transparent;
            }
            QTableView#flowControlTable QHeaderView::section {
                background: %(header)s;
                border: none;
                border-right: 1px solid %(border)s;
                border-bottom: 1px solid %(border)s;
                padding: 0px 1px;
                font-size: 10px;
                font-weight: 600;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
            }
            QScrollBar::handle:vertical {
                background: %(scroll)s;
                border-radius: 4px;
                min-height: 30px;
            }
            QScrollBar::handle:vertical:hover {
                background: %(scroll_hover)s;
            }
            QSplitter::handle {
                background: %(splitter)s;
            }
            QSplitter::handle:vertical {
                height: 6px;
                margin: 0 4px;
                border-radius: 3px;
            }
            """ % palette
        )

    def _update_time_unit_ui(self, current_seconds: float | None = None) -> None:
        labels = {"s": "s", "min": "min", "h": "h"}
        current_label = labels.get(self._time_unit_mode, "s")
        self.time_unit_toggle.setText(current_label)
        self.time_unit_toggle.setToolTip(
            f"Current display unit: {current_label}. Click to cycle between seconds, minutes, and hours. "
            "Internally and in saved data, times stay in seconds."
        )
        if current_seconds is None:
            current_seconds = self._editor_duration_seconds
        self._apply_duration_display_precision()
        self._suspend_duration_tracking = True
        self.step_duration_spin.blockSignals(True)
        if self._time_unit_mode == "min":
            self.step_duration_spin.setDecimals(1)
            self.step_duration_spin.setRange(0.0, 1440.0)
            self.step_duration_spin.setSingleStep(0.1)
            self.step_duration_spin.setSuffix("")
        elif self._time_unit_mode == "h":
            self.step_duration_spin.setDecimals(2)
            self.step_duration_spin.setRange(0.0, 24.0)
            self.step_duration_spin.setSingleStep(0.01)
            self.step_duration_spin.setSuffix("")
        else:
            self.step_duration_spin.setDecimals(0)
            self.step_duration_spin.setRange(0.0, 86400.0)
            self.step_duration_spin.setSingleStep(1.0)
            self.step_duration_spin.setSuffix("")
        self.step_duration_spin.setValue(round(self._seconds_to_display(current_seconds), self._duration_display_decimals()))
        self.step_duration_spin.blockSignals(False)
        self._suspend_duration_tracking = False
        self.timeline_widget.set_time_unit_mode(self._time_unit_mode)
        self._plan_model.set_time_unit_mode(self._time_unit_mode)
        self._update_experiment_control_headers()
        self._refresh_status_line()

    def _duration_display_decimals(self) -> int:
        if self._time_unit_mode == "min":
            return 1
        if self._time_unit_mode == "h":
            return 2
        return 0

    def _apply_duration_display_precision(self) -> None:
        decimals = self._duration_display_decimals()
        single_step = 1.0 if decimals == 0 else (0.1 if decimals == 1 else 0.01)
        if isinstance(self.step_duration_spin, QDoubleSpinBox):
            self.step_duration_spin.blockSignals(True)
            try:
                self.step_duration_spin.setDecimals(decimals)
                self.step_duration_spin.setSingleStep(single_step)
                self.step_duration_spin.setSuffix("")
                if decimals == 0:
                    self.step_duration_spin.setMaximum(86400.0)
                elif decimals == 1:
                    self.step_duration_spin.setMaximum(1440.0)
                else:
                    self.step_duration_spin.setMaximum(24.0)
            finally:
                self.step_duration_spin.blockSignals(False)

    def _format_seconds_display_value(self, seconds: float) -> str:
        value = max(self._seconds_to_display(seconds), 0.0)
        decimals = self._duration_display_decimals()
        if decimals == 0:
            return f"{int(round(value))}"
        return f"{value:.{decimals}f}"

    def _update_experiment_control_headers(self) -> None:
        unit = self._time_unit_mode
        headers = list(self.PLAN_COLUMNS)
        headers[0] = "Step"
        headers[1] = f"Duration [{unit}]"
        headers[2] = f"start_{unit}"
        headers[3] = f"end_{unit}"
        for channel_index in range(ACTIVE_PUMP_CHANNELS):
            headers[self._flow_rate_column(channel_index)] = f"CH{channel_index + 1}"
            headers[self._direction_column(channel_index)] = f"CH{channel_index + 1} Dir"
            headers[self._tube_column(channel_index)] = f"CH{channel_index + 1} Tube"
        headers[self._valve_column()] = "Valve"
        headers[self._switch_column()] = "Switch"
        headers[self._color_column()] = "Color"
        headers[self._description_column()] = "Comment"
        self._plan_model.set_headers(headers)
        self._configure_experiment_control_table_columns()

    def _configure_experiment_control_table_columns(self) -> None:
        configure_experiment_control_table_columns(self)

    def _default_experiment_control_table_column_widths(self) -> list[int]:
        widths = [36, 92, 0, 0]
        for _channel_index in range(ACTIVE_PUMP_CHANNELS):
            widths.extend([56, 42, 54])
        widths.extend([62, 90, 70, 200])
        return widths

    def _plan_table_column_widths(self) -> list[int]:
        widths: list[int] = []
        for column in range(self.plan_table.columnCount()):
            widths.append(int(self.plan_table.columnWidth(column)))
        return widths

    def _plan_table_header_state(self) -> str:
        header = self.plan_table.horizontalHeader()
        return bytes(header.saveState().toBase64()).decode("ascii")

    def _apply_plan_table_column_widths(self, widths: list[object] | None) -> None:
        defaults = self._default_experiment_control_table_column_widths()
        if not isinstance(widths, list):
            widths = []
        self._suppress_plan_table_layout_save = True
        self.plan_table.horizontalHeader().blockSignals(True)
        try:
            for column in range(self.plan_table.columnCount()):
                width_value = defaults[column] if column < len(defaults) else 68
                if column < len(widths):
                    try:
                        width_value = int(widths[column])
                    except (TypeError, ValueError):
                        pass
                if width_value > 0:
                    self.plan_table.setColumnWidth(column, width_value)
        finally:
            self.plan_table.horizontalHeader().blockSignals(False)
            self._suppress_plan_table_layout_save = False

    def _apply_plan_table_header_state(self, state_value: object) -> bool:
        if not isinstance(state_value, str) or not state_value:
            return False
        try:
            state_bytes = QByteArray.fromBase64(state_value.encode("ascii"))
        except Exception:
            return False
        if state_bytes.isEmpty():
            return False
        header = self.plan_table.horizontalHeader()
        self._suppress_plan_table_layout_save = True
        header.blockSignals(True)
        try:
            restored = bool(header.restoreState(state_bytes))
        finally:
            header.blockSignals(False)
            self._suppress_plan_table_layout_save = False
        return restored

    def _schedule_plan_table_layout_save(self, *_args) -> None:
        if self._suppress_plan_table_layout_save:
            return
        self._plan_table_layout_locked = True
        self._plan_table_layout_save_timer.start()

    def _restore_plan_table_column_widths(self, state: dict[str, object]) -> None:
        header_state = state.get("plan_table_header_state")
        if self._apply_plan_table_header_state(header_state):
            return
        widths = state.get("plan_table_column_widths")
        if isinstance(widths, list) and widths:
            self._apply_plan_table_column_widths(widths)
            return
        self._apply_plan_table_column_widths(self._default_experiment_control_table_column_widths())

    def _fit_plan_table_columns_to_viewport(self) -> None:
        self._run_gui_callback_timed("plan_table_fit", lambda: fit_plan_table_columns_to_viewport(self))

    def _update_plan_table_height(self) -> None:
        update_plan_table_height(self)

    def _experiment_control_editor_splitter_sizes(self) -> list[int]:
        splitter = getattr(self, "_flow_editor_splitter", None)
        if splitter is None:
            return []
        return [int(size) for size in splitter.sizes()]

    def _apply_experiment_control_editor_splitter_sizes(self, sizes: list[object] | None) -> None:
        splitter = getattr(self, "_flow_editor_splitter", None)
        if splitter is None or not isinstance(sizes, list) or len(sizes) < 2:
            return
        parsed_sizes: list[int] = []
        for value in sizes[:2]:
            try:
                parsed_sizes.append(max(int(value), 20))
            except (TypeError, ValueError):
                return
        self._suppress_plan_table_layout_save = True
        try:
            splitter.setSizes(parsed_sizes)
        finally:
            self._suppress_plan_table_layout_save = False
        self._flow_editor_splitter_initialized = True

    def _flow_editor_splitter_sizes(self) -> list[int]:
        return self._experiment_control_editor_splitter_sizes()

    def _apply_flow_editor_splitter_sizes(self, sizes: list[object] | None) -> None:
        self._apply_experiment_control_editor_splitter_sizes(sizes)

    def _on_flow_editor_splitter_moved(self, *_args) -> None:
        self._flow_editor_splitter_initialized = True
        self.save_ui_state()

    def _seconds_to_display(self, seconds: float) -> float:
        if self._time_unit_mode == "min":
            return float(seconds) / 60.0
        if self._time_unit_mode == "h":
            return float(seconds) / 3600.0
        return float(seconds)

    def _display_to_seconds(self, value: float) -> float:
        if self._time_unit_mode == "min":
            return float(value) * 60.0
        if self._time_unit_mode == "h":
            return float(value) * 3600.0
        return float(value)

    def _capture_editor_duration_from_spin(self, value: float) -> None:
        if self._suspend_duration_tracking:
            return
        self._editor_duration_seconds = max(self._display_to_seconds(value), 0.0)

    def _format_duration_for_status(self, seconds: float) -> str:
        seconds = max(float(seconds), 0.0)
        if self._time_unit_mode == "min":
            return f"{seconds / 60.0:.1f} min"
        if self._time_unit_mode == "h":
            return f"{seconds / 3600.0:.2f} h"
        return f"{int(round(seconds))} s"

    def _set_status_message(self, text: str) -> None:
        self._status_message_base = text
        self._refresh_status_line()

    def _show_error(self, message: str) -> None:
        self._set_status_message(message)
        _LOGGER.error("%s", message)

    def _begin_plan_table_edit(self, row: int, column: int) -> None:
        self._plan_table_active_editor = (int(row), int(column))

    def _end_plan_table_edit(self, row: int | None = None, column: int | None = None) -> None:
        active = self._plan_table_active_editor
        if active is None:
            return
        if row is None or column is None or active == (int(row), int(column)):
            self._plan_table_active_editor = None

    def _plan_table_is_editing(self, row: int, column: int) -> bool:
        active = self._plan_table_active_editor
        return active == (int(row), int(column))

    def _refresh_status_line(self) -> None:
        details: list[str] = []
        steps = self._read_experiment_control_steps()
        total_end_s = steps[-1].end_s if steps else 0.0
        if self._plan_running or self._plan_holding or self._plan_paused:
            active_row = self._plan_active_row
            if active_row is not None and 0 <= active_row < len(steps):
                step = steps[active_row]
                # step.end_s/total_end_s are plan-cumulative positions, but
                # _plan_elapsed_s is step-relative (it resets to 0 at every
                # step transition - see _advance_experiment_control_progress/
                # _jump_to_experiment_control_step). Subtracting it straight
                # from a cumulative value only happened to look right for
                # the first step (where cumulative and step-relative
                # coincide); from the second step on, "Plan left" barely
                # moved. step.start_s + _plan_elapsed_s is the plan-cumulative
                # elapsed position - the same combination
                # _timeline_progress_for_display already uses correctly.
                plan_elapsed_s = float(step.start_s) + max(float(self._plan_elapsed_s), 0.0)
                step_left_s = max(float(step.duration_s) - self._plan_elapsed_s, 0.0)
                plan_left_s = max(total_end_s - plan_elapsed_s, 0.0)
                details.append(f"Step left: {self._format_duration_for_status(step_left_s)}")
                details.append(f"Plan left: {self._format_duration_for_status(plan_left_s)}")
        elif steps:
            row = self._selected_experiment_control_row()
            if row is not None and 0 <= row < len(steps):
                step = steps[row]
                details.append(f"Step: {self._format_duration_for_status(step.duration_s)}")
            details.append(f"Plan: {self._format_duration_for_status(total_end_s)}")

        text = self._status_message_base
        if details:
            text = f"{text} | " + " | ".join(details)
        self.connection_status_label.setText(text)

    def _selected_step_start_s(self) -> float | None:
        row = self._selected_experiment_control_row()
        steps = self._read_experiment_control_steps()
        if row is None or not steps:
            return None
        if not (0 <= row < len(steps)):
            return None
        return float(steps[row].start_s)

    def set_theme(self, theme_mode: str) -> None:
        if theme_mode not in {"light", "dark"} or theme_mode == self._theme_mode:
            return
        self._theme_mode = theme_mode
        save_app_setting("theme_mode", self._theme_mode)
        self._apply_style()
        self._plan_model.set_theme_palette(self._theme_palette())
        self.previous_step_button.setIcon(transport_icon(self._theme_mode, "previous"))
        self.next_step_button.setIcon(transport_icon(self._theme_mode, "next"))
        self.stop_plan_button.setIcon(transport_icon(self._theme_mode, "stop"))
        self._update_experiment_control_toggle_button()
        self.timeline_widget.set_theme(self._theme_mode)
        self.timeline_widget.set_theme_palette(self._theme_palette())
        self.theme_changed.emit(self._theme_mode)

    def _default_color_palette_entries(self) -> list[tuple[str, str]]:
        return list(self.PLAN_COLOR_OPTIONS)

    def _normalize_color_entry(self, name: object, color: object, fallback_index: int) -> tuple[str, str] | None:
        label = str(name).strip() if isinstance(name, str) else ""
        color_text = str(color).strip().upper() if isinstance(color, str) else ""
        if not label:
            label = f"Custom {fallback_index + 1}"
        qcolor = QColor(color_text)
        if not qcolor.isValid():
            return None
        return label, qcolor.name().upper()

    def _load_color_palette_entries(self, state: dict[str, object]) -> list[tuple[str, str]]:
        payload = state.get("color_palette_entries")
        entries: list[tuple[str, str]] = []
        if isinstance(payload, list):
            for index, raw_entry in enumerate(payload):
                if isinstance(raw_entry, dict):
                    entry = self._normalize_color_entry(raw_entry.get("name"), raw_entry.get("color"), index)
                elif isinstance(raw_entry, (list, tuple)) and len(raw_entry) >= 2:
                    entry = self._normalize_color_entry(raw_entry[0], raw_entry[1], index)
                else:
                    entry = None
                if entry is not None:
                    entries.append(entry)
        if entries:
            return entries
        legacy_colors = state.get("custom_plan_colors", [])
        if isinstance(legacy_colors, list) and legacy_colors:
            entries.extend(self._default_color_palette_entries())
            for index, raw_color in enumerate(legacy_colors, start=1):
                if isinstance(raw_color, str) and raw_color.strip():
                    qcolor = QColor(raw_color.strip())
                    if qcolor.isValid():
                        entries.append((f"Custom {index}", qcolor.name().upper()))
            return entries
        return self._default_color_palette_entries()

    def _sync_custom_plan_colors_from_palette(self) -> None:
        self._custom_plan_colors = [color for _name, color in self._color_palette_entries]

    def _refresh_color_palette_widgets(self) -> None:
        current_editor_color = str(self.step_color_combo.currentData() or "")
        self._populate_color_combo(self.step_color_combo)
        if current_editor_color:
            editor_index = self.step_color_combo.findData(current_editor_color)
            if editor_index >= 0:
                self.step_color_combo.setCurrentIndex(editor_index)
        self._plan_model.set_color_options(self._color_palette_entries)
        self._plan_model.set_theme_palette(self._theme_palette())
        if hasattr(self, "timeline_widget"):
            self.timeline_widget.set_color_palette_entries(self._color_palette_entries)
            self.timeline_widget.update()
        self._update_timeline_selection()

    def _save_color_palette_entries(self) -> None:
        self._sync_custom_plan_colors_from_palette()
        self._refresh_color_palette_widgets()
        self.save_ui_state()

    def _populate_color_combo(self, combo: QComboBox) -> None:
        combo.clear()
        options = list(self._color_palette_entries or self._default_color_palette_entries())
        for index, (label, color) in enumerate(options):
            combo.addItem(label, color)
            combo.setItemData(index, QColor(color), Qt.ItemDataRole.BackgroundRole)
            combo.setItemData(index, QColor(self._contrast_text_color(color)), Qt.ItemDataRole.ForegroundRole)
            combo.setItemData(index, int(Qt.AlignmentFlag.AlignCenter), Qt.ItemDataRole.TextAlignmentRole)
        self._update_color_combo_style(combo)
        self._sync_custom_color_controls()

    def _contrast_text_color(self, color: str) -> str:
        qcolor = QColor(color)
        luminance = (
            0.299 * qcolor.red() + 0.587 * qcolor.green() + 0.114 * qcolor.blue()
        )
        return "#0f1720" if luminance > 150 else "#ffffff"

    def _update_color_combo_style(self, combo: QComboBox) -> None:
        color = combo.currentData()
        if not isinstance(color, str) or not color:
            combo.setStyleSheet("")
            return
        text_color = self._contrast_text_color(color)
        palette = combo.palette()
        qcolor = QColor(color)
        if qcolor.isValid():
            for role in (
                QPalette.ColorRole.Base,
                QPalette.ColorRole.Button,
                QPalette.ColorRole.Window,
            ):
                palette.setColor(role, qcolor)
        text_qcolor = QColor(text_color)
        for role in (
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
            QPalette.ColorRole.WindowText,
        ):
            palette.setColor(role, text_qcolor)
        combo.setPalette(palette)
        line_edit = combo.lineEdit()
        if line_edit is not None:
            line_edit.setPalette(palette)
            line_edit.setStyleSheet(
                "QLineEdit {"
                f" background-color: {color};"
                f" color: {text_color};"
                " border: none;"
                " border-radius: 10px;"
                " padding: 0px;"
                " margin: 0px;"
                "}"
            )
        combo.setStyleSheet(
            "QComboBox {"
            f" background: {color};"
            f" color: {text_color};"
            f" border: 1px solid {color};"
            " border-radius: 10px;"
            " padding: 0px 1px;"
            "}"
            "QComboBox QLineEdit {"
            f" background-color: {color};"
            f" color: {text_color};"
            " border: none;"
            " border-radius: 10px;"
            " padding: 0px;"
            " margin: 0px;"
            "}"
            "QComboBox QLineEdit:hover, QComboBox QLineEdit:focus {"
            f" background-color: {color};"
            f" color: {text_color};"
            " border: none;"
            " border-radius: 10px;"
            "}"
            "QComboBox::drop-down { border: none; width: 0px; }"
            "QComboBox::down-arrow { width: 0px; height: 0px; }"
            "QComboBox QAbstractItemView {"
            " selection-background-color: palette(highlight);"
            "}"
        )
        combo.update()

    def _handle_color_selection_changed(self) -> None:
        self._update_color_combo_style(self.step_color_combo)
        self._sync_custom_color_controls()

    def _sync_custom_color_controls(self) -> None:
        selected = self.step_color_combo.currentData()
        palette_colors = {color for _name, color in self._color_palette_entries}
        self.remove_custom_color_button.setEnabled(isinstance(selected, str) and selected in palette_colors)

    def _pick_custom_experiment_control_color(self) -> None:
        initial = QColor(str(self.step_color_combo.currentData() or "#4E79A7"))
        chosen = QColorDialog.getColor(initial, self, "Pick custom plan color")
        if not chosen.isValid():
            return
        color = chosen.name().upper()
        label = f"Custom {len(self._color_palette_entries) + 1}"
        self._color_palette_entries = [entry for entry in self._color_palette_entries if entry[1] != color]
        self._color_palette_entries.append((label, color))
        self._save_color_palette_entries()

    def _remove_selected_custom_color(self) -> None:
        selected = self.step_color_combo.currentData()
        if not isinstance(selected, str):
            return
        answer = QMessageBox.question(
            self,
            "Remove color",
            f"Remove selected color {selected} from the palette?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._color_palette_entries = [entry for entry in self._color_palette_entries if entry[1] != selected]
        if not self._color_palette_entries:
            self._color_palette_entries = self._default_color_palette_entries()
        self._save_color_palette_entries()
        fallback = self._color_palette_entries[0][1]
        index = self.step_color_combo.findData(fallback)
        if index >= 0:
            self.step_color_combo.setCurrentIndex(index)
        self._sync_custom_color_controls()

    def _default_experiment_control_color(self, step_index: int) -> str:
        palette = self._color_palette_entries or self._default_color_palette_entries()
        return palette[step_index % len(palette)][1]

    def _flow_rate_column(self, channel_index: int) -> int:
        return 4 + channel_index * 3

    def _direction_column(self, channel_index: int) -> int:
        return self._flow_rate_column(channel_index) + 1

    def _tube_column(self, channel_index: int) -> int:
        return self._flow_rate_column(channel_index) + 2

    def _valve_column(self) -> int:
        return 4 + ACTIVE_PUMP_CHANNELS * 3

    def _switch_column(self) -> int:
        return self._valve_column() + 1

    def _color_column(self) -> int:
        return self._valve_column() + 2

    def _description_column(self) -> int:
        return self._valve_column() + 3

    def _device_label_for(self, device_key: str) -> str:
        return device_label_for(device_key)

    def sync_from_lifecycle_controller(self) -> None:
        """Mirror DeviceLifecycleController's already-finished state into this
        panel's internal probe/client state, the shared status line, and the
        availability_changed/valve_availability_changed/mswitch_availability_changed
        signals MainWindow listens to.

        Pure UI/state sync - the controller has already discovered, connected, and
        (for the selector) homed every device by the time this is called; this
        method does no device I/O and never triggers a connect/disconnect of
        its own. Called after startup finishes and after a full shutdown, so
        it must correctly reflect *either* a connected or a not-connected
        state, not just paper over one of them.
        """
        controller = DeviceLifecycleController.shared()
        self._sync_pump_from_controller(controller)
        self._sync_valve_from_controller(controller)
        self._sync_mswitch_from_controller(controller)

    def _sync_pump_from_controller(self, controller: DeviceLifecycleController) -> None:
        # is_connected_cached(), not is_connected(): this can run while
        # device_io_pool is still mid-cycle (see sync_from_lifecycle_controller's
        # docstring and docs/device-layer/DEVICE_LAYER_AUDIT_2026.md, "UI
        # freezes during device initialization") - the live call would block
        # the GUI thread for as long as whatever's currently connecting.
        if controller.is_connected_cached(PUMP):
            probe = controller.probe_for(PUMP)
            port = str(getattr(probe, "port", "") or "")
            self._probe = probe
            model = getattr(probe, "model", "") or "pump"
            self._set_connection_visual(True, f"Connected to {model} on {port or 'unknown port'}.")
            self.availability_changed.emit(probe)
        else:
            self._probe = None
            event = controller.last_event(PUMP)
            message = event.message if event is not None and event.message else "Pump offline."
            self._set_connection_visual(False, message)
            self.availability_changed.emit(None)

    def _sync_valve_from_controller(self, controller: DeviceLifecycleController) -> None:
        if controller.is_connected_cached(SWITCH):
            probe = controller.probe_for(SWITCH)
            self._valve_probe = probe
            self.valve_availability_changed.emit(probe)
        else:
            self._valve_probe = None
            self.valve_availability_changed.emit(None)

    def _sync_mswitch_from_controller(self, controller: DeviceLifecycleController) -> None:
        if controller.is_connected_cached(SELECTOR):
            probe = controller.probe_for(SELECTOR)
            self._mswitch_probe = probe
            self.mswitch_availability_changed.emit(probe)
        else:
            self._mswitch_probe = None
            self.mswitch_availability_changed.emit(None)

    def _service_device_connected(self, device_key: str) -> bool:
        label = self._device_label_for(device_key)
        try:
            return bool(self._device_comm_service.is_connected(label))
        except Exception:
            connection = self._device_comm_service.connection(label)
            return bool(connection is not None and getattr(connection, "is_connected", lambda: False)())

    def _service_connection_detail(self, device_key: str) -> tuple[str | None, str | None]:
        label = self._device_label_for(device_key)
        connection = self._device_comm_service.connection(label)
        if connection is None:
            return None, None
        return (
            str(getattr(connection, "controller_type", None) or getattr(connection, "__class__", type(connection)).__name__ or ""),
            str(getattr(connection, "port", None) or ""),
        )

    def _send_device_command(self, device_key: str, command_type: str, payload: dict[str, object] | None = None) -> bool:
        label = self._device_label_for(device_key)
        result = self._device_comm_service.send_command(label, DeviceCommand(command_type, payload or {}))
        if result.success:
            return True
        _LOGGER.warning(
            "Device command failed | device=%s label=%s command=%s error=%s",
            device_key,
            label,
            command_type,
            result.error,
        )
        return False

    def refresh_device_ports(self) -> bool:
        """Trigger a background port rescan on the single-lane device I/O pool
        (ExperimentControlBackend.refresh_devices() protocol conformance; not
        currently wired to any UI trigger)."""
        device_io_pool().start(DevicePortRefreshTask())
        return True


    def _update_mswitch_state_from_probe(self) -> None:
        """Query the selector's live position after a switch-move plan-step
        command, so a failed/unexpected move is at least logged. Called from
        _apply_step_to_pump/_on_step_apply_async_done during real plan
        execution - not part of the dead manual-connect UI."""
        if not self._service_device_connected(SELECTOR):
            return
        try:
            self._device_comm_service.send_command(
                self._device_label_for(SELECTOR),
                DeviceCommand("switch.get_position", {}),
            )
        except Exception as exc:
            _LOGGER.warning("Could not refresh switch rotary valve state: %s", exc)

    def _set_connection_visual(self, connected: bool, text: str) -> None:
        """Pump connection message, surfaced through the panel's shared status
        line (see _set_status_message/_refresh_status_line). *connected* is
        unused now that this no longer drives a per-device indicator widget;
        kept in the signature since callers pass it for readability."""
        _ = connected
        self._status_message_base = text
        self._refresh_status_line()

    def _show_info(self, message: str) -> None:
        _LOGGER.info("%s", message)
        self._set_status_message(message)

    def _set_manual_uniform_mode(self, enabled: bool) -> None:
        self.manual_uniform_button.setText("=" if enabled else "≠")
        self.manual_uniform_button.setToolTip(
            "Shared direction and tube for all channels." if enabled
            else "Per-channel direction and tube settings are visible."
        )
        self._sync_detail_visibility()
        self._apply_shared_manual_settings()

    def _apply_shared_manual_settings(self, *_args) -> None:
        if not self.manual_uniform_button.isChecked():
            return
        direction = self._direction_button_value(self.shared_direction_button)
        tube_mm = self.shared_tube_spin.value()
        for button in self.manual_direction_buttons:
            self._set_direction_button(button, direction)
        for spin in self.manual_tube_spins:
            spin.blockSignals(True)
            spin.setValue(tube_mm)
            spin.blockSignals(False)

    def _sync_experiment_control_tube_columns(self) -> None:
        sync_experiment_control_tube_columns(self)

    def _set_experiment_control_details_visible(self, visible: bool) -> None:
        self._show_plan_details = visible
        self._sync_detail_visibility()
        self._refresh_experiment_control_view()
        self.save_ui_state()

    def _experiment_control_pause_row_visible(self) -> bool:
        return False

    def _set_experiment_control_pause_row_visible(self, visible: bool) -> None:
        _ = visible
        if hasattr(self, "pause_table"):
            self.pause_table.setVisible(False)

    def _experiment_control_table_row_offset(self) -> int:
        return 0

    def _table_row_from_plan_row(self, plan_row: int) -> int:
        return max(int(plan_row), 0)

    def _plan_row_from_table_row(self, table_row: int) -> int | None:
        if table_row < 0:
            return None
        return table_row

    def _selected_table_row(self) -> int | None:
        if self.plan_table.selectionMode() != QAbstractItemView.SelectionMode.NoSelection:
            selected_rows = sorted({index.row() for index in self.plan_table.selectedIndexes() if index.isValid()})
            if selected_rows:
                row = selected_rows[0]
                if 0 <= row < self.plan_table.rowCount():
                    return row
        row = self.plan_table.currentRow()
        if row < 0 or row >= self.plan_table.rowCount():
            return None
        return row

    def _selected_pause_row(self) -> bool:
        return False

    def _is_pause_flow_step(self, step: PumpPlanStep | None) -> bool:
        if step is None:
            return False
        if int(step.step) != 0:
            return False
        if abs(float(step.duration_s)) > 1e-9:
            return False
        if str(step.description or "").strip().casefold() != "pause":
            return False
        if str(step.valve or "").strip().casefold() != "open":
            return False
        if int(step.switch_position) != 1:
            return False
        return all(
            abs(float(channel.flow_ul_min)) <= 1e-9
            and str(channel.direction or "").strip().upper() in {"", "OFF", "CW"}
            for channel in step.channels[:ACTIVE_PUMP_CHANNELS]
        )

    def _strip_pause_flow_step(self, steps: list[PumpPlanStep]) -> list[PumpPlanStep]:
        if not steps:
            return []
        if self._is_pause_flow_step(steps[0]):
            return [deepcopy(step) for step in steps[1:]]
        return [deepcopy(step) for step in steps]

    def _serialize_experiment_control_pause_template(self) -> dict[str, object]:
        step = self._experiment_control_pause_template
        return {
            "duration_s": float(step.duration_s),
            "color": step.color,
            "valve": step.valve,
            "switch_position": int(step.switch_position),
            "description": step.description,
            "channels": [
                {"flow_ul_min": float(channel.flow_ul_min), "direction": channel.direction}
                for channel in step.channels[:ACTIVE_PUMP_CHANNELS]
            ],
        }

    def _deserialize_experiment_control_pause_template(self, payload: object) -> PumpPlanStep:
        if not isinstance(payload, dict):
            return PumpPlanStep(
                step=0,
                duration_s=0.0,
                color=self._default_experiment_control_color(0),
                valve="Open",
                switch_position=1,
                description="Pause",
                channels=[PumpChannelStep() for _ in range(ACTIVE_PUMP_CHANNELS)],
            )
        raw_channels = payload.get("channels", [])
        channels: list[PumpChannelStep] = []
        if isinstance(raw_channels, list):
            for raw_channel in raw_channels[:ACTIVE_PUMP_CHANNELS]:
                if isinstance(raw_channel, dict):
                    channels.append(
                        PumpChannelStep(
                            flow_ul_min=max(_safe_float(raw_channel.get("flow_ul_min", 0.0)), 0.0),
                            direction=str(raw_channel.get("direction", "OFF") or "OFF"),
                        )
                    )
        while len(channels) < ACTIVE_PUMP_CHANNELS:
            channels.append(PumpChannelStep())
        return PumpPlanStep(
            step=0,
            duration_s=max(_safe_float(payload.get("duration_s", 0.0)), 0.0),
            color=str(payload.get("color", self._default_experiment_control_color(0)) or self._default_experiment_control_color(0)),
            valve=str(payload.get("valve", "Open") or "Open"),
            switch_position=max(min(_safe_int(payload.get("switch_position", 1), 1), 12), 1),
            description=str(payload.get("description", "Pause") or "Pause"),
            channels=channels,
        )

    def _pause_row_step(self) -> PumpPlanStep:
        return deepcopy(self._experiment_control_pause_template)

    def _refresh_pause_row_view(self) -> None:
        return

    def _set_pause_table_item(self, row: int, column: int, text: str, editable: bool = True) -> None:
        item = self.pause_table.item(row, column)
        if item is None:
            item = QTableWidgetItem(text)
            self.pause_table.setItem(row, column, item)
        else:
            item.setText(text)
        flags = Qt.ItemFlag.ItemIsEnabled
        if editable:
            flags |= Qt.ItemFlag.ItemIsEditable
        item.setFlags(flags)

    def _set_pause_table_time_item(self, row: int, column: int, seconds: float, editable: bool) -> None:
        self._set_pause_table_item(row, column, self._format_seconds_display_value(seconds), editable=editable)

    def _select_experiment_control_plan_row(self, plan_row: int | None) -> None:
        if plan_row is None:
            return
        table_row = self._table_row_from_plan_row(plan_row)
        if 0 <= table_row < self.plan_table.rowCount():
            self.plan_table.selectRow(table_row)

    def _install_flow_navigation_filter(self, widget: QWidget | None) -> None:
        if widget is None:
            return
        widget.setProperty("flow_navigation", True)
        widget.installEventFilter(self)
        viewport = getattr(widget, "viewport", None)
        if callable(viewport):
            try:
                child_viewport = viewport()
            except Exception:
                child_viewport = None
            if child_viewport is not None:
                child_viewport.setProperty("flow_navigation", True)
                child_viewport.installEventFilter(self)
        line_edit = getattr(widget, "lineEdit", None)
        if callable(line_edit):
            try:
                child_line_edit = line_edit()
            except Exception:
                child_line_edit = None
            if child_line_edit is not None:
                child_line_edit.setProperty("flow_navigation", True)
                child_line_edit.installEventFilter(self)

    def _install_click_to_open_combo_filter(self, widget: QWidget | None) -> None:
        if widget is None:
            return
        widget.setProperty("open_popup_on_click", True)
        widget.installEventFilter(self)
        viewport = getattr(widget, "viewport", None)
        if callable(viewport):
            try:
                child_viewport = viewport()
            except Exception:
                child_viewport = None
            if child_viewport is not None:
                child_viewport.setProperty("open_popup_on_click", True)
                child_viewport.installEventFilter(self)
        line_edit = getattr(widget, "lineEdit", None)
        if callable(line_edit):
            try:
                child_line_edit = line_edit()
            except Exception:
                child_line_edit = None
            if child_line_edit is not None:
                child_line_edit.setProperty("open_popup_on_click", True)
                child_line_edit.installEventFilter(self)

    def _install_table_wheel_scroll_filter(self, widget: QWidget | None) -> None:
        if widget is None:
            return
        widget.setProperty("flow_wheel_scroll", True)
        widget.installEventFilter(self)
        viewport = getattr(widget, "viewport", None)
        if callable(viewport):
            try:
                child_viewport = viewport()
            except Exception:
                child_viewport = None
            if child_viewport is not None:
                child_viewport.setProperty("flow_wheel_scroll", True)
                child_viewport.installEventFilter(self)
        line_edit = getattr(widget, "lineEdit", None)
        if callable(line_edit):
            try:
                child_line_edit = line_edit()
            except Exception:
                child_line_edit = None
            if child_line_edit is not None and not isinstance(widget, QDoubleSpinBox):
                child_line_edit.setProperty("flow_wheel_scroll", True)
                child_line_edit.installEventFilter(self)

    def _combo_popup_target(self, widget: object) -> QComboBox | None:
        candidate = widget
        while isinstance(candidate, QWidget):
            if isinstance(candidate, QComboBox):
                return candidate
            candidate = candidate.parent()
        return None

    def _flow_table_cell_for_widget(self, widget: object) -> tuple[int, int] | None:
        candidate = widget
        while isinstance(candidate, QWidget):
            row_value = candidate.property("flow_row")
            column_value = candidate.property("flow_column")
            if isinstance(row_value, int) and isinstance(column_value, int):
                return row_value, column_value
            candidate = candidate.parent()
        return None

    def _wheel_event_index_for_plan_table(self, obj: object, event) -> QModelIndex | None:
        viewport = self.plan_table.viewport()
        if viewport is None:
            return None
        if obj is viewport:
            return self.plan_table.indexAt(event.position().toPoint())
        global_pos = getattr(event, "globalPosition", None)
        if callable(global_pos):
            return self.plan_table.indexAt(viewport.mapFromGlobal(global_pos().toPoint()))
        return QModelIndex()

    def _cycle_plan_table_cell_by_wheel(
        self,
        index: QModelIndex,
        wheel_delta: int,
        modifiers: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier,
    ) -> bool:
        if not index.isValid() or wheel_delta == 0:
            return False
        if index.row() != self.plan_table.currentRow():
            return False
        model = self.plan_table.model()
        if model is None:
            return False
        cycle_delta = 1 if wheel_delta > 0 else -1
        if index.column() == 1:
            step = 10.0
            if self._time_unit_mode == "min":
                step = 0.1
            elif self._time_unit_mode == "h":
                step = 0.1
            raw_value = index.data(Qt.ItemDataRole.EditRole)
            try:
                value = float(raw_value) if raw_value is not None else 0.0
            except (TypeError, ValueError):
                value = 0.0
            return bool(model.setData(index, max(value + (cycle_delta * step), 0.0), Qt.ItemDataRole.EditRole))
        flow_start = 4
        channel_count = ACTIVE_PUMP_CHANNELS
        if flow_start <= index.column() < flow_start + channel_count * 3:
            offset = index.column() - flow_start
            channel_index = offset // 3
            field = offset % 3
            if field == 0:
                # Default step of 5 for quick adjustments; hold Ctrl while
                # scrolling for the finer, single-unit step.
                fine = bool(modifiers & Qt.KeyboardModifier.ControlModifier)
                step = 1 if fine else 5
                raw_value = index.data(Qt.ItemDataRole.EditRole)
                try:
                    value = int(float(raw_value)) if raw_value is not None else 0
                except (TypeError, ValueError):
                    value = 0
                return bool(model.setData(index, max(value + (cycle_delta * step), 0), Qt.ItemDataRole.EditRole))
            if field == 1:
                current = str(index.data(Qt.ItemDataRole.EditRole) or "CW").strip().upper()
                next_value = "CW" if current == "CCW" else "CCW"
                return bool(model.setData(index, next_value, Qt.ItemDataRole.EditRole))
            if field == 2:
                # Tube diameter is a shared per-channel setting (the same
                # manual_tube_spins value backs every row for this channel,
                # not a per-step value like flow/direction), so scrolling
                # over any row's tube cell adjusts it for every row on that
                # channel at once - matching how editing it via the manual
                # control spinbox already behaves.
                if 0 <= channel_index < len(self.manual_tube_spins):
                    spin = self.manual_tube_spins[channel_index]
                    spin.setValue(spin.value() + (cycle_delta * spin.singleStep()))
                    return True
                return False
        if index.column() == self._switch_column() and hasattr(model, "cycle_switch"):
            handled = bool(model.cycle_switch(index.row(), cycle_delta))
        elif index.column() == self._color_column() and hasattr(model, "cycle_color"):
            handled = bool(model.cycle_color(index.row(), cycle_delta))
        else:
            handled = False
        if handled:
            self.plan_table.viewport().update()
        return handled

    def _experiment_control_index_from_mouse_event(self, obj: object, event) -> QModelIndex | None:
        model = self.plan_table.model()
        if model is None:
            return None
        viewport = self.plan_table.viewport()
        if viewport is None:
            return None
        index = QModelIndex()
        if obj is viewport:
            index = self.plan_table.indexAt(event.position().toPoint())
        else:
            global_pos = getattr(event, "globalPosition", None)
            if callable(global_pos):
                index = self.plan_table.indexAt(viewport.mapFromGlobal(global_pos().toPoint()))
        if index.isValid():
            return index
        cell = self._flow_table_cell_for_widget(obj)
        if cell is None:
            return None
        row, column = cell
        if 0 <= row < model.rowCount() and 0 <= column < model.columnCount():
            candidate = model.index(row, column)
            if candidate.isValid():
                return candidate
        return None

    def _experiment_control_column_kind(self, column: int) -> str | None:
        if column == 0:
            return "step"
        if column == 1:
            return "duration"
        if column in {2, 3}:
            return "time"
        for channel_index in range(ACTIVE_PUMP_CHANNELS):
            if column == self._flow_rate_column(channel_index):
                return "flow"
            if column == self._direction_column(channel_index):
                return "direction"
            if column == self._tube_column(channel_index):
                return "tube"
        if column == self._valve_column():
            return "valve"
        if column == self._switch_column():
            return "switch"
        if column == self._color_column():
            return "color"
        if column == self._description_column():
            return "comment"
        return None

    def _experiment_control_channel_field(self, column: int) -> tuple[int, int] | None:
        """Return ``(channel_index, field)`` for a flow/direction/tube column, else
        ``None``. ``field`` is 0 for flow, 1 for direction, 2 for tube diameter -
        matching the column layout ``_flow_rate_column``/``_direction_column``/
        ``_tube_column`` build (three columns per channel, flow first)."""
        flow_start = self._flow_rate_column(0)
        if flow_start <= column < flow_start + ACTIVE_PUMP_CHANNELS * 3:
            offset = column - flow_start
            return offset // 3, offset % 3
        return None

    def _experiment_control_read_cell_value(self, row: int, column: int) -> object:
        """Read the raw value backing a plan-table cell, for the copy side of
        cell copy/paste (see ExperimentControlEditingController.copy_selection).
        Mirrors ExperimentPlanTableModel.data()'s EditRole values so a copied
        value round-trips identically to what editing the cell directly would
        produce."""
        steps = self._read_experiment_control_steps()
        if row < 0 or row >= len(steps):
            return None
        step = steps[row]
        kind = self._experiment_control_column_kind(column)
        if kind == "duration":
            return self._seconds_to_display(step.duration_s)
        if kind == "time":
            seconds = step.start_s if column == 2 else step.end_s
            return self._seconds_to_display(seconds)
        channel_field = self._experiment_control_channel_field(column)
        if channel_field is not None:
            channel_index, field = channel_field
            if channel_index >= len(step.channels):
                return None
            channel = step.channels[channel_index]
            if field == 0:
                return float(channel.flow_ul_min)
            if field == 1:
                return "CCW" if str(channel.direction or "").upper() == "CCW" else "CW"
            tube_values = self._tube_mm_values()
            return float(tube_values[channel_index]) if channel_index < len(tube_values) else 0.25
        if kind == "valve":
            return "Close" if str(step.valve or "").strip().lower() == "close" else "Open"
        if kind == "switch":
            return int(step.switch_position)
        if kind == "color":
            return str(step.color or "#4E79A7")
        if kind == "comment":
            return str(step.description or "")
        return None

    def _experiment_control_value_to_text(self, kind: str | None, value: object) -> str:
        """Render a copied cell value as plain text for the clipboard's
        tab-separated representation (so a copied block can also be pasted into
        an external spreadsheet, not just back into this app)."""
        if value is None:
            return ""
        if kind in {"duration", "time", "flow", "tube"}:
            try:
                return f"{float(value):g}"
            except (TypeError, ValueError):
                return str(value)
        if kind == "switch":
            try:
                return str(int(value))
            except (TypeError, ValueError):
                return str(value)
        return str(value)

    def _experiment_control_write_row_value(self, steps: list[PumpPlanStep], row: int, column: int, value: object) -> bool:
        """Write a copied value into ``steps[row]`` at ``column``, for the paste
        side of cell copy/paste. Mutates the given step in place and returns
        whether the write applied; callers (ExperimentControlEditingController.
        paste_selection) treat ``False`` as "incompatible cell, skip it" -
        matches the parsing/clamping rules ExperimentPlanTableModel.setData()
        uses for the same columns, so pasting behaves like typing the same
        value directly into the cell."""
        if row < 0 or row >= len(steps):
            return False
        step = steps[row]
        kind = self._experiment_control_column_kind(column)
        if kind == "duration":
            try:
                step.duration_s = max(self._display_to_seconds(float(value)), 0.0)
            except (TypeError, ValueError):
                return False
            return True
        channel_field = self._experiment_control_channel_field(column)
        if channel_field is not None:
            channel_index, field = channel_field
            if channel_index >= len(step.channels):
                return False
            if field == 0:
                try:
                    step.channels[channel_index].flow_ul_min = max(round(float(value)), 0)
                except (TypeError, ValueError):
                    return False
                return True
            if field == 1:
                step.channels[channel_index].direction = "CCW" if str(value).upper() == "CCW" else "CW"
                return True
            # field == 2 (tube diameter) isn't a per-step value - it's a single
            # shared setting per channel (the manual_tube_spins row, applied to
            # every step), so there is nothing on this step to write into.
            # Copy-only, same as the Step# and time columns below.
            return False
        if kind == "valve":
            step.valve = "Close" if str(value).strip().lower() == "close" else "Open"
            return True
        if kind == "switch":
            try:
                step.switch_position = max(min(int(value), 12), 1)
            except (TypeError, ValueError):
                step.switch_position = 1
            return True
        if kind == "color":
            color = QColor(str(value or "").strip())
            step.color = color.name().upper() if color.isValid() else "#4E79A7"
            return True
        if kind == "comment":
            step.description = str(value or "").strip()
            return True
        return False

    def _update_experiment_control_edit_mode_button(self) -> None:
        controller = getattr(self, "_experiment_control_edit_controller", None)
        if controller is not None:
            self._experiment_control_edit_mode = bool(controller.edit_mode)
        self.apply_step_button.setChecked(self._experiment_control_edit_mode)
        self._set_experiment_control_edit_mode_button_icon(self._experiment_control_edit_mode)
        self.apply_step_button.setToolTip(
            "Table edit mode is active. Copy, paste, and multi-selection are enabled."
            if self._experiment_control_edit_mode
            else "Enable table edit mode for multi-cell selection, copy/paste, and row moves."
        )

    def _set_experiment_control_edit_mode_button_icon(self, active: bool) -> None:
        color = QColor("#ffd84d" if active else "#8a98a8")
        self.apply_step_button.setIcon(tint_tabler_icon(flow_tabler_icon("edit"), color))


    def _widget_or_ancestor_has_focus(self, widget: QWidget, types: tuple[type, ...]) -> bool:
        candidate: QWidget | None = widget
        while isinstance(candidate, QWidget):
            if isinstance(candidate, types) and candidate.hasFocus():
                return True
            candidate = candidate.parent() if isinstance(candidate.parent(), QWidget) else None
        return False

    def _scroll_plan_table_by_wheel(self, wheel_delta: int) -> bool:
        if wheel_delta == 0:
            return False
        scrollbar = self.plan_table.verticalScrollBar()
        step = scrollbar.singleStep() or max(self.plan_table.rowHeight(max(self.plan_table.currentRow(), 0)), 1)
        scrollbar.setValue(scrollbar.value() - int(step * wheel_delta / 120))
        return True

    def _normalize_experiment_control_view_mode(self, mode: object) -> str:
        normalized = str(mode or "").strip().lower().replace("-", "_").replace(" ", "_")
        if normalized in {"full", "table_timeline", "timeline"}:
            return normalized
        if normalized in {"tabletimeline", "tableplus_timeline", "table_plus_timeline"}:
            return "table_timeline"
        return "full"

    def _load_experiment_control_view_mode_sizes(self, state: dict[str, object]) -> dict[str, list[int]]:
        payload = state.get("experiment_control_view_mode_sizes")
        sizes: dict[str, list[int]] = {}
        if not isinstance(payload, dict):
            return sizes
        for raw_mode in ("full", "table_timeline", "timeline"):
            raw_sizes = payload.get(raw_mode)
            if not isinstance(raw_sizes, list):
                continue
            parsed = [int(value) for value in raw_sizes if isinstance(value, int) and int(value) >= 0]
            if len(parsed) == 2:
                sizes[raw_mode] = parsed
        return sizes

    def _load_experiment_control_view_mode_panel_sizes(self, state: dict[str, object]) -> dict[str, list[int]]:
        payload = state.get("experiment_control_view_mode_panel_sizes")
        sizes: dict[str, list[int]] = {}
        if not isinstance(payload, dict):
            return sizes
        for raw_mode in ("full", "table_timeline", "timeline"):
            raw_sizes = payload.get(raw_mode)
            if not isinstance(raw_sizes, list):
                continue
            parsed = [int(value) for value in raw_sizes if isinstance(value, int) and int(value) >= 0]
            if len(parsed) == 2:
                sizes[raw_mode] = parsed
        return sizes

    def _experiment_control_current_splitter_sizes(self) -> list[int]:
        splitter = getattr(self, "_flow_editor_splitter", None)
        if splitter is None:
            return []
        return [max(int(size), 0) for size in splitter.sizes()]

    def _experiment_control_parent_splitter(self):
        window = self.window()
        splitter = getattr(window, "plot_splitter", None)
        if splitter is None or splitter.count() < 2:
            return None
        return splitter

    def _experiment_control_current_parent_splitter_sizes(self) -> list[int]:
        splitter = self._experiment_control_parent_splitter()
        if splitter is None:
            return []
        return [max(int(size), 0) for size in splitter.sizes()]

    def _experiment_control_min_table_splitter_height(self) -> int:
        header = self.plan_table.horizontalHeader()
        table = self.plan_table
        rows = max(table.rowCount(), 0)
        header_height = int(header.sizeHint().height())
        if rows <= 0:
            return max(header_height + 36, 96)
        visible_rows = min(rows, 2)
        content_height = 0
        for row in range(visible_rows):
            content_height += max(int(table.rowHeight(row)), int(table.sizeHintForRow(row)), 24)
        return max(header_height + content_height + 8, header_height + 48)

    def _experiment_control_default_splitter_sizes(self, mode: str) -> list[int]:
        timeline_height = max(self.timeline_widget.minimumHeight(), self.timeline_widget.sizeHint().height(), 64)
        if mode == "timeline":
            return [0, timeline_height]
        min_table_height = self._experiment_control_min_table_splitter_height()
        if mode == "table_timeline":
            return [min_table_height, timeline_height]
        return [max(min_table_height, timeline_height * 2), timeline_height]

    def _experiment_control_target_panel_height(self, mode: str | None = None) -> int:
        normalized = self._normalize_experiment_control_view_mode(mode or self._experiment_control_view_mode)
        header_height = 0
        header_row = getattr(self, "_experiment_control_header_row", None)
        if header_row is not None:
            header_height = max(int(header_row.sizeHint().height()), int(header_row.minimumHeight()), 20)
        matrix_height = 0
        if normalized == "full" and hasattr(self, "_experiment_control_matrix_widget"):
            matrix_height = max(int(self._experiment_control_matrix_widget.sizeHint().height()), 0)
        action_height = 0
        if normalized == "full" and hasattr(self, "_experiment_control_editor_action_row"):
            action_height = max(int(self._experiment_control_editor_action_row.sizeHint().height()), 0)
        content_spacing = 4 if normalized == "full" else 2
        outer_margins = 16
        if normalized == "timeline":
            flow_action_height = 0
            if hasattr(self, "_experiment_control_flow_action_row"):
                flow_action_height = max(int(self._experiment_control_flow_action_row.sizeHint().height()), 0)
            timeline_height = max(self.timeline_widget.minimumHeight(), self.timeline_widget.sizeHint().height(), 64)
            splitter_handle = max(int(getattr(self._flow_editor_splitter, "handleWidth", lambda: 6)()), 6)
            return max(header_height + timeline_height + flow_action_height + splitter_handle + content_spacing + outer_margins, 140)
        timeline_controls_height = 0
        timeline_controls_widget = getattr(self, "_experiment_control_timeline_controls_widget", None)
        if timeline_controls_widget is not None:
            timeline_controls_height = max(int(timeline_controls_widget.sizeHint().height()), 0)
        return max(header_height + matrix_height + action_height + timeline_controls_height + content_spacing + outer_margins, 180)

    def _experiment_control_default_parent_splitter_sizes(self, mode: str) -> list[int]:
        splitter = self._experiment_control_parent_splitter()
        if splitter is None:
            return []
        sizes = self._experiment_control_current_parent_splitter_sizes()
        if len(sizes) != 2:
            sizes = [0, 0]
        total = sum(sizes)
        if total <= 0:
            total = max(int(splitter.height()), int(splitter.sizeHint().height()), 1)
        target_top = self._experiment_control_target_panel_height(mode)
        bottom_min = max(180, int(splitter.widget(1).minimumHeight()) if splitter.widget(1) is not None else 180)
        top = min(max(target_top, 120), max(total - bottom_min, 120))
        bottom = max(total - top, bottom_min)
        return [top, bottom]

    def _remember_experiment_control_view_mode_sizes(self, mode: str | None = None) -> None:
        splitter_sizes = self._experiment_control_current_splitter_sizes()
        if len(splitter_sizes) != 2:
            return
        normalized = self._normalize_experiment_control_view_mode(mode or self._experiment_control_view_mode)
        self._experiment_control_view_mode_sizes[normalized] = splitter_sizes

    def _remember_experiment_control_view_mode_panel_sizes(self, mode: str | None = None) -> None:
        splitter_sizes = self._experiment_control_current_parent_splitter_sizes()
        if len(splitter_sizes) != 2:
            return
        normalized = self._normalize_experiment_control_view_mode(mode or self._experiment_control_view_mode)
        self._experiment_control_view_mode_panel_sizes[normalized] = splitter_sizes

    def _apply_experiment_control_parent_splitter_sizes(self, mode: str | None = None) -> None:
        from lspr_app.gui.main_window_state import ensure_visible_top_content_splitter, normalize_top_content_mode

        splitter = self._experiment_control_parent_splitter()
        if splitter is None:
            return
        normalized = self._normalize_experiment_control_view_mode(mode or self._experiment_control_view_mode)
        window = self.window()
        if normalize_top_content_mode(getattr(window, "_top_view_mode", "spectra")) != "experimental_control":
            return
        stack = getattr(window, "_top_content_stack", None)
        if stack is None or stack.currentWidget() is not self:
            return
        cached_sizes = self._experiment_control_view_mode_panel_sizes.get(normalized)
        if cached_sizes is None:
            cached_sizes = self._experiment_control_default_parent_splitter_sizes(normalized)
        if len(cached_sizes) != 2:
            return
        top = max(int(cached_sizes[0]), 120)
        bottom = max(int(cached_sizes[1]), 180)
        splitter.setSizes([top, bottom])
        ensure_visible_top_content_splitter(window, mode=normalized)

    def _persist_experiment_control_view_mode_layout(self) -> None:
        self._apply_experiment_control_parent_splitter_sizes()
        self.save_ui_state()

    def _experiment_control_view_mode_label(self, mode: str | None = None) -> str:
        normalized = self._normalize_experiment_control_view_mode(mode or self._experiment_control_view_mode)
        return {
            "full": "Full",
            "table_timeline": "Table+Timeline",
            "timeline": "Timeline",
        }[normalized]

    def _experiment_control_view_mode_tooltip(self, mode: str | None = None) -> str:
        normalized = self._normalize_experiment_control_view_mode(mode or self._experiment_control_view_mode)
        current = self._experiment_control_view_mode_label(normalized)
        return (
            f"Current view: {current}. Click to cycle between Full, Table+Timeline, and Timeline."
            if normalized == "full"
            else f"Current view: {current}. Click to cycle to the next visibility mode."
        )

    def _update_experiment_control_view_mode_button(self) -> None:
        if not hasattr(self, "_experiment_control_view_mode_button"):
            return
        label = self._experiment_control_view_mode_label()
        self._experiment_control_view_mode_button.setText(f"[{label}]")
        self._experiment_control_view_mode_button.setToolTip(self._experiment_control_view_mode_tooltip())

    def _normalize_experiment_control_timeline_label_mode(self, mode: object) -> str:
        normalized = str(mode or "").strip().lower()
        return normalized if normalized in {"comment", "color_name"} else "comment"

    def _experiment_control_timeline_label_mode_label(self, mode: str | None = None) -> str:
        normalized = self._normalize_experiment_control_timeline_label_mode(mode or self._experiment_control_timeline_label_mode)
        return {
            "comment": "Comment",
            "color_name": "ColorName",
        }[normalized]

    def _update_experiment_control_timeline_label_mode(self) -> None:
        if not hasattr(self, "timeline_widget"):
            return
        self.timeline_widget.set_label_mode(self._experiment_control_timeline_label_mode)
        self.timeline_widget.set_color_palette_entries(self._color_palette_entries)
        self.timeline_widget.update()

    def _cycle_experiment_control_timeline_label_mode(self) -> None:
        current = self._normalize_experiment_control_timeline_label_mode(self._experiment_control_timeline_label_mode)
        self._experiment_control_timeline_label_mode = "color_name" if current == "comment" else "comment"
        self._update_experiment_control_timeline_label_mode()
        self.save_ui_state()
        # The sensorgram overlay re-resolves its labels from this same mode
        # on every sync (see _refresh_sensorgram_control_step_event_labels
        # in main_window_sensorgram_overlay.py), but syncs happen on a
        # tick/view-range cadence - trigger one immediately so the overlay
        # updates the moment the toggle is clicked, matching how the
        # timeline widget itself repaints instantly.
        recording_controller = getattr(self, "recording_controller", None)
        sync_overlay = getattr(recording_controller, "_sync_sensorgram_control_step_overlay", None)
        if callable(sync_overlay):
            try:
                sync_overlay()
            except Exception:
                pass

    def _apply_experiment_control_view_mode(self, *, save: bool = False) -> None:
        from lspr_app.gui.main_window_state import ensure_visible_top_content_splitter

        mode = self._normalize_experiment_control_view_mode(self._experiment_control_view_mode)
        self._experiment_control_view_mode = mode
        show_matrix = mode == "full"
        show_table = mode != "timeline"
        if hasattr(self, "_experiment_control_matrix_widget"):
            self._experiment_control_matrix_widget.setVisible(show_matrix)
        if hasattr(self, "_experiment_control_editor_action_row"):
            self._experiment_control_editor_action_row.setVisible(show_matrix)
        if hasattr(self, "_experiment_control_table_container"):
            self._experiment_control_table_container.setVisible(show_table)
        if hasattr(self, "_flow_editor_splitter"):
            if show_table:
                cached_sizes = self._experiment_control_view_mode_sizes.get(mode)
                if cached_sizes is None and mode == "table_timeline":
                    cached_sizes = self._experiment_control_view_mode_sizes.get("full")
                if cached_sizes is None:
                    cached_sizes = self._experiment_control_default_splitter_sizes(mode)
                if len(cached_sizes) == 2 and cached_sizes[0] > 0:
                    cached_sizes = [
                        max(int(cached_sizes[0]), self._experiment_control_min_table_splitter_height()),
                        max(int(cached_sizes[1]), max(self.timeline_widget.minimumHeight(), self.timeline_widget.sizeHint().height(), 64)),
                    ]
                    self._flow_editor_splitter.setSizes(cached_sizes)
                self._flow_editor_splitter.setStretchFactor(0, 1)
                self._flow_editor_splitter.setStretchFactor(1, 0)
            else:
                cached_sizes = self._experiment_control_view_mode_sizes.get("timeline")
                if cached_sizes is None:
                    cached_sizes = self._experiment_control_default_splitter_sizes("timeline")
                self._flow_editor_splitter.setSizes(cached_sizes)
        self._update_experiment_control_view_mode_button()
        if show_table:
            self._fit_plan_table_columns_to_viewport()
            self._update_plan_table_height()
        self._sync_detail_visibility()
        self._experiment_control_edit_controller.sync_overlay()
        self.updateGeometry()
        if self.parentWidget() is not None:
            self.parentWidget().updateGeometry()
        ensure_visible_top_content_splitter(self.window(), mode=getattr(self.window(), "_top_view_mode", "spectra"))
        if save:
            QTimer.singleShot(0, self._persist_experiment_control_view_mode_layout)
        else:
            QTimer.singleShot(0, self._apply_experiment_control_parent_splitter_sizes)

    def _cycle_experiment_control_view_mode(self) -> None:
        order = ["full", "table_timeline", "timeline"]
        current = self._normalize_experiment_control_view_mode(self._experiment_control_view_mode)
        self._remember_experiment_control_view_mode_sizes(current)
        self._remember_experiment_control_view_mode_panel_sizes(current)
        next_mode = order[(order.index(current) + 1) % len(order)]
        self._experiment_control_view_mode = next_mode
        self._apply_experiment_control_view_mode(save=True)

    def _apply_pending_experiment_control_view_mode(self) -> None:
        if not self._experiment_control_view_mode_apply_pending:
            return
        self._experiment_control_view_mode_apply_pending = False
        self._apply_experiment_control_view_mode()

    def _sync_detail_visibility(self) -> None:
        show_per_channel_editor = not self.manual_uniform_button.isChecked()
        self.manual_dir_label.setVisible(show_per_channel_editor)
        self.manual_tube_label.setVisible(show_per_channel_editor)
        self.shared_direction_row.setVisible(self.manual_uniform_button.isChecked())
        self.shared_tube_row.setVisible(self.manual_uniform_button.isChecked())
        for button in self.manual_direction_buttons:
            button.setVisible(show_per_channel_editor)
        for spin in self.manual_tube_spins:
            spin.setVisible(show_per_channel_editor)
        self._update_plan_detail_toggle_icon()
        if not hasattr(self, "_experiment_control_table_container") or self._experiment_control_table_container.isVisible():
            self._configure_experiment_control_table_columns()
            self._fit_plan_table_columns_to_viewport()

    def _update_plan_detail_toggle_icon(self) -> None:
        update_plan_detail_toggle_icon(self)

    def _populate_experiment_control_table(self, steps: list[PumpPlanStep], selected_row: int | None = None) -> None:
        if selected_row is None:
            selected_row = self._selected_experiment_control_row()
        recomputed_steps = recompute_plan_timing(self._strip_pause_flow_step(steps))
        self._updating_table = True
        try:
            self._plan_model.set_steps(recomputed_steps)
            self._plan_model.set_theme_palette(self._theme_palette())
            self._plan_model.set_time_unit_mode(self._time_unit_mode)
            self._plan_model.set_tube_mm_by_channel([spin.value() for spin in self.manual_tube_spins])
            self._plan_model.set_switch_solution_labels(self._switch_solution_labels)
            self._plan_model.set_color_options(self._color_palette_entries)
            self._plan_model.set_valve_state_labels(self._valve_state_labels)
            self._plan_model.set_valve_state_colors(self._valve_state_colors)
        finally:
            self._updating_table = False
        self._experiment_control_steps_cache = deepcopy(recomputed_steps)
        self.timeline_widget.set_steps(
            recomputed_steps,
            self._experiment_control_timeline_row(),
            self._timeline_progress_for_display(),
            self._plan_runtime_for_display(),
        )
        if recomputed_steps:
            row_to_select = 0 if selected_row is None else min(max(selected_row, 0), len(recomputed_steps) - 1)
            self._select_experiment_control_plan_row(row_to_select)
        else:
            self.plan_table.clearSelection()
        self._fit_plan_table_columns_to_viewport()
        self._update_plan_table_height()

    def _read_experiment_control_steps(self) -> list[PumpPlanStep]:
        if self._experiment_control_bootstrap_pending_steps:
            return recompute_plan_timing(self._strip_pause_flow_step(self._experiment_control_bootstrap_pending_steps))
        if self._experiment_plan_import_pending_steps:
            return recompute_plan_timing(self._strip_pause_flow_step(self._experiment_plan_import_pending_steps))
        steps = self._plan_model.steps()
        if steps:
            return recompute_plan_timing(self._strip_pause_flow_step(steps))
        return recompute_plan_timing([])

    def _step_from_experiment_control_row(self, row: int) -> PumpPlanStep | None:
        if row < 0 or row >= self.plan_table.rowCount():
            return None
        return self._plan_model.step_at(row)

    def _update_experiment_control_steps_cache_from_row(self, row: int) -> None:
        step = self._step_from_experiment_control_row(row)
        if step is None:
            return
        row_count = max(self.plan_table.rowCount(), 0)
        if len(self._experiment_control_steps_cache) != row_count:
            self._experiment_control_steps_cache = recompute_plan_timing(self._plan_model.steps())
            return
        cache = list(self._experiment_control_steps_cache)
        plan_row = self._plan_row_from_table_row(row)
        if plan_row is None or plan_row >= len(cache):
            return
        cache[plan_row] = deepcopy(step)
        self._experiment_control_steps_cache = recompute_plan_timing(cache)

    def _sync_experiment_control_table_derived_columns(self, steps: list[PumpPlanStep]) -> None:
        _ = steps
        self._plan_model.set_theme_palette(self._theme_palette())
        self._plan_model.set_time_unit_mode(self._time_unit_mode)
        self._plan_model.set_tube_mm_by_channel([spin.value() for spin in self.manual_tube_spins])
        self._plan_model.set_switch_solution_labels(self._switch_solution_labels)
        self._plan_model.set_color_options(self._color_palette_entries)
        self._plan_model.set_valve_state_labels(self._valve_state_labels)
        self._plan_model.set_valve_state_colors(self._valve_state_colors)

    def _refresh_experiment_control_view(self) -> None:
        selected_row = self._selected_experiment_control_row()
        steps = self._read_experiment_control_steps()
        self._set_experiment_control_pause_row_visible(self._experiment_control_pause_row_visible())
        if self._experiment_control_bootstrap_in_progress or self._experiment_plan_import_in_progress:
            self._experiment_control_steps_cache = deepcopy(steps)
            if steps:
                self._schedule_visible_experiment_control_rows_load()
            return
        expected_rows = len(steps)
        if self.plan_table.rowCount() != expected_rows:
            self._populate_experiment_control_table(steps, selected_row=selected_row)
            return
        self._experiment_control_steps_cache = deepcopy(steps)
        self._updating_table = True
        try:
            self._plan_model.set_steps(steps)
            self._sync_experiment_control_table_derived_columns(steps)
        finally:
            self._updating_table = False
        if selected_row is not None and 0 <= selected_row < self.plan_table.rowCount():
            self._select_experiment_control_plan_row(selected_row)
        self._update_timeline_selection()
        self._fit_plan_table_columns_to_viewport()
        self._update_plan_table_height()

    def _handle_experiment_control_model_changed(self, *args) -> None:
        if self._updating_table:
            return
        previous_steps = deepcopy(self._experiment_control_steps_cache)
        self._experiment_control_steps_cache = self._plan_model.steps()
        self._update_timeline_selection()
        if (
            self._plan_running
            and len(args) >= 2
            and isinstance(args[0], QModelIndex)
            and isinstance(args[1], QModelIndex)
            and self._plan_active_row is not None
        ):
            top_left = args[0]
            bottom_right = args[1]
            if top_left.row() <= self._plan_active_row <= bottom_right.row():
                updated_step = self._plan_model.step_at(self._plan_active_row)
                previous_step = previous_steps[self._plan_active_row] if self._plan_active_row < len(previous_steps) else None
                change_summary = self._experiment_control_step_change_summary(previous_step, updated_step)
                if updated_step is not None:
                    if change_summary:
                        # Only announce "step edited" once the device has
                        # actually received the change - announcing it
                        # immediately (before the async dispatch completes)
                        # would be a visible lie if the command failed.
                        self._apply_step_to_pump_async(
                            updated_step,
                            start=True,
                            on_success=lambda step=updated_step, summary=change_summary: self._emit_experimental_control_state(
                                "step_edited", step, status=summary
                            ),
                        )
                    else:
                        self._apply_step_to_pump_async(updated_step, start=True)
        self.save_ui_state()

    def _handle_experiment_control_table_change(self, *_args) -> None:
        self._handle_experiment_control_model_changed()

    def eventFilter(self, obj, event):  # pragma: no cover - GUI runtime path
        if hasattr(self, "_experiment_control_edit_controller") and self._experiment_control_edit_controller.event_filter(obj, event):
            return True
        if event.type() == QEvent.Type.Wheel and getattr(obj, "property", None) is not None:
            if bool(obj.property("flow_wheel_scroll")):
                if isinstance(obj, QDoubleSpinBox):
                    if obj.hasFocus():
                        return False
                    return self._scroll_plan_table_by_wheel(event.angleDelta().y())
                if isinstance(obj, QComboBox):
                    if obj.hasFocus():
                        return False
                    return self._scroll_plan_table_by_wheel(event.angleDelta().y())
                if isinstance(obj, QLineEdit):
                    if self._widget_or_ancestor_has_focus(obj, (QDoubleSpinBox, QComboBox, QLineEdit)):
                        return False
                    return self._scroll_plan_table_by_wheel(event.angleDelta().y())
        if event.type() == QEvent.Type.Wheel and obj in (self.plan_table, self.plan_table.viewport()):
            index = self._wheel_event_index_for_plan_table(obj, event)
            if self._cycle_plan_table_cell_by_wheel(index, event.angleDelta().y(), event.modifiers()):
                event.accept()
                return True
        if event.type() == QEvent.Type.MouseButtonPress and getattr(obj, "property", None) is not None:
            if bool(obj.property("open_popup_on_click")):
                combo = self._combo_popup_target(obj)
                if combo is not None and getattr(self, "_ui_startup_ready", False):
                    QTimer.singleShot(0, combo.showPopup)
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.FocusIn) and getattr(obj, "property", None) is not None:
            if bool(obj.property("flow_navigation")):
                cell = self._flow_table_cell_for_widget(obj)
                if cell is not None:
                    row, column = cell
                    if row != self.plan_table.currentRow() or column != self.plan_table.currentColumn():
                        self.plan_table.setCurrentCell(row, column)
                    self.plan_table.horizontalScrollBar().setValue(0)
                    if not self._plan_table_layout_locked:
                        self._fit_plan_table_columns_to_viewport()
        if event.type() == QEvent.Type.Resize and (obj is self.plan_table or obj is self.plan_table.viewport()):
            if not self._plan_table_layout_locked:
                self._plan_table_fit_timer.start()
            self._experiment_control_edit_controller.sync_overlay()
            self._schedule_visible_experiment_control_rows_load()
        return super().eventFilter(obj, event)

    def _handle_experiment_control_current_index_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        _ = previous
        if not current.isValid():
            return
        if self._plan_holding or self._plan_paused:
            row = self._selected_experiment_control_row()
            if row is None:
                row = self._plan_active_row if self._plan_active_row is not None else self._plan_row_from_table_row(current.row())
            if row is not None and row >= 0:
                self._resume_experiment_control_after_manual_step_change(
                    int(row),
                    status_message=f"Running experiment plan from step {int(row) + 1}.",
                    log_message=f"Experiment plan resumed on step {int(row) + 1} after table selection.",
                    emit_event="plan_resume",
                )
                return
        self._update_timeline_selection()
        self._load_selected_step_into_editor()
        self._experiment_control_edit_controller.sync_overlay()

    def _keep_plan_table_left_aligned(self, value: int = 0) -> None:
        if value:
            self.plan_table.horizontalScrollBar().setValue(0)
        controller = getattr(self, "_experiment_control_edit_controller", None)
        if controller is not None:
            controller.sync_overlay()

    def _selected_experiment_control_row(self) -> int | None:
        row = self._selected_table_row()
        if row is None:
            return None
        return self._plan_row_from_table_row(row)

    def _experiment_control_timeline_row(self) -> int | None:
        if self._plan_running or self._plan_holding or self._plan_paused:
            if self._plan_active_row is not None:
                return self._plan_active_row
        return self._selected_experiment_control_row()

    def _flow_table_row_from_point(self, point_f) -> int | None:
        if not self.plan_table.rowCount():
            return None
        index = self.plan_table.indexAt(point_f.toPoint())
        if index.isValid():
            return index.row()
        return None

    def _move_experiment_control_step_to_row(self, source_row: int, target_row: int) -> None:
        steps = self._read_experiment_control_steps()
        if not steps:
            return
        source_row = min(max(source_row, 0), len(steps) - 1)
        target_row = min(max(target_row, 0), len(steps) - 1)
        if source_row == target_row:
            return
        step = steps.pop(source_row)
        steps.insert(target_row, step)
        self._populate_experiment_control_table(steps, selected_row=target_row)
        controller = getattr(self, "_experiment_control_edit_controller", None)
        if controller is not None:
            controller.clear_copied_selection()
        self.save_ui_state()
        _LOGGER.info("Moved experiment-plan step from %s to %s via timeline drag.", source_row + 1, target_row + 1)
        self._set_status_message(f"Moved step to position {target_row + 1}.")

    def _update_timeline_selection(self) -> None:
        runtime_row = self._plan_active_row if (self._plan_running or self._plan_holding or self._plan_paused) else None
        self._set_experiment_control_runtime_row_property(runtime_row)
        self.timeline_widget.set_steps(
            self._read_experiment_control_steps(),
            self._experiment_control_timeline_row(),
            self._timeline_progress_for_display(),
            self._plan_runtime_for_display(),
        )
        controller = getattr(self, "_experiment_control_edit_controller", None)
        if controller is not None:
            controller.sync_overlay()
        self._update_plan_table_height()


    def _copy_color_names_to_comments(self) -> None:
        steps = self._read_experiment_control_steps()
        if not steps:
            return
        for step in steps:
            color_name = next((name for name, color in self._color_palette_entries if color == step.color), step.color)
            step.description = str(color_name or "").strip()
        self._populate_experiment_control_table(steps, selected_row=self._selected_experiment_control_row())

    def _current_editor_step(self, step_number: int | None = None) -> PumpPlanStep:
        row = self._selected_experiment_control_row()
        color = self.step_color_combo.currentData()
        return PumpPlanStep(
            step=step_number or (row + 1 if row is not None else 1),
            duration_s=max(self._editor_duration_seconds, 0.0),
            color=str(color or self._default_experiment_control_color((step_number or 1) - 1)),
            valve=str(self.step_valve_button.property("valve") or "Open"),
            switch_position=self._current_switch_position_from_editor(),
            description=self.step_comment_edit.text().strip(),
            channels=[
                PumpChannelStep(
                    flow_ul_min=max(round(self.manual_flow_spins[index].value()), 0),
                    direction=self._direction_button_value(self.manual_direction_buttons[index]),
                )
                for index in range(ACTIVE_PUMP_CHANNELS)
            ],
        )

    def _set_editor_from_step(self, step: PumpPlanStep) -> None:
        self._editor_duration_seconds = max(float(step.duration_s), 0.0)
        self._suspend_duration_tracking = True
        self.step_duration_spin.setValue(self._seconds_to_display(self._editor_duration_seconds))
        self._suspend_duration_tracking = False
        color_index = self.step_color_combo.findData(step.color)
        if color_index >= 0:
            self.step_color_combo.setCurrentIndex(color_index)
        self._set_step_valve_button_state(step.valve)
        self._updating_switch_editor = True
        try:
            switch_position = max(min(int(step.switch_position), 12), 1)
            self.step_switch_spin.setValue(switch_position)
            self.step_switch_combo.setCurrentIndex(switch_position - 1)
        finally:
            self._updating_switch_editor = False
        self.step_comment_edit.setText(step.description)
        for index, channel in enumerate(step.channels):
            self.manual_flow_spins[index].setValue(max(round(float(channel.flow_ul_min)), 0))
            self._set_direction_button(self.manual_direction_buttons[index], channel.direction)
        self._apply_shared_manual_settings()

    def _load_selected_step_into_editor(self) -> None:
        table_row = self._selected_table_row()
        if table_row is None:
            return
        row = self._selected_experiment_control_row()
        steps = self._read_experiment_control_steps()
        if row is None or row >= len(steps):
            return
        self._set_editor_from_step(steps[row])

    def _add_experiment_control_step_from_editor(self) -> None:
        steps = self._read_experiment_control_steps()
        row = self._selected_experiment_control_row()
        insert_at = len(steps) if row is None else min(max(row + 1, 0), len(steps))
        step = self._current_editor_step(step_number=insert_at + 1)
        if not step.color:
            step.color = self._default_experiment_control_color(insert_at)
        steps.insert(insert_at, step)
        if steps:
            self._populate_experiment_control_table(steps, selected_row=insert_at)
        else:
            self._populate_experiment_control_table(steps)

    def _update_selected_experiment_control_step_from_editor(self) -> None:
        row = self._selected_experiment_control_row()
        steps = self._read_experiment_control_steps()
        if row is None:
            self._add_experiment_control_step_from_editor()
            return
        updated = self._current_editor_step(step_number=row + 1)
        steps[row] = updated
        self._populate_experiment_control_table(steps, selected_row=row)

    def _plan_step_commands(
        self, step: PumpPlanStep, *, start: bool
    ) -> tuple[list[_PlannedCommand], bool, list[str]]:
        """Build an ordered command list for a step transition (main-thread only — reads widget state).

        Returns (commands, needs_mswitch_refresh, pre_status_messages).
        """
        previous = self._applied_plan_step
        status_messages: list[str] = []
        commands: list[_PlannedCommand] = []
        needs_mswitch_refresh = False

        valve = str(step.valve or "").strip()
        previous_valve = str(previous.valve or "").strip().lower() if previous is not None else ""
        switch_position = int(max(min(int(step.switch_position), 12), 1))
        previous_switch = int(max(min(int(previous.switch_position), 12), 1)) if previous is not None else -1
        switch_changed = switch_position != previous_switch
        wait_for_switch_first = bool(self._wait_for_mswitch_first and switch_changed)

        pump_label = self._device_label_for("pump")
        valve_label = self._device_label_for(SWITCH)
        switch_label = self._device_label_for(SELECTOR)
        pump_connected = self._service_device_connected("pump")
        valve_connected = self._service_device_connected(SWITCH)
        mswitch_connected = self._service_device_connected(SELECTOR)

        channels_to_stop: list[int] = []
        channels_to_start: list[int] = []
        channels_to_configure: list[tuple[int, float, str, float]] = []
        channels_to_restart_after_switch: list[int] = []

        _LOGGER.info(
            "Applying experiment-plan step | step=%s valve=%s previous_valve=%s controller=%s port=%s running=%s holding=%s start=%s",
            step.step,
            valve or "-",
            str(previous.valve or "").strip() or "-" if previous is not None else "-",
            *self._service_connection_detail(SWITCH),
            self._plan_running,
            self._plan_holding,
            start,
        )

        if pump_connected:
            for index, channel in enumerate(step.channels, start=1):
                direction = str(channel.direction or "OFF").upper()
                active = channel.flow_ul_min > 0.0 and direction != "OFF"
                tube_mm = self.manual_tube_spins[index - 1].value()
                previous_channel = previous.channels[index - 1] if previous is not None else None
                previous_direction = (
                    str(previous_channel.direction or "OFF").upper() if previous_channel is not None else "OFF"
                )
                previous_active = (
                    previous_channel is not None
                    and previous_channel.flow_ul_min > 0.0
                    and previous_direction != "OFF"
                )
                previous_flow = float(previous_channel.flow_ul_min) if previous_channel is not None else 0.0
                channel_changed = (
                    previous is None
                    or previous_channel is None
                    or previous_direction != direction
                    or abs(previous_flow - float(channel.flow_ul_min)) > 1e-9
                )
                if previous_active and (not active or channel_changed or (wait_for_switch_first and switch_changed)):
                    channels_to_stop.append(index)
                if wait_for_switch_first and switch_changed and previous_active and active and not channel_changed:
                    channels_to_restart_after_switch.append(index)
                if active and channel_changed:
                    channels_to_configure.append((index, float(channel.flow_ul_min), direction, tube_mm))
                    if start:
                        channels_to_start.append(index)
                elif active and start and not previous_active:
                    channels_to_start.append(index)
        else:
            _LOGGER.warning("Pump controller offline; skipping pump channel updates | step=%s", step.step)
            status_messages.append("Pump controller not connected.")

        effective_starts_after_switch = list(channels_to_start)
        if wait_for_switch_first and switch_changed:
            for index in channels_to_restart_after_switch:
                if index not in effective_starts_after_switch:
                    effective_starts_after_switch.append(index)

        def _pump_stop_cmds(indices: list[int]) -> list[_PlannedCommand]:
            return [_PlannedCommand(pump_label, "pump.stop", {"channel": i}, f"pump.stop ch={i}") for i in indices]

        def _pump_configure_cmds() -> list[_PlannedCommand]:
            return [
                _PlannedCommand(
                    pump_label, "pump.set_flow",
                    {"channel": i, "flow_ul_min": fl, "direction": d, "tube_mm": t, "start": False},
                    f"pump.set_flow ch={i} flow={fl:.2f} dir={d}",
                )
                for i, fl, d, t in channels_to_configure
            ]

        def _pump_start_cmds(indices: list[int]) -> list[_PlannedCommand]:
            return [_PlannedCommand(pump_label, "pump.start", {"channel": i}, f"pump.start ch={i}") for i in indices]

        def _valve_cmd() -> list[_PlannedCommand]:
            if not (valve and valve.lower() != previous_valve):
                return []
            if valve_connected:
                return [_PlannedCommand(valve_label, "switch.set_position", {"position": valve}, f"switch.set_position pos={valve}")]
            status_messages.append("Switch controller not connected.")
            _LOGGER.warning("Valve command skipped | controller not connected | step=%s valve=%s", step.step, valve)
            return []

        def _switch_cmd() -> list[_PlannedCommand]:
            if not switch_changed:
                return []
            if mswitch_connected:
                return [_PlannedCommand(
                    switch_label, "switch.move_to", {"position": switch_position, "block": True},
                    f"switch.move_to pos={switch_position}", is_switch_move=True,
                )]
            status_messages.append("Switch rotary valve not connected.")
            _LOGGER.warning("Switch rotary valve command skipped | controller not connected | step=%s switch=%s", step.step, switch_position)
            return []

        def _pump_display_cmd() -> list[_PlannedCommand]:
            # Always sent (not diffed against the previous step) so a step with the
            # option off reliably clears whatever the previous step left showing,
            # instead of leaving a stale comment on the pump's display.
            if not pump_connected:
                return []
            text = str(step.description or "").strip() if self._pump_display_enabled else ""
            return [_PlannedCommand(pump_label, "pump.set_display", {"text": text}, f"pump.set_display text={text!r}")]

        if wait_for_switch_first:
            if pump_connected:
                commands.extend(_pump_stop_cmds(channels_to_stop))
            commands.extend(_switch_cmd())
            commands.extend(_valve_cmd())
            if pump_connected:
                commands.extend(_pump_configure_cmds())
                commands.extend(_pump_start_cmds(effective_starts_after_switch))
                commands.extend(_pump_display_cmd())
        else:
            if pump_connected:
                commands.extend(_pump_stop_cmds(channels_to_stop))
                commands.extend(_pump_configure_cmds())
                commands.extend(_pump_start_cmds(channels_to_start))
                commands.extend(_pump_display_cmd())
            commands.extend(_valve_cmd())
            commands.extend(_switch_cmd())

        needs_mswitch_refresh = any(c.is_switch_move for c in commands)
        return commands, needs_mswitch_refresh, status_messages

    @property
    def _step_apply_pending(self) -> bool:
        """True while at least one step-apply is in flight on device_io_pool().

        A count, not the single dispatch's own state: multiple applies can
        legitimately overlap now that every GUI trigger dispatches async,
        not just auto-advance.
        """
        return self._step_apply_inflight > 0

    def _apply_step_to_pump_async(
        self,
        step: PumpPlanStep,
        *,
        start: bool,
        on_success: Callable[[], None] | None = None,
    ) -> None:
        """Dispatch device commands for a step transition to a QRunnable (main-thread safe entry point).

        *on_success* runs only if every command in this specific dispatch
        succeeded, once its own result comes back - never a different
        dispatch's, even if several are in flight at once (see
        _StepApplyResult.on_success's docstring).
        """
        _LOGGER.info(
            "Experiment control step apply (async) | step=%s pump_connected=%s valve_connected=%s switch_connected=%s",
            step.step,
            self._service_device_connected("pump"),
            self._service_device_connected(SWITCH),
            self._service_device_connected(SELECTOR),
        )
        try:
            commands, needs_mswitch_refresh, pre_status = self._plan_step_commands(step, start=start)
        except Exception as exc:
            _LOGGER.error("Step plan failed (async) | step=%s error=%s", step.step, exc)
            self._set_status_message(f"Step apply failed: {exc}")
            return
        # Optimistic state update: record the new step as applied so back-to-back
        # _plan_step_commands calls produce the correct diff even before the runnable finishes.
        self._applied_plan_step = step
        self._step_apply_inflight += 1
        runnable = _StepApplyRunnable(
            self._device_comm_service, commands, step, needs_mswitch_refresh, pre_status, on_success
        )
        runnable.signals.done.connect(self._on_step_apply_async_done)
        self.hw_status_refresh_requested.emit()
        # Must run on the single-lane device I/O pool, not the general-purpose
        # pool: this sends real commands (including switch.move_to) to drivers
        # that were connected/homed on device_io_pool's worker thread. The AMF
        # vendor SDK is not guaranteed thread-safe, and some vendor SDKs also
        # assume same-thread access to a device handle - dispatching from an
        # arbitrary QThreadPool.globalInstance() worker thread risks commands
        # silently failing/no-op'ing instead of raising, which would look
        # exactly like "the selector doesn't react" with no visible error.
        device_io_pool().start(runnable)

    def _on_step_apply_async_done(self, result: _StepApplyResult) -> None:
        self._step_apply_inflight = max(0, self._step_apply_inflight - 1)
        self.hw_status_refresh_requested.emit()
        if result.needs_mswitch_refresh:
            self._update_mswitch_state_from_probe()
        status = "; ".join(result.status_messages)
        self._set_status_message(
            ((" | ".join(result.status_messages) + " | ") if result.status_messages else "")
            + f"Applied experiment-plan step {result.step.step}."
        )
        _LOGGER.info("Applied experiment-plan step %s (async)", result.step.step)
        self._emit_experimental_control_state("step_applied", result.step, status=status)
        if result.success and result.on_success is not None:
            result.on_success()

    def _experiment_control_step_change_summary(self, previous: PumpPlanStep | None, updated: PumpPlanStep | None) -> str:
        if previous is None or updated is None:
            return ""
        changes: list[str] = []
        if abs(float(previous.duration_s) - float(updated.duration_s)) > 1e-9:
            changes.append(f"duration {float(previous.duration_s):g} -> {float(updated.duration_s):g}")
        if str(previous.color or "").strip() != str(updated.color or "").strip():
            changes.append(f"color {previous.color or '-'} -> {updated.color or '-'}")
        if str(previous.valve or "").strip() != str(updated.valve or "").strip():
            changes.append(f"valve {previous.valve or '-'} -> {updated.valve or '-'}")
        if int(previous.switch_position) != int(updated.switch_position):
            changes.append(f"switch {int(previous.switch_position)} -> {int(updated.switch_position)}")
        if str(previous.description or "").strip() != str(updated.description or "").strip():
            prev_desc = str(previous.description or "").strip() or "-"
            new_desc = str(updated.description or "").strip() or "-"
            changes.append(f"comment {prev_desc} -> {new_desc}")
        for index in range(min(len(previous.channels), len(updated.channels), ACTIVE_PUMP_CHANNELS)):
            prev_channel = previous.channels[index]
            new_channel = updated.channels[index]
            if abs(float(prev_channel.flow_ul_min) - float(new_channel.flow_ul_min)) > 1e-9:
                changes.append(f"CH{index + 1} flow {float(prev_channel.flow_ul_min):g} -> {float(new_channel.flow_ul_min):g}")
            prev_dir = str(prev_channel.direction or "OFF").upper()
            new_dir = str(new_channel.direction or "OFF").upper()
            if prev_dir != new_dir:
                changes.append(f"CH{index + 1} dir {prev_dir} -> {new_dir}")
        if not changes:
            return ""
        return "Edited active step: " + "; ".join(changes)

    def _set_experiment_control_runtime_row(
        self,
        row: int,
        *,
        event: str,
        status: str = "",
        apply_step: bool = False,
        refresh_status: bool = True,
    ) -> None:
        steps = self._read_experiment_control_steps()
        if row < 0 or row >= len(steps):
            return
        self._plan_active_row = row
        if apply_step:
            self._apply_step_to_pump_async(steps[row], start=True)
        self._sync_experiment_control_timeline(steps, row, refresh_status=refresh_status)
        self._emit_experimental_control_state(event, steps[row], status=status)

    def _jump_to_experiment_control_step(self, row: int) -> None:
        steps = self._read_experiment_control_steps()
        if row < 0 or row >= len(steps):
            return
        if self._plan_running or self._plan_holding or self._plan_paused:
            if self._plan_running:
                self._plan_active_row = row
                self._plan_elapsed_s = 0.0
                self._plan_resume_elapsed_s = 0.0
                self._plan_started_monotonic = monotonic()
                self._step_started_monotonic = monotonic()
                self._set_experiment_control_runtime_row(
                    row,
                    event="step_jump",
                    apply_step=True,
                )
                self._plan_runtime_s = self._step_runtime_for_display()
                self._plan_resume_runtime_s = self._plan_runtime_s
                return
            self._resume_experiment_control_after_manual_step_change(
                row,
                status_message=f"Running experiment plan from step {row + 1}.",
                log_message=f"Experiment plan resumed on step {row + 1} after manual step change.",
                emit_event="plan_resume",
            )
            return
        self._select_experiment_control_plan_row(row)
        self._load_selected_step_into_editor()
        self._update_timeline_selection()
        self._set_status_message(f"Selected experiment-plan step {row + 1}.")

    def _apply_selected_experiment_control_step(self, row: int) -> None:
        steps = self._read_experiment_control_steps()
        if row < 0 or row >= len(steps):
            return
        if self._plan_running:
            self._set_experiment_control_runtime_row(
                row,
                event="step_apply",
                apply_step=True,
            )
            return
        if self._plan_holding:
            self._resume_experiment_control_after_manual_step_change(
                row,
                status_message=f"Running experiment plan from step {row + 1}.",
                log_message=f"Experiment plan resumed on step {row + 1} after manual step apply.",
                emit_event="plan_resume",
            )
            return
        if self._plan_paused:
            self._resume_experiment_control_after_manual_step_change(
                row,
                status_message=f"Running experiment plan from step {row + 1}.",
                log_message=f"Experiment plan resumed on step {row + 1} after manual step apply.",
                emit_event="plan_resume",
            )
            return
        # Not running/holding/paused - only select the row. Applying the
        # step to hardware here would start devices moving with no way to
        # stop them: Stop is gated on _plan_running/_plan_holding/
        # _plan_paused, none of which double-clicking a step outside those
        # states ever sets, leaving the plan in an unstoppable "device is
        # moving but nothing is running" state.
        self._jump_to_experiment_control_step(row)

    def _run_experiment_control(self) -> None:
        self._start_or_resume_experiment_control()

    def _start_or_resume_experiment_control(self) -> None:
        steps = self._read_experiment_control_steps()
        if not steps:
            self._set_status_message("Experiment plan is empty.")
            return
        if self._plan_running:
            return
        if self._plan_holding or self._plan_paused:
            restore_step = None
            if self._plan_paused and self._paused_plan_step is not None:
                restore_step = deepcopy(self._paused_plan_step)
                self._paused_plan_step = None
            self._resume_experiment_plan(
                restore_step=restore_step,
                status_message=f"Resumed experiment plan on step {self._plan_active_row + 1 if self._plan_active_row is not None else 1}.",
                log_message=f"Experiment plan resumed | step={self._plan_active_row + 1 if self._plan_active_row is not None else 1}",
                emit_event="plan_resume",
            )
            return
        recording_active = bool(getattr(getattr(self, "recording_controller", None), "_measurement_active", False))
        if self.record_with_flow_button.isChecked() and not recording_active:
            row = self._selected_experiment_control_row()
            if row is None:
                row = 0
                self._select_experiment_control_plan_row(0)
            self._queue_experiment_control_start_after_recording(paused=False, row=row)
        if not self._request_recording_control("start"):
            self._pending_experiment_control_start_after_recording = None
            self._set_status_message("Experiment plan start cancelled because recording was not started.")
            return
        if self._pending_experiment_control_start_after_recording is not None:
            return
        row = self._selected_experiment_control_row()
        if row is None:
            row = 0
            self._select_experiment_control_plan_row(0)
        self._begin_experiment_plan_run(row, steps)

    def _hold_experiment_control(self) -> None:
        self._enter_hold_state()

    def _pause_experiment_control(self) -> None:
        self._enter_pause_state()

    def _stop_experiment_control(self) -> None:
        self._stop_experiment_plan(self._applied_plan_step)

    def _schedule_plan_timer(self, steps: list | None = None) -> None:
        if self._plan_timer.isActive():
            return
        if not self._plan_running and not self._plan_holding and not self._plan_paused:
            return
        if not self._plan_running or self._plan_started_monotonic is None or self._plan_holding or self._plan_paused:
            self._plan_timer.start(150)
            return
        if steps is None:
            steps = self._read_experiment_control_steps()
        active_row = self._plan_active_row
        if active_row is None or not steps or not (0 <= active_row < len(steps)):
            self._plan_timer.start(150)
            return
        step = steps[active_row]
        elapsed = self._plan_resume_elapsed_s + max(monotonic() - self._plan_started_monotonic, 0.0)
        remaining_ms = int((float(step.duration_s) - elapsed) * 1000)
        self._plan_timer.start(max(1, min(150, remaining_ms)))

    def _move_to_relative_experiment_control_step(self, delta: int) -> None:
        steps = self._read_experiment_control_steps()
        if not steps:
            return
        running = self._plan_running
        active = self._plan_running or self._plan_holding or self._plan_paused
        row = self._plan_active_row if active else self._selected_experiment_control_row()
        if row is None:
            row = 0
        raw_target = row + delta
        if active and raw_target >= len(steps):
            # Pressing Next past the last step finishes the plan, mirroring
            # what _advance_experiment_control_progress already does when
            # auto-advance reaches the end. Without this, target below just
            # clamps to the last step (same row), and the running branch's
            # elapsed-time reset would restart that same last step from 0
            # instead of finishing - "Next" on the last step should finish
            # the plan, not replay it.
            self._plan_elapsed_s = max(float(steps[-1].duration_s), 0.0)
            self._plan_resume_elapsed_s = self._plan_elapsed_s
            self._stop_experiment_control()
            self._set_status_message("Experiment plan finished.")
            _LOGGER.info("Experiment plan finished.")
            return
        target = min(max(raw_target, 0), len(steps) - 1)
        if active:
            if running:
                # Mirrors _jump_to_experiment_control_step's reset - without
                # it, the new step's elapsed/ETA tracking kept accumulating
                # from wherever the previous step left off instead of
                # restarting at 0 (elapsed = _plan_resume_elapsed_s + time
                # since _plan_started_monotonic, neither of which this used
                # to touch).
                self._plan_elapsed_s = 0.0
                self._plan_resume_elapsed_s = 0.0
                self._plan_started_monotonic = monotonic()
                self._set_experiment_control_runtime_row(
                    target,
                    event="step_jump",
                    apply_step=True,
                )
                self._step_started_monotonic = monotonic()
                self._plan_runtime_s = self._step_runtime_for_display()
                self._plan_resume_runtime_s = self._plan_runtime_s
            else:
                self._resume_experiment_control_after_manual_step_change(
                    target,
                    status_message=f"Running experiment plan from step {target + 1}.",
                    log_message=f"Experiment plan resumed on step {target + 1} after step navigation.",
                    emit_event="plan_resume",
                )
        else:
            self._jump_to_experiment_control_step(target)
        if not (self._plan_running or self._plan_holding or self._plan_paused):
            self._set_status_message(f"Selected experiment-plan step {target + 1}.")

    def _timeline_progress_for_display(self) -> float | None:
        if self._plan_running or self._plan_holding or self._plan_paused:
            row = self._plan_active_row if self._plan_active_row is not None else self._selected_experiment_control_row()
            steps = self._read_experiment_control_steps()
            if row is not None and 0 <= row < len(steps):
                return max(float(steps[row].start_s) + max(float(self._plan_elapsed_s), 0.0), 0.0)
            return max(float(self._plan_elapsed_s), 0.0)
        return None

    def _plan_runtime_for_display(self) -> float:
        if self._measurement_started_monotonic is not None:
            return max(monotonic() - self._measurement_started_monotonic, 0.0)
        return max(float(self._plan_runtime_s), 0.0)

    def _step_runtime_for_display(self) -> float:
        if self._step_started_monotonic is not None:
            return max(monotonic() - self._step_started_monotonic, 0.0)
        return max(float(self._plan_resume_runtime_s), 0.0)

    def _experiment_control_step_label_for_overlay(self, step: PumpPlanStep) -> str:
        """Label text for a step, matching whichever label mode the
        timeline widget is currently showing (comment vs color name) -
        reuses the timeline widget's own resolution logic directly so the
        sensorgram's step overlay can never drift out of sync with what
        the experiment-control panel's own timeline displays.
        """
        timeline_widget = getattr(self, "timeline_widget", None)
        if timeline_widget is not None and hasattr(timeline_widget, "_step_label_text"):
            try:
                return timeline_widget._step_label_text(step)
            except Exception:
                pass
        return str(step.description or "").strip()

    def _emit_experimental_control_state(self, event: str, step: PumpPlanStep | None = None, *, status: str = "") -> None:
        if step is None:
            step = self._applied_plan_step
        plan_state = self._experiment_runtime_snapshot().payload_state
        payload: dict[str, object] = {
            "event": event,
            "plan_state": plan_state,
            "step_index": int(step.step) if step is not None else "",
            "color": str(step.color or "") if step is not None else "",
            "label": self._experiment_control_step_label_for_overlay(step) if step is not None else "",
            "elapsed_in_step_ms": int(round(max(float(self._plan_elapsed_s), 0.0) * 1000.0)),
            "pump_running": bool(self._plan_running),
            "valve_position": str(step.valve or "") if step is not None else "",
            "switch_position": int(step.switch_position) if step is not None else "",
            "pump_connected": bool(self._service_device_connected("pump")),
            "valve_connected": bool(self._service_device_connected(SWITCH)),
            "switch_connected": bool(self._service_device_connected(SELECTOR)),
            "status": status,
        }
        tube_values = self._tube_mm_values()
        for index in range(6):
            channel = step.channels[index] if step is not None and index < len(step.channels) else None
            payload[f"ch{index + 1}_flow_ul_min"] = float(channel.flow_ul_min) if channel is not None else ""
            payload[f"ch{index + 1}_direction"] = str(channel.direction or "OFF") if channel is not None else ""
            payload[f"ch{index + 1}_tube_mm"] = float(tube_values[index]) if index < len(tube_values) else ""
        self.experimental_control_state_recorded.emit(payload)

    def _emit_flow_state(self, event: str, step: PumpPlanStep | None = None, *, status: str = "") -> None:
        self._emit_experimental_control_state(event, step, status=status)

    def _advance_experiment_control_progress(self) -> None:
        steps: list | None = None

        def _callback() -> None:
            nonlocal steps
            if self._plan_holding or self._plan_paused:
                steps = self._read_experiment_control_steps()
                if steps:
                    self._sync_experiment_control_timeline(steps, self._plan_active_row, refresh_status=True)
                return
            if not self._plan_running or self._plan_started_monotonic is None:
                return
            if self._step_apply_pending:
                # Previous step's device commands still running; wait and retry.
                self._plan_timer.start(50)
                return
            steps = self._read_experiment_control_steps()
            if not steps:
                self._stop_experiment_control()
                return
            elapsed = self._plan_resume_elapsed_s + max(monotonic() - self._plan_started_monotonic, 0.0)
            current_row = self._plan_active_row if self._plan_active_row is not None else self._selected_experiment_control_row()
            if current_row is None or not (0 <= current_row < len(steps)):
                current_row = 0
            current_step = steps[current_row]
            if elapsed >= max(float(current_step.duration_s), 0.0):
                next_row = current_row + 1
                if next_row >= len(steps):
                    self._plan_elapsed_s = max(float(current_step.duration_s), 0.0)
                    self._plan_resume_elapsed_s = self._plan_elapsed_s
                    self._stop_experiment_control()
                    self._set_status_message("Experiment plan finished.")
                    _LOGGER.info("Experiment plan finished.")
                    return
                self._plan_active_row = next_row
                self._apply_step_to_pump_async(steps[next_row], start=True)
                self._plan_elapsed_s = 0.0
                self._plan_resume_elapsed_s = 0.0
                self._plan_started_monotonic = monotonic()
                self._step_started_monotonic = monotonic()
                self._sync_experiment_control_timeline(steps, next_row, refresh_status=True)
                return
            self._plan_elapsed_s = elapsed
            self._sync_experiment_control_timeline(steps, current_row, refresh_status=True)

        self._run_gui_callback_timed("experiment_control_progress", _callback)
        self._schedule_plan_timer(steps)

    def _activate_experiment_control_step_for_elapsed(self, elapsed_s: float, *, force: bool) -> None:
        steps = self._read_experiment_control_steps()
        if not steps:
            self._plan_active_row = None
            self._plan_elapsed_s = 0.0
            return
        self._plan_elapsed_s = max(float(elapsed_s), 0.0)
        target_row = self._plan_active_row if self._plan_active_row is not None else self._selected_experiment_control_row()
        if target_row is None or not (0 <= target_row < len(steps)):
            target_row = 0
        if force or target_row != self._plan_active_row:
            self._plan_active_row = target_row
            self._apply_step_to_pump_async(steps[target_row], start=True)
        self._sync_experiment_control_timeline(steps, target_row)

    def _stop_all_channels(self) -> None:
        if not self._service_device_connected("pump"):
            self._set_status_message("Pump offline. Nothing to stop.")
            return
        try:
            self._send_device_command("pump", "pump.stop_all", {"channel_count": ACTIVE_PUMP_CHANNELS})
        except Exception as exc:
            self._set_status_message(f"Stop failed: {exc}")
            _LOGGER.error("Stop all channels failed: %s", exc)
            return
        self._applied_plan_step = None
        self._set_status_message("Stopped all pump channels.")
        _LOGGER.info("Stopped all pump channels.")

    def shutdown_devices(self) -> None:
        self._set_status_message("Shutting down devices...")
        try:
            if self._service_device_connected("pump"):
                try:
                    self._send_device_command("pump", "pump.stop_all", {"channel_count": ACTIVE_PUMP_CHANNELS})
                    _LOGGER.info("Shutdown: stopped all pump channels.")
                except Exception as exc:
                    _LOGGER.warning("Shutdown: could not stop pump channels: %s", exc)
            if self._service_device_connected(SELECTOR):
                _LOGGER.info("Shutdown: Selector left in current position before disconnect.")
        finally:
            self._stop_experiment_control()
            self._device_comm_service.disconnect_device(self._device_label_for(SWITCH))
            self._valve_probe = None
            self._device_comm_service.disconnect_device(self._device_label_for(SELECTOR))
            self._mswitch_probe = None
            self._device_comm_service.disconnect_device(self._device_label_for("pump"))
            self._client = None
            self._probe = None
            self.valve_availability_changed.emit(None)
            self.mswitch_availability_changed.emit(None)
            self.availability_changed.emit(None)
            self._set_connection_visual(False, "Pump disconnected.")

    def _read_live_status(self) -> None:
        if not self._service_device_connected("pump"):
            self._show_info("Connect the pump first.")
            return
        try:
            modes = [
                self._device_comm_service.send_command(
                    self._device_label_for("pump"),
                    DeviceCommand("pump.query", {"command": f"{channel}xM"}),
                ).response
                for channel in range(1, ACTIVE_PUMP_CHANNELS + 1)
            ]
            directions = [
                self._device_comm_service.send_command(
                    self._device_label_for("pump"),
                    DeviceCommand("pump.query", {"command": f"{channel}xD"}),
                ).response
                for channel in range(1, ACTIVE_PUMP_CHANNELS + 1)
            ]
        except Exception as exc:
            self._set_status_message(f"Read status failed: {exc}")
            _LOGGER.error("Live status read failed: %s", exc)
            return
        self._set_status_message(
            " | ".join(
                f"CH{index + 1}: {directions[index]} / {modes[index]}"
                for index in range(ACTIVE_PUMP_CHANNELS)
            )
        )
        _LOGGER.debug("Live status read.")

    def current_pump_plan_hdf5_rows(self) -> list[list[str]]:
        core_plan = to_core_experiment_plan(self._read_experiment_control_steps())
        table = build_legacy_experiment_plan_row_table(
            core_plan,
            tube_mm_by_channel=self._tube_mm_values(),
            active_channel_count=ACTIVE_PUMP_CHANNELS,
            hdf5_channel_count=HDF5_PUMP_CHANNELS,
        )
        return table.rows

    def switch_solution_hdf5_rows(self) -> list[list[str]]:
        return [[str(port), self._switch_solution_label(port)] for port in range(1, 13)]

    def switch_solution_hdf5_payload(self) -> dict[str, object]:
        return {
            "switch_solution_mode": bool(self._switch_solution_mode),
            "switch_solution_labels": list(self._switch_solution_labels),
            "switch_solution_rows": self.switch_solution_hdf5_rows(),
            "valve_state_labels": dict(self._valve_state_labels),
            "valve_state_colors": dict(self._valve_state_colors),
            "color_palette_entries": [
                {"name": name, "color": color} for name, color in self._color_palette_entries
            ],
        }

    def _tube_mm_values(self) -> list[float]:
        return [spin.value() for spin in self.manual_tube_spins]

    def _serialize_experiment_control_steps(self, steps: list[PumpPlanStep]) -> list[dict[str, object]]:
        payload: list[dict[str, object]] = []
        for step in self._strip_pause_flow_step(steps):
            payload.append(
                {
                    "duration_s": float(step.duration_s),
                    "color": step.color,
                    "valve": step.valve,
                    "switch_position": int(step.switch_position),
                    "description": step.description,
                    "channels": [
                        {
                            "flow_ul_min": float(channel.flow_ul_min),
                            "direction": channel.direction,
                        }
                        for channel in step.channels
                    ],
                }
            )
        return payload

    def _deserialize_experiment_control_steps(self, payload: object) -> list[PumpPlanStep]:
        if not isinstance(payload, list):
            return []
        steps: list[PumpPlanStep] = []
        for index, raw_step in enumerate(payload, start=1):
            if not isinstance(raw_step, dict):
                continue
            raw_channels = raw_step.get("channels", [])
            channels: list[PumpChannelStep] = []
            if isinstance(raw_channels, list):
                for raw_channel in raw_channels[:ACTIVE_PUMP_CHANNELS]:
                    if isinstance(raw_channel, dict):
                        channels.append(
                            PumpChannelStep(
                                flow_ul_min=max(round(_safe_float(raw_channel.get("flow_ul_min", 0.0))), 0),
                                direction=str(raw_channel.get("direction", "OFF") or "OFF"),
                            )
                        )
            while len(channels) < ACTIVE_PUMP_CHANNELS:
                channels.append(PumpChannelStep())
            steps.append(
                PumpPlanStep(
                    step=index,
                    duration_s=max(_safe_float(raw_step.get("duration_s", 0.0)), 0.0),
                    color=str(
                        raw_step.get("color", self._default_experiment_control_color(index - 1))
                        or self._default_experiment_control_color(index - 1)
                    ),
                    valve=str(raw_step.get("valve", "Open") or "Open"),
                    switch_position=max(min(_safe_int(raw_step.get("switch_position", 1), 1), 12), 1),
                    description=str(raw_step.get("description", "") or ""),
                    channels=channels,
                )
            )
        return recompute_plan_timing(self._strip_pause_flow_step(steps))

    def _restore_experiment_control_state(self) -> None:
        state = self._ui_state
        saved_time_unit = state.get("time_unit_mode")
        if isinstance(saved_time_unit, str) and saved_time_unit in {"s", "min"}:
            self._time_unit_mode = saved_time_unit
            self._update_time_unit_ui()
        tube_values = state.get("tube_mm_values")
        if isinstance(tube_values, list):
            for index, value in enumerate(tube_values[:ACTIVE_PUMP_CHANNELS]):
                try:
                    self.manual_tube_spins[index].setValue(float(value))
                except (TypeError, ValueError):
                    continue

        saved_duration = state.get("editor_duration_s")
        if isinstance(saved_duration, (int, float)):
            self._editor_duration_seconds = max(float(saved_duration), 0.0)
            self._suspend_duration_tracking = True
            self.step_duration_spin.setValue(self._seconds_to_display(self._editor_duration_seconds))
            self._suspend_duration_tracking = False
        saved_color = state.get("editor_color")
        if isinstance(saved_color, str):
            color_index = self.step_color_combo.findData(saved_color)
            if color_index >= 0:
                self.step_color_combo.setCurrentIndex(color_index)
        saved_valve = state.get("editor_valve")
        if isinstance(saved_valve, str):
            self._set_step_valve_button_state(saved_valve)
        saved_switch = state.get("editor_switch_position")
        if isinstance(saved_switch, (int, float)):
            switch_position = max(min(int(saved_switch), 12), 1)
            self.step_switch_spin.setValue(switch_position)
            self.step_switch_combo.setCurrentIndex(switch_position - 1)
        saved_comment = state.get("editor_comment")
        if isinstance(saved_comment, str):
            self.step_comment_edit.setText(saved_comment)
        saved_pump_display_enabled = state.get("pump_display_enabled")
        if isinstance(saved_pump_display_enabled, bool):
            self._pump_display_enabled = saved_pump_display_enabled
        saved_pump_display_highlight_enabled = state.get("pump_display_highlight_enabled")
        if isinstance(saved_pump_display_highlight_enabled, bool):
            self._pump_display_highlight_enabled = self._pump_display_enabled and saved_pump_display_highlight_enabled
        if isinstance(saved_pump_display_enabled, bool) or isinstance(saved_pump_display_highlight_enabled, bool):
            self._update_step_comment_display_button_icon()

        editor_channels = state.get("editor_channels")
        if isinstance(editor_channels, list):
            for index, raw_channel in enumerate(editor_channels[:ACTIVE_PUMP_CHANNELS]):
                if not isinstance(raw_channel, dict):
                    continue
                self.manual_flow_spins[index].setValue(max(round(float(raw_channel.get("flow_ul_min", 0.0))), 0))
                direction = str(raw_channel.get("direction", "CW") or "CW")
                self._set_direction_button(self.manual_direction_buttons[index], direction)

        saved_uniform = state.get("manual_uniform")
        if isinstance(saved_uniform, bool):
            self.manual_uniform_button.setChecked(saved_uniform)
        saved_details = state.get("show_plan_details")
        if isinstance(saved_details, bool):
            self.plan_detail_toggle.setChecked(saved_details)
            self._show_plan_details = saved_details
        saved_switch_mode = state.get("switch_solution_mode")
        if isinstance(saved_switch_mode, bool):
            self.step_switch_mode_button.setChecked(saved_switch_mode)
        saved_wait_for_mswitch_first = state.get("wait_for_mswitch_first")
        if isinstance(saved_wait_for_mswitch_first, bool):
            self._wait_for_mswitch_first = saved_wait_for_mswitch_first
        saved_valve_labels = state.get("valve_state_labels")
        if isinstance(saved_valve_labels, dict):
            self._valve_state_labels = self._load_valve_state_labels({"valve_state_labels": saved_valve_labels})
            set_step_valve_button_state_for_button(
                self,
                self.step_valve_button,
                str(self.step_valve_button.property("valve") or "Open"),
            )
        saved_valve_colors = state.get("valve_state_colors")
        if isinstance(saved_valve_colors, dict):
            self._valve_state_colors = self._load_valve_state_colors({"valve_state_colors": saved_valve_colors})
        saved_switch_labels = state.get("switch_solution_labels")
        if isinstance(saved_switch_labels, list):
            labels: list[str] = []
            for index, raw_label in enumerate(saved_switch_labels[:12], start=1):
                labels.append(str(raw_label).strip() or f"Solution {index}")
            while len(labels) < 12:
                labels.append(f"Solution {len(labels) + 1}")
            self._switch_solution_labels = labels
        self._update_experiment_control_toggle_button()
        self._refresh_switch_solution_controls()
        saved_pause_state = state.get("pause_state_step")
        if isinstance(saved_pause_state, dict):
            self._experiment_control_pause_template = self._deserialize_experiment_control_pause_template(saved_pause_state)
        saved_pause_dialog_state = state.get("pause_state_dialog_state")
        if isinstance(saved_pause_dialog_state, dict):
            self._pause_state_dialog_state = dict(saved_pause_dialog_state)
        saved_view_mode = state.get("experiment_control_view_mode")
        if isinstance(saved_view_mode, str):
            self._experiment_control_view_mode = self._normalize_experiment_control_view_mode(saved_view_mode)
        saved_timeline_label_mode = state.get("timeline_label_mode")
        if isinstance(saved_timeline_label_mode, str):
            self._experiment_control_timeline_label_mode = self._normalize_experiment_control_timeline_label_mode(
                saved_timeline_label_mode
            )
        saved_view_mode_panel_sizes = state.get("experiment_control_view_mode_panel_sizes")
        if isinstance(saved_view_mode_panel_sizes, dict):
            self._experiment_control_view_mode_panel_sizes = self._load_experiment_control_view_mode_panel_sizes(
                {"experiment_control_view_mode_panel_sizes": saved_view_mode_panel_sizes}
            )
        if hasattr(self, "timeline_widget"):
            self._update_experiment_control_timeline_label_mode()
        self._experiment_control_view_mode_apply_pending = True

        self._experiment_control_bootstrap_pending_state = dict(state)
        self._experiment_control_bootstrap_pending_steps = self._deserialize_experiment_control_steps(state.get("plan_steps"))
        self._experiment_control_bootstrap_pending_selected_row = state.get("selected_plan_row") if isinstance(state.get("selected_plan_row"), int) else None
        self._experiment_control_bootstrap_pending_step_index = 0
        if self._experiment_control_bootstrap_pending_steps:
            self.timeline_widget.set_steps(
                self._experiment_control_bootstrap_pending_steps,
                self._experiment_control_bootstrap_pending_selected_row,
                0.0,
            )
        self._schedule_experiment_control_bootstrap()

    def _schedule_experiment_control_bootstrap(self) -> None:
        if self._experiment_control_bootstrap_started:
            return
        self._experiment_control_bootstrap_started = True
        QTimer.singleShot(0, self._start_experiment_control_bootstrap)

    def _start_experiment_control_bootstrap(self) -> None:
        try:
            self._experiment_control_bootstrap_in_progress = True
            self._set_experiment_control_bootstrap_busy(True)
            state = self._experiment_control_bootstrap_pending_state or self._ui_state
            saved_steps = list(self._experiment_control_bootstrap_pending_steps)
            if not saved_steps:
                self._finalize_experiment_control_bootstrap_population(state, [])
                return
            self._begin_experiment_control_bootstrap_population(state, saved_steps)
        except Exception as exc:
            self._abort_experiment_control_bootstrap_population(str(exc))

    def _begin_experiment_control_bootstrap_population(self, state: dict[str, object], steps: list[PumpPlanStep]) -> None:
        self._experiment_control_bootstrap_pending_state = dict(state)
        self._experiment_control_bootstrap_pending_steps = list(steps)
        self._experiment_control_bootstrap_pending_step_index = 0
        self._experiment_control_steps_cache = deepcopy(steps)
        self._experiment_control_loaded_widget_rows.clear()
        _LOGGER.info(
            "Experiment control bootstrap +%.1f ms: populating %d step(s)",
            (perf_counter() - self._bootstrap_t0) * 1000.0,
            len(steps),
        )
        self.plan_table.blockSignals(True)
        self.plan_table.setUpdatesEnabled(False)
        try:
            self._plan_model.set_steps(steps)
            self.plan_table.clearSelection()
        finally:
            self.plan_table.setUpdatesEnabled(True)
            self.plan_table.blockSignals(False)
        self._experiment_plan_import_fill_timer.start()

    def _advance_experiment_control_bootstrap_population(self) -> None:
        try:
            steps = self._experiment_control_bootstrap_pending_steps
            state = self._experiment_control_bootstrap_pending_state or self._ui_state
            if not steps:
                self._experiment_plan_import_fill_timer.stop()
                self._finalize_experiment_control_bootstrap_population(state, [])
                return
            self._experiment_plan_import_fill_timer.stop()
            self._populate_experiment_control_table(steps, selected_row=self._experiment_control_bootstrap_pending_selected_row)
            self._finalize_experiment_control_bootstrap_population(state, steps)
        except Exception as exc:
            self._abort_experiment_control_bootstrap_population(str(exc))

    def _finalize_experiment_control_bootstrap_population(self, state: dict[str, object], steps: list[PumpPlanStep]) -> None:
        try:
            _LOGGER.info(
                "Experiment control bootstrap +%.1f ms: finalizing with %d step(s)",
                (perf_counter() - self._bootstrap_t0) * 1000.0,
                len(steps),
            )
            self._experiment_control_steps_cache = deepcopy(steps)
            if steps:
                selected_row = self._experiment_control_bootstrap_pending_selected_row
                if isinstance(selected_row, int) and 0 <= selected_row < len(steps):
                    self._select_experiment_control_plan_row(selected_row)
                elif self.plan_table.rowCount() > 0:
                    self._select_experiment_control_plan_row(0)
                self.timeline_widget.set_steps(
                    steps,
                    self._experiment_control_timeline_row(),
                    self._timeline_progress_for_display(),
                    self._plan_runtime_for_display(),
                )
                if self.plan_table.rowCount() > 0:
                    self._load_selected_step_into_editor()
                self._experiment_control_edit_controller.sync_overlay()
            self._restore_plan_table_column_widths(state)
            self._fit_plan_table_columns_to_viewport()
            self._update_plan_table_height()
            saved_splitter_sizes = state.get("experiment_control_editor_splitter_sizes")
            if saved_splitter_sizes is None:
                saved_splitter_sizes = state.get("flow_editor_splitter_sizes")
            if isinstance(saved_splitter_sizes, list) and not self._experiment_control_view_mode_sizes:
                self._apply_experiment_control_editor_splitter_sizes(saved_splitter_sizes)
            QTimer.singleShot(0, self._apply_pending_experiment_control_view_mode)
            top_stack = getattr(self.window(), "_top_content_stack", None)
            if top_stack is not None and top_stack.currentWidget() is not self:
                current_mode = str(getattr(self.window(), "_top_view_mode", "spectra") or "spectra").strip().lower()
                pending_mode = str(getattr(self.window(), "_pending_top_view_mode", current_mode) or current_mode).strip().lower()
                if pending_mode == "experimental_control" or current_mode == "experimental_control":
                    QTimer.singleShot(0, lambda stack=top_stack, widget=self: stack.setCurrentWidget(widget))
            self._set_status_message("Experiment control panel ready.")
        finally:
            self._experiment_control_bootstrap_pending_state = None
            self._experiment_control_bootstrap_pending_steps = []
            self._experiment_control_bootstrap_pending_row_order = []
            self._experiment_control_bootstrap_pending_selected_row = None
            self._experiment_control_bootstrap_pending_step_index = 0
            self._experiment_control_bootstrap_in_progress = False
            self._experiment_control_bootstrap_started = False
            self._set_experiment_control_bootstrap_busy(False)
            self.sync_from_lifecycle_controller()

    def _abort_experiment_control_bootstrap_population(self, message: str) -> None:
        self._experiment_plan_import_fill_timer.stop()
        self._experiment_control_bootstrap_pending_state = None
        self._experiment_control_bootstrap_pending_steps = []
        self._experiment_control_bootstrap_pending_row_order = []
        self._experiment_control_bootstrap_pending_selected_row = None
        self._experiment_control_bootstrap_pending_step_index = 0
        self._experiment_control_loaded_widget_rows.clear()
        self._experiment_control_bootstrap_in_progress = False
        self._experiment_control_bootstrap_started = False
        self._set_experiment_control_bootstrap_busy(False)
        self._show_error(f"Could not load experiment control panel:\n{message}")

    def _prioritized_experiment_control_row_order(self, row_count: int, selected_row: int | None = None) -> list[int]:
        if row_count <= 0:
            return []
        visible_start, visible_end = self._visible_experiment_control_row_range(row_count)
        priority: list[int] = []
        seen: set[int] = set()

        def add(row: int) -> None:
            if 0 <= row < row_count and row not in seen:
                seen.add(row)
                priority.append(row)

        if selected_row is not None:
            add(selected_row)
        for row in range(visible_start, visible_end + 1):
            add(row)
        for row in range(row_count):
            add(row)
        return priority

    def _visible_experiment_control_row_range(self, row_count: int | None = None) -> tuple[int, int]:
        total = self.plan_table.rowCount() if row_count is None else max(int(row_count), 0)
        if total <= 0:
            return (0, -1)
        viewport = self.plan_table.viewport()
        top = self.plan_table.rowAt(0)
        if top < 0:
            top = 0
        bottom = self.plan_table.rowAt(max(viewport.height() - 1, 0))
        if bottom < 0:
            bottom = min(total - 1, top + 18)
        buffer = 8
        start = max(top - buffer, 0)
        end = min(bottom + buffer, total - 1)
        if end < start:
            end = start
        return (start, end)

    def _schedule_visible_experiment_control_rows_load(self) -> None:
        if self._experiment_control_visible_rows_timer.isActive():
            return
        self._experiment_control_visible_rows_timer.start()

    def _load_visible_experiment_control_rows(self, force: bool = False) -> None:
        self._run_gui_callback_timed("experiment_control_visible_rows", lambda: None)

    def _restore_ui_state(self) -> None:
        state = self._ui_state
        width = state.get("width")
        height = state.get("height")
        x_pos = state.get("x")
        y_pos = state.get("y")
        maximized = state.get("maximized")

        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            self.resize(width, height)
        if isinstance(x_pos, int) and isinstance(y_pos, int):
            # Check if position is within available screen geometry
            app = QApplication.instance()
            if app:
                screen_geometry = app.primaryScreen().availableGeometry()
                window_width = width if isinstance(width, int) and width > 0 else self.width()
                window_height = height if isinstance(height, int) and height > 0 else self.height()
                window_rect = QRect(x_pos, y_pos, window_width, window_height)
                if not screen_geometry.contains(x_pos, y_pos) or not screen_geometry.intersects(window_rect):
                    # Position is off-screen, use default position
                    x_pos = max(100, screen_geometry.left())
                    y_pos = max(100, screen_geometry.top())
            self.move(x_pos, y_pos)
        self._start_maximized = bool(maximized)

    def save_ui_state(self) -> None:
        def _callback() -> None:
            if self.isMaximized():
                geometry = self.normalGeometry()
                width = geometry.width()
                height = geometry.height()
                x_pos = geometry.x()
                y_pos = geometry.y()
            else:
                width = self.width()
                height = self.height()
                x_pos = self.x()
                y_pos = self.y()

            self._remember_experiment_control_view_mode_sizes()
            self._remember_experiment_control_view_mode_panel_sizes()

            save_window_ui_state(
                "experiment_control_window",
                {
                    "x": int(x_pos),
                    "y": int(y_pos),
                    "width": int(width),
                    "height": int(height),
                    "maximized": bool(self.isMaximized()),
                    "time_unit_mode": self._time_unit_mode,
                    "selected_plan_row": self._selected_experiment_control_row(),
                    "plan_steps": self._serialize_experiment_control_steps(self._read_experiment_control_steps()),
                    "color_palette_entries": [
                        {"name": name, "color": color}
                        for name, color in self._color_palette_entries
                    ],
                    "custom_plan_colors": list(self._custom_plan_colors),
                    "tube_mm_values": self._tube_mm_values(),
                    "manual_uniform": self.manual_uniform_button.isChecked(),
                    "show_plan_details": self._show_plan_details,
                    "pump_display_enabled": self._pump_display_enabled,
                    "pump_display_highlight_enabled": self._pump_display_highlight_enabled,
                    "experiment_control_view_mode": self._experiment_control_view_mode,
                    "timeline_label_mode": self._experiment_control_timeline_label_mode,
                    "experiment_control_view_mode_sizes": dict(self._experiment_control_view_mode_sizes),
                    "experiment_control_view_mode_panel_sizes": dict(self._experiment_control_view_mode_panel_sizes),
                    "plan_table_column_widths": self._plan_table_column_widths(),
                    "plan_table_header_state": self._plan_table_header_state(),
                    "experiment_control_editor_splitter_sizes": self._experiment_control_editor_splitter_sizes(),
                    "flow_editor_splitter_sizes": self._experiment_control_editor_splitter_sizes(),
                    "editor_duration_s": self._editor_duration_seconds,
                    "editor_color": self.step_color_combo.currentData(),
                    "editor_valve": self.step_valve_button.property("valve"),
                    "editor_switch_position": self._current_switch_position_from_editor(),
                    "editor_comment": self.step_comment_edit.text(),
                    "valve_state_labels": dict(self._valve_state_labels),
                    "valve_state_colors": dict(self._valve_state_colors),
                    "switch_solution_mode": self._switch_solution_mode,
                    "wait_for_mswitch_first": self._wait_for_mswitch_first,
                    "switch_solution_labels": list(self._switch_solution_labels),
                    "pause_state_step": self._serialize_experiment_control_pause_template(),
                    "pause_state_dialog_state": dict(getattr(self, "_pause_state_dialog_state", {})),
                    "editor_channels": [
                        {
                            "flow_ul_min": self.manual_flow_spins[index].value(),
                            "direction": self._direction_button_value(self.manual_direction_buttons[index]),
                        }
                        for index in range(ACTIVE_PUMP_CHANNELS)
                    ],
                },
            )

        self._run_gui_callback_timed("experiment_control_save_ui_state", _callback)

    def showEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        self.setUpdatesEnabled(False)
        try:
            if self._experiment_control_view_mode_apply_pending:
                self._experiment_control_view_mode_apply_pending = False
                self._apply_experiment_control_view_mode()
            super().showEvent(event)
        finally:
            self.setUpdatesEnabled(True)
        if self._start_maximized and self.isWindow():
            self.showMaximized()
            self._start_maximized = False
        if self._plan_table_initial_fit_pending:
            self._plan_table_initial_fit_pending = False
            QTimer.singleShot(0, self._fit_plan_table_columns_to_viewport)
        QTimer.singleShot(0, self._apply_experiment_control_parent_splitter_sizes)

    def closeEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        _LOGGER.info("Experiment control window closed.")
        self.save_ui_state()
        self._device_comm_service.disconnect_device(self._device_label_for("pump"))
        self._device_comm_service.disconnect_device(self._device_label_for(SWITCH))
        self._valve_probe = None
        self.availability_changed.emit(None)
        self.valve_availability_changed.emit(None)
        self._device_comm_service.disconnect_device(self._device_label_for(SELECTOR))
        self._mswitch_probe = None
        self.mswitch_availability_changed.emit(None)
        super().closeEvent(event)


FlowControlTableView = ExperimentControlTableView
FlowControlWindow = ExperimentControlWindow
