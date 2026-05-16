from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter

import numpy as np
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from lspr_app.device.base import Spectrometer
from lspr_app.domain.models import AcquisitionSettings, ProcessingSettings, Spectrum
from lspr_app.domain.processing import fit_processed_spectrum, process_spectrum
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


class LiveAcquisitionWorker(threading.Thread):
    def __init__(
        self,
        acquire_sample,
        request: AcquisitionRequest,
        result_queue: queue.Queue[LiveAcquisitionEvent],
        processing_queue: queue.Queue[LiveAcquisitionEvent],
        stop_event: threading.Event,
    ) -> None:
        super().__init__(name="live-acquisition", daemon=True)
        self._acquire_sample = acquire_sample
        self._request = request
        self._result_queue = result_queue
        self._processing_queue = processing_queue
        self._stop_event = stop_event
        self._settings_lock = threading.Lock()
        self._settings = request.settings
        self._archive_lock = threading.Lock()
        self._cycle_lock = threading.Lock()
        self._cycle_period_s = 0.0

    def update_settings(self, settings: AcquisitionSettings) -> None:
        with self._settings_lock:
            self._settings = settings

    def update_cycle_period(self, cycle_period_s: float) -> None:
        with self._cycle_lock:
            self._cycle_period_s = max(float(cycle_period_s), 0.0)

    def update_archive_context(
        self,
        archive_writer: AsyncHDF5MeasurementWriter | None,
        archive_enabled: bool,
        measurement_started_at: datetime | None,
    ) -> None:
        with self._archive_lock:
            self._request.archive_writer = archive_writer
            self._request.archive_enabled = archive_enabled
            self._request.measurement_started_at = measurement_started_at

    def run(self) -> None:
        try:
            while not self._stop_event.is_set():
                cycle_period_s = self._cycle_period_s
                with self._settings_lock:
                    settings = self._settings
                started = perf_counter()
                spectrum = self._acquire_sample(settings)
                spectrum = spectrum.with_metadata(request_kind="sample")
                self._archive_sample_if_needed(spectrum)
                finished = perf_counter()
                elapsed_ms = (finished - started) * 1000.0
                event = LiveAcquisitionEvent(
                    result=AcquisitionResult(
                        spectrum=spectrum,
                        elapsed_ms=elapsed_ms,
                        settings=settings,
                        source_epoch=self._request.source_epoch,
                    ),
                    source_epoch=self._request.source_epoch,
                    produced_at_perf=finished,
                )
                self._push_latest(event)
                self._push_processing(event)
                if cycle_period_s > 0.0:
                    elapsed_s = perf_counter() - started
                    remaining_s = cycle_period_s - elapsed_s
                    if remaining_s > 0.0 and self._stop_event.wait(remaining_s):
                        break
        except Exception as exc:  # pragma: no cover - GUI runtime path
            self._push_latest(
                LiveAcquisitionEvent(
                    error=str(exc),
                    source_epoch=self._request.source_epoch,
                )
            )

    def _archive_sample_if_needed(self, spectrum: Spectrum) -> None:
        with self._archive_lock:
            request = AcquisitionRequest(
                kind=self._request.kind,
                settings=self._request.settings,
                source_epoch=self._request.source_epoch,
                archive_writer=self._request.archive_writer,
                archive_enabled=self._request.archive_enabled,
                measurement_started_at=self._request.measurement_started_at,
            )
        if not request.archive_enabled or request.archive_writer is None or request.kind != "sample":
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
        if request.measurement_started_at is not None:
            elapsed_s = (spectrum.acquired_at - request.measurement_started_at).total_seconds()
        try:
            request.archive_writer.append_batch([spectrum], [elapsed_s], [peak_nm])
        except Exception:
            logging.getLogger("lspr_app.storage").exception("Failed to enqueue measurement frame for storage.")

    def _push_latest(self, event: LiveAcquisitionEvent) -> None:
        while not self._stop_event.is_set():
            try:
                self._result_queue.put_nowait(event)
                return
            except queue.Full:
                try:
                    self._result_queue.get_nowait()
                except queue.Empty:
                    return

    def _push_processing(self, event: LiveAcquisitionEvent) -> None:
        while not self._stop_event.is_set():
            try:
                self._processing_queue.put_nowait(event)
                return
            except queue.Full:
                try:
                    self._processing_queue.get_nowait()
                except queue.Empty:
                    return


