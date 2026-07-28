"""Automated long-run performance probe for the singleLSPR Acquisition live loop.

Built to investigate: the live refresh rate starts near the configured target
(e.g. ~5 Hz) and drifts down (e.g. to ~3 Hz) over a few minutes of continuous
acquisition. The app already ships rich diagnostics for this exact class of
problem - see ``src/lspr_app/gui/runtime_probe.py`` (a "runtime drift probe"
that snapshots scheduler lag, queue depths, log/history buffer sizes, and
process working set once a minute) and the "Session statistics" panel in the
GUI (``Ctrl`` diagnostics -> per-callback timing breakdown). Both are designed
to be read by a human watching the running app for several minutes.

This script automates that workflow so the investigation does not require a
person to babysit the GUI, and adds two things the built-in tools do not
have:

1. A dense, unattended time series of the *actual* measured refresh rate
   (``window._actual_plot_refresh_rate_hz``, a 5-frame rolling average kept
   by ``acquisition_controller.py``) sampled every few seconds for the whole
   run, so the drift the maintainer described is visible as a curve, not
   just a "before/after" snapshot.
2. Heap-level attribution: a ``tracemalloc`` snapshot diff and a ``gc``
   object-count-by-type diff between the start and end of the run, to show
   *which Python objects* grew - useful because the runtime drift probe
   tracks specific known counters (log history, metric buffer points, ...)
   but can't tell you about a growing list/cache nobody instrumented yet.

It drives the REAL production code path - the actual ``MainWindow``, the
actual ``SimulatedSpectrometer`` backend, the actual multiprocess acquisition
and processing workers - not a reimplementation, so the timings it reports
are the timings a user would actually see.

Usage (from apps/sLSPR/acq, with the app's venv active):

    python tools/live_perf_drift_probe.py --duration-s 300 --rate-hz 5

Add --visible to show the real window instead of running offscreen (useful
to eyeball that the simulated spectrum is actually updating). Add
--report PATH to control where the text report is written (defaults next to
this script).
"""
from __future__ import annotations

import argparse
import gc
import os
import statistics
import sys
import tracemalloc
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--duration-s", type=float, default=300.0, help="Measured run length in seconds (default: 300 = 5 min).")
    parser.add_argument("--warmup-s", type=float, default=15.0, help="Seconds to run before the baseline snapshot is taken (lets startup allocations settle).")
    parser.add_argument("--sample-interval-s", type=float, default=5.0, help="How often to sample the refresh rate and runtime drift probe.")
    parser.add_argument("--rate-hz", type=float, default=5.0, help="Target live display/simulation output rate, matching the reported symptom.")
    parser.add_argument("--visible", action="store_true", help="Show the real window instead of the offscreen Qt platform.")
    parser.add_argument("--report", type=Path, default=None, help="Where to write the text report (default: alongside this script).")
    return parser.parse_args()


def _gc_type_counts() -> Counter:
    return Counter(type(obj).__name__ for obj in gc.get_objects())


@dataclass(slots=True)
class RefreshSample:
    elapsed_s: float
    actual_hz: float | None
    source_hz: float | None


def main() -> None:
    args = _parse_args()
    if not args.visible:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    src_dir = Path(__file__).resolve().parents[1] / "src"
    if str(src_dir) not in sys.path:
        sys.path.insert(0, str(src_dir))

    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QApplication

    from lspr_app import pyqtgraph_patches
    from lspr_app.device.simulated import SimulatedSpectrometer
    from lspr_app.domain.session import MeasurementSession
    from lspr_app.gui.main_window import MainWindow
    from lspr_app.gui.runtime_probe import build_runtime_drift_lines_for, capture_runtime_drift_sample_for

    app = QApplication(sys.argv[:1])
    pyqtgraph_patches.apply()

    spectrometer = SimulatedSpectrometer()
    session = MeasurementSession()
    window = MainWindow(spectrometer=spectrometer, session=session, launch_profile="simulation")
    window.show()
    app.processEvents()

    sample_interval_ms = max(int(args.sample_interval_s * 1000), 200)
    expected_samples = int((args.warmup_s + args.duration_s) / args.sample_interval_s) + 20
    window._runtime_drift_max_samples = expected_samples
    window._runtime_drift_samples = []

    window.live_rate_spin.setValue(args.rate_hz)
    window.sim_output_rate_spin.setValue(args.rate_hz)

    refresh_series: list[RefreshSample] = []
    state: dict[str, object] = {"baseline_snapshot": None, "baseline_gc": None, "t0": perf_counter()}

    def _record_refresh_sample() -> None:
        elapsed = perf_counter() - float(state["t0"])
        refresh_series.append(
            RefreshSample(
                elapsed_s=elapsed,
                actual_hz=getattr(window, "_actual_plot_refresh_rate_hz", None),
                source_hz=getattr(window, "_effective_raw_rate_hz", None),
            )
        )
        capture_runtime_drift_sample_for(window)

    sample_timer = QTimer()
    sample_timer.setInterval(sample_interval_ms)
    sample_timer.timeout.connect(_record_refresh_sample)

    tracemalloc.start(25)

    def _start_measuring() -> None:
        gc.collect()
        state["baseline_snapshot"] = tracemalloc.take_snapshot()
        state["baseline_gc"] = _gc_type_counts()
        window._runtime_drift_samples = []
        state["t0"] = perf_counter()
        sample_timer.start()
        _record_refresh_sample()
        print(f"[perf-probe] Warmup complete ({args.warmup_s:.0f}s). Measuring for {args.duration_s:.0f}s...")

    def _finish() -> None:
        sample_timer.stop()
        window._stop_live_acquisition("perf probe finished")
        app.processEvents()
        gc.collect()
        final_snapshot = tracemalloc.take_snapshot()
        final_gc = _gc_type_counts()
        _write_report(
            args=args,
            window=window,
            refresh_series=refresh_series,
            baseline_snapshot=state["baseline_snapshot"],
            final_snapshot=final_snapshot,
            baseline_gc=state["baseline_gc"],
            final_gc=final_gc,
        )
        app.quit()

    print(f"[perf-probe] Starting live simulation acquisition at {args.rate_hz:.2f} Hz target...")
    window._start_live_acquisition()
    QTimer.singleShot(int(args.warmup_s * 1000), _start_measuring)
    QTimer.singleShot(int((args.warmup_s + args.duration_s) * 1000), _finish)

    app.exec()


