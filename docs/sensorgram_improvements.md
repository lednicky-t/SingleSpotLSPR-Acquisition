# Sensorgram Module — Improvements & Optimisations

Analysis date: 2026-06-26
Source files analysed: `plot_controller.py`, `plot_view_cache.py`, `main_window_plotting.py`,
`main_window_sensorgram_archive.py`, `main_window_runtime.py`, `update_scheduler.py`

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
`setData()` calls in archive/replay mode.
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

### P8 — Activate `RollingMetricDisplayCache` for rolling-window mode
**File:** `plot_view_cache.py` ~line 61
**Impact:** MEDIUM — the class is fully implemented but never instantiated. Activating it for the
"last N seconds" rolling window path would skip the entire old-data pyramid traversal, roughly
halving display-rebuild work when the user has a short rolling window configured.
**Fix:** Instantiate `RollingMetricDisplayCache` when rolling-window mode is selected; route
`live_absolute_metric_series()` to it.
**Effort:** Medium
**Risk:** Medium — new code path, needs thorough testing

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
| P8 | Activate RollingMetricDisplayCache | Medium | Medium | No |
| P9 | Eliminate redundant dtype casts | Low | Tiny | No |
| F1 | LTTB downsampling | — | Medium | Discuss |
| F2 | Baseline subtraction view | — | Low | Discuss |
| F3 | dY/dX overlay | — | Medium | Discuss |
| F4 | Windowed stats export | — | Low | Discuss |
| F5 | Quality warning system | — | Low-Med | Discuss |
| F6 | Per-metric Y-axis label | — | Low | Discuss |
| F7 | Percentile envelope bands | — | Med-Large | Discuss |
| F8 | Metric time-shift alignment | — | Medium | Discuss |
