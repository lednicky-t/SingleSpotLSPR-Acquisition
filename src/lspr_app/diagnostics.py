from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Mapping, Any


def _env_flag(name: str, environ: Mapping[str, str] | None = None) -> bool:
    env = environ if environ is not None else os.environ
    return str(env.get(name, "")).strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class DiagnosticsConfig:
    quiet_mode: bool = False
    suppress_info_logs: bool = False
    export_diagnostic_events: bool = True

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "DiagnosticsConfig":
        return cls(
            quiet_mode=_env_flag("LSPR_QUIET_DIAGNOSTICS", environ),
            suppress_info_logs=_env_flag("LSPR_SUPPRESS_DIAGNOSTIC_INFO_LOGS", environ),
            export_diagnostic_events=not _env_flag("LSPR_DISABLE_DIAGNOSTIC_EXPORT", environ),
        )

    @classmethod
    def from_window(cls, window: Any) -> "DiagnosticsConfig":
        return cls(
            quiet_mode=bool(getattr(window, "_quiet_diagnostics_mode", False)),
            suppress_info_logs=bool(getattr(window, "_suppress_diagnostic_info_logs", False)),
            export_diagnostic_events=bool(getattr(window, "_export_diagnostic_events", True)),
        )

    def launch_flag_text(self) -> str:
        return (
            f"quiet={'on' if self.quiet_mode else 'off'} | "
            f"file_info={'off' if self.suppress_info_logs else 'on'} | "
            f"diag_export={'on' if self.export_diagnostic_events else 'off'}"
        )

    def summary_lines(self) -> list[str]:
        return [
            f"Diagnostics mode: {'quiet' if self.quiet_mode else 'normal'}",
            f"File info filter: {'off' if self.suppress_info_logs else 'on'}",
            f"Diagnostic export: {'on' if self.export_diagnostic_events else 'off'}",
        ]


def apply_diagnostic_info_filter(handler: logging.Handler, config: DiagnosticsConfig) -> None:
    if config.suppress_info_logs:
        handler.addFilter(lambda record: record.levelno >= logging.WARNING)
