"""Sensorgram secondary (right-hand) Y-axes - "XY" / "XYY" / "XY2Y".

Mirrors spectrum_plot_controller.py's residual-axis pattern (a second
pg.ViewBox overlaid on the same scene, X-linked to the primary plot, with
its own independent Y-only scroll-zoom) but attached to window.trace_plot
instead of window.spectrum_plot, and used to show one metric at a time
whose units don't belong on the 1Y (wavelength-position) axis.

Deliberately does not touch window.trace_curves / window.TRACE_METRIC_LABELS
at all - those remain exactly as they were (1Y is unchanged, per the
maintainer's explicit instruction). This is a wholly separate, additive
curve+axis+cache lineage, keyed by its own small set of metric names.

Cycling states, via the [XY]/[XYY]/[XY2Y] toggle button:
  - "xy":    no secondary axis at all.
  - "xyy":   one secondary axis ("slot A"), one metric selector.
  - "xy2y":  two independent secondary axes ("slot A" and "slot B"), each
             with its own metric selector, its own ViewBox, and its own
             physical axis column - a genuine second right-hand axis, not
             a second curve sharing the first axis's scale.

Slot A reuses the plain (unsuffixed) attribute names this module already
used before XY2Y existed (secondary_axis, secondary_axis_view,
secondary_axis_curve, ...); slot B is entirely additive, with a "_b" suffix
on every attribute. See _slot_suffix.

On z-order ("underlaying" the 1Y metric curves): both secondary ViewBoxes
are top-level scene siblings of trace_plot's own PlotItem (not its
children), and Qt paints top-level siblings strictly by zValue. The
PlotItem's own ViewBox paints an *opaque* background (main_window_style.py's
plot_bg) before its own curves, and that background+curves pair is one
atomic subtree from the perspective of anything outside it - there is no
zValue that puts a sibling "under the curves but over the background",
only "under both" (fully hidden) or "over both" (currently drawn on top).
So both secondary axes must stay above the PlotItem's own zValue to be
visible at all; what's actually achievable, and what's implemented here, is
relative ordering between the two secondary layers - slot B sits below
slot A - so within the "extra, less important" layer, A is still the more
prominent one and B recedes further.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPen
from PyQt6.QtWidgets import QGraphicsItem, QLabel, QMenu, QWidgetAction


if TYPE_CHECKING:
    from lspr_app.gui.main_window import MainWindow


SECONDARY_METRIC_KEYS: tuple[str, ...] = ("fwhm", "extinction", "temperature", "humidity")

SECONDARY_METRIC_LABELS: dict[str, str] = {
    "fwhm": "FWHM",
    "extinction": "Extinction",
    "temperature": "Temperature",
    "humidity": "Humidity",
}

SECONDARY_METRIC_UNITS: dict[str, str] = {
    "fwhm": "nm",
    "extinction": "AU",
    "temperature": "°C",
    "humidity": "% RH",
}

SECONDARY_METRIC_DEFAULT_COLORS: dict[str, str] = {
    "fwhm": "#CC79A7",
    "extinction": "#59A14F",
    "temperature": "#E45756",
    "humidity": "#4C78A8",
}

# Per-metric line thickness (px) and opacity (0-100%) - independent of
# color, set in Sensorgram Settings' secondary-axis table. Matches
# _sensorgram_line_width_px's own default (2.2px) so a metric that's never
# been customized looks the same as the 1Y curves by default.
SECONDARY_METRIC_DEFAULT_LINE_WIDTH_PX = 2.2
SECONDARY_METRIC_DEFAULT_OPACITY_PERCENT = 100

# Live-cache key -> (HDF5 group, dataset) used when reseeding from an archive
# file. "fwhm"/"extinction" reuse the already-existing processed-metrics
# group (fwhm_nm since schema 3.0, extinction_value since 6.2);
# temperature/humidity read the separate devices/environment group
# (schema 6.0). See storage/hdf5_export.py.
_SECONDARY_METRIC_ARCHIVE_SOURCE: dict[str, str] = {
    "fwhm": "processed_metrics",
    "extinction": "processed_metrics",
    "temperature": "environment",
    "humidity": "environment",
}

# Only "processed_metrics"-sourced metrics need this: their HDF5 dataset name
# differs from the live-cache key (e.g. "fwhm" -> "fwhm_nm"). Environment
# metrics ("temperature"/"humidity") already use matching names end to end -
# see load_environment_history in storage/hdf5_export.py.
_SECONDARY_METRIC_ARCHIVE_DATASET_NAME: dict[str, str] = {
    "fwhm": "fwhm_nm",
    "extinction": "extinction_value",
}

SECONDARY_AXIS_SLOTS: tuple[str, ...] = ("a", "b")

# Slot A defaults to fwhm (matches the original single-axis behavior); slot
# B - only ever shown in "xy2y" - defaults to something else so a first-time
# XY2Y switch shows two different traces rather than two identical ones.
_SECONDARY_AXIS_SLOT_DEFAULT_METRIC: dict[str, str] = {
    "a": "fwhm",
    "b": "temperature",
}

SECONDARY_AXIS_MODE_KEYS: tuple[str, ...] = ("xy", "xyy", "xy2y")

SECONDARY_AXIS_MODE_LABELS: dict[str, str] = {
    "xy": "XY",
    "xyy": "XYY",
    "xy2y": "XY2Y",
}

# Purely a visual state indicator on the toggle button - "how many extra
# axes are active right now", not a semantic color tied to any metric.
SECONDARY_AXIS_MODE_COLORS: dict[str, str] = {
    "xy": "#7a8291",
    "xyy": "#e8d85f",
    "xy2y": "#f2994a",
}


def _slot_suffix(slot: str) -> str:
    return "" if slot == "a" else f"_{slot}"


def normalize_secondary_axis_metric(value: object) -> str:
    normalized = str(value or "fwhm").strip().lower()
    return normalized if normalized in SECONDARY_METRIC_KEYS else "fwhm"


def secondary_axis_metric_label(metric_name: object) -> str:
    normalized = normalize_secondary_axis_metric(metric_name)
    return SECONDARY_METRIC_LABELS[normalized]


def secondary_axis_metric_unit(metric_name: object) -> str:
    normalized = normalize_secondary_axis_metric(metric_name)
    return SECONDARY_METRIC_UNITS[normalized]


def secondary_axis_metric_axis_label(metric_name: object) -> str:
    normalized = normalize_secondary_axis_metric(metric_name)
    return f"{SECONDARY_METRIC_LABELS[normalized]} ({SECONDARY_METRIC_UNITS[normalized]})"


def secondary_axis_color_for(window, metric_name: object) -> str:
    normalized = normalize_secondary_axis_metric(metric_name)
    colors = getattr(window, "SECONDARY_METRIC_COLORS", None)
    if isinstance(colors, dict) and normalized in colors:
        return str(colors[normalized])
    return SECONDARY_METRIC_DEFAULT_COLORS[normalized]


def secondary_axis_line_width_for(window, metric_name: object) -> float:
    normalized = normalize_secondary_axis_metric(metric_name)
    widths = getattr(window, "SECONDARY_METRIC_LINE_WIDTHS", None)
    if isinstance(widths, dict) and normalized in widths:
        return max(float(widths[normalized]), 0.5)
    return SECONDARY_METRIC_DEFAULT_LINE_WIDTH_PX


def secondary_axis_opacity_for(window, metric_name: object) -> int:
    normalized = normalize_secondary_axis_metric(metric_name)
    opacities = getattr(window, "SECONDARY_METRIC_OPACITY", None)
    if isinstance(opacities, dict) and normalized in opacities:
        return int(max(min(int(opacities[normalized]), 100), 0))
    return SECONDARY_METRIC_DEFAULT_OPACITY_PERCENT


def _secondary_axis_pen_for_metric(window, metric_name: object) -> QPen:
    """The curve pen for one metric: its own color, thickness, and
    opacity - all independently configurable in Sensorgram Settings. Used
    everywhere a secondary-axis curve's pen gets (re)built, so the three
    stay in sync regardless of which one changed."""
    normalized = normalize_secondary_axis_metric(metric_name)
    color = QColor(secondary_axis_color_for(window, normalized))
    color.setAlpha(int(round(secondary_axis_opacity_for(window, normalized) * 2.55)))
    width = secondary_axis_line_width_for(window, normalized)
    return pg.mkPen(color, width=width)


def set_secondary_axis_metric_line_width(window, metric_name: str, width_px: float, *, save: bool = False) -> None:
    normalized = normalize_secondary_axis_metric(metric_name)
    widths = getattr(window, "SECONDARY_METRIC_LINE_WIDTHS", None)
    if not isinstance(widths, dict):
        widths = {}
        window.SECONDARY_METRIC_LINE_WIDTHS = widths
    widths[normalized] = max(float(width_px), 0.5)
    for slot in SECONDARY_AXIS_SLOTS:
        if secondary_axis_metric_for(window, slot) != normalized:
            continue
        curve = getattr(window, f"secondary_axis_curve{_slot_suffix(slot)}", None)
        if curve is not None:
            curve.setPen(_secondary_axis_pen_for_metric(window, normalized))
    if save and hasattr(window, "_schedule_ui_state_persist"):
        window._schedule_ui_state_persist()


def set_secondary_axis_metric_opacity(window, metric_name: str, opacity_percent: int, *, save: bool = False) -> None:
    normalized = normalize_secondary_axis_metric(metric_name)
    opacities = getattr(window, "SECONDARY_METRIC_OPACITY", None)
    if not isinstance(opacities, dict):
        opacities = {}
        window.SECONDARY_METRIC_OPACITY = opacities
    opacities[normalized] = int(max(min(int(opacity_percent), 100), 0))
    for slot in SECONDARY_AXIS_SLOTS:
        if secondary_axis_metric_for(window, slot) != normalized:
            continue
        curve = getattr(window, f"secondary_axis_curve{_slot_suffix(slot)}", None)
        if curve is not None:
            curve.setPen(_secondary_axis_pen_for_metric(window, normalized))
    if save and hasattr(window, "_schedule_ui_state_persist"):
        window._schedule_ui_state_persist()


def secondary_axis_metric_for(window, slot: str = "a") -> str:
    default = _SECONDARY_AXIS_SLOT_DEFAULT_METRIC.get(slot, "fwhm")
    return normalize_secondary_axis_metric(getattr(window, f"_secondary_axis_metric_{slot}", default))


def normalize_secondary_axis_mode(value: object) -> str:
    normalized = str(value or "xy").strip().lower()
    return normalized if normalized in SECONDARY_AXIS_MODE_KEYS else "xy"


def secondary_axis_mode_label(mode: object | None = None) -> str:
    return SECONDARY_AXIS_MODE_LABELS[normalize_secondary_axis_mode(mode)]


def secondary_axis_mode_color(mode: object | None = None) -> str:
    return SECONDARY_AXIS_MODE_COLORS[normalize_secondary_axis_mode(mode)]


def _slot_active_for_mode(mode: str, slot: str) -> bool:
    if mode == "xy":
        return False
    if mode == "xyy":
        return slot == "a"
    return True  # "xy2y": both slots active


def secondary_axis_slot_active(window, slot: str) -> bool:
    mode = normalize_secondary_axis_mode(getattr(window, "_secondary_axis_mode", "xy"))
    return _slot_active_for_mode(mode, slot)


def _secondary_axis_slot_at_scene_pos(window, scene_pos) -> str | None:
    """Which active secondary axis slot (if any) a scene position falls
    over. Checked in SECONDARY_AXIS_SLOTS order ("a" then "b"), matching
    their real z-order - slot A's ViewBox is always the one that actually
    receives a raw wheel event first when both are active (see
    SecondaryAxisViewBox.wheelEvent), so this must be able to say "actually,
    that position belongs to slot B" even when called from slot A's own
    handler.
    """
    for slot in SECONDARY_AXIS_SLOTS:
        if not secondary_axis_slot_active(window, slot):
            continue
        axis_item = getattr(window, f"secondary_axis{_slot_suffix(slot)}", None)
        if axis_item is None:
            continue
        try:
            if axis_item.sceneBoundingRect().contains(scene_pos):
                return slot
        except Exception:
            continue
    return None


def _apply_secondary_axis_band_color(window, slot: str, color: str) -> None:
    band = getattr(window, f"secondary_axis_envelope_band{_slot_suffix(slot)}", None)
    if band is None:
        return
    band_color = QColor(str(color))
    band_alpha = int(round(max(min(float(getattr(window, "_sensorgram_metric_envelope_overlay_alpha", 16)), 100.0), 0.0) * 2.55))
    band_color.setAlpha(max(min(band_alpha, 255), 0))
    band.setBrush(pg.mkBrush(band_color))


def _apply_secondary_axis_line_color(window, slot: str) -> None:
    """Color everything that identifies this slot as "this metric's axis":
    the curve (with its own thickness/opacity - see
    _secondary_axis_pen_for_metric), its envelope band, the axis's own
    ticks/label text, and the [A] reset button - so which physical axis
    matches which curve is unambiguous at a glance, especially with two
    axes on screen in XY2Y."""
    metric_name = secondary_axis_metric_for(window, slot)
    suffix = _slot_suffix(slot)
    curve = getattr(window, f"secondary_axis_curve{suffix}", None)
    if curve is not None:
        curve.setPen(_secondary_axis_pen_for_metric(window, metric_name))
    color = secondary_axis_color_for(window, metric_name)
    _apply_secondary_axis_band_color(window, slot, color)
    axis_item = getattr(window, f"secondary_axis{suffix}", None)
    if axis_item is not None:
        axis_item.setPen(pg.mkPen(str(color)))
        axis_item.setTextPen(pg.mkPen(str(color)))
    auto_button = getattr(window, f"secondary_axis_auto_button{suffix}", None)
    if auto_button is not None:
        auto_button.set_color(str(color))


def set_secondary_axis_metric_color(window, metric_name: str, color: str, *, save: bool = False) -> None:
    normalized = normalize_secondary_axis_metric(metric_name)
    colors = getattr(window, "SECONDARY_METRIC_COLORS", None)
    if not isinstance(colors, dict):
        colors = dict(SECONDARY_METRIC_DEFAULT_COLORS)
        window.SECONDARY_METRIC_COLORS = colors
    colors[normalized] = str(color)
    # A color applies to whichever slot(s) currently show this metric -
    # both, if XY2Y happens to have both selectors on the same metric.
    for slot in SECONDARY_AXIS_SLOTS:
        if secondary_axis_metric_for(window, slot) != normalized:
            continue
        _apply_secondary_axis_line_color(window, slot)
    if save and hasattr(window, "_schedule_ui_state_persist"):
        window._schedule_ui_state_persist()


class _SecondaryAxisMetricMenuLabel(QLabel):
    """A QMenu entry rendered as colored text (matching that metric's plot
    color) rather than plain black/white menu text. QMenu/QAction have no
    per-item text-color property, so this is wrapped in a QWidgetAction
    below - the standard Qt idiom for a custom-styled menu row."""

    clicked = pyqtSignal()

    def mousePressEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        self.clicked.emit()
        super().mousePressEvent(event)


_SECONDARY_AXIS_METRIC_UNAVAILABLE_COLOR = "#8a98a8"


def secondary_axis_metric_has_data(window, metric_key: str) -> bool:
    """Whether metric_key currently has real data behind it - used to gray
    out unavailable entries in the metric picker dropdown below.

    fwhm comes straight from the spectrum, so it's available as soon as
    anything has been processed. extinction is additionally gated on
    Absorbance display mode - see acquisition_controller.py's
    _is_absorbance_plot_mode docstring: it's only ever recorded while
    genuinely viewing Absorbance, not silently recomputed against an
    absorbance curve while another plot mode is shown, so outside that mode
    there's nothing real to show for it either. temperature/humidity depend
    on hardware - only a connected Switch running ArduinoValveController
    firmware reports them (see poll_environment_sensors) - so they're only
    available once at least one real reading has actually arrived.
    """
    if metric_key == "extinction":
        from lspr_app.gui.acquisition_controller import _is_absorbance_plot_mode

        return bool(_is_absorbance_plot_mode(window))
    if metric_key == "fwhm":
        return getattr(window, "_last_processed_plot", None) is not None
    if metric_key == "temperature":
        return getattr(window, "_last_temperature_c", None) is not None
    if metric_key == "humidity":
        return getattr(window, "_last_humidity_percent", None) is not None
    return True


def build_secondary_axis_metric_menu(window, parent) -> QMenu:
    """Build the [FWHM]-style dropdown, one colored entry per metric.

    Each entry's text color matches secondary_axis_color_for(window, metric)
    - the same color the curve draws in when that metric is active - so the
    menu itself previews which color you're about to switch to. Slot A and
    slot B each get their own independent instance of this same menu.

    Entries for a metric with no data right now (see
    secondary_axis_metric_has_data) are grayed out and unclickable -
    re-evaluated fresh every time the menu opens (aboutToShow), not just
    once at construction, so e.g. a Switch connecting mid-session or
    switching into Absorbance mode makes its entry selectable immediately
    the next time the dropdown is opened, without rebuilding the menu.
    """
    menu = QMenu(parent)
    entries: dict[str, tuple[QWidgetAction, _SecondaryAxisMetricMenuLabel, str]] = {}
    for metric_key in SECONDARY_METRIC_KEYS:
        color = secondary_axis_color_for(window, metric_key)
        label = _SecondaryAxisMetricMenuLabel(secondary_axis_metric_label(metric_key), menu)
        label.setStyleSheet(f"QLabel {{ color: {color}; padding: 4px 18px; background: transparent; }}")
        label.setCursor(Qt.CursorShape.PointingHandCursor)
        action = QWidgetAction(menu)
        action.setDefaultWidget(label)
        action.setData(metric_key)
        label.clicked.connect(lambda _checked=False, a=action: (menu.close(), a.trigger()))
        menu.addAction(action)
        entries[metric_key] = (action, label, color)

    def _refresh_availability() -> None:
        for metric_key, (action, label, color) in entries.items():
            available = secondary_axis_metric_has_data(window, metric_key)
            action.setEnabled(available)
            label.setEnabled(available)
            entry_color = color if available else _SECONDARY_AXIS_METRIC_UNAVAILABLE_COLOR
            label.setStyleSheet(f"QLabel {{ color: {entry_color}; padding: 4px 18px; background: transparent; }}")
            label.setCursor(Qt.CursorShape.PointingHandCursor if available else Qt.CursorShape.ArrowCursor)

    menu.aboutToShow.connect(_refresh_availability)
    _refresh_availability()
    return menu


class _CompactLabelAxisItem(pg.AxisItem):
    """A right-hand AxisItem whose rotated title label sits close to the
    tick numbers instead of pyqtgraph's default placement (flush against
    the far outer edge of its own reserved column - see AxisItem.
    resizeEvent). That default is fine for a single right axis, but with a
    second one further out (XY2Y's slot B), it puts slot A's label right on
    top of slot B's axis line and ticks. Only the 'right'-orientation branch
    is overridden; these axes are always constructed with orientation
    "right", so the fallback is defensive, not a real code path.
    """

    _LABEL_GAP_PX = 0

    def resizeEvent(self, ev=None) -> None:  # pragma: no cover - GUI runtime path
        if self.label is None or self.orientation != "right":
            super().resizeEvent(ev)
            return
        br = self.label.boundingRect()
        text_extent = self.textWidth + self.style["tickTextOffset"][0] + max(0, self.style["tickLength"])
        p = QPointF(int(text_extent + self._LABEL_GAP_PX), int(self.size().height() / 2 + br.width() / 2))
        self.label.setPos(p)
        self.picture = None


class SecondaryAxisViewBox(pg.ViewBox):
    """Independent Y-only scroll-zoom-and-pan overlay, mirrors ResidualViewBox.

    Unlike the residual axis, this overlay's geometry spans the *entire*
    plot area (setGeometry(trace_vb.sceneBoundingRect()) - required so the
    curve renders across the full width, X-aligned with the 1Y data). That
    means a naive wheel/drag handler here would steal scroll/drag input
    from the main plot everywhere, not just over this axis's own tick
    column - both handlers below gate on the event's *scene* position
    actually falling within the axis item's own on-screen rect
    (self._axis_item.sceneBoundingRect()) before doing anything, and
    otherwise ignore() so the event falls through to whatever's underneath
    (the main plot, or another secondary axis stacked below this one).
    """

    def __init__(self, main_window: "MainWindow", slot: str, axis_item) -> None:
        super().__init__()
        self._main_window = main_window
        self._slot = slot
        self._axis_item = axis_item

    def _event_over_own_axis(self, scene_pos) -> bool:
        if self._axis_item is None:
            return False
        try:
            return bool(self._axis_item.sceneBoundingRect().contains(scene_pos))
        except Exception:
            return False

    def _mark_manual_zoom(self) -> None:
        y_min, y_max = self.viewRange()[1]
        self._manual_y_zoom = True
        setattr(self._main_window, f"_secondary_axis_y_range_{self._slot}", [float(y_min), float(y_max)])
        setattr(self._main_window, f"_secondary_axis_autoscaled_{self._slot}", True)
        update_auto_button = getattr(self._main_window, "_update_secondary_axis_auto_button_visibility", None)
        if callable(update_auto_button):
            update_auto_button(self._slot)
        if hasattr(self._main_window, "_schedule_ui_state_persist"):
            self._main_window._schedule_ui_state_persist()

    def wheelEvent(self, event, axis=None) -> None:  # pragma: no cover - GUI runtime path
        # Qt delivers a wheel event to exactly one item - the topmost one
        # under the cursor - and unlike pyqtgraph's own mouseDragEvent
        # dispatch (which retries lower z-order items when one ignore()s,
        # see sendDragEvent in pyqtgraph's GraphicsScene), an ignored wheel
        # event is simply dropped, not retried. Since this overlay's
        # ViewBox spans the *entire* plot (needed so the curve renders full
        # width) and sits above everything else, it would always be that
        # topmost item - so a plain ignore() here would silently swallow
        # every scroll over the graph while any secondary axis is active,
        # not just fail to zoom this axis. Forward explicitly instead: to
        # whichever OTHER active slot's axis the cursor is actually over
        # (only relevant in XY2Y, where slot A's ViewBox intercepts events
        # even over slot B's own column), or to the main plot's own wheel
        # zoom if the cursor isn't over any secondary axis at all.
        target_slot = _secondary_axis_slot_at_scene_pos(self._main_window, event.scenePos())
        if target_slot is None:
            main_plot = getattr(self._main_window, "trace_plot", None)
            main_vb = main_plot.getPlotItem().vb if main_plot is not None else None
            if main_vb is not None:
                main_vb.wheelEvent(event, axis)
            else:
                event.ignore()
            return
        if target_slot == self._slot:
            self._apply_wheel_zoom(event, axis)
            return
        target_view_box = getattr(self._main_window, f"secondary_axis_view{_slot_suffix(target_slot)}", None)
        if target_view_box is not None:
            target_view_box._apply_wheel_zoom(event, axis)
        else:
            event.ignore()

    def _apply_wheel_zoom(self, event, axis=None) -> None:
        if axis == 0:
            event.ignore()
            return
        angle_delta = getattr(event, "angleDelta", None)
        delta_y = angle_delta().y() if angle_delta is not None else 0
        if delta_y == 0 and hasattr(event, "delta"):
            delta_y = int(event.delta())
        if delta_y == 0:
            event.ignore()
            return
        if axis not in (None, 1):
            event.ignore()
            return
        view_range = self.viewRange()
        y_range = view_range[1]
        y_min = float(y_range[0])
        y_max = float(y_range[1])
        if not np.isfinite(y_min) or not np.isfinite(y_max) or y_max <= y_min:
            event.ignore()
            return
        factor = 1.12 if delta_y > 0 else 1 / 1.12
        center = (y_min + y_max) * 0.5
        half_span = max((y_max - y_min) * 0.5 * factor, 1e-9)
        self.setYRange(center - half_span, center + half_span, padding=0.0)
        self._mark_manual_zoom()
        event.accept()

    def mouseDragEvent(self, event, axis=None) -> None:  # pragma: no cover - GUI runtime path
        if not self._event_over_own_axis(event.buttonDownScenePos()):
            event.ignore()
            return
        if event.button() not in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            event.ignore()
            return
        event.accept()
        pos = event.pos()
        last_pos = event.lastPos()
        dif = (pos - last_pos) * -1
        tr = pg.functions.invertQTransform(self.childGroup.transform())
        y_delta = tr.map(pg.Point(0, dif.y())).y() - tr.map(pg.Point(0, 0)).y()
        self._resetTarget()
        self.translateBy(y=y_delta)
        self._mark_manual_zoom()


class _SecondaryAxisAutoButton(pg.GraphicsObject):
    """The [A] reset button next to one secondary axis - same purpose as
    pyqtgraph's own PlotItem.autoBtn, just positioned per secondary axis
    instead of once for the whole plot, and colored to match that axis's
    current metric. Only visible while that axis has a manual pan/zoom
    applied (see update_secondary_axis_auto_button_visibility)."""

    clicked = pyqtSignal()

    _SIZE = 16.0

    def __init__(self, color: str) -> None:
        super().__init__()
        self._color = QColor(str(color))
        self.setOpacity(0.75)
        self.setZValue(1000)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Reset this axis's zoom/pan to automatic.")

    def set_color(self, color: str) -> None:
        self._color = QColor(str(color))
        self.update()

    def boundingRect(self) -> QRectF:
        return QRectF(0, 0, self._SIZE, self._SIZE)

    def paint(self, painter, *args) -> None:  # pragma: no cover - GUI runtime path
        painter.setRenderHint(painter.RenderHint.Antialiasing)
        rect = self.boundingRect().adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(pg.mkPen(self._color, width=1.2))
        painter.setBrush(pg.mkBrush(20, 20, 24, 190))
        painter.drawRoundedRect(rect, 3, 3)
        font = painter.font()
        font.setBold(True)
        font.setPointSizeF(8.5)
        painter.setFont(font)
        painter.setPen(pg.mkPen(self._color))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "A")

    def mouseClickEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        event.accept()
        self.clicked.emit()

    def hoverEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.isEnter():
            self.setOpacity(1.0)
        elif event.isExit():
            self.setOpacity(0.75)


def _build_secondary_axis_slot(window, slot: str, axis_item) -> None:
    suffix = _slot_suffix(slot)
    trace_plot_item = window.trace_plot.getPlotItem()

    setattr(window, f"secondary_axis{suffix}", axis_item)
    axis_item.enableAutoSIPrefix(False)
    # Critical for _position_secondary_axis_auto_button's geometry math:
    # AxisItem.boundingRect() unions its own rect with its *linked view's*
    # boundingRect whenever grid is enabled (see AxisItem.setGrid's
    # docstring - "tick lines are extended to cover the extent of the
    # linked ViewBox"). Slot A inherited a truthy grid setting from
    # trace_plot.showGrid(x=True, y=True, ...) before being relinked here to
    # this slot's own full-plot-width overlay ViewBox, so its
    # sceneBoundingRect() was ballooning to nearly the whole plot width -
    # putting the [A] button (positioned from that rect) in the middle of
    # the plot instead of over the axis. Grid lines for the main 1Y plot are
    # still drawn fine via the left/bottom axes; these overlay axes never
    # need their own.
    axis_item.setGrid(False)

    view_box = SecondaryAxisViewBox(window, slot, axis_item)
    view_box._manual_y_zoom = False
    setattr(window, f"secondary_axis_view{suffix}", view_box)
    setattr(window, f"_secondary_axis_autoscaled_{slot}", False)
    if not hasattr(window, f"_secondary_axis_y_range_{slot}"):
        setattr(window, f"_secondary_axis_y_range_{slot}", None)
    trace_plot_item.scene().addItem(view_box)
    # See the module docstring's "On z-order" note: both slots must stay
    # above the PlotItem's own zValue to be visible at all (the plot's
    # opaque background would otherwise fully occlude them); B is kept
    # below A so the two secondary layers still have a "less important"
    # ordering between themselves.
    view_box.setZValue(trace_plot_item.zValue() + (1.0 if slot == "a" else 0.5))

    metric_name = secondary_axis_metric_for(window, slot)
    curve = pg.PlotDataItem(pen=_secondary_axis_pen_for_metric(window, metric_name))
    curve._diagnostics_owner = window
    curve._diagnostics_prefix = "trace_plot"
    curve.setClipToView(True)
    curve.setDownsampling(auto=False, ds=1)
    curve.setSkipFiniteCheck(True)
    view_box.addItem(curve)
    setattr(window, f"secondary_axis_curve{suffix}", curve)

    auto_button = _SecondaryAxisAutoButton(secondary_axis_color_for(window, metric_name))
    auto_button.setVisible(False)
    auto_button.clicked.connect(lambda s=slot: reset_secondary_axis_zoom(window, s))
    trace_plot_item.scene().addItem(auto_button)
    setattr(window, f"secondary_axis_auto_button{suffix}", auto_button)

    # Envelope overlay (min/max band) - same feature as the 1Y metrics'
    # trace_metric_envelope_bands/trace_metric_envelope_curves, just one
    # instance per slot. Must be added to this slot's own ViewBox, not
    # trace_plot itself, or it would render in the wrong coordinate space.
    min_curve = pg.PlotDataItem()
    max_curve = pg.PlotDataItem()
    for envelope_curve in (min_curve, max_curve):
        envelope_curve._diagnostics_owner = window
        envelope_curve._diagnostics_prefix = "trace_plot"
        envelope_curve.setClipToView(True)
        envelope_curve.setDownsampling(auto=False, ds=1)
        envelope_curve.setSkipFiniteCheck(True)
        envelope_curve.setVisible(False)
        view_box.addItem(envelope_curve)
    band = pg.FillBetweenItem(min_curve, max_curve)
    band._diagnostics_owner = window
    band._diagnostics_prefix = "trace_plot"
    band.setVisible(False)
    band.setZValue(-2)
    view_box.addItem(band)
    setattr(window, f"secondary_axis_envelope_min_curve{suffix}", min_curve)
    setattr(window, f"secondary_axis_envelope_max_curve{suffix}", max_curve)
    setattr(window, f"secondary_axis_envelope_band{suffix}", band)

    axis_item.linkToView(view_box)
    view_box.setXLink(trace_plot_item.vb)
    # Mouse interaction is handled entirely by SecondaryAxisViewBox's own
    # overridden wheelEvent/mouseDragEvent (axis-hover-gated), not
    # pyqtgraph's default per-axis drag/zoom - mouseEnabled stays off so
    # nothing else tries to layer default behavior on top.
    view_box.setMouseEnabled(x=False, y=False)
    axis_item.setLabel(secondary_axis_metric_axis_label(metric_name))
    # Curve already got its pen above (from _secondary_axis_pen_for_metric
    # at construction); this also colors the envelope band, axis ticks/
    # label, and the [A] button the same way, so the physical axis is
    # visually identified with its curve - see _apply_secondary_axis_line_color.
    _apply_secondary_axis_line_color(window, slot)


def build_secondary_axis_for(window) -> None:
    """Build both secondary-axis slots on window.trace_plot.

    Called once, after trace_plot itself is fully constructed - mirrors the
    residual-axis construction block in main_window.py's
    _build_spectrum_and_trace_plots. Slot A reuses the PlotItem's built-in
    "right" axis slot (showAxis/getAxis - so hiding it in "xy" mode frees
    its layout column, same as the residual axis). Slot B has no built-in
    counterpart - PlotItem only reserves one right-hand column - so it's a
    manually-constructed pg.AxisItem inserted one column further right into
    the PlotItem's own QGraphicsGridLayout (col 3; left=0, viewbox=1,
    right=2, per pyqtgraph's PlotItem.setAxisItems). Hiding a QGraphicsItem
    inside that layout still frees its column exactly like the built-in
    axes do, since the layout excludes invisible items from its size
    calculation - no need for pyqtgraph's own showAxis() plumbing, which
    only knows about axes it created itself.
    """
    trace_plot_item = window.trace_plot.getPlotItem()

    # Swap in _CompactLabelAxisItem for the built-in right axis (see that
    # class's docstring) - setAxisItems() removes the stock AxisItem
    # PlotItem auto-created and installs this one at the same layout
    # position, re-linked to trace_plot's own 1Y viewbox; re-linked again to
    # this slot's own secondary viewbox a few lines below, same as before.
    trace_plot_item.setAxisItems({"right": _CompactLabelAxisItem("right")})
    trace_plot_item.showAxis("right")
    axis_a = trace_plot_item.getAxis("right")
    _build_secondary_axis_slot(window, "a", axis_a)

    axis_b = _CompactLabelAxisItem("right", parent=trace_plot_item)
    axis_b.setZValue(0.5)
    axis_b.setFlag(QGraphicsItem.GraphicsItemFlag.ItemNegativeZStacksBehindParent)
    trace_plot_item.layout.addItem(axis_b, 2, 3)
    axis_b.hide()
    _build_secondary_axis_slot(window, "b", axis_b)

    trace_plot_item.vb.sigResized.connect(window._update_secondary_axis_geometry)
    update_secondary_axis_geometry(window)
    update_secondary_axis_visibility(window)


def update_secondary_axis_geometry(window) -> None:
    trace_vb = window.trace_plot.getPlotItem().vb
    rect = trace_vb.sceneBoundingRect()
    for slot in SECONDARY_AXIS_SLOTS:
        view_box = getattr(window, f"secondary_axis_view{_slot_suffix(slot)}", None)
        if view_box is None:
            continue
        view_box.setGeometry(rect)
        view_box.linkedViewChanged(trace_vb, pg.ViewBox.XAxis)
        _position_secondary_axis_auto_button(window, slot)


def _position_secondary_axis_auto_button(window, slot: str) -> None:
    suffix = _slot_suffix(slot)
    button = getattr(window, f"secondary_axis_auto_button{suffix}", None)
    axis_item = getattr(window, f"secondary_axis{suffix}", None)
    if button is None or axis_item is None:
        return
    axis_rect = axis_item.sceneBoundingRect()
    plot_rect = window.trace_plot.getPlotItem().vb.sceneBoundingRect()
    if axis_rect.isEmpty() or plot_rect.isEmpty():
        return
    button_width = button.boundingRect().width()
    button_height = button.boundingRect().height()
    x = axis_rect.center().x() - button_width / 2.0
    # Vertically centered on the bottom (x) axis's own tick *value* labels,
    # not the axis title further below - mirrors the same
    # tickLength/tickTextOffset/textHeight math AxisItem._updateHeight uses
    # to size itself, read from the real bottom axis so this tracks its
    # actual font size/tick style instead of a guessed pixel offset. Can't
    # use the bottom axis's own sceneBoundingRect() here - like the
    # secondary axes themselves (see _build_secondary_axis_slot's grid
    # note), it's unioned with the full linked-viewbox height because the
    # main plot's grid lines are (intentionally) still on for this axis,
    # so plot_rect.bottom() is the only reliable anchor for "where the
    # viewbox ends and the axis area begins".
    bottom_axis = window.trace_plot.getPlotItem().getAxis("bottom")
    tick_length = max(0.0, float(bottom_axis.style.get("tickLength", 0) or 0))
    tick_text_offset_y = float(bottom_axis.style.get("tickTextOffset", (0, 0))[1])
    tick_text_height = float(getattr(bottom_axis, "textHeight", 0) or 0)
    tick_value_center_y = plot_rect.bottom() + tick_length + tick_text_offset_y + tick_text_height / 2.0
    y = tick_value_center_y - button_height / 2.0
    button.setPos(x, y)


def update_secondary_axis_auto_button_visibility(window, slot: str) -> None:
    button = getattr(window, f"secondary_axis_auto_button{_slot_suffix(slot)}", None)
    if button is None:
        return
    view_box = getattr(window, f"secondary_axis_view{_slot_suffix(slot)}", None)
    manual = bool(getattr(view_box, "_manual_y_zoom", False)) if view_box is not None else False
    button.setVisible(secondary_axis_slot_active(window, slot) and manual)
    if button.isVisible():
        _position_secondary_axis_auto_button(window, slot)


def reset_secondary_axis_zoom(window, slot: str = "a") -> None:
    """The [A] button's action for one slot: clear manual pan/zoom and
    resume automatic scaling - mirrors pyqtgraph's own PlotItem autoBtn,
    just scoped to this one secondary axis instead of the whole plot."""
    suffix = _slot_suffix(slot)
    view_box = getattr(window, f"secondary_axis_view{suffix}", None)
    if view_box is None:
        return
    view_box._manual_y_zoom = False
    setattr(window, f"_secondary_axis_y_range_{slot}", None)
    setattr(window, f"_secondary_axis_autoscaled_{slot}", False)
    autoscale_secondary_axis(window, slot)
    update_secondary_axis_auto_button_visibility(window, slot)
    if hasattr(window, "_schedule_ui_state_persist"):
        window._schedule_ui_state_persist()


def _set_secondary_axis_slot_visible(window, slot: str, active: bool) -> None:
    suffix = _slot_suffix(slot)
    trace_plot_item = window.trace_plot.getPlotItem()
    if slot == "a":
        # showAxis(False) (not merely .setVisible(False)) actually removes
        # the axis's column from the PlotItem's layout grid, so the plot
        # regains the freed width instead of leaving a reserved blank gap.
        trace_plot_item.showAxis("right", show=bool(active))
    else:
        axis_item = getattr(window, f"secondary_axis{suffix}", None)
        if axis_item is not None:
            axis_item.setVisible(bool(active))
    view_box = getattr(window, f"secondary_axis_view{suffix}", None)
    if view_box is not None:
        view_box.setVisible(bool(active))
    update_secondary_axis_auto_button_visibility(window, slot)
    if active:
        request_secondary_axis_archive_reseed(window, slot)
        update_secondary_axis_display(window, slot)


def update_secondary_axis_visibility(window) -> None:
    """Apply window._secondary_axis_mode to both slots' visibility."""
    mode = normalize_secondary_axis_mode(getattr(window, "_secondary_axis_mode", "xy"))
    window._secondary_axis_mode = mode
    for slot in SECONDARY_AXIS_SLOTS:
        _set_secondary_axis_slot_visible(window, slot, _slot_active_for_mode(mode, slot))


def autoscale_secondary_axis(window, slot: str = "a") -> None:
    suffix = _slot_suffix(slot)
    view_box = getattr(window, f"secondary_axis_view{suffix}", None)
    curve = getattr(window, f"secondary_axis_curve{suffix}", None)
    if view_box is None or curve is None or not secondary_axis_slot_active(window, slot):
        return
    if bool(getattr(view_box, "_manual_y_zoom", False)):
        return
    y_data = getattr(curve, "yData", None)
    if y_data is None or len(y_data) == 0:
        view_box.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
        setattr(window, f"_secondary_axis_autoscaled_{slot}", True)
        return
    y = np.asarray(y_data, dtype=np.float64)
    finite = y[np.isfinite(y)]
    if len(finite) == 0:
        view_box.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
        setattr(window, f"_secondary_axis_autoscaled_{slot}", True)
        return
    y_min = float(np.min(finite))
    y_max = float(np.max(finite))
    if y_max <= y_min:
        pad = max(abs(y_min) * 0.05, 1e-6)
        y_min -= pad
        y_max += pad
    else:
        pad = (y_max - y_min) * 0.08
        y_min -= pad
        y_max += pad
    view_box.setYRange(y_min, y_max, padding=0.0)
    setattr(window, f"_secondary_axis_autoscaled_{slot}", True)


def _update_secondary_axis_envelope(
    window,
    slot: str,
    overlay_data: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None,
) -> None:
    """Draw (or hide) a secondary axis slot's min/max envelope band -
    mirrors render_metric_series's _update_metric_envelope_overlay for the
    1Y metrics, just for this slot's single active metric."""
    suffix = _slot_suffix(slot)
    band = getattr(window, f"secondary_axis_envelope_band{suffix}", None)
    min_curve = getattr(window, f"secondary_axis_envelope_min_curve{suffix}", None)
    max_curve = getattr(window, f"secondary_axis_envelope_max_curve{suffix}", None)
    if band is None or min_curve is None or max_curve is None:
        return
    if overlay_data is None:
        band.setVisible(False)
        min_curve.setData([], [])
        max_curve.setData([], [])
        return
    min_x, min_y, max_x, max_y = overlay_data
    if len(min_x) == 0 or len(min_y) == 0 or len(max_x) == 0 or len(max_y) == 0:
        band.setVisible(False)
        min_curve.setData([], [])
        max_curve.setData([], [])
        return
    # See plot_controller.py's _update_metric_envelope_overlay: FillBetweenItem
    # rebuilds its whole fill path on either curve's sigPlotChanged, so setting
    # both separately rebuilt it twice per tick - block min_curve's signal so
    # only max_curve's setData() triggers the (now single) rebuild.
    min_curve.blockSignals(True)
    try:
        min_curve.setData(min_x, min_y)
    finally:
        min_curve.blockSignals(False)
    max_curve.setData(max_x, max_y)
    band.setVisible(True)


def update_secondary_axis_display(window, slot: str = "a") -> None:
    """Pull whichever metric this slot is currently showing from the live
    cache and draw it.

    Fully self-contained: unlike render_metric_series (which loops over
    window.trace_curves / window._active_trace_series, both scoped to the
    1Y fit-position metrics), this reads directly from the generic,
    metric-name-keyed compression cache - no dependency on the 1Y pipeline.
    """
    if not secondary_axis_slot_active(window, slot):
        return
    # Respect "freeze sensorgram" the same way refresh_metric_plot does
    # (plot_controller.py) - this function is called directly from the
    # per-point append sites (acquisition_controller.py) rather than a
    # shared refresh tick, so it has to check the flag itself instead of
    # inheriting an early-return from a caller.
    if bool(getattr(window, "_sensorgram_frozen", False)):
        return
    cache = getattr(window, "_plot_view_cache", None)
    if cache is None or not hasattr(cache, "live_absolute_metric_view"):
        return
    suffix = _slot_suffix(slot)
    curve = getattr(window, f"secondary_axis_curve{suffix}", None)
    view_box = getattr(window, f"secondary_axis_view{suffix}", None)
    if curve is None or view_box is None:
        return
    metric_name = secondary_axis_metric_for(window, slot)
    recent_tail_points = max(int(getattr(window, "_sensorgram_compression_recent_tail_points", 300)), 0)
    target_points = max(int(getattr(window, "_plot_display_points", 512)), 1)
    display = cache.live_absolute_metric_view(
        metric_name,
        target_points=target_points,
        recent_tail_points=recent_tail_points,
    )
    if display is None:
        curve.setData([], [])
        _update_secondary_axis_envelope(window, slot, None)
        return
    x, y = display
    # Same line-style options as the 1Y metrics (see render_metric_series in
    # plot_controller.py): "spline" resamples through a cubic spline first,
    # a step mode ("left"/"right"/"center") draws as steps, plain None is a
    # straight line - one shared window setting, applied consistently here.
    step_mode = getattr(window, "_sensorgram_line_step_mode", None)
    if step_mode == "spline":
        from lspr_app.gui.plot_controller import spline_render_series

        spline_x, spline_y = spline_render_series(x, y)
        curve.setData(spline_x, spline_y, stepMode=False)
    elif step_mode is None:
        curve.setData(x, y, stepMode=False)
    else:
        curve.setData(x, y, stepMode=step_mode)

    overlay_data = None
    if bool(getattr(window, "_sensorgram_metric_envelope_overlay_enabled", False)) and hasattr(
        cache, "live_absolute_metric_envelope_view"
    ):
        try:
            overlay_data = cache.live_absolute_metric_envelope_view(
                metric_name,
                target_points=target_points,
                recent_tail_points=recent_tail_points,
            )
        except Exception:
            overlay_data = None
    _update_secondary_axis_envelope(window, slot, overlay_data)

    if bool(getattr(window, f"_secondary_axis_autoscaled_{slot}", False)) or not bool(
        getattr(view_box, "_manual_y_zoom", False)
    ):
        autoscale_secondary_axis(window, slot)


def set_secondary_axis_metric(window, metric_name: object, slot: str = "a", *, save: bool = False) -> None:
    normalized = normalize_secondary_axis_metric(metric_name)
    if normalized == secondary_axis_metric_for(window, slot):
        return
    setattr(window, f"_secondary_axis_metric_{slot}", normalized)
    suffix = _slot_suffix(slot)
    axis_item = getattr(window, f"secondary_axis{suffix}", None)
    if axis_item is not None:
        axis_item.setLabel(secondary_axis_metric_axis_label(normalized))
    _apply_secondary_axis_line_color(window, slot)
    # Switching metric means the on-screen curve now points at a different
    # cache entry - not "corrupt", just possibly not seeded yet (e.g. never
    # shown before this session). Reset manual zoom so autoscale gets a
    # fresh look at the new metric's own value range instead of keeping
    # whatever Y-range the previous metric's units happened to need.
    setattr(window, f"_secondary_axis_autoscaled_{slot}", False)
    view_box = getattr(window, f"secondary_axis_view{suffix}", None)
    if view_box is not None:
        view_box._manual_y_zoom = False
    update_secondary_axis_auto_button_visibility(window, slot)
    request_secondary_axis_archive_reseed(window, slot)
    update_secondary_axis_display(window, slot)
    if save and hasattr(window, "_schedule_ui_state_persist"):
        window._schedule_ui_state_persist()


def set_secondary_axis_mode(window, mode: object, *, save: bool = False) -> None:
    normalized = normalize_secondary_axis_mode(mode)
    if normalized == normalize_secondary_axis_mode(getattr(window, "_secondary_axis_mode", "xy")):
        return
    window._secondary_axis_mode = normalized
    update_secondary_axis_visibility(window)
    if save and hasattr(window, "_schedule_ui_state_persist"):
        window._schedule_ui_state_persist()


def cycle_secondary_axis_mode(window) -> None:
    current = normalize_secondary_axis_mode(getattr(window, "_secondary_axis_mode", "xy"))
    next_mode = SECONDARY_AXIS_MODE_KEYS[(SECONDARY_AXIS_MODE_KEYS.index(current) + 1) % len(SECONDARY_AXIS_MODE_KEYS)]
    set_secondary_axis_mode(window, next_mode, save=True)


def request_secondary_axis_archive_reseed(window, slot: str = "a") -> None:
    """Backfill one secondary-axis slot's cache from the current archive file.

    Called whenever that slot becomes active, its metric changes, or a
    primary-metric archive reload fires (session/measurement switch, Stop,
    the manual reload button) - see the call in
    request_absolute_sensorgram_metric_archive_reload
    (main_window_sensorgram_archive.py), which reseeds every currently
    active slot - so each secondary curve rebases at the exact same moments
    the 1Y curves do, without duplicating any of that function's carefully-
    fixed anchor logic (see docs/sensorgram_improvements.md C1-C9): this
    always computes elapsed_s fresh from acquired_at_unix_ms at read time,
    never a stored anchor.
    """
    if not secondary_axis_slot_active(window, slot):
        return
    from lspr_app.gui.main_window_sensorgram_archive import _sensorgram_active_archive_path

    archive_path = _sensorgram_active_archive_path(window)
    if archive_path is None or not archive_path.exists():
        return
    metric_name = secondary_axis_metric_for(window, slot)
    from lspr_app.gui.workers import SecondaryAxisReloadRequest, SecondaryAxisReloadTask

    task = SecondaryAxisReloadTask(
        SecondaryAxisReloadRequest(
            path=archive_path,
            metric_name=metric_name,
            source=_SECONDARY_METRIC_ARCHIVE_SOURCE[metric_name],
            dataset_name=_SECONDARY_METRIC_ARCHIVE_DATASET_NAME.get(metric_name, metric_name),
        )
    )
    task.signals.finished.connect(lambda result, s=slot: window._handle_secondary_axis_reload_result(s, result))
    task.signals.failed.connect(window._handle_secondary_axis_reload_failed)
    window._thread_pool.start(task)


def handle_secondary_axis_reload_result(window, slot: str, result) -> None:
    if secondary_axis_metric_for(window, slot) != result.metric_name:
        return  # user switched this slot's metric again before this background read finished
    cache = getattr(window, "_plot_view_cache", None)
    if cache is None or not hasattr(cache, "seed_live_absolute_metric_cache"):
        return
    if len(result.x_values) == 0:
        return
    x_values = np.asarray(result.x_values, dtype=np.float64)
    y_values = np.asarray(result.y_values, dtype=np.float64)
    # Preserve any not-yet-flushed live tail newer than the file data, same
    # reasoning as the 1Y reload path (main_window_sensorgram_archive.py).
    tail = None
    if hasattr(cache, "live_tail_snapshot"):
        cutoff_t = float(x_values[-1]) if len(x_values) else -np.inf
        try:
            tail = cache.live_tail_snapshot({result.metric_name}, after_t=cutoff_t).get(result.metric_name)
        except Exception:
            tail = None
    if tail is not None:
        tail_x, tail_y = tail
        if len(tail_x) > 0:
            x_values = np.concatenate([x_values, np.asarray(tail_x, dtype=np.float64)])
            y_values = np.concatenate([y_values, np.asarray(tail_y, dtype=np.float64)])
    cache.seed_live_absolute_metric_cache(
        result.metric_name,
        x_values,
        y_values,
        target_points=max(int(getattr(window, "_plot_display_points", 512)), 1),
        recent_tail_points=max(int(getattr(window, "_sensorgram_compression_recent_tail_points", 300)), 0),
    )
    update_secondary_axis_display(window, slot)


def handle_secondary_axis_reload_failed(window, message: str) -> None:
    logger = getattr(window, "_ui_logger", None)
    if logger is not None:
        logger.warning("Secondary sensorgram axis reload failed: %s", message)
