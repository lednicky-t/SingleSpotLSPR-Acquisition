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

from lspr_app.gui.panel_help import make_help_button
from lspr_app.gui.panel_help_text import SIMULATION_BODY, SIMULATION_TITLE, SIMULATION_TOOLTIP

SPECTRA_PROCESSING_SECTION_H_SPACING = 6
SPECTRA_PROCESSING_SECTION_V_SPACING = 1
PROCESSING_SECTION_LABEL_COL_WIDTH = 64  # wide enough for "Smoothing"


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

    acquisition_stats_row = QWidget(window)
    acquisition_stats_layout = QHBoxLayout()
    acquisition_stats_layout.setContentsMargins(0, 0, 0, 0)
    acquisition_stats_layout.setSpacing(8)
    acquisition_stats_layout.addWidget(window.spectrometer_stats_label, 1)
    acquisition_stats_row.setLayout(acquisition_stats_layout)
    acquisition_stats_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    page_layout = QVBoxLayout()
    page_layout.setContentsMargins(0, 0, 0, 0)
    page_layout.addWidget(device_group)
    page_layout.addWidget(acquisition_stats_row)
    page_layout.addStretch(1)
    page = QWidget(window)
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
    simulation_info_row.addWidget(make_help_button(SIMULATION_TOOLTIP, title=SIMULATION_TITLE, body=SIMULATION_BODY))
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
    page = QWidget(window)
    page.setLayout(page_layout)
    return page


def build_spectra_processing_group(window) -> QGroupBox:
    spectra_processing_group = QGroupBox(window)
    spectra_processing_layout = QVBoxLayout()
    spectra_processing_layout.setContentsMargins(4, 2, 4, 4)
    spectra_processing_layout.setSpacing(4)

    spectra_processing_layout.addWidget(_build_processing_stacked_row(
        window,
        "Range",
        "Wavelength range in nm used for processing and fits.",
        [
            ("Min", "Minimum wavelength for processing and fit range (nm).", window.range_min_spin),
            ("Max", "Maximum wavelength for processing and fit range (nm).", window.range_max_spin),
            ("Res", "Wavelength resolution for peak and centroid analysis (nm).", window.analysis_resolution_spin),
        ],
    ))
    spectra_processing_layout.addWidget(_build_processing_stacked_row(
        window,
        "Baseline",
        "Baseline subtraction method.",
        [
            ("Method", "Baseline subtraction method.", window.baseline_method_combo),
        ],
    ))
    spectra_processing_layout.addWidget(_build_processing_stacked_row(
        window,
        "Smoothing",
        "Smoothing settings for displayed and fitted spectra.",
        [
            ("Temporal", "Temporal smoothing of displayed processed spectra.", window.temporal_smoothing_spin),
            ("Spectral", "Spectral smoothing method.", window.smoothing_method_combo),
            ("Window", "Spectral smoothing window size in data points.", window.smoothing_window_spin),
        ],
        store_labels={"Window": "_processing_smoothing_window_title_widget"},
    ))
    spectra_processing_layout.addWidget(_build_processing_fitting_stacked(window))
    spectra_processing_group.setLayout(spectra_processing_layout)
    return spectra_processing_group


def configure_spectra_processing_group_controls(window) -> None:
    range_width = max(window.range_min_spin.sizeHint().width(), window.range_max_spin.sizeHint().width())
    range_width = max(int(round(range_width * 0.88)), 52)
    window.range_min_spin.setFixedWidth(range_width)
    window.range_max_spin.setFixedWidth(range_width)

    res_width = max(window.analysis_resolution_spin.sizeHint().width(), range_width)
    window.analysis_resolution_spin.setFixedWidth(res_width)

    # fit window: practical max 3 digits
    fit_w = 44
    window.fit_window_spin.setFixedWidth(fit_w)
    # crop fraction: double spin (e.g. "0.95"), size from hint
    frac_w = max(int(round(window.crop_fraction_spin.sizeHint().width() * 0.80)), 44)
    window.crop_fraction_spin.setFixedWidth(frac_w)
    window.crop_parameter_stack.setFixedWidth(max(fit_w, frac_w))

    # temporal: range 1–64 (2 digits) — size from hint
    temp_width = max(int(round(window.temporal_smoothing_spin.sizeHint().width() * 0.80)), 38)
    window.temporal_smoothing_spin.setFixedWidth(temp_width)
    # smoothing window: practical max 3 digits
    window.smoothing_window_spin.setFixedWidth(44)
    # poly order: practical max 2 digits
    window.poly_order_spin.setFixedWidth(40)

    combo_controls = (
        window.baseline_method_combo,
        window.smoothing_method_combo,
        window.crop_method_combo,
        window.fit_method_combo,
    )
    combo_width = max(c.sizeHint().width() for c in combo_controls)
    combo_width = max(int(round(combo_width * 0.80)), 72)
    for c in combo_controls:
        c.setFixedWidth(combo_width)


