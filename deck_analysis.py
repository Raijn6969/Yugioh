"""
Deck-Analyse für das Extras-Menü: Welche Karte liegt unter der Maus, wie oft ist sie im Deck,
wie wahrscheinlich ist sie auf der Starthand, ist sie ein Starter? Und: Hat sich das Deck seit
dem Einlesen verändert?
"""

import threading
from collections import OrderedDict
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

import mss
from PIL import Image

import md_layout
import md_memory
import win_api
from auto_calibration import count_region, read_deck_count
from app_paths import LOG_FILE
from debug_log import append
from card_stats import CardInfo, CardStatsDB, StarterInfo
from deck_export import (DeckScan, background_mask, card_center_box, check_layout, signature,
                         signature_distance)
from draw_odds import HAND_FIRST, HAND_SECOND, p_at_least

CHANGED_DISTANCE = 12.0  # Ab dieser Abweichung liegt an einem Platz eine andere Karte (Kopien: < 3)


class DeckEntry(NamedTuple):
    key: str            # Karten-ID, bei nicht erkannten Karten "?Zone#Platz"
    name: str
    zone: str           # "Main" oder "Extra"
    copies: int
    slots: Tuple[int, ...]


class CardStats(NamedTuple):
    entry: DeckEntry
    deck_size: int
    first: float        # mind. 1 auf der Starthand (5 Karten)
    second: float       # mind. 1 bei 6 Karten (als Zweiter)
    two_first: float    # mind. 2 Kopien bei 5 Karten
    starter: StarterInfo
    info: Optional[CardInfo]


class DeckAnalysis:
    def __init__(self, scan: DeckScan, stats_db: Optional[CardStatsDB]):
        self.scan = scan
        self.stats_db = stats_db
        self.sizes = {name: len(positions) for name, positions, _ in scan.zones}
        self._entries: Dict[str, "OrderedDict[str, DeckEntry]"] = {}
        self._slot_keys: Dict[Tuple[str, int], str] = {}
        grouped: Dict[str, "OrderedDict[str, list]"] = {}
        for card in scan.cards:
            key = card.match.cid or f"?{card.zone}#{card.slot}"
            name = card.match.name or "Nicht erkannt"
            zone = grouped.setdefault(card.zone, OrderedDict())
            zone.setdefault(key, [name, []])[1].append(card.slot - 1)
            self._slot_keys[(card.zone, card.slot - 1)] = key
        for zone, cards in grouped.items():
            self._entries[zone] = OrderedDict(
                (key, DeckEntry(key, name, zone, len(slots), tuple(slots))) for key, (name, slots) in cards.items())
        self._starter_cache: Dict[str, StarterInfo] = {}

    # ── Karten ──
    @property
    def main_size(self) -> int:
        return self.sizes.get("Main", 0)

    def entries(self, zone: str = "Main") -> List[DeckEntry]:
        return list(self._entries.get(zone, {}).values())

    def entry_at(self, zone: str, index: int) -> Optional[DeckEntry]:
        key = self._slot_keys.get((zone, index))
        return self._entries[zone][key] if key else None

    def info(self, entry: DeckEntry) -> Optional[CardInfo]:
        if self.stats_db is None or entry.key.startswith("?"):
            return None
        return self.stats_db.info(entry.key)

    def starter(self, entry: DeckEntry) -> StarterInfo:
        if entry.zone != "Main":
            return StarterInfo(None, "Extra Deck – wird nicht gezogen", False)
        if self.stats_db is None or entry.key.startswith("?"):
            return StarterInfo(None, "Karte nicht erkannt", False)
        if entry.key not in self._starter_cache:
            self._starter_cache[entry.key] = self.stats_db.starter(entry.key)
        return self._starter_cache[entry.key]

    def reload_starters(self) -> None:
        """Nach neuen Einstufungen (z.B. aus Guides) alles neu nachschlagen."""
        self._starter_cache.clear()

    def set_starter(self, entry: DeckEntry, value: Optional[bool]) -> None:
        if self.stats_db is not None and not entry.key.startswith("?"):
            self.stats_db.set_starter(entry.key, value)
            self._starter_cache.pop(entry.key, None)

    # ── Wahrscheinlichkeiten ──
    def stats(self, entry: DeckEntry) -> CardStats:
        deck = self.main_size
        copies = entry.copies if entry.zone == "Main" else 0
        return CardStats(entry, deck, p_at_least(deck, copies, HAND_FIRST), p_at_least(deck, copies, HAND_SECOND),
                         p_at_least(deck, copies, HAND_FIRST, 2), self.starter(entry), self.info(entry))

    def starter_copies(self) -> Tuple[int, int]:
        """(Starter im Main Deck, davon unbekannt)"""
        starters = unknown = 0
        for entry in self.entries("Main"):
            value = self.starter(entry).starter
            if value:
                starters += entry.copies
            elif value is None:
                unknown += entry.copies
        return starters, unknown

    def starter_odds(self) -> Tuple[float, float]:
        starters, _ = self.starter_copies()
        return (p_at_least(self.main_size, starters, HAND_FIRST),
                p_at_least(self.main_size, starters, HAND_SECOND))


