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
Settings. There was no dedicated `SensorgramDisplayState`-style object unifying this with the
other five loosely-related display flags (`_trace_view_locked`, `_live_active`,
`_sensorgram_frozen`, `_plots_frozen`, `_sensorgram_metric_archive_reload_loading`) — flagged as
a separate, larger consolidation task at the time. Done as of the 2026-07-22 update below.

**2026-07-22 update:** consolidated four of the six display flags flagged above into
`gui/sensorgram_display_state.py`'s `SensorgramDisplayState` — see "Correctness fixes" C4 below
for the real bug this uncovered and fixed along the way. `_sensorgram_display_mode`,
`_trace_view_locked`, `_sensorgram_frozen`, and `_sensorgram_metric_archive_reload_loading` (plus
its three sibling reload-bookkeeping fields) are now properties on `MainWindow` delegating to one
`self._sensorgram_display` instance, so they can't drift out of sync with each other internally,
while every existing call site keeps working unchanged (property shims are transparent to
`getattr`/`setattr`/`hasattr`). `_live_active` stays external and untouched — it's fundamentally
an acquisition-loop flag (40+ read sites in `acquisition_controller.py`), not a display flag; the
consolidated object reads it as a dependency where needed rather than absorbing it.
`_plots_frozen` also stays external — see C4, it turned out not to belong here at all.

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

### C4 — 2026-07-22: "freeze sensorgram" didn't independently freeze anything; consolidated into `SensorgramDisplayState`
**Files:** `gui/sensorgram_display_state.py` (new), `gui/main_window.py`, `gui/plot_controller.py`,
`gui/acquisition_controller.py`
**Symptom, found while designing the display-flag consolidation flagged in the 2026-07-21 update
above:** "Freeze plots" (next to the spectrum controls) and "Freeze sensorgram" (next to the
sensorgram's own controls) look like they should each freeze only their own plot, independently.
They didn't: `refresh_metric_plot()` — the single low-level function that actually redraws the
sensorgram, which every one of the ~13 call sites of `window._refresh_trace_plot(...)` funnels
through — checked `_plots_frozen`, not `_sensorgram_frozen`. So freezing the spectrum plot also
silently froze the sensorgram as a side effect, and "freeze sensorgram" alone didn't reliably
freeze anything (`_sensorgram_frozen` only gated 3 minor "should I schedule a refresh" call
sites, none of which is the actual draw function).
**Fix:** `plot_controller.py:1007` now checks `_sensorgram_frozen` instead of `_plots_frozen` -
this single-line change, at the one real chokepoint, fixes all ~13 call sites at once. Each
freeze button now correctly and independently freezes only its own plot.
**Consolidation:** `_sensorgram_display_mode`, `_trace_view_locked`, `_sensorgram_frozen` (now
correct), and the archive-reload bookkeeping (`_sensorgram_metric_archive_reload_loading` + 3
sibling fields) moved into one `SensorgramDisplayState` dataclass
(`gui/sensorgram_display_state.py`), exposed on `MainWindow` as property shims under the old
attribute names so every existing call site keeps working unchanged. `start_measurement_run()`/
`stop_measurement_run()` now use its `begin_measurement()`/`end_measurement()` helpers, which also
fixed a latent asymmetry: starting a measurement now releases a stale pan/zoom lock (matching what
stopping already did), so the view correctly re-follows once a new recording starts instead of
silently staying locked from a previous session-view interaction.
**Explicitly NOT merged in:** `_plots_frozen` turned out to be a genuinely separate, spectrum-only
concern (confirmed via `enqueue_plot_processing_for`/`refresh_spectrum_plot_for`/
`_autoscale_spectrum_plot`, all spectrum-specific) and doesn't belong in a "sensorgram" state
object at all — it stays a plain attribute, untouched. `_live_active` stays external too (an
acquisition-loop flag, not a display flag).
**Effort:** Small-Medium (~90 lines: one new file, one bug-fix line, ~70 lines of property shims,
two call-site swaps to the atomic helpers)
**Risk:** Low for the consolidation itself (property shims are transparent to every existing
`getattr`/`setattr`/`hasattr` call site, confirmed via full test suite + an isolated verification
script exercising the property descriptors directly). The freeze-bug fix and the view-lock
asymmetry fix are both deliberate, user-confirmed behavior changes — verify by hand: toggle each
freeze button independently and confirm only its own plot stops updating; pan/zoom the sensorgram,
start a measurement, confirm the view re-follows instead of staying locked.

### C5 — 2026-07-23: live-cache tail still on the measurement's time scale spliced a backward segment into the reload-on-stop plot
**Files:** `gui/acquisition_controller.py` (`stop_measurement_run`), `gui/plot_view_cache.py`
(`PlotViewCache.rebase_live_absolute_metric_recent_tail`, new)
**Symptom:** distinct from C1 (same visible symptom, different root cause - C1 was fixed
2026-07-21 and stayed fixed). While a measurement records, the live sensorgram counts elapsed time
from when Record was pressed (`_measurement_started_at`); once back in session view, it counts
from session start (`_metric_archive_started_at`) instead - intentional, so "measurement" mode
genuinely shows "5 seconds into this recording," not "3625 seconds into this session." On Stop,
`request_absolute_sensorgram_metric_archive_reload` reloads the full session file
(session-relative) and merges in whatever is still sitting in the live cache's "recent tail" (to
avoid a gap for points not yet flushed to disk) - but that tail was written while still
measurement-relative. C2's defensive sort (`seed_live_absolute_metric_cache`) only guarantees the
merged array ends up in x-order; it can't fix values computed on the wrong scale to begin with.
The result: a handful of tail points land at small x-values that fall inside territory the session
view already drew minutes earlier, appearing as a short new line segment stitched into
already-drawn history, specifically at the moment a measurement stops (the C1 fix's own
description of the normal workflow - live preview already running before Record is pressed -
is exactly the condition that makes the offset large enough to be visible).
**Fix:** `stop_measurement_run` now computes
`offset_s = (measurement_started_at - metric_archive_started_at).total_seconds()` - both anchors
are still valid at that exact point, before either gets cleared - and calls the new
`rebase_live_absolute_metric_recent_tail(offset_s)` to shift the live cache's raw tail onto the
session's scale before the mode switch and reload happen. A pure additive shift, not a
re-derivation, so it can't reorder or mismatch x/y pairs. Only the raw tail needs rebasing - the
compressed display pyramid (`x_display`/`levels`) is rebuilt from scratch on every reload
regardless, never carried across it. `_metric_archive_started_at` (C1's anchor, already reused for
axis-label clock-mode display per C3) now has this third legitimate use.
**Effort:** Small (~20 lines: one new cache method, one call site)
**Risk:** Low - the rebase is a no-op whenever either anchor is unavailable (guarded by
`is not None` checks) or the offset is zero, and only ever runs once, synchronously, at the one
call site that has both anchors still valid.

