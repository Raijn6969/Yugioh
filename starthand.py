"""
Starthand aus einem Replay-Mitschnitt (replay_recorder).

Ein Schritt (frame) enthält alle Karten, die das Spiel zeigt: [uid, team, zone, index, Konami-ID, offen, …]. Die eigene
Hand sind die Karten in Zone 13 mit team == my_id; Konami-ID 0 heißt "nicht bekannt".

Die Starthand ist der erste Schritt, in dem die eigene Hand aus genau 5 bekannten Karten besteht – und zwar vor Zug 2:
Als Erster zieht man im ersten Zug nicht, als Zweiter kommt die Karte aus Zug 2 erst danach dazu (die zählt nicht zur
Starthand). Beginnt der Mitschnitt erst später (keine 5 Karten in Zug 1) oder ist die Hand nicht lesbar, gibt es keine
verlässliche Starthand → None, und das Replay wird übersprungen.
"""

import re
from typing import List, Optional

HAND = 13          # Zone Hand (replay_recorder.HAND)
HAND_SIZE = 5
LAST_TURN = 1      # Zug 1: danach hat auch der Zweite schon gezogen


def turn_number(text) -> Optional[int]:
    """'Turn 2' / 'Zug 2' → 2 (Phasen-Knopf, Sprache des Spiels); None, solange der Text fehlt."""
    found = re.search(r"\d+", text or "") if isinstance(text, str) else None
    return int(found.group()) if found else None


def starting_hand(frames: List[dict], my_id: int) -> Optional[List[int]]:
    """Die 5 Karten der Starthand (Konami-IDs, sortiert) oder None."""
    for frame in frames:
        turn = turn_number(frame.get("turn"))
        if turn is not None and turn > LAST_TURN:
            return None  # schon gezogen bzw. gespielt: keine verlässliche Starthand mehr
        hand = [card[4] for card in frame.get("cards", []) if card[1] == my_id and card[2] == HAND]
        if len(hand) == HAND_SIZE and all(hand):
            return sorted(hand)
    return None
