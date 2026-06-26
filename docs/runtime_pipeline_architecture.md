# Runtime Pipeline Architecture

This document defines the core runtime rules for acquisition, processing,
plotting, and file writing in `sLSPR acq`.  It is the authoritative contract
for any code that touches the live acquisition pipeline.

---

## Runtime pipeline invariant

Raw acquisition and active recording are lossless and independent of GUI
speed.  Latest-only queues and skipped-frame policies are allowed only for
processing, fitting, metric preview, and plotting.  Manual refresh rate
controls processing/display freshness, not raw recording.  Any raw recording
queue overflow is a serious acquisition/recording error and must be surfaced
explicitly.

---

## Two-layer architecture

The application splits the live pipeline into two layers with different
guarantees.

### Layer 1 — acquisition, device control, and raw recording (lossless)

This is the scientific measurement layer.  It is the highest-priority layer.

| Guarantee | Description |
|-----------|-------------|
| Every acquired spectrum is saved | When measurement recording is active, no spectrum may be silently dropped. |
| Independent of GUI speed | A slow or frozen GUI does not reduce the number of spectra written to disk. |
| Independent of processing speed | Processing backlog does not affect raw capture or storage. |
| Buffer overflow is an error | If a recording-path queue overflows, it must be logged as a serious recording error, not treated as a normal display-frame drop. |

### Layer 2 — processing, fitting, metrics, and GUI display (freshness-oriented)

This is the user-feedback layer.  It is allowed to be lossy.

| Policy | Description |
|--------|-------------|
| Latest-only queues allowed | Processing and preview queues may discard older events when newer ones arrive. |
| Display-frame skips are normal | When processing is slower than acquisition, skipping intermediate frames is expected and counted. |
| Manual refresh rate applies here | The user's refresh-rate setting governs how often Layer 2 runs, not Layer 1. |

---

## Concrete data flow

```
Acquisition worker process (lspr_app.gui.workers._live_acquisition_worker_main)
│
│  acquire raw spectrum N
│
├─── if recording_enabled ──────────────────────────────────────── Layer 1
│        recording_queue.put_nowait(event)          # lossless; maxsize=2048
│        on Full: recording_dropped_pending += 1    # backpressure counter
│
├─── _queue_put_latest(result_queue, event)         # lossy; maxsize≈1 ──── Layer 2 (preview)
│
└─── _queue_put_latest(processing_queue, event)     # lossy; maxsize≈1 ──── Layer 2 (processing)

GUI main thread  (flush_live_recording_results, flush_live_acquisition_results)
│
├─── drain _live_recording_queue (ALL events) ──────────────────── Layer 1
│        for every event: _archive_live_sample_if_needed()
│        increment _raw_recording_written_count
│
├─── drain _live_result_queue (latest-only display) ─────────────── Layer 2
│        sum recording_dropped_count from all drained events
│        → _raw_recording_backpressure_count
│        display only newest valid event
│
└─── drain _live_processing_queue (latest-only processing) ──────── Layer 2
```

### Queue inventory

| Queue | Owner | Semantics | Max size |
|-------|-------|-----------|----------|
| `_live_recording_queue` | `acquisition_controller.py` | Lossless; every acquired spectrum when recording | 2048 events |
| `_live_result_queue` | `acquisition_controller.py` | Latest-only; GUI preview | 1 event (replaced) |
| `_live_processing_queue` | `acquisition_controller.py` | Latest-only; processing input | 1 event (replaced) |

---

## Diagnostic counters

All counters live on the main-window object and are reset on each
acquisition start.

| Counter | Layer | Meaning |
|---------|-------|---------|
| `_raw_acquired_count` | 1 | Spectra drained from the recording queue (valid, correct epoch) |
| `_raw_recording_enqueued_count` | 1 | Same — events accepted into the recording path |
| `_raw_recording_written_count` | 1 | Spectra successfully passed to `AsyncHDF5MeasurementWriter` |
| `_raw_recording_backpressure_count` | 1 | Recording-queue overflows reported by the worker (serious error) |
| `_raw_recording_failed_count` | 1 | Writer-side errors during `append_batch` |
| `_ui_preview_replaced_count` | 2 | Events discarded in the result queue before the GUI drained them |

`_raw_recording_backpressure_count` and `_raw_recording_failed_count` must
remain zero during normal operation.  Any non-zero value indicates a
recording integrity problem and must be surfaced in the diagnostics panel.

