from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pyqtgraph as pg

from PyQt6.QtCore import Qt, QSize, QSignalBlocker, pyqtSignal
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QColorDialog,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QGridLayout,
    QLineEdit,
    QLabel,
    QVBoxLayout,
    QSlider,
    QDoubleSpinBox,
    QSpinBox,
    QSizePolicy,
    QToolButton,
    QTabWidget,
    QWidget,
)

from lspr_app.gui.ui_helpers import make_compact_spinbox
from lspr_app.gui.main_window_processing import normalize_sensorgram_metric_name, sensorgram_metric_order
from lspr_app.gui.icon_helpers import flow_tabler_icon, tint_tabler_icon


@dataclass(frozen=True)
class _LineModeOption:
    label: str
    step_mode: str | None


@dataclass(frozen=True)
class MetricModeSelectionState:
    visible_modes: frozenset[str]
    primary_mode: str


@dataclass(frozen=True)
class MetricModeRow:
    mode: str
    color_button: QToolButton
    checkbox: QCheckBox
    star_label: QToolButton
    label: QLabel


class MetricColorButton(QToolButton):
    colorChanged = pyqtSignal(str)

    def __init__(self, color: str, parent=None) -> None:
        super().__init__(parent)
        self._color = "#444444"
        self.setFixedSize(22, 22)
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip("Pick a custom color for this metric.")
        self.clicked.connect(self._pick_color)
        self.set_color(color)

    def color(self) -> str:
        return self._color

    def set_color(self, color: str) -> None:
        qcolor = QColor(str(color))
        if not qcolor.isValid():
            qcolor = QColor("#444444")
        self._color = qcolor.name()
        self.setStyleSheet(
            "QToolButton {"
            f" background: {self._color};"
            " border: 1px solid #666666;"
            " border-radius: 3px;"
            " padding: 0px;"
            " margin: 0px;"
            " }"
            "QToolButton:hover { border: 1px solid #d0d0d0; }"
        )
        self.setToolTip(f"Metric color: {self._color}. Click to change.")

    def _pick_color(self) -> None:  # pragma: no cover - GUI runtime path
        selected = QColorDialog.getColor(QColor(self._color), self, "Select metric color")
        if not selected.isValid():
            return
        self.set_color(selected.name())
        self.colorChanged.emit(self._color)


def _make_frameless_icon_button(icon: QIcon, tooltip: str, *, size: int = 28, parent: QWidget | None = None) -> QToolButton:
    button = QToolButton(parent)
    button.setAutoRaise(True)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    button.setFixedSize(size, size)
    button.setIcon(icon)
    button.setIconSize(QSize(max(size - 6, 12), max(size - 6, 12)))
    button.setToolTip(tooltip)
    button.setStyleSheet(
        "QToolButton {"
        " border: none;"
        " background: transparent;"
        " padding: 0px;"
        " margin: 0px;"
        " }"
        "QToolButton:hover { background: rgba(127, 127, 127, 0.10); }"
        "QToolButton:pressed { background: rgba(127, 127, 127, 0.18); }"
    )
    return button


_LINE_MODE_OPTIONS: tuple[_LineModeOption, ...] = (
    _LineModeOption("Linear", None),
    _LineModeOption("Step left", "left"),
    _LineModeOption("Step right", "right"),
    _LineModeOption("Step center", "center"),
    _LineModeOption("Spline", "spline"),
)

_TIME_AXIS_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Elapsed time", "elapsed"),
    ("Local clock time", "clock"),
)

_ROLLING_WINDOW_OPTIONS: tuple[tuple[str, float], ...] = (
    ("1 min", 60.0),
    ("5 min", 300.0),
    ("15 min", 900.0),
    ("30 min", 1800.0),
    ("60 min", 3600.0),
)

_BUFFER_OPTIONS: tuple[tuple[str, float | None], ...] = (
    ("None", None),
    ("1%", 0.01),
    ("2%", 0.02),
    ("5%", 0.05),
)

