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
    def test_second_start_shows_hint_and_opens_no_overlay(self):
        import Overlay
        shown = []
        with mock.patch.object(Overlay.single_instance, "acquire", return_value=False), \
                mock.patch.object(Overlay, "show_message", lambda master, title, *a, **k: shown.append(title)), \
                mock.patch.object(Overlay, "MasterDuelImporter") as importer:
            Overlay.main()
        self.assertEqual(shown, ["MD Importer läuft bereits"])
        importer.assert_not_called()
