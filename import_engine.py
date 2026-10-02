"""
IMPORT ENGINE
Steuert den Deck-Import: Karten suchen, Suchergebnisse per Texterkennung (OCR) prüfen und
passende Karten ins Deck klicken. Jede Entscheidung landet im Diagnose-Log (md_debug.log),
damit sich Fehlklicks im Nachhinein nachvollziehen lassen.
"""

import os
import re
import time
import hashlib
import traceback
from collections import Counter
from typing import NamedTuple, Optional, Tuple, List, Dict, Callable
from difflib import SequenceMatcher
import mss
import pyperclip
from PIL import Image, ImageOps

from input_utils import type_card_name
from utils import (clean_text, sanitize_name, parse_clipboard, fetch_card_names, fetch_card_types,
                   get_cached_ocr, is_extra_deck_type)
from card_engine import CardMatcher
from window_automation import WindowAutomator, get_md_window_size
from deck_counter import DeckCounter
import resume_state
import vision_engine
import win_api
from match_validator import MatchValidator, knight_night_conflict
from debug_log import dlog, start_run, end_run
from app_paths import APP_VERSION, LOG_FILE

# ── Abgleich-Schwellen (SequenceMatcher-Ratio 0..1) ──
BATCH_MATCH_THRESHOLD = 0.85   # Archetyp-Suche: Karte der Gruppe
OPPO_MATCH_THRESHOLD = 0.92    # Archetyp-Suche: nebenbei sichtbare andere Deck-Karte (strenger)
AMBIGUITY_MARGIN = 0.05        # Liegen zwei Kandidaten näher beieinander, sind sie nicht unterscheidbar
GHOST_RATIO = 0.88             # Slot zeigt (fast) dieselbe Karte wie Slot 0
TRUNCATION_MIN_OCR = 10        # Ab so vielen gelesenen Zeichen darf ein abgeschnittener Name matchen
TRUNCATED_LONG_OCR = 18        # So lange Texte sind im Detail-Panel meist abgeschnitten
BETTER_MATCH_MARGIN = 0.03     # Passt eine andere Deck-Karte um so viel besser → nicht diese Karte

# ── Sonderregeln für lange / abgeschnittene Namen ──
ULTRA_LONG_NAME = 25           # Längere Namen werden im Detail-Panel oft abgeschnitten
ULTRA_LONG_MIN_OCR = 12
PREFIX_OVERLAP_MIN = 12        # Mindestens so viele Zeichen am Anfang identisch …
PREFIX_OVERLAP_SHARE = 0.6     # … und mindestens dieser Anteil der gelesenen Zeichen
SINGLE_RESULT_OVERLAP_MIN = 10       # Lockerer, wenn die Suche nur ein Ergebnis hat
SINGLE_RESULT_OVERLAP_SHARE = 0.5
NUMBER_TRUNCATED_MIN_OCR = 18

# ── Raster ──
MAX_GRID_SLOTS = 42            # 7 Zeilen à 6 Karten
GRID_BOTTOM_LIMIT = 0.93       # Anteil der Bildschirmhöhe; darunter liegt kein Slot mehr
END_REPEAT_STREAK = 3          # 4x dieselbe Karte in Folge = Ende der Ergebnisse (max. 3 Raritäten)
END_EMPTY_STREAK = 6           # So viele leere Reads in Folge = Ende der Ergebnisse
SCROLL_NOTCHES = 4             # Mausrad-Rasten pro Seite (1 Raste ≈ ¾ Zeile → 4 Rasten = 3 Zeilen)
MAX_SCROLL_PAGES = 3           # So oft wird höchstens weitergescrollt
RETYPE_AFTER_READS = 4         # Zeigt Slot 0 so lange (~1,5 s) die alte Karte → Suche neu eintippen

# ── Pause nach dem Einfügen (× Tempo), bevor die Maus weitermacht ──
POST_ADD_PAUSE = 0.35            # Standard
POST_ADD_PAUSE_CONFIRMED = 0.15  # Deck-Zählung hat bestätigt, dass das Spiel die Karte angenommen hat

BLIND_CARD = "BLIND_CARD"      # Marker: Texterkennung liefert dauerhaft nichts

SPEED_MULTIPLIERS = {"fast": 0.85, "normal": 1.0, "slow": 1.5}

# ── Selbstheilung ──
TEMPO_STEPS = (0.85, 1.0, 1.5, 2.0)   # Stufen der automatischen Verlangsamung
TROUBLE_LIMIT = 2                     # So viele Timing-Probleme → eine Stufe langsamer
SECOND_PASS_SPEED = 2.0               # Tempo im zweiten Durchgang für nicht gefundene Karten
VERIFY_RETRIES = 2                    # Nachklick-Versuche, wenn die Deck-Zählung nicht stimmt
CALIBRATION_KEYS = ("SEARCH_BAR", "FIRST_CARD", "TRASH_BTN", "TRASH_CONFIRM", "UNOWNED_BTN")


class DeckCard(NamedTuple):
    cid: str
    name: str
    amount: int


def common_prefix_len(target: str, ocr: str) -> Tuple[int, int]:
    """
    Wie viele Zeichen am Anfang stimmen überein? Ein führendes 'l' in der OCR ist ein
    bekanntes Artefakt (Icon links vom Namen) und wird ignoriert.
    Returns: (gemeinsame Zeichen, Länge der bereinigten OCR).
    """
    ocr = ocr[1:] if ocr.startswith("l") else ocr
    common = 0
    for a, b in zip(target, ocr):
        if a != b:
            break
        common += 1
    return common, len(ocr)


def name_score(name_clean: str, s_c: str) -> Tuple[float, bool]:
    """
    Wie gut passt der gelesene Text zu einem Kartennamen (0..1)? Berücksichtigt das bekannte
    führende 'l' (Icon links vom Namen) und im Detail-Panel abgeschnittene Namen: Dann zählt
    der Vergleich mit dem Anfang des Namens ('lamenomurakumonomit' → Ame no Murakumo no Mitsurugi).
    Returns: (Ratio, per_Abschneiden_gematcht).
    """
    best, truncated = 0.0, False
    texts = (s_c, s_c[1:]) if s_c.startswith("l") and len(s_c) > 1 else (s_c,)
    for text in texts:
        ratio = SequenceMatcher(None, name_clean, text).ratio()
        if ratio > best:
            best, truncated = ratio, False
        cut_off = ((len(name_clean) > len(text) * 1.3 and len(text) >= TRUNCATION_MIN_OCR)
                   or (len(name_clean) > len(text) and len(text) >= TRUNCATED_LONG_OCR))
        if cut_off:
            prefix_ratio = SequenceMatcher(None, name_clean[:len(text)], text).ratio()
            if prefix_ratio > best:
                best, truncated = prefix_ratio, True
    return best, truncated


def number_card_overrule(target_clean: str, s_c: str) -> Optional[str]:
    """
    Number-Karten, deren Name im Panel abgeschnitten oder am Ende verrauscht ist.
    Nur wenn die Nummer exakt gleich ist (inkl. C/S/F-Präfix: 106 ≠ C106), die OCR abgeschnitten
    aussieht und NICHT länger ist als das Ziel (sonst würde "Number 39: Utopia" die Karte
    "Number 39: Utopia Beyond" matchen). Deutsche Namen beginnen mit "Nummer".
    Returns: die Nummer bei Treffer, sonst None.
    """
    ocr = s_c[1:] if re.match(r"^l(?:number|nummer)", s_c) else s_c
    t = re.match(r"^(?:number|nummer)([a-z]{0,2}\d+)(.*)$", target_clean)
    o = re.match(r"^(?:number|nummer)([a-z]{0,2}\d+)(.*)$", ocr)
    if not (t and o and t.group(1) == o.group(1)):
        return None
    t_rest, o_rest = t.group(2), o.group(2)
    if (len(ocr) >= NUMBER_TRUNCATED_MIN_OCR and o_rest and len(o_rest) <= len(t_rest)
            and SequenceMatcher(None, t_rest[:len(o_rest)], o_rest).ratio() >= 0.85):
        return t.group(1)
    return None


