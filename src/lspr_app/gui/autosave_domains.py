from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter

from PyQt6.QtCore import QTimer

from lspr_app.storage.app_config import save_app_setting


@dataclass(frozen=True, slots=True)
class AutosaveDomain:
    """One "own QTimer + housekeeping-poll fallback" persisted-state domain.

    Both paths save the same way on purpose (see drain_gui_housekeeping_tasks in
    main_window_runtime.py): a save still happens even if one path is disabled or
    misbehaves. This dataclass exists so that redundancy only has to be wired up
    once per domain instead of by hand across main_window.py, main_window_lifecycle.py,
    and main_window_runtime.py. It intentionally covers only domains that already
    share this exact shape (single-shot timer, its own enable checkbox) - log_buffer
    and session_stats_recording do not, and are not forced into this shape.

    Deliberately has no dependency on main_window_state.py (which owns the actual
    save_ui_state/persist_acquisition_state functions and defines the concrete
    UI_STATE_DOMAIN/ACQUISITION_STATE_DOMAIN instances) - that keeps the import
    direction one-way (main_window_state -> autosave_domains) so main_window_state
    can keep exposing schedule_acquisition_state_persist as a plain, standalone,
    directly-testable function (see tests/integration/test_main_window_state.py).
    """

    name: str
    timer_attr: str
    requested_at_attr: str
    enabled_attr: str
    mirror_enabled_attr: str | None
    interval_ms: float
    save_fn: Callable[[object], None]
    timing_label: str
    enabled_setting_key: str
    log_label: str
    persist_method_name: str
    # Pre-existing, distinct behavior between the two domains, preserved exactly:
    # ui_state clears its _requested_at when autosave is off; acquisition_state
    # only stops the timer and leaves _requested_at as-is. Not unified here since
    # that would be a behavior change, not a refactor.
    clear_requested_at_when_disabled: bool


def init_autosave_timer(window, domain: AutosaveDomain) -> None:
    """Construct, configure, and connect *domain*'s QTimer; init its tracking attrs.

    Connects to window's existing named method (e.g. window._save_ui_state) rather
    than calling persist_autosave directly - that method is also called directly
    from other places (window close, plot-settings dialog), so it has to keep
    existing as a real, independently-callable method, not just a timer target.
    """
    timer = QTimer(window)
    timer.setSingleShot(True)
    timer.setInterval(int(domain.interval_ms))
    timer.timeout.connect(getattr(window, domain.persist_method_name))
    setattr(window, domain.timer_attr, timer)
    setattr(window, domain.requested_at_attr, None)
    setattr(window, f"_last_{domain.name}_delay_ms", None)
    setattr(window, f"_last_{domain.name}_save_ms", None)
    setattr(window, f"_last_{domain.name}_total_ms", None)


def schedule_autosave(window, domain: AutosaveDomain) -> None:
    if not getattr(window, domain.enabled_attr, True):
        if domain.clear_requested_at_when_disabled:
            setattr(window, domain.requested_at_attr, None)
        timer = getattr(window, domain.timer_attr, None)
        if timer is not None:
            timer.stop()
        return
    setattr(window, domain.requested_at_attr, perf_counter())
    timer = getattr(window, domain.timer_attr, None)
    if timer is not None:
        timer.start()


def persist_autosave(window, domain: AutosaveDomain) -> None:
    started = perf_counter()
    requested_at = getattr(window, domain.requested_at_attr, None)
    if requested_at is not None:
        try:
            setattr(window, f"_last_{domain.name}_delay_ms", max((started - float(requested_at)) * 1000.0, 0.0))
        except (TypeError, ValueError):
            setattr(window, f"_last_{domain.name}_delay_ms", None)

    def _callback() -> None:
        domain.save_fn(window)

    window._run_gui_callback_timed(domain.timing_label, _callback)
    setattr(window, f"_last_{domain.name}_save_ms", (perf_counter() - started) * 1000.0)
    setattr(window, domain.requested_at_attr, None)


def set_autosave_enabled(window, domain: AutosaveDomain, enabled: bool) -> None:
    enabled = bool(enabled)
    setattr(window, domain.enabled_attr, enabled)
    if domain.mirror_enabled_attr is not None:
        setattr(window, domain.mirror_enabled_attr, enabled)
    save_app_setting(domain.enabled_setting_key, enabled)
    timer = getattr(window, domain.timer_attr, None)
    if not enabled and timer is not None:
        timer.stop()
        setattr(window, domain.requested_at_attr, None)
    state_text = "enabled" if enabled else "disabled"
    window._log_info(f"{domain.log_label} {state_text}.")


def due_autosave_domain(window, now: float, domains: tuple[AutosaveDomain, ...]) -> AutosaveDomain | None:
    """First domain in *domains* whose request has aged past its timer's
    interval, or None. Mirrors the _due() helper in drain_gui_housekeeping_tasks."""
    for domain in domains:
        requested_at = getattr(window, domain.requested_at_attr, None)
        if requested_at is None:
            continue
        timer = getattr(window, domain.timer_attr, None)
        interval_ms = float(timer.interval()) if timer is not None else domain.interval_ms
        try:
            due = (now - float(requested_at)) * 1000.0 >= interval_ms
        except (TypeError, ValueError):
            due = False
        if due:
            return domain
    return None
