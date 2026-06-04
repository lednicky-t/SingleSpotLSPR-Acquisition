# Codex implementation guide V8: lossless acquisition/storage + lossy fresh UI

This document is a concrete implementation guide for Codex. It extends `CODEX_ARCHITECTURE_RAILS_V7.md`.

Use this document as the next task prompt. Do not do broad refactors. Do not continue optimizing metric plotting before the acquisition/recording pipeline contract is made explicit and tested.

---

## 0. Controlling principle

The application has two different layers. They must not share the same queue semantics.

```text
Layer 1: device control + raw acquisition + raw recording
    Lossless while recording is active.
    Highest priority.
    Independent of GUI refresh rate.

Layer 2: processing + fitting + metric calculation + plotting
    Freshness-oriented.
    May skip old samples/results.
    Governed by manual refresh rate and GUI responsiveness.
```

The manual refresh rate applies to Layer 2 only. It must not reduce the number of raw acquired spectra saved during active measurement recording.

A slow GUI may skip displayed frames. A slow GUI must not cause raw spectra to be lost from the measurement file.

---

## 1. Current suspected violation to inspect first

Search for these functions/classes:

```text
_queue_put_latest
_live_acquisition_worker_main
LiveAcquisitionEvent
flush_live_acquisition_results
_archive_live_sample_if_needed
request_live_acquisition_poll
request_live_processed_poll
GuiTaskScheduler.request
_live_result_queue
_live_processing_queue
_live_recording_queue
AsyncHDF5MeasurementWriter
HDF5MeasurementWriter
```

Known risky pattern:

```python
_queue_put_latest(result_queue, LiveAcquisitionEvent(...))
_queue_put_latest(processing_queue, LiveAcquisitionEvent(...))
```

and then in the GUI:

```python
latest_event = None
while True:
    event = window._live_result_queue.get_nowait()
    if latest_event is not None:
        dropped_events += 1
    latest_event = event

_archive_live_sample_if_needed(window, latest_event.result.spectrum)
```

This is wrong for active recording. It means raw archiving is coupled to GUI polling and latest-only queue behavior.

Allowed latest-only paths:

```text
GUI preview queue
processing preview queue
plot update queue
```

Forbidden latest-only paths:

```text
raw recording queue
writer enqueue path
anything required to save every acquired spectrum
```

---

## 2. Required target architecture

Implement this separation:

```text
Acquisition worker / acquisition loop
    acquire raw spectrum N
    assign monotonically increasing raw_sample_index N
    if recording active:
        enqueue raw spectrum N to lossless recording path
    enqueue spectrum N to latest-only GUI preview path
    enqueue spectrum N to latest-only processing path
```

The recording path must receive every raw sample before any latest-only dropping can happen.

The processing path may skip samples. The GUI path may skip samples. The recording path may not skip samples silently.

---

## 3. Minimal safe implementation path

Prefer implementing in three stages so behavior can be tested after each stage.

### Stage A: add explicit counters and diagnostics before changing behavior

Add counters with clearly separated meaning:

```text
raw_acquired_count
raw_recording_enqueued_count
raw_recording_written_count
raw_recording_backpressure_count
raw_recording_failed_count
ui_preview_enqueued_count
ui_preview_replaced_count
ui_preview_displayed_count
processing_enqueued_count
processing_replaced_count
processing_completed_count
processing_displayed_count
```

Do not reuse `dropped_frames` for everything. It must be clear whether a dropped item is:

```text
raw recording loss       -> serious error
processing skip          -> normal if CPU limited
UI display skip          -> normal if fresh UI policy
```

Add these to the diagnostics/log summary:

```text
Raw acquired: X
Raw recording enqueued/written: X/Y
Raw recording loss/backpressure: Z
UI preview replaced/skipped: A
Processing replaced/skipped: B
Displayed processed results: C
Manual refresh target: F Hz
Actual display refresh: G Hz
```

Acceptance after Stage A:

```text
The log distinguishes raw recording loss from UI skipped frames.
```

---

### Stage B: stop saving raw data only from the latest GUI event

Find `flush_live_acquisition_results()`.

If it currently keeps only the newest event and archives only that newest event, change it at least temporarily to archive every drained event and display only the newest.

Patch shape:

```python
def flush_live_acquisition_results(window):
    drained_events = []

    while True:
        try:
            event = window._live_result_queue.get_nowait()
        except queue.Empty:
            break
        drained_events.append(event)

    if not drained_events:
        return

    # Archive every valid drained raw sample first.
    for event in drained_events:
        if getattr(event, "error", None) is not None:
            continue
        if getattr(event, "result", None) is None:
            continue
        if getattr(event, "source_epoch", None) != getattr(window, "_source_epoch", None):
            continue

        spectrum = event.result.spectrum
        _archive_live_sample_if_needed(window, spectrum)
        window._raw_recording_enqueued_count = getattr(window, "_raw_recording_enqueued_count", 0) + 1

    # UI preview uses only the newest valid event.
    latest_event = None
    for event in reversed(drained_events):
        if getattr(event, "error", None) is None and getattr(event, "result", None) is not None:
            latest_event = event
            break

    skipped_for_ui = max(0, len(drained_events) - 1)
    window._ui_preview_skipped_count = getattr(window, "_ui_preview_skipped_count", 0) + skipped_for_ui

    if latest_event is None:
        return

    # Existing display/update handling continues here using latest_event only.
```

