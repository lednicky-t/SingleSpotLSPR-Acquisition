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
    simulation_layout.setVerticalSpacing(6)

    simulation_info_row = QHBoxLayout()
    simulation_info_row.setContentsMargins(0, 0, 0, 0)
    simulation_info_row.setSpacing(4)
    simulation_info_row.addWidget(
        make_info_button(
            "Synthetic spectrum controls used in Simulation mode. "
            "These settings shape the generated display data and do not affect spectrometer hardware."
        )
    )
    simulation_info_row.addStretch(1)
    simulation_layout.addLayout(simulation_info_row, 0, 0, 1, 2)

    _add_sim_row(
        simulation_layout,
        1,
        "Peak center",
        window.sim_peak_center_slider,
        window.sim_peak_center_value,
        "Center wavelength of the synthetic spectral peak.",
    )
    _add_sim_row(
        simulation_layout,
        2,
        "Peak width",
        window.sim_peak_width_slider,
        window.sim_peak_width_value,
        "Width of the synthetic spectral peak.",
    )
    _add_sim_row(
        simulation_layout,
        3,
        "Peak height",
        window.sim_peak_height_slider,
        window.sim_peak_height_value,
        "Peak height in the synthetic display model.",
    )
    _add_sim_row(
        simulation_layout,
        4,
        "Baseline",
        window.sim_baseline_slider,
        window.sim_baseline_value,
        "Baseline offset in the synthetic display model.",
    )
    _add_sim_row(
        simulation_layout,
        5,
        "Slope",
        window.sim_slope_slider,
        window.sim_slope_value,
        "Linear slope in the synthetic display model.",
    )
    _add_sim_row(
        simulation_layout,
        6,
        "Noise",
        window.sim_noise_slider,
        window.sim_noise_value,
        "Random noise level added to the synthetic display model.",
    )

    resolution_label = QLabel("Resolution")
    resolution_label.setToolTip("Wavelength spacing of the synthetic spectrum grid.")
    simulation_layout.addWidget(resolution_label, 7, 0)
    window.sim_resolution_spin.setToolTip("Wavelength spacing of the synthetic spectrum grid.")
    simulation_layout.addWidget(window.sim_resolution_spin, 7, 1)
    output_rate_label = QLabel("Output rate")
    output_rate_label.setToolTip(
        "Frame production rate of the simulation backend. This does not affect spectrometer hardware."
    )
    simulation_layout.addWidget(output_rate_label, 8, 0)
    simulation_layout.addWidget(window.sim_output_rate_spin, 8, 1)
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
    processing_layout.setHorizontalSpacing(12)
    processing_layout.setVerticalSpacing(8)

    range_row = _build_processing_range_widget(window)
    range_label = QLabel("Range (nm)")
    range_label.setToolTip("Wavelength range used for processing and fits.")
    processing_layout.addRow(range_label, range_row)

    baseline_label = QLabel("Baseline removal")
    baseline_label.setToolTip("Baseline subtraction method.")
    processing_layout.addRow(baseline_label, window.baseline_method_combo)

    smoothing_row = QHBoxLayout()
    smoothing_row.setContentsMargins(0, 0, 0, 0)
    smoothing_row.setSpacing(6)
    smoothing_row.addWidget(window.smoothing_method_combo, 1)
    smoothing_row.addWidget(window.smoothing_window_spin)
    spectral_label = QLabel("Spectral smooth")
    spectral_label.setToolTip("Spectral smoothing settings.")
    processing_layout.addRow(spectral_label, smoothing_row)

    temporal_row = QHBoxLayout()
    temporal_row.setContentsMargins(0, 0, 0, 0)
    temporal_row.setSpacing(6)
    temporal_row.addWidget(window.temporal_smoothing_spin)
    temporal_row.addStretch(1)
    temporal_label = QLabel("Temporal smooth")
    temporal_label.setToolTip("Temporal smoothing of displayed processed spectra.")
    processing_layout.addRow(temporal_label, temporal_row)

    crop_row = QHBoxLayout()
    crop_row.setContentsMargins(0, 0, 0, 0)
    crop_row.setSpacing(6)
    crop_row.addWidget(window.crop_method_combo, 1)
    crop_row.addWidget(window.crop_fraction_spin)
    crop_label = QLabel("Crop")
    crop_label.setToolTip("Crop the fit region around the detected peak.")
    processing_layout.addRow(crop_label, crop_row)

    fit_row = QHBoxLayout()
    fit_row.setContentsMargins(0, 0, 0, 0)
    fit_row.setSpacing(6)
    fit_row.addWidget(window.fit_method_combo, 1)
    fit_row.addWidget(window.fit_window_spin)
    fit_label = QLabel("Fitting")
    fit_label.setToolTip("Peak fitting configuration.")
    processing_layout.addRow(fit_label, fit_row)

    poly_row = QHBoxLayout()
    poly_row.addWidget(window.poly_order_spin)
    poly_row.addWidget(window.poly_warning_label)
    poly_row.addStretch(1)
    poly_label = QLabel("Poly order")
    poly_label.setToolTip("Polynomial order used when polynomial fitting is selected.")
    processing_layout.addRow(poly_label, poly_row)

    peak_label = QLabel("Peak method")
    peak_label.setToolTip("Choose which peak metric is shown in the metric plot and summary.")
    processing_layout.addRow(peak_label, window.peak_metric_combo)

    trace_label = QLabel("Metric")
    trace_label.setToolTip("Toggle individual metric traces on and off.")
    trace_row = QVBoxLayout()
    trace_row.setContentsMargins(0, 0, 0, 0)
    trace_row.setSpacing(2)
    trace_row.addWidget(window.trace_max_check)
    trace_row.addWidget(window.trace_centroid_check)
    trace_row.addWidget(window.trace_poly_check)
    trace_row.addWidget(window.trace_gaussian_check)
    trace_row.addStretch(1)
    processing_layout.addRow(trace_label, trace_row)

    processing_buttons = QHBoxLayout()
    processing_buttons.setSpacing(6)
    processing_buttons.addWidget(window.save_processing_button)
    processing_buttons.addWidget(window.load_processing_button)
    processing_layout.addRow(processing_buttons)
    processing_group.setLayout(processing_layout)
    return processing_group