class LiveProcessingWorker(threading.Thread):
    def __init__(
        self,
        result_queue: queue.Queue[LiveProcessedEvent],
        input_queue: queue.Queue[LiveAcquisitionEvent],
        stop_event: threading.Event,
        settings: ProcessingSettings,
    ) -> None:
        super().__init__(name="live-processing", daemon=True)
        self._result_queue = result_queue
        self._input_queue = input_queue
        self._stop_event = stop_event
        self._settings_lock = threading.Lock()
        self._settings = settings
        self._temporal_history: list[Spectrum] = []
        self._temporal_key: tuple[object, ...] | None = None

    def update_settings(self, settings: ProcessingSettings) -> None:
        with self._settings_lock:
            self._settings = settings

    def run(self) -> None:
        try:
            while not self._stop_event.is_set():
                try:
                    event = self._input_queue.get(timeout=0.05)
                except queue.Empty:
                    continue
                if event.result is None:
                    continue
                if event.error is not None:
                    self._push_latest(
                        LiveProcessedEvent(
                            error=event.error,
                            source_epoch=event.source_epoch,
                            produced_at_perf=event.produced_at_perf,
                        )
                    )
                    continue

                with self._settings_lock:
                    settings = self._settings
                started = perf_counter()
                processed, fit = process_spectrum(event.result.spectrum, settings)
                processed = self._apply_temporal_smoothing(processed, settings)
                if processed is not None and fit is not None and processed is not event.result.spectrum:
                    fit = fit_processed_spectrum(processed, settings)
                self._push_latest(
                    LiveProcessedEvent(
                        result=ProcessingResult(
                            processed=processed,
                            fit=fit,
                            epoch=event.result.source_epoch,
                            processing_ms=(perf_counter() - started) * 1000.0,
                        ),
                        source_epoch=event.source_epoch,
                        produced_at_perf=event.produced_at_perf,
                    )
                )
        except Exception as exc:  # pragma: no cover - GUI runtime path
            self._push_latest(
                LiveProcessedEvent(
                    error=str(exc),
                    source_epoch=0,
                )
            )

    def _apply_temporal_smoothing(self, processed: Spectrum | None, settings: ProcessingSettings) -> Spectrum | None:
        if processed is None:
            self._temporal_history.clear()
            self._temporal_key = None
            return None
        window = max(int(getattr(settings, "temporal_smoothing", 1)), 1)
        key = (
            len(processed.wavelengths_nm),
            float(processed.wavelengths_nm[0]) if len(processed.wavelengths_nm) else None,
            float(processed.wavelengths_nm[-1]) if len(processed.wavelengths_nm) else None,
            window,
        )
        if key != self._temporal_key:
            self._temporal_history.clear()
            self._temporal_key = key
        self._temporal_history.append(processed)
        if len(self._temporal_history) > window:
            self._temporal_history = self._temporal_history[-window:]
        if window <= 1 or len(self._temporal_history) == 1:
            return processed
        stack = np.vstack([item.values for item in self._temporal_history])
        averaged_values = np.nanmean(stack, axis=0)
        return Spectrum(
            wavelengths_nm=processed.wavelengths_nm.copy(),
            values=averaged_values,
            y_label=processed.y_label,
            acquired_at=processed.acquired_at,
            metadata={
                **processed.metadata,
                "temporal_smoothing": window,
                "temporal_average_count": len(self._temporal_history),
            },
        )

    def _push_latest(self, event: LiveProcessedEvent) -> None:
        while not self._stop_event.is_set():
            try:
                self._result_queue.put_nowait(event)
                return
            except queue.Full:
                try:
                    self._result_queue.get_nowait()
                except queue.Empty:
                    return


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
            )
        )
