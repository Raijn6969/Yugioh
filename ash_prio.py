"""
Ash-Prio-Spickzettel: Für jede Starthand des Decks, an welcher Stelle der Combo eine Handtrap am meisten schadet.

Die Daten kommen aus der Factory (C:\\Python\\Sim, nur lesend): Sie rechnet für jede Starthand einer Deck-Variante die
beste Linie aus und prüft sie gegen Ash Blossom, Imperm/Veiler, Nibiru, Droll & Lock und Ghost Ogre ("stress" in
combo_results). Pro Handtrap steht dort die für uns schlimmste Stelle (Schritt der Linie), was dann noch übrig
bleibt (Unterbrechungen auf dem Board) und wie man danach am besten weiterspielt.

Das Deck im Deck-Fenster wird der Variante mit den meisten gleichen Karten zugeordnet.

Ohne Factory auf dem PC (z.B. bei Freunden): Schnappschuss der Ergebnisse, der beim Bauen der exe mit eingepackt wird
(build_snapshot, MD_Importer.spec). Kennt auch der das Deck nicht, schätzt das Ash-Prio-Fenster aus den Kartentexten
(handtrap_targets).
"""

import gzip
import json
import os
import sqlite3
import time
from collections import Counter
from typing import Dict, Iterable, List, Mapping, NamedTuple, Optional, Tuple
from urllib.request import pathname2url

FACTORY_DB = os.environ.get("MD_FACTORY_DB", r"C:\Python\Sim\data\factory.db")
SNAPSHOT_NAME = "ash_prio_snapshot.json.gz"
MIN_OVERLAP = 0.6   # so viel des Decks muss mit der Variante übereinstimmen
HAND_LIMIT = 40

# Reihenfolge und Anzeige wie in der Factory (factory/combo/handtraps.py)
HANDTRAPS = (("ash", "Ash Blossom"), ("imperm", "Imperm/Veiler"), ("nibiru", "Nibiru"), ("droll", "Droll & Lock"),
             ("ogre", "Ghost Ogre"))


class Threat(NamedTuple):
    key: str                 # "ash", "imperm", …
    name: str                # Anzeige
    step: Optional[int]      # Index der Aktion in der Linie (None = findet keinen Angriffspunkt)
    after: float             # Unterbrechungen auf dem Board danach
    text: str                # was passiert (aus der Factory)
    continuation: List[str]  # beste Fortsetzung danach


class HandSheet(NamedTuple):
    hand: str                # "Karte A + Karte B"
    score: float             # Unterbrechungen auf dem Board ohne Handtrap
    steps: List[str]         # die Linie
    threats: Dict[str, Threat]

    def damage(self, key: str) -> float:
        threat = self.threats.get(key)
        return self.score - threat.after if threat is not None and threat.step is not None else 0.0


class Variant(NamedTuple):
    key: str
    name: str
    overlap: float           # Anteil gleicher Karten (0–1)


def snapshot_file() -> str:
    """Schnappschuss neben dem Programm (in der exe im Entpack-Ordner)."""
    from app_paths import BUNDLE_DIR
    return os.path.join(BUNDLE_DIR, SNAPSHOT_NAME)


def available(path: str = FACTORY_DB) -> bool:
    """Gibt es auf diesem PC eine Factory?"""
    return os.path.exists(path)


def connect(path: str = FACTORY_DB) -> sqlite3.Connection:
    """Factory-Datenbank nur lesend öffnen (die Factory läuft evtl. gerade und schreibt)."""
    uri = "file:" + pathname2url(os.path.abspath(path)) + "?mode=ro"
    return sqlite3.connect(uri, uri=True, timeout=5)


def overlap(deck: Mapping[str, int], other: Mapping[str, int]) -> float:
    """Anteil gleicher Karten (mit Kopien) bezogen aufs größere Deck."""
    total = max(sum(deck.values()), sum(other.values()))
    return sum(min(n, other.get(cid, 0)) for cid, n in deck.items()) / total if total else 0.0


def _cards(csv: str) -> Counter:
    return Counter(c for c in (csv or "").split(",") if c)


def normalize(con: sqlite3.Connection, deck: Mapping[str, int], names: Mapping[str, str]) -> Counter:
    """Alternativ-Artworks haben in Master Duel eigene Passcodes: über den Namen auf den Passcode der Factory."""
    wanted = {names[cid] for cid in deck if cid in names}
    by_name: Dict[str, str] = {}
    if wanted:
        try:
            rows = con.execute(f"SELECT name, MIN(id) FROM cards WHERE name IN ({','.join('?' * len(wanted))}) "
                               "GROUP BY name", sorted(wanted)).fetchall()
            by_name = dict(rows)
        except sqlite3.Error:  # ältere Factory ohne Kartentabelle → Passcodes wie sie sind
            pass
    result: Counter = Counter()
    for cid, n in deck.items():
        result[by_name.get(names.get(cid, ""), cid)] += n
    return result


