from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

import numpy as np

import seabreeze
seabreeze.use("cseabreeze")
from seabreeze.spectrometers import Spectrometer as SeaBreezeSpectrometer  # noqa: E402 - must follow seabreeze.use()
from seabreeze.spectrometers import list_devices  # noqa: E402 - must follow seabreeze.use()

from lspr_app.device.base import Spectrometer, SpectrometerCapabilities, SpectrometerError  # noqa: E402 - must follow seabreeze.use()
from lspr_app.domain.models import AcquisitionSettings, Spectrum  # noqa: E402 - must follow seabreeze.use()


class OceanSpectrometer(Spectrometer):
    """Hardware backend for Ocean Insight / Ocean Optics devices via SeaBreeze."""

    def __init__(self) -> None:
        devices = list_devices()
        if not devices:
            raise SpectrometerError(
                "No SeaBreeze-compatible spectrometer detected. "
                "Close OceanView and verify the USB driver configuration."
            )

        self._spectrometer = SeaBreezeSpectrometer(devices[0])
        self._wavelengths = np.asarray(self._spectrometer.wavelengths(), dtype=np.float64)
        self._integration_limits_us = self._spectrometer.integration_time_micros_limits
        self._max_intensity = float(self._spectrometer.max_intensity)
        self._electric_dark_pixel_indices = tuple(
            self._spectrometer.features["spectrometer"][0].get_electric_dark_pixel_indices()
        )

    def device_name(self) -> str:
        model = getattr(self._spectrometer, "model", "Ocean spectrometer")
        serial = getattr(self._spectrometer, "serial_number", "unknown-serial")
        return f"{model} ({serial})"

    def capabilities(self) -> SpectrometerCapabilities:
        return SpectrometerCapabilities(
            hardware_boxcar=False,
            simulated_boxcar=False,
            supports_dark_correction=True,
            supports_nonlinearity_correction=True,
            supports_trigger=True,
        )

    def max_intensity(self) -> float:
        return self._max_intensity

    def integration_time_limits_us(self) -> tuple[int, int]:
        return (int(self._integration_limits_us[0]), int(self._integration_limits_us[1]))

    def acquire_spectrum(self, settings: AcquisitionSettings) -> Spectrum:
        settings = replace(settings)
        integration_time_us = self._resolve_integration_time_us(settings)

        try:
            self._spectrometer.trigger_mode(settings.trigger_mode)
            self._spectrometer.integration_time_micros(integration_time_us)
            captures = [
                np.asarray(
                    self._spectrometer.intensities(
                        correct_dark_counts=settings.correct_dark_counts,
                        correct_nonlinearity=settings.correct_nonlinearity,
                    ),
                    dtype=np.float64,
                )
                for _ in range(max(settings.averages, 1))
            ]
        except Exception as exc:  # pragma: no cover - hardware runtime path
            raise SpectrometerError(f"Spectrometer acquisition failed: {exc}") from exc

        averaged = np.mean(captures, axis=0)
        return Spectrum(
            wavelengths_nm=self._wavelengths.copy(),
            values=averaged,
            y_label="Intensity (counts)",
            acquired_at=datetime.now(timezone.utc),
            metadata={
                "device": self.device_name(),
                "integration_time_ms": integration_time_us / 1000.0,
                "averages": settings.averages,
                "mode": "hardware",
                "correct_dark_counts": settings.correct_dark_counts,
                "correct_nonlinearity": settings.correct_nonlinearity,
                "trigger_mode": settings.trigger_mode,
                "electric_dark_pixel_correction": settings.correct_dark_counts,
                "electric_dark_pixel_count": len(self._electric_dark_pixel_indices),
            },
        )

    def close(self) -> None:
        self._spectrometer.close()

    def _resolve_integration_time_us(self, settings: AcquisitionSettings) -> int:
        requested_us = int(max(settings.integration_time_ms * 1000.0, 1_000.0))
        return int(np.clip(requested_us, *self._integration_limits_us))
