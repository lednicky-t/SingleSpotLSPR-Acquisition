from __future__ import annotations

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLayout,
    QLabel,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lspr_app.gui.main_window_panels import build_spectra_processing_group, configure_spectra_processing_group_controls
from lspr_app.gui.icon_helpers import flow_tabler_icon, tint_tabler_icon
from lspr_app.gui.panel_help import make_help_button
from lspr_app.gui.panel_help_text import (
    SPECTRAL_SOURCE_BODY,
    SPECTRAL_SOURCE_TITLE,
    SPECTRAL_SOURCE_TOOLTIP,
    LOG_BODY,
    LOG_TITLE,
    LOG_TOOLTIP,
    SESSION_BODY,
    SESSION_TITLE,
    SESSION_TOOLTIP,
    SPECTRA_PROCESSING_BODY,
    SPECTRA_PROCESSING_TITLE,
    SPECTRA_PROCESSING_TOOLTIP,
)
from lspr_app.gui.widgets import CollapsibleSection, CompactSplitter


def build_recording_context_row_for(window) -> QWidget:
    panel = QWidget(window)
    panel.setObjectName("recordingContextPanel")
    layout = QHBoxLayout()
    layout.setContentsMargins(6, 0, 6, 0)
    layout.setSpacing(5)
    controls_row = QHBoxLayout()
    controls_row.setContentsMargins(0, 0, 0, 0)
    controls_row.setSpacing(5)
    user_label = QLabel("User")
    user_label.setToolTip("Who is using this instrument - see the field's own tooltip for details.")
    project_label = QLabel("Project's destination")
    project_label.setToolTip("Root folder for saved experiment folders.")
    experiment_label = QLabel("Experiment name")
    experiment_label.setToolTip("Folder name and filename component for the current experiment.")
    controls_row.addWidget(user_label)
    # Fixed, not stretch-sized like the fields to its right: it only ever
    # holds a short name, not a path - ~60% of the ~198px it rendered at
    # with a stretch factor of 1 alongside destination(3)/experiment(2).
    window.user_combo.setFixedWidth(119)
    controls_row.addWidget(window.user_combo)
    controls_row.addSpacing(6)
    controls_row.addWidget(project_label)
    controls_row.addWidget(window.project_destination_edit, 3)
    controls_row.addWidget(window.project_destination_browse_button)
    controls_row.addSpacing(6)
    controls_row.addWidget(experiment_label)
    controls_row.addWidget(window.experiment_name_edit, 2)
    controls_row.addSpacing(4)
    controls_row.addWidget(window.measurement_compression_button)
    layout.addLayout(controls_row)
    panel.setLayout(layout)
    panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    panel.setFixedHeight(28)
    return panel


def _make_hide_panel_button(window, tooltip: str):
    return window._make_frameless_icon_button(
        tint_tabler_icon(flow_tabler_icon("eye_off", "eye-off"), QColor("#8a98a8")),
        tooltip,
        size=22,
    )


def _add_hide_panel_button(layout, window, tooltip: str, on_click) -> None:
    """Append `addStretch(1)` + a hide-panel icon button to *layout*, wired
    to *on_click* - the one truly identical tail shared by every panel
    header row below (sensorgram/tool-panel/spectrum), even though the rest
    of each row - title stylesheet, extra buttons, tooltips - differs enough
    per panel that unifying the whole row isn't worth the parameter list it
    would take."""
    layout.addStretch(1)
    hide_button = _make_hide_panel_button(window, tooltip)
    hide_button.clicked.connect(lambda _checked=False: on_click())
    layout.addWidget(hide_button)


