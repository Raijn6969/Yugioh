"""
Ist eine Karte ein Starter?

Ein Starter ist hier eine Karte, die auf leerem Feld allein aus der Hand eine Kombo beginnen
kann: Sie holt eine Karte aus dem Deck (suchen, beschwören, setzen) oder beschwört selbst eine
Fusion. Online gibt es dafür kein festes Merkmal. Deshalb wird der englische Kartentext von
YGOPRODeck gelesen. Das ist eine Faustregel, die man im Deck-Fenster pro Karte korrigieren kann.
Die Begründung sagt genau, was die Karte holt, z.B. "sucht 2 Karten aus dem Deck (Ritual Spell +
Ritual Monster)".
"""

import re
from typing import List, NamedTuple, Optional, Tuple

RULES_VERSION = 4  # Erhöhen, wenn sich die Regeln ändern (gespeicherte Ergebnisse werden dann neu berechnet)
EXTRA_FRAMES = ("fusion", "synchro", "xyz", "link")


class StarterGuess(NamedTuple):
    starter: Optional[bool]  # None = Extra Deck (wird nicht gezogen)
    reason: str


_DECK = r"from your (?:hand(?:,| or) )?(?:field(?:,| or) )?deck"
# Effekte, die etwas aus dem Deck holen: (Art, Muster). Gruppe "what" = was geholt wird.
SIGNALS = [
    # auch "add to your hand, or Special Summon, 1 … from your Deck" und "add from your Deck to your hand, 1 …"
    ("add", re.compile(rf"\badd\b(?P<what>[^.;]*?){_DECK}")),
    ("add", re.compile(rf"\btake (?P<what>[^.;]*?){_DECK}[^.;]*?add it to your hand")),
    ("summon", re.compile(rf"\bspecial summon\b(?P<what>[^.;]*?){_DECK}")),
    ("set", re.compile(rf"\bset (?P<what>(?:1|one|up to \w+) [^.;]*?){_DECK}")),
    # z.B. Elfnote Fortuna, Maiden of White: Karte aus dem Deck offen aufs Feld legen
    ("place", re.compile(rf"\bplace (?P<what>(?:1|one) [^.;]*?){_DECK}[^.;]*?(?:on your field|spell & trap zone|field zone)")),
    # Beschwörung mit Material aus dem Deck (z.B. Branded Fusion). Zählt nicht, wenn das Material nur aus Hand/Feld
    # kommen darf oder das beschworene Monster schon auf der Hand sein muss (z.B. Mitsurugi Ritual)
    ("material", re.compile(rf"(?P<what>fusion|synchro|ritual|xyz|link) summon (?:(?!from your hand(?!(?:,| or) deck))[^.;])*?"
                            rf"(?:using|tributing|banishing|shuffling|sending)[^.;]*?{_DECK}")),
]
# Nur bei Monstern
MONSTER_SIGNALS = [
    # Fusion aus Hand/Feld, das Monster ist selbst Material (z.B. Dracotail Mululu)
    ("fusion", re.compile(r"fusion summon[^.;]*?using [^.;]*?from your hand")),
    # Aufdecken mit Hinzufügen (z.B. Purrely)
    ("excavate", re.compile(r"excavate the top \w+ cards? of your deck[^.;]*?add [^.;]*?to your hand")),
    # z.B. Sky Striker Ace - Raye: tributet sich selbst und beschwört per Effekt aus dem Extra Deck
    ("extra", re.compile(r"\bspecial summon (?!this card)(?P<what>[^.;]*?)from your extra deck")),
]

