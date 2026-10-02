"""
Baut die Grunddatenbank md_card_stats_base.db mit allen Karten von YGOPRODeck (englisch) für die
Extras-Stats. Sie wird mit der exe ausgeliefert, damit die Stats ab dem ersten Start offline gehen.

MD_Importer.spec ruft das beim Bauen automatisch auf, wenn die Datei fehlt oder älter als
MAX_AGE_DAYS ist. Von Hand:
    .venv\\Scripts\\python.exe build_card_db.py
"""

import json
import os
import sqlite3
import sys
import time

import requests

from card_stats import BASE_DB_NAME, cards_from_api, create_tables, store_auto
from utils import YGOPRO_API_URL

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(PROJECT_DIR, BASE_DB_NAME)
STARTER_LIST = os.path.join(PROJECT_DIR, "starter_list.json")
MAX_AGE_DAYS = 14


def curated_rows(cards: dict, path: str = STARTER_LIST) -> list:
    """starter_list.json → [(ID, Starter, Grund)] für alle IDs (auch Alternativ-Artworks) der genannten Karten."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    ids_by_name = {}
    for info in cards.values():
        ids_by_name.setdefault(info.name, []).append(info.cid)
    rows = []
    for key, value in (("starter", 1), ("kein_starter", 0)):
        for name, reason in data.get(key, {}).items():
            if name not in ids_by_name:
                print(f"[build_card_db] WARNUNG: '{name}' aus starter_list.json gibt es bei YGOPRODeck nicht")
            rows += [(cid, value, reason) for cid in ids_by_name.get(name, [])]
    return rows


def build(path: str = TARGET) -> int:
    """Lädt alle Karten (eine Anfrage, ~20 MB) und schreibt die Datenbank. Returns: Anzahl Einträge."""
    resp = requests.get(YGOPRO_API_URL, timeout=120)
    resp.raise_for_status()
    cards = cards_from_api(resp.json().get("data", []))
    tmp = path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    con = sqlite3.connect(tmp)
    try:
        create_tables(con)
        now = time.time()
        con.executemany("INSERT OR REPLACE INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [(*info, now) for info in cards.values()])
        con.executemany("INSERT OR REPLACE INTO starter_curated VALUES (?, ?, ?)", curated_rows(cards))
        store_auto(con, cards.values())  # Starter-Einstufung nach den Textregeln, mit Regel-Version
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()
    os.replace(tmp, path)
    return len(cards)


def ensure_fresh(path: str = TARGET) -> None:
    """
    Für den Build: neu bauen, wenn die Datei fehlt, zu alt ist oder starter_list.json / die Starter-Regeln
    geändert wurden (offline → alte Datei behalten).
    """
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        sources = (STARTER_LIST, os.path.join(PROJECT_DIR, "starter_rules.py"))
        if age < MAX_AGE_DAYS * 86400 and all(os.path.getmtime(s) <= os.path.getmtime(path) for s in sources):
            return
    try:
        count = build(path)
        print(f"[build_card_db] {count} Karten -> {path} ({os.path.getsize(path) / 1e6:.1f} MB)")
    except Exception as e:
        if not os.path.exists(path):
            raise
        print(f"[build_card_db] Aktualisieren fehlgeschlagen ({e}) - alte Datenbank wird verwendet.")


if __name__ == "__main__":
    count = build()
    print(f"{count} Karten -> {TARGET} ({os.path.getsize(TARGET) / 1e6:.1f} MB)")
    sys.exit(0)
