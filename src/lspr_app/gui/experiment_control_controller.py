from __future__ import annotations

from PyQt6.QtCore import QObject

from lspr_app.gui.experiment_control_backend import ExperimentControlBackend
from lspr_app.gui.experiment_control_capabilities import ExperimentControlCapabilities


class ExperimentControlController(QObject):
    def __init__(
        self,
        window: QObject,
        backend: ExperimentControlBackend,
        capabilities: ExperimentControlCapabilities,
    ) -> None:
        super().__init__(window)
        self.window = window
        self.backend = backend
        self.capabilities = capabilities

    def set_capabilities(self, capabilities: ExperimentControlCapabilities) -> None:
        self.capabilities = capabilities

    def bind_backend(self, backend: ExperimentControlBackend) -> None:
        self.backend = backend

    def toggle_run_hold(self, *_args) -> None:
        if hasattr(self.window, "_toggle_experiment_control_run_hold"):
            self.window._toggle_experiment_control_run_hold()

    def toggle_hold(self, *_args) -> None:
        if hasattr(self.window, "_toggle_experiment_control_hold"):
            self.window._toggle_experiment_control_hold()

    def toggle_pause(self, *_args) -> None:
        if hasattr(self.window, "_toggle_experiment_control_pause"):
            self.window._toggle_experiment_control_pause()

    def stop(self, *_args) -> None:
        if hasattr(self.window, "_stop_experiment_control"):
            self.window._stop_experiment_control()

    def move_relative(self, delta: int, *_args) -> None:
        if hasattr(self.window, "_move_to_relative_experiment_control_step"):
            self.window._move_to_relative_experiment_control_step(int(delta))

    def refresh_devices(self, *_args) -> bool:
        return self.backend.refresh_devices()

    def device_states(self) -> list:
        return self.backend.device_states()

    def is_device_connected(self, device_key: str) -> bool:
        return self.backend.is_device_connected(device_key)

    def shutdown_devices(self, *_args) -> None:
        if hasattr(self.window, "shutdown_devices"):
            self.window.shutdown_devices()