# Bedingungen/Kosten, die auf leerem Feld im eigenen ersten Zug nicht erfüllt sind
BLOCKERS = (
    "sent to the gy", "is sent to", "destroyed", "banished", "in your gy", "from your gy", "is in the gy",
    "opponent", "attack", "battle", "damage step", "detached", "as material", "as a material",
    "is tributed", "leaves the field", "end phase", "standby phase", "returned to", "flip summoned",
    "if you control a", "if you control an", "if you control 2", "while you control", "you control a ",
    "you control an ", "cards you control", "monsters you control", "if a monster", "if a \"",
    "if another", "if exactly", "if there is", "if there are", "each time", "except the turn",
    "target", "equipped", "if this card is flipped", "if you have", "you control \"",
)
# Kosten, die ein weiteres Monster brauchen ("Tribute 1 Tuner"); "you can also Tribute" ist freiwillig
COST_BLOCKERS = re.compile(r"(?<!also )\btribute (?:1|one|2|two)\b")
# Effekte, die direkt aus der Hand gehen (Monster muss dafür nicht aufs Feld)
HAND_COSTS = ("discard this card", "this card from your hand", "reveal this card", "if this card is in your hand",
              "(from your hand)", "this card in your hand")
SELF_SUMMON = re.compile(r"special summon this card \(from your hand\)|special summon this card from your hand")

NUMBERS = {"1": 1, "one": 1, "2": 2, "two": 2, "3": 3, "three": 3}
WHAT_MAX = 34  # Zeichen pro Kartenbeschreibung in der Begründung


def _normalize(text: str) -> str:
    """Leerraum, Anführungszeichen, "Graveyard" vereinheitlichen. Groß-/Kleinschreibung bleibt (für die Anzeige)."""
    text = re.sub(r"graveyard", "GY", text, flags=re.IGNORECASE).replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", " ", text).strip()


def _sentences(text: str) -> List[str]:
    """Kartentext in Effekte zerlegen. ●-Aufzählungen gehören zum Satz davor."""
    text = text.replace("\r", " ").replace("\n", " ")
    parts = re.split(r"(?<=\.)\s+(?=[A-Z(\[\"])", text)
    return [_normalize(p) for p in parts if p.strip()]


def _blocked(part: str) -> bool:
    return any(b in part for b in BLOCKERS)


def _condition_blocked(condition: str) -> bool:
    """Bedingung mit Alternativen ("If … Summoned, or if this card is Tributed"): eine erfüllbare reicht."""
    return all(_blocked(alt) for alt in re.split(r",? or (?=if |when )", condition))


def _summons_itself(sentences: List[str]) -> bool:
    """Kann sich das Monster auf leerem Feld selbst aus der Hand beschwören? (sentences: kleingeschrieben)"""
    for s in sentences:
        hit = SELF_SUMMON.search(s)
        if hit and (not _blocked(s[:hit.start()]) or "if you control no monsters" in s[:hit.start()]):
            return True
    return False


def _can_reach_field(level: Optional[int], sentences: List[str]) -> bool:
    """Kommt das Monster allein aufs Feld? (Normalbeschwörung ohne Tribut oder eigene Spezialbeschwörung)"""
    if any("cannot be normal summoned" in s for s in sentences):
        return _summons_itself(sentences)
    if level is not None and level <= 4:
        return True
    # z.B. K9: "If your opponent has 2 or more cards in their hand, you can Normal Summon this card without Tributing"
    return any("normal summon this card without tributing" in s for s in sentences) or _summons_itself(sentences)


def _split_effect(sentence: str) -> Tuple[str, str, str, int]:
    """(Bedingung vor ':', Kosten vor ';', Wirkung, Startposition der Wirkung im Satz)"""
    if ":" in sentence.split(";")[0]:
        condition, _, body = sentence.partition(":")
        start = len(condition) + 1
    else:
        condition, body, start = "", sentence, 0
    if ";" in body:
        cost, _, effect = body.partition(";")
        start += len(cost) + 1
    else:
        cost, effect = "", body
    return condition, cost, effect, start