# ── Raster: Bildschirm ↔ Kartenplatz ──
def _step(positions: list) -> float:
    return positions[1][0] - positions[0][0] if len(positions) > 1 else md_layout.COLUMN_PITCH


def slot_at(frame: md_layout.Frame, zones, x: int, y: int) -> Optional[Tuple[str, int]]:
    """Welcher Kartenplatz liegt unter dem Bildschirmpunkt (x, y)? (Zone, Index) oder None."""
    rx = (x - frame.left) / frame.scale_x
    ry = (y - frame.top) / frame.scale_y
    w, h = md_layout.CARD_SIZE
    for name, positions, columns in zones:
        if not positions:
            continue
        step = _step(positions)
        first_x, first_y = positions[0]
        col = round((rx - first_x) / step)
        row = round((ry - first_y) / md_layout.ROW_PITCH)
        if not (0 <= col < columns and row >= 0):
            continue
        if abs(rx - (first_x + col * step)) > min(w, step) / 2 or abs(ry - (first_y + row * md_layout.ROW_PITCH)) > h / 2:
            continue
        index = row * columns + col
        if index < len(positions):
            return name, index
    return None


def slot_rect(frame: md_layout.Frame, zones, zone: str, index: int) -> Tuple[int, int, int, int]:
    """Bildschirm-Rechteck (links, oben, rechts, unten) eines Kartenplatzes."""
    positions = next(p for name, p, _ in zones if name == zone)
    x, y = positions[index]
    w, h = md_layout.CARD_SIZE
    left, top = frame.point(x - w / 2, y - h / 2)
    right, bottom = frame.point(x + w / 2, y + h / 2)
    return left, top, right, bottom


def _neighbours(zones, slot: Optional[Tuple[str, int]]) -> set:
    """Platz unter der Maus und die direkt angrenzenden (Hover-Effekte von Master Duel)."""
    if slot is None:
        return set()
    zone, index = slot
    columns = next(c for name, _, c in zones if name == zone)
    row, col = divmod(index, columns)
    return {(zone, r * columns + c) for r in (row - 1, row, row + 1) for c in (col - 1, col, col + 1)
            if r >= 0 and 0 <= c < columns}


def grab_deck(sct, frame: md_layout.Frame) -> Tuple[Image.Image, Tuple[int, int]]:
    region = frame.region(*md_layout.DECK_PANEL)
    shot = sct.grab(region)
    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX"), (region["left"], region["top"])


