from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QSignalBlocker
from PyQt6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QSpinBox


class UIPersistenceBinder:
    """Binds UI controls to a flat key-value state dict for save/restore.

    Each bind_*() call registers a control with a JSON key. collect() returns
    a snapshot dict suitable for merging into the main UI state; apply() sets
    all registered controls from a previously saved dict.

    An optional on_change callback is connected to every bound widget's change
    signal so that saves are scheduled automatically on interaction — pass
    window._schedule_ui_state_persist to get the same 500 ms debounced save
    that splitter moves and other controls already use.

    Integration pattern
    -------------------
    At window/dialog init time:
        self._ui_binder = UIPersistenceBinder(on_change=self._schedule_ui_state_persist)
        self._ui_binder.bind_check(self.antialias_check, "plot_antialias_enabled", False)
        self._ui_binder.bind_spinbox(self.my_spin, "my_spin_key", default=100)

    In save_ui_state():
        state = {
            "x": x_pos,
            ...other explicit fields...,
            **window._ui_binder.collect(),   # auto-collected binder keys
        }

    In restore_ui_state():
        window._ui_binder.apply(ui_state)    # auto-restored binder keys

    New controls just call bind_*() once at creation; they are then covered by
    the existing save/restore cycle without any further changes.
    """

    def __init__(self, *, on_change: Callable[[], None] | None = None) -> None:
        self._on_change = on_change
        # (key, getter, setter, default)
        self._entries: list[tuple[str, Callable[[], Any], Callable[[Any], None], Any]] = []

    def _register(
        self,
        key: str,
        get: Callable[[], Any],
        set_: Callable[[Any], None],
        default: Any,
        signal: Any = None,
    ) -> None:
        self._entries.append((key, get, set_, default))
        if signal is not None and self._on_change is not None:
            signal.connect(lambda *_: self._on_change())

    # ------------------------------------------------------------------
    # Per-widget bind helpers
    # ------------------------------------------------------------------

    def bind_check(self, widget: QCheckBox, key: str, default: bool = False) -> None:
        def _set(v: Any) -> None:
            with QSignalBlocker(widget):
                widget.setChecked(bool(v))
        self._register(key, lambda: bool(widget.isChecked()), _set, default, widget.toggled)

    def bind_combo_data(self, widget: QComboBox, key: str, default: Any = None) -> None:
        """Bind by itemData (not display text). Restores by matching itemData."""
        def _set(v: Any) -> None:
            with QSignalBlocker(widget):
                for i in range(widget.count()):
                    if widget.itemData(i) == v:
                        widget.setCurrentIndex(i)
                        return
                for i in range(widget.count()):
                    if widget.itemData(i) == default:
                        widget.setCurrentIndex(i)
                        return
        self._register(key, lambda: widget.currentData(), _set, default, widget.currentIndexChanged)

    def bind_spinbox(self, widget: QSpinBox, key: str, default: int = 0) -> None:
        def _set(v: Any) -> None:
            with QSignalBlocker(widget):
                try:
                    widget.setValue(int(v))
                except (TypeError, ValueError):
                    widget.setValue(default)
        self._register(key, lambda: int(widget.value()), _set, default, widget.valueChanged)

    def bind_double_spinbox(
        self, widget: QDoubleSpinBox, key: str, default: float = 0.0
    ) -> None:
        def _set(v: Any) -> None:
            with QSignalBlocker(widget):
                try:
                    widget.setValue(float(v))
                except (TypeError, ValueError):
                    widget.setValue(default)
        self._register(key, lambda: float(widget.value()), _set, default, widget.valueChanged)

    def bind_custom(
        self,
        key: str,
        *,
        get: Callable[[], Any],
        set_: Callable[[Any], None],
        default: Any = None,
        signal: Any = None,
    ) -> None:
        """Register an arbitrary getter/setter pair for controls not covered
        by the typed helpers above (e.g. window attributes, custom widgets)."""
        self._register(key, get, set_, default, signal)

    # ------------------------------------------------------------------
    # Bulk save / restore
    # ------------------------------------------------------------------

    def collect(self) -> dict[str, Any]:
        """Return a dict of all registered keys and their current values."""
        result: dict[str, Any] = {}
        for key, get, _set, default in self._entries:
            try:
                result[key] = get()
            except Exception:
                result[key] = default
        return result

    def apply(self, state: dict[str, Any]) -> None:
        """Set all registered controls from *state*, falling back to defaults."""
        for key, _get, set_, default in self._entries:
            value = state.get(key, default)
            try:
                set_(value)
            except Exception:
                try:
                    set_(default)
                except Exception:
                    pass