### C6 — 2026-07-23 follow-up: every point recorded after the first Stop in a session used a broken time anchor, corrupting both the live plot and the saved session file
**Files:** `gui/acquisition_controller.py` (`append_processed_trace_history`,
`_archive_to_session_writer_if_available`)
**Symptom:** reported after C5 landed - C5 fixed the one-time reload-merge splice, but the
maintainer still saw (1) measurement data appearing at the *start* of the x-axis instead of after
the existing session data, and (2) two separate places in the plot receiving new data
simultaneously ("overlapping"), both starting the moment a measurement stopped and continuing for
the rest of the session.
**Root cause:** distinct from C5, same neighborhood. Both functions anchor their elapsed-time
calculation to `window._live_trace_started_at` when no measurement is active. `stop_measurement_run`
unconditionally clears that value to `None`, and it is *only* ever restored inside
`_start_live_acquisition()`, guarded by `if not window._live_active` - which never runs across a
Record/Stop cycle if live preview was already running before Record was pressed (the normal
workflow, per C1). So from the moment Stop is pressed onward, for the rest of the session:
`append_processed_trace_history` fell through to an unrelated display-cursor counter (not a time
value at all - just an incrementing counter left over from wherever it was during the measurement,
i.e. small values), continuously appending new live points at the wrong, low x-position - a second,
ongoing stream visually distinct from the correctly-reloaded session curve, matching both reported
symptoms. Independently, `_archive_to_session_writer_if_available` fell through to
`elapsed_s = 0.0`, silently writing a near-zero elapsed time into the session file's *persisted*
`time_series` dataset (schema-required, read by `apps/sLSPR/eva`) for every spectrum recorded after
the first Stop - a real data-integrity bug, not just a display one.
**Fix:** both functions now prefer `_metric_archive_started_at` - the same stable,
session-lifetime anchor C1 introduced and C5 already relies on, set once when the session writer is
created and never touched by Record/Stop - falling back to `_live_trace_started_at` only for the
narrow window before the session writer exists yet (first spectrum of a session), and to the
display-cursor/zero fallback only if neither anchor has ever been set at all.
**Effort:** Small (~10 lines across 2 functions, both one-line anchor-preference changes)
**Risk:** Low - `_metric_archive_started_at` was already proven stable across Record/Stop by C1;
this just extends its use to two more read sites that were still using the fragile anchor.