Important: this is only a mitigation. It still does not protect against `_queue_put_latest()` dropping events before the GUI receives them. Stage C is the real fix.

Acceptance after Stage B:

```text
When GUI drains multiple raw events in one poll, all drained raw spectra are archived, but only newest is displayed.
```

---

### Stage C: add a dedicated lossless recording queue/path

This is the required real fix.

Do not use `_queue_put_latest()` for recording.

#### Option C1: dedicated recording queue from acquisition worker to GUI/writer side

Create a third queue, for example:

```python
recording_queue = ctx.Queue(maxsize=RECORDING_QUEUE_SIZE)
```

Use a size large enough to tolerate short GUI/writer stalls, for example 1000â€“10000 spectra depending on memory size of spectra. Do not make it tiny like the preview queue.

In the acquisition worker signature, add this queue:

```python
def _live_acquisition_worker_main(..., result_queue, processing_queue, recording_queue, ...):
    ...
```

When each spectrum is acquired:

```python
event = LiveAcquisitionEvent(
    source_epoch=source_epoch,
    sample_index=sample_index,
    result=result,
    error=None,
    acquired_at=time.perf_counter(),
)

# Lossless recording path first.
if recording_enabled:
    try:
        recording_queue.put(event, timeout=RECORDING_QUEUE_TIMEOUT_S)
        raw_recording_enqueued_count += 1
    except queue.Full:
        # This is not a normal UI frame drop.
        # Treat as recording backpressure/error.
        raw_recording_backpressure_count += 1
        error_queue.put_nowait(RecordingBackpressureEvent(...))
        # Decide policy: pause acquisition, stop recording, or block longer.
        # Do not silently discard while claiming full recording.

# Lossy latest-only paths.
_queue_put_latest(result_queue, event)
_queue_put_latest(processing_queue, event)
```

If `recording_enabled` cannot be known inside the worker, send every raw sample to `recording_queue` while live acquisition is active, and let the writer side decide whether to write based on current recording state. But be careful not to lose spectra at the exact moment recording starts/stops. Prefer explicit recording state messages with epochs.

#### Option C2: dedicated writer process/thread owns HDF5 writer

If HDF5 writer objects are not safe to use across multiprocessing, do not pass them to the acquisition worker. Instead:

```text
Acquisition worker
    -> recording_queue with plain serializable raw spectrum events
Recording writer thread/process
    -> owns HDF5 writer
    -> drains recording_queue
    -> writes every event in order
```

The writer side can batch writes for performance:

```python
batch = []
while len(batch) < MAX_BATCH and not timeout:
    batch.append(recording_queue.get(timeout=...))
writer.append_many(batch)
```

But batching must preserve all samples and order.

Acceptance after Stage C:

```text
Raw acquired count == raw recording enqueued count == raw recording written count
for active recording intervals, unless a clearly logged recording error occurred.
```

---

## 4. Scheduler policy: correct semantics by task type

Do not use one scheduler coalescing policy for everything.

### Lossless acquisition/recording

Do not schedule raw recording through GUI debounce/coalescing. Raw recording should be driven by acquisition events and writer queue draining.

Scheduler rules:

```text
Raw acquisition cadence: hardware/device-driven.
Raw recording enqueue: immediate per acquired sample.
Raw recording write: writer thread/process drains queue, may batch, never latest-only.
```

### Periodic polling tasks

For recurring polling tasks, avoid `coalesce="latest"` if it pushes the due time forward on every request. That creates debounce and can reduce steady refresh below target.

Use:

```python
coalesce="earliest"
```

for tasks such as:

```text
live_acquisition poll
live_processed poll
```

because the queue content already stores latest state. The deadline should not be postponed.

Patch shape:

```python
window._ui_task_scheduler.request(
    "live_acquisition",
    delay_ms,
    window._flush_live_acquisition_results,
    priority=-20,
    coalesce="earliest",
)

window._ui_task_scheduler.request(
    "live_processed",
    delay_ms,
    window._flush_live_processed_results,
    priority=-19,
    coalesce="earliest",
)
```

### Visual redraw tasks

For pure redraw tasks, latest-state semantics are OK, but do not use them as debounce that misses the manual refresh target.

Allowed `coalesce="latest"` candidates:

