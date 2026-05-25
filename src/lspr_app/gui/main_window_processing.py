from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QFileDialog

from lspr_app.domain.models import ProcessingSettings
from lspr_app.storage.app_config import DEFAULT_CONFIG_PATH, load_processing_settings, save_processing_settings

SMOOTHING_METHOD_LABELS = {
    "none": "none",
    "moving_average": "moving average",
    "savitzky_golay": "savitzky golay",
}

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


def current_processing_settings(window) -> ProcessingSettings:
    low = min(window.range_min_spin.value(), window.range_max_spin.value())
    high = max(window.range_min_spin.value(), window.range_max_spin.value())
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
        analysis_resolution_nm=window.analysis_resolution_spin.value(),
        peak_tracking_mode=window.peak_metric_combo.currentText(),
        trace_noise_window_s=window.trace_noise_window_spin.value(),
        trace_metrics=selected_trace_metrics(window),
    )


def selected_trace_metrics(window) -> list[str]:
    selected: list[str] = []
    if window.trace_max_check.isChecked():
        selected.append("smoothed_max")
    if window.trace_centroid_check.isChecked():
        selected.append("centroid")
    if window.trace_poly_check.isChecked():
        selected.append("poly_max")
    if window.trace_gaussian_check.isChecked():
        selected.append("gaussian_center")
    return selected or ["smoothed_max"]


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
    window.analysis_resolution_spin.setValue(float(getattr(settings, "analysis_resolution_nm", 0.001)))
    window.peak_metric_combo.setCurrentText(settings.peak_tracking_mode)
    window.trace_noise_window_spin.setValue(float(getattr(settings, "trace_noise_window_s", 10.0)))
    trace_metrics = set(getattr(settings, "trace_metrics", ["smoothed_max", "centroid"]))
    window.trace_max_check.setChecked("smoothed_max" in trace_metrics)
    window.trace_centroid_check.setChecked("centroid" in trace_metrics)
    window.trace_poly_check.setChecked("poly_max" in trace_metrics)
    window.trace_gaussian_check.setChecked("gaussian_center" in trace_metrics)
    if window._trace_stats_metric_name not in selected_trace_metrics(window):
        window._trace_stats_metric_name = primary_trace_metric(window)
    window._suspend_processing_autosave = False


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
        "JSON files (*.json)",
    )
    if not path_str:
        return
    settings = load_processing_settings(Path(path_str))
    window._processing_settings = settings
    apply_processing_settings_to_widgets(window, settings)
    save_processing_settings(settings)
    window._refresh_plot()
    window.status_label.setText(f"Loaded processing settings from {path_str}")
    window._log_success(f"Processing settings loaded from {Path(path_str).name}.")


def primary_trace_metric(window) -> str:
    peak_mode = current_processing_settings(window).peak_tracking_mode
    selected = selected_trace_metrics(window)
    if peak_mode in selected:
        return peak_mode
    return selected[0]


def schedule_processing_refresh(window) -> None:
    if not window._stats_refresh_timer.isActive():
        window._stats_refresh_timer.start(0)
