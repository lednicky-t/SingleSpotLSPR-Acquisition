"""Single source of truth for "which time anchor applies right now" for the
sensorgram - its live display cache, its persisted session-file writes, and
its per-step overlay.

Before this module existed, several call sites each independently
reimplemented this selection logic and drifted out of sync with each other
whenever a measurement started or stopped - the root cause behind bugs
C1, C5, C6, and C7 in docs/sensorgram_improvements.md. Every one of those
was some variant of "this code used the wrong anchor for the current
display mode." Centralizing the selection here means there is exactly one
place to get it right, and every consumer automatically stays consistent
with every other consumer.
"""

from __future__ import annotations

from datetime import datetime


def session_time_anchor(window) -> datetime | None:
    """The stable, whole-session-lifetime anchor.

    Set once when the always-on session file's writer is created
    (storage/measurement_archive.py, ensure_session_writer) and cleared
    only when it closes (close_session_writer) - never touched by a
    measurement starting or stopping. Anything that should stay
    session-relative regardless of whether a measurement happens to be
    recording right now (the session file's own raw-spectra timeline, the
    plot once back in session view, the per-step overlay after Stop)
    anchors to this.
    """
    anchor = getattr(window, "_metric_archive_started_at", None)
    if isinstance(anchor, datetime):
        return anchor
    # Fallback for the narrow window before the session writer exists yet
    # (very first spectrum of a session, before ensure_session_writer has
    # run) - _live_trace_started_at is set when live acquisition begins,
    # which happens before the session writer can exist.
    anchor = getattr(window, "_live_trace_started_at", None)
    if isinstance(anchor, datetime):
        return anchor
    return None


def display_time_anchor(window) -> datetime | None:
    """The anchor for whatever the sensorgram plot is showing right now.

    Measurement-relative while a measurement is actively recording (so
    "measurement" view genuinely reads "5 seconds into this recording",
    not "3625 seconds into this session"), session-relative otherwise.
    This is what the live display cache (acquisition_controller.py,
    append_processed_trace_history) and the per-step overlay
    (main_window_sensorgram_overlay.py) must both agree with, and what the
    axis's own clock-mode tick formatting resolves to.
    """
    if bool(getattr(window, "_measurement_active", False)):
        anchor = getattr(window, "_measurement_started_at", None)
        if isinstance(anchor, datetime):
            return anchor
    return session_time_anchor(window)


def measurement_to_session_offset_s(window) -> float | None:
    """Seconds to add to a measurement-relative elapsed-time value to make
    it session-relative.

    Used by stop_measurement_run to rebase the live cache's tail before
    the display anchor switches back to session (see
    PlotViewCache.rebase_live_absolute_metric_recent_tail). None if either
    anchor is unavailable - nothing to rebase against.
    """
    measurement_started_at = getattr(window, "_measurement_started_at", None)
    if not isinstance(measurement_started_at, datetime):
        return None
    session_started_at = session_time_anchor(window)
    if session_started_at is None:
        return None
    return (measurement_started_at - session_started_at).total_seconds()
