"""
Overlay-Fenster des Master Duel Deck Importers (Start, Kalibrierung, Tempo, Timer).
"""

import ctypes
import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox
# Nicht entfernen, obwohl hier nicht direkt benutzt: pyautogui setzt beim Import die
# DPI-Awareness des Prozesses. Die Import-Reihenfolge bestimmt also die DPI-Behandlung.
import pyautogui  # noqa: F401
import threading
import queue
import time
import os
import shutil
import subprocess
import sys
import json
import win32gui
import win32process
from PIL import ImageTk

try:
    # Zwingt Windows zu echten Hardware-Pixeln. Macht manuelles DPI-Scaling überflüssig!
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

from calibration import CalibrationWizard
from import_engine import DeckImporterCore
from app_paths import APP_DIR, APP_VERSION, CONFIG_FILE, TESSERACT_CMD
from app_icon import create_icon_image
from dark_dialog import show_message
from window_style import apply_frame
from rounded_button import RoundedButton
from tray import TrayIcon
from utils import parse_clipboard
from window_automation import get_md_window_size
import resume_state
import win_api

# Tempo-Profile für die Auswahl: (Config-Wert, Anzeige, Farbe, Menü-Eintrag)
SPEED_PROFILES = [
    ("fast", "Schnell", "#ffaa00", "Schnell  (starker PC)"),
    ("normal", "Normal", "#ffffff", "Normal"),
    ("slow", "Langsam", "#66aaff", "Langsam  (schwacher PC)"),
]


