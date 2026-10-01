"""
MATCH VALIDATOR
Entscheidet, ob ein per Texterkennung gelesener Kartenname zur gesuchten Karte passt.
  - Dynamisches Längen-Delta gegen Suffix-Hijacking (Basis-Karte vs. Boss-Monster, z.B. Purrely).
  - Core-Bruch-Filter gegen Infix-Hijacking.
  - Veto gegen Verwechslung von "Knight" und "Night".
  - Prefix-Schild im Scramble-Filter gegen Überlappungs-Fallen (Temple vs Treasures).
  - Erkennung abgeschnittener Scans (Siegfried).
  - Suffix-Schild mit 50%-Hürde (Kewl Tune Cue vs. Synchro), lange Suffixe bleiben geschützt (Clovis).
"""

from difflib import SequenceMatcher
from collections import Counter
from utils import clean_text
from debug_log import dlog

CROSS_CARD_THRESHOLD = 0.35


def _night_signature(text: str) -> list:
    """Für jedes 'night' im Text: wie viele 'k' direkt davor stehen."""
    signature = []
    pos = text.find("night")
    while pos != -1:
        k_count = 0
        while pos - k_count > 0 and text[pos - k_count - 1] == "k":
            k_count += 1
        signature.append(k_count)
        pos = text.find("night", pos + 1)
    return signature


def knight_night_conflict(target: str, ocr: str) -> bool:
    """
    True, wenn Ziel und OCR sich bei Knight/Night unterscheiden. Zählt die 'k' vor jedem
    'night', weil ohne Leerzeichen auch 'Clockwork Night' zu 'clockworknight' wird und damit
    'knight' enthält – Clockwork Knight ist dagegen 'clockworkknight' (zwei 'k').
    """
    target_sig, ocr_sig = _night_signature(target), _night_signature(ocr)
    return any(t != o for t, o in zip(target_sig, ocr_sig))


