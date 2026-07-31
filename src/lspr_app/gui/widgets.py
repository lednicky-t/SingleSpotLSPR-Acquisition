from __future__ import annotations

from datetime import datetime, timedelta
import logging
import math
from time import perf_counter
import pyqtgraph as pg

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QAction, QActionGroup, QColor, QFontMetrics, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLayout,
    QMenu,
    QSplitter,
    QSplitterHandle,
    QToolButton,
    QVBoxLayout,
    QSizePolicy,
    QWidget,
)


class SeparatorStyle:
    handle_alpha = 130
    handle_width = 2
    length_ratio = 0.5
    min_length = 4


def set_separator_style(*, alpha: int | None = None, width: int | None = None, length_ratio: float | None = None, min_length: int | None = None) -> None:
    if alpha is not None:
        SeparatorStyle.handle_alpha = max(0, min(int(alpha), 255))
    if width is not None:
        SeparatorStyle.handle_width = max(int(width), 1)
    if length_ratio is not None:
        SeparatorStyle.length_ratio = max(float(length_ratio), 0.05)
    if min_length is not None:
        SeparatorStyle.min_length = max(int(min_length), 1)


def configure_compact_splitter(splitter: QSplitter, *, handle_width: int | None = None) -> QSplitter:
    splitter.setHandleWidth(int(handle_width or 12))
    return splitter


class FlexibleTimeAxis(pg.AxisItem):
    doubleClicked = pyqtSignal()

    def __init__(self, orientation: str = "bottom") -> None:
        super().__init__(orientation=orientation)
        self._mode = "elapsed"
        self._start_datetime: datetime | None = None
        self._diagnostics_owner = None
        self._diagnostics_prefix = ""
        # Double-clicking this axis toggles the time display mode - a pointing
        # hand cursor and tooltip are the only hints that it's clickable at
        # all, since it looks like a plain, inert axis otherwise.
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Double-click to change how the time axis is displayed (elapsed HH:MM:SS / elapsed seconds / local clock time).")

    def _record_tickstrings(self, count: int, elapsed_ms: float) -> None:
        owner = getattr(self, "_diagnostics_owner", None)
        prefix = getattr(self, "_diagnostics_prefix", "")
        if owner is None or not prefix:
            return
        try:
            events = getattr(owner, "_axis_tick_events", None)
            if events is not None:
                now = perf_counter()
                events.append((now, prefix, int(count), float(elapsed_ms)))
            recent_count_name = f"_{prefix}_tick_recent_count"
            recent_total_name = f"_{prefix}_tick_recent_total_ms"
            recent_max_name = f"_{prefix}_tick_recent_max_ms"
            recent_avg_name = f"_{prefix}_tick_recent_avg_ms"
            recent_values = [value for ts, name, _, value in events if name == prefix and (now - float(ts)) <= 10.0]
            if recent_values:
                recent_total = float(sum(recent_values))
                setattr(owner, recent_count_name, len(recent_values))
                setattr(owner, recent_total_name, recent_total)
                setattr(owner, recent_max_name, max(recent_values))
                setattr(owner, recent_avg_name, recent_total / len(recent_values))
            else:
                setattr(owner, recent_count_name, 0)
                setattr(owner, recent_total_name, 0.0)
                setattr(owner, recent_max_name, 0.0)
                setattr(owner, recent_avg_name, 0.0)
        except Exception:
            return

    def set_time_mode(self, mode: str, *, start_datetime: datetime | None = None) -> None:
        normalized = mode if mode in {"elapsed", "seconds", "clock"} else "elapsed"
        if self._mode != normalized:
            self._mode = normalized
        self._start_datetime = start_datetime if normalized == "clock" else None
        self.picture = None
        self.update()

    def set_mode(self, mode: str) -> None:
        self.set_time_mode(mode)

    def mouseDoubleClickEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        self.doubleClicked.emit()
        if event is not None and hasattr(event, "accept"):
            event.accept()
        super().mouseDoubleClickEvent(event)

    def _format_elapsed_value(self, value: float) -> str:
        total_seconds = max(int(round(float(value))), 0)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def tickStrings(self, values, scale, spacing):  # type: ignore[override]
        started = perf_counter()
        if self._mode == "clock" and self._start_datetime is not None:
            labels = []
            for value in values:
                try:
                    clock_dt = self._start_datetime + timedelta(seconds=float(value))
                    labels.append(clock_dt.astimezone().strftime("%H:%M:%S"))
                except (OverflowError, OSError, ValueError):
                    labels.append("")
            result = labels
        elif self._mode == "seconds":
            # Plain numeric seconds - reuse AxisItem's own numeric formatter
            # so decimal precision adapts to tick spacing (e.g. "0", "50",
            # "100" when zoomed out, "12.5" when zoomed in enough for
            # sub-second spacing), the same way an un-overridden axis would.
            result = super().tickStrings(values, scale, spacing)
        else:
            result = [self._format_elapsed_value(float(value)) for value in values]
        elapsed_ms = (perf_counter() - started) * 1000.0
        self._record_tickstrings(len(values), elapsed_ms)
        if elapsed_ms > 5.0:
            logging.getLogger("lspr_app.plot").info(
                "FlexibleTimeAxis.tickStrings slow: %.2f ms | ticks=%d | mode=%s",
                elapsed_ms,
                len(values),
                self._mode,
            )
        return result


