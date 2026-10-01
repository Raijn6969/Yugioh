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


if __name__ == "__main__":
    unittest.main()
