from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

from lspr_acq_shell.settings_store import (
    get_and_clear_settings_corruption_notice,
    load_app_setting,
    load_ui_state,
    load_window_ui_state,
    read_settings_payload,
    save_app_setting,
    save_ui_state,
    save_window_ui_state,
    write_settings_payload,
)
from lspr_app.domain.models import ProcessingSettings, Spectrum
from lspr_app import __version__ as APP_VERSION
from lspr_app.storage.user_profile import GLOBAL_CONFIG_PATH, active_user, current_config_path
from lspr_io import (
    read_processing_settings_metadata,
    standard_measurement_metadata,
    write_measurement_root_metadata,
    write_processed_metrics_metadata,
    write_processing_settings_metadata,
)

# Re-exported for existing call sites (`from lspr_app.storage.app_config import
# save_app_setting`, etc.) - the actual implementation is
# lspr_acq_shell.settings_store as of the Phase 1 shell extraction,
# 2026-08-07. See that module's docstring for why.
__all__ = [
    "DEFAULT_CONFIG_PATH",
    "get_and_clear_settings_corruption_notice",
    "load_acquisition_state",
    "load_app_setting",
    "load_dark_reference_cache",
    "load_processing_settings",
    "load_processing_settings_from_hdf5",
    "load_ui_state",
    "load_window_ui_state",
    "save_acquisition_state",
    "save_app_setting",
    "save_dark_reference_cache",
    "save_processing_settings",
    "save_processing_settings_to_hdf5",
    "save_ui_state",
    "save_window_ui_state",
]


# The flat, pre-user settings file. Still used as-is: as the starting
# directory for the explicit "Save/load processing settings..." export
# dialogs (main_window_processing.py), and as the fallback path before any
# user has been entered yet. The functions below no longer default to this
# path directly - see current_config_path()/_resolve_path().
DEFAULT_CONFIG_PATH = GLOBAL_CONFIG_PATH


def _resolve_path(path: Path | None) -> Path:
    """Every load/save function's `path` parameter defaults to None, not a
    fixed constant - the active user can change during a run (see the user
    look-up field next to the destination-folder/experiment-name row), and
    a `path: Path = DEFAULT_CONFIG_PATH` default would be baked in once at
    import time, never re-evaluated. Resolving here means callers that
    pass no explicit path automatically follow whichever user is active
    *right now*."""
    return path if path is not None else current_config_path()

_logger = logging.getLogger(__name__)


# Renamed fields from older config/HDF5 schemas: old_name → new_name.
# When both names are present the new name wins (no-op rename).
_PROCESSING_SETTINGS_FIELD_RENAMES: dict[str, str] = {
    "peak_tracking_mode": "spectrum_tracking_mode",
}


def _clamp(value: object, minimum: float, maximum: float) -> float:
    """min(max(value, minimum), maximum) as a named helper, for the several
    settings below that clamp a saved value into a valid range."""
    return float(min(max(value, minimum), maximum))


def _coerce_processing_settings(raw: object) -> ProcessingSettings:
    defaults = asdict(ProcessingSettings())
    if isinstance(raw, dict):
        raw = dict(raw)
        for old_key, new_key in _PROCESSING_SETTINGS_FIELD_RENAMES.items():
            if old_key in raw and new_key not in raw:
                raw[new_key] = raw[old_key]
        # Older format stored fit_enabled=True with fit_method absent or "none".
        if raw.get("fit_enabled") is True:
            if raw.get("fit_method") in (None, "none"):
                raw["fit_method"] = "poly"
        defaults.update({key: value for key, value in raw.items() if key in defaults})
    if defaults.get("baseline_method") == "asls":
        defaults["baseline_method"] = "linear"
    if defaults.get("crop_method") not in {"fixed_width", "threshold"}:
        defaults["crop_method"] = "fixed_width"
    defaults["crop_fraction"] = _clamp(defaults.get("crop_fraction", 0.7), 0.05, 0.95)
    if defaults.get("fit_method") not in {"none", "poly", "gaussian"}:
        defaults["fit_method"] = "none"
    if defaults.get("analysis_resolution_nm") is None:
        defaults["analysis_resolution_nm"] = 0.001
    defaults["analysis_resolution_nm"] = _clamp(defaults.get("analysis_resolution_nm", 0.001), 0.000001, 0.1)
    defaults["trace_noise_window_s"] = _clamp(defaults.get("trace_noise_window_s", 10.0), 0.5, 600.0)
    trace_metrics = defaults.get("trace_metrics")
    if not isinstance(trace_metrics, list):
        defaults["trace_metrics"] = ["smoothed_max", "centroid"]
    else:
        allowed = {"smoothed_max", "poly_max", "gaussian_center", "centroid"}
        filtered = [item for item in trace_metrics if item in allowed]
        defaults["trace_metrics"] = filtered or ["smoothed_max", "centroid"]
    return ProcessingSettings(**defaults)


