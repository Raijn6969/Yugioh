"""
Lokale Mini-Datenbank für Karten-Stats (SQLite).

- md_card_stats_base.db: alle Karten (Text, Typ, Stufe, Archetyp) und die festen Starter-Einstufungen aus
  starter_list.json, wird mit der exe ausgeliefert (build_card_db.py erzeugt sie beim Bauen). Nur lesend –
  damit geht alles ab dem ersten Start offline.
- md_card_stats.db neben dem Programm: Karten, die neuer sind als die Grunddatenbank (einmalig online
  bei YGOPRODeck nachgeschlagen), Starter-Einstufungen aus den Guides von Master Duel Meta (im Deck-Fenster
  über Optionen nachgeladen) und eigene Starter-Korrekturen aus dem Deck-Fenster (haben Vorrang).

Ob eine Karte ein Starter ist, wird aus dem gespeicherten Kartentext abgeleitet (starter_rules).
"""

import os
import re
import sqlite3
import time
from contextlib import contextmanager
from typing import Dict, Iterable, List, NamedTuple, Optional
from urllib.request import pathname2url

import requests

from app_paths import APP_DIR, BUNDLE_DIR
from debug_log import dlog
from starter_rules import RULES_VERSION, classify
from utils import YGOPRO_API_URL

DB_FILE = os.path.join(APP_DIR, "md_card_stats.db")
BASE_DB_NAME = "md_card_stats_base.db"
BASE_DB_FILE = os.path.join(BUNDLE_DIR, BASE_DB_NAME)
BATCH = 50  # IDs pro Anfrage (YGOPRODeck erlaubt mehrere IDs, der Link bleibt kurz genug)
COLUMNS = "id, name, type, frame, race, level, archetype, desc"


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
