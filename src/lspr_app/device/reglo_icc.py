from __future__ import annotations

from dataclasses import dataclass
from math import floor, log10
from time import monotonic, sleep

from lspr_app.device.communication_models import DeviceCommand
from lspr_app.device.device_driver import DeviceError, DeviceTimeoutError
from lspr_app.device.serial_controllers import SerialController
from serial.tools import list_ports


@dataclass(slots=True)
class PumpPort:
    device: str
    description: str
    hwid: str


@dataclass(slots=True)
class PumpProbe:
    port: str
    protocol_version: str
    serial_number: str
    channel_count: int
    model: str


class RegloICCError(DeviceError):
    pass


PUMP_DISPLAY_MAX_LENGTH = 16


def sanitize_pump_display_text(text: str, max_length: int = PUMP_DISPLAY_MAX_LENGTH) -> str:
    """Reduce *text* to what the pump's display protocol will actually accept.

    Per the Reglo ICC manual's ``String`` data type (used by the ``D``/``DA``
    display-write commands): only printable ASCII (0x20-0x7E) is allowed, and
    the request delimiter (carriage return) cannot appear in the value. The
    physical display is also a single ~16-character line, so the result is
    truncated to *max_length*.
    """
    cleaned = "".join(ch for ch in str(text or "") if 0x20 <= ord(ch) <= 0x7E)
    return cleaned[: max(int(max_length), 0)]


def is_probable_reglo_port(port: PumpPort) -> bool:
    description = port.description.upper()
    hwid = port.hwid.upper()
    return (
        "265C:0001" in hwid
        or "ISMATEC" in description
        or "REGLO" in description
        or "ISMATEC" in hwid
        or "REGLO" in hwid
    )


