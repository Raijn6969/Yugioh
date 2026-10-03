"""
Hilfsfunktionen: Kartennamen per YGOPRODeck-API (mit Cache), Text-Normalisierung,
Deck-Code aus der Zwischenablage lesen, OCR-Cache.
"""

import re
import time
import unicodedata
import pyperclip
import requests
import base64
import struct
import json
import os

from app_paths import CACHE_FILE
from debug_log import dlog

_card_cache = None


def _load_cache():
    global _card_cache
    if _card_cache is None:
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    _card_cache = json.load(f)
            except Exception as e:
                dlog(f"[CACHE] {CACHE_FILE} nicht lesbar ({e}) → Namen werden neu geladen.")
                _card_cache = {}
        else:
            _card_cache = {}
    return _card_cache


def _save_cache():
    if _card_cache is not None:
        try:
            # Erst in Temp-Datei schreiben, dann atomar ersetzen → Cache nie halb geschrieben
            tmp_path = CACHE_FILE + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(_card_cache, f, indent=4)
            os.replace(tmp_path, CACHE_FILE)
        except Exception as e:
            dlog(f"[CACHE] Speichern fehlgeschlagen: {e}")


YGOPRO_API_URL = "https://db.ygoprodeck.com/api/v7/cardinfo.php"


def _request_cards(card_ids, lang: str):
    """
    Holt Name und Kartentyp mehrerer Karten mit EINER Anfrage (YGOPRODeck erlaubt ids=a,b,c).
    Unbekannte IDs fehlen einfach im Ergebnis. Alternativ-Artworks werden über
    card_images ihrer Karte zugeordnet.
    Returns: {cid: (name, typ)}, oder None bei Netzwerk-/Serverfehler.
    """
    params = {"id": ",".join(card_ids)}
    # YGOPRODeck nutzt für Englisch den Standard-Link ohne Sprach-Parameter
    if lang != "en":
        params["language"] = lang

    resp = requests.get(YGOPRO_API_URL, params=params, timeout=10)
    if resp.status_code == 400:
        return {}  # Keine der IDs bekannt
    if resp.status_code != 200:
        return None

    wanted = set(card_ids)
    cards = {}
    for card in resp.json().get("data", []):
        known_ids = {str(card.get("id"))} | {str(img.get("id")) for img in card.get("card_images", [])}
        for cid in known_ids & wanted:
            cards[cid] = (card["name"], card.get("type", ""))
    return cards


def _type_key(cid: str) -> str:
    return f"type:{cid}"


def _cache_key(cid: str, lang: str) -> str:
    # Englisch ohne Präfix (kompatibel mit bestehenden Caches), andere Sprachen z.B. "de:14558127"
    return cid if lang == "en" else f"{lang}:{cid}"


def _fetch_uncached(card_ids, lang: str, cache: dict, result: dict) -> None:
    """Holt alle noch fehlenden IDs mit EINER Anfrage (bis zu 3 Versuche) und cacht sie."""
    missing = [cid for cid in card_ids if cid not in result]
    if not missing:
        return
    fetched = None
    for attempt in range(3):
        try:
            fetched = _request_cards(missing, lang)
        except Exception as e:
            dlog(f"[API] Anfrage fehlgeschlagen (Versuch {attempt + 1}/3): {e}")
            fetched = None
        if fetched is not None:
            break
        time.sleep(1.0 + attempt)

    if fetched:
        result.update({cid: name for cid, (name, _) in fetched.items()})
        cache.update({_cache_key(cid, lang): name for cid, (name, _) in fetched.items()})
        # Kartentyp ist sprachunabhängig (von der API immer englisch, z.B. "Fusion Monster")
        cache.update({_type_key(cid): typ for cid, (_, typ) in fetched.items() if typ})
        _save_cache()


def fetch_card_names(card_ids, lang: str = "en") -> dict:
    """
    Löst alle Karten-IDs zu Namen auf: erst aus dem Cache, der Rest mit einer gebündelten
    API-Anfrage (statt eine Anfrage pro Karte). Das bleibt sicher unter dem YGOPRODeck-Limit
    von 20 Anfragen/Sekunde, dessen Überschreitung eine einstündige IP-Sperre auslöst.

    Hat eine Karte keinen Namen in der gewählten Sprache (z.B. manche Karten auf Deutsch),
    wird der englische Name verwendet, damit sie nicht ganz aus dem Import fällt.
    Returns: {cid: name} für alle auflösbaren IDs.
    """
    cache = _load_cache()
    ids = [str(c) for c in card_ids]
    result = {cid: cache[_cache_key(cid, lang)] for cid in ids if _cache_key(cid, lang) in cache}
    _fetch_uncached(ids, lang, cache, result)

    if lang != "en":
        still_missing = [cid for cid in ids if cid not in result]
        if still_missing:
            english = {cid: cache[_cache_key(cid, "en")] for cid in still_missing
                       if _cache_key(cid, "en") in cache}
            _fetch_uncached(still_missing, "en", cache, english)
            for cid, name in english.items():
                dlog(f"[API] Kein Name in Sprache '{lang}' für ID {cid} → englischer Name: '{name}'")
            result.update(english)

    return result


