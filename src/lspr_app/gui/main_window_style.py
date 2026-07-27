"""Application stylesheet and plot-widget styling for the main window.

These functions read theme colours from ``window._theme_palette()`` (which
remains an instance method on ``MainWindow``) and apply the resulting styles.

Functions
---------
``apply_modern_style_for(window)``
    Build and set the complete application ``QSS`` stylesheet.
``style_plot_widgets_for(window)``
    Restyle pyqtgraph plot widgets, crosshairs, markers, and region items to
    match the current theme.
"""

from __future__ import annotations

from pathlib import Path

import pyqtgraph as pg

from PyQt6.QtGui import QColor

from lspr_app.gui.main_window_plotting import apply_metric_color_styles_for


def apply_modern_style_for(window) -> None:
    """Build and apply the full application QSS stylesheet.

    Reads the colour palette from ``window._theme_palette()`` and injects
    ``TRACE_METRIC_COLORS`` for the metric-specific checkbox tints.  The
    checkmark SVG asset path is resolved relative to this file's directory
    (same ``gui/`` package as the caller).
    """
    palette = window._theme_palette()
    checkmark_icon = (Path(__file__).resolve().parent / "assets" / "checkmark.svg").as_posix()
    stylesheet = """
        QMainWindow, QWidget {
            background: %(bg)s;
            color: %(fg)s;
            font-size: 12px;
        }
        QToolTip {
            background-color: %(bg)s;
            color: %(fg)s;
            border: 1px solid %(border)s;
            padding: 4px 6px;
        }
        QLabel {
            color: %(muted)s;
        }
        QScrollArea {
            border: none;
            background: transparent;
        }
        QWidget#titleBar {
            background: %(bg)s;
            border-bottom: 1px solid %(border)s;
        }
        QLabel#brandIconLabel {
            background: transparent;
            border: none;
        }
        QLabel#windowModeLabel {
            color: %(fg)s;
            font-weight: 700;
            background: transparent;
            border: none;
            padding: 0px;
            margin: 0px;
        }
        QLabel#sensorgramHeaderLabel {
            color: %(title)s;
            font-size: 13px;
            font-weight: 800;
            letter-spacing: 0.8px;
            background: transparent;
            border: none;
            padding: 0px;
            margin: 0px;
        }
        QToolButton#logViewButton {
            color: %(fg)s;
            background: rgba(255, 255, 255, 0.04);
            border: 1px solid %(border)s;
            border-radius: 10px;
            padding: 1px 10px;
            margin: 0px;
            font-size: 11px;
            font-weight: 700;
        }
        QToolButton#logViewButton:hover {
            background: rgba(255, 255, 255, 0.07);
            border-color: %(accent_hover)s;
        }
        QToolButton#logViewButton:checked {
            background: %(accent)s;
            color: %(bg)s;
            border-color: %(accent_hover)s;
        }
        QToolButton#logViewButton:checked:hover {
            background: %(accent_hover)s;
            color: %(bg)s;
            border-color: %(accent_hover)s;
        }
        QToolButton#sensorgramViewModeButton,
        QToolButton#sensorgramContentModeButton,
        QToolButton#sensorgramYAxisModeButton,
        QToolButton#sensorgramWindowButton {
            background: transparent;
            border: none;
            padding: 0px;
            margin: 0px;
            color: #e8d85f;
            font-weight: 600;
        }
        QToolButton#sensorgramViewModeButton:hover,
        QToolButton#sensorgramContentModeButton:hover,
        QToolButton#sensorgramYAxisModeButton:hover,
        QToolButton#sensorgramWindowButton:hover {
            background: transparent;
            border: none;
        }
        QToolButton#sensorgramViewModeButton:pressed,
        QToolButton#sensorgramContentModeButton:pressed,
        QToolButton#sensorgramYAxisModeButton:pressed,
        QToolButton#sensorgramWindowButton:pressed {
            background: transparent;
            border: none;
        }
        QLabel#traceClearLabel {
            color: %(accent)s;
            background: transparent;
            border: none;
            font-size: 11px;
            font-weight: 700;
            padding: 0px 2px;
        }
        QLabel#traceClearLabel:hover {
            color: %(accent_hover)s;
        }
        QToolButton#windowButton {
            background: transparent;
            border: none;
            border-radius: 0px;
            padding: 0px;
            margin: 0px;
            min-width: 0px;
            min-height: 0px;
        }
        QToolButton#windowButton:hover {
            background: %(button_hover)s;
            border: none;
        }
        QToolButton#windowButton:pressed {
            background: %(button_pressed)s;
            border: none;
        }
        QToolButton {
            background: %(button)s;
            border: 1px solid %(border)s;
            border-radius: 8px;
            padding: 3px 6px;
        }
        QToolButton:hover, QPushButton:hover {
            background: %(button_hover)s;
            border-color: %(border_hover)s;
        }
        QToolButton:checked {
            background: %(button_pressed)s;
            border-color: %(accent)s;
        }
        QPushButton, QComboBox, QSpinBox, QDoubleSpinBox {
            background: %(field)s;
            border: 1px solid %(border)s;
            border-radius: 8px;
            padding: 3px 6px;
            min-width: 0px;
        }
        QSpinBox, QDoubleSpinBox {
            border-radius: 3px;
            padding: 2px 5px;
        }
        QSpinBox::up-button, QSpinBox::down-button,
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
            width: 0px;
            border: none;
            background: transparent;
        }
        QSpinBox::up-arrow, QSpinBox::down-arrow,
        QDoubleSpinBox::up-arrow, QDoubleSpinBox::down-arrow {
            width: 0px;
            height: 0px;
        }
        QPushButton:pressed {
            background: %(button_pressed)s;
        }
        QPushButton#toolbarButton {
            background: %(button)s;
        }
        QPushButton#toolbarButton:hover {
            background: %(button_hover)s;
        }
        QToolButton#primaryIconButton {
            background: %(accent_button)s;
            border-color: %(accent_button)s;
        }
        QToolButton#primaryIconButton:hover {
            background: %(accent_hover)s;
            border-color: %(accent_hover)s;
        }
        QToolButton#dangerIconButton {
            background: %(danger_button)s;
            border-color: %(danger_button)s;
        }
        QToolButton#dangerIconButton:hover {
            background: %(danger_hover)s;
            border-color: %(danger_hover)s;
        }
        QToolButton#themeToggle {
            min-width: 26px;
            min-height: 26px;
            border-radius: 13px;
            padding: 0px;
            background: %(button)s;
        }
        QComboBox::drop-down {
            border: none;
            width: 16px;
        }
        QLineEdit, QTextEdit {
            background: %(field)s;
            border: 1px solid %(border)s;
            border-radius: 10px;
            padding: 6px;
        }
        QWidget#recordingContextPanel {
            background: transparent;
            border: none;
        }
        QLineEdit#recordingPathEdit, QLineEdit#recordingExperimentEdit {
            background: transparent;
            border: none;
            border-bottom: 1px solid %(border)s;
            border-radius: 0px;
            padding: 0px 2px;
            margin: 0px;
            min-height: 20px;
        }
        QLineEdit#recordingPathEdit:focus, QLineEdit#recordingExperimentEdit:focus {
            border-bottom-color: %(accent)s;
        }
        QToolButton#framelessIconButton {
            min-width: 24px;
            min-height: 24px;
            padding: 0px;
            margin: 0px;
        }
        QMenuBar {
            background: %(bg)s;
            color: %(fg)s;
            border: none;
        }
        QMenuBar::item {
            background: transparent;
            padding: 4px 10px;
            margin: 2px 2px;
            border-radius: 4px;
        }
        QMenuBar::item:selected {
            background: %(button_hover)s;
        }
        QMenuBar::item:pressed {
            background: %(button_pressed)s;
        }
        QMenu {
            background-color: %(field)s;
            color: %(fg)s;
            border: 1px solid %(border)s;
            padding: 4px;
        }
        QMenu::item {
            padding: 4px 20px 4px 20px;
            border-radius: 4px;
        }
        QMenu::item:selected {
            background-color: %(button_hover)s;
            color: %(fg)s;
        }
        QMenu::separator {
            height: 1px;
            background: %(border)s;
            margin: 4px 8px;
        }
        QTextEdit {
            color: %(muted)s;
        }
        QTextEdit#logTerminal {
            padding: 4px;
            color: %(fg)s;
            border-radius: 10px;
        }
        QTextEdit#sessionStatisticsText,
        QTextEdit#sessionSettingsText {
            padding: 4px;
            color: %(fg)s;
            border-radius: 10px;
            background: %(field)s;
            border: 1px solid %(border)s;
        }
        QCheckBox {
            spacing: 6px;
        }
        QCheckBox::indicator {
            width: 16px;
            height: 16px;
            border-radius: 4px;
            border: 1px solid %(border)s;
            background: %(field)s;
        }
        QCheckBox::indicator:checked {
            background: %(checkbox_accent)s;
            border-color: %(checkbox_accent)s;
            image: url(__CHECKMARK_ICON__);
        }
        QCheckBox#traceMaxCheck {
            color: %(trace_max)s;
        }
        QCheckBox#traceMaxCheck::indicator:checked {
            background: %(trace_max)s;
            border-color: %(trace_max)s;
        }
        QCheckBox#traceCentroidCheck {
            color: %(trace_centroid)s;
        }
        QCheckBox#traceCentroidCheck::indicator:checked {
            background: %(trace_centroid)s;
            border-color: %(trace_centroid)s;
        }
        QCheckBox#tracePolyCheck {
            color: %(trace_poly)s;
        }
        QCheckBox#tracePolyCheck::indicator:checked {
            background: %(trace_poly)s;
            border-color: %(trace_poly)s;
        }
        QCheckBox#traceGaussianCheck {
            color: %(trace_gaussian)s;
        }
        QCheckBox#traceGaussianCheck::indicator:checked {
            background: %(trace_gaussian)s;
            border-color: %(trace_gaussian)s;
        }
        QTabWidget::pane {
            border: none;
            border-radius: 0px;
            background: transparent;
            top: 0px;
        }
        QTabBar::tab {
            background: transparent;
            border: none;
            padding: 1px 4px;
            margin-right: 2px;
            border-top-left-radius: 0px;
            border-top-right-radius: 0px;
            color: %(muted)s;
            text-align: left;
        }
        QTabBar::tab:selected {
            background: transparent;
            color: %(fg)s;
            border: none;
        }
        QTabBar::tab:!selected:hover {
            color: %(fg)s;
        }
        QGroupBox {
            background: transparent;
            border: none;
            border-radius: 0px;
            margin-top: 0px;
            padding-top: 0px;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 0px;
            padding: 0px;
            color: transparent;
            height: 0px;
        }
        QToolButton#collapseToggle {
            background: transparent;
            border: none;
            border-radius: 0px;
            padding: 2px 0px 4px 0px;
            font-weight: 600;
            color: %(muted)s;
            text-align: left;
        }
        QSlider::groove:horizontal {
            height: 8px;
            background: %(grid)s;
            border: 1px solid %(border)s;
            border-radius: 2px;
        }
        QSlider::handle:horizontal {
            width: 16px;
            margin: -3px 0;
            border-radius: 2px;
            border: 1px solid %(border)s;
            background: %(accent)s;
        }
        QScrollBar:vertical {
            background: transparent;
            width: 8px;
            margin: 2px 0 2px 0;
        }
        QScrollBar::handle:vertical {
            background: %(scroll)s;
            border-radius: 4px;
            min-height: 28px;
        }
        QScrollBar::handle:vertical:hover {
            background: %(scroll_hover)s;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
            background: transparent;
            border: none;
        }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
            background: transparent;
        }
        QScrollBar:horizontal {
            background: transparent;
            height: 8px;
            margin: 0 2px 0 2px;
        }
        QScrollBar::handle:horizontal {
            background: %(scroll)s;
            border-radius: 4px;
            min-width: 28px;
        }
        QScrollBar::handle:horizontal:hover {
            background: %(scroll_hover)s;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0px;
            background: transparent;
            border: none;
        }
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
            background: transparent;
        }
        QSplitter::handle {
            background: transparent;
        }
        QSplitter::handle:horizontal {
            width: 8px;
            margin: 0px;
        }
        QSplitter::handle:vertical {
            height: 8px;
            margin: 0px;
        }
        QSplitter::handle:hover {
            background: transparent;
        }
        """ % {
            **palette,
            "trace_max": window.TRACE_METRIC_COLORS["smoothed_max"],
            "trace_centroid": window.TRACE_METRIC_COLORS["centroid"],
            "trace_poly": window.TRACE_METRIC_COLORS["poly_max"],
            "trace_gaussian": window.TRACE_METRIC_COLORS["gaussian_center"],
        }
    window.setStyleSheet(stylesheet.replace("__CHECKMARK_ICON__", checkmark_icon))


