# Flow Table Migration Plan

Status: deferred

This is a future migration note for the experiment-plan table. The current table is responsive enough, so this should not be implemented now. Keep this document as the reference for when the existing table UI starts feeling slow again.

## Why This Migration Exists

The current table uses many live widgets per row. That is convenient and visually rich, but it becomes expensive when:

- the plan has many steps
- edits trigger full-table refreshes
- import or reorder operations become noticeably laggy
- the app starts spending time creating, destroying, or updating many cell widgets

The long-term goal is a `QTableView` + model/view implementation with delegates only where needed.

## Expected Benefits

- Faster startup and faster plan refreshes
- Better scaling for long experiment plans
- Less memory usage because the view does not own one widget per cell
- Cleaner separation between data, presentation, and hardware logic
- Easier caching of derived values such as timing, labels, and color text
- Better foundation for future features like sorting, filtering, undo/redo, and batch editing

## When To Consider Doing It

This migration is appropriate when one or more of the following becomes true:

- the table visibly lags while editing single rows
- import of large experiment plans freezes the UI
- moving, duplicating, or reordering rows becomes sluggish
- the number of visible or total rows grows enough that widget creation is a problem
- the code starts accumulating more per-cell widget special cases

Practical trigger:

- if the table starts feeling slow during normal use, especially on plans with many steps, this migration should be prioritized

## Migration Strategy

1. Move the plan data into a table model.
2. Keep `PumpPlanStep` as the canonical row data.
3. Cache derived timing so only affected rows are recomputed.
4. Use delegates only for editable columns that need custom UI.
5. Keep the timeline, hardware control, and file I/O logic outside the table itself.
6. Preserve the existing visual style and interaction behavior as closely as possible.

## Columns And Proposed Handling

### Model-owned columns

- Step number
- Duration
- Start time
- End time
- Per-channel flow
- Per-channel direction
- Tube diameter
- Valve state
- Switch position
- Color
- Comment

### Delegate or view-level interactions

- Duration and flow should use spinbox delegates
- Comment should use a line-edit delegate
- Switch can use a combo delegate or wheel cycling
- Color should use a custom painted cell with a combo delegate only if needed
- Valve is usually better handled as a direct click toggle in the view

## Cache Plan

Recommended caches in the model or controller:

- row -> `PumpPlanStep`
- recomputed step timing
- color label lookup
- switch label lookup
- derived display strings for fast painting

Update policy:

- when one row changes, recompute timing from that row forward
- do not reread the whole table from the GUI unless a full refresh is unavoidable

## Behavior To Preserve

The migration should keep the current behavior as much as possible:

- row selection stays visible
- current step stays synced with the timeline
- valve can be toggled quickly
- switch and color remain easy to change
- wheel scrolling stays useful on numeric and choice columns
- row colors and compact styling remain intact
- import/export still works with the same plan data

## Notes

- Do not start this migration unless the current table becomes a real usability bottleneck.
- Prefer keeping the current widget table while it is still fast enough.
- If the migration starts, do it in small steps so the app stays usable throughout.
