from __future__ import annotations

from pathlib import Path
from PyQt6.QtWidgets import QFileDialog

from lspr_app.domain.models import ProcessingSettings
from lspr_app.storage.app_config import (
    DEFAULT_CONFIG_PATH,
    load_processing_settings,
    load_processing_settings_from_hdf5,
    save_processing_settings,
)

METRIC_MODE_ORDER = ("smoothed_max", "poly_max", "centroid", "gaussian_center")

SMOOTHING_METHOD_LABELS = {
    "none": "None",
    "moving_average": "Moving average",
    "savitzky_golay": "Savitzky-Golay",
}
ANALYSIS_RESOLUTION_OPTIONS = (
    ("10\u207B\u00B9", 0.1),
    ("10\u207B\u00B2", 0.01),
    ("10\u207B\u00B3", 0.001),
    ("10\u207B\u2074", 0.0001),
    ("10\u207B\u2075", 0.00001),
    ("10\u207B\u2076", 0.000001),
)


def populate_analysis_resolution_combo(combo) -> None:
    combo.clear()
    for label, value in ANALYSIS_RESOLUTION_OPTIONS:
        combo.addItem(label, value)


def analysis_resolution_value(combo) -> float:
    value = combo.currentData()
    if isinstance(value, (int, float)):
        return float(value)
    fallback = combo.currentText()
    for label, option_value in ANALYSIS_RESOLUTION_OPTIONS:
        if fallback == label:
            return float(option_value)
    return 0.001


def set_analysis_resolution_value(combo, value: float) -> None:
    index = combo.findData(float(value))
    if index >= 0:
        combo.setCurrentIndex(index)
        return
    closest_index = 0
    closest_delta = float("inf")
    for index, (_, option_value) in enumerate(ANALYSIS_RESOLUTION_OPTIONS):
        delta = abs(float(option_value) - float(value))
        if delta < closest_delta:
            closest_delta = delta
            closest_index = index
    combo.setCurrentIndex(closest_index)


def _combo_value(combo) -> str:
    value = combo.currentData()
    if value is None or value == "":
        return combo.currentText()
    return str(value)


def _set_combo_value(combo, value: str, *, fallback: str | None = None) -> None:
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)
        return
    if fallback is not None:
        index = combo.findText(fallback)
        if index >= 0:
            combo.setCurrentIndex(index)
            return
    combo.setCurrentText(value)


def normalize_sensorgram_metric_name(metric_name: object) -> str:
    normalized = str(metric_name or "").strip().lower()
    return normalized if normalized in METRIC_MODE_ORDER else "smoothed_max"


def sensorgram_metric_order(window) -> list[str]:
    labels = getattr(window, "TRACE_METRIC_LABELS", {})
    if isinstance(labels, dict) and labels:
        ordered = [normalize_sensorgram_metric_name(name) for name in labels.keys()]
        return [name for name in ordered if name in METRIC_MODE_ORDER]
    return list(METRIC_MODE_ORDER)


def sensorgram_metric_selection(window) -> tuple[list[str], str]:
    order = sensorgram_metric_order(window)
    visible_modes = getattr(window, "_sensorgram_metric_visible_modes", None)
    primary_mode = getattr(window, "_sensorgram_metric_primary_mode", None)
    visible: list[str] = []
    if isinstance(visible_modes, (set, list, tuple)):
        visible_set = {normalize_sensorgram_metric_name(mode) for mode in visible_modes}
        visible = [mode for mode in order if mode in visible_set]
    if not visible:
        visible = [mode for mode in order if mode in selected_trace_metrics_from_legacy_widgets(window)]
    if not visible:
        visible = [order[0] if order else "smoothed_max"]
    primary = normalize_sensorgram_metric_name(primary_mode)
    if primary not in visible:
        primary = visible[0]
    return visible, primary


def selected_trace_metrics_from_legacy_widgets(window) -> list[str]:
    selected: list[str] = []
    if getattr(window, "trace_max_check", None) is not None and window.trace_max_check.isChecked():
        selected.append("smoothed_max")
    if getattr(window, "trace_centroid_check", None) is not None and window.trace_centroid_check.isChecked():
        selected.append("centroid")
    if getattr(window, "trace_poly_check", None) is not None and window.trace_poly_check.isChecked():
        selected.append("poly_max")
    if getattr(window, "trace_gaussian_check", None) is not None and window.trace_gaussian_check.isChecked():
        selected.append("gaussian_center")
    return selected or ["smoothed_max"]


def sync_legacy_metric_widgets_from_state(window) -> None:
    visible, primary = sensorgram_metric_selection(window)
    visible_set = set(visible)
    if getattr(window, "metric_mode_combo", None) is not None:
        window.metric_mode_combo.blockSignals(True)
        window.metric_mode_combo.setCurrentText(primary)
        window.metric_mode_combo.blockSignals(False)
    for mode, check in (
        ("smoothed_max", getattr(window, "trace_max_check", None)),
        ("centroid", getattr(window, "trace_centroid_check", None)),
        ("poly_max", getattr(window, "trace_poly_check", None)),
        ("gaussian_center", getattr(window, "trace_gaussian_check", None)),
    ):
        if check is None:
            continue
        check.blockSignals(True)
        check.setChecked(mode in visible_set)
        check.blockSignals(False)