def style_plot_widgets_for(window) -> None:
    """Restyle pyqtgraph plots, crosshairs, markers, and regions for the current theme."""
    palette = window._theme_palette()
    for plot in (window.spectrum_plot, window.trace_plot):
        # The GraphicsView background (this widget's overall background, which
        # shows through the margins around the axes/title, not just "inside"
        # the plot) matches the app's general background so the plot blends
        # into its surroundings; the ViewBox background (the actual plotting
        # area behind the data) keeps the previous, separately-set dark color.
        plot.setBackground(palette["bg"])
        plot.getPlotItem().getViewBox().setBackgroundColor(palette["plot_bg"])
        plot.getPlotItem().getViewBox().setBorder(pg.mkPen(palette["plot_border"]))
        bottom_axis = plot.getPlotItem().getAxis("bottom")
        left_axis = plot.getPlotItem().getAxis("left")
        bottom_axis.enableAutoSIPrefix(False)
        left_axis.enableAutoSIPrefix(False)
        bottom_axis.setStyle(tickTextOffset=2)
        left_axis.setStyle(tickTextOffset=2)
        bottom_axis.setTextPen(pg.mkPen(palette["axis_text"]))
        left_axis.setTextPen(pg.mkPen(palette["axis_text"]))
        bottom_axis.setPen(pg.mkPen(palette["axis_pen"]))
        left_axis.setPen(pg.mkPen(palette["axis_pen"]))
        plot.getPlotItem().titleLabel.item.setDefaultTextColor(QColor(palette["fg"]))
        plot.getPlotItem().showGrid(x=True, y=True, alpha=0.16 if window._theme_mode == "dark" else 0.18)
    if hasattr(window, "residual_axis"):
        window.residual_axis.setStyle(tickTextOffset=2)
        window.residual_axis.setTextPen(pg.mkPen(palette["axis_text"]))
        window.residual_axis.setPen(pg.mkPen(palette["axis_pen"]))
    crosshair_color = "#7f93a8" if window._theme_mode == "dark" else "#666666"
    window.spectrum_vline.setPen(pg.mkPen(crosshair_color, width=1))
    window.spectrum_hline.setPen(pg.mkPen(crosshair_color, width=1))
    window.trace_vline.setPen(pg.mkPen(crosshair_color, width=1))
    window.trace_hline.setPen(pg.mkPen(crosshair_color, width=1))
    if window._theme_mode == "dark":
        window.processing_region_item.setBrush(pg.mkBrush(111, 179, 255, 26))
        window.fit_region_item.setBrush(pg.mkBrush(255, 190, 92, 28))
        window.max_marker.setPen(pg.mkPen("#11161c", width=1.4))
        window.poly_marker.setPen(pg.mkPen("#11161c", width=1.4))
        window.gaussian_marker.setPen(pg.mkPen("#11161c", width=1.4))
        window.centroid_marker.setPen(pg.mkPen("#11161c", width=1.4))
        window.residual_curve.setPen(pg.mkPen((0, 0, 0, 0), width=0))
    else:
        window.processing_region_item.setBrush(pg.mkBrush(90, 160, 255, 30))
        window.fit_region_item.setBrush(pg.mkBrush(255, 180, 80, 40))
        window.max_marker.setPen(pg.mkPen("w", width=1.4))
        window.poly_marker.setPen(pg.mkPen("w", width=1.4))
        window.gaussian_marker.setPen(pg.mkPen("w", width=1.4))
        window.centroid_marker.setPen(pg.mkPen("w", width=1.4))
        window.residual_curve.setPen(pg.mkPen((0, 0, 0, 0), width=0))
    apply_metric_color_styles_for(window)
    window._update_freeze_button_icon()
    window._update_residual_button_icon()
    window._update_dark_reference_button_icons()
