from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from lspr_app.gui.workers import SessionCopySignals
from lspr_app.storage.output_paths import recording_experiment_base_dir_for, safe_path_component


def save_session_copy_as_for(window) -> None:
    writer = getattr(window, "_session_writer", None)
    source_path = getattr(window, "_session_writer_path", None)
    if writer is None or source_path is None:
        QMessageBox.information(
            window,
            "Nothing to save yet",
            "No spectra have been acquired this session yet.",
        )
        return

    session_name = getattr(getattr(window, "_session", None), "name", None) or "session"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    default_name = f"{safe_path_component(session_name)}_copy_{timestamp}.h5"
    base_dir = recording_experiment_base_dir_for(window, fallback_base=Path.cwd() / "data")
    default_path = base_dir / default_name

    file_path, _ = QFileDialog.getSaveFileName(
        window,
        "Save session copy",
        str(default_path),
        "HDF5 files (*.h5)",
    )
    if not file_path:
        return
    dest_path = Path(file_path)
    if dest_path.suffix.lower() != ".h5":
        dest_path = dest_path.with_suffix(".h5")

    window.status_label.setText(f"Saving session copy to {dest_path.name}...")
    window._log_info(f"Saving session copy to {dest_path}.")

    signals = SessionCopySignals()
    signals.finished.connect(window._handle_session_copy_finished)
    # Kept alive on window until the callback fires - a QObject with no
    # surviving Python reference can be garbage-collected even with live
    # connections (same reasoning as _measurement_writer_error_signals in
    # acquisition_controller.py).
    window._session_copy_signals = signals
    dest_path_str = str(dest_path)
    writer.save_copy(dest_path, on_done=lambda ok, msg: signals.finished.emit(ok, msg, dest_path_str))


def handle_session_copy_finished_for(window, success: bool, message: str, dest_path: str) -> None:
    window._session_copy_signals = None
    if success:
        window.status_label.setText(f"Session copy saved: {Path(dest_path).name}")
        window._log_success(f"Session copy saved to {dest_path}.")
        return
    window.status_label.setText("Session copy failed.")
    window._log_warning(f"Session copy to {dest_path} failed: {message}")
    QMessageBox.warning(
        window,
        "Save session copy failed",
        f"Could not save a copy of the current session:\n{message}",
    )
