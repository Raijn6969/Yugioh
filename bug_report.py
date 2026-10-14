"""
Fehlerbericht (Tray-Menü → "Fehlerbericht erstellen"): eine ZIP-Datei im Downloads-Ordner, die man bei einem Problem
verschicken kann.

Inhalt: das Log (md_debug.log und die rotierten md_debug.1.log … bzw. md_startup_error.log, falls vorhanden), die
Einstellungen (md_config.json) und info.txt (Version, Windows, Python, Bildschirm, Datenordner). Nie dabei: Datenbanken,
Sicherungen und Replays.

Gegnernamen: Ältere Logs enthalten beim Mitschnitt eines Replays die Spielernamen ("[REPLAY] Mitschnitt gestartet
(A vs B …"; neuere schreiben sie nicht mehr). Die Namen der Gegner, die in den gespeicherten Replays vorkommen,
werden im Log-Text durch "<Gegner>" ersetzt. (Die "Gegner-Decks" der Matches sind Deck-Namen aus den Archetypen,
keine Spielernamen – die bleiben, sonst wären die Kartennamen im Log unleserlich.)

Benutzerordner: der Windows-Benutzerordner (Logs, Tracebacks, Datenordner) wird überall durch %USERPROFILE% ersetzt (hide_user).
"""

import ctypes
import glob
import os
import platform
import re
import subprocess
import sys
import time
import zipfile
from typing import Iterable, List, Optional, Tuple

from app_paths import APP_DIR, APP_VERSION, CONFIG_FILE, DATA_DIR, LOG_FILE
from deck_export import downloads_dir

REDACTED = "<Gegner>"
MIN_NAME_LENGTH = 3            # kürzere Namen würden zu viel im Log treffen ("Ash", "Al" …) und bleiben stehen
MAX_LOG_BYTES = 8 * 1024 * 1024  # größere Logs: nur das Ende (das Neueste ist meist das Wichtige)
STARTUP_ERROR_LOG = os.path.join(APP_DIR, "md_startup_error.log")  # schreibt MD_Importer.pyw (Fehler beim Start)


def redact(text: str, names: Iterable[str]) -> Tuple[str, int]:
    """Gegnernamen als ganze Wörter (Groß-/Kleinschreibung wie gespeichert) durch REDACTED ersetzen.
    Returns: (Text, Anzahl der Ersetzungen)."""
    total = 0
    for name in sorted({n.strip() for n in names if isinstance(n, str)}, key=len, reverse=True):
        if len(name) < MIN_NAME_LENGTH:
            continue
        text, count = re.subn(rf"(?<!\w){re.escape(name)}(?!\w)", REDACTED, text)
        total += count
    return text, total


def hide_user(text: str, home: str = os.path.expanduser("~")) -> str:
    """Den Windows-Benutzerordner (mit \\ oder /, auch in JSON doppelt geschrieben, Groß-/Kleinschreibung egal)
    durch %USERPROFILE% ersetzen. Leerer/ungültiger home (ohne Ordnertrennzeichen) → Text unverändert."""
    parts = [p for p in re.split(r"[\\/]+", home or "") if p]
    if len(parts) < 2:  # "~" (nicht aufgelöst) oder nur ein Laufwerk: zu ungenau
        return text
    pattern = r"[\\/]+".join(re.escape(p) for p in parts)
    return re.sub(rf"(?<!\w){pattern}(?!\w)", lambda m: "%USERPROFILE%", text, flags=re.IGNORECASE)


def read_text(path: str, limit: int = MAX_LOG_BYTES) -> str:
    """Datei als Text (bei großen Dateien nur das Ende, ab einer ganzen Zeile)."""
    with open(path, "rb") as f:
        size = os.fstat(f.fileno()).st_size
        cut = size > limit
        if cut:
            f.seek(size - limit)
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    if cut:
        text = f"[... Anfang gekürzt, nur die letzten {limit // 1024 // 1024} MB ...]\n" + text.split("\n", 1)[-1]
    return text


def log_files(log_file: str = LOG_FILE, startup_error: Optional[str] = STARTUP_ERROR_LOG) -> List[str]:
    """Die vorhandenen Logs: aktuelles, rotierte (md_debug.1.log …) und der Startfehler."""
    base, ext = os.path.splitext(log_file)
    found = [log_file] if os.path.exists(log_file) else []
    found += sorted(glob.glob(f"{glob.escape(base)}.*{ext}"))
    if startup_error and os.path.exists(startup_error):
        found.append(startup_error)
    return found


