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
            # Abfrage der Lesemethode beim ersten Start: im Test nichts gewählt (eigener Test unten)
            mock.patch.object(Overlay, "ask_choice", return_value=None),
            # Nie die echte Zwischenablage oder die echte Datenbank (Verlauf) des Spielers anfassen
            mock.patch.object(Overlay.pyperclip, "paste", lambda: self.clipboard[0]),
            mock.patch.object(Overlay.pyperclip, "copy", lambda text: self.clipboard.__setitem__(0, text)),
            mock.patch.object(Overlay, "CardStatsDB", lambda: self.make_db()),
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

    def test_corrupt_config_is_not_overwritten(self):
        self.write_config('{"FIRST_CARD": [1, 2], "SPEED_PROFILE": "slow",}')  # Komma zu viel
        self.open_app()
        with open(self.config_file, encoding="utf-8") as f:
            self.assertIn('"SPEED_PROFILE": "slow",}', f.read())
        self.assertTrue(os.path.exists(self.config_file + ".defekt.bak"))
        self.assertEqual(len(self.warnings), 1)

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
        self.assertEqual(app.timer_label.cget("text"), "⏱ 0:01")
        self.assertEqual(str(app.speed_btn.cget("state")), "normal")

    def test_end_time_is_cleared_after_leaving_the_editor(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        root, app = self.open_app()
        app.is_running = True
        app._on_import_started()
        app._on_import_finished(True, False, [])
        self.assertTrue(app.timer_label.cget("text").startswith("⏱"))
        app.editor_watch.detector = mock.Mock(visible=True)   # noch im Editor: Endzeit bleibt
        app._clear_timer_after_editor()
        self.assertTrue(app.timer_label.cget("text").startswith("⏱"))
        app.editor_watch.detector.visible = False  # Editor verlassen
        app._clear_timer_after_editor()
        self.assertEqual(app.timer_label.cget("text"), "")

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
        with mock.patch.object(Overlay, "DeckExporter", FakeExporter),                 mock.patch("extras_panel.DeckWatcher.start", lambda self: None):
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
            with mock.patch.object(Overlay, "parse_clipboard", return_value=None),                     mock.patch.object(Overlay.resume_state, "load_progress", return_value=None),                     mock.patch.object(Overlay, "DeckImporterCore") as core:
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
        self.assertTrue(any(isinstance(w, tk.Scrollbar) for w in texts[0].winfo_children()))  # > 10 Karten
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
                mock.patch.object(Overlay.md_memory, "preload") as preload:
            app = Overlay.MasterDuelImporter(tk.Toplevel(self.root))
            app._preload_memory()
            app._memory_thread.join(2)
            app._preload_memory()  # kurz danach: kein zweiter Versuch (erst nach MEMORY_RETRY)
            app.close()
        preload.assert_called_once()

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


class AppIconTest(unittest.TestCase):
    def test_icon_sizes(self):
        from app_icon import create_icon_image
        for size in (16, 32, 64, 256):
            image = create_icon_image(size)
            self.assertEqual((image.size, image.mode), ((size, size), "RGBA"))
            self.assertIsNotNone(image.getbbox())  # nicht leer


class MatchStatusTest(unittest.TestCase):
    """Meldung nach einem erfassten Duell: kurz in der schmalen Statuszeile, ausführlich im Tray."""

    def notify(self, *matches, day=""):
        from types import SimpleNamespace
        shown, tray = [], []
        fake = SimpleNamespace(update_status=lambda text, color: shown.append(text), extras=None,
                               tray=SimpleNamespace(notify=lambda text, title: tray.append(text)),
                               _day_summary=lambda: day)
        Overlay.MasterDuelImporter._on_new_matches(fake, list(matches))
        return shown[0], tray[0]

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


if __name__ == "__main__":
    unittest.main()
