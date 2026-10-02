"""
Bildschirm-Layout von Master Duel.

Master Duel skaliert seine Oberfläche gleichmäßig mit der Fenstergröße. Alle Positionen sind
deshalb einmal für 1920×1080 (Referenz) hinterlegt und werden auf das echte Fenster umgerechnet
(andere 16:9-Auflösungen, Fenstermodus, zweiter Monitor).
"""

from typing import NamedTuple, Optional, Tuple

import win_api

REF_W, REF_H = 1920, 1080
ASPECT_TOLERANCE = 0.02  # Abweichung vom 16:9-Seitenverhältnis, die noch als 16:9 gilt

# Kalibrierpunkte im Deck-Editor (Referenz 1920×1080, aus einer funktionierenden Kalibrierung)
REFERENCE_POINTS = {
    "SEARCH_BAR": (1464, 206),
    "FIRST_CARD": (1370, 379),
    "TRASH_BTN": (1238, 144),
    "TRASH_CONFIRM": (1137, 662),
    "UNOWNED_BTN": (1764, 209),
    "DECK_COUNT": (741, 192),
}

# Detail-Panel links: Kartenname (x1, y1, x2, y2)
NAME_REGION = (15, 115, 380, 155)
# Überschrift "Main Deck" (zur Prüfung, ob der Deck-Editor offen ist)
MAIN_HEADER_REGION = (500, 168, 700, 218)

# Deck-Raster (vermessen an Screenshots mit 41 und 65 Karten im Main Deck)
EXTRA_COUNT_POINT = (742, 771)   # Zahl neben "Extra Deck"
MAIN_FIRST_CARD = (543, 267)     # Mitte der ersten Karte im Main Deck
EXTRA_FIRST_Y = 845              # Mitte der ersten Reihe im Extra Deck
COLUMN_PITCH = 76.75             # Abstand der Spalten bei 10 Karten pro Reihe
ROW_PITCH = 106.7
CARD_SIZE = (64, 94)
MIN_COLUMNS = 10                 # Ab 51 Karten rückt Master Duel die Spalten zusammen …
MAIN_ROWS = 5                    # … die Zahl der Reihen bleibt bei 5
EXTRA_ROWS = 2
# Deck-Bereich (für die Prüfung, ob an den berechneten Stellen wirklich Karten liegen)
DECK_PANEL = (495, 215, 1270, 1000)

# Freie Stelle zwischen Deck und Kartenliste: Hier parkt die Maus beim Scannen (keine Karte hervorgehoben)
PARK_POINT = (1292, 600)

# Bereiche, die Import und Export anklicken: Dort darf das Overlay nicht liegen.
# Unten etwas Luft, damit das Overlay am unteren Bildschirmrand (Standardplatz) nicht stört.
CLICK_AREAS = {
    "das Deck": (500, 220, 1265, 990),
    "die Kartenliste": (1320, 180, 1860, 990),
}


class Frame(NamedTuple):
    """Innenbereich des Master-Duel-Fensters auf dem Bildschirm."""
    left: int
    top: int
    width: int
    height: int

    @property
    def scale_x(self) -> float:
        return self.width / REF_W

    @property
    def scale_y(self) -> float:
        return self.height / REF_H

    @property
    def is_16_9(self) -> bool:
        return abs(self.width / self.height - REF_W / REF_H) <= ASPECT_TOLERANCE * REF_W / REF_H

    def point(self, ref_x: float, ref_y: float) -> Tuple[int, int]:
        """Referenzpunkt → Bildschirmpunkt."""
        return round(self.left + ref_x * self.scale_x), round(self.top + ref_y * self.scale_y)

    def region(self, x1: float, y1: float, x2: float, y2: float) -> dict:
        """Referenz-Rechteck → Bildschirmausschnitt für mss."""
        left, top = self.point(x1, y1)
        right, bottom = self.point(x2, y2)
        return {"left": left, "top": top, "width": max(1, right - left), "height": max(1, bottom - top)}


def md_frame() -> Optional[Frame]:
    """Innenbereich von Master Duel, None wenn das Spiel nicht läuft."""
    from window_automation import find_md_window  # window_automation konfiguriert beim Import pyautogui
    hwnd = find_md_window()
    rect = win_api.get_client_rect(hwnd) if hwnd else None
    if not rect or rect[2] <= 0 or rect[3] <= 0:
        return None
    return Frame(*rect)


def deck_columns(count: int, rows: int) -> int:
    """Karten pro Reihe: 10, bei mehr Karten als 10 × Reihen entsprechend mehr (zusammengerückt)."""
    return max(MIN_COLUMNS, -(-count // rows))


def deck_slot_positions(count: int, first_y: float, rows: int) -> list:
    """Referenz-Mittelpunkte der Karten im Deck-Raster, in Lesereihenfolge."""
    columns = deck_columns(count, rows)
    # Die erste und letzte Spalte bleiben an derselben Stelle, dazwischen wird verteilt
    step = COLUMN_PITCH * (MIN_COLUMNS - 1) / (columns - 1)
    return [(MAIN_FIRST_CARD[0] + (i % columns) * step, first_y + (i // columns) * ROW_PITCH)
            for i in range(count)]
