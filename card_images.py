"""
Kartenbilder (klein) für die Anzeige, z.B. im Staples-Fenster.

Einmal von YGOPRODeck geladen und neben dem Programm gespeichert (card_images/<Passcode>.jpg) – YGOPRODeck bittet
darum, Bilder lokal zu speichern statt sie immer wieder abzurufen. Danach geht alles offline.
"""

import os
import time
from typing import Dict, Iterable, Optional, Tuple

import requests

from app_paths import APP_DIR

FOLDER = os.path.join(APP_DIR, "card_images")
IMAGE_URL = "https://images.ygoprodeck.com/images/cards_small/{id}.jpg"
TIMEOUT = 20
PAUSE = 0.05  # zwischen zwei Downloads (die Seite freundlich behandeln)


def image_path(passcode: str, folder: str = FOLDER) -> str:
    return os.path.join(folder, f"{passcode}.jpg")


def download(passcodes: Iterable[str], session=requests, folder: str = FOLDER) -> int:
    """Fehlende Bilder laden (unbekannte/offline werden übersprungen). Returns: Anzahl neu geladener Bilder."""
    todo = [p for p in dict.fromkeys(str(p) for p in passcodes if p)
            if p.isdigit() and not os.path.exists(image_path(p, folder))]
    if not todo:
        return 0
    os.makedirs(folder, exist_ok=True)
    loaded = 0
    for passcode in todo:
        try:
            resp = session.get(IMAGE_URL.format(id=passcode), timeout=TIMEOUT)
        except Exception:
            continue
        if resp.status_code == 200 and resp.content[:2] == b"\xff\xd8":  # wirklich ein JPEG
            tmp = image_path(passcode, folder) + ".part"
            with open(tmp, "wb") as f:
                f.write(resp.content)
            os.replace(tmp, image_path(passcode, folder))
            loaded += 1
        time.sleep(PAUSE)
    return loaded


class PhotoCache:
    """Geladene Bilder für Tk (in einer Größe); hält die Referenzen, sonst räumt Python sie weg."""

    def __init__(self, master, size: Tuple[int, int], folder: str = FOLDER):
        self.master, self.size, self.folder = master, size, folder
        self._photos: Dict[str, object] = {}

    def get(self, passcode: Optional[str]):
        """PhotoImage oder None (kein Bild vorhanden)."""
        if not passcode:
            return None
        if passcode not in self._photos:
            from PIL import Image, ImageTk
            try:
                with Image.open(image_path(passcode, self.folder)) as image:
                    resized = image.convert("RGB").resize(self.size, Image.LANCZOS)
                self._photos[passcode] = ImageTk.PhotoImage(resized, master=self.master)
            except (OSError, ValueError):
                self._photos[passcode] = None
        return self._photos[passcode]