class DeckImporterCore:
    def __init__(
        self,
        config: dict,
        tesseract_cmd: str,
        status_callback: Callable,
        finish_callback: Callable,
        start_callback: Optional[Callable] = None,
        resume: Optional[dict] = None
    ):
        self.config = config
        self.tesseract_cmd = tesseract_cmd
        self.status_callback = status_callback
        self.finish_callback = finish_callback
        self.start_callback = start_callback  # Wird nach dem Countdown aufgerufen (Start des Timers)

        self.validator = MatchValidator(CardMatcher())

        self._last_frame_hash = ""
        self._last_raw_ocr = ""
        self._last_clean_ocr = ""

        # Speed-Profil skaliert kritische Wartezeiten für unterschiedliche Hardware.
        self.speed_mult = SPEED_MULTIPLIERS.get(self.config.get("SPEED_PROFILE", "normal"), 1.0)

        # Texterkennungs-Sprache: Deutsch nur, wenn der deutsche Sprachdatensatz vorhanden ist
        self.wants_german = self.config.get("LANGUAGE", "en") == "de"
        deu_data = os.path.join(os.path.dirname(tesseract_cmd), "tessdata", "deu.traineddata")
        self.ocr_lang = "deu" if (self.wants_german and os.path.exists(deu_data)) else "eng"

        # Selbstheilung
        self.resume = resume              # Gespeicherter Stand eines abgebrochenen Imports
        self._progress: Dict[str, int] = {}   # cid → eingefügte Anzahl (für "Fortsetzen")
        self._card_ids: List[str] = []
        self.card_types: Dict[str, str] = {}
        self._deck_clean_names: set = set()  # Alle Kartennamen des Decks (clean_text)
        self._open_pool: List[DeckCard] = []  # Karten dieses Laufs (für "nebenbei einfügen")
        self._deck_scores_for: Optional[tuple] = None
        self._deck_scores_cache: Dict[str, float] = {}
        self.deck_counter: Optional[DeckCounter] = None
        self._trouble = 0
        self._auto_slowed = False
        self._lag_events = 0              # Wie oft das Spiel verzögert auf Klicks reagiert hat
        self._insert_confirmed = False    # Letzte Einfügung per Deck-Zählung bestätigt?
        self._grid_seen: set = set()      # Texte, die bei der aktuellen Suche im Raster gelesen wurden
        self._previous_grid: set = set()  # … und bei der vorigen Suche (siehe Schnell-Sync)
        self.notes: List[str] = []        # Hinweise für den Nutzer am Ende

    def execute_import(self):
        start_run(LOG_FILE)
        dlog(f"=== MD DECK IMPORTER {APP_VERSION} | Lauf vom {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
        dlog(f"=== Tempo: {self.config.get('SPEED_PROFILE', 'normal')} | "
             f"Sprache: {self.config.get('LANGUAGE', 'en')} | Texterkennung: {self.ocr_lang} ===")
        if self.wants_german and self.ocr_lang != "deu":
            dlog("[WARNUNG] Sprache Deutsch gewählt, aber Tesseract-OCR/tessdata/deu.traineddata fehlt. "
                 "Texterkennung läuft auf Englisch (Umlaute werden schlechter erkannt).")
        # Die Suche läuft über die Zwischenablage. Danach den vorherigen Inhalt (meist den
        # Deck-Code) wiederherstellen, damit man direkt erneut importieren kann.
        original_clipboard = self._read_clipboard()
        try:
            self._run_import()
        except Exception as e:
            dlog(f"\n[CRITICAL ERROR]: {e}\n{traceback.format_exc()}")
            message = str(e)
            if self._progress:
                message += (f"\n\nFortschritt gespeichert ({len(self._progress)} Karten eingefügt). "
                            "Beim nächsten Start mit demselben Deck-Code kannst du fortsetzen.")
            self.status_callback("Abbruch / Fehler", "red")
            self.finish_callback(success=False, has_errors=True, failed_cards=[], message=message)
        finally:
            self._restore_clipboard(original_clipboard)
            end_run()

    @staticmethod
    def _read_clipboard() -> Optional[str]:
        try:
            return pyperclip.paste()
        except Exception as e:
            dlog(f"[ZWISCHENABLAGE] Konnte nicht gelesen werden: {e}")
            return None

    @staticmethod
    def _restore_clipboard(text: Optional[str]) -> None:
        if text is None:
            return
        try:
            pyperclip.copy(text)
        except Exception as e:
            dlog(f"[ZWISCHENABLAGE] Konnte nicht wiederhergestellt werden: {e}")

    def _run_import(self):
        card_ids = parse_clipboard()
        if not card_ids:
            self.status_callback("Fehler: Kein YDKE/YDK!", "red")
            self.finish_callback(success=False, has_errors=True, failed_cards=[],
                                 message="Kein Deck-Code in der Zwischenablage. Bitte einen YDKE-Link "
                                         "oder eine YDK-Liste kopieren und erneut starten.")
            return
        self._card_ids = card_ids

        # ── Prüfung vor dem Start: lieber klar abbrechen als blind falsch klicken ──
        problem = self._preflight()
        if problem:
            dlog(f"[CHECK] Abbruch vor dem Start: {problem}")
            self.status_callback("Start nicht möglich", "red")
            self.finish_callback(success=False, has_errors=True, failed_cards=[], message=problem)
            return

        original_counts = Counter(card_ids)
        cards_ready, id_to_name_map = self._resolve_card_names(original_counts, self.config.get("LANGUAGE", "en"))
        self._deck_clean_names = {clean_text(sanitize_name(c.name)) for c in cards_ready}
        if self.config.get("DECK_COUNT"):
            self.card_types = fetch_card_types([c.cid for c in cards_ready])

        done = dict(self.resume["done"]) if self.resume else {}
        self._progress = dict(done)

        for i in range(3, 0, -1):
            self.status_callback(f"Maus loslassen! ({i}s)", "yellow")
            time.sleep(1)

        import_t0 = time.perf_counter()
        if self.start_callback:
            self.start_callback()

        automator = WindowAutomator(self.config)
        automator.last_bot_pos = win_api.get_cursor_pos()

        successfully_added = []
        if done:
            dlog(f"[FORTSETZEN] {len(done)} Karten waren schon eingefügt → Deck wird nicht geleert.")
            for cid, amount in done.items():
                name = sanitize_name(id_to_name_map.get(cid, ""))
                successfully_added.append({"expected_clean": clean_text(name), "expected_raw": name,
                                           "actual_ocr": "FORTGESETZT", "amount": amount,
                                           "is_fallback": False, "stall_suspect": False})
        else:
            self._clear_existing_deck(automator)

        remaining = [c for c in cards_ready if c.cid not in done]
        self._open_pool = remaining  # Karten, die bei jeder Suche nebenbei eingefügt werden dürfen
        last_added_ocr_clean = ""
        failed: List[DeckCard] = []

        # Batch-Gruppen vor dem Loop berechnen
        batch_groups = self._compute_batch_groups(remaining)
        cid_to_batch_prefix: Dict[str, str] = {}
        for prefix, group in batch_groups.items():
            for c, _, _ in group:
                cid_to_batch_prefix[c] = prefix
        batch_done_cids: set = set()
        batch_triggered: set = set()

        if batch_groups:
            dlog(f"[BATCH-PLAN] {len(batch_groups)} Archetype-Gruppen erkannt:")
            for pfx, grp in batch_groups.items():
                dlog(f"    '{pfx}' -> {len(grp)} Karten")
        else:
            dlog("[BATCH-PLAN] Keine Batch-Gruppen gefunden.")

        with mss.MSS() as sct:
            self.deck_counter = self._init_deck_counter(sct, automator, expect_empty=not done)

            monitor, t_x, t_y = self._get_slot_geometry(0, automator)
            automator.iron_grip_click(t_x, t_y)
            time.sleep(0.35)
            _, last_seen_slot_00 = self._capture_and_ocr_slot(sct, monitor)

            dlog(f"[INIT] Start-Sicherheits-Scan auf Slot 00 registriert: '{last_seen_slot_00}'")

            for i, card in enumerate(remaining):
                cid, raw_name, amount = card
                clean_name = sanitize_name(raw_name)

                # ── BATCH: Bereits via Archetype-Scan gefunden ──
                if cid in batch_done_cids:
                    self.status_callback(f"-> {clean_name[:15]} [BATCH✓]", "green")
                    dlog(f"\n[BATCH SKIP] '{clean_name}' wurde bereits im Batch-Scan gefunden.")
                    continue
                # ── Schon bei der Suche nach einer anderen Karte nebenbei eingefügt ──
                if cid in self._progress:
                    self.status_callback(f"-> {clean_name[:15]} [✓]", "green")
                    dlog(f"\n[NEBENBEI SKIP] '{clean_name}' wurde schon bei einer anderen Suche eingefügt.")
                    continue

                # ── BATCH: Erste Karte dieser Gruppe → Batch-Scan auslösen ──
                prefix = cid_to_batch_prefix.get(cid)
                if prefix and prefix not in batch_triggered:
                    batch_triggered.add(prefix)
                    group = [g for g in batch_groups[prefix] if g[0] not in self._progress]
                    self.status_callback(f"[BATCH] {prefix[:12]}...", "magenta")

                    # Opportunistic: alle noch offenen Karten außerhalb der Gruppe.
                    # Der Batch kann diese direkt einfügen, falls sie in den Such-Ergebnissen
                    # sichtbar sind (z.B. Mirror Swordknight im Chimera-Batch).
                    opportunistic_cards = self._open_cards(exclude={c for c, _, _ in group})

                    found_in_batch, batch_slot0 = self._batch_scan_for_archetype(
                        sct, automator, prefix, group, successfully_added,
                        last_seen_slot_00=last_seen_slot_00,
                        opportunistic_cards=opportunistic_cards
                    )
                    batch_done_cids.update(found_in_batch)
                    # last_seen_slot_00 aktualisieren, damit Folge-Einzel-Scans korrekt synchen
                    if batch_slot0:
                        last_seen_slot_00 = batch_slot0
                    not_found_in_batch = [c for c, _, _ in group if c not in found_in_batch]
                    if not_found_in_batch:
                        dlog(f"    [BATCH] {len(not_found_in_batch)} Karten fallen auf Einzel-Scan zurück.")
                    if cid in batch_done_cids:
                        continue
                    # Aktuelle Karte NICHT im Batch → normaler Scan folgt

                found, last_seen_slot_00, last_added_ocr_clean = self._search_single_card(
                    sct, automator, card, successfully_added, last_seen_slot_00, last_added_ocr_clean)
                if not found:
                    failed.append(card)

            # ── Zweiter Durchgang: nicht gefundene Karten mit langsamem Tempo erneut suchen.
            # Sicher, weil diese Karten nachweislich nie angeklickt wurden (keine Kopie zu viel).
            # Inzwischen nebenbei bei einer anderen Suche eingefügte Karten fallen heraus.
            failed = [c for c in failed if c.cid not in self._progress]
            if failed:
                dlog(f"\n[NACHLAUF] {len(failed)} Karte(n) nicht gefunden → zweiter Versuch mit "
                     f"langsamem Tempo (×{SECOND_PASS_SPEED}).")
                self.status_callback("Zweiter Durchgang...", "yellow")
                normal_mult = self.speed_mult
                self.speed_mult = max(self.speed_mult, SECOND_PASS_SPEED)
                recovered = 0
                for card in failed:
                    found, last_seen_slot_00, last_added_ocr_clean = self._search_single_card(
                        sct, automator, card, successfully_added, last_seen_slot_00, last_added_ocr_clean)
                    recovered += bool(found)
                self.speed_mult = normal_mult
                dlog(f"[NACHLAUF] {recovered} von {len(failed)} Karte(n) nachgeholt.")
                if recovered:
                    self.notes.append(f"{recovered} Karte(n) wurden im zweiten Durchgang nachgeholt.")

        has_errors, popup_failed_cards = self._write_final_audit(
            original_counts, id_to_name_map, successfully_added
        )

        minutes, secs = divmod(int(time.perf_counter() - import_t0), 60)
        dlog(f"\n[DAUER] Import in {minutes}:{secs:02d} min abgeschlossen.")

        if self._auto_slowed:
            self.notes.append(f"Master Duel hat mehrmals langsamer reagiert als erwartet, das Tempo wurde "
                              f"automatisch auf ×{self.speed_mult:.2f} erhöht. Passiert das öfter, "
                              f"stelle 'Tempo: Langsam' ein.")
        if self._lag_events:
            dlog(f"[LAG] Master Duel hat {self._lag_events}x verzögert auf Klicks reagiert.")
            if not self.deck_counter:
                self.notes.append(
                    f"Master Duel hat {self._lag_events}x verzögert auf Klicks reagiert (PC ausgelastet?). "
                    f"Ob dabei Karten nicht eingefügt wurden, kann der Import nur prüfen, wenn der Punkt "
                    f"'Deck-Kartenzahl' kalibriert ist. Bitte das Deck kurz kontrollieren und beim nächsten "
                    f"Kalibrieren den 6. Punkt setzen.")
        resume_state.clear_progress()
        self.finish_callback(success=True, has_errors=has_errors, failed_cards=popup_failed_cards,
                             notes=self.notes)

    def _search_single_card(self, sct, automator, card: DeckCard, successfully_added,
                            last_seen_slot_00: str, last_added_ocr_clean: str) -> Tuple[bool, str, str]:
        """Eine Karte einzeln suchen und einfügen. Returns: (gefunden, last_seen_slot_00, last_added)."""
        clean_name = sanitize_name(card.name)
        self.status_callback(f"-> {clean_name[:15]}", "cyan")
        dlog(f"\n[SUCHE] '{clean_name}' (ID: {card.cid}, Erwartet: {card.amount}x)")

        self._begin_search(automator, clean_name)

        found, last_added_ocr_clean, first_slot_text = self._scan_slots_for_card(
            sct, automator, clean_name, card.amount,
            last_added_ocr_clean, successfully_added, last_seen_slot_00, cid=card.cid
        )
        if first_slot_text and first_slot_text != BLIND_CARD:
            last_seen_slot_00 = first_slot_text
        return found, last_seen_slot_00, last_added_ocr_clean

    # =====================================================================
    # SELBSTHEILUNG: Start-Prüfung, Tempo-Anpassung, Deck-Prüfung, Fortsetzen
    # =====================================================================

    def _preflight(self) -> Optional[str]:
        """Prüft vor dem ersten Klick, ob alles bereit ist. Returns: Fehlermeldung oder None."""
        problem = vision_engine.check_tesseract(self.tesseract_cmd, "eng")
        if problem:
            return problem

        size = get_md_window_size()
        if size is None:
            return ("Master Duel wurde nicht gefunden. Bitte das Spiel starten, den Deck-Editor "
                    "öffnen und den Import erneut starten.")
        calibrated = self.config.get("CALIBRATED_SIZE")
        if calibrated and (abs(calibrated[0] - size[0]) > 4 or abs(calibrated[1] - size[1]) > 4):
            return (f"Die Fenstergröße von Master Duel hat sich seit der Kalibrierung geändert "
                    f"({calibrated[0]}×{calibrated[1]} → {size[0]}×{size[1]}). Bitte neu kalibrieren.")
        if not calibrated:
            # Ältere Kalibrierung ohne gespeicherte Größe: aktuellen Stand als Referenz merken
            self.config["CALIBRATED_SIZE"] = list(size)
            dlog(f"[CHECK] Fenstergröße {size[0]}×{size[1]} als Kalibrierungs-Referenz gemerkt.")

        vx, vy, vw, vh = win_api.virtual_screen_rect()
        for key in CALIBRATION_KEYS:
            point = self.config.get(key)
            if not point or not (vx <= point[0] < vx + vw and vy <= point[1] < vy + vh):
                return f"Kalibrierpunkt '{key}' fehlt oder liegt außerhalb des Bildschirms. Bitte neu kalibrieren."
        dlog(f"[CHECK] Start-Prüfung ok: Master Duel {size[0]}×{size[1]}, Texterkennung bereit.")
        return None

    def _report_trouble(self, reason: str) -> None:
        """Anzeichen, dass Master Duel langsamer ist als das Tempo. Häufen sie sich → langsamer."""
        self._trouble += 1
        dlog(f"    [TEMPO] Anzeichen für zu schnelles Tempo: {reason} ({self._trouble}/{TROUBLE_LIMIT})")
        if self._trouble < TROUBLE_LIMIT:
            return
        self._trouble = 0
        slower = next((step for step in TEMPO_STEPS if step > self.speed_mult + 1e-9), None)
        if slower is None:
            return
        dlog(f"    [TEMPO] Automatisch verlangsamt: ×{self.speed_mult:.2f} → ×{slower:.2f}")
        self.speed_mult = slower
        self._auto_slowed = True
        self.status_callback("Tempo automatisch verlangsamt", "yellow")

    def _init_deck_counter(self, sct, automator, expect_empty: bool) -> Optional[DeckCounter]:
        if not self.config.get("DECK_COUNT"):
            dlog("[PRÜFUNG] Deck-Zählung nicht kalibriert → Einfüge-Prüfung aus "
                 "(Kalibrieren → Punkt 'Deck-Kartenzahl').")
            return None
        counter = DeckCounter(sct, self.config, self.tesseract_cmd, automator.scale_x, automator.scale_y)
        value = counter.read()
        if value is None:
            dlog("[PRÜFUNG] Deck-Zählung nicht lesbar → Einfüge-Prüfung für diesen Lauf aus.")
            self.notes.append("Die Deck-Kartenzahl war nicht lesbar, eingefügte Karten wurden nicht "
                              "nachgeprüft. Evtl. den Punkt 'Deck-Kartenzahl' neu kalibrieren.")
            return None
        if expect_empty and value != 0:
            # Zeigt nach dem Leeren nicht 0: entweder falsch gelesen oder Deck nicht geleert.
            # Nicht blind erneut leeren (Klicks könnten woanders landen) → Prüfung aus, Hinweis.
            dlog(f"[PRÜFUNG] Deck-Zählung zeigt nach dem Leeren {value} statt 0 → Prüfung aus.")
            self.notes.append(f"Nach dem Leeren zeigte das Deck {value} statt 0 Karten. Bitte prüfen, ob "
                              f"das alte Deck wirklich geleert wurde.")
            return None
        counter.value = value
        dlog(f"[PRÜFUNG] Deck-Zählung aktiv, Startwert: {value}.")
        return counter

    def _verify_insert(self, automator, x: int, y: int, amount: int, name: str, cid: Optional[str],
                       stalled: bool) -> bool:
        """
        Prüft per Deck-Zählung, ob die Karte angekommen ist, und klickt fehlende Kopien nach.
        Nur für Main-Deck-Karten mit bekanntem Typ (Extra-Deck-Karten ändern die Zahl nicht).
        Returns: True, wenn die Karte trotzdem unsicher ist (→ im Report markieren).
        """
        self._insert_confirmed = False
        counter = self.deck_counter
        card_type = self.card_types.get(cid) if cid else None
        if not counter or card_type is None or is_extra_deck_type(card_type):
            return stalled
        if counter.value is None:
            return stalled  # Stand unbekannt (zuvor nicht lesbar) → nicht prüfbar
        expected = counter.value + amount
        got = counter.wait_for(expected, 0.8 * self.speed_mult)
        if got is not None and got < expected:
            # Auf schwachen PCs zählt das Spiel evtl. nur verspätet hoch. Erst nach einer zweiten
            # Wartezeit nachklicken: Ein Nachklick auf eine doch angekommene Karte wäre eine Kopie zu viel.
            got = counter.wait_for(expected, 0.8 * self.speed_mult)

        for attempt in range(VERIFY_RETRIES):
            if got is None or got >= expected:
                break
            missing = expected - got
            dlog(f"    [PRÜFUNG] '{name}': Deck zeigt {got} statt {expected} → {missing}x nachklicken "
                 f"(Versuch {attempt + 1}/{VERIFY_RETRIES}).")
            self._report_trouble("Klicks beim Einfügen nicht angekommen")
            automator.add_card_to_deck(x, y, missing)
            got = counter.wait_for(expected, 0.8 * self.speed_mult)

        if got == expected:
            counter.value = got
            self._insert_confirmed = True
            if stalled:
                dlog(f"    [PRÜFUNG] '{name}' trotz Ruckler bestätigt ({got} Karten im Deck).")
            return False
        if got is None:
            dlog(f"    [PRÜFUNG] Deck-Zählung nicht lesbar → '{name}' ungeprüft.")
            counter.value = counter.read()
            return stalled
        dlog(f"    [PRÜFUNG] '{name}': Deck zeigt {got}, erwartet {expected} → im Report markiert.")
        counter.value = got
        return True

    def _mark_done(self, cid: str, amount: int) -> None:
        """Fortschritt speichern, damit ein abgebrochener Import fortgesetzt werden kann."""
        self._progress[cid] = amount
        if not self._card_ids:
            return  # kein laufender Import (z.B. einzelne Funktionen in Tests)
        try:
            resume_state.save_progress(self._card_ids, self._progress)
        except OSError as e:
            dlog(f"[FORTSETZEN] Fortschritt konnte nicht gespeichert werden: {e}")

    def _resolve_card_names(self, original_counts: Counter, lang: str) -> Tuple[List[DeckCard], Dict[str, str]]:
        self.status_callback("API Check...", "cyan")
        names = fetch_card_names(list(original_counts.keys()), lang)

        cards_ready = []
        id_to_name_map = {}
        for cid, amount in original_counts.items():
            name = names.get(str(cid))
            if name:
                cards_ready.append(DeckCard(str(cid), name, amount))
                id_to_name_map[str(cid)] = name
            else:
                dlog(f"[API] Kein Kartenname für ID {cid} gefunden → wird im Abschluss-Report gemeldet.")
        return cards_ready, id_to_name_map

    def _clear_existing_deck(self, automator: WindowAutomator):
        self.status_callback("Leere Deck...", "yellow")
        automator.iron_grip_click(*self.config["TRASH_BTN"])
        time.sleep(0.4)
        automator.iron_grip_click(*self.config["TRASH_CONFIRM"])
        time.sleep(0.5)
        if not automator.is_crafting_active():
            automator.iron_grip_click(*self.config["UNOWNED_BTN"])
            time.sleep(0.15)

    def _type_search_term(self, automator: WindowAutomator, clean_name: str):
        type_card_name(automator, clean_name, self.config)

    def _begin_search(self, automator: WindowAutomator, term: str) -> None:
        """Neue Suche starten; merkt sich, welche Karten das bisherige Raster zeigte."""
        self._previous_grid, self._grid_seen = self._grid_seen, set()
        self._type_search_term(automator, term)

    def _add_card(self, automator: WindowAutomator, x: int, y: int, amount: int, name: str,
                  cid: Optional[str] = None) -> bool:
        """
        Fügt die Karte ein und prüft sie (falls möglich) per Deck-Zählung.
        Returns: True, wenn unsicher ist, ob die Karte angekommen ist (→ im Report markieren).
        """
        stalled = automator.add_card_to_deck(x, y, amount)
        # Sofort als eingefügt merken: Bricht der Import danach ab, darf "Fortsetzen" diese
        # Karte nicht noch einmal einfügen (sonst eine Kopie zu viel).
        if cid is not None:
            self._mark_done(cid, amount)
        if stalled:
            dlog(f"    [RUCKLER] System-Hänger beim Einfügen von '{name}' erkannt. "
                 f"Klicks evtl. verloren.")
        return self._verify_insert(automator, x, y, amount, name, cid, stalled)

    def _pause_after_insert(self) -> None:
        """
        Kurz warten, bevor die Maus weitermacht. Hat die Deck-Zählung bestätigt, dass das Spiel
        die Karte angenommen hat, reicht eine kürzere Pause (das Warten auf die Zahl lief ja schon).
        """
        pause = POST_ADD_PAUSE_CONFIRMED if self._insert_confirmed else POST_ADD_PAUSE
        time.sleep(pause * self.speed_mult)

    # =====================================================================
    # ARCHETYPE-BATCH-SCAN (V49 - Speed Boost)
    # =====================================================================

    # Erste Wörter, die zu generisch sind für eine Batch-Suche
    _BATCH_NOISE = {
        'dark', 'light', 'fire', 'water', 'earth', 'wind', 'divine', 'chaos',
        'black', 'white', 'red', 'blue', 'green', 'yellow', 'number', 'numeron',
        'true', 'super', 'ultra', 'hyper', 'mega', 'neo', 'great', 'high', 'evil',
        'ancient', 'sacred', 'crystal', 'cyber', 'dragon', 'galaxy', 'star', 'solar',
        'elemental', 'armed', 'the', 'of', 'for', 'a', 'an',
        # Deutsche Master-Duel-Namen
        'dunkel', 'dunkle', 'dunkler', 'licht', 'feuer', 'wasser', 'erde', 'göttlich', 'göttliche',
        'schwarz', 'schwarze', 'schwarzer', 'weiß', 'weiße', 'weißer', 'roter', 'rote', 'blauer',
        'blaue', 'grüner', 'grüne', 'gelber', 'nummer', 'wahrer', 'wahre', 'uralter', 'uralte',
        'heilige', 'heiliger', 'kristall', 'drache', 'drachen', 'galaxie', 'stern', 'sonne',
    }

    @staticmethod
    def _normalize_word(w: str) -> str:
        """Normalisiert ein Wort für Archetype-Vergleiche: 'exosisters' → 'exosister'."""
        return w[:-1] if (w.endswith('s') and len(w) > 4) else w

    def _compute_batch_groups(self, cards_ready: List) -> Dict[str, List]:
        """
        Erkennt Karten-Gruppen die denselben Archetype-Präfix teilen.
        Nur Gruppen mit >= 3 Karten und einem Präfix >= 6 Zeichen werden gebündelt.

        Plural-Normalisierung: "exosisters" fällt in denselben Bucket wie "exosister".
        Präfix-Berechnung: wortweise (mit Leerzeichen!) damit der Search-Term korrekt ist,
        z.B. "kewl tune" statt "kewltune". Plural-Varianten werden beim Wort-Vergleich
        normalisiert, sodass "exosisters magnifica" mit "exosister martha" grouped wird.

        Returns: {prefix_str: [(cid, raw_name, amount), ...]}
        """
        MIN_GROUP_SIZE = 3
        MIN_PREFIX_CHARS = 6

        # Schritt 1: Nach normalisiertem ersten Wort gruppieren
        # Plural-Behandlung: "exosisters" → Bucket "exosister" (trailing 's' entfernen)
        first_word_groups: Dict[str, List] = {}
        for cid, raw_name, amount in cards_ready:
            words = re.findall(r'[^\W\d_]+', raw_name.lower())
            if not words:
                continue
            first = words[0]
            if len(first) < 4 or first in self._BATCH_NOISE:
                continue
            bucket_key = self._normalize_word(first)
            first_word_groups.setdefault(bucket_key, []).append((cid, raw_name, amount))

        # Schritt 2: Längsten gemeinsamen Wort-Präfix bestimmen (mit Plural-Normalisierung)
        # Wort-Level (nicht Zeichen-Level!) damit der Such-Term Leerzeichen enthält.
        result: Dict[str, List] = {}
        for bucket_key, group in first_word_groups.items():
            if len(group) < MIN_GROUP_SIZE:
                continue

            all_word_lists = [re.findall(r'[^\W\d_]+', raw.lower()) for _, raw, _ in group]
            prefix_words = []
            for word_tuple in zip(*all_word_lists):
                # Normalisierte Menge: "exosisters" und "exosister" gelten als gleich
                normalized = {self._normalize_word(w) for w in word_tuple}
                if len(normalized) == 1:
                    prefix_words.append(next(iter(normalized)))
                else:
                    break

            prefix = ' '.join(prefix_words)
            if len(prefix) >= MIN_PREFIX_CHARS:
                result[prefix] = group

        return result

    def _batch_scan_for_archetype(
        self, sct, automator, prefix: str, group_cards: List, successfully_added: List,
        last_seen_slot_00: str = "",
        opportunistic_cards: Optional[Dict[str, Tuple]] = None
    ) -> Tuple[set, str]:
        """
        Tippt den Archetype-Präfix einmal und matched alle sichtbaren Slots
        gegen alle ausstehenden Gruppenkarten in einem einzigen Scan-Durchgang.
        Wartet aktiv bis Slot 0 neue Ergebnisse zeigt (kein blindes Sleep).
        Returns: (Set von gefundenen cids, aktueller Slot-0-Text für last_seen_slot_00).
        """
        # Pending: clean_name -> (cid, raw_name, amount)
        pending: Dict[str, Tuple] = {}
        for cid, raw_name, amount in group_cards:
            pending[sanitize_name(raw_name)] = (cid, raw_name, amount)

        found_cids: set = set()
        max_y = 1080 * automator.scale_y * GRID_BOTTOM_LIMIT

        dlog(f"\n[BATCH] Archetype-Präfix '{prefix}' | {len(group_cards)} Karten\n")
        for cn in pending:
            dlog(f"    - '{cn}'\n")

        self._begin_search(automator, prefix)

        # ── Aktiv warten bis Slot 0 neue Suchergebnisse zeigt ──
        # Zuverlässiges Signal: Such-Prefix muss in der OCR auftauchen.
        # Der reine "hat sich geändert"-Check ist unzuverlässig weil das Detail-Panel
        # zwischen Slot-0-Klicks andere Karten der ALTEN Suche zeigen kann (z.B. nach
        # Swordsoul-Batch hovert Maus auf Swordsoul Assessment statt Blackout).
        monitor0, x0, y0 = self._get_slot_geometry(0, automator)
        automator.iron_grip_click(x0, y0)
        time.sleep(0.20 * self.speed_mult)
        new_slot0_text = ""
        prefix_clean = clean_text(prefix)  # z.B. "kewl tune" → "kewltune"

        for wait_try in range(20):  # max ~2,5 Sekunden (×1.5 bei slow → ~3,75s)
            self._last_frame_hash = ""
            _, s0 = self._capture_and_ocr_slot(sct, monitor0)
            self._grid_seen.add(s0)
            # Such-Prefix in OCR → neues Such-Ergebnis geladen.
            # Ausnahme: Zeigt Slot 0 exakt dieselbe Karte wie vor der Suche (z.B. Artmage Power
            # Patron war schon vorher in Slot 0), kann das noch die alte Anzeige sein. Dann erst
            # nach Mindest-Ladezeit inkl. Re-Klick (wait_try >= 4, ~0.75s) akzeptieren.
            is_same_as_before = bool(last_seen_slot_00) and s0 == last_seen_slot_00
            if s0 and prefix_clean and prefix_clean in s0 and (not is_same_as_before or wait_try >= 4):
                new_slot0_text = s0
                dlog(f"    [BATCH] Slot 0 geladen nach {wait_try} Retries: '{s0}'\n")
                break
            # Nach ~1,5 s noch kein Ergebnis der Archetyp-Suche → Suche kam nicht an, neu eintippen
            if wait_try == 10:
                dlog(f"    [SUCHE NEU] Such-Prefix '{prefix_clean}' nach {wait_try} Versuchen nicht in Slot 0 "
                     f"→ tippe '{prefix}' erneut.")
                self._report_trouble("Suche kam nicht im Spiel an")
                self._type_search_term(automator, prefix)
                time.sleep(0.40 * self.speed_mult)
                automator.iron_grip_click(x0, y0)
            # Alle 3 Retries Slot 0 erneut klicken, damit das Detail-Panel aktualisiert wird
            elif wait_try > 0 and wait_try % 3 == 0:
                automator.iron_grip_click(x0, y0)
                time.sleep(0.05 * self.speed_mult)
            time.sleep(0.125 * self.speed_mult)
        else:
            new_slot0_text = last_seen_slot_00
            dlog(f"    [BATCH] WARNUNG: Such-Prefix '{prefix_clean}' nicht in Slot 0 erkannt nach 2.5s. Fahre trotzdem fort.\n")
            self._report_trouble("Archetyp-Suche nicht rechtzeitig geladen")

        self._last_frame_hash = ""

        # Verfolgt OCR-Text der zuletzt eingefügten Karte.
        # Wird als last_seen_slot_00 für den nächsten Batch/Einzel-Scan zurückgegeben,
        # damit der Folge-Scan weiß, was VOR dem neuen Suchergebnis im Panel stand.
        last_added_s_c = new_slot0_text

        # Ende-Erkennung wie im Einzel-Scan: Nach dem letzten Such-Ergebnis zeigt das Panel
        # beim Klick auf leere Slots weiter die letzte Karte. 4x identischer Text in Folge
        # (eine Karte hat max. 3 Raritäten) oder 6 leere Reads = Ergebnisliste zu Ende.
        repeat_streak = 0
        empty_streak = 0
        prev_s_c = ""

        for slot in range(MAX_GRID_SLOTS):
            if not pending:
                break

            monitor, target_x, target_y = self._get_slot_geometry(slot, automator)
            if target_y > max_y:
                break

            raw_ocr, s_c = self._read_slot(sct, automator, monitor, target_x, target_y, prev_s_c)

            if not s_c:
                empty_streak += 1
                if empty_streak >= END_EMPTY_STREAK:
                    dlog(f"    [BATCH] Ende erkannt: {empty_streak} leere Slots in Folge (Slot {slot:02d}).\n")
                    break
                continue
            empty_streak = 0

            repeat_streak = repeat_streak + 1 if s_c == prev_s_c else 0
            prev_s_c = s_c
            if repeat_streak >= END_REPEAT_STREAK:
                dlog(f"    [BATCH] Ende der Such-Ergebnisse erkannt: 4x '{s_c}' in Folge (Slot {slot:02d}).\n")
                break

            # ── BATCH BEST-MATCH: Karte mit höchster Ratio gewinnt ──
            # Verhindert dass zwei ähnliche Archetype-Karten sich gegenseitig
            # falsch matchen (z.B. Asophiel ↔ Kaspitell, Irene ↔ Gibrine).
            # Nach dem Einfügen zeigt der nächste Slot oft dieselbe Karte in anderer Seltenheit;
            # die darf nicht der ähnlich benannten nächsten Karte zugeordnet werden (Knight → Night).
            candidates = self._plausible_candidates(pending, s_c, BATCH_MATCH_THRESHOLD)
            best_key, best_ratio, best_truncated = (candidates[0] if candidates else (None, 0.0, False))

            # ── AMBIGUITÄTS-CHECK: Zwei Karten die per Truncation gleich gut matchen
            # können nicht unterschieden werden (z.B. zwei Varuroon-Varianten teilen
            # 'radianttyphoonvaruroon' als Präfix). Diesen Slot überspringen, damit
            # der Einzel-Scan mit vollem Namen die richtige Variante findet.
            is_ambiguous = False
            if best_truncated and len(candidates) >= 2:
                second_ratio = candidates[1][1]
                if second_ratio >= BATCH_MATCH_THRESHOLD and (best_ratio - second_ratio) < AMBIGUITY_MARGIN:
                    is_ambiguous = True
                    dlog(f"    [BATCH] Slot {slot:02d} '{s_c}' AMBIGUOUS - mehrere Karten matchen via Truncation ({best_key!r}={best_ratio:.3f} vs {candidates[1][0]!r}={second_ratio:.3f}). Überspringe → Einzel-Scan.\n")

            hit = None
            if best_key and best_ratio >= BATCH_MATCH_THRESHOLD and not is_ambiguous:
                hit = ("BATCH", pending, best_key, best_ratio)

            # ── OPPORTUNISTIC MATCH: Slot passt zu keiner Archetype-Karte,
            # aber zu einer anderen Deck-Karte die gerade sichtbar ist ──
            elif opportunistic_cards and not is_ambiguous:
                ranked = self._plausible_candidates(opportunistic_cards, s_c, OPPO_MATCH_THRESHOLD)
                if ranked and ranked[0][1] >= OPPO_MATCH_THRESHOLD:
                    hit = ("BATCH-OPPO", opportunistic_cards, ranked[0][0], ranked[0][1])

            if not hit and not is_ambiguous:
                best_info = f" (am ähnlichsten: {best_key!r} {best_ratio:.3f})" if best_key else ""
                dlog(f"    [BATCH] Slot {slot:02d} '{s_c}' → keine offene Deck-Karte{best_info}")
            if hit:
                tag, source, key, ratio = hit
                cid, _, amount = source.pop(key)
                match_type = "EXACT" if ratio >= 0.99 else "FUZZY"
                dlog(f"    [{tag}] Slot {slot:02d} '{s_c}' ==> MATCH '{key}' ({amount}x, {match_type}, ratio={ratio:.3f})\n")
                stalled = self._add_card(automator, target_x, target_y, amount, key, cid)
                self._pause_after_insert()
                found_cids.add(cid)
                last_added_s_c = s_c  # Panel zeigt jetzt diese Karte – für nächsten Sync merken
                successfully_added.append({
                    "expected_clean": clean_text(key),
                    "expected_raw": key,
                    "actual_ocr": raw_ocr,
                    "amount": amount,
                    "is_fallback": False,
                    "stall_suspect": stalled,
                })

        if pending:
            dlog(f"    [BATCH] Nicht im Raster (→ Einzel-Scan): {[r for _, r, _ in pending.values()]}\n")

        group_hits = len(found_cids & {c for c, _, _ in group_cards})
        oppo_hits = len(found_cids) - group_hits
        dlog(f"    [BATCH] Ergebnis: {group_hits}/{len(group_cards)} gefunden"
             f"{f' (+{oppo_hits} opportunistisch)' if oppo_hits else ''}\n")

        # last_added_s_c statt new_slot0_text zurückgeben: zeigt was das Panel
        # NACH dem Scan zeigt (letzte eingefügte Karte), nicht was am Anfang
        # beim Warten auf Slot 0 gelesen wurde.
        return found_cids, last_added_s_c

    @staticmethod
    def _rank_candidates(names, s_c: str) -> List[Tuple[str, float, bool]]:
        """
        Bewertet jeden Namen gegen den gelesenen Text (siehe name_score).
        Returns: [(name, ratio, per_Abschneiden_gematcht)] absteigend nach Ratio; bei
        Gleichstand bleibt die ursprüngliche Reihenfolge erhalten.
        """
        ranked = [(name, *name_score(clean_text(name), s_c)) for name in names]
        ranked.sort(key=lambda c: -c[1])
        return ranked

    def _get_slot_geometry(self, slot: int, automator: WindowAutomator) -> Tuple[dict, int, int]:
        row, col = slot // 6, slot % 6
        target_x = self.config["FIRST_CARD"][0] + int(col * self.config.get("OFFSET_X", 88) * automator.scale_x)
        target_y = self.config["FIRST_CARD"][1] + int(row * self.config.get("OFFSET_Y", 140) * automator.scale_y)
        x1, y1 = int(15 * automator.scale_x), int(115 * automator.scale_y)

        x2, y2 = int(380 * automator.scale_x), int(155 * automator.scale_y)
        monitor = {"top": y1, "left": x1, "width": x2 - x1, "height": y2 - y1}
        return monitor, target_x, target_y

    def _capture_and_ocr_slot(self, sct, monitor: dict) -> Tuple[str, str]:
        sct_img = sct.grab(monitor)
        img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX").convert('L')
        img_inverted = ImageOps.invert(img)

        img_processed = img_inverted.point(lambda p: 0 if p < 150 else 255)
        img_hash = hashlib.md5(img_processed.tobytes()).hexdigest()

        if img_hash == self._last_frame_hash:
            img.close()
            img_inverted.close()
            img_processed.close()
            return self._last_raw_ocr, self._last_clean_ocr

        def ocr(im):
            return vision_engine.do_ocr(im, self.tesseract_cmd, self.ocr_lang)

        # Cache-Schlüssel enthält die Sprache, damit ein Sprachwechsel keine alten Ergebnisse liefert
        raw_ocr = get_cached_ocr(f"{img_hash}_{self.ocr_lang}", ocr, img_processed)
        s_c = clean_text(raw_ocr)

        if not s_c:
            img_hash_raw = hashlib.md5(img_inverted.tobytes()).hexdigest() + "_raw_" + self.ocr_lang
            raw_ocr = get_cached_ocr(img_hash_raw, ocr, img_inverted)
            s_c = clean_text(raw_ocr)

        if not s_c:
            img_dark = img_inverted.point(lambda p: 0 if p < 80 else 255)
            img_hash_dark = hashlib.md5(img_dark.tobytes()).hexdigest() + "_dark_" + self.ocr_lang
            raw_ocr = get_cached_ocr(img_hash_dark, ocr, img_dark)
            s_c = clean_text(raw_ocr)
            img_dark.close()

        self._last_frame_hash = img_hash
        self._last_raw_ocr = raw_ocr
        self._last_clean_ocr = s_c

        img.close()
        img_inverted.close()
        img_processed.close()
        return raw_ocr, s_c

    def _check_match_with_overrule(self, clean_name, s_c) -> Tuple[bool, str]:
        """Validator-Entscheidung plus Overrule für abgeschnittene ultra-lange Namen."""
        is_match, match_type = self.validator.check_match(clean_name, s_c)

        # --- OVERRULE FÜR ABGESCHNITTENE ULTRA-LANGE NAMEN ---
        # clean_text() nötig: clean_name hat noch Großbuchstaben/Bindestriche/Leerzeichen,
        # s_c ist bereits lowercase ohne Sonderzeichen → Vergleich muss auf gleicher Basis sein.
        _cname_t = clean_text(clean_name)
        if not is_match and len(_cname_t) > ULTRA_LONG_NAME and s_c and len(s_c) >= ULTRA_LONG_MIN_OCR:
            overrule_reason = None
            # Pfad 1: SequenceMatcher-Ratio auf Präfix-Slice
            if SequenceMatcher(None, _cname_t[:len(s_c)], s_c).ratio() >= 0.85:
                overrule_reason = "ratio"
            else:
                # Pfad 2: Char-für-Char-Präfix-Overlap (toleriert OCR-Korruption am Ende
                # wie 'sinistar3b' statt 'sinistersov' bei Qixing Longyuan)
                common, ocr_len = common_prefix_len(_cname_t, s_c)
                if common >= PREFIX_OVERLAP_MIN and common >= PREFIX_OVERLAP_SHARE * ocr_len:
                    overrule_reason = f"prefix-overlap {common}/{ocr_len}"
            if overrule_reason:
                is_match = True
                match_type = "FUZZY"
                dlog(f"        [OVERRULE] Ultra-Long Truncation Match ({overrule_reason}) für '{clean_name}'\n")

        if is_match and match_type != "EXACT":
            reason = self._wrong_card_reason(_cname_t, s_c)
            if reason:
                dlog(f"        [VETO] '{s_c}' ist nicht '{clean_name}' ({reason}).\n")
                return False, "NONE"
        return is_match, match_type

    def _wrong_card_reason(self, name_clean: str, s_c: str) -> Optional[str]:
        """
        Gemeinsamer Schutz für Einzel-, Archetyp- und Nebenbei-Treffer: Ein ähnlicher Name ist
        trotzdem die falsche Karte, wenn
          - er Knight statt Night liest (oder umgekehrt),
          - er exakt eine ANDERE Karte aus dem Deck ist (Suche 'Clockwork Night' zeigt 'Clockwork Knight'),
          - eine andere Deck-Karte deutlich besser passt (abgeschnitten 'mitsuruginomikotoaram' ähnelt
            '...saji' zu 86 %, ist aber Aramasa in anderer Seltenheit).
        Returns: Grund, oder None wenn nichts dagegen spricht.
        """
        if s_c == name_clean:
            return None
        if knight_night_conflict(name_clean, s_c):
            return "Knight/Night-Konflikt"
        if s_c in self._deck_clean_names:
            return "andere Karte aus dem Deck"
        scores = self._deck_scores(s_c)
        own = scores[name_clean] if name_clean in scores else name_score(name_clean, s_c)[0]
        better = max(((score, other) for other, score in scores.items() if other != name_clean), default=None)
        if better and better[0] > own + BETTER_MATCH_MARGIN:
            return f"passt besser zu '{better[1]}' ({better[0]:.2f} statt {own:.2f})"
        return None

    def _deck_scores(self, s_c: str) -> Dict[str, float]:
        """name_score des gelesenen Texts für jede Deck-Karte (für denselben Text nur einmal berechnet)."""
        key = (s_c, id(self._deck_clean_names))
        if self._deck_scores_for != key:
            self._deck_scores_for = key
            self._deck_scores_cache = {name: name_score(name, s_c)[0] for name in self._deck_clean_names}
        return self._deck_scores_cache

    def _plausible_candidates(self, names, s_c: str, threshold: float) -> List[Tuple[str, float, bool]]:
        """
        _rank_candidates ohne Namen, die laut _wrong_card_reason sicher nicht passen. Geprüft wird
        nur, was die Schwelle erreicht – nur solche Kandidaten können eingefügt werden.
        """
        result = []
        for name, ratio, truncated in self._rank_candidates(names, s_c):
            reason = self._wrong_card_reason(clean_text(name), s_c) if ratio >= threshold else None
            if reason is None:
                result.append((name, ratio, truncated))
            else:
                dlog(f"        [VETO] '{s_c}' passt zu '{name}' (ratio={ratio:.3f}), aber {reason}.\n")
        return result

    def _try_fast_sync(self, sct, automator, monitor, target_x, target_y, stale_texts, clean_name, t0):
        """
        Schnellstart nach dem Tippen: Liest Slot 0 sofort mit, statt fix 0,8s zu warten.
        Akzeptiert NUR, wenn die Anzeige beweisbar die neue Suche zeigt:
          - gelesener Text ist die gesuchte Karte (Match),
          - und NICHT die alte Anzeige (stale_texts: letzte Slot-0-Karte / zuletzt gelesene Karte),
          - zweimal gleich gelesen im Abstand >= 0,1s,
          - frühestens 0,35s nach dem Tippen, danach 0,1s Beruhigung vor dem Einfügen.
        Ohne Treffer wird Slot 0 alle 0,15s neu angeklickt (der erste Klick kann das alte Raster
        getroffen haben) und wie bisher bei 0,6s ein Klick garantiert.
        Returns: (Ergebnis oder None, Zeitpunkt des letzten Klicks). Bei None übernimmt die alte Logik.
        """
        m = self.speed_mult
        min_accept = 0.35 * m
        legacy_click_at = 0.60 * m  # Hier klickt die alte Logik zum zweiten Mal
        fast_window = 0.80 * m      # Ab hier liest die alte Logik → dann übernimmt sie
        last_click = t0
        candidate, candidate_since = None, 0.0

        # Eine begonnene Kontrolle (Treffer einmal gesehen) darf nach Fensterende noch zu Ende laufen
        while (time.perf_counter() - t0 < fast_window
               or (candidate is not None and time.perf_counter() - candidate_since < 0.20 * m)):
            self._last_frame_hash = ""
            raw_ocr, s_c = self._capture_and_ocr_slot(sct, monitor)
            now = time.perf_counter()

            if s_c and s_c not in stale_texts:
                is_match, match_type = self._check_match_with_overrule(clean_name, s_c)
                if is_match:
                    if s_c == candidate and now - candidate_since >= 0.10 * m and now - t0 >= min_accept:
                        dlog(f"    [SYNC SCHNELL] Neue Suche geladen, Treffer '{s_c}' stabil "
                             f"nach {now - t0:.2f}s (statt fix 0,80s).\n")
                        time.sleep(0.10 * m)  # Beruhigung, bevor die Maus zum Einfügen startet
                        return (s_c, raw_ocr, match_type == "EXACT", match_type == "FUZZY"), last_click
                    if s_c != candidate:
                        candidate, candidate_since = s_c, now
                else:
                    candidate = None
            else:
                candidate = None

            # Neu anklicken: garantierter Klick zum alten Zeitpunkt (0,6s) und alle 0,15s,
            # solange noch kein Treffer zu sehen ist (Suche evtl. erst nach dem Klick geladen).
            need_legacy_click = now - t0 >= legacy_click_at and last_click - t0 < legacy_click_at
            if need_legacy_click or (candidate is None and now - last_click >= 0.15 * m):
                automator.iron_grip_click(target_x, target_y)
                last_click = time.perf_counter()

            time.sleep(0.05 * m)
        return None, last_click

    def _sync_and_stabilize_slot_00(self, sct, automator, monitor, target_x, target_y, last_seen_slot_00, clean_name) -> Tuple[str, str, bool, bool]:
        retries = 0
        empty_reads = 0
        stable_ocr = 0
        current_text = ""
        final_s_c, final_raw = "", ""

        is_exact_match = False
        is_fuzzy_match = False

        dlog(f"    [SYNC START] Führe Commit & Select aus (Alte Karte: '{last_seen_slot_00}')\n")

        t0 = time.perf_counter()
        # Was das Panel VOR der neuen Suche gezeigt haben kann: letzte Slot-0-Karte und
        # die zuletzt gelesene Karte (z.B. die gerade eingefügte Karte aus Slot 3).
        stale_texts = {t for t in (last_seen_slot_00, self._last_clean_ocr) if t and t != BLIND_CARD}

        automator.iron_grip_click(target_x, target_y)
        time.sleep(0.12 * self.speed_mult)

        # Schnellstart nur mit Beweis, dass die neue Suche geladen ist (siehe _try_fast_sync).
        # Nur möglich, wenn bekannt ist, was vorher angezeigt wurde. Karten, die schon im alten
        # Raster zu sehen waren, beweisen nichts: Das Panel kann sie noch aus dem alten Raster zeigen.
        last_click = t0
        if stale_texts:
            fast, last_click = self._try_fast_sync(sct, automator, monitor, target_x, target_y,
                                                   stale_texts | self._previous_grid, clean_name, t0)
            if fast:
                return fast

        # Kein Beweis → alte, sichere Logik mit denselben Zeiten wie bisher:
        # Klick frühestens bei 0,60s, danach 0,20s warten, erst dann lesen.
        m = self.speed_mult
        if last_click - t0 < 0.60 * m:
            remaining = 0.60 * m - (time.perf_counter() - t0)
            if remaining > 0:
                time.sleep(remaining)
            automator.iron_grip_click(target_x, target_y)
            time.sleep(0.20 * m)
        else:
            since_click = time.perf_counter() - last_click
            if since_click < 0.20 * m:
                time.sleep(0.20 * m - since_click)
        self._last_frame_hash = ""
        retyped = False

        while retries < 15:
            raw_ocr, s_c = self._capture_and_ocr_slot(sct, monitor)

            is_match, match_type = self._check_match_with_overrule(clean_name, s_c)

            # FAST-PATH SHORTCUT
            if is_match and match_type == "EXACT":
                dlog(f"    [SYNC FAST-PATH] Sofortiger EXACT Match auf '{s_c}'. Beende Sync vorzeitig.\n")
                return s_c, raw_ocr, True, False

            dlog(f"    [SYNC READ] Retry {retries} -> Gelesen: '{s_c if s_c else '(LEER)'}'\n")

            if not s_c:
                empty_reads += 1
                if empty_reads >= 5:
                    dlog(f"      -> BLIND-TRUST: Multi-Channel OCR fehlgeschlagen. Tesseract ist blind.\n")
                    return BLIND_CARD, "(LEER)", False, False
            else:
                empty_reads = 0

            is_valid_state = False
            if s_c:
                if s_c != last_seen_slot_00 or is_match:
                    is_valid_state = True
                    if is_match:
                        if match_type == "EXACT": is_exact_match = True
                        if match_type == "FUZZY": is_fuzzy_match = True

            if is_valid_state:
                if s_c == current_text:
                    stable_ocr += 1
                else:
                    stable_ocr = 0
                    current_text = s_c

                if stable_ocr >= 1:
                    final_s_c = s_c
                    final_raw = raw_ocr
                    dlog(f"    [SYNC ERFOLG] Slot 00 stabilisiert nach {retries} Retries.\n")
                    break

            time.sleep(0.12 * self.speed_mult)
            retries += 1

            # Zeigt das Panel nach ~1,5 s immer noch die alte Karte, ist die Suche nicht im Spiel
            # angekommen (das Raster zeigt dann noch die Ergebnisse der vorigen Suche). Einmal neu
            # eintippen statt bis zum Timeout zu warten und das alte Raster abzusuchen.
            # Vorher Slot 0 noch einmal anklicken: Auf langsamen PCs lädt die Suche evtl. erst jetzt,
            # das Panel zeigt die neue Karte aber erst nach einem Klick.
            if retries == RETYPE_AFTER_READS and not retyped and s_c in stale_texts and not is_match:
                automator.iron_grip_click(target_x, target_y)
                time.sleep(0.15 * self.speed_mult)
                self._last_frame_hash = ""
                _, s_check = self._capture_and_ocr_slot(sct, monitor)
                if s_check and s_check not in stale_texts:
                    continue  # Suche ist doch da → normal weiterlesen
                retyped = True
                dlog(f"    [SUCHE NEU] Panel zeigt nach {time.perf_counter() - t0:.1f}s noch '{s_c}' "
                     f"→ Suche kam nicht an, tippe '{clean_name}' erneut.")
                self._report_trouble("Suche kam nicht im Spiel an")
                self._type_search_term(automator, clean_name)
                time.sleep(0.60 * self.speed_mult)
                automator.iron_grip_click(target_x, target_y)
                time.sleep(0.20 * self.speed_mult)
                self._last_frame_hash = ""
                retries = 0
                continue

            if retries == 8:
                automator.iron_grip_click(target_x, target_y)
                time.sleep(0.10 * self.speed_mult)

        if not final_s_c:
            final_raw, final_s_c = self._capture_and_ocr_slot(sct, monitor)

        return final_s_c, final_raw, is_exact_match, is_fuzzy_match

    def _read_slot(self, sct, automator, monitor: dict, x: int, y: int, prev_text: str) -> Tuple[str, str]:
        """
        Slot anklicken und das Detail-Panel lesen.
        Zeigt das Panel denselben Text wie beim vorigen Slot, wird einmal nachgeklickt:
          - bleibt der Text gleich → wirklich dieselbe Karte (andere Seltenheit) oder leerer Slot,
          - ändert er sich → Master Duel hat den ersten Klick verzögert verarbeitet (Spiel hängt,
            z.B. weil der PC ausgelastet ist). Das zählt als Tempo-Problem → ggf. automatisch langsamer.
        Returns: (raw_ocr, s_c).
        """
        automator.iron_grip_click(x, y)
        time.sleep(0.040 * self.speed_mult)
        raw_ocr, s_c = self._capture_and_ocr_slot(sct, monitor)
        if s_c and s_c == prev_text:
            time.sleep(0.10 * self.speed_mult)
            automator.iron_grip_click(x, y)
            time.sleep(0.08 * self.speed_mult)
            self._last_frame_hash = ""
            raw_retry, s_retry = self._capture_and_ocr_slot(sct, monitor)
            # Nur eine wirklich andere Karte zählt als Lag, nicht dieselbe Karte mit einem
            # anders gelesenen Buchstaben ('tearlamentskashtira' vs. 'ttearlamentskashtira')
            if s_retry and SequenceMatcher(None, s_retry, s_c).ratio() < GHOST_RATIO:
                dlog(f"    [LAG] Panel zeigte noch '{s_c}' – nach erneutem Klick '{s_retry}'. "
                     f"Master Duel reagiert verzögert.")
                self._lag_events += 1
                self._report_trouble("Master Duel reagiert verzögert auf Klicks")
                raw_ocr, s_c = raw_retry, s_retry
        self._grid_seen.add(s_c)
        return raw_ocr, s_c

    def _open_cards(self, exclude=()) -> Dict[str, Tuple[str, str, int]]:
        """Noch nicht eingefügte Karten dieses Laufs: {sanitize_name: (cid, Name, Anzahl)}."""
        return {sanitize_name(c.name): (c.cid, c.name, c.amount) for c in self._open_pool
                if c.cid not in self._progress and c.cid not in exclude}

    def _take_along(self, automator, slot: int, x: int, y: int, s_c: str, raw_ocr: str,
                    target_cid: Optional[str], successfully_added: List) -> bool:
        """
        Zeigt ein Slot bei der Suche nach einer Karte eine ANDERE noch offene Deck-Karte
        (z.B. Suche 'Cyber Dragon Nova' zeigt 'Cyber Dragon Infinity'), wird sie direkt
        eingefügt. Ihre eigene Suche entfällt dann. Gleiche Regeln wie bei der Archetyp-Suche:
        strenge Schwelle, keine ähnlichen Namen, nicht mehrdeutig.
        Returns: True, wenn eine Karte eingefügt wurde.
        """
        if not s_c or s_c == BLIND_CARD:
            return False
        others = self._open_cards(exclude={target_cid})
        if not others:
            return False
        ranked = self._plausible_candidates(others, s_c, OPPO_MATCH_THRESHOLD)
        if not ranked or ranked[0][1] < OPPO_MATCH_THRESHOLD:
            return False
        key, ratio, _ = ranked[0]
        if len(ranked) > 1 and ranked[1][1] >= OPPO_MATCH_THRESHOLD and ratio - ranked[1][1] < AMBIGUITY_MARGIN:
            dlog(f"    [NEBENBEI] Slot {slot:02d} '{s_c}' passt zu mehreren Deck-Karten → nicht einfügen.")
            return False
        cid, _, amount = others[key]
        dlog(f"    [NEBENBEI] Slot {slot:02d} '{s_c}' ist '{key}' aus dem Deck ({amount}x, ratio={ratio:.3f}) "
             f"→ direkt einfügen, eigene Suche entfällt.")
        self._insert_found_card(automator, slot, x, y, amount, key, cid, raw_ocr, successfully_added)
        return True

    def _match_slot_text(self, clean_name: str, target_clean: str, s_c: str) -> Optional[str]:
        """Passt der gelesene Slot-Text zur gesuchten Karte? Returns: "EXACT", "FUZZY" oder None."""
        is_match, match_type = self._check_match_with_overrule(clean_name, s_c)
        if is_match:
            return match_type
        if s_c:
            number = number_card_overrule(target_clean, s_c)
            if number:
                dlog(f"        [OVERRULE] Number-Card Match (Nummer {number}, "
                     f"abgeschnittener Rest) für '{clean_name}'.")
                return "FUZZY"
        return None

    def _insert_found_card(self, automator, slot: int, x: int, y: int, amount: int, clean_name: str,
                           cid: Optional[str], raw_ocr: str, successfully_added: List) -> None:
        if slot > 0:
            # Slot erneut anklicken: Der Klick fürs Lesen kann vom Spiel noch verarbeitet werden
            automator.iron_grip_click(x, y)
            time.sleep(0.15 * self.speed_mult)
        stalled = self._add_card(automator, x, y, amount, clean_name, cid)
        self._pause_after_insert()
        successfully_added.append({
            "expected_clean": clean_text(clean_name),
            "expected_raw": clean_name,
            "actual_ocr": raw_ocr,
            "amount": amount,
            "is_fallback": False,
            "stall_suspect": stalled,
        })

    def _scan_scrolled_pages(self, sct, automator, clean_name: str, target_clean: str, amount: int,
                             cid: Optional[str], successfully_added: List,
                             first_page: List[str]) -> Tuple[bool, str]:
        """
        Die Ergebnisliste ist länger als das sichtbare Raster: per Mausrad weiterscrollen und die
        sichtbaren Slots erneut lesen. Eine Raste verschiebt das Raster in Master Duel um ca. ¾
        Zeile, 4 Rasten also um genau 3 Zeilen – dann liegen die Klickpositionen wieder mittig auf
        den Karten. Jede Seite wird komplett gelesen (Überlappung schadet nicht). Danach wird
        wieder nach oben gescrollt, damit die nächste Suche das Raster wie gewohnt vorfindet.
        Returns: (gefunden, gelesener Text der eingefügten Zielkarte).
        """
        max_y = 1080 * automator.scale_y * GRID_BOTTOM_LIMIT
        visible = [g for g in (self._get_slot_geometry(s, automator) for s in range(MAX_GRID_SLOTS))
                   if g[2] <= max_y]
        if not visible:
            return False, ""
        notches = max(1, int(self.config.get("SCROLL_NOTCHES", SCROLL_NOTCHES)))
        _, anchor_x, anchor_y = visible[0]
        scrolled = 0
        previous_page = first_page

        try:
            for page in range(1, MAX_SCROLL_PAGES + 1):
                dlog(f"    [SCROLL] Alle {len(visible)} sichtbaren Slots voll, '{clean_name}' nicht dabei "
                     f"→ scrolle {notches} Raste(n) weiter (Seite {page}/{MAX_SCROLL_PAGES}).")
                automator.scroll(anchor_x, anchor_y, -notches)
                scrolled += notches
                time.sleep(0.30 * self.speed_mult)
                self._last_frame_hash = ""

                page_texts = []
                repeat_streak, empty_streak = 0, 0
                prev_text = self._last_clean_ocr  # Panel-Text vor dem ersten Klick dieser Seite
                end_of_list = False
                for slot, (monitor, x, y) in enumerate(visible):
                    if slot == 0:
                        # Direkt nach dem Scrollen schluckt das Spiel oft den ersten Klick → nachklicken
                        raw_ocr, s_c = self._click_until_panel_changes(sct, automator, monitor, x, y, prev_text)
                    else:
                        raw_ocr, s_c = self._read_slot(sct, automator, monitor, x, y, prev_text)
                    page_texts.append(s_c)
                    dlog(f"[{time.strftime('%H:%M:%S')}] Seite {page} Slot {slot:02d} -> '{s_c}'")

                    empty_streak = empty_streak + 1 if not s_c else 0
                    repeat_streak = repeat_streak + 1 if (s_c and s_c == prev_text and slot > 0) else 0
                    panel_changed = s_c != prev_text
                    prev_text = s_c
                    if repeat_streak >= END_REPEAT_STREAK or empty_streak >= END_EMPTY_STREAK:
                        dlog("    [SCROLL] Ende der Ergebnisliste erreicht.")
                        end_of_list = True
                        break

                    match_type = self._match_slot_text(clean_name, target_clean, s_c)
                    if match_type:
                        self._log_row_shift(previous_page, page_texts)
                        dlog(f"    ==> MATCH ({match_type})! Klicke Slot {slot:02d} auf Seite {page}")
                        self._insert_found_card(automator, slot, x, y, amount, clean_name, cid,
                                                raw_ocr, successfully_added)
                        return True, s_c
                    if panel_changed:
                        self._take_along(automator, slot, x, y, s_c, raw_ocr, cid, successfully_added)

                self._log_row_shift(previous_page, page_texts)
                if end_of_list:
                    break
                if page_texts == previous_page:
                    dlog("    [SCROLL] Raster hat sich nicht mehr bewegt → Ende der Liste.")
                    break
                previous_page = page_texts
            return False, ""
        finally:
            if scrolled:
                self._scroll_back_to_top(sct, automator, visible[0], scrolled, first_page[0] if first_page else "")

    def _scroll_back_to_top(self, sct, automator, slot0_geometry, scrolled: int, expected_slot0: str) -> None:
        """
        Zurück nach oben scrollen (2 Rasten extra schaden nicht, oben ist Schluss) und prüfen,
        ob Slot 0 wieder die erste Karte zeigt. Sonst würde die nächste Suche anfangs Karten aus
        dem verschobenen alten Raster lesen.
        """
        monitor, x, y = slot0_geometry
        for attempt in range(2):
            automator.scroll(x, y, scrolled + 2)
            time.sleep(0.30 * self.speed_mult)
            # Der erste Klick nach dem Scrollen geht oft verloren → bis zur Reaktion nachklicken
            _, s_c = self._click_until_panel_changes(sct, automator, monitor, x, y, self._last_clean_ocr)
            if not expected_slot0 or s_c == expected_slot0:
                return
            dlog(f"    [SCROLL] Slot 0 zeigt nach dem Zurückscrollen '{s_c}' statt '{expected_slot0}' "
                 f"→ scrolle erneut nach oben.")
            self._report_trouble("Zurückscrollen nicht angekommen")

    def _click_until_panel_changes(self, sct, automator, monitor, x: int, y: int,
                                   old_text: str) -> Tuple[str, str]:
        """Klickt den Slot (alle 0,15 s erneut), bis das Panel etwas anderes als old_text zeigt (max. 0,6 s)."""
        deadline = time.perf_counter() + 0.6 * self.speed_mult
        automator.iron_grip_click(x, y)
        last_click = time.perf_counter()
        while True:
            time.sleep(0.05 * self.speed_mult)
            self._last_frame_hash = ""
            raw_ocr, s_c = self._capture_and_ocr_slot(sct, monitor)
            now = time.perf_counter()
            if s_c != old_text or now >= deadline:
                self._grid_seen.add(s_c)
                return raw_ocr, s_c
            if now - last_click >= 0.15 * self.speed_mult:
                automator.iron_grip_click(x, y)
                last_click = now

    def _log_row_shift(self, before: List[str], after: List[str]) -> None:
        rows = self._detect_row_shift(before, after)
        dlog(f"    [SCROLL] Raster um {rows} Zeile(n) verschoben." if rows else
             "    [SCROLL] Verschiebung nicht eindeutig erkennbar (evtl. SCROLL_NOTCHES anpassen).")

    @staticmethod
    def _detect_row_shift(before: List[str], after: List[str]) -> Optional[int]:
        """
        Um wie viele Zeilen ist das Raster gescrollt? Vergleicht die gelesenen Texte beider Seiten
        (nur zur Diagnose im Log). Returns: Zeilen, oder None wenn nicht eindeutig.
        """
        for rows in range(1, len(before) // 6 + 1):
            shifted = before[rows * 6:]
            pairs = [(a, b) for a, b in zip(shifted, after) if a and b]
            if len(pairs) >= 6 and sum(a == b for a, b in pairs) >= 0.7 * len(pairs):
                return rows
        return None

    def _scan_slots_for_card(
        self, sct, automator, clean_name, amount,
        last_added_ocr_clean, successfully_added, last_seen_slot_00, cid: Optional[str] = None
    ) -> Tuple[bool, str, str]:
        self._last_frame_hash = ""
        found = False
        first_slot_text = ""
        consecutive_empty = 0
        ghost_streak = 0
        repeat_streak = 0
        target_clean = clean_text(clean_name)
        max_y = 1080 * automator.scale_y * GRID_BOTTOM_LIMIT
        grid_full = False  # Alle sichtbaren Slots zeigten Karten → Liste geht evtl. weiter
        # Was das Panel vor dieser Suche zeigte. Liest Slot 0 noch das, ist die Anzeige evtl. alt
        # → dann dort nichts nebenbei einfügen (es könnte eine ganz andere Karte angeklickt sein).
        previous_text = {last_seen_slot_00, self._last_clean_ocr}
        page_texts: List[str] = []  # Gelesene Texte pro Slot (Vergleich nach dem Scrollen)

        for slot in range(MAX_GRID_SLOTS):
            monitor, target_x, target_y = self._get_slot_geometry(slot, automator)

            if target_y > max_y:
                dlog(f"    [INFO] Raster-Limit erreicht. Zeile wird ignoriert (Y:{target_y:.0f} > Max:{max_y:.0f}).")
                grid_full = True
                break

            is_exact = False
            is_fuzzy = False

            if slot == 0:
                s_c, raw_ocr, is_exact, is_fuzzy = self._sync_and_stabilize_slot_00(
                    sct, automator, monitor, target_x, target_y, last_seen_slot_00, clean_name
                )
                first_slot_text = s_c
                self._grid_seen.add(s_c)

                if s_c == BLIND_CARD:
                    self._report_trouble("Texterkennung liest nichts")
                    break

                if not s_c or (s_c == last_seen_slot_00 and not is_exact and not is_fuzzy):
                    dlog(f"    [WARNUNG] Slot 00 Timeout! Zeigt immer noch: '{s_c}'. Raster wird weiter gescannt.")
                    self._report_trouble("Slot 0 zeigt noch die alte Karte")
            else:
                raw_ocr, s_c = self._read_slot(sct, automator, monitor, target_x, target_y, page_texts[-1])

            # In-Line Ghost Detection: zeigt der Slot (fast) dieselbe Karte wie Slot 0?
            is_ghost = bool(slot > 0 and s_c and first_slot_text and first_slot_text != BLIND_CARD
                            and SequenceMatcher(None, s_c, first_slot_text).ratio() >= GHOST_RATIO)
            ghost_streak = ghost_streak + 1 if is_ghost else 0
            # Gleicher Text wie der vorige Slot (trotz Nachklick in _read_slot): Ende der Liste,
            # leere Slots zeigen weiter die letzte Karte
            repeat_streak = repeat_streak + 1 if (slot > 0 and s_c and s_c == page_texts[-1]) else 0
            consecutive_empty = consecutive_empty + 1 if not s_c else 0

            page_texts.append(s_c)
            type_str = " (Ghost von Slot 00)" if is_ghost else ""
            dlog(f"[{time.strftime('%H:%M:%S')}] Slot {slot:02d} -> '{s_c}'{type_str} | "
                 f"empty_streak={consecutive_empty} | ghost_streak={ghost_streak}")

            if ghost_streak >= END_REPEAT_STREAK:
                dlog("    [EARLY EXIT] 4x selbe Karte gelesen (Max 3 Raritäten). Raster wird abgebrochen.")
                break
            if repeat_streak >= END_REPEAT_STREAK:
                dlog(f"    [EARLY EXIT] 4x '{s_c}' in Folge → Ende der Ergebnisse.")
                break

            if consecutive_empty >= END_EMPTY_STREAK:
                dlog(f"    [EARLY EXIT] {consecutive_empty} leere Slots in Folge. Raster beendet.")
                break

            if not is_exact and not is_fuzzy:
                match_type = self._match_slot_text(clean_name, target_clean, s_c)
                is_exact = match_type == "EXACT"
                is_fuzzy = match_type == "FUZZY"

            if is_exact or is_fuzzy:
                found = True
                last_added_ocr_clean = s_c
                dlog(f"    ==> MATCH ({'EXACT' if is_exact else 'FUZZY'})! Klicke Slot {slot:02d}")
                self._insert_found_card(automator, slot, target_x, target_y, amount, clean_name, cid,
                                        raw_ocr, successfully_added)
                break

            # Andere offene Deck-Karte im Raster → gleich mitnehmen (nur wenn das Panel sicher neu ist)
            if s_c not in previous_text and self._take_along(
                    automator, slot, target_x, target_y, s_c, raw_ocr, cid, successfully_added):
                last_added_ocr_clean = s_c
            previous_text = {s_c}
        else:
            grid_full = True  # Alle MAX_GRID_SLOTS gelesen, ohne Ende der Liste

        # ── Mehr Ergebnisse als sichtbar (z.B. 'Cyber Dragon' → 30+ Cyber-Karten): weiterscrollen
        if not found and grid_full and first_slot_text != BLIND_CARD:
            found, scrolled_text = self._scan_scrolled_pages(
                sct, automator, clean_name, target_clean, amount, cid, successfully_added, page_texts)
            if found:
                last_added_ocr_clean = scrolled_text

        if (not found and first_slot_text != target_clean
                and first_slot_text in self._deck_clean_names):
            dlog(f"    [FAILSAFE BLOCK] Slot 00 zeigt '{first_slot_text}', eine andere Karte aus dem Deck. "
                 f"Kein Fallback.")
        elif not found and first_slot_text:
            found = self.validator.evaluate_fallback(
                target_clean, first_slot_text, last_added_ocr_clean, last_seen_slot_00
            )

            # ── SINGLE-RESULT OVERRULE: ghost_streak >= 3 bestätigt, dass die Suche nur EIN
            # Ergebnis hat. Teilt die OCR dann einen starken Anfang mit dem Ziel, wird die Karte
            # trotz Validator-VETO akzeptiert – auch bei kurzen Namen, wo der Overrule nicht greift.
            # Beispiel: 'stellarwindwolfrayet' vs OCR 'stellarwindwoltraw' → 14 gleiche Zeichen.
            if not found and ghost_streak >= END_REPEAT_STREAK and first_slot_text != BLIND_CARD:
                common, ocr_len = common_prefix_len(target_clean, first_slot_text)
                if (common >= SINGLE_RESULT_OVERLAP_MIN and ocr_len > 0
                        and common >= SINGLE_RESULT_OVERLAP_SHARE * ocr_len):
                    found = True
                    dlog(f"    [SINGLE-RESULT OVERRULE] Einziges Ergebnis (ghost_streak={ghost_streak}), "
                         f"Char-Overlap {common}/{ocr_len} → akzeptiere '{clean_name}'")

            if found:
                click_x, click_y = self.config["FIRST_CARD"]
                automator.iron_grip_click(click_x, click_y)
                time.sleep(0.15 * self.speed_mult)
                stalled = self._add_card(automator, click_x, click_y, amount, clean_name, cid)
                self._pause_after_insert()

                successfully_added.append({
                    "expected_clean": target_clean,
                    "expected_raw": clean_name,
                    "actual_ocr": "FORCED_FALLBACK_SLOT00" if first_slot_text != BLIND_CARD else "BLIND_TRUST_FALLBACK",
                    "amount": amount,
                    "is_fallback": True,
                    "stall_suspect": stalled,
                })

        return found, last_added_ocr_clean, first_slot_text

    def _write_final_audit(self, original_counts, id_to_name_map, successfully_added) -> Tuple[bool, List]:
        """
        Vergleicht Soll und Ist pro Kartenname und schreibt jede Karte ins Log.
        Alternativ-Artworks (andere ID, gleicher Name) werden zusammengezählt, weil sie im Spiel
        dieselbe Karte sind. Returns: (Fehler vorhanden, Karten für das Hinweis-Fenster).
        """
        popup_failed_cards = []
        has_errors = False

        dlog("\n" + "=" * 85 + "\n")
        dlog(f"{' ' * 30}ABSCHLUSS-REPORT")
        dlog("=" * 85 + "\n\n")

        required: Dict[str, List] = {}  # clean_text → [Anzeigename, Soll]
        for req_id, req_amount in original_counts.items():
            req_name = id_to_name_map.get(req_id, f"Unbekannte ID {req_id}")
            key = clean_text(sanitize_name(req_name)) if req_id in id_to_name_map else f"id:{req_id}"
            entry = required.setdefault(key, [req_name, 0])
            entry[1] += req_amount

        for key, (req_name, req_amount) in required.items():
            items = [item for item in successfully_added if item["expected_clean"] == key]
            actual_amount = sum(item["amount"] for item in items)
            is_fallback = any(item["is_fallback"] for item in items)
            is_stall_suspect = any(item.get("stall_suspect") for item in items)
            label = sanitize_name(req_name)[:40]

            if actual_amount == req_amount and is_stall_suspect:
                # Laut Bot eingefügt, aber während eines System-Rucklers → Klicks evtl. verloren
                has_errors = True
                popup_failed_cards.append(f"{req_name} (Ruckler beim Einfügen – bitte prüfen)")
                dlog(f"{'[RUCKLER]':<15} | {label:<40} | Soll: {req_amount:<2} | Ist: ?  | bitte im Deck prüfen")
            elif actual_amount == req_amount:
                status = "[FALLBACK]" if is_fallback else "[OK]"
                dlog(f"{status:<15} | {label:<40} | Soll: {req_amount:<2} | Ist: {actual_amount:<2}")
            elif actual_amount > req_amount:
                extra = actual_amount - req_amount
                has_errors = True
                popup_failed_cards.append(f"{req_name} ({extra}x zu viel – bitte entfernen)")
                dlog(f"{'[ZU VIELE]':<15} | {label:<40} | Soll: {req_amount:<2} | Ist: {actual_amount:<2} | "
                     f"Zu viel: {extra}")
            else:
                missing = req_amount - actual_amount
                has_errors = True
                popup_failed_cards.append(f"{req_name} ({missing}x fehlend)")
                dlog(f"{'[DECK-LÜCKE]':<15} | {label:<40} | Soll: {req_amount:<2} | Ist: {actual_amount:<2} | "
                     f"Fehlt: {missing} !")

        return has_errors, popup_failed_cards