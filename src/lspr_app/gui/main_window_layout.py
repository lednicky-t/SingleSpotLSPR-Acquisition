from __future__ import annotations

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lspr_app.gui.main_window_panels import build_processing_group
from lspr_app.gui.icon_helpers import flow_tabler_icon, storage_compression_icon, tint_tabler_icon
from lspr_app.gui.widgets import CollapsibleSection, CompactSplitter
from lspr_app.gui.main_window_titlebar import refresh_hw_device_status_strip
from lspr_app.gui.main_window_headers import update_source_tab_headers, update_source_link_buttons


def build_recording_context_row_for(window) -> QWidget:
    panel = QWidget()
    panel.setObjectName("recordingContextPanel")
    layout = QHBoxLayout()
    layout.setContentsMargins(6, 0, 6, 0)
    layout.setSpacing(5)
    controls_row = QHBoxLayout()
    controls_row.setContentsMargins(0, 0, 0, 0)
    controls_row.setSpacing(5)
    project_label = QLabel("Project's destination")
    project_label.setToolTip("Root folder for saved experiment folders.")
    experiment_label = QLabel("Experiment name")
    experiment_label.setToolTip("Folder name and filename component for the current experiment.")
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


def build_main_layout_for(window) -> None:
    measurement_bar = QHBoxLayout()
    measurement_bar.setSpacing(4)
    measurement_bar.addStretch(1)

    processing_group = build_processing_group(window)

    source_block = QWidget()
    source_layout = QVBoxLayout()
    source_layout.setContentsMargins(0, 0, 0, 0)
    source_layout.setSpacing(6)
    source_layout.addWidget(window.source_tabs)
    source_block.setLayout(source_layout)

    plot_bar = QHBoxLayout()
    plot_bar.setSpacing(6)
    plot_bar.addWidget(QLabel("Plot"))
    plot_bar.addWidget(window.plot_selector)
    plot_bar.addWidget(window.acquire_dark_button)
    plot_bar.addWidget(window.acquire_reference_button)
    plot_bar.addWidget(window.show_residual_button)
    plot_bar.addWidget(window.freeze_plots_button)
    plot_bar.addStretch(1)

    spectrum_stats_bar = QHBoxLayout()
    spectrum_stats_bar.setSpacing(8)
    spectrum_stats_bar.addWidget(window.spectrum_stats_label, 1)
    spectrum_stats_bar.addWidget(window.spectrum_cursor_label)

    trace_title = QLabel("Sensorgram")
    trace_title.setObjectName("sensorgramHeaderLabel")
    trace_title.setStyleSheet("color: #8FE3A1;")
    window.sensorgram_view_mode_button.setObjectName("sensorgramViewModeButton")
    window.sensorgram_view_mode_button.setAutoRaise(True)
    window.sensorgram_view_mode_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    window.sensorgram_view_mode_button.setCursor(Qt.CursorShape.PointingHandCursor)
    window.sensorgram_view_mode_button.clicked.connect(window._cycle_sensorgram_view_mode)
    window._update_sensorgram_view_mode_button()
    window.sensorgram_downsampling_button.setObjectName("sensorgramDownsamplingButton")
    window.sensorgram_downsampling_button.setAutoRaise(True)
    window.sensorgram_downsampling_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    window.sensorgram_downsampling_button.setCursor(Qt.CursorShape.PointingHandCursor)
    window.sensorgram_downsampling_button.clicked.connect(window._cycle_sensorgram_downsampling_enabled)
    window._update_sensorgram_downsampling_button()
    window.sensorgram_content_mode_button.setObjectName("sensorgramContentModeButton")
    window.sensorgram_content_mode_button.setAutoRaise(True)
    window.sensorgram_content_mode_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    window.sensorgram_content_mode_button.setCursor(Qt.CursorShape.PointingHandCursor)
    window.sensorgram_content_mode_button.clicked.connect(window._cycle_sensorgram_content_mode)
    window._update_sensorgram_content_mode_button()
    window.sensorgram_window_button.setObjectName("sensorgramWindowButton")
    window.sensorgram_window_button.setAutoRaise(True)
    window.sensorgram_window_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
    window.sensorgram_window_button.setCursor(Qt.CursorShape.PointingHandCursor)
    window.sensorgram_window_button.clicked.connect(window._cycle_sensorgram_display_window)
    window._update_sensorgram_display_window_button()
    window._update_sensorgram_header_control_visibility()

    trace_title_row = QHBoxLayout()
    trace_title_row.setContentsMargins(0, 0, 0, 0)
    trace_title_row.setSpacing(6)
    trace_title_row.addWidget(trace_title)
    trace_title_row.addWidget(window.sensorgram_view_mode_button)
    trace_title_row.addWidget(window.sensorgram_downsampling_button)
    trace_title_row.addWidget(window.sensorgram_window_button)
    trace_title_row.addWidget(window.sensorgram_content_mode_button)
    trace_title_row.addStretch(1)
    trace_title_row_widget = QWidget()
    trace_title_row_widget.setLayout(trace_title_row)
    trace_title_row_widget.setContentsMargins(0, 0, 0, 0)

    trace_left_field = QHBoxLayout()
    trace_left_field.setContentsMargins(0, 0, 0, 0)
    trace_left_field.setSpacing(6)
    trace_left_field.addWidget(window.trace_record_button, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    trace_left_field.addWidget(window.sensorgram_freeze_button, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    trace_left_field.addWidget(window.clear_trace_button, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    trace_left_field.addStretch(1)

    trace_right_field = QVBoxLayout()
    trace_right_field.setContentsMargins(0, 0, 0, 0)
    trace_right_field.setSpacing(0)

    trace_metrics_row = QHBoxLayout()
    trace_metrics_row.setContentsMargins(0, 0, 0, 0)
    trace_metrics_row.setSpacing(6)
    trace_metrics_row.addWidget(window.trace_stats_label, 1)
    trace_metrics_widget = QWidget()
    trace_metrics_widget.setLayout(trace_metrics_row)
    trace_metrics_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    trace_noise_row = QHBoxLayout()
    trace_noise_row.setContentsMargins(0, 0, 0, 0)
    trace_noise_row.setSpacing(6)
    trace_noise_row.addWidget(QLabel("Noise"))
    trace_noise_row.addWidget(window.trace_noise_window_spin)
    trace_noise_row.addWidget(window.trace_noise_summary_label, 1)
    trace_noise_widget = QWidget()
    trace_noise_widget.setLayout(trace_noise_row)
    trace_noise_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    trace_noise_cursor_row = QHBoxLayout()
    trace_noise_cursor_row.setContentsMargins(0, 0, 0, 0)
    trace_noise_cursor_row.setSpacing(6)
    trace_noise_cursor_row.addWidget(trace_noise_widget, 0, Qt.AlignmentFlag.AlignLeft)
    trace_noise_cursor_row.addStretch(1)
    trace_noise_cursor_row.addWidget(window.trace_cursor_label, 0, Qt.AlignmentFlag.AlignRight)

    trace_noise_cursor_widget = QWidget()
    trace_noise_cursor_widget.setLayout(trace_noise_cursor_row)
    trace_noise_cursor_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    trace_right_field.addWidget(trace_metrics_widget)
    trace_right_field.addWidget(trace_noise_cursor_widget)
    trace_metrics_widget.setFixedHeight(trace_metrics_widget.sizeHint().height())
    trace_noise_widget.setFixedHeight(trace_noise_widget.sizeHint().height())
    trace_noise_cursor_widget.setFixedHeight(max(trace_noise_widget.sizeHint().height(), window.trace_cursor_label.sizeHint().height()))

    trace_left_widget = QWidget()
    trace_left_widget.setLayout(trace_left_field)
    trace_left_widget.setMinimumWidth(160)
    trace_left_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
    trace_right_widget = QWidget()
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
    window.sensorgram_header_splitter = trace_body_splitter

    source_section = CollapsibleSection("Light source", source_block, expanded=True)
    processing_section = CollapsibleSection("Processing", processing_group, expanded=False)
    session_top_row = QHBoxLayout()
    session_top_row.setContentsMargins(0, 0, 0, 0)
    session_top_row.setSpacing(6)
    session_rate_label = QLabel("Refresh rate")
    session_rate_label.setToolTip("GUI display refresh rate for live spectra and sensorgram updates.")
    session_top_row.addWidget(session_rate_label)
    session_top_row.addWidget(window.live_rate_spin)
    session_top_row.addStretch(1)
    session_top_row.addWidget(window.session_stats_snapshot_button)
    session_top_row.addWidget(window.session_stats_save_button)
    session_top_row.addWidget(window.session_stats_record_button)

    session_block = QWidget()
    session_layout = QVBoxLayout()
    session_layout.setContentsMargins(0, 0, 0, 0)
    session_layout.setSpacing(4)
    session_layout.addLayout(session_top_row)
    session_layout.addWidget(window.session_summary, 1)
    session_block.setLayout(session_layout)
    session_section = CollapsibleSection("Session", session_block, expanded=False)

    log_header_row = QHBoxLayout()
    log_header_row.setContentsMargins(0, 0, 0, 0)
    log_header_row.setSpacing(6)
    log_header_row.addWidget(QLabel("View"))
    log_header_row.addWidget(window.log_view_all_button)
    log_header_row.addWidget(window.log_view_gui_button)
    log_header_row.addWidget(window.log_view_devices_button)
    log_header_row.addStretch(1)
    log_header_row.addWidget(window.log_follow_button)
    log_header_row.addWidget(window.log_copy_button)
    log_header_row.addWidget(window.log_clear_button)
    log_block = QWidget()
    log_layout = QVBoxLayout()
    log_layout.setContentsMargins(0, 0, 0, 0)
    log_layout.setSpacing(4)
    log_layout.addLayout(log_header_row)
    log_layout.addWidget(window.log_terminal)
    log_block.setLayout(log_layout)
    log_section = CollapsibleSection("Log", log_block, expanded=True)

    window._source_section = source_section
    window._processing_section = processing_section
    window._session_section = session_section
    window._log_section = log_section
    window._restore_collapsible_section_state()

    left_panel = QVBoxLayout()
    left_panel.setSpacing(6)
    left_panel.addLayout(measurement_bar)
    left_panel.addWidget(source_section)
    left_panel.addWidget(processing_section)
    left_panel.addWidget(session_section)
    left_panel.addWidget(log_section)
    left_panel.addStretch(1)

    left_widget = QWidget()
    left_widget.setLayout(left_panel)
    left_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

    left_scroll = QScrollArea()
    left_scroll.setWidgetResizable(True)
    left_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    left_scroll.setWidget(left_widget)
    left_scroll.setMinimumWidth(250)
    left_scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
    window._left_controls_scroll = left_scroll

    spectrum_block = QWidget()
    spectrum_layout = QVBoxLayout()
    spectrum_layout.setContentsMargins(0, 0, 0, 0)
    spectrum_layout.setSpacing(4)
    spectrum_header = QLabel("Processed Spectrum")
    spectrum_header.setStyleSheet("font-size: 13px; font-weight: 800; letter-spacing: 0.8px; color: #5b6775;")
    spectrum_layout.addWidget(spectrum_header)
    spectrum_layout.addLayout(plot_bar)
    spectrum_layout.addLayout(spectrum_stats_bar)
    spectrum_layout.addWidget(window.spectrum_plot, 1)
    spectrum_block.setLayout(spectrum_layout)
    window._spectra_block = spectrum_block

    trace_block = QWidget()
    trace_layout = QVBoxLayout()
    trace_layout.setContentsMargins(0, 0, 0, 0)
    trace_layout.setSpacing(4)
    trace_layout.addWidget(trace_title_row_widget)
    trace_layout.addWidget(trace_body_splitter)
    trace_layout.addWidget(window.trace_plot, 1)
    trace_block.setLayout(trace_layout)
    window._sensorgram_block = trace_block

    window._top_content_stack = QStackedWidget()
    window._top_content_stack.addWidget(spectrum_block)
    window._experiment_control_panel_placeholder = QWidget()
    window._flow_panel_placeholder = window._experiment_control_panel_placeholder
    window._top_content_stack.addWidget(window._experiment_control_panel_placeholder)
    window._top_content_stack.setCurrentIndex(0)

    footer_bar = QHBoxLayout()
    footer_bar.setContentsMargins(0, 0, 0, 0)
    footer_bar.setSpacing(6)
    footer_bar.addWidget(window.status_label, 1)
    footer_bar.addWidget(window.live_estimate, 1)
    footer_bar.addWidget(window.telemetry_label, 2)
    window._window_size_grip = QSizeGrip(window)
    window._window_size_grip.setToolTip("Drag to resize the window.")
    footer_bar.addWidget(window._window_size_grip, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)
    footer_widget = QWidget()
    footer_widget.setLayout(footer_bar)
    footer_widget.setObjectName("mainWindowStatusFooter")
    footer_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    plot_splitter = CompactSplitter(Qt.Orientation.Vertical)
    plot_splitter.setChildrenCollapsible(False)
    plot_splitter.addWidget(window._top_content_stack)
    plot_splitter.addWidget(trace_block)
    plot_splitter.setStretchFactor(0, 2)
    plot_splitter.setStretchFactor(1, 3)
    plot_splitter.setSizes([430, 470])
    window.plot_splitter = plot_splitter

    right_panel = QVBoxLayout()
    right_panel.addWidget(window.plot_splitter, 1)
    right_panel.addWidget(footer_widget, 0)

    right_widget = QWidget()
    right_widget.setLayout(right_panel)
    right_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    splitter = CompactSplitter(Qt.Orientation.Horizontal)
    splitter.setChildrenCollapsible(False)
    splitter.addWidget(left_scroll)
    splitter.addWidget(right_widget)
    splitter.setStretchFactor(0, 0)
    splitter.setStretchFactor(1, 1)
    splitter.setSizes([260, 1100])
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

    container = QWidget()
    container.setLayout(root_layout)
    window._main_content_widget = container
    window.setCentralWidget(container)
    window._sensorgram_header_controls_ready = True
    window._update_sensorgram_header_control_visibility()
