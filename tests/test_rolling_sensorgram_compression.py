from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from lspr_app.gui.plot_view_cache import PlotViewCache  # noqa: E402


class RollingSensorgramCompressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cache = PlotViewCache()
        self.token = ("smoothed_max", "history", "metric", 1)
        self.x = np.arange(0.0, 3600.0 + 0.2, 0.2, dtype=np.float64)
        self.y = np.sin(self.x / 30.0).astype(np.float64)

    def test_rolling_window_is_bounded_and_clipped(self) -> None:
        display_x, display_y = self.cache.rolling_metric_view(
            self.token,
            self.x,
            self.y,
            view_x_min=3300.0,
            view_x_max=3600.0,
            view_width_px=512.0,
            enabled=True,
            minimum_points=128,
            oversample=1.0,
            default_points=512,
            recent_tail_points=300,
        )

        self.assertGreater(len(display_x), 0)
        self.assertLessEqual(len(display_x), 512)
        self.assertGreaterEqual(float(display_x[0]), 3300.0)
        self.assertLessEqual(float(display_x[-1]), 3600.0)
        self.assertEqual(len(display_x), len(display_y))

    def test_short_rolling_window_stays_nearly_raw(self) -> None:
        display_x, display_y = self.cache.rolling_metric_view(
            self.token,
            self.x,
            self.y,
            view_x_min=3540.0,
            view_x_max=3600.0,
            view_width_px=2048.0,
            enabled=True,
            minimum_points=128,
            oversample=1.0,
            default_points=2048,
            recent_tail_points=300,
        )

        self.assertGreater(len(display_x), 0)
        self.assertLessEqual(len(display_x), len(self.x[self.x >= 3540.0]))
        self.assertGreaterEqual(float(display_x[0]), 3540.0)
        self.assertLessEqual(float(display_x[-1]), 3600.0)
        self.assertEqual(len(display_x), len(display_y))


if __name__ == "__main__":
    unittest.main()
