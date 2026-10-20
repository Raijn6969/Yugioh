"""Overlay-Fenster: Config-Schutz, Tempo-Auswahl, Fensterposition, Timer, UI aus Threads."""

import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAS_TK = True
except Exception:
    HAS_TK = False

import Overlay
from dark_scrollbar import DarkScrollbar

_REAL_FOCUS_LOOP = Overlay.MasterDuelImporter._check_window_focus  # vor dem Patch in OverlayTest.setUp


class ValidateConfigTest(unittest.TestCase):
    def test_valid_config_passes_without_warnings(self):
        cfg = {"SPEED_PROFILE": "fast", "READ_METHOD": "memory", "SEARCH_BAR": [1400, 200], "FIRST_CARD": [1300, 300]}
        cleaned, warnings = Overlay.validate_config(cfg)
        self.assertEqual((warnings, cleaned), ([], cfg))

    def test_invalid_speed_profile_and_read_method_fall_back(self):
        cleaned, warnings = Overlay.validate_config({"SPEED_PROFILE": "turbo_ultra", "READ_METHOD": "telepathy"})
        self.assertEqual(len(warnings), 2)
        self.assertEqual((cleaned["SPEED_PROFILE"], cleaned["READ_METHOD"]), ("normal", "memory"))

    def test_malformed_coordinates_warning(self):
        cleaned, warnings = Overlay.validate_config({"SEARCH_BAR": "not_a_coordinate"})
        self.assertEqual(len(warnings), 1)
        self.assertIn("SEARCH_BAR", warnings[0])


