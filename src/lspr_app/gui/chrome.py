from __future__ import annotations

from PyQt6.QtGui import QActionGroup, QIcon, QKeySequence
from PyQt6.QtWidgets import QMenuBar, QSizePolicy


def build_menu_bar(window) -> QMenuBar:
    menu_bar = QMenuBar()
    menu_bar.setNativeMenuBar(False)
    menu_bar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

    file_menu = menu_bar.addMenu("File")
    save_processing_action = file_menu.addAction("Save processing settings")
    save_processing_action.triggered.connect(window._save_processing_settings_dialog)

    load_processing_action = file_menu.addAction("Load processing settings")
    load_processing_action.triggered.connect(window._load_processing_settings_dialog)

    file_menu.addSeparator()
    export_action = file_menu.addAction("Export current plot")
    export_action.triggered.connect(window._export_current_plot)

    file_menu.addSeparator()
    exit_action = file_menu.addAction("Exit")
    exit_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Quit))
    exit_action.triggered.connect(window.close)

    view_menu = menu_bar.addMenu("View")
    view_menu_actions = {"top_view": {}}
    top_view_group = QActionGroup(menu_bar)
    top_view_group.setExclusive(True)

    spectra_action = view_menu.addAction("Spectra")
    spectra_action.setCheckable(True)
    spectra_action.setActionGroup(top_view_group)
    spectra_action.setChecked(True)
    spectra_action.triggered.connect(lambda _checked=False: window._activate_spectra_view())
    view_menu_actions["top_view"]["spectra"] = spectra_action

    flow_action = view_menu.addAction("Experimental control")
    flow_action.setCheckable(True)
    flow_action.setActionGroup(top_view_group)
    flow_action.triggered.connect(lambda _checked=False: window._activate_flow_view())
    view_menu_actions["top_view"]["flow"] = flow_action

    left_controls_action = view_menu.addAction("Left controls")
    left_controls_action.setCheckable(True)
    left_controls_action.setChecked(True)
    left_controls_action.triggered.connect(window._toggle_left_controls)
    view_menu_actions["left_controls"] = left_controls_action

    sensorgram_action = view_menu.addAction("Sensorgram")
    sensorgram_action.setCheckable(True)
    sensorgram_action.setChecked(True)
    sensorgram_action.triggered.connect(window._toggle_sensorgram)
    view_menu_actions["sensorgram"] = sensorgram_action

    view_menu.addSeparator()
    refresh_action = view_menu.addAction("Refresh plots")
    refresh_action.triggered.connect(window._refresh_plot)

    view_menu.addSeparator()
    spectra_preset_action = view_menu.addAction("Spectra preset")
    spectra_preset_action.triggered.connect(window._activate_spectra_view)
    flow_preset_action = view_menu.addAction("Experimental control preset")
    flow_preset_action.triggered.connect(window._activate_flow_view)
    window._view_menu_actions = view_menu_actions

    hw_menu = menu_bar.addMenu("HW")
    hw_init_action = hw_menu.addAction("Initialize devices")
    hw_init_action.setToolTip("Scan the connected spectrometer and pump controller.")
    hw_init_action.triggered.connect(window._start_hardware_initialization)

    hw_menu.addSeparator()
    device_settings_menu = hw_menu.addMenu("Device settings")
    device_settings_placeholder = device_settings_menu.addAction("Reserved for device-specific settings")
    device_settings_placeholder.setEnabled(False)

    help_menu = menu_bar.addMenu("Help")

    quick_help_action = help_menu.addAction("Quick help")
    quick_help_action.setToolTip("Show a short guide for the condensed status readouts.")
    quick_help_action.triggered.connect(window._show_quick_help_dialog)
    quick_help_action.setShortcut("F1")

    shortcuts_action = help_menu.addAction("Shortcuts")
    shortcuts_action.setToolTip("Show all keyboard shortcuts and panel tips.")
    shortcuts_action.triggered.connect(window._show_shortcuts_dialog)
    shortcuts_action.setShortcut("Ctrl+/")

    legend_action = help_menu.addAction("Diagnostics legend")
    legend_action.setToolTip("Show a short legend for the status and telemetry labels.")
    legend_action.triggered.connect(window._show_diagnostics_legend_dialog)

    debug_mode_action = help_menu.addAction("Debug mode")
    debug_mode_action.setCheckable(True)
    debug_mode_action.setChecked(bool(getattr(window, "_processing_debug_mode_enabled", False)))
    debug_mode_action.setToolTip("Enable slow-spectrum profiling and other developer diagnostics.")
    debug_mode_action.toggled.connect(window._set_processing_debug_mode_enabled)

    help_menu.addSeparator()
    about_action = help_menu.addAction("About")
    about_action.setToolTip("Show application information.")
    about_action.triggered.connect(window._show_about_dialog)

    return menu_bar