def _clean_what(text: str) -> Tuple[int, str]:
    """' 1 "Dracotail" monster ' → (1, '"Dracotail" monster')"""
    text = re.sub(r"^(?:to your hand,? (?:or special summon,? )?|or special summon,? )", "", text.strip(" ,"),
                  flags=re.IGNORECASE).strip(" ,")
    count = 1
    match = re.match(r"(?:up to )?(\w+) ", text, flags=re.IGNORECASE)
    if match and match.group(1).lower() in NUMBERS:
        count = NUMBERS[match.group(1).lower()]
        text = text[match.end():]
    text = re.sub(r"\b(?:directly|in defense position)\b", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip(" ,")
    if len(text) > WHAT_MAX:
        text = text[:WHAT_MAX - 1].rstrip() + "…"
    return count, text


def _karten(count: int) -> str:
    return "1 Karte" if count == 1 else f"{count} Karten"


def _reason(kind: str, pattern, effect: str, effect_low: str) -> str:
    """Genaue Begründung: wie viele Karten und welche (aus der Originalschreibung des Kartentexts)."""
    count, whats = 0, []
    for match in pattern.finditer(effect_low):
        if "what" not in pattern.groupindex or match.group("what") is None:
            count += 1
            continue
        n, what = _clean_what(effect[match.start("what"):match.end("what")])
        count += n
        if what and what not in whats:
            whats.append(what)
    detail = f" ({' + '.join(whats)})" if whats else ""
    if kind == "add":
        return f"sucht {_karten(count)} aus dem Deck{detail}"
    if kind == "summon":
        return f"beschwört {count} Monster aus dem Deck{detail}"
    if kind == "set":
        return f"setzt {_karten(count)} aus dem Deck{detail}"
    if kind == "place":
        return f"legt {_karten(count)} aus dem Deck aufs Feld{detail}"
    if kind == "material":
        summon = pattern.search(effect_low).group("what").capitalize()
        return f"{summon}-Beschwörung mit Material aus dem Deck"
    if kind == "extra":
        return f"beschwört per Effekt aus dem Extra Deck{detail}"
    if kind == "fusion":
        return "beschwört selbst eine Fusion"
    return "sucht eine Karte (aufdecken)"


def classify(card_type: str, frame: str, desc: str, level: Optional[int] = None,
             race: str = "") -> StarterGuess:
    """card_type/frame/desc/level/race wie bei YGOPRODeck (englisch). race = Zauberart bei Zaubern."""
    card_type = (card_type or "").lower()
    frame = (frame or "").lower()
    if frame.startswith(EXTRA_FRAMES):
        return StarterGuess(None, "Extra Deck – wird nicht gezogen")
    if "trap" in card_type:
        return StarterGuess(False, "Falle – erst im nächsten Zug nutzbar")
    if frame == "normal" or card_type.startswith("normal"):
        return StarterGuess(False, "Monster ohne Effekt")
    if "flip" in card_type:
        return StarterGuess(False, "Flipp-Effekt – braucht einen Zug")
    if "spell" in card_type and (race or "").lower() == "equip":
        return StarterGuess(False, "Ausrüstung – braucht ein Monster")

    is_monster = "monster" in card_type
    text = desc or ""
    pendulum_text, monster_text = "", text
    if "[ Pendulum Effect ]" in text:
        pendulum_text, _, monster_text = text.partition("[ Monster Effect ]")
        pendulum_text = pendulum_text.replace("[ Pendulum Effect ]", "").strip(" -\r\n")
    sentences = _sentences(monster_text)
    lowered = [s.lower() for s in sentences]
    on_field = is_monster and _can_reach_field(level, lowered)
    self_summon = is_monster and _summons_itself(lowered)
    signals = SIGNALS + (MONSTER_SIGNALS if is_monster else [])

    # Pendel-Effekte gehen aus der Pendelzone (wie ein Zauber aus der Hand)
    candidates = [(s, True) for s in _sentences(pendulum_text)] + [(s, not is_monster) for s in sentences]
    for sentence, from_hand in candidates:
        low = sentence.lower()  # gleiche Länge wie sentence → Positionen passen zur Originalschreibung
        condition, cost, effect_low, start = _split_effect(low)
        hit = next(((kind, pattern) for kind, pattern in signals if pattern.search(effect_low)), None)
        if hit is None or _condition_blocked(condition) or _blocked(cost) or COST_BLOCKERS.search(cost):
            continue
        if not (from_hand or any(h in condition or h in cost for h in HAND_COSTS)):
            if not on_field:
                continue
            if "if this card is special summoned" in condition and not self_summon:
                continue
        kind, pattern = hit
        return StarterGuess(True, _reason(kind, pattern, sentence[start:], effect_low))
    return StarterGuess(False, "holt allein nichts aus dem Deck")
