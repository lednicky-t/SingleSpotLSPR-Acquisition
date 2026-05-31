from __future__ import annotations

import logging
import ctypes
import os
import socket
import sys
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from time import perf_counter

from PyQt6.QtCore import QObject, QEasingCurve, QLockFile, QPropertyAnimation, QStandardPaths, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QGuiApplication, QIcon, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QStyleOptionProgressBar,
    QVBoxLayout,
    QWidget,
)

from lspr_ui import app_icon, apply_base_app_theme
from lspr_app.storage.app_config import load_app_setting, save_app_setting

from lspr_app import __version__
from lspr_app.gui.main_window_logging import build_recording_experiment_log_path
from lspr_core import (
    DEFAULT_LAUNCH_PROFILE,
    LAUNCH_PROFILE_ENV_VAR,
    launch_profile_spec,
    normalize_launch_profile,
)


def _brand_logo_path() -> Path:
    return Path(__file__).resolve().parent / "resources" / "icons" / "app_icon.svg"


def _lock_file_path() -> Path:
    app_data = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    base = Path(app_data) if app_data else (Path.cwd() / ".appdata")
    base.mkdir(parents=True, exist_ok=True)
    return base / "lspr_acquisition.lock"


def _process_is_running(pid: int) -> bool:
    if pid <= 0 or os.name != "nt":
        return False

    kernel32 = ctypes.windll.kernel32
    process_query_limited_information = 0x1000
    synchronize = 0x00100000
    handle = kernel32.OpenProcess(process_query_limited_information | synchronize, False, pid)
    if handle:
        kernel32.CloseHandle(handle)
        return True

    return kernel32.GetLastError() == 5


def _recover_stale_lock(lock: QLockFile) -> bool:
    found, pid, host, _app_name = lock.getLockInfo()
    if not found or pid <= 0:
        return False
    if host and host.lower() != socket.gethostname().lower():
        return False
    if _process_is_running(pid):
        return False

    try:
        lock.removeStaleLockFile()
    except Exception:
        pass

    if lock.tryLock(0):
        return True

    try:
        Path(lock.fileName()).unlink(missing_ok=True)
    except OSError:
        pass

    return lock.tryLock(0)


class StartupProgressBar(QProgressBar):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._glow_phase = 0.0
        self._glow_timer = QTimer(self)
        self._glow_timer.setInterval(20)
        self._glow_timer.timeout.connect(self._advance_glow)
        self._glow_timer.start()

    def _advance_glow(self) -> None:
        self._glow_phase = (self._glow_phase + 0.018) % 1.0
        self.update()

    def paintEvent(self, event) -> None:  # pragma: no cover - GUI runtime path
        option = QStyleOptionProgressBar()
        self.initStyleOption(option)
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

            rect = self.rect().adjusted(0, 0, -1, -1)
            radius = 8.0
            bg = QColor("#1f242b")
            border = QColor("#2f353d")
            fill_left = QColor("#7a5cff")
            fill_mid1 = QColor("#4f88ff")
            fill_mid2 = QColor("#39c7ba")
            fill_mid3 = QColor("#e8d85f")
            fill_right = QColor("#ef6b58")

            painter.setPen(QPen(border, 1))
            painter.setBrush(bg)
            painter.drawRoundedRect(rect, radius, radius)

            value = max(min(int(option.progress), int(option.maximum)), int(option.minimum))
            span = max(option.maximum - option.minimum, 1)
            fill_width = int(round(rect.width() * (value - option.minimum) / span))
            fill_rect = rect.adjusted(1, 1, 0, -1)
            fill_rect.setWidth(max(fill_width - 1, 0))
            if fill_rect.width() > 0:
                painter.setPen(Qt.PenStyle.NoPen)
                fill = QLinearGradient(fill_rect.left(), fill_rect.top(), fill_rect.right(), fill_rect.top())
                fill.setColorAt(0.0, fill_left)
                fill.setColorAt(0.22, fill_mid1)
                fill.setColorAt(0.45, fill_mid2)
                fill.setColorAt(0.72, fill_mid3)
                fill.setColorAt(1.0, fill_right)
                painter.setBrush(fill)
                painter.drawRoundedRect(fill_rect, radius, radius)

                glow_width = max(int(fill_rect.width() * 0.42), 92)
                travel_width = fill_rect.width() + glow_width
                glow_center = fill_rect.left() + int(travel_width * self._glow_phase)
                glow_left = int(glow_center - glow_width / 2)
                glow_rect = fill_rect.adjusted(0, 0, 0, 0)
                glow_rect.setLeft(glow_left)
                glow_rect.setWidth(glow_width)
                if glow_rect.right() >= fill_rect.left() and glow_rect.left() <= fill_rect.right():
                    visible_left = max(glow_rect.left(), rect.left())
                    visible_right = min(glow_rect.right(), fill_rect.right())
                    if visible_right > visible_left:
                        shine = QLinearGradient(visible_left, glow_rect.top(), visible_right, glow_rect.top())
                        shine.setColorAt(0.0, QColor(255, 255, 255, 0))
                        shine.setColorAt(0.36, QColor(255, 255, 255, 18))
                        shine.setColorAt(0.50, QColor(255, 255, 255, 130))
                        shine.setColorAt(0.64, QColor(255, 255, 255, 22))
                        shine.setColorAt(1.0, QColor(255, 255, 255, 0))
                        painter.save()
                        painter.setClipRect(fill_rect)
                        painter.setBrush(shine)
                        painter.drawRoundedRect(fill_rect, radius, radius)
                        painter.restore()
        finally:
            painter.end()


