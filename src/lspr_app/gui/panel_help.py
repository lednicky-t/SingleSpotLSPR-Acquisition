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

from lspr_app.gui.theme_palette import theme_palette


def _help_button_style(theme_mode: str) -> str:
    # Text/border color needs to branch on theme: this is a transparent
    # button with no background of its own, so it directly shows through to
    # whatever panel color is behind it - a near-white glyph that reads fine
    # on the dark theme's near-black panels is nearly invisible on the light
    # theme's near-white ones.
    palette = theme_palette(theme_mode)
    return (
        "QToolButton#helpButton {"
        f" border: 1px solid {palette['help_button_border']};"
        " border-radius: 9px;"
        " background-color: transparent;"
        f" color: {palette['help_button_text']};"
        " font-size: 11px;"
        " font-weight: 700;"
        " padding: 0px;"
        " margin: 0px;"
        "}"
        "QToolButton#helpButton:hover {"
        f" background-color: {palette['help_button_hover']};"
        "}"
        "QToolButton#helpButton:pressed {"
        f" background-color: {palette['help_button_pressed']};"
        "}"
    )


def apply_help_button_theme(button: QToolButton, theme_mode: str) -> None:
    """Re-apply the "?" bubble style for the given theme - call this on any
    persistent window's live theme switch, since the style is set directly
    on the button rather than inherited from an ancestor stylesheet."""
    button.setStyleSheet(_help_button_style(theme_mode))


def make_help_button(
    tooltip: str,
    *,
    title: str | None = None,
    body: str | None = None,
    parent: QWidget | None = None,
    theme_mode: str = "dark",
) -> QToolButton:
    button = QToolButton(parent)
    button.setObjectName("helpButton")
    button.setText("?")
    button.setToolTip(tooltip)
    button.setAutoRaise(True)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFixedSize(18, 18)
    apply_help_button_theme(button, theme_mode)
    if title and body:
        def _show_dialog(_checked: bool = False, _title: str = title, _body: str = body) -> None:
            QMessageBox.information(button, _title, _body)

        button.clicked.connect(_show_dialog)
    return button
