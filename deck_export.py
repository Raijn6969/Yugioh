"""
DECK-EXPORT
Liest das Deck, das gerade im Deck-Editor von Master Duel offen ist, und macht daraus eine .ydk.
Jede Karte im Main und Extra Deck wird mit links angeklickt (zeigt nur die Details, verändert
das Deck nicht), der Name im Detail-Panel per Texterkennung gelesen und über die Kartenliste
von YGOPRODeck einer Karten-ID zugeordnet.
"""

import os
import time
import traceback
from collections import Counter
from typing import Callable, Dict, List, NamedTuple, Optional, Tuple

import mss
import numpy as np
import pyperclip
from PIL import Image

import md_layout
import vision_engine
import win_api
from app_paths import APP_VERSION, LOG_FILE
from auto_calibration import deck_editor_visible, read_deck_count
from card_db import CardMatch, load_card_db
from debug_log import dlog, end_run, start_run
from import_engine import DeckImporterCore
from input_utils import focus_master_duel
from utils import clean_text
from window_automation import WindowAutomator

LAYOUT_MISS_SHARE = 0.10  # Liegt an mehr Kartenplätzen keine Karte, stimmt das Raster nicht
MAX_COPIES = 3
SIGNATURE_SIZE = (8, 12)  # Mini-Abbild der Kartenmitte (Breite, Höhe) zum Wiedererkennen


class ExportedCard(NamedTuple):
    zone: str          # "Main" oder "Extra"
    slot: int          # 1-basiert
    raw_ocr: str
    match: CardMatch


class DeckScan(NamedTuple):
    """Gelesenes Deck mit Raster (für das Extras-Menü: welche Karte liegt wo)."""
    frame: md_layout.Frame
    zones: list                 # [(Name, Referenz-Positionen, Spalten)]
    cards: List[ExportedCard]
    signatures: dict            # {(Zone, Index): Mini-Abbild der Kartenmitte}


class ExportResult(NamedTuple):
    ydk: str
    main_count: int
    extra_count: int
    cards: List[ExportedCard]
    scan: Optional[DeckScan] = None   # Für das Deck-Fenster (kein neuer Scan nötig)
    reused: bool = False              # Letzter Scan übernommen statt neu gelesen

    @property
    def problems(self) -> List[str]:
        """Karten, die fehlen oder unsicher erkannt wurden (für die Anzeige)."""
        lines = []
        for card in self.cards:
            if card.match.cid and card.match.sure:
                continue
            read = card.raw_ocr.strip() or "(nichts)"
            target = card.match.name or "nicht erkannt"
            lines.append(f"{card.zone} #{card.slot}: '{read}' → {target} – {card.match.note}")
        # Mehr als 3 Kopien gibt es nicht: Dann hat das Panel beim Lesen gehangen
        counts = Counter(card.match.cid for card in self.cards if card.match.cid)
        for cid, amount in counts.items():
            if amount > MAX_COPIES:
                name = next(card.match.name for card in self.cards if card.match.cid == cid)
                lines.append(f"{name}: {amount}x gelesen (max. {MAX_COPIES}) – bitte prüfen")
        return lines

    @property
    def missing(self) -> int:
        return sum(1 for card in self.cards if not card.match.cid)


def compare_deck(expected: Dict[str, int], cards: List[ExportedCard]) -> Tuple[Dict[str, int], Dict[str, int]]:
    """
    Vergleicht das gelesene Deck mit dem Soll (clean_text-Name → Anzahl).
    Zählt nur sicher erkannte Karten. Returns: (zu viel {Name: Anzahl}, zu wenig {Name: Anzahl}).
    """
    counts = Counter(clean_text(c.match.name) for c in cards if c.match.cid and c.match.sure)
    too_many = {k: n - expected.get(k, 0) for k, n in counts.items() if n > expected.get(k, 0)}
    too_few = {k: n - counts.get(k, 0) for k, n in expected.items() if counts.get(k, 0) < n}
    return too_many, too_few


def build_ydk(main_ids: List[str], extra_ids: List[str]) -> str:
    lines = [f"#created by MD Importer {APP_VERSION}", "#main", *main_ids, "#extra", *extra_ids, "!side"]
    return "\n".join(lines) + "\n"


def downloads_dir() -> str:
    return win_api.known_folder_path(win_api.FOLDERID_DOWNLOADS) or os.path.join(os.path.expanduser("~"), "Downloads")


