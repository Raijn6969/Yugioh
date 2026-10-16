"""
Ash-Prio ohne Factory-Daten: Welche Effekte einer Karte kann eine Handtrap treffen? (aus dem englischen Kartentext)

- Ash Blossom: ein Effekt, der aus dem Deck der Hand hinzufügt, aus dem Deck beschwört oder aus dem Deck auf den
  Friedhof legt – auch Karten aus dem Deck als Material (Branded Fusion). Nur die Wirkung zählt, nicht die Kosten
  vor dem ";".
- Droll & Lock Bird: ein Effekt, der aus dem Deck der Hand hinzufügt (danach geht nichts mehr aus dem Deck auf
  die Hand).
- Imperm/Veiler: ein Monster-Effekt, der auf dem Feld aktiviert wird (nicht aus Hand oder Friedhof).
- Ghost Ogre: ein Monster, das auf dem Feld seinen Effekt aktiviert, oder ein offener Dauer-/Feldzauber bzw. eine
  Dauerfalle, die ihren Effekt aktiviert.
Die Reihenfolge einer Combo kennt das nicht – dafür gibt es die Starthände der Factory (ash_prio).
"""

import re
from typing import Dict

from starter_rules import _DECK, _sentences, _split_effect

ASH = re.compile(rf"\b(?:add|special summon|send)\b[^.;]*?{_DECK}"
                 # Material aus dem Deck ("using 2 monsters from your hand, Deck, or field as material"),
                 # nicht Extra Deck
                 r"|\busing\b[^.;]*?from your [^.;]*?(?<!extra )\bdeck\b[^.;]*?\bas (?:\w+ )?materials?\b")
DROLL = re.compile(rf"\badd\b[^.;]*?{_DECK}")
# Effekte, die nicht auf dem Feld aktiviert werden (Hand, Friedhof, verbannt)
OFF_FIELD = re.compile(r"\(from your hand\)|if this card is in your (?:hand|gy|graveyard)|discard this card|"
                       r"this card from your hand|reveal this card|banish this card from your (?:gy|graveyard)|"
                       r"if this card is (?:sent to the gy|banished|discarded|in your gy)|"
                       r"while this card is in your (?:hand|gy)|from your gy\)")
ON_FIELD_SPELLS = ("continuous", "field")
WHAT_MAX = 70


def _activated(sentence: str, spell_activation: bool = False) -> bool:
    """Wird dieser Effekt aktiviert? (Bedingung mit ":" oder "You can"; Dauereffekte und Einschränkungen wie
    "You can only … once per turn" nicht). Normale Zauber/Fallen: ihr Text ist die Aktivierung selbst."""
    if sentence.startswith(("you can only", "you cannot", "you can't")):
        return False
    head = sentence.split(";")[0]
    return spell_activation or ":" in head or sentence.startswith("you can") or " you can " in head


def _short(text: str) -> str:
    text = text.strip().rstrip(".")
    return text if len(text) <= WHAT_MAX else text[:WHAT_MAX - 1].rstrip() + "…"


def targets(card_type: str, frame: str, race: str, desc: str) -> Dict[str, str]:
    """{Handtrap: Effekt, den sie trifft} für eine Karte; leer = keine erkennbare Angriffsfläche."""
    race, card_type = (race or "").lower(), (card_type or "").lower()
    monster = "monster" in card_type
    spell_activation = not monster and race not in ON_FIELD_SPELLS  # Normal-, Schnell-, Ritualzauber, normale Fallen
    hits: Dict[str, str] = {}
    for original in _sentences(desc or ""):
        sentence = original.lower()  # Muster klein, angezeigt wird der Originaltext
        if not _activated(sentence, spell_activation):
            continue
        _, _, effect, start = _split_effect(sentence)
        shown = original[start:]
        if "ash" not in hits and ASH.search(effect):
            hits["ash"] = _short(shown)
        if "droll" not in hits and DROLL.search(effect):
            hits["droll"] = _short(shown)
        on_field = not OFF_FIELD.search(sentence)
        if monster and on_field:
            hits.setdefault("imperm", _short(shown))
            hits.setdefault("ogre", _short(shown))
        if not monster and on_field and race in ON_FIELD_SPELLS and " you can " in f" {sentence} ":
            hits.setdefault("ogre", _short(shown))
    return hits


def card_targets(info) -> Dict[str, str]:
    """Wie targets() für eine CardInfo (card_stats)."""
    if info is None:
        return {}
    return targets(info.card_type, info.frame, info.race, info.desc)
