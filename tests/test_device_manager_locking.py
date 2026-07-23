"""Tests for the B4 lock split in DeviceCommunicationService
(device/device_manager.py) - see docs/device-layer/DEVICE_LAYER_AUDIT_2026.md
item 29. Proves the fast state-lock reads (is_connected/status/connection/
list_statuses/list_profiles) never block behind slow hardware-I/O-lock
operations (connect/disconnect/send_command), and that the reentrant
connect-over-existing-connection path still works correctly.
"""
from __future__ import annotations

import queue
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from lspr_app.device import device_manager  # noqa: E402
from lspr_app.device.communication_models import DeviceCommand  # noqa: E402
from lspr_app.device.device_driver import DeviceDriver  # noqa: E402
from lspr_app.device.reglo_icc import PumpProbe  # noqa: E402

_FAKE_PROBE = PumpProbe(port="", protocol_version="1", serial_number="fake", channel_count=4, model="FakePump")


class _GatedFakePumpDriver(DeviceDriver):
    """Stand-in for RegloICCClient whose connect()/close() can be paused
    mid-call from a test, to deterministically exercise concurrency without
    real sleeps or real hardware."""

    def __init__(self) -> None:
        self.port: str | None = None
        self._claim_owner = f"fake-pump:{id(self)}"
        self._connected = False
        self.gate_connect = False
        self.gate_close = False
        self.gate_execute = False
        self.raise_on_execute = False
        self.connect_started = threading.Event()
        self.connect_release = threading.Event()
        self.close_started = threading.Event()
        self.close_release = threading.Event()
        self.execute_started = threading.Event()
        self.execute_release = threading.Event()
        self.close_calls = 0

    def connect(self, endpoint: str) -> None:
        self.connect_started.set()
        if self.gate_connect:
            assert self.connect_release.wait(timeout=5.0), "connect_release never signaled"
        self.port = endpoint
        self._connected = True

    def close(self) -> None:
        self.close_started.set()
        if self.gate_close:
            assert self.close_release.wait(timeout=5.0), "close_release never signaled"
        self._connected = False
        self.close_calls += 1

    def is_connected(self) -> bool:
        return self._connected

    def get_probe(self) -> object:
        return _FAKE_PROBE

    def execute_command(self, command: DeviceCommand) -> object | None:
        self.execute_started.set()
        if self.gate_execute:
            assert self.execute_release.wait(timeout=5.0), "execute_release never signaled"
        if self.raise_on_execute:
            raise RuntimeError("simulated command failure")
        return {"ok": True, "command_type": command.command_type}


class _DeviceManagerFakePumpTestCase(unittest.TestCase):
    """Shared fixture: a DeviceCommunicationService with a fake, gate-able
    pump driver wired in place of the real RegloICCClient. Split out so
    DeviceManagerBusyStateTests doesn't inherit (and re-run) every test
    method already defined on DeviceManagerLockingTests."""

    def setUp(self) -> None:
        patcher_load = patch.object(device_manager, "load_app_setting", return_value=[])
        patcher_save = patch.object(device_manager, "save_app_setting", return_value=None)
        patcher_load.start()
        patcher_save.start()
        self.addCleanup(patcher_load.stop)
        self.addCleanup(patcher_save.stop)

        # Patched ONCE here, before any threads exist, and left installed for
        # the whole test - unittest.mock.patch is not thread-safe to
        # enter/exit concurrently, so per-call `with patch(...)` from
        # multiple threads (as in the churn test below) races the patch
        # in/out and can leak the real RegloICCClient. A thread-safe queue
        # of pending fake drivers avoids that entirely.
        self._pending_drivers: "queue.Queue[_GatedFakePumpDriver]" = queue.Queue()

        def _fake_reglo_icc_client_factory() -> _GatedFakePumpDriver:
            try:
                return self._pending_drivers.get_nowait()
            except queue.Empty:
                return _GatedFakePumpDriver()

        patcher_client = patch.object(device_manager, "RegloICCClient", side_effect=_fake_reglo_icc_client_factory)
        patcher_client.start()
        self.addCleanup(patcher_client.stop)

        self.service = device_manager.DeviceCommunicationService()
        self.service.ensure_default_profiles()
        self.service.register_endpoint_assignment(
            "pump_1", "COM_FAKE", device_type="pump", driver="reglo_icc", mark_manual=False
        )

    def _connect_with_fake(self, driver: _GatedFakePumpDriver):
        self._pending_drivers.put(driver)
        return self.service.connect("pump_1", cached_pump_probe=_FAKE_PROBE)


