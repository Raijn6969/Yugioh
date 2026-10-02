"""
Deck-Prüfung: liest die Kartenzahl des Main Decks vom Bildschirm.

Damit lässt sich nach jedem Einfügen kontrollieren, ob die Karte wirklich im Deck gelandet ist
(verlorene Klicks bei Rucklern oder während die Suche neu lädt). Kalibrierpunkt: DECK_COUNT
(Mitte der Zahl beim Main Deck). Ist er nicht kalibriert oder die Zahl nicht lesbar, bleibt die
Prüfung aus – der Import läuft dann wie ohne sie.
"""

import hashlib
import re
import time
from typing import Optional

from PIL import Image, ImageOps

import vision_engine
from utils import get_cached_ocr

MAIN_DECK_MAX = 60


class DeckCounter:
    def __init__(self, sct, config: dict, tesseract_cmd: str, scale_x: float = 1.0, scale_y: float = 1.0,
                 point=None):
        self.sct = sct
        self.tesseract_cmd = tesseract_cmd
        self.value: Optional[int] = None  # zuletzt bestätigter Stand
        # Standard: Main Deck (Kalibrierpunkt). Für das Extra Deck wird der Punkt übergeben.
        point = point or config.get("DECK_COUNT")
        self.enabled = bool(point)
        self.region = None
        if point:
            width, height = int(70 * scale_x), int(34 * scale_y)
            self.region = {"left": int(point[0] - width / 2), "top": int(point[1] - height / 2),
                           "width": width, "height": height}

    def _ocr_digits(self, image: Image.Image, variant: str) -> str:
        key = hashlib.md5(image.tobytes()).hexdigest() + "_digits_" + variant
        return get_cached_ocr(
            key, lambda im: vision_engine.do_ocr(im, self.tesseract_cmd, "eng", vision_engine.DIGITS), image)

    def read(self) -> Optional[int]:
        """Aktuelle Kartenzahl, None falls nicht (plausibel) lesbar."""
        if not self.region:
            return None
        shot = self.sct.grab(self.region)
        gray = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX").convert("L")
        inverted = ImageOps.invert(gray)
        # Wie bei den Kartennamen: helle Schrift auf dunklem Grund zuerst, dann Alternativen
        variants = (("thr", inverted.point(lambda p: 0 if p < 150 else 255)), ("inv", inverted), ("raw", gray))
        for name, image in variants:
            match = re.search(r"\d+", self._ocr_digits(image, name) or "")
            if match and 0 <= int(match.group()) <= MAIN_DECK_MAX:
                return int(match.group())
        return None

    def wait_for(self, expected: int, timeout: float) -> Optional[int]:
        """Liest wiederholt, bis `expected` erscheint oder die Zeit abläuft. Returns: letzter Wert."""
        deadline = time.perf_counter() + timeout
        last = None
        while True:
            value = self.read()
            if value == expected:
                return value
            if value is not None:
                last = value
            if time.perf_counter() >= deadline:
                return last
            time.sleep(0.08)
