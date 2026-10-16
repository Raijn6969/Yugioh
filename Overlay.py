"""
Overlay-Fenster des Master Duel Deck Importers (Import, Export, Deck-Fenster, Kalibrierung, Tempo, Timer).
Fertige Importe kommen in den Verlauf (Deck-Fenster → „Verlauf“). Im Speicher-Modus erfasst match_history die
eigenen Duelle (Deck-Fenster → „Winrate“, Meldung übers Tray-Icon).
Sichtbar nur, wenn Master Duel vorne ist und den Deck-Editor zeigt (editor_watch), während eines Imports/Exports
oder wenn ein Fenster des Importers selbst vorne ist.
"""

import ctypes
from collections import Counter
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
import tempfile
import traceback
import json
import webbrowser
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
from match_history import LOSS, RANKED, WIN, MatchWatcher
from replay_recorder import ReplayRecorder
from app_paths import APP_DIR, APP_VERSION, CONFIG_FILE, DATA_DIR, LOG_FILE, TESSERACT_CMD
from app_icon import create_icon_image
from dark_dialog import ask_choice, show_message, show_toast
from dark_menu import DarkMenu
from debug_log import append, dlog
from editor_watch import EditorWatcher
from hang_watch import HangWatch
from window_style import apply_frame, no_activate
from rounded_button import RoundedButton
from tray import TrayIcon
from utils import DeckCodeError, parse_clipboard
from window_automation import get_md_window_size, is_md_process
import bug_report
import md_layout
import md_memory
import resume_state
import season_review
import single_instance
import update_check
import updater
import win_api

MEMORY_RETRY = 30.0  # Sekunden bis zum nächsten Vorlade-Versuch, wenn Master Duel noch nicht bereit war
MEMORY_WARN_AFTER = 6  # so viele Fehlversuche (je MEMORY_RETRY mit Master Duel vorne) bis zum Hinweis
STARTED_TOAST_MS = 5000  # Startmeldung "läuft jetzt" über der Taskleiste
REMINDER_DELAY_MS = 6000  # erst das Ergebnis zeigen, dann die Erinnerung an die Match History
SHORT_REMINDER = "Bitte Match History öffnen (Gegner-Decks)"
REMINDER_CHECK_MS = 3000  # so oft prüfen, ob die Match History inzwischen geöffnet wurde (solange der Hinweis steht)
STATUS_DECK_CHARS = 14  # längere MD-Decknamen in der Statuszeile kürzen (Platz neben dem ✕)
UPDATE_NOTIFIED_KEY = "UPDATE_NOTIFIED"  # Einstellung: zuletzt gemeldete neuere Version (Frage nur einmal je Version)
UPDATE_RETRY_MS = 1000  # läuft ein Import, wird die Update-Frage so lange verschoben
TOAST_GAP_MS = 300  # Pause zwischen zwei Hinweisen über der Taskleiste
REVIEW_NOTICE_DELAY_MS = STARTED_TOAST_MS + 2000  # so lange nach dem Start wird der Rückblick-Hinweis geprüft

# Tempo-Profile für die Auswahl: (Config-Wert, Anzeige, Farbe, Menü-Eintrag)
SPEED_PROFILES = [
    ("fast", "Schnell", "#ffaa00", "Schnell"),
    ("normal", "Normal", "#ffffff", "Normal"),
    ("slow", "Langsam", "#66aaff", "Langsam"),
]