### C7 — 2026-07-23 follow-up: the control-step overlay baked in a fixed elapsed_s at record time, so it drifted out of alignment with the plot on Stop
**Files:** `gui/main_window_sensorgram_overlay.py` (`record_sensorgram_control_step_event`,
`sync_sensorgram_control_step_overlay`, `sensorgram_control_step_overlay_current_elapsed_s`, plus
new `_rebase_sensorgram_control_step_events`)
**Symptom:** the maintainer asked directly whether the sensorgram's per-step overlay bars (marking
when each experiment-plan step ran) had "the same issue" as C1/C5/C6 - they did. The overlay's
bars stayed positioned at their measurement-relative location even after switching back to session
view, instead of moving to the step's true session-relative position.
**Root cause:** same family as C1/C5/C6, third occurrence. `record_sensorgram_control_step_event`
is only ever called while a measurement is recording, and stored `elapsed_s = payload["t_ms"] / 1000`
- the same measurement-relative value written to the per-measurement HDF5 file. That's the right
value for that file, but the overlay is redrawn against the *sensorgram plot's own* x-axis, which
switches to session-relative the moment the display goes back to session view on Stop - the events
list was never rebased, and unlike the metric curves (fixed by C6, computed at every new point) the
overlay's events are a fixed record of the past with no new points arriving to naturally correct
via a fresh anchor.
**Fix:** don't bake in a fixed elapsed_s at all - events now also store their absolute
`timestamp_utc_ms` (already available on the payload, added for C6's HDF5-durability work), and
`sync_sensorgram_control_step_overlay`/`sensorgram_control_step_overlay_current_elapsed_s` recompute
`elapsed_s` fresh on every sync, via the new `_rebase_sensorgram_control_step_events`, against
`display_time_anchor` (originally `plot_controller.py`'s `_sensorgram_axis_start_datetime`,
relocated to `gui/sensorgram_time_anchor.py` by C8 below) - the same anchor resolver the axis's own
clock-mode display uses, so the overlay is guaranteed to agree with whatever anchor the plot itself
is using, in both session and measurement view, without duplicating any anchor-selection logic. The
stored fallback `elapsed_s` (from `t_ms`) is kept only for events that somehow lack a timestamp; a
missing/unavailable anchor leaves events untouched rather than raising.
**Effort:** Small (~50 lines: one new pure helper, two call sites updated, one field added at
record time)
**Risk:** Low - the rebase is a no-op (returns the same list) whenever no anchor is resolvable, and
`build_sensorgram_control_step_overlay_segments` (the actual segment-building logic, unit-tested
separately) is untouched - only the elapsed_s values fed into it changed.

### C8 — 2026-07-23 follow-up: unified the anchor-selection logic itself into one module
**Files:** `gui/sensorgram_time_anchor.py` (new), `gui/plot_controller.py`,
`gui/acquisition_controller.py`, `gui/main_window_sensorgram_overlay.py`,
`gui/main_window_sensorgram.py`, `gui/runtime_diagnostics.py`, `gui/main_window.py`,
`gui/main_window_plot_settings.py`
**Rationale:** the maintainer asked directly why C1/C5/C6/C7 kept recurring instead of being fixed
once - because each was a different call site independently reimplementing "which time anchor
applies right now" and drifting out of sync with the others. There was no single source of truth.
**Change:** extracted three functions into a new dedicated module:
- `session_time_anchor(window)` - the stable, whole-session anchor (`_metric_archive_started_at`,
  falling back to `_live_trace_started_at` only before the session writer exists yet).
- `display_time_anchor(window)` - whichever anchor the plot is showing right now
  (measurement-relative while `_measurement_active`, session-relative otherwise).
- `measurement_to_session_offset_s(window)` - the conversion C5's tail-rebase needs.

Every scattered call site now delegates to these instead of reimplementing the selection: C6's two
`acquisition_controller.py` functions, C5's rebase-offset calculation in `stop_measurement_run`,
C7's overlay rebase, and `plot_controller.py`'s/`main_window_sensorgram.py`'s/
`runtime_diagnostics.py`'s clock-mode-display anchor lookups (previously
`_sensorgram_axis_start_datetime`, now deleted).

