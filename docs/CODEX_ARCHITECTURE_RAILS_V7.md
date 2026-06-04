# Codex architecture rails: acquisition/storage must be lossless, UI/analysis may be lossy

This document is the controlling architecture rule for the sLSPR live pipeline. It should be added to the project docs and referenced from AGENTS.md / Codex instructions before any performance work.

## Desired behavior

The application has two separate layers with different guarantees.

### Layer 1: acquisition, device control, and raw-data persistence

This is the scientific acquisition layer. It is the highest-priority layer.

Requirements:

1. Device control must continue smoothly.
2. Every acquired raw sample spectrum must be saved when measurement recording is active.
3. The raw saved stream must not depend on GUI refresh rate, plot speed, processing speed, or user-interface responsiveness.
4. This layer must not drop spectra just because the UI cannot keep up.
5. This layer may buffer data, but if a buffer overflows during active recording it must be treated as a serious recording error, not as a normal display-frame drop.
6. File writing should be asynchronous, but the enqueue of raw acquired spectra must happen before any lossy GUI/latest-only queue.

### Layer 2: processing, fitting, metrics, and GUI display

This is the user-feedback layer. It is allowed to be lossy.

Requirements:

1. The UI should stay fresh and responsive.
2. It is acceptable to process only the newest available spectrum or a bounded subset of spectra.
3. It is acceptable to skip old pending UI frames if newer data exists.
4. Metric plotting should preserve full stored raw data, but display only a bounded/downsampled representation.
5. Manual refresh rate controls the target display/processing cadence, not the raw acquisition/storage cadence.
6. Processing queues should have latest-only semantics unless the user explicitly requests full offline processing.

## Current implementation concern found in the reviewed source

The current code appears to violate the Layer 1 rule during live acquisition.

In `lspr_app/gui/workers.py`, `_live_acquisition_worker_main()` pushes every acquired spectrum into two multiprocessing queues using `_queue_put_latest()`:

```python
_queue_put_latest(result_queue, LiveAcquisitionEvent(...))
_queue_put_latest(processing_queue, LiveAcquisitionEvent(...))
```

`_queue_put_latest()` intentionally drops older queued items when the queue is full:

```python
def _queue_put_latest(log_queue, payload):
    while True:
        try:
            log_queue.put_nowait(payload)
            return
        except queue.Full:
            try:
                log_queue.get_nowait()
            except queue.Empty:
                return
```

In `lspr_app/gui/acquisition_controller.py`, `flush_live_acquisition_results()` drains `window._live_result_queue`, keeps only the newest event, counts older events as dropped, and then calls raw archiving only for the newest event:

```python
latest_event = None
while True:
    event = window._live_result_queue.get_nowait()
    if latest_event is not None:
        dropped_events += 1
    latest_event = event
...
_archive_live_sample_if_needed(window, spectrum)
```

That means raw recording is currently coupled to GUI polling. If the GUI drains once every ~250–500 ms while raw acquisition is faster, older raw spectra can be discarded before archiving. This contradicts the desired behavior.

## Required architecture change

Separate the live acquisition output into at least two paths:

```text
Spectrometer / device acquisition process
    -> LOSSLESS recording queue / writer enqueue path
    -> LATEST-ONLY GUI preview queue
    -> LATEST-ONLY processing queue, or bounded processing queue with explicit drop policy
```

The recording path must receive every raw sample before any latest-only display dropping happens.

## Preferred implementation options

### Option A: archive raw spectra directly inside the acquisition worker/process

This is conceptually clean, but check whether `AsyncHDF5MeasurementWriter` or `HDF5MeasurementWriter` can safely be used from the acquisition process. Do not pass a live thread-owning writer object through multiprocessing if it is not process-safe/picklable.

If implemented, the acquisition worker should append every acquired spectrum to a dedicated recording writer or process-safe recording queue immediately after acquisition:

```python
spectrum = backend.acquire_spectrum(current_settings)
if archive_enabled:
    recording_sink.append_raw_sample(spectrum, source_sample_index, acquired_time)
_queue_put_latest(result_queue, event)       # GUI preview only
_queue_put_latest(processing_queue, event)   # processing preview only
```

### Option B: add a dedicated lossless raw-recording queue consumed by a writer thread/process

This is usually safer and more explicit.

Create a third queue:

```python
window._live_recording_queue = ctx.Queue(maxsize=RECORDING_QUEUE_SIZE)
```

The acquisition worker must put every acquired sample into this queue using lossless/blocking semantics while recording is active:

```python
if archive_enabled:
    recording_queue.put(recording_event, timeout=RECORDING_QUEUE_TIMEOUT_S)
```

Do **not** use `_queue_put_latest()` for the recording queue.

The GUI/display queues may still use `_queue_put_latest()`.

A recording drain/writer loop should append every raw sample to `AsyncHDF5MeasurementWriter` or a writer process. If the recording queue cannot accept data, this should be logged as a recording backpressure error and surfaced to the user.

### Option C: drain all raw GUI events and archive every drained event, but display only latest

