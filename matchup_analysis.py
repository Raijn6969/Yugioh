"""
Matchup gegen Störkarten: Welche Störkarten (Handtraps, negierende Schnellzauber/Fallen) spielen deine Gegner, und
wie oft gewinnst du gegen Decks mit bzw. ohne sie?

Grundlage sind die erfassten Duelle (card_stats, Tabelle matches) mit dem Deck des Gegners – das kommt aus der
Match History von Master Duel (live erfasste Duelle haben es erst, nachdem man sie einmal geöffnet hat).
Alternativ-Artworks zählen als dieselbe Karte (Vergleich über den Namen).
"""

import re
from typing import Dict, List, NamedTuple, Optional

from card_stats import CardInfo, MatchEntry
from handtrap_rules import classify_handtrap
from match_history import DRAW, WIN
from utils import parse_deck_code

NEGATES = re.compile(r"\bnegat")  # negate, negated, negates


class Record(NamedTuple):
    """Ergebnisse einer Gruppe von Duellen (z.B. gegen Decks mit einer bestimmten Karte)."""
    matches: int = 0
    wins: int = 0
    draws: int = 0
    first: int = 0
    first_wins: int = 0
    second: int = 0
    second_wins: int = 0

    @property
    def losses(self) -> int:
        return self.matches - self.wins - self.draws

    def add(self, match: MatchEntry) -> "Record":
        win = int(match.result == WIN)
        return Record(self.matches + 1, self.wins + win, self.draws + int(match.result == DRAW),
                      self.first + int(match.first is True), self.first_wins + (win if match.first is True else 0),
                      self.second + int(match.first is False),
                      self.second_wins + (win if match.first is False else 0))


class Threat(NamedTuple):
    name: str
    passcode: str        # fürs Kartenbild
    kind: str            # "Handtrap", "Schnellzauber", "Falle"
    decks: int           # in so vielen Gegner-Decks
    share: float         # Anteil der Gegner-Decks (0–1)
    against: Record      # deine Ergebnisse gegen Decks mit der Karte
    without: Record      # … und gegen Decks ohne sie


class Matchup(NamedTuple):
    opponents: int        # Duelle mit bekanntem Gegner-Deck
    unknown: int          # Duelle ohne Gegner-Deck (live erfasst, Match History noch nicht geöffnet)
    overall: Record       # alle Duelle mit bekanntem Gegner-Deck
    threats: List[Threat]
    per_deck: float       # Ø Störkarten (verschiedene) pro Gegner-Deck


def disruption_kind(info: Optional[CardInfo]) -> Optional[str]:
    """Ist die Karte eine Störkarte gegen deinen Zug? "Handtrap"/"Schnellzauber"/"Falle" oder None."""
    if info is None:
        return None
    if classify_handtrap(info.card_type, info.frame, info.desc, info.name).handtrap:
        return "Handtrap"
    card_type, text = info.card_type.lower(), (info.desc or "").lower()
    if "spell" in card_type and info.race == "Quick-Play" and NEGATES.search(text):
        return "Schnellzauber"
    if "trap" in card_type and NEGATES.search(text):
        return "Falle"
    return None


def analyse(db, deck_name: Optional[str] = None, mode: Optional[int] = None, limit: int = 1000) -> Matchup:
    """Störkarten der Gegner in den gespeicherten Duellen (optional nur ein eigenes Deck bzw. ein Modus)."""
    matches = db.matches(deck_name, mode, limit)
    known = [m for m in matches if m.opp_code]
    decks = {m.did: parse_deck_code(m.opp_code) for m in known}
    infos: Dict[str, CardInfo] = db.infos(cid for ids in decks.values() for cid in ids)  # eine Abfrage
    kinds = {cid: disruption_kind(info) for cid, info in infos.items()}
    passcode_of: Dict[str, str] = {}
    decks_with: Dict[str, List[MatchEntry]] = {}
    overall = Record()
    for match in known:
        overall = overall.add(match)
        names = set()
        for cid in decks[match.did]:
            if kinds.get(cid):
                info = infos[cid]
                names.add(info.name)
                # Bild: bei mehreren Artworks der kleinste Passcode (meist das ursprüngliche)
                passcode_of[info.name] = min(passcode_of.get(info.name, cid), cid, key=int)
        for name in names:
            decks_with.setdefault(name, []).append(match)
    kind_of = {infos[cid].name: kind for cid, kind in kinds.items() if kind}
    threats = []
    for name, with_card in decks_with.items():
        against = Record()
        for match in with_card:
            against = against.add(match)
        without = Record(*(total - part for total, part in zip(overall, against)))
        threats.append(Threat(name, passcode_of[name], kind_of[name], len(with_card), len(with_card) / len(known),
                              against, without))
    threats.sort(key=lambda t: (-t.decks, t.name))
    per_deck = sum(t.decks for t in threats) / len(known) if known else 0.0
    return Matchup(len(known), len(matches) - len(known), overall, threats, per_deck)
