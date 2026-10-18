"""
"Mit Windows starten": Eintrag unter HKCU\\...\\Run (nur der eigene Benutzer, keine Admin-Rechte).
Quelle der Wahrheit ist die Registry selbst: an = Eintrag existiert und zeigt auf diese Installation.
Das Ein-Klick-Update kopiert in denselben Ordner, der Pfad bleibt also nach einem Update gültig.
"""

import os
import sys
import winreg
from typing import Optional

from app_paths import APP_DIR
from debug_log import dlog

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"  # Tests biegen es auf einen Test-Schlüssel um
VALUE_NAME = "MD Importer"


def command() -> str:
    """Startbefehl dieser Installation: die exe, aus dem Quellcode pythonw.exe mit MD_Importer.pyw."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return f'"{pythonw}" "{os.path.join(APP_DIR, "MD_Importer.pyw")}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
    except OSError:
        return False
    return str(value).lower() == command().lower()


def set_enabled(on: bool) -> Optional[str]:
    """Eintrag setzen bzw. entfernen. Zurück: None bei Erfolg, sonst die Fehlermeldung (auch im Log)."""
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            if on:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
    except OSError as e:
        dlog(f"Autostart: {'Eintragen' if on else 'Entfernen'} fehlgeschlagen: {e}")
        return f"Mit Windows starten nicht möglich: {e}"
    return None
