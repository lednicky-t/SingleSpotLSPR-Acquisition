from __future__ import annotations

from PyQt6.QtWidgets import (
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt

from lspr_ui import make_info_button


def build_spectrometer_page(window) -> QWidget:
    device_group = QGroupBox("Spectrometer device")
    device_layout = QFormLayout()
    device_layout.setHorizontalSpacing(12)
    device_layout.setVerticalSpacing(8)

    integration_row = QHBoxLayout()
    integration_row.setContentsMargins(0, 0, 0, 0)
    integration_row.setSpacing(8)
    integration_row.addWidget(window.integration_spin)
    integration_row.addWidget(window.auto_integration_button)
    integration_row.addStretch(1)
    device_layout.addRow("Integration time", integration_row)

    window.averages_spin.setToolTip("Average this many frames into one displayed spectrum.")
    accumulation_row = QHBoxLayout()
    accumulation_row.setContentsMargins(0, 0, 0, 0)
    accumulation_row.setSpacing(8)
    accumulation_row.addWidget(window.averages_spin)
    accumulation_row.addStretch(1)
    device_layout.addRow("Accumulation", accumulation_row)

    window.correct_dark_check.setText("")
    window.correct_dark_check.setToolTip("Apply dark-count correction to spectrometer data.")
    window.correct_nonlinearity_check.setText("")
    window.correct_nonlinearity_check.setToolTip("Apply nonlinearity correction to spectrometer data.")
    device_layout.addRow("Correct dark counts", window.correct_dark_check)
    device_layout.addRow("Correct nonlinearity", window.correct_nonlinearity_check)
    device_group.setLayout(device_layout)

    page_layout = QVBoxLayout()
    page_layout.setContentsMargins(0, 0, 0, 0)
    page_layout.addWidget(device_group)
    page_layout.addStretch(1)
    page = QWidget()
    page.setLayout(page_layout)
    return page


def build_simulation_page(window) -> QWidget:
    simulation_group = QGroupBox("Simulation display model")
    simulation_layout = QGridLayout()
    simulation_layout.setHorizontalSpacing(8)
    simulation_layout.setVerticalSpacing(3)

    simulation_info_row = QHBoxLayout()
    simulation_info_row.setContentsMargins(0, 0, 0, 0)
    simulation_info_row.setSpacing(2)
    simulation_info_row.addWidget(
        make_info_button(
            "Synthetic spectrum controls used in Simulation mode. "
            "These settings shape the generated display data, including the primary and secondary peaks, "
            "and do not affect spectrometer hardware."
        )
    )
    simulation_info_row.addStretch(1)
    simulation_layout.addLayout(simulation_info_row, 0, 0, 1, 2)

    _add_sim_section_header(simulation_layout, 1, "Primary peak")
    _add_sim_row(
        simulation_layout,
        2,
        "Peak center",
        window.sim_peak_center_slider,
        window.sim_peak_center_value,
        "Center wavelength of the synthetic spectral peak.",
    )
    _add_sim_row(
        simulation_layout,
        3,
        "Peak width",
        window.sim_peak_width_slider,
        window.sim_peak_width_value,
        "Width of the synthetic spectral peak.",
    )
    _add_sim_row(
        simulation_layout,
        4,
        "Peak height",
        window.sim_peak_height_slider,
        window.sim_peak_height_value,
        "Peak height in the synthetic display model.",
    )
    _add_sim_section_header(simulation_layout, 5, "Secondary peak")
    _add_sim_row(
        simulation_layout,
        6,
        "2nd center offset",
        window.sim_secondary_peak_offset_slider,
        window.sim_secondary_peak_offset_value,
        "Center-to-center offset of the second peak relative to the first peak.",
    )
    _add_sim_row(
        simulation_layout,
        7,
        "2nd relative height",
        window.sim_secondary_peak_height_slider,
        window.sim_secondary_peak_height_value,
        "Second peak height as a percentage of the first peak height.",
    )
    _add_sim_row(
        simulation_layout,
        8,
        "2nd relative width",
        window.sim_secondary_peak_width_slider,
        window.sim_secondary_peak_width_value,
        "Second peak width as a percentage of the first peak width.",
    )
    _add_sim_row(
        simulation_layout,
        9,
        "Baseline",
        window.sim_baseline_slider,
        window.sim_baseline_value,
        "Baseline offset in the synthetic display model.",
    )
    _add_sim_row(
        simulation_layout,
        10,
        "Relative slope",
        window.sim_slope_slider,
        window.sim_slope_value,
        "Relative linear slope in the synthetic display model, scaled by baseline + peak height across the wavelength span.",
    )
    _add_sim_row(
        simulation_layout,
        11,
        "Noise",
        window.sim_noise_slider,
        window.sim_noise_value,
        "Random noise level added to the synthetic display model.",
    )

    resolution_label = QLabel("Resolution")
    resolution_label.setToolTip("Wavelength spacing of the synthetic spectrum grid.")
    simulation_layout.addWidget(resolution_label, 12, 0)
    window.sim_resolution_spin.setToolTip("Wavelength spacing of the synthetic spectrum grid.")
    simulation_layout.addWidget(window.sim_resolution_spin, 12, 1)
    output_rate_label = QLabel("Output rate")
    output_rate_label.setToolTip(
        "Frame production rate of the simulation backend. This does not affect spectrometer hardware."
    )
    simulation_layout.addWidget(output_rate_label, 13, 0)
    simulation_layout.addWidget(window.sim_output_rate_spin, 13, 1)
    simulation_group.setLayout(simulation_layout)

    page_layout = QVBoxLayout()
    page_layout.setContentsMargins(0, 0, 0, 0)
    page_layout.addWidget(simulation_group)
    page_layout.addStretch(1)
    page = QWidget()
    page.setLayout(page_layout)
    return page


def build_processing_group(window) -> QGroupBox:
    processing_group = QGroupBox()
    processing_layout = QFormLayout()
    processing_layout.setHorizontalSpacing(8)
    processing_layout.setVerticalSpacing(6)

    processing_layout.addRow(_build_processing_range_widget(window))

    baseline_label = QLabel("Baseline removal")
    baseline_label.setToolTip("Baseline subtraction method.")
    processing_layout.addRow(baseline_label, window.baseline_method_combo)

    processing_layout.addRow(_build_processing_smoothing_widget(window))
    processing_layout.addRow(_build_processing_fitting_widget(window))

    poly_row = QHBoxLayout()
    poly_row.addWidget(window.poly_order_spin)
    poly_row.addWidget(window.poly_warning_label)
    poly_row.addStretch(1)
    poly_label = QLabel("Poly order")
    poly_label.setToolTip("Polynomial order used when polynomial fitting is selected.")
    processing_layout.addRow(poly_label, poly_row)

    processing_buttons = QHBoxLayout()
    processing_buttons.setSpacing(6)
    processing_buttons.addWidget(window.save_processing_button)
    processing_buttons.addWidget(window.load_processing_button)
    processing_layout.addRow(processing_buttons)
    processing_group.setLayout(processing_layout)
    return processing_group


def configure_processing_group_controls(window) -> None:
    range_width = max(window.range_min_spin.sizeHint().width(), window.range_max_spin.sizeHint().width())
    expanded_range_width = max(int(round(range_width * 1.05)), 40)
    resolution_width = max(int(round(expanded_range_width * 0.8)), 64)
    window.range_min_spin.setFixedWidth(expanded_range_width)
    window.range_max_spin.setFixedWidth(expanded_range_width)
    window.analysis_resolution_spin.setFixedWidth(resolution_width)

    crop_width = max(window.fit_window_spin.sizeHint().width(), window.crop_fraction_spin.sizeHint().width())
    crop_width = max(int(round(crop_width * 0.85)), 72)
    window.fit_window_spin.setFixedWidth(crop_width)
    window.crop_fraction_spin.setFixedWidth(crop_width)
    window.crop_parameter_stack.setFixedWidth(crop_width)

    uniform_controls = (
        window.baseline_method_combo,
        window.smoothing_method_combo,
        window.smoothing_window_spin,
        window.temporal_smoothing_spin,
        window.crop_method_combo,
        window.fit_method_combo,
        window.poly_order_spin,
        window.metric_mode_combo,
    )
    uniform_width = max(control.sizeHint().width() for control in uniform_controls)
    uniform_width = max(int(round(uniform_width * 0.8)), 84)
    for control in uniform_controls:
        control.setFixedWidth(uniform_width)


def _build_processing_range_widget(window) -> QWidget:
    range_widget = QWidget()
    range_layout = QGridLayout()
    range_layout.setContentsMargins(0, 0, 0, 0)
    range_layout.setHorizontalSpacing(6)
    range_layout.setVerticalSpacing(2)
    title_widget = QLabel("Range (nm)")
    title_widget.setToolTip("Wavelength range used for processing and fits.")
    range_layout.addWidget(title_widget, 0, 0, 1, 3, alignment=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

    range_layout.setColumnStretch(0, 0)
    range_layout.setColumnStretch(1, 0)
    range_layout.setColumnStretch(2, 0)

    labels = [
        ("Min", "Minimum wavelength used for processing and fit range.", window.range_min_spin),
        ("Max", "Maximum wavelength used for processing and fit range.", window.range_max_spin),
        ("Resolution", "Resolution used for peak and centroid analysis.", window.analysis_resolution_spin),
    ]
    for column, (label, tooltip, control) in enumerate(labels):
        label_widget = QLabel(label)
        label_widget.setToolTip(tooltip)
        control.setToolTip(tooltip)
        range_layout.addWidget(label_widget, 1, column, alignment=Qt.AlignmentFlag.AlignLeft)
        range_layout.addWidget(control, 2, column)

    range_layout.setRowStretch(0, 0)
    range_layout.setRowStretch(1, 0)
    range_layout.setRowStretch(2, 0)
    range_widget.setLayout(range_layout)
    range_widget.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
    return range_widget


def _build_processing_smoothing_widget(window) -> QWidget:
    smoothing_widget = QWidget()
    smoothing_layout = QGridLayout()
    smoothing_layout.setContentsMargins(0, 0, 0, 0)
    smoothing_layout.setHorizontalSpacing(6)
    smoothing_layout.setVerticalSpacing(2)

    title_widget = QLabel("Smoothing")
    title_widget.setToolTip("Smoothing settings for displayed and fitted spectra.")
    smoothing_layout.addWidget(title_widget, 0, 0, 1, 3, alignment=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

    smoothing_layout.setColumnStretch(0, 0)
    smoothing_layout.setColumnStretch(1, 0)
    smoothing_layout.setColumnStretch(2, 0)

    specs = [
        ("Temporal", "Temporal smoothing of displayed processed spectra.", window.temporal_smoothing_spin),
        ("Method", "Spectral smoothing method.", window.smoothing_method_combo),
        ("Window", "Spectral smoothing window size.", window.smoothing_window_spin),
    ]
    for column, (label, tooltip, control) in enumerate(specs):
        label_widget = QLabel(label)
        label_widget.setToolTip(tooltip)
        control.setToolTip(tooltip)
        smoothing_layout.addWidget(label_widget, 1, column, alignment=Qt.AlignmentFlag.AlignLeft)
        smoothing_layout.addWidget(control, 2, column)

    smoothing_layout.setRowStretch(0, 0)
    smoothing_layout.setRowStretch(1, 0)
    smoothing_layout.setRowStretch(2, 0)
    smoothing_widget.setLayout(smoothing_layout)
    smoothing_widget.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
    return smoothing_widget


def _build_processing_fitting_widget(window) -> QWidget:
    fitting_widget = QWidget()
    fitting_layout = QGridLayout()
    fitting_layout.setContentsMargins(0, 0, 0, 0)
    fitting_layout.setHorizontalSpacing(6)
    fitting_layout.setVerticalSpacing(2)

    title_widget = QLabel("Fitting")
    title_widget.setToolTip("Peak fitting configuration.")
    fitting_layout.addWidget(title_widget, 0, 0, 1, 3, alignment=Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

    fitting_layout.setColumnStretch(0, 0)
    fitting_layout.setColumnStretch(1, 0)
    fitting_layout.setColumnStretch(2, 0)

    specs = [
        ("Method", "Peak fitting model used to estimate the peak position.", window.fit_method_combo),
        ("Crop", "Choose how the fit range is cropped around the detected peak.", window.crop_method_combo),
        (window.crop_parameter_label, window.crop_parameter_label.toolTip(), window.crop_parameter_stack),
    ]
    for column, spec in enumerate(specs):
        label_or_widget, tooltip, control = spec
        if isinstance(label_or_widget, QLabel):
            label_widget = label_or_widget
        else:
            label_widget = QLabel(str(label_or_widget))
        label_widget.setToolTip(tooltip)
        control.setToolTip(tooltip)
        fitting_layout.addWidget(label_widget, 1, column, alignment=Qt.AlignmentFlag.AlignLeft)
        fitting_layout.addWidget(control, 2, column)

    fitting_layout.setRowStretch(0, 0)
    fitting_layout.setRowStretch(1, 0)
    fitting_layout.setRowStretch(2, 0)
    fitting_widget.setLayout(fitting_layout)
    fitting_widget.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
    return fitting_widget


def _add_sim_row(
    layout: QGridLayout,
    row: int,
    label: str,
    slider,
    value_label: QLabel,
    tooltip: str,
) -> None:
    value_label.setMinimumWidth(70)
    label_widget = QLabel(label)
    label_widget.setToolTip(tooltip)
    slider.setToolTip(tooltip)
    value_label.setToolTip(tooltip)
    layout.addWidget(label_widget, row, 0)
    layout.addWidget(slider, row, 1)
    layout.addWidget(value_label, row, 2)


def _add_sim_section_header(layout: QGridLayout, row: int, text: str) -> None:
    header = QLabel(text)
    header.setStyleSheet("font-weight: 700; margin-top: 2px; margin-bottom: 0px;")
    header.setToolTip(text)
    layout.addWidget(header, row, 0, 1, 3)
