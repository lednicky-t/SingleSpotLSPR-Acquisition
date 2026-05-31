from __future__ import annotations

import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Callable

from PyQt6.QtCore import QObject, QTimer


@dataclass(slots=True)
class _ScheduledTask:
    name: str
    due_at: float
    callback: Callable[[], None]
    priority: int
    sequence: int


class GuiTaskScheduler(QObject):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._dispatch_due_tasks)
        self._tasks: dict[str, _ScheduledTask] = {}
        self._sequence = 0
        self._last_dispatch_lag_ms: float | None = None
        self._last_dispatch_duration_ms: float | None = None
        self._last_dispatch_task_count: int = 0
        self._max_dispatch_lag_ms: float | None = None
        self._max_dispatch_duration_ms: float | None = None
        self._dispatch_count = 0

    def request(self, name: str, delay_ms: float, callback: Callable[[], None], *, priority: int = 0) -> None:
        now = perf_counter()
        due_at = now + max(float(delay_ms), 0.0) / 1000.0
        task = self._tasks.get(str(name))
        if task is None:
            task = _ScheduledTask(
                name=str(name),
                due_at=due_at,
                callback=callback,
                priority=int(priority),
                sequence=self._sequence,
            )
            self._sequence += 1
        else:
            task.due_at = min(task.due_at, due_at)
            task.callback = callback
            task.priority = int(priority)
        self._tasks[task.name] = task
        self._arm_timer()

    def cancel(self, name: str) -> None:
        self._tasks.pop(str(name), None)
        self._arm_timer()

    def clear(self) -> None:
        self._tasks.clear()
        self._timer.stop()

    def is_pending(self, name: str) -> bool:
        return str(name) in self._tasks

    def pending_count(self) -> int:
        return len(self._tasks)

    def _arm_timer(self) -> None:
        if not self._tasks:
            self._timer.stop()
            return
        next_due = min(task.due_at for task in self._tasks.values())
        delay_ms = max(int(round((next_due - perf_counter()) * 1000.0)), 0)
        self._timer.start(delay_ms)

    def _dispatch_due_tasks(self) -> None:
        dispatch_started = perf_counter()
        now = dispatch_started
        due_tasks = [task for task in self._tasks.values() if task.due_at <= now]
        if not due_tasks:
            self._arm_timer()
            return
        earliest_due = min(task.due_at for task in due_tasks)
        lag_ms = max((dispatch_started - earliest_due) * 1000.0, 0.0)
        self._last_dispatch_lag_ms = lag_ms
        self._last_dispatch_task_count = len(due_tasks)
        self._max_dispatch_lag_ms = lag_ms if self._max_dispatch_lag_ms is None else max(self._max_dispatch_lag_ms, lag_ms)
        for task in sorted(due_tasks, key=lambda item: (item.priority, item.due_at, item.sequence)):
            self._tasks.pop(task.name, None)
            try:
                task.callback()
            except Exception:
                logging.getLogger("lspr_app.scheduler").exception("GUI scheduler task failed: %s", task.name)
        duration_ms = (perf_counter() - dispatch_started) * 1000.0
        self._last_dispatch_duration_ms = duration_ms
        self._max_dispatch_duration_ms = (
            duration_ms if self._max_dispatch_duration_ms is None else max(self._max_dispatch_duration_ms, duration_ms)
        )
        self._dispatch_count += 1
        if lag_ms >= 100.0 or duration_ms >= 25.0:
            logging.getLogger("lspr_app.scheduler").info(
                "GUI scheduler dispatch | due=%d | lag=%.2f ms | duration=%.2f ms | pending=%d",
                len(due_tasks),
                lag_ms,
                duration_ms,
                len(self._tasks),
            )
        self._arm_timer()
