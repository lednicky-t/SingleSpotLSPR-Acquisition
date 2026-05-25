from __future__ import annotations

from PyQt6.QtWidgets import (
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)
from PyQt6.QtCore import Qt


def build_spectrometer_page(window) -> QWidget:
    settings_group = QGroupBox()
    settings_layout = QFormLayout()
    settings_layout.setHorizontalSpacing(12)
    settings_layout.setVerticalSpacing(8)

    integration_row = QHBoxLayout()
    integration_row.setContentsMargins(0, 0, 0, 0)
    integration_row.setSpacing(8)
    integration_row.addWidget(window.integration_spin)
    integration_row.addWidget(window.auto_integration_button)
    integration_row.addStretch(1)
    settings_layout.addRow("Integration time", integration_row)

    window.averages_spin.setToolTip("Average this many frames into one displayed spectrum.")
    accumulation_row = QHBoxLayout()
    accumulation_row.setContentsMargins(0, 0, 0, 0)
    accumulation_row.setSpacing(8)
    accumulation_row.addWidget(window.averages_spin)
    accumulation_row.addStretch(1)
    settings_layout.addRow("Accumulation", accumulation_row)

    window.correct_dark_check.setText("")
    window.correct_dark_check.setToolTip("Apply dark-count correction to spectrometer data.")
    window.correct_nonlinearity_check.setText("")
    window.correct_nonlinearity_check.setToolTip("Apply nonlinearity correction to spectrometer data.")
    settings_layout.addRow("Correct dark counts", window.correct_dark_check)
    settings_layout.addRow("Correct nonlinearity", window.correct_nonlinearity_check)
    settings_group.setLayout(settings_layout)

    page_layout = QVBoxLayout()
    page_layout.setContentsMargins(0, 0, 0, 0)
    page_layout.addWidget(settings_group)
    page_layout.addStretch(1)
    page = QWidget()
    page.setLayout(page_layout)
    return page


def build_simulation_page(window) -> QWidget:
    simulation_group = QGroupBox()
    simulation_layout = QGridLayout()
    simulation_layout.setHorizontalSpacing(8)
    simulation_layout.setVerticalSpacing(6)

    _add_sim_row(
        simulation_layout,
        0,
        "Peak center",
        window.sim_peak_center_slider,
        window.sim_peak_center_value,
        "Center wavelength of the simulated spectral peak.",
    )
    _add_sim_row(
        simulation_layout,
        1,
        "Peak width",
        window.sim_peak_width_slider,
        window.sim_peak_width_value,
        "Width of the simulated spectral peak.",
    )
    _add_sim_row(
        simulation_layout,
        2,
        "Peak height",
        window.sim_peak_height_slider,
        window.sim_peak_height_value,
        "Height of the simulated peak above the baseline.",
    )
    _add_sim_row(
        simulation_layout,
        3,
        "Baseline",
        window.sim_baseline_slider,
        window.sim_baseline_value,
        "Baseline offset applied to the simulated spectrum.",
    )
    _add_sim_row(
        simulation_layout,
        4,
        "Slope",
        window.sim_slope_slider,
        window.sim_slope_value,
        "Linear slope added to the simulated spectrum.",
    )
    _add_sim_row(
        simulation_layout,
        5,
        "Noise",
        window.sim_noise_slider,
        window.sim_noise_value,
        "Random noise level added to the simulated spectrum.",
    )

    resolution_label = QLabel("Resolution")
    resolution_label.setToolTip("Wavelength spacing used by the simulation backend.")
    simulation_layout.addWidget(resolution_label, 6, 0)
    window.sim_resolution_spin.setToolTip("Wavelength spacing used by the simulation backend.")
    simulation_layout.addWidget(window.sim_resolution_spin, 6, 1)
    output_rate_label = QLabel("Output rate")
    output_rate_label.setToolTip("Simulation frame production rate.")
    simulation_layout.addWidget(output_rate_label, 7, 0)
    simulation_layout.addWidget(window.sim_output_rate_spin, 7, 1)
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

    range_row = QHBoxLayout()
    range_row.setContentsMargins(0, 0, 0, 0)
    range_row.setSpacing(6)
    range_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
    range_row.addWidget(QLabel("Min"))
    range_row.addWidget(window.range_min_spin)
    range_row.addSpacing(10)
    range_row.addWidget(QLabel("Max"))
    range_row.addWidget(window.range_max_spin)
    range_row.addStretch(1)
    range_label = QLabel("Range (nm)")
    range_label.setToolTip("Wavelength range used for processing and fits.")
    processing_layout.addRow(range_label, range_row)

    baseline_label = QLabel("Baseline")
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

    analysis_row = QHBoxLayout()
    analysis_row.setContentsMargins(0, 0, 0, 0)
    analysis_row.setSpacing(6)
    analysis_row.addWidget(window.analysis_resolution_spin)
    analysis_row.addStretch(1)
    analysis_label = QLabel("Analysis")
    analysis_label.setToolTip("Resolution used for peak and centroid analysis.")
    processing_layout.addRow(analysis_label, analysis_row)

    peak_label = QLabel("Peak method")
    peak_label.setToolTip("Choose which peak metric is shown in the trace plot and summary.")
    processing_layout.addRow(peak_label, window.peak_metric_combo)

    trace_label = QLabel("Trace")
    trace_label.setToolTip("Toggle individual trace metrics on and off.")
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
