"""
Kartenliste für den Deck-Export: gelesener Kartenname → Karten-ID.

Die Liste kommt einmalig von YGOPRODeck (eine Anfrage für alle Karten) und wird als kompakte
Datei neben dem Programm gespeichert. Nach MAX_AGE_DAYS wird sie neu geladen, damit neue
Karten dazukommen.
"""

import difflib
import json
import os
import time
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

import requests

from app_paths import APP_DIR
from debug_log import dlog
from utils import YGOPRO_API_URL, clean_text

MAX_AGE_DAYS = 7
EXTRA_FRAMES = ("fusion", "synchro", "xyz", "link")  # auch *_pendulum
MIN_PREFIX = 12          # So viele Zeichen müssen bei einem abgeschnittenen Namen passen
SURE_RATIO = 0.92        # Ähnlichkeit, ab der ein ungenau gelesener Name als sicher gilt
SURE_MARGIN = 0.04       # … wenn der Zweitbeste so viel schlechter ist
MIN_RATIO = 0.75         # Darunter: Karte nicht erkannt


def db_file(lang: str) -> str:
    return os.path.join(APP_DIR, f"md_card_list_{lang}.json")


class CardMatch(NamedTuple):
    cid: Optional[str]
    name: str
    sure: bool
    note: str = ""


class CardDB:
    def __init__(self, cards: List[Tuple[str, str, str]]):
        """cards: [(id, name, frameType)]"""
        self.main: Dict[str, Tuple[str, str]] = {}
        self.extra: Dict[str, Tuple[str, str]] = {}
        for cid, name, frame in cards:
            if frame in ("token", "skill"):
                continue
            pool = self.extra if frame.startswith(EXTRA_FRAMES) else self.main
            pool.setdefault(clean_text(name), (str(cid), name))
        self._names = {False: list(self.main), True: list(self.extra)}
        self._identified: Dict[str, Optional[str]] = {}  # Cache für identify()

    def __len__(self):
        return len(self.main) + len(self.extra)

    def identify(self, s_c: str) -> Optional[str]:
        """
        Welche Karte ist das EINDEUTIG? Nur exakter Name oder eindeutig abgeschnittener Name
        (kein Raten), egal ob Main oder Extra Deck. Returns: Kartenname oder None.
        """
        if s_c in self._identified:
            return self._identified[s_c]
        texts = [s_c] + ([s_c[1:]] if s_c.startswith("l") and len(s_c) > 1 else [])
        result = None
        for text in texts:
            hit = self.main.get(text) or self.extra.get(text)
            if hit:
                result = hit[1]
                break
        else:
            for text in texts:
                if len(text) < MIN_PREFIX:
                    continue
                hits = [n for pool in (False, True) for n in self._names[pool] if n.startswith(text)]
                if len(hits) == 1:
                    result = (self.main.get(hits[0]) or self.extra.get(hits[0]))[1]
                    break
        self._identified[s_c] = result
        return result

    def match(self, s_c: str, extra: bool) -> CardMatch:
        """Bester Treffer für einen gelesenen Namen (clean_text) aus dem Main- oder Extra Deck."""
        pool = self.extra if extra else self.main
        if not s_c:
            return CardMatch(None, "", False, "nichts gelesen")
        # Führendes 'l' ist ein bekanntes Artefakt (Symbol links vom Namen)
        texts = [s_c] + ([s_c[1:]] if s_c.startswith("l") and len(s_c) > 1 else [])

        for text in texts:
            if text in pool:
                cid, name = pool[text]
                return CardMatch(cid, name, True)

        # Im Detail-Panel abgeschnittener Name: Anfang muss exakt passen
        for text in texts:
            if len(text) < MIN_PREFIX:
                continue
            hits = [n for n in self._names[extra] if n.startswith(text)]
            if len(hits) == 1:
                cid, name = pool[hits[0]]
                return CardMatch(cid, name, True)
            if hits:
                hits.sort(key=len)
                cid, name = pool[hits[0]]
                others = ", ".join(pool[h][1] for h in hits[1:3])
                return CardMatch(cid, name, False, f"abgeschnitten, könnte auch {others} sein")

        # Ungenau gelesen: ähnlichster Name (auch mit dem Anfang längerer Namen verglichen)
        scored = {}
        for text in texts:
            for name in difflib.get_close_matches(text, self._names[extra], n=5, cutoff=MIN_RATIO - 0.1):
                scored[name] = max(scored.get(name, 0.0), difflib.SequenceMatcher(None, text, name).ratio())
            if len(text) >= MIN_PREFIX:
                for name in self._names[extra]:
                    if len(name) > len(text) and name[:3] == text[:3]:
                        ratio = difflib.SequenceMatcher(None, text, name[:len(text)]).ratio()
                        if ratio > scored.get(name, 0.0):
                            scored[name] = ratio
        if not scored:
            return CardMatch(None, "", False, "keine passende Karte gefunden")
        ranked = sorted(scored.items(), key=lambda kv: -kv[1])
        best, ratio = ranked[0]
        if ratio < MIN_RATIO:
            return CardMatch(None, "", False, f"keine passende Karte gefunden (am ähnlichsten: "
                                              f"{pool[best][1]}, {ratio:.0%})")
        second = ranked[1][1] if len(ranked) > 1 else 0.0
        cid, name = pool[best]
        sure = ratio >= SURE_RATIO and ratio - second >= SURE_MARGIN
        return CardMatch(cid, name, sure, "" if sure else f"unsicher gelesen ({ratio:.0%})")


def _download(lang: str) -> List[Tuple[str, str, str]]:
    params = {} if lang == "en" else {"language": lang}
    resp = requests.get(YGOPRO_API_URL, params=params, timeout=90)
    resp.raise_for_status()
    return [(str(c["id"]), c["name"], c.get("frameType", "")) for c in resp.json().get("data", [])]


def _load_file(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data.get("cards"), list) else None
    except (OSError, ValueError):
        return None


def load_card_list(lang: str, status: Callable[[str], None] = lambda text: None) -> List[Tuple[str, str, str]]:
    """Kartenliste aus der Datei, bei Bedarf (fehlt / älter als MAX_AGE_DAYS) neu von YGOPRODeck."""
    path = db_file(lang)
    cached = _load_file(path)
    if cached and time.time() - cached.get("saved_at", 0) < MAX_AGE_DAYS * 86400:
        return [tuple(c) for c in cached["cards"]]

    status("Lade Kartenliste…")
    try:
        cards = _download(lang)
    except Exception as e:
        if cached:
            dlog(f"[KARTENLISTE] Aktualisieren fehlgeschlagen ({e}) → nutze gespeicherte Liste.")
            return [tuple(c) for c in cached["cards"]]
        raise RuntimeError(f"Die Kartenliste konnte nicht von YGOPRODeck geladen werden: {e}") from e
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"saved_at": time.time(), "cards": cards}, f, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as e:
        dlog(f"[KARTENLISTE] Konnte nicht gespeichert werden: {e}")
    dlog(f"[KARTENLISTE] {len(cards)} Karten ({lang}) geladen.")
    return cards


def load_card_db(lang: str, status: Callable[[str], None] = lambda text: None) -> CardDB:
    """
    Kartenliste in der Spielsprache. Für andere Sprachen kommen die englischen Namen dazu:
    Nicht jede Karte hat bei YGOPRODeck einen Namen in jeder Sprache.
    """
    cards = load_card_list(lang, status)
    if lang != "en":
        cards = cards + load_card_list("en", status)
    return CardDB(cards)
