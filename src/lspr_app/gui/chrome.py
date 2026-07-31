from __future__ import annotations

from PyQt6.QtGui import QActionGroup, QKeySequence
from PyQt6.QtWidgets import QMenuBar, QSizePolicy


def build_menu_bar(window) -> QMenuBar:
    menu_bar = QMenuBar()
    menu_bar.setNativeMenuBar(False)
    menu_bar.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)

    # ── File ─────────────────────────────────────────────────────────────────
    file_menu = menu_bar.addMenu("File")

    new_session_action = file_menu.addAction("New Session…")
    new_session_action.setToolTip(
        "Start a new session: clears the current sample/absorbance trace and starts "
        "a new measurement file, with the option to keep the dark/reference spectra."
    )
    new_session_action.setShortcut(QKeySequence(QKeySequence.StandardKey.New))
    new_session_action.triggered.connect(window._show_new_session_dialog)

    file_menu.addSeparator()

    preferences_action = file_menu.addAction("Preferences…")
    preferences_action.setToolTip("Open the application preferences dialog.")
    preferences_action.triggered.connect(window._show_preferences_dialog)

    file_menu.addSeparator()

    import_menu = file_menu.addMenu("Import")
    import_menu.setToolTip("Import sections from a previous session's HDF5 measurement file.")

    latest_session_action = import_menu.addAction("Latest Session…")
    latest_session_action.setToolTip(
        "Reopen the import checklist for the most recent session file, without a file picker."
    )
    latest_session_action.triggered.connect(window._show_import_latest_session_dialog)

    custom_import_action = import_menu.addAction("Custom…")
    custom_import_action.setToolTip(
        "Open an HDF5 measurement file and choose which sections to import:\n"
        "processing settings, experiment plan, dark spectrum, or reference spectrum."
    )
    custom_import_action.triggered.connect(window._show_import_from_measurement_dialog)

    open_output_action = file_menu.addAction("Open output folder")
    open_output_action.setToolTip("Open the current measurement output folder in the file browser.")
    open_output_action.triggered.connect(window._open_recording_project_destination_in_explorer)

    open_settings_folder_action = file_menu.addAction("Open settings folder")
    open_settings_folder_action.setToolTip(
        "Open the application settings and log folder in the file browser."
    )
    open_settings_folder_action.triggered.connect(window._open_app_config_folder)

    save_session_copy_action = file_menu.addAction("Save session copy…")
    save_session_copy_action.setToolTip("Save a copy of the current session's data to a new file.")
    save_session_copy_action.triggered.connect(window._save_session_copy_as)

    file_menu.addSeparator()

    exit_action = file_menu.addAction("Exit")
    exit_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Quit))
    exit_action.triggered.connect(window.close)

    # ── Edit ─────────────────────────────────────────────────────────────────
    edit_menu = menu_bar.addMenu("Edit")
    undo_action = window.undo_stack.createUndoAction(menu_bar, "Undo")
    undo_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Undo))
    edit_menu.addAction(undo_action)
    redo_action = window.undo_stack.createRedoAction(menu_bar, "Redo")
    redo_action.setShortcut(QKeySequence(QKeySequence.StandardKey.Redo))
    edit_menu.addAction(redo_action)
    window._undo_action = undo_action
    window._redo_action = redo_action

    # ── View ──────────────────────────────────────────────────────────────────
    view_menu = menu_bar.addMenu("View")
    view_menu_actions = {"top_view": {}}
    top_view_group = QActionGroup(menu_bar)
    top_view_group.setExclusive(True)

    presets_menu = view_menu.addMenu("Presets")
    layout_preset_group = QActionGroup(menu_bar)
    layout_preset_group.setExclusive(True)
    preset_actions = {}
    for preset_key, label, callback in (
        ("spectra", "Spectra", window._apply_layout_preset),
        ("control", "Control", window._apply_layout_preset),
        ("measurement", "Measurement", window._apply_layout_preset),
    ):
        action = presets_menu.addAction(label)
        action.setCheckable(True)
        action.setActionGroup(layout_preset_group)
        action.triggered.connect(lambda _checked=False, key=preset_key, cb=callback: cb(key))
        preset_actions[preset_key] = action
    view_menu_actions["layout_presets"] = preset_actions
    presets_menu.addSeparator()
    save_preset_action = presets_menu.addAction("Save preset")
    save_preset_action.setToolTip("Save the current layout and panel visibility to the selected preset.")
    save_preset_action.triggered.connect(window._save_current_layout_to_preset)
    reset_preset_action = presets_menu.addAction("Reset default presets")
    reset_preset_action.setToolTip("Restore the built-in preset layouts.")
    reset_preset_action.triggered.connect(window._reset_layout_presets_to_defaults)

    view_menu.addSeparator()

    spectra_action = view_menu.addAction("Spectra")
    spectra_action.setCheckable(True)
    spectra_action.setActionGroup(top_view_group)
    spectra_action.setChecked(True)
    spectra_action.triggered.connect(lambda _checked=False: window._activate_spectra_view())
    view_menu_actions["top_view"]["spectra"] = spectra_action

    experimental_control_action = view_menu.addAction("Experimental control")
    experimental_control_action.setCheckable(True)
    experimental_control_action.setActionGroup(top_view_group)
    experimental_control_action.triggered.connect(lambda _checked=False: window._activate_experimental_control_view())
    view_menu_actions["top_view"]["experimental_control"] = experimental_control_action

    left_controls_action = view_menu.addAction("Tool panel")
    left_controls_action.setCheckable(True)
    left_controls_action.setChecked(True)
    left_controls_action.triggered.connect(window._toggle_left_controls)
    view_menu_actions["left_controls"] = left_controls_action

    sensorgram_action = view_menu.addAction("Sensorgram")
    sensorgram_action.setCheckable(True)
    sensorgram_action.setChecked(True)
    sensorgram_action.triggered.connect(window._toggle_sensorgram)
    view_menu_actions["sensorgram"] = sensorgram_action

    diagnostics_panel_action = view_menu.addAction("Diagnostic panel")
    diagnostics_panel_action.setCheckable(True)
    diagnostics_panel_action.setChecked(bool(getattr(window, "_diagnostics_panel_enabled", False)))
    diagnostics_panel_action.setToolTip(
        "Show or hide the log and diagnostics panel on the left side of the window."
    )
    diagnostics_panel_action.toggled.connect(window._toggle_diagnostics_panel)
    window._diagnostics_panel_action = diagnostics_panel_action

    view_menu.addSeparator()

    always_on_top_action = view_menu.addAction("Always on top")
    always_on_top_action.setCheckable(True)
    always_on_top_action.setChecked(bool(getattr(window, "_always_on_top", False)))
    always_on_top_action.setToolTip("Keep the application window above all other windows.")
    always_on_top_action.toggled.connect(lambda checked: window._set_always_on_top(checked))
    window._always_on_top_action = always_on_top_action

    view_menu_actions["always_on_top"] = always_on_top_action
    window._view_menu_actions = view_menu_actions

    # ── Hardware ──────────────────────────────────────────────────────────────
    hw_menu = menu_bar.addMenu("Hardware")

    device_manager_action = hw_menu.addAction("Device Manager")
    device_manager_action.setToolTip("View connection status, manage device profiles, run diagnostics, and send commands.")
    device_manager_action.triggered.connect(window._show_device_manager_dialog)

    hw_init_action = hw_menu.addAction("Reinitialize hardware")
    hw_init_action.setToolTip("Disconnect all devices, then scan and reconnect all available hardware.")
    hw_init_action.triggered.connect(window._start_hardware_initialization)

    hw_disconnect_all_action = hw_menu.addAction("Disconnect all")
    hw_disconnect_all_action.setToolTip("Stop the active devices and release all app-owned hardware connections.")
    hw_disconnect_all_action.triggered.connect(window._disconnect_all_devices)
    window._hw_disconnect_all_action = hw_disconnect_all_action

    # ── Help ──────────────────────────────────────────────────────────────────
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

    help_menu.addSeparator()

    about_action = help_menu.addAction("About")
    about_action.setToolTip("Show application information.")
    about_action.triggered.connect(window._show_about_dialog)

    return menu_bar