def save_ydk(text: str, folder: Optional[str] = None) -> str:
    """Speichert die .ydk im Downloads-Ordner (ohne etwas zu überschreiben). Returns: Pfad."""
    folder = folder or downloads_dir()
    os.makedirs(folder, exist_ok=True)
    base = f"MD_Deck_{time.strftime('%Y-%m-%d_%H%M')}"
    path, n = os.path.join(folder, base + ".ydk"), 2
    while os.path.exists(path):
        path, n = os.path.join(folder, f"{base}_{n}.ydk"), n + 1
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def card_coverage(mask: np.ndarray, frame: md_layout.Frame, ref_x: float, ref_y: float) -> float:
    """Anteil 'Nicht-Hintergrund' in der Mitte eines Kartenplatzes (mask: Hintergrund des Deck-Bereichs)."""
    px, py = md_layout.DECK_PANEL[:2]
    w, h = md_layout.CARD_SIZE
    x1 = int((ref_x - w * 0.3 - px) * frame.scale_x)
    x2 = int((ref_x + w * 0.3 - px) * frame.scale_x)
    y1 = int((ref_y - h * 0.35 - py) * frame.scale_y)
    y2 = int((ref_y + h * 0.35 - py) * frame.scale_y)
    patch = mask[max(0, y1):max(0, y2), max(0, x1):max(0, x2)]
    return float(1.0 - patch.mean()) if patch.size else 0.0


def background_mask(image: Image.Image) -> np.ndarray:
    """True = dunkelblauer Hintergrund des Deck-Bereichs."""
    rgb = np.asarray(image.convert("RGB")).astype(int)
    return (rgb.max(axis=2) < 75) & (rgb[..., 2] >= rgb[..., 0])


def card_center_box(frame: md_layout.Frame, ref_x: float, ref_y: float) -> Tuple[int, int, int, int]:
    """
    Mitte einer Karte auf dem Bildschirm (links, oben, rechts, unten) – ohne Rand, Seltenheit (oben rechts)
    und Anzahl-Abzeichen (oben links), die Master Duel bei ausgewählten Karten einblendet.
    """
    w, h = md_layout.CARD_SIZE
    left, top = frame.point(ref_x - w * 0.28, ref_y - h * 0.2)
    right, bottom = frame.point(ref_x + w * 0.28, ref_y + h * 0.3)
    return left, top, max(left + 1, right), max(top + 1, bottom)


def signature(image: Image.Image, origin: Tuple[int, int], box: Tuple[int, int, int, int]) -> np.ndarray:
    """Mini-Abbild (Graustufen) des Ausschnitts `box` (Bildschirm) aus `image`, das bei `origin` beginnt."""
    ox, oy = origin
    crop = image.crop((box[0] - ox, box[1] - oy, box[2] - ox, box[3] - oy)).convert("L")
    return np.asarray(crop.resize(SIGNATURE_SIZE, Image.BILINEAR), dtype=np.float32)


