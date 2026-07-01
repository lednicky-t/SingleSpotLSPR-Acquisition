"""Background connection tasks for the Experiment Control panel.

Each task runs on a Qt thread-pool thread, probes or connects one device,
and emits either a ``finished`` or ``failed`` signal so the window can react
on the GUI thread without blocking.

Classes
-------
``PumpConnectTask``
    Probes a serial port for a Reglo ICC pump.
``DeviceConnectTask``
    Connects any device (switch, selector, …) via the device service on a
    thread-pool thread.  Previously split into ``ValveConnectTask`` and
    ``SelectorConnectTask``; merged because the logic was identical.
``PortRefreshTask``
    Scans all available ports and returns a refresh payload.
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from lspr_app.device.device_manager import DeviceCommunicationService


# ── Pump ──────────────────────────────────────────────────────────────────────

class PumpConnectSignals(QObject):
    """Qt signals for :class:`PumpConnectTask`."""

    finished = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)


class PumpConnectTask(QRunnable):
    """Probe *port* for a Reglo ICC pump on a thread-pool thread.

    The *generation* counter lets the caller discard results from stale tasks
    when the user re-triggers a connection before a previous probe completes.
    """

    def __init__(self, generation: int, port: str) -> None:
        super().__init__()
        self._generation = generation
        self._port = port
        self.signals = PumpConnectSignals()

    def run(self) -> None:  # pragma: no cover - thread-pool path
        try:
            probe = DeviceCommunicationService.shared().probe_pump_port(self._port)
        except Exception as exc:
            self.signals.failed.emit(self._generation, str(exc))
            return
        self.signals.finished.emit(self._generation, probe)


# ── Generic device connect (switch, selector, or any label-based device) ───────

class DeviceConnectSignals(QObject):
    """Qt signals for :class:`DeviceConnectTask`."""

    finished = pyqtSignal(object)


class DeviceConnectTask(QRunnable):
    """Connect any device via the device service on a thread-pool thread.

    The profile for *label* must have its endpoint set to *port* before this
    task is started.  Emits ``finished`` with a ``(port, status, error)`` tuple;
    *error* is ``None`` on success, a string message on failure.
    *status* is the :class:`~lspr_app.device.communication_models.DeviceStatus`
    returned by the service on success, ``None`` on failure.
    """

    def __init__(self, label: str, port: str) -> None:
        super().__init__()
        self._label = label
        self._port = port
        self.signals = DeviceConnectSignals()

    def run(self) -> None:  # pragma: no cover - thread-pool path
        try:
            status = DeviceCommunicationService.shared().connect(self._label)
        except Exception as exc:
            self.signals.finished.emit((self._port, None, str(exc)))
            return
        self.signals.finished.emit((self._port, status, None))


# ── Port refresh ──────────────────────────────────────────────────────────────

class PortRefreshSignals(QObject):
    """Qt signals for :class:`PortRefreshTask`."""

    finished = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)


class PortRefreshTask(QRunnable):
    """Scan available serial ports and emit the refresh payload.

    Used to repopulate port-selection combo boxes without blocking the GUI.
    """

    def __init__(self, generation: int) -> None:
        super().__init__()
        self._generation = generation
        self.signals = PortRefreshSignals()

    def run(self) -> None:  # pragma: no cover - thread-pool path
        try:
            payload = DeviceCommunicationService.shared().refresh_device_ports(self._generation)
        except Exception as exc:
            self.signals.failed.emit(self._generation, str(exc))
            return
        self.signals.finished.emit(self._generation, payload)
