"""
Staple-Analyse: das eigene Deck mit den Top-Listen in Master Duel vergleichen (Master Duel Meta, "Deck Types").

Master Duel Meta wertet die Top-Listen jedes Deck-Typs aus ("deckBreakdown"): pro Karte der Anteil der Listen, die
sie spielen, die häufigste Kopienzahl und der Durchschnitt. Für die häufigsten Archetypen im Deck wird der passende
Deck-Typ geholt (einmal online, danach aus der lokalen Datenbank, nach CACHE_DAYS neu) und der Typ gewählt, mit dem
das Deck die meisten Karten teilt. Verglichen wird über den Kartennamen (Alternativ-Artworks zählen gleich).
"""

import time
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple

import requests

from card_stats import name_key
from starter_guides import API_URL

TIMEOUT = 20
NAME_BATCH = 50         # Karten-IDs pro Namensabfrage (die Adresse bleibt kurz genug)
CACHE_DAYS = 3          # so lange gelten gespeicherte Auswertungen
MISSING_SHARE = 0.5     # "Fehlt dir": so viele Top-Listen spielen die Karte mindestens
RARE_SHARE = 0.2        # "Nur bei dir": höchstens so viele Top-Listen spielen sie
DIFFERENT_SHARE = 0.5   # Kopienzahl nur vergleichen, wenn so viele Listen die Karte spielen …
USUAL_SHARE = 0.5       # … und die häufigste Kopienzahl so oft vorkommt


class StapleCard(NamedTuple):
    name: str
    share: float        # Anteil der Top-Listen, die die Karte spielen (0–1)
    usual: int          # häufigste Kopienzahl
    usual_share: float  # Anteil der Listen mit genau dieser Kopienzahl (0–1)
    avg: float          # Ø Kopien in den Listen, die sie spielen
    passcode: str = ""  # für das Kartenbild ("" = unbekannt)


class DeckTypeStats(NamedTuple):
    deck_type: str
    lists: int          # so viele Top-Listen liegen der Auswertung zugrunde
    cards: List[StapleCard]
    fetched_at: float

    def to_json(self) -> dict:
        return {"deck_type": self.deck_type, "lists": self.lists, "fetched_at": self.fetched_at,
                "cards": [list(c) for c in self.cards]}

    @classmethod
    def from_json(cls, data: dict) -> "DeckTypeStats":
        return cls(data["deck_type"], data["lists"], [StapleCard(*c) for c in data["cards"]], data["fetched_at"])


class Difference(NamedTuple):
    card: StapleCard
    mine: int           # Kopien im eigenen Deck (0 = fehlt)


class Comparison(NamedTuple):
    stats: DeckTypeStats
    different: List[Difference]   # gespielt, aber andere Kopienzahl als üblich
    missing: List[Difference]     # von vielen Top-Listen gespielt, fehlt im Deck
    only_mine: List[Difference]   # im Deck, aber kaum (oder gar nicht) in den Top-Listen
    matching: int                 # Karten, die wie in den Top-Listen gespielt werden
    overlap: float                # Anteil der eigenen Karten, die auch die Top-Listen spielen


def parse_breakdown(deck_type: dict, names: Dict[str, Tuple[str, str]],
                    now: Optional[float] = None) -> Optional[DeckTypeStats]:
    """Antwort von /deck-types + {MDM-Karten-ID: (Name, Passcode)} → Auswertung; None ohne Top-Listen."""
    breakdown = deck_type.get("deckBreakdown") or {}
    lists = int(breakdown.get("total") or 0)
    if not lists:
        return None
    cards = []
    for entry in breakdown.get("cards") or []:
        name, passcode = names.get(entry.get("card")) or ("", "")
        if not name:
            continue
        cards.append(StapleCard(name, float(entry.get("totalPer") or 0) / 100, int(entry.get("at") or 0),
                                float(entry.get("per") or 0) / 100, float(entry.get("avgAt") or 0), passcode))
    return DeckTypeStats(deck_type.get("name", ""), lists, cards, time.time() if now is None else now)


