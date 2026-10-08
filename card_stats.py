"""
Lokale Mini-Datenbank für Karten-Stats (SQLite).

- md_card_stats_base.db: alle Karten (Text, Typ, Stufe, Archetyp) und die festen Starter-Einstufungen aus
  starter_list.json, wird mit der exe ausgeliefert (build_card_db.py erzeugt sie beim Bauen). Nur lesend –
  damit geht alles ab dem ersten Start offline.
- md_card_stats.db neben dem Programm: Karten, die neuer sind als die Grunddatenbank (einmalig online
  bei YGOPRODeck nachgeschlagen), Starter-Einstufungen aus den Guides von Master Duel Meta (im Deck-Fenster
  über Optionen nachgeladen), eigene Starter-/Handtrap-Korrekturen aus dem Deck-Fenster (haben Vorrang),
  der Verlauf der importierten Decks, die erfassten Duelle mit den gemerkten Decks aus Master Duel
  (match_history) und die Auswertung der Top-Listen je Archetyp (staple_analysis).

Ob eine Karte ein Starter bzw. eine Handtrap ist, wird aus dem gespeicherten Kartentext abgeleitet
(starter_rules, handtrap_rules).
"""

import json
import os
import re
import sqlite3
import time
from contextlib import contextmanager
from typing import Dict, Iterable, List, NamedTuple, Optional, Tuple
from urllib.request import pathname2url

import requests

from app_paths import APP_DIR, BUNDLE_DIR
from debug_log import dlog
from handtrap_rules import classify_handtrap
from starter_rules import EXTRA_FRAMES, RULES_VERSION, classify
from utils import YGOPRO_API_URL

DB_FILE = os.path.join(APP_DIR, "md_card_stats.db")
BASE_DB_NAME = "md_card_stats_base.db"
BASE_DB_FILE = os.path.join(BUNDLE_DIR, BASE_DB_NAME)
BATCH = 50  # IDs pro Anfrage (YGOPRODeck erlaubt mehrere IDs, der Link bleibt kurz genug)
COLUMNS = "id, name, type, frame, race, level, archetype, desc"
HISTORY_MIN_COPIES = 3  # Archetyp zählt für den Deck-Namen ab so vielen Karten


class CardInfo(NamedTuple):
    cid: str
    name: str
    card_type: str   # z.B. "Effect Monster", "Spell Card"
    frame: str       # z.B. "effect", "spell", "fusion"
    race: str        # Monstertyp bzw. Zauber-/Fallenart ("Quick-Play", "Field" …)
    level: Optional[int]
    archetype: str
    desc: str


class StarterInfo(NamedTuple):
    starter: Optional[bool]  # None = unbekannt bzw. Extra Deck
    reason: str
    manual: bool              # True = von Hand im Extras-Menü gesetzt


class HandtrapInfo(NamedTuple):
    handtrap: Optional[bool]  # None = unbekannt bzw. Extra Deck
    reason: str
    manual: bool               # True = von Hand im Deck-Fenster gesetzt


class HistoryEntry(NamedTuple):
    id: int
    code: str            # Deck-Code wie kopiert (YDKE-Link oder .ydk-Text) → erneut importierbar
    name: str            # aus den Archetypen gebildet
    archetypes: str
    main: int
    extra: int
    imported_at: float
    times: int           # wie oft importiert
    result: str          # "ok" oder "Lücken"


class MatchEntry(NamedTuple):
    did: str
    played_at: float
    mode: int
    result: int               # 1 Sieg, 2 Niederlage, 3 Unentschieden (match_history)
    first: Optional[bool]     # selbst angefangen?
    turns: int
    finish: int
    deck_name: str            # aus den Archetypen ("" = Deck unbekannt)
    deck_code: str            # .ydk-Text → im Deck-Fenster anzeigbar
    opp_name: str             # "" = unbekannt (live erfasst, Match History noch nicht gelesen)
    opp_code: str
    md_deck: str              # Name des Decks in Master Duel ("" = unbekannt)
    coin: Optional[bool] = None  # Münzwurf gewonnen? (None = unbekannt)


class WinStats(NamedTuple):
    matches: int = 0
    wins: int = 0
    draws: int = 0
    first: int = 0            # davon als Erster
    first_wins: int = 0
    second: int = 0
    second_wins: int = 0
    last_played: float = 0.0
    coin_won: int = 0         # Münzwurf gewonnen (nur Matches, bei denen er bekannt ist)
    coin_won_wins: int = 0
    coin_lost: int = 0
    coin_lost_wins: int = 0

    @property
    def coin_known(self) -> int:
        return self.coin_won + self.coin_lost

    @property
    def losses(self) -> int:
        return self.matches - self.wins - self.draws


class DeckWinStats(NamedTuple):
    deck_name: str
    stats: WinStats
    deck_code: str            # Deck des letzten Matches
    md_deck: str              # MD-Name des Decks beim letzten Match ("" = unbekannt)


