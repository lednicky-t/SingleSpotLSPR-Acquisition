"""Qt wrapper around :mod:`lspr_app.device.device_lifecycle`.

Thin ``QRunnable`` + companion ``QObject`` signals classes, following the
same idiom used throughout ``gui/`` for background work (the pattern
``hardware_initializer.py`` and ``experiment_control_connection.py`` also
use). All device connect/disconnect/discover/post-connect/refresh work runs
on a single dedicated worker lane (:func:`device_io_pool`), separate from the
app's general-purpose thread pool, so that no two hardware-touching
operations can ever run concurrently - the AMF vendor SDK in particular is
not guaranteed thread-safe (see R7/R8 in
``docs/device-layer/DEVICE_LAYER_AUDIT_2026.md``).
"""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from lspr_acq_shell.device_io_pool import device_io_pool
from lspr_app.device.communication_models import DeviceCommand
from lspr_app.device.device_lifecycle import DeviceLifecycleController, DeviceLifecycleEvent, device_label_for
from lspr_app.device.device_manager import DeviceCommunicationService
from lspr_app.device.device_types import SWITCH

# device_io_pool re-exported (not called in this file) so existing
# `from lspr_app.gui.device_lifecycle_task import device_io_pool` call sites
# keep working unchanged after the move to lspr_acq_shell.device_io_pool.
_ = device_io_pool


class DeviceLifecycleSignals(QObject):
    device_event = pyqtSignal(object)     # DeviceLifecycleEvent, every stage transition
    cycle_finished = pyqtSignal(object)   # DeviceLifecycleReport, whole cycle done


class DeviceLifecycleCycleTask(QRunnable):
    """Runs ``controller.run_full_cycle()`` on the device I/O worker thread.

    *controller_factory* is called here, inside :meth:`run`, rather than by
    the caller before constructing this task - same reasoning as the
    already-fixed ``HardwareInitTask``: resolving the controller (and the
    real device I/O ``run_full_cycle`` performs) must not happen on the GUI
    thread before the background task starts.
    """

    def __init__(self, controller_factory: Callable[[], DeviceLifecycleController]) -> None:
        super().__init__()
        self._controller_factory = controller_factory
        self.signals = DeviceLifecycleSignals()

    def run(self) -> None:
        controller = self._controller_factory()
        report = controller.run_full_cycle(self.signals.device_event.emit)
        self.signals.cycle_finished.emit(report)


class DeviceConnectSignals(QObject):
    finished = pyqtSignal(object)   # DeviceLifecycleEvent (terminal)


class DeviceConnectTask(QRunnable):
    """Manual "Connect" button: connect *device_key* to an explicit *port*."""

    def __init__(self, device_key: str, port: str) -> None:
        super().__init__()
        self._device_key = device_key
        self._port = port
        self.signals = DeviceConnectSignals()

    def run(self) -> None:
        controller = DeviceLifecycleController.shared()
        events: list[DeviceLifecycleEvent] = []
        controller.request_connect(self._device_key, self._port, events.append)
        if events:
            self.signals.finished.emit(events[-1])


class DeviceDisconnectSignals(QObject):
    finished = pyqtSignal(object)   # DeviceLifecycleEvent


class DeviceDisconnectTask(QRunnable):
    """Manual "Disconnect" button."""

    def __init__(self, device_key: str) -> None:
        super().__init__()
        self._device_key = device_key
        self.signals = DeviceDisconnectSignals()

    def run(self) -> None:
        controller = DeviceLifecycleController.shared()
        events: list[DeviceLifecycleEvent] = []
        controller.request_disconnect(self._device_key, events.append)
        if events:
            self.signals.finished.emit(events[-1])


class DevicePortRefreshSignals(QObject):
    finished = pyqtSignal(object)   # PortRefreshData
    failed = pyqtSignal(str)


class DevicePortRefreshTask(QRunnable):
    """Manual "Refresh ports" button."""

    def __init__(self) -> None:
        super().__init__()
        self.signals = DevicePortRefreshSignals()

    def run(self) -> None:
        try:
            payload = DeviceLifecycleController.shared().refresh_ports()
        except Exception as exc:
            self.signals.failed.emit(str(exc))
            return
        self.signals.finished.emit(payload)


class DevicePostConnectSignals(QObject):
    finished = pyqtSignal(object)   # DeviceLifecycleEvent


class DevicePostConnectTask(QRunnable):
    """Manual "Home" button (or any future post-connect re-run)."""

    def __init__(self, device_key: str) -> None:
        super().__init__()
        self._device_key = device_key
        self.signals = DevicePostConnectSignals()

    def run(self) -> None:
        controller = DeviceLifecycleController.shared()
        events: list[DeviceLifecycleEvent] = []
        controller.rerun_post_connect(self._device_key, events.append)
        if events:
            self.signals.finished.emit(events[-1])


class DeviceEnvironmentReadSignals(QObject):
    finished = pyqtSignal(object, object)   # temperature_c: float | None, humidity_percent: float | None


class DeviceEnvironmentReadTask(QRunnable):
    """Periodic ambient temperature/humidity poll of the Switch device.

    Only ArduinoValveController actually supports this (see
    docs/hardware/arduino_valve_controller_protocol.md) - a connected
    ItsyBitsy/Legacy controller, or no connection at all, just yields
    (None, None) via DeviceCommunicationService.send_command()'s normal
    success=False result, which this task never raises on (send_command()
    itself never raises - see device_manager.py's send_command).
    """

    def __init__(self) -> None:
        super().__init__()
        self.signals = DeviceEnvironmentReadSignals()

    def run(self) -> None:
        service = DeviceCommunicationService.shared()
        label = device_label_for(SWITCH)
        temperature_c: float | None = None
        humidity_percent: float | None = None

        temperature_result = service.send_command(label, DeviceCommand("switch.read_ambient_temperature"))
        if temperature_result.success and isinstance(temperature_result.response, (int, float)):
            temperature_c = float(temperature_result.response)

        humidity_result = service.send_command(label, DeviceCommand("switch.read_humidity"))
        if humidity_result.success and isinstance(humidity_result.response, (int, float)):
            humidity_percent = float(humidity_result.response)

        self.signals.finished.emit(temperature_c, humidity_percent)