class StartupSplash(QWidget):
    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint,
        )
        self.setObjectName("startupSplash")
        self.setWindowTitle("Starting LSPR Acquisition")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)

        accent = QFrame()
        accent.setObjectName("startupAccent")
        accent.setFixedHeight(4)
        icon_label = QLabel()
        icon_label.setObjectName("startupIcon")
        icon_label.setFixedSize(108, 108)
        icon_label.setPixmap(QIcon(str(_brand_logo_path())).pixmap(92, 92))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_opacity_effect = QGraphicsOpacityEffect(self)
        self._icon_opacity_effect.setOpacity(0.25)
        icon_label.setGraphicsEffect(self._icon_opacity_effect)
        self._icon_opacity_anim = QPropertyAnimation(self._icon_opacity_effect, b"opacity", self)
        self._icon_opacity_anim.setDuration(220)
        self._icon_opacity_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        title = QLabel("LSPR Acquisition")
        title.setObjectName("startupTitle")
        title.setContentsMargins(0, 0, 0, 4)

        version_label = QLabel(f"ver. {__version__}")
        version_label.setObjectName("startupVersion")
        version_label.setContentsMargins(0, 0, 0, 4)

        subtitle = QLabel("Spectroscopy and experiment control are waking up.")
        subtitle.setObjectName("startupSubtitle")
        self._status_label = QLabel("Starting...")
        self._status_label.setObjectName("startupStatus")
        self._progress = StartupProgressBar()
        self._progress.setRange(0, 100)
        self._progress.setValue(0)
        self._progress.setTextVisible(False)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(accent)

        body = QWidget()
        content_row = QWidget()
        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(3)

        title_row = QWidget()
        title_row_layout = QHBoxLayout()
        title_row_layout.setContentsMargins(0, 0, 0, 0)
        title_row_layout.setSpacing(8)
        title_row_layout.addWidget(title, 1)
        title_row_layout.addWidget(version_label, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        title_row.setLayout(title_row_layout)

        content_layout.addWidget(title_row)
        content_layout.addWidget(subtitle)
        content_layout.addWidget(self._status_label)
        content_layout.addWidget(self._progress)
        content_row.setLayout(content_layout)

        header_row = QWidget()
        header_layout = QVBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(0)
        header_layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        header_row.setLayout(header_layout)

        body_layout = QHBoxLayout()
        body_layout.setContentsMargins(24, 16, 24, 18)
        body_layout.setSpacing(14)
        body_layout.addWidget(header_row, 0, Qt.AlignmentFlag.AlignTop)
        body_layout.addWidget(content_row, 1)
        body.setLayout(body_layout)

        layout.addWidget(body)
        self.setLayout(layout)
        self.setFixedSize(560, 190)
        self.setStyleSheet(
            """
            QWidget#startupSplash {
                background: #15191f;
                border: 1px solid #2d333b;
                border-radius: 18px;
            }
            QFrame#startupAccent {
                background: qlineargradient(
                    x1: 0, y1: 0, x2: 1, y2: 0,
                    stop: 0 #7a5cff,
                    stop: 0.22 #4f88ff,
                    stop: 0.45 #39c7ba,
                    stop: 0.72 #e8d85f,
                    stop: 1 #ef6b58
                );
                border-top-left-radius: 18px;
                border-top-right-radius: 18px;
            }
            QLabel#startupIcon {
                background: transparent;
            }
            QLabel#startupTitle {
                font-size: 18px;
                font-weight: 700;
                color: #eef2f6;
                min-height: 24px;
                padding-bottom: 0px;
            }
            QLabel#startupVersion {
                color: #8a98a8;
                font-size: 10px;
                padding-top: 2px;
            }
            QLabel#startupSubtitle {
                color: #95a0ac;
                font-size: 12px;
            }
            QLabel#startupStatus {
                color: #d7dce2;
                font-size: 12px;
            }
            QProgressBar {
                border: none;
                background: transparent;
                min-height: 18px;
                max-height: 18px;
                padding: 0px;
            }
            """
        )

    def show_centered(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            available = screen.availableGeometry()
            x_pos = available.x() + (available.width() - self.width()) // 2
            y_pos = available.y() + (available.height() - self.height()) // 2
            self.move(x_pos, y_pos)
        self.show()
        QApplication.processEvents()

    def update_progress(self, value: int, text: str) -> None:
        value = max(0, min(value, 100))
        self._progress.setValue(value)
        self._status_label.setText(text)
        self._animate_icon_opacity(value)
        QApplication.processEvents()

    def _animate_icon_opacity(self, progress_value: int) -> None:
        target = 0.25 + (max(0, min(progress_value, 100)) / 100.0) * 0.75
        self._icon_opacity_anim.stop()
        self._icon_opacity_anim.setStartValue(self._icon_opacity_effect.opacity())
        self._icon_opacity_anim.setEndValue(target)
        self._icon_opacity_anim.start()


class StartupLoader(QObject):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, launch_profile: str = DEFAULT_LAUNCH_PROFILE) -> None:
        super().__init__()
        self._launch_profile_key = normalize_launch_profile(launch_profile)

    def run(self) -> None:
        try:
            profile = launch_profile_spec(self._launch_profile_key)
            self.progress.emit(4, "Checking for an already running instance...")
            lock = QLockFile(str(_lock_file_path()))
            self.progress.emit(6, "Waiting for previous instance to close...")
            if not lock.tryLock(5000):
                if not _recover_stale_lock(lock):
                    self.failed.emit(
                        "Another instance is still open or closing. "
                        "Please wait a moment and try again."
                    )
                    return

            self.progress.emit(10, "Loading core modules...")
            from lspr_app.domain.session import MeasurementSession

            self.progress.emit(30, "Preparing spectrometer backend...")
            spectrometer = create_spectrometer(force_simulator=profile.force_simulator)
            session = MeasurementSession()

            if profile.scan_devices:
                self.progress.emit(45, "Checking connected devices...")
                pump_probe = discover_pump()
            else:
                self.progress.emit(45, "Skipping connected device lookup for this launch mode.")
                pump_probe = None

            self.progress.emit(60, "Loading user interface...")
            from lspr_app.gui.experiment_control_window import ExperimentControlWindow  # noqa: F401
            from lspr_app.gui.main_window import MainWindow

            self.progress.emit(88, "Building main window...")
            self.finished.emit((lock, spectrometer, session, pump_probe, MainWindow, self._launch_profile_key))
        except Exception as exc:  # pragma: no cover - startup failure path
            self.failed.emit(str(exc))


def create_spectrometer(*, force_simulator: bool = False):
    from lspr_app.device.base import SpectrometerError
    from lspr_app.device.ocean import OceanSpectrometer
    from lspr_app.device.simulated import SimulatedSpectrometer

    force_env = os.environ.get("LSPR_FORCE_SIMULATOR", "").lower() in {"1", "true", "yes"}
    if force_simulator or force_env:
        return SimulatedSpectrometer()

    try:
        return OceanSpectrometer()
    except (ImportError, SpectrometerError):
        return SimulatedSpectrometer()


def discover_pump():
    from lspr_app.device.reglo_icc import RegloICCClient, is_probable_reglo_port

    sink = StringIO()
    with redirect_stdout(sink), redirect_stderr(sink):
        ports = [port for port in RegloICCClient.list_ports() if is_probable_reglo_port(port)]
        ordered = sorted(
            ports,
            key=lambda port: ("265C:0001" not in port.hwid.upper(), port.device),
        )
        for port in ordered:
            try:
                return RegloICCClient.probe_port(port.device)
            except Exception:
                continue
    return None


def _attach_startup_file_logging() -> logging.Handler | None:
    project_destination = str(load_app_setting("recording_project_destination", "") or "").strip()
    experiment_name = str(load_app_setting("recording_experiment_name", "") or "").strip()
    try:
        log_path = build_recording_experiment_log_path(
            project_destination,
            experiment_name,
            prefix="startup_log",
            suffix=".log",
        )
    except Exception:
        return None

    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s %(message)s", "%H:%M:%S"))

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.addHandler(handler)
    logging.getLogger("lspr_app").setLevel(logging.INFO)
    logging.getLogger("lspr_app.bootstrap").setLevel(logging.INFO)
    return handler


def main() -> None:
    file_handler = _attach_startup_file_logging()
    app = QApplication(sys.argv)
    app.setApplicationDisplayName("LSPR Acquisition")
    app.setApplicationVersion(__version__)
    apply_base_app_theme(app)
    save_app_setting("theme_mode", "dark")
    app.setWindowIcon(app_icon())
    splash = StartupSplash()
    splash.show_centered()

    loader_thread = QThread()
    loader = StartupLoader(os.environ.get(LAUNCH_PROFILE_ENV_VAR, DEFAULT_LAUNCH_PROFILE))
    loader.moveToThread(loader_thread)
    loader.progress.connect(splash.update_progress)

    def _cleanup_loader() -> None:
        loader_thread.quit()
        loader_thread.wait()
        loader.deleteLater()
        loader_thread.deleteLater()

    def _show_main_window(payload: object) -> None:
        try:
            instance_lock, spectrometer, session, pump_probe, main_window_cls, launch_profile = payload  # type: ignore[misc]
        except Exception as exc:  # pragma: no cover - defensive startup path
            splash.close()
            QMessageBox.critical(None, "Startup error", f"Failed to unpack startup payload: {exc}")
            app.quit()
            return
        splash.update_progress(100, "Ready.")
        app.instance_lock = instance_lock
        window = main_window_cls(
            spectrometer=spectrometer,
            session=session,
            discovered_pump_probe=pump_probe,
            launch_profile=launch_profile,
        )
        app.main_window = window
        window._startup_show_requested_t0 = perf_counter()
        window.show()
        QTimer.singleShot(0, splash.close)

    def _handle_startup_failure(message: str) -> None:
        splash.close()
        QMessageBox.critical(None, "Startup error", message)
        app.quit()

    loader.finished.connect(_show_main_window)
    loader.finished.connect(_cleanup_loader)
    loader.failed.connect(_handle_startup_failure)
    loader.failed.connect(_cleanup_loader)
    loader_thread.started.connect(loader.run)
    loader_thread.start()

    try:
        sys.exit(app.exec())
    finally:
        if file_handler is not None:
            root_logger = logging.getLogger()
            root_logger.removeHandler(file_handler)
            file_handler.close()
