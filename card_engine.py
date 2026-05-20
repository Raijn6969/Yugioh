"""
CORE DIAGNOSTICS ENGINE (V26.1 - JSON-CONFIGURED ARCHETYPE SYSTEM)
Thema: Vollständig konfigurierbare Archetypen-Profile statt Hardcoding.
        Substring-Matching mit Längenbeschränkung gegen False-Positives.
        NEU: Angepasster GhostTracker (>14) um lange Archetypen wie
             "Radiant Typhoon" nicht fälschlicherweise abzubrechen (MST-Fix).
"""

import re
import json
import os
from difflib import SequenceMatcher
from typing import Dict, List, Optional

ARCHETYPES_FILE = "archetypes_config.json"

class ArchetypeProfile:
    """Datengetriebenes Archetypen-Profil"""

    def __init__(self, config: dict):
        self.name = config.get("name", "unknown")
        self.tokens = set(config.get("tokens", []))
        self.exclusivity = config.get("exclusivity", "medium")  # high, medium, low
        self.common_mistakes = config.get("common_mistakes", {})
        self.mutation_pairs = config.get("mutation_pairs", [])
        self.min_match_ratio = config.get("min_match_ratio", 0.72)

    def is_exclusive_token(self, token: str) -> bool:
        """Prüft ob Token exklusiv zu diesem Archetyp gehört"""
        return token in self.tokens

    def get_correction(self, token: str) -> Optional[str]:
        """Holt OCR-Korrektur für häufige Fehlerkennungen"""
        for correct, mistakes in self.common_mistakes.items():
            if token in mistakes:
                return correct
        return None

