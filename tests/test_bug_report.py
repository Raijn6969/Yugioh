"""Fehlerbericht: ZIP mit Logs, Einstellungen und Infos; Gegnernamen geschwärzt; nichts überschrieben,
keine Datenbanken."""

import os
import tempfile
import time
import unittest
import zipfile
from types import SimpleNamespace
from unittest import mock

import bug_report as br
import Overlay
import replay_recorder as rr
import tray
from card_stats import CardStatsDB

DUEL = {"MyID": 0, "FirstPlayer": 0, "time": 1791623726, "did": 1, "name": ["Raijn", "WXGY"],
        "Deck": [{"Main": {"CardIds": [22116]}, "Extra": {"CardIds": []}},
                 {"Main": {"CardIds": [4007]}, "Extra": {"CardIds": []}}]}
LOG = ("=== MD DECK IMPORTER 9.2 | Import ===\n"
       "[REPLAY] Mitschnitt gestartet (Raijn vs WXGY, Start 1791623726).\n"
       "[SPEICHER] Main #01 → Ash Blossom & Joyous Spring (14558)\n"
       "WXGYZ ist ein anderer Name, [WXGY] aber nicht\n")


def make_db():
    return CardStatsDB(os.path.join(tempfile.mkdtemp(), "stats.db"), base_path=None, fetch=lambda ids: {})


def add_replay(db, names=("Raijn", "WXGY"), my_id=0, start=1791623726):
    frames = [{"cards": [], "lp": {}, "turn": "", "phase": "", "t": i} for i in range(4)]
    db.save_replay(rr.replay_info(dict(DUEL, name=list(names), MyID=my_id, time=start)), frames)


class RedactTest(unittest.TestCase):
    def test_whole_words_only(self):
        text, count = br.redact(LOG, ["WXGY"])
        self.assertEqual(count, 2)
        self.assertIn("(Raijn vs <Gegner>, Start", text)
        self.assertIn("[<Gegner>] aber nicht", text)
        self.assertIn("WXGYZ ist", text)                 # anderes Wort bleibt
        self.assertIn("Ash Blossom & Joyous Spring", text)

    def test_special_characters_and_longest_first(self):
        text, count = br.redact("a.b*c und a.b*c Meier", ["a.b*c", "a.b*c Meier"])
        self.assertEqual(text, "<Gegner> und <Gegner>")   # der längere Name zuerst
        self.assertEqual(count, 2)

    def test_too_short_or_empty_names_are_skipped(self):
        self.assertEqual(br.redact("Al and Bo", ["Al", "", "  ", None]), ("Al and Bo", 0))


class HideUserTest(unittest.TestCase):
    HOME = "C:\\Users\\Raijn"

    def test_both_spellings_and_any_case(self):
        text = ("C:\\Users\\Raijn\\AppData\\x.py, c:/users/raijn/AppData, C:\\\\USERS\\\\RAIJN\\\\y "
                "und C:/Users/Raijn")
        self.assertEqual(br.hide_user(text, self.HOME),
                         "%USERPROFILE%\\AppData\\x.py, %USERPROFILE%/AppData, %USERPROFILE%\\\\y "
                         "und %USERPROFILE%")

    def test_other_folders_stay(self):
        text = "C:\\Users\\Andere\\x und C:\\Users\\Raijn2\\x und D:\\Users\\Raijn"
        self.assertEqual(br.hide_user(text, self.HOME), text)

    def test_empty_or_invalid_home_changes_nothing(self):
        for home in ("", None, "~", "C:\\"):
            self.assertEqual(br.hide_user("C:\\Users\\Raijn ~", home), "C:\\Users\\Raijn ~")


class OpponentNamesTest(unittest.TestCase):
    def test_only_the_other_player_of_every_replay(self):
        db = make_db()
        add_replay(db, ("Raijn", "WXGY"), 0, 1000)
        add_replay(db, ("Neo", "Raijn"), 1, 2000)       # hier war man Spieler 1
        add_replay(db, ("Raijn", "WXGY"), 0, 3000)
        self.assertEqual(db.opponent_names(), ["Neo", "WXGY"])
        self.assertEqual(make_db().opponent_names(), [])


