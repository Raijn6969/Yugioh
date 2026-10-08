"""
Nur ein Importer gleichzeitig: Zwei laufende Importer zeigen zwei Overlays und würden jedes Duell doppelt erfassen.

Ein benannter Windows-Mutex gehört dem laufenden Importer (Windows gibt ihn beim Beenden von selbst frei, auch nach
einem Absturz). Beim Neustart (Tray → Neustart) startet die neue Instanz, während die alte noch schließt – sie wartet
deshalb kurz, bis der Mutex frei ist (Umgebungsvariable RESTART_ENV).
"""

import ctypes
import os
import time
from typing import Optional

MUTEX_NAME = "Local\\MD_Importer_single_instance"
RESTART_ENV = "MD_IMPORTER_RESTART"  # gesetzt beim Neustart → auf das Ende der alten Instanz warten
RESTART_WAIT = 15.0                  # Sekunden
ERROR_ALREADY_EXISTS = 183

_handle = None  # bleibt offen, solange das Programm läuft


def _create(name: str):
    """Returns: Handle, wenn der Mutex neu angelegt wurde; None, wenn ihn schon ein anderer Prozess hat."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return 0  # Mutex nicht anlegbar (sollte nie vorkommen) → Start nicht verhindern
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(ctypes.c_void_p(handle))
        return None
    return handle


def acquire(name: str = MUTEX_NAME, wait: Optional[float] = None) -> bool:
    """
    Diesen Prozess als den laufenden Importer eintragen. wait: so lange warten, bis ein anderer frei gibt
    (Standard: beim Neustart RESTART_WAIT, sonst gar nicht). Returns: False, wenn schon ein Importer läuft.
    """
    global _handle
    if os.name != "nt":
        return True
    if wait is None:
        wait = RESTART_WAIT if os.environ.pop(RESTART_ENV, None) else 0.0
    end = time.monotonic() + wait
    while True:
        handle = _create(name)
        if handle is not None:
            _handle = handle
            return True
        if time.monotonic() >= end:
            return False
        time.sleep(0.2)


def release() -> None:
    """Mutex sofort freigeben (beim Neustart, damit die neue Instanz nicht warten muss)."""
    global _handle
    if _handle:
        ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(_handle))
    _handle = None


def restart_env() -> dict:
    """Umgebung für den Neustart: die neue Instanz wartet auf das Ende dieser."""
    return {**os.environ, RESTART_ENV: "1"}
