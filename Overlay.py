"""
Overlay-Fenster des Master Duel Deck Importers (Import, Export, Deck-Fenster, Kalibrierung, Tempo, Timer).
Fertige Importe kommen in den Verlauf (Deck-Fenster → „Verlauf“). Im Speicher-Modus erfasst match_history die
eigenen Duelle (Deck-Fenster → „Winrate“, Meldung übers Tray-Icon).
Sichtbar nur, wenn Master Duel vorne ist und den Deck-Editor zeigt (editor_watch), während eines Imports/Exports
oder wenn ein Fenster des Importers selbst vorne ist.
"""

import ctypes
import tkinter as tk
import tkinter.font as tkfont
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
import pyperclip
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

from auto_calibration import auto_calibrate
from calibration import CalibrationWizard
from card_stats import CardStatsDB
from deck_analysis import current_changes
from deck_export import DeckExporter, save_ydk
from extras_panel import READ_METHOD_KEY, READ_METHODS, ExtrasPanel
from import_engine import DeckImporterCore
from match_history import LOSS, WIN, MatchWatcher
from app_paths import APP_DIR, APP_VERSION, CONFIG_FILE, TESSERACT_CMD
from app_icon import create_icon_image
from dark_dialog import ask_choice, show_message
from dark_menu import DarkMenu
from editor_watch import EditorWatcher
from window_style import apply_frame, no_activate
from rounded_button import RoundedButton
from tray import TrayIcon
from utils import DeckCodeError, parse_clipboard
from window_automation import get_md_window_size
import md_layout
import md_memory
import resume_state
import win_api

MEMORY_RETRY = 30.0  # Sekunden bis zum nächsten Vorlade-Versuch, wenn Master Duel noch nicht bereit war

# Tempo-Profile für die Auswahl: (Config-Wert, Anzeige, Farbe, Menü-Eintrag)
SPEED_PROFILES = [
    ("fast", "Schnell", "#ffaa00", "Schnell"),
    ("normal", "Normal", "#ffffff", "Normal"),
    ("slow", "Langsam", "#66aaff", "Langsam"),
]


