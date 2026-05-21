from __future__ import annotations

import multiprocessing as mp
import logging
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter

import numpy as np
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from lspr_app.device.base import Spectrometer
from lspr_app.device.ocean import OceanSpectrometer
from lspr_app.device.simulated import SimulatedSpectrometer, SimulationParameters
from lspr_app.domain.models import AcquisitionSettings, ProcessingSettings, Spectrum
from lspr_app.domain.processing import (
    fit_processed_spectrum,
    process_spectrum,
    processing_debug_mode_enabled,
    set_processing_debug_mode_enabled,
)
from lspr_app.storage.hdf5_export import AsyncHDF5MeasurementWriter


@dataclass(slots=True)
class AcquisitionRequest:
    kind: str
    settings: AcquisitionSettings
    source_epoch: int
    archive_writer: AsyncHDF5MeasurementWriter | None = None
    archive_enabled: bool = False
    measurement_started_at: datetime | None = None


@dataclass(slots=True)
class AcquisitionResult:
    spectrum: Spectrum
    elapsed_ms: float
    settings: AcquisitionSettings
    source_epoch: int


@dataclass(slots=True)
class LiveAcquisitionEvent:
    result: AcquisitionResult | None = None
    error: str | None = None
    source_epoch: int = 0
    produced_at_perf: float | None = None


@dataclass(slots=True)
class ProcessingRequest:
    spectrum: Spectrum | None
    settings: ProcessingSettings
    epoch: int


@dataclass(slots=True)
class ProcessingResult:
    processed: Spectrum | None
    fit: Spectrum | None
    epoch: int
    processing_ms: float
    queue_wait_ms: float = 0.0


@dataclass(slots=True)
class LiveProcessingControlMessage:
    settings: ProcessingSettings | None = None
    debug_mode_enabled: bool | None = None


@dataclass(slots=True)
class LiveAcquisitionControlMessage:
    settings: AcquisitionSettings | None = None
    cycle_period_s: float | None = None
    debug_mode_enabled: bool | None = None
    simulation_parameters: SimulationParameters | None = None


@dataclass(slots=True)
class LiveProcessedEvent:
    result: ProcessingResult | None = None
    error: str | None = None
    source_epoch: int = 0
    produced_at_perf: float | None = None


class AcquisitionSignals(QObject):
    finished = pyqtSignal(str, object)
    failed = pyqtSignal(int, str)


class ProcessingSignals(QObject):
    finished = pyqtSignal(object)


class _QueueLogHandler(logging.Handler):
    def __init__(self, log_queue: mp.Queue) -> None:
        super().__init__()
        self._log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return
        payload = (int(record.levelno), str(record.name), message)
        try:
            self._log_queue.put_nowait(payload)
        except queue.Full:
            try:
                self._log_queue.get_nowait()
            except queue.Empty:
                return
            try:
                self._log_queue.put_nowait(payload)
            except queue.Full:
                return


def _queue_put_latest(log_queue: mp.Queue, payload: object) -> None:
    while True:
        try:
            log_queue.put_nowait(payload)
            return
        except queue.Full:
            try:
                log_queue.get_nowait()
            except queue.Empty:
                return


def _queue_qsize_safe(queue_obj) -> int:
    try:
        size = queue_obj.qsize()
    except (AttributeError, NotImplementedError, OSError):
        return 0
    try:
        return max(int(size), 0)
    except (TypeError, ValueError):
        return 0


def _configure_child_logging(log_queue: mp.Queue) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    queue_handler = _QueueLogHandler(log_queue)
    queue_handler.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(queue_handler)
    root.setLevel(logging.DEBUG)


