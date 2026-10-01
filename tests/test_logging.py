"""Diagnose-Log: Rotation der alten Läufe, kein Schreiben außerhalb eines Laufs."""

import os
import tempfile
import unittest

import debug_log
from debug_log import dlog


class DebugLogTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "md_debug.log")

    def tearDown(self):
        debug_log.end_run()

    def read(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_writes_lines_and_strips_trailing_newline(self):
        debug_log.start_run(self.path)
        dlog("Zeile 1\n")
        dlog("\n[SUCHE] Karte")
        debug_log.end_run()
        self.assertEqual(self.read(self.path), "Zeile 1\n\n[SUCHE] Karte\n")

    def test_keeps_previous_runs(self):
        for run in range(1, 5):
            debug_log.start_run(self.path)
            dlog(f"Lauf {run}")
            debug_log.end_run()
        base, ext = os.path.splitext(self.path)
        self.assertEqual(self.read(self.path), "Lauf 4\n")
        self.assertEqual(self.read(f"{base}.1{ext}"), "Lauf 3\n")
        self.assertEqual(self.read(f"{base}.3{ext}"), "Lauf 1\n")
        self.assertFalse(os.path.exists(f"{base}.4{ext}"))

    def test_nothing_written_outside_a_run(self):
        dlog("ohne Lauf")  # darf keine Datei anlegen und nicht abstürzen
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
