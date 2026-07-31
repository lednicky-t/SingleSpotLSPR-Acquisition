from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone

import numpy as np

from lspr_app.device.base import Spectrometer, SpectrometerCapabilities
from lspr_app.domain.models import AcquisitionSettings, Spectrum


@dataclass(slots=True)
class SimulationParameters:
    wavelength_min_nm: float = 400.0
    wavelength_max_nm: float = 900.0
    wavelength_resolution_nm: float = 0.5
    peak_center_nm: float = 620.0
    peak_width_nm: float = 35.0
    peak_height: float = 1800.0
    secondary_peak_offset_nm: float = 130.0
    secondary_peak_height_percent: float = 18.0
    secondary_peak_width_percent: float = 180.0
    baseline: float = 900.0
    slope: float = 0.12
    noise: float = 40.0


class SimulatedSpectrometer(Spectrometer):
    """Development backend that emulates a spectrometer response.

    The synthetic spectrum depends only on the simulation parameters, not on
    acquisition settings such as integration time or averaging.
    """

    def __init__(self, parameters: SimulationParameters | None = None) -> None:
        self._parameters = parameters or SimulationParameters()
        self._drift_level = 0.0
        self._cached_axis_key: tuple[float, float, float] | None = None
        self._cached_axis: np.ndarray | None = None
        self._cached_x_relative: np.ndarray | None = None
        self._cached_linear_baseline: np.ndarray | None = None
        self._cached_peak_signal: np.ndarray | None = None
        self._cached_secondary_peak_signal: np.ndarray | None = None

    def device_name(self) -> str:
        return "Simulated Spectrometer"

    def capabilities(self) -> SpectrometerCapabilities:
        return SpectrometerCapabilities(
            simulated_boxcar=True,
            supports_dark_correction=True,
            supports_nonlinearity_correction=True,
            supports_trigger=True,
        )

    def acquire_spectrum(self, settings: AcquisitionSettings) -> Spectrum:
        settings = replace(settings)
        return self.acquire_kind_spectrum("sample", settings)

    def acquire_kind_spectrum(self, kind: str, settings: AcquisitionSettings) -> Spectrum:
        settings = replace(settings)
        wavelengths = self.wavelength_axis()
        x_relative = self._cached_x_relative
        linear_baseline = self._cached_linear_baseline
        peak_signal = self._cached_peak_signal
        secondary_peak_signal = self._cached_secondary_peak_signal
        if (
            x_relative is None
            or linear_baseline is None
            or peak_signal is None
            or secondary_peak_signal is None
            or len(wavelengths) == 0
        ):
            self._refresh_waveform_cache()
            wavelengths = self.wavelength_axis()
            x_relative = self._cached_x_relative
            linear_baseline = self._cached_linear_baseline
            peak_signal = self._cached_peak_signal
            secondary_peak_signal = self._cached_secondary_peak_signal
        assert x_relative is not None
        assert linear_baseline is not None
        assert peak_signal is not None
        assert secondary_peak_signal is not None
        # Mean-reverting random walk (AR(1)): baseline_signal wanders up/down like
        # real instrument thermal/mechanical drift. The 0.98 reversion factor keeps
        # the walk bounded around zero rather than drifting away indefinitely; the
        # step scale is tuned to give a steady-state wander of a similar order of
        # magnitude to the noise parameter.
        drift_step = float(np.random.normal(0.0, 0.1 * max(self._parameters.noise, 0.0)))
        self._drift_level = 0.98 * self._drift_level + drift_step
        drift = np.full_like(x_relative, self._drift_level)
        baseline_signal = linear_baseline + drift
        sample_signal = baseline_signal + peak_signal + secondary_peak_signal
        noise_scale = max(self._parameters.noise, 0.0)
        noise = np.random.normal(0.0, noise_scale, size=wavelengths.shape)

        if kind == "dark":
            values = np.clip(20.0 + 0.1 * baseline_signal + noise * 0.3, 1e-6, None)
        elif kind == "reference":
            values = np.clip(baseline_signal + noise, 1e-6, None)
        else:
            values = np.clip(sample_signal + noise, 1e-6, None)

        return Spectrum(
            wavelengths_nm=wavelengths.copy(),
            values=values,
            y_label="Intensity (a.u.)",
            acquired_at=datetime.now(timezone.utc),
            metadata={
                "device": self.device_name(),
                "integration_time_ms": settings.integration_time_ms,
                "averages": settings.averages,
                "mode": "simulated",
                "correct_dark_counts": settings.correct_dark_counts,
                "correct_nonlinearity": settings.correct_nonlinearity,
                "trigger_mode": settings.trigger_mode,
                "electric_dark_pixel_correction": settings.correct_dark_counts,
                "electric_dark_pixel_count": 0,
                "simulation_kind": kind,
                "simulation_resolution_nm": self._parameters.wavelength_resolution_nm,
                "simulation_peak_center_nm": self._parameters.peak_center_nm,
                "simulation_peak_width_nm": self._parameters.peak_width_nm,
                "simulation_peak_height": self._parameters.peak_height,
                "simulation_secondary_peak_offset_nm": self._parameters.secondary_peak_offset_nm,
                "simulation_secondary_peak_height_percent": self._parameters.secondary_peak_height_percent,
                "simulation_secondary_peak_width_percent": self._parameters.secondary_peak_width_percent,
            },
        )

    def simulation_parameters(self) -> SimulationParameters:
        return replace(self._parameters)

    def set_simulation_parameters(self, parameters: SimulationParameters | None = None, **kwargs) -> None:
        current = self._parameters
        if parameters is not None:
            current = replace(parameters)
        elif kwargs:
            current = replace(current, **kwargs)
        self._parameters = current
        self._invalidate_cache()

    def wavelength_axis(self) -> np.ndarray:
        params = self._parameters
        resolution_nm = max(float(params.wavelength_resolution_nm), 0.0001)
        cache_key = (
            float(params.wavelength_min_nm),
            float(params.wavelength_max_nm),
            resolution_nm,
        )
        if self._cached_axis_key == cache_key and self._cached_axis is not None:
            return self._cached_axis
        wavelengths = np.arange(
            cache_key[0],
            cache_key[1] + resolution_nm * 0.5,
            resolution_nm,
            dtype=np.float64,
        )
        if len(wavelengths) == 0:
            wavelengths = np.array([cache_key[0], cache_key[1]], dtype=np.float64)
        elif wavelengths[-1] < cache_key[1]:
            wavelengths = np.append(wavelengths, cache_key[1])
        self._cached_axis_key = cache_key
        self._cached_axis = wavelengths
        self._cached_x_relative = wavelengths - cache_key[0]
        self._cached_linear_baseline = self._build_linear_baseline(self._cached_x_relative)
        self._cached_peak_signal = self._build_sample_peak(wavelengths)
        self._cached_secondary_peak_signal = self._build_secondary_peak(wavelengths)
        return wavelengths

    def _invalidate_cache(self) -> None:
        self._cached_axis_key = None
        self._cached_axis = None
        self._cached_x_relative = None
        self._cached_linear_baseline = None
        self._cached_peak_signal = None
        self._cached_secondary_peak_signal = None

    def _build_linear_baseline(self, x_relative: np.ndarray) -> np.ndarray:
        params = self._parameters
        if len(x_relative) == 0:
            return np.empty(0, dtype=np.float64)
        baseline = float(params.baseline)
        peak_height = max(float(params.peak_height), 0.0)
        signal_scale = max(baseline + peak_height, 1e-9)
        x_span = max(float(x_relative[-1]) - float(x_relative[0]), 1e-9)
        normalized_position = (x_relative - float(x_relative[0])) / x_span
        return baseline + float(params.slope) * signal_scale * normalized_position

    def _build_sample_peak(self, wavelengths: np.ndarray) -> np.ndarray:
        params = self._parameters
        return float(params.peak_height) * np.exp(
            -0.5 * ((wavelengths - float(params.peak_center_nm)) / max(float(params.peak_width_nm), 1e-6)) ** 2
        )

    def _build_secondary_peak(self, wavelengths: np.ndarray) -> np.ndarray:
        params = self._parameters
        secondary_peak_height = float(params.peak_height) * float(params.secondary_peak_height_percent) / 100.0
        secondary_peak_width = max(float(params.peak_width_nm) * float(params.secondary_peak_width_percent) / 100.0, 1e-6)
        secondary_peak_center = float(params.peak_center_nm) + float(params.secondary_peak_offset_nm)
        return secondary_peak_height * np.exp(
            -0.5
            * (
                (wavelengths - secondary_peak_center)
                / secondary_peak_width
            )
            ** 2
        )

    def _build_sample_signal(self, wavelengths: np.ndarray) -> np.ndarray:
        return (
            self._build_baseline_signal(wavelengths)
            + self._build_sample_peak(wavelengths)
            + self._build_secondary_peak(wavelengths)
        )

    def _build_baseline_signal(self, wavelengths: np.ndarray) -> np.ndarray:
        wavelengths = np.asarray(wavelengths, dtype=np.float64)
        if len(wavelengths) == 0:
            return np.empty(0, dtype=np.float64)
        x_relative = wavelengths - float(wavelengths[0])
        return self._build_linear_baseline(x_relative)
