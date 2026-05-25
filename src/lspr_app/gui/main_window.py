from __future__ import annotations

import logging
import math
import queue
import threading
from html import escape
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
from pyqtgraph import exporters as pg_exporters

from PyQt6.QtCore import QPoint, QSize, Qt, QThreadPool, QTimer, QUrl, QRectF, pyqtSignal
from PyQt6.QtCore import QEvent
from PyQt6.QtGui import (
    QDesktopServices,
    QFont,
    QFontInfo,
    QColor,
    QGuiApplication,
    QIcon,
    QKeySequence,
    QPainter,
    QPixmap,
    QShortcut,
    QTextCursor,
)
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QMenu,
    QPushButton,
    QScrollArea,
    QSizeGrip,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QToolButton,
    QToolBar,
    QTabWidget,
    QSlider,
    QSplitter,
    QSplitterHandle,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from lspr_app import __version__
from lspr_app.device.base import Spectrometer, SpectrometerCapabilities
from lspr_app.device.amf_mswitch import detect_amf_mswitch_devices
from lspr_app.device.reglo_icc import RegloICCClient, is_probable_reglo_port
from lspr_app.device.serial_controllers import SerialController, controller_port_priority
from lspr_app.device.valve_controllers import detect_valve_controller
from lspr_app.device.simulated import SimulationParameters, SimulatedSpectrometer
from lspr_app.domain.models import AcquisitionSettings, ProcessingSettings, Spectrum
from lspr_app.domain.processing import fit_processed_spectrum, set_processing_debug_mode_enabled
from lspr_app.domain.session import MeasurementError, MeasurementSession
from lspr_app.storage.app_config import (
    load_processing_settings,
    load_acquisition_state,
    load_app_setting,
    load_window_ui_state,
    save_acquisition_state,
    save_app_setting,
    save_window_ui_state,
)
from lspr_app.storage.csv_export import export_spectrum_to_csv
from lspr_app.storage.hdf5_export import AsyncHDF5MeasurementWriter
from lspr_app.gui.chrome import build_menu_bar
from lspr_app.gui.logging_utils import GuiLogBridge, GuiLogHandler, SUCCESS_LOG_LEVEL
from lspr_app.gui.main_window_headers import (
    install_source_link_buttons,
    install_source_tab_headers,
    math_function_tab_icon,
    prism_tab_icon,
    update_source_link_buttons,
    update_source_tab_headers,
)
from lspr_app.gui.icon_helpers import (
    dark_icon,
    reference_icon,
    residual_icon,
    flow_tabler_icon,
    snowflake_icon,
    storage_compression_icon,
    transport_icon,
    tint_tabler_icon,
)
from lspr_app.gui.main_window_titlebar import build_title_bar, refresh_hw_device_status_strip, sync_window_control_icons
from lspr_app.gui.main_window_panels import build_processing_group, build_simulation_page, build_spectrometer_page
from lspr_app.gui.main_window_state import (
    acquisition_state_payload,
    apply_acquisition_state_to_widgets,
    collapsible_section_state,
    persist_acquisition_state,
    restore_collapsible_section_state,
    restore_ui_state,
    save_ui_state,
    schedule_acquisition_state_persist,
)
from lspr_app.gui.main_window_processing import (
    apply_processing_settings_to_widgets,
    configure_processing_group_controls,
    populate_analysis_resolution_combo,
    current_processing_settings,
    load_processing_settings_dialog,
    persist_processing_settings,
    primary_trace_metric,
    save_processing_settings_dialog,
    selected_trace_metrics,
    schedule_processing_refresh,
)
from lspr_app.gui.main_window_plotting import (
    analysis_cache_token_for,
    analysis_metrics_cache_token_for,
    apply_temporal_smoothing_for,
    autoscale_residual_axis_for,
    autoscale_spectrum_plot_for,
    autoscale_trace_plot_for,
    build_summary_text_for,
    clear_trace_history_for,
    compute_centroid_nm_for,
    compute_metric_nm_for,
    compute_peak_metric_nm_for,
    compute_trace_metrics_for,
    headroom_value_text_for,
    enqueue_plot_processing_for,
    flush_deferred_ui_refreshes_for,
    flush_plot_refreshes_for,
    get_analysis_metrics_for,
    get_analysis_processed_spectrum_for,
    get_dense_analysis_curve_for,
    get_processed_spectrum_for,
    handle_plot_processing_result_for,
    handle_residual_toggle_for,
    handle_live_setting_change_for,
    handle_simulation_output_rate_change_for,
    handle_spectrum_mouse_moved_for,
    handle_trace_mouse_moved_for,
    live_skip_rate_hz_for,
    needs_gaussian_metric_for,
    processing_cache_token_for,
    refresh_plot_for,
    refresh_telemetry_for,
    refresh_trace_plot_for,
    reference_peak_nm_for_shift_for,
    render_trace_series_for,
    request_trace_autoscale_for,
    set_sensorgram_frozen_for,
    set_plots_frozen_for,
    temporal_history_token_for,
    update_poly_warning_indicator_for,
    update_residual_axis_visibility_for,
    update_residual_view_geometry_for,
    update_spectrum_stats_for,
    update_trace_stats_for,
    update_live_estimate_for,
    update_window_mode_label_for,
)
from lspr_app.gui.main_window_logging import (
    append_log_record,
    append_log_record_now,
    clear_log_terminal,
    copy_log_terminal,
    copy_session_stats_log_for,
    describe_spectrum_for,
    flush_log_buffer,
    log_debug,
    log_error,
    log_event,
    log_info,
    log_success,
    log_throttled,
    log_warning,
    refresh_session_summary_for,
    refresh_session_statistics_for,
    set_log_following,
)
from lspr_app.gui.acquisition_controller import (
    append_processed_trace_history,
    flush_measurement_frames,
    flush_live_acquisition_results,
    flush_live_processed_results,
    handle_acquisition_error,
    handle_acquisition_success,
    request_manual_acquisition,
    set_manual_acquisition_buttons_enabled,
    set_measurement_buttons_enabled,
    set_measurement_ui_locked,
    start_acquisition,
    start_measurement_run,
    start_live_acquisition,
    stop_measurement_run,
    stop_live_acquisition,
    toggle_measurement_run,
    update_measurement_toggle_button,
    update_window_mode_label,
)
from lspr_app.gui.shortcut_help import build_shortcuts_help_text
from lspr_app.gui.plot_controller import (
    autoscale_residual_axis,
    autoscale_spectrum_plot,
    autoscale_trace_plot,
    clip_series_to_window,
    downsample_spectrum_series_for_view,
    flush_deferred_ui_refreshes,
    flush_plot_refreshes,
    handle_spectrum_mouse_moved,
    handle_trace_mouse_moved,
    refresh_plot,
    refresh_trace_plot,
    render_trace_series,
    request_trace_autoscale,
    spectrum_render_cache_key,
    trim_history_tail_in_place,
    update_residual_axis_visibility,
    update_residual_view_geometry,
    update_spectrum_stats,
    update_trace_stats,
)
from lspr_app.gui.processing_helpers import (
    analysis_cache_token,
    analysis_metrics_cache_token,
    compute_centroid_nm,
    compute_metric_nm,
    compute_peak_metric_nm,
    compute_trace_metrics,
    get_analysis_metrics,
    get_dense_analysis_curve,
    get_processed_spectrum,
    needs_gaussian_metric,
    processing_cache_token,
)
from lspr_app.gui.hardware_initializer import (
    HardwareInitResult,
    HardwareInitStep,
    HardwareInitStepResult,
    HardwareInitTask,
)
from lspr_app.gui.workers import (
    AcquisitionResult,
    LiveAcquisitionEvent,
    LiveAcquisitionWorker,
    LiveProcessedEvent,
    LiveProcessingWorker,
    ProcessingRequest,
    ProcessingResult,
    ProcessingTask,
)
from lspr_app.gui.widgets import InlineWheelDoubleLabel
from lspr_app.gui.ui_helpers import (
    create_status_dot_icon,
    make_compact_spinbox,
    make_sim_slider,
    make_window_button,
    window_control_icon,
)
from lspr_app.gui.widgets import (
    CollapsibleSection,
    CompactSplitter,
    FlexibleTimeAxis,
    ScientificAxis,
)
if TYPE_CHECKING:
    from lspr_app.device.reglo_icc import PumpProbe
    from lspr_app.gui.experiment_control_window import ExperimentControlWindow



class LogTerminalTextEdit(QTextEdit):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._min_font_size = 7.0
        self._max_font_size = 16.0

    def wheelEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta_y = event.angleDelta().y()
            if delta_y == 0:
                event.ignore()
                return
            factor = 1.1 if delta_y > 0 else 1 / 1.1
            font_info = QFontInfo(self.font())
            current_size = float(font_info.pointSizeF())
            if current_size <= 0:
                current_size = float(font_info.pointSize()) if font_info.pointSize() > 0 else 9.0
            font = QFont(font_info.family())
            new_size = max(self._min_font_size, min(current_size * factor, self._max_font_size))
            font.setPointSizeF(new_size)
            self.setFont(font)
            self.document().setDefaultFont(font)
            event.accept()
            return
        super().wheelEvent(event)