def _build_processing_stacked_row(
    window,
    section_label: str,
    section_tooltip: str,
    specs: list[tuple[str, str, QWidget]],
    *,
    store_labels: dict[str, str] | None = None,
) -> QWidget:
    """Section label left in control row; sub-labels above each control.

    store_labels: optional mapping of sub-label text → window attribute name to
    store the QLabel on window (used for later show/hide).
    """
    widget = QWidget(window)
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(SPECTRA_PROCESSING_SECTION_H_SPACING)
    grid.setVerticalSpacing(SPECTRA_PROCESSING_SECTION_V_SPACING)
    grid.setColumnMinimumWidth(0, PROCESSING_SECTION_LABEL_COL_WIDTH)
    grid.setColumnStretch(len(specs) + 1, 1)

    section_widget = QLabel(section_label)
    section_widget.setToolTip(section_tooltip)
    grid.addWidget(section_widget, 1, 0, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    for col, (label_text, tooltip, control) in enumerate(specs, start=1):
        lbl = QLabel(label_text)
        lbl.setToolTip(tooltip)
        control.setToolTip(tooltip)
        if store_labels and label_text in store_labels:
            setattr(window, store_labels[label_text], lbl)
        grid.addWidget(lbl, 0, col, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
        grid.addWidget(control, 1, col, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    widget.setLayout(grid)
    widget.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
    return widget


def _build_processing_fitting_stacked(window) -> QWidget:
    """Fitting row: labels on top, controls on bottom; stores dynamic title refs."""
    widget = QWidget(window)
    grid = QGridLayout()
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setHorizontalSpacing(SPECTRA_PROCESSING_SECTION_H_SPACING)
    grid.setVerticalSpacing(SPECTRA_PROCESSING_SECTION_V_SPACING)
    grid.setColumnMinimumWidth(0, PROCESSING_SECTION_LABEL_COL_WIDTH)
    grid.setColumnStretch(5, 1)

    section_lbl = QLabel("Fitting")
    section_lbl.setToolTip("Peak fitting configuration.")
    grid.addWidget(section_lbl, 1, 0, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    method_title = QLabel("Method")
    method_title.setToolTip("Peak fitting model used to estimate the peak position.")
    window.fit_method_combo.setToolTip("Peak fitting model used to estimate the peak position.")
    grid.addWidget(method_title, 0, 1, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
    grid.addWidget(window.fit_method_combo, 1, 1, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    order_title = QLabel("Order")
    order_title.setToolTip("Polynomial order used when polynomial fitting is selected.")
    window.poly_order_spin.setToolTip("Polynomial order used when polynomial fitting is selected.")
    grid.addWidget(order_title, 0, 2, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
    grid.addWidget(window.poly_order_spin, 1, 2, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    crop_title = QLabel("Crop")
    crop_title.setToolTip("Choose how the fit range is cropped around the detected peak.")
    window.crop_method_combo.setToolTip("Choose how the fit range is cropped around the detected peak.")
    grid.addWidget(crop_title, 0, 3, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
    grid.addWidget(window.crop_method_combo, 1, 3, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    parameter_title = QLabel("Param")
    parameter_title.setToolTip(window.crop_parameter_label.toolTip())
    grid.addWidget(parameter_title, 0, 4, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom)
    grid.addWidget(window.crop_parameter_stack, 1, 4, alignment=Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

    window._processing_fit_method_title_widget = method_title
    window._processing_fit_order_title_widget = order_title
    window._processing_fit_crop_title_widget = crop_title
    window._processing_fit_parameter_title_widget = parameter_title

    widget.setLayout(grid)
    widget.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Fixed)
    return widget


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
