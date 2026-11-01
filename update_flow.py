"""
Update-Ablauf des Importers (aus Overlay.py herausgelöst, damit er mit kleinen Fakes testbar ist).

- Prüfung beim Start im Hintergrund, nur als exe (aus dem Quellcode holt man Updates mit git).
- Die Frage kommt nur einmal je Version, nie mitten im Duell bzw. mit Master Duel vorne und nie während eines
  Imports/Exports. Läuft Master Duel schon beim Start, wartet sie bis zum nächsten Start (Knopf/Tray fragen sofort).
- Optionen → „Nach Update suchen“ prüft sofort; „Nach Duell auf Update prüfen“ sieht nach jedem Duell nach
  (höchstens alle 30 Minuten, nur im Speicher-Modus).
- Ein-Klick-Update: laden, entpacken, Hilfsskript starten, beenden. Ein unvollständiges Update meldet das nächste
  Programm beim Start (update_failed.txt).
"""

import os
import sys
import tempfile
import threading
import time
import tkinter as tk
import webbrowser

import update_check
import updater
from app_paths import APP_DIR, APP_VERSION, DATA_DIR, LOG_FILE
from dark_dialog import ask_choice, show_message
from debug_log import append, dlog
from extras_panel import READ_METHOD_KEY, UPDATE_AFTER_DUEL_KEY
from window_automation import find_md_window
from window_style import BAD, GOOD, INFO, NEUTRAL, PRIMARY

UPDATE_NOTIFIED_KEY = "UPDATE_NOTIFIED"  # Einstellung: zuletzt gemeldete neuere Version (Frage nur einmal je Version)
UPDATE_RETRY_MS = 1000  # läuft ein Import, wird die Update-Frage so lange verschoben
UPDATE_AFTER_DUEL_S = 30 * 60  # „Nach Duell auf Update prüfen“: höchstens so oft bei GitHub nachsehen


def update_after_duel(config: dict) -> bool:
    """Option „Nach Duell auf Update prüfen“ – gilt nur im Speicher-Modus (nur dann ist bekannt, ob ein Duell läuft;
    mit Texterkennung wird nur beim Start geprüft)."""
    return bool(config.get(UPDATE_AFTER_DUEL_KEY)) and config.get(READ_METHOD_KEY) == "memory"


