from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from lspr_app.domain.models import AcquisitionSettings, Spectrum


class SpectrometerError(RuntimeError):
    """Raised when acquisition fails."""


@dataclass(slots=True)
class SpectrometerCapabilities:
    hardware_boxcar: bool = False
    simulated_boxcar: bool = False
    supports_dark_correction: bool = False
    supports_nonlinearity_correction: bool = False
    supports_trigger: bool = False


class Spectrometer(ABC):
    @abstractmethod
    def device_name(self) -> str:
        """Return a user-facing device identifier."""

    def capabilities(self) -> SpectrometerCapabilities:
        return SpectrometerCapabilities()

    def max_intensity(self) -> float:
        """Return the detector's saturation count (its full-scale ADC value).

        Defaults to 65535 (16-bit ADC), the common case for Ocean spectrometers.
        Backends should override this with the value reported by the hardware.
        """
        return 65535.0

    def integration_time_limits_us(self) -> tuple[int, int] | None:
        """Return the hardware's own allowed integration-time range in
        microseconds, or None if the backend has no such limit to report.

        Used by the live auto-exposure procedure (see
        gui/acquisition_controller.py) to intersect its own configured search
        bounds with what the connected device will actually accept, so it
        never requests a time the hardware would silently clip to something
        else.
        """
        return None

    @abstractmethod
    def acquire_spectrum(self, settings: AcquisitionSettings) -> Spectrum:
        """Acquire one spectrum using the provided settings."""