This is the smallest patch but not ideal. It still depends on the GUI polling often enough and on the small GUI queue not overflowing.

If used as a temporary patch, change `flush_live_acquisition_results()` to keep all drained events in a list:

```python
drained_events = []
while True:
    try:
        event = window._live_result_queue.get_nowait()
    except queue.Empty:
        break
    drained_events.append(event)

# Archive all sample events first.
for event in drained_events:
    if event.result is not None and event.error is None and event.source_epoch == window._source_epoch:
        _archive_live_sample_if_needed(window, event.result.spectrum)

# Display only the latest event.
latest_event = drained_events[-1] if drained_events else None
```

However, this does not fix data loss if `_queue_put_latest()` already dropped events before the GUI drained the queue. It is only a partial mitigation.

## Scheduler rules

The GUI scheduler must distinguish between **deadline coalescing** and **state coalescing**.

### Do not use `coalesce="latest"` for periodic live polling

Current source uses:

```python
request_live_acquisition_poll(..., coalesce="latest")
request_live_processed_poll(..., coalesce="latest")
```

This can behave like debounce: every new request can push the deadline later. For periodic live polling, usually use `coalesce="earliest"` so an already due poll is not postponed.

Recommended:

```python
request_live_acquisition_poll(..., coalesce="earliest")
request_live_processed_poll(..., coalesce="earliest")
```

The queue contents themselves provide newest-state behavior.

### Use `coalesce="latest"` only for pure visual redraws where old frames are obsolete

Good candidates:

```text
deferred_display_flush
deferred_metric_flush
deferred_stats_flush
plot_refresh
```

But even for these, do not push deadlines so far that the display cadence falls below the manual refresh rate. Dirty flags should represent state coalescing; deadlines should preserve the next planned display tick.

## Recommended live pipeline structure

Use a small explicit state machine rather than many independent ad-hoc timer requests.

```text
Raw acquisition cadence:
    controlled by hardware/integration/simulation rate
    independent of GUI refresh rate

Recording cadence:
    receives every raw sample
    append-batches to HDF5 asynchronously
    never latest-only

Processing cadence:
    target = manual refresh rate or lower if CPU limited
    consumes newest available raw sample
    may skip intermediate raw samples
    must expose number of skipped samples as display-skip count

Display cadence:
    target = manual refresh rate
    consumes newest processed result
    may skip intermediate processed results
    plots bounded display arrays
```

## Diagnostics that must be added

Add separate counters. Do not mix raw dropped frames with UI skipped frames.

Required counters:

```text
raw_acquired_count
raw_recording_enqueued_count
raw_recording_written_count
raw_recording_queue_depth
raw_recording_queue_max_depth
raw_recording_backpressure_count
preview_queue_dropped_count
processing_input_dropped_count
processed_output_dropped_count
ui_displayed_count
ui_skipped_count
latest_raw_sample_index
latest_recorded_sample_index
latest_processed_sample_index
latest_displayed_sample_index
```

Acceptance during active recording:

```text
raw_acquired_count == raw_recording_enqueued_count == raw_recording_written_count
```

Allow this only for UI/processing:

```text
ui_displayed_count <= raw_acquired_count
processed_count <= raw_acquired_count
```

## Documentation changes needed

The existing docs describe file format and metric math, but they do not clearly define the runtime pipeline contract. Add a new doc, for example:

```text
docs/runtime_pipeline_architecture.md
```

Also update `docs/README.md` to link to it near the top.

The doc must explicitly say:

```text
Raw acquisition/storage is lossless and independent of GUI.
Processing/display is freshness-oriented and may drop/skips frames.
Manual refresh rate applies to processing/display, not raw recording.
Latest-only queues are forbidden on the raw recording path.
```

## Codex task checklist

1. Inspect the live acquisition and recording path.
2. Confirm whether every acquired spectrum is currently saved during active recording.
3. If not, implement a lossless recording path before latest-only GUI/processing queues.
4. Change live acquisition/live processed scheduler polling from `coalesce="latest"` to `coalesce="earliest"`, unless a stronger tick scheduler is implemented.
5. Keep latest-only behavior only for GUI preview/processing display frames.
6. Add counters separating raw acquisition/writing from UI/display skipped frames.
7. Add a test that simulates raw 5 Hz, display 2 Hz, recording active, for at least 60 seconds. Assert that every raw sample is written but only ~120 UI frames are displayed.
8. Add a test that simulates raw faster than processing. Assert that raw recording remains complete while processing skips old samples.
9. Update project docs so future Codex runs do not confuse data loss with display-frame skipping.

## Do not do these false fixes

Do not:

- use `_queue_put_latest()` for raw recording;
- save only the newest event drained by the GUI;
- make acquisition wait for plotting;
- make file writing happen inside the GUI event loop;
- lower raw acquisition rate to match UI rate unless the user explicitly changes acquisition settings;
- treat UI dropped frames as raw dropped spectra;
- hide recording data loss under a generic "dropped frames" counter.

