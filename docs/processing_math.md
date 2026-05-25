# Processing Math

This note defines the metric semantics used by LSPR Acquisition.
The goal is to keep the scientific meaning stable even when the GUI or fit implementation changes.

## Signal Stages

- `raw spectrum`: the measured spectrum from the device, before baseline correction or smoothing.
- `processed spectrum`: the cropped, baseline-corrected, and smoothed spectrum used for analysis.
- `fit spectrum`: an optional fitted curve built from the processed spectrum inside the fit window.
- `dense analysis curve`: a high-resolution interpolation of the processed spectrum inside the analysis window.

## Metric Definitions

### `smoothed_max`

- The wavelength of the maximum of the processed spectrum.
- It is derived from the processed spectrum only.
- Enabling or disabling polynomial/Gaussian fitting must not change it.

### `centroid`

- The centroid of the processed spectrum inside the analysis window.
- It is derived from the processed spectrum only.
- It must not drift when fit settings change unless the processed spectrum itself changes.

### `poly_max`

- The peak position of the polynomial-fit-aware metric.
- When a polynomial fit exists, it uses the fit result.
- When fitting is disabled, it falls back to the processed spectrum analysis window.

### `gaussian_center`

- The center of the Gaussian fit when a Gaussian fit exists.
- If the fit is unavailable, the analysis can fall back to the processed spectrum behavior used by the current implementation.

## Analysis Window

The analysis window is the wavelength range used for metric evaluation and dense interpolation.

It is controlled by:

- the processing wavelength limits,
- the crop method,
- the fit window width,
- the analysis resolution.

## Residuals

Residuals are the difference between the processed spectrum and the fit curve over the fit window.
They are used for diagnostics and fit quality, not for changing the raw measurement.

## Notes

- Raw data is never modified in place.
- Fit-aware metrics must remain explicitly labeled in the UI and the measurement export.
- Any future metric changes should be documented here together with their unit and dependence on fit versus processed data.
