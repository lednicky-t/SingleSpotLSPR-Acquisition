from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pyqtgraph as pg

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QLabel,
    QVBoxLayout,
    QSlider,
    QSpinBox,
    QWidget,
)

from lspr_app.gui.ui_helpers import make_compact_spinbox


@dataclass(frozen=True)
class _LineModeOption:
    label: str
    step_mode: str | None


_LINE_MODE_OPTIONS: tuple[_LineModeOption, ...] = (
    _LineModeOption("Linear", None),
    _LineModeOption("Step left", "left"),
    _LineModeOption("Step right", "right"),
    _LineModeOption("Step center", "center"),
)

_TIME_AXIS_OPTIONS: tuple[tuple[str, str], ...] = (
    ("Elapsed time", "elapsed"),
    ("Local clock time", "clock"),
)

_ROLLING_WINDOW_OPTIONS: tuple[tuple[str, float], ...] = (
    ("1 min", 60.0),
    ("10 min", 600.0),
    ("30 min", 1800.0),
    ("1 h", 3600.0),
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
    return normalized if normalized in {"left", "right", "center"} else None


class SensorgramPlotSettingsDialog(QDialog):
    def __init__(self, window) -> None:
        super().__init__(window)
        self._window = window
        self.setWindowTitle("Sensorgram plot settings")
        self.setModal(True)
        self.setMinimumWidth(430)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Close,
            parent=self,
        )
        apply_button = buttons.button(QDialogButtonBox.StandardButton.Apply)
        close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        apply_button.setDefault(False)
        apply_button.setAutoDefault(False)
        close_button.setDefault(False)
        close_button.setAutoDefault(False)
        apply_button.clicked.connect(self.apply_settings)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout()
        layout.addWidget(self._build_single_page())
        layout.addWidget(buttons)
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

    def _build_single_page(self) -> QWidget:
        page = QWidget(self)
        outer = QVBoxLayout(page)
        outer.setSpacing(10)
        outer.setContentsMargins(0, 0, 0, 0)

        self._add_section_header(outer, "Common")
        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.antialias_check = QCheckBox("Enabled")
        self.antialias_check.setChecked(bool(getattr(self._window, "_plot_antialias_enabled", False)))
        self.antialias_check.setToolTip(
            "Enable anti-aliasing for sensorgram drawing."
        )
        form.addRow("Anti-aliasing", self.antialias_check)

        self.time_axis_mode_combo = QComboBox()
        current_time_axis_mode = str(getattr(self._window, "_sensorgram_time_axis_mode", "elapsed") or "elapsed").strip().lower()
        if current_time_axis_mode not in {"elapsed", "clock"}:
            current_time_axis_mode = "elapsed"
        current_index = 0
        for index, (label, mode) in enumerate(_TIME_AXIS_OPTIONS):
            self.time_axis_mode_combo.addItem(label, mode)
            if mode == current_time_axis_mode:
                current_index = index
        self.time_axis_mode_combo.setCurrentIndex(current_index)
        self.time_axis_mode_combo.setToolTip(
            "Choose whether the sensorgram x-axis shows elapsed time since start or local clock time. The underlying data remain elapsed seconds."
        )
        form.addRow("Time axis", self.time_axis_mode_combo)

        points_row = QHBoxLayout()
        points_row.setContentsMargins(0, 0, 0, 0)
        points_row.setSpacing(8)

        points_title = QLabel("Points rendered:")
        points_title.setToolTip(
            "Limit the amount of data drawn in the sensorgram display. The plot will keep the newest points and compress older history as needed."
        )
        points_row.addWidget(points_title)

        metric_label = QLabel("Metric")
        metric_label.setToolTip("Display-point cap for the metric time plot.")
        points_row.addWidget(metric_label)
        self.metric_display_points_spin = QSpinBox()
        make_compact_spinbox(self.metric_display_points_spin)
        self.metric_display_points_spin.setRange(16, 100000)
        self.metric_display_points_spin.setValue(int(getattr(self._window, "_plot_display_points", 512)))
        self.metric_display_points_spin.setToolTip(
            "Maximum number of points kept visible in the metric time plot."
        )
        points_row.addWidget(self.metric_display_points_spin)

        heatmap_label = QLabel("Heatmap")
        heatmap_label.setToolTip("Display-point cap for the heatmap row history.")
        points_row.addWidget(heatmap_label)
        self.heatmap_rows_spin = QSpinBox()
        make_compact_spinbox(self.heatmap_rows_spin)
        self.heatmap_rows_spin.setRange(16, 100000)
        self.heatmap_rows_spin.setValue(int(getattr(self._window, "_sensorgram_heatmap_history_max_rows", 800)))
        self.heatmap_rows_spin.setToolTip(
            "Maximum number of heatmap rows kept in the displayed history."
        )
        points_row.addWidget(self.heatmap_rows_spin)
        points_row.addStretch(1)

        points_row_widget = QWidget(self)
        points_row_widget.setLayout(points_row)
        form.addRow(points_row_widget)

        self.rolling_window_combo = QComboBox()
        current_window_s = float(getattr(self._window, "_trace_display_window_s", 60.0))
        current_index = 0
        for index, (label, seconds) in enumerate(_ROLLING_WINDOW_OPTIONS):
            self.rolling_window_combo.addItem(label, seconds)
            if abs(float(seconds) - current_window_s) < abs(float(_ROLLING_WINDOW_OPTIONS[current_index][1]) - current_window_s):
                current_index = index
        self.rolling_window_combo.setCurrentIndex(current_index)
        self.rolling_window_combo.setToolTip(
            "Set the shared rolling window length. This controls the visible time span when the sensorgram is in rolling mode."
        )
        form.addRow("Rolling window", self.rolling_window_combo)

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
            "Keep a blank buffer on the right side of the plot so new data does not immediately touch the edge."
        )
        form.addRow("Right buffer", self.follow_latest_buffer_combo)

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
            "Limit how often the plot may rescale its axes. Higher throttling reduces redraw churn, but the view follows new data more slowly."
        )
        form.addRow("Autoscale throttling", self.autoscale_throttle_combo)

        self.skip_tiny_changes_check = QCheckBox("Skip tiny changes")
        self.skip_tiny_changes_check.setChecked(
            bool(getattr(self._window, "_metric_autoscale_skip_tiny_changes_enabled", True))
        )
        self.skip_tiny_changes_check.setToolTip(
            "Skip autoscale updates when the visible range change is very small. This reduces unnecessary redraws."
        )
        form.addRow("Autoscale gate", self.skip_tiny_changes_check)

        outer.addLayout(form)

        self._add_section_header(outer, "Metric")
        metric_form = QFormLayout()
        metric_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
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
            "Choose how points are connected in the plot: straight line, or stepped between samples."
        )
        metric_form.addRow("Line interpolation", self.line_mode_combo)

        self.recent_tail_points_spin = QSpinBox()
        make_compact_spinbox(self.recent_tail_points_spin)
        self.recent_tail_points_spin.setRange(0, 10000)
        self.recent_tail_points_spin.setSingleStep(50)
        self.recent_tail_points_spin.setValue(
            int(getattr(self._window, "_sensorgram_compression_recent_tail_points", 300))
        )
        self.recent_tail_points_spin.setToolTip(
            "Number of newest metric points kept uncompressed in the absolute sensorgram plot. Older data is shown using compression."
        )
        metric_form.addRow("Recent raw tail points", self.recent_tail_points_spin)

        self.envelope_overlay_check = QCheckBox("Show envelope overlay")
        self.envelope_overlay_check.setChecked(
            bool(getattr(self._window, "_sensorgram_metric_envelope_overlay_enabled", False))
        )
        self.envelope_overlay_check.setToolTip(
            "Draw the min/max envelope as a faint secondary layer over the trend line. The main trace still uses the compression trend, not the envelope."
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

        note = QLabel("These settings affect the sensorgram time plot and the spectrum line plot display.")
        note.setWordWrap(True)
        metric_form.addRow("", note)

        outer.addLayout(metric_form)
        return page

    def apply_settings(self) -> None:
        window = self._window
        window._plot_antialias_enabled = bool(self.antialias_check.isChecked())
        pg.setConfigOptions(antialias=window._plot_antialias_enabled)
        window._sensorgram_time_axis_mode = str(self.time_axis_mode_combo.currentData() or "elapsed")
        window._plot_display_points = int(self.metric_display_points_spin.value())
        window._sensorgram_heatmap_history_max_rows = int(self.heatmap_rows_spin.value())
        window._trace_display_window_s = float(self.rolling_window_combo.currentData())
        buffer_fraction = self.follow_latest_buffer_combo.currentData()
        window._metric_autoscale_follow_latest_buffer_fraction = 0.0 if buffer_fraction is None else float(buffer_fraction)
        throttle_s = self.autoscale_throttle_combo.currentData()
        window._metric_autoscale_min_interval_s = float(throttle_s or 0.0)
        window._metric_autoscale_throttle_mode = str(self.autoscale_throttle_combo.currentText())
        window._metric_autoscale_skip_tiny_changes_enabled = bool(self.skip_tiny_changes_check.isChecked())
        window._sensorgram_line_step_mode = _normalize_line_mode(self.line_mode_combo.currentData())
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
        if hasattr(window, "_apply_sensorgram_time_axis_mode"):
            try:
                window._apply_sensorgram_time_axis_mode(redraw=False)
            except Exception:
                pass
        if hasattr(window, "_schedule_ui_state_persist"):
            window._schedule_ui_state_persist()
        if hasattr(window, "_request_deferred_ui_refresh"):
            window._request_deferred_ui_refresh(trace_plot=True, telemetry=True, live_estimate=True, summary=True)

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