def find_variant(con: sqlite3.Connection, deck: Mapping[str, int], tested: bool = True) -> Optional[Variant]:
    """Die Variante mit den meisten gleichen Karten (tested: nur solche mit Handtrap-Ergebnissen der Factory)."""
    keys = {row[0] for row in con.execute(
        "SELECT DISTINCT deck FROM combo_results WHERE data LIKE '%\"stress\"%'")} if tested else None
    best: Optional[Variant] = None
    for key, name, main, extra in con.execute("SELECT key, name, main, extra FROM variants"):
        if keys is not None and key not in keys:
            continue
        share = overlap(deck, _cards(main) + _cards(extra))
        if best is None or share > best.overlap:
            best = Variant(key, name or key, share)
    return best if best is not None and best.overlap >= MIN_OVERLAP else None


def parse_sheet(hand: str, score: float, data: dict) -> Optional[HandSheet]:
    stress = data.get("stress")
    if not isinstance(stress, dict):
        return None
    threats = {}
    for key, name in HANDTRAPS:
        entry = stress.get(key)
        if not isinstance(entry, dict):
            continue
        step = entry.get("step")
        threats[key] = Threat(key, name, step if isinstance(step, int) else None, float(entry.get("score") or 0),
                              str(entry.get("text") or ""), [str(s) for s in entry.get("cont_steps") or []])
    return HandSheet(hand, float(score or 0), [str(s) for s in data.get("steps") or []], threats)


def hand_sheets(con: sqlite3.Connection, variant_key: str, limit: int = HAND_LIMIT) -> List[HandSheet]:
    """Starthände der Variante mit Handtrap-Prüfung, die stärksten zuerst."""
    sheets = []
    for hand, score, data in con.execute(
            "SELECT hand, score, data FROM combo_results WHERE deck = ? AND data LIKE '%\"stress\"%' "
            "ORDER BY score DESC LIMIT ?", (variant_key, limit)):
        try:
            sheet = parse_sheet(hand, score, json.loads(data))
        except (ValueError, TypeError):
            continue
        if sheet is not None and sheet.steps:
            sheets.append(sheet)
    return sheets


# ── Schnappschuss (für PCs ohne Factory) ──
def build_snapshot(path: str = FACTORY_DB, target: Optional[str] = None, hands: int = HAND_LIMIT) -> int:
    """Alle Varianten mit Handtrap-Prüfung samt Starthänden in eine Datei (gzip-JSON, ~150 KB). Nur lesend.
    Returns: Anzahl Varianten (0 = keine Factory, eine alte Datei bleibt dann liegen)."""
    if not os.path.exists(path):
        return 0
    target = target or snapshot_file()
    con = connect(path)
    try:
        tested = {row[0] for row in con.execute(
            "SELECT DISTINCT deck FROM combo_results WHERE data LIKE '%\"stress\"%'")}
        variants, ids = [], set()
        for key, name, main, extra in con.execute("SELECT key, name, main, extra FROM variants"):
            if key not in tested:
                continue
            sheets = [dict(s._asdict(), threats={k: t._asdict() for k, t in s.threats.items()})
                      for s in hand_sheets(con, key, hands)]
            if sheets:
                variants.append({"key": key, "name": name or key, "main": main, "extra": extra, "sheets": sheets})
                ids.update(_cards(main) + _cards(extra))
        names: Dict[str, str] = {}
        try:
            for cid, name in con.execute(f"SELECT id, name FROM cards WHERE id IN ({','.join('?' * len(ids))})",
                                         sorted(ids)):
                if name not in names or int(cid) < int(names[name]):
                    names[name] = cid
        except (sqlite3.Error, ValueError):
            pass
    finally:
        con.close()
    data = {"built": time.time(), "variants": variants, "names": names}
    tmp = target + ".tmp"
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, target)
    return len(variants)


def _sheet_from_json(data: dict) -> HandSheet:
    return HandSheet(data["hand"], data["score"], data["steps"],
                     {k: Threat(**t) for k, t in data["threats"].items()})