class UpdateFlow:
    def __init__(self, *, root, get_config, save_config, status, run_on_ui, after_ids, get_tray, busy, md_in_front,
                 md_retry_ms, in_duel, set_buttons, show_overlay, close):
        self.root = root              # für after() und als Elternfenster der Dialoge
        self.get_config = get_config  # liefert das aktuelle Einstellungen-dict (wird nach einer Kalibrierung ersetzt)
        self.save_config = save_config
        self.status = status          # status(text, color): Statuszeile des Overlays
        self.run_on_ui = run_on_ui    # aus Hintergrund-Threads in den Tk-Thread
        self.after_ids = after_ids    # Timer-IDs des Overlays (Overlay.close bricht sie ab)
        self.get_tray = get_tray      # Tray oder None (entsteht erst nach dem UpdateFlow)
        self.busy = busy              # läuft ein Import/Export?
        self.md_in_front = md_in_front
        self.md_retry_ms = md_retry_ms  # so lange warten, bis Master Duel nicht mehr vorne liegt
        self.in_duel = in_duel        # True/False, None = unbekannt (keine Speicher-Verbindung)
        self.set_buttons = set_buttons
        self.show_overlay = show_overlay  # Overlay zeigen: der Fortschritt soll sichtbar sein
        self.close = close
        self.release = None   # neuerer Release laut Update-Prüfung (None = keiner bzw. nicht geprüft)
        self.running = False  # Update wird gerade geladen/installiert
        self.checked_at = time.monotonic()  # letzte Update-Prüfung (die beim Start)

    def start_check(self):
        """Gibt es eine neuere Version? Nur als exe (aus dem Quellcode holt man sich Updates mit git selbst)."""
        if not getattr(sys, "frozen", False):
            dlog("[UPDATE] Lauf aus dem Quellcode – keine Update-Prüfung.")
            return
        threading.Thread(target=self._worker, daemon=True, name="update").start()

    def check_now(self):
        """Optionen → „Nach Update suchen“: sofort prüfen und fragen (auch aus dem Quellcode – dort nur der Hinweis)."""
        self.status("Suche nach Update …", INFO)
        threading.Thread(target=self._worker, args=(True,), daemon=True, name="update").start()

    def _worker(self, manual=False):
        updater.cleanup_old(tempfile.gettempdir())  # Reste eines früheren Updates
        try:
            release = update_check.find_update(APP_VERSION, log=lambda message: append(LOG_FILE, message),
                                               raise_errors=manual)
        except Exception:  # nur beim Knopf: sonst hieße es fälschlich „kein Update“
            self.run_on_ui(self.status, "Update-Prüfung nicht möglich (keine Verbindung?)", BAD)
            return
        if release:
            self.run_on_ui(self.on_found, release, manual)
        elif manual:
            self.run_on_ui(self.status, f"Kein Update gefunden (installiert: {APP_VERSION})", GOOD)

    def note_release(self, release):
        """Release aus der Speicher-Prüfung merken (falls noch keiner bekannt) und dem Tray melden."""
        if release is not None and self.release is None:
            self.release = release
            tray = self.get_tray()
            if tray:
                tray.set_update(release.version)

    def on_found(self, release, manual=False):
        self.release = release
        tray = self.get_tray()
        if tray:
            tray.set_update(release.version)
        if manual:
            self.ask_when_idle()
            return
        config = self.get_config()
        if not update_after_duel(config) and find_md_window() is not None:
            # Das Spiel läuft schon: in dieser Sitzung nicht fragen (mitten im Spielen stört es), erst beim nächsten
            # Start – oder über Optionen/Tray. Deshalb auch nicht als „gefragt“ merken. Mit „Nach Duell auf Update
            # prüfen“ kommt die Frage dagegen, sobald kein Duell mehr läuft.
            dlog(f"[UPDATE] {release.version} verfügbar – Master Duel läuft, Frage beim nächsten Start.")
            self.status(f"Update {release.version} verfügbar – Optionen → Nach Update suchen", INFO)
            return
        notified = config.get(UPDATE_NOTIFIED_KEY)
        if notified and not update_check.is_newer(release.version, notified):
            return  # zu dieser Version schon gefragt – der Tray-Menüpunkt bleibt
        config[UPDATE_NOTIFIED_KEY] = release.version
        self.save_config()
        self.ask_when_idle(user=False)

    def can_self_update(self, release) -> bool:
        """Ein-Klick-Update nur als exe, mit zip am Release und beschreibbarem Programmordner."""
        return bool(getattr(sys, "frozen", False) and release.zip_url and DATA_DIR == APP_DIR)

    def ask_when_idle(self, user: bool = True):
        """Update-Frage; läuft gerade ein Import/Export, erst danach (alle UPDATE_RETRY_MS nachsehen). Kommt sie
        ohne Zutun des Nutzers (user=False, nach der Update-Prüfung), auch erst, wenn Master Duel nicht vorne liegt –
        mit „Nach Duell auf Update prüfen“ schon, sobald kein Duell mehr läuft."""
        self.after_ids.pop("update_ask", None)
        if self.release is None or self.running:
            return
        in_duel = self.in_duel() if update_after_duel(self.get_config()) else None
        # ohne Speicher-Verbindung (z. B. kurz nach dem Start) ist unbekannt, ob ein Duell läuft
        blocked = self.md_in_front() if in_duel is None else in_duel
        if self.busy() or (not user and blocked):
            self.after_ids["update_ask"] = self.root.after(UPDATE_RETRY_MS, lambda: self.ask_when_idle(user))
            return
        release = self.release
        text = f"MD-Importer {release.version} ist da (installiert: {APP_VERSION}). "
        if self.can_self_update(release):
            choices = [("Jetzt aktualisieren", "install", PRIMARY), ("Was ist neu?", "page", NEUTRAL),
                       ("Später", None, NEUTRAL)]
            text += "Der Importer lädt die neue Version, ersetzt sich selbst und startet neu – deine Daten bleiben."
        else:
            choices = [("Release-Seite öffnen", "page", PRIMARY), ("Später", None, NEUTRAL)]
            text += "Neue Version auf der Release-Seite herunterladen."
        choice = ask_choice(self.root, "Update verfügbar", text, choices)
        if choice == "install":
            self.start(release)
        elif choice == "page":
            self.open_page(release.page)

    def start(self, release):
        """Laden, entpacken, Hilfsskript starten, beenden (im Hintergrund; Fortschritt in der Statuszeile)."""
        self.running = True
        self.set_buttons(tk.DISABLED)
        self.show_overlay()
        self.status("Update wird geladen … 0 %", INFO)
        threading.Thread(target=self._install_worker, args=(release,), daemon=True, name="update-install").start()

    def _install_worker(self, release):
        last = [0]

        def progress(done, total):
            percent = done * 100 // total
            step = 1 if total > 10 * 1024 * 1024 else 5
            if percent >= last[0] + step or done == total:
                last[0] = percent
                text = "Update wird entpackt …" if done == total else f"Update wird geladen … {percent} %"
                self.run_on_ui(self.status, text, INFO)

        try:
            temp_root = tempfile.gettempdir()
            source = updater.prepare(release, temp_root, progress, app_dir=APP_DIR)
            script = updater.write_script(source, APP_DIR, os.getpid(), updater.EXE_NAME,
                                          updater.update_dir(temp_root, release.version))
            updater.launch(script)
        except Exception as e:  # Netz weg, zip kaputt, Platte voll …: nichts wird beendet
            append(LOG_FILE, f"[UPDATE] Update fehlgeschlagen: {type(e).__name__}: {e}")
            self.run_on_ui(self._on_failed, str(e) or type(e).__name__)
            return
        self.run_on_ui(self._on_launched, release.version)

    def _on_launched(self, version):
        dlog(f"[UPDATE] Update auf {version} gestartet – Importer beendet sich, das Skript kopiert und startet neu.")
        self.close()

    def _on_failed(self, error):
        self.running = False
        self.set_buttons(tk.NORMAL)
        self.status("Update fehlgeschlagen – Release-Seite über das Tray-Symbol", BAD)
        show_message(self.root, "Update fehlgeschlagen",
                     f"{error}\n\nDie neue Version lässt sich von Hand laden: Tray-Symbol → Update.", kind="error")

    def show_failed_marker(self):
        """Das Update-Skript konnte nicht alles kopieren (update_failed.txt im Programmordner): einmal Bescheid
        geben."""
        if self.md_in_front():
            self.after_ids["update_failed"] = self.root.after(self.md_retry_ms, self.show_failed_marker)
            return
        code = updater.take_failed_marker(APP_DIR)
        if code is None:
            return
        dlog(f"[UPDATE] Letztes Update unvollständig ({code}).")
        show_message(self.root, "Update unvollständig",
                     "Update unvollständig (Datei gesperrt). Bitte die zip von GitHub neu laden und entpacken.\n"
                     f"({code})", kind="warning", wait=False,
                     actions=[("Release-Seite öffnen", self.open_page)])

    def open_page(self, url=None):
        """Release-Seite im Browser (kein Download, keine Installation)."""
        try:
            webbrowser.open(url or update_check.RELEASES_URL)
        except Exception as e:
            dlog(f"[UPDATE] Browser nicht geöffnet: {e}")

    def check_after_duel(self):
        """Option „Nach Duell auf Update prüfen“: nach einem Duell erneut nachsehen (höchstens alle
        UPDATE_AFTER_DUEL_S, nur als exe); die Frage kommt dann außerhalb des Duells (ask_when_idle)."""
        if not (update_after_duel(self.get_config()) and getattr(sys, "frozen", False)):
            return
        if self.release is not None:   # beim Start gefunden, aber zurückgestellt (Option erst später an)
            self.on_found(self.release)   # fragt nur, wenn zu dieser Version noch nicht gefragt
            return
        if time.monotonic() - self.checked_at < UPDATE_AFTER_DUEL_S:
            return
        self.checked_at = time.monotonic()
        threading.Thread(target=self._worker, daemon=True, name="update").start()
