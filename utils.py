import re
import difflib
import pyperclip
import requests
import base64
import struct
import json
import os
import requests

# --- NEU: CACHE LOGIK FÜR API ---
CACHE_FILE = "md_card_cache.json"
_card_cache = None


def _load_cache():
    global _card_cache
    if _card_cache is None:
        if os.path.exists(CACHE_FILE):
            try:
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    _card_cache = json.load(f)
            except Exception:
                _card_cache = {}
        else:
            _card_cache = {}
    return _card_cache


def _save_cache():
    if _card_cache is not None:
        try:
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(_card_cache, f, indent=4)
        except Exception:
            pass


def fetch_name_from_api(cid: str, amount: int, lang: str = "en"):
    cache = _load_cache()
    cid_str = str(cid)

    # 1. Cache-Check (Das fotografische Gedächtnis)
    if cid_str in cache:
        return cache[cid_str], amount

    # 2. Normaler API-Call, falls nicht im Cache
    try:
        url = f"https://db.ygoprodeck.com/api/v7/cardinfo.php?id={cid}"
        # YGOPRODeck nutzt für Englisch den Standard-Link ohne Sprach-Parameter
        if lang != "en":
            url += f"&language={lang}"

        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            name = data["data"][0]["name"]

            # 3. Direkt im Cache abspeichern für die Zukunft!
            cache[cid_str] = name
            _save_cache()

            return name, amount
    except Exception:
        pass

    return None

# =====================================================================
# 1. MODULARE DIAGNOSE- & VERGLEICHS-KLASSEN (V22.4 - BULLETPROOF)
# =====================================================================

class CardMatcher:
    @staticmethod
    def clean_tokens(text):
        words = [w.lower() for w in re.findall(r'[a-zA-Z0-9]+', text) if len(w) >= 1]
        noise = {'res', 'fe', 'ge', 'te', 'co', 'id', 'dark', 'light', 'earth', 'water', 'fire', 'wind'}
        return [w for w in words if w not in noise and len(w) >= 2 or w in ['c']]

    @staticmethod
    def normalize_luna(words):
        """Vereint 'luna' und 'light' zu einem Token, um Struktur-Gleichheit zu garantieren."""
        res = []
        i = 0
        while i < len(words):
            if i < len(words) - 1 and words[i] == 'luna' and words[i + 1] == 'light':
                res.append('lunalight')
                i += 2
            else:
                res.append(words[i])
                i += 1
        return res

    @classmethod
    def is_exact_match(cls, target_raw, scan_raw):
        t_words = cls.normalize_luna(cls.clean_tokens(target_raw))
        s_words = cls.normalize_luna(cls.clean_tokens(scan_raw))

        if not t_words or not s_words:
            return False

        # --- HISTORISCHER FIX 1: ARCHETYPEN-EXKLUSIVITÄT (Sky Striker) ---
        striker_aces = {'roze', 'zero', 'raye', 'zeke', 'azalea', 'camellia', 'shizuku', 'kagari', 'hayate'}
        active_ace = next((w for w in t_words if w in striker_aces), None)
        if active_ace:
            if not any(difflib.SequenceMatcher(None, active_ace, sw).ratio() > 0.85 for sw in s_words):
                return False

        # --- HISTORISCHER FIX 2: MUTATIONS-SPERRE (Anti Leo -> Liger Mutation) ---
        if 'leo' in t_words and 'liger' in s_words: return False
        if 'liger' in t_words and 'leo' in s_words: return False

        # --- HISTORISCHER FIX 3: SUB-STRING-PRÄFIX-AKZEPTANZ (Conduction Warrior) ---
        if len(t_words) >= 4 and len(s_words) >= 3:
            prefix_match = True
            for i in range(3):
                if i < len(s_words) and i < len(t_words):
                    if difflib.SequenceMatcher(None, t_words[i], s_words[i]).ratio() < 0.75:
                        prefix_match = False
            if prefix_match:
                return True

        # Wort-Abdeckungs-Analyse
        matched_target_count = 0
        clean_s_words = [sw for sw in s_words if len(sw) >= 3 or sw in t_words]

        for tw in t_words:
            if any(difflib.SequenceMatcher(None, tw, sw).ratio() > 0.72 or tw in sw or sw in tw for sw in
                   clean_s_words):
                matched_target_count += 1

        target_coverage = matched_target_count / len(t_words)

        # --- HISTORISCHER FIX 4: STRANGER-WORD-SCHILD (Anti Perfume -> Perfume Dancer) ---
        stranger_words = []
        for sw in s_words:
            has_match = False
            for tw in t_words:
                if difflib.SequenceMatcher(None, tw, sw).ratio() > 0.70 or sw in tw or tw in sw:
                    has_match = True
                    break
            if not has_match and len(sw) >= 3 and sw.isalpha():
                stranger_words.append(sw)

        if stranger_words:
            for sw in stranger_words:
                if sw in ['dancer', 'liger', 'roze', 'raye', 'zero', 'shizuku', 'kagari', 'hayate', 'exterio',
                          'barkion']:
                    return False
                if len(sw) >= 4 and sw not in target_raw.lower():
                    return False

        # --- OPTIMIERTER FIX 5: TESSERACT-MÜLL-TOLERANZ (Rettet Naturia Beast vor 'oe i edi') ---
        if len(t_words) >= 2 and len(s_words) >= 2:
            if t_words[0] == s_words[0]:
                actual_blocking_strangers = [sw for sw in stranger_words if
                                             len(sw) >= 4 or sw in ['exterio', 'barkion', 'liger', 'dancer']]
                remaining_are_garbage = all(len(sw) <= 3 for sw in s_words[1:])
                if remaining_are_garbage and not actual_blocking_strangers:
                    return True

        # --- HISTORISCHER FIX 6: FLEXIBLE ABDECKUNGSRATE ---
        required_coverage = 1.0 if len(t_words) <= 2 else 0.70
        return target_coverage >= required_coverage


