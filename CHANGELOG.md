# Changelog

All notable changes to LSPR Acquisition are tracked here.

The project uses semantic versioning: `MAJOR.MINOR.PATCH`.

## 0.4.0 - Milestone: Status clarity and GUI safety polish

### Added

- Sensorgram secondary Y-axis system (FWHM, extinction, temperature, humidity) with independent zoom/pan, per-metric axis coloring, autoscale reset buttons, and per-metric line thickness/opacity controls in Sensorgram Settings.
- Extinction-maxima computation, gated to absorbance mode.
- "Manual" Y-axis-scale mode for the sensorgram that leaves the view exactly as the user set it.
- Hz/ms toggle for read-only timing displays (Preferences > Appearance).
- Live temperature/humidity readout in the titlebar, next to the device status strip.
- Center titlebar label now shows the live/measurement state and which spectra source is linked (spectrometer vs. simulation), plus a plan-state suffix and blinking record indicator while recording.
- Confirmation prompt before pausing spectrum tracing in live mode ("Are you sure to pause tracing the spectra?"); the button is disabled during measurement to prevent accidental data loss. Spectrum toolbar icon order is now Reference, Dark, Trace Run/Pause, Freeze.
- Cursor readout on both the spectrum and sensorgram plots now shows a small crosshair icon in its corner while inactive, and collapses to plain coordinates (no "cursor:" prefix) while active.
- Status bar timing values auto-switch from milliseconds to seconds above 1.5 s (and back below 1.0 s, to avoid flicker at the boundary); millisecond values are shown with one decimal place instead of two.

### Changed

- Spectrum stats panel now shows every peak-position metric currently visible on the sensorgram (not just one hardcoded pair), each colored to match its own sensorgram trace - fixes S_Max sometimes being missing from the spectrum panel, and the P_Max/S_Max naming mismatch between the two panels. Default tracking mode changed to `smoothed_max`.
- Fixed double-click-to-maximize not responding when double-clicking most of the menu bar row (File/Edit/.../Help).
- Fixed a regression where clicking the cursor readout label to toggle the plot crosshair on/off stopped working.
- Secondary axes now start autoscaled every session (no stale manual-zoom state on launch); the autoscale button moved to the x-axis tick-value row. Secondary-axis metric picker entries with no real data behind them are grayed out.

### Performance

- Fixed a long-run regression where the sensorgram control-step overlay rescanned the entire session's event history on every render tick, three times per tick, while recording.
- Fixed a render-cache key that fell back to identity comparison, defeating redundant-redraw skipping during manual pan/zoom.
- Fixed an unconditional per-tick filesystem stat call in metric-series token computation.

### Notes

- This milestone folds together the sensorgram secondary-axis feature work, its follow-up UX polish, and a round of GUI safety/clarity fixes (state visibility, accidental-pause protection, cursor-toggle regression). No HDF5 schema changes.

## 0.3.0 - Data safety, correctness, and performance improvements

### Changed

- Reduced HDF5 flush interval default from 5 s to 1 s (measurement writer and session writer fallback path).
- HDF5 file probe now runs in a background task so a slow/network drive can no longer freeze the UI during import.
- Added `_closing` guards to QRunnable signal handlers (compression finished/failed, archive reload failed, hardware init finished) so late-arriving signals after window teardown are no-ops.
- Disconnected all six experiment-control-window signals before close, preventing stale signal deliveries during window destruction.
- Fixed the experiment-control window forcing dark mode unconditionally on every open; it now syncs to the main window's theme, with an idempotency guard to prevent a theme-changed feedback loop.
- Fixed flow-plan delegate editors and table cells using hardcoded dark-mode fallback colors in light theme.
- Fixed a bug where a `return` inside `finally` silently swallowed exceptions raised by a callback.
- Fixed a settings migration bug: an absent `fit_method` was not treated as `'none'`.
- Replaced deprecated `datetime.utcnow()` calls (Python 3.12 deprecation).

### Added

- HDF5 import/export for processing settings and experiment plan.

### Performance

- Capped the plot view cache's raw ring buffer at 36,000 blocks (~2.5 h at 4 Hz) to prevent unbounded memory growth on long sessions; higher compression levels retain full-session history.

### Notes

- This entry was backfilled from the `release: v0.3.0` commit message - the changelog file wasn't updated at the time of that release.

## 0.2.0 - Milestone: Sensorgram workflow refinement

### Added

- Rolling sensorgram mode now uses bounded compression with the same display-point budget as absolute mode.
- Rolling mode preserves the selected time window while keeping the recent raw tail visible.
- Metric and preview controls now have clearer tooltips that distinguish absolute-only and preview-only behavior.
- Regression tests now cover the rolling-window cycle order and rolling-window compression clipping.

### Changed

- Fixed the rolling-window toggle so it advances one step per click and no longer skips values.
- The sensorgram settings window now routes display, line, autoscale, and envelope controls through clearer tabbed layout sections.
- Metric cache diagnostics now report the active mode, including rolling-view compression state.
- Sensorgram version bumped to `0.2.0` for this milestone.

### Notes

- The most visible milestone effects are the new rolling compression behavior, the corrected rolling toggle, and the updated sensorgram settings workflow.

## 0.1.0 - Unreleased

- Initial tracked application version.
- Added native YAML experiment-plan import/export alongside CSV/TXT compatibility.
- Documented the native experiment-plan format.
