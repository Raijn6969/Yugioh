"""
Batch-Planung und Archetyp-Gruppierung für den Master Duel Deck Import.

Gruppiert Karten mit gemeinsamen Namensstämmen (z. B. "Sky Striker", "Exosister", "Ryzeal"),
damit sie in Sammelsuchen statt Einzelabfragen gefunden werden können.
"""

from difflib import SequenceMatcher
import re
from typing import Dict, List, Optional, Set, Tuple


# Abgleich-Schwellen für Textvergleiche
TRUNCATION_MIN_OCR = 10
TRUNCATED_LONG_OCR = 18
NUMBER_TRUNCATED_MIN_OCR = 18

# Wörter, die zu generisch sind für eine Batch-Suche
BATCH_NOISE: Set[str] = {
    'dark', 'light', 'fire', 'water', 'earth', 'wind', 'divine', 'chaos',
    'black', 'white', 'red', 'blue', 'green', 'yellow', 'number', 'numeron',
    'true', 'super', 'ultra', 'hyper', 'mega', 'neo', 'great', 'high', 'evil',
    'ancient', 'sacred', 'crystal', 'cyber', 'dragon', 'galaxy', 'star', 'solar',
    'elemental', 'armed', 'the', 'of', 'for', 'a', 'an',
    # Deutsche Master-Duel-Namen
    'dunkel', 'dunkle', 'dunkler', 'licht', 'feuer', 'wasser', 'erde', 'göttlich', 'göttliche',
    'schwarz', 'schwarze', 'schwarzer', 'weiß', 'weiße', 'weißer', 'roter', 'rote', 'blauer',
    'blaue', 'grüner', 'grüne', 'gelber', 'nummer', 'wahrer', 'wahre', 'uralter', 'uralte',
    'heilige', 'heiliger', 'kristall', 'drache', 'drachen', 'galaxie', 'stern', 'sonne',
}


def normalize_word(w: str) -> str:
    """Normalisiert ein Wort für Archetype-Vergleiche: 'exosisters' → 'exosister'."""
    return w[:-1] if (w.endswith('s') and len(w) > 4) else w


def common_prefix_len(target: str, ocr: str) -> Tuple[int, int]:
    """
    Wie viele Zeichen am Anfang stimmen überein? Ein führendes 'l' in der OCR ist ein
    bekanntes Artefakt (Icon links vom Namen) und wird ignoriert.
    Returns: (gemeinsame Zeichen, Länge der bereinigten OCR).
    """
    ocr = ocr[1:] if ocr.startswith("l") else ocr
    common = 0
    for a, b in zip(target, ocr):
        if a != b:
            break
        common += 1
    return common, len(ocr)


def name_score(name_clean: str, s_c: str) -> Tuple[float, bool]:
    """
    Wie gut passt der gelesene Text zu einem Kartennamen (0..1)? Berücksichtigt das bekannte
    führende 'l' (Icon links vom Namen) und im Detail-Panel abgeschnittene Namen: Dann zählt
    der Vergleich mit dem Anfang des Namens ('lamenomurakumonomit' → Ame no Murakumo no Mitsurugi).
    Returns: (Ratio, per_Abschneiden_gematcht).
    """
    best, truncated = 0.0, False
    texts = (s_c, s_c[1:]) if s_c.startswith("l") and len(s_c) > 1 else (s_c,)
    for text in texts:
        ratio = SequenceMatcher(None, name_clean, text).ratio()
        if ratio > best:
            best, truncated = ratio, False
        cut_off = ((len(name_clean) > len(text) * 1.3 and len(text) >= TRUNCATION_MIN_OCR)
                   or (len(name_clean) > len(text) and len(text) >= TRUNCATED_LONG_OCR))
        if cut_off:
            prefix_ratio = SequenceMatcher(None, name_clean[:len(text)], text).ratio()
            if prefix_ratio > best:
                best, truncated = prefix_ratio, True
    return best, truncated


