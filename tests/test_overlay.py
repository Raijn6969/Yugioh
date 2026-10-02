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
            mock.patch.object(Overlay.messagebox, "showwarning", lambda t, m: self.warnings.append(t)),
            # Auto-Ausblenden aus: im Test ist Master Duel nicht im Vordergrund
            mock.patch.object(Overlay.MasterDuelImporter, "_check_window_focus", lambda self: None),
        ]
        for p in self.patches:
            p.start()
        self.apps = []

    def tearDown(self):
        for app in self.apps:
            app.close()
        for p in self.patches:
            p.stop()

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
        menu = root.nametowidget(app.speed_btn.cget("menu"))
        menu.invoke(2)  # Langsam
        self.assertEqual(app.speed_btn.cget("text"), "Tempo: Langsam ▾")
        with open(self.config_file, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["SPEED_PROFILE"], "slow")

    def test_window_position_remembered(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        root, app = self.open_app()
        root.geometry("+400+300")
        root.update()
        app._moved = True
        app.end_move(None)
        root2, _ = self.open_app()
        self.assertEqual((root2.winfo_x(), root2.winfo_y()), (400, 300))

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

    def test_restart_starts_new_instance_and_closes_everything(self):
        self.write_config(json.dumps({"IS_CALIBRATED": True}))
        root, app = self.open_app()
        app.tray = mock.Mock()
        tray = app.tray
        with mock.patch.object(Overlay.subprocess, "Popen") as popen:
            app.restart()
        popen.assert_called_once()
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


class AppIconTest(unittest.TestCase):
    def test_icon_sizes(self):
        from app_icon import create_icon_image
        for size in (16, 32, 64, 256):
            image = create_icon_image(size)
            self.assertEqual((image.size, image.mode), ((size, size), "RGBA"))
            self.assertIsNotNone(image.getbbox())  # nicht leer


if __name__ == "__main__":
    unittest.main()