_AUTOSCALE_THROTTLE_OPTIONS: tuple[tuple[str, float | None], ...] = (
    ("Off", 0.0),
    ("Light", 0.5),
    ("Medium", 1.0),
    ("Heavy", 2.0),
)


def _normalize_line_mode(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    return normalized if normalized in {"left", "right", "center", "spline"} else None


class MetricModeSelector(QWidget):
    selectionChanged = pyqtSignal(object, str)

    def __init__(self, window) -> None:
        super().__init__(window)
        self._window = window
        self._ordered_modes = sensorgram_metric_order(window)
        if not self._ordered_modes:
            self._ordered_modes = ["smoothed_max"]
        self._rows: dict[str, MetricModeRow] = {}
        self._state = self._make_valid_state(self._ordered_modes[:1], self._ordered_modes[0])
        self._build_ui()

    def _make_valid_state(self, visible_modes, primary_mode: str) -> MetricModeSelectionState:
        visible_set = {mode for mode in self._ordered_modes if mode in {normalize_sensorgram_metric_name(name) for name in visible_modes}}
        if not visible_set:
            fallback = self._ordered_modes[0]
            return MetricModeSelectionState(frozenset({fallback}), fallback)
        primary = normalize_sensorgram_metric_name(primary_mode)
        if primary not in visible_set:
            primary = next(mode for mode in self._ordered_modes if mode in visible_set)
        return MetricModeSelectionState(frozenset(visible_set), primary)

    def _build_ui(self) -> None:
        layout = QGridLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(2)
        layout.setColumnStretch(0, 0)
        layout.setColumnStretch(1, 0)
        layout.setColumnStretch(2, 0)
        layout.setColumnStretch(3, 0)

        metric_header = QLabel("Metric")
        metric_header.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(metric_header, 0, 0)

        color_header = QLabel("Color")
        color_header.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(color_header, 0, 1)

        show_header = QLabel("Show")
        show_header.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(show_header, 0, 2)

        primary_header = QLabel("Primary")
        primary_header.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(primary_header, 0, 3)

        labels = getattr(self._window, "TRACE_METRIC_LABELS", {})
        descriptions = getattr(self._window, "TRACE_METRIC_DESCRIPTIONS", {})
        colors = getattr(self._window, "TRACE_METRIC_COLORS", {})
        for row_index, mode in enumerate(self._ordered_modes, start=1):
            metric_color = str(colors.get(mode, "#444444"))

            label = QLabel(labels.get(mode, mode))
            label.setMinimumHeight(22)
            label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            description = descriptions.get(mode, mode)
            label.setToolTip(f"{mode}: {description}")
            label.setStyleSheet(f"color: {metric_color};")

            color_button = MetricColorButton(metric_color)
            color_button.colorChanged.connect(lambda color, metric=mode: self._on_color_changed(metric, color))

            star_label = QToolButton()
            star_label.setFixedSize(22, 22)
            star_label.setAutoRaise(True)
            star_label.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            star_label.setText("\u2606")
            star_label.setCursor(Qt.CursorShape.PointingHandCursor)
            star_label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            star_label.setToolTip("Set this visible metric as the primary metric.")
            star_label.setStyleSheet(
                "QToolButton { background: transparent; border: none; padding: 0px; margin: 0px; color: #8a8a8a; }"
            )
            star_label.clicked.connect(lambda _checked=False, metric=mode: self._on_primary_clicked(metric))

            checkbox = QCheckBox()
            checkbox.setFixedHeight(22)
            checkbox.setCursor(Qt.CursorShape.PointingHandCursor)
            checkbox.setToolTip("Include this metric in the sensorgram display and statistics.")
            checkbox.setStyleSheet(
                f"QCheckBox {{ color: {metric_color}; }}"
                f"QCheckBox::indicator {{ width: 12px; height: 12px; border: 1px solid {metric_color}; border-radius: 2px; }}"
                f"QCheckBox::indicator:unchecked {{ background: transparent; }}"
                f"QCheckBox::indicator:checked {{ background: {metric_color}; }}"
            )
            checkbox.toggled.connect(lambda checked, metric=mode: self._on_visible_toggled(metric, checked))

            self._rows[mode] = MetricModeRow(
                mode=mode,
                color_button=color_button,
                checkbox=checkbox,
                star_label=star_label,
                label=label,
            )
            layout.addWidget(label, row_index, 0)
            layout.addWidget(color_button, row_index, 1, alignment=Qt.AlignmentFlag.AlignHCenter)
            layout.addWidget(checkbox, row_index, 2, alignment=Qt.AlignmentFlag.AlignHCenter)
            layout.addWidget(star_label, row_index, 3, alignment=Qt.AlignmentFlag.AlignHCenter)

        self.setLayout(layout)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._sync_widgets()

    def state(self) -> MetricModeSelectionState:
        return MetricModeSelectionState(frozenset(self._state.visible_modes), self._state.primary_mode)

    def set_state(self, visible_modes, primary_mode: str) -> None:
        self._state = self._make_valid_state(visible_modes, primary_mode)
        self._sync_widgets()

    def _show_status(self, message: str) -> None:
        status_label = getattr(self._window, "status_label", None)
        if status_label is not None:
            status_label.setText(message)

    def _validate_state(self) -> None:
        self._state = self._make_valid_state(self._state.visible_modes, self._state.primary_mode)

    def _sync_widgets(self) -> None:
        visible_modes = set(self._state.visible_modes)
        primary_mode = self._state.primary_mode
        for mode, row in self._rows.items():
            visible = mode in visible_modes
            primary = mode == primary_mode
            with QSignalBlocker(row.checkbox), QSignalBlocker(row.star_label):
                row.checkbox.setChecked(visible)
                row.star_label.setText("\u2605" if primary else "\u2606")
                row.star_label.setEnabled(visible)
                row.star_label.setToolTip(
                    "Primary metric" if primary else "Set this visible metric as the primary metric."
                )
            metric_color = getattr(self._window, "TRACE_METRIC_COLORS", {}).get(mode, "#444444")
            description = getattr(self._window, "TRACE_METRIC_DESCRIPTIONS", {}).get(mode, mode)
            row.color_button.set_color(metric_color)
            row.label.setStyleSheet(f"font-weight: 700; color: {metric_color};" if primary else f"color: {metric_color};")
            row.label.setToolTip(f"{mode}: {description}")
            row.star_label.setStyleSheet(
                "QToolButton { background: transparent; border: none; padding: 0px; margin: 0px; "
                f"color: {'#f2c94c' if primary else '#8a8a8a'}; font-size: 16px; font-weight: 700; }}"
            )

    def _emit_state_changed(self) -> None:
        self.selectionChanged.emit(frozenset(self._state.visible_modes), self._state.primary_mode)

    def _on_visible_toggled(self, mode: str, checked: bool) -> None:
        mode = normalize_sensorgram_metric_name(mode)
        visible = set(self._state.visible_modes)
        primary = self._state.primary_mode
        if checked:
            if mode in visible:
                self._sync_widgets()
                return
            self._state = self._make_valid_state(visible | {mode}, primary)
            self._sync_widgets()
            self._emit_state_changed()
            return
        if mode not in visible:
            self._sync_widgets()
            return
        if mode == primary:
            self._show_status("Select another primary metric before hiding this one.")
            self._sync_widgets()
            return
        if len(visible) <= 1:
            self._sync_widgets()
            return
        visible.discard(mode)
        self._state = self._make_valid_state(visible, primary)
        self._sync_widgets()
        self._emit_state_changed()

    def _on_primary_clicked(self, mode: str) -> None:
        mode = normalize_sensorgram_metric_name(mode)
        if mode not in self._state.visible_modes:
            self._sync_widgets()
            return
        if mode == self._state.primary_mode:
            return
        self._state = self._make_valid_state(self._state.visible_modes, mode)
        self._sync_widgets()
        self._emit_state_changed()

    def _on_color_changed(self, mode: str, color: str) -> None:
        window = self._window
        if hasattr(window, "_set_sensorgram_metric_color"):
            window._set_sensorgram_metric_color(mode, color, save=False)
        else:
            colors = getattr(window, "TRACE_METRIC_COLORS", None)
            if isinstance(colors, dict):
                colors[normalize_sensorgram_metric_name(mode)] = str(color)
            time_plot_colors = getattr(window, "SENSORGRAM_TIME_PLOT_COLORS", None)
            if isinstance(time_plot_colors, dict):
                time_plot_colors[normalize_sensorgram_metric_name(mode)] = str(color)
            if hasattr(window, "_apply_metric_color_styles"):
                window._apply_metric_color_styles()
            if hasattr(window, "_request_deferred_ui_refresh"):
                window._request_deferred_ui_refresh(trace_plot=True, summary=True)
        self._sync_widgets()


class SensorgramPlotSettingsDialog(QDialog):
    def __init__(self, window) -> None:
        super().__init__(window)
        self._window = window
        self.setWindowTitle("Sensogram settings")
        self.setModal(True)
        self.setMinimumWidth(430)

        layout = QVBoxLayout()
        tabs = QTabWidget(self)
        tabs.setDocumentMode(False)
        tabs.setStyleSheet(
            "QTabWidget::pane {"
            "  border: 1px solid #3a4250;"
            "  border-radius: 8px;"
            "  background: #10151d;"
            "}"
            "QTabBar::tab {"
            "  background: #1a2029;"
            "  color: #d7dde7;"
            "  border: 1px solid #3a4250;"
            "  border-bottom: none;"
            "  padding: 8px 14px;"
            "  margin-right: 4px;"
            "  border-top-left-radius: 8px;"
            "  border-top-right-radius: 8px;"
            "}"
            "QTabBar::tab:selected {"
            "  background: #10151d;"
            "  color: #ffffff;"
            "  margin-bottom: -1px;"
            "}"
            "QTabBar::tab:!selected {"
            "  margin-top: 2px;"
            "}"
        )
        tabs.addTab(self._build_live_tab(), "Live mode")
        tabs.addTab(self._build_preview_tab(), "Preview mode")
        tabs.setCurrentIndex(0)
        layout.addWidget(tabs)

        buttons_row = QHBoxLayout()
        buttons_row.setContentsMargins(0, 0, 0, 0)
        buttons_row.setSpacing(8)
        buttons_row.addStretch(1)

        self.apply_icon_button = _make_frameless_icon_button(
            tint_tabler_icon(flow_tabler_icon("checkbox"), QColor("#50d890")),
            "Apply settings",
            size=30,
            parent=self,
        )
        self.apply_icon_button.clicked.connect(self.apply_settings)
        buttons_row.addWidget(self.apply_icon_button)

        self.close_icon_button = _make_frameless_icon_button(
            tint_tabler_icon(flow_tabler_icon("x"), QColor("#e46a6a")),
            "Close dialog",
            size=30,
            parent=self,
        )
        self.close_icon_button.clicked.connect(self.reject)
        buttons_row.addWidget(self.close_icon_button)

        buttons_widget = QWidget(self)
        buttons_widget.setLayout(buttons_row)
        layout.addWidget(buttons_widget)
        self.setLayout(layout)

    def _add_section_header(self, layout: QVBoxLayout, title: str) -> None:
        label = QLabel(title)
        label.setProperty("class", "sectionTitle")
        label.setStyleSheet("font-weight: 700;")
        layout.addWidget(label)
        separator = QFrame(self)
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator)

    def _build_live_tab(self) -> QWidget:
        page = QWidget(self)
        outer = QVBoxLayout(page)
        outer.setSpacing(12)
        outer.setContentsMargins(12, 12, 12, 12)

        self.metric_mode_selector = MetricModeSelector(self._window)
        self.metric_mode_selector.set_state(
            getattr(self._window, "_sensorgram_metric_visible_modes", {"smoothed_max"}),
            getattr(self._window, "_sensorgram_metric_primary_mode", "smoothed_max"),
        )
        self.metric_mode_selector.selectionChanged.connect(self._on_metric_mode_selection_changed)
        outer.addWidget(self.metric_mode_selector)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.antialias_check = QCheckBox("Enabled")
        self.antialias_check.setChecked(bool(getattr(self._window, "_plot_antialias_enabled", False)))
        self.antialias_check.setToolTip(
            "Enable anti-aliasing for sensorgram drawing."
        )
        form.addRow("Anti-aliasing", self.antialias_check)

        line_style_row = QHBoxLayout()
        line_style_row.setContentsMargins(0, 0, 0, 0)
        line_style_row.setSpacing(8)

        interp_label = QLabel("Interpolation")
        interp_label.setToolTip("Choose how the sensorgram line is rendered.")
        line_style_row.addWidget(interp_label)

        self.line_mode_combo = QComboBox()
        for option in _LINE_MODE_OPTIONS:
            self.line_mode_combo.addItem(option.label, option.step_mode)
        current_step_mode = _normalize_line_mode(getattr(self._window, "_sensorgram_line_step_mode", None))
        current_index = 0
        for index in range(self.line_mode_combo.count()):
            if self.line_mode_combo.itemData(index) == current_step_mode:
                current_index = index
                break
        self.line_mode_combo.setCurrentIndex(current_index)
        self.line_mode_combo.setToolTip(
            "Choose how points are connected in the sensorgram line: straight, stepped, or spline-smoothed."
        )
        line_style_row.addWidget(self.line_mode_combo)

        width_label = QLabel("Width")
        width_label.setToolTip("Line thickness for the sensorgram curves.")
        line_style_row.addWidget(width_label)

        self.line_width_spin = QDoubleSpinBox()
        self.line_width_spin.setRange(0.5, 10.0)
        self.line_width_spin.setSingleStep(0.1)
        self.line_width_spin.setDecimals(1)
        self.line_width_spin.setSuffix(" px")
        self.line_width_spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.line_width_spin.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.line_width_spin.setValue(float(getattr(self._window, "_sensorgram_line_width_px", 2.2)))
        self.line_width_spin.setToolTip("Set the thickness of the sensorgram line in pixels.")
        self.line_width_spin.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.line_width_spin.setFixedWidth(62)
        line_style_row.addWidget(self.line_width_spin)
        line_style_row.addStretch(1)

        self.follow_latest_buffer_combo = QComboBox()
        current_buffer_fraction = getattr(self._window, "_metric_autoscale_follow_latest_buffer_fraction", 0.05)
        if current_buffer_fraction is None:
            current_buffer_fraction = 0.0
        try:
            current_buffer_fraction = float(current_buffer_fraction)
        except (TypeError, ValueError):
            current_buffer_fraction = 0.05
        current_index = 0
        for index, (label, fraction) in enumerate(_BUFFER_OPTIONS):
            self.follow_latest_buffer_combo.addItem(label, fraction)
            if fraction is None:
                match = current_buffer_fraction <= 0.0
            else:
                match = abs(float(fraction) - current_buffer_fraction) < abs(
                    float(_BUFFER_OPTIONS[current_index][1] or 0.0) - current_buffer_fraction
                )
            if match:
                current_index = index
        self.follow_latest_buffer_combo.setCurrentIndex(current_index)
        self.follow_latest_buffer_combo.setToolTip(
            "Keep a blank buffer on the right side of the sensorgram plot so new data does not immediately touch the edge."
        )
        form.addRow("Right buffer", self.follow_latest_buffer_combo)

        autoscale_row = QHBoxLayout()
        autoscale_row.setContentsMargins(0, 0, 0, 0)
        autoscale_row.setSpacing(8)

        self.autoscale_throttle_combo = QComboBox()
        current_throttle_s = getattr(self._window, "_metric_autoscale_min_interval_s", 1.0)
        try:
            current_throttle_s = float(current_throttle_s)
        except (TypeError, ValueError):
            current_throttle_s = 1.0
        current_index = 0
        for index, (label, seconds) in enumerate(_AUTOSCALE_THROTTLE_OPTIONS):
            self.autoscale_throttle_combo.addItem(label, seconds)
            if abs(float(seconds or 0.0) - current_throttle_s) < abs(float(_AUTOSCALE_THROTTLE_OPTIONS[current_index][1] or 0.0) - current_throttle_s):
                current_index = index
        self.autoscale_throttle_combo.setCurrentIndex(current_index)
        self.autoscale_throttle_combo.setToolTip(
            "Limit how often the sensorgram plot may rescale its axes. Higher throttling reduces redraw churn, but the view follows new data more slowly."
        )
        autoscale_row.addWidget(QLabel("Throttling"))
        autoscale_row.addWidget(self.autoscale_throttle_combo)

        self.skip_tiny_changes_check = QCheckBox("Gate")
        self.skip_tiny_changes_check.setChecked(
            bool(getattr(self._window, "_metric_autoscale_skip_tiny_changes_enabled", True))
        )
        self.skip_tiny_changes_check.setToolTip(
            "Skip autoscale updates when the visible range change is very small. This reduces unnecessary redraws in both display modes."
        )
        autoscale_row.addSpacing(12)
        autoscale_row.addWidget(self.skip_tiny_changes_check)
        autoscale_row.addStretch(1)

        autoscale_row_widget = QWidget(self)
        autoscale_row_widget.setLayout(autoscale_row)
        form.addRow("Autoscale", autoscale_row_widget)

        metric_form = QFormLayout()
        metric_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        points_row = QHBoxLayout()
        points_row.setContentsMargins(0, 0, 0, 0)
        points_row.setSpacing(12)

        self.metric_display_points_spin = QSpinBox()
        make_compact_spinbox(self.metric_display_points_spin)
        self.metric_display_points_spin.setRange(16, 100000)
        self.metric_display_points_spin.setValue(int(getattr(self._window, "_plot_display_points", 512)))
        self.metric_display_points_spin.setToolTip(
            "Maximum number of points kept visible in the sensorgram time plot."
        )
        points_row.addWidget(self.metric_display_points_spin)

        self.recent_tail_points_spin = QSpinBox()
        make_compact_spinbox(self.recent_tail_points_spin)
        self.recent_tail_points_spin.setRange(0, 10000)
        self.recent_tail_points_spin.setSingleStep(50)
        self.recent_tail_points_spin.setValue(
            int(getattr(self._window, "_sensorgram_compression_recent_tail_points", 300))
        )
        self.recent_tail_points_spin.setToolTip(
            "Number of newest points kept uncompressed in the absolute sensorgram plot. Older data is shown using compression."
        )
        points_row.addSpacing(12)
        points_row.addWidget(QLabel("Live tail"))
        points_row.addWidget(self.recent_tail_points_spin)
        points_row.addStretch(1)

        points_row_widget = QWidget(self)
        points_row_widget.setLayout(points_row)
        metric_form.addRow("Display points", points_row_widget)

        line_style_row_widget = QWidget(self)
        line_style_row_widget.setLayout(line_style_row)
        metric_form.addRow("Line", line_style_row_widget)

        self.envelope_overlay_check = QCheckBox("Show")
        self.envelope_overlay_check.setChecked(
            bool(getattr(self._window, "_sensorgram_metric_envelope_overlay_enabled", False))
        )
        self.envelope_overlay_check.setToolTip(
            "Draw the min/max envelope as a faint secondary layer over the trend line."
        )
        envelope_row = QHBoxLayout()
        envelope_row.setContentsMargins(0, 0, 0, 0)
        envelope_row.setSpacing(8)
        envelope_row.addWidget(self.envelope_overlay_check)

        self.envelope_overlay_alpha_slider = QSlider(Qt.Orientation.Horizontal)
        self.envelope_overlay_alpha_slider.setRange(0, 100)
        self.envelope_overlay_alpha_slider.setSingleStep(1)
        self.envelope_overlay_alpha_slider.setPageStep(10)
        current_alpha = int(getattr(self._window, "_sensorgram_metric_envelope_overlay_alpha", 16))
        current_alpha = max(0, min(current_alpha, 100))
        self.envelope_overlay_alpha_slider.setValue(current_alpha)
        self.envelope_overlay_alpha_value = QLabel(f"{current_alpha}%")
        self.envelope_overlay_alpha_value.setMinimumWidth(40)
        self.envelope_overlay_alpha_slider.setToolTip(
            "Control how visible the envelope band is. Lower values are more subtle; higher values make the band more obvious."
        )
        self.envelope_overlay_alpha_slider.valueChanged.connect(
            lambda value: self.envelope_overlay_alpha_value.setText(f"{int(value)}%")
        )
        envelope_row.addWidget(self.envelope_overlay_alpha_slider, 1)
        envelope_row.addWidget(self.envelope_overlay_alpha_value)
        envelope_row_widget = QWidget(self)
        envelope_row_widget.setLayout(envelope_row)
        metric_form.addRow("Envelope overlay", envelope_row_widget)

        outer.addLayout(metric_form)
        outer.addLayout(form)
        return page

    def _build_preview_tab(self) -> QWidget:
        page = QWidget(self)
        outer = QVBoxLayout(page)
        outer.setSpacing(12)
        outer.setContentsMargins(12, 12, 12, 12)

        preview_form = QFormLayout()
        preview_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        heatmap_points_row = QHBoxLayout()
        heatmap_points_row.setContentsMargins(0, 0, 0, 0)
        heatmap_points_row.setSpacing(8)

        heatmap_label = QLabel("Heatmap")
        heatmap_label.setToolTip("Display-point cap for the preview heatmap row history.")
        heatmap_points_row.addWidget(heatmap_label)

        self.heatmap_rows_spin = QSpinBox()
        make_compact_spinbox(self.heatmap_rows_spin)
        self.heatmap_rows_spin.setRange(16, 100000)
        self.heatmap_rows_spin.setValue(int(getattr(self._window, "_sensorgram_heatmap_history_max_rows", 800)))
        self.heatmap_rows_spin.setToolTip("Maximum number of heatmap rows kept in the preview history.")
        heatmap_points_row.addWidget(self.heatmap_rows_spin)
        heatmap_points_row.addStretch(1)

        heatmap_points_row_widget = QWidget(self)
        heatmap_points_row_widget.setLayout(heatmap_points_row)
        preview_form.addRow("Points rendered", heatmap_points_row_widget)

        preview_note = QLabel("These settings affect the sensorgram heatmap preview display.")
        preview_note.setWordWrap(True)
        preview_form.addRow("", preview_note)

        outer.addLayout(preview_form)
        outer.addStretch(1)
        return page

    def apply_settings(self) -> None:
        window = self._window
        selector = getattr(self, "metric_mode_selector", None)
        if selector is not None:
            state = selector.state()
            if hasattr(window, "_apply_sensorgram_metric_selection"):
                window._apply_sensorgram_metric_selection(state.visible_modes, state.primary_mode, save=False)
            else:
                window._sensorgram_metric_visible_modes = set(state.visible_modes)
                window._sensorgram_metric_primary_mode = state.primary_mode
                window._trace_stats_metric_name = state.primary_mode
        window._plot_antialias_enabled = bool(self.antialias_check.isChecked())
        pg.setConfigOptions(antialias=window._plot_antialias_enabled)
        window._plot_display_points = int(self.metric_display_points_spin.value())
        window._sensorgram_line_step_mode = _normalize_line_mode(self.line_mode_combo.currentData())
        window._sensorgram_line_width_px = max(float(self.line_width_spin.value()), 0.5)
        window._sensorgram_heatmap_history_max_rows = int(self.heatmap_rows_spin.value())
        buffer_fraction = self.follow_latest_buffer_combo.currentData()
        window._metric_autoscale_follow_latest_buffer_fraction = 0.0 if buffer_fraction is None else float(buffer_fraction)
        throttle_s = self.autoscale_throttle_combo.currentData()
        window._metric_autoscale_min_interval_s = float(throttle_s or 0.0)
        window._metric_autoscale_throttle_mode = str(self.autoscale_throttle_combo.currentText())
        window._metric_autoscale_skip_tiny_changes_enabled = bool(self.skip_tiny_changes_check.isChecked())
        window._sensorgram_compression_recent_tail_points = max(int(self.recent_tail_points_spin.value()), 0)
        window._sensorgram_metric_envelope_overlay_enabled = bool(self.envelope_overlay_check.isChecked())
        window._sensorgram_metric_envelope_overlay_alpha = max(int(self.envelope_overlay_alpha_slider.value()), 0)
        window._spectrum_render_cache_key = None
        if hasattr(window, "_plot_view_cache") and hasattr(window._plot_view_cache, "refresh_live_absolute_metric_cache"):
            try:
                if bool(getattr(window, "_live_active", False)) and getattr(window, "_normalize_sensorgram_view_mode", lambda value: value)(getattr(window, "_sensorgram_view_mode", "absolute")) == "absolute":
                    selected_metrics = set(getattr(window, "_selected_trace_metrics", lambda: [])())
                    archive_path = getattr(window, "_metric_archive_path", None)
                    if hasattr(window._plot_view_cache, "set_live_absolute_metric_tail_size"):
                        window._plot_view_cache.set_live_absolute_metric_tail_size(
                            selected_metrics,
                            recent_tail_points=int(window._sensorgram_compression_recent_tail_points),
                        )
                    window._plot_view_cache.refresh_live_absolute_metric_cache(
                        selected_metrics,
                        target_points=int(window._plot_display_points),
                        archive_path=Path(archive_path) if archive_path else None,
                    )
            except Exception:
                pass
        if hasattr(window, "_apply_sensorgram_display_style"):
            try:
                window._apply_sensorgram_display_style()
            except Exception:
                pass
        if hasattr(window, "_apply_metric_color_styles"):
            try:
                window._apply_metric_color_styles()
            except Exception:
                pass
        if hasattr(window, "_apply_sensorgram_time_axis_mode"):
            try:
                window._apply_sensorgram_time_axis_mode(redraw=False)
            except Exception:
                pass
        if hasattr(window, "_save_ui_state"):
            window._save_ui_state()
        elif hasattr(window, "_schedule_ui_state_persist"):
            window._schedule_ui_state_persist()
        if hasattr(window, "_request_deferred_ui_refresh"):
            window._request_deferred_ui_refresh(trace_plot=True, telemetry=True, live_estimate=True, summary=True)

    def _on_metric_mode_selection_changed(self, visible_modes, primary_mode: str) -> None:
        window = self._window
        if hasattr(window, "_apply_sensorgram_metric_selection"):
            window._apply_sensorgram_metric_selection(visible_modes, primary_mode, save=False)
        else:
            window._sensorgram_metric_visible_modes = set(visible_modes)
            window._sensorgram_metric_primary_mode = primary_mode
            window._trace_stats_metric_name = primary_mode

    def accept(self) -> None:
        self.apply_settings()
        super().accept()

    def keyPressEvent(self, event) -> None:
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            focus_widget = self.focusWidget()
            if isinstance(focus_widget, (QComboBox, QAbstractSpinBox, QLineEdit)):
                event.ignore()
                return
        super().keyPressEvent(event)


def show_sensorgram_plot_settings_dialog_for(window) -> None:
    dialog = SensorgramPlotSettingsDialog(window)
    dialog.exec()