def signature_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Mittlere Helligkeitsabweichung 0–255: klein = dieselbe Karte."""
    return float(np.abs(a - b).mean())


def slot_signatures(image: Image.Image, origin: Tuple[int, int], frame: md_layout.Frame, zones) -> dict:
    return {(name, i): signature(image, origin, card_center_box(frame, x, y))
            for name, positions, _ in zones for i, (x, y) in enumerate(positions)}


def check_layout(mask: np.ndarray, frame: md_layout.Frame, zones) -> Optional[str]:
    """
    Liegen an den berechneten Plätzen Karten – und direkt dahinter keine? Sonst ordnet Master Duel
    das Raster anders an als erwartet, und der Export würde falsche Karten lesen.
    zones: [(Name, Positionen, Spalten)]. Returns: Fehlermeldung oder None.
    """
    for name, positions, columns in zones:
        if not positions:
            continue
        misses = [i for i, (x, y) in enumerate(positions) if card_coverage(mask, frame, x, y) < 0.25]
        if len(misses) > max(2, LAYOUT_MISS_SHARE * len(positions)):
            return f"{name}: an {len(misses)} von {len(positions)} Plätzen keine Karte erkannt"
        last = len(positions) - 1
        if (last + 1) % columns:  # letzte Reihe nicht voll → der Platz dahinter muss leer sein
            x, y = positions[last]
            step = (positions[1][0] - positions[0][0]) if len(positions) > 1 else md_layout.COLUMN_PITCH
            if card_coverage(mask, frame, x + step, y) > 0.5:
                return f"{name}: hinter der letzten Karte liegt noch eine Karte"
    return None


class DeckExporter:
    def __init__(self, config: dict, tesseract_cmd: str, status_callback: Callable,
                 finish_callback: Callable, frame: Optional[md_layout.Frame] = None, reader=None,
                 start_callback: Optional[Callable] = None, scan: Optional[DeckScan] = None):
        self.config = config
        self.tesseract_cmd = tesseract_cmd
        self.status_callback = status_callback
        self.finish_callback = finish_callback  # finish(result=ExportResult | None, error=str)
        self.start_callback = start_callback  # Wird nach dem Countdown aufgerufen (Start des Timers)
        self.frame = frame
        self.label = "Export"  # Statusanzeige ("Kontrolle" bei der Prüfung nach dem Import, "Analyse")
        self.signatures: dict = {}  # Mini-Abbilder der Karten beim Einlesen (siehe _plan)
        self.reuse = scan           # Deck seit diesem Scan unverändert → Export ohne neues Lesen
        # Texterkennung und Ruckler-Erkennung genau wie beim Import
        self.reader = reader or DeckImporterCore(config, tesseract_cmd, status_callback, lambda **kwargs: None)

    def execute(self) -> None:
        """Deck lesen und als .ydk in die Zwischenablage. finish(result=…, error=…)"""
        self._execute("Deck-Export", self._run, "result")

    def execute_scan(self) -> None:
        """Deck nur lesen (Extras-Menü). finish(scan=DeckScan | None, error=…)"""
        self._execute("Deck-Analyse", self.scan, "scan")

    def _execute(self, title: str, work: Callable, key: str) -> None:
        start_run(LOG_FILE)
        dlog(f"=== MD DECK IMPORTER {APP_VERSION} | {title} vom {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
        try:
            value = work()
        except Exception as e:
            dlog(f"\n[CRITICAL ERROR]: {e}\n{traceback.format_exc()}")
            self.status_callback(f"{self.label} abgebrochen", "red")
            self.finish_callback(**{key: None, "error": str(e)})
        else:
            self.finish_callback(**{key: value, "error": ""})
        finally:
            end_run()

    def scan(self) -> DeckScan:
        problem = vision_engine.check_tesseract(self.tesseract_cmd, "eng")
        if problem:
            raise RuntimeError(problem)
        frame = self.frame or md_layout.md_frame()
        if frame is None:
            raise RuntimeError("Master Duel wurde nicht gefunden. Bitte das Spiel starten und ein Deck "
                               "im Deck-Editor öffnen.")
        if not frame.is_16_9:
            raise RuntimeError(f"Das Deck-Lesen braucht Master Duel im 16:9-Format "
                               f"(gerade {frame.width}×{frame.height}).")

        self.status_callback("Kartenliste…", "cyan")
        db = load_card_db(self.config.get("LANGUAGE", "en"), lambda text: self.status_callback(text, "cyan"))

        for i in range(3, 0, -1):
            self.status_callback(f"Maus loslassen! ({i}s)", "yellow")
            time.sleep(1)
        if self.start_callback:
            self.start_callback()

        with mss.MSS() as sct:
            # Maus neben das Deck: Eine Karte unter der Maus hebt Master Duel hervor, das gehört nicht
            # in die Vergleichsbilder (sonst meldet das Deck-Fenster später "Deck geändert")
            win_api.set_cursor_pos(*frame.point(*md_layout.PARK_POINT))
            time.sleep(0.2)
            zones = self._plan(sct, frame)
            automator = WindowAutomator(self.config)
            automator.last_bot_pos = win_api.get_cursor_pos()
            focus_master_duel()
            time.sleep(0.15)
            cards = self._read_cards(sct, frame, automator, db, zones)
            # Danach wieder neben das Deck: Welche Karte analysiert wird, entscheidet man selbst
            win_api.set_cursor_pos(*frame.point(*md_layout.PARK_POINT))
        return DeckScan(frame, zones, cards, self.signatures)

    def _run(self) -> ExportResult:
        reused = self.reuse is not None
        if reused:
            dlog("\n[EXPORT] Deck unverändert seit dem letzten Scan – Scan übernommen")
        scan = self.reuse if reused else self.scan()
        zones, cards = scan.zones, scan.cards
        main_ids = [c.match.cid for c in cards if c.zone == "Main" and c.match.cid]
        extra_ids = [c.match.cid for c in cards if c.zone == "Extra" and c.match.cid]
        result = ExportResult(build_ydk(main_ids, extra_ids), len(zones[0][1]), len(zones[1][1]), cards,
                              scan, reused)
        pyperclip.copy(result.ydk)
        dlog(f"\n[EXPORT] {len(main_ids)}/{result.main_count} Main, {len(extra_ids)}/{result.extra_count} Extra "
             f"erkannt, {len(result.problems)} unsicher/fehlend. .ydk liegt in der Zwischenablage.")
        self.status_callback("Deck exportiert!", "#00ff00")
        return result

    def _plan(self, sct, frame: md_layout.Frame) -> List[Tuple[str, list, int]]:
        """Kartenzahlen lesen und die Kartenplätze berechnen (und prüfen)."""
        if not deck_editor_visible(sct, frame, self.tesseract_cmd):
            raise RuntimeError("Deck-Editor nicht erkannt. Bitte in Master Duel ein Deck zum Bearbeiten "
                               "öffnen und den Export erneut starten.")
        main_n = read_deck_count(sct, frame, md_layout.REFERENCE_POINTS["DECK_COUNT"], self.tesseract_cmd)
        extra_n = read_deck_count(sct, frame, md_layout.EXTRA_COUNT_POINT, self.tesseract_cmd)
        dlog(f"[EXPORT] Kartenzahl gelesen: Main {main_n}, Extra {extra_n}")

        panel = frame.region(*md_layout.DECK_PANEL)
        shot = sct.grab(panel)
        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        mask = background_mask(image)
        if extra_n is None:
            first_extra = (md_layout.MAIN_FIRST_CARD[0], md_layout.EXTRA_FIRST_Y)
            if card_coverage(mask, frame, *first_extra) < 0.25:
                extra_n = 0  # Extra Deck leer (dann steht evtl. keine Zahl da)
        if main_n is None or extra_n is None:
            raise RuntimeError("Die Kartenzahl neben 'Main Deck' / 'Extra Deck' war nicht lesbar.")

        zones = []
        for name, count, first_y, rows in (("Main", main_n, md_layout.MAIN_FIRST_CARD[1], md_layout.MAIN_ROWS),
                                           ("Extra", extra_n, md_layout.EXTRA_FIRST_Y, md_layout.EXTRA_ROWS)):
            zones.append((name, md_layout.deck_slot_positions(count, first_y, rows),
                          md_layout.deck_columns(count, rows)))
        problem = check_layout(mask, frame, zones)
        if problem:
            raise RuntimeError(f"Das Deck-Raster sieht anders aus als erwartet ({problem}). "
                               f"{self.label} abgebrochen, damit keine falschen Karten gelesen werden.")
        self.signatures = slot_signatures(image, (panel["left"], panel["top"]), frame, zones)
        return zones

    def _read_cards(self, sct, frame, automator, db, zones) -> List[ExportedCard]:
        reader = self.reader
        name_region = frame.region(*md_layout.NAME_REGION)
        reader._last_frame_hash = ""
        _, previous = reader._capture_and_ocr_slot(sct, name_region)
        cards = []
        for zone, positions, _ in zones:
            for i, (rx, ry) in enumerate(positions):
                self.status_callback(f"{self.label} {zone} {i + 1}/{len(positions)}", "cyan")
                x, y = frame.point(rx, ry)
                raw, s_c = reader._read_slot(sct, automator, name_region, x, y, previous)
                if not s_c:
                    # Panel noch nicht aufgebaut → etwas länger warten und erneut lesen
                    automator.iron_grip_click(x, y)
                    time.sleep(0.25 * reader.speed_mult)
                    reader._last_frame_hash = ""
                    raw, s_c = reader._capture_and_ocr_slot(sct, name_region)
                previous = s_c or previous
                match = db.match(s_c, extra=(zone == "Extra"))
                dlog(f"    [EXPORT] {zone} #{i + 1:02d} '{s_c}' → {match.name or '?'} ({match.cid or '-'})"
                     f"{'' if match.sure else ' UNSICHER: ' + match.note}")
                cards.append(ExportedCard(zone, i + 1, raw, match))
        return cards
