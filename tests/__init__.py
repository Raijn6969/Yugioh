"""
Tests für den Master Duel Deck Importer.

Ausführen (im Projektordner):
    .venv\\Scripts\\python.exe -m unittest discover -s tests -t .

tests/data/golden.json enthält die Ergebnisse des Codes VOR dem Refactoring (echte Fälle aus
md_debug.log). Die Tests stellen sicher, dass Umbauten am Verhalten nichts ändern.
Testfenster öffnen sich auf einem unsichtbaren Desktop; MD_TEST_SHOW=1 zeigt sie.
"""

import ctypes
import os
import sys

# Testfenster auf einem unsichtbaren Windows-Desktop: nichts ploppt auf, der echte Mauszeiger stört keine Tests
if sys.platform == "win32" and not os.environ.get("MD_TEST_SHOW"):
    _user32 = ctypes.WinDLL("user32")
    _user32.CreateDesktopW.restype = ctypes.c_void_p
    _user32.CreateDesktopW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong,
                                       ctypes.c_ulong, ctypes.c_void_p]
    _user32.SetThreadDesktop.argtypes = [ctypes.c_void_p]
    _desktop = _user32.CreateDesktopW("md_importer_tests", None, None, 0, 0x10000000, None)  # GENERIC_ALL
    if _desktop:
        _user32.SetThreadDesktop(_desktop)  # schlägt das fehl, erscheinen die Fenster wie früher

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Sicherheitsnetz: Tests dürfen nie echte Tasten drücken, die Maus bewegen oder ein Fenster nach vorne holen
# (Master Duel läuft beim Testen oft nebenher). Wer so etwas testet, muss es mit mock.patch simulieren.
import pyautogui  # noqa: E402
import win_api  # noqa: E402


def _no_real_input(*args, **kwargs):
    raise AssertionError("Test wollte echte Eingaben machen (Taste/Maus/Fokus) – bitte mit mock.patch simulieren")


for _name in ("press", "hotkey", "typewrite", "write", "keyDown", "keyUp", "click", "moveTo", "dragTo", "scroll"):
    setattr(pyautogui, _name, _no_real_input)
for _name in ("set_foreground_window", "set_cursor_pos", "mouse_event", "mouse_wheel"):
    setattr(win_api, _name, _no_real_input)

# Tk-Tests legen eigene Hauptfenster an. Bleiben davon Python-Zyklen übrig, räumt die Speicherbereinigung sie
# irgendwann auf – evtl. in einem fremden Thread eines späteren Tests, und dann stürzt Tk ab ("Tcl_AsyncDelete:
# async handler deleted by the wrong thread"). Deshalb vor jedem Test im Hauptthread aufräumen.
import gc  # noqa: E402
import unittest  # noqa: E402

_run_test = unittest.TestCase.run


def _run_after_cleanup(self, result=None):
    gc.collect()
    return _run_test(self, result)


unittest.TestCase.run = _run_after_cleanup
