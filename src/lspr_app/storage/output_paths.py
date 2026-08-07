from __future__ import annotations

from pathlib import Path
from typing import Any

# Moved into lspr_acq_shell.user_profile (Phase 1 shell extraction,
# 2026-08-07) since that module needs it too and now sits below this app in
# the dependency graph. Re-exported here so existing
# `from lspr_app.storage.output_paths import safe_path_component` call sites
# keep working unchanged.
from lspr_acq_shell.user_profile import safe_path_component

__all__ = ["safe_path_component", "build_recording_experiment_base_dir", "recording_experiment_base_dir_for"]


def build_recording_experiment_base_dir(
    project_destination: str,
    experiment_name: str,
    *,
    fallback_base: Path | None = None,
) -> Path:
    base_dir = (
        Path(project_destination).expanduser()
        if str(project_destination or "").strip()
        else (fallback_base or (Path.cwd() / "data"))
    )
    if str(experiment_name or "").strip():
        base_dir = base_dir / safe_path_component(experiment_name)
    return base_dir


def recording_experiment_base_dir_for(window: Any, *, fallback_base: Path | None = None) -> Path:
    project_destination = ""
    if hasattr(window, "recording_project_destination"):
        project_destination = str(window.recording_project_destination() or "").strip()
    experiment_name = ""
    if hasattr(window, "recording_experiment_name"):
        experiment_name = str(window.recording_experiment_name() or "").strip()
    return build_recording_experiment_base_dir(
        project_destination,
        experiment_name,
        fallback_base=fallback_base,
    )
