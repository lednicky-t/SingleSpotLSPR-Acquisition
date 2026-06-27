"""Specialised plot and log widgets used by the main acquisition window.

These are self-contained subclasses with no dependency on
``MainWindow`` state — they can be constructed and used independently.

Classes / functions
-------------------
``LogTerminalTextEdit``
    ``QTextEdit`` subclass with Ctrl+Wheel font-size adjustment.
``TimedPlotWidget``
    ``pyqtgraph.PlotWidget`` subclass that records paint timings and operation
    counts into attributes on a delegate owner object for diagnostics.
``_spline_render_series``
    Upsample a sparse (x, y) series to a smooth cubic spline for display.
"""

from __future__ import annotations

from time import perf_counter

import numpy as np
import pyqtgraph as pg
from scipy.interpolate import CubicSpline

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QTextEdit


class LogTerminalTextEdit(QTextEdit):
    """``QTextEdit`` that adjusts its font size with Ctrl+Wheel.

    Font size is clamped between ``_min_font_size`` (7 pt) and
    ``_max_font_size`` (16 pt).
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._min_font_size = 7.0
        self._max_font_size = 16.0

    def wheelEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta_y = event.angleDelta().y()
            if delta_y == 0:
                event.ignore()
                return
            factor = 1.1 if delta_y > 0 else 1 / 1.1
            current_font = QFont(self.font())
            current_size = float(current_font.pointSizeF())
            if current_size <= 0:
                current_size = float(current_font.pointSize()) if current_font.pointSize() > 0 else 9.0
            if current_size <= 0:
                current_size = 9.0
            font = QFont(current_font)
            new_size = max(self._min_font_size, min(current_size * factor, self._max_font_size))
            if new_size <= 0:
                new_size = self._min_font_size
            font.setPointSizeF(new_size)
            self.setFont(font)
            self.document().setDefaultFont(font)
            event.accept()
            return
        super().wheelEvent(event)


class TimedPlotWidget(pg.PlotWidget):
    """``pyqtgraph.PlotWidget`` that records paint / operation timings.

    On every ``paintEvent`` the widget writes elapsed-ms statistics into
    attributes on *paint_owner* (prefix-namespaced so multiple widgets can
    share one owner):

    ``_{prefix}_paint_count``
        Total number of paint calls.
    ``_{prefix}_paint_total_ms``
        Cumulative paint time in milliseconds.
    ``_{prefix}_paint_max_ms``
        Maximum single paint time.
    ``_{prefix}_paint_recent_*``
        Rolling stats over the last 10 seconds.

    Operation-count attributes are updated by :meth:`setLabel`,
    :meth:`setXRange`, :meth:`setYRange`, :meth:`enableAutoRange`, and
    :meth:`autoRange` via :meth:`_record_operation`.

    If *paint_owner* is ``None`` or *paint_prefix* is empty, all recording
    is silently skipped.
    """

    def __init__(self, *args, paint_owner=None, paint_prefix: str = "", **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._paint_owner = paint_owner
        self._paint_prefix = str(paint_prefix)
        self._diagnostics_owner = paint_owner
        self._diagnostics_prefix = str(paint_prefix)

    def _record_operation(self, operation: str) -> None:
        owner = getattr(self, "_diagnostics_owner", None)
        prefix = getattr(self, "_diagnostics_prefix", "")
        if owner is None or not prefix:
            return
        try:
            events = getattr(owner, "_plot_operation_events", None)
            if events is not None:
                events.append((perf_counter(), prefix, str(operation)))
            total_name = f"_{prefix}_{operation}_count"
            setattr(owner, total_name, int(getattr(owner, total_name, 0)) + 1)
        except Exception:
            return

    def paintEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        started = perf_counter()
        super().paintEvent(event)
        elapsed_ms = (perf_counter() - started) * 1000.0
        owner = getattr(self, "_paint_owner", None)
        prefix = getattr(self, "_paint_prefix", "")
        if owner is None or not prefix:
            return
        count_name = f"_{prefix}_paint_count"
        total_name = f"_{prefix}_paint_total_ms"
        max_name = f"_{prefix}_paint_max_ms"
        try:
            setattr(owner, count_name, int(getattr(owner, count_name, 0)) + 1)
            setattr(owner, total_name, float(getattr(owner, total_name, 0.0) or 0.0) + elapsed_ms)
            current_max = float(getattr(owner, max_name, 0.0) or 0.0)
            setattr(owner, max_name, max(current_max, elapsed_ms))
            events = getattr(owner, "_plot_paint_events", None)
            if events is not None:
                now = perf_counter()
                events.append((now, prefix, float(elapsed_ms)))
                recent_values = [value for ts, name, value in events if name == prefix and (now - float(ts)) <= 10.0]
                recent_count_name = f"_{prefix}_paint_recent_count"
                recent_total_name = f"_{prefix}_paint_recent_total_ms"
                recent_max_name = f"_{prefix}_paint_recent_max_ms"
                recent_avg_name = f"_{prefix}_paint_recent_avg_ms"
                if recent_values:
                    recent_total_ms = float(sum(recent_values))
                    setattr(owner, recent_count_name, len(recent_values))
                    setattr(owner, recent_total_name, recent_total_ms)
                    setattr(owner, recent_max_name, max(recent_values))
                    setattr(owner, recent_avg_name, recent_total_ms / len(recent_values))
                else:
                    setattr(owner, recent_count_name, 0)
                    setattr(owner, recent_total_name, 0.0)
                    setattr(owner, recent_max_name, 0.0)
                    setattr(owner, recent_avg_name, 0.0)
            notify_startup_paint = getattr(owner, "_notify_startup_plot_painted", None)
            if callable(notify_startup_paint):
                notify_startup_paint(prefix)
        except Exception:
            return

    def setLabel(self, *args, **kwargs):  # pragma: no cover - GUI runtime path
        self._record_operation("setLabel")
        plot_item = self.getPlotItem()
        return plot_item.setLabel(*args, **kwargs)

    def setXRange(self, *args, **kwargs):  # pragma: no cover - GUI runtime path
        self._record_operation("setXRange")
        return super().setXRange(*args, **kwargs)

    def setYRange(self, *args, **kwargs):  # pragma: no cover - GUI runtime path
        self._record_operation("setYRange")
        return super().setYRange(*args, **kwargs)

    def enableAutoRange(self, *args, **kwargs):  # pragma: no cover - GUI runtime path
        self._record_operation("enableAutoRange")
        return super().enableAutoRange(*args, **kwargs)

    def autoRange(self, *args, **kwargs):  # pragma: no cover - GUI runtime path
        self._record_operation("autoRange")
        return super().autoRange(*args, **kwargs)


def _spline_render_series(
    x: np.ndarray,
    y: np.ndarray,
    *,
    max_points: int = 4096,
) -> tuple[np.ndarray, np.ndarray]:
    """Upsample a sparse series to a smooth cubic spline for rendering.

    Filters non-finite values, deduplicates x-coordinates, and fits a
    natural cubic spline.  If the series has fewer than 4 points or the
    desired point count does not exceed the input count the original arrays
    are returned unchanged.

    Args:
        x: 1-D array of x-coordinates.
        y: 1-D array of y-coordinates.
        max_points: Maximum number of points in the returned dense series.

    Returns:
        ``(dense_x, dense_y)`` numpy arrays ready for plotting.
    """
    x_values = np.asarray(x, dtype=np.float64)
    y_values = np.asarray(y, dtype=np.float64)
    finite = np.isfinite(x_values) & np.isfinite(y_values)
    x_values = x_values[finite]
    y_values = y_values[finite]
    if len(x_values) < 4:
        return x_values, y_values
    unique_x, unique_indices = np.unique(x_values, return_index=True)
    y_values = y_values[unique_indices]
    x_values = unique_x
    if len(x_values) < 4:
        return x_values, y_values
    point_count = min(max(len(x_values) * 3, len(x_values)), max_points)
    if point_count <= len(x_values):
        return x_values, y_values
    spline = CubicSpline(x_values, y_values, bc_type="natural")
    dense_x = np.linspace(float(x_values[0]), float(x_values[-1]), point_count, dtype=np.float64)
    dense_y = spline(dense_x)
    return dense_x, np.asarray(dense_y, dtype=np.float64)