Also retired `window._sensorgram_axis_started_at` entirely (write sites in
`_start_live_acquisition`, `start_measurement_run`, `stop_measurement_run`, and the manual
display-mode switch in `main_window_plot_settings.py`, plus the attribute declaration in
`MainWindow.__init__`) - it was a redundant shadow copy of `_measurement_started_at`/
`_live_trace_started_at` that existed only to feed the old chain-based lookup, and was itself a
smaller instance of the same class of bug: it also got reset whenever live acquisition restarted
mid-session (independent of any measurement), which could have shown a wrong clock-mode timestamp
in an edge case nobody had reported yet. `display_time_anchor` doesn't need it - `session_time_anchor`
is stable across live-acquisition restarts by construction.
**Effort:** Medium (one new ~80-line module, seven call sites migrated, one attribute retired across
five write sites)
**Risk:** Low - confirmed via a full re-run of the C5/C6 reproduction scripts against the refactored
code, producing byte-identical results to before the refactor, plus the full test suite.

### C9 — 2026-07-23 follow-up: session view only ever showed the most recent measurement's step overlay
**Files:** `gui/acquisition_controller.py` (`start_measurement_run`, `stop_measurement_run`),
`gui/main_window_sensorgram_overlay.py` (new `_visible_sensorgram_control_step_events`,
`close_sensorgram_control_step_overlay_segment`), `gui/main_window.py`
**Symptom:** the maintainer wanted session view to show step markers from *every* measurement run
during the session (run twice, see both sets of markers), not just the latest - "steps are written
down for every data point... maybe issue is only in display" (correct diagnosis).
**Root cause:** `start_measurement_run` called `window._sensorgram_control_step_events.clear()` on
every Record press, discarding the previous recording's overlay events even though the underlying
data itself (the session file's `experiment_control_runtime` table, durably logged since Part A of
the 2026-07-22 work) was never lost - purely an in-memory display list being wiped too eagerly.
**Fix:** stopped clearing the events list at measurement start - it now accumulates across the
whole session, exactly mirroring how the session file itself already accumulates every
measurement's flow-state rows. Two things had to be added to make that safe:
- `sync_sensorgram_control_step_overlay` now filters through `_visible_sensorgram_control_step_events`,
  which restricts to the current measurement's own events while one is actively recording (so
  "measurement" view doesn't also pick up every earlier recording's steps) but returns everything
  otherwise (so session view shows the full accumulated history).
- `stop_measurement_run` now calls a new `close_sensorgram_control_step_overlay_segment`, which
  appends a synthetic STOP-state boundary event if the last recorded event wasn't already STOP.
  Without it, if the experiment-control plan was still RUN/HOLD/PAUSE when recording stopped (a
  real, common case - recording and the plan are independent controls), the segment-builder's
  existing "don't extend past a STOP event" guard would never trigger, and the idle gap between two
  separate measurements would render as one continuous "step still running" bar spanning the whole
  gap.
`clear_trace_history_for` (main_window_plotting.py) remains the actual "wipe everything" point, for
when the whole session resets - unchanged.
**Effort:** Small (~50 lines: one filter function, one boundary-marker function, one line removed)
**Risk:** Low - the filter is a no-op (returns the same list) whenever not actively measuring, and
the boundary marker is a no-op whenever the last event is already STOP; verified with a real
multi-measurement reproduction (two measurements' events, a boundary between them, confirmed the
segment-builder produces two separate segments instead of one spanning the gap).

### C10 — 2026-07-23 follow-up: double-clicking a timeline step while idle moved devices with no way to stop them
**Files:** `gui/experiment_control_window.py` (`_apply_selected_experiment_control_step`)
**Symptom:** the maintainer reported that double-clicking a step on the timeline applied it to the
real devices even when the experiment plan wasn't running, holding, or paused - moving pumps/valves/
the M-switch into an "awkward state" with no Stop control available to undo it (Stop is gated on
`_plan_running`/`_plan_holding`/`_plan_paused`, none of which a bare double-click outside those
states ever sets).
**Root cause:** `_apply_selected_experiment_control_step` (wired to the timeline widget's
`step_double_activated` signal) had three correctly-gated branches for running/holding/paused, but
its fallback (none of the three) called `_jump_to_experiment_control_step(row)` *and* separately
`self._apply_step_to_pump_async(steps[row], start=True)` - an extra, unconditional hardware action
`_jump_to_experiment_control_step`'s own idle branch never takes on its own (it only selects the
row).
**Fix:** removed the extra `_apply_step_to_pump_async` call from the idle fallback - double-clicking
a step while idle now only selects/highlights it (matching `_jump_to_experiment_control_step`'s own
idle behavior), never touches hardware. The three active-state branches are untouched.
**Effort:** Trivial (one line removed)
**Risk:** Low - `_apply_selected_experiment_control_step` has exactly one caller
(`step_double_activated`), so this couldn't affect any other code path.

