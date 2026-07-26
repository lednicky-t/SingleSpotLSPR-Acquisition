"""Unified import-from-measurement-file dialog.

Probes an HDF5 measurement file and lets the user select which sections to
import into the running application:
  * Processing settings (wavelength range, baseline, smoothing, fitting)
  * Experiment plan (steps, timing, device assignments, labels)

Usage from the main window::

    show_import_from_measurement_for(window)         # opens file picker first
    show_import_from_measurement_for(window, path)   # uses given path directly
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

try:
    import h5py as _h5py
except ImportError:  # pragma: no cover
    _h5py = None

log = logging.getLogger(__name__)


# ── File probe ────────────────────────────────────────────────────────────────

@dataclass
class _SectionInfo:
    available: bool = False
    summary: str = "Not found in this file"


@dataclass
class MeasurementFileProbe:
    path: Path
    schema_version: str = ""
    processing: _SectionInfo = field(default_factory=_SectionInfo)
    plan: _SectionInfo = field(default_factory=_SectionInfo)
    error: str = ""
    warning: str = ""


def probe_measurement_hdf5(path: Path) -> MeasurementFileProbe:
    """Open *path* read-only and detect which importable sections are present."""
    probe = MeasurementFileProbe(path=path)
    if _h5py is None:
        probe.error = "h5py is not installed."
        return probe
    try:
        with _h5py.File(path, "r") as f:
            # Schema version
            ver = f.attrs.get("schema_version", "")
            if isinstance(ver, bytes):
                ver = ver.decode("utf-8")
            probe.schema_version = str(ver)

            # Reject files from an incompatible (newer-major) schema outright,
            # same as the plan-import path already does - a user picking an
            # arbitrary .h5 file here shouldn't get a silently-wrong import
            # from a future format this app version doesn't understand yet.
            # Older/minor mismatches are shown as a warning but don't block.
            try:
                from lspr_io import validate_measurement_metadata

                validation = validate_measurement_metadata(dict(f.attrs))
                if not validation.is_valid:
                    probe.error = "; ".join(validation.errors)
                    return probe
                if validation.warnings:
                    probe.warning = "; ".join(validation.warnings)
            except Exception:
                log.debug("Could not validate measurement schema for %s", path, exc_info=True)

            # ── Processing settings ───────────────────────────────────────
            try:
                processed = f.get("processed")
                metrics = processed.get("metrics") if processed is not None else None
                config = metrics.get("config") if metrics is not None else None
                if config is not None and "processing_settings_json" in config:
                    raw = config["processing_settings_json"][()]
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    payload = json.loads(raw)
                    wl_min = payload.get("wavelength_min_nm", "?")
                    wl_max = payload.get("wavelength_max_nm", "?")
                    baseline = payload.get("baseline_method", "?")
                    smoothing = payload.get("smoothing_method", "?")
                    fit = payload.get("fit_method", "?")
                    probe.processing = _SectionInfo(
                        available=True,
                        summary=(
                            f"Range {wl_min}–{wl_max} nm  ·  "
                            f"baseline={baseline}  ·  smooth={smoothing}  ·  fit={fit}"
                        ),
                    )
                else:
                    metadata = f.get("metadata")
                    if metadata is not None and "processing_range_min_nm" in metadata.attrs:
                        wl_min = float(metadata.attrs.get("processing_range_min_nm", 0))
                        wl_max = float(metadata.attrs.get("processing_range_max_nm", 0))
                        baseline = str(metadata.attrs.get("processing_baseline_method", "?"))
                        probe.processing = _SectionInfo(
                            available=True,
                            summary=(
                                f"Range {wl_min:.0f}–{wl_max:.0f} nm  ·  "
                                f"baseline={baseline}  (legacy format)"
                            ),
                        )
            except Exception:
                log.debug("Could not probe processing settings in %s", path, exc_info=True)

            # ── Experiment plan ───────────────────────────────────────────
            try:
                metadata = f.get("metadata")
                plan_ds = None
                if metadata is not None and "experiment_plan" in metadata:
                    plan_ds = metadata["experiment_plan"]
                elif "plans" in f and "experiment_plan" in f["plans"]:
                    plan_ds = f["plans"]["experiment_plan"]
                if plan_ds is not None:
                    n_steps = plan_ds.shape[0] if len(plan_ds.shape) >= 1 else 0
                    duration_text = ""
                    cols_attr = plan_ds.attrs.get("columns")
                    if cols_attr is not None:
                        cols = [
                            c.decode("utf-8") if isinstance(c, bytes) else str(c)
                            for c in cols_attr
                        ]
                        if "duration_s" in cols:
                            dur_idx = cols.index("duration_s")
                            total_s = 0.0
                            for row in plan_ds[()]:
                                try:
                                    total_s += float(str(row[dur_idx]))
                                except (ValueError, IndexError):
                                    pass
                            duration_text = f"  ·  {total_s:.0f} s total"
                    at_parts: list[str] = []
                    at_group = None
                    if metadata is not None and "assignment_tables" in metadata:
                        at_group = metadata["assignment_tables"]
                    if at_group is not None:
                        if "switch_solution_map" in at_group:
                            n = at_group["switch_solution_map"].shape[0]
                            at_parts.append(f"{n} switch labels")
                        if "valve_state_map" in at_group:
                            at_parts.append("valve colors")
                        if "color_palette_entries" in at_group:
                            n = at_group["color_palette_entries"].shape[0]
                            at_parts.append(f"{n} palette entries")
                    at_text = ""
                    if at_parts:
                        at_text = "  ·  labels: " + ", ".join(at_parts)
                    probe.plan = _SectionInfo(
                        available=True,
                        summary=f"{n_steps} steps{duration_text}{at_text}",
                    )
            except Exception:
                log.debug("Could not probe experiment plan in %s", path, exc_info=True)

    except Exception as exc:
        probe.error = str(exc)
        log.debug("Could not open measurement file %s for probing: %s", path, exc)
    return probe


# ── Background probe ─────────────────────────────────────────────────────────

class _ProbeSignals(QObject):
    done = pyqtSignal(object)


class _ProbeTask(QRunnable):
    def __init__(self, path: Path, signals: _ProbeSignals) -> None:
        super().__init__()
        self._path = path
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        result = probe_measurement_hdf5(self._path)
        self._signals.done.emit(result)


# ── Dialog ────────────────────────────────────────────────────────────────────

class ImportFromMeasurementDialog(QDialog):
    """Section-selection dialog for importing from an HDF5 measurement file."""

    def __init__(self, probe: MeasurementFileProbe, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Import from measurement file")
        self.setModal(True)
        self.setMinimumWidth(460)
        self._probe = probe
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # File header
        name_label = QLabel(f"<b>{self._probe.path.name}</b>")
        name_label.setWordWrap(True)
        layout.addWidget(name_label)
        if self._probe.schema_version:
            ver_label = QLabel(f"HDF5 schema version {self._probe.schema_version}")
            ver_label.setStyleSheet("color: gray; font-size: 10px;")
            layout.addWidget(ver_label)
        if self._probe.warning:
            warn_label = QLabel(self._probe.warning)
            warn_label.setWordWrap(True)
            warn_label.setStyleSheet("color: #b8860b; font-size: 10px;")
            layout.addWidget(warn_label)

        if self._probe.error:
            err = QLabel(f"Could not read file:\n{self._probe.error}")
            err.setWordWrap(True)
            err.setStyleSheet("color: #b44a4a;")
            layout.addWidget(err)
            bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
            bb.rejected.connect(self.reject)
            layout.addWidget(bb)
            return

        # Sections
        box = QGroupBox("Select sections to import")
        form = QFormLayout(box)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        # Processing settings row
        self.processing_check = QCheckBox("Processing settings")
        self.processing_check.setChecked(self._probe.processing.available)
        self.processing_check.setEnabled(self._probe.processing.available)
        proc_detail = QLabel(self._probe.processing.summary)
        proc_detail.setWordWrap(True)
        if not self._probe.processing.available:
            proc_detail.setStyleSheet("color: gray;")
        form.addRow(self.processing_check, proc_detail)

        # Experiment plan row
        self.plan_check = QCheckBox("Experiment plan")
        self.plan_check.setChecked(self._probe.plan.available)
        self.plan_check.setEnabled(self._probe.plan.available)
        plan_detail = QLabel(self._probe.plan.summary)
        plan_detail.setWordWrap(True)
        if not self._probe.plan.available:
            plan_detail.setStyleSheet("color: gray;")
        form.addRow(self.plan_check, plan_detail)

        # Runtime sub-option (indented under plan)
        self.runtime_check = QCheckBox("  Restore last active step")
        self.runtime_check.setChecked(self._probe.plan.available)
        self.runtime_check.setEnabled(self._probe.plan.available)
        self.runtime_check.setToolTip(
            "Restore the step that was active at the end of the recorded run."
        )
        self.plan_check.toggled.connect(
            lambda checked: self.runtime_check.setEnabled(checked and self._probe.plan.available)
        )
        form.addRow("", self.runtime_check)

        layout.addWidget(box)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("Import")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    @property
    def wants_processing(self) -> bool:
        return bool(getattr(self, "processing_check", None) and self.processing_check.isChecked())

    @property
    def wants_plan(self) -> bool:
        return bool(getattr(self, "plan_check", None) and self.plan_check.isChecked())

    @property
    def wants_runtime(self) -> bool:
        return bool(getattr(self, "runtime_check", None) and self.runtime_check.isChecked())


# ── Top-level entry point ─────────────────────────────────────────────────────

def show_import_from_measurement_for(window, path: Path | None = None) -> None:
    """Show the unified import dialog and apply selected sections.

    If *path* is None a file-picker dialog is shown first.
    The HDF5 probe runs in a background thread so the UI stays responsive
    while reading files on slow or network-mounted drives.
    """
    from lspr_app.storage.app_config import DEFAULT_CONFIG_PATH  # lazy — avoids circular import

    if path is None:
        start_dir = DEFAULT_CONFIG_PATH.parent
        start_dir.mkdir(parents=True, exist_ok=True)
        path_str, _ = QFileDialog.getOpenFileName(
            window,
            "Import from measurement file",
            str(start_dir),
            "LSPR measurement files (*.h5 *.hdf5);;All files (*)",
        )
        if not path_str:
            return
        path = Path(path_str)

    signals = _ProbeSignals()
    signals.done.connect(lambda probe: _on_probe_done(window, probe))
    QThreadPool.globalInstance().start(_ProbeTask(path, signals))


def _on_probe_done(window, probe: MeasurementFileProbe) -> None:
    """Called on the main thread once the background probe completes."""
    if getattr(window, "_closing", False):
        return

    dialog = ImportFromMeasurementDialog(probe, parent=window)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return

    path = probe.path
    errors: list[str] = []
    any_done = False

    # ── Processing settings ───────────────────────────────────────────────
    if dialog.wants_processing:
        try:
            import dataclasses
            from lspr_app.storage.app_config import (
                load_processing_settings_from_hdf5,
                save_processing_settings,
            )
            from lspr_app.gui.main_window_processing import apply_processing_settings_to_widgets
            settings = load_processing_settings_from_hdf5(path)
            # Preserve current sensorgram metric selection so curves are not
            # replaced by whatever the imported file had recorded.
            current = getattr(window, "_processing_settings", None)
            visible_modes = getattr(window, "_sensorgram_metric_visible_modes", None)
            primary_mode = getattr(window, "_sensorgram_metric_primary_mode", None)
            if visible_modes or primary_mode or current is not None:
                settings = dataclasses.replace(
                    settings,
                    spectrum_tracking_mode=(
                        primary_mode
                        if primary_mode
                        else (current.spectrum_tracking_mode if current else settings.spectrum_tracking_mode)
                    ),
                    trace_metrics=(
                        sorted(visible_modes)
                        if visible_modes
                        else (current.trace_metrics if current else settings.trace_metrics)
                    ),
                    trace_noise_window_s=(
                        current.trace_noise_window_s if current else settings.trace_noise_window_s
                    ),
                )
            window._processing_settings = settings
            apply_processing_settings_to_widgets(window, settings)
            save_processing_settings(settings)
            window._refresh_plot()
            window._log_success(f"Processing settings imported from {path.name}.")
            any_done = True
        except Exception as exc:
            log.exception("Processing settings import failed")
            errors.append(f"Processing settings: {exc}")

    # ── Experiment plan ───────────────────────────────────────────────────
    if dialog.wants_plan:
        try:
            from lspr_app.gui.main_window_lifecycle import ensure_experiment_control_panel_for
            ensure_experiment_control_panel_for(window)
            ecw = getattr(window, "_experiment_control_window", None)
            if ecw is None:
                errors.append("Experiment plan: could not open the experiment control panel.")
            else:
                ecw._start_experiment_plan_import(
                    path,
                    preset_hdf5_options=(True, dialog.wants_runtime),
                )
                any_done = True
        except Exception as exc:
            log.exception("Experiment plan import failed")
            errors.append(f"Experiment plan: {exc}")

    if any_done and hasattr(window, "status_label"):
        window.status_label.setText(f"Imported from {path.name}")
    if errors:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(
            window,
            "Import from measurement file",
            "Some sections could not be imported:\n" + "\n".join(f"  • {e}" for e in errors),
        )