def load_snapshot(deck: Mapping[str, int], names: Mapping[str, str],
                  target: Optional[str] = None) -> Tuple[Optional[Variant], List[HandSheet], str]:
    target = target or snapshot_file()
    try:
        with gzip.open(target, "rt", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None, [], ""
    by_name = data.get("names", {})
    normalized: Counter = Counter()
    for cid, n in deck.items():
        normalized[by_name.get(names.get(cid, ""), cid)] += n
    best = max(data.get("variants", []), key=lambda v: overlap(normalized, _cards(v["main"]) + _cards(v["extra"])),
               default=None)
    if best is None:
        return None, [], ""
    share = overlap(normalized, _cards(best["main"]) + _cards(best["extra"]))
    if share < MIN_OVERLAP:
        return None, [], ""
    built = time.strftime("%d.%m.%Y", time.localtime(data.get("built", 0)))
    return (Variant(best["key"], best["name"], share), [_sheet_from_json(s) for s in best["sheets"]],
            f"Stand der Factory vom {built}")


def load(deck: Mapping[str, int], path: str = FACTORY_DB,
         names: Optional[Mapping[str, str]] = None,
         snapshot: Optional[str] = None) -> Tuple[Optional[Variant], List[HandSheet], str]:
    """(Variante, Starthände, Hinweis) für das Deck ({Passcode: Kopien}, names: {Passcode: Name} für Alt-Arts);
    der Hinweis erklärt, warum nichts da ist. Ohne Factory: aus dem mitgelieferten Schnappschuss."""
    if not os.path.exists(path):
        variant, sheets, note = load_snapshot(deck, names or {}, snapshot)
        if variant is not None:
            return variant, sheets, note
        return None, [], "Für dieses Deck gibt es keine Starthände der Factory."
    try:
        con = connect(path)
        try:
            normalized = normalize(con, deck, names or {})
            variant = find_variant(con, normalized)
            if variant is None:
                close = find_variant(con, normalized, tested=False)
                if close is not None:
                    return None, [], (f"Die Factory kennt eine passende Liste („{close.name}“, "
                                      f"{close.overlap * 100:.0f} % "
                                      f"gleiche Karten), hat sie aber noch nicht gegen Handtraps geprüft. Sobald sie "
                                      f"das getan hat, erscheint sie hier.")
                return None, [], "Die Factory hat dieses Deck noch nicht durchgerechnet."
            return variant, hand_sheets(con, variant.key), ""
        finally:
            con.close()
    except sqlite3.Error as e:
        return None, [], f"Factory-Datenbank nicht lesbar: {e}"


def advice(sheet: HandSheet, key: str) -> str:
    """'Ash trifft am härtesten bei Schritt 1 (Branded Fusion) → Board 0,1 statt 7,7'"""
    threat = sheet.threats.get(key)
    if threat is None:
        return "nicht geprüft"
    if threat.step is None:
        return f"{threat.name} findet keinen Angriffspunkt"
    action = sheet.steps[threat.step] if 0 <= threat.step < len(sheet.steps) else "?"
    when = "nach" if " nach Schritt " in threat.text else "als Antwort auf"  # Nibiru/Droll kommen danach
    return (f"{when} Schritt {threat.step + 1}: {short_step(action)} → Board {number(threat.after)} statt "
            f"{number(sheet.score)}")


class Target(NamedTuple):
    name: str                # Karte, deren Effekt die Handtrap trifft
    hands: int               # in so vielen Starthänden ist das die schlimmste Stelle
    loss: float              # Ø so viele Unterbrechungen weniger auf dem Board


def step_card(step: str, names: Iterable[str] = ()) -> Optional[str]:
    """Die Karte eines Schritts: 'Elfnotes: Welcome Home: schickt …' → 'Elfnotes: Welcome Home' (längster bekannter
    Name, sonst bis zum ersten ': '); 'Normalbeschwörung: X' → 'X'; ohne Karte ('Main Phase beenden …') → None."""
    step = short_step(step)
    for name in sorted(names, key=len, reverse=True):
        if name and step.startswith(name + ":"):
            return name
    if ": " not in step:
        return None
    head, rest = step.split(": ", 1)
    return rest.strip() if head == "Normalbeschwörung" else head


def targets(sheets: List[HandSheet], key: str, names: Iterable[str] = ()) -> List[Target]:
    """Ash-Prio auf einen Blick: welche Karten die Handtrap über alle Starthände am häufigsten trifft (dann die mit
    dem größeren Schaden)."""
    names = list(names)
    found: Dict[str, Tuple[int, float]] = {}
    for sheet in sheets:
        threat = sheet.threats.get(key)
        if threat is None or threat.step is None or not 0 <= threat.step < len(sheet.steps):
            continue
        card = step_card(sheet.steps[threat.step], names)
        if card is None:
            continue
        hands, loss = found.get(card, (0, 0.0))
        found[card] = (hands + 1, loss + sheet.damage(key))
    result = [Target(card, hands, loss / hands) for card, (hands, loss) in found.items()]
    return sorted(result, key=lambda t: (-t.hands, -t.loss, t.name))


def hits(sheet: HandSheet, key: str, card: str, names: Iterable[str] = ()) -> bool:
    """Trifft die Handtrap in dieser Starthand die Karte `card`?"""
    threat = sheet.threats.get(key)
    return (threat is not None and threat.step is not None and 0 <= threat.step < len(sheet.steps)
            and step_card(sheet.steps[threat.step], names) == card)


def short_step(step: str) -> str:
    """Schritt ohne Ketten-Zusatz: 'Ketu Dracotail: sucht Dracotail Lukias'"""
    return step.split("  [", 1)[0]


def number(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")