@unittest.skipUnless(HAS_TK, "Tk nicht verfügbar")
class OverlayTest(unittest.TestCase):
    def setUp(self):
        self.config_file = os.path.join(tempfile.mkdtemp(), "md_config.json")
        self.warnings = []
        self.patches = [
            mock.patch.object(Overlay, "CONFIG_FILE", self.config_file),
            mock.patch.object(Overlay, "show_message", lambda master, title, *a, **k: self.warnings.append(title)),
            # Auto-Ausblenden aus: im Test ist Master Duel nicht im Vordergrund
            mock.patch.object(Overlay.MasterDuelImporter, "_check_window_focus", lambda self: None),
            # Keine Sicherung der echten Datenbank aus dem Test heraus (eigener Test in test_db_backup)
            mock.patch.object(Overlay.MasterDuelImporter, "_backup_database", lambda self: None),
            # Abfrage der Lesemethode beim ersten Start: im Test nichts gewählt (eigener Test unten)
            mock.patch.object(Overlay, "ask_choice", return_value=None),
            # Nie die echte Zwischenablage oder die echte Datenbank (Verlauf) des Spielers anfassen
            mock.patch.object(Overlay.pyperclip, "paste", lambda: self.clipboard[0]),
            mock.patch.object(Overlay.pyperclip, "copy", lambda text: self.clipboard.__setitem__(0, text)),
            mock.patch.object(Overlay, "CardStatsDB", lambda: self.make_db()),
            # Kein Hänger-Wächter im Test (kein Herzschlag, und das echte Log bliebe unberührt)
            mock.patch.object(Overlay.HangWatch, "start", lambda self: None),
        ]
        self.clipboard = [""]
        self.db_path = os.path.join(tempfile.mkdtemp(), "stats.db")
        for p in self.patches:
            p.start()
        self.apps = []

    def tearDown(self):
        for app in self.apps:
            app.close()
        for p in self.patches:
            p.stop()

    def make_db(self):
        from card_stats import CardStatsDB
        return CardStatsDB(self.db_path, fetch=lambda ids: {}, base_path=None)

    def write_config(self, text):
        with open(self.config_file, "w", encoding="utf-8") as f:
            f.write(text)

    def open_app(self):
        root = tk.Tk()
        app = Overlay.MasterDuelImporter(root)
        self.apps.append(app)
        self.pump(root, 0.2)
        return root, app

    @staticmethod
    def pump(root, seconds):
        end = time.time() + seconds
        while time.time() < end:
            root.update()
            time.sleep(0.01)

    def test_dialogs_also_wait_while_a_duel_runs_with_another_window_in_front(self):
        root, app = self.open_app()
        app.match_watch.enabled = False   # der Wächter-Thread soll den Stand im Test nicht überschreiben
        with mock.patch.object(Overlay.win32gui, "GetForegroundWindow", return_value=0):   # z. B. Discord vorne
            app.match_watch._views = ["HomeViewController", "ColosseumInfoViewController", "DuelClient"]
            self.assertTrue(app._md_in_front())
            app.match_watch._views = ["ColosseumHistoryViewController", "DuelClient"]     # Replay: kein Duell
            self.assertFalse(app._md_in_front())
            app.match_watch._views = ["DuelClient"]
            app.match_watch.check()                                           # Speicher-Modus aus: Stand vergessen
            self.assertFalse(app._md_in_front())

    def test_dialogs_without_user_action_wait_until_md_is_not_in_front(self):
        from types import SimpleNamespace
        import updater
        self.write_config(json.dumps({"IS_CALIBRATED": True, "READ_METHOD": "memory"}))
        root, app = self.open_app()
        folder = tempfile.mkdtemp()
        with open(os.path.join(folder, updater.FAILED_MARKER), "w", encoding="utf-8") as f:
            f.write("robocopy exit code 8")
        release = SimpleNamespace(version="99.0", zip_url="", page="")
        front = [True]
        app._md_in_front = lambda: front[0]
        with mock.patch.object(Overlay, "ask_choice", return_value=None) as ask, \
                mock.patch.object(Overlay, "show_message") as show, \
                mock.patch.object(Overlay, "APP_DIR", folder), \
                mock.patch.object(Overlay, "MD_FRONT_RETRY_MS", 20), mock.patch.object(Overlay, "UPDATE_RETRY_MS", 20):
            app.update_release = release
            app._ask_update_when_idle()  # Klick im Tray-Menü: sofort, auch mit Master Duel vorne
            self.assertEqual(ask.call_count, 1)
            ask.reset_mock()
            app._ask_read_method()
            app._on_update_found(release)
            app._offer_ocr_when_idle()
            app._show_db_recovered("md_card_stats.db.defekt-1", "2026-10-15")
            app._show_update_failed()
            app._show_config_broken("kaputt")
            self.pump(root, 0.15)
            self.assertEqual((ask.call_count, show.call_count), (0, 0))  # Master Duel vorne: kein Dialog
            front[0] = False
            self.pump(root, 0.15)
            self.assertEqual((ask.call_count, show.call_count), (3, 3))  # nicht mehr vorne: alle kommen

    def test_tk_scaling_does_not_follow_the_windows_scaling(self):
        root = tk.Tk()
        root.tk.call("tk", "scaling", 2.0)  # wie bei Windows-Skalierung 150 %
        self.apps.append(Overlay.MasterDuelImporter(root))
        self.assertAlmostEqual(float(root.tk.call("tk", "scaling")), 96 / 72)

    def test_panels_look_the_same_at_150_percent_windows_scaling(self):
        import history_panel
        import winrate_panel
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        root.winfo_screenheight = lambda: 1080  # 1080p: Maßstab 1,0
        db = mock.Mock()
        db.history.return_value = []
        db.deck_win_stats.return_value = db.matches.return_value = db.opponent_stats.return_value = []
        db.win_stats.return_value = winrate_panel.WinStats()
        db.replay_starts_by_match.return_value = {}

        def widest():
            sizes = []
            for panel in (history_panel.HistoryPanel(root, db, lambda c: None, lambda c: None, lambda: None),
                          winrate_panel.WinratePanel(root, db, {}, lambda: None, lambda *a: None, lambda: None,
                                                     tracking=True)):
                root.update()
                stack = [panel.win]
                widths = []
                while stack:
                    widget = stack.pop()
                    stack.extend(widget.winfo_children())
                    widths.append(widget.winfo_reqwidth())
                sizes.append(max(widths))
                panel.close()
            return sizes

        Overlay.fix_tk_scaling(root)
        at_100 = widest()
        root.tk.call("tk", "scaling", 2.0)
        self.assertNotEqual(widest(), at_100)  # ohne die Korrektur wären die Fenster zu schmal
        Overlay.fix_tk_scaling(root)
        self.assertEqual(widest(), at_100)

    def test_corrupt_config_is_not_overwritten(self):
        self.write_config('{"FIRST_CARD": [1, 2], "SPEED_PROFILE": "slow",}')  # Komma zu viel
        self.open_app()
        with open(self.config_file, encoding="utf-8") as f:
            self.assertIn('"SPEED_PROFILE": "slow",}', f.read())
        self.assertTrue(os.path.exists(self.config_file + ".defekt.bak"))
        self.assertEqual(len(self.warnings), 1)

    def test_calibration_remembers_window_origin(self):
        root, app = self.open_app()
        frame = Overlay.md_layout.Frame(300, 40, 1920, 1080)
        with mock.patch.object(Overlay, "get_md_window_size", return_value=(1920, 1080)), \
                mock.patch.object(Overlay.md_layout, "md_frame", return_value=frame):
            app.on_calibration_done({"FIRST_CARD": [1, 2]})
        with open(self.config_file, encoding="utf-8") as f:
            saved = json.load(f)
        self.assertEqual((saved["CALIBRATED_SIZE"], saved["CALIBRATED_ORIGIN"]), ([1920, 1080], [300, 40]))

    def test_speed_selection_is_saved(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        root, app = self.open_app()
        app.speed_menu.invoke(2)  # Langsam
        self.assertEqual(app.speed_btn.cget("text"), "Tempo: Langsam ▾")
        with open(self.config_file, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["SPEED_PROFILE"], "slow")

    def test_speed_button_only_with_text_recognition(self):
        # Speicher lesen: Tempo ist nur Reserve → im Deck-Fenster unter Optionen, nicht im Overlay
        self.write_config(json.dumps({"IS_CALIBRATED": True, "READ_METHOD": "memory"}))
        root, app = self.open_app()
        self.assertFalse(app.speed_btn.winfo_manager())
        narrow = root.winfo_width()
        right = root.winfo_x() + narrow
        # In den Optionen auf Texterkennung umgestellt → Button wieder da, Overlay breiter, rechte Kante bleibt
        app.config["READ_METHOD"] = "ocr"
        app._on_settings_changed()
        self.pump(root, 0.1)
        self.assertTrue(app.speed_btn.winfo_manager())
        self.assertGreater(root.winfo_width(), narrow)
        if right <= root.winfo_screenwidth() - root.winfo_width():  # sonst (z.B. hochkant per RDP): an den Rand
            self.assertEqual(root.winfo_x() + root.winfo_width(), right)
        # Tempo in den Optionen geändert → Overlay übernimmt es
        app.config["SPEED_PROFILE"] = "slow"
        app._on_settings_changed()
        self.assertEqual(app.speed_btn.cget("text"), "Tempo: Langsam ▾")
        with open(self.config_file, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["SPEED_PROFILE"], "slow")

    def test_calibration_lives_in_deck_options(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        root, app = self.open_app()
        self.assertFalse(hasattr(app, "calib_btn"))
        with mock.patch.object(Overlay.md_layout, "md_frame", return_value=None), \
                mock.patch.object(Overlay.MasterDuelImporter, "start_auto_calibration") as auto, \
                mock.patch.object(Overlay.MasterDuelImporter, "open_calibration") as manual:
            self.open_extras(app, root, None)
            app.extras.on_auto_calibrate()
            app.extras.on_calibrate_manual()
        auto.assert_called_once_with()
        manual.assert_called_once_with()

    def test_import_export_and_deck_have_equal_width(self):
        for method in ("memory", "ocr"):  # mit und ohne Tempo-Knopf daneben
            self.write_config(json.dumps({"IS_CALIBRATED": True, "READ_METHOD": method}))
            root, app = self.open_app()
            widths = [b.winfo_width() for b in (app.start_btn, app.export_btn, app.extras_btn)]
            self.assertLessEqual(max(widths) - min(widths), 2, widths)

    def test_default_position_follows_game_window(self):
        # Fenstermodus: ohne gemerkte Position sitzt die Leiste am unteren Rand von Master Duel, nicht des Bildschirms
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        with mock.patch.object(Overlay.md_layout, "md_frame", return_value=Overlay.md_layout.Frame(100, 50, 800, 450)):
            root, app = self.open_app()
        self.assertEqual(root.winfo_y() + root.winfo_height(), 500)
        self.assertGreaterEqual(root.winfo_x(), 100)

    def test_window_position_remembered(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        root, app = self.open_app()
        # Position knapp innerhalb des Desktops (unabhängig von Bildschirmgröße und Skalierung)
        vx, vy, _, _ = Overlay.win_api.virtual_screen_rect()
        x, y = vx + 20, vy + 30
        root.geometry(f"+{x}+{y}")
        root.update()
        app._moved = True
        app.end_move(None)
        root2, _ = self.open_app()
        self.assertEqual((root2.winfo_x(), root2.winfo_y()), (x, y))

    def test_status_and_timer_from_worker_thread(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        root, app = self.open_app()
        app.is_running = True

        def worker():
            app._run_on_ui(app._on_import_started)
            for i in range(20):
                app._run_on_ui(app.update_status, f"-> Karte {i}", "cyan")
            time.sleep(1.1)
            app._run_on_ui(app._on_import_finished, True, False, [])

        threading.Thread(target=worker, daemon=True).start()
        self.pump(root, 1.6)
        self.assertEqual(app.status_label.cget("text"), "Import Erfolgreich!")
        self.assertEqual(str(app.speed_btn.cget("state")), "normal")

    def test_timer_is_part_of_the_status_line_only_while_running(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        root, app = self.open_app()
        app.update_status("Bereit für Import")
        self.assertEqual(app.status_label.cget("text"), "Bereit für Import")  # Leerlauf: keine Zeit
        app.is_running = True
        with mock.patch.object(Overlay.time, "perf_counter", return_value=100.0):
            app._on_import_started()
        with mock.patch.object(Overlay.time, "perf_counter", return_value=142.0):
            app.update_status("Importiere …")
            self.assertEqual(app.status_label.cget("text"), "Importiere … · 0:42")
        app.is_running = False
        app._import_t0 = None
        app.update_status("Import Erfolgreich!")
        self.assertEqual(app.status_label.cget("text"), "Import Erfolgreich!")

    def test_own_window_over_header_pauses_the_editor_check(self):
        import md_layout
        root, app = self.open_app()
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.geometry("300x200+100+100")
        self.pump(root, 0.1)
        frame = md_layout.Frame(0, 0, 1920, 1080)
        header = frame.region(*md_layout.MAIN_HEADER_REGION)

        def over(frame_or_none=frame):
            with mock.patch.object(md_layout, "md_frame", return_value=frame_or_none):
                return app._own_window_over_header()

        win.geometry(f"300x200+{header['left'] + 10}+{header['top'] + 10}")
        self.pump(root, 0.1)
        self.assertTrue(over())
        win.geometry(f"300x200+{header['left'] + header['width'] + 50}+{header['top']}")  # daneben
        self.pump(root, 0.1)
        self.assertFalse(over())
        win.geometry(f"300x200+{header['left'] + 10}+{header['top'] + 10}")
        win.wm_attributes("-alpha", 0.0)  # ausgeblendet stört die Erkennung nicht
        self.pump(root, 0.1)
        self.assertFalse(over())
        win.wm_attributes("-alpha", 1.0)
        self.pump(root, 0.1)
        self.assertFalse(over(None))  # ohne Master-Duel-Fenster

        # Focus-Loop übernimmt es nach editor_watch.paused
        app.extras = None
        with mock.patch.object(Overlay.win32gui, "GetForegroundWindow", return_value=1), \
             mock.patch.object(Overlay.win32process, "GetWindowThreadProcessId", return_value=(0, 4242)), \
             mock.patch.object(app, "_is_md_active", return_value=True), \
             mock.patch.object(app, "_should_show", return_value=True), \
             mock.patch.object(app, "_own_window_over_header", return_value=True):
            _REAL_FOCUS_LOOP(app)
            self.assertTrue(app.editor_watch.paused)
        with mock.patch.object(Overlay.win32gui, "GetForegroundWindow", return_value=1), \
             mock.patch.object(Overlay.win32process, "GetWindowThreadProcessId", return_value=(0, 4242)), \
             mock.patch.object(app, "_is_md_active", return_value=True), \
             mock.patch.object(app, "_should_show", return_value=True), \
             mock.patch.object(app, "_own_window_over_header", return_value=False):
            _REAL_FOCUS_LOOP(app)
            self.assertFalse(app.editor_watch.paused)
        root.after_cancel(app._after_ids["focus"])  # die Schleife hat sich neu eingeplant

    def test_own_window_over_header_not_checked_without_master_duel_front(self):
        root, app = self.open_app()
        app.extras = None
        with mock.patch.object(Overlay.win32gui, "GetForegroundWindow", return_value=1), \
             mock.patch.object(Overlay.win32process, "GetWindowThreadProcessId", return_value=(0, 4242)), \
             mock.patch.object(app, "_is_md_active", return_value=False), \
             mock.patch.object(app, "_should_show", return_value=False), \
             mock.patch.object(app, "_own_window_over_header", return_value=True) as over:
            _REAL_FOCUS_LOOP(app)
            over.assert_not_called()  # ohne Master Duel vorne keine Fenstersuche alle 300 ms
            self.assertFalse(app.editor_watch.paused)
        root.after_cancel(app._after_ids["focus"])

    def test_flat_and_snaps_to_bottom_edge(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        root, app = self.open_app()
        scale = max(1.0, root.winfo_screenheight() / 1080.0)
        self.assertLess(root.winfo_height(), 70 * scale)  # flacher als früher (85)
        screen_h, height = root.winfo_screenheight(), root.winfo_height()
        # Position vom früheren, höheren Overlay (unten angedockt) → wieder genau am Rand
        old_docked_y = screen_h - int(85 * scale)
        self.assertEqual(app._snap_to_bottom(old_docked_y, height), screen_h - height)
        self.assertEqual(app._snap_to_bottom(300, height), 300)  # mitten auf dem Bildschirm: bleibt
        # Knapp unter dem Rand losgelassen (ragt 20 px hinaus) → ebenfalls genau an den Rand
        self.assertEqual(app._snap_to_bottom(screen_h - height + 20, height), screen_h - height)

    def test_warning_when_dropped_over_the_deck(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        root, app = self.open_app()
        frame = Overlay.md_layout.Frame(0, 0, 1920, 1080)
        with mock.patch.object(Overlay.md_layout, "md_frame", return_value=frame):
            for position, expected in (("+600+500", "verdeckt das Deck"), ("+1400+500", "verdeckt die Kartenliste"),
                                       ("+40+1000", "Bereit für Import")):
                root.geometry(position)
                root.update()
                app._moved = True
                app.end_move(None)
                self.assertIn(expected, app.status_label.cget("text"))

    def open_extras(self, app, root, scan):
        """Extras öffnen; der Scan liefert sofort `scan` (statt Master Duel zu lesen)."""
        class FakeExporter:
            memory_fallback = False

            def __init__(self, config, cmd, status_cb, finish_cb, **kwargs):
                self.finish_cb = finish_cb

            def execute_scan(self):
                self.finish_cb(scan=scan, error="")

        from card_stats import HandtrapInfo, StarterInfo
        stats = mock.Mock()
        stats.ensure.return_value = 0
        stats.info.return_value = None
        stats.starter.return_value = StarterInfo(True, "sucht", False)
        stats.handtrap.return_value = HandtrapInfo(False, "", False)
        app.card_stats = stats
        with mock.patch.object(Overlay, "DeckExporter", FakeExporter), \
                mock.patch("extras_panel.DeckWatcher.start", lambda self: None):
            app.toggle_extras()
            self.pump(root, 0.3)

    def test_resume_question_uses_dark_dialog(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        root, app = self.open_app()
        resume = {"done": {"1": 1}, "saved_at": "2026-10-03 10:00:00"}
        for answer, clears, starts in ((None, False, False), (False, True, True), (True, False, True)):
            with mock.patch.object(Overlay, "parse_clipboard", return_value=["1"]), \
                    mock.patch.object(Overlay.resume_state, "load_progress", return_value=resume), \
                    mock.patch.object(Overlay.resume_state, "clear_progress") as clear, \
                    mock.patch.object(Overlay, "ask_choice", return_value=answer) as ask, \
                    mock.patch.object(Overlay, "DeckImporterCore") as core:
                app.is_running = False
                app.start_import_thread()
            labels = [label for label, _, _ in ask.call_args.args[3]]
            self.assertEqual(labels, ["Fortsetzen", "Neu starten", "Abbrechen"])
            self.assertEqual(clear.called, clears)
            self.assertEqual(core.called, starts)
            if starts:  # Fortsetzen: gespeicherter Stand geht an den Import, Neu starten: keiner
                self.assertEqual(core.call_args.kwargs["resume"], resume if answer else None)

    def test_first_start_asks_for_read_method_and_saves_it(self):
        root, app = self.open_app()  # keine Config: erster Start
        self.assertIn("read_method", app._after_ids)
        with mock.patch.object(Overlay, "ask_choice", return_value="memory") as ask:
            app._ask_read_method()
        self.assertEqual([label for label, _, _ in ask.call_args.args[3]], ["Speicher lesen", "Texterkennung"])
        with open(self.config_file, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["READ_METHOD"], "memory")
        root2, app2 = self.open_app()
        self.assertNotIn("read_method", app2._after_ids)  # danach nicht mehr gefragt

    def memory_app(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True, "READ_METHOD": "memory"}))
        return self.open_app()

    def saved_read_method(self):
        with open(self.config_file, encoding="utf-8") as f:
            return json.load(f)["READ_METHOD"]

    def test_memory_outage_popup_can_switch_to_ocr(self):
        root, app = self.memory_app()
        with mock.patch.object(Overlay, "ask_choice", return_value="ocr") as ask:
            app._offer_ocr_when_idle()
        self.assertEqual([label for label, _, _ in ask.call_args.args[3]], ["Zur Texterkennung wechseln", "Später"])
        self.assertEqual(self.saved_read_method(), "ocr")  # Fake-Config, nicht die echte
        self.assertFalse(app._use_memory())
        self.assertTrue(app.speed_btn.winfo_manager())  # Tempo-Knopf gehört zur Texterkennung

    def test_memory_outage_popup_later_changes_nothing(self):
        root, app = self.memory_app()
        with mock.patch.object(Overlay, "ask_choice", return_value=None) as ask:
            app._offer_ocr_when_idle()
        self.assertEqual(ask.call_count, 1)
        self.assertTrue(app._use_memory())
        self.assertEqual(self.saved_read_method(), "memory")

    def test_memory_outage_popup_waits_until_import_is_done(self):
        root, app = self.memory_app()
        app.is_running = True
        with mock.patch.object(Overlay, "ask_choice", return_value=None) as ask:
            app._offer_ocr_when_idle()
            self.assertEqual(ask.call_count, 0)
            self.assertIn("ocr_offer", app._after_ids)  # wird später erneut versucht
            app.is_running = False
            app._offer_ocr_when_idle()
        self.assertEqual(ask.call_count, 1)

    def test_no_popup_when_already_using_ocr(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True, "READ_METHOD": "ocr"}))
        root, app = self.open_app()
        with mock.patch.object(Overlay, "ask_choice") as ask, \
                 mock.patch.object(Overlay.md_memory, "is_ready", return_value=False):
            app._offer_ocr_when_idle()
            app._memory_fell_back()
        self.assertEqual(ask.call_count, 0)

    def test_failed_preloads_open_the_popup_exactly_once(self):
        root, app = self.memory_app()
        with mock.patch.object(Overlay, "ask_choice", return_value=None) as ask, \
                 mock.patch.object(Overlay.md_memory, "is_ready", return_value=False), \
                 mock.patch.object(Overlay.md_memory, "preload_failures", return_value=Overlay.MEMORY_WARN_AFTER), \
                 mock.patch.object(Overlay.md_memory, "preload", lambda: None):
            with mock.patch.object(app, "update_status") as status:
                for _ in range(3):
                    app._memory_preload_at = -1000.0
                    app._preload_memory()
                    app._memory_thread.join()
                self.pump(root, 0.3)
        self.assertEqual(ask.call_count, 1)
        status.assert_called_once_with("Speicher-Modus nicht verfügbar", Overlay.WARN)  # beim Vorladen wie bisher

    def test_fallback_of_an_export_opens_the_popup_afterwards(self):
        root, app = self.memory_app()
        with mock.patch.object(Overlay, "DeckExporter") as exporter, mock.patch.object(Overlay.threading, "Thread"), \
                 mock.patch.object(Overlay, "ask_choice", return_value=None) as ask, \
                 mock.patch.object(Overlay.md_memory, "is_ready", return_value=False), \
                 mock.patch.object(app, "update_status") as status:
            app.start_export_thread()
            exporter.return_value.memory_fallback = True
            finish_cb = exporter.call_args.args[3]
            finish_cb(None, "Abbruch")  # Ergebnis-Fenster (im Test nur protokolliert), dann der Hinweis
            self.assertEqual(ask.call_count, 0)  # nie mitten im Vorgang
            self.pump(root, 0.3)
            self.assertEqual(self.warnings, ["Export abgebrochen"])
            self.assertEqual(ask.call_count, 1)
            self.assertEqual(status.call_args.args[0], "Export abgebrochen")  # Ergebnis bleibt in der Statuszeile
            finish_cb(None, "Abbruch")
            self.pump(root, 0.3)
        self.assertEqual(ask.call_count, 1)  # einmal pro Sitzung

    def test_no_popup_when_memory_is_connected_or_works(self):
        root, app = self.memory_app()
        with mock.patch.object(Overlay, "ask_choice") as ask:
            with mock.patch.object(Overlay.md_memory, "is_ready", return_value=True):
                app._memory_fell_back()  # z.B. nur der Deck-Editor war zu
            self.pump(root, 0.3)
        self.assertEqual(ask.call_count, 0)
        self.assertFalse(app._memory_warned)

    def test_start_import_uses_the_configured_read_method(self):
        for method, memory in (("memory", True), ("ocr", False)):
            self.write_config(json.dumps({"IS_CALIBRATED": True, "READ_METHOD": method}))
            root, app = self.open_app()
            with mock.patch.object(Overlay, "parse_clipboard", return_value=["1"]), \
                    mock.patch.object(Overlay.resume_state, "load_progress", return_value=None), \
                    mock.patch.object(Overlay, "DeckImporterCore") as core, \
                    mock.patch.object(Overlay.threading, "Thread"):
                app.start_btn.invoke()  # Button ohne Menü: startet direkt
            self.assertEqual(core.call_args.kwargs["memory"], memory)

    def test_ask_choice_is_dark_and_keyboard_friendly(self):
        import dark_dialog
        root = tk.Tk()
        root.withdraw()
        choices = [("Fortsetzen", True, "#007acc"), ("Neu starten", False, "#ef6c00"), ("Abbrechen", None, "#444")]
        for key, expected in (("<Return>", True), ("<Escape>", None)):
            root.after(150, lambda key=key: root.winfo_children()[-1].event_generate(key))
            self.assertIs(dark_dialog.ask_choice(root, "Import fortsetzen?", "Text", choices), expected)
        # Button-Klick
        def click_second():
            win = root.winfo_children()[-1]
            self.assertEqual(win.cget("bg"), dark_dialog.BG)
            win.choice_buttons[1].invoke()
        root.after(150, click_second)
        self.assertIs(dark_dialog.ask_choice(root, "Import fortsetzen?", "Text", choices), False)
        root.destroy()

    def test_extras_scan_and_close_when_import_starts(self):
        import deck_export
        from card_db import CardMatch
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        root, app = self.open_app()
        frame = Overlay.md_layout.Frame(0, 0, 1920, 1080)
        cards = [deck_export.ExportedCard("Main", i + 1, "Bonfire", CardMatch("1", "Bonfire", True)) for i in range(40)]
        zones = [("Main", Overlay.md_layout.deck_slot_positions(40, 267, 5), 10), ("Extra", [], 10)]
        scan = deck_export.DeckScan(frame, zones, cards, {})
        with mock.patch.object(Overlay.md_layout, "md_frame", return_value=frame):
            self.open_extras(app, root, scan)
            self.assertIsNotNone(app.extras)
            self.assertIs(app._last_scan, scan)
            self.assertEqual(app.extras.analysis.main_size, 40)
            self.assertFalse(app.is_running)
            # Import startet → Extras gehen zu
            with mock.patch.object(Overlay, "parse_clipboard", return_value=None), \
                    mock.patch.object(Overlay.resume_state, "load_progress", return_value=None), \
                    mock.patch.object(Overlay, "DeckImporterCore") as core:
                app.start_import_thread()
                self.pump(root, 0.1)
            core.assert_called_once()
            self.assertIsNone(app.extras)
            self.assertIsNone(app._last_scan)  # Import verändert das Deck → Scan verfällt
            # Am Ende hat die Kontrolle das Deck gelesen → gilt als gescannt (Deck-Fenster/Export lesen nicht neu)
            finish = core.call_args.args[3]
            finish(success=True, has_errors=False, failed_cards=[], notes=[], scan=scan)
            self.pump(root, 0.1)
            self.assertIs(app._last_scan, scan)

    def test_imported_deck_lands_in_history_and_can_be_imported_again(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        root, app = self.open_app()
        self.clipboard[0] = "ydke://AAAA!!!"
        with mock.patch.object(Overlay.resume_state, "load_progress", return_value=None), \
                mock.patch.object(Overlay, "DeckImporterCore") as core:
            app.start_import_thread()
            core.return_value._card_ids = ["1", "1", "2"]
            core.return_value.deck_names = {"1": "Feuerwerk"}
            core.call_args.args[3](success=True, has_errors=False, failed_cards=["x"], notes=[])
            self.pump(root, 0.1)
        entry, = self.make_db().history()
        self.assertEqual((entry.code, entry.result, entry.main), ("ydke://AAAA!!!", "Lücken", 3))
        self.assertEqual([e.code for e in self.make_db().history("feuerwerk")], ["ydke://AAAA!!!"])
        # Abgebrochene Importe kommen nicht in den Verlauf
        with mock.patch.object(Overlay.resume_state, "load_progress", return_value=None), \
                mock.patch.object(Overlay, "DeckImporterCore") as core:
            self.clipboard[0] = "ydke://BBBB!!!"
            app.start_import_thread()
            core.return_value._card_ids = ["5"]
            core.call_args.args[3](success=False, has_errors=True, failed_cards=[], message="Abbruch")
            self.pump(root, 0.1)
        self.assertEqual(len(self.make_db().history()), 1)
        # Aus dem Verlauf (im Deck-Fenster) erneut importieren: Code in die Zwischenablage, Import startet
        self.clipboard[0] = ""
        with mock.patch.object(app, "start_import_thread") as start:
            app._import_from_history("ydke://AAAA!!!")
        start.assert_called_once()
        self.assertEqual(self.clipboard[0], "ydke://AAAA!!!")
        self.assertFalse(hasattr(app, "history_btn"))  # Verlauf sitzt im Deck-Fenster, nicht im Overlay

    def test_side_swap_runs_import_without_clearing_and_skips_history(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        root, app = self.open_app()
        self.clipboard[0] = "ydke://NICHT!!!"
        with mock.patch.object(Overlay.resume_state, "load_progress") as load, \
                mock.patch.object(Overlay, "DeckImporterCore") as core:
            app.start_side_swap(["1", "1", "2"], "Zweiter")
            load.assert_not_called()  # kein "Fortsetzen?"-Dialog
            kwargs = core.call_args.kwargs
            self.assertEqual(kwargs["card_ids"], ["1", "1", "2"])
            self.assertEqual(kwargs["resume"], {"done": {"1": 2, "2": 1}})  # alles da → Deck wird nicht geleert
            core.return_value._card_ids = ["1", "1", "2"]
            core.call_args.args[3](success=True, has_errors=False, failed_cards=[], notes=[])
            self.pump(root, 0.1)
        self.assertEqual(app.status_label.cget("text"), "Getauscht: Zweiter")
        self.assertEqual(self.make_db().history(), [])  # Varianten nicht in den Verlauf
        self.assertIsNone(app._swap_label)
        # Danach wieder ein normaler Import
        with mock.patch.object(Overlay.resume_state, "load_progress", return_value=None), \
                mock.patch.object(Overlay, "DeckImporterCore") as core:
            app.start_import_thread()
            self.assertIsNone(core.call_args.kwargs["card_ids"])

    def test_export_reuses_scan_only_if_deck_unchanged(self):
        import deck_export
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        root, app = self.open_app()
        scan = deck_export.DeckScan(Overlay.md_layout.Frame(0, 0, 1920, 1080), [], [], {})
        passed = []

        class FakeExporter:
            def __init__(self, *args, scan=None, **kwargs):
                passed.append(scan)

            def execute(self):
                pass

        for changes, expected in ((None, scan), ("2 Karte(n) anders", None)):
            app._last_scan, app.is_running = scan, False
            with mock.patch.object(Overlay, "DeckExporter", FakeExporter), \
                    mock.patch.object(Overlay, "current_changes", return_value=changes):
                app.start_export_thread()
            self.assertIs(passed[-1], expected)
        # Ohne letzten Scan wird ganz normal gelesen
        app._last_scan, app.is_running = None, False
        with mock.patch.object(Overlay, "DeckExporter", FakeExporter), \
                mock.patch.object(Overlay, "current_changes") as check:
            app.start_export_thread()
        check.assert_not_called()
        self.assertIsNone(passed[-1])

    def test_extras_button_toggles(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        root, app = self.open_app()
        with mock.patch.object(Overlay.md_layout, "md_frame", return_value=None):
            self.open_extras(app, root, None)  # Scan schlägt fehl (z.B. Deck-Editor nicht offen)
            self.assertIsNotNone(app.extras)
            self.assertIn("Scan nicht möglich", app.extras.status_label.cget("text"))
            app.extras_btn.invoke()
            self.assertIsNone(app.extras)

    def test_restart_starts_new_instance_and_closes_everything(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        root, app = self.open_app()
        app.tray = mock.Mock()
        tray = app.tray
        with mock.patch.object(Overlay.subprocess, "Popen") as popen:
            app.restart()
        popen.assert_called_once()
        self.assertEqual(popen.call_args.kwargs["env"]["MD_IMPORTER_RESTART"], "1")  # wartet auf diese Instanz
        tray.stop.assert_called_once()        # Tray-Icon weg
        self.assertFalse(app._after_ids)      # Timer gestoppt, Fenster zerstört
        self.apps.remove(app)

    def test_close_button_also_removes_tray_icon(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        _, app = self.open_app()
        app.tray = tray = mock.Mock()
        app.close()
        tray.stop.assert_called_once()
        self.apps.remove(app)

    def test_close_button_hides_in_tray_only_with_option_and_tray(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        _, app = self.open_app()
        app.tray = mock.Mock()
        app.config["CLOSE_TO_TRAY"] = True
        app._on_close_clicked()
        self.assertTrue(app._hidden_by_x)
        app.tray.stop.assert_not_called()          # Programm läuft weiter
        self.assertTrue(app.root.winfo_exists())
        # Versteckt, solange der Deck-Editor sichtbar bleibt; nach einer Pause ohne Overlay wieder da
        self.assertFalse(app._should_show(False, True))
        self.assertTrue(app._hidden_by_x)
        self.assertFalse(app._should_show(False, False))
        self.assertFalse(app._hidden_by_x)
        self.assertTrue(app._should_show(False, True))
        app.config["CLOSE_TO_TRAY"] = False        # Option aus: ✕ beendet
        app._on_close_clicked()
        self.assertEqual(app._after_ids, {})
        self.apps.remove(app)

    def test_close_button_quits_without_tray_even_with_option(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True, "CLOSE_TO_TRAY": True}))
        _, app = self.open_app()
        app.tray = None
        app._on_close_clicked()
        self.assertEqual(app._after_ids, {})
        self.apps.remove(app)

    def test_review_notice_is_checked_after_the_start_message_and_cancelled_on_close(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        root, app = self.open_app()
        with mock.patch.object(Overlay, "find_md_window", return_value=None):
            app._check_md_started()
        self.assertNotIn("review_notice", app._after_ids)     # ohne Master Duel kein Hinweis
        with mock.patch.object(Overlay, "find_md_window", return_value=42),                 mock.patch.object(Overlay, "show_toast"):
            app._md_check_at = 0.0
            app._check_md_started()
        self.assertIn("review_notice", app._after_ids)       # später, nicht beim Start (Datenbank, Startmeldung)
        self.assertGreater(Overlay.REVIEW_NOTICE_DELAY_MS, Overlay.STARTED_TOAST_MS)
        app.close()
        self.assertEqual(app._after_ids, {})
        self.apps.remove(app)

    def test_restart_command(self):
        with mock.patch.object(Overlay.sys, "argv", ["MD_Importer.pyw"]), \
                mock.patch.object(Overlay.sys, "executable", r"C:\Python\pythonw.exe"):
            cmd = Overlay.MasterDuelImporter._restart_command()
        self.assertEqual(cmd[0], r"C:\Python\pythonw.exe")
        self.assertTrue(cmd[1].endswith("MD_Importer.pyw") and os.path.isabs(cmd[1]))
        with mock.patch.object(Overlay.sys, "frozen", True, create=True), \
                mock.patch.object(Overlay.sys, "argv", [r"C:\MD\MD_Importer.exe"]), \
                mock.patch.object(Overlay.sys, "executable", r"C:\MD\MD_Importer.exe"):
            self.assertEqual(Overlay.MasterDuelImporter._restart_command(), [r"C:\MD\MD_Importer.exe"])


@unittest.skipUnless(HAS_TK, "Tk nicht verfügbar")
class DarkDialogTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        backup = mock.patch.object(Overlay.MasterDuelImporter, "_backup_database", lambda self: None)
        backup.start()
        self.addCleanup(backup.stop)  # keine Sicherung der echten Datenbank aus dem Test heraus

    def tearDown(self):
        self.root.destroy()

    def test_dark_warning_with_card_list_closes_on_ok(self):
        import dark_dialog
        cards = [f"Karte {i} (1x fehlend)" for i in range(15)]
        with mock.patch.object(dark_dialog, "apply_frame", wraps=dark_dialog.apply_frame) as frame:
            win = dark_dialog.show_message(self.root, "Deck-Audit", "Bitte prüfen:", kind="warning",
                                           items=cards, notes=["Hinweis"], wait=False)
        self.root.update()
        self.assertEqual(win.cget("bg"), dark_dialog.BG)
        frame.assert_called_once_with(win)  # Goldrand + runde Ecken wie das Overlay
        texts = [w for w in win.winfo_children()[1].winfo_children() if isinstance(w, tk.Frame)]
        list_text = texts[0].winfo_children()[0].get("1.0", "end")
        self.assertIn("• Karte 14 (1x fehlend)", list_text)
        self.assertTrue(any(isinstance(w, DarkScrollbar) for w in texts[0].winfo_children()))  # > 10 Karten
        win.ok_button.invoke()
        self.assertFalse(win.winfo_exists())

    def test_action_button_shows_feedback_and_keeps_dialog_open(self):
        import dark_dialog
        win = dark_dialog.show_message(self.root, "Deck exportiert", "Fertig", wait=False,
                                       actions=[("Download als .ydk", lambda: "Gespeichert: C:\\x.ydk")])
        win.action_buttons[0].invoke()
        self.root.update()
        self.assertEqual(win.feedback_label.cget("text"), "Gespeichert: C:\\x.ydk")
        self.assertEqual(win.ok_button.cget("text"), "Schließen")
        self.assertTrue(win.winfo_exists())
        win.destroy()

    def test_export_result_offers_download(self):
        import deck_export
        from card_db import CardMatch
        cards = [deck_export.ExportedCard("Main", 1, "Bonfire", CardMatch("1", "Bonfire", True))]
        result = deck_export.ExportResult("#main\n1\n", 1, 0, cards)
        with mock.patch.object(Overlay, "show_message") as show, \
                mock.patch.object(Overlay, "save_ydk", return_value="C:\\Downloads\\d.ydk") as save, \
                mock.patch.object(Overlay, "CONFIG_FILE", os.path.join(tempfile.mkdtemp(), "c.json")), \
                mock.patch.object(Overlay.MasterDuelImporter, "_check_window_focus", lambda self: None):
            app = Overlay.MasterDuelImporter(tk.Toplevel(self.root))
            app._on_export_finished(result, "")
            (label, download), = show.call_args.kwargs["actions"]
            self.assertEqual(label, "Download als .ydk")
            self.assertIn("d.ydk", download())
            app.close()
        save.assert_called_once_with("#main\n1\n")
        self.assertEqual(show.call_args.kwargs["kind"], "info")

    def test_memory_is_preloaded_in_background_once(self):
        with mock.patch.object(Overlay, "CONFIG_FILE", os.path.join(tempfile.mkdtemp(), "c.json")), \
                mock.patch.object(Overlay.MasterDuelImporter, "_check_window_focus", lambda self: None), \
                mock.patch.object(Overlay.md_memory, "is_ready", return_value=False), \
                mock.patch.object(Overlay.md_memory, "master_duel_pid", return_value=4711), \
                mock.patch.object(Overlay.md_memory, "preload") as preload:
            app = Overlay.MasterDuelImporter(tk.Toplevel(self.root))
            app._preload_memory()
            app._memory_thread.join(2)
            app._preload_memory()  # kurz danach: kein zweiter Versuch (erst nach MEMORY_RETRY)
            app.close()
        preload.assert_called_once()

    def test_focus_loop_logs_each_error_type_once(self):
        with mock.patch.object(Overlay, "CONFIG_FILE", os.path.join(tempfile.mkdtemp(), "c.json")), \
                mock.patch.object(Overlay.MasterDuelImporter, "_check_window_focus", lambda self: None), \
                mock.patch.object(Overlay, "ask_choice", return_value=None):
            app = Overlay.MasterDuelImporter(tk.Toplevel(self.root))
        with mock.patch.object(Overlay, "dlog") as log, \
                mock.patch.object(Overlay.win32gui, "GetForegroundWindow", side_effect=OSError("weg")):
            for _ in range(3):
                _REAL_FOCUS_LOOP(app)
            self.assertEqual(log.call_count, 1)
            self.assertIn("OSError", log.call_args.args[0])
            with mock.patch.object(Overlay.win32gui, "GetForegroundWindow", side_effect=ValueError("anders")):
                _REAL_FOCUS_LOOP(app)
            self.assertEqual(log.call_count, 2)                    # anderer Fehlertyp: noch einmal
        app.close()

    def test_auto_calibration_error_unlocks_buttons_and_opens_wizard(self):
        with mock.patch.object(Overlay, "CONFIG_FILE", os.path.join(tempfile.mkdtemp(), "c.json")), \
                mock.patch.object(Overlay.MasterDuelImporter, "_check_window_focus", lambda self: None), \
                mock.patch.object(Overlay, "show_message"), \
                mock.patch.object(Overlay, "ask_choice", return_value=None), \
                mock.patch.object(Overlay, "auto_calibrate", side_effect=OSError("kein Bild")), \
                mock.patch.object(Overlay.MasterDuelImporter, "open_calibration") as wizard:
            app = Overlay.MasterDuelImporter(tk.Toplevel(self.root))
            app.start_auto_calibration()
            self.assertEqual(str(app.start_btn.cget("state")), "disabled")
            end = time.time() + 3
            while not wizard.called and time.time() < end:
                self.root.update()
                time.sleep(0.01)
            app.close()
        wizard.assert_called_once()

    def test_extras_scan_survives_failing_stats_lookup(self):
        from types import SimpleNamespace

        class FakeExporter:
            memory_fallback = False

            def __init__(self, config, tesseract, status, finish, **kwargs):
                self.finish = finish

            def execute_scan(self):
                card = SimpleNamespace(match=SimpleNamespace(cid="1"))
                self.finish(scan=SimpleNamespace(cards=[card]), error="")

        with mock.patch.object(Overlay, "CONFIG_FILE", os.path.join(tempfile.mkdtemp(), "c.json")), \
                mock.patch.object(Overlay.MasterDuelImporter, "_check_window_focus", lambda self: None), \
                mock.patch.object(Overlay, "ask_choice", return_value=None), \
                mock.patch.object(Overlay, "DeckExporter", FakeExporter):
            app = Overlay.MasterDuelImporter(tk.Toplevel(self.root))
            app.extras = mock.Mock()
            app.card_stats = mock.Mock()
            app.card_stats.ensure.side_effect = OSError("gesperrt")
            app._start_extras_scan()
            end = time.time() + 3
            while app.is_running and time.time() < end:
                self.root.update()
                time.sleep(0.01)
            running = app.is_running
            scan_done = app.extras.set_scan.call_args
            app.extras = None
            app.close()
        self.assertFalse(running)
        self.assertEqual(scan_done.args[1], 1)  # eine Karte ohne Stats

    def test_scan_status_is_shown_in_one_place_only(self):
        with mock.patch.object(Overlay, "CONFIG_FILE", os.path.join(tempfile.mkdtemp(), "c.json")), \
                mock.patch.object(Overlay.MasterDuelImporter, "_check_window_focus", lambda self: None), \
                mock.patch.object(Overlay, "ask_choice", return_value=None):
            app = Overlay.MasterDuelImporter(tk.Toplevel(self.root))
            before = app.status_label.cget("text")
            app.extras = mock.Mock()
            app._extras_status("Deck wird gescannt …", Overlay.INFO)
            app._on_extras_scan_done(mock.Mock(), "", 0)
            deck_window_status = app.extras.set_status.call_args.args[0]
            leiste = app.status_label.cget("text")
            app.extras = None
            app._extras_status("Scan läuft", Overlay.INFO)  # Deck-Fenster zu: die Leiste zeigt es
            fallback = app.status_label.cget("text")
            app.close()
        self.assertEqual((deck_window_status, leiste, fallback), ("Deck wird gescannt …", before, "Scan läuft"))

    def test_uncalibrated_start_tries_automatic_first(self):
        with mock.patch.object(Overlay, "CONFIG_FILE", os.path.join(tempfile.mkdtemp(), "c.json")), \
                mock.patch.object(Overlay.MasterDuelImporter, "_check_window_focus", lambda self: None), \
                mock.patch.object(Overlay.MasterDuelImporter, "start_auto_calibration") as auto:
            app = Overlay.MasterDuelImporter(tk.Toplevel(self.root))
            app.start_import_thread()
            app.close()
        auto.assert_called_once()

    def test_import_error_uses_dark_dialog(self):
        with mock.patch.object(Overlay, "show_message") as show, \
                mock.patch.object(Overlay, "CONFIG_FILE", os.path.join(tempfile.mkdtemp(), "c.json")), \
                mock.patch.object(Overlay.MasterDuelImporter, "_check_window_focus", lambda self: None):
            app = Overlay.MasterDuelImporter(tk.Toplevel(self.root))
            app._on_import_finished(False, True, [], message="Master Duel wurde nicht gefunden.")
            app.close()
        show.assert_called_once()
        self.assertEqual(show.call_args.kwargs["kind"], "error")


@unittest.skipUnless(HAS_TK, "Tk nicht verfügbar")
class WindowFrameTest(unittest.TestCase):
    def test_fallback_border_when_windows_cannot_round(self):
        # Windows 10: DWM kennt die Attribute nicht → Tk zeichnet einen Goldrand
        import window_style
        root = tk.Tk()
        try:
            with mock.patch.object(window_style.win_api, "set_dwm_attribute", return_value=False):
                self.assertFalse(window_style.apply_frame(root))
            self.assertEqual(int(root.cget("highlightthickness")), 1)
            self.assertEqual(root.cget("highlightbackground"), window_style.GOLD)
        finally:
            root.destroy()

    def test_colorref(self):
        import window_style
        self.assertEqual(window_style._colorref("#c9a02f"), 0x002FA0C9)

    def test_clicks_do_not_take_focus_from_the_game_but_typing_can(self):
        # Sonst wird beim Klick der Importer aktiv und Windows blendet über dem Vollbild-Spiel die Taskleiste ein
        import window_style
        import win_api
        root = tk.Tk()
        try:
            entry = tk.Entry(root)
            entry.pack()
            window_style.no_activate(root)
            hwnd = int(root.wm_frame(), 16)
            style = lambda: win_api._GetWindowLong(hwnd, win_api.GWL_EXSTYLE) & win_api.WS_EX_NOACTIVATE  # noqa
            self.assertTrue(style())
            window_style.allow_typing(entry)
            root.update()
            entry.event_generate("<Button-1>")   # ins Feld klicken → darf aktiv werden (Tastatur)
            self.assertFalse(style())
            entry.event_generate("<FocusOut>")   # Feld verlassen → wieder ohne Fokus
            self.assertTrue(style())
        finally:
            root.destroy()

    def test_menu_closes_on_click_outside_but_not_inside(self):
        import window_style
        root = tk.Tk()
        try:
            root.geometry("200x100+300+300")
            anchor = tk.Label(root, text="Optionen")
            anchor.place(x=0, y=0, width=60, height=20)
            root.update()
            closed = []
            mouse = {"down": True, "pos": (305, 305)}  # Klick zum Öffnen ist noch gedrückt
            with mock.patch.object(window_style.win_api, "mouse_button_down", lambda: mouse["down"]), \
                    mock.patch.object(window_style.win_api, "get_cursor_pos", lambda: mouse["pos"]):
                watcher = window_style.ClickOutside(root, anchor, lambda: closed.append(True), lambda: not closed)
                watcher._check()                                   # Öffnen-Klick zählt nicht
                mouse.update(down=False); watcher._check()         # noqa: E702
                mouse.update(down=True, pos=(350, 350)); watcher._check()  # noqa: E702 – Klick ins Menü
                self.assertEqual(closed, [])
                mouse.update(down=False); watcher._check()         # noqa: E702
                mouse.update(down=True, pos=(900, 900)); watcher._check()  # noqa: E702 – Klick daneben
                self.assertEqual(closed, [True])
        finally:
            root.destroy()


    def test_menu_also_closes_on_escape(self):
        import window_style
        root = tk.Tk()
        try:
            closed = []
            with mock.patch.object(window_style.win_api, "mouse_button_down", lambda: False), \
                    mock.patch.object(window_style.win_api, "escape_down", lambda: True):
                window_style.ClickOutside(root, root, lambda: closed.append(True), lambda: not closed)._check()
            self.assertEqual(closed, [True])
        finally:
            root.destroy()


MONITORS = [(0, 0, 1920, 1040), (1920, 0, 3840, 1080)]  # Hauptmonitor mit Taskleiste, Monitor 2 rechts daneben


def work_area_of(x, y):
    return next((m for m in MONITORS if m[0] <= x < m[2] and m[1] <= y < m[3]), MONITORS[0])


class WorkAreaTest(unittest.TestCase):
    def test_real_monitor_has_a_work_area(self):
        import win_api
        left, top, right, bottom = win_api.monitor_work_area(0, 0)
        self.assertTrue(right > left and bottom > top)

    def test_window_next_to_the_anchor_stays_on_monitor_2(self):
        import window_style
        anchor = mock.Mock()
        anchor.winfo_toplevel.return_value = mock.Mock(winfo_rootx=lambda: 2400, winfo_rooty=lambda: 180,
                                                       winfo_width=lambda: 540, winfo_height=lambda: 810)
        with mock.patch.object(window_style.win_api, "monitor_work_area", side_effect=work_area_of):
            self.assertEqual(window_style.place_in_work_area(2452, 200, 480, 460, anchor), (2452, 200))
            # am rechten Rand von Monitor 2: an dessen Rand geklemmt, nicht an den Hauptmonitor
            self.assertEqual(window_style.place_in_work_area(3700, 900, 480, 460, anchor), (3360, 620))

    def test_monitor_left_of_the_main_monitor_with_negative_coordinates(self):
        import window_style
        left_monitor = (-1920, 0, 0, 1080)
        with mock.patch.object(window_style.win_api, "monitor_work_area", return_value=left_monitor):
            self.assertEqual(window_style.place_in_work_area(-1000, -50, 520, 660), (-1000, 0))
            self.assertEqual(window_style.place_in_work_area(-100, 700, 520, 660), (-520, 420))


@unittest.skipUnless(HAS_TK, "Tk nicht verfügbar")
class RoundedButtonTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.configure(bg="#1e1e1e")

    def tearDown(self):
        self.root.destroy()

    def test_behaves_like_a_button(self):
        from rounded_button import RoundedButton
        clicks = []
        button = RoundedButton(self.root, text="Start", command=lambda: clicks.append(1))
        button.invoke()
        button.config(state=tk.DISABLED)
        button.invoke()  # gesperrt → nichts
        self.assertEqual(clicks, [1])
        self.assertEqual(str(button.cget("state")), "disabled")

    def test_text_change_resizes_and_keeps_min_width(self):
        from rounded_button import RoundedButton
        button = RoundedButton(self.root, text="Tempo: Normal ▾", min_width=200)
        self.assertEqual(int(button.cget("width")), 200)
        button.config(text="Ein deutlich längerer Button-Text als vorher")
        self.assertGreater(int(button.cget("width")), 200)
        self.assertEqual(button.cget("text"), "Ein deutlich längerer Button-Text als vorher")

    def test_equal_buttons_share_one_image(self):
        from rounded_button import RoundedButton
        first, second = RoundedButton(self.root, text="Start"), RoundedButton(self.root, text="Start")
        other = RoundedButton(self.root, text="Start", bg="#cc0000")
        self.assertEqual(str(first.cget("image")), str(second.cget("image")))
        self.assertNotEqual(str(first.cget("image")), str(other.cget("image")))

    def test_roles_set_fill_and_text_color_and_stay_readable(self):
        from rounded_button import RoundedButton, _shade
        from window_style import BG, GOLD, PANEL, ROLES

        def contrast(a, b):
            def lum(h):
                c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
                c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
                return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
            hi, lo = sorted((lum(a), lum(b)), reverse=True)
            return (hi + 0.05) / (lo + 0.05)

        for role, (fill, fg, border) in ROLES.items():
            fg = "#ffffff" if fg == "white" else fg
            self.assertEqual(border == GOLD, role == "active", role)  # nur der aktive Knopf hat Goldrand
            self.assertGreaterEqual(contrast(border, PANEL), 3.0, role)  # Rand gut sichtbar (WCAG 3:1)
            self.assertGreaterEqual(contrast(border, BG), 3.0, role)
            button = RoundedButton(self.root, text="X", role=role)
            self.assertEqual(button.cget("bg"), fill)
            self.assertGreaterEqual(contrast(fill, fg), 4.5, role)
            self.assertGreaterEqual(contrast(_shade(fill, 1.06), fg), 4.5, role)        # Hover
            self.assertGreaterEqual(contrast(_shade(fill, 0.55), "#a8a8a8"), 4.5, role)  # gesperrt
        button.config(role="danger")
        self.assertEqual(button.cget("bg"), ROLES["danger"][0])

    def test_images_in_use_survive_clearing_the_shared_cache(self):
        import rounded_button
        from rounded_button import RoundedButton
        with mock.patch.object(rounded_button, "SHARED_MAX", 1):
            first = RoundedButton(self.root, text="Eins")
            RoundedButton(self.root, text="Zwei, deutlich länger")  # leert den gemeinsamen Speicher
        self.assertEqual(self.root.tk.call("image", "type", str(first.cget("image"))), "photo")

    def test_body_has_gold_border_and_parent_background_in_corner(self):
        from rounded_button import render_body
        from window_style import GOLD
        body = render_body(100, 30, 7, "#007acc", GOLD, "#1e1e1e")
        self.assertEqual(body.getpixel((0, 0)), (0x1e, 0x1e, 0x1e))   # Ecke außen: Fensterhintergrund
        top = body.getpixel((50, 0))                                    # oben Mitte: Goldrand (geglättet)
        self.assertTrue(all(abs(a - b) <= 25 for a, b in zip(top, (0xc9, 0xa0, 0x2f))), top)
        self.assertEqual(body.getpixel((50, 15)), (0x00, 0x7a, 0xcc)) # innen: Button-Farbe


@unittest.skipUnless(HAS_TK, "Tk nicht verfügbar")
class DarkMenuTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.configure(bg="#1e1e1e")

    def tearDown(self):
        self.root.destroy()

    def test_opens_above_button_with_gold_frame_and_selects(self):
        from dark_menu import DarkMenu
        from rounded_button import RoundedButton
        import dark_menu
        var = tk.StringVar(value="normal")
        chosen = []
        menu = DarkMenu(self.root)
        for key in ("fast", "normal", "slow"):
            menu.add_radiobutton(label=key, value=key, variable=var, command=lambda: chosen.append(var.get()))
        button = RoundedButton(self.root, text="Tempo ▾", menu=menu)
        button.pack(pady=(200, 0))
        self.root.update()
        with mock.patch.object(dark_menu, "apply_frame") as frame:
            button.invoke()
        self.root.update()
        self.assertTrue(menu.is_open)
        frame.assert_called_once_with(menu.window)
        self.assertLessEqual(menu.window.winfo_rooty() + menu.window.winfo_height(), button.winfo_rooty())
        menu.invoke(2)
        self.assertFalse(menu.is_open)
        self.assertEqual((var.get(), chosen), ("slow", ["slow"]))

    def test_second_click_closes(self):
        from dark_menu import DarkMenu
        menu = DarkMenu(self.root)
        menu.add_command(label="Automatisch", command=lambda: None)
        button = tk.Label(self.root, text="x")
        button.pack()
        self.root.update()
        menu.popup_above(button)
        menu.popup_above(button)
        self.assertFalse(menu.is_open)


    def test_popup_at_puts_bottom_right_corner_at_point_inside_work_area(self):
        from dark_menu import DarkMenu
        import dark_menu
        menu = DarkMenu(self.root)
        menu.add_command(label="Beenden", command=lambda: None)

        def corner(x, y):
            menu.popup_at(x, y)
            self.root.update()
            win = menu.window
            return win.winfo_rootx() + win.winfo_width(), win.winfo_rooty() + win.winfo_height()
        with mock.patch.object(dark_menu.win_api, "work_area", lambda: (0, 0, 800, 600)):
            self.assertEqual(corner(500, 400), (500, 400))
            self.assertEqual(corner(5000, 5000), (800, 600))   # Mauszeiger auf der Taskleiste → darüber
            corner(5, 5)                                  # links oben abgeschnitten → nach rechts unten
            self.assertEqual((menu.window.winfo_rootx(), menu.window.winfo_rooty()), (0, 0))
        menu.close()
        self.assertFalse(menu.is_open)

    def test_label_is_not_clickable_and_rows_grow_with_row_pady(self):
        from dark_menu import DarkMenu
        clicked, heights = [], []
        for pady in (3, 8):
            menu = DarkMenu(self.root, row_pady=pady)
            menu.add_label("Titel")
            menu.add_separator()
            menu.add_command(label="Neustart", command=lambda: clicked.append(True))
            menu.popup_at(500, 500)
            self.root.update()
            rows = menu.window.winfo_children()[0].winfo_children()
            rows[0].event_generate("<ButtonRelease-1>")   # Titel: keine Reaktion
            self.assertTrue(menu.is_open)
            heights.append(menu.window.winfo_height())
            rows[2].event_generate("<ButtonRelease-1>")   # Neustart
            self.assertFalse(menu.is_open)
        self.assertEqual(len(clicked), 2)
        self.assertGreater(heights[1], heights[0] * 1.3)

    def test_tray_menu_content_and_actions(self):
        import tray
        calls = []
        icon = tray.TrayIcon.__new__(tray.TrayIcon)
        icon._icon, icon._menu, icon._title, icon._update_version = None, None, "MD Importer 9.5.2", None
        icon._on_restart, icon._on_quit = lambda: calls.append("restart"), lambda: calls.append("quit")
        icon._on_update, icon._on_report = lambda: calls.append("update"), lambda: calls.append("report")
        state = {"on": True}
        with mock.patch("autostart.is_enabled", lambda: state["on"]), \
                mock.patch("autostart.set_enabled", lambda on: state.update(on=on)):
            icon.show_menu(self.root, 500, 500)
            self.assertEqual([i.get("label") for i in icon._menu.items],
                             ["MD Importer 9.5.2", None, "Fehlerbericht erstellen", "Mit Windows starten",
                              "Neustart", "Beenden"])
            self.assertTrue(icon._menu.items[0]["disabled"])
            self.assertTrue(icon._menu.items[3]["variable"].get())  # Haken = Registry
            icon._menu.invoke(3)
            self.assertFalse(state["on"])
            icon.set_update("9.6")
            icon.show_menu(self.root, 500, 500)   # ersetzt das offene Menü
            self.assertFalse(icon._menu.items[4]["variable"].get())
            self.assertEqual(icon._menu.items[1]["label"], "Update auf 9.6 installieren")
            icon._menu.invoke(1)
            icon.show_menu(self.root, 500, 500)
            icon._menu.invoke(len(icon._menu.items) - 1)
        self.assertEqual(calls, ["update", "quit"])
        self.assertEqual(icon._menu.items[-1]["hover"], tray.QUIT_HOVER)
        icon.stop()


class AppIconTest(unittest.TestCase):
    def test_icon_sizes(self):
        from app_icon import create_icon_image
        for size in (16, 32, 64, 256):
            image = create_icon_image(size)
            self.assertEqual((image.size, image.mode), ((size, size), "RGBA"))
            self.assertIsNotNone(image.getbbox())  # nicht leer


class MatchStatusTest(unittest.TestCase):
    """Meldung nach einem erfassten Duell: kurz in der schmalen Statuszeile, ausführlich im Tray."""

    def notify(self, *matches, day="", reminder=""):
        from types import SimpleNamespace
        shown, tray, self.later = [], [], []
        fake = SimpleNamespace(update_status=lambda text, color: shown.append(text), extras=None,
                               tray=SimpleNamespace(notify=lambda text, title: tray.append(text)),
                               _day_summary=lambda: day, _history_reminder=lambda: reminder, _after_ids={},
                               _unconfirmed_matches=lambda: self.unconfirmed,
                               status_label=SimpleNamespace(cget=lambda option: shown[-1]),
                               root=SimpleNamespace(after=lambda ms, func: self.later.append(func) or "id"))
        for name in ("_show_history_reminder", "_check_history_reminder"):
            setattr(fake, name, getattr(Overlay.MasterDuelImporter, name).__get__(fake))
        self.unconfirmed = 15
        Overlay.MasterDuelImporter._on_new_matches(fake, list(matches))
        self.shown, self.tray = shown, tray
        return shown[0], tray[0]

    def test_reminder_to_open_match_history(self):
        import match_history as mh
        match = mh.MatchRecord("1", 0, mh.RANKED, mh.WIN, True, 0, 0, [], [], [], [], "", "live", True)
        self.notify(match)
        self.assertEqual((len(self.tray), self.later), (1, []))  # nicht nötig → keine Erinnerung
        self.notify(match, reminder=mh.history_reminder(15))
        self.assertIn("Match History", self.tray[1])
        self.later.pop(0)()  # nach ein paar Sekunden in der Statuszeile
        self.assertEqual(self.shown[-1], Overlay.SHORT_REMINDER)
        self.later.pop(0)()  # Match History noch nicht geöffnet → Hinweis bleibt
        self.assertEqual(self.shown[-1], Overlay.SHORT_REMINDER)
        self.unconfirmed = 0  # jetzt geöffnet → Hinweis verschwindet beim nächsten Blick
        self.later.pop(0)()
        self.assertEqual(self.shown[-1], "Match History übernommen")
        self.assertEqual(self.later, [])

    def test_no_reminder_if_match_history_was_opened_in_the_meantime(self):
        import match_history as mh
        match = mh.MatchRecord("1", 0, mh.RANKED, mh.WIN, True, 0, 0, [], [], [], [], "", "live", True)
        self.notify(match, reminder=mh.history_reminder(15))
        self.unconfirmed = 0  # in den Sekunden bis zum Hinweis geöffnet
        self.later.pop(0)()
        self.assertNotIn(Overlay.SHORT_REMINDER, self.shown)

    def test_short_status_and_full_tray_text(self):
        import match_history as mh
        match = mh.MatchRecord("1", 0, mh.RANKED, mh.WIN, False, 0, 0, [], [], [], [], "Zwölfnote", "live", False)
        status, tray = self.notify(match)
        self.assertEqual(status, "Sieg · Zweiter · Münze verloren · Zwölfnote")
        self.assertTrue(tray.startswith("Sieg als Zweiter (Münzwurf verloren) mit Zwölfnote erfasst"))
        long_name = match._replace(md_deck="Ryzeal Mitsurugi Fiendsmith", result=mh.LOSS, first=None, coin=None)
        self.assertEqual(self.notify(long_name)[0], "Niederlage · Ryzeal Mitsur…")
        self.assertEqual(self.notify(match, long_name)[0], "2 Matches erfasst")

    def test_day_summary_replaces_deck_name_in_status(self):
        import match_history as mh
        match = mh.MatchRecord("1", 0, mh.RANKED, mh.LOSS, False, 0, 0, [], [], [], [], "Zwölfnote", "live", False)
        status, tray = self.notify(match, day="Heute 12–10")
        self.assertEqual(status, "Niederlage · Zweiter · Münze verloren · Heute 12–10")
        self.assertIn("mit Zwölfnote", tray)  # Deckname dann nur in der Tray-Meldung
        self.assertEqual(self.notify(match, match, day="Heute 3–1")[0], "2 Matches erfasst · Heute 3–1")


class MemoryRetryBackoffTest(unittest.TestCase):
    """Schlägt das Vorladen fehl, wird die Wartezeit je Fehlversuch verdoppelt (30 s … 10 min)."""

    def attempts(self, failures, pid=4711, app=None):
        from types import SimpleNamespace
        app = app or SimpleNamespace(_memory_thread=None, _memory_preload_at=time.monotonic(), _memory_warned=True,
                                     _memory_pid=pid)
        started = []
        with mock.patch.object(Overlay.md_memory, "is_ready", return_value=False), \
                mock.patch.object(Overlay.md_memory, "master_duel_pid", return_value=pid), \
                mock.patch.object(Overlay.md_memory, "preload_failures", return_value=failures), \
                mock.patch.object(Overlay.md_memory, "reset_preload_failures") as reset, \
                mock.patch.object(Overlay.md_memory, "preload", lambda: started.append(1)):
            for waited in (0, 29, 31, 119, 121, 590, 700):
                app._memory_preload_at = time.monotonic() - waited
                app._memory_thread = None
                Overlay.MasterDuelImporter._preload_memory(app)
                if app._memory_thread is not None:
                    app._memory_thread.join()
                    yield waited
        self.reset = reset

    def test_wait_doubles_with_every_failure_up_to_ten_minutes(self):
        self.assertEqual(list(self.attempts(1)), [31, 119, 121, 590, 700])  # 30 s
        self.assertEqual(list(self.attempts(2)), [119, 121, 590, 700])      # 60 s
        self.assertEqual(list(self.attempts(3)), [121, 590, 700])           # 120 s
        self.assertEqual(list(self.attempts(9)), [700])                     # gedeckelt: 600 s
        self.assertEqual(list(self.attempts(40)), [700])
        self.assertEqual(list(self.attempts(0)), [31, 119, 121, 590, 700])

    def test_new_game_process_starts_over(self):
        from types import SimpleNamespace
        app = SimpleNamespace(_memory_thread=None, _memory_preload_at=time.monotonic(), _memory_warned=True,
                              _memory_pid=100)
        self.assertEqual(list(self.attempts(9, pid=200, app=app)), [0, 700])  # sofort, trotz 9 Fehlversuchen
        self.reset.assert_called_once_with()


class MemoryWarningTest(unittest.TestCase):
    """Verbindet sich der Speicher-Modus minutenlang nicht (Spiel-Update), einmal Bescheid geben."""

    def preload(self, app, failures):
        with mock.patch.object(Overlay.md_memory, "is_ready", return_value=False), \
                mock.patch.object(Overlay.md_memory, "master_duel_pid", return_value=0), \
                mock.patch.object(Overlay.md_memory, "preload_failures", return_value=failures), \
                mock.patch.object(Overlay.md_memory, "preload", lambda: None):
            app._memory_preload_at = -1000.0
            Overlay.MasterDuelImporter._preload_memory(app)
            app._memory_thread.join()

    def make_app(self):
        from types import SimpleNamespace
        app = SimpleNamespace(_memory_thread=None, _memory_preload_at=0.0, _memory_warned=False, _memory_pid=0,
                              shown=[], tray=[])
        app._offer_ocr_when_idle = None
        app._run_on_ui = lambda func: app.shown.append("popup")
        app.update_status = lambda text, color: app.shown.append(text)
        app.tray = SimpleNamespace(notify=lambda text, title: app.shown.append("tray: " + text))
        app._warn_memory_unavailable = lambda: Overlay.MasterDuelImporter._warn_memory_unavailable(app)
        return app

    def test_warns_once_after_repeated_failures(self):
        app = self.make_app()
        self.preload(app, Overlay.MEMORY_WARN_AFTER - 1)
        self.assertEqual(app.shown, [])  # Spiel lädt evtl. noch
        self.preload(app, Overlay.MEMORY_WARN_AFTER)
        self.preload(app, Overlay.MEMORY_WARN_AFTER + 1)
        self.assertEqual(app.shown[0], "Speicher-Modus nicht verfügbar")
        self.assertEqual(len(app.shown), 3)  # Statuszeile + Tray + Popup (über die UI-Warteschlange), nur einmal
        self.assertIn("Winrate", app.shown[1])
        self.assertEqual(app.shown[2], "popup")


class MemoryChangedWarningTest(unittest.TestCase):
    """Fall (a): fehlende Namen nach einem Spiel-Update – einmal pro Spiel-Prozess melden, nicht weiter scannen."""

    def test_warns_once_per_process_and_does_not_scan(self):
        from types import SimpleNamespace
        app = SimpleNamespace(_memory_thread=None, _memory_preload_at=0.0, _memory_warned=False, _memory_pid=0,
                              _memory_structure_pid=0, warned=[])
        app._warn_memory_changed = app.warned.append
        pids = iter([100, 100, 200])
        with mock.patch.object(Overlay.md_memory, "is_ready", return_value=False), \
                mock.patch.object(Overlay.md_memory, "master_duel_pid", side_effect=lambda: next(pids)), \
                mock.patch.object(Overlay.md_memory, "structure_missing", return_value=["Foo"]), \
                mock.patch.object(Overlay.md_memory, "reset_preload_failures"), \
                mock.patch.object(Overlay.md_memory, "preload") as preload:
            for _ in range(3):
                Overlay.MasterDuelImporter._preload_memory(app)
        self.assertEqual(app.warned, [["Foo"], ["Foo"]])  # Prozess 100 einmal, neuer Prozess 200 wieder
        preload.assert_not_called()
        self.assertFalse(app._memory_warned)  # der allgemeine Hinweis (Fall b) bleibt unberührt


class StartNoticesTest(unittest.TestCase):
    """Startmeldung und Rückblick-Hinweis kommen erst, wenn Master Duel läuft – einmal pro Programmlauf."""

    def check(self, app, hwnd, advance=0.0):
        with mock.patch.object(Overlay, "find_md_window", return_value=hwnd), \
                mock.patch.object(Overlay.time, "monotonic", return_value=1000.0 + advance):
            Overlay.MasterDuelImporter._check_md_started(app)

    def make_app(self):
        from types import SimpleNamespace
        app = SimpleNamespace(_start_notices_pending=True, _md_check_at=0.0, toasts=[], _after_ids={},
                              root=SimpleNamespace(after=lambda ms, func: ("review", ms)))
        app._queue_toast = app.toasts.append
        app._start_review_notice = None
        return app

    def test_no_notice_while_md_is_not_running(self):
        app = self.make_app()
        self.check(app, None)
        self.check(app, None, advance=Overlay.MD_START_CHECK_S)
        self.assertEqual((app.toasts, app._after_ids), ([], {}))

    def test_exactly_one_notice_when_md_appears(self):
        app = self.make_app()
        self.check(app, None)
        self.check(app, 42, advance=Overlay.MD_START_CHECK_S)
        self.check(app, 42, advance=2 * Overlay.MD_START_CHECK_S)
        self.assertEqual(app.toasts, [Overlay.started_text()])
        self.assertEqual(app._after_ids, {"review_notice": ("review", Overlay.REVIEW_NOTICE_DELAY_MS)})

    def test_md_already_running_shows_notice_at_once(self):
        app = self.make_app()
        self.check(app, 42)
        self.assertEqual(app.toasts, [Overlay.started_text()])


if __name__ == "__main__":
    unittest.main()
