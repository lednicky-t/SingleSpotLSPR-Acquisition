"""Small in-panel "?" help buttons.

``make_help_button`` renders a "?" bubble matching the style of
``lspr_ui.make_info_button``, but adds an optional click-to-open dialog for
content too long to read comfortably in a hover tooltip. Panels with a short
one-line explanation can pass only *tooltip*; panels with real behavior to
explain (multiple controls, non-obvious click/drag gestures, workflow order)
should also pass *title* and *body* so a click shows the full text.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QToolButton, QWidget

_STYLE = (
    "QToolButton#helpButton {"
    " border: 1px solid rgba(230, 235, 241, 0.22);"
    " border-radius: 9px;"
    " background-color: transparent;"
    " color: #e6ebf1;"
    " font-size: 11px;"
    " font-weight: 700;"
    " padding: 0px;"
    " margin: 0px;"
    "}"
    "QToolButton#helpButton:hover {"
    " background-color: rgba(255, 255, 255, 0.06);"
    "}"
    "QToolButton#helpButton:pressed {"
    " background-color: rgba(255, 255, 255, 0.10);"
    "}"
)


def make_help_button(
    tooltip: str,
    *,
    title: str | None = None,
    body: str | None = None,
    parent: QWidget | None = None,
) -> QToolButton:
    button = QToolButton(parent)
    button.setObjectName("helpButton")
    button.setText("?")
    button.setToolTip(tooltip)
    button.setAutoRaise(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFixedSize(18, 18)
    button.setStyleSheet(_STYLE)
    if title and body:
        def _show_dialog(_checked: bool = False, _title: str = title, _body: str = body) -> None:
            QMessageBox.information(button, _title, _body)

        button.clicked.connect(_show_dialog)
    return button
