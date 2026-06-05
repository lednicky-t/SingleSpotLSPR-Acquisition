# Codex Runtime Simplicity Guide

This guide sets the default runtime shape for the live measurement app.

## Core rule

Prefer simple, explicit runtime architecture over layered helper machinery.

Do not solve performance or coordination problems by adding more schedulers, deferred tasks, queues, throttles, wrappers, or diagnostics unless there is a measured need and no simpler alternative.

## Mental model

The live app should be easy to explain:

```text
Device acquisition -> raw saving -> latest data for UI -> optional processing -> plotting
```

If a change makes the live pipeline harder to explain, it is probably the wrong change.

## Two layers

### Layer 1: acquisition and raw recording

This layer must be lossless.

- Acquire raw data continuously.
- Timestamp raw data.
- Save raw data continuously when recording is active.
- Do not depend on GUI speed.
- Do not depend on plotting speed.
- Do not lose raw data because the UI is slow.

Manual UI refresh rate does not apply here.

### Layer 2: processing, fitting, metrics, and plotting

This layer may be freshness-based.

It may:

- process only the newest spectrum;
- skip stale spectra;
- update plots at the manual refresh rate;
- drop old UI preview frames;
- downsample displayed data;
- throttle expensive visual updates.

## Hard rule

```text
Raw acquisition and raw saving are lossless.
Processing and plotting may be lossy/freshness-oriented.
```

Never mix those two concepts.

## Preferred runtime shape

Use a simple flow:

```text
acquisition worker -> lossless recording queue -> writer
                   -> latest UI preview slot
                   -> latest processed result slot

GUI timer -> read newest state -> update only what changed
```

## Allowed queues

Use queues only at real boundaries.

Allowed:

- device/acquisition worker -> lossless recording writer queue
- device/acquisition worker -> latest UI preview slot/queue
- optional processing worker -> latest processed result slot/queue

Not allowed unless strongly justified:

```text
queue -> scheduler -> deferred flush -> throttle -> another queue -> another scheduler
```

## Timer and scheduler rule

Prefer one clear GUI refresh cadence where possible.

Avoid a separate live task for every small operation.

Use additional timers only when they have a clear independent cadence, such as:

- slow status summary every 1 to 2 seconds;
- housekeeping every several seconds;
- autosave only when settings changed;
- low-frequency log buffer flush.

## Helper rule

Helpers are good for pure logic.

Dangerous helpers are control-flow wrappers that only add orchestration.

Before adding a helper, answer:

1. What repeated code does this simplify?
2. Does it cross a real boundary?
3. Does it introduce hidden state?
4. Does it add another schedule/defer/coalesce step?
5. Can the same result be achieved by deleting or simplifying code?

If the helper mainly adds orchestration, do not add it.

## Diagnostics rule

Diagnostics must not slow down the app.

- Do not spam the GUI log during live acquisition.
- Store performance diagnostics as counters/timers.
- Show them in stats panels or write them to file at low frequency.
- Do not write routine timing warnings into a QTextEdit or QPlainTextEdit on every refresh.
- If throttled logging is needed, throttle by key, not exact message text.

## Plotting rule

Plotting should be bounded and state-based.

- Do not send full growing history to live plots.
- Downsample or window display arrays.
- Use cached display arrays.
- Skip `setData()` if display output did not change.
- Do not call `setXRange()`, `setYRange()`, `autoRange()`, `setLabel()`, `setVisible()`, or legend updates if the state is unchanged.
- Disable antialiasing during live acquisition unless there is a measured reason to keep it.

## Raw recording rule

Raw recording must be independent from GUI.

Forbidden:

```text
acquisition -> latest-only GUI queue -> GUI drains newest only -> save newest only
```

Allowed:

```text
acquisition -> lossless recording queue -> writer
           -> latest-only UI state
```

UI skipped frames are acceptable.
Raw recording loss is not acceptable.

## Manual refresh rate rule

Manual refresh rate controls processing/display freshness, not device acquisition.

Correct behavior:

- save all raw spectra;
- process/display about the manual rate or less;
- skip stale spectra for UI if needed;
- keep GUI responsive.

Incorrect behavior:

- lower raw acquisition because GUI refresh is lower;
- save only displayed spectra;
- drop raw spectra because GUI queue is full.

## Anti-patterns to avoid

- Adding a new scheduler task to fix every timing issue.
- Adding a new queue when a latest-value slot is enough.
- Adding a latest-only queue on the raw recording path.
- Adding logging that writes to the GUI during a high-frequency loop.
- Adding caches without invalidation rules.
- Adding helper wrappers that call other wrappers that schedule more wrappers.
- Fixing slow UI by slowing raw acquisition.
- Fixing a plot problem by deleting raw history.
- Using warnings for normal live-loop timing.
- Rebuilding full arrays during every GUI tick.

## Required bug-fix mindset

When fixing bugs, first simplify the data path.

Do not wrap a bad flow in more helpers. A correct fix should make the live pipeline easier to explain, not harder.

## Acceptance tests

The architecture is good only if these remain true:

- raw recording stays lossless;
- UI remains fresh under load;
- one-metric and three-metric runs do not collapse the scheduler;
- diagnostics do not cause slowdown;
- a developer can explain the live data path in under one minute.