class RegloICCClient(SerialController):
    # Shares SerialController's connect()/close()/is_connected()/claim-owner
    # lifecycle (see serial_controllers.py) instead of reimplementing the
    # same claim-port-then-open-serial-then-rollback-on-failure pattern
    # independently - this used to be a near-verbatim copy of it. Note: a
    # busy port now raises ControllerError (from serial_controllers.py)
    # rather than RegloICCError, since that check now happens in the shared
    # base class; nothing in this codebase catches RegloICCError specifically
    # for that case, only DeviceError more broadly.
    controller_type = "reglo-icc"
    _BAUD_RATE = 9600
    _TIMEOUT = 0.35

    @staticmethod
    def list_ports() -> list[PumpPort]:
        return [
            PumpPort(device=port.device, description=port.description, hwid=port.hwid)
            for port in list_ports.comports()
            if str(getattr(port, "device", "") or "").strip()
        ]

    @classmethod
    def probe_port(cls, port: str) -> PumpProbe:
        client = cls()
        try:
            client.connect(port)
            return client.get_probe()
        finally:
            client.close()

    def get_probe(self) -> PumpProbe:
        # Try broadcast address (0) first; fall back to pump address 1 if rejected.
        # Some Reglo ICC units in single-pump RS-232 mode only answer to address 1.
        try:
            addr = self._discover_pump_address()
        except (RegloICCError, DeviceTimeoutError):
            addr = "0"
        protocol_version = self.query(f"{addr}x!")
        serial_number = self.query(f"{addr}xS")
        raw_channels = self.query(f"{addr}xA")
        try:
            channel_count = int(raw_channels.split()[0])
        except (ValueError, IndexError):
            channel_count = 0
        model = self.query(f"{addr}#")
        return PumpProbe(
            port=self.port or "",
            protocol_version=protocol_version,
            serial_number=serial_number,
            channel_count=channel_count,
            model=model,
        )

    def execute_command(self, command: DeviceCommand) -> object | None:
        command_type = str(command.command_type or "").strip().casefold()
        payload = dict(command.payload or {})
        if command_type == "pump.stop_all":
            self.stop_all(int(payload.get("channel_count", 4)))
            return None
        if command_type == "pump.start":
            self.start_channel(int(payload.get("channel", 1)))
            return None
        if command_type == "pump.stop":
            self.stop_channel(int(payload.get("channel", 1)))
            return None
        if command_type == "pump.set_flow":
            self.apply_channel(
                int(payload.get("channel", 1)),
                float(payload.get("flow_ul_min", 0.0)),
                str(payload.get("direction", "OFF")),
                float(payload.get("tube_mm", 0.0)),
                start=bool(payload.get("start", False)),
            )
            return None
        if command_type == "pump.set_display":
            self.set_display_text(str(payload.get("text", "")))
            return None
        if command_type in {"pump.query", "raw.query"}:
            return self.query(str(payload.get("command", "")))
        raise RegloICCError(f"Unsupported command type {command.command_type!r} for {type(self).__name__}.")

    def _discover_pump_address(self) -> str:
        """Return the pump address string ('0' or '1') that responds to x!."""
        try:
            self.query("0x!")
            return "0"
        except (RegloICCError, DeviceTimeoutError):
            self.query("1x!")  # raises if address 1 also fails
            return "1"

    def query(self, command: str) -> str:
        return self.send(command)

    def send(self, command: str, idle_timeout_s: float = 0.03, max_wait_s: float = 0.75) -> str:
        # Retries a lost response (see _call_with_retry on SerialController,
        # inherited here) - confirmed safe for this pump's start/stop/set-flow
        # commands: a lost response doesn't mean the pump didn't apply the
        # command, and resending it is a safe no-op in that case. Does not
        # retry an explicit pump rejection ("#") below - that's a real answer.
        return self._call_with_retry(
            lambda: self._send_once(command, idle_timeout_s, max_wait_s), f"send {command!r}"
        )

    def _send_once(self, command: str, idle_timeout_s: float, max_wait_s: float) -> str:
        if self._serial is None:
            raise RegloICCError("Pump is not connected.")

        self._serial.reset_input_buffer()
        self._serial.reset_output_buffer()
        self._serial.write((command + "\r").encode("ascii"))
        self._serial.flush()

        chunks: list[bytes] = []
        started = monotonic()
        last_rx = started
        while monotonic() - started < max_wait_s:
            chunk = self._serial.read_all()
            if chunk:
                chunks.append(chunk)
                last_rx = monotonic()
                joined = b"".join(chunks)
                if b"\r" in joined or joined.endswith((b"*", b"#", b"+", b"-")):
                    break
            elif chunks and monotonic() - last_rx >= idle_timeout_s:
                break
            else:
                sleep(0.005)

        raw = b"".join(chunks).decode("ascii", errors="replace").strip()
        if not raw:
            raise DeviceTimeoutError(f"No response from pump for command {command!r}.")
        if raw == "#":
            raise RegloICCError(f"Pump rejected command {command!r}.")
        return raw

    def start_channel(self, channel: int) -> None:
        self._expect_status(f"{channel}H")

    def stop_channel(self, channel: int) -> None:
        self._expect_status(f"{channel}I")

    def stop_all(self, channel_count: int = 4) -> None:
        for channel in range(1, channel_count + 1):
            self.stop_channel(channel)

    def set_display_text(self, text: str) -> None:
        """Write *text* to the pump's display while it's under remote control.

        Uses the ``xN`` ("Set pump's temporary display name") command,
        addressed to pump 0 since this is a per-pump, not per-channel,
        parameter. *text* is sanitized to printable ASCII and truncated to
        16 characters first - see :func:`sanitize_pump_display_text`.

        Switched from the originally-implemented ``DA`` ("Write letters to
        the pump to display...", manual section 6.20): that command is
        only ever listed in the summary command table - unlike nearly
        every other command in the manual, it has no worked example
        anywhere in section 18, which was the first real hint it might not
        be the intended way to show custom text. ``xN`` (section 6.5) does
        have one, for exactly this use case (section 18.6.2: "Set the
        pump's display name to 'Reagent A.'" -> request `0xNReagent A[CR]`
        -> response `*`). Both are per-pump String commands addressed to
        pump 0 the same way, so the only change here is the command code.

        Still UNVERIFIED against real hardware as of this change - see
        "Pump Display" in docs/experiment-control/pump_control_guide.md.
        """
        sanitized = sanitize_pump_display_text(text)
        self._expect_status(f"0xN{sanitized}")

    def configure_channel(
        self,
        channel: int,
        flow_ul_min: float,
        direction: str,
        tube_mm: float,
    ) -> None:
        flow_ul_min = max(float(flow_ul_min), 0.0)
        direction = str(direction or "OFF").upper()
        self._expect_status(f"{channel}+{self._encode_tube_mm(tube_mm)}")
        if flow_ul_min <= 0.0 or direction == "OFF":
            return
        self._expect_status(f"{channel}{'J' if direction == 'CW' else 'K'}")
        self._expect_status(f"{channel}M")
        self.send(f"{channel}f{self._encode_volume_type2(flow_ul_min / 1000.0)}")

    def apply_channel(
        self,
        channel: int,
        flow_ul_min: float,
        direction: str,
        tube_mm: float,
        *,
        start: bool,
    ) -> None:
        flow_ul_min = max(float(flow_ul_min), 0.0)
        direction = str(direction or "OFF").upper()
        self.configure_channel(channel, flow_ul_min, direction, tube_mm)
        if flow_ul_min <= 0.0 or direction == "OFF":
            self.stop_channel(channel)
            return
        if start:
            self.start_channel(channel)

    def _expect_status(self, command: str) -> None:
        response = self.send(command)
        if response != "*":
            raise RegloICCError(f"Unexpected pump response {response!r} for {command!r}.")

    @staticmethod
    def _encode_tube_mm(tube_mm: float) -> str:
        hundredths = int(round(max(float(tube_mm), 0.0) * 100.0))
        return f"{hundredths:04d}"

    @staticmethod
    def _encode_volume_type2(value_ml: float) -> str:
        value_ml = max(float(value_ml), 0.0)
        if value_ml == 0.0:
            return "0000+0"

        exponent = floor(log10(value_ml))
        mantissa = value_ml / (10**exponent)
        while mantissa < 1.0:
            mantissa *= 10.0
            exponent -= 1
        while mantissa >= 10.0:
            mantissa /= 10.0
            exponent += 1

        mantissa_digits = int(round(mantissa * 1000.0))
        if mantissa_digits >= 10000:
            mantissa_digits //= 10
            exponent += 1

        exponent = max(min(exponent, 9), -9)
        sign = "+" if exponent >= 0 else "-"
        return f"{mantissa_digits:04d}{sign}{abs(exponent)}"
