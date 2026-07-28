"""Interactive console for testing raw Reglo ICC pump commands against real hardware.

Built to investigate: the pump's physical LCD stops showing the step-comment
text (sent via the ``xN`` command in ``set_display_text``) once more than one
channel is being configured/started in the same step. It used to work when
only channel 1 ever moved. Two live hypotheses:

1. Sending several channel commands (configure + start) right before the
   display write leaves the pump in a state where it drops/ignores the
   display command - a timing/sequencing issue, not a wrong command.
2. One of the channel commands is itself malformed for some channel/flow
   combination, and that response leaves the pump's parser out of sync with
   the *next* command line it reads over serial (so the immediately-following
   command - often the display write, since it's always sent last - gets
   corrupted or dropped).

This tool reuses the app's actual ``RegloICCClient`` driver (not a
reimplementation) so behavior here matches the real app exactly. Use it to:

- Send single raw commands and see the exact request/response bytes.
- Run the "channel sequence" preset with a variable number of channels and a
  variable delay between commands, mirroring the real order the app sends
  commands in (``_plan_step_commands`` in ``experiment_control_window.py``:
  configure *all* selected channels, then start *all* selected channels,
  then the display write last) - to see whether adding a delay before the
  display write, or reducing the channel count, makes the display start
  working again.

BEFORE RUNNING: close the LSPR Acquisition app (or disconnect the pump in its
Device Manager) - only one process can hold the pump's serial port at a time.

Run: python pump_command_console.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from time import sleep
from typing import Callable

import serial
from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from lspr_app.device.device_driver import DeviceError, DeviceTimeoutError
from lspr_app.device.reglo_icc import PUMP_DISPLAY_MAX_LENGTH, RegloICCClient, is_probable_reglo_port, sanitize_pump_display_text
from lspr_app.domain.pump_plan import DEFAULT_TUBE_MM, TUBE_DIAMETER_OPTIONS, nearest_tube_diameter_option


@dataclass(slots=True)
class _Step:
    description: str
    action: Callable[[], str]


class _JobSignals(QObject):
    step_done = pyqtSignal(str, str, bool)  # description, response_or_error, is_error
    finished = pyqtSignal()


class _CommandJob(QRunnable):
    """Runs a list of *steps* sequentially on a worker thread, in order, even
    if one step fails - matching how the real app's _StepApplyRunnable never
    aborts a batch early on a single command failure (confirmed by reading
    that class: it logs the failure and continues the loop)."""

    def __init__(self, steps: list[_Step], delay_s: float) -> None:
        super().__init__()
        self.signals = _JobSignals()
        self._steps = steps
        self._delay_s = delay_s
        self.setAutoDelete(True)

    def run(self) -> None:  # pragma: no cover - interactive tool, not unit tested
        for index, step in enumerate(self._steps):
            try:
                response = step.action()
                self.signals.step_done.emit(step.description, response, False)
            except (DeviceError, DeviceTimeoutError, serial.SerialException) as exc:
                self.signals.step_done.emit(step.description, str(exc), True)
            except Exception as exc:  # boundary: keep the worker thread alive so the UI never hangs
                self.signals.step_done.emit(step.description, f"Unexpected error: {exc}", True)
            if self._delay_s > 0.0 and index < len(self._steps) - 1:
                sleep(self._delay_s)
        self.signals.finished.emit()


class PumpConsole(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Reglo ICC pump command console")
        self.resize(760, 640)

        self._client = RegloICCClient()
        self._pool = QThreadPool.globalInstance()

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        root.addWidget(self._build_connection_row())
        root.addWidget(self._build_raw_command_group())
        root.addWidget(self._build_channel_group())
        root.addWidget(self._build_display_group())
        root.addWidget(self._build_sequence_group())

        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 9))
        root.addWidget(QLabel("Log"))
        root.addWidget(self._log, stretch=1)

        clear_row = QHBoxLayout()
        clear_row.addStretch(1)
        clear_button = QPushButton("Clear log")
        clear_button.clicked.connect(self._log.clear)
        clear_row.addWidget(clear_button)
        root.addLayout(clear_row)

        self._refresh_ports()
        self._set_connected_ui(False)

    # ---- connection -----------------------------------------------------

    def _build_connection_row(self) -> QWidget:
        box = QGroupBox("Connection")
        row = QHBoxLayout(box)
        self._port_combo = QComboBox()
        self._port_combo.setEditable(True)
        self._port_combo.setMinimumWidth(320)
        refresh_button = QPushButton("Refresh ports")
        refresh_button.clicked.connect(self._refresh_ports)
        self._connect_button = QPushButton("Connect")
        self._connect_button.clicked.connect(self._toggle_connection)
        probe_button = QPushButton("Get pump info")
        probe_button.clicked.connect(self._probe)
        self._status_label = QLabel("Disconnected")
        row.addWidget(QLabel("Port:"))
        row.addWidget(self._port_combo, stretch=1)
        row.addWidget(refresh_button)
        row.addWidget(self._connect_button)
        row.addWidget(probe_button)
        row.addWidget(self._status_label)
        return box

    def _refresh_ports(self) -> None:
        self._port_combo.clear()
        ports = RegloICCClient.list_ports()
        ports.sort(key=lambda p: 0 if is_probable_reglo_port(p) else 1)
        for port in ports:
            marker = "* " if is_probable_reglo_port(port) else "  "
            self._port_combo.addItem(f"{marker}{port.device}  ({port.description})", port.device)
        if not ports:
            self._log_line("No serial ports found.")

    def _selected_port(self) -> str:
        data = self._port_combo.currentData()
        if data:
            return str(data)
        text = self._port_combo.currentText().strip()
        return text.split()[0] if text else ""

    def _toggle_connection(self) -> None:
        if self._client.is_connected():
            self._client.close()
            self._set_connected_ui(False)
            self._log_line("Disconnected.")
            return
        port = self._selected_port()
        if not port:
            self._log_line("No port selected.")
            return
        try:
            self._client.connect(port)
        except (DeviceError, DeviceTimeoutError, serial.SerialException) as exc:
            self._log_line(f"Connect failed on {port}: {exc}")
            return
        self._set_connected_ui(True)
        self._log_line(f"Connected on {port}.")

    def _set_connected_ui(self, connected: bool) -> None:
        self._connect_button.setText("Disconnect" if connected else "Connect")
        self._status_label.setText("Connected" if connected else "Disconnected")
        self._status_label.setStyleSheet(f"color: {'green' if connected else 'red'}; font-weight: bold;")

    def _probe(self) -> None:
        if not self._client.is_connected():
            self._log_line("Not connected.")
            return
        self._run_steps([_Step("get_probe", self._probe_action)], delay_s=0.0)

    def _probe_action(self) -> str:
        probe = self._client.get_probe()
        return (
            f"port={probe.port} protocol={probe.protocol_version} "
            f"serial={probe.serial_number} channels={probe.channel_count} model={probe.model}"
        )

    # ---- raw command ------------------------------------------------------

    def _build_raw_command_group(self) -> QWidget:
        box = QGroupBox("Raw command (exact bytes sent to the pump, address prefix included)")
        row = QHBoxLayout(box)
        self._raw_template_combo = QComboBox()
        self._raw_template_combo.addItem("Insert template...", "")
        for label, template in _RAW_TEMPLATES:
            self._raw_template_combo.addItem(label, template)
        self._raw_template_combo.currentIndexChanged.connect(self._insert_raw_template)
        self._raw_input = QLineEdit()
        self._raw_input.setPlaceholderText("e.g. 0xNReagent A   or   1H")
        self._raw_input.returnPressed.connect(self._send_raw)
        send_button = QPushButton("Send")
        send_button.clicked.connect(self._send_raw)
        row.addWidget(self._raw_template_combo)
        row.addWidget(self._raw_input, stretch=1)
        row.addWidget(send_button)
        return box

    def _insert_raw_template(self, _index: int) -> None:
        template = self._raw_template_combo.currentData()
        if template:
            self._raw_input.setText(template)
        self._raw_template_combo.setCurrentIndex(0)

    def _send_raw(self) -> None:
        command = self._raw_input.text().strip()
        if not command:
            return
        self._run_steps([_Step(command, lambda c=command: self._client.query(c))], delay_s=0.0)

    # ---- channel configure/start ------------------------------------------

    def _build_channel_group(self) -> QWidget:
        box = QGroupBox("Configure + start / stop one channel (matches pump.set_flow / pump.start / pump.stop)")
        row = QHBoxLayout(box)
        self._channel_spin = QSpinBox()
        self._channel_spin.setRange(1, 4)
        self._flow_spin = QDoubleSpinBox()
        self._flow_spin.setRange(0.0, 50000.0)
        self._flow_spin.setValue(100.0)
        self._flow_spin.setSuffix(" uL/min")
        self._direction_combo = QComboBox()
        self._direction_combo.addItems(["CW", "CCW"])
        self._tube_spin = QComboBox()
        self._tube_spin.setMaxVisibleItems(10)
        for option in TUBE_DIAMETER_OPTIONS:
            self._tube_spin.addItem(f"{option.mm:.2f} mm ({option.order_no})", option.mm)
        self._tube_spin.setCurrentIndex(TUBE_DIAMETER_OPTIONS.index(nearest_tube_diameter_option(DEFAULT_TUBE_MM)))
        configure_start_button = QPushButton("Configure + Start")
        configure_start_button.clicked.connect(self._configure_and_start_channel)
        stop_button = QPushButton("Stop")
        stop_button.clicked.connect(self._stop_channel)
        row.addWidget(QLabel("Channel:"))
        row.addWidget(self._channel_spin)
        row.addWidget(QLabel("Flow:"))
        row.addWidget(self._flow_spin)
        row.addWidget(QLabel("Dir:"))
        row.addWidget(self._direction_combo)
        row.addWidget(QLabel("Tube:"))
        row.addWidget(self._tube_spin)
        row.addWidget(configure_start_button)
        row.addWidget(stop_button)
        return box

    def _configure_and_start_channel(self) -> None:
        channel = self._channel_spin.value()
        flow = self._flow_spin.value()
        direction = self._direction_combo.currentText()
        tube = float(self._tube_spin.currentData())
        steps = [
            _Step(
                f"configure ch{channel} flow={flow:.1f} dir={direction}",
                lambda: (self._client.configure_channel(channel, flow, direction, tube), "*")[1],
            ),
            _Step(f"start ch{channel}", lambda: (self._client.start_channel(channel), "*")[1]),
        ]
        self._run_steps(steps, delay_s=0.0)

    def _stop_channel(self) -> None:
        channel = self._channel_spin.value()
        self._run_steps([_Step(f"stop ch{channel}", lambda: (self._client.stop_channel(channel), "*")[1])], delay_s=0.0)

    # ---- display text ------------------------------------------------------

    def _build_display_group(self) -> QWidget:
        box = QGroupBox(f"Display text (max {PUMP_DISPLAY_MAX_LENGTH} chars)")
        row = QHBoxLayout(box)
        self._display_input = QLineEdit()
        self._display_input.setPlaceholderText("Text to show on the pump's LCD")
        self._display_input.setMaxLength(64)
        xn_button = QPushButton("Send via xN (current app)")
        xn_button.clicked.connect(lambda: self._send_display("xN"))
        da_button = QPushButton("Send via DA (legacy)")
        da_button.clicked.connect(lambda: self._send_display("DA"))
        d_button = QPushButton("Send via D (numbers, manual-literal)")
        d_button.clicked.connect(lambda: self._send_display("D"))
        row.addWidget(self._display_input, stretch=1)
        row.addWidget(xn_button)
        row.addWidget(da_button)
        row.addWidget(d_button)
        return box

    def _send_display(self, variant: str) -> None:
        text = sanitize_pump_display_text(self._display_input.text())
        if variant == "xN":
            self._run_steps(
                [_Step(f"set_display_text(xN) {text!r}", lambda: (self._client.set_display_text(text), "*")[1])],
                delay_s=0.0,
            )
            return
        command = f"0{variant}{text}"
        self._run_steps([_Step(command, lambda c=command: self._client.query(c))], delay_s=0.0)

    # ---- sequence test ------------------------------------------------------

    def _build_sequence_group(self) -> QWidget:
        box = QGroupBox(
            "Sequence test: configure+start N channels, then send display - same order as "
            "_plan_step_commands (all configures, then all starts, then display last)"
        )
        layout = QVBoxLayout(box)

        controls = QHBoxLayout()
        self._sequence_channel_count = QSpinBox()
        self._sequence_channel_count.setRange(1, 4)
        self._sequence_channel_count.setValue(1)
        self._sequence_flow = QDoubleSpinBox()
        self._sequence_flow.setRange(0.0, 50000.0)
        self._sequence_flow.setValue(100.0)
        self._sequence_flow.setSuffix(" uL/min")
        self._sequence_delay = QSpinBox()
        self._sequence_delay.setRange(0, 5000)
        self._sequence_delay.setValue(0)
        self._sequence_delay.setSuffix(" ms between commands")
        controls.addWidget(QLabel("Channels 1..N:"))
        controls.addWidget(self._sequence_channel_count)
        controls.addWidget(QLabel("Flow each:"))
        controls.addWidget(self._sequence_flow)
        controls.addWidget(QLabel("Delay:"))
        controls.addWidget(self._sequence_delay)
        layout.addLayout(controls)

        run_row = QHBoxLayout()
        run_button = QPushButton("Run sequence (configure+start channels, then display text above)")
        run_button.clicked.connect(self._run_sequence)
        baseline_button = QPushButton("Baseline: send display alone first (no channel commands)")
        baseline_button.clicked.connect(self._run_display_baseline)
        run_row.addWidget(run_button)
        run_row.addWidget(baseline_button)
        layout.addLayout(run_row)
        return box

    def _run_display_baseline(self) -> None:
        self._send_display("xN")

    def _run_sequence(self) -> None:
        count = self._sequence_channel_count.value()
        flow = self._sequence_flow.value()
        delay_s = self._sequence_delay.value() / 1000.0
        text = sanitize_pump_display_text(self._display_input.text())
        channels = list(range(1, count + 1))

        steps: list[_Step] = []
        for channel in channels:
            steps.append(
                _Step(
                    f"configure ch{channel} flow={flow:.1f}",
                    lambda c=channel: (self._client.configure_channel(c, flow, "CW", float(self._tube_spin.currentData())), "*")[1],
                )
            )
        for channel in channels:
            steps.append(_Step(f"start ch{channel}", lambda c=channel: (self._client.start_channel(c), "*")[1]))
        steps.append(
            _Step(f"set_display_text(xN) {text!r}", lambda: (self._client.set_display_text(text), "*")[1])
        )
        self._log_line(f"--- Running sequence: {count} channel(s), {delay_s * 1000:.0f} ms delay between commands ---")
        self._run_steps(steps, delay_s=delay_s)

    # ---- job plumbing ------------------------------------------------------

    def _run_steps(self, steps: list[_Step], *, delay_s: float) -> None:
        if not self._client.is_connected():
            self._log_line("Not connected.")
            return
        job = _CommandJob(steps, delay_s)
        job.signals.step_done.connect(self._on_step_done)
        job.signals.finished.connect(lambda: None)
        self._pool.start(job)

    def _on_step_done(self, description: str, response_or_error: str, is_error: bool) -> None:
        marker = "!!" if is_error else "<<"
        self._log_line(f">> {description}")
        self._log_line(f"{marker} {response_or_error}")

    def _log_line(self, text: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._log.appendPlainText(f"{timestamp}  {text}")


_RAW_TEMPLATES = [
    ("0# - pump model/info", "0#"),
    ("0x! - protocol version", "0x!"),
    ("0xS - serial number", "0xS"),
    ("0xA - channel count", "0xA"),
    ("1H - start ch1", "1H"),
    ("1I - stop ch1", "1I"),
    ("1J - ch1 direction CW", "1J"),
    ("1K - ch1 direction CCW", "1K"),
    ("1M - ch1 set flow-rate mode", "1M"),
    ("1+0100 - ch1 tube 1.00mm", "1+0100"),
    ("0xNTest - display via xN", "0xNTest"),
    ("0DATest - display via DA (legacy)", "0DATest"),
    ("0DTest - display via D (manual-literal numbers)", "0DTest"),
]


def main() -> None:
    app = QApplication(sys.argv)
    window = PumpConsole()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