def build_main_layout_for(window) -> None:
    measurement_bar = QHBoxLayout()
    measurement_bar.setSpacing(4)
    measurement_bar.addStretch(1)

    spectra_processing_group = build_spectra_processing_group(window)

    source_block = QWidget(window)
    source_layout = QVBoxLayout()
    source_layout.setContentsMargins(0, 0, 0, 0)
    source_layout.setSpacing(6)
    # spacing/rate/ovh (spectrometer_stats_label) reflects acquisition timing
    # that applies equally to the Hardware and Simulation tabs - shown once,
    # here, above both tabs, rather than duplicated inside (or missing from)
    # either tab's own page. See build_spectrometer_page (main_window_panels.py),
    # which used to own this row.
    acquisition_stats_row = QWidget(window)
    acquisition_stats_layout = QHBoxLayout()
    acquisition_stats_layout.setContentsMargins(0, 0, 0, 0)
    acquisition_stats_layout.setSpacing(8)
    acquisition_stats_layout.addWidget(window.spectrometer_stats_label, 1)
    acquisition_stats_row.setLayout(acquisition_stats_layout)
    acquisition_stats_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    source_layout.addWidget(acquisition_stats_row)
    source_layout.addWidget(window.source_tabs)
    source_block.setLayout(source_layout)
    source_block.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    window.source_tabs.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # plot_selector/spectrum_y_axis_format_button/show_residual_button moved
    # into spectrum_header_layout (next to the "Spectra" title, same spot as
    # the Sensorgram title row's [Auto]/[2Y] buttons) - this row now only
    # holds dark/reference acquisition and the freeze toggle.
    plot_bar = QHBoxLayout()
    plot_bar.setSpacing(6)
    plot_bar.addWidget(window.acquire_reference_button)
    plot_bar.addWidget(window.acquire_dark_button)
    plot_bar.addWidget(window.start_tracking_button)
    plot_bar.addWidget(window.freeze_plots_button)
    plot_bar.addStretch(1)

    # spectrum_stats_label/spectrum_cursor_label used to live in a row here -
    # they're now an overlay pinned to the top-left corner of spectrum_plot
    # itself (see _build_spectrum_and_trace_plots's overlay setup), freeing
    # this vertical space.

    trace_title = QLabel("Sensorgram")
    trace_title.setObjectName("sensorgramHeaderLabel")
    trace_title.setStyleSheet("color: #8FE3A1;")
    window._update_sensorgram_metric_y_axis_mode_button()
    window.sensorgram_settings_button.clicked.connect(window._show_sensorgram_plot_settings_dialog)

    trace_title_row = QHBoxLayout()
    trace_title_row.setContentsMargins(0, 0, 0, 0)
    trace_title_row.setSpacing(6)
    trace_title_row.addWidget(trace_title)
    trace_title_row.addWidget(window.sensorgram_metric_y_axis_mode_button)
    trace_title_row.addWidget(window.show_secondary_axis_button)
    trace_title_row.addWidget(window.secondary_axis_metric_button)
    trace_title_row.addWidget(window.secondary_axis_metric_button_b)
    _add_hide_panel_button(trace_title_row, window, "Hide sensorgram.", lambda: window._toggle_sensorgram(False))
    trace_title_row_widget = QWidget(window)
    trace_title_row_widget.setLayout(trace_title_row)
    trace_title_row_widget.setContentsMargins(0, 0, 0, 0)

    trace_left_field = QHBoxLayout()
    trace_left_field.setContentsMargins(0, 0, 0, 0)
    trace_left_field.setSpacing(6)
    trace_left_field.addWidget(window.trace_record_button, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    trace_left_field.addWidget(window.sensorgram_save_copy_button, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    trace_left_field.addWidget(window.sensorgram_freeze_button, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    trace_left_field.addWidget(window.sensorgram_reload_button, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    trace_left_field.addWidget(window.clear_trace_button, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    trace_left_field.addWidget(window.sensorgram_settings_button, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    trace_left_field.addStretch(1)

    # trace_stats_label/trace_cursor_label used to live in their own rows here
    # (trace_metrics_row / a cursor column in trace_noise_cursor_row) - they're
    # now an overlay pinned to the top-left corner of trace_plot itself (see
    # _build_spectrum_and_trace_plots's overlay setup), so this side of the
    # splitter now only holds the Noise controls, freeing the rest of the space.
    trace_right_field = QVBoxLayout()
    trace_right_field.setContentsMargins(0, 0, 0, 0)
    trace_right_field.setSpacing(0)

    trace_noise_row = QHBoxLayout()
    trace_noise_row.setContentsMargins(0, 0, 0, 0)
    trace_noise_row.setSpacing(6)
    trace_noise_row.addWidget(QLabel("Noise"))
    trace_noise_row.addWidget(window.trace_noise_window_spin)
    trace_noise_row.addWidget(window.trace_noise_summary_label, 1)
    trace_noise_widget = QWidget(window)
    trace_noise_widget.setLayout(trace_noise_row)
    trace_noise_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    trace_right_field.addWidget(trace_noise_widget)
    trace_noise_widget.setFixedHeight(trace_noise_widget.sizeHint().height())

    trace_left_widget = QWidget(window)
    trace_left_widget.setLayout(trace_left_field)
    trace_left_widget.setMinimumWidth(160)
    trace_left_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    trace_right_widget = QWidget(window)
    trace_right_widget.setLayout(trace_right_field)
    trace_right_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    trace_body_splitter = CompactSplitter(Qt.Orientation.Horizontal)
    trace_body_splitter.setObjectName("sensorgramHeaderSplitter")
    trace_body_splitter.setChildrenCollapsible(False)
    trace_body_splitter.setOpaqueResize(True)
    trace_body_splitter.setHandleWidth(12)
    trace_body_splitter.addWidget(trace_left_widget)
    trace_body_splitter.addWidget(trace_right_widget)
    trace_body_splitter.setStretchFactor(0, 0)
    trace_body_splitter.setStretchFactor(1, 1)
    trace_body_splitter.setSizes([170, 610])
    trace_body_splitter.splitterMoved.connect(lambda *_: window._schedule_ui_state_persist())
    window.sensorgram_header_splitter = trace_body_splitter

    source_section = CollapsibleSection(
        "Spectral source",
        source_block,
        expanded=True,
        header_widgets=[make_help_button(SPECTRAL_SOURCE_TOOLTIP, title=SPECTRAL_SOURCE_TITLE, body=SPECTRAL_SOURCE_BODY)],
    )
    source_section.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    spectra_processing_section = CollapsibleSection(
        "Spectra Processing",
        spectra_processing_group,
        expanded=False,
        header_widgets=[
            window.save_processing_button,
            window.load_processing_button,
            make_help_button(SPECTRA_PROCESSING_TOOLTIP, title=SPECTRA_PROCESSING_TITLE, body=SPECTRA_PROCESSING_BODY),
        ],
    )
    session_top_row = QHBoxLayout()
    session_top_row.setContentsMargins(0, 0, 0, 0)
    session_top_row.setSpacing(6)
    session_rate_label = QLabel("Refresh rate")
    session_rate_label.setToolTip("GUI display refresh rate for live spectra and sensorgram updates.")
    session_top_row.addWidget(session_rate_label)
    session_top_row.addWidget(window.live_rate_spin)
    session_top_row.addStretch(1)
    session_top_row.addWidget(window.session_font_down_button)
    session_top_row.addWidget(window.session_font_up_button)
    session_top_row.addWidget(window.session_stats_snapshot_button)
    session_top_row.addWidget(window.session_stats_save_button)
    session_top_row.addWidget(window.session_stats_record_button)

    session_block = QWidget(window)
    session_layout = QVBoxLayout()
    session_layout.setContentsMargins(0, 0, 0, 0)
    session_layout.setSpacing(4)
    session_layout.addLayout(session_top_row)
    session_layout.addWidget(window.session_summary, 1)
    session_block.setLayout(session_layout)
    session_section = CollapsibleSection(
        "Session",
        session_block,
        expanded=False,
        header_widgets=[make_help_button(SESSION_TOOLTIP, title=SESSION_TITLE, body=SESSION_BODY)],
    )

    log_header_row = QHBoxLayout()
    log_header_row.setContentsMargins(0, 0, 0, 0)
    log_header_row.setSpacing(6)
    log_header_row.addWidget(window.log_view_all_button)
    log_header_row.addWidget(window.log_view_gui_button)
    log_header_row.addWidget(window.log_view_devices_button)
    log_header_row.addStretch(1)
    log_header_row.addWidget(window.log_font_down_button)
    log_header_row.addWidget(window.log_font_up_button)
    log_header_row.addWidget(window.log_follow_button)
    log_header_row.addWidget(window.log_copy_button)
    log_header_row.addWidget(window.log_clear_button)
    log_block = QWidget(window)
    log_layout = QVBoxLayout()
    log_layout.setContentsMargins(0, 0, 0, 0)
    log_layout.setSpacing(4)
    log_layout.addLayout(log_header_row)
    log_layout.addWidget(window.log_terminal)
    log_block.setLayout(log_layout)
    log_section = CollapsibleSection(
        "Log",
        log_block,
        expanded=True,
        header_widgets=[make_help_button(LOG_TOOLTIP, title=LOG_TITLE, body=LOG_BODY)],
    )
    log_section.setVisible(bool(getattr(window, "_diagnostics_panel_enabled", False)))

    window._source_section = source_section
    window._spectra_processing_section = spectra_processing_section
    window._session_section = session_section
    window._log_section = log_section
    window._restore_collapsible_section_state()
    source_section.setMinimumHeight(source_section.sizeHint().height())

    left_panel = QVBoxLayout()
    left_panel.setSpacing(6)
    left_panel.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
    tool_panel_title_row = QWidget(window)
    tool_panel_title_layout = QHBoxLayout()
    tool_panel_title_layout.setContentsMargins(0, 0, 0, 0)
    tool_panel_title_layout.setSpacing(6)
    tool_panel_title = QLabel("Tool panel")
    tool_panel_title.setObjectName("toolPanelTitleLabel")
    tool_panel_title.setStyleSheet("font-size: 13px; font-weight: 800; letter-spacing: 0.8px; color: #e0a84a;")
    tool_panel_title_layout.addWidget(tool_panel_title)
    _add_hide_panel_button(
        tool_panel_title_layout, window, "Hide left controls.", lambda: window._toggle_left_controls(False)
    )
    tool_panel_title_row.setLayout(tool_panel_title_layout)
    left_panel.addWidget(tool_panel_title_row)
    left_panel.addLayout(measurement_bar)
    left_panel.addWidget(source_section)
    left_panel.addWidget(spectra_processing_section)
    left_panel.addWidget(session_section)
    left_panel.addWidget(log_section)
    left_panel.addStretch(1)

    left_widget = QWidget(window)
    left_widget.setLayout(left_panel)
    left_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

    left_scroll = QScrollArea(window)
    left_scroll.setWidgetResizable(True)
    left_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    left_scroll.setWidget(left_widget)
    left_scroll.setMinimumWidth(250)
    left_scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    window._left_controls_scroll = left_scroll

    spectrum_block = QWidget(window)
    spectrum_layout = QVBoxLayout()
    spectrum_layout.setContentsMargins(0, 0, 0, 0)
    spectrum_layout.setSpacing(4)
    spectrum_header_row = QWidget(window)
    spectrum_header_layout = QHBoxLayout()
    spectrum_header_layout.setContentsMargins(0, 0, 0, 0)
    spectrum_header_layout.setSpacing(6)
    spectrum_header = QLabel("Spectra")
    spectrum_header.setObjectName("topContentHeaderLabel")
    spectrum_header.setStyleSheet("font-size: 13px; font-weight: 800; letter-spacing: 0.8px; color: #5b6775;")
    spectrum_header.setCursor(Qt.CursorShape.PointingHandCursor)
    spectrum_header.setToolTip("Double-click to switch to experimental control.")
    spectrum_header.installEventFilter(window)
    window._processed_spectra_header_label = spectrum_header
    spectrum_header_layout.addWidget(spectrum_header)
    spectrum_header_layout.addWidget(window.plot_selector)
    spectrum_header_layout.addWidget(window.spectrum_y_axis_format_button)
    spectrum_header_layout.addWidget(window.show_residual_button)
    _add_hide_panel_button(
        spectrum_header_layout, window, "Hide spectra.", lambda: window._activate_experiment_control_view()
    )
    spectrum_header_row.setLayout(spectrum_header_layout)
    spectrum_layout.addWidget(spectrum_header_row)
    spectrum_layout.addLayout(plot_bar)
    spectrum_layout.addWidget(window.spectrum_plot, 1)
    spectrum_block.setLayout(spectrum_layout)
    window._spectra_block = spectrum_block
    spectrum_block.installEventFilter(window)

    trace_block = QWidget(window)
    trace_layout = QVBoxLayout()
    trace_layout.setContentsMargins(0, 0, 0, 0)
    trace_layout.setSpacing(4)
    trace_layout.addWidget(trace_title_row_widget)
    trace_layout.addWidget(trace_body_splitter)
    trace_layout.addWidget(window.trace_plot, 1)
    trace_block.setLayout(trace_layout)
    window._sensorgram_block = trace_block
    trace_block.installEventFilter(window)

    measurement_top_host = QWidget(window)
    measurement_top_layout = QVBoxLayout()
    measurement_top_layout.setContentsMargins(0, 0, 0, 0)
    measurement_top_layout.setSpacing(0)
    measurement_top_host.setLayout(measurement_top_layout)
    measurement_top_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

    measurement_bottom_left_host = QWidget(window)
    measurement_bottom_left_layout = QVBoxLayout()
    measurement_bottom_left_layout.setContentsMargins(0, 0, 0, 0)
    measurement_bottom_left_layout.setSpacing(0)
    measurement_bottom_left_host.setLayout(measurement_bottom_left_layout)
    measurement_bottom_left_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    measurement_bottom_right_host = QWidget(window)
    measurement_bottom_right_layout = QVBoxLayout()
    measurement_bottom_right_layout.setContentsMargins(0, 0, 0, 0)
    measurement_bottom_right_layout.setSpacing(0)
    measurement_bottom_right_host.setLayout(measurement_bottom_right_layout)
    measurement_bottom_right_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    measurement_bottom_splitter = CompactSplitter(Qt.Orientation.Horizontal)
    measurement_bottom_splitter.setObjectName("measurementBottomSplitter")
    measurement_bottom_splitter.setChildrenCollapsible(False)
    measurement_bottom_splitter.setOpaqueResize(True)
    measurement_bottom_splitter.setHandleWidth(12)
    measurement_bottom_splitter.addWidget(measurement_bottom_left_host)
    measurement_bottom_splitter.addWidget(measurement_bottom_right_host)
    measurement_bottom_splitter.setStretchFactor(0, 1)
    measurement_bottom_splitter.setStretchFactor(1, 1)
    measurement_bottom_splitter.setSizes([520, 520])
    measurement_bottom_splitter.splitterMoved.connect(lambda *_: window._schedule_ui_state_persist())

    measurement_vertical_splitter = CompactSplitter(Qt.Orientation.Vertical)
    measurement_vertical_splitter.setObjectName("measurementVerticalSplitter")
    measurement_vertical_splitter.setChildrenCollapsible(False)
    measurement_vertical_splitter.setOpaqueResize(True)
    measurement_vertical_splitter.setHandleWidth(12)
    measurement_vertical_splitter.addWidget(measurement_top_host)
    measurement_vertical_splitter.addWidget(measurement_bottom_splitter)
    measurement_vertical_splitter.setStretchFactor(0, 0)
    measurement_vertical_splitter.setStretchFactor(1, 1)
    measurement_vertical_splitter.setSizes([360, 540])
    measurement_vertical_splitter.splitterMoved.connect(lambda *_: window._schedule_ui_state_persist())

    measurement_page = QWidget(window)
    measurement_page.setObjectName("measurementLayoutPage")
    measurement_page_layout = QVBoxLayout()
    measurement_page_layout.setContentsMargins(0, 0, 0, 0)
    measurement_page_layout.setSpacing(0)
    measurement_page_layout.addWidget(measurement_vertical_splitter, 1)
    measurement_page.setLayout(measurement_page_layout)

    window._measurement_top_host = measurement_top_host
    window._measurement_bottom_left_host = measurement_bottom_left_host
    window._measurement_bottom_right_host = measurement_bottom_right_host
    window._measurement_bottom_splitter = measurement_bottom_splitter
    window._measurement_vertical_splitter = measurement_vertical_splitter
    window._measurement_layout_page = measurement_page

    window._top_content_stack = QStackedWidget()
    window._top_content_stack.addWidget(spectrum_block)
    window._experiment_control_panel_placeholder = QWidget(window)
    window._experiment_control_panel_placeholder.setVisible(False)
    window._flow_panel_placeholder = window._experiment_control_panel_placeholder
    window._top_content_stack.addWidget(window._experiment_control_panel_placeholder)
    window._top_content_stack.setCurrentIndex(0)

    footer_bar = QHBoxLayout()
    footer_bar.setContentsMargins(0, 0, 0, 0)
    footer_bar.setSpacing(6)
    footer_bar.addWidget(window.status_label, 1)
    footer_bar.addWidget(window.live_estimate, 1)
    footer_bar.addWidget(window.status_help_button, 0, Qt.AlignmentFlag.AlignVCenter)
    footer_bar.addWidget(window.telemetry_label, 2)
    # Keep the detailed telemetry available for diagnostics exports, but remove it from the visible footer.
    window.telemetry_label.setVisible(False)
    window._window_size_grip = QSizeGrip(window)
    window._window_size_grip.setToolTip("Drag to resize the window.")
    footer_bar.addWidget(window._window_size_grip, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
    footer_widget = QWidget(window)
    footer_widget.setContentsMargins(0, 0, 0, 0)
    footer_widget.setLayout(footer_bar)
    footer_widget.setObjectName("mainWindowStatusFooter")
    footer_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    footer_widget.setMinimumHeight(footer_widget.sizeHint().height())
    footer_widget.setMaximumHeight(footer_widget.sizeHint().height())

    plot_splitter = CompactSplitter(Qt.Orientation.Vertical)
    plot_splitter.setChildrenCollapsible(False)
    plot_splitter.addWidget(window._top_content_stack)
    plot_splitter.addWidget(trace_block)
    plot_splitter.setStretchFactor(0, 2)
    plot_splitter.setStretchFactor(1, 3)
    plot_splitter.setSizes([430, 470])
    plot_splitter.splitterMoved.connect(lambda *_: window._schedule_ui_state_persist())
    window.plot_splitter = plot_splitter

    right_content_stack = QStackedWidget()
    right_content_stack.addWidget(plot_splitter)
    right_content_stack.addWidget(measurement_page)
    right_content_stack.setCurrentWidget(plot_splitter)
    window._right_content_stack = right_content_stack
    window._standard_right_page = plot_splitter
    window._measurement_right_page = measurement_page

    right_panel = QVBoxLayout()
    right_panel.addWidget(right_content_stack, 1)
    right_panel.addWidget(footer_widget, 0)

    right_widget = QWidget(window)
    right_widget.setLayout(right_panel)
    right_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    splitter = CompactSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(left_scroll)
    splitter.addWidget(right_widget)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([260, 1100])
    splitter.splitterMoved.connect(lambda *_: window._schedule_ui_state_persist())
    window.left_right_splitter = splitter

    recording_context_row = None
    if window._launch_profile_spec.show_recording_context:
        recording_context_row = build_recording_context_row_for(window)
    window._recording_context_row = recording_context_row

    root_layout = QVBoxLayout()
    root_layout.setContentsMargins(6, 4, 6, 6)
    root_layout.setSpacing(5)
    if recording_context_row is not None:
        root_layout.addWidget(recording_context_row)
    root_layout.addWidget(splitter, 1)
    root_layout.addWidget(footer_widget, 0)

    container = QWidget(window)
    container.setLayout(root_layout)
    window._main_content_widget = container
    container.installEventFilter(window)
    window.setCentralWidget(container)
    window._sensorgram_header_controls_ready = True


def apply_control_sizing_for(window) -> None:
    """Set minimum sizes and size policies for all acquisition-panel controls."""
    tall_widgets = [
        window.integration_spin,
        window.averages_spin,
        window.auto_integration_button,
        window.auto_integration_status_label,
        window.correct_dark_check,
        window.correct_nonlinearity_check,
        window.live_rate_spin,
        window.trace_noise_window_spin,
        window.range_min_spin,
        window.range_max_spin,
        window.baseline_method_combo,
        window.smoothing_method_combo,
        window.smoothing_window_spin,
        window.temporal_smoothing_spin,
        window.crop_method_combo,
        window.fit_method_combo,
        window.poly_order_spin,
        window.fit_window_spin,
        window.analysis_resolution_spin,
        window.plot_selector,
        window.spectrum_y_axis_format_button,
        window.save_processing_button,
        window.load_processing_button,
        window.clear_trace_button,
        window.trace_record_button,
        window.show_residual_button,
        window.freeze_plots_button,
        window.start_tracking_button,
        window.autoscale_spectrum_button,
        window.autoscale_trace_button,
        window.sim_resolution_spin,
    ]
    for widget in tall_widgets:
        widget.setMinimumHeight(26)

    # window.plot_selector is a MenuDropdownButton (QToolButton), not a
    # QComboBox, since the "[Absorbance]" rework - it doesn't have
    # setSizeAdjustPolicy/view(), so it's sized via tall_widgets above only.
    compact_combos = [
        window.baseline_method_combo,
        window.smoothing_method_combo,
        window.crop_method_combo,
        window.fit_method_combo,
        window.analysis_resolution_spin,
    ]
    for combo in compact_combos:
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        combo.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        combo.setMinimumContentsLength(1)
        combo.view().setTextElideMode(Qt.TextElideMode.ElideRight)

    configure_spectra_processing_group_controls(window)

    for button in (
        window.measurement_toggle_button,
        window.stop_measurement_button,
        window.next_measurement_button,
        window.acquire_dark_button,
        window.acquire_reference_button,
    ):
        button.setMinimumSize(QSize(34, 34))

    for slider in (
        window.sim_peak_center_slider,
        window.sim_peak_width_slider,
        window.sim_peak_height_slider,
        window.sim_secondary_peak_offset_slider,
        window.sim_secondary_peak_height_slider,
        window.sim_secondary_peak_width_slider,
        window.sim_baseline_slider,
        window.sim_slope_slider,
        window.sim_noise_slider,
    ):
        slider.setMinimumHeight(18)

    window.source_tabs.setMinimumHeight(220)
