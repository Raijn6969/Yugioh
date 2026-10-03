"""
Ist eine Karte eine Handtrap?

Eine Handtrap stört den Gegner in seinem Zug direkt aus der Hand – ohne dass sie vorher gelegt oder
beschworen werden muss (z.B. Ash Blossom, Maxx "C", Effect Veiler, Infinite Impermanence). Wie bei den
Startern (starter_rules) wird dafür der englische Kartentext von YGOPRODeck gelesen: Der Effekt
- wird aus der Hand benutzt (abwerfen, von der Hand auf den Friedhof, verbannen, aufdecken, sich beschwören),
- geht im Zug des Gegners (Quick Effect bzw. ausgelöst durch eine Aktion des Gegners) und
- trifft den Gegner (negiert, zerstört, verbannt, gilt für "your opponent" …).
Fallen zählen, wenn sie sich aus der Hand aktivieren lassen. Effekte rund um Angriffe und Kampfschaden
(z.B. Kuriboh) zählen nicht. Faustregel – im Deck-Fenster pro Karte korrigierbar.
"""

import re
from typing import NamedTuple, Optional

from starter_rules import EXTRA_FRAMES, _sentences, _split_effect


class HandtrapGuess(NamedTuple):
    handtrap: Optional[bool]  # None = Extra Deck (wird nicht gezogen)
    reason: str


# Wie der Effekt aus der Hand benutzt wird → Begründung
HAND_USES = [
    (re.compile(r"discard this card"), "abwerfen"),
    (re.compile(r"special summon this card from your hand"), "beschwört sich aus der Hand"),
    (re.compile(r"banish this card from your hand"), "aus der Hand verbannen"),
    (re.compile(r"reveal this card"), "aufdecken"),
    (re.compile(r"this card (?:and [^;:]*?)?from your hand|this card in your hand"), "von der Hand auf den Friedhof"),
]
# Im Zug des Gegners: Quick Effect oder ausgelöst durch den Gegner
OPPONENT_TURN = re.compile(r"\(quick effect\)|your opponent|opponent's|either player|either turn")
# Trifft den Gegner (in Kosten oder Wirkung)
INTERACTS = re.compile(r"opponent|either player|neither player|either gy|negate|destroy that|"
                       r"any card sent to the gy|monsters on the field")
# Kampf/Schaden (Kuriboh & Co.) und Karten, die sich aufs gegnerische Feld beschwören (Contact "C")
NOT_HANDTRAP = re.compile(r"\battack|battle phase|battle damage|damage calculation|you would take|inflicts? damage|"
                          r"from your hand to (?:the |your )?opponent's (?:field|side)")
# Schützt nur eigene Karten bzw. braucht eine eigene Karte auf dem Feld ("if you control …", nicht "control no")
NEEDS_OWN_CARD = re.compile(r"you control(?! no)")

# Feste Einstufungen für Karten, die die Regeln nicht richtig erkennen (Namen wie bei YGOPRODeck)
CURATED = {
    "Ghost Sister & Spooky Dogwood": (True, "abwerfen: Lebenspunkte für jede Beschwörung des Gegners"),
}


def classify_handtrap(card_type: str, frame: str, desc: str, name: str = "") -> HandtrapGuess:
    """card_type/frame/desc wie bei YGOPRODeck (englisch)."""
    if name in CURATED:
        return HandtrapGuess(*CURATED[name])
    card_type = (card_type or "").lower()
    frame = (frame or "").lower()
    if frame.startswith(EXTRA_FRAMES):
        return HandtrapGuess(None, "Extra Deck – wird nicht gezogen")
    text = desc or ""
    if "trap" in card_type:
        if "activate this card from your hand" in text.lower():
            return HandtrapGuess(True, "Falle, aus der Hand aktivierbar")
        return HandtrapGuess(False, "Falle – muss erst gesetzt werden")
    if "monster" not in card_type:
        return HandtrapGuess(False, "Zauber – nur im eigenen Zug aus der Hand")
    if "[ Monster Effect ]" in text:
        text = text.partition("[ Monster Effect ]")[2]
    sentences = _sentences(text)
    for i, sentence in enumerate(sentences):
        low = sentence.lower()
        condition, cost, effect, _ = _split_effect(low)
        # z.B. Bystial: "This is a Quick Effect if your opponent controls a monster." im Satz danach
        quick = i + 1 < len(sentences) and sentences[i + 1].lower().startswith("this is a quick effect")
        if not (quick or OPPONENT_TURN.search(condition)) or NEEDS_OWN_CARD.search(condition):
            continue
        use = next((label for pattern, label in HAND_USES if pattern.search(f"{condition};{cost};{effect}")), None)
        if use is None or NOT_HANDTRAP.search(low) or not INTERACTS.search(f"{cost};{effect}"):
            continue
        what = "negiert" if "negate" in effect else "stört den Gegner"
        return HandtrapGuess(True, f"im Zug des Gegners aus der Hand ({use}) – {what}")
    return HandtrapGuess(False, "nicht aus der Hand im Zug des Gegners nutzbar")
