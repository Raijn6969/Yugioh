"""
Fortsetzen nach Abbruch.

Während des Imports wird nach jeder eingefügten Karte gespeichert, welche Karten schon im
Deck sind (md_resume.json). Bricht der Import ab (z.B. Maus bewegt), kann der nächste Start
mit demselben Deck-Code dort weitermachen, statt das Deck zu leeren und neu anzufangen.
"""

import json
import os
import time
from typing import Dict, Iterable, Optional

from app_paths import RESUME_FILE


def _deck_key(card_ids: Iterable[str]) -> list:
    # Reihenfolge egal, Anzahl zählt: gleicher Deck-Code ⇔ gleiche sortierte ID-Liste
    return sorted(str(c) for c in card_ids)


def save_progress(card_ids: Iterable[str], done: Dict[str, int], path: str = None) -> None:
    path = path or RESUME_FILE
    data = {"deck": _deck_key(card_ids), "done": done, "saved_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp_path, path)


def load_progress(card_ids: Iterable[str], path: str = None) -> Optional[dict]:
    """Gespeicherter Stand, falls er zum selben Deck gehört und schon Karten eingefügt wurden."""
    path = path or RESUME_FILE
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if data.get("deck") != _deck_key(card_ids) or not data.get("done"):
        return None
    return data


def clear_progress(path: str = None) -> None:
    path = path or RESUME_FILE
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
