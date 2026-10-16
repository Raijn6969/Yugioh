"""
Combos von Master Duel Meta (Community-Combos zu einem Deck-Typ) und der Abgleich mit dem eigenen Deck.

Zu einem Archetyp wird der Deck-Typ gesucht und seine Combos geholt (einmal online, danach aus der lokalen Datenbank,
nach CACHE_DAYS neu). Je Combo: Starthand, benötigte Karten, Schritte mit Verzweigungen, Endboard und die Handtraps,
gegen die sie durchspielt. match() sagt, welche Karten dem Deck fehlen und wie wahrscheinlich die Starthand ist.
"""

import re
import time
from itertools import combinations
from math import comb
from typing import Dict, List, NamedTuple, Optional

from lazy_requests import requests

from card_stats import name_key
from starter_guides import API_URL

TIMEOUT = 20
CACHE_DAYS = 3
MAX_DEPTH = 3            # Verzweigungen in Verzweigungen …
HAND_SIZE = 5            # Wahrscheinlichkeit als Erster (5 Karten)
LIMIT = 100              # Combos pro Deck-Typ


class Entry(NamedTuple):
    card: str            # "" bei Einträgen ohne Karte
    label: str
    connector: str = ""  # Verbindung zum nächsten Eintrag: "arrow" (→) oder "plus" (+)


class Step(NamedTuple):
    entries: List[Entry]
    branch_label: str
    branches: List["Branch"]


class Branch(NamedTuple):
    label: str
    steps: List[Step]


class HandCard(NamedTuple):
    name: str            # "" = Platzhalter (dann steht der Text in generic)
    generic: str
    label: str


class Combo(NamedTuple):
    mdm_id: str
    title: str
    deck_type: str
    author: str
    votes: int
    featured: bool
    start: List[HandCard]
    required: List[str]
    steps: List[Step]
    end: List[Entry]
    vs_handtraps: List[str]
    updated: str


class ComboSet(NamedTuple):
    deck_type: str
    combos: List[Combo]
    fetched_at: float

    def to_json(self) -> dict:
        return {"deck_type": self.deck_type, "fetched_at": self.fetched_at,
                "combos": [_plain(c) for c in self.combos]}

    @classmethod
    def from_json(cls, data: dict) -> "ComboSet":
        return cls(data["deck_type"], [_combo_from(c) for c in data["combos"]], data["fetched_at"])


class ComboMatch(NamedTuple):
    combo: Combo
    missing: List[str]
    odds: Optional[float]


def _plain(x):
    """NamedTuples (auch verschachtelt) → Listen, die JSON speichern kann."""
    return [_plain(v) for v in x] if isinstance(x, (tuple, list)) else x


def _step_from(data: list) -> Step:
    return Step([Entry(*e) for e in data[0]], data[1], [Branch(b[0], [_step_from(s) for s in b[1]]) for b in data[2]])


def _combo_from(data: list) -> Combo:
    c = Combo(*data)
    return c._replace(start=[HandCard(*h) for h in c.start], steps=[_step_from(s) for s in c.steps],
                      end=[Entry(*e) for e in c.end])


# ── Antwort von Master Duel Meta lesen ──
def _name(item: dict) -> str:
    return ((item or {}).get("card") or {}).get("name") or ""


def _entry(data: dict) -> Entry:
    return Entry(_name(data), data.get("label") or "", data.get("connector") or "")


def _step(data: dict, depth: int) -> Step:
    condition = data.get("condition") or {}
    branches = []
    if depth < MAX_DEPTH:
        branches = [Branch(b.get("label") or "", [_step(s, depth + 1) for s in b.get("steps") or []])
                    for b in condition.get("branches") or []]
    return Step([_entry(e) for e in data.get("entries") or []], condition.get("label") or "", branches)


def _hand_card(data: dict) -> HandCard:
    """Startkarte; eine Karte mit "any" im Label ("Can be any non-fire monster") ist nur ein Beispiel → Platzhalter."""
    name, label = _name(data), data.get("label") or ""
    if name and re.search(r"\bany\b", label, re.I):
        return HandCard("", re.sub(r"^can be\s+", "", label, flags=re.I).strip(), f"z. B. {name}")
    return HandCard(name, "" if name else data.get("generic") or "", label)