def _format_hz(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f} Hz"


def _linear_slope_per_min(samples: list[tuple[float, float]]) -> float | None:
    if len(samples) < 2:
        return None
    xs = [s for s, _ in samples]
    ys = [v for _, v in samples]
    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    slope_per_s = numerator / denominator
    return slope_per_s * 60.0


def _write_report(
    *,
    args: argparse.Namespace,
    window,
    refresh_series: list[RefreshSample],
    baseline_snapshot,
    final_snapshot,
    baseline_gc: Counter,
    final_gc: Counter,
) -> None:
    from lspr_app.gui.runtime_probe import build_runtime_drift_lines_for

    report_path = args.report or (Path(__file__).resolve().parent / "live_perf_drift_report.txt")
    lines: list[str] = []
    lines.append("singleLSPR Acquisition - live perf drift probe report")
    lines.append(f"Target rate: {args.rate_hz:.2f} Hz | warmup: {args.warmup_s:.0f}s | measured: {args.duration_s:.0f}s | sample interval: {args.sample_interval_s:.0f}s")
    lines.append("")

    lines.append("Refresh-rate time series (actual_plot_refresh_rate_hz, a 5-frame rolling average):")
    valid_points = [(s.elapsed_s, s.actual_hz) for s in refresh_series if s.actual_hz is not None]
    for sample in refresh_series:
        lines.append(f"  t={sample.elapsed_s:7.1f}s | display={_format_hz(sample.actual_hz)} | source={_format_hz(sample.source_hz)}")
    if valid_points:
        first_third = valid_points[: max(len(valid_points) // 3, 1)]
        last_third = valid_points[-max(len(valid_points) // 3, 1):]
        first_avg = statistics.fmean(v for _, v in first_third)
        last_avg = statistics.fmean(v for _, v in last_third)
        slope = _linear_slope_per_min(valid_points)
        lines.append("")
        lines.append(f"  First-third avg: {first_avg:.2f} Hz | Last-third avg: {last_avg:.2f} Hz | change: {last_avg - first_avg:+.2f} Hz")
        if slope is not None:
            lines.append(f"  Linear trend: {slope:+.3f} Hz/min")
        if first_avg > 0:
            pct = (last_avg - first_avg) / first_avg * 100.0
            lines.append(f"  Relative drop: {pct:+.1f}%")
    else:
        lines.append("  No refresh-rate samples were captured (window never produced a display frame).")
    lines.append("")

    lines.append("Built-in runtime drift probe (reused from src/lspr_app/gui/runtime_probe.py):")
    lines.extend(build_runtime_drift_lines_for(window))
    lines.append("")

    lines.append("tracemalloc: top 20 growing allocation sites (by size), start -> end of measured window:")
    if baseline_snapshot is not None and final_snapshot is not None:
        diff = final_snapshot.compare_to(baseline_snapshot, "lineno")
        for stat in diff[:20]:
            lines.append(f"  {stat}")
    else:
        lines.append("  tracemalloc snapshots unavailable.")
    lines.append("")

    lines.append("gc object counts: top 20 growing Python types, start -> end of measured window:")
    growth = Counter()
    all_types = set(baseline_gc) | set(final_gc)
    for type_name in all_types:
        delta = final_gc.get(type_name, 0) - baseline_gc.get(type_name, 0)
        if delta != 0:
            growth[type_name] = delta
    for type_name, delta in growth.most_common(20):
        lines.append(f"  {type_name}: {baseline_gc.get(type_name, 0)} -> {final_gc.get(type_name, 0)} ({delta:+d})")
    lines.append("")

    report_text = "\n".join(lines)
    report_path.write_text(report_text, encoding="utf-8")
    print(report_text)
    print(f"\n[perf-probe] Report written to {report_path}")


if __name__ == "__main__":
    main()
