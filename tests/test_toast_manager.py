"""Tests für ToastManager."""

import unittest
from unittest import mock
import tkinter as tk

from toast_manager import ToastManager

try:
    _root = tk.Tk()
    _root.destroy()
    HAS_TK = True
except Exception:
    HAS_TK = False


@unittest.skipUnless(HAS_TK, "Tk nicht verfügbar")
class TestToastManager(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.mgr = ToastManager(self.root, duration_ms=1000, gap_ms=100)

    def tearDown(self):
        self.mgr.cancel_all()
        self.root.destroy()

    def test_queue_toast_increments_count(self):
        self.assertEqual(self.mgr.toast_count, 0)
        key1 = self.mgr.queue_toast("Meldung 1")
        self.assertEqual(self.mgr.toast_count, 1)
        self.assertEqual(key1, "toast1")

        key2 = self.mgr.queue_toast("Meldung 2")
        self.assertEqual(self.mgr.toast_count, 2)
        self.assertEqual(key2, "toast2")

    def test_cancel_all(self):
        self.mgr.queue_toast("Meldung")
        self.mgr.cancel_all()
        self.assertEqual(len(self.mgr._active_timers), 0)


if __name__ == "__main__":
    unittest.main()