class DeviceManagerLockingTests(_DeviceManagerFakePumpTestCase):
    def test_status_does_not_block_during_slow_connect(self) -> None:
        driver = _GatedFakePumpDriver()
        driver.gate_connect = True
        errors: list[Exception] = []

        def _run_connect() -> None:
            try:
                self._connect_with_fake(driver)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        thread = threading.Thread(target=_run_connect)
        thread.start()
        self.assertTrue(driver.connect_started.wait(timeout=5.0), "connect() never started")

        started = time.perf_counter()
        status = self.service.status("pump_1")
        elapsed_s = time.perf_counter() - started

        self.assertLess(elapsed_s, 0.5, "status() blocked behind an in-progress connect()")
        self.assertFalse(status.connected, "status() should see the truthful pre-connect snapshot")

        driver.connect_release.set()
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])

        status_after = self.service.status("pump_1")
        self.assertTrue(status_after.connected)

    def test_is_connected_and_connection_do_not_block_during_slow_disconnect(self) -> None:
        driver = _GatedFakePumpDriver()
        self._connect_with_fake(driver)
        self.assertTrue(self.service.is_connected("pump_1"))

        driver.gate_close = True
        errors: list[Exception] = []

        def _run_disconnect() -> None:
            try:
                self.service.disconnect("pump_1")
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        thread = threading.Thread(target=_run_disconnect)
        thread.start()
        self.assertTrue(driver.close_started.wait(timeout=5.0), "close() never started")

        started = time.perf_counter()
        connected = self.service.is_connected("pump_1")
        connection = self.service.connection("pump_1")
        elapsed_s = time.perf_counter() - started

        self.assertLess(elapsed_s, 0.5, "is_connected()/connection() blocked behind an in-progress disconnect()")
        self.assertFalse(connected, "connections dict is popped before close() completes")
        self.assertIsNone(connection)

        driver.close_release.set()
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(driver.close_calls, 1)

    def test_reentrant_connect_over_existing_connection_still_works(self) -> None:
        driver_a = _GatedFakePumpDriver()
        self._connect_with_fake(driver_a)
        self.assertIs(self.service.connection("pump_1"), driver_a)

        driver_b = _GatedFakePumpDriver()
        errors: list[Exception] = []

        def _run_reconnect() -> None:
            try:
                self._connect_with_fake(driver_b)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        thread = threading.Thread(target=_run_reconnect)
        thread.start()
        thread.join(timeout=5.0)

        self.assertFalse(thread.is_alive(), "reentrant connect-over-existing-connection deadlocked")
        self.assertEqual(errors, [])
        self.assertEqual(driver_a.close_calls, 1)
        self.assertIs(self.service.connection("pump_1"), driver_b)

    def test_concurrent_status_reads_survive_connect_disconnect_churn(self) -> None:
        """Probabilistic regression guard, not a formal proof: repeated
        connect/disconnect churn on a background thread must never cause a
        concurrent status/list_statuses/list_profiles reader to raise -
        specifically guards against "dictionary changed size during
        iteration", the hazard a naive lock split could reintroduce.
        """
        stop = threading.Event()
        churn_errors: list[Exception] = []

        def _churn() -> None:
            while not stop.is_set():
                try:
                    driver = _GatedFakePumpDriver()
                    self._connect_with_fake(driver)
                    self.service.disconnect("pump_1")
                except Exception as exc:  # pragma: no cover - failure path
                    churn_errors.append(exc)

        threads = [threading.Thread(target=_churn) for _ in range(3)]
        for thread in threads:
            thread.start()

        read_errors: list[Exception] = []
        deadline = time.perf_counter() + 0.5
        while time.perf_counter() < deadline:
            try:
                self.service.status("pump_1")
                self.service.list_statuses()
                self.service.list_profiles()
            except Exception as exc:  # pragma: no cover - failure path
                read_errors.append(exc)

        stop.set()
        for thread in threads:
            thread.join(timeout=5.0)
            self.assertFalse(thread.is_alive())

        self.assertEqual(churn_errors, [])
        self.assertEqual(read_errors, [])


class DeviceManagerBusyStateTests(_DeviceManagerFakePumpTestCase):
    """BUSY device-status coverage (Part C1): status() must report BUSY
    while a send_command() dispatch is in flight, revert to CONNECTED once
    it completes - on both the success and the exception path - and must
    keep answering status() promptly the whole time (no B4 regression:
    _make_status only reads the fast state-lock-protected _busy_labels set,
    never the slow hardware-I/O lock send_command holds for its duration).
    """

    def test_status_reports_busy_during_in_flight_command_and_reverts_after(self) -> None:
        driver = _GatedFakePumpDriver()
        driver.gate_execute = True
        self._connect_with_fake(driver)
        self.assertEqual(self.service.status("pump_1").state, "connected")

        errors: list[Exception] = []
        results: list[object] = []

        def _run_command() -> None:
            try:
                results.append(self.service.send_command("pump_1", DeviceCommand("noop", {})))
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        thread = threading.Thread(target=_run_command)
        thread.start()
        self.assertTrue(driver.execute_started.wait(timeout=5.0), "execute_command() never started")

        started = time.perf_counter()
        status = self.service.status("pump_1")
        elapsed_s = time.perf_counter() - started

        self.assertLess(elapsed_s, 0.5, "status() blocked behind an in-progress send_command()")
        self.assertEqual(status.state, "busy")
        self.assertTrue(status.connected, "busy still counts as connected for gating purposes")

        driver.execute_release.set()
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].success)

        status_after = self.service.status("pump_1")
        self.assertEqual(status_after.state, "connected")

    def test_status_reverts_to_connected_after_failed_command(self) -> None:
        driver = _GatedFakePumpDriver()
        driver.raise_on_execute = True
        self._connect_with_fake(driver)

        result = self.service.send_command("pump_1", DeviceCommand("noop", {}))

        self.assertFalse(result.success)
        # The finally-block busy-clear must run even though execute_command
        # raised - a stuck BUSY status would be worse than the original bug.
        status_after = self.service.status("pump_1")
        self.assertEqual(status_after.state, "connected")

    def test_list_statuses_reports_busy_for_the_in_flight_label_only(self) -> None:
        driver = _GatedFakePumpDriver()
        driver.gate_execute = True
        self._connect_with_fake(driver)

        thread = threading.Thread(
            target=lambda: self.service.send_command("pump_1", DeviceCommand("noop", {}))
        )
        thread.start()
        self.assertTrue(driver.execute_started.wait(timeout=5.0), "execute_command() never started")

        statuses = {status.label: status.state for status in self.service.list_statuses()}
        self.assertEqual(statuses.get("pump_1"), "busy")

        driver.execute_release.set()
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())


if __name__ == "__main__":
    unittest.main()
