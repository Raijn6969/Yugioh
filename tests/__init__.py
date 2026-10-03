"""
Tests für den Master Duel Deck Importer.

Ausführen (im Projektordner):
    .venv\\Scripts\\python.exe -m unittest discover -s tests -t .

tests/data/golden.json enthält die Ergebnisse des Codes VOR dem Refactoring (echte Fälle aus
md_debug.log). Die Tests stellen sicher, dass Umbauten am Verhalten nichts ändern.
"""

import os
import sys

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
