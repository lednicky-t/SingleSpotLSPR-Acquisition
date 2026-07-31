# Spectral Processing Pipeline Architecture

This document defines the contract between raw spectral data and the
processing overlay (crop, baseline removal, smoothing, fitting) in
`sLSPR acq`. It is the authoritative reference for any code that computes,
stores, or displays Raw, Dark, Reference, or Absorbance spectra.

---

## Core invariant

**Narrowing the processing (wavelength) range must never change the numeric
value reported for any wavelength that remains inside the new range.**

Cropping is a display/analysis *window*, not a recomputation trigger. If
moving a UI slider changes the value at a wavelength that was in view both
before and after, that is a bug, not "recomputing over the new window."

---

## Data flow

```
Spectrometer (governed only by integration time, averages, hardware) │
│ raw spectrum, full range │
▼
MeasurementSession.set_dark / set_reference / set_sample (domain/session.py) │
│ stored verbatim, timestamped, no processing applied │
▼
MeasurementSession.compute_absorbance (domain/session.py:43-80) │
│ absorbance = -log10((sample-dark)/(reference-dark)); no processing │
│ applied before this calculation │
▼
process_spectrum (domain/processing.py) — the "overlay" layer described below
```

Dark and Reference are frozen at the moment they're captured — `set_dark`/
`set_reference`/`set_sample` (`domain/session.py:18-30`) store whatever
`Spectrum` they're given untouched, tagging it with `metadata["kind"]` (
`"dark"` / `"reference"` / `"sample"` / `"absorbance"`) so downstream code
can tell spectra apart without re-deriving their origin. Nothing under
`storage/hdf5_export.py` applies processing settings before writing raw,
dark, reference, or sample data to disk — `update_processing`/
`_write_processing_metadata` only stamp the current `ProcessingSettings` as
informational metadata attributes, never using them to slice or transform
stored arrays.

---

## The processing overlay (`domain/processing.py::process_spectrum`)

`process_spectrum(spectrum, settings)` never touches stored/archived data —
its output only feeds the live plot preview (`gui/main_window_plotting.py`)
and live peak-tracking metrics computed from that preview. It applies, in
this order:

1. **Sanitize.** Drop/interpolate non-finite samples across the *full*
   incoming spectrum.
2. **Baseline removal and smoothing — Absorbance only.** Gated on
   `spectrum.metadata.get("kind") == "absorbance"`. Raw (Sample), Dark, and
   Reference spectra are never baseline-corrected or smoothed, regardless of
   `ProcessingSettings` — only cropped for display. This runs on the full
   (not-yet-cropped) array specifically so that its edge behavior (e.g. a
   linear baseline's endpoint anchors, or a smoothing kernel's boundary
   handling) sits at the true ends of the acquired spectrum, not wherever
   the user's crop slider currently happens to be.
3. **Crop.** Mask to `[wavelength_min_nm, wavelength_max_nm]` — applied
   *last*, as a pure slice. This is what makes the core invariant above hold
   by construction: nothing computed in steps 1-2 depends on the crop
   bounds, so narrowing them can only add or remove points at the edges of
   what's displayed, never change a retained value.
4. **Fit.** `fit_processed_spectrum` runs against the cropped, processed
   output — a further, narrower sub-window (`crop_method` /
   `fit_window_width_nm`) used only to pick which points feed the
   peak/centroid/Gaussian/polynomial fit. This sub-window is a separate,
   already-isolated concept from the main processing range and is not
   affected by this document's fix.

| Plot mode (`PLOT_MODES`) | `Spectrum.metadata["kind"]` | Crop applied | Baseline/smoothing applied |
|---|---|---|---|
| Raw | `sample` | yes | no |
| Dark | `dark` | yes | no |
| Reference | `reference` | yes | no |
| Absorbance | `absorbance` | yes | yes (per `ProcessingSettings`) |

---

## Known bug this document's fix addresses

Before this fix, `process_spectrum` cropped *first*, then ran baseline
removal and smoothing on the already-cropped array, unconditionally for
whatever spectrum kind it was given. Two consequences:

- Narrowing `wavelength_min_nm`/`wavelength_max_nm` moved the array edge
  next to wavelengths that used to be safely interior, so those points got
  measurably different baseline/smoothing output purely because the crop
  boundary moved — confirmed against exported CSV data (values differing by
  up to ~0.024 absorbance units near a moved edge, decaying to noise level
  within ~10 nm).
- Raw/Dark/Reference spectra were baseline-corrected and smoothed whenever
  those settings were non-`"none"`, even though only Absorbance should ever
  be.

Fixed by reordering (crop last) and gating (kind == `"absorbance"`) as
described above. Regression tests: `tests/unit/test_processing_profile.py`
(`test_narrowing_processing_range_does_not_change_overlapping_values`,
`test_baseline_and_smoothing_skipped_for_non_absorbance_kind`,
`test_baseline_and_smoothing_still_apply_to_absorbance`).

---

## Known, separate issue (not fixed by this document)

`compute_absorbance` (`domain/session.py:61-63`) guards its division only
with `corrected_reference > 0` / `corrected_sample > 0`, not a
minimum-intensity/SNR threshold — a weak-signal band can produce numerically
exploding (not `NaN`) absorbance values instead of being marked invalid.
Different mechanism from the crop-leakage bug above; tracked separately and
not addressed here (needs real raw dark/reference/sample data to confirm
before touching the validity gate).

---

## Out of scope

`apps/sLSPR/eva` (singleLSPR Evaluation) has an analogous
crop-before-baseline/smoothing ordering bug in its own
`processing.py::apply_processing_to_spectrum`, and no gate at all preventing
baseline/smoothing from running on non-absorbance spectra (it has no `kind`
concept). Not fixed here — flagged for a future, separate pass on that
submodule.

---

## Review checklist

When modifying `process_spectrum`, `ProcessingSettings`, or anything in the
Raw → Dark/Reference → Absorbance → overlay chain, verify:

- Does baseline removal or smoothing still run only when
  `spectrum.metadata.get("kind") == "absorbance"`?
- Does the wavelength-range crop still run *after* baseline/smoothing, as a
  pure slice?
- Does narrowing `wavelength_min_nm`/`wavelength_max_nm` leave every
  still-in-range value unchanged? (Covered by
  `test_narrowing_processing_range_does_not_change_overlapping_values`.)
- Does this change avoid applying any processing to Dark/Reference/Sample
  before they're stored or archived?

---

## Related documents

- `runtime_pipeline_architecture.md` — the acquisition/recording lossless
  vs. lossy-display contract (a different, orthogonal layer — that document
  covers *when* spectra get processed relative to acquisition timing; this
  one covers *what* processing is allowed to do to *which* spectra).
