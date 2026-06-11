# Changelog

All notable changes to LSPR Acquisition are tracked here.

The project uses semantic versioning: `MAJOR.MINOR.PATCH`.

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
