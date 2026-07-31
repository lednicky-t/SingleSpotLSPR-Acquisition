"""File > New Session…

Starts a fresh session without restarting the app: clears the current
sample/absorbance trace, closes the always-on session HDF5 writer so the
next spectrum starts a brand-new file (see storage/measurement_archive.py),
and optionally keeps the currently captured dark/reference spectra so they
don't need to be re-measured.

Only the active MeasurementSession (hardware or simulation - see
apply_source_mode_for in main_window_state.py) is reset; the other source's
session is left untouched, matching how switching between them already
keeps their dark/reference/sample state independent.
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)


class NewSessionDialog(QDialog):
    """Confirmation dialog with keep-dark/keep-reference checkboxes."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New session")
        self.setModal(True)
        self.setMinimumWidth(420)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        warning = QLabel(
            "This clears the current sample/absorbance trace and starts a new "
            "measurement file. This cannot be undone."
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)

        box = QGroupBox("Keep from the current session")
        form = QFormLayout(box)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)

        self.keep_dark_check = QCheckBox("Dark spectrum")
        self.keep_dark_check.setChecked(True)
        form.addRow(self.keep_dark_check)

        self.keep_reference_check = QCheckBox("Reference spectrum")
        self.keep_reference_check.setChecked(True)
        form.addRow(self.keep_reference_check)

        layout.addWidget(box)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_btn = bb.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setText("Start New Session")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        layout.addWidget(bb)

    @property
    def keep_dark(self) -> bool:
        return bool(getattr(self, "keep_dark_check", None) and self.keep_dark_check.isChecked())

    @property
    def keep_reference(self) -> bool:
        return bool(getattr(self, "keep_reference_check", None) and self.keep_reference_check.isChecked())


def show_new_session_dialog_for(window) -> None:
    """Entry point wired to File > New Session…"""
    if getattr(window, "_measurement_active", False):
        answer = QMessageBox.question(
            window,
            "Recording in progress",
            "A measurement recording is currently active.\nStop recording and start a new session?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        window._stop_measurement_run()

    dialog = NewSessionDialog(parent=window)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return

    _start_new_session_for(window, keep_dark=dialog.keep_dark, keep_reference=dialog.keep_reference)


def _start_new_session_for(window, *, keep_dark: bool, keep_reference: bool) -> None:
    from lspr_app.domain.models import SessionState
    from lspr_app.storage.app_config import save_dark_reference_cache
    from lspr_app.storage.measurement_archive import close_session_writer
    from lspr_app.gui.main_window_plotting import clear_trace_history_for

    session = window._session
    dark = session.state.dark if keep_dark else None
    reference = session.state.reference if keep_reference else None
    session.state = SessionState(dark=dark, reference=reference)

    # Dropping the writer/path here (rather than creating a file immediately)
    # matches how sessions already start lazily - ensure_session_writer picks
    # a fresh timestamped path and re-seeds it with the dark/reference kept
    # above the moment the next spectrum arrives.
    close_session_writer(window)
    save_dark_reference_cache(dark, reference)

    clear_trace_history_for(window)
    window._update_dark_reference_button_icons()
    window._refresh_plot()

    kept = [name for name, flag in (("dark", keep_dark), ("reference", keep_reference)) if flag]
    kept_text = f" (kept {', '.join(kept)} spectrum)" if kept else ""
    window._log_success(f"Started a new session{kept_text}.")
    window.status_label.setText("New session started.")
