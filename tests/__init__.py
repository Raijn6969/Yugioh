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
