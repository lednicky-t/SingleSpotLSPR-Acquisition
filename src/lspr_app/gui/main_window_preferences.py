from __future__ import annotations

import logging

from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QListWidget,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lspr_app.gui.undo_support import DEFAULT_UNDO_HISTORY_SIZE
from lspr_app.storage import user_profile

_GUI_LOG_LEVELS = [
    ("Errors only", logging.ERROR),
    ("Warnings+", logging.WARNING),
    ("Info+", logging.INFO),
    ("Debug", logging.DEBUG),
]

# The app's global stylesheet deliberately hides native QGroupBox titles
# (color: transparent) elsewhere, since most of the app pairs QGroupBox
# with its own CollapsibleSection header widget instead. This dialog has
# no such header, so its section titles would otherwise render as
# invisible text - this instance-level stylesheet overrides that (Qt's
# cascade lets a widget's own stylesheet win over an ancestor/application
# one) just for these boxes, without touching the shared app-wide style.
_SECTION_BOX_STYLE = (
    "QGroupBox {"
    " border: 1px solid rgba(255, 255, 255, 0.10);"
    " border-radius: 6px;"
    " margin-top: 8px;"
    " padding-top: 14px;"
    "}"
    "QGroupBox::title {"
    " subcontrol-origin: margin;"
    " subcontrol-position: top left;"
    " left: 9px;"
    " padding: 0px 4px;"
    " color: #9fb3c5;"
    " font-weight: 700;"
    "}"
)


def _make_section_box(title: str) -> QGroupBox:
    box = QGroupBox(title)
    box.setStyleSheet(_SECTION_BOX_STYLE)
    return box


