import ctypes
import ctypes.wintypes
import tkinter as tk
from tkinter import messagebox
import pyautogui
import pyperclip
import time
import threading
import os
import sys
import json

try:
    # Zwingt Windows zu echten Hardware-Pixeln. Macht manuelles DPI-Scaling überflüssig!
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

from utils import clean_text, sanitize_name, parse_clipboard
from calibration import CalibrationWizard
from window_automation import WindowAutomator

# IMPORT DER AUSGELAGERTEN ENGINE
from import_engine import DeckImporterCore


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


TESSERACT_CMD = resource_path(os.path.join("Tesseract-OCR", "tesseract.exe"))
CONFIG_FILE = "md_config.json"


class MasterDuelImporter:
    def __init__(self, root):
        self.root = root
        self.is_running = False
        self.config = self.load_config()
        self._build_main_ui()

    def load_config(self):
        base_config = {
            "SEARCH_BAR": [1404, 245], "FIRST_CARD": [1390, 400],
            "TRASH_BTN": [1246, 127], "TRASH_CONFIRM": [1167, 665],
            "UNOWNED_BTN": [1786, 209],
            "OFFSET_X": 88, "OFFSET_Y": 135,
            "CLICK_SPEED": 0.03, "SEARCH_DELAY": 0.6,
            "LANGUAGE": "en",
            "IS_CALIBRATED": False
        }
        loaded = base_config.copy()
        needs_save = True

        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    file_data = json.load(f)
                    for key, val in file_data.items():
                        loaded[key] = val
                needs_save = any(k not in file_data for k in base_config)
            except Exception:
                pass

        self.config = loaded
        if needs_save:
            self.save_config()
        return loaded

    def save_config(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.config, f, indent=4)

    def _build_main_ui(self):
        self.root.title("MD Importer V24.00 (Modular UI)")
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        self.root.wm_attributes("-alpha", 0.95)
        self.root.configure(bg='#1e1e1e')
        self.root.geometry("280x85+1343+900")

        self.root.bind("<ButtonPress-1>", self.start_move)
        self.root.bind("<B1-Motion>", self.do_move)

        self.status_label = tk.Label(self.root, text="Bereit für Import", fg="#00ff00", bg="#1e1e1e",
                                     font=("Helvetica", 10, "bold"))
        self.status_label.pack(pady=4)

        btn_frame = tk.Frame(self.root, bg="#1e1e1e")
        btn_frame.pack()

        self.start_btn = tk.Button(btn_frame, text="Start Import", command=self.start_import_thread, bg="#007acc",
                                   fg="white", bd=0, width=12, font=("Helvetica", 10, "bold"))
        self.start_btn.pack(side=tk.LEFT, padx=5, ipady=3)

        self.calib_btn = tk.Button(btn_frame, text="Kalibrieren", command=self.open_calibration, bg="#444444",
                                   fg="white", bd=0, width=12, font=("Helvetica", 10))
        self.calib_btn.pack(side=tk.LEFT, padx=5, ipady=3)

        tk.Button(self.root, text="X", command=self.root.destroy, bg="#cc0000", fg="white", bd=0,
                  font=("Helvetica", 9, "bold")).place(x=245, y=0, width=35, height=22)

    def start_move(self, event):
        if event.widget == self.root:
            self.root.x, self.root.y = event.x, event.y
        else:
            self.root.x = None

    def do_move(self, event):
        if getattr(self.root, 'x', None) is not None:
            self.root.geometry(
                f"+{self.root.winfo_pointerx() - self.root.x}+{self.root.winfo_pointery() - self.root.y}")

    def update_status(self, text, color="white"):
        self.status_label.config(text=text, fg=color)

    def open_calibration(self):
        self.start_btn.config(state=tk.DISABLED)
        self.calib_btn.config(state=tk.DISABLED)
        CalibrationWizard(self.root, self.config, self.on_calibration_done, self.on_calibration_cancel)

    def on_calibration_cancel(self):
        self.update_status("Kalibrierung abgebrochen", "yellow")
        self.start_btn.config(state=tk.NORMAL)
        self.calib_btn.config(state=tk.NORMAL)

    def on_calibration_done(self, new_config):
        self.config = new_config
        self.config["IS_CALIBRATED"] = True
        self.save_config()
        self.update_status("Kalibrierung aktiv!", "#00ff00")
        self.start_btn.config(state=tk.NORMAL)
        self.calib_btn.config(state=tk.NORMAL)

    def start_import_thread(self):
        if not self.config.get("IS_CALIBRATED", False):
            self.update_status("Kalibrierung nötig!", "yellow")
            self.open_calibration()
            return

        if not self.is_running:
            self.is_running = True
            self.start_btn.config(state=tk.DISABLED)

            def status_cb(text, color="white"):
                self.update_status(text, color)

            # --- UPGRADE: DETAILLIERTE BENUTZERMELDUNG FÜR DECK-LÜCKEN ---
            def finish_cb(success, has_errors, failed_cards):
                self.is_running = False
                self.start_btn.config(state=tk.NORMAL)
                if success:
                    if failed_cards or has_errors:
                        self.update_status("Mit Lücken fertig!", "#ffaa00")
                        msg = "Der Import ist abgeschlossen, aber folgende Karten weisen eine Lücke auf und müssen manuell hinzugefügt werden:\n\n"
                        msg += "\n".join(f"• {card}" for card in failed_cards)
                        messagebox.showwarning("Deck-Audit - Fehlende Karten", msg)
                    else:
                        self.update_status("Import Erfolgreich!", "#00ff00")
                else:
                    self.update_status("Abbruch / Fehler", "red")

            core = DeckImporterCore(self.config, TESSERACT_CMD, status_cb, finish_cb)
            threading.Thread(target=core.execute_import, daemon=True).start()


if __name__ == "__main__":
    root = tk.Tk()
    app = MasterDuelImporter(root)
    root.mainloop()