### C11 — 2026-07-23 follow-up: sensorgram overlay labels ignored the timeline widget's label mode
**Files:** `gui/experiment_control_window.py` (`_emit_experimental_control_state`, new
`_experiment_control_step_label_for_overlay`)
**Symptom:** the maintainer noticed the sensorgram's step-overlay labels never matched the
experiment-control panel's own timeline (`PumpPlanTimelineWidget`) - the timeline can show either
each step's comment or its color's palette name (toggled via the small button in its corner,
`_label_mode` - `"comment"` vs `"color_name"`), but the sensorgram overlay always showed the
comment, regardless of that setting.
**Root cause:** `_emit_experimental_control_state` (which builds the payload
`record_sensorgram_control_step_event` reads its `label` field from) hardcoded
`str(step.description or "").strip()` - never consulting
`self._experiment_control_timeline_label_mode` (the window's own persisted setting, pushed down to
`self.timeline_widget.set_label_mode(...)` whenever it changes) or the timeline widget's existing
color-name resolution logic at all.
**Fix:** new `_experiment_control_step_label_for_overlay(step)` delegates directly to
`self.timeline_widget._step_label_text(step)` - the exact method the timeline widget itself uses to
decide what to draw - so the two can never drift out of sync again; falls back to
`step.description` only if the timeline widget isn't built yet or its label resolution raises.
`_emit_experimental_control_state` now calls this instead of hardcoding the description.
**Note:** originally resolved once at record time, not retroactively - see C12 immediately below,
which the maintainer asked for after finding that choice didn't match the timeline widget's own
live-updating behavior.
**Effort:** Small (~15 lines: one new delegating method, one call-site swap)
**Risk:** Low - `PumpPlanTimelineWidget._step_label_text` is pure logic (reads only
`_label_mode`/`_color_palette_entries`, both already kept in sync with the window's own settings),
reused as-is rather than reimplemented.

### C12 — 2026-07-23 follow-up: overlay labels didn't update live when the label-mode switch was toggled mid-measurement
**Files:** `gui/main_window_sensorgram_overlay.py` (new
`_refresh_sensorgram_control_step_event_labels`), `gui/experiment_control_window.py`
(`_cycle_experiment_control_timeline_label_mode`)
**Symptom:** the maintainer asked to check for "some block or not good wiring" after finding that
toggling the timeline's comment/color-name switch during a measurement had no visible effect on the
sensorgram overlay - the timeline widget itself updates instantly (it resolves its label fresh on
every repaint), so the overlay's lack of a live response looked broken by comparison.
**Root cause:** C11 deliberately resolved each event's label once, at record time - deliberate, but
wrong once compared directly against the timeline widget's own always-fresh behavior, which is what
the maintainer (reasonably) expected parity with. Toggling only pushed the new mode into
`self.timeline_widget` (via `_update_experiment_control_timeline_label_mode`); nothing told the
sensorgram overlay's events to reflect it, and nothing re-synced the overlay either, so even the
next natural sync (driven by the plot's own tick/view-range cadence) would have kept showing the
stale label until a fresh step transition happened to overwrite it.
**Fix:** two parts, mirroring how C7 already keeps `elapsed_s` fresh instead of trusting a
record-time value:
- New `_refresh_sensorgram_control_step_event_labels`, called from `sync_sensorgram_control_step_overlay`
  right alongside the existing elapsed_s rebase, re-resolves every event's label via
  `_experiment_control_step_label_for_overlay` (C11) against whichever step it points to and the
  *current* label mode - every sync, not just at record time. Events without a resolvable
  `step_index` (the C9 STOP-boundary marker on a legacy path, or a malformed payload) are left
  untouched.
- `_cycle_experiment_control_timeline_label_mode` now also triggers an immediate
  `_sync_sensorgram_control_step_overlay()` (via `recording_controller`) right after updating the
  timeline widget, so the overlay updates the instant the toggle is clicked instead of waiting for
  the next incidental sync.
**Effort:** Small (~50 lines: one new refresh function, one call-site addition, one sync trigger)
**Risk:** Low - the refresh is a no-op for any event whose step can't be resolved, and reuses
`_experiment_control_step_label_for_overlay`'s existing exception handling; verified live in the
running app (toggling mid-measurement, with no new step transition, visibly changed an
already-drawn segment's label from `"-"` to a resolved color name).

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