_SI_PREFIXES_LARGE = [(1e9, "G"), (1e6, "M"), (1e3, "k")]
_SI_PREFIXES_SMALL = [(1e-3, "m"), (1e-6, "µ"), (1e-9, "n")]


class ScientificAxis(pg.AxisItem):
    def __init__(self, orientation: str = "left") -> None:
        super().__init__(orientation=orientation)
        self.enableAutoSIPrefix(False)
        self._diagnostics_owner = None
        self._diagnostics_prefix = ""
        self._format_mode = "scientific"

    def set_format_mode(self, mode: str) -> None:
        """Switch between "scientific" (6.55e+04) and "si" (65.5k) tick labels."""
        normalized = mode if mode in {"scientific", "si"} else "scientific"
        if self._format_mode == normalized:
            return
        self._format_mode = normalized
        self.picture = None
        self.update()

    def _record_tickstrings(self, count: int, elapsed_ms: float) -> None:
        owner = getattr(self, "_diagnostics_owner", None)
        prefix = getattr(self, "_diagnostics_prefix", "")
        if owner is None or not prefix:
            return
        try:
            events = getattr(owner, "_axis_tick_events", None)
            if events is not None:
                now = perf_counter()
                events.append((now, prefix, int(count), float(elapsed_ms)))
            recent_count_name = f"_{prefix}_tick_recent_count"
            recent_total_name = f"_{prefix}_tick_recent_total_ms"
            recent_max_name = f"_{prefix}_tick_recent_max_ms"
            recent_avg_name = f"_{prefix}_tick_recent_avg_ms"
            recent_values = [value for ts, name, _, value in events if name == prefix and (now - float(ts)) <= 10.0]
            if recent_values:
                recent_total = float(sum(recent_values))
                setattr(owner, recent_count_name, len(recent_values))
                setattr(owner, recent_total_name, recent_total)
                setattr(owner, recent_max_name, max(recent_values))
                setattr(owner, recent_avg_name, recent_total / len(recent_values))
            else:
                setattr(owner, recent_count_name, 0)
                setattr(owner, recent_total_name, 0.0)
                setattr(owner, recent_max_name, 0.0)
                setattr(owner, recent_avg_name, 0.0)
        except Exception:
            return

    @staticmethod
    def _decimal_places_for_spacing(spacing: float) -> int:
        """How many decimal places are needed so that two ticks *spacing*
        data-units apart never round to the same label - e.g. spacing=0.1 ->
        1 place, spacing=50 -> 0 places, spacing=0.001 -> 3 places. Same
        formula pg.AxisItem's own default tickStrings uses
        (`places = max(0, ceil(-log10(spacing)))`). Replaces the previous
        fixed-by-magnitude bucketing (always 1 decimal for values >= 100,
        however tightly the view was zoomed in - the source of duplicate-
        looking adjacent tick labels), and stays capped so a pathologically
        tiny spacing can't produce an unreadable wall of digits.
        """
        if not math.isfinite(spacing) or spacing <= 0:
            return 1
        return min(max(0, math.ceil(-math.log10(spacing))), 6)

    @staticmethod
    def _format_normal_range(numeric: float, places: int) -> str:
        return f"{numeric:.{places}f}"

    def _format_scientific(self, numeric: float, abs_value: float, places: int) -> str:
        if abs_value < 1e-2 or abs_value >= 1e4:
            return f"{numeric:.2e}"
        return self._format_normal_range(numeric, places)

    def _format_si(self, numeric: float, abs_value: float, places: int) -> str:
        # Same magnitude boundaries as _format_scientific (>=1e4 / <1e-2
        # switch out of plain decimal) - only the compact notation used
        # outside that middle range differs (65.5k instead of 6.55e+04).
        for threshold, suffix in _SI_PREFIXES_LARGE:
            if abs_value >= threshold:
                return f"{numeric / threshold:.3g}{suffix}"
        if abs_value < 1e-2:
            for threshold, suffix in _SI_PREFIXES_SMALL:
                if abs_value >= threshold:
                    return f"{numeric / threshold:.3g}{suffix}"
            return f"{numeric:.2e}"  # smaller than 1 nano - vanishingly rare
        return self._format_normal_range(numeric, places)

    def tickStrings(self, values, scale, spacing):  # type: ignore[override]
        started = perf_counter()
        places = self._decimal_places_for_spacing(spacing * scale)
        formatter = self._format_si if self._format_mode == "si" else self._format_scientific
        labels: list[str] = []
        for value in values:
            numeric = float(value)
            abs_value = abs(numeric)
            labels.append("0" if abs_value == 0 else formatter(numeric, abs_value, places))
        elapsed_ms = (perf_counter() - started) * 1000.0
        self._record_tickstrings(len(values), elapsed_ms)
        return labels