class ArchetypeDatabase:
    """Verwaltet alle Archetypen-Profile"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._loaded = False
        return cls._instance

    def __init__(self):
        if self._loaded:
            return
        self._loaded = True
        self.profiles: Dict[str, ArchetypeProfile] = {}
        self._load_profiles()

    def _load_profiles(self):
        """Lädt Profile aus JSON oder erstellt Default-Konfiguration"""
        if os.path.exists(ARCHETYPES_FILE):
            try:
                with open(ARCHETYPES_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for name, config in data.get("archetypes", {}).items():
                    config["name"] = name
                    self.profiles[name] = ArchetypeProfile(config)
                return
            except Exception:
                pass

        self._create_default_profiles()
        self._save_profiles()

    def _create_default_profiles(self):
        """Erstellt die Standard-Profile aus den historischen Fixes"""
        defaults = {
            "sky_striker": {
                "tokens": ["roze", "raye", "zeke", "shizuku", "kagari", "hayate", "azalea", "camellia"],
                "exclusivity": "high",
                "common_mistakes": {
                    "roze": ["zero", "rose"],
                    "raye": ["rave", "rage"],
                    "shizuku": ["shizuka"]
                },
                "min_match_ratio": 0.85
            },
            "lunalight": {
                "tokens": ["lunalight", "luna", "light", "leo", "dancer"],
                "exclusivity": "medium",
                "mutation_pairs": [["leo", "liger"]],
                "common_mistakes": {
                    "lunalight": ["luna light", "lunalite"]
                },
                "min_match_ratio": 0.72
            },
            "hecahands": {
                "tokens": ["hecahands", "henshande", "henahande", "henshands", "hecahand"],
                "exclusivity": "high",
                "common_mistakes": {
                    "hecahands": ["henshande", "henahande", "henshands", "hecahand"]
                },
                "min_match_ratio": 0.80
            },
            "naturia": {
                "tokens": ["exterio", "barkion", "beast", "bark"],
                "exclusivity": "medium",
                "mutation_pairs": [["exterio", "exterior"]],
                "min_match_ratio": 0.75
            },
            "generic_synchro": {
                "tokens": ["warrior", "dragon", "conduction", "ib", "al", "yad"],
                "exclusivity": "low",
                "min_match_ratio": 0.70
            }
        }

        for name, config in defaults.items():
            self.profiles[name] = ArchetypeProfile(config)

    def _save_profiles(self):
        """Speichert Profile als JSON"""
        data = {"archetypes": {}}
        for name, profile in self.profiles.items():
            data["archetypes"][name] = {
                "tokens": list(profile.tokens),
                "exclusivity": profile.exclusivity,
                "common_mistakes": profile.common_mistakes,
                "mutation_pairs": profile.mutation_pairs,
                "min_match_ratio": profile.min_match_ratio
            }
        with open(ARCHETYPES_FILE, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def detect_archetype(self, tokens: List[str]) -> Optional[ArchetypeProfile]:
        """Erkennt Archetyp aus Token-Liste"""
        best_match = None
        best_score = 0

        for profile in self.profiles.values():
            matched = sum(1 for t in tokens if t in profile.tokens)
            if matched > 0:
                score = matched / len(tokens)
                if score > best_score:
                    best_score = score
                    best_match = profile

        return best_match if best_score > 0.3 else None

    def is_mutation(self, tokens1: List[str], tokens2: List[str]) -> bool:
        """Prüft auf bekannte Mutationen (Leo -> Liger)"""
        for profile in self.profiles.values():
            for pair in profile.mutation_pairs:
                if any(p in tokens1 for p in pair) and any(p in tokens2 for p in pair):
                    set1 = set(pair) & set(tokens1)
                    set2 = set(pair) & set(tokens2)
                    if set1 and set2 and not (set1 & set2):
                        return True
        return False


class CardMatcher:
    """JSON-konfigurierter Karten-Matcher mit Substring-Längenschutz"""

    def __init__(self):
        self.db = ArchetypeDatabase()

    @staticmethod
    def clean_tokens(text: str) -> List[str]:
        """Extrahiert und filtert Tokens aus Text"""
        if not text:
            return []
        words = [w.lower() for w in re.findall(r'[a-zA-Z0-9]+', text) if len(w) >= 1]
        noise = {'res', 'fe', 'ge', 'te', 'co', 'id', 'dark', 'light', 'earth',
                 'water', 'fire', 'wind', 'bes', 'ts', 'gk', 'the', 'for'}
        return [w for w in words if w not in noise and len(w) >= 2 or w in ['c']]

    @staticmethod
    def normalize_special(words: List[str]) -> List[str]:
        """Normalisiert Spezialfälle wie lunalight"""
        res = []
        i = 0
        while i < len(words):
            if i < len(words) - 1 and words[i] == 'luna' and words[i+1] == 'light':
                res.append('lunalight')
                i += 2
                continue

            w = words[i]
            if w in {'henshande', 'henahande', 'henshands', 'hecahands'} or \
               (w.startswith('hen') and ('hand' in w or 'hande' in w)):
                res.append('hecahands')
                i += 1
                continue

            res.append(w)
            i += 1
        return res

    def _token_matches(self, tw: str, sw: str) -> bool:
        """
        Prüft ob Target-Token tw mit Scan-Token sw matcht.
        Mit Längenbeschränkung für Substring-Matches.
        """
        norm_tw = tw.replace('1', 'l').replace('0', 'o').replace('i', 'l')
        norm_sw = sw.replace('1', 'l').replace('0', 'o').replace('i', 'l')

        # Exakter Match
        if tw == sw:
            return True

        # Hohe Similarity
        if SequenceMatcher(None, tw, sw).ratio() > 0.72:
            return True

        len_tw, len_sw = len(tw), len(sw)

        # Substring-Match nur wenn Längenunterschied ≤ 50%
        # Verhindert: "over" matcht nicht "overtoadfuturefusion4"
        if len_tw >= 2:
            if tw in sw and len_sw <= len_tw * 1.5:
                return True
            if sw in tw and len_tw <= len_sw * 1.5:
                return True

        # Normalisierte Substring-Prüfung
        if len_tw >= 2 and len_sw >= 2:
            if norm_tw in norm_sw and len_sw <= len_tw * 1.5:
                return True
            if norm_sw in norm_tw and len_tw <= len_sw * 1.5:
                return True

        return False

    def is_exact_match(self, target_raw: str, scan_raw: str) -> bool:
        """Haupt-Matching-Funktion mit Archetypen-Kontext"""
        if not target_raw or not scan_raw:
            return False

        t_words = self.normalize_special(self.clean_tokens(target_raw))
        s_words = self.normalize_special(self.clean_tokens(scan_raw))

        if not t_words or not s_words:
            return False

        # Archetypen-Erkennung
        archetype = self.db.detect_archetype(t_words)

        # Mutations-Check
        if self.db.is_mutation(t_words, s_words):
            return False

        # Exklusivitäts-Check für High-Exclusivity-Archetypen
        if archetype and archetype.exclusivity == "high":
            exclusive_token = next(
                (t for t in t_words if archetype.is_exclusive_token(t)),
                None
            )
            if exclusive_token:
                if not any(
                    SequenceMatcher(None, exclusive_token, sw).ratio() > archetype.min_match_ratio
                    for sw in s_words
                ):
                    return False

        # Flexibler Token-Abgleich mit Längenschutz
        matched_count = 0
        clean_s_words = [sw for sw in s_words if len(sw) >= 3 or sw in t_words]

        for tw in t_words:
            # Korrekturversuch via Common-Mistakes
            norm_tw = tw
            if archetype:
                correction = archetype.get_correction(tw)
                if correction:
                    norm_tw = correction

            has_match = any(self._token_matches(tw, sw) for sw in clean_s_words)

            if has_match:
                matched_count += 1

        # Berechne Coverage
        coverage = matched_count / len(t_words) if t_words else 0.0

        # Mindest-Coverage basierend auf Token-Anzahl und Archetyp
        if archetype:
            min_coverage = archetype.min_match_ratio
        else:
            min_coverage = 1.0 if len(t_words) <= 2 else (0.65 if len(t_words) == 3 else 0.70)

        # Prefix-Match für lange Namen (3+ Wörter)
        if not (coverage >= min_coverage) and len(t_words) >= 4 and len(s_words) >= 3:
            prefix_match = all(
                self._token_matches(t_words[i], s_words[i])
                for i in range(min(3, len(s_words), len(t_words)))
            )
            if prefix_match:
                return True

        # Stranger-Words-Check für Falscherkennungen
        stranger_words = []
        for sw in s_words:
            if not any(
                SequenceMatcher(None, tw, sw).ratio() > 0.70 or
                sw in tw or tw in sw
                for tw in t_words
            ) and len(sw) >= 2 and sw.isalpha():
                stranger_words.append(sw)

        # Blockiere bei zu vielen Fremdwörtern
        if stranger_words and archetype and archetype.exclusivity in ["high", "medium"]:
            blocking_strangers = [sw for sw in stranger_words if len(sw) >= 4]
            if blocking_strangers:
                return False

        return coverage >= min_coverage


class GhostTracker:
    """Verfolgt UI-Geisterkarten mit Archetypen-Bewusstsein"""

    def __init__(self):
        self.current_origin_slot = 0
        self.last_slot_text = ""
        self.db = ArchetypeDatabase()

    def track_slot(self, slot: int, current_scan_clean: str) -> bool:
        """Prüft ob aktueller Slot ein Geist des Originals ist"""
        if slot == 0:
            self.current_origin_slot = 0
            self.last_slot_text = current_scan_clean
            return False

        if not current_scan_clean or not self.last_slot_text:
            self.current_origin_slot = slot
            self.last_slot_text = current_scan_clean
            return False

        # Mutations-Check
        if self.db.is_mutation(
            self.last_slot_text.split(),
            current_scan_clean.split()
        ):
            self.current_origin_slot = slot
            self.last_slot_text = current_scan_clean
            return False

        # Sequence-Matching
        sm = SequenceMatcher(None, self.last_slot_text, current_scan_clean)
        ratio = sm.ratio()

        is_ghost = False
        if ratio > 0.95 or current_scan_clean == self.last_slot_text:
            is_ghost = True
        else:
            match = sm.find_longest_match(
                0, len(self.last_slot_text),
                0, len(current_scan_clean)
            )
            # FIX FÜR MYSTICAL SPACE TYPHOON:
            # Toleranz für extrem lange Archetypen-Stämme erhöht (von >= 10 auf > 14).
            if match and match.size > 14:
                is_ghost = True

        if is_ghost:
            self.last_slot_text = current_scan_clean
            return True

        self.current_origin_slot = slot
        self.last_slot_text = current_scan_clean
        return False

    def get_origin(self) -> int:
        return self.current_origin_slot