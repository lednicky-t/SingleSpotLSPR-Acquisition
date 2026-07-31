from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from lspr_app.storage.output_paths import recording_experiment_base_dir_for


_WRITER_ATTR = "_metric_archive_writer"
_WRITER_PATH_ATTR = "_metric_archive_writer_path"
_WRITER_TEMP_ATTR = "_metric_archive_writer_is_temp"
_SESSION_WRITER_ATTR = "_session_writer"
_SESSION_PATH_ATTR = "_session_writer_path"


def _session_file_path(window: Any) -> Path:
    existing = getattr(window, _SESSION_PATH_ATTR, None)
    if existing:
        return Path(existing)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    session_name = getattr(getattr(window, "_session", None), "name", None) or "session"
    safe_session = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in str(session_name))
    base_dir = recording_experiment_base_dir_for(window, fallback_base=Path.cwd() / "data")
    # Mirrors start_measurement_run()'s destination.parent.mkdir() in
    # acquisition_controller.py - without this, h5py.File(path, "w") inside
    # AsyncHDF5MeasurementWriter's background thread fails with no visible
    # error (ensure_session_writer() passes no on_error callback), silently
    # disabling the whole always-on session archive - raw spectra, metrics,
    # and environment readings alike - whenever the target folder doesn't
    # already exist (e.g. a fresh install, or a project destination that
    # hasn't been created yet).
    base_dir.mkdir(parents=True, exist_ok=True)
    path = base_dir / f"session_{safe_session}_{stamp}.h5"
    setattr(window, _SESSION_PATH_ATTR, path)
    # Keep _metric_archive_writer_path in sync for backwards compat with reload tasks
    setattr(window, _WRITER_PATH_ATTR, path)
    return path


def ensure_session_writer(window: Any, spectrum: Any = None) -> Any:
    """Get-or-create the always-on session writer that records the full session.

    Uses a full AsyncHDF5MeasurementWriter (same schema as the measurement file) so
    the session file can be loaded by the evaluation app or used for reload after clear.
    Requires at least one spectrum to know the wavelength axis — returns None until then.
    """
    writer = getattr(window, _SESSION_WRITER_ATTR, None)
    if writer is not None:
        return writer

    if spectrum is None:
        return None

    try:
        wavelengths_nm = np.asarray(spectrum.wavelengths_nm, dtype=np.float64)
    except Exception:
        return None
    if len(wavelengths_nm) == 0:
        return None

    path = _session_file_path(window)

    try:
        from lspr_app.storage.hdf5_export import AsyncHDF5MeasurementWriter
    except Exception:
        return None

    try:
        processing = window._current_processing_settings()
    except Exception:
        return None

    session_name = getattr(getattr(window, "_session", None), "name", None) or "session"
    started_at = getattr(window, "_live_trace_started_at", None) or spectrum.acquired_at

    # Derived via PLOT_MODES (display label -> internal kind), not a raw
    # .lower() of the label text, so this stays correct regardless of what the
    # dropdown currently displays (e.g. "Raw" instead of the old "Sample").
    signal_mode = "sample"
    try:
        mode = window.PLOT_MODES.get(window.plot_selector.currentText())
        if mode in {"sample", "absorbance"}:
            signal_mode = mode
    except Exception:
        pass

    flush_interval = float(getattr(window, "_measurement_flush_interval_s", 1.0))

    try:
        writer = AsyncHDF5MeasurementWriter(
            path,
            signal_mode,
            wavelengths_nm,
            processing,
            experiment_name=str(session_name),
            started_at_utc=started_at,
            flush_interval_s=flush_interval,
        )
    except Exception:
        return None

    setattr(window, _SESSION_WRITER_ATTR, writer)
    # Keep backwards-compat aliases so reload tasks and plot-settings code keep working
    setattr(window, _WRITER_ATTR, writer)
    window._metric_archive_path = path
    try:
        from lspr_app.storage.app_config import save_app_setting

        save_app_setting("last_session_file_path", str(path))
    except Exception:
        pass
    # Stable anchor for this session file's t_ms column for the rest of its
    # lifetime (see append_processed_trace_history) - set once, here, not
    # touched again until close_session_writer clears it for the next file.
    window._metric_archive_started_at = started_at

    try:
        session = getattr(window, "_session", None)
        if session is not None:
            state = session.state
            writer.update_baselines(state.dark, state.reference)
    except Exception:
        pass
    try:
        if hasattr(window, "_acquisition_state_payload"):
            writer.update_acquisition_state(window._acquisition_state_payload())
    except Exception:
        pass

    return writer


def close_session_writer(window: Any) -> None:
    """Close and clear the always-on session writer."""
    writer = getattr(window, _SESSION_WRITER_ATTR, None)
    if writer is None:
        writer = getattr(window, _WRITER_ATTR, None)
    if writer is not None:
        try:
            close_fn = getattr(writer, "close", None)
            if callable(close_fn):
                close_fn()
        except Exception:
            pass
    setattr(window, _SESSION_WRITER_ATTR, None)
    setattr(window, _WRITER_ATTR, None)
    setattr(window, _WRITER_TEMP_ATTR, False)
    # Clear path attrs so the next live session generates a fresh timestamped file.
    setattr(window, _SESSION_PATH_ATTR, None)
    setattr(window, _WRITER_PATH_ATTR, None)
    # Clear the t_ms anchor too, so the next session file (ensure_session_writer)
    # gets its own fresh one instead of inheriting this file's.
    window._metric_archive_started_at = None


def close_temp_measurement_writer(window: Any) -> None:
    """Close the session writer (legacy name)."""
    close_session_writer(window)
