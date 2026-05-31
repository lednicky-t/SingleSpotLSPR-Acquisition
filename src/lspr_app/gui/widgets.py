from __future__ import annotations

from datetime import datetime
import pyqtgraph as pg

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QLabel, QSplitter, QSplitterHandle, QToolButton, QVBoxLayout, QSizePolicy, QWidget


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
    def __init__(self, orientation: str = "bottom") -> None:
        super().__init__(orientation=orientation)
        self._mode = "elapsed"

    def set_mode(self, mode: str) -> None:
        self._mode = mode

    def tickStrings(self, values, scale, spacing):  # type: ignore[override]
        if self._mode == "clock":
            labels = []
            for value in values:
                try:
                    labels.append(datetime.fromtimestamp(float(value)).strftime("%H:%M:%S"))
                except (OverflowError, OSError, ValueError):
                    labels.append("")
            return labels
        return [f"{float(value):.0f}" if abs(float(value)) >= 10 else f"{float(value):.1f}" for value in values]


class ScientificAxis(pg.AxisItem):
    def __init__(self, orientation: str = "left") -> None:
        super().__init__(orientation=orientation)
        self.enableAutoSIPrefix(False)

    def tickStrings(self, values, scale, spacing):  # type: ignore[override]
        labels: list[str] = []
        for value in values:
            numeric = float(value)
            abs_value = abs(numeric)
            if abs_value == 0:
                labels.append("0")
            elif abs_value < 1e-2 or abs_value >= 1e4:
                labels.append(f"{numeric:.2e}")
            elif abs_value < 1:
                labels.append(f"{numeric:.4f}")
            elif abs_value < 10:
                labels.append(f"{numeric:.3f}")
            elif abs_value < 100:
                labels.append(f"{numeric:.2f}")
            else:
                labels.append(f"{numeric:.1f}")
        return labels


class CollapsibleSection(QWidget):
    def __init__(self, title: str, content: QWidget, expanded: bool = True) -> None:
        super().__init__()
        self._content = content
        self._toggle = QToolButton()
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
        layout.addWidget(self._toggle)
        layout.addWidget(self._content)
        self.setLayout(layout)
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
        self._content.setVisible(expanded)

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