def _screen_text() -> str:
    parts = []
    try:
        import win_api
        _, _, width, height = win_api.virtual_screen_rect()
        parts.append(f"Desktop {width}×{height} (alle Monitore)")
    except Exception:
        parts.append("Desktop: nicht lesbar")
    try:
        dpi = ctypes.windll.user32.GetDpiForSystem()
        parts.append(f"DPI {dpi} ({round(dpi * 100 / 96)} %)")
    except Exception:
        parts.append("DPI: nicht lesbar")
    return ", ".join(parts)


def system_info(now: Optional[float] = None, redacted: Optional[Tuple[int, int]] = None,
                included: Iterable[str] = (), data_dir: str = DATA_DIR) -> str:
    """Inhalt von info.txt. redacted: (bekannte Gegnernamen, Ersetzungen) für den Hinweis zur Schwärzung."""
    now = time.time() if now is None else now
    lines = ["MD-Importer Fehlerbericht",
             f"Erstellt: {time.strftime('%d.%m.%Y %H:%M:%S', time.localtime(now))}",
             f"App-Version: {APP_VERSION}",
             f"Windows: {platform.platform()} (Build {platform.version()})",
             f"Python: {platform.python_version()} ({platform.architecture()[0]}), "
             f"als exe: {'ja' if getattr(sys, 'frozen', False) else 'nein (Quellcode)'}",
             f"Bildschirm: {_screen_text()}",
             f"Datenordner: {data_dir}",
             "",
             "Enthalten: " + ", ".join(included),
             "Nicht enthalten: Datenbanken, Sicherungen, Replays.",
             "Der Windows-Benutzerordner ist überall als %USERPROFILE% ersetzt."]
    if redacted is not None:
        names, count = redacted
        lines.append(f"Gegnernamen: {names} bekannte Name(n) aus den Replays, {count}× im Text durch "
                     f"\"{REDACTED}\" ersetzt (Namen unter {MIN_NAME_LENGTH} Zeichen bleiben stehen).")
    return "\n".join(lines) + "\n"


def create(db=None, folder: Optional[str] = None, now: Optional[float] = None, log_file: str = LOG_FILE,
           config_file: str = CONFIG_FILE, startup_error: Optional[str] = STARTUP_ERROR_LOG,
           data_dir: str = DATA_DIR, home: str = os.path.expanduser("~")) -> str:
    """ZIP schreiben (Downloads-Ordner, nichts überschreiben). db: Karten-Datenbank für die Gegnernamen (None =
    keine Schwärzung). Returns: Pfad."""
    now = time.time() if now is None else now
    names = []
    if db is not None:
        try:
            names = list(db.opponent_names())
        except Exception:
            names = []  # Datenbank gesperrt/alt: Bericht trotzdem erstellen
    entries, replaced = [], 0
    for path in log_files(log_file, startup_error):
        try:
            text, count = redact(hide_user(read_text(path), home), names)
        except OSError:
            continue  # Log ist gerade gesperrt
        entries.append((os.path.basename(path), text))
        replaced += count
    if os.path.exists(config_file):
        try:
            text, count = redact(hide_user(read_text(config_file), home), names)
            entries.append((os.path.basename(config_file), text))
            replaced += count
        except OSError:
            pass
    info = hide_user(system_info(now, (len(names), replaced), [name for name, _ in entries] + ["info.txt"], data_dir),
                     home)

    folder = folder or downloads_dir()
    os.makedirs(folder, exist_ok=True)
    base = f"MD-Importer_Fehlerbericht_{time.strftime('%Y-%m-%d_%H%M%S', time.localtime(now))}"
    path, n = os.path.join(folder, base + ".zip"), 2
    while os.path.exists(path):
        path, n = os.path.join(folder, f"{base}_{n}.zip"), n + 1
    archive = zipfile.ZipFile(path, "x", zipfile.ZIP_DEFLATED)  # "x": überschreibt nie
    try:
        with archive:
            archive.writestr("info.txt", info)
            for name, text in entries:
                archive.writestr(name, text)
    except BaseException:
        os.remove(path)  # halbe ZIP nicht liegen lassen
        raise
    return path


def reveal(path: str) -> None:
    """Explorer mit der Datei markiert öffnen."""
    try:
        subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
    except OSError:
        pass
