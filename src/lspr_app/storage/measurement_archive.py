from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import h5py
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

    signal_mode = "sample"
    try:
        text = window.plot_selector.currentText().lower()
        if text in {"sample", "absorbance"}:
            signal_mode = text
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
    setattr(window, "_metric_archive_path", path)
    # Stable anchor for this session file's t_ms column for the rest of its
    # lifetime (see append_processed_trace_history) - set once, here, not
    # touched again until close_session_writer clears it for the next file.
    setattr(window, "_metric_archive_started_at", started_at)

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
    setattr(window, "_metric_archive_started_at", None)


# ---------------------------------------------------------------------------
# Legacy helpers — kept for backwards compatibility
# ---------------------------------------------------------------------------

def ensure_temp_measurement_path(window: Any) -> Path:
    """Return the session file path (legacy name kept for callers that use it)."""
    return _session_file_path(window)


def ensure_temp_measurement_writer(window: Any) -> Any:
    """Return the session writer if one already exists, otherwise fall back to the
    lightweight metric-only writer so callers that invoke this before a spectrum
    is available still get *something* usable."""
    writer = getattr(window, _SESSION_WRITER_ATTR, None) or getattr(window, _WRITER_ATTR, None)
    if writer is not None:
        return writer

    path = _session_file_path(window)
    writer = _FallbackTempMeasurementWriter.open(path)

    setattr(window, _WRITER_ATTR, writer)
    setattr(window, "_metric_archive_path", path)
    setattr(window, _WRITER_TEMP_ATTR, True)
    return writer


def close_temp_measurement_writer(window: Any) -> None:
    """Close the session writer (legacy name)."""
    close_session_writer(window)


def metric_archive_path(window: Any) -> Path:
    return _session_file_path(window)


@dataclass
class _FallbackTempMeasurementWriter:
    path: Path
    handle: h5py.File

    @classmethod
    def open(cls, path: Path) -> "_FallbackTempMeasurementWriter":
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = h5py.File(path, "a")
        processed = handle.require_group("processed").require_group("metrics")
        if "t_ms" not in processed:
            processed.create_dataset("t_ms", shape=(0,), maxshape=(None,), dtype="f8")
        handle.require_group("raw").require_group("events")
        handle.require_group("device_state")
        handle.require_group("control")
        return cls(path=path, handle=handle)

    def _append_generic_event(self, group_path: str, payload: dict[str, Any]) -> None:
        group = self.handle.require_group(group_path)
        dataset_name = "events"
        if dataset_name not in group:
            dt = h5py.string_dtype(encoding="utf-8")
            group.create_dataset(dataset_name, shape=(0,), maxshape=(None,), dtype=dt)
        ds = group[dataset_name]
        row = np.array([str(payload)], dtype=ds.dtype)
        size = int(ds.shape[0])
        ds.resize((size + 1,))
        ds[size] = row[0]

    def append_metrics(self, rows: list[dict[str, Any]]) -> None:
        metrics = self.handle["processed"]["metrics"]
        for row in rows:
            if not isinstance(row, dict):
                try:
                    row = dict(row)
                except Exception:
                    row = {}
            t_ms = row.get("t_ms")
            if t_ms is None:
                t_ms = row.get("acquired_at_unix_ms")
            if t_ms is None:
                t_ms = float(metrics["t_ms"].shape[0]) * 1000.0
            ds = metrics["t_ms"]
            size = int(ds.shape[0])
            ds.resize((size + 1,))
            ds[size] = float(t_ms)
            for key, value in row.items():
                if key in {"t_ms", "acquired_at_unix_ms", "sample_index"}:
                    continue
                if key not in metrics:
                    metrics.create_dataset(key, shape=(0,), maxshape=(None,), dtype="f8")
                ds_key = metrics[key]
                ds_size = int(ds_key.shape[0])
                ds_key.resize((ds_size + 1,))
                try:
                    ds_key[ds_size] = float(np.asarray(value).item())
                except Exception:
                    ds_key[ds_size] = np.nan
        self.handle.flush()

    def append_batch(self, batch: list[Any], *args: Any, **kwargs: Any) -> None:
        payload = {
            "kind": "raw_batch",
            "batch_size": len(batch),
            "args": [str(arg) for arg in args],
            "kwargs": {key: str(value) for key, value in kwargs.items()},
        }
        self._append_generic_event("raw", payload)
        self.handle.flush()

    def flush(self) -> None:
        self.handle.flush()

    def close(self) -> None:
        self.handle.flush()
        self.handle.close()