class OpponentStats(NamedTuple):
    opp_name: str             # Gegner-Deck (nach Archetypen benannt)
    stats: WinStats           # eigene Bilanz gegen dieses Deck


class SideProfile(NamedTuple):
    """Side-Deck-Profil eines Decks: diese Karten raus, jene rein (Passcode → Kopien)."""
    deck_name: str
    name: str                 # z.B. "Zweiter"
    cards_out: Dict[str, int]
    cards_in: Dict[str, int]


class MdDeck(NamedTuple):
    """Ein Deck in Master Duel (aus der Deck-Auswahl gemerkt): Karten als Konami-IDs."""
    deck_id: str
    name: str
    main: List[int]
    extra: List[int]


def create_tables(con: sqlite3.Connection) -> None:
    con.execute("""CREATE TABLE IF NOT EXISTS cards (
                       id TEXT PRIMARY KEY, name TEXT, type TEXT, frame TEXT, race TEXT,
                       level INTEGER, archetype TEXT, desc TEXT, fetched_at REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS starter_overrides (
                       id TEXT PRIMARY KEY, starter INTEGER NOT NULL, set_at REAL)""")
    # Feste Einstufungen aus starter_list.json (nur in der Grunddatenbank befüllt)
    con.execute("""CREATE TABLE IF NOT EXISTS starter_curated (
                       id TEXT PRIMARY KEY, starter INTEGER NOT NULL, reason TEXT)""")
    # Ergebnis der Textregeln (starter_rules), gültig für die Regel-Version in meta
    con.execute("""CREATE TABLE IF NOT EXISTS starter_auto (
                       id TEXT PRIMARY KEY, starter INTEGER, reason TEXT)""")
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    # Konamis interne Karten-ID (steht im Speicher von Master Duel) für jeden Passcode (auch Artworks)
    con.execute("CREATE TABLE IF NOT EXISTS konami (id TEXT PRIMARY KEY, konami_id INTEGER, name TEXT)")
    con.execute("CREATE INDEX IF NOT EXISTS konami_by_kid ON konami (konami_id)")
    # Artworks, die nur Master Duel kennt (eigene Konami-ID, YGOPRODeck fehlt sie): im Import gelernt
    con.execute("CREATE TABLE IF NOT EXISTS konami_alias (konami_id INTEGER PRIMARY KEY, id TEXT)")
    # Aus den Deck-Guides von Master Duel Meta nachgeladen (starter_guides)
    con.execute("""CREATE TABLE IF NOT EXISTS starter_guide (
                       id TEXT PRIMARY KEY, starter INTEGER NOT NULL, reason TEXT, quote TEXT, set_at REAL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS handtrap_overrides (
                       id TEXT PRIMARY KEY, handtrap INTEGER NOT NULL, set_at REAL)""")
    # Importierte Decks (ein Eintrag pro Deck; erneuter Import desselben Decks rückt es nach oben).
    # cards: Kartennamen fürs Suchen (englisch und in Spielsprache)
    con.execute("""CREATE TABLE IF NOT EXISTS deck_history (
                       id INTEGER PRIMARY KEY AUTOINCREMENT, deck_key TEXT UNIQUE, code TEXT, name TEXT,
                       archetypes TEXT, cards TEXT, main INTEGER, extra INTEGER, imported_at REAL,
                       times INTEGER, result TEXT)""")
    # Duelle aus Master Duel (match_history), eins pro Duell-ID. source: "live" (Ergebnis-Bildschirm) oder
    # "history" (Match History – vollständig: Zugzahl, Gegner-Deck; ersetzt einen Live-Eintrag)
    con.execute("""CREATE TABLE IF NOT EXISTS matches (
                       did TEXT PRIMARY KEY, played_at REAL, mode INTEGER, result INTEGER, first INTEGER,
                       turns INTEGER, finish INTEGER, deck_name TEXT, deck_code TEXT, opp_name TEXT, opp_code TEXT,
                       md_deck TEXT, source TEXT, added_at REAL, coin INTEGER)""")
    # Ältere Tabelle ergänzen (erste Version ohne MD-Deck und Quelle – alte Einträge stammen aus der Match History;
    # bis V9.0 ohne Münzwurf)
    columns = {row[1] for row in con.execute("PRAGMA table_info(matches)")}
    for column, kind in (("md_deck", "TEXT DEFAULT ''"), ("source", "TEXT DEFAULT 'history'"), ("coin", "INTEGER")):
        if column not in columns:
            con.execute(f"ALTER TABLE matches ADD COLUMN {column} {kind}")
    # Decks in Master Duel (aus der Deck-Auswahl gelesen): Welches Deck gerade gewählt ist, steht nur als ID im Spiel
    con.execute("""CREATE TABLE IF NOT EXISTS md_decks (
                       deck_id TEXT PRIMARY KEY, name TEXT, main TEXT, extra TEXT, seen_at REAL)""")
    # Auswertung der Top-Listen je Archetyp (staple_analysis, Master Duel Meta) als JSON
    con.execute("CREATE TABLE IF NOT EXISTS staple_cache (archetype TEXT PRIMARY KEY, data TEXT, fetched_at REAL)")
    # Side-Deck-Profile je Deck (Name nach Archetypen wie im Verlauf), Karten als JSON {Passcode: Kopien}
    con.execute("""CREATE TABLE IF NOT EXISTS side_profiles (deck_name TEXT, name TEXT, cards_out TEXT, cards_in TEXT,
                       saved_at REAL, PRIMARY KEY (deck_name, name))""")