def fetch_deck_type(archetype: str, session=requests) -> Optional[DeckTypeStats]:
    """Auswertung des Deck-Typs mit diesem Namen (online); None, wenn Master Duel Meta ihn nicht kennt."""
    resp = session.get(f"{API_URL}/deck-types", params={"name": archetype}, timeout=TIMEOUT)
    resp.raise_for_status()
    found = resp.json() or []
    if not found:
        return None
    deck_type = found[0]
    ids = [c.get("card") for c in (deck_type.get("deckBreakdown") or {}).get("cards") or [] if c.get("card")]
    names: Dict[str, Tuple[str, str]] = {}
    for start in range(0, len(ids), NAME_BATCH):
        chunk = ids[start:start + NAME_BATCH]
        resp = session.get(f"{API_URL}/cards", params={"_id[$in]": ",".join(chunk), "limit": len(chunk)},
                           timeout=TIMEOUT)
        resp.raise_for_status()
        # "konamiID" ist bei Master Duel Meta der Passcode
        names.update({c["_id"]: (c.get("name", ""), str(c.get("konamiID") or ""))
                      for c in resp.json() or [] if c.get("_id")})
    return parse_breakdown(deck_type, names)


def load(db, archetype: str, refresh: bool = False, session=requests) -> Optional[DeckTypeStats]:
    """Gespeicherte Auswertung (jünger als CACHE_DAYS) oder online neu holen und speichern."""
    cached = db.staples(archetype)
    if cached is not None and not refresh and time.time() - cached.get("fetched_at", 0) < CACHE_DAYS * 86400:
        return DeckTypeStats.from_json(cached) if cached.get("cards") is not None else None
    try:
        stats = fetch_deck_type(archetype, session)
    except Exception:
        if cached is not None:  # offline → lieber die alte Auswertung als gar keine
            return DeckTypeStats.from_json(cached) if cached.get("cards") is not None else None
        raise
    # Auch "unbekannt" merken (cards = None), damit nicht bei jedem Öffnen neu gefragt wird
    db.store_staples(archetype, stats.to_json() if stats else {"fetched_at": time.time(), "cards": None})
    return stats


def compare(deck: Dict[str, int], stats: DeckTypeStats, others: Iterable[DeckTypeStats] = ()) -> Comparison:
    """
    deck: {Kartenname: Kopien} (Main + Extra) mit den Top-Listen vergleichen. others: weitere Deck-Typen des Decks
    (Hybrid wie Ryzeal/Mitsurugi) – deren übliche Karten zählen nicht als "nur bei dir".
    """
    elsewhere = {name_key(card.name) for other in others for card in other.cards if card.share > RARE_SHARE}
    mine = {name_key(name): (name, copies) for name, copies in deck.items()}
    by_key = {name_key(card.name): card for card in stats.cards}
    different, missing, only_mine, matching = [], [], [], 0
    for key, card in by_key.items():
        copies = mine.get(key, ("", 0))[1]
        if not copies:
            if card.share >= MISSING_SHARE:
                missing.append(Difference(card, 0))
        elif card.share >= DIFFERENT_SHARE and card.usual_share >= USUAL_SHARE and copies != card.usual:
            different.append(Difference(card, copies))
        elif card.share > RARE_SHARE:
            matching += 1
    for key, (name, copies) in mine.items():
        card = by_key.get(key)
        if (card is None or card.share <= RARE_SHARE) and key not in elsewhere:
            only_mine.append(Difference(card or StapleCard(name, 0.0, 0, 0.0, 0.0), copies))
    shared = sum(1 for key in mine if key in by_key and by_key[key].share > RARE_SHARE)
    different.sort(key=lambda d: -d.card.share)
    missing.sort(key=lambda d: -d.card.share)
    only_mine.sort(key=lambda d: (d.card.share, d.card.name))
    return Comparison(stats, different, missing, only_mine, matching, shared / max(1, len(mine)))


def best_match(deck: Dict[str, int], candidates: Iterable[DeckTypeStats]) -> List[Comparison]:
    """Vergleiche mit allen Kandidaten, der passendste (größte Übereinstimmung) zuerst."""
    candidates = list(candidates)
    return sorted((compare(deck, stats, [o for o in candidates if o is not stats]) for stats in candidates),
                  key=lambda c: -c.overlap)