def deck_changes(scan: DeckScan, frame: md_layout.Frame, image: Image.Image, origin: Tuple[int, int],
                 cursor_slot: Optional[Tuple[str, int]] = None) -> Optional[str]:
    """
    Liegt noch dasselbe Deck da wie beim Einlesen? Returns: Grund, falls nicht, sonst None.
    Der Platz unter der Maus (und seine Nachbarn) wird nicht verglichen, weil Master Duel ihn hervorhebt.
    """
    if (frame.width, frame.height) != (scan.frame.width, scan.frame.height):
        return "Fenstergröße von Master Duel hat sich geändert"
    scan_frame = scan.frame._replace(left=frame.left, top=frame.top)  # Fenster darf verschoben sein
    if scan.signatures:
        # Liegt hinter der letzten Karte eine weitere (Karte dazu) oder fehlen Karten?
        panel = scan_frame.region(*md_layout.DECK_PANEL)
        ox, oy = origin
        deck_area = image.crop((panel["left"] - ox, panel["top"] - oy,
                                panel["left"] - ox + panel["width"], panel["top"] - oy + panel["height"]))
        if check_layout(background_mask(deck_area), scan_frame, scan.zones):
            return "Kartenzahl hat sich geändert"
    skip = _neighbours(scan.zones, cursor_slot)
    changed = []
    for key, old in scan.signatures.items():
        if key in skip:
            continue
        box = card_center_box(scan_frame, *_position(scan.zones, *key))
        distance = signature_distance(old, signature(image, origin, box))
        if distance > CHANGED_DISTANCE:
            changed.append((distance, key))
    if changed:
        distance, (zone, index) = max(changed)
        return f"{len(changed)} Karte(n) anders, z.B. {zone} #{index + 1} (Abweichung {distance:.0f})"
    return None


def memory_changes(scan: DeckScan) -> Tuple[bool, Optional[str]]:
    """
    Lesemethode "Speicher": das Deck im Speicher mit dem gescannten vergleichen – exakt, ohne Bildvergleich (der
    z.B. den Auswahlrahmen der zuletzt angeklickten Karte für eine Änderung halten kann).
    Returns: (geprüft?, Grund falls geändert). Nicht geprüft: Scan nicht aus dem Speicher oder Speicher nicht lesbar.
    """
    if scan.kids is None or not md_memory.is_ready():
        return False, None
    try:
        deck = md_memory.shared().deck()
    except Exception:  # z.B. Deck-Editor geschlossen → wie bisher über den Bildschirm
        return False, None
    zones = list(zip(("Main", "Extra"), scan.kids, deck))
    for zone, old, new in zones:
        if len(old) != len(new):
            return True, f"{zone} Deck hat jetzt {len(new)} statt {len(old)} Karten"
    for zone, old, new in zones:
        if old != new:
            if sorted(old) == sorted(new):
                return True, f"Reihenfolge im {zone} Deck geändert"
            index = next(i for i, (a, b) in enumerate(zip(old, new)) if a != b)
            return True, f"{zone} #{index + 1} ist eine andere Karte"
    return True, None


def current_changes(scan: DeckScan) -> Optional[str]:
    """Jetzt nachsehen, ob noch dasselbe Deck daliegt. Returns: Grund, falls nicht (oder nicht prüfbar), sonst None."""
    checked, reason = memory_changes(scan)
    if checked:
        return reason
    frame = md_layout.md_frame()
    if frame is None:
        return "Master Duel nicht gefunden"
    try:
        with mss.MSS() as sct:
            image, origin = grab_deck(sct, frame)
    except Exception as e:
        return f"Bildschirmfoto fehlgeschlagen ({e})"
    return deck_changes(scan, frame, image, origin, slot_at(frame, scan.zones, *win_api.get_cursor_pos()))


def _position(zones, zone: str, index: int) -> Tuple[float, float]:
    return next(p for name, p, _ in zones if name == zone)[index]


