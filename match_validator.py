"""
MATCH VALIDATOR (V41.00)
Thema: Ausgelagerte Gehirn-Logik der Import Engine.
       Neuer "OCR Scramble" Filter (Character-Overlap) rettet verdrehte Texte wie Motorbike.
       Intelligenter Fallback mit Längen-Delta-Discriminator.
"""

import os
from difflib import SequenceMatcher
from collections import Counter
from utils import clean_text

CROSS_CARD_THRESHOLD = 0.35

class MatchValidator:
    def __init__(self, card_matcher):
        self.matcher = card_matcher

    def check_match(self, clean_name: str, s_c: str, log_path: str) -> tuple[bool, str]:
        target = clean_text(clean_name)
        ocr = s_c

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"        [AUDIT] Matcher prüft Target: '{target}' vs OCR: '{ocr}'\n")

        if not target or not ocr:
            with open(log_path, "a", encoding="utf-8") as f: f.write(f"        [AUDIT] -> NONE (Leer)\n")
            return False, "NONE"

        if target == ocr:
            with open(log_path, "a", encoding="utf-8") as f: f.write(f"        [AUDIT] -> EXACT (100% Identisch)\n")
            return True, "EXACT"

        is_fuzzy = False
        reason = ""

        if len(target) <= 6:
            match_chars = 0
            ocr_idx = 0
            for char in target:
                found_idx = ocr.find(char, ocr_idx)
                if found_idx != -1:
                    match_chars += 1
                    ocr_idx = found_idx + 1
            if match_chars == len(target):
                is_fuzzy = True
                reason = "Inklusionsfilter für kurze Namen (<6)"

        if not is_fuzzy and len(target) >= 6 and target in ocr and abs(len(ocr) - len(target)) <= 5:
            is_fuzzy = True
            reason = f"Substring-Match (Target vollständig im OCR, Längen-Delta <= 5)"

        if not is_fuzzy:
            ratio = SequenceMatcher(None, target, ocr).ratio()
            is_match = self.matcher.is_exact_match(clean_name, s_c)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"        [AUDIT] Ratio berechnet: {ratio:.3f}\n")

            if is_match or ratio >= 0.70:
                if ratio >= 0.92:
                    is_fuzzy = True
                    reason = "High-Ratio >= 0.92"
                elif len(target) <= 12 and ratio >= 0.85:
                    is_fuzzy = True
                    reason = "Mid-Ratio >= 0.85 (Name <= 12)"
                elif len(target) > 12 and ratio >= 0.82:
                    is_fuzzy = True
                    reason = "Mid-Ratio >= 0.82 (Name > 12)"
                elif len(target) >= 15 and ratio >= 0.72 and (target[:6] in ocr or ocr[:6] in target):
                    is_fuzzy = True
                    reason = "Low-Ratio >= 0.72 mit Prefix-Match (Name >= 15)"

            # --- UPGRADE: DER OCR SCRAMBLE-FILTER (Fixt Motorbike) ---
            if not is_fuzzy and len(target) >= 10:
                delta = abs(len(ocr) - len(target))
                if delta <= 5 and ratio >= 0.50:
                    # Berechne die Schnittmenge der genutzten Buchstaben
                    target_ctr = Counter(target)
                    ocr_ctr = Counter(ocr)
                    common_chars = sum((target_ctr & ocr_ctr).values())
                    overlap = common_chars / len(target)

                    if overlap >= 0.80:
                        is_fuzzy = True
                        reason = f"OCR Scramble (Char-Overlap {overlap:.0%}, Ratio {ratio:.2f}, Delta {delta})"

        if is_fuzzy:
            with open(log_path, "a", encoding="utf-8") as f: f.write(f"        [AUDIT] -> FUZZY ({reason})\n")
            return True, "FUZZY"

        with open(log_path, "a", encoding="utf-8") as f: f.write(f"        [AUDIT] -> ABGELEHNT\n")
        return False, "NONE"

    def evaluate_fallback(
        self, target_clean: str, first_slot_text: str,
        last_added_ocr_clean: str, last_seen_slot_00: str, log_path: str
    ) -> bool:

        if first_slot_text == "BLIND_CARD":
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"    [FALLBACK ACTIVE] Blind-Card erkannt. Master Duel Suche wird vertraut. Erzwinge Klick.\n")
            return True

        similarity = SequenceMatcher(None, target_clean, first_slot_text).ratio()
        delta = abs(len(first_slot_text) - len(target_clean))
        is_cross_card = similarity < CROSS_CARD_THRESHOLD

        if target_clean in first_slot_text or first_slot_text in target_clean:
            is_cross_card = False

        edge_cases = ["murakumo", "habakiri", "ritual", "prayers", "mirror"]
        for ec in edge_cases:
            if ec in first_slot_text and ec not in target_clean:
                is_cross_card = True

        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"    [FALLBACK-CHECK] sim={similarity:.3f} delta={delta} cross={is_cross_card} "
                   f"last_added='{last_added_ocr_clean}' slot00='{first_slot_text}'\n")

        is_stale = False
        if last_added_ocr_clean and first_slot_text == last_added_ocr_clean:
            is_stale = True
        if last_seen_slot_00 and first_slot_text == last_seen_slot_00:
            is_stale = True

        if is_stale:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("    [FAILSAFE BLOCK] Fallback blockiert! Slot 00 zeigt noch alte Karte.\n")
            return False
        elif is_cross_card:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"    [FAILSAFE BLOCK] Fremdkarte erkannt (sim={similarity:.3f}), Fallback verweigert.\n")
            return False
        elif similarity >= 0.65:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"    [FALLBACK ACTIVE] Slot 00 valid (sim={similarity:.3f} >= 0.65). Erzwinge Klick.\n")
            return True
        # --- UPGRADE: DUAL-LAYER FALLBACK FÜR SCRAMBLES ---
        elif similarity >= 0.50 and delta <= 6:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"    [FALLBACK ACTIVE] Slot 00 OCR Scramble valid (sim={similarity:.3f}, delta={delta}). Erzwinge Klick.\n")
            return True
        else:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"    [FAILSAFE BLOCK] sim={similarity:.3f} zu niedrig / Längen-Delta zu groß für Fallback.\n")
            return False