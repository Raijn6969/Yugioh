"""
Kartenbilder (klein) für die Anzeige, z.B. im Staples-Fenster.

Einmal von YGOPRODeck geladen und neben dem Programm gespeichert (card_images/<Passcode>.jpg) – YGOPRODeck bittet
darum, Bilder lokal zu speichern statt sie immer wieder abzurufen. Danach geht alles offline.
"""

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Iterable, Optional, Tuple

import requests

from app_paths import APP_DIR

FOLDER = os.path.join(APP_DIR, "card_images")
IMAGE_URL = "https://images.ygoprodeck.com/images/cards_small/{id}.jpg"
TIMEOUT = 20
PAUSE = 0.05   # nach jedem Download (die Seite freundlich behandeln)
PARALLEL = 4   # Bilder gleichzeitig
JPEG_START = bytes([0xFF, 0xD8])


def image_path(passcode: str, folder: str = FOLDER) -> str:
    return os.path.join(folder, f"{passcode}.jpg")


def download(passcodes: Iterable[str], session=None, folder: str = FOLDER) -> int:
    """Fehlende Bilder laden (unbekannte/offline werden übersprungen). Returns: Anzahl neu geladener Bilder."""
    todo = [p for p in dict.fromkeys(str(p) for p in passcodes if p)
            if p.isdigit() and not os.path.exists(image_path(p, folder))]
    if not todo:
        return 0
    os.makedirs(folder, exist_ok=True)
    own = session is None
    session = session or requests.Session()  # eine Verbindung für alle Bilder (statt je Bild neu aufbauen)

    def load(passcode: str) -> bool:
        try:
            resp = session.get(IMAGE_URL.format(id=passcode), timeout=TIMEOUT)
        except Exception:
            return False
        if resp.status_code != 200 or resp.content[:2] != JPEG_START:  # wirklich ein JPEG?
            return False
        tmp = image_path(passcode, folder) + f".{threading.get_ident()}.part"
        with open(tmp, "wb") as f:
            f.write(resp.content)
        os.replace(tmp, image_path(passcode, folder))
        time.sleep(PAUSE)
        return True

    try:
        with ThreadPoolExecutor(max_workers=min(PARALLEL, len(todo))) as pool:
            return sum(pool.map(load, todo))
    finally:
        if own:
            session.close()


class PhotoCache:
    """Geladene Bilder für Tk (in einer Größe); hält die Referenzen, sonst räumt Python sie weg."""

    def __init__(self, master, size: Tuple[int, int], folder: str = FOLDER):
        self.master, self.size, self.folder = master, size, folder
        self._photos: Dict[str, object] = {}
        self._prepared: Dict[str, object] = {}  # schon verkleinert (im Hintergrund), noch kein PhotoImage

    def get(self, passcode: Optional[str]):
        """PhotoImage oder None (kein Bild vorhanden)."""
        if not passcode:
            return None
        if passcode not in self._photos:
            from PIL import ImageTk
            resized = self._prepared.pop(passcode, None) or self._load(passcode)
            self._photos[passcode] = ImageTk.PhotoImage(resized, master=self.master) if resized else None
        return self._photos[passcode]

    def prepare(self, passcodes) -> None:
        """Bilder schon laden und verkleinern (darf im Hintergrund laufen) – get() ist danach schnell."""
        for passcode in passcodes:
            if passcode and passcode not in self._photos and passcode not in self._prepared:
                resized = self._load(passcode)
                if resized is not None:
                    self._prepared[passcode] = resized

    def _load(self, passcode: str):
        from PIL import Image
        try:
            with Image.open(image_path(passcode, self.folder)) as image:
                return image.convert("RGB").resize(self.size, Image.LANCZOS)
        except (OSError, ValueError):
            return None

    def forget_missing(self) -> None:
        """Bilder, die noch fehlten, beim nächsten get() erneut von der Platte lesen (inzwischen geladen)."""
        self._photos = {k: v for k, v in self._photos.items() if v is not None}

    def missing(self, passcodes) -> list:
        """Passcodes ohne gespeichertes Bild."""
        return [p for p in passcodes if p and not os.path.exists(image_path(p, self.folder))]