EXTRA_DECK_TYPES = ("fusion", "synchro", "xyz", "link")


def is_extra_deck_type(card_type: str) -> bool:
    """Landet eine Karte dieses Typs im Extra Deck (Fusion/Synchro/XYZ/Link)?"""
    card_type = (card_type or "").lower()
    return any(t in card_type for t in EXTRA_DECK_TYPES)


def fetch_card_types(card_ids) -> dict:
    """
    Kartentypen (z.B. "Spell Card", "Fusion Monster") aus dem Cache; fehlende werden mit einer
    gebündelten Anfrage nachgeladen. Returns: {cid: typ} für alle bekannten IDs.
    """
    cache = _load_cache()
    ids = [str(c) for c in card_ids]
    missing = [cid for cid in ids if _type_key(cid) not in cache]
    if missing:
        _fetch_uncached(missing, "en", cache, {})
    return {cid: cache[_type_key(cid)] for cid in ids if _type_key(cid) in cache}

# =====================================================================
# 2. STANDALONE HILFSFUNKTIONEN FÜR DIE OVERLAY-SCHNITTSTELLE
# =====================================================================

def clean_text(text):
    """
    Vergleichsform eines Namens: nur Buchstaben/Ziffern, klein geschrieben.
    Umlaute und Akzente werden zu Grundbuchstaben (ü→u, é→e, ß→ss), statt wegzufallen.
    Sonst würde "Aschenblüte" zu "aschenblte", die Texterkennung liest aber "aschenblute".
    """
    if not text:
        return ""
    if not text.isascii():
        text = unicodedata.normalize("NFKD", text.replace("ß", "ss").replace("ẞ", "SS"))
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r'[^a-zA-Z0-9]', '', text).lower()


def sanitize_name(name):
    if not name:
        return ""
    name = name.replace('—', '-').replace('"', '').replace('!', '')
    return name.strip()


class DeckCodeError(ValueError):
    """Deck-Code in der Zwischenablage ist beschädigt (lieber gar nicht importieren als ein falsches Deck)."""


def _parse_ydke(payload: str) -> list:
    """ydke://MAIN!EXTRA!SIDE! – Master Duel hat kein Side Deck, also nur Main und Extra."""
    ids = []
    for section in payload.split("!")[:2]:
        section = section.strip()
        if not section:
            continue
        try:
            data = base64.b64decode(section + "=" * (-len(section) % 4), validate=True)
        except ValueError as e:
            raise DeckCodeError(f"Der YDKE-Code ist beschädigt ({e}).") from e
        if len(data) % 4:
            raise DeckCodeError("Der YDKE-Code ist unvollständig (abgeschnitten?).")
        for (cid,) in struct.iter_unpack("<I", data):
            if cid == 0:
                raise DeckCodeError("Der YDKE-Code enthält eine ungültige Karten-ID (0).")
            ids.append(str(cid))
    return ids


def _parse_ydk(text: str) -> list:
    """.ydk-Text oder einfache ID-Liste. Karten nach "!side" (Side Deck) werden ignoriert."""
    ids = []
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("!side"):
            break
        if line.isdigit():
            ids.append(line)
    return ids


def parse_deck_code(text: str) -> list:
    """
    Karten-IDs (Main + Extra) aus einem YDKE-Link oder .ydk-Text.
    Raises DeckCodeError, wenn ein YDKE-Code beschädigt ist. Kein Deck-Code → [].
    """
    text = (text or "").strip()
    if text.startswith("ydke://"):
        return _parse_ydke(text[len("ydke://"):])
    return _parse_ydk(text)


def search_text(name: str) -> str:
    """
    Suchbegriff fürs Suchfeld von Master Duel. Teile in spitzen Klammern ("Maliss <P> Chessy Cat") findet die
    Suche nicht (das Eingabefeld behandelt sie als Formatierung) → nur den längsten Teil ohne sie suchen.
    """
    if "<" not in name and ">" not in name:
        return name
    parts = [p.strip() for p in re.split(r"<[^>]*>|[<>]", name)]
    return max(parts, key=len) or name


def parse_clipboard():
    """Deck-Code aus der Zwischenablage (siehe parse_deck_code)."""
    return parse_deck_code(pyperclip.paste())

_ocr_cache = {}
OCR_CACHE_LIMIT = 5000  # Bei mehreren Importen ohne Neustart nicht unbegrenzt wachsen


def get_cached_ocr(img_hash, ocr_func, img):
    if img_hash in _ocr_cache:
        return _ocr_cache[img_hash]
    text = ocr_func(img)
    if len(_ocr_cache) >= OCR_CACHE_LIMIT:
        _ocr_cache.clear()
    _ocr_cache[img_hash] = text
    return text