def configure_processing_group_controls(window) -> None:
    range_width = max(window.range_min_spin.sizeHint().width(), window.range_max_spin.sizeHint().width())
    narrow_range_width = max(int(round(range_width * 0.5)), 32)
    window.range_min_spin.setFixedWidth(narrow_range_width)
    window.range_max_spin.setFixedWidth(narrow_range_width)
    uniform_controls = (
        window.baseline_method_combo,
        window.smoothing_method_combo,
        window.smoothing_window_spin,
        window.temporal_smoothing_spin,
        window.crop_method_combo,
        window.crop_fraction_spin,
        window.fit_method_combo,
        window.poly_order_spin,
        window.fit_window_spin,
        window.analysis_resolution_spin,
        window.peak_metric_combo,
    )
    uniform_width = max(control.sizeHint().width() for control in uniform_controls)
    uniform_width = max(uniform_width, 110)
    for control in uniform_controls:
        control.setFixedWidth(uniform_width)


def _build_processing_range_widget(window) -> QWidget:
    range_widget = QWidget()
    range_layout = QGridLayout()
    range_layout.setContentsMargins(0, 0, 0, 0)
    range_layout.setHorizontalSpacing(6)
    range_layout.setVerticalSpacing(2)
    range_layout.setColumnStretch(0, 0)
    range_layout.setColumnStretch(1, 0)
    range_layout.setColumnStretch(2, 0)

    labels = [
        ("Min", "Minimum wavelength used for processing and fit range."),
        ("Max", "Maximum wavelength used for processing and fit range."),
        ("Resolution", "Resolution used for peak and centroid analysis."),
    ]
    controls = [window.range_min_spin, window.range_max_spin, window.analysis_resolution_spin]
    for column, ((label, tooltip), control) in enumerate(zip(labels, controls, strict=True)):
        label_widget = QLabel(label)
        label_widget.setToolTip(tooltip)
        control.setToolTip(tooltip)
        range_layout.addWidget(label_widget, 0, column, alignment=Qt.AlignmentFlag.AlignHCenter)
        range_layout.addWidget(control, 1, column)

    range_layout.setRowStretch(0, 0)
    range_layout.setRowStretch(1, 0)
    range_widget.setLayout(range_layout)
    range_widget.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
    return range_widget


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
