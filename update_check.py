"""
Update-Hinweis: Beim Start schaut der Importer einmal im Hintergrund nach, ob es einen neueren Release gibt.

Quelle ist der neueste GitHub-Release (ein Push ohne Release löst keinen Hinweis aus): Version = Tag ("v9.4" → "9.4"),
numerisch mit der eigenen Version verglichen ("9.10" ist neuer als "9.2"). Hängt am Release eine .zip, kann der
Importer sich damit selbst ersetzen (updater.py, nur auf Klick des Nutzers). Ohne Netz oder bei jedem Fehler passiert
nichts außer einem Log-Eintrag.
"""

import json
import re
import urllib.request
from typing import Callable, NamedTuple, Optional, Tuple

RELEASE_API = "https://api.github.com/repos/Raijn6969/Yugioh/releases/latest"
RELEASES_URL = "https://github.com/Raijn6969/Yugioh/releases/latest"
TIMEOUT = 5.0           # Sekunden
MAX_BYTES = 256 * 1024  # die Release-Antwort ist klein – mehr wird nicht gelesen
# Nur von diesen Hosts wird heruntergeladen (GitHub leitet Release-Dateien auf objects.githubusercontent.com um)
DOWNLOAD_PREFIXES = ("https://github.com/", "https://objects.githubusercontent.com/")


class Release(NamedTuple):
    version: str    # "9.4"
    page: str       # Seite des Releases im Browser
    zip_url: str    # "" = keine (erlaubte) zip am Release → kein Ein-Klick-Update
    zip_size: int


def parse_version(text: str) -> Optional[Tuple[int, ...]]:
    """'9.10' → (9, 10); None, wenn es keine Versionsnummer aus Zahlen ist."""
    if not isinstance(text, str) or not re.fullmatch(r"[0-9]+(\.[0-9]+)*", text.strip()):
        return None
    parts = [int(p) for p in text.strip().split(".")]
    while len(parts) > 1 and parts[-1] == 0:  # "9.2.0" ist dieselbe Version wie "9.2"
        parts.pop()
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    """Ist `latest` eine höhere Version als `current`? (unlesbare Versionen: nein)"""
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def parse_release(data: dict) -> Optional[Release]:
    """Release aus der GitHub-Antwort; None, wenn der Tag keine Version ist. Fehlende Felder machen nichts kaputt."""
    if not isinstance(data, dict) or not isinstance(data.get("tag_name"), str):
        return None
    version = data["tag_name"].strip()
    if version[:1] in ("v", "V"):
        version = version[1:]
    if parse_version(version) is None:
        return None
    page = data.get("html_url")
    zip_url, zip_size = "", 0
    for asset in data.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        name, url, size = asset.get("name"), asset.get("browser_download_url"), asset.get("size")
        if (isinstance(name, str) and name.lower().endswith(".zip") and isinstance(url, str)
                and url.startswith(DOWNLOAD_PREFIXES) and isinstance(size, int) and size > 0):
            zip_url, zip_size = url, size
            break
    return Release(version, page if isinstance(page, str) and page.startswith("https://") else RELEASES_URL,
                   zip_url, zip_size)


def fetch_latest(timeout: float = TIMEOUT, opener: Callable = urllib.request.urlopen) -> Optional[Release]:
    """Neuester Release. Wirft bei Netzfehlern; None, wenn die Antwort keine Version enthält."""
    request = urllib.request.Request(RELEASE_API, headers={
        "User-Agent": "MD-Importer-Update", "Accept": "application/vnd.github+json"})
    with opener(request, timeout=timeout) as response:
        raw = response.read(MAX_BYTES)
    try:
        return parse_release(json.loads(raw.decode("utf-8", errors="replace")))
    except ValueError:
        return None


def find_update(current: str, log: Callable[[str], None] = lambda message: None,
                opener: Callable = urllib.request.urlopen) -> Optional[Release]:
    """Der neuere Release, falls es einen gibt – sonst None. Wirft nie (Netz weg, GitHub down …): nur Log."""
    try:
        latest = fetch_latest(opener=opener)
    except Exception as e:
        log(f"[UPDATE] Prüfung nicht möglich ({type(e).__name__}: {e}).")
        return None
    if latest is None:
        log("[UPDATE] Keine Version in der Antwort gefunden.")
        return None
    if is_newer(latest.version, current):
        log(f"[UPDATE] Neue Version {latest.version} verfügbar (installiert: {current}, zip: {bool(latest.zip_url)}).")
        return latest
    log(f"[UPDATE] Aktuell (installiert: {current}, Release: {latest.version}).")
    return None
