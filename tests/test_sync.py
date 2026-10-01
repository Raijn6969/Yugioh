"""
Warten nach dem Tippen (Slot 0): Das Programm darf NIE vor dem Laden der neuen Suche
einfügen. Schnellstart nur mit Beweis, sonst die bisherigen festen Wartezeiten.
"""

import time
import unittest

from tests.fakes import FakeSearchGame, make_core

TARGET = "Artmage Power Patron"
NEW = "lartmagepowerpatron"


class SyncTest(unittest.TestCase):
    def sync(self, load_at, old_slot0, new_slot0, panel_start, speed="normal"):
        core = make_core(speed)
        core._last_clean_ocr = panel_start
        game = FakeSearchGame(load_at * core.speed_mult, old_slot0, new_slot0, panel_start)
        core._capture_and_ocr_slot = game.read
        t0 = time.perf_counter()
        s_c, _, exact, fuzzy = core._sync_and_stabilize_slot_00(
            None, game.automator(), {}, 0, 0, old_slot0, TARGET)
        return s_c, exact or fuzzy, time.perf_counter() - t0, load_at * core.speed_mult

    def assert_after_load(self, dt, load):
        self.assertGreaterEqual(dt, load, "hat vor dem Laden der Suche losgelegt")

    def test_fast_when_search_loads_quickly(self):
        s_c, match, dt, load = self.sync(0.2, "lmaxxc", NEW, "lmaxxc")
        self.assertEqual((s_c, match), (NEW, True))
        self.assert_after_load(dt, load)
        self.assertLess(dt, 0.75)  # bisher fix ~1,0 s

    def test_slow_pc_falls_back_to_old_timing(self):
        s_c, match, dt, load = self.sync(1.2, "lmaxxc", NEW, "lmaxxc")
        self.assertEqual((s_c, match), (NEW, True))
        self.assert_after_load(dt, load)

    def test_target_already_shown_before_uses_old_timing(self):
        # Artmage-Fall: alte Anzeige = gesuchte Karte → kein Schnellstart möglich
        s_c, match, dt, load = self.sync(0.3, NEW, NEW, NEW)
        self.assertEqual((s_c, match), (NEW, True))
        self.assertGreaterEqual(dt, 0.8)

    def test_other_card_in_slot0_uses_old_timing(self):
        s_c, match, dt, load = self.sync(0.2, "lmaxxc", "lnervathepowerpatronof", "lmaxxc")
        self.assertEqual((s_c, match), ("lnervathepowerpatronof", False))
        self.assertGreaterEqual(dt, 0.8)

    def test_last_read_card_counts_as_old_display(self):
        s_c, match, dt, load = self.sync(0.4, "lmaxxc", NEW, "lartmagefinmel")
        self.assertEqual((s_c, match), (NEW, True))
        self.assert_after_load(dt, load)

    def test_slow_profile_scales_waits(self):
        s_c, match, dt, load = self.sync(0.2, "lmaxxc", NEW, "lmaxxc", speed="slow")
        self.assertEqual((s_c, match), (NEW, True))
        self.assert_after_load(dt, load)


if __name__ == "__main__":
    unittest.main()
