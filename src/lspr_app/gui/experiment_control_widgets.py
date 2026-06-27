"""Small widget and delegate classes for the Experiment Control panel.

These are self-contained UI helpers that do not depend on
``ExperimentControlWindow`` state — they can be imported independently.

Classes
-------
``_make_frameless_icon_button``
    Factory for borderless, transparent icon ``QToolButton`` instances.
``_NoFocusItemDelegate``
    Item delegate that suppresses the focus/selection/hover visual state so
    cells look clean without a selection border.
``ExperimentControlTableView``
    ``QTableView`` subclass with keyboard shortcuts (copy/paste/reorder) and a
    pinned horizontal scroll position.
``PlanColorDelegate``
    Delegate that renders a cell as a rounded colour swatch with elided text.
"""

from __future__ import annotations

from PyQt6.QtCore import QItemSelectionModel, QModelIndex, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTableView,
    QToolButton,
    QWidget,
)


def _make_frameless_icon_button(
    icon: QIcon,
    tooltip: str,
    *,
    size: int = 22,
    parent: QWidget | None = None,
) -> QToolButton:
    """Create a borderless, transparent icon button.

    The button has no frame or background in its normal state; a subtle
    background tint appears on hover, checked, and pressed states via
    an inline stylesheet.
    """
    button = QToolButton(parent)
    button.setObjectName("framelessIconButton")
    button.setIcon(icon)
    button.setToolTip(tooltip)
    button.setAutoRaise(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setIconSize(QSize(size - 8, size - 8))
    button.setFixedSize(size, size)
    button.setStyleSheet(
        "QToolButton#framelessIconButton { background: transparent; border: none; padding: 0px; }"
        "QToolButton#framelessIconButton:hover { background: rgba(127, 127, 127, 0.10); border: none; }"
        "QToolButton#framelessIconButton:checked { background: rgba(127, 127, 127, 0.14); border: none; }"
        "QToolButton#framelessIconButton:checked:hover { background: rgba(127, 127, 127, 0.18); border: none; }"
        "QToolButton#framelessIconButton:pressed { background: rgba(127, 127, 127, 0.18); border: none; }"
    )
    return button


class _NoFocusItemDelegate(QStyledItemDelegate):
    """Item delegate that strips focus/selection/hover visual decorations.

    Useful for read-only or purely informational table columns where the
    default Qt selection highlight would be distracting.
    """

    def paint(self, painter, option, index) -> None:  # type: ignore[override]
        opt = QStyleOptionViewItem(option)
        opt.state &= ~QStyle.StateFlag.State_HasFocus
        opt.state &= ~QStyle.StateFlag.State_Selected
        opt.state &= ~QStyle.StateFlag.State_MouseOver
        opt.showDecorationSelected = False
        super().paint(painter, opt, index)


class ExperimentControlTableView(QTableView):
    """``QTableView`` subclass for the plan table.

    Adds:
    * Keyboard shortcuts — ``Ctrl+C`` / ``Ctrl+V`` emit :attr:`copy_requested`
      / :attr:`paste_requested` when edit-mode is active.
    * ``PageUp`` / ``PageDown`` emit :attr:`step_move_requested` for row
      reordering without a drag gesture.
    * :meth:`scrollTo` resets the horizontal scroll position to the left so
      the step number is always visible after a vertical scroll.
    * Convenience row/column accessors (:meth:`currentRow`,
      :meth:`currentColumn`, :meth:`setCurrentCell`, :meth:`selectRow`) that
      guard against invalid indices.
    """

    step_move_requested = pyqtSignal(int)
    copy_requested = pyqtSignal()
    paste_requested = pyqtSignal()

    def scrollTo(self, index, hint=QAbstractItemView.ScrollHint.EnsureVisible) -> None:  # type: ignore[override]
        super().scrollTo(index, hint)
        self.horizontalScrollBar().setValue(0)

    def currentRow(self) -> int:
        return self.currentIndex().row()

    def currentColumn(self) -> int:
        return self.currentIndex().column()

    def rowCount(self) -> int:
        model = self.model()
        return int(model.rowCount()) if model is not None else 0

    def columnCount(self) -> int:
        model = self.model()
        return int(model.columnCount()) if model is not None else 0

    def setCurrentCell(self, row: int, column: int) -> None:
        model = self.model()
        if model is None or row < 0 or column < 0 or row >= model.rowCount() or column >= model.columnCount():
            self.setCurrentIndex(QModelIndex())
            return
        self.setCurrentIndex(model.index(row, column))

    def selectRow(self, row: int) -> None:  # noqa: N802 - Qt API compatibility
        model = self.model()
        selection_model = self.selectionModel()
        if model is None or selection_model is None:
            return
        if row < 0 or row >= model.rowCount():
            self.setCurrentCell(-1, -1)
            self.viewport().update()
            return
        column = self.currentColumn()
        if column < 0 or column >= model.columnCount():
            column = 0
        index = model.index(row, column)
        if index.isValid():
            self.setCurrentIndex(index)
            if self.selectionMode() != QAbstractItemView.SelectionMode.NoSelection:
                selection_model.select(
                    index,
                    QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
                )
            else:
                selection_model.clearSelection()
        self.horizontalScrollBar().setValue(0)
        self.viewport().update()

    def mousePressEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        edit_mode = bool(self.property("experiment_control_edit_mode"))
        if edit_mode and event.matches(QKeySequence.StandardKey.Copy):
            self.copy_requested.emit()
            event.accept()
            return
        if edit_mode and event.matches(QKeySequence.StandardKey.Paste):
            self.paste_requested.emit()
            event.accept()
            return
        if edit_mode and event.key() in (Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            self.step_move_requested.emit(-1 if event.key() == Qt.Key.Key_PageUp else 1)
            event.accept()
            return
        super().keyPressEvent(event)


class PlanColorDelegate(QStyledItemDelegate):
    """Delegate that renders a plan-table color cell as a rounded colour swatch.

    The cell background is filled with the colour value stored in the model,
    and the hex string is drawn in contrasting text (black on light swatches,
    white on dark ones).  A gold border appears when the row is selected.
    """

    def paint(self, painter: QPainter, option, index) -> None:  # pragma: no cover - GUI runtime path
        value = str(index.data(Qt.ItemDataRole.DisplayRole) or "").strip() or "#4E79A7"
        color = QColor(value)
        if not color.isValid():
            color = QColor("#4E79A7")

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        rect = option.rect.adjusted(3, 3, -3, -3)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(rect, 5, 5)

        if option.state & QStyle.StateFlag.State_Selected:
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#d8b44a"), 2))
            painter.drawRoundedRect(rect, 5, 5)

        luminance = (0.2126 * color.redF()) + (0.7152 * color.greenF()) + (0.0722 * color.blueF())
        text_color = QColor("#111111" if luminance > 0.62 else "#f5f7fb")
        painter.setPen(QPen(text_color))
        text_rect = rect.adjusted(8, 0, -8, 0)
        label = painter.fontMetrics().elidedText(value, Qt.TextElideMode.ElideRight, max(text_rect.width(), 10))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)

        painter.restore()
