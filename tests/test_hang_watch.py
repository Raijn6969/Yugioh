"""Hänger-Wächter: meldet blockierten Haupt-Thread einmal pro Hänger; Tk-Fehler landen im Log."""

import sys
import time
import unittest
from unittest import mock

import Overlay
from hang_watch import HangWatch


class HangWatchTest(unittest.TestCase):
    def setUp(self):
        self.lines = []
        self.watch = HangWatch(self.lines.append, threshold=0.2, check=0.05)
        self.addCleanup(self.watch.stop)

    def block(self, seconds):
        time.sleep(seconds)  # der Test-Thread ist der "Haupt-Thread"

    def test_regelmaessiger_beat_meldet_nichts(self):
        self.watch.start()
        for _ in range(15):
            self.watch.beat()
            time.sleep(0.05)
        self.assertEqual(self.lines, [])

    def test_haenger_wird_einmal_gemeldet_mit_stack_dann_vorbei(self):
        self.watch.start()
        self.block(0.6)
        self.watch.beat()
        time.sleep(0.2)
        self.assertEqual(len(self.lines), 2, self.lines)
        self.assertIn("[HÄNGER] Fenster reagiert seit", self.lines[0])
        self.assertIn("block", self.lines[0])  # Stack-Zeile aus der Testfunktion
        self.assertIn("vorbei nach", self.lines[1])

    def test_zweiter_haenger_zweiter_eintrag(self):
        self.watch.start()
        for _ in range(2):
            self.block(0.5)
            self.watch.beat()
            time.sleep(0.2)
        starts = [line for line in self.lines if "reagiert seit" in line]
        self.assertEqual(len(starts), 2, self.lines)
        self.assertEqual(len(self.lines), 4, self.lines)


class TkFehlerTest(unittest.TestCase):
    def test_traceback_ins_log(self):
        log = []
        with mock.patch.object(Overlay, "append", lambda path, text: log.append(text)):
            try:
                raise ValueError("kaputt")
            except ValueError:
                Overlay.report_tk_error(*sys.exc_info())
        self.assertTrue(log[0].startswith("[TK-FEHLER]"))
        self.assertIn("ValueError: kaputt", log[0])
        self.assertIn("Traceback", log[0])


if __name__ == "__main__":
    unittest.main()