def parse_combo(data: dict, deck_type: str) -> Combo:
    """Eine Combo der API → Combo; fehlende Felder werfen nichts."""
    start = [_hand_card(c) for c in data.get("startingCards") or []]
    return Combo(str(data.get("_id") or ""), data.get("title") or "", deck_type,
                 (data.get("author") or {}).get("username") or "",
                 len(data.get("upvotes") or []) - len(data.get("downvotes") or []), bool(data.get("featured")),
                 start, [n for n in (_name(c) for c in data.get("requiredCards") or []) if n],
                 [_step(s, 1) for s in data.get("steps") or []], [_entry(e) for e in data.get("endBoard") or []],
                 [h.get("name") or "" for h in data.get("vsHandTraps") or [] if h.get("name")],
                 data.get("updatedAt") or data.get("createdAt") or "")


def _as_list(found) -> list:
    """Mit limit=1 antwortet die API mit einem einzelnen Objekt statt einer Liste."""
    found = found or []
    return found if isinstance(found, list) else [found]


def fetch(archetype: str, session=requests) -> Optional[ComboSet]:
    """Combos des Deck-Typs mit diesem Namen (online); None, wenn Master Duel Meta ihn nicht kennt."""
    resp = session.get(f"{API_URL}/deck-types", params={"name": archetype, "limit": 3}, timeout=TIMEOUT)
    resp.raise_for_status()
    wanted = archetype.lower()
    types = [t for t in _as_list(resp.json()) if isinstance(t, dict) and wanted in (t.get("name") or "").lower()]
    if not types:
        return None
    # "Elfnote" vor "Elfnote Snake-Eye": erst der Name, der genau passt
    deck_type = next((t for t in types if t["name"].lower() == wanted), types[0])
    resp = session.get(f"{API_URL}/combos", params={"deckType": deck_type.get("_id"), "limit": LIMIT},
                       timeout=TIMEOUT)
    resp.raise_for_status()
    name = deck_type.get("name") or archetype
    return ComboSet(name, [parse_combo(c, name) for c in _as_list(resp.json())], time.time())


def load(db, archetype: str, refresh: bool = False, session=requests) -> Optional[ComboSet]:
    """Gespeicherte Combos (jünger als CACHE_DAYS) oder online neu holen und speichern."""
    cached = db.combos(archetype)
    if cached is not None and not refresh and time.time() - cached.get("fetched_at", 0) < CACHE_DAYS * 86400:
        return ComboSet.from_json(cached) if cached.get("combos") is not None else None
    try:
        found = fetch(archetype, session)
    except Exception:
        if cached is not None:  # offline → lieber den alten Stand als gar keinen
            return ComboSet.from_json(cached) if cached.get("combos") is not None else None
        raise
    # Auch "unbekannt" merken (combos = None), damit nicht bei jedem Öffnen neu gefragt wird
    db.store_combos(archetype, found.to_json() if found else {"fetched_at": time.time(), "combos": None})
    return found


# ── Abgleich mit dem eigenen Deck ──
def _copies(deck: Dict[str, int]) -> Dict[str, int]:
    result: Dict[str, int] = {}
    for name, copies in deck.items():
        result[name_key(name)] = result.get(name_key(name), 0) + copies
    return result


def match(combo: Combo, main: Dict[str, int], extra: Dict[str, int]) -> ComboMatch:
    """
    main/extra: {Kartenname: Kopien}. Fehlende Karten und die Chance, als Erster (5 Karten) alle genannten
    Startkarten auf der Hand zu haben. Auf der Hand liegen nur Karten des Main Decks; benötigte Karten (z. B. im
    Extra Deck) zählen aus beiden.
    """
    mine, other = _copies(main), _copies(extra)
    start = list(dict.fromkeys((h.name for h in combo.start if h.name)))  # shortcut: 2× derselbe Name zählt einmal
    missing = [n for n in start if name_key(n) not in mine]
    missing += [n for n in dict.fromkeys(combo.required)
                if name_key(n) not in mine and name_key(n) not in other and n not in missing]
    total = sum(mine.values())
    if missing or not start or total < 1:
        return ComboMatch(combo, missing, None)
    counts = [mine[name_key(n)] for n in start]
    n = min(HAND_SIZE, total)
    # Inklusion–Exklusion: Σ (−1)^|S| · C(N − Σc, n) / C(N, n) über alle Teilmengen S der Startkarten
    odds = sum((-1) ** size * comb(total - sum(subset), n)
               for size in range(len(counts) + 1) for subset in combinations(counts, size)) / comb(total, n)
    return ComboMatch(combo, missing, max(0.0, min(1.0, odds)))


def ranked(combos: List[Combo], main: Dict[str, int], extra: Dict[str, int]) -> List[ComboMatch]:
    """Spielbare (nichts fehlt) zuerst, dann empfohlene, dann nach Stimmen."""
    matches = [match(c, main, extra) for c in combos]
    return sorted(matches, key=lambda m: (bool(m.missing), not m.combo.featured, -m.combo.votes))
