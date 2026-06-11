from __future__ import annotations

import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from lspr_app.gui.sensorgram_display_window_cycle import cycle_sensorgram_display_window_s  # noqa: E402


class SensorgramDisplayWindowCycleTest(unittest.TestCase):
    def test_display_window_cycles_in_order(self) -> None:
        current = 60.0
        expected_sequence = [300.0, 900.0, 1800.0, 3600.0, 60.0]

        for expected in expected_sequence:
            current = cycle_sensorgram_display_window_s(current)
            self.assertEqual(current, expected)


if __name__ == "__main__":
    unittest.main()
