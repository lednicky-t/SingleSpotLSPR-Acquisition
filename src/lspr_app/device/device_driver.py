"""Common interface every hardware device driver must implement.

All three driver families (pump, switch/valve, selector) implement this
contract.  The connection lifecycle is identical across all of them; only the
probe return type and the device-specific commands differ.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from lspr_app.device.communication_models import DeviceCommand


class DeviceError(RuntimeError):
    """Base class for all hardware device errors.

    Both ``ControllerError`` (serial/AMF drivers) and ``RegloICCError`` (pump
    driver) inherit from this class, so callers can catch either the specific
    type or ``DeviceError`` to handle any hardware failure uniformly.
    """


class DeviceDriver(ABC):
    """Abstract base class for hardware device drivers.

    Guarantees that every driver exposes the four connection-lifecycle methods
    that ``DeviceCommunicationService`` relies on.  Device-specific behaviour
    (commands, probe fields) lives in the concrete subclasses.
    """

    @abstractmethod
    def connect(self, endpoint: str) -> None:
        """Open a connection to the device at *endpoint* (port, USB path, …)."""

    @abstractmethod
    def close(self) -> None:
        """Close the connection and release any claimed port resources."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Return ``True`` if the device connection is currently open."""

    @abstractmethod
    def get_probe(self) -> object:
        """Return a probe dataclass with identity information about the device.

        The concrete return type differs per driver family
        (``PumpProbe`` for the pump, ``ControllerProbe`` for switch/selector).
        """

    @abstractmethod
    def execute_command(self, command: DeviceCommand) -> object | None:
        """Execute a typed device command and return an optional response value.

        Raise ``ControllerError`` (or a driver-specific subclass) for unknown
        or unsupported command types.
        """
