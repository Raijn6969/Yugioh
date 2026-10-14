"""Duell-Übersicht: ein gespeichertes Match auf dem Spielfeld (Decks beider Spieler, Ergebnis, blättern)."""

import tempfile
import time
import unittest

import duel_view as dv
import match_history as mh
from card_stats import MatchEntry

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAS_TK = True
except Exception:
    HAS_TK = False

MINE = "#created by MD Importer\n#main\n1\n1\n2\n3\n#extra\n9\n!side\n"
THEIRS = "#main\n5\n5\n5\n#extra\n!side\n"


def entry(did, result=mh.WIN, opp_code=THEIRS, coin=False, first=False, turns=9):
    return MatchEntry(str(did), 1000.0 + did, mh.RANKED, result, first, turns, 1, "Elfnote", MINE,
                      "Blue-Eyes" if opp_code else "", opp_code, "Zwölfnote", coin)


class HelperTest(unittest.TestCase):
    def test_ydk_parts_and_unique(self):
        self.assertEqual(dv.ydk_parts(MINE), (["1", "1", "2", "3"], ["9"]))
        self.assertEqual(dv.ydk_parts(""), ([], []))
        self.assertEqual(dv.unique(["1", "1", "2", "1"]), [("1", 3), ("2", 1)])

    def test_result_and_details(self):
        self.assertEqual(dv.result_text(entry(1))[0], "SIEG")
        self.assertEqual(dv.result_text(entry(1, mh.LOSS))[0], "NIEDERLAGE")
        text = dv.details(entry(1))
        self.assertTrue(text.startswith("Zweiter  ·  Münzwurf verloren  ·  9 Züge  ·  Ranked"), text)
        self.assertNotIn("Münzwurf", dv.details(entry(1, coin=None)))


@unittest.skipUnless(HAS_TK, "kein Tk")
class DuelViewTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.db = mock_db()
        self.folder = tempfile.mkdtemp()  # keine Kartenbilder → Platzhalter mit Namen, kein Download im Test

    def view(self, matches, index=0):
        from unittest import mock
        patch = mock.patch.object(dv.card_images, "download", return_value=0)
        patch.start()
        self.addCleanup(patch.stop)
        view = dv.DuelView(self.root, self.db, matches, index, image_folder=self.folder)
        self.addCleanup(view.close)
        self.root.update()
        return view

    def texts(self, view):
        return [view.canvas.itemcget(i, "text") for i in view.canvas.find_all() if view.canvas.type(i) == "text"]

    def test_shows_both_decks_result_and_browses(self):
        view = self.view([entry(2), entry(1, mh.LOSS, opp_code="")])
        texts = self.texts(view)
        self.assertIn("SIEG", texts)
        self.assertIn("GEGNER · Blue-Eyes", texts)
        self.assertIn("Ash Blossom", texts)       # Platzhalter mit Kartenname (kein Bild)
        self.assertIn("3×", texts)                 # Gegner: 3 Kopien
        self.assertIn("4", texts)                  # dein Deck-Stapel: 4 Karten
        self.assertIn("1 / 2", view.title_label.cget("text"))
        view.newer_btn.invoke()                    # schon das neueste
        self.assertEqual(view.index, 0)
        view.older_btn.invoke()
        texts = self.texts(view)
        self.assertIn("NIEDERLAGE", texts)
        self.assertTrue(any(t.startswith("Deck unbekannt – das Gegner-Deck kommt mit der Match History")
                            for t in texts))

    def test_hover_shows_card_name(self):
        view = self.view([entry(1)])
        tile = next(i for i in view.canvas.find_all() if any(t.startswith("card5_") for t in view.canvas.gettags(i)))
        tag = next(t for t in view.canvas.gettags(tile) if t.startswith("card5_"))
        view._tip("5", 3, 100, 100)
        self.assertIn("3× Ash Blossom", self.texts(view))
        self.assertTrue(tag)

    def test_opens_from_winrate_match_row(self):
        from winrate_panel import WinratePanel
        from tests.test_winrate import entry as history_entry, make_db
        db = make_db()
        mh.store_matches(db, mh.parse_history({"58": {"1": history_entry(1, time.time() - 30, mh.WIN)}}))
        panel = WinratePanel(self.root, db, {}, lambda: None, on_open=lambda *a: None, on_close=lambda: None,
                             tracking=True)
        self.addCleanup(panel.close)
        from unittest import mock
        with mock.patch.object(dv.card_images, "download", return_value=0):
            panel.view_duel(0)
            self.root.update()
        self.assertIsNotNone(panel.duel)
        self.assertEqual(panel.duel.match.did, "1")
        panel.close()
        self.assertIsNone(panel.duel)


def mock_db():
    class Info:
        def __init__(self, name):
            self.name = name

    class Db:
        def infos(self, ids):
            names = {"1": "Elfnote Lucina", "2": "Elfnote Fortuna", "3": "Droll", "5": "Ash Blossom", "9": "Fusion"}
            return {cid: Info(names[cid]) for cid in ids if cid in names}
    return Db()


if __name__ == "__main__":
    unittest.main()
