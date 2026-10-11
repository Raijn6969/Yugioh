"""
Starthände (Abschnitt im Winrate-Fenster): Welche Karten waren in deiner Starthand, und wie lief das Duell dann?

Grundlage sind die Replay-Mitschnitte (replay_recorder): Zu jedem Match mit Mitschnitt gehört die Starthand
(starthand.py: die 5 Karten vor dem ersten eigenen Zug; ohne verlässliche Starthand wird das Replay übersprungen).
Ausgewertet wird immer ein Deck – im Winrate-Fenster das angeklickte; ohne Auswahl das einzige Deck, aus dem
Starthände vorliegen. Alternativ-Artworks zählen als dieselbe Karte (Vergleich über den Namen).

Pro Karte: in wie vielen Starthänden sie war und die Winrate in diesen Duellen gegenüber den Duellen ohne sie (erst ab
MIN_DUELS Duellen, sonst sind die Zahlen Zufall). Brick-Quote: Anteil der Starthände ohne Starter – ob eine Karte ein
Starter ist, sagt dieselbe Einstufung wie im Deck-Fenster (CardStatsDB.starter, von dir änderbar).
"""

from typing import Dict, List, NamedTuple, Optional

from card_stats import name_key
from matchup_analysis import Record

MIN_DUELS = 3        # ab so vielen Starthänden zeigt eine Karte eine Winrate
MATCH_LIMIT = 5000


class CardRate(NamedTuple):
    name: str
    hands: int            # Starthände mit der Karte
    with_card: Record     # deine Ergebnisse in Duellen mit der Karte in der Starthand
    without_card: Record  # … und in Duellen ohne sie


class Brick(NamedTuple):
    hands: int            # Starthände mit eindeutiger Einstufung (alle Karten bekannt)
    bricks: int           # davon ohne einen einzigen Starter
    brick: Record         # Ergebnisse mit Brick-Starthand
    playable: Record      # … mit mindestens einem Starter


class HandStats(NamedTuple):
    deck: Optional[str] = None        # ausgewertetes Deck (None: alle bzw. mehrere zur Auswahl)
    replays: int = 0                  # Matches mit Mitschnitt (im Filter)
    duels: int = 0                    # davon mit verlässlicher Starthand
    cards: List[CardRate] = []        # Karten mit mindestens MIN_DUELS Starthänden, häufigste zuerst
    brick: Optional[Brick] = None
    needs_deck: bool = False          # Starthände aus mehreren Decks, aber keins gewählt
    decks: List[str] = []


def subtract(total: Record, part: Record) -> Record:
    return Record(*(a - b for a, b in zip(total, part)))


def analyse(db, deck_name: Optional[str] = None, mode: Optional[int] = None,
            since: Optional[float] = None) -> HandStats:
    """Starthand-Auswertung der Matches mit Mitschnitt (Filter wie im Winrate-Fenster)."""
    matches = db.matches(deck_name, mode, MATCH_LIMIT, since)
    assigned = db.replay_starts_by_match()
    with_replay = [(m, assigned[m.did]) for m in matches if m.did in assigned]
    hands = db.starting_hands(start for _, start in with_replay)
    duels = [(m, hands[start]) for m, start in with_replay if hands.get(start)]
    decks = sorted({m.deck_name for m, _ in duels})
    if deck_name is None and len(decks) > 1:
        return HandStats(None, len(with_replay), len(duels), needs_deck=True, decks=decks)
    deck = deck_name if deck_name is not None else (decks[0] if decks else None)
    if not duels:
        return HandStats(deck, len(with_replay), 0, decks=decks)

    names = db.konami_names()
    label: Dict[str, str] = {}      # Kartenschlüssel (Name ohne Sonderzeichen) → Anzeigename
    key_of: Dict[int, str] = {}     # Konami-ID → Kartenschlüssel
    for _, hand in duels:
        for kid in hand:
            if kid not in key_of:
                name = names.get(kid) or f"Karte {kid}"
                key_of[kid] = name_key(name) or str(kid)
                label.setdefault(key_of[kid], name)
    total, with_card = Record(), {}
    for match, hand in duels:
        total = total.add(match)
        for key in {key_of[kid] for kid in hand}:  # zwei Kopien in der Hand zählen als ein Duell
            with_card[key] = with_card.get(key, Record()).add(match)
    cards = [CardRate(label[key], record.matches, record, subtract(total, record))
             for key, record in with_card.items() if record.matches >= MIN_DUELS]
    cards.sort(key=lambda c: (-c.hands, c.name))
    return HandStats(deck, len(with_replay), len(duels), cards, brick_rate(db, duels), decks=decks)


def brick_rate(db, duels) -> Optional[Brick]:
    """Anteil der Starthände ohne Starter. Hände mit einer Karte ohne Einstufung (keine Kartendaten) zählen nicht mit."""
    passcodes = db.konami_passcodes()
    starter: Dict[int, Optional[bool]] = {}
    hands = bricks = 0
    brick, playable = Record(), Record()
    for match, hand in duels:
        for kid in hand:
            if kid not in starter:
                try:
                    starter[kid] = db.starter(passcodes[kid]).starter if kid in passcodes else None
                except Exception:
                    starter[kid] = None
        flags = [starter[kid] for kid in hand]
        if any(flag is True for flag in flags):
            hands += 1
            playable = playable.add(match)
        elif all(flag is False for flag in flags):
            hands += 1
            bricks += 1
            brick = brick.add(match)
    return Brick(hands, bricks, brick, playable) if hands else None