def number_card_overrule(target_clean: str, s_c: str) -> Optional[str]:
    """
    Number-Karten, deren Name im Panel abgeschnitten oder am Ende verrauscht ist.
    Nur wenn die Nummer exakt gleich ist (inkl. C/S/F-Präfix: 106 ≠ C106), die OCR abgeschnitten
    aussieht und NICHT länger ist als das Ziel.
    Returns: die Nummer bei Treffer, sonst None.
    """
    ocr = s_c[1:] if re.match(r"^l(?:number|nummer)", s_c) else s_c
    t = re.match(r"^(?:number|nummer)([a-z]{0,2}\d+)(.*)$", target_clean)
    o = re.match(r"^(?:number|nummer)([a-z]{0,2}\d+)(.*)$", ocr)
    if not (t and o and t.group(1) == o.group(1)):
        return None
    t_rest, o_rest = t.group(2), o.group(2)
    if (len(ocr) >= NUMBER_TRUNCATED_MIN_OCR and o_rest and len(o_rest) <= len(t_rest)
            and SequenceMatcher(None, t_rest[:len(o_rest)], o_rest).ratio() >= 0.85):
        return t.group(1)
    return None


def compute_batch_groups(
    cards_ready: List,
    noise: Optional[Set[str]] = None
) -> Dict[str, List]:
    """
    Erkennt Karten-Gruppen, die denselben Archetype-Präfix teilen (Sammelsuche statt einzeln suchen).
    Nur Gruppen mit >= 3 Karten und einem Suchbegriff >= 6 Zeichen werden gebündelt – mit Sonderzeichen
    reichen 3 ("d/d" für D/D und D/D/D, "u.a."), die machen ihn eindeutig genug.

    Returns: {prefix_str: [(cid, raw_name, amount), ...]} (in der Reihenfolge des Decks)
    """
    batch_noise = noise if noise is not None else BATCH_NOISE
    MIN_GROUP_SIZE = 3
    MIN_PREFIX_CHARS = 6
    MIN_SPECIAL_PREFIX_CHARS = 3  # Suchbegriff mit Sonderzeichen ("d/d")
    ANYWHERE = "*"  # Kennung: einzelnes Wort irgendwo im Namen (kein Namensanfang)

    # Schritt 1: Alle Wort-Präfixe (normalisiert) → {Kartenindex: echter Namensanfang bis zu diesem Wort}
    candidates: Dict[tuple, Dict[int, str]] = {}
    for index, card in enumerate(cards_ready):
        name = card[1].lower()
        spans = list(re.finditer(r"<[^>]*>|[^\W\d_]+", name))
        for n in range(1, len(spans) + 1):
            if spans[n - 1].group().startswith("<"):
                break
            key = tuple(normalize_word(m.group()) for m in spans[:n])
            candidates.setdefault(key, {})[index] = name[:spans[n - 1].end()]
        for m in spans:
            if not m.group().startswith("<"):
                candidates.setdefault((ANYWHERE, normalize_word(m.group())), {})[index] = m.group()

    def term(key: tuple, members: List[int]) -> str:
        return min((candidates[key][i] for i in members), key=len)

    def usable(key: tuple, members: List[int]) -> bool:
        if len(members) < MIN_GROUP_SIZE:
            return False
        text = term(key, members)
        if len(text) < (MIN_SPECIAL_PREFIX_CHARS if re.search(r"[^\w\s]", text) else MIN_PREFIX_CHARS):
            return False
        single = len(key) == 1 or key[0] == ANYWHERE
        return not single or not any(candidates[key][i] in batch_noise for i in members)

    def rank(key: tuple, members: List[int]) -> tuple:
        anywhere = key[0] == ANYWHERE
        return len(members), not anywhere, 1 if anywhere else len(key)

    # Schritt 2: Gierig die größte Gruppe nehmen, ihre Karten entfernen, weiter mit dem Rest
    taken: set = set()
    chosen = []
    while True:
        best = None
        for key, cards in candidates.items():
            members = [i for i in cards if i not in taken]
            if usable(key, members) and (best is None or rank(key, members) > rank(*best)):
                best = (key, members)
        if best is None:
            break
        taken.update(best[1])
        chosen.append(best)

    chosen.sort(key=lambda group: min(group[1]))
    return {term(key, members): [cards_ready[i] for i in members] for key, members in chosen}