class MasterDuelImporter:
    def __init__(self, root, tray: bool = False):
        self.root = root
        self.tray = None
        self.is_running = False
        self.is_visible = True  # Status für den Smart Visibility Tracker
        self._import_t0 = None  # Startzeit des laufenden Imports (für den Timer)
        self._moved = False  # Wurde das Fenster seit dem letzten Klick verschoben?
        self._drag_offset = None  # Mausposition im Fenster beim Start des Verschiebens
        # Tkinter ist nicht thread-sicher: Der Import-Thread legt UI-Aufträge nur in diese
        # Warteschlange, der Haupt-Thread arbeitet sie regelmäßig ab.
        self._ui_queue = queue.Queue()
        # IDs der wiederkehrenden Tk-Timer, damit sie beim Schließen gestoppt werden können
        self._after_ids = {}
        self.config = self.load_config()
        self._build_main_ui()
        self._process_ui_queue()
        if tray:
            # Menü-Aktionen kommen aus dem Tray-Thread → über die UI-Warteschlange in den Tk-Thread
            self.tray = TrayIcon(f"MD Importer {APP_VERSION}",
                                 on_restart=lambda: self._run_on_ui(self.restart),
                                 on_quit=lambda: self._run_on_ui(self.close))

        # Starte den unsichtbaren Radar für den Fenster-Fokus
        self._check_window_focus()

    def load_config(self):
        base_config = {
            "SEARCH_BAR": [1404, 245], "FIRST_CARD": [1390, 400],
            "TRASH_BTN": [1246, 127], "TRASH_CONFIRM": [1167, 665],
            "UNOWNED_BTN": [1786, 209],
            "OFFSET_X": 88, "OFFSET_Y": 140,
            "CLICK_SPEED": 0.03,
            "LANGUAGE": "en",
            "IS_CALIBRATED": False,
            # Skaliert kritische Wait-Zeiten für schwächere PCs.
            # "fast" = 0.85x (high-end), "normal" = 1.0x (default), "slow" = 1.5x (alter/langsamer PC)
            "SPEED_PROFILE": "normal"
        }
        loaded = base_config.copy()
        needs_save = True

        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    file_data = json.load(f)
                    for key, val in file_data.items():
                        loaded[key] = val
                needs_save = any(k not in file_data for k in base_config)
            except Exception as e:
                # Beschädigte Config NICHT mit Defaults überschreiben (sonst ist die Kalibrierung weg).
                # Sicherungskopie anlegen, damit sie auch nach einer Neu-Kalibrierung erhalten bleibt.
                needs_save = False
                backup = CONFIG_FILE + ".defekt.bak"
                try:
                    shutil.copyfile(CONFIG_FILE, backup)
                except Exception:
                    backup = "(Sicherung fehlgeschlagen)"
                messagebox.showwarning(
                    "Konfiguration beschädigt",
                    f"{CONFIG_FILE} konnte nicht gelesen werden:\n{e}\n\n"
                    f"Die Datei wurde NICHT überschrieben. Sicherung: {backup}\n\n"
                    "Bitte den Fehler in der Datei korrigieren und neu starten "
                    "oder neu kalibrieren."
                )

        self.config = loaded
        if needs_save:
            self.save_config()
        return loaded

    def save_config(self):
        # Atomar speichern: erst Temp-Datei, dann ersetzen → Config nie halb geschrieben
        tmp_path = CONFIG_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.config, f, indent=4)
        os.replace(tmp_path, CONFIG_FILE)

    def _build_main_ui(self):
        self.root.withdraw()  # Erst nach dem Aufbau zeigen (Breite hängt vom Inhalt ab)
        self.root.title(f"MD IMPORTER {APP_VERSION}")
        # App-Icon auch für Dialoge (Fortsetzen?, Ergebnis); Referenz halten, sonst räumt Python es weg
        self._icon_photo = ImageTk.PhotoImage(create_icon_image(64), master=self.root)
        self.root.iconphoto(True, self._icon_photo)
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        self.root.wm_attributes("-alpha", 0.95)
        self.root.configure(bg='#1e1e1e')

        # --- DYNAMISCHE SKALIERUNG & 0-PIXEL POSITIONIERUNG ---
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()

        scale = max(1.0, screen_h / 1080.0)

        self.root.bind("<ButtonPress-1>", self.start_move)
        self.root.bind("<B1-Motion>", self.do_move)
        self.root.bind("<ButtonRelease-1>", self.end_move)

        font_main = ("Helvetica", int(10 * scale), "bold")
        font_sub = ("Helvetica", int(10 * scale))
        font_x = ("Helvetica", int(9 * scale), "bold")

        self.status_label = tk.Label(self.root, text="Bereit für Import", fg="#00ff00", bg="#1e1e1e",
                                     font=font_main)
        self.status_label.pack(pady=int(4 * scale))

        btn_frame = tk.Frame(self.root, bg="#1e1e1e")
        btn_frame.pack()

        btn_pack = dict(side=tk.LEFT, padx=int(4 * scale))
        btn_size = dict(padx=int(14 * scale), pady=int(5 * scale), radius=int(7 * scale))

        self.start_btn = RoundedButton(btn_frame, text="Start Import", command=self.start_import_thread,
                                       bg="#007acc", font=font_main, **btn_size)
        self.start_btn.pack(**btn_pack)

        self.calib_btn = RoundedButton(btn_frame, text="Kalibrieren", command=self.open_calibration,
                                       bg="#444444", font=font_sub, **btn_size)
        self.calib_btn.pack(**btn_pack)

        # Tempo-Auswahl ganz rechts: Button mit ▾, öffnet ein Auswahlmenü (nach oben, da das
        # Fenster am unteren Bildschirmrand sitzt). Die aktuelle Wahl ist im Menü markiert.
        # Feste Breite für den längsten Eintrag, damit der Button beim Umschalten nicht springt.
        self.speed_var = tk.StringVar(value=SPEED_PROFILES[self._current_speed_index()][0])
        longest = max(tkfont.Font(font=font_sub).measure(f"Tempo: {label} ▾") for _, label, _, _ in SPEED_PROFILES)
        self.speed_btn = RoundedButton(btn_frame, bg="#444444", font=font_sub,
                                       min_width=longest + 2 * btn_size["padx"], **btn_size)
        speed_menu = tk.Menu(self.speed_btn, tearoff=0, bg="#2b2b2b", fg="white",
                             activebackground="#007acc", activeforeground="white",
                             selectcolor="#00ff00", bd=0, font=font_sub)
        for key, _, _, menu_text in SPEED_PROFILES:
            speed_menu.add_radiobutton(label=menu_text, value=key, variable=self.speed_var,
                                       command=self._on_speed_selected)
        self.speed_btn.config(menu=speed_menu)
        self.speed_btn.pack(**btn_pack)
        self._refresh_speed_button()

        # Etwas eingerückt, damit der abgerundete Goldrand im Eck frei bleibt
        RoundedButton(self.root, text="✕", command=self.close, bg="#cc0000", border="#ff8a80", font=font_x,
                      padx=int(9 * scale), pady=int(2 * scale), radius=int(6 * scale)
                      ).place(relx=1.0, x=-int(5 * scale), y=int(5 * scale), anchor="ne")

        # Import-Dauer, dezent unten rechts (leer bis zum ersten Import)
        self.timer_label = tk.Label(self.root, text="", fg="#888888", bg="#1e1e1e",
                                    font=("Helvetica", int(8 * scale)))
        self.timer_label.place(relx=1.0, rely=1.0, anchor="se", x=-int(4 * scale), y=-int(2 * scale))

        # Fenstergröße nach Inhalt: mindestens wie bisher, breiter falls die Buttons mehr brauchen
        self.root.update_idletasks()
        win_w = max(int(280 * scale), btn_frame.winfo_reqwidth() + int(16 * scale))
        win_h = int(85 * scale)

        # Position: Mittig auf der X-Achse, 0 Pixel Abstand zum unteren Rand
        x_pos = int((screen_w - win_w) / 2) + int(screen_w * 0.25)
        y_pos = int(screen_h - win_h)

        # Zuletzt gemerkte Position verwenden, sofern sie noch auf einem Bildschirm liegt
        saved_pos = self.config.get("WINDOW_POS")
        if self._is_position_visible(saved_pos, win_w, win_h):
            x_pos, y_pos = int(saved_pos[0]), int(saved_pos[1])

        self.root.geometry(f"{win_w}x{win_h}+{x_pos}+{y_pos}")
        self.root.deiconify()
        apply_frame(self.root)

    # --- SMART VISIBILITY TRACKER ---
    def _is_md_active(self, fg_hwnd, fg_pid):
        """Prüft, ob das aktive Fenster Master Duel ist (egal ob Vollbild oder Fenster)"""
        title = win32gui.GetWindowText(fg_hwnd).upper()
        if "MASTER DUEL" in title:
            return True

        # Fallback-Check über den Prozessnamen (für randloses Vollbild)
        exe_path = win_api.process_image_name(fg_pid)
        return bool(exe_path) and "masterduel.exe" in exe_path.lower()

    def _check_window_focus(self):
        """Loop, der alle 300ms prüft, welches Fenster in Windows gerade vorne liegt."""
        try:
            fg_hwnd = win32gui.GetForegroundWindow()
            if fg_hwnd:
                _, fg_pid = win32process.GetWindowThreadProcessId(fg_hwnd)
                my_pid = os.getpid()

                # Soll sichtbar sein, wenn Master Duel offen ist ODER du gerade das Overlay selbst anklickst
                if fg_pid == my_pid or self._is_md_active(fg_hwnd, fg_pid):
                    if not self.is_visible:
                        self.root.wm_attributes("-alpha", 0.95)
                        self.is_visible = True
                else:
                    if self.is_visible:
                        self.root.wm_attributes("-alpha", 0.0)  # Verstecken!
                        self.is_visible = False
        except Exception:
            pass  # Läuft alle 300 ms; ein einzelner Fehlschlag (Fenster gerade geschlossen) ist egal
        finally:
            self._after_ids["focus"] = self.root.after(300, self._check_window_focus)

    # --- FENSTER LOGIK ---
    def start_move(self, event):
        # Nur am Fensterhintergrund ziehen, nicht an Buttons
        self._drag_offset = (event.x, event.y) if event.widget == self.root else None

    def do_move(self, event):
        if self._drag_offset is not None:
            self._moved = True
            dx, dy = self._drag_offset
            self.root.geometry(f"+{self.root.winfo_pointerx() - dx}+{self.root.winfo_pointery() - dy}")

    def end_move(self, event):
        # Nach dem Verschieben die neue Position speichern (einmal pro Loslassen, nicht pro Pixel)
        if self._moved:
            self._moved = False
            self.config["WINDOW_POS"] = [self.root.winfo_x(), self.root.winfo_y()]
            self._save_config_safely()

    def _save_config_safely(self):
        try:
            self.save_config()
        except OSError as e:
            self.update_status("Einstellung nicht gespeichert!", "yellow")
            print(f"Config konnte nicht gespeichert werden: {e}")

    @staticmethod
    def _is_position_visible(pos, win_w, win_h):
        """Prüft, ob eine gespeicherte Position komplett auf dem (virtuellen) Desktop liegt.
        Schützt davor, dass das Fenster nach Abstecken eines zweiten Monitors unsichtbar wird."""
        if not (isinstance(pos, list) and len(pos) == 2 and all(isinstance(v, int) for v in pos)):
            return False
        vx, vy, vw, vh = win_api.virtual_screen_rect()
        x, y = pos
        return vx <= x and vy <= y and x + win_w <= vx + vw and y + win_h <= vy + vh

    # --- TEMPO-AUSWAHL ---
    def _current_speed_index(self):
        keys = [p[0] for p in SPEED_PROFILES]
        current = self.config.get("SPEED_PROFILE", "normal")
        return keys.index(current) if current in keys else keys.index("normal")

    def _refresh_speed_button(self):
        _, label, color, _ = SPEED_PROFILES[self._current_speed_index()]
        self.speed_btn.config(text=f"Tempo: {label} ▾", fg=color, activeforeground=color)

    def _on_speed_selected(self):
        key = self.speed_var.get()
        if key == self.config.get("SPEED_PROFILE"):
            return
        self.config["SPEED_PROFILE"] = key
        self._refresh_speed_button()
        self._save_config_safely()
        label = SPEED_PROFILES[self._current_speed_index()][1]
        self.update_status(f"Tempo: {label}", "#00ff00")

    # --- UI-WARTESCHLANGE (thread-sicher) ---
    def _run_on_ui(self, func, *args):
        """Aus beliebigem Thread aufrufbar: führt func(*args) im Tk-Haupt-Thread aus."""
        self._ui_queue.put((func, args))

    def _process_ui_queue(self):
        try:
            while True:
                try:
                    func, args = self._ui_queue.get_nowait()
                except queue.Empty:
                    break
                func(*args)
        finally:
            self._after_ids["ui_queue"] = self.root.after(50, self._process_ui_queue)

    # --- IMPORT-TIMER ---
    @staticmethod
    def _format_duration(seconds):
        minutes, secs = divmod(int(seconds), 60)
        return f"{minutes}:{secs:02d}"

    def _on_import_started(self):
        self._import_t0 = time.perf_counter()
        self._tick_timer()

    def _tick_timer(self):
        if self._import_t0 is None:
            return
        self.timer_label.config(text=f"⏱ {self._format_duration(time.perf_counter() - self._import_t0)}")
        if self.is_running:
            self._after_ids["timer"] = self.root.after(250, self._tick_timer)

    @staticmethod
    def _restart_command():
        """Befehl, der das Programm genauso neu startet, wie es gestartet wurde (.exe, .pyw oder .py)."""
        if getattr(sys, "frozen", False):
            return [sys.executable] + sys.argv[1:]
        return [sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:]

    def restart(self):
        """Neue Instanz starten und diese komplett schließen. Ein laufender Import wird abgebrochen
        (sein Fortschritt ist gespeichert und kann beim nächsten Start fortgesetzt werden)."""
        try:
            subprocess.Popen(self._restart_command(), cwd=APP_DIR)
        except OSError as e:
            messagebox.showerror("Neustart fehlgeschlagen", str(e))
            return
        self.close()

    def close(self):
        """Alles schließen: Tray-Icon, wiederkehrende Timer, Fenster."""
        if self.tray:
            self.tray.stop()
            self.tray = None
        for after_id in self._after_ids.values():
            try:
                self.root.after_cancel(after_id)
            except tk.TclError:
                pass
        self._after_ids.clear()
        self.root.destroy()

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
        # Fenstergröße merken: Ändert sie sich später, meldet die Start-Prüfung "neu kalibrieren"
        size = get_md_window_size()
        if size:
            self.config["CALIBRATED_SIZE"] = list(size)
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
            # Abgebrochener Import mit demselben Deck-Code? Dann Fortsetzen anbieten.
            resume = resume_state.load_progress(parse_clipboard())
            if resume:
                answer = messagebox.askyesnocancel(
                    "Import fortsetzen?",
                    f"Der letzte Import mit diesem Deck wurde abgebrochen "
                    f"({len(resume['done'])} Karten waren schon eingefügt, {resume['saved_at']}).\n\n"
                    "Ja = dort weitermachen (Deck wird NICHT geleert – es darf seit dem Abbruch "
                    "nicht verändert worden sein)\n"
                    "Nein = neu starten (Deck wird geleert)")
                if answer is None:
                    return
                if not answer:
                    resume_state.clear_progress()
                    resume = None

            self.is_running = True
            for button in (self.start_btn, self.calib_btn, self.speed_btn):
                button.config(state=tk.DISABLED)

            self.timer_label.config(text="")
            self._import_t0 = None

            # Tkinter ist nicht thread-sicher: Alle UI-Änderungen aus dem Import-Thread
            # laufen über die UI-Warteschlange in den Haupt-Thread.
            def status_cb(text, color="white"):
                self._run_on_ui(self.update_status, text, color)

            def finish_cb(success, has_errors, failed_cards, message="", notes=None):
                self._run_on_ui(self._on_import_finished, success, has_errors, failed_cards, message, notes)

            def start_cb():
                self._run_on_ui(self._on_import_started)

            core = DeckImporterCore(self.config, TESSERACT_CMD, status_cb, finish_cb,
                                    start_callback=start_cb, resume=resume)
            threading.Thread(target=core.execute_import, daemon=True).start()

    def _on_import_finished(self, success, has_errors, failed_cards, message="", notes=None):
        self.is_running = False
        for button in (self.start_btn, self.calib_btn, self.speed_btn):
            button.config(state=tk.NORMAL)
        # Timer anhalten und Endzeit stehen lassen
        if self._import_t0 is not None:
            self.timer_label.config(text=f"⏱ {self._format_duration(time.perf_counter() - self._import_t0)}")
            self._import_t0 = None
        # Die Engine kann Einstellungen ergänzt haben (z.B. gemerkte Fenstergröße)
        self._save_config_safely()
        if success:
            if failed_cards or has_errors:
                self.update_status("Mit Lücken fertig!", "#ffaa00")
                show_message(self.root, "Deck-Audit – bitte prüfen",
                             "Der Import ist abgeschlossen, aber bei diesen Karten stimmt die Anzahl "
                             "nicht oder ist unsicher. Bitte im Deck prüfen und von Hand korrigieren:",
                             kind="warning", items=failed_cards, notes=notes)
            else:
                self.update_status("Import Erfolgreich!", "#00ff00")
                if notes:
                    show_message(self.root, "Import erfolgreich", "Alle Karten wurden importiert.",
                                 kind="info", notes=notes)
        else:
            self.update_status("Abbruch / Fehler", "red")
            if message:
                show_message(self.root, "Import abgebrochen", message, kind="error")


def main():
    root = tk.Tk()
    MasterDuelImporter(root, tray=True)
    root.mainloop()


if __name__ == "__main__":
    main()