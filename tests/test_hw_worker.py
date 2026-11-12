# SPDX-License-Identifier: GPL-3.0-or-later

import os
import socket
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app", "server"))
import hw_worker  # noqa: E402


class HardwareWorkerTest(unittest.TestCase):
    def setUp(self):
        self.parent_sock, child_sock = socket.socketpair()
        self.addCleanup(self.parent_sock.close)
        self.worker = hw_worker.HardwareWorker(child_sock.detach(), "/tmp/config.json", lambda message: None)
        self.addCleanup(self.worker.sock.close)

    def test_rejects_unknown_operation(self):
        with self.assertRaises(ValueError):
            self.worker._dispatch("shell", {})

    def test_rejects_non_integer_count_before_hardware_call(self):
        with mock.patch.object(hw_worker.sriovlib, "set_count") as set_count:
            with self.assertRaises(ValueError):
                self.worker._dispatch("set_count", {"count": True})
            set_count.assert_not_called()

    def test_dispatches_fixed_operations(self):
        with mock.patch.object(hw_worker.sriovlib, "status", return_value={"version": "1.1.7"}):
            self.assertEqual(self.worker._dispatch("status", {}), {"version": "1.1.7"})
        with mock.patch.object(hw_worker.sriovlib, "set_count", return_value=(True, "ok")) as set_count:
            self.assertEqual(
                self.worker._dispatch("set_count", {"count": 2}),
                {"ok": True, "message": "ok"},
            )
            set_count.assert_called_once_with(2)


if __name__ == "__main__":
    unittest.main()