def _apply_temporal_smoothing(
    processed: Spectrum | None,
    settings: ProcessingSettings,
    temporal_history: list[Spectrum],
    temporal_key: tuple[object, ...] | None,
) -> tuple[Spectrum | None, tuple[object, ...] | None]:
    if processed is None:
        temporal_history.clear()
        return None, None
    window = max(int(getattr(settings, "temporal_smoothing", 1)), 1)
    key = (
        len(processed.wavelengths_nm),
        float(processed.wavelengths_nm[0]) if len(processed.wavelengths_nm) else None,
        float(processed.wavelengths_nm[-1]) if len(processed.wavelengths_nm) else None,
        window,
    )
    if key != temporal_key:
        temporal_history.clear()
        temporal_key = key
    temporal_history.append(processed)
    if len(temporal_history) > window:
        temporal_history[:] = temporal_history[-window:]
    if window <= 1 or len(temporal_history) == 1:
        return processed, temporal_key

    stack = np.vstack([item.values for item in temporal_history])
    averaged_values = np.nanmean(stack, axis=0)
    return (
        Spectrum(
            wavelengths_nm=processed.wavelengths_nm.copy(),
            values=averaged_values,
            y_label=processed.y_label,
            acquired_at=processed.acquired_at,
            metadata={
                **processed.metadata,
                "temporal_smoothing": window,
                "temporal_average_count": len(temporal_history),
            },
        ),
        temporal_key,
    )


def _live_processing_worker_main(
    result_queue: mp.Queue,
    input_queue: mp.Queue,
    control_queue: mp.Queue,
    stop_event,
    settings: ProcessingSettings,
    debug_mode_enabled: bool,
    log_queue: mp.Queue,
) -> None:
    _configure_child_logging(log_queue)
    set_processing_debug_mode_enabled(bool(debug_mode_enabled))
    current_settings = settings
    temporal_history: list[Spectrum] = []
    temporal_key: tuple[object, ...] | None = None
    logger = logging.getLogger("lspr_app.processing")

    try:
        while not stop_event.is_set():
            while True:
                try:
                    control_message = control_queue.get_nowait()
                except queue.Empty:
                    break
                if isinstance(control_message, LiveProcessingControlMessage):
                    if control_message.settings is not None:
                        current_settings = control_message.settings
                    if control_message.debug_mode_enabled is not None:
                        set_processing_debug_mode_enabled(bool(control_message.debug_mode_enabled))

            try:
                event = input_queue.get(timeout=0.05)
            except queue.Empty:
                continue
            if event.result is None:
                continue
            if event.error is not None:
                _queue_put_latest(
                    result_queue,
                    LiveProcessedEvent(
                        error=event.error,
                        source_epoch=event.source_epoch,
                        produced_at_perf=event.produced_at_perf,
                    ),
                )
                continue

            started = perf_counter()
            queue_wait_ms = 0.0
            if event.produced_at_perf is not None:
                queue_wait_ms = max((started - event.produced_at_perf) * 1000.0, 0.0)
            processed, fit = process_spectrum(event.result.spectrum, current_settings)
            processed, temporal_key = _apply_temporal_smoothing(processed, current_settings, temporal_history, temporal_key)
            if processed is not None and fit is not None and processed is not event.result.spectrum:
                fit = fit_processed_spectrum(processed, current_settings)
            finished = perf_counter()
            processing_ms = (finished - started) * 1000.0
            if processing_debug_mode_enabled() and (queue_wait_ms >= 1.0 or processing_ms >= 5.0):
                logger.info(
                    "Live processing latency: wait=%.2f ms | compute=%.2f ms | total=%.2f ms | backlog=%d",
                    queue_wait_ms,
                    processing_ms,
                    queue_wait_ms + processing_ms,
                    _queue_qsize_safe(input_queue),
                )
            _queue_put_latest(
                result_queue,
                LiveProcessedEvent(
                    result=ProcessingResult(
                        processed=processed,
                        fit=fit,
                        epoch=event.result.source_epoch,
                        processing_ms=processing_ms,
                        queue_wait_ms=queue_wait_ms,
                    ),
                    source_epoch=event.source_epoch,
                    produced_at_perf=event.produced_at_perf,
                ),
            )
    except Exception as exc:  # pragma: no cover - GUI runtime path
        _queue_put_latest(
            result_queue,
            LiveProcessedEvent(
                error=str(exc),
                source_epoch=0,
            ),
        )


def _create_live_backend(source_mode: str, simulation_parameters: SimulationParameters | None) -> Spectrometer:
    normalized = str(source_mode or "").strip().lower()
    if normalized == "simulation":
        return SimulatedSpectrometer(simulation_parameters)
    try:
        return OceanSpectrometer()
    except Exception as exc:  # pragma: no cover - hardware runtime path
        raise RuntimeError(f"Failed to start live acquisition backend: {exc}") from exc


