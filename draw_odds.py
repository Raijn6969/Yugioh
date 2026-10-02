"""
Ziehchancen (hypergeometrische Verteilung): Wie wahrscheinlich ist eine Karte auf der Starthand?
"""

from math import comb

HAND_FIRST = 5   # Starthand, wenn man anfängt
HAND_SECOND = 6  # Starthand + erste Ziehung, wenn man als Zweiter dran ist


def p_exactly(deck: int, copies: int, hand: int, k: int) -> float:
    """Wahrscheinlichkeit, genau k von `copies` Karten unter `hand` gezogenen aus `deck` zu haben."""
    if deck <= 0 or hand < 0 or not 0 <= k <= copies or copies > deck:
        return 0.0
    hand = min(hand, deck)
    if k > hand or hand - k > deck - copies:
        return 0.0
    return comb(copies, k) * comb(deck - copies, hand - k) / comb(deck, hand)


def p_at_least(deck: int, copies: int, hand: int, k: int = 1) -> float:
    """Wahrscheinlichkeit, mindestens k Kopien zu ziehen."""
    if k <= 0:
        return 1.0
    if deck <= 0 or copies <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - sum(p_exactly(deck, copies, hand, i) for i in range(k))))
