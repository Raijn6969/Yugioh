"""
Zentrale Pfade und Version der App.

Alle Dateien liegen neben dem Programm, nicht im aktuellen Arbeitsverzeichnis. Sonst
landen Config, Cache und Log je nach Startart woanders, und ein fremdes
'Tesseract-OCR\\tesseract.exe' im Arbeitsverzeichnis könnte ausgeführt werden.
"""

import os
import sys

APP_VERSION = "9.0"


def _app_dir() -> str:
    # Als PyInstaller-EXE: Ordner der .exe. Aus dem Quellcode: Ordner dieser Datei.
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


APP_DIR = _app_dir()
# Mitgelieferte Daten (Tesseract) liegen in der EXE im Entpack-Ordner (_MEIPASS)
BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)

CONFIG_FILE = os.path.join(APP_DIR, "md_config.json")
CACHE_FILE = os.path.join(APP_DIR, "md_card_cache.json")
LOG_FILE = os.path.join(APP_DIR, "md_debug.log")
ARCHETYPES_FILE = os.path.join(APP_DIR, "archetypes_config.json")
RESUME_FILE = os.path.join(APP_DIR, "md_resume.json")

TESSERACT_DIR = os.path.join(BUNDLE_DIR, "Tesseract-OCR")
TESSERACT_CMD = os.path.join(TESSERACT_DIR, "tesseract.exe")
TESSDATA_DIR = os.path.join(TESSERACT_DIR, "tessdata")
