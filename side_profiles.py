"""
Side-Deck-Profile ("Zug-2-Modus"): Master Duel ist Bo1, umgestellt wird zwischen den Matches. Ein Profil sagt
z.B. „als Zweiter: −3 Droll & Lock Bird, +3 Lightning Storm“. Ein Klick tauscht die Karten im Deck-Editor, ein
zweiter tauscht zurück.

Der Tausch ist ein kleiner Import: Ziel-Deck = jetziges Deck − raus + rein. Der Import läuft dafür wie beim
Fortsetzen (Deck wird nicht geleert, alles gilt als schon eingefügt); seine Kontrolle am Ende liest das Deck,
entfernt die überzähligen Karten per Rechtsklick (vorher geprüft) und sucht die fehlenden – also genau den Tausch.
"""

from collections import Counter
from typing import Dict, List, Mapping

from card_stats import SideProfile

MAX_COPIES = 3


def swap(deck: Mapping[str, int], cards_out: Mapping[str, int], cards_in: Mapping[str, int]) -> List[str]:
    """Ziel-Deck als Liste von Passcodes (wie ein Deck-Code): deck − cards_out + cards_in."""
    target = Counter({cid: n for cid, n in deck.items() if n > 0})
    target.subtract(cards_out)
    target.update(cards_in)
    return sorted(cid for cid, n in target.items() for _ in range(max(0, n)))


def has_cards(deck: Mapping[str, int], cards: Mapping[str, int]) -> bool:
    """Liegen diese Karten (mindestens so oft) im Deck?"""
    return all(deck.get(cid, 0) >= n for cid, n in cards.items())


def can_apply(deck: Mapping[str, int], profile: SideProfile) -> bool:
    """Profil anwendbar: Die Karten, die raus sollen, sind im Deck, und rein darf jede Karte höchstens 3×."""
    if not has_cards(deck, profile.cards_out):
        return False
    after = Counter(deck)
    after.subtract(profile.cards_out)
    after.update(profile.cards_in)
    return all(after[cid] <= MAX_COPIES for cid in profile.cards_in)


def can_revert(deck: Mapping[str, int], profile: SideProfile) -> bool:
    """Zurücktauschen möglich: Die Karten, die das Profil hereingenommen hat, sind im Deck."""
    return can_apply(deck, profile._replace(cards_out=profile.cards_in, cards_in=profile.cards_out))


def is_active(deck: Mapping[str, int], profile: SideProfile) -> bool:
    """Sieht das Deck aus, als wäre das Profil angewendet? (Rein-Karten da, aber nicht zurücktauschbar heißt
    nichts – entscheidend ist, dass man nur noch zurück kann.)"""
    return can_revert(deck, profile) and not can_apply(deck, profile)


def describe(cards: Mapping[str, int], names: Mapping[str, str]) -> str:
    """'3× Droll & Lock Bird, 1× Nibiru'"""
    return ", ".join(f"{n}× {names.get(cid, cid)}" for cid, n in sorted(cards.items(), key=lambda x: -x[1])) or "–"


def clean(cards: Dict[str, int]) -> Dict[str, int]:
    return {cid: n for cid, n in cards.items() if n > 0}
