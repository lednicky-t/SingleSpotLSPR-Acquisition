from __future__ import annotations

import sys
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from lspr_app.gui.sensorgram_display_state import SensorgramDisplayState  # noqa: E402


class SensorgramDisplayStateDefaultsTests(unittest.TestCase):
    def test_defaults(self) -> None:
        state = SensorgramDisplayState()
        self.assertEqual(state.mode, "session")
        self.assertFalse(state.view_locked)
        self.assertFalse(state.frozen)
        self.assertFalse(state.reload_loading)
        self.assertIsNone(state.reload_task)
        self.assertIsNone(state.reload_request_token)
        self.assertIsNone(state.reload_pending_token)


class MeasurementTransitionTests(unittest.TestCase):
    def test_begin_measurement_sets_mode_and_clears_view_lock(self) -> None:
        state = SensorgramDisplayState(mode="session", view_locked=True, frozen=True)

        state.begin_measurement()

        self.assertEqual(state.mode, "measurement")
        self.assertFalse(state.view_locked)
        # frozen is untouched by measurement transitions - it's an independent
        # user-driven freeze, not something that should be silently cleared.
        self.assertTrue(state.frozen)

    def test_end_measurement_resets_mode_and_clears_view_lock(self) -> None:
        state = SensorgramDisplayState(mode="measurement", view_locked=True, frozen=True)

        state.end_measurement()

        self.assertEqual(state.mode, "session")
        self.assertFalse(state.view_locked)
        self.assertTrue(state.frozen)

    def test_begin_measurement_from_already_unlocked_state_is_a_no_op_on_lock(self) -> None:
        state = SensorgramDisplayState(mode="session", view_locked=False)

        state.begin_measurement()

        self.assertEqual(state.mode, "measurement")
        self.assertFalse(state.view_locked)


class ReloadTransitionTests(unittest.TestCase):
    def test_begin_reload_sets_loading_and_clears_pending(self) -> None:
        state = SensorgramDisplayState(reload_pending_token=("stale",))
        task = object()

        state.begin_reload(("token-1",), task)

        self.assertTrue(state.reload_loading)
        self.assertEqual(state.reload_request_token, ("token-1",))
        self.assertIsNone(state.reload_pending_token)
        self.assertIs(state.reload_task, task)

    def test_finish_reload_clears_loading_and_task_but_keeps_request_token(self) -> None:
        state = SensorgramDisplayState()
        state.begin_reload(("token-1",), object())

        state.finish_reload()

        self.assertFalse(state.reload_loading)
        self.assertIsNone(state.reload_task)
        # The request token is the last-completed reload's identity, not
        # in-flight bookkeeping - finish_reload should not erase it.
        self.assertEqual(state.reload_request_token, ("token-1",))

    def test_second_begin_reload_while_first_still_loading_replaces_it(self) -> None:
        # Mirrors request_absolute_sensorgram_metric_archive_reload's real
        # pattern: a second request that arrives while one is already loading
        # gets stashed as reload_pending_token by the caller *before* calling
        # begin_reload again once the first finishes - begin_reload itself
        # always clears any pending token, since by the time it runs the
        # pending request is the one becoming the new in-flight request.
        state = SensorgramDisplayState()
        first_task = object()
        second_task = object()

        state.begin_reload(("token-1",), first_task)
        state.reload_pending_token = ("token-2",)
        state.begin_reload(("token-2",), second_task)

        self.assertTrue(state.reload_loading)
        self.assertEqual(state.reload_request_token, ("token-2",))
        self.assertIsNone(state.reload_pending_token)
        self.assertIs(state.reload_task, second_task)


if __name__ == "__main__":
    unittest.main()
