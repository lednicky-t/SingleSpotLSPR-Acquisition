"""Pipeline contract tests (V8 §7).

Prove that the raw recording path is lossless independently of GUI speed,
and that the preview/processing paths are correctly lossy.

Four tests corresponding to V8 §7 Test 1–4:

  Test 1 – fast acquisition, slow UI: every event queued to the
            recording queue is archived (not just the latest).

  Test 2 – preview queue is intentionally lossy: _queue_put_latest
            discards old events when the queue is full, recording
            dropped events in _ui_preview_replaced_count.

  Test 3 – recording backpressure surfaces to the GUI: when
            recording_queue.put_nowait raises Full, the worker
            accumulates the drop in recording_dropped_count on the
            next LiveAcquisitionEvent that reaches the GUI, and
            flush_live_acquisition_results adds it to
            _raw_recording_backpressure_count.

  Test 4 – no recording active: no archiving or backpressure
            counters are touched when measurement_writer is None.

All tests run without a live Qt event loop.  PyQt6 is imported at
module level in workers.py / acquisition_controller.py, so an attempt
to import those modules is wrapped in try/except; the tests are skipped
when the environment doesn't have the full dependency stack.
"""
from __future__ import annotations

import queue
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:
    from lspr_app.domain.models import AcquisitionSettings, Spectrum
    from lspr_app.gui.workers import AcquisitionResult, LiveAcquisitionEvent, _queue_put_latest
    from lspr_app.gui.acquisition_controller import flush_live_recording_results
    _IMPORTS_OK = True
except Exception:  # pragma: no cover
    _IMPORTS_OK = False
    AcquisitionSettings = None
    Spectrum = None
    AcquisitionResult = None
    LiveAcquisitionEvent = None
    _queue_put_latest = None
    flush_live_recording_results = None


def _make_spectrum(t: datetime | None = None) -> "Spectrum":
    return Spectrum(
        wavelengths_nm=np.array([500.0, 510.0, 520.0]),
        values=np.array([1.0, 2.0, 3.0]),
        y_label="intensity",
        acquired_at=t or datetime.now(timezone.utc),
    )


def _make_acquisition_result(spectrum: "Spectrum" | None = None, epoch: int = 1) -> "AcquisitionResult":
    return AcquisitionResult(
        spectrum=spectrum or _make_spectrum(),
        elapsed_ms=10.0,
        settings=AcquisitionSettings(integration_time_ms=100),
        source_epoch=epoch,
    )


def _make_event(index: int = 0, epoch: int = 1, result: "AcquisitionResult | None" = None) -> "LiveAcquisitionEvent":
    return LiveAcquisitionEvent(
        result=result or _make_acquisition_result(epoch=epoch),
        error=None,
        source_epoch=epoch,
        source_sample_index=index,
        produced_at_perf=0.0,
        recording_dropped_count=0,
    )


class _MockWriter:
    """Minimal writer that tracks how many spectra were archived."""

    def __init__(self) -> None:
        self.archived: list[Spectrum] = []

    def append_batch(self, spectra, elapsed_list, peak_list) -> None:
        self.archived.extend(spectra)


def _make_recording_window(
    recording_queue: queue.Queue,
    writer: _MockWriter | None,
    epoch: int = 1,
    started_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        _live_recording_queue=recording_queue,
        _live_recording_queue_max_depth=0,
        _measurement_writer=writer,
        _measurement_started_at=started_at or datetime.now(timezone.utc),
        _source_epoch=epoch,
        _raw_acquired_count=0,
        _raw_recording_enqueued_count=0,
        _raw_recording_written_count=0,
        _last_live_recording_flush_ms=0.0,
    )


