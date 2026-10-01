"""Archetyp-Suche: Gruppierung und Scan des Such-Rasters."""

import unittest

from tests.fakes import FakeGrid, load_golden, make_core
from import_engine import DeckCard
from utils import clean_text, sanitize_name


class BatchGroupingTest(unittest.TestCase):
    def test_groups_unchanged(self):
        golden = load_golden()
        deck = [DeckCard(*c) for c in golden["deck"]]
        groups = {k: [c[1] for c in g] for k, g in make_core()._compute_batch_groups(deck).items()}
        self.assertEqual(groups, golden["batch_groups"])

    def test_german_umlaut_prefix_stays_whole(self):
        # Vorher wurde "Götterdämmerung" an den Umlauten zerschnitten → keine sinnvolle Gruppe
        deck = [DeckCard(str(i), f"Götterdämmerung {n}", 1) for i, n in enumerate(["Alpha", "Beta", "Gamma"])]
        self.assertEqual(list(make_core()._compute_batch_groups(deck)), ["götterdämmerung"])

    def test_generic_german_first_words_not_grouped(self):
        deck = [DeckCard(str(i), f"Schwarzer {n}", 1) for i, n in enumerate(["Ritter", "Magier", "Drache"])]
        self.assertEqual(make_core()._compute_batch_groups(deck), {})


class BatchScanTest(unittest.TestCase):
    def run_scan(self, grid_texts, group, opportunistic=None, prefix="artmage"):
        core = make_core("fast")
        core._deck_clean_names = {clean_text(sanitize_name(name))
                                  for _, name, _ in list(group) + list((opportunistic or {}).values())}
        grid = FakeGrid(grid_texts)
        core._type_search_term = lambda automator, text: None
        core._get_slot_geometry = grid.geometry
        core._capture_and_ocr_slot = grid.read
        added = []
        found, _ = core._batch_scan_for_archetype(
            None, grid.automator(), prefix, group, added,
            last_seen_slot_00="xyz", opportunistic_cards=opportunistic)
        return found, added, grid

    def test_stops_at_end_of_results(self):
        texts = ["artmagepowerpatron", "artmagefinmel", "artmagelitera"] + ["artmagelitera"] * 39
        group = [("1", "Artmage Power Patron", 2), ("2", "Artmage Finmel", 3),
                 ("3", "Artmage Litera", 2), ("4", "Artmage Graflare", 2)]  # Graflare fehlt im Raster
        found, added, grid = self.run_scan(texts, group)
        self.assertEqual(found, {"1", "2", "3"})
        # ohne Ende-Erkennung ~31 Lesevorgänge (gleiche Nachbar-Slots werden einmal nachgelesen)
        self.assertLess(grid.reads, 12)
        self.assertEqual([a["amount"] for a in added], [2, 3, 2])

    def test_ambiguous_truncated_names_are_skipped(self):
        # Beide Varuroon-Varianten lesen sich abgeschnitten identisch → nicht zuordnen
        texts = ["artmagefinmel", "radianttyphoonvaruroon", "radianttyphoonvaruroon"] + ["x"] * 39
        group = [("1", "Artmage Finmel", 1),
                 ("2", "Radiant Typhoon Varuroon, the Vibrant Vortex", 1),
                 ("3", "Radiant Typhoon Varuroon, the Marine Eidolon", 2)]
        found, added, _ = self.run_scan(texts, group)
        self.assertEqual(found, {"1"})

    def test_opportunistic_card_is_added(self):
        texts = ["artmagefinmel", "lnervathepowerpatronof", "artmagelitera"] + ["artmagelitera"] * 39
        group = [("1", "Artmage Finmel", 3), ("2", "Artmage Litera", 2)]
        oppo = {"Nerva the Power Patron of Creation": ("9", "Nerva the Power Patron of Creation", 1)}
        found, added, _ = self.run_scan(texts, group, oppo)
        self.assertEqual(found, {"1", "2", "9"})

    def test_second_rarity_is_not_taken_for_similar_card(self):
        # Echter Fall: Aramasa in zwei Seltenheiten, abgeschnitten gelesen. Der zweite Slot ähnelt
        # 'Mitsurugi no Mikoto, Saji' zu 86 % → darf NICHT als Saji eingefügt werden.
        texts = ["mitsuruginomikotoaram", "mitsuruginomikotoaram", "mitsurugiprayers",
                 "mitsuruginomikotosaji", "mitsuruginomikotokusa"] + ["mitsuruginomikotokusa"] * 37
        group = [("1", "Mitsurugi no Mikoto, Kusanagi", 1), ("2", "Mitsurugi no Mikoto, Saji", 1),
                 ("3", "Mitsurugi no Mikoto, Aramasa", 2), ("4", "Mitsurugi Prayers", 1)]
        found, added, grid = self.run_scan(texts, group, prefix="mitsurugi")
        self.assertEqual(found, {"1", "2", "3", "4"})
        slot_of = {a["expected_raw"]: slot for a, (slot, _) in zip(added, grid.added)}
        self.assertEqual(slot_of["Mitsurugi no Mikoto, Aramasa"], 0)
        self.assertEqual(slot_of["Mitsurugi no Mikoto, Saji"], 3)  # nicht Slot 1 (Aramasa)

    def test_truncated_card_with_leading_l_is_taken_along(self):
        # Im Mitsurugi-Raster sichtbar, aber abgeschnitten und mit 'l'-Artefakt gelesen
        texts = ["mitsurugiprayers", "lamenomurakumonomit", "lfutsunomitamanomitsut",
                 "mitsurugimirror"] + ["x"] * 38
        group = [("1", "Mitsurugi Prayers", 1), ("2", "Mitsurugi Mirror", 1)]
        oppo = {name: (cid, name, 1) for cid, name in [("7", "Ame no Murakumo no Mitsurugi"),
                                                        ("8", "Futsu no Mitama no Mitsurugi")]}
        found, _, _ = self.run_scan(texts, group, oppo, prefix="mitsurugi")
        self.assertEqual(found, {"1", "2", "7", "8"})


if __name__ == "__main__":
    unittest.main()
