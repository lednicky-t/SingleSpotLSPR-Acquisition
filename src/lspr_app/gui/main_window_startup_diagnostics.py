"""Startup-time widget and stack diagnostics for the main window.

These functions are called once during startup (after the first paint event)
to capture a snapshot of the Qt widget hierarchy and layout stack for
debugging visibility / stacking issues.

Public API
----------
``log_startup_widget_snapshot_for(window)``
    Log a one-shot snapshot of the top-level widget hierarchy and content-
    stack state.  A no-op unless ``debug_timing_enabled`` or
    ``deep_timing_enabled`` is set on ``window._diagnostics``.

Private helpers (module-internal)
----------------------------------
``_top_content_stack_diagnostics_for``
    Return a structured dict describing the current state of the content
    ``QStackedWidget``.
``_log_startup_widget_trace_for``
    Emit a detailed per-widget trace log entry for every top-level widget.
"""

from __future__ import annotations

import math
from time import perf_counter
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget


def _top_content_stack_diagnostics_for(window) -> dict[str, object]:
    """Return a structured dict describing the top-content QStackedWidget state."""
    stack = getattr(window, "_top_content_stack", None)
    experiment_control_widget = getattr(window, "_experiment_control_window", None)
    placeholder = getattr(window, "_experiment_control_panel_placeholder", None)
    spectra_block = getattr(window, "_spectra_block", None)
    pages: list[dict[str, object]] = []
    if stack is not None:
        try:
            current_index = int(stack.currentIndex())
            count = int(stack.count())
        except Exception:
            current_index = -1
            count = 0
        for index in range(count):
            page = stack.widget(index)
            if page is None:
                continue
            if page is spectra_block:
                label = "spectra"
            elif page is experiment_control_widget:
                label = "experimental_control"
            elif page is placeholder:
                label = "placeholder"
            else:
                label = "unknown"
            pages.append(
                {
                    "index": index,
                    "label": label,
                    "class": page.__class__.__name__,
                    "visible": bool(page.isVisible()),
                    "is_current": index == current_index,
                    "parent": page.parent().__class__.__name__ if page.parent() is not None else None,
                }
            )
    else:
        current_index = -1
        count = 0
    experiment_control_index = -1
    placeholder_index = -1
    spectra_index = -1
    if stack is not None:
        try:
            if experiment_control_widget is not None:
                experiment_control_index = int(stack.indexOf(experiment_control_widget))
            if placeholder is not None:
                placeholder_index = int(stack.indexOf(placeholder))
            if spectra_block is not None:
                spectra_index = int(stack.indexOf(spectra_block))
        except Exception:
            pass
    return {
        "top_content_mode": getattr(window, "_top_view_mode", "-"),
        "top_content_stack_current_index": current_index,
        "top_content_stack_current_widget": (
            f"{stack.currentWidget().__class__.__name__}(objectName={stack.currentWidget().objectName()!r})"
            if stack is not None and stack.currentWidget() is not None
            else None
        ),
        "top_content_stack_page_count": count,
        "top_content_stack_pages": pages,
        "experiment_control_window_exists": experiment_control_widget is not None,
        "experiment_control_window_index": experiment_control_index,
        "placeholder_index": placeholder_index,
        "spectra_block_index": spectra_index,
    }


