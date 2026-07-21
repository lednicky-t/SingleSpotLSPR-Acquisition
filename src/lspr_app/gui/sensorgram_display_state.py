from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lspr_app.gui.workers import MetricArchiveReloadTask


@dataclass(slots=True)
class SensorgramDisplayState:
    mode: str = "session"
    view_locked: bool = False
    frozen: bool = False
    reload_loading: bool = False
    reload_task: "MetricArchiveReloadTask | None" = None
    reload_request_token: tuple[object, ...] | None = None
    reload_pending_token: tuple[object, ...] | None = None

    def begin_measurement(self) -> None:
        """Atomic transition for start_measurement_run().

        Also releases any stale pan/zoom lock so the view re-follows once
        recording starts, matching what end_measurement already does on stop.
        """
        self.mode = "measurement"
        self.view_locked = False

    def end_measurement(self) -> None:
        """Atomic transition for stop_measurement_run()."""
        self.mode = "session"
        self.view_locked = False

    def begin_reload(self, request_token: tuple[object, ...], task: object) -> None:
        self.reload_request_token = request_token
        self.reload_loading = True
        self.reload_pending_token = None
        self.reload_task = task

    def finish_reload(self) -> None:
        self.reload_loading = False
        self.reload_task = None
