"""Diagnose-Log: Rotation der alten Läufe, kein Schreiben ohne start_run, keine Logflut."""

import os
import tempfile
import unittest
import unittest.mock

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

    def test_lines_appended_during_a_run_are_not_overwritten(self):
        debug_log.start_run(self.path)
        dlog("eins")
        debug_log.append(self.path, "[HÄNGER] von einem anderen Thread")
        dlog("zwei")
        debug_log.end_run()
        self.assertEqual(self.read(self.path), "eins\n[HÄNGER] von einem anderen Thread\nzwei\n")

    def test_locked_log_file_does_not_stop_the_run(self):
        with unittest.mock.patch.object(debug_log, "_CappedHandler", side_effect=PermissionError("gesperrt")):
            debug_log.start_run(self.path)
        dlog("geht ins Leere")
        debug_log.end_run()

    def test_append_stops_at_the_size_limit(self):
        debug_log.append(self.path, "eins")
        with unittest.mock.patch.object(debug_log, "MAX_LOG_BYTES", 3):
            debug_log.append(self.path, "zwei")
        self.assertEqual(self.read(self.path), "eins\n")

    def test_dlog_stops_at_the_size_limit(self):
        debug_log.start_run(self.path)
        dlog("eins")
        with unittest.mock.patch.object(debug_log, "MAX_LOG_BYTES", 3):
            dlog("zwei")
        debug_log.end_run()
        self.assertEqual(self.read(self.path), "eins\n")

    def test_changed_message_is_logged_once_until_cleared(self):
        debug_log.start_run(self.path)
        for _ in range(3):
            debug_log.dlog_changed("t", "Fehler A")
        debug_log.dlog_changed("t", "Fehler B")
        debug_log.clear_changed("t")  # Erfolg dazwischen: derselbe Fehler gilt wieder als neu
        debug_log.dlog_changed("t", "Fehler B")
        debug_log.end_run()
        self.assertEqual(self.read(self.path), "Fehler A\nFehler B\nFehler B\n")

    def test_nothing_written_outside_a_run(self):
        dlog("ohne Lauf")  # darf keine Datei anlegen und nicht abstürzen
        self.assertFalse(os.path.exists(self.path))


if __name__ == "__main__":
    unittest.main()
