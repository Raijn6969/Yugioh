"""
Schutz gegen die Fehler aus dem Dracotail-Import: Gulamel (nicht im Deck, Fusion) wurde als
Dracotail Flame erkannt und 3x eingefügt, Arthalion (Fusion) beim Ruckler nur 1x.
"""

import unittest
from unittest import mock

import deck_export
import md_layout
from card_db import CardDB, CardMatch
from deck_export import ExportedCard
from import_engine import DeckCard
from tests.fakes import make_core
from utils import clean_text

CARDS = [("1", "Dracotail Flame", "trap"), ("2", "Dracotail Gulamel", "fusion"),
         ("3", "Dracotail Arthalion", "fusion"), ("4", "Dracotail Sting", "trap"),
         ("5", "Dracotail Faimena", "effect")]


def make_check_core():
    core = make_core()
    core.card_db = CardDB(CARDS)
    core._deck_clean_names = {"dracotailflame", "dracotailarthalion", "dracotailsting", "dracotailfaimena"}
    return core


class CardNotInDeckTest(unittest.TestCase):
    def test_gulamel_is_not_taken_as_flame(self):
        core = make_check_core()
        reason = core._wrong_card_reason("dracotailflame", "dracotailgulame")
        self.assertIn("Dracotail Gulamel", reason)
        pending = {"Dracotail Flame": ("1", "Dracotail Flame", 1)}
        self.assertEqual(core._plausible_candidates(pending, "dracotailgulame", 0.85), [])

    def test_cut_off_name_that_is_another_card_name(self):
        # Echter Fall: "Wynn the Wind Charmer, Verdant" steht abgeschnitten als "Wynn the Wind Charmer" im Panel –
        # das ist auch eine (andere) Karte, die bei der Suche nach "…, Verdant" aber gar nicht erscheinen kann
        core = make_core()
        core.card_db = CardDB([("1", "Wynn the Wind Charmer", "effect"),
                               ("2", "Wynn the Wind Charmer, Verdant", "link")])
        core._search_term = "wynnthewindcharmerverdant"
        self.assertEqual(core._check_match_with_overrule("Wynn the Wind Charmer, Verdant", "lwynnthewindcharmer"),
                         (True, "FUZZY"))
        # Archetyp-Suche "wynn": Die alte Wynn kann im Ergebnis stehen → weiter ablehnen
        core._search_term = "wynn"
        self.assertIn("laut Kartenliste", core._wrong_card_reason("wynnthewindcharmerverdant", "lwynnthewindcharmer"))

    def test_own_card_read_slightly_cut_off_still_matches(self):
        core = make_check_core()
        self.assertIsNone(core._wrong_card_reason("dracotailarthalion", "dracotailarthalio"))
        self.assertIsNone(core._wrong_card_reason("dracotailfaimena", "ldracotailfaimena"))

    def test_identify_needs_a_unique_card(self):
        db = CardDB(CARDS)
        self.assertEqual(db.identify("dracotailgulame"), "Dracotail Gulamel")
        self.assertIsNone(db.identify("dracotail"))       # zu kurz / mehrdeutig
        self.assertIsNone(db.identify("dracotailflamme"))  # verlesen → kein eindeutiger Name


class WrongDeckAreaTest(unittest.TestCase):
    def test_no_extra_clicks_when_card_landed_in_extra_deck(self):
        core = make_check_core()
        core.card_types = {"1": "Trap Card"}
        core.deck_counter = mock.Mock(value=13, wait_for=lambda expected, timeout: 13)
        core.extra_counter = mock.Mock(value=5)
        core.extra_counter.read.return_value = 6  # die "Falle" ist im Extra Deck gelandet
        automator = mock.Mock()
        unsure = core._verify_insert(automator, 0, 0, 1, "Dracotail Flame", "1", stalled=False)
        self.assertTrue(unsure)
        automator.add_card_to_deck.assert_not_called()
        self.assertEqual(core.extra_counter.value, 6)

    def test_extra_deck_card_checked_with_extra_count(self):
        core = make_check_core()
        core.card_types = {"3": "Fusion Monster"}
        readings = iter([5, 5, 6])  # 1 von 2 Klicks verloren → 1x nachklicken
        core.extra_counter = mock.Mock(value=4, wait_for=lambda expected, timeout: next(readings))
        core.deck_counter = mock.Mock(value=20)
        core.deck_counter.read.return_value = 20
        automator = mock.Mock()
        self.assertFalse(core._verify_insert(automator, 0, 0, 2, "Dracotail Arthalion", "3", stalled=True))
        automator.add_card_to_deck.assert_called_once_with(0, 0, 1)


def read(zone, names):
    return [ExportedCard(zone, i + 1, name, CardMatch(str(i), name, True)) for i, name in enumerate(names)]