def _log_startup_widget_trace_for(window, widgets: list[QWidget]) -> None:
    """Emit a detailed trace log entry for each top-level widget."""
    if not widgets:
        window._log_info("Startup widget trace | count=0")
        return

    def _format_flags(widget: QWidget) -> str:
        try:
            flags = widget.windowFlags()
            raw_value = getattr(flags, "value", flags)
            return f"0x{int(raw_value):x}"
        except Exception:
            return "-"

    def _format_geometry(widget: QWidget) -> str:
        try:
            geo = widget.geometry()
            frame = widget.frameGeometry()
            return (
                f"geo=({geo.x()},{geo.y()},{geo.width()},{geo.height()}) "
                f"frame=({frame.x()},{frame.y()},{frame.width()},{frame.height()})"
            )
        except Exception:
            return "geo=- frame=-"

    def _format_parent_chain(widget: QWidget) -> str:
        chain: list[str] = []
        parent = widget.parentWidget()
        depth = 0
        while isinstance(parent, QWidget) and depth < 6:
            try:
                chain.append(
                    f"{parent.__class__.__name__}(objectName={parent.objectName()!r}, "
                    f"title={parent.windowTitle()!r}, visible={parent.isVisible()}, window={parent.isWindow()})"
                )
            except Exception:
                chain.append(parent.__class__.__name__)
            parent = parent.parentWidget()
            depth += 1
        return " -> ".join(chain) if chain else "-"

    visible_count = 0
    window._log_info(f"Startup widget trace | count={len(widgets)}")
    for index, widget in enumerate(widgets, start=1):
        try:
            is_visible = bool(widget.isVisible())
            visible_count += int(is_visible)
            window._log_info(
                "Startup widget trace | "
                f"#{index} class={widget.__class__.__name__} "
                f"objectName={widget.objectName()!r} "
                f"title={widget.windowTitle()!r} "
                f"visible={is_visible} "
                f"window={widget.isWindow()} "
                f"flags={_format_flags(widget)} "
                f"{_format_geometry(widget)} "
                f"parent_chain={_format_parent_chain(widget)}"
            )
        except Exception as exc:
            window._log_warning(f"Startup widget trace entry {index} failed: {exc}")
    window._log_info(f"Startup widget trace | visible_count={visible_count}")


def render_startup_loading_indicator_for(window) -> None:
    """Paint one animation frame into the startup loading label pixmap."""
    label = getattr(window, "_startup_loading_label", None)
    if label is None:
        return
    size = 22
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))

    painter = QPainter(pixmap)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        center = size / 2.0
        ring_radius = 8.8
        dot_radius = 2.0
        base_color = QColor("#39c7ba")
        trail_alphas = [255, 234, 206, 176, 146, 120, 96, 76, 58, 44, 32, 22]

        painter.setPen(Qt.PenStyle.NoPen)

        frame_index = int(getattr(window, "_startup_loading_frame_index", 0)) % 12
        for index in range(12):
            distance = (index - frame_index) % 12
            alpha = trail_alphas[distance]
            color = QColor(base_color)
            color.setAlpha(alpha)
            angle = (index / 12.0) * 6.283185307179586 - 1.5707963267948966
            x_pos = center + ring_radius * math.cos(angle)
            y_pos = center + ring_radius * math.sin(angle)
            painter.setBrush(color)
            painter.drawEllipse(int(round(x_pos - dot_radius)), int(round(y_pos - dot_radius)), 4, 4)
    finally:
        painter.end()
    label.setPixmap(pixmap)


