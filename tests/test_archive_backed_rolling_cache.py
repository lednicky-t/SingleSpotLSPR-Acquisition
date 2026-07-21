from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import numpy as np

try:
    import h5py
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    h5py = None


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

try:  # pragma: no cover - environment dependent
    from lspr_app.gui.main_window_processing import sensorgram_metric_archive_names  # noqa: E402
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    sensorgram_metric_archive_names = None

try:  # pragma: no cover - environment dependent
    from lspr_app.gui.workers import MetricArchiveReloadRequest, MetricArchiveReloadTask  # noqa: E402
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    MetricArchiveReloadRequest = None
    MetricArchiveReloadTask = None

try:  # pragma: no cover - environment dependent
    from lspr_app.storage.hdf5_export import load_processed_metric_history  # noqa: E402
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    load_processed_metric_history = None

try:  # pragma: no cover - environment dependent
    from lspr_app.storage.metric_archive import load_metric_archive_history  # noqa: E402
except ModuleNotFoundError:  # pragma: no cover - environment dependent
    load_metric_archive_history = None


class ArchiveBackedReloadTests(unittest.TestCase):
    def test_processed_metric_history_respects_time_range(self) -> None:
        if h5py is None or load_processed_metric_history is None:
            self.skipTest("h5py is not available in this Python environment")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "metrics.h5"
            # Absolute Unix-epoch ms (schema 6.0+ - see docs/sensorgram_improvements.md,
            # "Correctness fixes" C3): the reader derives relative seconds from this at
            # read time, anchored to the first sample, so the expected x_values below
            # (0..10s) are unaffected by the arbitrary epoch base chosen here.
            epoch_base_ms = 1_700_000_000_000.0
            unix_ms = epoch_base_ms + np.arange(0, 11, dtype=np.float64) * 1000.0
            metric = np.arange(0, 11, dtype=np.float64)
            with h5py.File(path, "w") as handle:
                processed = handle.create_group("processed")
                metrics = processed.create_group("metrics")
                metrics.create_dataset("acquired_at_unix_ms", data=unix_ms)
                metrics.create_dataset("smoothed_max_nm", data=metric)

            series = load_processed_metric_history(path, {"smoothed_max_nm"}, time_range_s=(3.0, 7.0))

            self.assertIn("smoothed_max_nm", series)
            x_values, y_values = series["smoothed_max_nm"]
            self.assertEqual(x_values.tolist(), [3.0, 4.0, 5.0, 6.0, 7.0])
            self.assertEqual(y_values.tolist(), [3.0, 4.0, 5.0, 6.0, 7.0])

    def test_jsonl_metric_history_respects_time_range(self) -> None:
        if load_metric_archive_history is None:
            self.skipTest("JSONL metric archive loader is not available in this Python environment")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "metrics.jsonl"
            rows = [
                {"t_ms": 0.0, "smoothed_max_nm": 0.0, "poly_max_nm": 10.0},
                {"t_ms": 1000.0, "smoothed_max_nm": 1.0, "poly_max_nm": 11.0},
                {"t_ms": 2000.0, "smoothed_max_nm": 2.0, "poly_max_nm": 12.0},
                {"t_ms": 3000.0, "smoothed_max_nm": 3.0, "poly_max_nm": 13.0},
            ]
            path.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")

            series = load_metric_archive_history(path, {"smoothed_max_nm"}, time_range_s=(1.0, 2.0))

            self.assertIn("smoothed_max_nm", series)
            x_values, y_values = series["smoothed_max_nm"]
            self.assertEqual(x_values.tolist(), [1.0, 2.0])
            self.assertEqual(y_values.tolist(), [1.0, 2.0])

    def test_absolute_reload_task_uses_full_history(self) -> None:
        if MetricArchiveReloadRequest is None or MetricArchiveReloadTask is None:
            self.skipTest("PyQt6 is not available in this Python environment")
        from unittest.mock import patch

        captured: dict[str, object] = {}

        def fake_load(path, metric_names=None, *, time_range_s=None):
            captured["path"] = path
            captured["metric_names"] = metric_names
            captured["time_range_s"] = time_range_s
            return {"smoothed_max_nm": (np.asarray([1.0, 2.0]), np.asarray([3.0, 4.0]))}

        class _DummySignal:
            def __init__(self) -> None:
                self.payload = None

            def emit(self, payload) -> None:
                self.payload = payload

        request = MetricArchiveReloadRequest(
            path=Path("dummy.h5"),
            source_epoch=1,
            request_token=("token",),
            metric_names=("smoothed_max",),
        )
        task = MetricArchiveReloadTask(request)
        task.signals = SimpleNamespace(finished=_DummySignal(), failed=_DummySignal())

        with patch("lspr_app.storage.hdf5_export.load_processed_metric_history", side_effect=fake_load):
            task.run()

        self.assertIsNone(captured["time_range_s"])
        self.assertEqual(captured["metric_names"], {"smoothed_max_nm"})
        self.assertIsNotNone(task.signals.finished.payload)
        self.assertEqual(task.signals.finished.payload.point_count, 2)
        self.assertEqual(set(task.signals.finished.payload.series.keys()), {"smoothed_max"})

    def test_sensorgram_metric_archive_name_translation(self) -> None:
        if sensorgram_metric_archive_names is None:
            self.skipTest("GUI helpers are not available in this Python environment")
        self.assertEqual(
            sensorgram_metric_archive_names(["smoothed_max", "centroid", "poly_max"]),
            ("smoothed_max_nm", "centroid_nm", "poly_max_nm"),
        )


if __name__ == "__main__":
    unittest.main()
