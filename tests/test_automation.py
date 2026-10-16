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
        import pyautogui
        self.assertEqual(win_api.screen_size(), tuple(pyautogui.size()))

    def test_startup_does_not_load_pyautogui_and_requests(self):
        # Beide kosten Startzeit und werden erst später gebraucht (Tastenkürzel, Netzzugriff). Die DPI-Behandlung
        # (System-DPI-aware, wie früher durch den Import von pyautogui) muss trotzdem schon beim Start stehen
        import ctypes
        import subprocess
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        code = ("import ctypes, sys; sys.path.insert(0, sys.argv[1]); import Overlay; aw = ctypes.c_int(-1); "
                "ctypes.windll.shcore.GetProcessDpiAwareness(0, ctypes.byref(aw)); "
                "print('pyautogui' in sys.modules, 'requests' in sys.modules, aw.value)")
        out = subprocess.run([sys.executable, "-c", code, root], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.stdout.split(), ["False", "False", "1"], out.stderr)

    def test_lazy_requests_is_the_real_module(self):
        import requests
        from lazy_requests import requests as lazy
        self.assertIs(lazy.get, requests.get)
        self.assertIs(lazy.Session, requests.Session)


class ClickFocusTest(unittest.TestCase):
    """Vor jedem Klick muss Master Duel vorne sein – sonst landet der Klick in einem anderen Programm."""

    def make_automator(self):
        a = wa.WindowAutomator.__new__(wa.WindowAutomator)
        a.last_bot_pos = (0, 0)
        a.check_user_interruption = lambda: None
        return a

    def click(self, md_in_front):
        import input_utils
        events = []
        with mock.patch.object(wa, "find_md_window", return_value=123), \
                mock.patch.object(input_utils, "md_in_front", return_value=md_in_front), \
                mock.patch.object(input_utils, "FOCUS_TIMEOUT", 0.05), \
                mock.patch.object(win_api, "set_foreground_window", lambda hwnd: False), \
                mock.patch.object(win_api, "set_cursor_pos", lambda x, y: None), \
                mock.patch.object(win_api, "mouse_event", events.append), \
                mock.patch.object(win_api, "mouse_wheel", events.append):
            a = self.make_automator()
            a.iron_grip_click(10, 10)
            a.scroll(10, 10, 1)
        return events

    def test_clicks_while_master_duel_is_in_front(self):
        self.assertEqual(len(self.click(md_in_front=True)), 3)  # Klick runter/hoch + 1 Raste

    def test_no_click_when_another_window_stays_in_front(self):
        import input_utils
        with self.assertRaises(input_utils.FocusLostError):
            self.click(md_in_front=False)


class UserInterruptionTest(unittest.TestCase):
    def test_first_check_with_none_pos_does_not_interrupt(self):
        a = wa.WindowAutomator.__new__(wa.WindowAutomator)
        a.last_bot_pos = None
        with mock.patch.object(win_api, "get_cursor_pos", return_value=(500, 500)):
            a.check_user_interruption()
            self.assertEqual(a.last_bot_pos, (500, 500))

    def test_interruption_detected_when_moved(self):
        a = wa.WindowAutomator.__new__(wa.WindowAutomator)
        a.last_bot_pos = (500, 500)
        with mock.patch.object(win_api, "get_cursor_pos", return_value=(505, 510)):
            a.check_user_interruption()  # innerhalb der Toleranz
        with mock.patch.object(win_api, "get_cursor_pos", return_value=(600, 500)):
            with self.assertRaises(wa.UserInterrupt):
                a.check_user_interruption()


class DataDirTest(unittest.TestCase):
    """Schreibgeschützter Programmordner (z.B. C:\\Programme): Daten nach %APPDATA%\\MD_Importer."""

    def test_writable_program_folder_is_used(self):
        import tempfile
        import app_paths
        folder = tempfile.mkdtemp()
        self.assertEqual(app_paths._data_dir(folder), folder)

    def test_read_only_program_folder_falls_back_to_appdata(self):
        import tempfile
        import app_paths
        appdata = tempfile.mkdtemp()
        with mock.patch.object(app_paths, "_writable", return_value=False), \
                mock.patch.dict(os.environ, {"APPDATA": appdata}):
            folder = app_paths._data_dir(r"C:\\Program Files\\MD_Importer")
        self.assertEqual(folder, os.path.join(appdata, "MD_Importer"))
        self.assertTrue(os.path.isdir(folder))


class MasterDuelDetectionTest(unittest.TestCase):
    """Master Duel nur am Prozess erkennen: Ein Browser mit "Master Duel" im Titel (YouTube-Video) ist es nicht."""

    EXES = {1: r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
            2: r"D:\\Steam\\steamapps\\common\\Yu-Gi-Oh!  Master Duel\\masterduel.exe"}

    def test_browser_with_master_duel_video_is_not_master_duel(self):
        import Overlay
        with mock.patch.object(win_api, "process_image_name", self.EXES.get):
            self.assertFalse(Overlay.MasterDuelImporter._is_md_active(10, 1))
            self.assertTrue(Overlay.MasterDuelImporter._is_md_active(20, 2))

    def test_window_title_alone_is_not_enough_to_find_master_duel(self):
        with mock.patch.object(win_api, "process_image_name", self.EXES.get), \
                mock.patch.object(win_api, "find_window", return_value=10), \
                mock.patch.object(win_api, "window_pid", return_value=1), \
                mock.patch.object(wa.win32gui, "EnumWindows", lambda callback, extra: None):
            self.assertIsNone(wa._search_md_window())
        with mock.patch.object(win_api, "process_image_name", self.EXES.get), \
                mock.patch.object(win_api, "find_window", return_value=20), \
                mock.patch.object(win_api, "window_pid", return_value=2):
            self.assertEqual(wa._search_md_window(), 20)


if __name__ == "__main__":
    unittest.main()
