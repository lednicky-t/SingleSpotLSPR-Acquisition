# Runtime Pipeline Architecture

This document defines the core runtime rules for acquisition, processing, plotting, and file writing in `sLSPR acq`.

## Hard Rules

- Raw acquisition and raw storage are lossless.
- The user-controlled refresh rate applies to processing and display, not to raw recording.
- Latest-only queues are forbidden on the raw recording path.
- Processing and plotting may skip old frames to stay fresh.
- File writing must be asynchronous, but it must receive every raw acquired spectrum.
- UI dropped frames must be counted separately from raw recording loss.

## Required Behavior

- Acquisition code must append every raw spectrum to the recording pipeline.
- GUI throttling and coalescing may reduce display work, but they must never discard raw acquisition data.
- Any skipped UI frame must be visible in diagnostics as a UI/display drop, not as recording loss.
- Buffering is allowed when needed for file I/O, but buffers must be lossless until the writer has persisted the data.
- If a backlog grows, the display may lag or skip, but the raw measurement stream must remain complete.

## Review Checklist

- Does this change preserve every raw acquired spectrum?
- Does this change keep display refresh separate from raw capture?
- Does this change avoid latest-only behavior on the recording path?
- Does this change update diagnostics to distinguish UI drops from raw data loss?
- Does this change keep file writing asynchronous without dropping data?

## Notes

This document is intentionally strict. If a future implementation needs to relax one of these rules, it should be discussed explicitly and documented here before the behavior changes.
