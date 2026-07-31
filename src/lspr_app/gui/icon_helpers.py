from __future__ import annotations

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from lspr_ui import (
    bulb_icon,
    dark_icon,
    device_status_icon,
    flow_icon,
    flow_tabler_icon,
    reference_icon,
    residual_icon,
    snowflake_icon,
    tabler_icon,
    tint_tabler_icon,
    transport_icon,
    trash_icon,
)



_PRISM_ICON_SVG = """
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path stroke="none" d="M0 0h24v24H0z" fill="none"/>
  <path d="M2 13h4.45" stroke="#ffffff"/>
  <path d="M18 5l-4.5 6" stroke="#7b3ff2"/>
  <path d="M22 9l-7.75 3.25" stroke="#00c853"/>
  <path d="M22 15l-7 -1.5" stroke="#ff3b30"/>
  <path d="M4.731 19h11.539a1 1 0 0 0 .866 -1.5l-5.769 -10a1 1 0 0 0 -1.732 0l-5.769 10a1 1 0 0 0 .865 1.5" fill="#8edcff" fill-opacity="0.25" stroke="#8edcff"/>
</svg>
"""


def prism_tab_icon() -> QIcon:
    pixmap = QPixmap(24, 24)
    pixmap.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(_PRISM_ICON_SVG.encode("utf-8")))
    painter = QPainter(pixmap)
    try:
        renderer.render(painter)
    finally:
        painter.end()
    return QIcon(pixmap)


def math_function_tab_icon(color: QColor | None = None) -> QIcon:
    tint = color or QColor("#8a98a8")
    try:
        return tint_tabler_icon(tabler_icon("math_function"), tint)
    except Exception:
        return tint_tabler_icon(tabler_icon("math"), tint)


def storage_compression_icon(active: bool) -> QIcon:
    tint = QColor("#f2c94c" if active else "#b9a24b")
    try:
        return tint_tabler_icon(tabler_icon("file_zip"), tint)
    except Exception:
        return tint_tabler_icon(flow_tabler_icon("archive", "database", "box"), tint)


def storage_save_icon() -> QIcon:
    tint = QColor("#47a861")
    try:
        return tint_tabler_icon(tabler_icon("device_floppy"), tint)
    except Exception:
        return tint_tabler_icon(flow_tabler_icon("device_floppy", "device_save", "floppy"), tint)


def reload_icon() -> QIcon:
    tint = QColor("#4da3ff")
    try:
        return tint_tabler_icon(tabler_icon("reload"), tint)
    except Exception:
        return tint_tabler_icon(tabler_icon("refresh"), tint)


def temperature_status_icon() -> QIcon:
    # Same red used for this metric elsewhere (secondary-axis curve/label
    # color - see SECONDARY_METRIC_DEFAULT_COLORS in
    # gui/sensorgram_secondary_axis.py), so the titlebar reading is visually
    # tied to the same metric there.
    return tint_tabler_icon(tabler_icon("temperature"), QColor("#E45756"))


def humidity_status_icon() -> QIcon:
    return tint_tabler_icon(tabler_icon("droplet"), QColor("#4C78A8"))


def crosshair_cursor_icon() -> QIcon:
    # Muted gray, not the overlay's normal light text color (#d7dee6) - this
    # icon marks the cursor readout as *off*, so it should read as dimmer/
    # inactive rather than as another live value.
    return tint_tabler_icon(tabler_icon("crosshair"), QColor("#8a94a6"))


def stats_hidden_icon() -> QIcon:
    # Same muted gray as crosshair_cursor_icon, for the same "this is off"
    # reason - a distinct shape (bullet list vs. crosshair) so the two hidden
    # states in adjacent plot corners aren't easy to confuse at a glance.
    return tint_tabler_icon(tabler_icon("list"), QColor("#8a94a6"))


def tracking_ready_icon(blink_visible: bool) -> QIcon:
    # Same green as transport_icon's "play" tint (#47a861), but filled and
    # alpha-pulsed - same alpha-blink idiom acquisition_controller.py's
    # update_measurement_toggle_button already uses for trace_record_button
    # while a measurement is active. Shown on start_tracking_button once dark
    # and reference are both captured (see
    # MainWindow._refresh_tracking_ready_indicator) so it's visually obvious
    # tracking can be started, without the button changing shape/position.
    color = QColor("#47a861")
    color.setAlpha(255 if blink_visible else 90)
    return tint_tabler_icon(tabler_icon("player_play_filled"), color)


__all__ = [
    "bulb_icon",
    "crosshair_cursor_icon",
    "dark_icon",
    "device_status_icon",
    "flow_icon",
    "flow_tabler_icon",
    "humidity_status_icon",
    "math_function_tab_icon",
    "reload_icon",
    "prism_tab_icon",
    "reference_icon",
    "residual_icon",
    "snowflake_icon",
    "stats_hidden_icon",
    "storage_compression_icon",
    "storage_save_icon",
    "tabler_icon",
    "temperature_status_icon",
    "tint_tabler_icon",
    "tracking_ready_icon",
    "transport_icon",
    "trash_icon",
]