class FinalDeckCheckTest(unittest.TestCase):
    def run_check(self, reads):
        core = make_check_core()
        frame = md_layout.Frame(0, 0, 1920, 1080)
        deck = [DeckCard("1", "Dracotail Flame", 1), DeckCard("4", "Dracotail Sting", 1),
                DeckCard("3", "Dracotail Arthalion", 2)]
        reads = iter(reads)
        zones = lambda self, sct, frame, counts=None: [("Main", [(543, 267)], 10), ("Extra", [(543, 845)] * 4, 10)]
        searched = []
        core._search_single_card = lambda sct, a, card, added, s0, last: (searched.append(card) or True, s0, last)
        core._capture_and_ocr_slot = lambda sct, monitor: ("", "dracotailgulamel")
        automator = mock.Mock()
        with mock.patch.object(md_layout, "md_frame", return_value=frame), \
                mock.patch.object(deck_export.DeckExporter, "_plan", zones), \
                mock.patch.object(deck_export.DeckExporter, "_read_cards", lambda *args: next(reads)), \
                mock.patch("import_engine.win_api.set_cursor_pos") as self.park, \
                mock.patch("import_engine.win_api.get_cursor_pos", return_value=(1292, 600)), \
                mock.patch("import_engine.time.sleep"):
            problems = core._final_deck_check(None, automator, deck, "", "")
        right_clicks = [c for c in automator.iron_grip_click.call_args_list if c.kwargs.get("button") == "right"]
        return problems, searched, right_clicks, core

    def test_dracotail_import_is_repaired(self):
        wrong = read("Main", ["Dracotail Sting"]) + read(
            "Extra", ["Dracotail Arthalion", "Dracotail Gulamel", "Dracotail Gulamel", "Dracotail Gulamel"])
        right = read("Main", ["Dracotail Flame", "Dracotail Sting"]) + read(
            "Extra", ["Dracotail Arthalion", "Dracotail Arthalion"])
        problems, searched, right_clicks, core = self.run_check([wrong, right])
        self.assertEqual(problems, [])
        self.assertEqual(len(right_clicks), 3)  # 3x Gulamel entfernt
        self.assertEqual([(c.name, c.amount) for c in searched], [("Dracotail Flame", 1), ("Dracotail Arthalion", 1)])
        self.assertTrue(any("korrigiert" in note for note in core.notes))

    def test_correct_deck_needs_no_changes(self):
        right = read("Main", ["Dracotail Flame", "Dracotail Sting"]) + read(
            "Extra", ["Dracotail Arthalion", "Dracotail Arthalion"])
        problems, searched, right_clicks, _ = self.run_check([right])
        self.assertEqual((problems, searched, right_clicks), ([], [], []))

    def test_last_read_counts_as_scan(self):
        wrong = read("Main", ["Dracotail Sting"]) + read(
            "Extra", ["Dracotail Arthalion", "Dracotail Gulamel", "Dracotail Gulamel", "Dracotail Gulamel"])
        right = read("Main", ["Dracotail Flame", "Dracotail Sting"]) + read(
            "Extra", ["Dracotail Arthalion", "Dracotail Arthalion"])
        _, _, _, core = self.run_check([wrong, right])
        self.assertEqual(core.final_scan.cards, right)  # Stand nach der Korrektur
        self.park.assert_called_with(*md_layout.PARK_POINT)  # Maus neben dem Deck (fürs Deck-Fenster)

    def test_memory_mode_checks_without_clicks(self):
        core = make_check_core()
        core.memory = mock.Mock()
        core.memory.deck.return_value = ([101, 104], [103, 103])  # Konami-IDs: Main, Extra
        core._kid_names = {101: "Dracotail Flame", 104: "Dracotail Sting", 103: "Dracotail Arthalion"}
        core._kid_passcodes = {101: "1", 104: "4", 103: "3"}
        frame = md_layout.Frame(0, 0, 1920, 1080)
        deck = [DeckCard("1", "Dracotail Flame", 1), DeckCard("4", "Dracotail Sting", 1),
                DeckCard("3", "Dracotail Arthalion", 2)]
        plan = mock.Mock(return_value=[("Main", [(543, 267)] * 2, 10), ("Extra", [(543, 845)] * 2, 10)])
        automator = mock.Mock()
        with mock.patch.object(md_layout, "md_frame", return_value=frame), \
                mock.patch.object(deck_export.DeckExporter, "_plan", lambda self, sct, fr, counts=None: plan(counts)), \
                mock.patch.object(deck_export.DeckExporter, "_read_cards", side_effect=AssertionError("Klicks!")), \
                mock.patch("import_engine.win_api.set_cursor_pos"), \
                mock.patch("import_engine.win_api.get_cursor_pos", return_value=(1292, 600)), \
                mock.patch("import_engine.time.sleep"):
            problems = core._final_deck_check(None, automator, deck, "", "")
        self.assertEqual(problems, [])
        plan.assert_called_with((2, 2))  # Kartenzahlen aus dem Speicher statt per Texterkennung
        automator.iron_grip_click.assert_not_called()
        self.assertEqual([(c.zone, c.slot, c.match.cid) for c in core.final_scan.cards],
                         [("Main", 1, "1"), ("Main", 2, "4"), ("Extra", 1, "3"), ("Extra", 2, "3")])

    def test_nothing_changed_when_a_card_was_not_read_safely(self):
        unsure = ExportedCard("Extra", 2, "dracotail?", CardMatch(None, "", False, "nichts erkannt"))
        cards = read("Main", ["Dracotail Flame", "Dracotail Sting"]) + read("Extra", ["Dracotail Arthalion"]) + [unsure]
        problems, searched, right_clicks, _ = self.run_check([cards])
        self.assertEqual((searched, right_clicks), ([], []))
        self.assertTrue(any("nicht sicher erkannt" in p for p in problems))
        self.assertTrue(any("Arthalion (1x fehlend)" in p for p in problems))

    def test_compare_counts_only_safe_reads(self):
        expected = {clean_text("Dracotail Flame"): 1}
        cards = read("Main", ["Dracotail Flame", "Dracotail Flame"])
        self.assertEqual(deck_export.compare_deck(expected, cards), ({"dracotailflame": 1}, {}))


if __name__ == "__main__":
    unittest.main()