class CreateReportTest(unittest.TestCase):
    def setUp(self):
        self.home = os.path.join(tempfile.mkdtemp(), "Users", "Testnutzer")
        self.data = os.path.join(self.home, "AppData", "MD")
        os.makedirs(self.data)
        self.downloads = tempfile.mkdtemp()
        self.log = os.path.join(self.data, "md_debug.log")
        self.config = os.path.join(self.data, "md_config.json")
        self.startup = os.path.join(self.data, "md_startup_error.log")
        slashes = self.home.lower().replace("\\", "/")           # zweite Schreibweise, kleingeschrieben
        self.write(self.log, LOG + f"Traceback File \"{self.home}\\x.py\" und {slashes}/y\n")
        self.write(os.path.join(self.data, "md_debug.1.log"), "alter Lauf gegen WXGY\n")
        self.write(os.path.join(self.data, "md_debug.2.log"), "noch älter\n")
        self.write(self.config, '{"SPEED_PROFILE": "normal", "READ_METHOD": "memory"}')
        # Dinge, die nie in den Bericht gehören
        for name in ("md_card_stats.db", "md_card_stats_base.db"):
            self.write(os.path.join(self.data, name), "SQLite")
        os.makedirs(os.path.join(self.data, "backups"))
        self.write(os.path.join(self.data, "backups", "md_card_stats_2026-10-10.db"), "Sicherung")
        self.db = make_db()
        add_replay(self.db)

    @staticmethod
    def write(path, text):
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)

    def create(self, **kwargs):
        args = dict(db=self.db, folder=self.downloads, log_file=self.log, config_file=self.config,
                    startup_error=self.startup, data_dir=self.data, home=self.home)
        args.update(kwargs)
        return br.create(**args)

    def test_contents_and_redaction(self):
        path = self.create(now=time.mktime((2026, 10, 11, 14, 3, 22, 0, 0, -1)))
        self.assertEqual(os.path.basename(path), "MD-Importer_Fehlerbericht_2026-10-11_140322.zip")
        self.assertEqual(os.path.dirname(path), self.downloads)
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(sorted(archive.namelist()),
                             ["info.txt", "md_config.json", "md_debug.1.log", "md_debug.2.log", "md_debug.log"])
            log = archive.read("md_debug.log").decode("utf-8")
            old = archive.read("md_debug.1.log").decode("utf-8")
            info = archive.read("info.txt").decode("utf-8")
        self.assertNotIn("WXGY\n", log.replace("WXGYZ", ""))
        self.assertIn("(Raijn vs <Gegner>, Start", log)           # eigener Name bleibt, der des Gegners nicht
        self.assertIn("Ash Blossom & Joyous Spring", log)
        self.assertEqual(old, "alter Lauf gegen <Gegner>\n")      # auch in den rotierten Logs
        self.assertIn(f"App-Version: {br.APP_VERSION}", info)
        self.assertIn("Windows:", info)
        self.assertIn("Python:", info)
        self.assertIn("Bildschirm:", info)
        self.assertIn("Datenordner: %USERPROFILE%", info)
        self.assertIn("als %USERPROFILE% ersetzt", info)
        self.assertIn("Nicht enthalten: Datenbanken, Sicherungen, Replays.", info)
        self.assertIn("1 bekannte Name(n)", info)
        self.assertIn('durch "<Gegner>" ersetzt', info)
        self.assertNotIn("WXGY", info)                            # der Name selbst steht nirgends in info.txt

    def test_user_folder_is_nowhere_in_the_zip(self):
        self.write(self.config, '{"DIR": "' + self.home.replace("\\", "\\\\") + '\\\\x"}')
        with zipfile.ZipFile(self.create()) as archive:
            texts = {n: archive.read(n).decode("utf-8").lower() for n in archive.namelist()}
        user = "testnutzer"
        self.assertEqual([n for n, t in texts.items() if user in t], [])
        self.assertIn("%userprofile%", texts["md_debug.log"])
        self.assertIn("%userprofile%", texts["md_config.json"])

    def test_never_contains_databases_backups_or_replays(self):
        path = self.create()
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
        self.assertFalse([n for n in names if n.endswith(".db") or "backups" in n or "replay" in n.lower()], names)

    def test_startup_error_log_is_included(self):
        self.write(self.startup, "Traceback ... gegen WXGY")
        with zipfile.ZipFile(self.create()) as archive:
            self.assertEqual(archive.read("md_startup_error.log").decode("utf-8"), "Traceback ... gegen <Gegner>")

    def test_never_overwrites(self):
        now = time.time()
        first, second = self.create(now=now), self.create(now=now)
        self.assertNotEqual(first, second)
        self.assertTrue(second.endswith("_2.zip"))
        self.assertEqual(len(os.listdir(self.downloads)), 2)

    def test_works_without_logs_config_or_database(self):
        empty = tempfile.mkdtemp()
        path = self.create(db=None, log_file=os.path.join(empty, "md_debug.log"),
                           config_file=os.path.join(empty, "md_config.json"))
        with zipfile.ZipFile(path) as archive:
            self.assertEqual(archive.namelist(), ["info.txt"])
            self.assertIn("0 bekannte Name(n)", archive.read("info.txt").decode("utf-8"))

    def test_broken_database_still_gives_a_report(self):
        broken = mock.Mock()
        broken.opponent_names.side_effect = Exception("database is locked")
        with zipfile.ZipFile(self.create(db=broken)) as archive:
            self.assertIn("md_debug.log", archive.namelist())

    def test_huge_log_keeps_only_the_end(self):
        self.write(self.log, "alt\n" * 10 + "neu\n")
        text = br.read_text(self.log, limit=8)
        self.assertTrue(text.startswith("[... Anfang gekürzt"))
        self.assertTrue(text.endswith("neu\n"))

    def test_failed_zip_leaves_nothing_behind(self):
        with mock.patch.object(br.zipfile.ZipFile, "writestr", side_effect=OSError("Platte voll")):
            with self.assertRaises(OSError):
                self.create()
        self.assertEqual(os.listdir(self.downloads), [])

    def test_default_folder_is_downloads(self):
        with mock.patch.object(br, "downloads_dir", return_value=self.downloads):
            path = self.create(folder=None)
        self.assertEqual(os.path.dirname(path), self.downloads)

    def test_real_files_are_not_touched(self):
        # Keine Zugriffe auf die echten Daten: alles lief mit den temporären Pfaden aus setUp
        self.assertNotEqual(os.path.abspath(self.data), os.path.abspath(br.DATA_DIR))