class DeckWatcher:
    """
    Prüft im Hintergrund, ob das Deck noch dem eingelesenen entspricht.
    - Aus dem Speicher gelesen (Lesemethode "Speicher"): exakter Vergleich der Karten-IDs, sonst über den Bildschirm:
    - Kartenzahlen neben "Main Deck"/"Extra Deck": Texterkennung nur, wenn sich die Pixel der Zahl
      ändern (spart Rechenzeit auf schwachen PCs). Eine andere Zahl heißt sicher: Deck bearbeitet.
    - Kartenbilder: erkennen auch einen Tausch bei gleicher Kartenzahl. Muss zweimal hintereinander
      auffallen und verschwindet wieder, wenn das Deck wieder gleich aussieht.
    """
    INTERVAL = 2.0      # Sekunden zwischen zwei Prüfungen der Kartenzahlen
    IMAGE_EVERY = 2     # Kartenbilder nur jede 2. Runde (Aufnahme des Deck-Bereichs kostet FPS)

    def __init__(self, scan: DeckScan, tesseract_cmd: str, cursor_slot: Callable[[], Optional[Tuple[str, int]]],
                 active: Callable[[], bool] = lambda: True, frame_source=md_layout.md_frame, read_count=None):
        self.scan = scan
        self.tesseract_cmd = tesseract_cmd
        self.cursor_slot = cursor_slot
        self.active = active
        self.frame_source = frame_source
        self.read_count = read_count or read_deck_count
        self.expected = {name: len(positions) for name, positions, _ in scan.zones}
        self.memory_reason: Optional[str] = None  # exakt aus dem Speicher (verschwindet, wenn das Deck wieder stimmt)
        self.count_reason: Optional[str] = None   # bleibt bis zum neuen Einlesen
        self.image_reason: Optional[str] = None
        self._image_hits = self._image_clean = 0
        self._count_pixels: Dict[str, bytes] = {}
        self._pending_count: Dict[str, int] = {}  # neue Zahl, die noch ein zweites Mal gelesen werden muss
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def reason(self) -> Optional[str]:
        return self.memory_reason or self.count_reason or self.image_reason

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        try:
            with mss.MSS() as sct:
                rounds = 0
                while not self._stop.wait(self.INTERVAL):
                    if self.active():
                        rounds += 1
                        try:
                            self.check(sct, images=rounds % self.IMAGE_EVERY == 0)
                        except Exception as e:  # z.B. Spiel gerade geschlossen – nächste Runde neu versuchen
                            append(LOG_FILE, f"[DECK-FENSTER] Deck-Prüfung fehlgeschlagen: {e}")
        except Exception as e:
            append(LOG_FILE, f"[DECK-FENSTER] Deck-Überwachung beendet: {e}")

    def check(self, sct, images: bool = True) -> Optional[str]:
        checked, reason = memory_changes(self.scan)
        if checked:
            if reason and reason != self.memory_reason:
                append(LOG_FILE, f"[DECK-FENSTER] {reason} (Speicher) → Deck geändert.")
            self.memory_reason = reason
            return self.reason
        frame = self.frame_source()
        if frame is None:
            return self.reason
        if self.count_reason is None:
            self.count_reason = self._check_counts(sct, frame)
            if self.count_reason:
                append(LOG_FILE, f"[DECK-FENSTER] {self.count_reason} → Deck geändert.")
        if self.count_reason is None and images:
            image, origin = grab_deck(sct, frame)
            found = deck_changes(self.scan, frame, image, origin, self.cursor_slot())
            if found:
                self._image_hits, self._image_clean = self._image_hits + 1, 0
                if self._image_hits >= 2:
                    if self.image_reason is None:
                        append(LOG_FILE, f"[DECK-FENSTER] {found} → Deck geändert.")
                    self.image_reason = found
            else:
                self._image_hits, self._image_clean = 0, self._image_clean + 1
                if self._image_clean >= 2:
                    self.image_reason = None
        return self.reason

    def _check_counts(self, sct, frame: md_layout.Frame) -> Optional[str]:
        for zone, point in (("Main", md_layout.REFERENCE_POINTS["DECK_COUNT"]), ("Extra", md_layout.EXTRA_COUNT_POINT)):
            pixels = sct.grab(count_region(frame, point)).rgb
            if pixels == self._count_pixels.get(zone) and zone not in self._pending_count:
                continue  # Zahl sieht aus wie vorher → nicht neu lesen
            self._count_pixels[zone] = pixels
            count = self.read_count(sct, frame, point, self.tesseract_cmd)
            if count is None or count == self.expected.get(zone):
                self._pending_count.pop(zone, None)
                continue
            # Eine andere Zahl erst glauben, wenn sie zweimal hintereinander gelesen wird (Lesefehler)
            if self._pending_count.get(zone) == count:
                return f"{zone} Deck hat jetzt {count} statt {self.expected.get(zone)} Karten"
            self._pending_count[zone] = count
        return None
