# Sensorgram Module — Improvements & Optimisations

Analysis date: 2026-06-26
Source files analysed: `plot_controller.py`, `plot_view_cache.py`, `main_window_plotting.py`,
`main_window_sensorgram_archive.py`, `main_window_runtime.py`, `update_scheduler.py`

**2026-07-21 update:** a full audit of the live-plotting/storage pipeline (prompted by "the
sensorgram redraws over already-drawn points when a measurement stops") found and fixed the root
cause — see "Correctness fixes" below. That audit also clarified the app's actual view-mode
architecture, which wasn't documented anywhere: `window._sensorgram_display_mode`
(`"session"` / `"measurement"`, in `main_window.py`) is the real, already-implemented view-mode
concept — not the `view_mode` local variable in `plot_controller.py`'s `render_metric_series`
(that one is an unrelated, unused literal, already fixed as a separate `UnboundLocalError` bug).
`"session"` mode reads from the always-on session file (`storage/measurement_archive.py`,
`ensure_session_writer`/`close_session_writer` — a single HDF5 file spanning the whole app
session, closed only at app close); `"measurement"` mode shows only the currently-recording
file. It auto-switches on measurement start/stop and can be changed manually in Sensorgram
Settings. There is no dedicated `SensorgramDisplayState`-style object unifying this with the
other five loosely-related display flags (`_trace_view_locked`, `_live_active`,
`_sensorgram_frozen`, `_plots_frozen`, `_sensorgram_metric_archive_reload_loading`) — flagged as
a separate, larger consolidation task, not done here.

---

## Correctness fixes

### C1 — Session file's timeline went non-monotonic across a measurement, corrupting the reload-on-stop plot
**Files:** `gui/acquisition_controller.py`, `storage/measurement_archive.py`, `gui/main_window.py`
**Symptom:** while a measurement recorded, the sensorgram correctly showed only the recording
(measurement mode). When the measurement stopped, the plot reloaded the full session and visibly
redrew a line backward over history it had already drawn.
**Root cause:** every processed point writes to two files at once — the per-measurement file
(measurement-relative `t_ms`) and the always-on session file (session-relative `t_ms`). The
session file's `t_ms` anchor was `window._live_trace_started_at`, which both
`start_measurement_run` and `stop_measurement_run` unconditionally set to `None`, only restored
by `_start_live_acquisition()` — which runs `if not window._live_active`, i.e. never, if live
preview was already active before Record was pressed (the normal workflow). While the anchor was
`None`, the session-file write silently fell back to the *measurement's own* elapsed time,
splicing a near-zero-restarting range into the middle of an otherwise-increasing `t_ms` column.
Reading that column back (on stop, or on manual session/measurement mode switch) via
`np.searchsorted`-based range slicing on unsorted data, then handing the result straight to
`curve.setData()`, is what produced the visible backward redraw — pyqtgraph draws points in
array order, not sorted by x.
**Fix:** introduced a dedicated, session-file-lifecycle-scoped anchor
(`window._metric_archive_started_at`, an existing but previously write-only/dead attribute,
repurposed) — set once when the session writer is created (`ensure_session_writer`) and cleared
only when it closes (`close_session_writer`). Not touched by live-acquisition or measurement
start/stop at all, so the session file's timeline now stays stable regardless of how many
measurements start and stop within one session. Verified with an isolated script: the anchor
stays identical across a simulated measurement start → append → stop cycle, and the resulting
elapsed-time values stay strictly increasing.
**Effort:** Small (~15 lines changed across 3 files)
**Risk:** Low — the new anchor is set/read through the same `getattr`/`setattr` pattern already
used throughout this code; no schema or file-format change.

### C2 — Defensive: sort by timestamp wherever plot data gets loaded from file or merged
**Files:** `storage/hdf5_export.py` (`load_processed_metric_history`), `storage/metric_archive.py`
(`load_metric_archive_history`, both `.h5` and `.jsonl` branches), `gui/plot_view_cache.py`
(`seed_live_absolute_metric_cache`)
**Rationale:** C1 was the actual bug, but nothing prevented a *future* timestamp anomaly from
producing the same silent visual corruption — none of these functions verified their input was
monotonic before slicing (`np.searchsorted` silently misbehaves on unsorted input) or handing
arrays to pyqtgraph. Since the maintainer's stated requirement is "all data should have to have
correct timestamp, and sensorgram should distinguish when data was obtained," these are now
guaranteed to return/consume monotonic arrays regardless of what produced the input, with values
reordered by the same permutation as their timestamps (never independently re-sorted, which would
scramble the pairing). `seed_live_absolute_metric_cache` in particular is the single point every
reload-seed call goes through, including the file-portion + live-tail concatenation in
`main_window_sensorgram_archive.py`, so sorting there is the most robust single guarantee point.
**Effort:** Small
**Risk:** Low — sorting an already-sorted array is a cheap no-op (`np.argsort` returns the
identity permutation, detected and skipped); only pays extra cost (a full read instead of a
partial HDF5 read) in the rare case where reordering is actually needed.

### C3 — 2026-07-21 follow-up: removed the write-time relative `t_ms` for spectra/metrics entirely (schema 6.0)
**Files:** `packages/lspr_io/src/lspr_io/schema.py`, `storage/hdf5_export.py`,
`storage/metric_archive.py`, `storage/measurement_archive.py`, `gui/acquisition_controller.py`,
plus `apps/sLSPR/eva`'s `io.py` time-axis detection
**Rationale:** C1's root cause was fundamentally "a relative anchor computed at write time can go
stale or get reset mid-file." C1's fix (a dedicated, stable anchor) and C2's defensive sorting
addressed the *symptom* for the session/measurement metrics streams specifically, but the same
class of write-time-relative-anchor risk still existed by construction anywhere a relative `t_ms`
was written alongside the always-present absolute `acquired_at_unix_ms`. Rather than adding more
anchor bookkeeping, schema 6.0 removes the relative column outright for raw-spectra rows and
processed-metric rows: `acquired_at_unix_ms` is now the sole per-row timestamp on disk, and every
reader computes relative/elapsed seconds at read time (anchored to the first row of the stream
being read), never trusting a value baked in at write time. C1's dedicated anchor
(`_metric_archive_started_at`) is kept, but only for its other, legitimate use (axis-label
clock-mode display) — it is no longer needed to compute a written `t_ms`.
**Note:** this does *not* touch the separate experiment-control/flow-state runtime log's own
`t_ms` column (`LSPR_MEASUREMENT_RUNTIME_COLUMNS`) — that table is plan/step-relative by design,
not a spectrum-acquisition timestamp, and was out of scope for this change.
**Cross-repo impact:** `apps/sLSPR/eva`'s HDF5 loader infers the time axis from dataset name/shape
heuristics rather than the shared `lspr_io` schema, and would otherwise have silently misread the
new absolute-only files (dividing `acquired_at_unix_ms` by 1000 without normalizing, producing a
nonsense epoch-scale axis). Fixed alongside this change — see `_score_time_candidate` and
`_decode_time_axis` in `apps/sLSPR/eva/src/lspr_single_evaluation/io.py`.
**Effort:** Medium (touches every spectra/metrics writer and reader in the acq app, plus eva)
**Risk:** Medium — breaking schema change (major version bump 5.2 → 6.0); old files remain
readable (readers only warn, not error, on an older major), but this session's own smoke tests
plus the full test suite must confirm nothing else depended on the removed column.

---

## Performance fixes

### P1 — Move autoscale throttle check before computation
**File:** `plot_controller.py` ~line 850
**Impact:** HIGH — saves 20-30% autoscale CPU; currently the throttle fires *after* the expensive
`np.concatenate / np.isfinite / np.min / np.max` block it is supposed to skip.
**Fix:** Move the `min_interval_s` guard to the very top of `autoscale_metric_plot`, before any
array work. If throttled and not forced, return immediately.
**Effort:** Small (~10 lines moved)
**Risk:** Low

---

### P2 — Batch display-array rebuilds in `append_live_absolute_metric_point`
**File:** `plot_view_cache.py` ~line 1054
**Impact:** HIGH — `_build_absolute_metric_display_arrays()` is called on *every single point*,
doing full block selection + concatenation + tail merge. At 10 Hz → 10 rebuilds/sec; at 100 Hz →
100. The compression pyramid exists to avoid exactly this.
**Fix:** Accumulate incoming points and rebuild display arrays at most once per render frame
(~16 ms budget). Track a `_pending_rebuild` flag; flush in `live_absolute_metric_series()` rather
than on each append.
**Effort:** Medium
**Risk:** Medium — needs careful handling of the pending-flush boundary

---

### P3 — Cache recent-tail numpy arrays
**File:** `plot_view_cache.py` ~line 200
**Impact:** MEDIUM-HIGH — `_recent_tail_arrays()` calls `np.asarray(list(deque), dtype=float64)`
on every append *and* every autoscale, allocating a Python list + numpy array each time.
**Fix:** Add `recent_tail_x_np: np.ndarray` and `recent_tail_y_np: np.ndarray` fields to
`MetricDisplayCache`; rebuild them only when the deque changes (append or trim).
**Effort:** Small
**Risk:** Low

---

### P4 — Cache envelope data between autoscale and render
**File:** `plot_controller.py` ~lines 772-841 and 1234-1270
**Impact:** MEDIUM — when the envelope overlay is enabled, the same source data is used to build
envelope min/max candidates in `autoscale_metric_plot` and then independently again in
`render_metric_series`. Two full passes over the compression blocks per cycle.
**Fix:** Store `_last_envelope_display_data` keyed by a `(len, first_x, last_x)` signature;
reuse in the same render cycle if signature matches.
**Effort:** Small
**Risk:** Low

---

### P5 — Fix render-state cache key: replace `id()` with content signature
**File:** `plot_controller.py` ~line 1313
**Impact:** MEDIUM — the cache key uses `id(display_x), id(display_y)`, which changes on every
render even when data is identical (new array object, same contents). This causes redundant
`setData()` calls in "session" display mode (`window._sensorgram_display_mode == "session"`,
see the 2026-07-21 update above) whenever data is reloaded from the archive file without
actually changing.
**Fix:** Replace with a `(len, first_x, last_x)` tuple — cheap to compute, stable for unchanged
data.
**Effort:** Small (~5 lines)
**Risk:** Low

---

### P6 — Batch recent-tail trimming
**File:** `plot_view_cache.py` ~line 210
**Impact:** LOW-MEDIUM — `_trim_recent_tail()` is called on every append and pops one item at a
time in a while-loop. When the tail lags behind the append rate this becomes O(N) per point.
**Fix:** Only trim when `len(tail) > max_points * 1.1`; trim in a single `islice` slice rather
than a loop.
**Effort:** Small
**Risk:** Low

---

### P7 — Cache compression-level selection result
**File:** `plot_view_cache.py` ~line 267 (`_select_metric_compression_blocks`)
**Impact:** LOW-MEDIUM — level selection is O(number of levels) and is recomputed on every append
even though the result only changes when `source_len` or `target_points` changes.
**Fix:** Store `last_selection_source_len` and `last_selection_target_points` in the cache;
short-circuit if unchanged.
**Effort:** Small
**Risk:** Low

---

### P8 — `RollingMetricDisplayCache` removed (2026-07-21): was scaffolding, not a working optimization
**File:** `plot_view_cache.py` (was ~line 61)
**Correction to the original P8 entry above:** re-checked while acting on this backlog item. The
class was a bare `@dataclass` with no methods and zero call sites anywhere in the codebase -
not "fully implemented but never instantiated" as originally written here, just a placeholder
that was never wired to anything. Windowed/rolling-window display already works today through
the ordinary `MetricDisplayCache` path (`_build_windowed_metric_arrays` /
`_build_windowed_metric_envelope_arrays` in this same file both operate on `MetricDisplayCache`,
not this class), so nothing was providing the "roughly halving display-rebuild work" benefit this
entry originally described - that benefit was speculative, not measured or working.
**Action taken:** deleted the dead class outright rather than "activating" it, since there was no
working implementation to activate. If a dedicated rolling-window cache turns out to be worth
building later, it should be designed fresh against the current `MetricDisplayCache`/compression-
pyramid architecture rather than resurrected from this unused stub.

---

### P9 — Eliminate redundant dtype conversions
**File:** `plot_controller.py` ~lines 104-105, 1095-1103
**Impact:** LOW — `_sensorgram_display_x_values()` calls `np.asarray(..., dtype=float64)`
unconditionally; `_series_to_arrays()` does the same; then cache methods convert again. Triple
conversion on the hot render path.
**Fix:** Guard with `if arr.dtype == np.float64: return arr` before re-allocating.
**Effort:** Tiny
**Risk:** Low

---

## Feature additions

### F1 — LTTB downsampling algorithm
**Rationale:** Current weighted-mean decimation loses spike anomalies — often the most
scientifically significant events in a sensorgram. LTTB (Largest Triangle Three Buckets) preserves
the visual shape of the curve including outliers regardless of decimation ratio.
**Implementation:** Add `lttb` as a new `trend_method` option in `_blocks_to_trend_arrays()`;
expose as a user-selectable option in the Sensorgram settings dialog.
**Effort:** Medium (~80 lines)

---

### F2 — Baseline subtraction view
**Rationale:** Standard in kinetics analysis — subtract the mean of the first N seconds to
normalise all subsequent metric values to a t=0 baseline. Does not alter stored data; applied only
at display time.
**Implementation:** Add `_sensorgram_baseline_subtract_s: float | None` window state variable;
apply offset in `append_live_absolute_metric_point` or in `_series_to_arrays()`.
**Effort:** Low (~25 lines)

---

### F3 — dY/dX rate-of-change overlay
**Rationale:** Derivative of the primary metric helps identify binding kinetics inflection points,
phase transitions, and step changes that are invisible in the raw trace.
**Implementation:** Compute finite-differences on the compressed display arrays; render as a
secondary semi-transparent curve on a right Y-axis.
**Effort:** Medium

---

### F4 — Windowed statistics export (CSV)
**Rationale:** Users need to export min/max/mean/std.dev for specific time windows (e.g. a single
experiment phase) for downstream analysis.
**Implementation:** Right-click context menu on the sensorgram plot → "Export stats for visible
window". Uses existing `update_metric_stats_for()` logic with a time range filter.
**Effort:** Low (~30 lines)

---

### F5 — Metric quality warning system
**Rationale:** Early detection of failed experiments, instrumental drift, or SNR collapse during a
run.
**Implementation:** After each `append_processed_trace_history` call, check SNR, std.dev, and
rate-of-change against user-configurable thresholds. Emit a coloured status-bar warning and an
optional audible beep.
**Effort:** Low-Medium (~40 lines + settings UI)

---

### F6 — Per-metric Y-axis label
**Rationale:** When mixing metric types (centroid nm, FWHM, SNR) in one plot, a single "Metric
position (nm)" label is misleading.
**Implementation:** Add `TRACE_METRIC_Y_LABELS: dict[str, str]` lookup; update
`_set_plot_label_if_changed()` to use the primary metric's label.
**Effort:** Low (~15 lines)

---

### F7 — Histogram / percentile envelope bands (P5–P95)
**Rationale:** Current envelope is hard min/max of compression blocks (sharp, noisy lines). P5/P95
or ±1σ bands give better intuition about signal stability.
**Implementation:** Store `p05`, `p95`, `std` per `MetricCompressionBlock` during pyramid build.
Add render path for soft-edge band. Increases per-metric memory by ~15 KB.
**Effort:** Medium-Large

---

### F8 — Metric time-shift alignment
**Rationale:** When comparing multiple metrics or replaying experiments side-by-side, aligning all
traces to a user-selected event (t=0 reference) reveals temporal relationships and lag.
**Implementation:** Add per-metric `_sensorgram_metric_time_offset_s: dict[str, float]`; apply as
X-offset in `_sensorgram_display_x_values()`.
**Effort:** Medium (~60 lines)

---

## Priority summary

| ID | Title | Impact | Effort | Do first |
|----|-------|--------|--------|----------|
| P1 | Throttle check before computation | High | Small | Yes |
| P2 | Batch display-array rebuilds | High | Medium | Yes |
| P3 | Cache recent-tail numpy arrays | Med-High | Small | Yes |
| P4 | Cache envelope data | Medium | Small | Yes |
| P5 | Fix render-state cache key | Medium | Small | Yes |
| P6 | Batch tail trimming | Low-Med | Small | No |
| P7 | Cache level selection | Low-Med | Small | No |
| P8 | ~~Activate RollingMetricDisplayCache~~ — deleted, was dead scaffolding | — | — | Done |
| P9 | Eliminate redundant dtype casts | Low | Tiny | No |
| F1 | LTTB downsampling | — | Medium | Discuss |
| F2 | Baseline subtraction view | — | Low | Discuss |
| F3 | dY/dX overlay | — | Medium | Discuss |
| F4 | Windowed stats export | — | Low | Discuss |
| F5 | Quality warning system | — | Low-Med | Discuss |
| F6 | Per-metric Y-axis label | — | Low | Discuss |
| F7 | Percentile envelope bands | — | Med-Large | Discuss |
| F8 | Metric time-shift alignment | — | Medium | Discuss |