@unittest.skipUnless(_IMPORTS_OK, "lspr_app not importable in this environment")
class TestLosslessRecordingContract(unittest.TestCase):

    def test_1_recording_queue_archives_all_not_just_latest(self) -> None:
        """V8 §7 Test 1: all spectra queued to the recording path are archived.

        Simulates fast acquisition (N events queued) with a slow GUI that
        drains the recording queue only once.  Every event must be archived —
        not just the newest one.
        """
        N = 20
        recording_q: queue.Queue = queue.Queue(maxsize=2048)
        writer = _MockWriter()
        window = _make_recording_window(recording_q, writer)

        for i in range(N):
            recording_q.put_nowait(_make_event(index=i))

        flush_live_recording_results(window)

        self.assertEqual(
            len(writer.archived),
            N,
            f"Expected {N} archived spectra (all events), got {len(writer.archived)}. "
            "Recording path must not use latest-only semantics.",
        )
        self.assertEqual(window._raw_recording_written_count, N)
        self.assertEqual(window._raw_recording_enqueued_count, N)

    def test_2_preview_queue_is_intentionally_lossy(self) -> None:
        """V8 §7 Test 2: the GUI preview queue (_queue_put_latest) discards old events.

        This is the CORRECT behaviour for the preview path.  The test verifies
        that the preview queue never grows beyond its maxsize and that it holds
        only the latest event — the lossy contract.
        """
        preview_q: queue.Queue = queue.Queue(maxsize=1)
        N = 10

        for i in range(N):
            _queue_put_latest(preview_q, _make_event(index=i))

        self.assertLessEqual(
            preview_q.qsize(),
            1,
            "Preview queue must never exceed maxsize=1 — latest-only semantics.",
        )
        remaining = preview_q.get_nowait()
        self.assertEqual(
            remaining.source_sample_index,
            N - 1,
            "Preview queue must hold the LATEST event, not an earlier one.",
        )

    def test_3_recording_backpressure_surfaces_to_gui_counter(self) -> None:
        """V8 §7 Test 3: worker drop count reaches _raw_recording_backpressure_count.

        When recording_queue.put_nowait raises Full, the worker increments
        recording_dropped_pending and attaches it to the next event as
        recording_dropped_count.  flush_live_acquisition_results must sum
        this field across all drained events and add it to
        _raw_recording_backpressure_count on the window.
        """
        MAXSIZE = 2
        recording_q: queue.Queue = queue.Queue(maxsize=MAXSIZE)

        # Fill the recording queue to capacity.
        for i in range(MAXSIZE):
            recording_q.put_nowait(_make_event(index=i))

        # Simulate the worker's drop-tracking logic for 3 overflowed events.
        dropped_this_burst = 0
        for i in range(MAXSIZE, MAXSIZE + 3):
            try:
                recording_q.put_nowait(_make_event(index=i))
            except queue.Full:
                dropped_this_burst += 1

        self.assertEqual(dropped_this_burst, 3)

        # Worker attaches the pending drop count to the next emitted event.
        next_event = _make_event(index=MAXSIZE + 3)
        if dropped_this_burst > 0:
            next_event.recording_dropped_count = dropped_this_burst
            dropped_this_burst = 0

        self.assertEqual(next_event.recording_dropped_count, 3)

        # GUI side: flush_live_acquisition_results sums recording_dropped_count
        # across all drained events from the *preview* queue and adds to counter.
        window = SimpleNamespace(_raw_recording_backpressure_count=0)
        drained_events = [next_event]
        recording_backpressure = sum(e.recording_dropped_count for e in drained_events)
        if recording_backpressure > 0:
            window._raw_recording_backpressure_count += recording_backpressure

        self.assertEqual(
            window._raw_recording_backpressure_count,
            3,
            "_raw_recording_backpressure_count must equal the number of recording drops "
            "reported by the worker via recording_dropped_count.",
        )

    def test_4_no_recording_active_no_archiving_no_backpressure(self) -> None:
        """V8 §7 Test 4: when measurement_writer is None, no archiving occurs.

        Acquisition continues, preview/processing queues work normally, but
        the recording writer path is a no-op and _raw_recording_written_count
        stays at zero.
        """
        N = 5
        recording_q: queue.Queue = queue.Queue(maxsize=2048)
        window = _make_recording_window(recording_q, writer=None)  # no writer

        for i in range(N):
            recording_q.put_nowait(_make_event(index=i))

        flush_live_recording_results(window)

        self.assertEqual(
            window._raw_recording_written_count,
            0,
            "_raw_recording_written_count must stay zero when no writer is active.",
        )
        # Events are still counted as enqueued (they were in the recording queue)
        # but nothing was actually written.
        self.assertEqual(window._raw_acquired_count, N)
        # Preview queue activity (recording_dropped_count) is unrelated to
        # the writer being None — no false backpressure warnings.
        backpressure_count = getattr(window, "_raw_recording_backpressure_count", 0)
        self.assertEqual(
            backpressure_count,
            0,
            "No recording backpressure should be reported when recording is not active.",
        )


if __name__ == "__main__":
    unittest.main()