def _live_acquisition_worker_main(
    result_queue: mp.Queue,
    processing_queue: mp.Queue,
    control_queue: mp.Queue,
    stop_event,
    source_mode: str,
    source_epoch: int,
    settings: AcquisitionSettings,
    cycle_period_s: float,
    simulation_parameters: SimulationParameters | None,
    debug_mode_enabled: bool,
    log_queue: mp.Queue,
) -> None:
    _configure_child_logging(log_queue)
    set_processing_debug_mode_enabled(bool(debug_mode_enabled))
    current_settings = settings
    current_cycle_period_s = max(float(cycle_period_s), 0.0)
    logger = logging.getLogger("lspr_app.acquisition")

    try:
        try:
            backend = _create_live_backend(source_mode, simulation_parameters)
        except Exception as exc:  # pragma: no cover - hardware/runtime path
            logger.exception("Live acquisition backend failed to start: %s", exc)
            _queue_put_latest(
                result_queue,
                LiveAcquisitionEvent(
                    error=str(exc),
                    source_epoch=source_epoch,
                    produced_at_perf=perf_counter(),
                ),
            )
            _queue_put_latest(
                processing_queue,
                LiveAcquisitionEvent(
                    error=str(exc),
                    source_epoch=source_epoch,
                    produced_at_perf=perf_counter(),
                ),
            )
            return

        while not stop_event.is_set():
            while True:
                try:
                    control_message = control_queue.get_nowait()
                except queue.Empty:
                    break
                if isinstance(control_message, LiveAcquisitionControlMessage):
                    if control_message.settings is not None:
                        current_settings = control_message.settings
                    if control_message.cycle_period_s is not None:
                        current_cycle_period_s = max(float(control_message.cycle_period_s), 0.0)
                    if control_message.debug_mode_enabled is not None:
                        set_processing_debug_mode_enabled(bool(control_message.debug_mode_enabled))
                    if control_message.simulation_parameters is not None and hasattr(backend, "set_simulation_parameters"):
                        backend.set_simulation_parameters(control_message.simulation_parameters)

            started = perf_counter()
            try:
                spectrum = backend.acquire_spectrum(current_settings)
                spectrum = spectrum.with_metadata(request_kind="sample")
                finished = perf_counter()
                elapsed_ms = (finished - started) * 1000.0
                _queue_put_latest(
                    result_queue,
                    LiveAcquisitionEvent(
                        result=AcquisitionResult(
                            spectrum=spectrum,
                            elapsed_ms=elapsed_ms,
                            settings=current_settings,
                            source_epoch=source_epoch,
                        ),
                        source_epoch=source_epoch,
                        produced_at_perf=finished,
                    ),
                )
                _queue_put_latest(
                    processing_queue,
                    LiveAcquisitionEvent(
                        result=AcquisitionResult(
                            spectrum=spectrum,
                            elapsed_ms=elapsed_ms,
                            settings=current_settings,
                            source_epoch=source_epoch,
                        ),
                        source_epoch=source_epoch,
                        produced_at_perf=finished,
                    ),
                )
            except Exception as exc:  # pragma: no cover - hardware/runtime path
                _queue_put_latest(
                    result_queue,
                    LiveAcquisitionEvent(
                        error=str(exc),
                        source_epoch=source_epoch,
                        produced_at_perf=perf_counter(),
                    ),
                )
                _queue_put_latest(
                    processing_queue,
                    LiveAcquisitionEvent(
                        error=str(exc),
                        source_epoch=source_epoch,
                        produced_at_perf=perf_counter(),
                    ),
                )
                break

            if current_cycle_period_s > 0.0:
                elapsed_s = perf_counter() - started
                remaining_s = current_cycle_period_s - elapsed_s
                if remaining_s > 0.0 and stop_event.wait(remaining_s):
                    break
    finally:
        close = getattr(backend, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        logger.debug("Live acquisition backend stopped.")


class AcquisitionTask(QRunnable):
    def __init__(self, spectrometer: Spectrometer, request: AcquisitionRequest) -> None:
        super().__init__()
        self._spectrometer = spectrometer
        self._request = request
        self.signals = AcquisitionSignals()

    def run(self) -> None:
        started = perf_counter()
        try:
            spectrum = self._spectrometer.acquire_spectrum(self._request.settings)
            spectrum = spectrum.with_metadata(request_kind=self._request.kind)
            self._archive_sample_if_needed(spectrum)
            elapsed_ms = (perf_counter() - started) * 1000.0
            self.signals.finished.emit(
                self._request.kind,
                AcquisitionResult(
                    spectrum=spectrum,
                    elapsed_ms=elapsed_ms,
                    settings=self._request.settings,
                    source_epoch=self._request.source_epoch,
                ),
            )
        except Exception as exc:  # pragma: no cover - GUI runtime path
            self.signals.failed.emit(self._request.source_epoch, str(exc))

    def _archive_sample_if_needed(self, spectrum: Spectrum) -> None:
        if not self._request.archive_enabled:
            return
        if self._request.archive_writer is None:
            return
        if self._request.kind != "sample":
            return

        values = np.asarray(spectrum.values, dtype=np.float64)
        wavelengths = np.asarray(spectrum.wavelengths_nm, dtype=np.float64)
        peak_nm = float("nan")
        if len(values) > 0 and len(wavelengths) > 0:
            finite = np.isfinite(values)
            if np.any(finite):
                safe_values = np.where(finite, values, -np.inf)
                peak_index = int(np.argmax(safe_values))
                if 0 <= peak_index < len(wavelengths):
                    peak_nm = float(wavelengths[peak_index])
        elapsed_s = 0.0
        if self._request.measurement_started_at is not None:
            elapsed_s = (spectrum.acquired_at - self._request.measurement_started_at).total_seconds()
        try:
            self._request.archive_writer.append_batch([spectrum], [elapsed_s], [peak_nm])
        except Exception:
            logging.getLogger("lspr_app.storage").exception("Failed to enqueue measurement frame for storage.")


class LiveAcquisitionWorker:
    def __init__(
        self,
        request: AcquisitionRequest,
        result_queue: queue.Queue[LiveAcquisitionEvent],
        processing_queue: queue.Queue[LiveAcquisitionEvent],
        stop_event: threading.Event,
        *,
        source_mode: str,
        simulation_parameters: SimulationParameters | None = None,
        debug_mode_enabled: bool = False,
        log_queue: mp.Queue | None = None,
    ) -> None:
        self._ctx = mp.get_context("spawn")
        self._request = request
        self._result_queue = result_queue
        self._processing_queue = processing_queue
        self._stop_event = stop_event
        self._settings = request.settings
        self._cycle_period_s = 0.0
        self._source_mode = source_mode
        self._simulation_parameters = simulation_parameters
        self._debug_mode_enabled = bool(debug_mode_enabled)
        self._control_queue: mp.Queue = self._ctx.Queue(maxsize=8)
        self._log_queue: mp.Queue = log_queue if log_queue is not None else self._ctx.Queue(maxsize=32)
        self._process = self._ctx.Process(
            name="live-acquisition",
            target=_live_acquisition_worker_main,
            args=(
                self._result_queue,
                self._processing_queue,
                self._control_queue,
                self._stop_event,
                self._source_mode,
                self._request.source_epoch,
                self._settings,
                self._cycle_period_s,
                self._simulation_parameters,
                self._debug_mode_enabled,
                self._log_queue,
            ),
            daemon=True,
        )
        self._started = False

    def update_settings(self, settings: AcquisitionSettings) -> None:
        self._settings = settings
        _queue_put_latest(self._control_queue, LiveAcquisitionControlMessage(settings=settings))

    def update_cycle_period(self, cycle_period_s: float) -> None:
        self._cycle_period_s = max(float(cycle_period_s), 0.0)
        _queue_put_latest(self._control_queue, LiveAcquisitionControlMessage(cycle_period_s=self._cycle_period_s))

    def update_debug_mode(self, enabled: bool) -> None:
        self._debug_mode_enabled = bool(enabled)
        _queue_put_latest(self._control_queue, LiveAcquisitionControlMessage(debug_mode_enabled=self._debug_mode_enabled))

    def update_simulation_parameters(self, parameters: SimulationParameters) -> None:
        self._simulation_parameters = parameters
        _queue_put_latest(self._control_queue, LiveAcquisitionControlMessage(simulation_parameters=parameters))

    def update_archive_context(
        self,
        archive_writer: AsyncHDF5MeasurementWriter | None,
        archive_enabled: bool,
        measurement_started_at: datetime | None,
    ) -> None:
        # Parent-side archival now handles live sample persistence.
        self._request.archive_writer = archive_writer
        self._request.archive_enabled = archive_enabled
        self._request.measurement_started_at = measurement_started_at

    @property
    def log_queue(self) -> mp.Queue:
        return self._log_queue

    def start(self) -> None:
        if self._started:
            return
        self._process.start()
        self._started = True

    def run(self) -> None:
        self.start()

    def is_alive(self) -> bool:
        return self._started and self._process.is_alive()

    def stop(self) -> None:
        try:
            self._stop_event.set()
        except Exception:
            pass

    def join(self, timeout: float | None = None) -> None:
        if not self._started:
            return
        self._process.join(timeout)

    def terminate(self) -> None:
        if not self._started:
            return
        self._process.terminate()


class LiveProcessingWorker:
    def __init__(
        self,
        result_queue: queue.Queue[LiveProcessedEvent],
        input_queue: queue.Queue[LiveAcquisitionEvent],
        stop_event: threading.Event,
        settings: ProcessingSettings,
        *,
        debug_mode_enabled: bool = False,
        log_queue: mp.Queue | None = None,
    ) -> None:
        self._ctx = mp.get_context("spawn")
        self._result_queue = result_queue
        self._input_queue = input_queue
        self._stop_event = stop_event
        self._settings = settings
        self._debug_mode_enabled = bool(debug_mode_enabled)
        self._control_queue: mp.Queue = self._ctx.Queue(maxsize=8)
        self._log_queue: mp.Queue = log_queue if log_queue is not None else self._ctx.Queue(maxsize=32)
        self._process = self._ctx.Process(
            name="live-processing",
            target=_live_processing_worker_main,
            args=(
                self._result_queue,
                self._input_queue,
                self._control_queue,
                self._stop_event,
                self._settings,
                self._debug_mode_enabled,
                self._log_queue,
            ),
            daemon=True,
        )
        self._started = False

    @property
    def log_queue(self) -> mp.Queue:
        return self._log_queue

    def update_settings(self, settings: ProcessingSettings) -> None:
        self._settings = settings
        _queue_put_latest(self._control_queue, LiveProcessingControlMessage(settings=settings))

    def update_debug_mode(self, enabled: bool) -> None:
        self._debug_mode_enabled = bool(enabled)
        _queue_put_latest(self._control_queue, LiveProcessingControlMessage(debug_mode_enabled=self._debug_mode_enabled))

    def start(self) -> None:
        if self._started:
            return
        self._process.start()
        self._started = True

    def run(self) -> None:
        self.start()

    def is_alive(self) -> bool:
        return self._started and self._process.is_alive()

    def stop(self) -> None:
        try:
            self._stop_event.set()
        except Exception:
            pass

    def join(self, timeout: float | None = None) -> None:
        if not self._started:
            return
        self._process.join(timeout)

    def terminate(self) -> None:
        if not self._started:
            return
        self._process.terminate()


class ProcessingTask(QRunnable):
    def __init__(self, request: ProcessingRequest) -> None:
        super().__init__()
        self._request = request
        self.signals = ProcessingSignals()

    def run(self) -> None:
        started = perf_counter()
        try:
            processed, fit = process_spectrum(self._request.spectrum, self._request.settings)
        except Exception as exc:  # pragma: no cover - GUI runtime path
            logging.getLogger("lspr_app.processing").exception("Spectrum processing failed: %s", exc)
            processed, fit = None, None
        self.signals.finished.emit(
            ProcessingResult(
                processed=processed,
                fit=fit,
                epoch=self._request.epoch,
                processing_ms=(perf_counter() - started) * 1000.0,
                queue_wait_ms=0.0,
            )
        )