def save_processing_settings(settings: ProcessingSettings, path: Path | None = None) -> None:
    path = _resolve_path(path)
    payload = read_settings_payload(path)
    payload["processing"] = asdict(settings)
    write_settings_payload(payload, path)


def save_processing_settings_to_hdf5(settings: ProcessingSettings, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with h5py.File(path, "w") as handle:
        write_measurement_root_metadata(
            handle,
            **standard_measurement_metadata(
                created_by="LSPR Acquisition",
                started_at_utc=datetime.now(timezone.utc),
                app_name="LSPR Acquisition",
                app_version=APP_VERSION,
                experiment_name="",
                user=active_user() or "",
            ),
        )
        metadata = handle.create_group("metadata")
        processed = handle.create_group("processed")
        metrics_group = processed.create_group("metrics")
        write_processed_metrics_metadata(metrics_group)
        write_processing_settings_metadata(metrics_group, asdict(settings))

        for key, value in (
            ("processing_range_min_nm", settings.wavelength_min_nm),
            ("processing_range_max_nm", settings.wavelength_max_nm),
            ("processing_baseline_method", settings.baseline_method),
            ("processing_smoothing_method", settings.smoothing_method),
            ("processing_smoothing_window", settings.smoothing_window),
            ("processing_temporal_smoothing", settings.temporal_smoothing),
            ("processing_crop_method", settings.crop_method),
            ("processing_crop_fraction", settings.crop_fraction),
            ("processing_fit_method", settings.fit_method),
            ("processing_polynomial_order", settings.polynomial_order),
            ("processing_fit_window_width_nm", settings.fit_window_width_nm),
            ("processing_analysis_resolution_nm", settings.analysis_resolution_nm),
            ("spectrum_tracking_mode", settings.spectrum_tracking_mode),
            ("peak_tracking_mode", settings.spectrum_tracking_mode),
            ("processing_trace_noise_window_s", settings.trace_noise_window_s),
        ):
            metadata.attrs[key] = value
        metadata.attrs["trace_metrics"] = np.asarray(settings.trace_metrics, dtype=h5py.string_dtype(encoding="utf-8"))


def load_processing_settings(path: Path | None = None) -> ProcessingSettings:
    path = _resolve_path(path)
    if not path.exists():
        return ProcessingSettings()

    payload = read_settings_payload(path)
    processing = payload.get("processing", {})
    return _coerce_processing_settings(processing)


def load_processing_settings_from_hdf5(path: Path) -> ProcessingSettings:
    if not path.exists():
        return ProcessingSettings()

    with h5py.File(path, "r") as handle:
        processed_group = handle.get("processed")
        if processed_group is not None:
            metrics_group = processed_group.get("metrics")
            if metrics_group is not None:
                payload = read_processing_settings_metadata(metrics_group)
                if isinstance(payload, dict) and payload:
                    return _coerce_processing_settings(payload)

        legacy_payload: dict[str, object] = {}
        metadata_group = handle.get("metadata")
        if metadata_group is not None:
            attrs = metadata_group.attrs
            legacy_payload = {
                "wavelength_min_nm": attrs.get("processing_range_min_nm", 400.0),
                "wavelength_max_nm": attrs.get("processing_range_max_nm", 900.0),
                "baseline_method": attrs.get("processing_baseline_method", "none"),
                "smoothing_method": attrs.get("processing_smoothing_method", "none"),
                "smoothing_window": attrs.get("processing_smoothing_window", 5),
                "temporal_smoothing": attrs.get("processing_temporal_smoothing", 1),
                "crop_method": attrs.get("processing_crop_method", "fixed_width"),
                "crop_fraction": attrs.get("processing_crop_fraction", 0.7),
                "fit_method": attrs.get("processing_fit_method", "none"),
                "polynomial_order": attrs.get("processing_polynomial_order", 2),
                "fit_window_width_nm": attrs.get("processing_fit_window_width_nm", 120.0),
                "analysis_resolution_nm": attrs.get("processing_analysis_resolution_nm", 0.001),
                "spectrum_tracking_mode": attrs.get("spectrum_tracking_mode", attrs.get("peak_tracking_mode", "poly_max")),
                "trace_noise_window_s": attrs.get("processing_trace_noise_window_s", 10.0),
            }
            trace_metrics = attrs.get("trace_metrics", ["smoothed_max", "centroid"])
            if isinstance(trace_metrics, np.ndarray):
                legacy_payload["trace_metrics"] = [
                    item.decode("utf-8") if isinstance(item, bytes) else str(item) for item in trace_metrics.tolist()
                ]
            elif isinstance(trace_metrics, (list, tuple)):
                legacy_payload["trace_metrics"] = [
                    item.decode("utf-8") if isinstance(item, bytes) else str(item) for item in trace_metrics
                ]
            elif trace_metrics is not None:
                legacy_payload["trace_metrics"] = [str(trace_metrics)]
        return _coerce_processing_settings(legacy_payload)


def save_acquisition_state(
    state: dict[str, object],
    path: Path | None = None,
) -> None:
    save_app_setting("acquisition_state", state, path)


def load_acquisition_state(
    path: Path | None = None,
) -> dict[str, object]:
    state = load_app_setting("acquisition_state", {}, path)
    return state if isinstance(state, dict) else {}


def _spectrum_to_payload(spectrum: Spectrum) -> dict[str, object]:
    return {
        "wavelengths_nm": [float(v) for v in spectrum.wavelengths_nm],
        "values": [float(v) for v in spectrum.values],
        "y_label": spectrum.y_label,
        "acquired_at": spectrum.acquired_at.isoformat(),
        "metadata": dict(spectrum.metadata),
    }


def _spectrum_from_payload(payload: object) -> Spectrum | None:
    if not isinstance(payload, dict):
        return None
    try:
        return Spectrum(
            wavelengths_nm=np.asarray(payload["wavelengths_nm"], dtype=np.float64),
            values=np.asarray(payload["values"], dtype=np.float64),
            y_label=str(payload.get("y_label", "")),
            acquired_at=datetime.fromisoformat(str(payload["acquired_at"])),
            metadata=dict(payload["metadata"]) if isinstance(payload.get("metadata"), dict) else {},
        )
    except (KeyError, ValueError, TypeError) as exc:
        _logger.warning("Could not restore a cached dark/reference spectrum: %s", exc)
        return None


def save_dark_reference_cache(
    dark: Spectrum | None,
    reference: Spectrum | None,
    path: Path | None = None,
) -> None:
    """Cache the acquired Dark/Reference spectra in the active user's own
    settings file, so they survive an app restart ("session reset") instead
    of being lost every time - without this, a fresh MeasurementSession()
    always starts with no dark/reference (see app.py's main()), forcing a
    redundant re-acquisition every launch even when the optical setup hasn't
    changed. Called eagerly whenever either one changes (see
    acquisition_controller.py's request_manual_acquisition/
    handle_acquisition_success) rather than folded into the debounced
    acquisition-state autosave, since these arrays are much larger than the
    rest of that payload and change far less often than e.g. a slider being
    dragged.
    """
    save_app_setting(
        "dark_reference_cache",
        {
            "dark": _spectrum_to_payload(dark) if dark is not None else None,
            "reference": _spectrum_to_payload(reference) if reference is not None else None,
        },
        path,
    )


def load_dark_reference_cache(path: Path | None = None) -> tuple[Spectrum | None, Spectrum | None]:
    cached = load_app_setting("dark_reference_cache", {}, path)
    if not isinstance(cached, dict):
        return None, None
    return _spectrum_from_payload(cached.get("dark")), _spectrum_from_payload(cached.get("reference"))