def log_startup_widget_snapshot_for(window) -> None:
    """Log a snapshot of the widget hierarchy if debug timing is enabled.

    Captures the top-content stack state, experiment-control visibility,
    and (when ``deep_timing_enabled``) a full top-level widget trace.
    Does nothing unless ``window._diagnostics.debug_timing_enabled`` or
    ``deep_timing_enabled`` is truthy.
    """
    diagnostics = getattr(window, "_diagnostics", None)
    debug_enabled = bool(getattr(diagnostics, "debug_timing_enabled", False))
    deep_enabled = bool(getattr(diagnostics, "deep_timing_enabled", False))
    if not debug_enabled and not deep_enabled:
        return
    try:
        app = QApplication.instance()
        top_level_widgets: list[QWidget] = []
        if app is not None:
            top_level_widgets = list(app.topLevelWidgets())
        current_widget = None
        stack_pages: list[str] = []
        stack_index = -1
        if hasattr(window, "_top_content_stack") and window._top_content_stack is not None:
            stack_index = int(window._top_content_stack.currentIndex())
            try:
                for page_index in range(window._top_content_stack.count()):
                    page = window._top_content_stack.widget(page_index)
                    if page is None:
                        continue
                    page_label = "page"
                    if page is getattr(window, "_spectra_block", None):
                        page_label = "spectra_block"
                    elif page is getattr(window, "_sensorgram_block", None):
                        page_label = "sensorgram_block"
                    elif page is getattr(window, "_experiment_control_panel_placeholder", None):
                        page_label = "flow_placeholder"
                    page_current = page_index == stack_index
                    stack_pages.append(
                        f"{page_index}:{page_label}:{page.__class__.__name__}(objectName={page.objectName()!r}, "
                        f"visible={page.isVisible()}, current={page_current})"
                    )
            except Exception:
                stack_pages = []
            stack_widget = window._top_content_stack.currentWidget()
            if stack_widget is not None:
                current_widget = (
                    f"{stack_widget.__class__.__name__}(objectName={stack_widget.objectName()!r}, "
                    f"visible={stack_widget.isVisible()}, window={stack_widget.isWindow()})"
                )
        experiment_control_widget = getattr(window, "_experiment_control_window", None)
        experiment_control_widget_text = (
            f"{experiment_control_widget.__class__.__name__}(visible={experiment_control_widget.isVisible()}, window={experiment_control_widget.isWindow()})"
            if experiment_control_widget is not None
            else "None"
        )
        top_level_summary: list[str] = []
        for widget in top_level_widgets:
            try:
                top_level_summary.append(
                    f"{widget.__class__.__name__}(title={widget.windowTitle()!r}, "
                    f"visible={widget.isVisible()}, window={widget.isWindow()})"
                )
            except Exception:
                top_level_summary.append(widget.__class__.__name__)
        main_content = getattr(window, "_main_content_widget", None)
        window._log_info(
            "Startup widget snapshot | "
            f"main_visible={window.isVisible()} | "
            f"main_content_visible={bool(main_content.isVisible()) if main_content is not None else False} | "
            f"top_view={getattr(window, '_top_view_mode', '-')} | "
            f"stack_index={stack_index} | "
            f"stack_current={current_widget or '-'} | "
            f"experiment_control_widget={experiment_control_widget_text} | "
            f"experiment_control_bootstrap_pending={bool(getattr(window, '_experiment_control_panel_bootstrap_pending', getattr(window, '_flow_panel_bootstrap_pending', False)))} | "
            f"ui_startup_ready={bool(getattr(window, '_ui_startup_ready', False))} | "
            f"stack_pages=[{' ; '.join(stack_pages) if stack_pages else '-'}] | "
            f"top_levels=[{' ; '.join(top_level_summary) if top_level_summary else '-'}]"
        )
        window._log_info(f"Top content diagnostics | {_top_content_stack_diagnostics_for(window)}")
        if deep_enabled:
            _log_startup_widget_trace_for(window, top_level_widgets)
    except Exception as exc:
        window._log_warning(f"Startup widget snapshot failed: {exc}")


def log_startup_plot_state_for(window) -> None:
    """Log the paint-count and visibility state of spectrum and trace plots at startup."""
    try:
        spectrum_paints = int(getattr(window, "_spectrum_plot_paint_count", 0) or 0)
        trace_paints = int(getattr(window, "_trace_plot_paint_count", 0) or 0)
        spectrum_visible = bool(getattr(window, "spectrum_plot", None).isVisible()) if hasattr(window, "spectrum_plot") else False
        trace_visible = bool(getattr(window, "trace_plot", None).isVisible()) if hasattr(window, "trace_plot") else False
        window._log_info(
            "Startup plot state | "
            f"spectrum_paints={spectrum_paints} | "
            f"trace_paints={trace_paints} | "
            f"spectrum_visible={spectrum_visible} | "
            f"trace_visible={trace_visible}"
        )
    except Exception as exc:
        window._log_warning(f"Startup plot state failed: {exc}")


def notify_startup_plot_painted_for(window, prefix: str) -> None:
    """Record the first paint of a named plot and log the elapsed startup time."""
    if prefix not in {"spectrum_plot", "trace_plot"}:
        return
    painted = getattr(window, "_startup_plot_painted", None)
    if not isinstance(painted, set):
        painted = set()
        window._startup_plot_painted = painted
    if prefix in painted:
        return
    painted.add(prefix)
    startup_show_t0 = getattr(window, "_startup_show_requested_t0", None)
    if startup_show_t0 is not None:
        window._log_info(f"Startup +{(perf_counter() - startup_show_t0) * 1000.0:.1f} ms: first plot paint on {prefix}")


