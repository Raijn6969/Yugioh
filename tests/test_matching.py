"""Kartennamen-Abgleich: muss exakt wie vor dem Refactoring entscheiden (Golden Files)."""

import unittest

from tests.fakes import load_golden, make_core
import import_engine as ie
from card_engine import CardMatcher
from match_validator import MatchValidator
from utils import clean_text


class GoldenMatchingTest(unittest.TestCase):
    """107 echte Fälle (gesuchter Name, gelesener Text) aus md_debug.log."""

    @classmethod
    def setUpClass(cls):
        cls.golden = load_golden()
        cls.validator = MatchValidator(CardMatcher())
        cls.core = make_core()

    def test_check_match_unchanged(self):
        for target, ocr, ok, kind in self.golden["check_match"]:
            with self.subTest(target=target, ocr=ocr):
                self.assertEqual(list(self.validator.check_match(target, ocr)), [ok, kind])

    def test_check_match_with_overrule_unchanged(self):
        for target, ocr, ok, kind in self.golden["check_match_with_overrule"]:
            with self.subTest(target=target, ocr=ocr):
                self.assertEqual(list(self.core._check_match_with_overrule(target, ocr)), [ok, kind])

    def test_evaluate_fallback_unchanged(self):
        for target, ocr, expected in self.golden["evaluate_fallback"]:
            with self.subTest(target=target, ocr=ocr):
                self.assertEqual(self.validator.evaluate_fallback(target, ocr, "", ""), expected)


class CleanTextTest(unittest.TestCase):
    def test_ascii_names_unchanged(self):
        for name, expected in load_golden()["clean_text"].items():
            if name.isascii():
                with self.subTest(name=name):
                    self.assertEqual(clean_text(name), expected)

    def test_umlauts_become_base_letters(self):
        # Vorher fielen Umlaute weg ("aschenbltefreudigerfrhling")
        self.assertEqual(clean_text("Aschenblüte & Freudiger Frühling"), "aschenblutefreudigerfruhling")
        self.assertEqual(clean_text("Weißer Drache"), "weisserdrache")
        self.assertEqual(clean_text("Maxx „C“"), "maxxc")
        self.assertEqual(clean_text(""), "")
        self.assertEqual(clean_text(None), "")

    def test_german_ocr_without_umlauts_matches(self):
        # Englische Texterkennung liest "ü" oft als "u" → muss trotzdem passen
        ok, kind = MatchValidator(CardMatcher()).check_match(
            "Aschenblüte & Freudiger Frühling", "aschenblutefreudigerfruhling")
        self.assertEqual((ok, kind), (True, "EXACT"))


class KnightNightTest(unittest.TestCase):
    """'Clockwork Night' wird ohne Leerzeichen zu 'clockworknight' und enthält damit 'knight'."""

    def setUp(self):
        self.validator = MatchValidator(CardMatcher())

    def test_clockwork_night_vs_knight(self):
        cases = [
            ("Clockwork Night", "clockworkknight", False),   # vorher FUZZY (Ratio 0.966)
            ("Clockwork Knight", "clockworknight", False),
            ("Clockwork Night", "clockworknight", True),
            ("Clockwork Knight", "lclockworkknight", True),  # führendes 'l' bleibt ok
            ("Night Sword", "knightsword", False),           # alter Fall bleibt geschützt
            ("Knight Sword", "nightsword", False),
        ]
        for target, ocr, ok in cases:
            with self.subTest(target=target, ocr=ocr):
                self.assertEqual(self.validator.check_match(target, ocr)[0], ok)

    def test_fallback_blocked(self):
        self.assertFalse(self.validator.evaluate_fallback("clockworknight", "clockworkknight", "", ""))
        self.assertFalse(self.validator.evaluate_fallback("clockworkknight", "clockworknight", "", ""))

    def test_other_deck_card_is_vetoed(self):
        core = make_core()
        core._deck_clean_names = {"clockworkknight", "clockworknight", "sparkbladesoldier"}
        # Ähnlicher Name, aber exakt eine andere Karte aus dem Deck → kein Treffer
        self.assertEqual(core._check_match_with_overrule("Spark Blade Soldiers", "sparkbladesoldier"),
                         (False, "NONE"))
        self.assertEqual(core._check_match_with_overrule("Clockwork Knight", "clockworkknight"),
                         (True, "EXACT"))


class HelperTest(unittest.TestCase):
    def test_common_prefix_len_ignores_leading_l(self):
        self.assertEqual(ie.common_prefix_len("swordsoulsinistersov", "lswordsoulsinistar3b"), (15, 19))
        self.assertEqual(ie.common_prefix_len("stellarwindwolfrayet", "stellarwindwoltraw"), (14, 18))
        self.assertEqual(ie.common_prefix_len("abc", ""), (0, 0))

    def test_number_card_overrule(self):
        cases = [
            ("number75bamboozlinggossipshadow", "number75bamboozlinq", "75"),   # abgeschnitten + verrauscht
            ("number75bamboozlinggossipshadow", "lnumber75bamboozling", "75"),  # mit führendem 'l'
            ("nummer75verblueffenderklatschschatten", "nummer75verblueffendek", "75"),  # deutsch
            ("number106gianthand", "numberc106giantredt", None),       # 106 ≠ C106
            ("number39utopiabeyond", "number39utopia", None),          # Basis-Utopia, nicht abgeschnitten
            ("number39utopia", "number39utopiabeyond", None),          # OCR länger als Ziel
            ("number39utopiabeyond", "number39utopiarising", None),    # gleiche Nummer, anderer Rest
        ]
        for target, ocr, expected in cases:
            with self.subTest(ocr=ocr):
                self.assertEqual(ie.number_card_overrule(target, ocr), expected)


if __name__ == "__main__":
    unittest.main()