class MasterDuelImporter:
    def __init__(self, root, tray: bool = False):
        self.root = root
        self.tray = None
        self.is_running = False
        self.is_visible = True  # Status für den Smart Visibility Tracker
        self._import_t0 = None  # Startzeit des laufenden Imports (für den Timer)
        self._moved = False  # Wurde das Fenster seit dem letzten Klick verschoben?
        self._position_warning = False  # Zeigt der Status gerade die Warnung "verdeckt …"?
        self._drag_offset = None  # Mausposition im Fenster beim Start des Verschiebens
        self.extras = None       # Extras-Menü (über der Kartenliste), None = zu
        self._import_code = ""   # Deck-Code des laufenden Imports (kommt danach in den Verlauf)
        self._core = None        # laufender bzw. letzter Import
        self.card_stats = None   # Lokale Karten-Datenbank (erst beim ersten Öffnen der Extras)
        self._last_scan = None   # Letzter Deck-Scan der Extras (wird übernommen, wenn das Deck gleich ist)
        self._memory_thread = None    # Vorladen für den Speicher-Modus (läuft im Hintergrund)
        self._memory_preload_at = -MEMORY_RETRY  # letzter Versuch (time.monotonic)
        # Ist der Deck-Editor zu sehen? (Hintergrund-Thread, prüft nur, solange Master Duel vorne ist)
        self.editor_watch = EditorWatcher(TESSERACT_CMD)
        # Winrate-Tracker: erfasst Duelle am Ergebnis-Bildschirm von Master Duel (Speicher-Modus, nur lesend)
        self.match_watch = MatchWatcher(self._stats_db, lambda new: self._run_on_ui(self._on_new_matches, new),
                                        connected=md_memory.is_ready, memory=md_memory.shared)
        # Tkinter ist nicht thread-sicher: Der Import-Thread legt UI-Aufträge nur in diese
        # Warteschlange, der Haupt-Thread arbeitet sie regelmäßig ab.
        self._ui_queue = queue.Queue()
        # IDs der wiederkehrenden Tk-Timer, damit sie beim Schließen gestoppt werden können
        self._after_ids = {}
        self.config = self.load_config()
        self._build_main_ui()
        self._process_ui_queue()
        if self.config.get(READ_METHOD_KEY) not in READ_METHODS:
            # Erster Start: Lesemethode wählen lassen (danach in den Optionen des Deck-Fensters umschaltbar)
            self._after_ids["read_method"] = self.root.after(300, self._ask_read_method)
        if tray:
            # Menü-Aktionen kommen aus dem Tray-Thread → über die UI-Warteschlange in den Tk-Thread
            self.tray = TrayIcon(f"MD Importer {APP_VERSION}",
                                 on_restart=lambda: self._run_on_ui(self.restart),
                                 on_quit=lambda: self._run_on_ui(self.close))

        # Starte den unsichtbaren Radar für den Fenster-Fokus
        self.editor_watch.start()
        self.match_watch.start()
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
                show_message(
                    self.root, "Konfiguration beschädigt",
                    f"{CONFIG_FILE} konnte nicht gelesen werden:\n{e}\n\n"
                    f"Die Datei wurde NICHT überschrieben. Sicherung: {backup}\n\n"
                    "Bitte den Fehler in der Datei korrigieren und neu starten "
                    "oder neu kalibrieren.", kind="warning"
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
        self.status_label.pack(pady=(int(3 * scale), int(1 * scale)))

        btn_frame = tk.Frame(self.root, bg="#1e1e1e")
        btn_frame.pack(pady=(0, int(7 * scale)))

        btn_pack = dict(side=tk.LEFT, padx=int(4 * scale))
        btn_size = dict(padx=int(14 * scale), pady=int(5 * scale), radius=int(7 * scale))

        # Lesemethode (Speicher oder Texterkennung) steht in den Optionen des Deck-Fensters
        self.start_btn = RoundedButton(btn_frame, text="Importieren", command=self.start_import_thread,
                                       bg="#007acc", font=font_main, **btn_size)
        self.start_btn.pack(**btn_pack)

        self.export_btn = RoundedButton(btn_frame, text="Exportieren", command=self.start_export_thread,
                                        bg="#2e7d32", font=font_main, **btn_size)
        self.export_btn.pack(**btn_pack)

        self.extras_btn = RoundedButton(btn_frame, text="Deck", command=self.toggle_extras,
                                        bg="#5e35b1", font=font_main, **btn_size)
        self.extras_btn.pack(**btn_pack)

        # Kalibrieren: automatisch (ein Klick) oder von Hand mit dem Assistenten
        self.calib_btn = RoundedButton(btn_frame, text="Kalibrieren ▾", bg="#444444", font=font_sub, **btn_size)
        self.calib_menu = DarkMenu(self.root, font=font_sub)
        self.calib_menu.add_command(label="Automatisch", command=self.start_auto_calibration)
        self.calib_menu.add_command(label="Manuell (Assistent)", command=self.open_calibration)
        self.calib_btn.config(menu=self.calib_menu)
        self.calib_btn.pack(**btn_pack)

        # Tempo-Auswahl ganz rechts: Button mit ▾, öffnet ein Auswahlmenü (nach oben, da das
        # Fenster am unteren Bildschirmrand sitzt). Die aktuelle Wahl ist im Menü markiert.
        # Feste Breite für den längsten Eintrag, damit der Button beim Umschalten nicht springt.
        self.speed_var = tk.StringVar(value=SPEED_PROFILES[self._current_speed_index()][0])
        longest = max(tkfont.Font(font=font_sub).measure(f"Tempo: {label} ▾") for _, label, _, _ in SPEED_PROFILES)
        self.speed_btn = RoundedButton(btn_frame, bg="#444444", font=font_sub,
                                       min_width=longest + 2 * btn_size["padx"], **btn_size)
        self.speed_menu = DarkMenu(self.root, font=font_sub)
        for key, _, _, menu_text in SPEED_PROFILES:
            self.speed_menu.add_radiobutton(label=menu_text, value=key, variable=self.speed_var,
                                            command=self._on_speed_selected)
        self.speed_btn.config(menu=self.speed_menu)
        self._btn_pack = btn_pack
        # Bei "Speicher lesen" ist Tempo nur noch Reserve → steht dann in den Optionen des Deck-Fensters
        if not self._use_memory():
            self.speed_btn.pack(**btn_pack)
        self._refresh_speed_button()
        self._btn_frame, self._scale = btn_frame, scale

        # Etwas eingerückt, damit der abgerundete Goldrand im Eck frei bleibt
        RoundedButton(self.root, text="✕", command=self.close, bg="#cc0000", border="#ff8a80", font=font_x,
                      padx=int(9 * scale), pady=int(2 * scale), radius=int(6 * scale)
                      ).place(relx=1.0, x=-int(5 * scale), y=int(5 * scale), anchor="ne")

        # Import-Dauer, dezent oben links (leer bis zum ersten Import)
        self.timer_label = tk.Label(self.root, text="", fg="#888888", bg="#1e1e1e",
                                    font=("Helvetica", int(8 * scale)))
        self.timer_label.place(x=int(10 * scale), y=int(6 * scale), anchor="nw")

        # Fenstergröße nach Inhalt: mindestens wie bisher, breiter falls die Buttons mehr brauchen
        self.root.update_idletasks()
        win_w = self._content_width()
        # So flach wie möglich: Höhe nach Inhalt (Statuszeile + Buttons), damit das Overlay am
        # unteren Rand nicht in die Kartenliste von Master Duel ragt
        win_h = self.root.winfo_reqheight()

        # Position: Mittig auf der X-Achse, 0 Pixel Abstand zum unteren Rand
        x_pos = int((screen_w - win_w) / 2) + int(screen_w * 0.25)
        y_pos = int(screen_h - win_h)

        # Zuletzt gemerkte Position verwenden, sofern sie noch auf einem Bildschirm liegt
        saved_pos = self.config.get("WINDOW_POS")
        if self._is_position_visible(saved_pos, win_w, win_h):
            x_pos, y_pos = int(saved_pos[0]), self._snap_to_bottom(int(saved_pos[1]), win_h)

        self.root.geometry(f"{win_w}x{win_h}+{x_pos}+{y_pos}")
        self.root.deiconify()
        apply_frame(self.root)
        no_activate(self.root)  # Klicks lassen Master Duel aktiv (sonst Taskleiste über dem Spiel)
        self.root.update_idletasks()
        self._check_overlay_position()  # gemerkte Position könnte über dem Deck liegen

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

                md_active = fg_pid != my_pid and self._is_md_active(fg_hwnd, fg_pid)
                # Editor-Prüfung nur mit Master Duel vorne – und nicht, solange das Mouseover-Fenster des
                # Deck-Fensters offen ist (es kann über der Überschrift "Main Deck" liegen)
                self.editor_watch.md_front = md_active
                self.editor_watch.paused = bool(self.extras and self.extras.hover.visible)
                self.match_watch.enabled = self._use_memory()
                if md_active and self._use_memory():
                    self._preload_memory()
                if self._should_show(fg_pid == my_pid, md_active):
                    if not self.is_visible:
                        self.root.wm_attributes("-alpha", 0.95)
                        self.is_visible = True
                else:
                    if self.is_visible:
                        self.root.wm_attributes("-alpha", 0.0)  # Verstecken!
                        self.is_visible = False
                if self.extras:
                    self.extras.set_visible(self.is_visible)
        except Exception:
            pass  # Läuft alle 300 ms; ein einzelner Fehlschlag (Fenster gerade geschlossen) ist egal
        finally:
            self._after_ids["focus"] = self.root.after(300, self._check_window_focus)

    def _should_show(self, own_window_in_front: bool, md_in_front: bool) -> bool:
        """
        Sichtbar: ein Fenster des Importers ist vorne (z.B. gerade angeklickt), oder Master Duel ist vorne und
        zeigt den Deck-Editor (unbekannt bzw. nicht prüfbar zählt als ja) – während eines Imports immer.
        """
        if own_window_in_front:
            return True
        return md_in_front and (self.is_running or self.editor_watch.visible is not False)

    # --- LESEMETHODE: Speicher (nur lesend) oder Texterkennung ---
    def _use_memory(self) -> bool:
        return self.config.get(READ_METHOD_KEY) == "memory"

    def _ask_read_method(self):
        """Beim ersten Start fragen, wie Karten gelesen werden sollen (gilt für Import, Deck-Scan und Export)."""
        self._after_ids.pop("read_method", None)
        choice = ask_choice(
            self.root, "Wie sollen Karten gelesen werden?",
            "Speicher lesen (empfohlen): Der Importer liest die Karten-IDs direkt aus Master Duel – schneller "
            "und ohne Lesefehler. Es wird nur gelesen, am Spiel wird nichts verändert. Konami erlaubt so etwas "
            "nicht ausdrücklich (wie bei Trackern, z.B. untapped.gg) – Nutzung auf eigenes Risiko.\n\n"
            "Texterkennung: Der Importer liest die Kartennamen vom Bildschirm – langsamer, aber ganz ohne "
            "Zugriff auf das Spiel.\n\n"
            "Gilt für Import, Deck-Scan und Export. Umschalten: Deck → Optionen.",
            [("Speicher lesen", "memory", "#007acc"), ("Texterkennung", "ocr", "#444444")])
        if choice is None:
            return  # nichts gewählt: Texterkennung, beim nächsten Start wird wieder gefragt
        self.config[READ_METHOD_KEY] = choice
        self._save_config_safely()
        self._update_speed_button()
        self.update_status(f"Lesemethode: {READ_METHODS[choice]}", "#00ff00")

    def _preload_memory(self):
        """
        Speicher-Modus schon vorbereiten, sobald Master Duel läuft (Hintergrund-Thread, nur lesend).
        Der Import muss dann nicht erst warten; die Verbindung gilt, solange das Spiel läuft.
        """
        if md_memory.is_ready() or (self._memory_thread is not None and self._memory_thread.is_alive()):
            return
        if time.monotonic() - self._memory_preload_at < MEMORY_RETRY:
            return
        self._memory_preload_at = time.monotonic()
        self._memory_thread = threading.Thread(target=md_memory.preload, daemon=True)
        self._memory_thread.start()

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
            snapped = self._snap_to_bottom(self.root.winfo_y(), self.root.winfo_height())
            if snapped != self.root.winfo_y():
                self.root.geometry(f"+{self.root.winfo_x()}+{snapped}")
                self.root.update_idletasks()
            self.config["WINDOW_POS"] = [self.root.winfo_x(), snapped]
            self._save_config_safely()
            self._check_overlay_position()

    def _snap_to_bottom(self, y, height):
        """
        Fast am unteren Bildschirmrand → genau an den Rand: knapp darüber losgelassen und auch knapp darunter
        (ragt ein Stück über den Rand hinaus). Gilt auch für Positionen vom früheren, höheren Overlay.
        """
        screen_h = self.root.winfo_screenheight()
        snap = int(40 * max(1.0, screen_h / 1080.0))
        bottom_gap = screen_h - (y + height)  # negativ = ragt unten über den Rand
        return screen_h - height if abs(bottom_gap) <= snap else y

    def _covered_area(self):
        """Welchen Klickbereich von Master Duel verdeckt das Overlay? None = keinen (oder Spiel nicht offen)."""
        frame = md_layout.md_frame()
        if frame is None:
            return None
        x, y = self.root.winfo_x(), self.root.winfo_y()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        for name, area in md_layout.CLICK_AREAS.items():
            r = frame.region(*area)
            if x < r["left"] + r["width"] and r["left"] < x + w and y < r["top"] + r["height"] and r["top"] < y + h:
                return name
        return None

    def _content_width(self):
        """Mindestens wie bisher, breiter falls die Buttons mehr brauchen."""
        return max(int(280 * self._scale), self._btn_frame.winfo_reqwidth() + int(16 * self._scale))

    def _update_speed_button(self):
        """Tempo-Button nur bei Texterkennung; das Overlay passt seine Breite an (rechte Kante bleibt)."""
        shown = bool(self.speed_btn.winfo_manager())
        if shown == (not self._use_memory()):
            return
        if shown:
            self.speed_btn.pack_forget()
        else:
            self.speed_btn.pack(**self._btn_pack)
        self.root.update_idletasks()
        right = self.root.winfo_x() + self.root.winfo_width()
        width = self._content_width()
        x = max(0, min(right - width, self.root.winfo_screenwidth() - width))
        self.root.geometry(f"{width}x{self.root.winfo_height()}+{x}+{self.root.winfo_y()}")

    def _on_settings_changed(self):
        """Einstellung im Deck-Fenster geändert (Lesemethode, Tempo, …): speichern und Overlay anpassen."""
        self._save_config_safely()
        self.speed_var.set(SPEED_PROFILES[self._current_speed_index()][0])
        self._refresh_speed_button()
        self._update_speed_button()

    def _check_overlay_position(self):
        """Warnt im Status, wenn das Overlay dort liegt, wo Import/Export klicken (Klicks träfen das Overlay)."""
        if self.is_running:
            return
        covered = self._covered_area()
        if covered:
            self.update_status(f"⚠ Overlay verdeckt {covered} – bitte verschieben", "#ffaa00")
        elif self._position_warning:
            self.update_status("Bereit für Import", "#00ff00")
        self._position_warning = bool(covered)

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
            show_message(self.root, "Neustart fehlgeschlagen", str(e), kind="error")
            return
        self.close()

    def close(self):
        """Alles schließen: Tray-Icon, wiederkehrende Timer, Fenster."""
        self._close_extras()
        self.editor_watch.stop()
        self.match_watch.stop()
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

    def _set_buttons(self, state):
        for button in (self.start_btn, self.export_btn, self.extras_btn, self.calib_btn, self.speed_btn):
            button.config(state=state)

    # --- AUTOMATISCHE KALIBRIERUNG ---
    def start_auto_calibration(self, then=None):
        """Kalibriert im Hintergrund (Texterkennung dauert kurz). `then`: danach ausführen, wenn es geklappt hat."""
        self._close_extras()
        self._set_buttons(tk.DISABLED)
        self.update_status("Kalibriere automatisch...", "cyan")

        def work():
            new_config, message = auto_calibrate(self.config, TESSERACT_CMD)
            self._run_on_ui(self._on_auto_calibration_done, new_config, message, then)

        threading.Thread(target=work, daemon=True).start()

    def _on_auto_calibration_done(self, new_config, message, then):
        if new_config is None:
            # Nicht sicher genug → Assistent wie bisher
            self.update_status("Automatisch nicht möglich", "yellow")
            show_message(self.root, "Automatische Kalibrierung",
                         f"{message}\n\nDer Kalibrierungs-Assistent startet jetzt.", kind="warning")
            self.open_calibration()
            return
        self.on_calibration_done(new_config)
        self.update_status(message, "#00ff00")
        if then:
            then()

    def open_calibration(self):
        self._close_extras()  # Der Assistent braucht Klicks auf die Kartenliste
        self._set_buttons(tk.DISABLED)
        CalibrationWizard(self.root, self.config, self.on_calibration_done, self.on_calibration_cancel)

    def on_calibration_cancel(self):
        self.update_status("Kalibrierung abgebrochen", "yellow")
        self._set_buttons(tk.NORMAL)

    def on_calibration_done(self, new_config):
        self.config = new_config
        self.config["IS_CALIBRATED"] = True
        # Fenstergröße merken: Ändert sie sich später, meldet die Start-Prüfung "neu kalibrieren"
        size = get_md_window_size()
        if size:
            self.config["CALIBRATED_SIZE"] = list(size)
        self.save_config()
        self.update_status("Kalibrierung aktiv!", "#00ff00")
        self._set_buttons(tk.NORMAL)

    def start_import_thread(self, memory=None):
        """memory=True: Karten per ID aus dem Speicher lesen (nur lesend); None = wie in den Optionen eingestellt."""
        if memory is None:
            memory = self._use_memory()
        if not self.config.get("IS_CALIBRATED", False):
            # Erst automatisch versuchen und dann direkt importieren; klappt das nicht → Assistent
            self.start_auto_calibration(then=lambda: self.start_import_thread(memory=memory))
            return

        if not self.is_running:
            # Abgebrochener Import mit demselben Deck-Code? Dann Fortsetzen anbieten.
            try:
                resume = resume_state.load_progress(parse_clipboard())
            except DeckCodeError:
                resume = None  # Der Import meldet den beschädigten Code selbst
            if resume:
                answer = ask_choice(
                    self.root, "Import fortsetzen?",
                    f"Der letzte Import mit diesem Deck wurde abgebrochen "
                    f"({len(resume['done'])} Karten waren schon eingefügt, {resume['saved_at']}).\n\n"
                    "Fortsetzen: dort weitermachen – das Deck wird NICHT geleert (es darf seit dem Abbruch "
                    "nicht verändert worden sein).\n"
                    "Neu starten: Deck wird geleert und komplett neu importiert.",
                    [("Fortsetzen", True, "#007acc"), ("Neu starten", False, "#ef6c00"),
                     ("Abbrechen", None, "#444444")])
                if answer is None:
                    return
                if not answer:
                    resume_state.clear_progress()
                    resume = None

            self.is_running = True
            self._close_extras()  # Import klickt in Deck und Kartenliste
            try:
                self._import_code = pyperclip.paste() or ""
            except Exception:  # Zwischenablage gerade belegt – der Import liest sie gleich selbst
                self._import_code = ""
            self._last_scan = None  # Import verändert das Deck
            self._set_buttons(tk.DISABLED)

            self.timer_label.config(text="")
            self._import_t0 = None

            # Tkinter ist nicht thread-sicher: Alle UI-Änderungen aus dem Import-Thread
            # laufen über die UI-Warteschlange in den Haupt-Thread.
            def status_cb(text, color="white"):
                self._run_on_ui(self.update_status, text, color)

            def finish_cb(success, has_errors, failed_cards, message="", notes=None, scan=None):
                self._run_on_ui(self._on_import_finished, success, has_errors, failed_cards, message, notes, scan)

            def start_cb():
                self._run_on_ui(self._on_import_started)

            core = DeckImporterCore(self.config, TESSERACT_CMD, status_cb, finish_cb,
                                    start_callback=start_cb, resume=resume, memory=memory)
            self._core = core
            threading.Thread(target=core.execute_import, daemon=True).start()

    def _on_import_finished(self, success, has_errors, failed_cards, message="", notes=None, scan=None):
        self.is_running = False
        if scan is not None:
            self._last_scan = scan  # Die Kontrolle am Ende hat das Deck gelesen → gilt als gescannt
        self._set_buttons(tk.NORMAL)
        # Timer anhalten und Endzeit stehen lassen
        if self._import_t0 is not None:
            self.timer_label.config(text=f"⏱ {self._format_duration(time.perf_counter() - self._import_t0)}")
            self._import_t0 = None
        # Die Engine kann Einstellungen ergänzt haben (z.B. gemerkte Fenstergröße)
        self._save_config_safely()
        if success:
            self._add_to_history("ok" if not (failed_cards or has_errors) else "Lücken")
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

    # --- DECK-EXPORT ---
    def start_export_thread(self):
        if self.is_running:
            return
        self.is_running = True
        # Schon gescannt (Deck-Fenster oder letzter Export) und Deck seitdem unverändert? Dann nicht neu lesen
        flagged = self.extras.change_reason() if self.extras else None
        self._close_extras()
        scan = self._last_scan if self._last_scan and not flagged and not current_changes(self._last_scan) else None
        self._set_buttons(tk.DISABLED)
        self.timer_label.config(text="")
        self._import_t0 = None
        if scan:
            self.update_status("Deck unverändert – letzter Scan wird exportiert", "cyan")

        def status_cb(text, color="white"):
            self._run_on_ui(self.update_status, text, color)

        def finish_cb(result, error=""):
            self._run_on_ui(self._on_export_finished, result, error)

        def start_cb():
            self._run_on_ui(self._on_import_started)  # Timer läuft auch beim Export (ab Ende des Countdowns)

        exporter = DeckExporter(self.config, TESSERACT_CMD, status_cb, finish_cb, start_callback=start_cb, scan=scan,
                                memory=self._use_memory())
        threading.Thread(target=exporter.execute, daemon=True).start()

    def _on_export_finished(self, result, error=""):
        self.is_running = False
        self._set_buttons(tk.NORMAL)
        if self._import_t0 is not None:
            self.timer_label.config(text=f"⏱ {self._format_duration(time.perf_counter() - self._import_t0)}")
            self._import_t0 = None
        if result is None:
            self.update_status("Export abgebrochen", "red")
            show_message(self.root, "Export abgebrochen", error, kind="error")
            return
        if result.scan is not None:
            self._last_scan = result.scan  # Deck-Fenster muss danach nicht neu scannen

        found = len(result.cards) - result.missing
        message = (f"{found} von {len(result.cards)} Karten erkannt "
                   f"(Main {result.main_count}, Extra {result.extra_count}).\n"
                   f"Die .ydk liegt in der Zwischenablage.")
        if result.reused:
            message += "\n(Deck unverändert – letzter Scan übernommen, nicht neu gelesen.)"
        problems = result.problems
        if problems:
            message += "\n\nBitte prüfen (nicht erkannte Karten fehlen in der .ydk):"
            self.update_status("Export mit Hinweisen", "#ffaa00")
        else:
            self.update_status("Deck exportiert!", "#00ff00")

        def download():
            try:
                return f"Gespeichert: {save_ydk(result.ydk)}"
            except OSError as e:
                return f"Speichern fehlgeschlagen: {e}"

        show_message(self.root, "Deck exportiert", message, kind="warning" if problems else "info",
                     items=problems, actions=[("Download als .ydk", download)])


    # --- VERLAUF der importierten Decks ---
    def _stats_db(self):
        """Lokale Karten-Datenbank (auch für den Verlauf); None, wenn sie nicht angelegt werden kann."""
        if self.card_stats is None:
            try:
                self.card_stats = CardStatsDB()
            except Exception as e:  # z.B. Ordner schreibgeschützt
                print(f"Karten-Datenbank nicht verfügbar: {e}")
        return self.card_stats

    def _add_to_history(self, result):
        card_ids = getattr(self._core, "_card_ids", None)
        if not card_ids or not self._import_code.strip():
            return
        db = self._stats_db()
        if db is None:
            return
        try:
            db.add_history(self._import_code, card_ids, getattr(self._core, "deck_names", {}).values(), result)
        except Exception as e:  # Verlauf ist nur Zusatz – der Import selbst hat geklappt
            print(f"Verlauf nicht gespeichert: {e}")

    def _copy_from_history(self, code):
        pyperclip.copy(code)
        self.update_status("Deck-Code kopiert", "#00ff00")

    def _import_from_history(self, code):
        """Deck aus dem Verlauf erneut importieren: Deck-Code in die Zwischenablage, dann wie gewohnt starten."""
        if self.is_running:
            return
        pyperclip.copy(code)
        self.start_import_thread()

    # --- WINRATE (eigene Duelle aus Master Duel) ---
    def _on_new_matches(self, new):
        if len(new) == 1:
            match = new[0]
            text = {WIN: "Sieg", LOSS: "Niederlage"}.get(match.result, "Unentschieden")
            if match.first is not None:
                text += " als Erster" if match.first else " als Zweiter"
            if match.md_deck:
                text += f" mit {match.md_deck}"
            text += " erfasst"
        else:
            wins = sum(1 for m in new if m.result == WIN)
            losses = sum(1 for m in new if m.result == LOSS)
            text = f"{len(new)} Matches erfasst: {wins} Sieg(e), {losses} Niederlage(n)"
        self.update_status(text, "#00ff00")
        if self.tray:
            self.tray.notify(text + " – Winrate im Deck-Fenster", "MD Importer")
        if self.extras:
            self.extras.refresh_winrate()

    # --- EXTRAS (Draw-Chance & Starter) ---
    def toggle_extras(self):
        if self.extras:
            self._close_extras()
            return
        if self.is_running:
            return
        self._stats_db()  # fehlt sie (z.B. Ordner schreibgeschützt) → Extras ohne Starter-Infos
        self.extras = ExtrasPanel(self.root, self.card_stats, TESSERACT_CMD, on_rescan=self._start_extras_scan,
                                  on_close=self._on_extras_closed, is_active=lambda: self.is_visible,
                                  settings=self.config, save_settings=self._on_settings_changed,
                                  on_import_code=self._import_from_history, on_copy_code=self._copy_from_history)
        if not (self._last_scan and self.extras.try_reuse(self._last_scan)):
            self._start_extras_scan()

    def _close_extras(self):
        if self.extras:
            self.extras.close()  # ruft _on_extras_closed

    def _on_extras_closed(self):
        self.extras = None

    def _start_extras_scan(self):
        if self.is_running or not self.extras:
            return
        self.is_running = True
        self._set_buttons(tk.DISABLED)
        self.extras_btn.config(state=tk.NORMAL)  # Extras lassen sich auch während des Scans schließen
        self.extras.set_scanning()

        def status_cb(text, color="white"):
            self._run_on_ui(self._extras_status, text, color)

        def finish_cb(scan=None, error=""):
            missing = 0
            if scan is not None and self.card_stats is not None:
                status_cb("Karten-Stats werden nachgeschlagen …", "cyan")
                missing = self.card_stats.ensure(c.match.cid for c in scan.cards if c.match.cid)
            self._run_on_ui(self._on_extras_scan_done, scan, error, missing)

        exporter = DeckExporter(self.config, TESSERACT_CMD, status_cb, finish_cb, memory=self._use_memory())
        exporter.label = "Scan"
        threading.Thread(target=exporter.execute_scan, daemon=True).start()

    def _extras_status(self, text, color):
        self.update_status(text, color)
        if self.extras:
            self.extras.set_status(text, color)

    def _on_extras_scan_done(self, scan, error, missing):
        self.is_running = False
        self._set_buttons(tk.NORMAL)
        if scan is not None:
            self._last_scan = scan
            self.update_status("Deck gescannt – Analyse bereit", "#00ff00")
        else:
            self.update_status("Scan abgebrochen", "red")
        if self.extras:
            if scan is not None:
                self.extras.set_scan(scan, missing)
            else:
                self.extras.show_error(error)


def main():
    root = tk.Tk()
    MasterDuelImporter(root, tray=True)
    root.mainloop()


if __name__ == "__main__":
    main()