class MenuDropdownButton(QToolButton):
    """A "[Label]" QToolButton that opens a checkable popup menu on click -
    the same interaction as the sensorgram's 2Y metric pickers
    (secondary_axis_metric_button) - but exposes a QComboBox-like
    currentText()/setCurrentText()/currentTextChanged surface, so it's a
    drop-in replacement anywhere code already talks to a QComboBox in those
    terms. blockSignals()/signalsBlocked() need no extra handling: they're
    plain QObject methods Qt already applies to currentTextChanged since
    it's a normal signal, C++ or Python-defined.
    """

    currentTextChanged = pyqtSignal(str)

    def __init__(self, sections: list[tuple[str | None, list[str]]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAutoRaise(True)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self)
        self._group = QActionGroup(self._menu)
        self._group.setExclusive(True)
        self._actions: dict[str, QAction] = {}
        first_option: str | None = None
        for header, options in sections:
            if header is not None:
                self._menu.addSection(header)
            for option in options:
                if first_option is None:
                    first_option = option
                action = QAction(option, self._menu)
                action.setCheckable(True)
                action.triggered.connect(lambda _checked=False, text=option: self._select(text))
                self._group.addAction(action)
                self._menu.addAction(action)
                self._actions[option] = action
        self.setMenu(self._menu)
        self._current = first_option or ""
        if self._current:
            self._actions[self._current].setChecked(True)
            self.setText(f"[{self._current}]")

    def _select(self, text: str) -> None:
        if text == self._current:
            return
        self._current = text
        self.setText(f"[{text}]")
        self.currentTextChanged.emit(text)

    def currentText(self) -> str:
        return self._current

    def setCurrentText(self, text: str) -> None:
        action = self._actions.get(text)
        if action is None or text == self._current:
            return
        action.setChecked(True)
        self._current = text
        self.setText(f"[{text}]")
        self.currentTextChanged.emit(text)

    def set_option_enabled(self, name: str, enabled: bool) -> None:
        """Show/hide and enable/disable one option (e.g. hiding Raw/Dark/
        Reference from plot_selector in Simulation mode - see
        MainWindow._update_absorbance_only_mode). No-op if *name* isn't one
        of this button's options. Hidden options are also excluded from
        wheelEvent's cycle, so they can't be reached by mouse-wheel either.
        """
        action = self._actions.get(name)
        if action is None:
            return
        action.setVisible(enabled)
        action.setEnabled(enabled)

    def wheelEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        # Same up=forward/down=back convention as InlineWheelDoubleLabel's
        # wheelEvent - cycles through options in the same order they were
        # passed to __init__ (e.g. plot_selector's Raw/Absorbance/Reference/Dark),
        # skipping any option hidden via set_option_enabled(False).
        options = [name for name, action in self._actions.items() if action.isVisible()]
        if len(options) < 2 or self._current not in options:
            event.ignore()
            return
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        step = 1 if delta > 0 else -1
        next_index = (options.index(self._current) + step) % len(options)
        self.setCurrentText(options[next_index])
        event.accept()


class CollapsibleSection(QWidget):
    def __init__(
        self,
        title: str,
        content: QWidget,
        expanded: bool = True,
        header_widgets: list[QWidget] | None = None,
    ) -> None:
        super().__init__()
        self._content = content
        self._toggle = QToolButton(self)
        self._toggle.setObjectName("collapseToggle")
        self._toggle.setText(title)
        self._toggle.setCheckable(True)
        self._toggle.setChecked(expanded)
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setIcon(self._make_chevron_icon(expanded))
        self._toggle.setIconSize(QSize(10, 10))
        self._toggle.clicked.connect(self._set_expanded)
        self._toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        header_row = QWidget(self)
        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)
        header_layout.addWidget(self._toggle)
        self._header_widgets = list(header_widgets or [])
        if self._header_widgets:
            header_layout.addStretch(1)
            header_layout.addWidget(self._header_widgets[0], 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if len(self._header_widgets) > 1:
                for widget in self._header_widgets[1:]:
                    header_layout.addWidget(widget, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        else:
            header_layout.addStretch(1)
        header_row.setLayout(header_layout)
        layout.addWidget(header_row)
        layout.addWidget(self._content)
        self.setLayout(layout)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setMinimumHeight(self.sizeHint().height())
        for widget in self._header_widgets:
            widget.setVisible(expanded)
        self._content.setVisible(expanded)

    def is_expanded(self) -> bool:
        return bool(self._toggle.isChecked())

    def set_expanded(self, expanded: bool) -> None:
        self._toggle.blockSignals(True)
        self._toggle.setChecked(expanded)
        self._toggle.blockSignals(False)
        self._set_expanded(expanded)

    def _set_expanded(self, expanded: bool) -> None:
        self._toggle.setIcon(self._make_chevron_icon(expanded))
        for widget in self._header_widgets:
            widget.setVisible(expanded)
        self._content.setVisible(expanded)
        self.setMinimumHeight(self.sizeHint().height())
        self.updateGeometry()

    def _make_chevron_icon(self, expanded: bool) -> QIcon:
        pixmap = QPixmap(12, 12)
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor("#5a6b7c"))
        pen.setWidth(2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        if expanded:
            painter.drawLine(3, 4, 6, 7)
            painter.drawLine(9, 4, 6, 7)
        else:
            painter.drawLine(4, 3, 7, 6)
            painter.drawLine(4, 9, 7, 6)
        painter.end()
        return QIcon(pixmap)


class CompactSplitterHandle(QSplitterHandle):
    def paintEvent(self, event) -> None:  # pragma: no cover - Qt painting
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        color = self.palette().color(self.foregroundRole())
        color.setAlpha(SeparatorStyle.handle_alpha)
        pen = QPen(color)
        pen.setWidth(SeparatorStyle.handle_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        rect = self.rect()
        if self.orientation() == Qt.Orientation.Horizontal:
            center_y = rect.center().y()
            half_length = max(int(rect.height() * SeparatorStyle.length_ratio * 0.5), SeparatorStyle.min_length)
            painter.drawLine(rect.center().x(), center_y - half_length, rect.center().x(), center_y + half_length)
        else:
            center_x = rect.center().x()
            half_length = max(int(rect.width() * SeparatorStyle.length_ratio * 0.5), SeparatorStyle.min_length)
            painter.drawLine(center_x - half_length, rect.center().y(), center_x + half_length, rect.center().y())
        painter.end()


class CompactSplitter(QSplitter):
    def createHandle(self) -> QSplitterHandle:
        return CompactSplitterHandle(self.orientation(), self)


class InlineWheelDoubleLabel(QLabel):
    valueChanged = pyqtSignal(float)

    def __init__(self, value: float = 0.0, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._minimum = float("-inf")
        self._maximum = float("inf")
        self._value = float(value)
        self._decimals = 1
        self._step = 1.0
        self._suffix = ""
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setStyleSheet("background: transparent; border: none; padding: 0px;")
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._refresh_text()

    def setRange(self, minimum: float, maximum: float) -> None:
        self._minimum = float(minimum)
        self._maximum = float(maximum)
        self.setValue(self._value)

    def setDecimals(self, decimals: int) -> None:
        self._decimals = max(int(decimals), 0)
        self._refresh_text()

    def setSingleStep(self, step: float) -> None:
        self._step = max(float(step), 0.001)

    def setSuffix(self, suffix: str) -> None:
        self._suffix = str(suffix)
        self._refresh_text()

    def value(self) -> float:
        return float(self._value)

    def setValue(self, value: float) -> None:
        bounded = min(max(float(value), self._minimum), self._maximum)
        if abs(bounded - self._value) < 1e-12:
            return
        self._value = bounded
        self._refresh_text()
        self.valueChanged.emit(self._value)

    def mousePressEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        self.setFocus()
        super().mousePressEvent(event)

    def wheelEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if not self.hasFocus() and not self.underMouse():
            event.ignore()
            return
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        direction = 1.0 if delta > 0 else -1.0
        self.setValue(self._value + direction * self._step)
        event.accept()

    def keyPressEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        key = event.key()
        if key in {Qt.Key.Key_Up, Qt.Key.Key_Right}:
            self.setValue(self._value + self._step)
            event.accept()
            return
        if key in {Qt.Key.Key_Down, Qt.Key.Key_Left}:
            self.setValue(self._value - self._step)
            event.accept()
            return
        super().keyPressEvent(event)

    def _refresh_text(self) -> None:
        number = f"{self._value:.{self._decimals}f}"
        self.setText(f"{number}{self._suffix}")


class ElidingLabel(QLabel):
    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = str(text)
        self.setWordWrap(False)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._refresh_elided_text()

    def setText(self, text: str) -> None:  # type: ignore[override]
        self._full_text = str(text)
        self._refresh_elided_text()

    def setFont(self, font) -> None:  # type: ignore[override]
        super().setFont(font)
        self._refresh_elided_text()

    def resizeEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        super().resizeEvent(event)
        self._refresh_elided_text()

    def _refresh_elided_text(self) -> None:
        metrics = QFontMetrics(self.font())
        available_width = max(self.width() - 4, 20)
        super().setText(metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, available_width))