class MainWindow(QMainWindow):
    hardware_init_progress = pyqtSignal(int, str)
    hardware_init_finished = pyqtSignal()
    PLOT_MODES = {
        "Dark": "dark",
        "Reference": "reference",
        "Sample": "sample",
        "Absorbance": "absorbance",
    }
    TRACE_METRIC_LABELS = {
        "smoothed_max": "Max",
        "centroid": "Centroid",
        "poly_max": "Poly",
        "gaussian_center": "Gauss",
    }
    TRACE_METRIC_COLORS = {
        "smoothed_max": "#0072B2",
        "centroid": "#009E73",
        "poly_max": "#E69F00",
        "gaussian_center": "#D55E00",
    }
    SENSORGRAM_TIME_PLOT_COLORS = {
        "smoothed_max": "#1F77B4",
        "centroid": "#009E73",
        "poly_max": "#E69F00",
        "gaussian_center": "#D55E00",
    }

    def __init__(
        self,
        spectrometer: Spectrometer,
        session: MeasurementSession,
        discovered_pump_probe: "PumpProbe | None" = None,
    ) -> None:
        super().__init__()
        self._startup_t0 = perf_counter()
        self._startup_timing_buffer: list[str] = []

        def _startup_mark(label: str) -> None:
            message = f"Startup +{(perf_counter() - self._startup_t0) * 1000.0:.1f} ms: {label}"
            if hasattr(self, "_ui_logger"):
                self._ui_logger.info(message)
            else:
                self._startup_timing_buffer.append(message)

        _startup_mark("QMainWindow base initialized")
        self._spectrometer = spectrometer
        self._hardware_session = session
        self._simulation_session = MeasurementSession()
        self._simulation_backend = SimulatedSpectrometer()
        self._session = self._hardware_session
        self._source_mode = "spectrometer"
        self._hardware_available = not isinstance(spectrometer, SimulatedSpectrometer)
        self._thread_pool = QThreadPool.globalInstance()
        self._simulation_refresh_timer = QTimer(self)
        self._simulation_refresh_timer.setSingleShot(True)
        self._simulation_refresh_timer.setInterval(60)
        self._simulation_refresh_timer.timeout.connect(self._flush_simulation_parameter_change)
        self._live_result_timer = QTimer(self)
        self._live_result_timer.setInterval(25)
        self._live_result_timer.timeout.connect(self._flush_live_acquisition_results)
        self._live_processed_timer = QTimer(self)
        self._live_processed_timer.setSingleShot(True)
        self._live_processed_timer.timeout.connect(self._flush_live_processed_results)
        self._live_display_timer = QTimer(self)
        self._live_display_timer.setSingleShot(True)
        self._live_display_timer.timeout.connect(self._flush_live_processed_results)
        self._processing_refresh_timer = QTimer(self)
        self._processing_refresh_timer.setSingleShot(True)
        self._processing_refresh_timer.timeout.connect(self._enqueue_plot_processing)
        self._plot_refresh_timer = QTimer(self)
        self._plot_refresh_timer.setSingleShot(True)
        self._plot_refresh_timer.setInterval(33)
        self._plot_refresh_timer.timeout.connect(self._flush_plot_refreshes)
        self._trace_autoscale_timer = QTimer(self)
        self._trace_autoscale_timer.setSingleShot(True)
        self._trace_autoscale_timer.setInterval(120)
        self._trace_autoscale_timer.timeout.connect(self._autoscale_trace_plot)
        self._stats_refresh_timer = QTimer(self)
        self._stats_refresh_timer.setSingleShot(True)
        self._stats_refresh_timer.timeout.connect(self._flush_deferred_ui_refreshes)
        self._busy = False
        self._live_active = False
        self._live_worker: LiveAcquisitionWorker | None = None
        self._live_processing_worker: LiveProcessingWorker | None = None
        self._live_stop_event = threading.Event()
        self._live_result_queue: queue.Queue[LiveAcquisitionEvent] = queue.Queue(maxsize=4)
        self._live_processing_input_queue: queue.Queue[LiveAcquisitionEvent] = queue.Queue(maxsize=16)
        self._live_processed_queue: queue.Queue[LiveProcessedEvent] = queue.Queue(maxsize=4)
        self._live_processing_log_queue: queue.Queue[tuple[int, str, str]] = queue.Queue(maxsize=32)
        self._measurement_active = False
        self._measurement_paused = False
        self._measurement_writer: AsyncHDF5MeasurementWriter | None = None
        self._measurement_path: Path | None = None
        self._measurement_compression_task: MeasurementCompressionTask | None = None
        self._measurement_signal_mode = "absorbance"
        self._measurement_started_at: datetime | None = None
        self._measurement_experiment_name = ""
        self._measurement_flush_interval_s = 5.0
        self._measurement_axis_lock: np.ndarray | None = None
        self._peak_history: dict[str, list[tuple[float, float]]] = {}
        self._peak_reference_processed: Spectrum | None = None
        self._live_trace_started_at: datetime | None = None
        self._pending_manual_kind: str | None = None
        self._pending_source_mode: str | None = None
        self._resume_live_after_source_switch = False
        self._resume_live_after_manual = False
        self._pending_auto_integration = False
        self._resume_live_after_auto_integration = False
        self._display_window_ms = 0.0
        self._raw_last_finish_ts: float | None = None
        self._last_elapsed_ms: float | None = None
        self._last_spacing_ms: float | None = None
        self._last_overhead_ms: float | None = None
        self._effective_raw_rate_hz: float | None = None
        self._last_processing_ms: float | None = None
        self._last_processing_queue_wait_ms: float | None = None
        self._processing_rate_hz: float | None = None
        self._processing_headroom_ratio: float | None = None
        self._last_display_average_count: int | None = None
        self._last_display_period_ms: float | None = None
        self._accumulator_sum: np.ndarray | None = None
        self._accumulator_count = 0
        self._accumulator_started_ts: float | None = None
        self._accumulator_settings_key: tuple[object, ...] | None = None
        self._accumulator_template: Spectrum | None = None
        self._live_display_dropped_frames = 0
        self._live_display_started_at: float | None = None
        self._last_live_processing_perf: float | None = None
        self._live_plot_update_counter = 0
        self._live_plot_refresh_stride = 2
        self._capabilities: SpectrometerCapabilities = self._spectrometer.capabilities()
        self._processing_settings = load_processing_settings()
        self._ui_state = load_window_ui_state("main_window")
        self._experiment_control_window_ui_state = load_window_ui_state("experiment_control_window")
        self._acquisition_state = load_acquisition_state()
        loaded_theme = str(load_app_setting("theme_mode", "dark"))
        self._theme_mode = "dark" if loaded_theme not in {"light", "dark"} else loaded_theme
        if self._theme_mode != "dark":
            self._theme_mode = "dark"
            save_app_setting("theme_mode", self._theme_mode)
        self._suspend_processing_autosave = False
        self._suspend_acquisition_autosave = False
        self._last_processed_plot: Spectrum | None = None
        self._last_fit_plot: Spectrum | None = None
        self._processed_cache_key: tuple[object, ...] | None = None
        self._processed_cache_result: tuple[Spectrum | None, Spectrum | None] = (None, None)
        self._analysis_cache_key: tuple[object, ...] | None = None
        self._analysis_cache_result: tuple[np.ndarray, np.ndarray, dict[str, float | str]] = (
            np.empty(0, dtype=np.float64),
            np.empty(0, dtype=np.float64),
            {},
        )
        self._analysis_metrics_cache_key: tuple[object, ...] | None = None
        self._analysis_metrics_cache_result: dict[str, object] = {}
        self._plot_processing_epoch = 0
        self._active_plot_processing_epoch = 0
        self._plot_processing_running = False
        self._pending_plot_request: ProcessingRequest | None = None
        self._temporal_processed_history: list[Spectrum] = []
        self._temporal_history_key: tuple[object, ...] | None = None
        self._trace_display_window_s = 60.0
        self._sensorgram_downsampling_enabled = True
        self._trace_display_cursor_s = 0.0
        self._trace_history_max_points = 6000
        self._recording_blink_visible = True
        self._processing_debug_mode_enabled = bool(load_app_setting("processing_debug_mode", False))
        set_processing_debug_mode_enabled(self._processing_debug_mode_enabled)
        hdf5_compression_setting = load_app_setting("measurement_hdf5_compression_enabled", True)
        if isinstance(hdf5_compression_setting, str):
            self._hdf5_compression_enabled = hdf5_compression_setting.strip().lower() in {"1", "true", "yes", "on"}
        else:
            self._hdf5_compression_enabled = bool(hdf5_compression_setting)
        self._sensorgram_view_mode = "absolute"
        self._sensorgram_content_mode = "metric"
        self._sensorgram_heatmap_history: list[tuple[float, np.ndarray]] = []
        self._sensorgram_heatmap_history_max_rows = 2000
        self._sensorgram_heatmap_wavelengths: np.ndarray | None = None
        self._last_summary_text: str = ""
        self._last_summary_refresh_ts: float = 0.0
        self._pending_trace_label: str = "Peak position (nm)"
        self._trace_view_locked = False
        self._trace_view_autoscaling = False
        self._ui_summary_dirty = False
        self._ui_telemetry_dirty = False
        self._ui_live_estimate_dirty = False
        self._ui_stats_dirty = False
        self._ui_session_stats_dirty = False
        self._ui_trace_plot_dirty = False
        self._plot_render_dirty = False
        self._visible_processed_plot: Spectrum | None = None
        self._visible_fit_plot: Spectrum | None = None
        self._spectrum_render_cache_key: tuple[object, ...] | None = None
        self._visible_trace_x: np.ndarray | None = None
        self._visible_trace_y: np.ndarray | None = None
        self._visible_trace_mode = "elapsed"
        self._plots_frozen = False
        self._sensorgram_frozen = False
        self._closing = False
        self._screen_fitted = False
        self._source_epoch = 0
        self._processing_refresh_delay_ms = 120
        self._stats_refresh_delay_ms = 180
        self._live_ui_refresh_delay_ms = 220
        self._acquisition_state_timer = QTimer(self)
        self._acquisition_state_timer.setSingleShot(True)
        self._acquisition_state_timer.setInterval(250)
        self._acquisition_state_timer.timeout.connect(self._persist_acquisition_state)
        self._hardware_init_task: HardwareInitTask | None = None
        self._hardware_init_scheduled = False
        self._spectrum_cursor_text = "cursor: -"
        self._trace_cursor_text = "cursor: -"
        self._start_maximized = False
        self._experiment_control_window: ExperimentControlWindow | None = None
        self._main_content_widget: QWidget | None = None
        self._top_view_mode = "spectra"
        self._trace_stats_metric_name: str | None = None
        self._discovered_pump_probe = discovered_pump_probe
        self._mswitch_probe = None
        self._initial_mswitch_devices: list[object] = []
        self._hardware_init_ready_emitted = False
        self._hardware_status_overrides: dict[str, tuple[bool, str]] = {}
        self.session_statistics_text: QTextEdit | None = None
        self.session_settings_text: QTextEdit | None = None
        self.session_summary: QTextEdit | None = None
        self.session_stats_splitter: QSplitter | None = None
        self._last_session_stats_text = ""
        self._last_session_stats_refresh_ts = 0.0
        self._session_stats_log: list[str] = []
        self._session_stats_log_last_text = ""
        self._session_stats_log_last_capture_ts = 0.0
        self._title_bar_widget: QWidget | None = None
        self._title_bar_drag_active = False
        self._title_bar_drag_offset = QPoint(0, 0)
        self._brand_icon_path = Path(__file__).resolve().parent.parent / "resources" / "icons" / "app_icon.svg"
        self._prism_icon_svg = """
<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
  <path stroke="none" d="M0 0h24v24H0z" fill="none"/>
  <path d="M2 13h4.45" stroke="#ffffff"/>
  <path d="M18 5l-4.5 6" stroke="#7b3ff2"/>
  <path d="M22 9l-7.75 3.25" stroke="#00c853"/>
  <path d="M22 15l-7 -1.5" stroke="#ff3b30"/>
  <path
    d="M4.731 19h11.539a1 1 0 0 0 .866 -1.5l-5.769 -10a1 1 0 0 0 -1.732 0l-5.769 10a1 1 0 0 0 .865 1.5"
    fill="#8edcff"
    fill-opacity="0.25"
    stroke="#8edcff"
  />
</svg>
"""

        self.setWindowTitle(f"LSPR Acquisition {__version__}")
        self.setWindowIcon(QIcon(str(self._brand_icon_path)))
        self.resize(1380, 920)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        _startup_mark("window flags and icon set")

        pg.setConfigOptions(antialias=True)
        self._apply_modern_style()
        _startup_mark("application style applied")

        self._initialize_logging_ui()
        if self._startup_timing_buffer:
            for message in self._startup_timing_buffer:
                self._ui_logger.info(message)
            self._startup_timing_buffer.clear()
        _startup_mark("logging UI initialized")
        self._install_shortcuts()
        _startup_mark("shortcuts installed")

        self.status_label = QLabel(f"Connected backend: {self._spectrometer.device_name()}")
        self.status_label.setWordWrap(False)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.status_label.setToolTip("Current source/backend connection and important application status messages.")

        self.project_destination_edit = QLineEdit(str(load_app_setting("recording_project_destination", "")))
        self.project_destination_edit.setObjectName("recordingPathEdit")
        self.project_destination_edit.setPlaceholderText("Project's destination")
        self.project_destination_edit.setToolTip("Root folder where experiment folders and HDF5 files will be saved.")
        self.project_destination_edit.setFrame(False)
        self.project_destination_edit.setFixedHeight(24)
        self.project_destination_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.project_destination_browse_button = self._make_frameless_icon_button(
            tint_tabler_icon(flow_tabler_icon("folder", "folder_open"), QColor("#8fbaff")),
            "Open the project destination folder in the file explorer.",
            size=24,
        )
        self.experiment_name_edit = QLineEdit(str(load_app_setting("recording_experiment_name", "")))
        self.experiment_name_edit.setObjectName("recordingExperimentEdit")
        self.experiment_name_edit.setPlaceholderText("Experiment name")
        self.experiment_name_edit.setToolTip("Experiment folder name and part of the HDF5 file name.")
        self.experiment_name_edit.setFrame(False)
        self.experiment_name_edit.setFixedHeight(24)
        self.measurement_compression_button = self._make_frameless_icon_button(
            storage_compression_icon(self._hdf5_compression_enabled),
            "Enable or disable gzip compression for new HDF5 measurement files.",
            size=24,
        )
        self.measurement_compression_button.setCheckable(True)
        self.measurement_compression_button.setChecked(self._hdf5_compression_enabled)
        self.measurement_compression_button.clicked.connect(self._set_measurement_hdf5_compression_enabled)
        self.project_destination_edit.installEventFilter(self)
        self.project_destination_browse_button.clicked.connect(self._open_recording_project_destination_in_explorer)
        self.project_destination_edit.editingFinished.connect(self._remember_recording_project_destination)
        self.experiment_name_edit.editingFinished.connect(self._remember_recording_experiment_name)

        self.measurement_toggle_button = self._make_icon_button(
            transport_icon(self._theme_mode, "play"),
            "Start measurement",
        )
        self.stop_measurement_button = self._make_icon_button(
            transport_icon(self._theme_mode, "stop"),
            "Stop measurement",
        )
        self.stop_measurement_button.setObjectName("dangerIconButton")
        self.next_measurement_button = self._make_icon_button(
            transport_icon(self._theme_mode, "next"),
            "Next",
        )
        self.measurement_toggle_button.clicked.connect(self._toggle_measurement_run)
        self.stop_measurement_button.clicked.connect(self._stop_measurement_run)

        self.integration_spin = QDoubleSpinBox()
        make_compact_spinbox(self.integration_spin)
        self.integration_spin.setRange(1.0, 10000.0)
        self.integration_spin.setValue(50.0)
        self.integration_spin.setSuffix(" ms")
        self.integration_spin.setDecimals(3)
        self.integration_spin.setSingleStep(1.0)

        self.averages_spin = QSpinBox()
        make_compact_spinbox(self.averages_spin)
        self.averages_spin.setRange(1, 1000)
        self.averages_spin.setValue(3)

        self.auto_integration_button = QPushButton("Auto")
        self.auto_integration_button.setToolTip("Automatically set integration time.")
        self.auto_integration_button.clicked.connect(self._auto_set_integration_time)

        self.correct_dark_check = QCheckBox("Elec. dark correction")
        self.correct_dark_check.setChecked(True)
        self.correct_nonlinearity_check = QCheckBox("Nonlinearity correction")
        self.correct_nonlinearity_check.setChecked(True)

        self.acquire_dark_button = self._make_frameless_icon_button(
            dark_icon(False),
            "Acquire dark spectrum",
            size=30,
        )
        self.acquire_reference_button = self._make_frameless_icon_button(
            reference_icon(False),
            "Acquire reference spectrum",
            size=30,
        )

        self.acquire_dark_button.clicked.connect(lambda: self._request_manual_acquisition("dark"))
        self.acquire_reference_button.clicked.connect(lambda: self._request_manual_acquisition("reference"))

        self.live_rate_spin = QDoubleSpinBox()
        make_compact_spinbox(self.live_rate_spin)
        self.live_rate_spin.setRange(0.1, 200.0)
        self.live_rate_spin.setValue(4.0)
        self.live_rate_spin.setDecimals(2)
        self.live_rate_spin.setSuffix(" Hz")
        self.live_rate_spin.setToolTip(
            "GUI display refresh rate for live spectra and sensorgram updates. "
            "Lower values skip more display frames and reduce GUI work."
        )

        self.sim_output_rate_spin = QDoubleSpinBox()
        make_compact_spinbox(self.sim_output_rate_spin)
        self.sim_output_rate_spin.setRange(0.1, 200.0)
        self.sim_output_rate_spin.setValue(4.0)
        self.sim_output_rate_spin.setDecimals(2)
        self.sim_output_rate_spin.setSuffix(" Hz")
        self.sim_output_rate_spin.setToolTip("Simulation frame production rate.")

        self.live_estimate = QLabel()
        self.live_estimate.setWordWrap(False)
        self.live_estimate.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.live_estimate.setToolTip(
            "src = source acquisition rate; disp = GUI display rate; proc = processing time per spectrum; "
            "head = display-period / processing-time; skip = dropped GUI updates per second."
        )
        self.telemetry_label = QLabel()
        self.telemetry_label.setWordWrap(False)
        self.telemetry_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.telemetry_label.setToolTip(
            "acq = acquisition latency; interval = time since previous source frame; overhead = acquisition latency minus expected budget; "
            "disp = last displayed frame/window summary."
        )
        self.spectrum_stats_label = QLabel("peak: - | centroid: - | FWHM: - | MSE: - | R: - | S/N: -")
        self.spectrum_stats_label.setWordWrap(True)
        self.spectrum_stats_label.setToolTip(
            "Spectrum stats: peak position, centroid, FWHM, fit error (MSE), fit quality (R), and signal-to-noise."
        )
        self.spectrum_cursor_label = QLabel("cursor: -")
        self.spectrum_cursor_label.setToolTip("Spectrum cursor readout under the mouse pointer.")
        self.trace_stats_label = QLabel("latest: - | min/max: - | span: - | dt -")
        self.trace_stats_label.setWordWrap(False)
        self.trace_stats_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.trace_stats_label.setToolTip(
            "Trace stats: click to cycle through selected trace metrics. Shows latest value, min/max, span, and average time step."
        )
        self.trace_stats_label.mousePressEvent = self._handle_trace_stats_label_click  # type: ignore[assignment]
        self.trace_cursor_label = QLabel("cursor: -")
        self.trace_cursor_label.setToolTip("Trace cursor readout under the mouse pointer.")
        self.trace_noise_window_spin = InlineWheelDoubleLabel(10.0)
        self.trace_noise_window_spin.setObjectName("traceNoiseWindowLabel")
        self.trace_noise_window_spin.setRange(0.5, 600.0)
        self.trace_noise_window_spin.setDecimals(1)
        self.trace_noise_window_spin.setSingleStep(0.5)
        self.trace_noise_window_spin.setSuffix(" s")
        self.trace_noise_window_spin.setToolTip(
            "Noise window in seconds for the sensorgram trace. Click to focus, then use the mouse wheel or arrow keys."
        )
        self.trace_noise_summary_label = QLabel("noise: -")
        self.trace_noise_summary_label.setWordWrap(False)
        self.trace_noise_summary_label.setToolTip("Noise estimate for each trace metric over the selected window.")
        self.show_residual_button = self._make_frameless_icon_button(
            residual_icon(False),
            "Show or hide the fit residual on the right axis.",
            size=30,
        )
        self.show_residual_button.setCheckable(True)
        self.show_residual_button.toggled.connect(self._handle_residual_toggle)
        self.freeze_plots_button = self._make_frameless_icon_button(
            snowflake_icon(self._theme_mode, False),
            "Freeze the plots so they stop updating while acquisition continues.",
            size=30,
        )
        self.freeze_plots_button.setCheckable(True)
        self.freeze_plots_button.toggled.connect(self._set_plots_frozen)
        self.sensorgram_freeze_button = self._make_frameless_icon_button(
            snowflake_icon(self._theme_mode, False),
            "Freeze only the sensorgram trace and heatmap so you can inspect it while acquisition continues.",
            size=30,
        )
        self.sensorgram_freeze_button.setCheckable(True)
        self.sensorgram_freeze_button.toggled.connect(self._set_sensorgram_frozen)
        self._update_sensorgram_freeze_button_icon()
        self.clear_trace_button = QToolButton()
        self.clear_trace_button.setObjectName("flowStepActionButton")
        self.clear_trace_button.setAutoRaise(True)
        self.clear_trace_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.clear_trace_button.setFixedSize(32, 32)
        self.clear_trace_button.setIconSize(QSize(24, 24))
        self.clear_trace_button.setIcon(
            tint_tabler_icon(flow_tabler_icon("trash"), QColor("#b44a4a"))
        )
        self.clear_trace_button.setToolTip("Clear the sensorgram trace history.")
        self.clear_trace_button.setStyleSheet(
            "QToolButton#flowStepActionButton { background: transparent; border: none; padding: 0px; margin: 0px; }"
            "QToolButton#flowStepActionButton:hover { background: rgba(127, 127, 127, 0.10); border: none; }"
            "QToolButton#flowStepActionButton:pressed { background: rgba(127, 127, 127, 0.18); border: none; }"
        )
        self.clear_trace_button.clicked.connect(self._clear_trace_history)
        self.trace_record_button = self._make_icon_button(
            transport_icon(self._theme_mode, "record"),
            "Start recording sensorgram data",
        )
        self.trace_record_button.clicked.connect(self._toggle_measurement_run)
        self.autoscale_spectrum_button = QPushButton("Auto spectrum")
        self.autoscale_spectrum_button.setObjectName("toolbarButton")
        self.autoscale_spectrum_button.clicked.connect(self._autoscale_spectrum_plot)
        self.autoscale_trace_button = QPushButton("Auto trace")
        self.autoscale_trace_button.setObjectName("toolbarButton")
        self.autoscale_trace_button.clicked.connect(self._autoscale_trace_plot)

        self.range_min_spin = QSpinBox()
        make_compact_spinbox(self.range_min_spin)
        self.range_min_spin.setRange(0, 5000)
        self.range_min_spin.setToolTip("Minimum wavelength used for processing and fit range.")
        self.range_max_spin = QSpinBox()
        make_compact_spinbox(self.range_max_spin)
        self.range_max_spin.setRange(0, 5000)
        self.range_max_spin.setToolTip("Maximum wavelength used for processing and fit range.")

        self.smoothing_method_combo = QComboBox()
        self.smoothing_method_combo.addItem("None", "none")
        self.smoothing_method_combo.addItem("Moving average", "moving_average")
        self.smoothing_method_combo.addItem("Savitzky-Golay", "savitzky_golay")
        self.smoothing_method_combo.setToolTip("Method used to smooth the spectrum before analysis.")
        self.baseline_method_combo = QComboBox()
        self.baseline_method_combo.addItems(["none", "linear"])
        self.baseline_method_combo.setToolTip("Method used to estimate and subtract the baseline.")
        self.smoothing_window_spin = QSpinBox()
        make_compact_spinbox(self.smoothing_window_spin)
        self.smoothing_window_spin.setRange(1, 2147483647)
        self.smoothing_window_spin.setSingleStep(2)
        self.smoothing_window_spin.setToolTip("Smoothing window in data points. Larger values smooth more strongly.")
        self.temporal_smoothing_spin = QSpinBox()
        make_compact_spinbox(self.temporal_smoothing_spin)
        self.temporal_smoothing_spin.setRange(1, 64)
        self.temporal_smoothing_spin.setValue(1)
        self.temporal_smoothing_spin.setToolTip(
            "Average the last N displayed processed spectra before fit and peak extraction. 1 keeps no temporal accumulation."
        )
        self.crop_method_combo = QComboBox()
        self.crop_method_combo.addItems(["fixed_width", "threshold"])
        self.crop_method_combo.setToolTip("Choose how the fit region is cropped around the detected peak.")
        self.crop_fraction_spin = QDoubleSpinBox()
        make_compact_spinbox(self.crop_fraction_spin)
        self.crop_fraction_spin.setRange(0.05, 0.95)
        self.crop_fraction_spin.setDecimals(2)
        self.crop_fraction_spin.setSingleStep(0.05)
        self.crop_fraction_spin.setValue(0.70)
        self.crop_fraction_spin.setPrefix("frac ")
        self.crop_fraction_spin.setToolTip(
            "Crop the fit region using either a fixed width around the local maximum or a threshold fraction of peak height."
        )

        self.fit_method_combo = QComboBox()
        self.fit_method_combo.addItems(["none", "poly", "gaussian"])
        self.fit_method_combo.setToolTip("Choose the fit model used to estimate the peak position.")
        self.poly_order_spin = QSpinBox()
        make_compact_spinbox(self.poly_order_spin)
        self.poly_order_spin.setRange(1, 999)
        self.poly_order_spin.setToolTip("Polynomial order used when polynomial fit is selected.")
        self.poly_warning_label = QLabel("ÃƒÆ’Ã†â€™Ãƒâ€ Ã¢â‚¬â„¢ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã†â€™Ãƒâ€šÃ‚Â¢ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡Ãƒâ€šÃ‚Â¬ÃƒÆ’Ã‚Â¢ÃƒÂ¢Ã¢â‚¬Å¡Ã‚Â¬Ãƒâ€šÃ‚ÂÃƒÆ’Ã†â€™ÃƒÂ¢Ã¢â€šÂ¬Ã…Â¡ÃƒÆ’Ã¢â‚¬Å¡Ãƒâ€šÃ‚Â")
        self.poly_warning_label.setStyleSheet("color: #c62828; font-size: 14px;")
        self.poly_warning_label.setToolTip("Polynomial order warning.")
        self.poly_warning_label.hide()
        self.fit_window_spin = QSpinBox()
        make_compact_spinbox(self.fit_window_spin)
        self.fit_window_spin.setRange(0, 5000)
        self.fit_window_spin.setValue(120)
        self.fit_window_spin.setSuffix(" nm")
        self.fit_window_spin.setToolTip("Fit range width in nm around the detected local peak maximum.")
        self.analysis_resolution_spin = QComboBox()
        populate_analysis_resolution_combo(self.analysis_resolution_spin)
        self.analysis_resolution_spin.setCurrentIndex(2)
        self.analysis_resolution_spin.setToolTip(
            "Resolution used for peak and centroid analysis. Lower values improve sub-sample tracking but cost more CPU."
        )
        self.peak_metric_combo = QComboBox()
        self.peak_metric_combo.addItems(["smoothed_max", "poly_max", "gaussian_center", "centroid"])
        self.peak_metric_combo.setToolTip("Choose which peak metric is shown in the trace plot and summary.")
        self.trace_max_check = QCheckBox("Max")
        self.trace_centroid_check = QCheckBox("Centroid")
        self.trace_poly_check = QCheckBox("Poly")
        self.trace_gaussian_check = QCheckBox("Gauss")
        self.trace_max_check.setObjectName("traceMaxCheck")
        self.trace_centroid_check.setObjectName("traceCentroidCheck")
        self.trace_poly_check.setObjectName("tracePolyCheck")
        self.trace_gaussian_check.setObjectName("traceGaussianCheck")
        self.trace_max_check.setToolTip("Show the smoothed maximum trace metric.")
        self.trace_centroid_check.setToolTip("Show the centroid trace metric.")
        self.trace_poly_check.setToolTip("Show the polynomial-fit peak trace metric.")
        self.trace_gaussian_check.setToolTip("Show the Gaussian-fit peak trace metric.")

        self.save_processing_button = QPushButton("Save settings")
        self.load_processing_button = QPushButton("Load settings")
        self.save_processing_button.clicked.connect(self._save_processing_settings_dialog)
        self.load_processing_button.clicked.connect(self._load_processing_settings_dialog)
        self.save_processing_button.setToolTip("Save the current processing configuration.")
        self.load_processing_button.setToolTip("Load a previously saved processing configuration.")

        self.sim_peak_center_slider = make_sim_slider(450, 850, 620)
        self.sim_peak_width_slider = make_sim_slider(10, 120, 35)
        self.sim_peak_height_slider = make_sim_slider(-5000, 5000, 1800)
        self.sim_baseline_slider = make_sim_slider(0, 4000, 900)
        self.sim_slope_slider = make_sim_slider(-100, 100, 12)
        self.sim_noise_slider = make_sim_slider(0, 250, 40)
        self.sim_resolution_spin = QDoubleSpinBox()
        make_compact_spinbox(self.sim_resolution_spin)
        self.sim_resolution_spin.setRange(0.01, 10.0)
        self.sim_resolution_spin.setDecimals(3)
        self.sim_resolution_spin.setSingleStep(0.01)
        self.sim_resolution_spin.setSuffix(" nm")
        self.sim_resolution_spin.setValue(self._default_simulation_resolution_nm())
        self.sim_peak_center_value = QLabel()
        self.sim_peak_width_value = QLabel()
        self.sim_peak_height_value = QLabel()
        self.sim_baseline_value = QLabel()
        self.sim_slope_value = QLabel()
        self.sim_noise_value = QLabel()

        self.plot_selector = QComboBox()
        self.plot_selector.addItems(self.PLOT_MODES.keys())
        self.plot_selector.setCurrentText("Sample")
        self.plot_selector.currentTextChanged.connect(self._refresh_plot)

        self.source_tabs = QTabWidget()
        _startup_mark("building source tabs")
        self.source_tabs.addTab(self._build_spectrometer_page(), "Spectrometer")
        self.source_tabs.addTab(self._build_simulation_page(), "Simulation")
        self.source_tabs.setDocumentMode(True)
        tab_bar = self.source_tabs.tabBar()
        tab_bar.setExpanding(True)
        tab_bar.setUsesScrollButtons(False)
        tab_bar.setElideMode(Qt.TextElideMode.ElideNone)
        self.source_tabs.setIconSize(QSize(16, 16))
        self.source_tabs.currentChanged.connect(self._handle_source_tab_changed)
        install_source_link_buttons(self)
        install_source_tab_headers(self)
        self._configure_source_tabs()
        _startup_mark("source tabs configured")

        self.trace_time_axis = FlexibleTimeAxis("bottom")
        _startup_mark("building spectrum and trace plots")
        self.spectrum_plot = pg.PlotWidget(axisItems={"left": ScientificAxis("left")}, background="w")
        self.spectrum_plot.showGrid(x=True, y=True, alpha=0.18)
        self.spectrum_plot.setLabel("bottom", "Wavelength (nm)")
        self.spectrum_plot.setMouseEnabled(x=True, y=True)
        self.spectrum_curve = self.spectrum_plot.plot(pen=pg.mkPen("#1f77b4", width=2))
        self.fit_curve = self.spectrum_plot.plot(pen=pg.mkPen("#d95f02", width=2, style=Qt.PenStyle.DashLine))
        spectrum_plot_item = self.spectrum_plot.getPlotItem()
        spectrum_plot_item.showAxis("right")
        self.residual_axis = spectrum_plot_item.getAxis("right")
        self.residual_axis.setLabel("Residual")
        self.residual_axis.enableAutoSIPrefix(False)
        self.residual_axis.setTextPen(pg.mkPen("#7a7a7a"))
        self.residual_axis.setPen(pg.mkPen("#7a7a7a"))
        self.residual_view = pg.ViewBox()
        spectrum_plot_item.scene().addItem(self.residual_view)
        self.residual_curve = pg.PlotDataItem(pen=pg.mkPen("#888888", width=1))
        self.residual_view.addItem(self.residual_curve)
        spectrum_plot_item.getAxis("right").linkToView(self.residual_view)
        self.residual_view.setXLink(spectrum_plot_item.vb)
        self.residual_view.setMouseEnabled(x=False, y=False)
        spectrum_plot_item.vb.sigResized.connect(self._update_residual_view_geometry)
        self._update_residual_view_geometry()
        self._update_residual_axis_visibility(False)
        for curve in (self.spectrum_curve, self.fit_curve):
            curve.setClipToView(True)
            curve.setDownsampling(auto=True, method="peak")
        self.residual_curve.setClipToView(True)
        self.residual_curve.setDownsampling(auto=False, ds=1)
        self.processing_region_item = pg.LinearRegionItem(values=(0, 1), movable=False, brush=pg.mkBrush(90, 160, 255, 30), pen=pg.mkPen(None))
        self.fit_region_item = pg.LinearRegionItem(values=(0, 1), movable=False, brush=pg.mkBrush(255, 180, 80, 40), pen=pg.mkPen(None))
        self.spectrum_plot.addItem(self.processing_region_item)
        self.spectrum_plot.addItem(self.fit_region_item)
        self.max_marker = pg.ScatterPlotItem(
            size=12,
            symbol="o",
            brush=pg.mkBrush(self.TRACE_METRIC_COLORS["smoothed_max"]),
            pen=pg.mkPen("w", width=1.4),
        )
        self.poly_marker = pg.ScatterPlotItem(
            size=13,
            symbol="star",
            brush=pg.mkBrush(self.TRACE_METRIC_COLORS["poly_max"]),
            pen=pg.mkPen("w", width=1.4),
        )
        self.gaussian_marker = pg.ScatterPlotItem(
            size=12,
            symbol="d",
            brush=pg.mkBrush(self.TRACE_METRIC_COLORS["gaussian_center"]),
            pen=pg.mkPen("w", width=1.4),
        )
        self.centroid_marker = pg.ScatterPlotItem(
            size=14,
            symbol="t",
            brush=pg.mkBrush(self.TRACE_METRIC_COLORS["centroid"]),
            pen=pg.mkPen("w", width=1.4),
        )
        self.spectrum_plot.addItem(self.max_marker)
        self.spectrum_plot.addItem(self.poly_marker)
        self.spectrum_plot.addItem(self.gaussian_marker)
        self.spectrum_plot.addItem(self.centroid_marker)
        self.spectrum_vline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#666666", width=1))
        self.spectrum_hline = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#666666", width=1))
        self.spectrum_plot.addItem(self.spectrum_vline, ignoreBounds=True)
        self.spectrum_plot.addItem(self.spectrum_hline, ignoreBounds=True)
        self.spectrum_proxy = pg.SignalProxy(
            self.spectrum_plot.scene().sigMouseMoved,
            rateLimit=180,
            slot=self._handle_spectrum_mouse_moved,
        )

        self.trace_plot = pg.PlotWidget(
            axisItems={"bottom": self.trace_time_axis, "left": ScientificAxis("left")},
            background="w",
        )
        self.trace_plot.showGrid(x=True, y=True, alpha=0.18)
        self.trace_plot.setMouseEnabled(x=True, y=True)
        self.trace_curves = {
            metric_name: self.trace_plot.plot(
                pen=pg.mkPen(self.TRACE_METRIC_COLORS[metric_name], width=2.2),
                name=self.TRACE_METRIC_LABELS[metric_name],
            )
            for metric_name in self.TRACE_METRIC_LABELS
        }
        for curve in self.trace_curves.values():
            curve.setClipToView(True)
            curve.setDownsampling(auto=True, method="peak")
        self.trace_heatmap_image = pg.ImageItem(axisOrder="row-major")
        self.trace_heatmap_image.setVisible(False)
        self.trace_heatmap_image.setZValue(-10)
        self.trace_heatmap_image.setAutoDownsample(True)
        self.trace_heatmap_image.setLookupTable(self._sensorgram_heatmap_lookup_table())
        self.trace_plot.addItem(self.trace_heatmap_image, ignoreBounds=True)
        self.trace_legend = self.trace_plot.addLegend(offset=(10, 10))
        self.trace_vline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen("#666666", width=1))
        self.trace_hline = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen("#666666", width=1))
        self.trace_plot.addItem(self.trace_vline, ignoreBounds=True)
        self.trace_plot.addItem(self.trace_hline, ignoreBounds=True)
        self.trace_proxy = pg.SignalProxy(
            self.trace_plot.scene().sigMouseMoved,
            rateLimit=180,
            slot=self._handle_trace_mouse_moved,
        )
        self.trace_plot.getPlotItem().vb.sigRangeChanged.connect(self._handle_trace_view_range_changed)
        self._style_plot_widgets()
        self._apply_sensorgram_display_style()
        _startup_mark("plot widgets styled")

        session_font = QFont("Consolas", 9)

        self.session_statistics_text = QTextEdit()
        self.session_statistics_text.setObjectName("sessionStatisticsText")
        self.session_statistics_text.setReadOnly(True)
        self.session_statistics_text.setAcceptRichText(False)
        self.session_statistics_text.setUndoRedoEnabled(False)
        self.session_statistics_text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.session_statistics_text.setFont(session_font)
        self.session_statistics_text.document().setDefaultFont(session_font)
        self.session_statistics_text.setToolTip("Live timing and performance statistics. Scroll without the view jumping back to the top.")

        self.session_settings_text = QTextEdit()
        self.session_settings_text.setObjectName("sessionSettingsText")
        self.session_settings_text.setReadOnly(True)
        self.session_settings_text.setAcceptRichText(False)
        self.session_settings_text.setUndoRedoEnabled(False)
        self.session_settings_text.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.session_settings_text.setFont(session_font)
        self.session_settings_text.document().setDefaultFont(session_font)
        self.session_settings_text.setToolTip("Current acquisition and processing settings for the active session.")

        self.session_summary = self.session_settings_text

        self.session_stats_copy_button = QPushButton("Copy stats log")
        self.session_stats_copy_button.setToolTip("Copy the recording-period statistics log to the clipboard.")
        self.session_stats_copy_button.clicked.connect(self._copy_session_stats_log)

        self._apply_control_sizing()
        _startup_mark("control sizing applied")
        self._build_layout()
        _startup_mark("main layout built")
        self._build_menu_bar()
        _startup_mark("menu bar built")
        self._sync_view_actions()
        _startup_mark("experimental control panel deferred")
        self.log_clear_button.clicked.connect(self._clear_log_terminal)
        self.log_copy_button.clicked.connect(self._copy_log_terminal)
        self.log_follow_button.toggled.connect(self._set_log_following)
        self._restore_ui_state()
        _startup_mark("UI state restored")
        self._fit_window_to_available_screen()
        self._apply_processing_settings_to_widgets(self._processing_settings)
        self._apply_acquisition_state_to_widgets(self._acquisition_state)
        _startup_mark("saved settings applied")
        self._connect_processing_widgets()
        self._connect_simulation_widgets()
        _startup_mark("widget signals connected")
        self._update_simulation_labels()
        self._sync_simulation_backend_from_controls()
        self.plot_selector.currentTextChanged.connect(lambda _text: self._schedule_acquisition_state_persist())
        self.live_rate_spin.valueChanged.connect(self._handle_live_setting_change)
        self.sim_output_rate_spin.valueChanged.connect(self._handle_simulation_output_rate_change)
        self.integration_spin.valueChanged.connect(self._handle_live_setting_change)
        self.averages_spin.valueChanged.connect(self._handle_live_setting_change)
        self.correct_dark_check.toggled.connect(self._handle_live_setting_change)
        self.correct_nonlinearity_check.toggled.connect(self._handle_live_setting_change)
        self.show_residual_button.toggled.connect(lambda _checked: self._schedule_acquisition_state_persist())
        self.freeze_plots_button.toggled.connect(lambda _checked: self._schedule_acquisition_state_persist())
        self.trace_noise_window_spin.valueChanged.connect(self._handle_processing_setting_change)
        self._update_live_estimate()
        self._refresh_telemetry()
        self._refresh_session_statistics(force=True)
        self._refresh_session_summary(force=True)
        self._log_info(
            f"Ready | source={self._source_mode} | backend={self._spectrometer.device_name()}"
        )
        if self._discovered_pump_probe is not None and hasattr(self._discovered_pump_probe, "port"):
            self._log_success(f"Pump discovered on {getattr(self._discovered_pump_probe.port, 'device', 'unknown')}")
        else:
            self._log_debug("Pump not discovered at startup.")
        if isinstance(self._spectrometer, SimulatedSpectrometer):
            self.source_tabs.blockSignals(True)
            self.source_tabs.setCurrentIndex(1)
            self.source_tabs.blockSignals(False)
            self._apply_source_mode("simulation", restart_live=False)
        else:
            self._refresh_plot()
        self._set_measurement_buttons_enabled(True)
        self.hardware_init_progress.connect(self._update_startup_loading_indicator)
        self.hardware_init_finished.connect(lambda: self._set_startup_loading_indicator(False))
        self.hardware_init_finished.connect(self._start_live_acquisition)
        _startup_mark("startup wiring complete")

    def _initialize_logging_ui(self) -> None:
        if hasattr(self, "log_terminal"):
            return
        self.log_terminal = LogTerminalTextEdit()
        self.log_terminal.setObjectName("logTerminal")
        self.log_terminal.setReadOnly(True)
        self.log_terminal.setAcceptRichText(True)
        self.log_terminal.setUndoRedoEnabled(False)
        self.log_terminal.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.log_terminal.setMaximumHeight(190)
        self.log_terminal.setMinimumHeight(150)
        log_font = QFont("Consolas", 9)
        self.log_terminal.setFont(log_font)
        self.log_terminal.document().setDefaultFont(log_font)
        self.log_terminal.document().setMaximumBlockCount(220)
        self.log_terminal.setToolTip("Live event log for acquisition, processing, and controller activity.")

        self.log_clear_button = QPushButton("Clear")
        self.log_clear_button.setToolTip("Clear the visible log terminal.")
        self.log_clear_button.setFixedHeight(24)
        self.log_follow_button = QPushButton("Follow")
        self.log_follow_button.setCheckable(True)
        self.log_follow_button.setChecked(True)
        self.log_follow_button.setToolTip("Keep the log scrolled to the newest entry.")
        self.log_follow_button.setFixedHeight(24)
        self.log_copy_button = QPushButton("Copy")
        self.log_copy_button.setToolTip("Copy the visible log text to the clipboard.")
        self.log_copy_button.setFixedHeight(24)
        self._log_follow_enabled = True
        self._log_bridge = GuiLogBridge()
        self._log_bridge.record_received.connect(self._append_log_record)
        self._log_handler = GuiLogHandler(self._log_bridge)
        self._log_handler.setFormatter(logging.Formatter("%(message)s"))
        self._ui_logger = logging.getLogger("lspr_app")
        self._ui_logger.setLevel(logging.INFO)
        self._ui_logger.addHandler(self._log_handler)
        self._ui_logger.propagate = False
        self._log_buffer: list[tuple[int, str, str]] = []
        self._log_buffer_timer = QTimer(self)
        self._log_buffer_timer.setInterval(75)
        self._log_buffer_timer.timeout.connect(self._flush_log_buffer)
        self._log_throttle_state: dict[str, tuple[float, str]] = {}
        self._log_emit_levels = {
            logging.INFO,
            SUCCESS_LOG_LEVEL,
            logging.WARNING,
            logging.ERROR,
            logging.CRITICAL,
        }

    def _build_spectrometer_page(self) -> QWidget:
        return build_spectrometer_page(self)

    def _build_simulation_page(self) -> QWidget:
        return build_simulation_page(self)

    def _build_layout(self) -> None:
        measurement_bar = QHBoxLayout()
        measurement_bar.setSpacing(4)
        measurement_bar.addStretch(1)

        processing_group = build_processing_group(self)

        source_block = QWidget()
        source_layout = QVBoxLayout()
        source_layout.setContentsMargins(0, 0, 0, 0)
        source_layout.setSpacing(6)
        source_layout.addWidget(self.source_tabs)
        source_block.setLayout(source_layout)

        plot_bar = QHBoxLayout()
        plot_bar.setSpacing(6)
        plot_bar.addWidget(QLabel("Plot"))
        plot_bar.addWidget(self.plot_selector)
        plot_bar.addWidget(self.acquire_dark_button)
        plot_bar.addWidget(self.acquire_reference_button)
        plot_bar.addWidget(self.show_residual_button)
        plot_bar.addWidget(self.freeze_plots_button)
        plot_bar.addStretch(1)
        plot_bar.addWidget(QLabel("Refresh Rate"))
        plot_bar.addWidget(self.live_rate_spin)

        spectrum_stats_bar = QHBoxLayout()
        spectrum_stats_bar.setSpacing(8)
        spectrum_stats_bar.addWidget(self.spectrum_stats_label, 1)
        spectrum_stats_bar.addWidget(self.spectrum_cursor_label)

        trace_title = QLabel("Sensorgram")
        trace_title.setObjectName("sensorgramHeaderLabel")
        self.sensorgram_view_mode_button = QToolButton()
        self.sensorgram_view_mode_button.setObjectName("sensorgramViewModeButton")
        self.sensorgram_view_mode_button.setAutoRaise(True)
        self.sensorgram_view_mode_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.sensorgram_view_mode_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sensorgram_view_mode_button.clicked.connect(self._cycle_sensorgram_view_mode)
        self._update_sensorgram_view_mode_button()
        self.sensorgram_downsampling_button = QToolButton()
        self.sensorgram_downsampling_button.setObjectName("sensorgramDownsamplingButton")
        self.sensorgram_downsampling_button.setAutoRaise(True)
        self.sensorgram_downsampling_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.sensorgram_downsampling_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sensorgram_downsampling_button.clicked.connect(self._cycle_sensorgram_downsampling_enabled)
        self._update_sensorgram_downsampling_button()
        self.sensorgram_content_mode_button = QToolButton()
        self.sensorgram_content_mode_button.setObjectName("sensorgramContentModeButton")
        self.sensorgram_content_mode_button.setAutoRaise(True)
        self.sensorgram_content_mode_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.sensorgram_content_mode_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sensorgram_content_mode_button.clicked.connect(self._cycle_sensorgram_content_mode)
        self._update_sensorgram_content_mode_button()
        self.sensorgram_window_button = QToolButton()
        self.sensorgram_window_button.setObjectName("sensorgramWindowButton")
        self.sensorgram_window_button.setAutoRaise(True)
        self.sensorgram_window_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.sensorgram_window_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sensorgram_window_button.clicked.connect(self._cycle_sensorgram_display_window)
        self._update_sensorgram_display_window_button()
        self._update_sensorgram_header_control_visibility()

        trace_title_row = QHBoxLayout()
        trace_title_row.setContentsMargins(0, 0, 0, 0)
        trace_title_row.setSpacing(6)
        trace_title_row.addWidget(trace_title)
        trace_title_row.addWidget(self.sensorgram_view_mode_button)
        trace_title_row.addWidget(self.sensorgram_downsampling_button)
        trace_title_row.addWidget(self.sensorgram_content_mode_button)
        trace_title_row.addWidget(self.sensorgram_window_button)
        trace_title_row.addStretch(1)
        trace_title_row_widget = QWidget()
        trace_title_row_widget.setLayout(trace_title_row)
        trace_title_row_widget.setContentsMargins(0, 0, 0, 0)

        trace_left_field = QHBoxLayout()
        trace_left_field.setContentsMargins(0, 0, 0, 0)
        trace_left_field.setSpacing(6)
        trace_left_field.addWidget(self.trace_record_button, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        trace_left_field.addWidget(self.sensorgram_freeze_button, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        trace_left_field.addWidget(self.clear_trace_button, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        trace_left_field.addStretch(1)

        trace_right_field = QVBoxLayout()
        trace_right_field.setContentsMargins(0, 0, 0, 0)
        trace_right_field.setSpacing(0)

        trace_metrics_row = QHBoxLayout()
        trace_metrics_row.setContentsMargins(0, 0, 0, 0)
        trace_metrics_row.setSpacing(6)
        trace_metrics_row.addWidget(self.trace_stats_label, 1)
        trace_metrics_widget = QWidget()
        trace_metrics_widget.setLayout(trace_metrics_row)
        trace_metrics_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        trace_noise_row = QHBoxLayout()
        trace_noise_row.setContentsMargins(0, 0, 0, 0)
        trace_noise_row.setSpacing(6)
        trace_noise_row.addWidget(QLabel("Noise"))
        trace_noise_row.addWidget(self.trace_noise_window_spin)
        trace_noise_row.addWidget(self.trace_noise_summary_label, 1)
        trace_noise_widget = QWidget()
        trace_noise_widget.setLayout(trace_noise_row)
        trace_noise_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        trace_noise_cursor_row = QHBoxLayout()
        trace_noise_cursor_row.setContentsMargins(0, 0, 0, 0)
        trace_noise_cursor_row.setSpacing(6)
        trace_noise_cursor_row.addWidget(trace_noise_widget, 0, Qt.AlignmentFlag.AlignLeft)
        trace_noise_cursor_row.addStretch(1)
        trace_noise_cursor_row.addWidget(self.trace_cursor_label, 0, Qt.AlignmentFlag.AlignRight)

        trace_noise_cursor_widget = QWidget()
        trace_noise_cursor_widget.setLayout(trace_noise_cursor_row)
        trace_noise_cursor_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        trace_right_field.addWidget(trace_metrics_widget)
        trace_right_field.addWidget(trace_noise_cursor_widget)
        trace_metrics_widget.setFixedHeight(trace_metrics_widget.sizeHint().height())
        trace_noise_widget.setFixedHeight(trace_noise_widget.sizeHint().height())
        trace_noise_cursor_widget.setFixedHeight(max(trace_noise_widget.sizeHint().height(), self.trace_cursor_label.sizeHint().height()))

        trace_left_widget = QWidget()
        trace_left_widget.setLayout(trace_left_field)
        trace_left_widget.setMinimumWidth(160)
        trace_left_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        trace_right_widget = QWidget()
        trace_right_widget.setLayout(trace_right_field)
        trace_right_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        trace_body_splitter = CompactSplitter(Qt.Orientation.Horizontal)
        trace_body_splitter.setObjectName("sensorgramHeaderSplitter")
        trace_body_splitter.setChildrenCollapsible(False)
        trace_body_splitter.setOpaqueResize(True)
        trace_body_splitter.setHandleWidth(12)
        trace_body_splitter.addWidget(trace_left_widget)
        trace_body_splitter.addWidget(trace_right_widget)
        trace_body_splitter.setStretchFactor(0, 0)
        trace_body_splitter.setStretchFactor(1, 1)
        trace_body_splitter.setSizes([170, 610])
        self.sensorgram_header_splitter = trace_body_splitter

        footer_bar = QHBoxLayout()
        footer_bar.setSpacing(10)
        footer_live_label = QLabel("Live")
        footer_live_label.setToolTip(
            "Live acquisition status and throughput information for the active source."
        )
        footer_bar.addWidget(footer_live_label)
        footer_bar.addWidget(self.live_estimate, 1)
        footer_stats_label = QLabel("Stats")
        footer_stats_label.setToolTip(
            "Latest processing and telemetry summary for the currently acquired spectrum."
        )
        footer_bar.addWidget(footer_stats_label)
        footer_bar.addWidget(self.telemetry_label, 2)
        self._window_size_grip = QSizeGrip(self)
        self._window_size_grip.setToolTip("Drag to resize the window.")
        footer_bar.addWidget(self._window_size_grip, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom)

        source_section = CollapsibleSection("Light source", source_block, expanded=True)
        processing_section = CollapsibleSection("Processing", processing_group, expanded=False)
        session_stats_header = QHBoxLayout()
        session_stats_header.setContentsMargins(0, 0, 0, 0)
        session_stats_header.setSpacing(6)
        session_stats_title = QLabel("Statistics")
        session_stats_title.setStyleSheet("font-size: 13px; font-weight: 800; letter-spacing: 0.8px; color: #5b6775;")
        session_stats_header.addWidget(session_stats_title)
        session_stats_header.addStretch(1)
        session_stats_header.addWidget(self.session_stats_copy_button)
        session_stats_header_widget = QWidget()
        session_stats_header_widget.setLayout(session_stats_header)

        session_stats_block = QWidget()
        session_stats_layout = QVBoxLayout()
        session_stats_layout.setContentsMargins(0, 0, 0, 0)
        session_stats_layout.setSpacing(4)
        session_stats_layout.addWidget(session_stats_header_widget)
        session_stats_layout.addWidget(self.session_statistics_text)
        session_stats_block.setLayout(session_stats_layout)

        session_settings_header = QLabel("Settings")
        session_settings_header.setStyleSheet("font-size: 13px; font-weight: 800; letter-spacing: 0.8px; color: #5b6775;")
        session_settings_header.setToolTip("Current acquisition and processing parameters.")

        session_settings_block = QWidget()
        session_settings_layout = QVBoxLayout()
        session_settings_layout.setContentsMargins(0, 0, 0, 0)
        session_settings_layout.setSpacing(4)
        session_settings_layout.addWidget(session_settings_header)
        session_settings_layout.addWidget(self.session_settings_text)
        session_settings_block.setLayout(session_settings_layout)

        session_splitter = CompactSplitter(Qt.Orientation.Vertical)
        session_splitter.setObjectName("sessionStatsSplitter")
        session_splitter.setChildrenCollapsible(False)
        session_splitter.setOpaqueResize(True)
        session_splitter.setHandleWidth(10)
        session_splitter.addWidget(session_stats_block)
        session_splitter.addWidget(session_settings_block)
        session_splitter.setStretchFactor(0, 1)
        session_splitter.setStretchFactor(1, 1)
        session_splitter.setSizes([220, 220])
        self.session_stats_splitter = session_splitter

        session_block = QWidget()
        session_layout = QVBoxLayout()
        session_layout.setContentsMargins(0, 0, 0, 0)
        session_layout.setSpacing(4)
        session_layout.addWidget(session_splitter)
        session_block.setLayout(session_layout)
        session_section = CollapsibleSection("Session", session_block, expanded=False)

        log_header_row = QHBoxLayout()
        log_header_row.setContentsMargins(0, 0, 0, 0)
        log_header_row.setSpacing(6)
        log_header_row.addWidget(QLabel("Terminal"))
        log_header_row.addStretch(1)
        log_header_row.addWidget(self.log_follow_button)
        log_header_row.addWidget(self.log_copy_button)
        log_header_row.addWidget(self.log_clear_button)
        log_block = QWidget()
        log_layout = QVBoxLayout()
        log_layout.setContentsMargins(0, 0, 0, 0)
        log_layout.setSpacing(4)
        log_layout.addLayout(log_header_row)
        log_layout.addWidget(self.log_terminal)
        log_block.setLayout(log_layout)
        log_section = CollapsibleSection("Log", log_block, expanded=True)

        self._source_section = source_section
        self._processing_section = processing_section
        self._session_section = session_section
        self._log_section = log_section
        self._restore_collapsible_section_state()

        left_panel = QVBoxLayout()
        left_panel.setSpacing(6)
        left_panel.addLayout(measurement_bar)
        left_panel.addWidget(source_section)
        left_panel.addWidget(processing_section)
        left_panel.addWidget(session_section)
        left_panel.addWidget(log_section)
        left_panel.addStretch(1)

        left_widget = QWidget()
        left_widget.setLayout(left_panel)
        left_widget.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)

        left_scroll = QScrollArea()
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left_scroll.setWidget(left_widget)
        left_scroll.setMinimumWidth(250)
        left_scroll.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._left_controls_scroll = left_scroll

        spectrum_block = QWidget()
        spectrum_layout = QVBoxLayout()
        spectrum_layout.setContentsMargins(0, 0, 0, 0)
        spectrum_layout.setSpacing(4)
        spectrum_header = QLabel("Spectrum")
        spectrum_header.setStyleSheet("font-size: 13px; font-weight: 800; letter-spacing: 0.8px; color: #5b6775;")
        spectrum_layout.addWidget(spectrum_header)
        spectrum_layout.addLayout(plot_bar)
        spectrum_layout.addLayout(spectrum_stats_bar)
        spectrum_layout.addWidget(self.spectrum_plot, 1)
        spectrum_block.setLayout(spectrum_layout)
        self._spectra_block = spectrum_block

        trace_block = QWidget()
        trace_layout = QVBoxLayout()
        trace_layout.setContentsMargins(0, 0, 0, 0)
        trace_layout.setSpacing(4)
        trace_layout.addWidget(trace_title_row_widget)
        trace_layout.addWidget(trace_body_splitter)
        trace_layout.addWidget(self.trace_plot, 1)
        trace_block.setLayout(trace_layout)
        self._sensorgram_block = trace_block

        self._top_content_stack = QStackedWidget()
        self._top_content_stack.addWidget(spectrum_block)
        self._flow_panel_placeholder = QWidget()
        self._top_content_stack.addWidget(self._flow_panel_placeholder)
        self._top_content_stack.setCurrentIndex(0)

        plot_splitter = CompactSplitter(Qt.Orientation.Vertical)
        plot_splitter.setChildrenCollapsible(False)
        plot_splitter.addWidget(self._top_content_stack)
        plot_splitter.addWidget(trace_block)
        plot_splitter.setStretchFactor(0, 3)
        plot_splitter.setStretchFactor(1, 2)
        plot_splitter.setSizes([560, 320])
        self.plot_splitter = plot_splitter

        right_panel = QVBoxLayout()
        right_panel.addWidget(self.plot_splitter, 1)
        right_panel.addLayout(footer_bar)

        right_widget = QWidget()
        right_widget.setLayout(right_panel)
        right_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        splitter = CompactSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left_scroll)
        splitter.addWidget(right_widget)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 1100])
        self.left_right_splitter = splitter

        recording_context_row = self._build_recording_context_row()

        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(6, 4, 6, 6)
        root_layout.setSpacing(5)
        root_layout.addWidget(recording_context_row)
        root_layout.addWidget(splitter, 1)

        container = QWidget()
        container.setLayout(root_layout)
        self._main_content_widget = container
        self.setCentralWidget(container)

    def _build_recording_context_row(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("recordingContextPanel")
        layout = QHBoxLayout()
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(5)
        controls_row = QHBoxLayout()
        controls_row.setContentsMargins(0, 0, 0, 0)
        controls_row.setSpacing(5)
        project_label = QLabel("Project's destination")
        project_label.setToolTip("Root folder for saved experiment folders.")
        experiment_label = QLabel("Experiment name")
        experiment_label.setToolTip("Folder name and filename component for the current experiment.")
        controls_row.addWidget(project_label)
        controls_row.addWidget(self.project_destination_edit, 3)
        controls_row.addWidget(self.project_destination_browse_button)
        controls_row.addSpacing(6)
        controls_row.addWidget(experiment_label)
        controls_row.addWidget(self.experiment_name_edit, 2)
        controls_row.addSpacing(4)
        controls_row.addWidget(self.measurement_compression_button)
        layout.addLayout(controls_row)
        panel.setLayout(layout)
        panel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        panel.setFixedHeight(28)
        return panel

    def recording_project_destination(self) -> str:
        return self.project_destination_edit.text().strip()

    def recording_experiment_name(self) -> str:
        return self.experiment_name_edit.text().strip()

    def _set_recording_context_controls_enabled(self, enabled: bool) -> None:
        self.project_destination_edit.setEnabled(enabled)
        self.project_destination_browse_button.setEnabled(enabled)
        self.experiment_name_edit.setEnabled(enabled)
        self.measurement_compression_button.setEnabled(enabled)

    def _choose_recording_project_destination(self) -> None:
        current = self.recording_project_destination()
        start_dir = current if current else str(Path.cwd())
        selected = QFileDialog.getExistingDirectory(self, "Project's destination", start_dir)
        if not selected:
            return
        self.project_destination_edit.setText(selected)
        self._remember_recording_project_destination()

    def _open_recording_project_destination_in_explorer(self) -> None:
        current = self.recording_project_destination()
        folder = Path(current).expanduser() if current else Path.cwd()
        if not folder.exists():
            folder = folder.parent if folder.parent.exists() else Path.cwd()
        if folder.is_file():
            folder = folder.parent
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder.resolve())))

    def _remember_recording_project_destination(self) -> None:
        save_app_setting("recording_project_destination", self.recording_project_destination())

    def _remember_recording_experiment_name(self) -> None:
        save_app_setting("recording_experiment_name", self.recording_experiment_name())

    def measurement_hdf5_compression_enabled(self) -> bool:
        return bool(self._hdf5_compression_enabled)

    def _set_measurement_hdf5_compression_enabled(self, enabled: bool) -> None:
        self._hdf5_compression_enabled = bool(enabled)
        save_app_setting("measurement_hdf5_compression_enabled", self._hdf5_compression_enabled)
        if hasattr(self, "measurement_compression_button"):
            self.measurement_compression_button.blockSignals(True)
            self.measurement_compression_button.setChecked(self._hdf5_compression_enabled)
            self.measurement_compression_button.blockSignals(False)
            self.measurement_compression_button.setIcon(
                storage_compression_icon(self._hdf5_compression_enabled)
            )
        state_text = "enabled" if self._hdf5_compression_enabled else "disabled"
        self._log_info(f"HDF5 compression {state_text}.")

    def _set_status_indicator(
        self,
        dot: QLabel | None,
        text_label: QLabel | None,
        *,
        online: bool,
        online_text: str,
        offline_text: str,
    ) -> None:
        color = "#2e7d32" if online else "#8a98a8"
        if dot is not None:
            dot.setStyleSheet(
                f"background: {color}; border-radius: 5px; border: 1px solid {color};"
            )
        if text_label is not None:
            text_label.setText(online_text if online else offline_text)

    def _configure_source_tabs(self) -> None:
        tab_bar = self.source_tabs.tabBar()
        self.source_tabs.setTabEnabled(0, True)
        tab_bar.setTabToolTip(0, "Ocean spectrometer control" if self._hardware_available else "Spectrometer not connected")
        tab_bar.setTabToolTip(1, "Simulation mode")
        self.source_tabs.setTabText(0, "")
        self.source_tabs.setTabText(1, "")
        self.source_tabs.setTabIcon(0, QIcon())
        self.source_tabs.setTabIcon(1, QIcon())
        update_source_link_buttons(self)
        update_source_tab_headers(self)
        self._update_pump_status()
        self._update_simulation_controls_enabled()

    def _update_pump_status(self, probe: "PumpProbe | None" = None) -> None:
        if probe is not None:
            self._discovered_pump_probe = probe
        refresh_hw_device_status_strip(self)

    def _theme_palette(self) -> dict[str, str]:
        if self._theme_mode == "dark":
            return {
                "bg": "#13161b",
                "fg": "#e6ebf1",
                "muted": "#a8b0ba",
                "panel": "#15191f",
                "field": "#171b21",
                "button": "#20252d",
                "button_hover": "#272d36",
                "button_pressed": "#303640",
                "accent_button": "#5d6876",
                "accent_hover": "#707d8c",
                "danger_button": "#8f5a61",
                "danger_hover": "#a46a72",
                "border": "#2b3138",
                "border_hover": "#414852",
                "pressed": "#252b33",
                "tab": "#1b2026",
                "tab_selected": "#13161b",
                "accent": "#b2bac4",
                "title": "#b2bac4",
                "scroll": "#49505a",
                "scroll_hover": "#5c6470",
                "splitter": "#2b3138",
                "splitter_hover": "#404854",
                "plot_bg": "#0f1216",
                "plot_border": "#2b3138",
                "axis_text": "#a8b0ba",
                "axis_pen": "#39404a",
                "grid": "#232830",
                "window": "#101318",
            }
        return {
            "bg": "#f4f6f8",
            "fg": "#1d2733",
            "muted": "#243241",
            "panel": "#f4f6f8",
            "field": "#f4f6f8",
            "button": "#eef3f7",
            "button_hover": "#e6edf3",
            "button_pressed": "#dde9f3",
            "accent_button": "#2f80c1",
            "accent_hover": "#3e8dcf",
            "danger_button": "#d65a63",
            "danger_hover": "#e06a73",
            "border": "#d9e0e7",
            "border_hover": "#9dbbd4",
            "pressed": "#dde9f3",
            "tab": "#e8eef3",
            "tab_selected": "#f4f6f8",
            "accent": "#2f80c1",
            "title": "#2f80c1",
            "scroll": "#bcc9d5",
            "scroll_hover": "#9fb3c5",
            "splitter": "#dde5ec",
            "splitter_hover": "#c7d5e2",
            "plot_bg": "#ffffff",
            "plot_border": "#d9e0e7",
            "axis_text": "#5e7288",
            "axis_pen": "#c8d3dd",
            "grid": "#d8e2ea",
            "window": "#f4f6f8",
        }

    def _apply_modern_style(self) -> None:
        palette = self._theme_palette()
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
            QToolButton#sensorgramViewModeButton,
            QToolButton#sensorgramContentModeButton,
            QToolButton#sensorgramDownsamplingButton,
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
            QToolButton#sensorgramDownsamplingButton:hover,
            QToolButton#sensorgramWindowButton:hover {
                background: transparent;
                border: none;
            }
            QToolButton#sensorgramViewModeButton:pressed,
            QToolButton#sensorgramContentModeButton:pressed,
            QToolButton#sensorgramDownsamplingButton:pressed,
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
                background: %(accent)s;
                border-color: %(accent)s;
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
                "trace_max": self.TRACE_METRIC_COLORS["smoothed_max"],
                "trace_centroid": self.TRACE_METRIC_COLORS["centroid"],
                "trace_poly": self.TRACE_METRIC_COLORS["poly_max"],
                "trace_gaussian": self.TRACE_METRIC_COLORS["gaussian_center"],
            }
        self.setStyleSheet(stylesheet.replace("__CHECKMARK_ICON__", checkmark_icon))

    def set_theme(self, theme_mode: str) -> None:
        if theme_mode not in {"light", "dark"} or theme_mode == self._theme_mode:
            return
        self._theme_mode = theme_mode
        save_app_setting("theme_mode", self._theme_mode)
        self._apply_modern_style()
        self._style_plot_widgets()
        self._apply_sensorgram_display_style()
        self._update_measurement_toggle_button()
        self._log_info(f"Theme switched to {self._theme_mode}.")
        if self._experiment_control_window is not None:
            self._experiment_control_window.set_theme(self._theme_mode)

    def _style_plot_widgets(self) -> None:
        palette = self._theme_palette()
        for plot in (self.spectrum_plot, self.trace_plot):
            plot.setBackground(palette["plot_bg"])
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
            plot.getPlotItem().showGrid(x=True, y=True, alpha=0.16 if self._theme_mode == "dark" else 0.18)
        if hasattr(self, "residual_axis"):
            self.residual_axis.setStyle(tickTextOffset=2)
            self.residual_axis.setTextPen(pg.mkPen(palette["axis_text"]))
            self.residual_axis.setPen(pg.mkPen(palette["axis_pen"]))
        crosshair_color = "#7f93a8" if self._theme_mode == "dark" else "#666666"
        self.spectrum_vline.setPen(pg.mkPen(crosshair_color, width=1))
        self.spectrum_hline.setPen(pg.mkPen(crosshair_color, width=1))
        self.trace_vline.setPen(pg.mkPen(crosshair_color, width=1))
        self.trace_hline.setPen(pg.mkPen(crosshair_color, width=1))
        if self._theme_mode == "dark":
            self.processing_region_item.setBrush(pg.mkBrush(111, 179, 255, 26))
            self.fit_region_item.setBrush(pg.mkBrush(255, 190, 92, 28))
            self.max_marker.setPen(pg.mkPen("#11161c", width=1.4))
            self.poly_marker.setPen(pg.mkPen("#11161c", width=1.4))
            self.gaussian_marker.setPen(pg.mkPen("#11161c", width=1.4))
            self.centroid_marker.setPen(pg.mkPen("#11161c", width=1.4))
            self.residual_curve.setPen(pg.mkPen("#b6b6b6", width=1))
        else:
            self.processing_region_item.setBrush(pg.mkBrush(90, 160, 255, 30))
            self.fit_region_item.setBrush(pg.mkBrush(255, 180, 80, 40))
            self.max_marker.setPen(pg.mkPen("w", width=1.4))
            self.poly_marker.setPen(pg.mkPen("w", width=1.4))
            self.gaussian_marker.setPen(pg.mkPen("w", width=1.4))
            self.centroid_marker.setPen(pg.mkPen("w", width=1.4))
            self.residual_curve.setPen(pg.mkPen("#7d7d7d", width=1))
        self._update_freeze_button_icon()
        self._update_residual_button_icon()
        self._update_dark_reference_button_icons()

    def _sensorgram_heatmap_lookup_table(self) -> np.ndarray:
        try:
            return pg.colormap.get("viridis").getLookupTable(0.0, 1.0, 256)
        except Exception:
            try:
                color_map = pg.ColorMap(
                    [0.0, 0.25, 0.5, 0.75, 1.0],
                    [
                        (68, 1, 84, 255),
                        (59, 82, 139, 255),
                        (33, 145, 140, 255),
                        (94, 201, 98, 255),
                        (253, 231, 37, 255),
                    ],
                )
                return color_map.getLookupTable(0.0, 1.0, 256)
            except Exception:
                return pg.colormap.get("plasma").getLookupTable(0.0, 1.0, 256)

    def _apply_sensorgram_display_style(self) -> None:
        mode = self._normalize_sensorgram_content_mode(getattr(self, "_sensorgram_content_mode", "metric"))
        if mode == "heatmap":
            self.trace_heatmap_image.setLookupTable(self._sensorgram_heatmap_lookup_table())
            for curve in self.trace_curves.values():
                curve.setPen(pg.mkPen("#8A5CFF", width=2.2))
            return

        for metric_name, curve in self.trace_curves.items():
            curve.setPen(pg.mkPen(self.SENSORGRAM_TIME_PLOT_COLORS.get(metric_name, "#1F77B4"), width=2.2))

    def _set_log_following(self, enabled: bool) -> None:
        set_log_following(self, enabled)

    def _clear_log_terminal(self) -> None:
        clear_log_terminal(self)

    def _copy_log_terminal(self) -> None:
        copy_log_terminal(self)

    def _flush_log_buffer(self) -> None:
        flush_log_buffer(self)

    def _append_log_record(self, levelno: int, source: str, text: str) -> None:
        append_log_record(self, levelno, source, text)

    def _insert_log_record(self, cursor: QTextCursor, levelno: int, source: str, text: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        color_map = {
            logging.DEBUG: "#5fa8ff",
            logging.INFO: "#c7d2e0",
            SUCCESS_LOG_LEVEL: "#44d07b",
            logging.WARNING: "#f4b23d",
            logging.ERROR: "#ff6b6b",
            logging.CRITICAL: "#f35f8d",
        }
        level_label_map = {
            logging.DEBUG: "DEBUG",
            logging.INFO: "INFO",
            SUCCESS_LOG_LEVEL: "SUCCESS",
            logging.WARNING: "WARN",
            logging.ERROR: "ERROR",
            logging.CRITICAL: "CRIT",
        }
        level_color = color_map.get(int(levelno), "#c7d2e0")
        level_label = level_label_map.get(int(levelno), "INFO")
        source_label = escape(str(source).split(".")[-1] or "app")
        escaped = escape(text).replace("\n", "<br>")
        html = (
            f"<div style='white-space:pre-wrap; margin:0;'>"
            f"<span style='color:#738193;'>{timestamp}</span> "
            f"<span style='color:{level_color}; font-weight:600;'>[{level_label}]</span> "
            f"<span style='color:#94a3b8;'>{source_label}</span> "
            f"<span style='color:#e5edf7;'>{escaped}</span>"
            f"</div>"
        )
        cursor.insertHtml(html)
        cursor.insertHtml("<br>")
        if self._log_follow_enabled:
            scrollbar = self.log_terminal.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _append_log_record_now(self, levelno: int, source: str, text: str) -> None:
        append_log_record_now(self, levelno, source, text)

    def _log_event(self, levelno: int, message: str, source: str = "main") -> None:
        log_event(self, levelno, message, source=source)

    def _log_debug(self, message: str, source: str = "main") -> None:
        log_debug(self, message, source=source)

    def _log_info(self, message: str, source: str = "main") -> None:
        log_info(self, message, source=source)

    def _log_success(self, message: str, source: str = "main") -> None:
        log_success(self, message, source=source)

    def _log_warning(self, message: str, source: str = "main") -> None:
        log_warning(self, message, source=source)

    def _log_error(self, message: str, source: str = "main") -> None:
        log_error(self, message, source=source)

    def _log_throttled(self, key: str, message: str, *, level: int = logging.DEBUG, min_interval: float = 1.5) -> None:
        log_throttled(self, key, message, level=level, min_interval=min_interval)

    def _fit_window_to_available_screen(self) -> None:
        screen = None
        if self.windowHandle() is not None:
            screen = self.windowHandle().screen()
        if screen is None:
            screen = self.screen()
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen is None:
            return

        available = screen.availableGeometry()
        margin = 12
        max_width = max(available.width() - margin * 2, 640)
        max_height = max(available.height() - margin * 2, 480)
        target_width = min(self.width(), max_width)
        target_height = min(self.height(), max_height)
        self.resize(target_width, target_height)

        if self._ui_state:
            x_pos = min(
                max(self.x(), available.x() + margin),
                available.x() + available.width() - self.width() - margin,
            )
            y_pos = min(
                max(self.y(), available.y() + margin),
                available.y() + available.height() - self.height() - margin,
            )
            self.move(x_pos, y_pos)
            return

        frame = self.frameGeometry()
        x_pos = available.x() + max((available.width() - frame.width()) // 2, 0)
        y_pos = available.y() + max((available.height() - frame.height()) // 2, 0)
        self.move(x_pos, y_pos)

    def showEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        super().showEvent(event)
        if not getattr(self, "_startup_show_event_reported", False):
            self._startup_show_event_reported = True
            startup_show_t0 = getattr(self, "_startup_show_requested_t0", None)
            if startup_show_t0 is not None:
                self._log_info(
                    f"Startup +{(perf_counter() - startup_show_t0) * 1000.0:.1f} ms: first showEvent reached"
                )
        if not self._screen_fitted:
            self._screen_fitted = True
            if self._start_maximized:
                self.showMaximized()
            else:
                self._fit_window_to_available_screen()
        if not self._hardware_init_scheduled:
            self._hardware_init_scheduled = True
            QTimer.singleShot(0, self._start_hardware_initialization)

    def _restore_ui_state(self) -> None:
        restore_ui_state(self)

    def _save_ui_state(self) -> None:
        save_ui_state(self)

    def _collapsible_section_state(self) -> dict[str, bool]:
        return collapsible_section_state(self)

    def _restore_collapsible_section_state(self) -> None:
        restore_collapsible_section_state(self)

    def _acquisition_state_payload(self) -> dict[str, object]:
        return acquisition_state_payload(self)

    def _persist_acquisition_state(self) -> None:
        persist_acquisition_state(self)

    def _schedule_acquisition_state_persist(self) -> None:
        schedule_acquisition_state_persist(self)

    def _apply_acquisition_state_to_widgets(self, state: dict[str, object]) -> None:
        apply_acquisition_state_to_widgets(self, state)

    def _open_experiment_control_window(self) -> None:
        self._activate_flow_view()

    def _toggle_flow_panel_visibility(self, checked: bool | None = None) -> None:
        if checked is None:
            self._activate_flow_view() if self._top_view_mode != "flow" else self._activate_spectra_view()
        elif checked:
            self._activate_flow_view()
        else:
            self._activate_spectra_view()

    def _ensure_flow_panel(self) -> None:
        if self._experiment_control_window is None:
            from lspr_app.gui.experiment_control_window import ExperimentControlWindow

            self._experiment_control_window = ExperimentControlWindow(
                self._experiment_control_window_ui_state,
                known_probe=self._discovered_pump_probe,
                theme_mode=self._theme_mode,
                initial_mswitch_devices=[probe for probe in self._initial_mswitch_devices if probe is not None],
                auto_connect_devices=True,
            )
            self._experiment_control_window.availability_changed.connect(self._handle_flow_availability_changed)
            self._experiment_control_window.valve_availability_changed.connect(self._handle_valve_availability_changed)
            self._experiment_control_window.mswitch_availability_changed.connect(self._handle_mswitch_availability_changed)
            self._experiment_control_window.recording_control_requested.connect(self._handle_flow_recording_control)
            self._experiment_control_window.flow_state_recorded.connect(self._handle_flow_state_recorded)
            self._experiment_control_window.recording_controller = self
            self._experiment_control_window.theme_changed.connect(self.set_theme)
            if hasattr(self._experiment_control_window, "_set_record_with_flow_recording_active"):
                self._experiment_control_window._set_record_with_flow_recording_active(bool(self._measurement_active))
            if hasattr(self, "_top_content_stack"):
                placeholder = getattr(self, "_flow_panel_placeholder", None)
                if placeholder is not None:
                    index = self._top_content_stack.indexOf(placeholder)
                    if index >= 0:
                        self._top_content_stack.removeWidget(placeholder)
                        placeholder.setParent(None)
                    self._top_content_stack.addWidget(self._experiment_control_window)
            self._log_info("Experiment control panel created.")

    def _sync_main_view_visibility(self) -> None:
        if self._main_content_widget is None:
            return
        self._main_content_widget.setVisible(True)
        refresh_hw_device_status_strip(self)

    def _show_flow_only(self) -> None:
        self._activate_flow_view()

    def _show_plots_only(self) -> None:
        self._activate_spectra_view()

    def _show_split_view(self) -> None:
        self._left_controls_scroll.setVisible(True)
        self._sensorgram_block.setVisible(True)
        self._activate_spectra_view()

    def _activate_spectra_view(self) -> None:
        if hasattr(self, "_top_content_stack"):
            self._top_content_stack.setCurrentWidget(self._spectra_block)
        self._top_view_mode = "spectra"
        self._sync_view_actions()

    def _activate_flow_view(self) -> None:
        self._ensure_flow_panel()
        if hasattr(self, "_top_content_stack") and self._experiment_control_window is not None:
            self._top_content_stack.setCurrentWidget(self._experiment_control_window)
        self._top_view_mode = "flow"
        self._sync_view_actions()

    def _activate_experiment_control_view(self) -> None:
        self._activate_flow_view()

    def _toggle_left_controls(self, checked: bool | None = None) -> None:
        visible = self._left_controls_scroll.isVisible() if checked is None else bool(checked)
        self._left_controls_scroll.setVisible(visible)
        self._sync_view_actions()

    def _toggle_sensorgram(self, checked: bool | None = None) -> None:
        visible = self._sensorgram_block.isVisible() if checked is None else bool(checked)
        self._sensorgram_block.setVisible(visible)
        self._sync_view_actions()

    def _sync_view_actions(self) -> None:
        actions = getattr(self, "_view_menu_actions", None)
        if not isinstance(actions, dict):
            return
        top = actions.get("top_view")
        if isinstance(top, dict):
            for mode, action in top.items():
                action.blockSignals(True)
                action.setChecked(self._top_view_mode == mode)
                action.blockSignals(False)
        left_action = actions.get("left_controls")
        if left_action is not None:
            left_action.blockSignals(True)
            left_action.setChecked(self._left_controls_scroll.isVisible())
            left_action.blockSignals(False)
        sensor_action = actions.get("sensorgram")
        if sensor_action is not None:
            sensor_action.blockSignals(True)
            sensor_action.setChecked(self._sensorgram_block.isVisible())
            sensor_action.blockSignals(False)

    def _handle_flow_availability_changed(self, probe: object) -> None:
        if probe is None:
            self._discovered_pump_probe = None
            self._update_pump_status(None)
            self._log_warning("Pump controller disconnected.")
            return
        if hasattr(probe, "port"):
            self._update_pump_status(probe)
            port_name = getattr(getattr(probe, "port", None), "device", "unknown")
            self._log_success(f"Pump controller connected on {port_name}.")

    def _handle_valve_availability_changed(self, probe: object) -> None:
        refresh_hw_device_status_strip(self)
        if probe is None:
            self._log_warning("Valve controller disconnected.")
            return
        port_name = getattr(probe, "port", "unknown")
        model = getattr(probe, "model", "Valve controller")
        self._log_success(f"Valve controller connected: {model} on {port_name}.")

    def _handle_mswitch_availability_changed(self, probe: object) -> None:
        self._mswitch_probe = probe if probe is not None else None
        refresh_hw_device_status_strip(self)
        if probe is None:
            self._log_warning("M-Switch disconnected.")
            return
        port_name = getattr(probe, "port", "unknown")
        model = getattr(probe, "model", "AMF switch")
        self._log_success(f"M-Switch connected: {model} on {port_name}.")

    def _handle_flow_recording_control(self, action: str) -> bool:
        action = str(action or "").strip().lower()
        # Recording is intentionally independent from flow HOLD/PAUSE.
        # The experiment controller may request "start" or "stop", but a flow HOLD should not
        # close the writer or freeze acquisition.
        if action == "start":
            if not self._measurement_active:
                self._start_measurement_run()
            return bool(self._measurement_active)
        elif action == "pause":
            return True
        elif action == "stop":
            if self._measurement_active:
                self._stop_measurement_run()
            return True
        return True

    def _handle_flow_state_recorded(self, payload: object) -> None:
        if self._measurement_writer is None or not self._measurement_active:
            return
        if not isinstance(payload, dict):
            return
        row = dict(payload)
        if self._measurement_started_at is not None:
            elapsed_ms = int(round((datetime.now(timezone.utc) - self._measurement_started_at).total_seconds() * 1000.0))
        else:
            elapsed_ms = 0
        row["t_ms"] = max(elapsed_ms, 0)
        self._measurement_writer.append_flow_state(row)

    def _apply_control_sizing(self) -> None:
        tall_widgets = [
            self.integration_spin,
            self.averages_spin,
            self.auto_integration_button,
            self.correct_dark_check,
            self.correct_nonlinearity_check,
            self.live_rate_spin,
            self.trace_noise_window_spin,
            self.range_min_spin,
            self.range_max_spin,
            self.baseline_method_combo,
            self.smoothing_method_combo,
            self.smoothing_window_spin,
            self.temporal_smoothing_spin,
            self.crop_method_combo,
            self.fit_method_combo,
            self.poly_order_spin,
            self.fit_window_spin,
            self.analysis_resolution_spin,
            self.peak_metric_combo,
            self.plot_selector,
            self.save_processing_button,
            self.load_processing_button,
            self.clear_trace_button,
            self.trace_record_button,
            self.show_residual_button,
            self.freeze_plots_button,
            self.autoscale_spectrum_button,
            self.autoscale_trace_button,
            self.sim_resolution_spin,
        ]
        for widget in tall_widgets:
            widget.setMinimumHeight(26)

        compact_combos = [
            self.baseline_method_combo,
            self.smoothing_method_combo,
            self.crop_method_combo,
            self.fit_method_combo,
            self.analysis_resolution_spin,
            self.peak_metric_combo,
            self.plot_selector,
        ]
        for combo in compact_combos:
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
            combo.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
            combo.setMinimumContentsLength(1)
            combo.view().setTextElideMode(Qt.TextElideMode.ElideRight)

        configure_processing_group_controls(self)

        for button in (
            self.measurement_toggle_button,
            self.stop_measurement_button,
            self.next_measurement_button,
            self.acquire_dark_button,
            self.acquire_reference_button,
        ):
            button.setMinimumSize(QSize(34, 34))

        for slider in (
            self.sim_peak_center_slider,
            self.sim_peak_width_slider,
            self.sim_peak_height_slider,
            self.sim_baseline_slider,
            self.sim_slope_slider,
            self.sim_noise_slider,
        ):
            slider.setMinimumHeight(20)

        self.source_tabs.setMinimumHeight(220)

    def _connect_simulation_widgets(self) -> None:
        for slider in (
            self.sim_peak_center_slider,
            self.sim_peak_width_slider,
            self.sim_peak_height_slider,
            self.sim_baseline_slider,
            self.sim_slope_slider,
            self.sim_noise_slider,
        ):
            slider.valueChanged.connect(self._handle_simulation_parameter_change)
        self.sim_resolution_spin.valueChanged.connect(self._handle_simulation_parameter_change)

    def _update_simulation_labels(self) -> None:
        self.sim_peak_center_value.setText(f"{self.sim_peak_center_slider.value()} nm")
        self.sim_peak_width_value.setText(f"{self.sim_peak_width_slider.value()} nm")
        self.sim_peak_height_value.setText(str(self.sim_peak_height_slider.value()))
        self.sim_baseline_value.setText(str(self.sim_baseline_slider.value()))
        self.sim_slope_value.setText(f"{self.sim_slope_slider.value() / 100.0:.2f}")
        self.sim_noise_value.setText(str(self.sim_noise_slider.value()))

    def _handle_simulation_parameter_change(self) -> None:
        self._update_simulation_labels()
        self._simulation_refresh_timer.start()
        self._schedule_acquisition_state_persist()

    def _flush_simulation_parameter_change(self) -> None:
        self._sync_simulation_backend_from_controls()
        if self._source_mode == "simulation" and not self._live_active:
            self._session.set_sample(self._build_simulation_spectrum("sample"))
            self._refresh_plot()
        self._log_throttled(
            "simulation_params",
            "Simulation parameters updated.",
            level=logging.DEBUG,
            min_interval=0.75,
        )

    def _handle_source_tab_changed(self, index: int) -> None:
        self._configure_source_tabs()

    def _apply_source_mode(self, new_mode: str, restart_live: bool) -> None:
        if self._measurement_active and new_mode != self._source_mode:
            self._log_warning("Source switching is disabled while measurement is running.")
            self.status_label.setText("Source switching is disabled while measurement is running.")
            return
        self._source_mode = new_mode
        self._source_epoch += 1
        self._session = self._hardware_session if new_mode == "spectrometer" else self._simulation_session
        self._raw_last_finish_ts = None
        self._last_elapsed_ms = None
        self._last_spacing_ms = None
        self._last_overhead_ms = None
        self._effective_raw_rate_hz = None
        self._last_display_average_count = None
        self._last_display_period_ms = None
        self._peak_history.clear()
        self._sensorgram_heatmap_history.clear()
        self._sensorgram_heatmap_wavelengths = None
        self._peak_reference_processed = None
        self._live_trace_started_at = None
        self._reset_live_accumulator()
        if new_mode == "simulation" and self._session.state.sample is None:
            self._session.set_sample(self._build_simulation_spectrum("sample"))
        self._log_success(
            f"Active source set to {'spectrometer' if new_mode == 'spectrometer' else 'simulation'}."
        )
        self._configure_source_tabs()
        self._refresh_plot()
        self._request_deferred_ui_refresh(telemetry=True, live_estimate=True, summary=True)
        self._update_simulation_controls_enabled()
        self._schedule_acquisition_state_persist()
        if restart_live:
            self._log_debug("Restarting live acquisition after source switch.")
            QTimer.singleShot(0, self._start_live_acquisition)

    def _default_simulation_resolution_nm(self) -> float:
        return float(self._simulation_backend.simulation_parameters().wavelength_resolution_nm)

    def _simulation_wavelength_axis(self) -> np.ndarray:
        if self._measurement_axis_lock is not None:
            return self._measurement_axis_lock.copy()
        return self._simulation_backend.wavelength_axis()

    def _current_simulation_interval_ms(self) -> float:
        process_interval_ms = 1000.0 / max(self.live_rate_spin.value(), 1e-9)
        return max(process_interval_ms, 1.0)

    def _build_simulation_spectrum(self, kind: str) -> Spectrum:
        return self._simulation_backend.acquire_kind_spectrum(kind, self._current_settings())

    def _sync_simulation_backend_from_controls(self) -> None:
        if self._measurement_active and self._measurement_axis_lock is not None:
            return
        self._simulation_backend.set_simulation_parameters(
            SimulationParameters(
                wavelength_min_nm=400.0,
                wavelength_max_nm=900.0,
                wavelength_resolution_nm=max(self.sim_resolution_spin.value(), 0.001),
                peak_center_nm=float(self.sim_peak_center_slider.value()),
                peak_width_nm=float(max(self.sim_peak_width_slider.value(), 1)),
                peak_height=float(self.sim_peak_height_slider.value()),
                baseline=float(self.sim_baseline_slider.value()),
                slope=float(self.sim_slope_slider.value()) / 100.0,
                noise=float(self.sim_noise_slider.value()),
            )
        )
        if (
            self._live_active
            and self._source_mode == "simulation"
            and self._live_worker is not None
            and self._live_worker.is_alive()
        ):
            self._live_worker.update_simulation_parameters(self._simulation_backend.simulation_parameters())

    def _connect_processing_widgets(self) -> None:
        self.range_min_spin.valueChanged.connect(self._handle_processing_setting_change)
        self.range_max_spin.valueChanged.connect(self._handle_processing_setting_change)
        self.baseline_method_combo.currentTextChanged.connect(self._handle_processing_setting_change)
        self.smoothing_method_combo.currentTextChanged.connect(self._handle_processing_setting_change)
        self.smoothing_window_spin.valueChanged.connect(self._handle_processing_setting_change)
        self.temporal_smoothing_spin.valueChanged.connect(self._handle_processing_setting_change)
        self.crop_method_combo.currentTextChanged.connect(self._handle_processing_setting_change)
        self.crop_fraction_spin.valueChanged.connect(self._handle_processing_setting_change)
        self.fit_method_combo.currentTextChanged.connect(self._handle_processing_setting_change)
        self.poly_order_spin.valueChanged.connect(self._handle_processing_setting_change)
        self.fit_window_spin.valueChanged.connect(self._handle_processing_setting_change)
        self.analysis_resolution_spin.currentIndexChanged.connect(self._handle_processing_setting_change)
        self.peak_metric_combo.currentTextChanged.connect(self._handle_processing_setting_change)
        self.trace_max_check.toggled.connect(self._handle_processing_setting_change)
        self.trace_centroid_check.toggled.connect(self._handle_processing_setting_change)
        self.trace_poly_check.toggled.connect(self._handle_processing_setting_change)
        self.trace_gaussian_check.toggled.connect(self._handle_processing_setting_change)

    def _current_settings(self) -> AcquisitionSettings:
        return AcquisitionSettings(
            integration_time_ms=self.integration_spin.value(),
            averages=self.averages_spin.value(),
            correct_dark_counts=self.correct_dark_check.isChecked() and self._capabilities.supports_dark_correction,
            correct_nonlinearity=(
                self.correct_nonlinearity_check.isChecked()
                and self._capabilities.supports_nonlinearity_correction
            ),
            trigger_mode=0,
        )

    def _current_processing_settings(self) -> ProcessingSettings:
        return current_processing_settings(self)

    def _selected_trace_metrics(self) -> list[str]:
        return selected_trace_metrics(self)

    def _apply_processing_settings_to_widgets(self, settings: ProcessingSettings) -> None:
        apply_processing_settings_to_widgets(self, settings)

    def _persist_processing_settings(self) -> None:
        persist_processing_settings(self)

    def _save_processing_settings_dialog(self) -> None:
        save_processing_settings_dialog(self)

    def _load_processing_settings_dialog(self) -> None:
        load_processing_settings_dialog(self)

    def _settings_key(self, settings: AcquisitionSettings) -> tuple[object, ...]:
        if self._source_mode == "simulation":
            axis_size = len(self._simulation_wavelength_axis())
        else:
            axis_size = len(getattr(self._spectrometer, "_wavelengths", []))
        return (
            round(settings.integration_time_ms, 6),
            settings.averages,
            settings.correct_dark_counts,
            settings.correct_nonlinearity,
            0,
            axis_size,
        )

    def _auto_set_integration_time(self) -> None:
        if self._source_mode == "simulation":
            self.status_label.setText("Auto integration is only available for the spectrometer source.")
            self._log_warning("Auto integration requested while simulation source is active.")
            return

        if self._busy:
            self._pending_auto_integration = True
            if self._live_active:
                self._resume_live_after_auto_integration = True
                self._live_active = False
            self.status_label.setText("Auto integration queued. Waiting for current acquisition to finish...")
            return

        if self._live_active:
            self._resume_live_after_auto_integration = True
            self._live_active = False
            self._reset_live_accumulator()
            self.status_label.setText("Pausing live acquisition for auto integration...")
            QTimer.singleShot(0, self._auto_set_integration_time)
            return

        try:
            tuned_ms = self._spectrometer.auto_integration_time_ms(self._current_settings())
        except Exception as exc:
            self._show_error(str(exc))
            return

        self.integration_spin.setValue(round(tuned_ms, 3))
        self.status_label.setText(f"Integration time set to {tuned_ms:.3f} ms.")
        self._log_success(f"Integration time tuned to {tuned_ms:.3f} ms.")
        if self._resume_live_after_auto_integration:
            self._resume_live_after_auto_integration = False
            QTimer.singleShot(0, self._start_live_acquisition)

    def _start_acquisition(self, kind: str) -> None:
        start_acquisition(self, kind)

    def _handle_acquisition_success(self, kind: str, result: AcquisitionResult) -> None:
        handle_acquisition_success(self, kind, result)

    def _flush_live_acquisition_results(self) -> None:
        flush_live_acquisition_results(self)

    def _flush_live_processed_results(self) -> None:
        flush_live_processed_results(self)

    def _reset_live_accumulator(self) -> None:
        self._accumulator_sum = None
        self._accumulator_count = 0
        self._accumulator_started_ts = None
        self._accumulator_template = None

    def _handle_acquisition_error(self, source_epoch: int, message: str) -> None:
        handle_acquisition_error(self, source_epoch, message)

    def _set_measurement_buttons_enabled(self, enabled: bool) -> None:
        set_measurement_buttons_enabled(self, enabled)

    def _set_manual_acquisition_buttons_enabled(self, enabled: bool) -> None:
        set_manual_acquisition_buttons_enabled(self, enabled)

    def _set_measurement_ui_locked(self, locked: bool) -> None:
        set_measurement_ui_locked(self, locked)

    def _update_simulation_controls_enabled(self) -> None:
        enabled = self._source_mode == "simulation" and not self._measurement_active
        self.sim_output_rate_spin.setEnabled(enabled)

    def _update_measurement_toggle_button(self) -> None:
        update_measurement_toggle_button(self)

    def _toggle_measurement_run(self) -> None:
        toggle_measurement_run(self)

    def _start_measurement_run(self) -> None:
        start_measurement_run(self)

    def _pause_measurement_run(self) -> None:
        pause_measurement_run(self)

    def _stop_measurement_run(self) -> None:
        stop_measurement_run(self)

    def _start_measurement_file_compression(self, path: Path) -> None:
        from lspr_app.gui.acquisition_controller import _start_measurement_file_compression

        _start_measurement_file_compression(self, path)

    def _handle_measurement_file_compression_finished(self, result: object) -> None:
        from lspr_app.gui.acquisition_controller import _handle_measurement_file_compression_finished

        _handle_measurement_file_compression_finished(self, result)

    def _handle_measurement_file_compression_failed(self, message: str) -> None:
        from lspr_app.gui.acquisition_controller import _handle_measurement_file_compression_failed

        _handle_measurement_file_compression_failed(self, message)

    def _append_processed_trace_history(self, processed: Spectrum, fit: Spectrum | None) -> None:
        append_processed_trace_history(self, processed, fit)

    def _get_analysis_processed_spectrum(
        self,
        signal: Spectrum,
    ) -> tuple[Spectrum | None, Spectrum | None]:
        return get_analysis_processed_spectrum_for(self, signal)

    def _flush_measurement_frames(self, force: bool = False) -> None:
        flush_measurement_frames(self, force=force)

    def _compute_peak_metric_nm(self, processed: Spectrum, fit: Spectrum | None) -> float:
        return compute_peak_metric_nm_for(self, processed, fit)

    def _compute_trace_metrics(self, processed: Spectrum, fit: Spectrum | None) -> dict[str, float]:
        return compute_trace_metrics_for(self, processed, fit)

    def _compute_metric_nm(self, mode: str, processed: Spectrum, fit: Spectrum | None) -> float:
        return compute_metric_nm_for(self, mode, processed, fit)

    def _processing_cache_token(self, spectrum: Spectrum | None, settings: ProcessingSettings) -> tuple[object, ...] | None:
        return processing_cache_token_for(self, spectrum, settings)

    def _analysis_cache_token(
        self,
        processed: Spectrum | None,
        fit: Spectrum | None,
        settings: ProcessingSettings,
    ) -> tuple[object, ...] | None:
        return analysis_cache_token_for(self, processed, fit, settings)

    def _analysis_metrics_cache_token(
        self,
        processed: Spectrum | None,
        fit: Spectrum | None,
        settings: ProcessingSettings,
    ) -> tuple[object, ...] | None:
        return analysis_metrics_cache_token_for(self, processed, fit, settings)

    def _needs_gaussian_metric(self, settings: ProcessingSettings | None = None) -> bool:
        return needs_gaussian_metric_for(self, settings)

    def _get_dense_analysis_curve(
        self,
        processed: Spectrum | None,
        fit: Spectrum | None,
        settings: ProcessingSettings,
    ) -> tuple[np.ndarray, np.ndarray, dict[str, float | str]]:
        return get_dense_analysis_curve_for(self, processed, fit, settings)

    def _get_analysis_metrics(
        self,
        processed: Spectrum | None,
        fit: Spectrum | None,
        settings: ProcessingSettings | None = None,
    ) -> dict[str, object]:
        if settings is None:
            settings = self._current_processing_settings()
        return get_analysis_metrics_for(self, processed, fit, settings)

    def _get_processed_spectrum(
        self,
        spectrum: Spectrum | None,
        settings: ProcessingSettings,
    ) -> tuple[Spectrum | None, Spectrum | None]:
        return get_processed_spectrum_for(self, spectrum, settings)

    def _refresh_session_summary(self, force: bool = False) -> None:
        refresh_session_summary_for(self, force=force)

    def _refresh_session_statistics(self, force: bool = False) -> None:
        refresh_session_statistics_for(self, force=force)

    def _copy_session_stats_log(self) -> None:
        copy_session_stats_log_for(self)

    def _hardware_init_steps(self) -> list[HardwareInitStep]:
        return [
            HardwareInitStep("spectrometer", "Spectrometer", self._spectrometer_init_step),
            HardwareInitStep("pump", "Pump controller", self._pump_init_step),
            HardwareInitStep("mswitch", "M-Switch", self._mswitch_init_step),
            HardwareInitStep("valve", "Valve controller", self._valve_init_step),
        ]

    def _spectrometer_init_step(self) -> HardwareInitStepResult:
        name = self._spectrometer.device_name()
        if isinstance(self._spectrometer, SimulatedSpectrometer):
            return HardwareInitStepResult(
                key="spectrometer",
                label="Spectrometer",
                state="simulation",
                message="Simulation backend ready.",
                connected=False,
                payload=name,
            )
        return HardwareInitStepResult(
            key="spectrometer",
            label="Spectrometer",
            state="ready",
            message=f"Spectrometer backend active: {name}.",
            connected=True,
            payload=name,
        )

    def _pump_init_step(self) -> HardwareInitStepResult:
        ports = [port for port in RegloICCClient.list_ports() if is_probable_reglo_port(port)]
        if not ports:
            return HardwareInitStepResult(
                key="pump",
                label="Pump controller",
                state="missing",
                message="No pump controller discovered.",
                connected=False,
                payload=[],
            )
        last_error: str | None = None
        for port in ports:
            try:
                probe = RegloICCClient.probe_port(port.device)
                return HardwareInitStepResult(
                    key="pump",
                    label="Pump controller",
                    state="discovered",
                    message=f"Pump controller discovered on {probe.port}.",
                    connected=False,
                    probe=probe,
                    payload=probe,
                )
            except Exception as exc:
                last_error = str(exc)
        return HardwareInitStepResult(
            key="pump",
            label="Pump controller",
            state="error",
            message="Pump controller scan completed with no usable device.",
            connected=False,
            error=last_error,
            payload=[],
        )

    def _mswitch_init_step(self) -> HardwareInitStepResult:
        try:
            devices = detect_amf_mswitch_devices()
        except Exception as exc:
            return HardwareInitStepResult(
                key="mswitch",
                label="M-Switch",
                state="error",
                message=f"M-Switch scan failed: {exc}",
                connected=False,
                error=str(exc),
                payload=[],
            )
        if not devices:
            return HardwareInitStepResult(
                key="mswitch",
                label="M-Switch",
                state="missing",
                message="M-Switch not discovered at startup.",
                connected=False,
                payload=[],
            )
        first = devices[0]
        port_name = getattr(first, "port", "unknown")
        return HardwareInitStepResult(
            key="mswitch",
            label="M-Switch",
            state="discovered",
            message=f"M-Switch discovered on {port_name}.",
            connected=False,
            probe=first,
            payload=devices,
        )

    def _valve_init_step(self) -> HardwareInitStepResult:
        ports = [port for port in SerialController.list_ports() if controller_port_priority(port) > 0]
        if not ports:
            return HardwareInitStepResult(
                key="valve",
                label="Valve controller",
                state="missing",
                message="No valve controller discovered.",
                connected=False,
                payload=None,
            )
        ports = sorted(ports, key=controller_port_priority, reverse=True)
        last_error: str | None = None
        for port in ports:
            try:
                client, probe = detect_valve_controller(port.device)
            except Exception as exc:
                last_error = str(exc)
                continue
            try:
                client.close()
            except Exception:
                pass
            return HardwareInitStepResult(
                key="valve",
                label="Valve controller",
                state="discovered",
                message=f"Valve controller discovered on {probe.port}.",
                connected=False,
                probe=probe,
                payload=probe,
            )
        return HardwareInitStepResult(
            key="valve",
            label="Valve controller",
            state="error",
            message="Valve controller scan completed with no usable device.",
            connected=False,
            error=last_error,
            payload=None,
        )

    def _emit_hardware_init_progress(self, percent: int, text: str) -> None:
        self.hardware_init_progress.emit(max(0, min(int(percent), 100)), str(text))

    def _update_startup_loading_indicator(self, percent: int, _text: str) -> None:
        self._set_startup_loading_indicator(int(percent) < 100)

    def _set_startup_loading_indicator(self, active: bool) -> None:
        label = getattr(self, "_startup_loading_label", None)
        timer = getattr(self, "_startup_loading_timer", None)
        if label is None or timer is None:
            return
        if active:
            if not timer.isActive():
                self._startup_loading_frame_index = 0
                label.setFixedSize(22, 22)
                label.setVisible(True)
                self._render_startup_loading_indicator()
                timer.start()
        else:
            timer.stop()
            label.setVisible(False)

    def _set_recording_blink_indicator(self, active: bool) -> None:
        timer = getattr(self, "_recording_blink_timer", None)
        if timer is None:
            return
        if active:
            if not timer.isActive():
                self._recording_blink_visible = True
                self._render_recording_blink_indicator()
                timer.start()
        else:
            timer.stop()
            self._recording_blink_visible = True
            self._render_recording_blink_indicator()

    def _advance_recording_blink_indicator(self) -> None:
        timer = getattr(self, "_recording_blink_timer", None)
        if timer is None or not timer.isActive():
            return
        self._recording_blink_visible = not bool(getattr(self, "_recording_blink_visible", True))
        self._render_recording_blink_indicator()

    def _render_recording_blink_indicator(self) -> None:
        self._update_measurement_toggle_button()
        self._update_window_mode_label()

    def _advance_startup_loading_indicator(self) -> None:
        label = getattr(self, "_startup_loading_label", None)
        if label is None:
            return
        frame_index = int(getattr(self, "_startup_loading_frame_index", 0))
        frame_index = (frame_index + 1) % 12
        self._startup_loading_frame_index = frame_index
        self._render_startup_loading_indicator()

    def _render_startup_loading_indicator(self) -> None:
        label = getattr(self, "_startup_loading_label", None)
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

            frame_index = int(getattr(self, "_startup_loading_frame_index", 0)) % 12
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

    def _finish_hardware_initialization(self, text: str = "Hardware initialization scan finished.") -> None:
        self.status_label.setText(text)
        if self._hardware_init_ready_emitted:
            return
        self._hardware_init_ready_emitted = True
        self._hardware_status_overrides.clear()
        refresh_hw_device_status_strip(self)
        self._emit_hardware_init_progress(100, text)
        self._set_startup_loading_indicator(False)
        self.hardware_init_finished.emit()

    def _start_hardware_initialization(self) -> None:
        if self._hardware_init_task is not None:
            return
        self.status_label.setText("Scanning connected devices...")
        self._emit_hardware_init_progress(12, "Scanning connected devices...")
        self._log_info("Hardware initialization queued.")
        task = HardwareInitTask(self._hardware_init_steps())
        task.signals.progress.connect(self._emit_hardware_init_progress)
        task.signals.step.connect(self._handle_hardware_init_step)
        task.signals.finished.connect(self._handle_hardware_init_finished)
        self._hardware_init_task = task
        self._thread_pool.start(task)

    def _handle_hardware_init_step(self, result: object) -> None:
        if not isinstance(result, HardwareInitStepResult):
            return
        self._hardware_status_overrides[result.key] = (bool(result.connected), result.message)
        self.status_label.setText(result.message)
        if result.key == "spectrometer":
            self._hardware_available = not isinstance(self._spectrometer, SimulatedSpectrometer)
        elif result.key == "pump":
            self._discovered_pump_probe = result.probe
            self._update_pump_status(result.probe)
        elif result.key == "mswitch":
            self._initial_mswitch_devices = list(result.payload or [])
            self._mswitch_probe = result.probe if result.probe is not None else None
        refresh_hw_device_status_strip(self)

    def _handle_hardware_init_finished(self, result: object) -> None:
        self._hardware_init_task = None
        if not isinstance(result, HardwareInitResult):
            self._log_warning("Hardware initialization finished with an unexpected result payload.")
            self._finish_hardware_initialization("Hardware initialization finished.")
            return
        if result.pump_probe is not None:
            self._discovered_pump_probe = result.pump_probe
            self._update_pump_status(result.pump_probe)
            self._log_info(f"Pump controller discovered on {result.pump_probe.port}.")
        elif result.pump_error:
            self._log_warning(f"Pump controller scan completed with no usable device ({result.pump_error}).")
        else:
            self._log_warning("No pump controller discovered.")

        if result.spectrometer_name:
            if isinstance(self._spectrometer, SimulatedSpectrometer):
                self._log_info(result.spectrometer_name)
            else:
                self._log_success(result.spectrometer_name)
        else:
            self._log_warning("Spectrometer initialization produced no name.")

        self._initial_mswitch_devices = list(result.mswitch_devices)
        self._mswitch_probe = result.mswitch_devices[0] if result.mswitch_devices else None
        refresh_hw_device_status_strip(self)
        if self._mswitch_probe is not None:
            self._log_info(f"M-Switch discovered on {self._mswitch_probe.port}.")
        elif result.mswitch_error:
            self._log_warning(f"M-Switch scan failed: {result.mswitch_error}")
        else:
            self._log_warning("M-Switch not discovered at startup.")

        if result.valve_probe is not None:
            self._log_info(f"Valve controller discovered on {result.valve_probe.port}.")
        elif result.valve_error:
            self._log_warning(f"Valve controller scan failed: {result.valve_error}")
        else:
            self._log_warning("No valve controller discovered.")

        self._emit_hardware_init_progress(100, "Hardware initialization complete.")
        self._finish_hardware_initialization("Hardware initialization complete.")

    def _build_menu_bar(self) -> None:
        menu_bar = build_menu_bar(self)
        title_widget = build_title_bar(self, menu_bar, self._brand_icon_path)
        self._title_bar_widget = title_widget
        self.setMenuWidget(title_widget)
        refresh_hw_device_status_strip(self)
        sync_window_control_icons(self)

    def _update_window_mode_label(self) -> None:
        update_window_mode_label(self)

    def eventFilter(self, obj, event) -> bool:  # pragma: no cover - GUI runtime path
        if obj is getattr(self, "project_destination_edit", None):
            event_type = event.type()
            if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._choose_recording_project_destination()
                return True
        if obj is self._title_bar_widget:
            event_type = event.type()
            if event_type == QEvent.Type.MouseButtonDblClick and event.button() == Qt.MouseButton.LeftButton:
                self._toggle_window_max_restore()
                return True
            if event_type == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._title_bar_drag_active = True
                self._title_bar_drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                return True
            if event_type == QEvent.Type.MouseMove and self._title_bar_drag_active:
                if not self.isMaximized() and event.buttons() & Qt.MouseButton.LeftButton:
                    self.move(event.globalPosition().toPoint() - self._title_bar_drag_offset)
                return True
            if event_type in {QEvent.Type.MouseButtonRelease, QEvent.Type.Leave}:
                self._title_bar_drag_active = False
        return super().eventFilter(obj, event)

    def changeEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event.type() == QEvent.Type.WindowStateChange:
            sync_window_control_icons(self)
        super().changeEvent(event)

    def _show_quick_help_dialog(self) -> None:
        QMessageBox.information(
            self,
            "Quick help",
            "Status readouts:\n"
            "src = source acquisition rate\n"
            "disp = GUI display refresh rate\n"
            "proc = processing time per spectrum\n"
            "head = display-period / processing-time\n"
            "skip = dropped GUI updates per second\n"
            "acq = acquisition latency\n"
            "int = interval between source frames\n"
            "ovh = acquisition latency minus expected budget\n"
            "show = last displayed frame/window summary",
        )

    def _show_about_dialog(self) -> None:
        QMessageBox.about(
            self,
            "About LSPR Acquisition",
            f"LSPR Acquisition {__version__}\n"
            "Spectrometer acquisition and experiment control UI.\n"
            "This view is optimized for fast acquisition first, display second.",
        )

    def _show_shortcuts_dialog(self) -> None:
        QMessageBox.information(
            self,
            "Shortcuts",
            build_shortcuts_help_text(),
        )

    def _show_diagnostics_legend_dialog(self) -> None:
        QMessageBox.information(
            self,
            "Diagnostics legend",
            "Status strip:\n"
            "src = source acquisition rate\n"
            "disp = GUI display refresh rate\n"
            "proc = processing time per spectrum\n"
            "head = display-period / processing-time\n"
            "skip = dropped GUI updates per second\n\n"
            "Telemetry strip:\n"
            "acq = acquisition latency\n"
            "int = interval between source frames\n"
            "ovh = acquisition latency minus expected budget\n"
            "show = last displayed frame/window summary",
        )

    def _set_processing_debug_mode_enabled(self, enabled: bool) -> None:
        self._processing_debug_mode_enabled = bool(enabled)
        set_processing_debug_mode_enabled(self._processing_debug_mode_enabled)
        save_app_setting("processing_debug_mode", self._processing_debug_mode_enabled)
        if self._live_processing_worker is not None:
            self._live_processing_worker.update_debug_mode(self._processing_debug_mode_enabled)
        state_text = "enabled" if self._processing_debug_mode_enabled else "disabled"
        self.status_label.setText(f"Debug mode {state_text}.")
        self._log_info(f"Processing debug mode {state_text}.")
        self._request_deferred_ui_refresh(live_estimate=True)

    def _install_shortcuts(self) -> None:
        shortcuts = [
            (QKeySequence("F1"), self._show_quick_help_dialog),
            (QKeySequence("Ctrl+/"), self._show_shortcuts_dialog),
            (QKeySequence("Ctrl+L"), self._clear_trace_history),
            (QKeySequence("Ctrl+S"), self._save_processing_settings_dialog),
            (QKeySequence("Ctrl+O"), self._load_processing_settings_dialog),
            (QKeySequence("Ctrl+E"), self._export_current_plot),
            (QKeySequence("Ctrl+Space"), self._toggle_measurement_run),
            (QKeySequence("Ctrl+Left"), lambda: self._move_flow_step(-1)),
            (QKeySequence("Ctrl+Right"), lambda: self._move_flow_step(1)),
        ]
        self._shortcuts: list[QShortcut] = []
        for sequence, callback in shortcuts:
            shortcut = QShortcut(sequence, self)
            shortcut.activated.connect(callback)
            self._shortcuts.append(shortcut)

    def _move_flow_step(self, delta: int) -> None:
        self._open_experiment_control_window()
        if self._experiment_control_window is None:
            self.status_label.setText("Experiment control window is unavailable.")
            self._log_warning("Experiment control window is unavailable for step navigation.")
            return
        if hasattr(self._experiment_control_window, "_move_to_relative_experiment_control_step"):
            self._experiment_control_window._move_to_relative_experiment_control_step(delta)  # noqa: SLF001
            direction = "next" if delta > 0 else "previous"
            self.status_label.setText(f"Moved to {direction} pump-plan step.")
            self._log_info(f"Moved to {direction} pump-plan step.")

    def _call_experiment_control_window_action(self, method_name: str) -> bool:
        self._open_experiment_control_window()
        if self._experiment_control_window is None:
            self.status_label.setText("Experiment control window is unavailable.")
            self._log_warning("Experiment control window is unavailable.")
            return False
        method = getattr(self._experiment_control_window, method_name, None)
        if not callable(method):
            self.status_label.setText("Experiment control action is unavailable.")
            self._log_warning(f"Experiment control action '{method_name}' is unavailable.")
            return False
        method()  # noqa: SLF001
        return True

    def _toggle_flow_run_hold(self) -> None:
        if self._call_experiment_control_window_action("_toggle_plan_run_hold"):
            self._log_info("Experimental control plan run/hold toggled.")

    def _stop_experiment_plan(self) -> None:
        if self._call_experiment_control_window_action("_stop_plan"):
            self._log_info("Experiment plan stop requested.")

    def _stop_all_flow_channels(self) -> None:
        if self._call_experiment_control_window_action("_stop_all_channels"):
            self._log_info("Experimental control channel stop-all requested.")

    def _read_flow_status(self) -> None:
        if self._call_experiment_control_window_action("_read_live_status"):
            self._log_info("Experimental control status read requested.")

    def _request_deferred_ui_refresh(
        self,
        *,
        stats: bool = False,
        trace_plot: bool = False,
        summary: bool = False,
        telemetry: bool = False,
        live_estimate: bool = False,
        session_stats: bool = False,
        trace_label: str | None = None,
    ) -> None:
        if stats:
            self._ui_stats_dirty = True
        if trace_plot:
            self._ui_trace_plot_dirty = True
        if summary:
            self._ui_summary_dirty = True
        if telemetry:
            self._ui_telemetry_dirty = True
        if live_estimate:
            self._ui_live_estimate_dirty = True
        if session_stats or stats or trace_plot or summary or telemetry or live_estimate or trace_label is not None:
            self._ui_session_stats_dirty = True
        if trace_label is not None:
            self._pending_trace_label = trace_label
        delay_ms = self._live_ui_refresh_delay_ms if self._live_active else self._stats_refresh_delay_ms
        if self._live_active and (trace_plot or live_estimate or telemetry):
            delay_ms = max(delay_ms, 220)
        if not self._stats_refresh_timer.isActive():
            self._stats_refresh_timer.start(delay_ms)

    def _temporal_history_token(self, processed: Spectrum | None) -> tuple[object, ...] | None:
        if processed is None:
            return None
        settings = self._current_processing_settings()
        return (
            self.PLOT_MODES[self.plot_selector.currentText()],
            self._source_mode,
            len(processed.wavelengths_nm),
            float(processed.wavelengths_nm[0]) if len(processed.wavelengths_nm) else None,
            float(processed.wavelengths_nm[-1]) if len(processed.wavelengths_nm) else None,
            settings.temporal_smoothing,
            settings.crop_method,
            settings.crop_fraction,
            settings.fit_method,
            settings.fit_window_width_nm,
        )

    def _apply_temporal_smoothing(self, processed: Spectrum | None) -> Spectrum | None:
        if processed is None:
            self._temporal_processed_history.clear()
            self._temporal_history_key = None
            return None

        settings = self._current_processing_settings()
        window = max(int(getattr(settings, "temporal_smoothing", 1)), 1)
        key = self._temporal_history_token(processed)
        if key != self._temporal_history_key:
            self._temporal_processed_history.clear()
            self._temporal_history_key = key

        self._temporal_processed_history.append(processed)
        if len(self._temporal_processed_history) > window:
            self._temporal_processed_history = self._temporal_processed_history[-window:]
        if window <= 1 or len(self._temporal_processed_history) == 1:
            return processed

        stack = np.vstack([item.values for item in self._temporal_processed_history])
        averaged_values = np.nanmean(stack, axis=0)
        return Spectrum(
            wavelengths_nm=processed.wavelengths_nm.copy(),
            values=averaged_values,
            y_label=processed.y_label,
            acquired_at=processed.acquired_at,
            metadata={
                **processed.metadata,
                "temporal_smoothing": window,
                "temporal_average_count": len(self._temporal_processed_history),
            },
        )

    def _enqueue_plot_processing(self) -> None:
        if self._plots_frozen:
            return

        plot_mode = self.PLOT_MODES[self.plot_selector.currentText()]
        raw_spectrum = self._session.get_plot_data(plot_mode)
        settings = self._current_processing_settings()
        self._plot_processing_epoch += 1
        request = ProcessingRequest(
            spectrum=raw_spectrum,
            settings=settings,
            epoch=self._plot_processing_epoch,
        )
        if self._plot_processing_running:
            self._pending_plot_request = request
            return
        self._start_plot_processing_task(request)

    def _start_plot_processing_task(self, request: ProcessingRequest) -> None:
        self._plot_processing_running = True
        self._active_plot_processing_epoch = request.epoch
        task = ProcessingTask(request)
        task.signals.finished.connect(self._handle_plot_processing_result)
        self._thread_pool.start(task)

    def _handle_plot_processing_result(self, result: ProcessingResult) -> None:
        if self._closing:
            self._plot_processing_running = False
            self._pending_plot_request = None
            return

        self._plot_processing_running = False
        if result.epoch != self._active_plot_processing_epoch:
            if self._pending_plot_request is not None:
                pending = self._pending_plot_request
                self._pending_plot_request = None
                self._start_plot_processing_task(pending)
            return

        processed = self._apply_temporal_smoothing(result.processed)
        fit = result.fit
        if processed is not None and fit is not None and processed is not result.processed:
            fit = fit_processed_spectrum(processed, self._current_processing_settings())
        self._last_processing_ms = result.processing_ms
        if result.processing_ms > 0:
            self._processing_rate_hz = 1000.0 / result.processing_ms
            display_period_ms = max(1000.0 / max(self.live_rate_spin.value(), 1e-9), 1.0)
            self._processing_headroom_ratio = display_period_ms / result.processing_ms
        else:
            self._processing_rate_hz = None
            self._processing_headroom_ratio = None
        self._last_processed_plot = processed
        self._last_fit_plot = fit
        self._processed_cache_key = None
        self._processed_cache_result = (processed, fit)
        self._analysis_cache_key = None
        self._analysis_cache_result = (
            np.empty(0, dtype=np.float64),
            np.empty(0, dtype=np.float64),
            {},
        )
        self._analysis_metrics_cache_key = None
        self._analysis_metrics_cache_result = {}
        self._update_poly_warning_indicator(fit)
        self._plot_render_dirty = True
        if not self._plot_refresh_timer.isActive():
            self._plot_refresh_timer.start()
        self._log_throttled(
            "plot_refresh",
            f"Plot refreshed | mode={self.plot_selector.currentText().lower()} | fit={'on' if fit is not None else 'off'}",
            level=logging.DEBUG,
            min_interval=0.75,
        )

        self._request_deferred_ui_refresh(trace_plot=True, summary=True, stats=True, trace_label="Peak position (nm)")
        if self._pending_plot_request is not None:
            pending = self._pending_plot_request
            self._pending_plot_request = None
            self._start_plot_processing_task(pending)

    def _flush_deferred_ui_refreshes(self) -> None:
        flush_deferred_ui_refreshes_for(self)

    def _flush_plot_refreshes(self) -> None:
        flush_plot_refreshes_for(self)

    def _refresh_plot(self) -> None:
        refresh_plot_for(self)

    def _handle_residual_toggle(self, visible: bool) -> None:
        handle_residual_toggle_for(self, visible)

    def _update_poly_warning_indicator(self, fit: Spectrum | None) -> None:
        update_poly_warning_indicator_for(self, fit)

    def _set_plots_frozen(self, frozen: bool) -> None:
        set_plots_frozen_for(self, frozen)

    def _set_sensorgram_frozen(self, frozen: bool) -> None:
        set_sensorgram_frozen_for(self, frozen)

    def _clear_trace_history(self) -> None:
        clear_trace_history_for(self)

    def _autoscale_spectrum_plot(self) -> None:
        autoscale_spectrum_plot_for(self)

    def _autoscale_trace_plot(self) -> None:
        self._trace_view_locked = False
        autoscale_trace_plot_for(self)

    def _update_residual_view_geometry(self) -> None:
        update_residual_view_geometry_for(self)

    def _autoscale_residual_axis(self) -> None:
        autoscale_residual_axis_for(self)

    def _update_residual_axis_visibility(self, visible: bool | None = None) -> None:
        update_residual_axis_visibility_for(self, visible)

    def _request_trace_autoscale(self) -> None:
        request_trace_autoscale_for(self)

    def _handle_spectrum_mouse_moved(self, event) -> None:
        handle_spectrum_mouse_moved_for(self, event)

    def _handle_trace_mouse_moved(self, event) -> None:
        handle_trace_mouse_moved_for(self, event)

    def _refresh_spectrum_plot(self, processed: Spectrum | None, fit: Spectrum | None) -> None:
        if self._plots_frozen:
            return
        view_x_min = view_x_max = view_width_px = None
        try:
            view_box = self.spectrum_plot.getPlotItem().vb
            view_range = view_box.viewRange()
            x_range = view_range[0]
            view_x_min = float(x_range[0])
            view_x_max = float(x_range[1])
            scene_rect = view_box.sceneBoundingRect()
            if scene_rect is not None:
                view_width_px = float(scene_rect.width())
                if not np.isfinite(view_width_px):
                    view_width_px = None
        except Exception:
            view_x_min = view_x_max = view_width_px = None

        residual_visible = bool(fit is not None and self.show_residual_button.isChecked())
        show_gaussian = self._needs_gaussian_metric()
        render_key = spectrum_render_cache_key(
            processed,
            fit,
            view_x_min=view_x_min,
            view_x_max=view_x_max,
            view_width_px=view_width_px,
            residual_visible=residual_visible,
            show_gaussian=show_gaussian,
            peak_tracking_mode=self._current_processing_settings().peak_tracking_mode,
        )
        if render_key == self._spectrum_render_cache_key:
            return
        self._spectrum_render_cache_key = render_key
        self._visible_processed_plot = processed
        self._visible_fit_plot = fit
        if processed is None:
            self.spectrum_curve.setData([], [])
            self.fit_curve.setData([], [])
            self.residual_curve.setData([], [])
            self.max_marker.setData([], [])
            self.poly_marker.setData([], [])
            self.gaussian_marker.setData([], [])
            self.centroid_marker.setData([], [])
            self.spectrum_plot.setLabel("left", "Signal")
            self._update_residual_axis_visibility(False)
            return

        low = float(np.min(processed.wavelengths_nm))
        high = float(np.max(processed.wavelengths_nm))
        self.processing_region_item.setRegion((low, high))

        display_x, display_y = downsample_spectrum_series_for_view(
            np.asarray(processed.wavelengths_nm, dtype=np.float64),
            np.asarray(processed.values, dtype=np.float64),
            view_x_min=view_x_min,
            view_x_max=view_x_max,
            view_width_px=view_width_px,
        )
        self.spectrum_curve.setData(display_x, display_y)
        if fit is not None:
            fit_values = np.asarray(fit.values, dtype=np.float64)
            fit_low = float(fit.metadata.get("fit_window_min_nm", np.min(fit.wavelengths_nm)))
            fit_high = float(fit.metadata.get("fit_window_max_nm", np.max(fit.wavelengths_nm)))
            display_fit_x, _ = clip_series_to_window(
                display_x,
                display_y,
                window_min=fit_low,
                window_max=fit_high,
            )
            if len(display_fit_x) > 0 and len(fit.wavelengths_nm) > 0:
                display_fit_y = np.interp(
                    display_fit_x,
                    np.asarray(fit.wavelengths_nm, dtype=np.float64),
                    fit_values,
                )
            else:
                display_fit_x = np.empty(0, dtype=np.float64)
                display_fit_y = fit_values[:0]
            self.fit_curve.setData(display_fit_x, display_fit_y)
            if residual_visible:
                residual_base = np.interp(
                    display_fit_x,
                    np.asarray(processed.wavelengths_nm, dtype=np.float64),
                    np.asarray(processed.values, dtype=np.float64),
                )
                residual_values = residual_base - display_fit_y
                self.residual_curve.setData(display_fit_x, residual_values)
                self._autoscale_residual_axis()
            else:
                self.residual_curve.setData([], [])
            self.fit_region_item.setRegion((fit_low, fit_high))
        else:
            self.fit_curve.setData([], [])
            self.residual_curve.setData([], [])
            self.fit_region_item.setRegion((low, low))
        self._update_residual_axis_visibility(residual_visible)
        self.spectrum_plot.setLabel("left", processed.y_label)
        max_index = int(np.nanargmax(processed.values))
        max_x = float(processed.wavelengths_nm[max_index])
        max_y = float(processed.values[max_index])
        poly_x = self._compute_metric_nm("poly_max", processed, fit)
        if fit is not None and len(fit.wavelengths_nm) > 0:
            poly_y = float(np.interp(poly_x, fit.wavelengths_nm, fit.values))
        else:
            poly_y = float(np.interp(poly_x, processed.wavelengths_nm, processed.values))
        show_gaussian = self._needs_gaussian_metric()
        if show_gaussian:
            gaussian_x = self._compute_metric_nm("gaussian_center", processed, fit)
            if fit is not None and fit.metadata.get("fit_method") == "gaussian" and len(fit.wavelengths_nm) > 0:
                gaussian_y = float(np.interp(gaussian_x, fit.wavelengths_nm, fit.values))
            else:
                gaussian_y = float(np.interp(gaussian_x, processed.wavelengths_nm, processed.values))
            if np.isfinite(gaussian_x) and np.isfinite(gaussian_y):
                self.gaussian_marker.setData([{"pos": (float(gaussian_x), float(gaussian_y)), "data": "gaussian"}])
                self.gaussian_marker.show()
            else:
                self.gaussian_marker.setData([], [])
                self.gaussian_marker.hide()
        else:
            self.gaussian_marker.setData([], [])
            self.gaussian_marker.hide()
        centroid_x = self._compute_centroid_nm(processed, fit)
        centroid_y = float(np.interp(centroid_x, processed.wavelengths_nm, processed.values))
        self._set_scatter_marker(self.max_marker, max_x, max_y, "max")
        self._set_scatter_marker(self.poly_marker, poly_x, poly_y, "poly")
        self._set_scatter_marker(self.centroid_marker, centroid_x, centroid_y, "centroid")
        self._log_throttled(
            "spectrum_refresh",
            f"Spectrum updated | points={len(processed.wavelengths_nm)} | fit={'yes' if fit is not None else 'no'}",
            level=logging.DEBUG,
            min_interval=1.5,
        )

    def _set_scatter_marker(self, marker: pg.ScatterPlotItem, x: float, y: float, label: str) -> None:
        if not (np.isfinite(x) and np.isfinite(y)):
            marker.setData([], [])
            return
        marker.setData([{"pos": (float(x), float(y)), "data": label}])

    def _refresh_trace_plot(self, trace_label: str) -> None:
        refresh_trace_plot_for(self, trace_label)

    def _render_trace_series(
        self,
        history: dict[str, list[tuple[object, float]]],
        clock_mode: bool,
    ) -> None:
        render_trace_series_for(self, history, clock_mode)

    def _handle_trace_view_range_changed(self, *_args) -> None:
        if self._plots_frozen or self._trace_view_autoscaling:
            return
        self._trace_view_locked = True

    def _primary_trace_metric(self) -> str:
        return primary_trace_metric(self)

    def _trace_stats_metric(self) -> str:
        selected = self._selected_trace_metrics()
        if not selected:
            return "smoothed_max"
        current = self._trace_stats_metric_name
        if current in selected:
            return current
        primary = self._primary_trace_metric()
        if primary in selected:
            self._trace_stats_metric_name = primary
            return primary
        self._trace_stats_metric_name = selected[0]
        return selected[0]

    def _cycle_trace_stats_metric(self) -> None:
        selected = self._selected_trace_metrics()
        if not selected:
            return
        current = self._trace_stats_metric()
        try:
            index = selected.index(current)
        except ValueError:
            index = -1
        next_metric = selected[(index + 1) % len(selected)]
        self._trace_stats_metric_name = next_metric
        self._update_trace_stats()
        self._schedule_acquisition_state_persist()

    def _handle_trace_stats_label_click(self, event) -> None:  # pragma: no cover - GUI runtime path
        if event is not None and hasattr(event, "button") and event.button() != Qt.MouseButton.LeftButton:
            return
        self._cycle_trace_stats_metric()
        if event is not None and hasattr(event, "accept"):
            event.accept()

    def _normalize_sensorgram_view_mode(self, mode: object) -> str:
        normalized = str(mode or "").strip().lower()
        return normalized if normalized in {"absolute", "rolling"} else "absolute"

    def _normalize_sensorgram_downsampling_enabled(self, value: object | None = None) -> bool:
        current = self._sensorgram_downsampling_enabled if value is None else value
        if isinstance(current, str):
            normalized = current.strip().lower()
            return normalized not in {"0", "false", "no", "off"}
        return bool(current)

    def _normalize_sensorgram_display_window_s(self, value: object | None = None) -> float:
        allowed_values = (60.0, 600.0, 1800.0, 3600.0)
        current = self._trace_display_window_s if value is None else value
        try:
            seconds = float(current)
        except (TypeError, ValueError):
            return allowed_values[0]
        if not np.isfinite(seconds) or seconds <= 0:
            return allowed_values[0]
        return min(allowed_values, key=lambda candidate: abs(candidate - seconds))

    def _sensorgram_view_mode_label(self, mode: str | None = None) -> str:
        normalized = self._normalize_sensorgram_view_mode(mode or self._sensorgram_view_mode)
        return "Absolute" if normalized == "absolute" else "Rolling"

    def _sensorgram_view_mode_tooltip(self, mode: str | None = None) -> str:
        normalized = self._normalize_sensorgram_view_mode(mode or self._sensorgram_view_mode)
        if normalized == "absolute":
            return "Current display: Absolute. Click to switch to Rolling and show only the most recent sensorgram window."
        return "Current display: Rolling. Click to switch to Absolute and show the full sensorgram history from the start of recording."

    def _update_sensorgram_view_mode_button(self) -> None:
        if not hasattr(self, "sensorgram_view_mode_button"):
            return
        label = self._sensorgram_view_mode_label()
        self.sensorgram_view_mode_button.setText(f"[{label}]")
        self.sensorgram_view_mode_button.setToolTip(self._sensorgram_view_mode_tooltip())
        self._update_sensorgram_header_control_visibility()

    def _update_sensorgram_header_control_visibility(self) -> None:
        view_mode = self._normalize_sensorgram_view_mode(self._sensorgram_view_mode)
        if hasattr(self, "sensorgram_downsampling_button"):
            self.sensorgram_downsampling_button.setVisible(view_mode == "absolute")
        if hasattr(self, "sensorgram_window_button"):
            self.sensorgram_window_button.setVisible(view_mode == "rolling")

    def _apply_sensorgram_view_mode(self, *, save: bool = False) -> None:
        self._sensorgram_view_mode = self._normalize_sensorgram_view_mode(self._sensorgram_view_mode)
        self._trace_view_locked = False
        self._update_sensorgram_view_mode_button()
        self._update_sensorgram_downsampling_button()
        self._update_sensorgram_display_window_button()
        self._request_trace_autoscale()
        self._request_deferred_ui_refresh(trace_plot=True, stats=True)
        if save:
            self._schedule_acquisition_state_persist()

    def _cycle_sensorgram_view_mode(self) -> None:
        current = self._normalize_sensorgram_view_mode(self._sensorgram_view_mode)
        self._sensorgram_view_mode = "rolling" if current == "absolute" else "absolute"
        self._apply_sensorgram_view_mode(save=True)

    def _sensorgram_downsampling_label(self, enabled: bool | None = None) -> str:
        current = self._normalize_sensorgram_downsampling_enabled(enabled)
        return "Downsampling: On" if current else "Downsampling: Off"

    def _sensorgram_downsampling_tooltip(self, enabled: bool | None = None) -> str:
        current = self._normalize_sensorgram_downsampling_enabled(enabled)
        if current:
            return "Current display: Downsampling is enabled. Click to disable resolution-based reduction."
        return "Current display: Downsampling is disabled. Click to enable resolution-based reduction."

    def _update_sensorgram_downsampling_button(self) -> None:
        if not hasattr(self, "sensorgram_downsampling_button"):
            return
        label = self._sensorgram_downsampling_label()
        self.sensorgram_downsampling_button.setText(f"[{label}]")
        self.sensorgram_downsampling_button.setToolTip(self._sensorgram_downsampling_tooltip())
        self._update_sensorgram_header_control_visibility()

    def _cycle_sensorgram_downsampling_enabled(self) -> None:
        self._sensorgram_downsampling_enabled = not self._normalize_sensorgram_downsampling_enabled()
        self._update_sensorgram_downsampling_button()
        self._request_trace_autoscale()
        self._request_deferred_ui_refresh(trace_plot=True, stats=True)
        self._schedule_acquisition_state_persist()

    def _sensorgram_display_window_label(self, seconds: float | None = None) -> str:
        current = self._normalize_sensorgram_display_window_s(seconds)
        if current >= 3600.0:
            return "Window: 1 h"
        if current >= 1800.0:
            return "Window: 30 min"
        if current >= 600.0:
            return "Window: 10 min"
        return "Window: 1 min"

    def _sensorgram_display_window_tooltip(self, seconds: float | None = None) -> str:
        current = self._normalize_sensorgram_display_window_s(seconds)
        current_label = self._sensorgram_display_window_label(current).replace("Window: ", "")
        if current >= 3600.0:
            next_label = "1 min"
        elif current >= 1800.0:
            next_label = "1 h"
        elif current >= 600.0:
            next_label = "30 min"
        else:
            next_label = "10 min"
        return f"Current rolling window: {current_label}. Click to cycle to {next_label}."

    def _update_sensorgram_display_window_button(self) -> None:
        if not hasattr(self, "sensorgram_window_button"):
            return
        label = self._sensorgram_display_window_label()
        self.sensorgram_window_button.setText(f"[{label}]")
        self.sensorgram_window_button.setToolTip(self._sensorgram_display_window_tooltip())
        self._update_sensorgram_header_control_visibility()

    def _cycle_sensorgram_display_window(self) -> None:
        current = self._normalize_sensorgram_display_window_s()
        window_values = (60.0, 600.0, 1800.0, 3600.0)
        try:
            index = window_values.index(current)
        except ValueError:
            index = 0
        self._trace_display_window_s = window_values[(index + 1) % len(window_values)]
        self._update_sensorgram_display_window_button()
        self._request_trace_autoscale()
        self._request_deferred_ui_refresh(trace_plot=True, stats=True)
        self._schedule_acquisition_state_persist()

    def _normalize_sensorgram_content_mode(self, mode: object) -> str:
        normalized = str(mode or "").strip().lower()
        return normalized if normalized in {"metric", "heatmap"} else "metric"

    def _sensorgram_content_mode_label(self, mode: str | None = None) -> str:
        normalized = self._normalize_sensorgram_content_mode(mode or self._sensorgram_content_mode)
        return "Metric time plot" if normalized == "metric" else "Extinction heatmap"

    def _sensorgram_content_mode_tooltip(self, mode: str | None = None) -> str:
        normalized = self._normalize_sensorgram_content_mode(mode or self._sensorgram_content_mode)
        if normalized == "metric":
            return (
                "Current display: Metric time plot. Click to switch to Extinction heatmap. "
                "The plot will change from time-tracked metrics to a wavelength-vs-time heatmap."
            )
        return (
            "Current display: Extinction heatmap. Click to switch to Metric time plot. "
            "The plot will change from a heatmap back to time-tracked metrics."
        )

    def _update_sensorgram_content_mode_button(self) -> None:
        if not hasattr(self, "sensorgram_content_mode_button"):
            return
        label = self._sensorgram_content_mode_label()
        self.sensorgram_content_mode_button.setText(f"[{label}]")
        self.sensorgram_content_mode_button.setToolTip(self._sensorgram_content_mode_tooltip())

    def _apply_sensorgram_content_mode(self, *, save: bool = False) -> None:
        self._sensorgram_content_mode = self._normalize_sensorgram_content_mode(self._sensorgram_content_mode)
        self._trace_view_locked = False
        self._update_sensorgram_content_mode_button()
        self._apply_sensorgram_display_style()
        self._request_trace_autoscale()
        self._request_deferred_ui_refresh(trace_plot=True, stats=True)
        if save:
            self._schedule_acquisition_state_persist()

    def _cycle_sensorgram_content_mode(self) -> None:
        current = self._normalize_sensorgram_content_mode(self._sensorgram_content_mode)
        self._sensorgram_content_mode = "heatmap" if current == "metric" else "metric"
        self._apply_sensorgram_content_mode(save=True)

    def _append_sensorgram_heatmap_history(self, spectrum: Spectrum | None) -> None:
        if spectrum is None or len(spectrum.wavelengths_nm) == 0 or len(spectrum.values) == 0:
            return
        if self._measurement_active and self._measurement_started_at is not None:
            time_value = max((spectrum.acquired_at - self._measurement_started_at).total_seconds(), 0.0)
        else:
            time_value = float(spectrum.acquired_at.timestamp())
        wavelengths = np.asarray(spectrum.wavelengths_nm, dtype=np.float64)
        values = np.asarray(spectrum.values, dtype=np.float64)
        if (
            self._sensorgram_heatmap_wavelengths is None
            or self._sensorgram_heatmap_wavelengths.shape != wavelengths.shape
            or not np.allclose(self._sensorgram_heatmap_wavelengths, wavelengths)
        ):
            self._sensorgram_heatmap_wavelengths = wavelengths.copy()
            self._sensorgram_heatmap_history.clear()
        self._sensorgram_heatmap_history.append((float(time_value), values.copy()))
        trim_history_tail_in_place(
            self._sensorgram_heatmap_history,
            int(getattr(self, "_sensorgram_heatmap_history_max_rows", 2000)),
        )

    def _active_trace_series(self) -> dict[str, tuple[np.ndarray, np.ndarray]]:
        if self._peak_history:
            return {
                name: (
                    np.asarray([item[0] for item in series], dtype=np.float64),
                    np.asarray([item[1] for item in series], dtype=np.float64),
                )
                for name, series in self._peak_history.items()
                if name in self._selected_trace_metrics() and series
            }
        return {}

    def _update_spectrum_stats(self, processed: Spectrum | None, fit: Spectrum | None) -> None:
        update_spectrum_stats_for(self, processed, fit)

    def _update_trace_stats(self) -> None:
        update_trace_stats_for(self)

    def _compute_centroid_nm(self, processed: Spectrum, fit: Spectrum | None) -> float:
        return compute_centroid_nm_for(self, processed, fit)

    def _reference_peak_nm_for_shift(self) -> float | None:
        reference = self._peak_reference_processed
        if reference is None:
            return None
        return float(reference.wavelengths_nm[int(np.nanargmax(reference.values))])

    def _build_summary_text(self) -> str:
        return build_summary_text_for(self)

    def _describe_spectrum(self, spectrum: Spectrum | None) -> str:
        return describe_spectrum_for(self, spectrum)

    def _update_live_estimate(self) -> None:
        update_live_estimate_for(self)

    def _refresh_telemetry(self) -> None:
        refresh_telemetry_for(self)

    def _live_skip_rate_hz(self) -> float:
        return live_skip_rate_hz_for(self)

    def _headroom_value_text(self, headroom_ratio: float | None) -> str:
        return headroom_value_text_for(headroom_ratio)

    def _handle_live_setting_change(self) -> None:
        handle_live_setting_change_for(self)

    def _handle_simulation_output_rate_change(self) -> None:
        handle_simulation_output_rate_change_for(self)

    def _request_manual_acquisition(self, kind: str) -> None:
        request_manual_acquisition(self, kind)

    def _handle_processing_setting_change(self) -> None:
        if self._suspend_processing_autosave:
            return
        self._persist_processing_settings()
        if self._live_processing_worker is not None:
            self._live_processing_worker.update_settings(self._current_processing_settings())
        self._schedule_processing_refresh()
        self._request_deferred_ui_refresh(summary=True)
        self._log_throttled(
            "processing_settings",
            "Processing settings updated.",
            level=logging.DEBUG,
            min_interval=1.0,
        )

    def _schedule_processing_refresh(self) -> None:
        schedule_processing_refresh(self)

    def _start_live_acquisition(self) -> None:
        start_live_acquisition(self)

    def _stop_live_acquisition(self, message: str = "Live acquisition stopped.") -> None:
        stop_live_acquisition(self, message)

    def _make_icon_button(self, icon: QIcon, tooltip: str) -> QToolButton:
        button = QToolButton()
        button.setIcon(icon)
        button.setToolTip(tooltip)
        button.setFixedSize(34, 34)
        button.setIconSize(QSize(20, 20))
        button.setAutoRaise(True)
        return button

    def _make_frameless_icon_button(self, icon: QIcon, tooltip: str, *, size: int = 28) -> QToolButton:
        button = QToolButton()
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
            "QToolButton#framelessIconButton:pressed { background: rgba(127, 127, 127, 0.18); border: none; }"
        )
        return button

    def _toggle_window_max_restore(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()
        sync_window_control_icons(self)

    def _update_freeze_button_icon(self) -> None:
        if not hasattr(self, "freeze_plots_button"):
            return
        self.freeze_plots_button.setIcon(snowflake_icon(self._theme_mode, self._plots_frozen))

    def _update_sensorgram_freeze_button_icon(self) -> None:
        if not hasattr(self, "sensorgram_freeze_button"):
            return
        self.sensorgram_freeze_button.setIcon(snowflake_icon(self._theme_mode, self._sensorgram_frozen))

    def _update_residual_button_icon(self) -> None:
        if not hasattr(self, "show_residual_button"):
            return
        self.show_residual_button.setIcon(residual_icon(self.show_residual_button.isChecked()))

    def _update_dark_reference_button_icons(self) -> None:
        if hasattr(self, "acquire_dark_button"):
            self.acquire_dark_button.setIcon(dark_icon(self._session.state.dark is not None))
        if hasattr(self, "acquire_reference_button"):
            self.acquire_reference_button.setIcon(reference_icon(self._session.state.reference is not None))

    def _export_current_plot(self) -> None:
        plot_mode = self.PLOT_MODES[self.plot_selector.currentText()]
        spectrum = self._last_processed_plot
        if spectrum is None:
            self.status_label.setText("There is no plotted data to export.")
            return

        default_name = f"{plot_mode}_{spectrum.acquired_at.strftime('%Y%m%d_%H%M%S')}.png"
        default_path = Path.cwd() / "data" / default_name
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export graph",
            str(default_path),
            "PNG image (*.png);;SVG vector (*.svg);;CSV data (*.csv)",
        )
        if not file_path:
            self.status_label.setText("Export cancelled.")
            return

        destination = Path(file_path)
        suffix = destination.suffix.lower()
        if suffix == ".csv":
            export_spectrum_to_csv(destination, spectrum)
            self.status_label.setText(f"Exported {plot_mode} data to {file_path}")
            return

        if suffix not in {".png", ".svg"}:
            destination = destination.with_suffix(".png")
            suffix = ".png"

        if suffix == ".png":
            exporter = pg_exporters.ImageExporter(self.spectrum_plot.plotItem)
            exporter.parameters()["width"] = 1600
            exporter.export(str(destination))
        else:
            exporter = pg_exporters.SVGExporter(self.spectrum_plot.plotItem)
            exporter.export(str(destination))
        self.status_label.setText(f"Exported {plot_mode} graph to {destination}")

    def _show_error(self, message: str) -> None:
        self.status_label.setText(message)
        if self._closing:
            return
        self._log_error(message)
        QMessageBox.critical(self, "Acquisition error", message)

    def closeEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        self._closing = True
        self._log_info("Closing application.")
        self._acquisition_state_timer.stop()
        self._persist_acquisition_state()
        self._save_ui_state()
        if self._experiment_control_window is not None:
            try:
                self._experiment_control_window.shutdown_devices()
            except Exception as exc:
                self._log_warning(f"Experimental control device shutdown failed: {exc}")
            self._experiment_control_window.save_ui_state()
            self._experiment_control_window.close()
        self._pending_manual_kind = None
        self._pending_source_mode = None
        self._pending_auto_integration = False
        self._resume_live_after_manual = False
        self._resume_live_after_source_switch = False
        self._resume_live_after_auto_integration = False
        self._live_active = False
        self._simulation_refresh_timer.stop()
        self._plot_refresh_timer.stop()
        self._stats_refresh_timer.stop()
        self._processing_refresh_timer.stop()
        self._live_stop_event.set()
        if self._live_worker is not None and self._live_worker.is_alive():
            try:
                self._live_worker.stop()
                self._live_worker.join(timeout=2.0)
                if self._live_worker.is_alive():
                    self._log_warning("Live acquisition worker did not exit cleanly; terminating it.")
                    self._live_worker.terminate()
                    self._live_worker.join(timeout=1.0)
            except Exception as exc:
                self._log_warning(f"Live acquisition worker shutdown failed: {exc}")
        self._live_worker = None
        if self._live_processing_worker is not None:
            try:
                self._live_processing_worker.stop()
                self._live_processing_worker.join(timeout=2.0)
                if self._live_processing_worker.is_alive():
                    self._log_warning("Live processing worker did not exit cleanly; terminating it.")
                    self._live_processing_worker.terminate()
                    self._live_processing_worker.join(timeout=1.0)
            except Exception as exc:
                self._log_warning(f"Live processing worker shutdown failed: {exc}")
        self._live_processing_worker = None
        self._live_result_timer.stop()
        self._reset_live_accumulator()
        if self._measurement_active:
            self._stop_measurement_run()
        elif self._measurement_writer is not None:
            self._flush_measurement_frames(force=True)
            self._measurement_writer.close()
            self._measurement_writer = None
        self._busy = False
        try:
            self._thread_pool.waitForDone(3000)
        except TypeError:
            self._thread_pool.waitForDone()
        try:
            self._ui_logger.removeHandler(self._log_handler)
        except Exception:
            pass
        super().closeEvent(event)
        app = QApplication.instance()
        if app is not None:
            app.quit()