class OverlayReportTest(unittest.TestCase):
    """Overlay-Teil ohne Fenster: Hintergrund-Thread, Statuszeile, Explorer."""

    def make_app(self):
        shown, tray_msgs = [], []
        app = SimpleNamespace(_report_running=False, shown=shown, tray_msgs=tray_msgs,
                              update_status=lambda text, color: shown.append(text),
                              tray=SimpleNamespace(notify=lambda text, title: tray_msgs.append(text)),
                              _stats_db=lambda: "db", queued=[])
        app._run_on_ui = lambda func, *args: app.queued.append((func, args))
        app._on_report_done = lambda path, error: Overlay.MasterDuelImporter._on_report_done(app, path, error)
        return app

    def test_runs_in_background_and_reports_in_status_line(self):
        app = self.make_app()
        with mock.patch.object(Overlay.bug_report, "create", return_value="C:\\Downloads\\Bericht.zip") as create, \
                mock.patch.object(Overlay.bug_report, "reveal") as reveal:
            Overlay.MasterDuelImporter._create_report(app)
            self.assertEqual(app.shown, ["Fehlerbericht wird erstellt …"])
            self.assertTrue(app._report_running)
            Overlay.MasterDuelImporter._create_report(app)        # zweiter Klick währenddessen: nichts
            deadline = time.time() + 5
            while not app.queued and time.time() < deadline:
                time.sleep(0.01)
            create.assert_called_once_with("db")
            (func, args), = app.queued
            func(*args)
        self.assertEqual(app.shown[-1], "Fehlerbericht gespeichert")
        self.assertFalse(app._report_running)
        reveal.assert_called_once_with("C:\\Downloads\\Bericht.zip")
        self.assertIn("Bericht.zip", app.tray_msgs[0])

    def test_failure_is_shown_not_raised(self):
        app = self.make_app()
        with mock.patch.object(Overlay.bug_report, "create", side_effect=OSError("kein Zugriff")), \
                mock.patch.object(Overlay, "append") as log, mock.patch.object(Overlay.bug_report, "reveal") as reveal:
            Overlay.MasterDuelImporter._create_report(app)
            deadline = time.time() + 5
            while not app.queued and time.time() < deadline:
                time.sleep(0.01)
            (func, args), = app.queued
            func(*args)
        self.assertEqual(app.shown[-1], "Fehlerbericht fehlgeschlagen")
        self.assertFalse(app._report_running)
        reveal.assert_not_called()
        self.assertIn("kein Zugriff", log.call_args.args[1])


class TrayReportEntryTest(unittest.TestCase):
    def test_menu_has_report_entry(self):
        from tests.test_update_check import FakePystray
        clicked = []
        with mock.patch.object(tray, "pystray", FakePystray), mock.patch.object(tray.threading, "Thread"):
            icon = tray.TrayIcon("MD Importer", on_restart=lambda: None, on_quit=lambda: None,
                                 on_report=lambda: clicked.append(True))
        items = [i for i in icon._icon.menu.items if isinstance(i, FakePystray.MenuItem)]
        labels = [i.label() for i in items if i.shown()]
        self.assertEqual(labels, ["MD Importer", "Fehlerbericht erstellen", "Neustart", "Beenden"])
        items[2].action(icon._icon, items[2])
        self.assertEqual(clicked, [True])


if __name__ == "__main__":
    unittest.main()
