from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from lspr_app.domain.models import ProcessingSettings


DEFAULT_CONFIG_PATH = Path.cwd() / "lspr_settings.json"


def _load_payload(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_payload(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def save_processing_settings(settings: ProcessingSettings, path: Path = DEFAULT_CONFIG_PATH) -> None:
    payload = _load_payload(path)
    payload["processing"] = asdict(settings)
    _write_payload(payload, path)


def load_processing_settings(path: Path = DEFAULT_CONFIG_PATH) -> ProcessingSettings:
    if not path.exists():
        return ProcessingSettings()

    payload = _load_payload(path)
    processing = payload.get("processing", {})
    defaults = asdict(ProcessingSettings())
    defaults.update({key: value for key, value in processing.items() if key in defaults})
    if defaults.get("baseline_method") == "asls":
        defaults["baseline_method"] = "linear"
    if defaults.get("crop_method") not in {"fixed_width", "threshold"}:
        defaults["crop_method"] = "fixed_width"
    defaults["crop_fraction"] = float(min(max(defaults.get("crop_fraction", 0.7), 0.05), 0.95))
    if defaults.get("fit_method") not in {"none", "poly", "gaussian"}:
        defaults["fit_method"] = "none"
    if processing.get("fit_enabled") is True and defaults.get("fit_method") == "none":
        defaults["fit_method"] = "poly"
    defaults["analysis_resolution_nm"] = float(
        min(max(defaults.get("analysis_resolution_nm", 0.001), 0.000001), 0.1)
    )
    defaults["trace_noise_window_s"] = float(min(max(defaults.get("trace_noise_window_s", 10.0), 0.5), 600.0))
    trace_metrics = defaults.get("trace_metrics")
    if not isinstance(trace_metrics, list):
        defaults["trace_metrics"] = ["smoothed_max", "centroid"]
    else:
        allowed = {"smoothed_max", "poly_max", "gaussian_center", "centroid"}
        filtered = [item for item in trace_metrics if item in allowed]
        defaults["trace_metrics"] = filtered or ["smoothed_max", "centroid"]
    return ProcessingSettings(**defaults)


def save_ui_state(state: dict[str, object], path: Path = DEFAULT_CONFIG_PATH) -> None:
    payload = _load_payload(path)
    payload["ui_state"] = state
    _write_payload(payload, path)


def load_ui_state(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, object]:
    if not path.exists():
        return {}
    payload = _load_payload(path)
    ui_state = payload.get("ui_state", {})
    return ui_state if isinstance(ui_state, dict) else {}


def save_window_ui_state(
    window_name: str,
    state: dict[str, object],
    path: Path = DEFAULT_CONFIG_PATH,
) -> None:
    payload = _load_payload(path)
    ui_state = payload.get("ui_state", {})
    if not isinstance(ui_state, dict):
        ui_state = {}
    ui_state[window_name] = state
    payload["ui_state"] = ui_state
    _write_payload(payload, path)


def load_window_ui_state(window_name: str, path: Path = DEFAULT_CONFIG_PATH) -> dict[str, object]:
    ui_state = load_ui_state(path)
    window_state = ui_state.get(window_name)
    if isinstance(window_state, dict):
        return window_state
    if window_name == "main_window":
        # Backward compatibility with older flat ui_state payloads.
        legacy_keys = {"x", "y", "width", "height", "maximized", "splitter_sizes"}
        if any(key in ui_state for key in legacy_keys):
            return ui_state
    return {}


def save_app_setting(
    key: str,
    value: object,
    path: Path = DEFAULT_CONFIG_PATH,
) -> None:
    payload = _load_payload(path)
    app_state = payload.get("app", {})
    if not isinstance(app_state, dict):
        app_state = {}
    app_state[key] = value
    payload["app"] = app_state
    _write_payload(payload, path)


def load_app_setting(
    key: str,
    default: object = None,
    path: Path = DEFAULT_CONFIG_PATH,
) -> object:
    payload = _load_payload(path)
    app_state = payload.get("app", {})
    if not isinstance(app_state, dict):
        return default
    return app_state.get(key, default)


def save_acquisition_state(
    state: dict[str, object],
    path: Path = DEFAULT_CONFIG_PATH,
) -> None:
    save_app_setting("acquisition_state", state, path)


def load_acquisition_state(
    path: Path = DEFAULT_CONFIG_PATH,
) -> dict[str, object]:
    state = load_app_setting("acquisition_state", {}, path)
    return state if isinstance(state, dict) else {}
