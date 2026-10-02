"""
Automatische Kalibrierung: rechnet die Klickpunkte aus dem Referenz-Layout (1920×1080) auf das
Master-Duel-Fenster um und prüft per Texterkennung, ob dort wirklich der Deck-Editor zu sehen ist.
Klappt das nicht (Deck-Editor nicht offen, kein 16:9), bleibt der Kalibrierungs-Assistent.
"""

import re
from typing import Optional, Tuple

import mss

import md_layout
import vision_engine
from debug_log import dlog
from deck_counter import MAIN_DECK_MAX
from utils import clean_text

DIGITS_ONLY = vision_engine.DIGITS


def count_region(frame: md_layout.Frame, point: Tuple[float, float]) -> dict:
    """Bildschirmausschnitt der Kartenzahl neben "Main Deck"/"Extra Deck" (Referenzpunkt = Mitte der Zahl)."""
    x, y = point
    return frame.region(x - 35, y - 20, x + 35, y + 20)


def read_deck_count(sct, frame: md_layout.Frame, point: Tuple[float, float], tesseract_cmd: str) -> Optional[int]:
    """Kartenzahl neben "Main Deck"/"Extra Deck" (Referenzpunkt = Mitte der Zahl)."""
    text = vision_engine.read_screen_text(sct, count_region(frame, point), tesseract_cmd, "eng", DIGITS_ONLY)
    match = re.search(r"\d+", text)
    if match and 0 <= int(match.group()) <= MAIN_DECK_MAX + 5:
        return int(match.group())
    return None


def deck_editor_visible(sct, frame: md_layout.Frame, tesseract_cmd: str) -> bool:
    """Steht oben im Deck-Bereich "Main Deck" (bzw. "…deck" in anderen Sprachen)?"""
    text = vision_engine.read_screen_text(sct, frame.region(*md_layout.MAIN_HEADER_REGION), tesseract_cmd)
    return "deck" in clean_text(text)


def auto_calibrate(config: dict, tesseract_cmd: str, frame: Optional[md_layout.Frame] = None,
                   sct=None) -> Tuple[Optional[dict], str]:
    """
    Returns: (neue Config, Meldung). Neue Config ist None, wenn die automatische Kalibrierung nicht
    sicher ist – dann sagt die Meldung warum.
    """
    problem = vision_engine.check_tesseract(tesseract_cmd, "eng")
    if problem:
        return None, problem
    frame = frame or md_layout.md_frame()
    if frame is None:
        return None, "Master Duel wurde nicht gefunden."
    if not frame.is_16_9:
        return None, (f"Master Duel läuft nicht im 16:9-Format ({frame.width}×{frame.height}). "
                      f"Bitte von Hand kalibrieren.")

    own_sct = sct is None
    sct = sct or mss.MSS()
    try:
        if not deck_editor_visible(sct, frame, tesseract_cmd):
            return None, "Deck-Editor nicht erkannt. Bitte in Master Duel ein Deck zum Bearbeiten öffnen."
        count = read_deck_count(sct, frame, md_layout.REFERENCE_POINTS["DECK_COUNT"], tesseract_cmd)
    finally:
        if own_sct:
            sct.close()

    new_config = dict(config)
    for key, (x, y) in md_layout.REFERENCE_POINTS.items():
        new_config[key] = list(frame.point(x, y))
    if count is None:
        # Ohne lesbare Zahl keine Einfüge-Prüfung (wie "übersprungen" im Assistenten)
        new_config.pop("DECK_COUNT", None)
    new_config["IS_CALIBRATED"] = True
    new_config["AUTO_CALIBRATED"] = True
    dlog(f"[KALIBRIERUNG] Automatisch für {frame.width}×{frame.height} bei ({frame.left}, {frame.top}), "
         f"Deck-Zahl {'lesbar' if count is not None else 'nicht lesbar'}.")
    return new_config, f"Automatisch kalibriert ({frame.width}×{frame.height})"
