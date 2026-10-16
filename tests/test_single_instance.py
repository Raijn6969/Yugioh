"""Nur ein Importer gleichzeitig (benannter Mutex); beim Neustart wartet die neue Instanz auf die alte."""

import os
import threading
import time
import unittest
import uuid
from unittest import mock

import single_instance as si


@unittest.skipUnless(os.name == "nt", "nur Windows")
class SingleInstanceTest(unittest.TestCase):
    def setUp(self):
        self.name = f"Local\\MD_Importer_test_{uuid.uuid4().hex}"  # nie der Mutex des echten Importers
        self.addCleanup(si.release)

    def test_second_start_is_refused_until_the_first_ends(self):
        self.assertTrue(si.acquire(self.name, wait=0))
        self.assertIsNone(si._create(self.name))  # zweiter Importer
        si.release()
        other = si._create(self.name)  # nach dem Beenden geht es wieder
        self.assertTrue(other)
        self.assertFalse(si.acquire(self.name, wait=0))  # jetzt läuft "der andere"
        import ctypes
        ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(other))

    def test_restart_waits_for_the_old_instance(self):
        other = si._create(self.name)  # alte Instanz schließt noch
        import ctypes

        def old_instance_ends():
            time.sleep(0.5)
            ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(other))

        threading.Thread(target=old_instance_ends, daemon=True).start()
        with mock.patch.dict(os.environ, {si.RESTART_ENV: "1"}):
            start = time.monotonic()
            self.assertTrue(si.acquire(self.name))
            self.assertNotIn(si.RESTART_ENV, os.environ)  # gilt nur für diesen Start, nicht für spätere
        self.assertGreater(time.monotonic() - start, 0.3)

    def test_restart_env_marks_the_new_instance(self):
        self.assertEqual(si.restart_env()[si.RESTART_ENV], "1")
        self.assertNotIn(si.RESTART_ENV, os.environ)


class SecondStartTest(unittest.TestCase):
    def test_second_start_shows_short_note_and_opens_no_overlay(self):
        import Overlay
        shown = []
        with mock.patch.object(Overlay.single_instance, "acquire", return_value=False), \
                mock.patch.object(Overlay, "STARTED_TOAST_MS", 10), \
                mock.patch.object(Overlay, "show_toast", lambda master, text, **k: shown.append(text)), \
                mock.patch.object(Overlay, "MasterDuelImporter") as importer:
            Overlay.main()
        self.assertEqual(shown, [f"MD-Importer {Overlay.APP_VERSION} läuft jetzt."])
        importer.assert_not_called()


class ToastTest(unittest.TestCase):
    def test_toast_sits_centered_above_taskbar_without_button_and_disappears(self):
        import tkinter as tk
        import dark_dialog
        root = tk.Tk()
        root.withdraw()
        try:
            with mock.patch.object(dark_dialog.win_api, "work_area", return_value=(0, 0, 1000, 700)):
                win = dark_dialog.show_toast(root, "MD-Importer 9.2 läuft jetzt.", duration_ms=200)
            root.update()
            bottom = win.winfo_y() + win.winfo_height()
            self.assertLess(bottom, 700)          # über der Taskleiste (Arbeitsbereich endet bei 700)
            self.assertGreater(bottom, 680)
            self.assertAlmostEqual(win.winfo_x() + win.winfo_width() / 2, 500, delta=1)
            self.assertEqual([type(w).__name__ for w in win.winfo_children()], ["Label"])
            end = time.monotonic() + 2
            while win.winfo_exists() and time.monotonic() < end:
                root.update()
                time.sleep(0.02)
            self.assertFalse(win.winfo_exists())
        finally:
            root.destroy()
