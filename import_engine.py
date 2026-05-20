"""
IMPORT ENGINE (V48.20 - BUGFIX & TRUNCATION PROTECTION)
Thema: Synchronisation auf V48.20.
       Beinhaltet In-Line Ghost-Streak Erkennung (0.88 Ratio).
       Änderung: type_search_term ruft jetzt input_utils.type_card_name auf.
       FIX: Variable-Typo (is_fuzzy) korrigiert & Sync-Statemachine repariert.
       FIX: Inline Overrule für abgeschnittene, ultra-lange Namen.
"""

import re
import time
import concurrent.futures
import os
import sys
import hashlib
import ctypes
import ctypes.wintypes
from collections import Counter
from typing import Optional, Tuple, List, Dict, Callable
from difflib import SequenceMatcher
import pyautogui
import pyperclip
import mss
from PIL import Image, ImageOps

# Import für das modulare Tippen
from input_utils import type_card_name

from utils import (
    clean_text, sanitize_name, parse_clipboard, fetch_name_from_api, get_cached_ocr,
)
from card_engine import CardMatcher, GhostTracker
from window_automation import WindowAutomator
import vision_engine
from match_validator import MatchValidator


class DeckImporterCore:
    def __init__(
        self,
        config: dict,
        tesseract_cmd: str,
        status_callback: Callable,
        finish_callback: Callable,
        ocr_callback: Optional[Callable] = None,
        stats_callback: Optional[Callable] = None
    ):
        self.config = config
        self.tesseract_cmd = tesseract_cmd
        self.status_callback = status_callback
        self.finish_callback = finish_callback
        self.ocr_callback = ocr_callback
        self.stats_callback = stats_callback

        self.stats = {
            "total_cards": 0,
            "successful_cards": 0,
            "failed_cards": 0,
            "start_time": 0.0
        }
        self.validator = MatchValidator(CardMatcher())

        self._last_frame_hash = ""
        self._last_raw_ocr = ""
        self._last_clean_ocr = ""

        # Speed-Profil skaliert kritische Sleep-Werte für unterschiedliche Hardware.
        # Greift in: _sync_and_stabilize_slot_00 (Initial-Waits + Retry-Sleep),
        # _batch_scan_for_archetype (Initial-Wait + Loop-Sleep) und post-add_card.
        _profile = self.config.get("SPEED_PROFILE", "normal")
        self.speed_mult = {"fast": 0.85, "normal": 1.0, "slow": 1.5}.get(_profile, 1.0)

    def safe_api_fetch(self, cid: str, amt: int, lang: str) -> Optional[Tuple[str, str, int]]:
        for attempt in range(4):
            try:
                res = fetch_name_from_api(cid, amt, lang)
                if res:
                    raw_name, amount = res
                    return (str(cid), raw_name, amount)
            except Exception:
                pass
            time.sleep(0.5 + (attempt * 0.5))
        return None

    def _update_stats(self, total: int = None, success: int = None, failed: int = None):
        if total is not None:
            self.stats["total_cards"] = total
        if success is not None:
            self.stats["successful_cards"] = success
        if failed is not None:
            self.stats["failed_cards"] = failed

        if self.stats_callback:
            elapsed = max(time.perf_counter() - self.stats["start_time"], 0.001)
            speed = self.stats["successful_cards"] / elapsed
            self.stats_callback(
                self.stats["total_cards"],
                self.stats["successful_cards"],
                self.stats["failed_cards"],
                speed
            )

    def _update_ocr_preview(self, text: str, img: Optional[Image.Image] = None, confidence: float = 0.0):
        if self.ocr_callback:
            self.ocr_callback(text, img, confidence)

    def execute_import(self):
        self.stats["start_time"] = time.perf_counter()
        log_path = "md_debug.log"

        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"=== DEEP MODULAR DIAGNOSTIC RUN: {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
            f.write(f"=== V48.20 THE DEEP GRID CORE ===\n")

        try:
            card_ids = parse_clipboard()
            if not card_ids:
                self.status_callback("Fehler: Kein YDKE/YDK!", "red")
                self.finish_callback(success=False, has_errors=True, failed_cards=[])
                return

            original_counts = Counter(card_ids)
            lang = self.config.get("LANGUAGE", "en")
            self._update_stats(total=len(original_counts))

            cards_ready, id_to_name_map = self._resolve_card_names(original_counts, lang)

            for i in range(3, 0, -1):
                self.status_callback(f"Maus loslassen! ({i}s)", "yellow")
                time.sleep(1)

            automator = WindowAutomator(self.config)
            point = ctypes.wintypes.POINT()
            ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
            automator.last_bot_pos = (point.x, point.y)

            self._clear_existing_deck(automator)

            successfully_added = []
            last_added_ocr_clean = ""
            speed_buffer = 0.0

            # Batch-Gruppen vor dem Loop berechnen
            batch_groups = self._compute_batch_groups(cards_ready)
            cid_to_batch_prefix: Dict[str, str] = {}
            for prefix, group in batch_groups.items():
                for c, _, _ in group:
                    cid_to_batch_prefix[c] = prefix
            batch_done_cids: set = set()
            batch_triggered: set = set()

            with open(log_path, "a", encoding="utf-8") as f:
                if batch_groups:
                    f.write(f"[BATCH-PLAN] {len(batch_groups)} Archetype-Gruppen erkannt:\n")
                    for pfx, grp in batch_groups.items():
                        f.write(f"    '{pfx}' -> {len(grp)} Karten\n")
                else:
                    f.write("[BATCH-PLAN] Keine Batch-Gruppen gefunden.\n")

            with mss.MSS() as sct:
                monitor, t_x, t_y = self._get_slot_geometry(0, automator)
                automator.iron_grip_click(t_x, t_y)
                time.sleep(0.35)
                _, last_seen_slot_00 = self._capture_and_ocr_slot(sct, monitor)

                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"[INIT] Start-Sicherheits-Scan auf Slot 00 registriert: '{last_seen_slot_00}'\n")

                for i, (cid, raw_name, amount) in enumerate(cards_ready):
                    clean_name = sanitize_name(raw_name)

                    # ── BATCH: Bereits via Archetype-Scan gefunden ──
                    if cid in batch_done_cids:
                        self.status_callback(f"-> {clean_name[:15]} [BATCH✓]", "green")
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(f"\n[BATCH SKIP] '{clean_name}' wurde bereits im Batch-Scan gefunden.\n")
                        # Stats wurden bereits beim Batch-Trigger gezählt → kein doppeltes Zählen
                        continue

                    # ── BATCH: Erste Karte dieser Gruppe → Batch-Scan auslösen ──
                    prefix = cid_to_batch_prefix.get(cid)
                    if prefix and prefix not in batch_triggered:
                        batch_triggered.add(prefix)
                        group = batch_groups[prefix]
                        self.status_callback(f"[BATCH] {prefix[:12]}...", "magenta")

                        # Opportunistic: alle noch nicht verarbeiteten Karten außerhalb der Gruppe.
                        # Der Batch kann diese direkt einfügen falls sie in den Such-Ergebnissen
                        # sichtbar sind (z.B. Mirror Swordknight im Chimera-Batch).
                        current_group_cids = {c for c, _, _ in group}
                        opportunistic_cards = {
                            sanitize_name(r): (c, r, a)
                            for c, r, a in cards_ready[i + 1:]
                            if c not in batch_done_cids and c not in current_group_cids
                        }

                        found_in_batch, batch_slot0 = self._batch_scan_for_archetype(
                            sct, automator, prefix, group, log_path, successfully_added,
                            last_seen_slot_00=last_seen_slot_00,
                            opportunistic_cards=opportunistic_cards
                        )
                        batch_done_cids.update(found_in_batch)
                        # last_seen_slot_00 aktualisieren damit Folge-Einzel-Scans korrekt synchen
                        if batch_slot0:
                            last_seen_slot_00 = batch_slot0
                        self._update_stats(success=self.stats["successful_cards"] + len(found_in_batch))
                        # Karten die NICHT im Batch gefunden wurden: normal weiterverarbeiten
                        not_found_in_batch = [c for c, _, _ in group if c not in found_in_batch]
                        if not_found_in_batch:
                            with open(log_path, "a", encoding="utf-8") as f:
                                f.write(f"    [BATCH] {len(not_found_in_batch)} Karten fallen auf Einzel-Scan zurück.\n")
                        # Aktuelle Karte gefunden → überspringen
                        if cid in batch_done_cids:
                            continue
                        # Aktuelle Karte NICHT im Batch → normaler Scan folgt

                    self.status_callback(f"-> {clean_name[:15]}", "cyan")

                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"\n[SUCHE] '{clean_name}' (ID: {cid}, Erwartet: {amount}x)\n")

                    # MODULARER AUFRUF
                    self._type_search_term(automator, clean_name, speed_buffer)

                    found, last_added_ocr_clean, speed_buffer, first_slot_text = self._scan_slots_for_card(
                        sct, automator, clean_name, raw_name, amount,
                        last_added_ocr_clean, speed_buffer,
                        log_path, successfully_added, last_seen_slot_00
                    )

                    if first_slot_text and first_slot_text != "BLIND_CARD":
                        last_seen_slot_00 = first_slot_text

                    if found:
                        self._update_stats(success=self.stats["successful_cards"] + 1)
                    else:
                        self._update_stats(failed=self.stats["failed_cards"] + 1)

            has_errors, popup_failed_cards = self._write_final_audit(
                original_counts, id_to_name_map, successfully_added, log_path
            )

            self.finish_callback(success=True, has_errors=has_errors, failed_cards=popup_failed_cards)

        except Exception as e:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[CRITICAL ERROR]: {str(e)}\n")
            self.status_callback("Abbruch / Fehler", "red")
            self.finish_callback(success=False, has_errors=True, failed_cards=[])

    def _resolve_card_names(self, original_counts: Counter, lang: str) -> Tuple[List, Dict]:
        self.status_callback("API Check...", "cyan")
        cards_ready = []
        id_to_name_map = {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=15) as exe:
            future_to_id = {
                exe.submit(self.safe_api_fetch, cid, amt, lang): (cid, amt)
                for cid, amt in original_counts.items()
            }
            for future in concurrent.futures.as_completed(future_to_id):
                cid, amt = future_to_id[future]
                try:
                    res = future.result()
                    if res:
                        res_cid, raw_name, _ = res
                        cards_ready.append(res)
                        id_to_name_map[res_cid] = raw_name
                except Exception:
                    pass

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

    def _hw_key_tap(self, vk_code, duration=0.01):
        """Ultra-schneller Hardware-Tipp über Windows API"""
        ctypes.windll.user32.keybd_event(vk_code, 0, 0, 0)
        time.sleep(duration)
        ctypes.windll.user32.keybd_event(vk_code, 0, 2, 0)
        time.sleep(duration)

    def _type_search_term(self, automator: WindowAutomator, clean_name: str, speed_buffer: float):
        type_card_name(automator, clean_name, self.config, speed_buffer)

    # =====================================================================
    # ARCHETYPE-BATCH-SCAN (V49 - Speed Boost)
    # =====================================================================

    # Erste Wörter, die zu generisch sind für eine Batch-Suche
    _BATCH_NOISE = {
        'dark', 'light', 'fire', 'water', 'earth', 'wind', 'divine', 'chaos',
        'black', 'white', 'red', 'blue', 'green', 'yellow', 'number', 'numeron',
        'true', 'super', 'ultra', 'hyper', 'mega', 'neo', 'great', 'high', 'evil',
        'ancient', 'sacred', 'crystal', 'cyber', 'dragon', 'galaxy', 'star', 'solar',
        'elemental', 'armed', 'the', 'of', 'for', 'a', 'an', 'armed',
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
            words = re.findall(r'[a-zA-Z]+', raw_name.lower())
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

            all_word_lists = [re.findall(r'[a-zA-Z]+', raw.lower()) for _, raw, _ in group]
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
        self, sct, automator, prefix: str, group_cards: List,
        log_path: str, successfully_added: List,
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
        max_y = 1080 * automator.scale_y * 0.93

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n[BATCH] Archetype-Präfix '{prefix}' | {len(group_cards)} Karten\n")
            for cn in pending:
                f.write(f"    - '{cn}'\n")

        self._type_search_term(automator, prefix, 0.0)

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
            # Such-Prefix in OCR → garantiert neues Such-Ergebnis geladen
            if s0 and prefix_clean and prefix_clean in s0:
                new_slot0_text = s0
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"    [BATCH] Slot 0 geladen nach {wait_try} Retries: '{s0}'\n")
                break
            # Alle 3 Retries Slot 0 erneut klicken, damit das Detail-Panel aktualisiert wird
            if wait_try > 0 and wait_try % 3 == 0:
                automator.iron_grip_click(x0, y0)
                time.sleep(0.05 * self.speed_mult)
            time.sleep(0.125 * self.speed_mult)
        else:
            new_slot0_text = last_seen_slot_00
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"    [BATCH] WARNUNG: Such-Prefix '{prefix_clean}' nicht in Slot 0 erkannt nach 2.5s. Fahre trotzdem fort.\n")

        self._last_frame_hash = ""

        # Verfolgt OCR-Text der zuletzt eingefügten Karte.
        # Wird als last_seen_slot_00 für den nächsten Batch/Einzel-Scan zurückgegeben,
        # damit der Folge-Scan weiß, was VOR dem neuen Suchergebnis im Panel stand.
        last_added_s_c = new_slot0_text

        for slot in range(42):
            if not pending:
                break

            monitor, target_x, target_y = self._get_slot_geometry(slot, automator)
            if target_y > max_y:
                break

            automator.iron_grip_click(target_x, target_y)
            time.sleep(0.040)
            raw_ocr, s_c = self._capture_and_ocr_slot(sct, monitor)

            if not s_c:
                continue

            # ── BATCH BEST-MATCH: Karte mit höchster Ratio gewinnt ──
            # Verhindert dass zwei ähnliche Archetype-Karten sich gegenseitig
            # falsch matchen (z.B. Asophiel ↔ Kaspitell, Irene ↔ Gibrine).
            BATCH_THRESHOLD = 0.85
            # Alle Kandidaten mit Ratio + Truncation-Flag sammeln um Ambiguität zu erkennen
            candidates = []  # (key, ratio, was_truncated)

            for clean_target in pending:
                clean_t = clean_text(clean_target)
                ratio = SequenceMatcher(None, clean_t, s_c).ratio()
                was_truncated = False

                # Truncation-Korrektur: wenn Target deutlich länger als OCR
                if ratio < BATCH_THRESHOLD and len(clean_t) > len(s_c) * 1.3 and len(s_c) >= 10:
                    prefix_ratio = SequenceMatcher(None, clean_t[:len(s_c)], s_c).ratio()
                    if prefix_ratio > ratio:
                        ratio = prefix_ratio
                        was_truncated = True

                candidates.append((clean_target, ratio, was_truncated))

            candidates.sort(key=lambda c: -c[1])
            best_key, best_ratio, best_truncated = (candidates[0] if candidates else (None, 0.0, False))

            # ── AMBIGUITÄTS-CHECK: Zwei Karten die per Truncation gleich gut matchen
            # können nicht unterschieden werden (z.B. zwei Varuroon-Varianten teilen
            # 'radianttyphoonvaruroon' als Präfix). Diesen Slot überspringen, damit
            # der Einzel-Scan mit vollem Namen die richtige Variante findet.
            is_ambiguous = False
            if best_truncated and len(candidates) >= 2:
                second_ratio = candidates[1][1]
                if second_ratio >= BATCH_THRESHOLD and (best_ratio - second_ratio) < 0.05:
                    is_ambiguous = True
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"    [BATCH] Slot {slot:02d} '{s_c}' AMBIGUOUS - mehrere Karten matchen via Truncation ({best_key!r}={best_ratio:.3f} vs {candidates[1][0]!r}={second_ratio:.3f}). Überspringe → Einzel-Scan.\n")

            if best_key and best_ratio >= BATCH_THRESHOLD and not is_ambiguous:
                cid, raw_name, amount = pending[best_key]
                match_type = "EXACT" if best_ratio >= 0.99 else "FUZZY"
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"    [BATCH] Slot {slot:02d} '{s_c}' ==> MATCH '{best_key}' ({amount}x, {match_type}, ratio={best_ratio:.3f})\n")
                automator.add_card_to_deck(target_x, target_y, amount)
                time.sleep(0.35 * self.speed_mult)
                found_cids.add(cid)
                last_added_s_c = s_c  # Panel zeigt jetzt diese Karte – für nächsten Sync merken
                successfully_added.append({
                    "expected_clean": clean_text(best_key),
                    "expected_raw": best_key,
                    "actual_ocr": raw_ocr,
                    "amount": amount,
                    "is_fallback": False,
                })
                del pending[best_key]

            # ── OPPORTUNISTIC MATCH: Slot passt zu keiner Archetype-Karte,
            # aber zu einer anderen Deck-Karte die gerade sichtbar ist ──
            elif opportunistic_cards and not is_ambiguous:
                OPPO_THRESHOLD = 0.92
                oppo_best_key = None
                oppo_best_ratio = 0.0

                for oppo_name in opportunistic_cards:
                    oppo_t = clean_text(oppo_name)
                    ratio = SequenceMatcher(None, oppo_t, s_c).ratio()
                    # Truncation-Korrektur
                    if ratio < OPPO_THRESHOLD and len(oppo_t) > len(s_c) * 1.3 and len(s_c) >= 10:
                        prefix_ratio = SequenceMatcher(None, oppo_t[:len(s_c)], s_c).ratio()
                        if prefix_ratio > ratio:
                            ratio = prefix_ratio
                    if ratio > oppo_best_ratio:
                        oppo_best_ratio = ratio
                        oppo_best_key = oppo_name

                if oppo_best_key and oppo_best_ratio >= OPPO_THRESHOLD:
                    o_cid, o_raw, o_amount = opportunistic_cards[oppo_best_key]
                    o_type = "EXACT" if oppo_best_ratio >= 0.99 else "FUZZY"
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"    [BATCH-OPPO] Slot {slot:02d} '{s_c}' ==> '{oppo_best_key}' ({o_amount}x, {o_type}, ratio={oppo_best_ratio:.3f})\n")
                    automator.add_card_to_deck(target_x, target_y, o_amount)
                    time.sleep(0.35 * self.speed_mult)
                    found_cids.add(o_cid)
                    last_added_s_c = s_c
                    successfully_added.append({
                        "expected_clean": clean_text(oppo_best_key),
                        "expected_raw": oppo_best_key,
                        "actual_ocr": raw_ocr,
                        "amount": o_amount,
                        "is_fallback": False,
                    })
                    del opportunistic_cards[oppo_best_key]

        if pending:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"    [BATCH] Nicht im Raster (→ Einzel-Scan): {[r for _, r, _ in pending.values()]}\n")

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"    [BATCH] Ergebnis: {len(found_cids)}/{len(group_cards)} gefunden\n")

        # last_added_s_c statt new_slot0_text zurückgeben: zeigt was das Panel
        # NACH dem Scan zeigt (letzte eingefügte Karte), nicht was am Anfang
        # beim Warten auf Slot 0 gelesen wurde.
        return found_cids, last_added_s_c

    def _get_slot_geometry(self, slot: int, automator: WindowAutomator) -> Tuple[dict, int, int]:
        row, col = slot // 6, slot % 6
        target_x = self.config["FIRST_CARD"][0] + int(col * self.config.get("OFFSET_X", 88) * automator.scale_x)
        target_y = self.config["FIRST_CARD"][1] + int(row * self.config.get("OFFSET_Y", 125) * automator.scale_y)
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

        raw_ocr = get_cached_ocr(img_hash, lambda im: vision_engine.do_ocr(im, self.tesseract_cmd), img_processed)
        s_c = clean_text(raw_ocr)

        if not s_c:
            img_hash_raw = hashlib.md5(img_inverted.tobytes()).hexdigest() + "_raw"
            raw_ocr = get_cached_ocr(img_hash_raw, lambda im: vision_engine.do_ocr(im, self.tesseract_cmd), img_inverted)
            s_c = clean_text(raw_ocr)

        if not s_c:
            img_dark = img_inverted.point(lambda p: 0 if p < 80 else 255)
            img_hash_dark = hashlib.md5(img_dark.tobytes()).hexdigest() + "_dark"
            raw_ocr = get_cached_ocr(img_hash_dark, lambda im: vision_engine.do_ocr(im, self.tesseract_cmd), img_dark)
            s_c = clean_text(raw_ocr)
            img_dark.close()

        confidence = 0.8 if s_c and len(s_c) > 3 else 0.3
        self._update_ocr_preview(s_c if s_c else "(leer)", img_processed, confidence)

        self._last_frame_hash = img_hash
        self._last_raw_ocr = raw_ocr
        self._last_clean_ocr = s_c

        img.close()
        img_inverted.close()
        img_processed.close()
        return raw_ocr, s_c

    def _sync_and_stabilize_slot_00(self, sct, automator, monitor, target_x, target_y, last_seen_slot_00, clean_name, log_path) -> Tuple[str, str, bool, bool]:
        retries = 0
        empty_reads = 0
        stable_ocr = 0
        current_text = ""
        final_s_c, final_raw = "", ""

        is_exact_match = False
        is_fuzzy_match = False

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"    [SYNC START] Führe Commit & Select aus (Alte Karte: '{last_seen_slot_00}')\n")

        automator.iron_grip_click(target_x, target_y)
        time.sleep(0.60 * self.speed_mult)
        automator.iron_grip_click(target_x, target_y)
        time.sleep(0.20 * self.speed_mult)

        while retries < 15:
            raw_ocr, s_c = self._capture_and_ocr_slot(sct, monitor)

            is_match, match_type = self.validator.check_match(clean_name, s_c, log_path)

            # --- OVERRULE FÜR ABGESCHNITTENE ULTRA-LANGE NAMEN ---
            # clean_text() nötig: clean_name hat noch Großbuchstaben/Bindestriche/Leerzeichen,
            # s_c ist bereits lowercase ohne Sonderzeichen → Vergleich muss auf gleicher Basis sein.
            _cname_t = clean_text(clean_name)
            if not is_match and len(_cname_t) > 25 and s_c and len(s_c) >= 12:
                overrule_reason = None
                # Pfad 1: SequenceMatcher-Ratio auf Präfix-Slice
                if SequenceMatcher(None, _cname_t[:len(s_c)], s_c).ratio() >= 0.85:
                    overrule_reason = "ratio"
                else:
                    # Pfad 2: Char-für-Char-Präfix-Overlap (toleriert OCR-Korruption am Ende
                    # wie 'sinistar3b' statt 'sinistersov' bei Qixing Longyuan)
                    _sc_stripped = s_c[1:] if s_c.startswith('l') else s_c
                    _common = 0
                    for _i in range(min(len(_cname_t), len(_sc_stripped))):
                        if _cname_t[_i] == _sc_stripped[_i]:
                            _common += 1
                        else:
                            break
                    if _common >= 12 and _common >= 0.6 * len(_sc_stripped):
                        overrule_reason = f"prefix-overlap {_common}/{len(_sc_stripped)}"
                if overrule_reason:
                    is_match = True
                    match_type = "FUZZY"
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"        [OVERRULE] Ultra-Long Truncation Match ({overrule_reason}) für '{clean_name}'\n")
            # ----------------------------------------------------------

            # FAST-PATH SHORTCUT
            if is_match and match_type == "EXACT":
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"    [SYNC FAST-PATH] Sofortiger EXACT Match auf '{s_c}'. Beende Sync vorzeitig.\n")
                return s_c, raw_ocr, True, False

            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"    [SYNC READ] Retry {retries} -> Gelesen: '{s_c if s_c else '(LEER)'}'\n")

            if not s_c:
                empty_reads += 1
                if empty_reads >= 5:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"      -> BLIND-TRUST: Multi-Channel OCR fehlgeschlagen. Tesseract ist blind.\n")
                    return "BLIND_CARD", "(LEER)", False, False
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
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"    [SYNC ERFOLG] Slot 00 stabilisiert nach {retries} Retries.\n")
                    break

            time.sleep(0.12 * self.speed_mult)
            retries += 1

            if retries == 8:
                automator.iron_grip_click(target_x, target_y)
                time.sleep(0.10 * self.speed_mult)

        if not final_s_c:
            final_raw, final_s_c = self._capture_and_ocr_slot(sct, monitor)

        return final_s_c, final_raw, is_exact_match, is_fuzzy_match

    def _scan_slots_for_card(
        self, sct, automator, clean_name, raw_name, amount,
        last_added_ocr_clean, speed_buffer,
        log_path, successfully_added, last_seen_slot_00
    ) -> Tuple[bool, str, float, str]:
        self._last_frame_hash = ""
        found = False
        tracker = GhostTracker()
        first_slot_text = ""
        consecutive_empty = 0
        ghost_streak = 0
        target_clean = clean_text(clean_name)

        for slot in range(42):
            monitor, target_x, target_y = self._get_slot_geometry(slot, automator)

            max_y = 1080 * automator.scale_y * 0.93
            if target_y > max_y:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"    [INFO] Raster-Limit erreicht. Zeile wird ignoriert (Y:{target_y:.0f} > Max:{max_y:.0f}).\n")
                break

            is_exact = False
            is_fuzzy = False

            if slot == 0:
                s_c, raw_ocr, is_exact_sync, is_fuzzy_sync = self._sync_and_stabilize_slot_00(
                    sct, automator, monitor, target_x, target_y, last_seen_slot_00, clean_name, log_path
                )
                first_slot_text = s_c
                is_exact = is_exact_sync
                is_fuzzy = is_fuzzy_sync

                if s_c == "BLIND_CARD":
                    break

                if not s_c or (s_c == last_seen_slot_00 and not is_exact and not is_fuzzy):
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"    [WARNUNG] Slot 00 Timeout! Zeigt immer noch: '{s_c}'. Raster wird weiter gescannt.\n")
            else:
                automator.iron_grip_click(target_x, target_y)
                time.sleep(0.040 + (speed_buffer * 0.3))
                raw_ocr, s_c = self._capture_and_ocr_slot(sct, monitor)

            # In-Line Ghost Detection
            is_ghost = False
            if slot > 0 and s_c and first_slot_text and first_slot_text != "BLIND_CARD":
                if SequenceMatcher(None, s_c, first_slot_text).ratio() >= 0.88:
                    is_ghost = True

            if is_ghost:
                ghost_streak += 1
            else:
                ghost_streak = 0

            if not s_c:
                consecutive_empty += 1
            else:
                consecutive_empty = 0

            with open(log_path, "a", encoding="utf-8") as f:
                type_str = " (Ghost von Slot 00)" if is_ghost else ""
                f.write(f"[{time.strftime('%H:%M:%S')}] Slot {slot:02d} -> '{s_c}'{type_str} | empty_streak={consecutive_empty} | ghost_streak={ghost_streak}\n")

            if ghost_streak >= 3:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"    [EARLY EXIT] 4x selbe Karte gelesen (Max 3 Raritäten). Raster wird abgebrochen.\n")
                break

            if consecutive_empty >= 6:
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"    [EARLY EXIT] {consecutive_empty} leere Slots in Folge. Raster beendet.\n")
                break

            if not is_exact and not is_fuzzy:
                match_result, match_type = self.validator.check_match(clean_name, s_c, log_path)

                # Wenn es eine Number-Karte ist, reicht es, wenn der "Number X" Teil identisch ist.
                if not match_result and "number" in clean_name and "number" in s_c:
                    if clean_name.split(":")[0] == s_c.split(":")[0]:
                        match_result = True
                        match_type = "FUZZY"
                        with open(log_path, "a", encoding="utf-8") as f:
                            f.write(f"        [OVERRULE] Number-Card Match für '{clean_name}' erzwingt.\n")

                # target_clean ist bereits clean_text(clean_name) → gleiche Basis wie s_c
                if not match_result and len(target_clean) > 25 and s_c and len(s_c) >= 12:
                    # Pfad 1: SequenceMatcher-Ratio
                    if SequenceMatcher(None, target_clean[:len(s_c)], s_c).ratio() >= 0.85:
                        match_result = True
                        match_type = "FUZZY"
                    else:
                        # Pfad 2: Char-für-Char-Präfix-Overlap (toleriert OCR-Korruption am Ende)
                        _sc_strip = s_c[1:] if s_c.startswith('l') else s_c
                        _cmn = 0
                        for _i in range(min(len(target_clean), len(_sc_strip))):
                            if target_clean[_i] == _sc_strip[_i]:
                                _cmn += 1
                            else:
                                break
                        if _cmn >= 12 and _cmn >= 0.6 * len(_sc_strip):
                            match_result = True
                            match_type = "FUZZY"
                            with open(log_path, "a", encoding="utf-8") as f:
                                f.write(f"        [OVERRULE] Prefix-Overlap Match ({_cmn}/{len(_sc_strip)}) für '{clean_name}'\n")

                if match_result:
                    if match_type == "EXACT": is_exact = True
                    if match_type == "FUZZY": is_fuzzy = True

            if is_exact or is_fuzzy:
                found = True
                last_added_ocr_clean = s_c
                match_type_str = "EXACT" if is_exact else "FUZZY"

                origin_slot = slot
                orig_row, orig_col = origin_slot // 6, origin_slot % 6
                click_x = self.config["FIRST_CARD"][0] + int(orig_col * self.config.get("OFFSET_X", 88) * automator.scale_x)
                click_y = self.config["FIRST_CARD"][1] + int(orig_row * self.config.get("OFFSET_Y", 125) * automator.scale_y)

                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"    ==> MATCH ({match_type_str})! Klicke Slot {origin_slot:02d}\n")

                if slot > 0:
                    automator.iron_grip_click(click_x, click_y)
                    time.sleep(0.15 * self.speed_mult)

                automator.add_card_to_deck(click_x, click_y, amount)
                time.sleep(0.350 * self.speed_mult)

                successfully_added.append({
                    "expected_clean": target_clean,
                    "expected_raw": clean_name,
                    "actual_ocr": raw_ocr,
                    "amount": amount,
                    "is_fallback": False
                })

                if slot == 0 and consecutive_empty == 0:
                    speed_buffer = max(0.0, speed_buffer - 0.005)
                break

        if not found and first_slot_text:
            found = self.validator.evaluate_fallback(
                target_clean, first_slot_text, last_added_ocr_clean, last_seen_slot_00, log_path
            )

            # ── SINGLE-RESULT OVERRULE: Wenn ghost_streak >= 3 (= einziges Such-Ergebnis
            # bestätigt durch 4× selbe Karte) UND OCR teilt einen starken Char-Präfix mit
            # dem Target, akzeptiere die Karte trotz Validator-VETO.
            # Greift auch für kurze Namen (≤ 25 chars) wo der OVERRULE nicht zieht.
            # Beispiel: 'stellarwindwolfrayet' (20 chars) vs OCR 'stellarwindwoltraw' →
            # 14 chars sauberer Präfix-Overlap → akzeptiert.
            if not found and ghost_streak >= 3 and first_slot_text != "BLIND_CARD":
                _sc_strip = first_slot_text[1:] if first_slot_text.startswith('l') else first_slot_text
                _cmn = 0
                for _i in range(min(len(target_clean), len(_sc_strip))):
                    if target_clean[_i] == _sc_strip[_i]:
                        _cmn += 1
                    else:
                        break
                if _cmn >= 10 and len(_sc_strip) > 0 and _cmn >= 0.5 * len(_sc_strip):
                    found = True
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"    [SINGLE-RESULT OVERRULE] Einziges Ergebnis (ghost_streak={ghost_streak}), Char-Overlap {_cmn}/{len(_sc_strip)} → akzeptiere '{clean_name}'\n")

            if found:
                click_x = self.config["FIRST_CARD"][0]
                click_y = self.config["FIRST_CARD"][1]
                automator.iron_grip_click(click_x, click_y)
                time.sleep(0.15 * self.speed_mult)
                automator.add_card_to_deck(click_x, click_y, amount)
                time.sleep(0.350 * self.speed_mult)

                successfully_added.append({
                    "expected_clean": target_clean,
                    "expected_raw": clean_name,
                    "actual_ocr": "FORCED_FALLBACK_SLOT00" if first_slot_text != "BLIND_CARD" else "BLIND_TRUST_FALLBACK",
                    "amount": amount,
                    "is_fallback": True
                })

        return found, last_added_ocr_clean, speed_buffer, first_slot_text

    def _write_final_audit(self, original_counts, id_to_name_map, successfully_added, log_path) -> Tuple[bool, List]:
        popup_failed_cards = []
        has_errors = False

        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\n" + "=" * 85 + "\n")
            f.write(f"{' ' * 20}ULTRA-DIAGNOSE MATRIX (V48.20)\n")
            f.write("=" * 85 + "\n\n")

            for req_id, req_amount in original_counts.items():
                req_name = id_to_name_map.get(req_id, f"Unbekannte ID {req_id}")
                req_clean = sanitize_name(req_name)

                actual_amount = sum(
                    item["amount"] for item in successfully_added
                    if item["expected_clean"] == clean_text(req_clean)
                )
                is_fallback = any(
                    item["expected_clean"] == clean_text(req_clean) and item["is_fallback"]
                    for item in successfully_added
                )

                missing_amount = max(0, req_amount - actual_amount)

                if actual_amount == req_amount:
                    status = "[FALLBACK]" if is_fallback else "[OK]"
                    missing_str = "0"
                else:
                    status = "[DECK-LÜCKE]"
                    has_errors = True
                    missing_str = f"{missing_amount} !"
                    if req_name not in popup_failed_cards:
                        popup_failed_cards.append(f"{req_name} ({missing_amount}x fehlend)")

                    f.write(f"{status:<15} | {req_clean[:40]:<40} | Soll: {req_amount:<2} | Ist: {actual_amount:<2} | Fehlt: {missing_str:<2}\n")

        return has_errors, popup_failed_cards