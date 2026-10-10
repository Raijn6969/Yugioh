"""
Tägliche Sicherung der eigenen Datenbank (md_card_stats.db: Matches, Münzwürfe, Side-Profile, Deck-Verlauf).

Einmal pro Tag (beim Start bzw. wenn das Programm über Mitternacht läuft) eine Kopie nach backups/, die letzten
KEEP Kopien bleiben. Kopiert wird mit der Backup-Funktion von SQLite: Die Kopie ist auch dann in sich stimmig, wenn
gerade ein Match gespeichert wird (eine einfache Dateikopie könnte mitten in einem Schreibvorgang landen).
"""

import datetime
import os
import sqlite3
from typing import List, Optional

from debug_log import dlog

KEEP = 7
PREFIX = "md_card_stats_"
CHECK_INTERVAL_MS = 60 * 60 * 1000  # stündlich nachsehen, ob heute schon gesichert wurde


def folder_for(db_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), "backups")


def backups(folder: str) -> List[str]:
    """Vorhandene Sicherungen, die älteste zuerst."""
    try:
        names = [n for n in os.listdir(folder) if n.startswith(PREFIX) and n.endswith(".db")]
    except OSError:
        return []
    return [os.path.join(folder, n) for n in sorted(names)]


def backup(db_path: str, folder: Optional[str] = None, today: Optional[datetime.date] = None,
           keep: int = KEEP) -> Optional[str]:
    """
    Heute noch nicht gesichert → Kopie anlegen und alte über `keep` hinaus löschen.
    Returns: Pfad der neuen Sicherung, None wenn nichts zu tun war oder es nicht ging.
    """
    if not os.path.exists(db_path):
        return None
    folder = folder or folder_for(db_path)
    today = today or datetime.date.today()
    target = os.path.join(folder, f"{PREFIX}{today.isoformat()}.db")
    if os.path.exists(target):
        return None
    partial = target + ".tmp"
    try:
        os.makedirs(folder, exist_ok=True)
        source = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
        try:
            copy = sqlite3.connect(partial)
            try:
                source.backup(copy)
            finally:
                copy.close()
        finally:
            source.close()
        os.replace(partial, target)  # erst die fertige Kopie bekommt ihren Namen
    except (OSError, sqlite3.Error) as e:
        dlog(f"[SICHERUNG] Datenbank nicht gesichert: {e}")
        try:
            os.remove(partial)
        except OSError:
            pass
        return None
    for old in backups(folder)[:-keep]:
        try:
            os.remove(old)
        except OSError:
            pass
    dlog(f"[SICHERUNG] Datenbank gesichert: {target}")
    return target
