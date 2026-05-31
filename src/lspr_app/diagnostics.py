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

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "DiagnosticsConfig":
        return cls(
            quiet_mode=_env_flag("LSPR_QUIET_DIAGNOSTICS", environ),
            suppress_info_logs=_env_flag("LSPR_SUPPRESS_DIAGNOSTIC_INFO_LOGS", environ),
        )

    @classmethod
    def from_window(cls, window: Any) -> "DiagnosticsConfig":
        return cls(
            quiet_mode=bool(getattr(window, "_quiet_diagnostics_mode", False)),
            suppress_info_logs=bool(getattr(window, "_suppress_diagnostic_info_logs", False)),
        )

    def launch_flag_text(self) -> str:
        return f"quiet={'on' if self.quiet_mode else 'off'} | file_info={'off' if self.suppress_info_logs else 'on'}"

    def summary_lines(self) -> list[str]:
        return [
            f"Diagnostics mode: {'quiet' if self.quiet_mode else 'normal'}",
            f"File info filter: {'off' if self.suppress_info_logs else 'on'}",
        ]


def apply_diagnostic_info_filter(handler: logging.Handler, config: DiagnosticsConfig) -> None:
    if config.suppress_info_logs:
        handler.addFilter(lambda record: record.levelno >= logging.WARNING)
