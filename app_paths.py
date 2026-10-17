"""
Zentrale Pfade und Version der App.

Alle Dateien liegen neben dem Programm, nicht im aktuellen Arbeitsverzeichnis. Sonst
landen Config, Cache und Log je nach Startart woanders, und ein fremdes
'Tesseract-OCR\\tesseract.exe' im Arbeitsverzeichnis könnte ausgeführt werden.
Ist der Programmordner schreibgeschützt (z.B. unter C:\\Programme), liegen Einstellungen, Datenbank und
Logs stattdessen in %APPDATA%\\MD_Importer (DATA_DIR).
"""

import os
import sys

APP_VERSION = "9.6.1"


def _app_dir() -> str:
    # Als PyInstaller-EXE: Ordner der .exe. Aus dem Quellcode: Ordner dieser Datei.
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _writable(folder: str) -> bool:
    probe = os.path.join(folder, f".md_write_test_{os.getpid()}")
    try:
        with open(probe, "w"):
            pass
    except OSError:
        return False
    try:
        os.remove(probe)
    except OSError:
        pass  # sonst wechselte ein gesperrter Probe-Datei-Löschversuch die Daten nach %APPDATA% (Config scheinbar weg)
    return True


def _data_dir(app_dir: str) -> str:
    """Ordner für Einstellungen, Datenbank und Logs: neben dem Programm wie bisher; ist der schreibgeschützt,
    in %APPDATA%\\MD_Importer (sonst scheitert schon das erste Speichern beim Start)."""
    if _writable(app_dir):
        return app_dir
    fallback = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"), "MD_Importer")
    try:
        os.makedirs(fallback, exist_ok=True)
    except OSError:
        return app_dir
    return fallback


APP_DIR = _app_dir()
DATA_DIR = _data_dir(APP_DIR)
# Mitgelieferte Daten (Tesseract) liegen in der EXE im Entpack-Ordner (_MEIPASS)
BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)

CONFIG_FILE = os.path.join(DATA_DIR, "md_config.json")
CACHE_FILE = os.path.join(DATA_DIR, "md_card_cache.json")
LOG_FILE = os.path.join(DATA_DIR, "md_debug.log")
ARCHETYPES_FILE = os.path.join(DATA_DIR, "archetypes_config.json")
RESUME_FILE = os.path.join(DATA_DIR, "md_resume.json")

TESSERACT_DIR = os.path.join(BUNDLE_DIR, "Tesseract-OCR")
TESSERACT_CMD = os.path.join(TESSERACT_DIR, "tesseract.exe")