class MatchValidator:
    def __init__(self, card_matcher):
        self.matcher = card_matcher

    def check_match(self, clean_name: str, s_c: str) -> tuple[bool, str]:
        target = clean_text(clean_name)
        ocr = s_c

        dlog(f"        [AUDIT] Matcher prüft Target: '{target}' vs OCR: '{ocr}'\n")

        if not target or not ocr:
            dlog(f"        [AUDIT] -> NONE (Leer)\n")
            return False, "NONE"

        if target == ocr:
            dlog(f"        [AUDIT] -> EXACT (100% Identisch)\n")
            return True, "EXACT"

        # --- INTELLIGENTES HARD VETO: KNIGHT / NIGHT ---
        if knight_night_conflict(target, ocr):
            dlog(f"        [AUDIT] VETO: Kritischer Knight/Night-Konflikt erkannt! Match verweigert.\n")
            return False, "NONE"

        # --- BASE-CARD HIJACKING VETO (Der Exorzist) ---
        if target in ocr and len(ocr) > len(target):
            max_allowed_delta = max(2, int(len(target) * 0.25))
            if (len(ocr) - len(target)) > max_allowed_delta:
                dlog(f"        [AUDIT] VETO: Base-Card Hijacking! '{target}' in '{ocr}' gefunden, aber OCR ist zu lang (Delta {len(ocr)-len(target)} > {max_allowed_delta}).\n")
                return False, "NONE"

        # --- TRUNCATION-ERKENNUNG: Abgeschnittene ultra-lange Namen ---
        if len(target) > len(ocr) * 1.5 and len(ocr) >= 10:
            prefix_ratio = SequenceMatcher(None, target[:len(ocr)], ocr).ratio()
            dlog(f"        [AUDIT] Truncation-Check: target[:{len(ocr)}]='{target[:len(ocr)]}' vs ocr='{ocr}' ratio={prefix_ratio:.3f}\n")
            if prefix_ratio >= 0.85:
                dlog(f"        [AUDIT] -> FUZZY (TRUNCATED_PREFIX, ratio={prefix_ratio:.3f})\n")
                return True, "FUZZY"

        # --- DYNAMISCHER STAMM-SCHILD MIT 50% HÜRDE & ABSOLUTER FADE-OUT GRENZE ---
        match = SequenceMatcher(None, target, ocr).find_longest_match(0, len(target), 0, len(ocr))
        if match.size >= 6:
            target_suffix = target[match.a + match.size:]
            ocr_suffix = ocr[match.b + match.size:]

            # Ein OCR-Suffix ist ein gefährlicher Hijack, wenn es 4+ Zeichen lang ist,
            # ODER wenn das gesuchte Original-Suffix selbst extrem kurz ist (<= 3).
            is_hijack_danger = len(ocr_suffix) >= 4 or (len(target_suffix) <= 3 and len(ocr_suffix) >= 3)

            if len(target_suffix) >= 2 and is_hijack_danger and len(ocr_suffix) >= (len(target_suffix) * 0.4):
                suf_ratio = SequenceMatcher(None, target_suffix, ocr_suffix).ratio()
                if suf_ratio < 0.50:
                    dlog(f"        [AUDIT] VETO: Archetypen-Hijack! Suffix '{target_suffix}' vs '{ocr_suffix}' zu unterschiedlich (Ratio {suf_ratio:.2f}).\n")
                    return False, "NONE"

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

        # DYNAMISCHES SUBSTRING-DELTA (Das Skalpell)
        if not is_fuzzy and len(target) >= 6 and target in ocr:
            max_delta = max(2, int(len(target) * 0.25))
            current_delta = abs(len(ocr) - len(target))

            if current_delta <= max_delta:
                if len(ocr) > len(target) + 2:
                    if target[:3] in ocr[:5]:
                        is_fuzzy = True
                        reason = f"Substring-Match + Prefix-Lock (Delta {current_delta} <= {max_delta})"
                else:
                    is_fuzzy = True
                    reason = f"Substring-Match (Target vollständig im OCR, Delta {current_delta} <= {max_delta})"

        if not is_fuzzy:
            sm = SequenceMatcher(None, target, ocr)
            ratio = sm.ratio()
            is_match = self.matcher.is_exact_match(clean_name, s_c)

            dlog(f"        [AUDIT] Ratio berechnet: {ratio:.3f}\n")

            # DER CORE-BRUCH-FILTER (Anatomische Infix-Lösung)
            has_core_break = False
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag in ('insert', 'replace'):
                    if i1 > 0 and i2 < len(target):
                        foreign_chars = j2 - j1
                        missing_chars = i2 - i1
                        if foreign_chars >= 3 and foreign_chars >= missing_chars + 2:
                            has_core_break = True
                            break

            if has_core_break:
                dlog(f"        [AUDIT] VETO: Infix-Hijacking erkannt (Core-Bruch). Blockiere Ratio-Check.\n")
            else:
                if is_match:
                    is_fuzzy = True
                    reason = "Custom Archetype Match"
                elif ratio >= 0.90:
                    is_fuzzy = True
                    reason = "High-Ratio >= 0.90"
                elif len(target) <= 12 and ratio >= 0.85:
                    is_fuzzy = True
                    reason = "Mid-Ratio >= 0.85 (Name <= 12)"
                elif len(target) > 12 and ratio >= 0.80 and target[:3] in ocr[:5]:
                    is_fuzzy = True
                    reason = "Mid-Ratio >= 0.80 mit Prefix-Lock (Name > 12)"
                elif len(target) >= 15 and ratio >= 0.68 and (target[:6] in ocr or ocr[:6] in target):
                    is_fuzzy = True
                    reason = "Low-Ratio >= 0.68 mit Prefix-Match (Name >= 15)"

            # Verschärfter OCR Scramble-Filter mit PREFIX-SCHILD
            if not is_fuzzy and not has_core_break and len(target) >= 10:
                delta = abs(len(ocr) - len(target))
                if delta <= 3 and ratio >= 0.50:
                    if target[:3] in ocr[:5]:
                        target_ctr = Counter(target)
                        ocr_ctr = Counter(ocr)
                        common_chars = sum((target_ctr & ocr_ctr).values())
                        overlap = common_chars / len(target)

                        if overlap >= 0.80:
                            is_fuzzy = True
                            reason = f"OCR Scramble (Char-Overlap {overlap:.0%}, Ratio {ratio:.2f}, Delta {delta})"

        if is_fuzzy:
            dlog(f"        [AUDIT] -> FUZZY ({reason})\n")
            return True, "FUZZY"

        dlog(f"        [AUDIT] -> ABGELEHNT\n")
        return False, "NONE"

    def evaluate_fallback(
        self, target_clean: str, first_slot_text: str,
        last_added_ocr_clean: str, last_seen_slot_00: str
    ) -> bool:

        if first_slot_text == "BLIND_CARD":
            dlog(f"    [FALLBACK ACTIVE] Blind-Card erkannt. Master Duel Suche wird vertraut. Erzwinge Klick.\n")
            return True

        if knight_night_conflict(target_clean, first_slot_text):
            dlog(f"    [FAILSAFE BLOCK] Fallback blockiert wegen kritischem Knight/Night-Konflikt.\n")
            return False

        # --- DYNAMISCHER STAMM-SCHILD IM FALLBACK ---
        match_fb = SequenceMatcher(None, target_clean, first_slot_text).find_longest_match(0, len(target_clean), 0, len(first_slot_text))
        if match_fb.size >= 6:
            t_suf_fb = target_clean[match_fb.a + match_fb.size:]
            o_suf_fb = first_slot_text[match_fb.b + match_fb.size:]

            is_hijack_danger_fb = len(o_suf_fb) >= 4 or (len(t_suf_fb) <= 3 and len(o_suf_fb) >= 3)

            if len(t_suf_fb) >= 2 and is_hijack_danger_fb and len(o_suf_fb) >= (len(t_suf_fb) * 0.4):
                suf_ratio_fb = SequenceMatcher(None, t_suf_fb, o_suf_fb).ratio()
                if suf_ratio_fb < 0.50:
                    dlog(f"    [FAILSAFE BLOCK] Fallback blockiert wegen Archetypen-Hijack (Suffix '{t_suf_fb}' vs '{o_suf_fb}', Ratio {suf_ratio_fb:.2f}).\n")
                    return False

        sm_fallback = SequenceMatcher(None, target_clean, first_slot_text)
        similarity = sm_fallback.ratio()
        delta = abs(len(first_slot_text) - len(target_clean))
        is_cross_card = similarity < CROSS_CARD_THRESHOLD

        if target_clean in first_slot_text or first_slot_text in target_clean:
            is_cross_card = False

        edge_cases = ["murakumo", "habakiri", "ritual", "prayers", "mirror"]
        for ec in edge_cases:
            if ec in first_slot_text and ec not in target_clean:
                is_cross_card = True

        dlog(f"    [FALLBACK-CHECK] sim={similarity:.3f} delta={delta} cross={is_cross_card} "
            f"last_added='{last_added_ocr_clean}' slot00='{first_slot_text}'\n")

        is_stale = False
        if last_added_ocr_clean and first_slot_text == last_added_ocr_clean:
            is_stale = True
        if last_seen_slot_00 and first_slot_text == last_seen_slot_00:
            is_stale = True

        if is_stale:
            dlog("    [FAILSAFE BLOCK] Fallback blockiert! Slot 00 zeigt noch alte Karte.\n")
            return False
        elif is_cross_card:
            dlog(f"    [FAILSAFE BLOCK] Fremdkarte erkannt (sim={similarity:.3f}), Fallback verweigert.\n")
            return False

        max_delta = max(2, int(len(target_clean) * 0.25))
        if target_clean in first_slot_text and delta > max_delta:
            dlog(f"    [FAILSAFE BLOCK] Fallback blockiert wegen Suffix-Hijacking (Delta {delta} > Limit {max_delta}).\n")
            return False

        for tag, i1, i2, j1, j2 in sm_fallback.get_opcodes():
            if tag in ('insert', 'replace') and i1 > 0 and i2 < len(target_clean):
                if (j2 - j1) >= 3 and (j2 - j1) >= (i2 - i1) + 2:
                    dlog(f"    [FAILSAFE BLOCK] Fallback blockiert wegen Infix-Hijacking (Core-Bruch).\n")
                    return False

        if similarity >= 0.65:
            if len(first_slot_text) > len(target_clean) + 2 and target_clean[:3] not in first_slot_text[:5]:
                 dlog(f"    [FAILSAFE BLOCK] Fallback blockiert wegen Prefix-Mismatch (sim={similarity:.3f}).\n")
                 return False

            dlog(f"    [FALLBACK ACTIVE] Slot 00 valid (sim={similarity:.3f} >= 0.65). Erzwinge Klick.\n")
            return True

        elif similarity >= 0.50 and delta <= 3:
            if target_clean[:3] in first_slot_text[:5]:
                target_ctr = Counter(target_clean)
                ocr_ctr = Counter(first_slot_text)
                common_chars = sum((target_ctr & ocr_ctr).values())
                overlap = common_chars / len(target_clean)

                if overlap >= 0.80:
                    dlog(f"    [FALLBACK ACTIVE] Slot 00 OCR Scramble valid (sim={similarity:.3f}, delta={delta}, overlap={overlap:.2f}). Erzwinge Klick.\n")
                    return True
                else:
                    dlog(f"    [FAILSAFE BLOCK] Fallback Scramble blockiert wegen zu geringem Char-Overlap (overlap={overlap:.2f} < 0.80).\n")
                    return False
            else:
                dlog(f"    [FAILSAFE BLOCK] Fallback Scramble blockiert wegen Prefix-Mismatch (Schild aktiv).\n")
                return False
        else:
            dlog(f"    [FAILSAFE BLOCK] sim={similarity:.3f} zu niedrig / Längen-Delta zu groß für Fallback.\n")
            return False