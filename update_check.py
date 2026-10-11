"""
Update-Hinweis: Beim Start schaut der Importer einmal im Hintergrund nach, ob es eine neuere Version gibt.

Quelle ist app_paths.py im öffentlichen Repo (die GitHub-Releases sind veraltet): daraus wird APP_VERSION gelesen und
numerisch mit der eigenen Version verglichen ("9.10" ist neuer als "9.2"). Es wird nichts heruntergeladen oder
installiert – der Hinweis führt nur auf die Seite des Projekts. Ohne Netz oder bei jedem Fehler passiert nichts
außer einem Log-Eintrag.
"""

import re
import urllib.request
from typing import Callable, Optional, Tuple

RAW_URL = "https://raw.githubusercontent.com/Raijn6969/Yugioh/main/app_paths.py"
REPO_URL = "https://github.com/Raijn6969/Yugioh"
TIMEOUT = 5.0          # Sekunden
MAX_BYTES = 64 * 1024  # app_paths.py ist winzig – mehr wird nicht gelesen
VERSION_LINE = re.compile(r"^APP_VERSION\s*=\s*[\"']([0-9]+(?:\.[0-9]+)*)[\"']", re.MULTILINE)


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


def version_from_source(source: str) -> Optional[str]:
    """APP_VERSION aus dem Text von app_paths.py."""
    found = VERSION_LINE.search(source)
    return found.group(1) if found else None


def fetch_latest(timeout: float = TIMEOUT, opener: Callable = urllib.request.urlopen) -> Optional[str]:
    """Neueste Version laut Repo (main). Wirft bei Netzfehlern; None, wenn die Datei keine Version enthält."""
    request = urllib.request.Request(RAW_URL, headers={"User-Agent": "MD-Importer-Update-Check"})
    with opener(request, timeout=timeout) as response:
        source = response.read(MAX_BYTES).decode("utf-8", errors="replace")
    return version_from_source(source)


def find_update(current: str, log: Callable[[str], None] = lambda message: None,
                opener: Callable = urllib.request.urlopen) -> Optional[str]:
    """Die neuere Version, falls es eine gibt – sonst None. Wirft nie (Netz weg, GitHub down …): nur ein Log-Eintrag."""
    try:
        latest = fetch_latest(opener=opener)
    except Exception as e:
        log(f"[UPDATE] Prüfung nicht möglich ({type(e).__name__}: {e}).")
        return None
    if latest is None:
        log("[UPDATE] Keine Version in der Antwort gefunden.")
        return None
    if is_newer(latest, current):
        log(f"[UPDATE] Neue Version {latest} verfügbar (installiert: {current}).")
        return latest
    log(f"[UPDATE] Aktuell (installiert: {current}, Repo: {latest}).")
    return None