```text
deferred_metric_flush
deferred_display_flush
deferred_stats_flush
plot_refresh
```

However, if these tasks become too slow, prefer an explicit fixed-cadence display timer with dirty flags:

```text
Every 250 ms at 4 Hz:
    if display_dirty:
        draw newest processed result
    if metric_dirty:
        draw newest metric state
```

This is better than repeatedly requesting new delayed tasks from many places.

---

## 5. Processing/fitting/metric policy

Processing is allowed to be lossy and freshness-oriented.

Use latest-only or bounded queues for processing:

```python
_queue_put_latest(processing_queue, event)
```

This is acceptable as long as it is documented and counted as processing skip, not raw loss.

Processing should consume newest available raw sample and may skip intermediate samples if behind:

```text
If processing queue has multiple pending samples:
    keep newest
    increment processing_skipped_count
    process newest
```

Metric history should store metrics for processed samples only unless full offline reprocessing is explicitly requested.

This is expected:

```text
Raw saved spectra: 10000
Processed live spectra: 2400
Displayed metric points: bounded/downsampled
```

This is not an error. It is the intended live behavior.

But it must be visible in diagnostics:

```text
Raw spectra saved: 10000
Live spectra processed: 2400
Live spectra skipped by processing freshness policy: 7600
```

---

## 6. UI/display policy

The UI consumes the newest processed result at approximately the manual refresh rate.

Do not try to plot every raw spectrum during live acquisition.

Metric plotting should use the already-fixed bounded display cache:

```text
Raw/processed metric history may grow.
Displayed metric points must remain bounded.
```

Display skip is acceptable:

```text
Processed results available faster than GUI can draw -> display newest only.
```

But do not confuse display skip with recording loss.

---

## 7. Required tests

Add or run tests/simulation that prove the contract.

### Test 1: fast acquisition, slow UI

Setup:

```text
Simulated acquisition: 10 Hz
Manual refresh rate: 4 Hz
Recording active
Run duration: 120 s
```

Expected:

```text
Raw acquired: about 1200
Raw written: about 1200
Processed live spectra: about <= 480, depending on CPU/manual rate
Displayed frames: about <= 480
UI skipped/processing skipped: allowed and counted
Raw recording loss: 0
```

### Test 2: intentionally slow plotting

Artificially slow plot refresh or disable metric plot later.

Expected:

```text
Raw written remains equal to raw acquired.
UI display refresh may fall.
Processing/display skip counters increase.
Raw recording loss remains zero.
```

### Test 3: recording queue backpressure

Temporarily make recording queue tiny and writer slow.

Expected:

```text
The app logs a serious recording backpressure error.
It does not silently drop raw spectra while reporting successful recording.
```

### Test 4: no recording active

Expected:

```text
Raw acquisition continues.
Recording queue may be inactive.
GUI/processing latest-only behavior remains allowed.
No false recording-loss warnings.
```

---

## 8. Acceptance criteria

Codex is done only when all are true:

```text
1. No latest-only queue is used on the raw recording path.
2. Raw recording enqueue happens before GUI/processing latest-only dropping.
3. Raw acquired/enqueued/written counters are present and separated from UI skipped frames.
4. Manual refresh rate affects processing/display only, not raw recording.
5. live_acquisition/live_processed scheduler tasks do not use deadline-postponing debounce semantics.
6. GUI can be slow without causing raw recording loss.
7. Diagnostics clearly report:
   - raw acquired
   - raw written
   - raw recording loss/backpressure
   - processing skipped
   - UI/display skipped
8. Existing metric plot optimizations remain intact:
   - bounded display points
   - incremental metric cache
   - disabled fast path
```

---

## 9. Things not to do

Do not solve by:

```text
- lowering raw acquisition rate to match GUI speed
- saving only displayed spectra
- saving only processed spectra
- using _queue_put_latest for recording
- treating recording queue overflow as normal dropped frames
- deleting or truncating raw scientific data
- making manual refresh rate control raw acquisition/storage
- hiding dropped raw spectra behind UI skipped-frame counters
- doing another broad metric plot refactor before fixing the pipeline contract
```

---

## 10. Suggested project documentation update

Add this to `AGENTS.md` and/or the main project instructions:

```text
Runtime pipeline invariant:
Raw acquisition and active recording are lossless and independent of GUI speed. Latest-only queues and skipped-frame policies are allowed only for processing, fitting, metric preview, and plotting. Manual refresh rate controls processing/display freshness, not raw recording. Any raw recording queue overflow is a serious acquisition/recording error and must be surfaced explicitly.
```

Also add a link from docs README:

```text
See docs/runtime_pipeline_architecture.md for the live acquisition/processing/display contract.
```

---

## 11. One-sentence goal

Make the app behave like a scientific recorder first and a live preview UI second: save every raw acquired spectrum during recording, while processing and displaying only the freshest subset the application can handle.