def notify_startup_data_rendered_for(window, source: str) -> None:
    """Record the first data render and trigger splash/window reveal when spectrum renders."""
    if source not in {"spectrum", "trace"}:
        return
    rendered = getattr(window, "_startup_data_rendered", None)
    if not isinstance(rendered, set):
        rendered = set()
        window._startup_data_rendered = rendered
    if source in rendered:
        return
    rendered.add(source)
    startup_show_t0 = getattr(window, "_startup_show_requested_t0", None)
    if startup_show_t0 is not None:
        window._log_info(f"Startup +{(perf_counter() - startup_show_t0) * 1000.0:.1f} ms: first data render on {source}")
    if source == "spectrum":
        complete_startup_show_for(window)
        splash = getattr(window, "_startup_splash", None)
        if splash is not None and not getattr(window, "_startup_splash_close_scheduled", False):
            window._startup_splash_close_scheduled = True
            window._log_info("Startup data renders complete; closing splash.")
            QTimer.singleShot(0, window._close_startup_splash)


def complete_startup_show_for(window) -> None:
    """Reveal the main window and close the splash once startup data is ready."""
    if not bool(getattr(window, "_startup_show_requested", False)):
        return
    if getattr(window, "_startup_window_revealed", False):
        return
    startup_show_t0 = getattr(window, "_startup_show_requested_t0", None)
    if startup_show_t0 is not None:
        window._log_info(
            f"Startup +{(perf_counter() - startup_show_t0) * 1000.0:.1f} ms: completing startup show"
        )
    window._startup_window_revealed = True
    if bool(getattr(window, "_always_on_top", False)):
        from PyQt6.QtCore import Qt as _Qt
        flags = window.windowFlags()
        window.setWindowFlags(flags | _Qt.WindowType.WindowStaysOnTopHint)
    if bool(getattr(window, "_start_maximized", False)):
        window._screen_fitted = True
        window.showMaximized()
    else:
        if not getattr(window, "_screen_fitted", False):
            window._fit_window_to_available_screen()
            window._screen_fitted = True
        window.show()
    splash = getattr(window, "_startup_splash", None)
    if splash is not None and not getattr(window, "_startup_splash_close_scheduled", False):
        window._startup_splash_close_scheduled = True
        QTimer.singleShot(200, window._close_startup_splash)


def close_startup_splash_for(window) -> None:
    """Close and remove the startup splash screen."""
    splash = getattr(window, "_startup_splash", None)
    if splash is None:
        return
    window._startup_splash = None
    app = QApplication.instance()
    tracer = getattr(app, "_startup_widget_tracer", None) if app is not None else None
    if app is not None and tracer is not None:
        try:
            app.removeEventFilter(tracer)
        except Exception:
            pass
        try:
            app._startup_widget_tracer = None
        except Exception:
            pass
    try:
        splash.close()
    except Exception:
        pass


def show_event_body_for(window) -> None:
    """Body of ``MainWindow.showEvent`` that runs after ``super().showEvent``."""
    if not getattr(window, "_startup_show_event_reported", False):
        window._startup_show_event_reported = True
        startup_show_t0 = getattr(window, "_startup_show_requested_t0", None)
        if startup_show_t0 is not None:
            window._log_info(
                f"Startup +{(perf_counter() - startup_show_t0) * 1000.0:.1f} ms: first showEvent reached"
            )
        window._log_startup_widget_snapshot()
    window._sync_main_view_visibility()
    if not window._screen_fitted:
        window._screen_fitted = True
        if window._start_maximized:
            window.showMaximized()
        elif not window._ui_state:
            window._fit_window_to_available_screen()
    if not window._hardware_init_scheduled:
        window._hardware_init_scheduled = True
        if window._launch_profile_spec.scan_devices:
            QTimer.singleShot(0, window._start_hardware_initialization)
        elif window._launch_profile_spec.start_live_acquisition:
            QTimer.singleShot(0, window._start_live_acquisition)
    window._sync_view_actions()


def paint_event_body_for(window, started: float) -> None:
    """Body of ``MainWindow.paintEvent`` that runs after ``super().paintEvent``."""
    elapsed_ms = (perf_counter() - started) * 1000.0
    if not getattr(window, "_startup_paint_event_reported", False):
        window._startup_paint_event_reported = True
        startup_show_t0 = getattr(window, "_startup_show_requested_t0", None)
        if startup_show_t0 is not None:
            window._log_info(
                f"Startup +{(perf_counter() - startup_show_t0) * 1000.0:.1f} ms: first paintEvent reached | "
                f"paint={elapsed_ms:.2f} ms"
            )
            window._log_startup_plot_state()