def name_key(name: str) -> str:
    """Kartenname zum Vergleichen: Guides schreiben z.B. "Maliss P Dormouse" statt "Maliss <P> Dormouse"."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def auto_rows(cards: Iterable[CardInfo]) -> list:
    """Starter-Einstufung nach den Textregeln für jede Karte: [(ID, 1/0/None, Grund)]"""
    rows = []
    for info in cards:
        guess = classify(info.card_type, info.frame, info.desc, info.level, info.race)
        rows.append((info.cid, None if guess.starter is None else int(guess.starter), guess.reason))
    return rows


def store_auto(con: sqlite3.Connection, cards: Iterable[CardInfo]) -> None:
    con.executemany("INSERT OR REPLACE INTO starter_auto VALUES (?, ?, ?)", auto_rows(cards))
    con.execute("INSERT OR REPLACE INTO meta VALUES ('rules_version', ?)", (str(RULES_VERSION),))


def konami_rows(data: List[dict]) -> list:
    """YGOPRODeck-Antwort (mit misc=yes) → [(Passcode, Konami-ID, Name)] für alle Artworks."""
    rows = []
    for card in data:
        kid = next((m.get("konami_id") for m in card.get("misc_info", []) if m.get("konami_id")), None)
        if not kid:
            continue
        ids = {str(card.get("id"))} | {str(img.get("id")) for img in card.get("card_images", [])}
        rows += [(cid, int(kid), card.get("name", "")) for cid in ids]
    return rows


def cards_from_api(data: List[dict], wanted: Optional[set] = None) -> Dict[str, CardInfo]:
    """YGOPRODeck-Antwort → {ID: CardInfo}. Alternativ-Artworks haben eigene IDs, gehören aber zur selben Karte."""
    result: Dict[str, CardInfo] = {}
    for card in data:
        known = {str(card.get("id"))} | {str(img.get("id")) for img in card.get("card_images", [])}
        for cid in (known & wanted) if wanted is not None else known:
            result[cid] = CardInfo(cid, card.get("name", ""), card.get("type", ""), card.get("frameType", ""),
                                   card.get("race", ""), card.get("level"), card.get("archetype") or "",
                                   card.get("desc", ""))
    return result


def fetch_cards(ids) -> Dict[str, CardInfo]:
    """Karteninfos (englisch) von YGOPRODeck. Unbekannte IDs fehlen im Ergebnis."""
    result: Dict[str, CardInfo] = {}
    ids = [str(i) for i in ids]
    for start in range(0, len(ids), BATCH):
        chunk = ids[start:start + BATCH]
        resp = requests.get(YGOPRO_API_URL, params={"id": ",".join(chunk)}, timeout=15)
        if resp.status_code == 400:
            continue  # keine der IDs bekannt
        resp.raise_for_status()
        result.update(cards_from_api(resp.json().get("data", []), set(chunk)))
    return result


class CardStatsDB:
    def __init__(self, path: str = DB_FILE, fetch=fetch_cards, base_path: Optional[str] = BASE_DB_FILE):
        self.path = path
        self._fetch = fetch
        self.base_path = base_path if base_path and os.path.exists(base_path) else None
        with self._connect() as con:
            create_tables(con)

    @contextmanager
    def _connect(self, base: bool = False):
        # Eigene Verbindung pro Aufruf: Die Datenbank wird aus dem UI- und dem Lese-Thread benutzt.
        # Die Grunddatenbank nur lesend öffnen (liegt in der exe ggf. in einem schreibgeschützten Ordner).
        if base:
            uri = "file:" + pathname2url(os.path.abspath(self.base_path)) + "?mode=ro"
            con = sqlite3.connect(uri, uri=True, timeout=5)
        else:
            con = sqlite3.connect(self.path, timeout=5)
        try:
            with con:  # Commit bzw. Rollback
                yield con
        finally:
            con.close()

    def _sources(self):
        return [False, True] if self.base_path else [False]  # eigene Datei zuerst (neuere Daten)

    # ── Karteninfos ──
    def info(self, cid: str) -> Optional[CardInfo]:
        for base in self._sources():
            with self._connect(base) as con:
                row = con.execute(f"SELECT {COLUMNS} FROM cards WHERE id = ?", (str(cid),)).fetchone()
            if row:
                return CardInfo(*row)
        return None

    def infos(self, ids: Iterable[str]) -> Dict[str, CardInfo]:
        """Karteninfos vieler Karten auf einmal (unbekannte fehlen) – schneller als info() je Karte."""
        wanted = sorted({str(i) for i in ids if i})
        result: Dict[str, CardInfo] = {}
        for base in self._sources():
            todo = [cid for cid in wanted if cid not in result]
            if not todo:
                break
            with self._connect(base) as con:
                for start in range(0, len(todo), 500):
                    chunk = todo[start:start + 500]
                    result.update((row[0], CardInfo(*row)) for row in con.execute(
                        f"SELECT {COLUMNS} FROM cards WHERE id IN ({','.join('?' * len(chunk))})", chunk))
        return result

    def search_cards(self, text: str, limit: int = 8) -> List[Tuple[str, str]]:
        """Karten in Master Duel, deren Name den Text enthält: [(Passcode, Name)], Namensanfang zuerst."""
        text = text.strip()
        if len(text) < 2:
            return []
        pattern = "%" + text.replace("%", "").replace("_", "") + "%"
        found: Dict[str, str] = {}  # Name → Passcode (Alternativ-Artworks haben eigene Passcodes: der kleinste)
        for base in self._sources():
            with self._connect(base) as con:
                for cid, name in con.execute(
                        """SELECT id, name FROM cards WHERE name LIKE ? AND id IN (SELECT id FROM konami)
                           ORDER BY name LIKE ? DESC, length(name) LIMIT ?""", (pattern, text + "%", limit * 3)):
                    if name not in found or int(cid) < int(found[name]):
                        found[name] = cid
        start = text.lower()
        ordered = sorted(found.items(), key=lambda item: (not item[0].lower().startswith(start), len(item[0])))
        return [(cid, name) for name, cid in ordered[:limit]]

    def missing(self, ids: Iterable[str]) -> list:
        ids = sorted({str(i) for i in ids if i})
        known = set()
        for base in self._sources():
            if not ids:
                break
            with self._connect(base) as con:
                known |= {row[0] for row in con.execute(
                    f"SELECT id FROM cards WHERE id IN ({','.join('?' * len(ids))})", ids)}
        return [cid for cid in ids if cid not in known]

    def ensure(self, ids: Iterable[str]) -> int:
        """
        Lädt alle noch unbekannten Karten (neuer als die Grunddatenbank) online nach und speichert sie.
        Returns: Anzahl der Karten, die danach immer noch fehlen (offline oder unbekannt).
        """
        missing = self.missing(ids)
        if not missing:
            return 0
        try:
            found = self._fetch(missing)
        except Exception as e:
            dlog(f"[STATS] Karteninfos nicht geladen ({e}) – {len(missing)} Karte(n) ohne Stats.")
            return len(missing)
        now = time.time()
        with self._connect() as con:
            con.executemany("INSERT OR REPLACE INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            [(*info, now) for info in found.values()])
            if self._auto_current(con):
                store_auto(con, found.values())
        dlog(f"[STATS] {len(found)} Karte(n) online nachgeschlagen und lokal gespeichert.")
        return len(missing) - len(found)

    def refresh(self, ids: Iterable[str]) -> int:
        """Karten neu von YGOPRODeck laden (z.B. geänderte Kartentexte) und neu einstufen. Returns: Anzahl."""
        found = self._fetch(sorted({str(i) for i in ids if i}))
        now = time.time()
        with self._connect() as con:
            con.executemany("INSERT OR REPLACE INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            [(*info, now) for info in found.values()])
            if self._auto_current(con):
                store_auto(con, found.values())
        return len(found)

    def ids_by_name(self, names: Iterable[str]) -> Dict[str, List[str]]:
        """{Name: [IDs]} – alle Artworks einer Karte, Schreibweise wie in name_key()."""
        wanted = {name_key(n): n for n in names}
        result: Dict[str, List[str]] = {}
        for base in self._sources():
            with self._connect(base) as con:
                for cid, name in con.execute("SELECT id, name FROM cards"):
                    original = wanted.get(name_key(name or ""))
                    if original is not None and cid not in result.get(original, []):
                        result.setdefault(original, []).append(cid)
        return result

    def store_guide(self, verdicts) -> Dict[str, bool]:
        """Einstufungen aus Guides speichern (starter_guides.Verdict). Returns: {ID: Starter ja/nein}"""
        ids = self.ids_by_name(v.name for v in verdicts)
        rows, now = [], time.time()
        for v in verdicts:
            for cid in ids.get(v.name, []):
                rows.append((cid, int(v.starter), f"laut Guide „{v.guide.strip()}“", v.quote, now))
        with self._connect() as con:
            con.executemany("INSERT OR REPLACE INTO starter_guide VALUES (?, ?, ?, ?, ?)", rows)
        return {row[0]: bool(row[1]) for row in rows}

    # ── Konami-IDs (Speicher-Modus) ──
    def konami_ids(self, passcodes: Iterable[str]) -> Dict[str, int]:
        """{Passcode: Konami-ID}; unbekannte werden einmal online nachgeschlagen (YGOPRODeck, misc=yes)."""
        wanted = sorted({str(p) for p in passcodes if p})
        result = self._konami_lookup("id", wanted)
        missing = [p for p in wanted if p not in result]
        if missing:
            try:
                rows = []
                for start in range(0, len(missing), BATCH):
                    resp = requests.get(YGOPRO_API_URL, params={"id": ",".join(missing[start:start + BATCH]),
                                                                "misc": "yes"}, timeout=15)
                    if resp.status_code != 400:
                        resp.raise_for_status()
                        rows += konami_rows(resp.json().get("data", []))
                with self._connect() as con:
                    con.executemany("INSERT OR REPLACE INTO konami VALUES (?, ?, ?)", rows)
                result.update({cid: kid for cid, kid, _ in rows if cid in missing})
            except Exception as e:
                dlog(f"[SPEICHER] Konami-IDs nicht nachgeladen ({e}).")
        return result

    def konami_names(self) -> Dict[int, str]:
        """{Konami-ID: englischer Name} aller bekannten Karten (inkl. gelernter Artworks)."""
        names: Dict[int, str] = {}
        for base in reversed(self._sources()):  # eigene Datei zuletzt → überschreibt
            try:
                with self._connect(base) as con:
                    names.update(con.execute("SELECT konami_id, name FROM konami"))
            except sqlite3.OperationalError:
                pass
        aliases = self.konami_aliases()
        base_ids = self._konami_lookup("id", sorted(set(aliases.values())))
        names.update({kid: names[base_ids[cid]] for kid, cid in aliases.items() if base_ids.get(cid) in names})
        return names

    def konami_passcodes(self) -> Dict[int, str]:
        """{Konami-ID: Passcode} aller bekannten Karten (bei mehreren Artworks der kleinste Passcode)."""
        result: Dict[int, str] = {}
        for base in reversed(self._sources()):
            try:
                with self._connect(base) as con:
                    result.update(con.execute("SELECT konami_id, MIN(id) FROM konami GROUP BY konami_id"))
            except sqlite3.OperationalError:
                pass
        result.update(self.konami_aliases())
        return result

    def konami_aliases(self) -> Dict[int, str]:
        """{Konami-ID: Passcode} der Artworks, die nur Master Duel kennt (im Import gelernt)."""
        with self._connect() as con:
            return dict(con.execute("SELECT konami_id, id FROM konami_alias"))

    def learn_konami_alias(self, konami_id: int, passcode: str) -> None:
        """Ein Artwork aus Master Duel (eigene Konami-ID) einer Karte zuordnen – gilt ab dem nächsten Import."""
        with self._connect() as con:
            con.execute("INSERT OR REPLACE INTO konami_alias VALUES (?, ?)", (int(konami_id), str(passcode)))

    def _konami_lookup(self, column: str, values: List[str]) -> Dict[str, int]:
        result: Dict[str, int] = {}
        for base in self._sources():
            if not values:
                break
            try:
                with self._connect(base) as con:
                    for start in range(0, len(values), 500):
                        chunk = values[start:start + 500]
                        result.update(con.execute(f"SELECT {column}, konami_id FROM konami WHERE {column} IN "
                                                  f"({','.join('?' * len(chunk))})", chunk))
            except sqlite3.OperationalError:  # ältere Datenbank ohne Tabelle
                continue
        return result

    # ── Starter ──
    def starter(self, cid: str) -> StarterInfo:
        with self._connect() as con:
            row = con.execute("SELECT starter FROM starter_overrides WHERE id = ?", (str(cid),)).fetchone()
        if row is not None:
            return StarterInfo(bool(row[0]), "von dir festgelegt", True)
        guide = self._query(False, "SELECT starter, reason FROM starter_guide WHERE id = ?", cid)
        if guide is not None:
            return StarterInfo(bool(guide[0]), guide[1], False)
        if self.base_path:
            curated = self._query(True, "SELECT starter, reason FROM starter_curated WHERE id = ?", cid)
            if curated is not None:
                return StarterInfo(bool(curated[0]), curated[1], False)
        # Gespeichertes Ergebnis der Textregeln (eigene Datei: online nachgeladene Karten, dann Grunddatenbank)
        for base in self._sources():
            stored = self._query(base, "SELECT starter, reason FROM starter_auto WHERE id = ?", cid, current=True)
            if stored is not None:
                return StarterInfo(None if stored[0] is None else bool(stored[0]), stored[1], False)
        info = self.info(cid)
        if info is None:
            return StarterInfo(None, "keine Kartendaten (offline?)", False)
        guess = classify(info.card_type, info.frame, info.desc, info.level, info.race)
        return StarterInfo(guess.starter, guess.reason, False)

    def _query(self, base: bool, sql: str, cid: str, current: bool = False):
        """Eine Zeile zur Karte; current=True: nur, wenn die gespeicherten Ergebnisse zur Regel-Version passen."""
        try:
            with self._connect(base) as con:
                if current and not self._auto_current(con):
                    return None
                return con.execute(sql, (str(cid),)).fetchone()
        except sqlite3.OperationalError:  # ältere Datenbank ohne diese Tabelle
            return None

    @staticmethod
    def _auto_current(con: sqlite3.Connection) -> bool:
        """Gespeicherte Starter-Ergebnisse stammen von den aktuellen Regeln (sonst neu berechnen)."""
        row = con.execute("SELECT value FROM meta WHERE key = 'rules_version'").fetchone()
        if row is None:
            # Neue eigene Datei ohne Einträge: ab jetzt mit der aktuellen Version befüllen
            if con.execute("SELECT COUNT(*) FROM starter_auto").fetchone()[0] == 0:
                return True
            return False
        return row[0] == str(RULES_VERSION)

    def set_starter(self, cid: str, value: Optional[bool]) -> None:
        """Eigene Einstufung speichern; None = wieder automatisch."""
        with self._connect() as con:
            if value is None:
                con.execute("DELETE FROM starter_overrides WHERE id = ?", (str(cid),))
            else:
                con.execute("INSERT OR REPLACE INTO starter_overrides VALUES (?, ?, ?)",
                            (str(cid), int(bool(value)), time.time()))

    # ── Handtraps ──
    def handtrap(self, cid: str) -> HandtrapInfo:
        with self._connect() as con:
            row = con.execute("SELECT handtrap FROM handtrap_overrides WHERE id = ?", (str(cid),)).fetchone()
        if row is not None:
            return HandtrapInfo(bool(row[0]), "von dir festgelegt", True)
        info = self.info(cid)
        if info is None:
            return HandtrapInfo(None, "keine Kartendaten (offline?)", False)
        guess = classify_handtrap(info.card_type, info.frame, info.desc, info.name)
        return HandtrapInfo(guess.handtrap, guess.reason, False)

    def set_handtrap(self, cid: str, value: Optional[bool]) -> None:
        """Eigene Einstufung speichern; None = wieder automatisch."""
        with self._connect() as con:
            if value is None:
                con.execute("DELETE FROM handtrap_overrides WHERE id = ?", (str(cid),))
            else:
                con.execute("INSERT OR REPLACE INTO handtrap_overrides VALUES (?, ?, ?)",
                            (str(cid), int(bool(value)), time.time()))

    # ── Verlauf der importierten Decks ──
    def add_history(self, code: str, card_ids: Iterable[str], names: Iterable[str] = (), result: str = "ok",
                    now: Optional[float] = None) -> int:
        """
        Importiertes Deck merken. Dasselbe Deck (gleiche Karten, egal in welcher Reihenfolge) noch einmal →
        derselbe Eintrag rückt nach oben. Name = Archetypen des Decks. Returns: ID des Eintrags.
        """
        card_ids = [str(c) for c in card_ids]
        key = ",".join(sorted(card_ids))
        name, archetypes, main, extra, english = self._describe(card_ids)
        cards = "\n".join(sorted({n for n in [*english, *names] if n}))
        now = time.time() if now is None else now
        with self._connect() as con:
            con.execute("""INSERT INTO deck_history (deck_key, code, name, archetypes, cards, main, extra,
                                                     imported_at, times, result)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                           ON CONFLICT(deck_key) DO UPDATE SET code = excluded.code, cards = excluded.cards,
                               imported_at = excluded.imported_at, times = times + 1, result = excluded.result""",
                        (key, code.strip(), name, archetypes, cards, main, extra, now, result))
            return con.execute("SELECT id FROM deck_history WHERE deck_key = ?", (key,)).fetchone()[0]

    def history(self, search: str = "", limit: int = 200) -> List[HistoryEntry]:
        """Importierte Decks, das letzte zuerst. search: alle Wörter müssen in Name, Archetyp oder Karten vorkommen."""
        words = search.lower().split()
        where = " AND ".join(r"LOWER(name || ' ' || archetypes || ' ' || cards) LIKE ? ESCAPE '\'"
                             for _ in words) or "1"
        like = ["%" + w.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%" for w in words]
        with self._connect() as con:
            rows = con.execute(f"""SELECT id, code, name, archetypes, main, extra, imported_at, times, result
                                   FROM deck_history WHERE {where} ORDER BY imported_at DESC, id DESC LIMIT ?""",
                               (*like, limit)).fetchall()
        return [HistoryEntry(*row) for row in rows]

    def delete_history(self, entry_id: int) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM deck_history WHERE id = ?", (entry_id,))

    def deck_name(self, card_ids: Iterable[str]) -> str:
        """Name eines Decks aus seinen Archetypen (wie im Verlauf)."""
        return self._describe([str(c) for c in card_ids])[0]

    # ── Matches (Winrate-Tracker) ──
    def match_sources(self, dids: Iterable[str]) -> Dict[str, str]:
        """{Duell-ID: "live"/"history"} der schon gespeicherten Matches."""
        dids = sorted({str(d) for d in dids})
        if not dids:
            return {}
        with self._connect() as con:
            return dict(con.execute(
                f"SELECT did, source FROM matches WHERE did IN ({','.join('?' * len(dids))})", dids))

    def add_matches(self, rows) -> None:
        """
        rows: [(MatchRecord, Deck-Name, Deck-Code, Gegner-Deck-Name, Gegner-Deck-Code)]. Neue Duelle kommen dazu;
        ein Live-Eintrag wird durch denselben Eintrag aus der Match History ersetzt (MD-Deckname und Münzwurf bleiben).
        """
        now = time.time()
        with self._connect() as con:
            for r, name, code, opp_name, opp_code in rows:
                old = con.execute("SELECT md_deck, coin FROM matches WHERE did = ?", (r.did,)).fetchone()
                coin = r.coin if r.coin is not None else (old[1] if old else None)
                # Spalten mit Namen: in einer ergänzten Tabelle (siehe create_tables) stehen sie anders
                con.execute("""INSERT OR REPLACE INTO matches (did, played_at, mode, result, first, turns, finish,
                                   deck_name, deck_code, opp_name, opp_code, md_deck, source, added_at, coin)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (r.did, r.played_at, r.mode, r.result, None if r.first is None else int(r.first),
                             r.turns, r.finish, name, code, opp_name, opp_code,
                             r.md_deck or (old[0] if old else "") or "", r.source, now,
                             None if coin is None else int(coin)))

    def remember_md_decks(self, decks: Iterable[MdDeck]) -> None:
        with self._connect() as con:
            con.executemany("INSERT OR REPLACE INTO md_decks VALUES (?, ?, ?, ?, ?)",
                            [(d.deck_id, d.name, ",".join(map(str, d.main)), ",".join(map(str, d.extra)),
                              time.time()) for d in decks])

    def md_deck(self, deck_id: str) -> Optional[MdDeck]:
        with self._connect() as con:
            row = con.execute("SELECT deck_id, name, main, extra FROM md_decks WHERE deck_id = ?",
                              (str(deck_id),)).fetchone()
        if row is None:
            return None
        ids = lambda text: [int(k) for k in text.split(",") if k]  # noqa: E731
        return MdDeck(row[0], row[1], ids(row[2]), ids(row[3]))

    # ── Side-Deck-Profile ──
    def side_profiles(self, deck_name: Optional[str]) -> List[SideProfile]:
        """Profile eines Decks (None = aller Decks), das älteste zuerst."""
        with self._connect() as con:
            rows = con.execute("SELECT deck_name, name, cards_out, cards_in FROM side_profiles "
                               "WHERE ? IS NULL OR deck_name = ? ORDER BY saved_at", (deck_name, deck_name)).fetchall()
        result = []
        for deck, name, cards_out, cards_in in rows:
            try:
                result.append(SideProfile(deck, name, json.loads(cards_out), json.loads(cards_in)))
            except ValueError:
                continue  # beschädigter Eintrag
        return result

    def save_side_profile(self, profile: SideProfile) -> None:
        with self._connect() as con:
            con.execute("INSERT OR REPLACE INTO side_profiles VALUES (?, ?, ?, ?, ?)",
                        (profile.deck_name, profile.name, json.dumps(profile.cards_out),
                         json.dumps(profile.cards_in), time.time()))

    def delete_side_profile(self, deck_name: str, name: str) -> None:
        with self._connect() as con:
            con.execute("DELETE FROM side_profiles WHERE deck_name = ? AND name = ?", (deck_name, name))

    # ── Top-Listen (Staple-Analyse) ──
    def staples(self, archetype: str) -> Optional[dict]:
        with self._connect() as con:
            row = con.execute("SELECT data FROM staple_cache WHERE archetype = ?", (archetype.lower(),)).fetchone()
        try:
            return json.loads(row[0]) if row else None
        except ValueError:
            return None

    def store_staples(self, archetype: str, data: dict) -> None:
        with self._connect() as con:
            con.execute("INSERT OR REPLACE INTO staple_cache VALUES (?, ?, ?)",
                        (archetype.lower(), json.dumps(data), data.get("fetched_at", time.time())))

    @staticmethod
    def _match_filter(deck_name: Optional[str], mode: Optional[int], since: Optional[float] = None):
        where, args = [], []
        if deck_name is not None:
            where.append("deck_name = ?")
            args.append(deck_name)
        if mode is not None:
            where.append("mode = ?")
            args.append(mode)
        if since is not None:
            where.append("played_at >= ?")
            args.append(since)
        return " AND ".join(where) or "1", args

    def matches(self, deck_name: Optional[str] = None, mode: Optional[int] = None,
                limit: int = 200, since: Optional[float] = None) -> List[MatchEntry]:
        """Gespeicherte Matches, das letzte zuerst (optional nur ein Deck, ein Modus bzw. ab einem Zeitpunkt)."""
        where, args = self._match_filter(deck_name, mode, since)
        with self._connect() as con:
            rows = con.execute(f"""SELECT did, played_at, mode, result, first, turns, finish, deck_name, deck_code,
                                          opp_name, opp_code, md_deck, coin FROM matches WHERE {where}
                                   ORDER BY played_at DESC LIMIT ?""", (*args, limit)).fetchall()
        flag = lambda value: None if value is None else bool(value)  # noqa: E731
        return [MatchEntry(*row[:4], flag(row[4]), *row[5:-1], flag(row[-1])) for row in rows]

    def win_stats(self, deck_name: Optional[str] = None, mode: Optional[int] = None,
                  since: Optional[float] = None) -> WinStats:
        where, args = self._match_filter(deck_name, mode, since)
        with self._connect() as con:
            row = con.execute(f"""{self._STATS_SQL} FROM matches WHERE {where}""", args).fetchone()
        return WinStats(*(value or 0 for value in row))

    def deck_win_stats(self, mode: Optional[int] = None, since: Optional[float] = None) -> List[DeckWinStats]:
        """Winrate je Deck (nach Name), das zuletzt gespielte zuerst."""
        where, args = self._match_filter(None, mode, since)
        with self._connect() as con:
            rows = con.execute(f"""{self._STATS_SQL}, deck_name,
                                       (SELECT deck_code FROM matches AS m WHERE m.deck_name = matches.deck_name
                                        ORDER BY played_at DESC LIMIT 1),
                                       (SELECT md_deck FROM matches AS m WHERE m.deck_name = matches.deck_name
                                        AND md_deck != '' ORDER BY played_at DESC LIMIT 1)
                                   FROM matches WHERE {where} GROUP BY deck_name ORDER BY MAX(played_at) DESC""",
                               args).fetchall()
        return [DeckWinStats(row[-3], WinStats(*(value or 0 for value in row[:-3])), row[-2] or "", row[-1] or "")
                for row in rows]

    def opponent_stats(self, deck_name: Optional[str] = None, mode: Optional[int] = None,
                       since: Optional[float] = None) -> List[OpponentStats]:
        """Eigene Bilanz je Gegner-Deck (nur Matches mit bekanntem Gegner), das häufigste zuerst."""
        where, args = self._match_filter(deck_name, mode, since)
        with self._connect() as con:
            rows = con.execute(f"""{self._STATS_SQL}, opp_name FROM matches WHERE {where} AND opp_name != ''
                                   GROUP BY opp_name ORDER BY COUNT(*) DESC, MAX(played_at) DESC""",
                               args).fetchall()
        return [OpponentStats(row[-1], WinStats(*(value or 0 for value in row[:-1]))) for row in rows]

    _STATS_SQL = """SELECT COUNT(*), SUM(result = 1), SUM(result = 3), SUM(first = 1), SUM(first = 1 AND result = 1),
                           SUM(first = 0), SUM(first = 0 AND result = 1), MAX(played_at),
                           SUM(coin = 1), SUM(coin = 1 AND result = 1), SUM(coin = 0), SUM(coin = 0 AND result = 1)"""

    def _describe(self, card_ids: List[str]):
        """(Name, Archetypen, Main, Extra, englische Kartennamen) aus den gespeicherten Kartendaten."""
        weight: Dict[str, int] = {}
        staples = set()  # Archetypen von Handtraps (Mulcharmy, "C" …)
        main = extra = 0
        names = []
        for cid in card_ids:
            info = self.info(cid)
            if info is None:
                main += 1
                continue
            names.append(info.name)
            if (info.frame or "").lower().startswith(EXTRA_FRAMES):
                extra += 1
            else:
                main += 1
            if info.archetype:
                weight[info.archetype] = weight.get(info.archetype, 0) + 1
                if classify_handtrap(info.card_type, info.frame, info.desc, info.name).handtrap:
                    staples.add(info.archetype)
        ranked = sorted(weight, key=lambda a: -weight[a])
        # Name: die Archetypen mit den meisten Karten (mind. HISTORY_MIN_COPIES), höchstens zwei – ohne Handtraps
        # (3× Maxx "C" macht noch kein „"C"“-Deck)
        main_types = [a for a in ranked if weight[a] >= HISTORY_MIN_COPIES and a not in staples][:2]
        if main_types:
            name = " / ".join(main_types)
        elif names:
            name = max(set(names), key=lambda n: (names.count(n), n))  # häufigste Karte
        else:
            name = f"Deck mit {len(card_ids)} Karten"
        return name, ", ".join(ranked), main, extra, names