def current_processing_settings(window) -> ProcessingSettings:
    low = min(window.range_min_spin.value(), window.range_max_spin.value())
    high = max(window.range_min_spin.value(), window.range_max_spin.value())
    visible_modes, primary_mode = sensorgram_metric_selection(window)
    return ProcessingSettings(
        wavelength_min_nm=low,
        wavelength_max_nm=high,
        baseline_method=window.baseline_method_combo.currentText(),
        smoothing_method=_combo_value(window.smoothing_method_combo),
        smoothing_window=window.smoothing_window_spin.value(),
        temporal_smoothing=window.temporal_smoothing_spin.value(),
        crop_method=window.crop_method_combo.currentText(),
        crop_fraction=window.crop_fraction_spin.value(),
        fit_method=window.fit_method_combo.currentText(),
        polynomial_order=window.poly_order_spin.value(),
        fit_window_width_nm=window.fit_window_spin.value(),
        analysis_resolution_nm=analysis_resolution_value(window.analysis_resolution_spin),
        spectrum_tracking_mode=primary_mode,
        trace_noise_window_s=window.trace_noise_window_spin.value(),
        trace_metrics=visible_modes,
    )


def selected_trace_metrics(window) -> list[str]:
    visible_modes, _ = sensorgram_metric_selection(window)
    return visible_modes


def apply_processing_settings_to_widgets(window, settings: ProcessingSettings) -> None:
    window._suspend_processing_autosave = True
    window.range_min_spin.setValue(int(round(settings.wavelength_min_nm)))
    window.range_max_spin.setValue(int(round(settings.wavelength_max_nm)))
    window.baseline_method_combo.setCurrentText(settings.baseline_method)
    _set_combo_value(
        window.smoothing_method_combo,
        settings.smoothing_method,
        fallback=SMOOTHING_METHOD_LABELS.get(settings.smoothing_method, settings.smoothing_method),
    )
    window.smoothing_window_spin.setValue(settings.smoothing_window)
    window.temporal_smoothing_spin.setValue(getattr(settings, "temporal_smoothing", 1))
    crop_method = getattr(settings, "crop_method", "fixed_width")
    window.crop_method_combo.setCurrentText(crop_method if crop_method in {"fixed_width", "threshold"} else "fixed_width")
    window.crop_fraction_spin.setValue(float(getattr(settings, "crop_fraction", 0.7)))
    fit_method = getattr(settings, "fit_method", "none")
    window.fit_method_combo.setCurrentText(fit_method if fit_method in {"none", "poly", "gaussian"} else "none")
    window.poly_order_spin.setValue(settings.polynomial_order)
    window.fit_window_spin.setValue(int(round(settings.fit_window_width_nm)))
    set_analysis_resolution_value(window.analysis_resolution_spin, float(getattr(settings, "analysis_resolution_nm", 0.001)))
    window.trace_noise_window_spin.setValue(float(getattr(settings, "trace_noise_window_s", 10.0)))
    visible_modes = [normalize_sensorgram_metric_name(mode) for mode in getattr(settings, "trace_metrics", ["smoothed_max", "centroid"])]
    visible_modes = [mode for mode in sensorgram_metric_order(window) if mode in set(visible_modes)]
    if not visible_modes:
        visible_modes = [normalize_sensorgram_metric_name(getattr(settings, "spectrum_tracking_mode", "smoothed_max"))]
    primary_mode = normalize_sensorgram_metric_name(getattr(settings, "spectrum_tracking_mode", "smoothed_max"))
    if primary_mode not in visible_modes:
        primary_mode = visible_modes[0]
    window._sensorgram_metric_visible_modes = set(visible_modes)
    window._sensorgram_metric_primary_mode = primary_mode
    window._trace_stats_metric_name = primary_mode
    sync_legacy_metric_widgets_from_state(window)
    sync_processing_crop_parameter_widget(window)
    window._suspend_processing_autosave = False


def sync_processing_crop_parameter_widget(window) -> None:
    stack = getattr(window, "crop_parameter_stack", None)
    label = getattr(window, "crop_parameter_label", None)
    if stack is None or label is None:
        return
    crop_method = window.crop_method_combo.currentText()
    if crop_method == "threshold":
        stack.setCurrentWidget(window.crop_fraction_spin)
        label.setText("Fraction")
        label.setToolTip("Threshold fraction of peak height used to crop the fit range.")
        return
    stack.setCurrentWidget(window.fit_window_spin)
    label.setText("Range")
    label.setToolTip("Fit-range width in nm used to crop the fit range when the crop method is fixed_width.")


def persist_processing_settings(window) -> None:
    window._processing_settings = current_processing_settings(window)
    save_processing_settings(window._processing_settings)


def save_processing_settings_dialog(window) -> None:
    path_str, _ = QFileDialog.getSaveFileName(
        window,
        "Save processing settings",
        str(DEFAULT_CONFIG_PATH),
        "JSON files (*.json)",
    )
    if not path_str:
        return
    settings = current_processing_settings(window)
    save_processing_settings(settings, Path(path_str))
    save_processing_settings(settings)
    window.status_label.setText(f"Saved processing settings to {path_str}")
    window._log_success(f"Processing settings saved to {Path(path_str).name}.")


def load_processing_settings_dialog(window) -> None:
    path_str, _ = QFileDialog.getOpenFileName(
        window,
        "Load processing settings",
        str(DEFAULT_CONFIG_PATH),
        "Settings files (*.json *.h5 *.hdf5)",
    )
    if not path_str:
        return
    path = Path(path_str)
    if path.suffix.lower() in {".h5", ".hdf5"}:
        settings = load_processing_settings_from_hdf5(path)
    else:
        settings = load_processing_settings(path)
    window._processing_settings = settings
    apply_processing_settings_to_widgets(window, settings)
    save_processing_settings(settings)
    window._refresh_plot()
    window.status_label.setText(f"Loaded processing settings from {path_str}")
    window._log_success(f"Processing settings loaded from {Path(path_str).name}.")


def primary_trace_metric(window) -> str:
    _, primary_mode = sensorgram_metric_selection(window)
    return primary_mode


def schedule_processing_refresh(window) -> None:
    window._request_deferred_ui_refresh(stats=True)