def validate_config(raw):
    """
    Prüft Konfigurationswerte auf Gültigkeit und liefert bereinigte Werte sowie Warnungen.
    Verändert ungültige Werte defensiv zu Standardwerten.
    """
    warnings = []
    cleaned = dict(raw)

    speed = cleaned.get("SPEED_PROFILE")
    if speed is not None and speed not in [p[0] for p in SPEED_PROFILES]:
        warnings.append(f"Ungültiges SPEED_PROFILE '{speed}', Fallback auf 'normal'.")
        cleaned["SPEED_PROFILE"] = "normal"

    method = cleaned.get("READ_METHOD")
    if method is not None and method not in READ_METHODS:
        warnings.append(f"Ungültige READ_METHOD '{method}', Fallback auf 'memory'.")
        cleaned["READ_METHOD"] = "memory"

    for coord_key in ("SEARCH_BAR", "FIRST_CARD", "TRASH_BTN", "TRASH_CONFIRM", "UNOWNED_BTN"):
        coord = cleaned.get(coord_key)
        if coord is not None:
            if not (isinstance(coord, (list, tuple)) and len(coord) == 2
                    and all(isinstance(v, (int, float)) for v in coord)):
                warnings.append(f"Koordinate {coord_key} fehlerhaft: {coord}")

    return cleaned, warnings


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
        self._swap_label = None  # Name des laufenden Side-Tauschs (None = normaler Import)
        self._core = None        # laufender bzw. letzter Import
        self.card_stats = None   # Lokale Karten-Datenbank (erst beim ersten Öffnen der Extras)
        self._last_scan = None   # Letzter Deck-Scan der Extras (wird übernommen, wenn das Deck gleich ist)
        self._memory_thread = None    # Vorladen für den Speicher-Modus (läuft im Hintergrund)
        self._memory_preload_at = -MEMORY_RETRY  # letzter Versuch (time.monotonic)
        self._memory_warned = False  # Hinweis "Speicher-Modus nicht verfügbar" schon gezeigt (einmal pro Sitzung)
        # Tkinter ist nicht thread-sicher: Der Import-Thread legt UI-Aufträge nur in diese
        # Warteschlange, der Haupt-Thread arbeitet sie regelmäßig ab.
        self._ui_queue = queue.Queue()
        self._toast_free_at = time.monotonic() + (STARTED_TOAST_MS + TOAST_GAP_MS) / 1000
        self._toast_count = 0
        self.update_release = None   # neuerer Release laut Update-Prüfung (None = keiner bzw. nicht geprüft)
        self._update_running = False  # Update wird gerade geladen/installiert
        self._report_running = False  # Fehlerbericht wird gerade erstellt
        # Ist der Deck-Editor zu sehen? (Hintergrund-Thread, prüft nur, solange Master Duel vorne ist)
        self.editor_watch = EditorWatcher(TESSERACT_CMD)
        # Hänger-Wächter: schreibt ins Log, wo der Haupt-Thread hängt (Herzschlag in _check_window_focus)
        self.hang_watch = HangWatch(lambda text: append(LOG_FILE, text))
        # Winrate-Tracker: erfasst Duelle am Ergebnis-Bildschirm von Master Duel (Speicher-Modus, nur lesend)
        # Replay-Mitschnitt: liest das Feld nur während eines Replays aus der Match History (replay_recorder)
        self.replay_watch = ReplayRecorder(self._stats_db, connected=md_memory.is_ready, memory=md_memory.shared,
                                           on_saved=lambda info, steps: self._run_on_ui(self._on_replay_saved, info,
                                                                                        steps))
        self.match_watch = MatchWatcher(self._stats_db, lambda new: self._run_on_ui(self._on_new_matches, new),
                                        connected=md_memory.is_ready, memory=md_memory.shared)
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
                                 on_quit=lambda: self._run_on_ui(self.close),
                                 on_update=lambda: self._run_on_ui(self._ask_update_when_idle),
                                 on_report=lambda: self._run_on_ui(self._create_report),
                                 on_menu=lambda x, y: self._run_on_ui(self._show_tray_menu, x, y))

        # Starte den unsichtbaren Radar für den Fenster-Fokus
        self.editor_watch.start()
        self.hang_watch.start()
        self.match_watch.start()
        self.replay_watch.start()
        self._check_window_focus()
        self._backup_database()
        self._start_update_check()
        # Erst nach der Startmeldung: dann sind Datenbank und Fenster längst bereit
        self._after_ids["review_notice"] = self.root.after(REVIEW_NOTICE_DELAY_MS, self._start_review_notice)

    # --- UPDATE-HINWEIS (einmal pro Start, im Hintergrund) ---
    def _start_update_check(self):
        """Gibt es eine neuere Version? Nur als exe (aus dem Quellcode holt man sich Updates mit git selbst)."""
        if not getattr(sys, "frozen", False):
            dlog("[UPDATE] Lauf aus dem Quellcode – keine Update-Prüfung.")
            return
        threading.Thread(target=self._update_worker, daemon=True, name="update").start()

    def _update_worker(self):
        updater.cleanup_old(tempfile.gettempdir())  # Reste eines früheren Updates
        release = update_check.find_update(APP_VERSION, log=lambda message: append(LOG_FILE, message))
        if release:
            self._run_on_ui(self._on_update_found, release)

    def _on_update_found(self, release):
        self.update_release = release
        if self.tray:
            self.tray.set_update(release.version)
        notified = self.config.get(UPDATE_NOTIFIED_KEY)
        if notified and not update_check.is_newer(release.version, notified):
            return  # zu dieser Version schon gefragt – der Tray-Menüpunkt bleibt
        self.config[UPDATE_NOTIFIED_KEY] = release.version
        self._save_config_safely()
        self._ask_update_when_idle()

    def _can_self_update(self, release) -> bool:
        """Ein-Klick-Update nur als exe, mit zip am Release und beschreibbarem Programmordner."""
        return bool(getattr(sys, "frozen", False) and release.zip_url and DATA_DIR == APP_DIR)

    def _ask_update_when_idle(self):
        """Update-Frage; läuft gerade ein Import/Export, erst danach (alle UPDATE_RETRY_MS nachsehen)."""
        self._after_ids.pop("update_ask", None)
        if self.update_release is None or self._update_running:
            return
        if self.is_running:
            self._after_ids["update_ask"] = self.root.after(UPDATE_RETRY_MS, self._ask_update_when_idle)
            return
        release = self.update_release
        text = f"MD-Importer {release.version} ist da (installiert: {APP_VERSION}). "
        if self._can_self_update(release):
            choices = [("Jetzt aktualisieren", "install", "#007acc"), ("Was ist neu?", "page", "#444444"),
                       ("Später", None, "#444444")]
            text += ("Jetzt aktualisieren? Der Importer lädt die neue Version, ersetzt sich selbst und startet neu "
                     "– deine Daten bleiben erhalten.")
        else:
            choices = [("Release-Seite öffnen", "page", "#007acc"), ("Später", None, "#444444")]
            text += "Neue Version auf der Release-Seite herunterladen."
        choice = ask_choice(self.root, "Update verfügbar", text, choices)
        if choice == "install":
            self._start_update(release)
        elif choice == "page":
            self._open_project_page(release.page)

    def _start_update(self, release):
        """Laden, entpacken, Hilfsskript starten, beenden (im Hintergrund; Fortschritt in der Statuszeile)."""
        self._update_running = True
        self._set_buttons(tk.DISABLED)
        if hasattr(self.root, "wm_attributes"):
            try:
                self.root.wm_attributes("-alpha", 0.95)
            except tk.TclError:
                pass
        self.is_visible = True
        self.update_status("Update wird geladen … 0 %", "cyan")
        threading.Thread(target=self._update_install_worker, args=(release,), daemon=True,
                         name="update-install").start()

    def _update_install_worker(self, release):
        last = [0]

        def progress(done, total):
            percent = done * 100 // total
            step = 1 if total > 10 * 1024 * 1024 else 5
            if percent >= last[0] + step or done == total:
                last[0] = percent
                text = "Update wird entpackt …" if done == total else f"Update wird geladen … {percent} %"
                self._run_on_ui(self.update_status, text, "cyan")

        try:
            temp_root = tempfile.gettempdir()
            source = updater.prepare(release, temp_root, progress, app_dir=APP_DIR)
            script = updater.write_script(source, APP_DIR, os.getpid(), updater.EXE_NAME,
                                          updater.update_dir(temp_root, release.version))
            updater.launch(script)
        except Exception as e:  # Netz weg, zip kaputt, Platte voll …: nichts wird beendet
            append(LOG_FILE, f"[UPDATE] Update fehlgeschlagen: {type(e).__name__}: {e}")
            self._run_on_ui(self._on_update_failed, str(e) or type(e).__name__)
            return
        self._run_on_ui(self._on_update_launched, release.version)

    def _on_update_launched(self, version):
        dlog(f"[UPDATE] Update auf {version} gestartet – Importer beendet sich, das Skript kopiert und startet neu.")
        self.close()

    def _on_update_failed(self, error):
        self._update_running = False
        self._set_buttons(tk.NORMAL)
        self.update_status("Update fehlgeschlagen – Release-Seite über das Tray-Symbol", "red")
        show_message(self.root, "Update fehlgeschlagen",
                     f"{error}\n\nDie neue Version lässt sich von Hand laden: Tray-Symbol → Update.", kind="error")

    def _queue_toast(self, text):
        """Hinweis über der Taskleiste. Mehrere (Startmeldung, Update, Rückblick) kommen nacheinander, nicht übereinander
        (alle säßen an derselben Stelle)."""
        now = time.monotonic()
        start = max(now, self._toast_free_at)
        self._toast_free_at = start + (STARTED_TOAST_MS + TOAST_GAP_MS) / 1000
        self._toast_count += 1
        key = f"toast{self._toast_count}"
        self._after_ids[key] = self.root.after(int((start - now) * 1000), lambda: self._show_queued_toast(key, text))

    def _show_queued_toast(self, key, text):
        self._after_ids.pop(key, None)
        show_toast(self.root, text, duration_ms=STARTED_TOAST_MS)

    # --- RÜCKBLICK-HINWEIS (einmal pro neuem Monat) ---
    def _start_review_notice(self):
        """Gab es im Vormonat Duelle und wurde auf den Rückblick noch nicht hingewiesen? (Hintergrund-Thread)"""
        self._after_ids.pop("review_notice", None)
        threading.Thread(target=self._review_worker, daemon=True, name="review").start()

    def _review_worker(self):
        db = self._stats_db()
        if db is None:
            return
        try:
            month = season_review.pending_notice(db, self.config.get(season_review.NOTICE_KEY))
        except Exception as e:  # Datenbank gesperrt – dann eben nächstes Mal
            append(LOG_FILE, f"[RÜCKBLICK] Hinweis nicht geprüft: {e}")
            return
        if month:
            self._run_on_ui(self._on_review_ready, *month)

    def _on_review_ready(self, year, month):
        self.config[season_review.NOTICE_KEY] = season_review.month_key(year, month)
        self._save_config_safely()
        self._queue_toast(f"Rückblick für {season_review.MONTHS[month - 1]} ist fertig – Deck → Winrate → Rückblick")

    def _open_project_page(self, url=None):
        """Release-Seite im Browser (kein Download, keine Installation)."""
        try:
            webbrowser.open(url or update_check.RELEASES_URL)
        except Exception as e:
            dlog(f"[UPDATE] Browser nicht geöffnet: {e}")

    def _show_tray_menu(self, x: int, y: int):
        if self.tray:  # kann zwischen Rechtsklick und Tk-Thread beendet worden sein
            self.tray.show_menu(self.root, x, y)

    # --- FEHLERBERICHT (Tray-Menü) ---
    def _create_report(self):
        """ZIP mit Log, Einstellungen und Systeminfos in den Downloads-Ordner (im Hintergrund)."""
        if self._report_running:
            return
        self._report_running = True
        self.update_status("Fehlerbericht wird erstellt …", "cyan")
        db = self._stats_db()

        def work():
            try:
                result = (bug_report.create(db), "")
            except Exception as e:  # Ordner nicht beschreibbar, Platte voll …
                result = (None, str(e))
            self._run_on_ui(self._on_report_done, *result)

        threading.Thread(target=work, daemon=True, name="report").start()

    def _on_report_done(self, path, error):
        self._report_running = False
        if path is None:
            append(LOG_FILE, f"[BERICHT] Fehlerbericht nicht erstellt: {error}")
            self.update_status("Fehlerbericht fehlgeschlagen", "red")
            if self.tray:
                self.tray.notify(f"Fehlerbericht nicht erstellt: {error}", "MD Importer")
            return
        self.update_status("Fehlerbericht gespeichert", "#00ff00")
        if self.tray:
            self.tray.notify(f"Fehlerbericht gespeichert: {os.path.basename(path)} (Downloads)", "MD Importer")
        bug_report.reveal(path)

    def _backup_database(self):
        """Einmal am Tag die eigene Datenbank sichern (im Hintergrund; danach stündlich nachsehen, falls das
        Programm über Mitternacht läuft)."""
        import card_stats
        import db_backup
        threading.Thread(target=db_backup.backup, args=(card_stats.DB_FILE,), daemon=True, name="backup").start()
        self._after_ids["backup"] = self.root.after(db_backup.CHECK_INTERVAL_MS, self._backup_database)

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
            "SPEED_PROFILE": "normal",
            "DECK_HOVER_SCAN_ENABLED": True
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

        loaded, config_warnings = validate_config(loaded)
        for w in config_warnings:
            dlog(f"[CONFIG] {w}")
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
    @staticmethod
    def _is_md_active(fg_hwnd, fg_pid):
        """Ist das aktive Fenster Master Duel (Vollbild oder Fenster)? Nur über den Prozess – sonst erschien das
        Overlay auch über einem Browser mit einem Master-Duel-Video (Titel enthält "Master Duel")."""
        return is_md_process(fg_pid)

    def _check_window_focus(self):
        """Loop, der alle 300ms prüft, welches Fenster in Windows gerade vorne liegt."""
        self.hang_watch.beat()
        try:
            fg_hwnd = win32gui.GetForegroundWindow()
            if fg_hwnd:
                _, fg_pid = win32process.GetWindowThreadProcessId(fg_hwnd)
                my_pid = os.getpid()

                md_active = fg_pid != my_pid and self._is_md_active(fg_hwnd, fg_pid)
                # Editor-Prüfung nur mit Master Duel vorne – und nicht, solange ein eigenes Fenster (Mouseover,
                # Duell-Ansicht …) über der Überschrift "Main Deck" liegt: Sonst läse die OCR unser Fenster
                self.editor_watch.md_front = md_active
                self.editor_watch.paused = (bool(self.extras and self.extras.hover.visible)
                                            or (md_active and self._own_window_over_header()))
                self.match_watch.enabled = self._use_memory()
                self.replay_watch.enabled = self._use_memory()
                self._clear_timer_after_editor()
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

    def _own_window_over_header(self) -> bool:
        """Liegt ein eigenes, sichtbares Fenster (außer dem Overlay) über der Überschrift "Main Deck"?"""
        try:
            frame = md_layout.md_frame()
            if frame is None:
                return False
            r = frame.region(*md_layout.MAIN_HEADER_REGION)
            left, top, right, bottom = r["left"], r["top"], r["left"] + r["width"], r["top"] + r["height"]
            todo = list(self.root.winfo_children())
            while todo:
                w = todo.pop()
                todo.extend(w.winfo_children())
                if not isinstance(w, tk.Toplevel) or not w.winfo_viewable():
                    continue
                if float(w.wm_attributes("-alpha")) <= 0:
                    continue
                x, y = w.winfo_rootx(), w.winfo_rooty()
                if x < right and x + w.winfo_width() > left and y < bottom and y + w.winfo_height() > top:
                    return True
        except tk.TclError:
            pass
        return False

    def _clear_timer_after_editor(self) -> None:
        """Die Endzeit des letzten Imports/Exports bleibt stehen, bis man den Deck-Editor verlässt – beim nächsten
        Öffnen des Editors gehört sie zu nichts mehr."""
        if self.editor_watch.visible is False and not self.is_running and self._import_t0 is None:
            if self.timer_label.cget("text"):
                self.timer_label.config(text="")

    def _should_show(self, own_window_in_front: bool, md_in_front: bool) -> bool:
        """
        Sichtbar: ein Fenster des Importers ist vorne (z.B. gerade angeklickt), oder Master Duel ist vorne und
        zeigt den Deck-Editor (unbekannt bzw. nicht prüfbar zählt als ja) – während eines Imports immer, ebenso
        während eines Updates (sonst sähe man den Fortschritt nicht).
        """
        if own_window_in_front or self._update_running:
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
        self._set_read_method(choice)

    def _set_read_method(self, choice: str):
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
        if not self._memory_warned and md_memory.preload_failures() >= MEMORY_WARN_AFTER:
            self._memory_warned = True
            self._warn_memory_unavailable()
        self._memory_preload_at = time.monotonic()
        self._memory_thread = threading.Thread(target=md_memory.preload, daemon=True)
        self._memory_thread.start()

    def _warn_memory_unavailable(self, status: bool = True):
        """Der Speicher-Modus verbindet sich seit Minuten nicht (meist ein Master-Duel-Update): Der Import weicht
        still auf die Texterkennung aus, der Winrate-Tracker würde aber unbemerkt nichts mehr erfassen."""
        text = ("Speicher-Modus verbindet sich nicht (Master-Duel-Update?). Der Import nutzt die Texterkennung, "
                "Duelle werden gerade nicht für die Winrate erfasst.")
        dlog(f"[MEMORY] {text} Letzter Fehler: {md_memory.last_error()}")
        if status:  # nach einem Vorgang bleibt dessen Ergebnis ("Import Erfolgreich!") in der Statuszeile stehen
            self.update_status("Speicher-Modus nicht verfügbar", "#ffaa00")
        if self.tray:
            self.tray.notify(text, "MD Importer")
        self._run_on_ui(self._offer_ocr_when_idle)  # nicht im Fokus-Timer blockieren

    def _memory_fell_back(self):
        """Ein Import/Scan/Export musste auf die Texterkennung ausweichen: Hinweis, einmal pro Sitzung – nur,
        wenn die Verbindung fehlt (nicht schon, weil der Deck-Editor gerade zu ist)."""
        if not self._memory_warned and self._use_memory() and not md_memory.is_ready():
            self._memory_warned = True
            self._warn_memory_unavailable(status=False)

    def _offer_ocr_when_idle(self):
        """Popup "zur Texterkennung wechseln"; nicht mitten in einem Vorgang oder über einem anderen Fenster,
        sondern erst danach (alle UPDATE_RETRY_MS nachsehen)."""
        self._after_ids.pop("ocr_offer", None)
        if not self._use_memory():
            return  # inzwischen schon umgestellt
        if self.is_running or self.root.grab_current():
            self._after_ids["ocr_offer"] = self.root.after(UPDATE_RETRY_MS, self._offer_ocr_when_idle)
            return
        choice = ask_choice(
            self.root, "Speicher-Modus nicht verfügbar",
            "Der Speicher-Modus funktioniert gerade nicht, oft nach einem Master-Duel-Update. "
            "Du kannst zur Texterkennung wechseln.",
            [("Zur Texterkennung wechseln", "ocr", "#007acc"), ("Später", None, "#444444")], kind="warning")
        if choice:
            self._set_read_method(choice)

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
                try:
                    func(*args)
                except tk.TclError as err:
                    dlog(f"[UI] TclError in Queue abgefangen: {err}")
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
            subprocess.Popen(self._restart_command(), cwd=APP_DIR, env=single_instance.restart_env())
        except OSError as e:
            show_message(self.root, "Neustart fehlgeschlagen", str(e), kind="error")
            return
        self.close()

    def close(self):
        """Alles schließen: Tray-Icon, wiederkehrende Timer, Fenster."""
        self._close_extras()
        self.editor_watch.stop()
        self.hang_watch.stop()
        self.match_watch.stop()
        self.replay_watch.stop()
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
        if getattr(self, "_update_running", False) and not str(text).startswith("Update"):
            return
        self.status_label.config(text=text, fg=color)
        try:
            self.status_label.update_idletasks()
        except (tk.TclError, AttributeError):
            pass

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
        self._save_config_safely()
        self.update_status("Kalibrierung aktiv!", "#00ff00")
        self._set_buttons(tk.NORMAL)

    def start_import_thread(self, memory=None, card_ids=None, swap_label=None):
        """
        memory=True: Karten per ID aus dem Speicher lesen (nur lesend); None = wie in den Optionen eingestellt.
        card_ids + swap_label: Side-Tausch (side_profiles) – Ziel-Deck statt Zwischenablage, Deck wird nicht geleert
        (alles gilt als eingefügt), die Kontrolle am Ende entfernt bzw. sucht die getauschten Karten.
        """
        if memory is None:
            memory = self._use_memory()
        if not self.config.get("IS_CALIBRATED", False):
            # Erst automatisch versuchen und dann direkt importieren; klappt das nicht → Assistent
            self.start_auto_calibration(then=lambda: self.start_import_thread(memory, card_ids, swap_label))
            return

        if not self.is_running:
            if card_ids is not None:
                resume = {"done": dict(Counter(card_ids))}
            else:
                # Abgebrochener Import mit demselben Deck-Code? Dann Fortsetzen anbieten.
                try:
                    resume = resume_state.load_progress(parse_clipboard())
                except DeckCodeError:
                    resume = None  # Der Import meldet den beschädigten Code selbst
            if resume and card_ids is None:
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
            self._swap_label = swap_label
            self._close_extras()  # Import klickt in Deck und Kartenliste
            try:
                # Side-Tausch: nicht in den Verlauf (kein neues Deck, sonst landen Varianten in der Statistik)
                self._import_code = "" if card_ids is not None else pyperclip.paste() or ""
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
                if core.memory_fallback:
                    self._run_on_ui(self._memory_fell_back)

            def start_cb():
                self._run_on_ui(self._on_import_started)

            core = DeckImporterCore(self.config, TESSERACT_CMD, status_cb, finish_cb,
                                    start_callback=start_cb, resume=resume, memory=memory, card_ids=card_ids)
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
        swap, self._swap_label = self._swap_label, None
        if swap:
            self._on_swap_finished(swap, success, has_errors, failed_cards, message, notes)
            return
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

    def start_side_swap(self, card_ids, label):
        """Side-Profil anwenden bzw. zurücktauschen (aus dem Deck-Fenster): card_ids = Ziel-Deck."""
        if self.is_running:
            return
        self.update_status(f"Side-Tausch: {label}", "cyan")
        self.start_import_thread(card_ids=list(card_ids), swap_label=label)

    def _on_swap_finished(self, label, success, has_errors, failed_cards, message, notes):
        if success and not (failed_cards or has_errors):
            self.update_status(f"Getauscht: {label}", "#00ff00")
        elif success:
            self.update_status("Tausch mit Lücken – bitte prüfen", "#ffaa00")
            show_message(self.root, "Side-Tausch – bitte prüfen",
                         f"„{label}“ ist durchgelaufen, aber bei diesen Karten stimmt die Anzahl nicht oder ist "
                         "unsicher. Bitte im Deck prüfen und von Hand korrigieren:",
                         kind="warning", items=failed_cards, notes=notes)
        else:
            self.update_status("Side-Tausch abgebrochen", "red")
            if message:
                show_message(self.root, "Side-Tausch abgebrochen", message, kind="error")

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
            if exporter.memory_fallback:
                self._run_on_ui(self._memory_fell_back)

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
            # Kurz für die Statuszeile (schmal), ausführlich für die Tray-Meldung
            match = new[0]
            short = [{WIN: "Sieg", LOSS: "Niederlage"}.get(match.result, "Unentschieden")]
            text = short[0]
            if match.first is not None:
                short.append("Erster" if match.first else "Zweiter")
                text += " als " + short[-1]
            if match.coin is not None:
                short.append("Münze " + ("gewonnen" if match.coin else "verloren"))
                text += f" (Münzwurf {'gewonnen' if match.coin else 'verloren'})"
            day = self._day_summary()
            if match.md_deck:
                deck = match.md_deck
                if not day:  # Tagesbilanz hat Vorrang, der Deckname steht in der Tray-Meldung
                    short.append(deck if len(deck) <= STATUS_DECK_CHARS else deck[:STATUS_DECK_CHARS - 1] + "…")
                text += f" mit {deck}"
            status, text = " · ".join(short + [day] if day else short), text + " erfasst"
        else:
            wins = sum(1 for m in new if m.result == WIN)
            losses = sum(1 for m in new if m.result == LOSS)
            day = self._day_summary()
            status = f"{len(new)} Matches erfasst" + (f" · {day}" if day else "")
            text = f"{len(new)} Matches erfasst: {wins} Sieg(e), {losses} Niederlage(n)"
        self.update_status(status, "#00ff00")
        if self.tray:
            self.tray.notify(text + " – Winrate im Deck-Fenster", "MD Importer")
        reminder = self._history_reminder()
        if reminder:
            if self.tray:
                self.tray.notify(reminder, "MD Importer")
            self._after_ids["reminder"] = self.root.after(REMINDER_DELAY_MS, self._show_history_reminder)
        if self.extras:
            self.extras.refresh_winrate()

    def _on_replay_saved(self, info, steps):
        """Replay mitgeschnitten → kurze Meldung (ansehen: Winrate → Match anklicken)."""
        opponent = next((n for i, n in enumerate(info.names) if i != info.my_id), "")
        self.update_status(f"Replay aufgenommen ({steps} Schritte)", "#00ff00")
        if self.tray:
            self.tray.notify(f"Replay{' gegen ' + opponent if opponent else ''} aufgenommen ({steps} Schritte) – "
                             f"ansehen: Deck-Fenster → Winrate → Match anklicken", "MD Importer")
        if self.extras:
            self.extras.refresh_winrate()

    def _show_history_reminder(self):
        """Hinweis in der Statuszeile – nur, solange er stimmt: Wurde die Match History inzwischen geöffnet, kommt er
        gar nicht erst bzw. verschwindet wieder (vorher blieb er stehen, bis zufällig etwas anderes kam)."""
        if not self._unconfirmed_matches():
            return
        self.update_status(SHORT_REMINDER, "#ffaa00")
        self._after_ids["reminder"] = self.root.after(REMINDER_CHECK_MS, self._check_history_reminder)

    def _check_history_reminder(self):
        if self.status_label.cget("text") != SHORT_REMINDER:
            return  # inzwischen steht etwas anderes da
        if self._unconfirmed_matches():
            self._after_ids["reminder"] = self.root.after(REMINDER_CHECK_MS, self._check_history_reminder)
            return
        day = self._day_summary()
        self.update_status("Match History übernommen" + (f" · {day}" if day else ""), "#00ff00")

    def _unconfirmed_matches(self) -> int:
        """Duelle ohne Gegner-Deck seit dem letzten Blick in die Match History (Fehler: 1 = Hinweis bleibt)."""
        db = self._stats_db()
        try:
            return db.unconfirmed_matches() if db is not None else 0
        except Exception:  # Datenbank gerade gesperrt → später nochmal
            return 1

    def _history_reminder(self) -> str:
        """Hinweis, die Match History zu öffnen (sonst fehlen Gegner-Decks), "" wenn nicht nötig."""
        from match_history import history_reminder
        db = self._stats_db()
        try:
            return history_reminder(db.unconfirmed_matches()) if db is not None else ""
        except Exception:  # Datenbank gerade gesperrt – nur Zusatz
            return ""

    def _day_summary(self) -> str:
        """'Heute 5–2' (Modus wie im Winrate-Fenster: Ranked bzw. alle); "" ohne Daten."""
        from winrate_panel import MODE_KEY, day_summary  # erst hier: zieht die Deck-Fenster-Module nach
        db = self._stats_db()
        if db is None:
            return ""
        try:
            return day_summary(db, None if self.config.get(MODE_KEY) == "all" else RANKED)
        except Exception:  # Datenbank gerade gesperrt – nur Zusatz
            return ""

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
                                  on_import_code=self._import_from_history, on_copy_code=self._copy_from_history,
                                  on_side_swap=self.start_side_swap)
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
            if exporter.memory_fallback:
                self._run_on_ui(self._memory_fell_back)

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


def started_text() -> str:
    return f"MD-Importer {APP_VERSION} läuft jetzt."


def report_tk_error(exc, value, tb) -> None:
    """Fehler in Tk-Callbacks ins Log (pythonw hat kein stderr); das Programm läuft weiter."""
    append(LOG_FILE, "[TK-FEHLER]\n" + "".join(traceback.format_exception(exc, value, tb)))


def main():
    if not single_instance.acquire():
        # Schon ein Importer offen: kein zweites Overlay (jedes Duell würde doppelt erfasst), nur kurz Bescheid geben
        root = tk.Tk()
        root.withdraw()
        show_toast(root, started_text(), duration_ms=STARTED_TOAST_MS)
        root.after(STARTED_TOAST_MS + 100, root.destroy)
        root.mainloop()
        return
    root = tk.Tk()
    root.report_callback_exception = report_tk_error
    MasterDuelImporter(root, tray=True)
    show_toast(root, started_text(), duration_ms=STARTED_TOAST_MS)
    root.mainloop()


if __name__ == "__main__":
    main()