class GhostTracker:
    def __init__(self):
        self.current_origin_slot = 0
        self.last_slot_text = ""

    def track_slot(self, slot, current_scan_clean):
        if slot == 0:
            self.current_origin_slot = 0
            self.last_slot_text = current_scan_clean
            return False

        if not current_scan_clean or not self.last_slot_text:
            self.current_origin_slot = slot
            self.last_slot_text = current_scan_clean
            return False

        # === HISTORISCHER FIX A: COMPRESSED MUTATION FIREWALL ===
        if ("leo" in current_scan_clean and "liger" in self.last_slot_text) or \
                ("liger" in current_scan_clean and "leo" in self.last_slot_text):
            self.current_origin_slot = slot
            self.last_slot_text = current_scan_clean
            return False

        # === HISTORISCHER FIX B: PERFUME SUB-CARD SHIELD ===
        if ("perfume" in current_scan_clean and "dancer" in current_scan_clean) != \
                ("perfume" in self.last_slot_text and "dancer" in self.last_slot_text):
            self.current_origin_slot = slot
            self.last_slot_text = current_scan_clean
            return False

        sm = difflib.SequenceMatcher(None, self.last_slot_text, current_scan_clean)
        ratio = sm.ratio()

        is_ghost = False
        if ratio > 0.95 or current_scan_clean == self.last_slot_text:
            is_ghost = True
        else:
            # === GEHÄRTETES FIX C: MARQUEE SCROLL SHIFT DETECTION (Anti Beta vs Berserkion) ===
            match = sm.find_longest_match(0, len(self.last_slot_text), 0, len(current_scan_clean))
            # match.b <= 1 stellt sicher, dass das Fragment am linken Rand klebt (echter Left-Shift des Scrolltextes)
            if match.size >= 10 and match.b < match.a and match.b <= 1:
                is_ghost = True

        if is_ghost:
            self.last_slot_text = current_scan_clean
            return True
        else:
            self.current_origin_slot = slot
            self.last_slot_text = current_scan_clean
            return False

    def get_origin(self):
        return self.current_origin_slot

# =====================================================================
# 2. STANDALONE HILFSFUNKTIONEN FÜR DIE OVERLAY-SCHNITTSTELLE
# =====================================================================

def clean_text(text):
    if not text:
        return ""
    return re.sub(r'[^a-zA-Z0-9]', '', text).lower()


def sanitize_name(name):
    if not name:
        return ""
    name = name.replace('—', '-').replace('"', '').replace('!', '')
    return name.strip()


def is_exact_match(target_raw, scan_raw):
    return CardMatcher.is_exact_match(target_raw, scan_raw)


def parse_clipboard():
    text = pyperclip.paste().strip()
    ids = []

    if text.startswith("ydke://"):
        try:
            payload = text[7:]
            sections = payload.split("!")
            for section in sections:
                if not section:
                    continue
                missing_padding = len(section) % 4
                if missing_padding:
                    section += '=' * (4 - missing_padding)
                data = base64.b64decode(section)
                for i in range(0, len(data), 4):
                    if i + 4 <= len(data):
                        cid = struct.unpack("<I", data[i:i + 4])[0]
                        ids.append(str(cid))
        except Exception:
            pass
    else:
        for line in text.splitlines():
            line = line.strip()
            if line.isdigit():
                ids.append(line)
    return ids

_ocr_cache = {}


def get_cached_ocr(img_hash, ocr_func, img):
    if img_hash in _ocr_cache:
        return _ocr_cache[img_hash]
    text = ocr_func(img)
    _ocr_cache[img_hash] = text
    return text