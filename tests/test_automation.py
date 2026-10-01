"""Maus-Steuerung (ohne echte Klicks) und Windows-API-Hilfen."""

import os
import sys
import time
import unittest
from unittest import mock

import win_api
import window_automation as wa


class StallDetectionTest(unittest.TestCase):
    def make_automator(self):
        a = wa.WindowAutomator.__new__(wa.WindowAutomator)
        a.config = {"CLICK_SPEED": 0.03}
        a.last_bot_pos = (0, 0)
        a.iron_grip_click = lambda x, y, button="left": None
        a.check_user_interruption = lambda: None
        return a

    def run_add(self, stall_on_click=None):
        clicks = {"n": 0}

        def fake_mouse_event(flags):
            if flags == win_api.MOUSE_RIGHT_DOWN:
                clicks["n"] += 1
                if clicks["n"] == stall_on_click:
                    time.sleep(0.30)  # simulierter System-Hänger während des Klicks

        with mock.patch.object(win_api, "mouse_event", fake_mouse_event):
            return self.make_automator().add_card_to_deck(100, 100, 3), clicks["n"]

    def test_no_false_alarm(self):
        for _ in range(20):
            stalled, clicks = self.run_add()
            self.assertFalse(stalled)
            self.assertEqual(clicks, 3)

    def test_stall_detected(self):
        stalled, clicks = self.run_add(stall_on_click=2)
        self.assertTrue(stalled)
        self.assertEqual(clicks, 3)  # Klicks werden nicht wiederholt (sonst evtl. Kopie zu viel)


class WinApiTest(unittest.TestCase):
    def test_own_process_name(self):
        path = win_api.process_image_name(os.getpid())
        self.assertTrue(path and path.lower().endswith(os.path.basename(sys.executable).lower()))

    def test_unknown_process(self):
        self.assertIsNone(win_api.process_image_name(0xFFFFFFF0))

    def test_process_handle_closed_exactly_once(self):
        # Regression: vorher wurde das Handle bei fremden Programmen doppelt geschlossen
        with mock.patch.object(win_api, "_CloseHandle", wraps=win_api._CloseHandle) as close:
            win_api.process_image_name(os.getpid())
        self.assertEqual(close.call_count, 1)

    def test_screen_queries(self):
        x, y = win_api.get_cursor_pos()
        self.assertIsInstance(x, int)
        vx, vy, vw, vh = win_api.virtual_screen_rect()
        self.assertGreater(vw, 0)
        self.assertGreater(vh, 0)
        self.assertEqual(len(win_api.get_pixel(0, 0) or (0, 0, 0)), 3)


if __name__ == "__main__":
    unittest.main()