---

## Scheduler policy

The GUI task scheduler (`UITaskScheduler`) dispatches deferred work.
Different task types require different coalesce semantics.

| Task key | Coalesce mode | Reason |
|----------|--------------|--------|
| `live_visual_refresh` | `"earliest"` | The recording queue already holds the latest state; the deadline must not be pushed forward on every new request or the refresh rate falls below the manual target. |
| `deferred_display_flush` | `"latest"` | Pure visual redraw; acceptable to batch if many requests arrive. |
| `deferred_metric_flush` | `"latest"` | Pure visual redraw. |
| `deferred_stats_flush` | `"latest"` | Pure visual redraw. |
| `plot_refresh` | `"latest"` | Pure visual redraw. |

Using `coalesce="latest"` on `live_visual_refresh` would debounce the flush
loop and could cause missed refresh-rate targets under sustained acquisition
load.

---

## Hard rules

1. Raw acquisition and raw storage are lossless.
2. The user-controlled refresh rate applies to processing and display only —
   not to raw recording.
3. Latest-only queues are forbidden on the raw recording path.
4. Processing and plotting may skip old frames to stay fresh.
5. File writing must be asynchronous, but it must receive every raw acquired
   spectrum.
6. UI dropped frames must be counted separately from raw recording loss.
7. When fixing a pipeline bug, simplify the data path.  Do not wrap a broken
   flow in more helpers — a correct fix makes the live pipeline easier to
   explain, not harder.

---

## Simplification rule

Prefer simple, explicit runtime architecture over layered helper machinery.

Do not solve performance or coordination problems by adding more schedulers,
deferred tasks, queues, throttles, wrappers, or diagnostics unless there is a
measured need and no simpler alternative.

Use queues only at real thread/process/disk/device boundaries, and use a
single clear GUI refresh cadence where possible.

Avoid chaining callbacks such as:

```
queue -> scheduler -> deferred flush -> throttle -> log -> GUI update
```

Before adding a helper or abstraction, explain what concrete boundary or
repeated logic it simplifies, and verify it does not create hidden control
flow, duplicate state, or event-loop work.

Prefer deleting or bypassing unnecessary orchestration over adding new
coordination code.

---

## Expected diagnostic output during normal recording

```
Raw acquired:               ~1200 (at 10 Hz over 120 s)
Raw recording enqueued:     ~1200
Raw recording written:      ~1200
Raw recording loss:         0         ← must be zero
UI preview replaced:        ~720      ← normal at 4 Hz display
Processing skipped:         ~720      ← normal at 4 Hz processing
```

Raw recording loss non-zero is always a serious error.  Processing/display
skips are expected and healthy under sustained acquisition load.

---

## Review checklist

When modifying any code that touches the live acquisition pipeline, verify:

- Does this change preserve every raw acquired spectrum during active
  recording?
- Does this change keep display refresh separate from raw capture?
- Does this change avoid latest-only behavior on the recording path?
- Does this change update diagnostics to distinguish UI drops from raw
  data loss?
- Does this change keep file writing asynchronous without dropping data?
- Does this change use `coalesce="earliest"` for the live-refresh scheduler
  task?

---

## Tests

Pipeline contract tests are in
`tests/test_pipeline_recording_contract.py`.

| Test | What it proves |
|------|----------------|
| `test_1_recording_queue_archives_all_not_just_latest` | All N events queued to the recording path are archived — no latest-only drop on the recording path. |
| `test_2_preview_queue_is_intentionally_lossy` | `_queue_put_latest` discards old events when the preview queue is full; the queue always holds the latest event. |
| `test_3_recording_backpressure_surfaces_to_gui_counter` | Worker recording-queue overflows are attached to the next event as `recording_dropped_count` and accumulated into `_raw_recording_backpressure_count` on the GUI side. |
| `test_4_no_recording_active_no_archiving_no_backpressure` | When `measurement_writer` is None, no archiving occurs and no false backpressure is reported. |

---

## Related documents

- `docs/CODEX_ARCHITECTURE_RAILS_V7.md` — controlling architecture rule
- `docs/CODEX_IMPLEMENTATION_GUIDE_V8_LOSSLESS_ACQ_AND_LOSSY_UI.md` — implementation guide for the lossless/lossy split
- `docs/CODEX_RUNTIME_SIMPLICITY_GUIDE_V12.md` — runtime simplicity rules and anti-patterns