class PreferencesDialog(QDialog):
    def __init__(self, window, parent: QWidget | None = None) -> None:
        super().__init__(parent or window)
        self._window = window
        self.setWindowTitle("Preferences")
        self.setModal(True)
        self.setMinimumWidth(420)

        # Appearance
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.addItem("Light", "light")

        # Users - who's using this instrument (see storage/user_profile.py).
        # No password: this list is bookkeeping for settings isolation and
        # HDF5 traceability, not access control.
        self.user_list = QListWidget()
        self.user_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.user_list.setToolTip("Known users, picked from the User field next to the recording destination.")
        self.switch_user_button = QPushButton("Switch to")
        self.switch_user_button.setToolTip(
            "Make the selected name the active user. Refused while a measurement is actively "
            "recording - stop it first."
        )
        self.switch_user_button.clicked.connect(self._switch_to_selected_user)
        self.remove_user_button = QPushButton("Remove")
        self.remove_user_button.setToolTip(
            "Remove the selected name from the look-up list. Their saved settings are kept on "
            "disk, not deleted - only removed from this list. The active user can't be removed."
        )
        self.remove_user_button.clicked.connect(self._remove_selected_user)
        self.user_list.itemSelectionChanged.connect(self._update_user_buttons_enabled)

        # Startup
        self.start_maximized_check = QCheckBox("Start maximized")
        self.start_maximized_check.setToolTip(
            "Open the application window in maximized state on every launch."
        )
        self.freeze_on_startup_check = QCheckBox("Start with plots frozen")
        self.freeze_on_startup_check.setToolTip(
            "When enabled, the processed spectra plot starts frozen each time the application opens.\n"
            "Freeze can always be toggled manually using the snowflake button in the toolbar."
        )
        self.confirm_exit_check = QCheckBox("Confirm exit when recording is active")
        self.confirm_exit_check.setToolTip(
            "Show a confirmation dialog before closing the application while a measurement recording is running."
        )

        # Acquisition & storage
        self.hdf5_compression_check = QCheckBox("Measurement HDF5 compression")
        self.hdf5_compression_check.setToolTip(
            "Enable gzip compression for measurement HDF5 files. "
            "Reduces file size at the cost of slightly higher CPU load during recording."
        )
        self.hdf5_flush_interval_spin = QDoubleSpinBox()
        self.hdf5_flush_interval_spin.setRange(0.25, 60.0)
        self.hdf5_flush_interval_spin.setSingleStep(0.5)
        self.hdf5_flush_interval_spin.setDecimals(2)
        self.hdf5_flush_interval_spin.setSuffix(" s")
        self.hdf5_flush_interval_spin.setToolTip(
            "How often the HDF5 measurement file is flushed to disk during acquisition.\n"
            "Lower values reduce data loss on crash but increase I/O load. Range: 0.25 – 60 s."
        )
        self.default_live_rate_spin = QDoubleSpinBox()
        self.default_live_rate_spin.setRange(0.1, 200.0)
        self.default_live_rate_spin.setSingleStep(0.5)
        self.default_live_rate_spin.setDecimals(2)
        self.default_live_rate_spin.setSuffix(" Hz")
        self.default_live_rate_spin.setToolTip(
            "Default GUI refresh rate for live spectra and sensorgram updates.\n"
            "This value is applied on first launch and when no saved session state exists."
        )

        # Performance
        self.ui_state_autosave_check = QCheckBox("UI state autosave")
        self.ui_state_autosave_check.setToolTip(
            "Automatically persist panel visibility, splitter positions, and other UI state to disk."
        )
        self.acquisition_state_autosave_check = QCheckBox("Acquisition state autosave")
        self.acquisition_state_autosave_check.setToolTip(
            "Automatically save acquisition state (processing settings, source mode, etc.) during changes."
        )
        self.log_buffering_check = QCheckBox("Log buffering")
        self.log_buffering_check.setToolTip(
            "Batch log writes before rendering them in the log panel. "
            "Disable to see log entries immediately at the cost of more frequent repaints."
        )
        self.gui_housekeeping_check = QCheckBox("GUI housekeeping")
        self.gui_housekeeping_check.setToolTip(
            "Enable deferred GUI maintenance tasks such as log flushing and state saves. "
            "Turn off to isolate housekeeping overhead during profiling."
        )
        self.metric_plot_check = QCheckBox("Metric plot")
        self.metric_plot_check.setToolTip(
            "Enable the sensorgram metric line plot. "
            "Disable to reduce rendering load when the sensorgram is not needed."
        )
        self.undo_history_size_spin = QSpinBox()
        self.undo_history_size_spin.setRange(1, 1000)
        self.undo_history_size_spin.setToolTip(
            "How many Ctrl+Z steps are kept in the undo history (experiment plan edits, "
            "and acquisition/processing setting changes). Takes effect on the next change; "
            "existing history isn't trimmed retroactively."
        )

        # Developer
        self.processing_debug_check = QCheckBox("Processing debug mode")
        self.processing_debug_check.setToolTip(
            "Enable slow-spectrum profiling and extended processing diagnostics.\n"
            "Intended for development and performance investigation."
        )
        self.gui_log_level_combo = QComboBox()
        for label, level in _GUI_LOG_LEVELS:
            self.gui_log_level_combo.addItem(label, level)
        self.gui_log_level_combo.setToolTip(
            "Minimum severity level shown in the in-app log panel.\n"
            "Lower levels show more detail but may increase log volume."
        )

        self._build_ui()
        self._load_from_window()
        self._fit_to_available_screen()

    def _build_ui(self) -> None:
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        # Sections go in a scroll area so a tall dialog (six bordered
        # sections, more as they're added) never pushes the OK/Cancel/
        # Apply row off the bottom of the screen - previously the whole
        # dialog just grew to fit its content, and on a screen shorter than
        # that content the button row ended up behind the Windows taskbar
        # with no way to reach it. See _fit_to_available_screen() for the
        # matching cap on the dialog's own height.
        scroll_area = QScrollArea(self)
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget(scroll_area)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # ── Appearance ────────────────────────────────────────────────────────
        appearance_box = _make_section_box("Appearance")
        appearance_layout = QFormLayout(appearance_box)
        appearance_layout.setHorizontalSpacing(16)
        appearance_layout.setVerticalSpacing(8)
        appearance_layout.addRow("Theme", self.theme_combo)

        # ── Users ─────────────────────────────────────────────────────────────
        users_box = _make_section_box("Users")
        users_layout = QVBoxLayout(users_box)
        users_layout.setSpacing(6)
        users_layout.addWidget(self.user_list)
        user_button_row = QHBoxLayout()
        user_button_row.addStretch(1)
        user_button_row.addWidget(self.switch_user_button)
        user_button_row.addWidget(self.remove_user_button)
        users_layout.addLayout(user_button_row)

        # ── Startup ───────────────────────────────────────────────────────────
        startup_box = _make_section_box("Startup")
        startup_layout = QFormLayout(startup_box)
        startup_layout.setHorizontalSpacing(16)
        startup_layout.setVerticalSpacing(8)
        startup_layout.addRow(self.start_maximized_check)
        startup_layout.addRow(self.freeze_on_startup_check)
        startup_layout.addRow(self.confirm_exit_check)

        # ── Acquisition & storage ─────────────────────────────────────────────
        acquisition_box = _make_section_box("Acquisition && storage")
        acquisition_layout = QFormLayout(acquisition_box)
        acquisition_layout.setHorizontalSpacing(16)
        acquisition_layout.setVerticalSpacing(8)
        acquisition_layout.addRow(self.hdf5_compression_check)
        acquisition_layout.addRow("HDF5 flush interval", self.hdf5_flush_interval_spin)
        acquisition_layout.addRow("Default live rate", self.default_live_rate_spin)

        # ── Performance ───────────────────────────────────────────────────────
        performance_box = _make_section_box("Performance")
        performance_layout = QFormLayout(performance_box)
        performance_layout.setHorizontalSpacing(16)
        performance_layout.setVerticalSpacing(8)
        performance_layout.addRow(self.ui_state_autosave_check)
        performance_layout.addRow(self.acquisition_state_autosave_check)
        performance_layout.addRow(self.log_buffering_check)
        performance_layout.addRow(self.gui_housekeeping_check)
        performance_layout.addRow(self.metric_plot_check)
        performance_layout.addRow("Undo history size", self.undo_history_size_spin)

        # ── Developer ─────────────────────────────────────────────────────────
        developer_box = _make_section_box("Developer")
        developer_layout = QFormLayout(developer_box)
        developer_layout.setHorizontalSpacing(16)
        developer_layout.setVerticalSpacing(8)
        developer_layout.addRow(self.processing_debug_check)
        developer_layout.addRow("Log panel level", self.gui_log_level_combo)

        layout.addWidget(appearance_box)
        layout.addWidget(users_box)
        layout.addWidget(startup_box)
        layout.addWidget(acquisition_box)
        layout.addWidget(performance_box)
        layout.addWidget(developer_box)

        scroll_area.setWidget(content)
        outer_layout.addWidget(scroll_area, 1)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Apply
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self._on_ok)
        button_box.rejected.connect(self.reject)
        apply_btn = button_box.button(QDialogButtonBox.StandardButton.Apply)
        if apply_btn is not None:
            apply_btn.clicked.connect(self.apply_changes)
        button_row = QWidget(self)
        button_row.setObjectName("preferencesButtonRow")
        button_row_layout = QVBoxLayout(button_row)
        button_row_layout.setContentsMargins(16, 10, 16, 16)
        button_row_layout.addWidget(button_box)
        outer_layout.addWidget(button_row, 0)

    def _fit_to_available_screen(self) -> None:
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        # Leave headroom for the taskbar/other chrome rather than filling
        # the whole usable screen - matches how the main window's own
        # startup sizing (fit_window_to_available_screen_for) leaves margin.
        max_height = max(int(available.height() * 0.85), 320)
        target_height = min(self.sizeHint().height(), max_height)
        self.resize(self.width(), target_height)

    def _on_ok(self) -> None:
        self.apply_changes()
        self.accept()

    def _refresh_user_list(self) -> None:
        active = user_profile.active_user()
        self.user_list.clear()
        for name in user_profile.list_known_users():
            self.user_list.addItem(f"{name} (active)" if name == active else name)
        self._update_user_buttons_enabled()

    def _selected_user_name(self) -> str | None:
        item = self.user_list.currentItem()
        return item.text().removesuffix(" (active)") if item is not None else None

    def _update_user_buttons_enabled(self) -> None:
        name = self._selected_user_name()
        is_active = name is not None and name == user_profile.active_user()
        self.switch_user_button.setEnabled(name is not None and not is_active)
        self.remove_user_button.setEnabled(name is not None and not is_active)

    def _switch_to_selected_user(self) -> None:
        name = self._selected_user_name()
        if name is None or name == user_profile.active_user():
            return
        switched = (
            self._window._switch_active_user(name)
            if hasattr(self._window, "_switch_active_user")
            else False
        )
        if not switched:
            QMessageBox.information(
                self, "Switch user", "Stop the current recording before switching users."
            )
            return
        self._refresh_user_list()

    def _remove_selected_user(self) -> None:
        name = self._selected_user_name()
        if name is None:
            return
        if name == user_profile.active_user():
            QMessageBox.information(
                self, "Remove user", "Switch to a different user before removing the active one."
            )
            return
        user_profile.remove_known_user(name)
        self._refresh_user_list()
        if hasattr(self._window, "_refresh_user_combo"):
            self._window._refresh_user_combo()

    def _load_from_window(self) -> None:
        self._refresh_user_list()

        theme = str(getattr(self._window, "_theme_mode", "dark"))
        index = self.theme_combo.findData(theme)
        if index >= 0:
            self.theme_combo.setCurrentIndex(index)

        self.start_maximized_check.setChecked(bool(getattr(self._window, "_start_maximized", False)))
        self.freeze_on_startup_check.setChecked(bool(getattr(self._window, "_startup_freeze_plots", False)))
        self.confirm_exit_check.setChecked(bool(getattr(self._window, "_confirm_exit_if_recording", True)))

        self.hdf5_compression_check.setChecked(bool(getattr(self._window, "_hdf5_compression_enabled", True)))
        self.hdf5_flush_interval_spin.setValue(float(getattr(self._window, "_measurement_flush_interval_s", 1.0)))
        self.default_live_rate_spin.setValue(float(getattr(self._window, "_default_live_rate_hz", 4.0)))

        self.ui_state_autosave_check.setChecked(bool(getattr(self._window, "_ui_state_autosave_enabled", True)))
        self.acquisition_state_autosave_check.setChecked(bool(getattr(self._window, "_acquisition_state_autosave_enabled", True)))
        self.log_buffering_check.setChecked(bool(getattr(self._window, "_log_buffering_enabled", True)))
        self.gui_housekeeping_check.setChecked(bool(getattr(self._window, "_gui_housekeeping_enabled", True)))
        self.metric_plot_check.setChecked(bool(getattr(self._window, "_metric_plot_enabled", True)))
        undo_stack = getattr(self._window, "undo_stack", None)
        current_limit = undo_stack.undoLimit() if undo_stack is not None else 0
        self.undo_history_size_spin.setValue(current_limit if current_limit > 0 else DEFAULT_UNDO_HISTORY_SIZE)

        self.processing_debug_check.setChecked(bool(getattr(self._window, "_processing_debug_mode_enabled", False)))

        current_level = int(getattr(self._window, "_gui_log_min_level", logging.WARNING))
        best_index = 0
        best_delta = float("inf")
        for i, (_, level) in enumerate(_GUI_LOG_LEVELS):
            delta = abs(level - current_level)
            if delta < best_delta:
                best_delta = delta
                best_index = i
        self.gui_log_level_combo.setCurrentIndex(best_index)

    def apply_changes(self) -> None:
        theme = str(self.theme_combo.currentData() or "dark")
        if hasattr(self._window, "set_theme"):
            self._window.set_theme(theme)

        if hasattr(self._window, "_set_start_maximized"):
            self._window._set_start_maximized(self.start_maximized_check.isChecked())
        if hasattr(self._window, "_set_startup_freeze_plots_enabled"):
            self._window._set_startup_freeze_plots_enabled(self.freeze_on_startup_check.isChecked())
        if hasattr(self._window, "_set_confirm_exit_if_recording"):
            self._window._set_confirm_exit_if_recording(self.confirm_exit_check.isChecked())

        if hasattr(self._window, "_set_measurement_hdf5_compression_enabled"):
            self._window._set_measurement_hdf5_compression_enabled(self.hdf5_compression_check.isChecked())
        if hasattr(self._window, "_set_measurement_hdf5_flush_interval_s"):
            self._window._set_measurement_hdf5_flush_interval_s(self.hdf5_flush_interval_spin.value())
        if hasattr(self._window, "_set_default_live_rate_hz"):
            self._window._set_default_live_rate_hz(self.default_live_rate_spin.value())

        if hasattr(self._window, "_set_ui_state_autosave_enabled"):
            self._window._set_ui_state_autosave_enabled(self.ui_state_autosave_check.isChecked())
        if hasattr(self._window, "_set_acquisition_state_autosave_enabled"):
            self._window._set_acquisition_state_autosave_enabled(self.acquisition_state_autosave_check.isChecked())
        if hasattr(self._window, "_set_log_buffering_enabled"):
            self._window._set_log_buffering_enabled(self.log_buffering_check.isChecked())
        if hasattr(self._window, "_set_gui_housekeeping_enabled"):
            self._window._set_gui_housekeeping_enabled(self.gui_housekeeping_check.isChecked())
        if hasattr(self._window, "_set_metric_plot_enabled"):
            self._window._set_metric_plot_enabled(self.metric_plot_check.isChecked())
        if hasattr(self._window, "_set_undo_history_size"):
            self._window._set_undo_history_size(self.undo_history_size_spin.value())

        if hasattr(self._window, "_set_processing_debug_mode_enabled"):
            self._window._set_processing_debug_mode_enabled(self.processing_debug_check.isChecked())
        if hasattr(self._window, "_set_gui_log_level"):
            level = self.gui_log_level_combo.currentData()
            if isinstance(level, int):
                self._window._set_gui_log_level(level)


def show_preferences_dialog_for(window) -> None:
    dialog = PreferencesDialog(window)
    dialog.exec()
