"""Duell-Übersicht: ein gespeichertes Match auf dem Spielfeld (Decks beider Spieler, Ergebnis, blättern)."""

import tempfile
import time
import unittest

import duel_view as dv
import match_history as mh
from card_stats import MatchEntry, Replay

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

    def view(self, matches, index=0, replay=False):
        from unittest import mock
        patch = mock.patch.object(dv.card_images, "download", return_value=0)
        patch.start()
        self.addCleanup(patch.stop)
        view = dv.DuelView(self.root, self.db, matches, index, image_folder=self.folder, replay=replay)
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
        view._tip("5", 3, 100, 100, 50)
        self.assertIn("3× Ash Blossom", self.texts(view))
        self.assertTrue(tag)

    def hover_card(self, view):
        """Liefert (Tag, bbox) der Gegner-Karte 5."""
        c = view.canvas
        tile = next(i for i in c.find_all() if any(t.startswith("card5_") for t in c.gettags(i)))
        tag = next(t for t in c.gettags(tile) if t.startswith("card5_"))
        self.assertTrue(c.tag_bind(tag, "<Enter>"))
        return tag, c.bbox(tag)

    def test_tip_is_disabled_and_not_over_card(self):
        view = self.view([entry(1)])
        c = view.canvas
        tag, box = self.hover_card(view)
        view._tip("5", 3, box[0], box[1], box[3] - box[1])
        tips = c.find_withtag("tip")
        self.assertEqual(len(tips), 2)  # Text + Rechteck
        self.assertTrue(all(c.itemcget(i, "state") == "disabled" for i in tips))
        tip_box = c.bbox("tip")
        self.assertGreaterEqual(tip_box[1], box[3])  # unterhalb der Karte
        # nahe am unteren Rand: kein Platz mehr unten, also oberhalb – und wieder nicht auf der Karte
        view._untip()
        self.assertEqual(c.find_withtag("tip"), ())
        low = view.size[1] - 10
        view._tip("5", 3, 100, low - 40, 40)
        tip_box = c.bbox("tip")
        self.assertLessEqual(tip_box[3], low - 40)
        self.assertLessEqual(tip_box[3], view.size[1])

    def test_tip_not_redrawn_for_same_card(self):
        from unittest import mock
        view = self.view([entry(1)])
        c = view.canvas
        tag, box = self.hover_card(view)
        x, y = (box[0] + box[2]) // 2, (box[1] + box[3]) // 2
        view.win.geometry("+-5000+-5000")  # der echte Mauszeiger darf keine Enter/Leave-Ereignisse beisteuern
        view.win.update()
        with mock.patch.object(c, "create_text", wraps=c.create_text) as create:
            for _ in range(6):  # echte Enter/Motion-Ereignisse über derselben Karte
                c.event_generate("<Motion>", x=x, y=y)
                view.win.update()
            tip_calls = [k for k in create.call_args_list if k.kwargs.get("tags") == ("tip",)]
            self.assertEqual(len(tip_calls), 1, create.call_args_list)  # einmal gemalt, dann nie wieder
            args = view._tip_key  # (cid, count, x, y): derselbe Aufruf bei stehendem Hinweis malt nicht neu
            view._tip(args[0], args[1], args[2], args[3], box[3] - box[1])
            self.assertEqual(len([k for k in create.call_args_list if k.kwargs.get("tags") == ("tip",)]), 1)
        view._untip()
        self.assertIsNone(view._tip_key)
        self.assertEqual(c.find_withtag("tip"), ())

    def test_replay_only_when_asked_for_and_browsing_keeps_the_wish(self):
        plain = self.view([entry(1)])                 # Mitschnitt vorhanden, aber nicht verlangt: nur die Decklisten
        self.assertIsNone(plain.replay)
        self.assertEqual(plain.mode, "decks")
        self.assertEqual(plain.controls.winfo_manager(), "")
        wanted = self.view([entry(2), entry(1)], replay=True)   # entry(2) hat keinen Mitschnitt
        self.assertIsNone(wanted.replay)
        self.assertEqual(wanted.controls.winfo_manager(), "")
        wanted.older_btn.invoke()
        self.assertIsNotNone(wanted.replay)
        self.assertEqual((wanted.mode, wanted.controls.winfo_manager()), ("replay", "pack"))
        wanted.newer_btn.invoke()
        self.assertIsNone(wanted.replay)
        wanted.older_btn.invoke()
        self.assertIsNotNone(wanted.replay)           # der Wunsch blieb beim Blättern erhalten
        both = self.view([entry(1), entry(2)])        # ohne Wunsch zeigt auch das Blättern nie einen Mitschnitt
        both.step(1)
        both.step(-1)
        self.assertIsNone(both.replay)

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

    def test_pile_inspector_open_and_close(self):
        view = self.view([entry(1)], replay=True)
        cards = [{"uid": 10, "card": 101, "team": 0}]
        view._open_pile_inspector("Friedhof", cards, 0)
        self.assertIsNotNone(view._pile_inspector)
        self.assertTrue(view._pile_inspector.winfo_exists())
        # Re-opening with another pile closes previous
        view._open_pile_inspector("Verbannt", [], 0)
        self.assertIsNotNone(view._pile_inspector)
        self.assertTrue(view._pile_inspector.winfo_exists())
        view._close_inspector()
        self.assertIsNone(view._pile_inspector)

    def test_tip_text_draws_tip_with_bold_font(self):
        view = self.view([entry(1)])
        view._tip_text("Friedhof: 5 Karten")
        tips = view.canvas.find_withtag("tip")
        self.assertTrue(len(tips) >= 1)
        view._untip()
        self.assertEqual(view.canvas.find_withtag("tip"), ())


def mock_db():
    class Info:
        def __init__(self, name):
            self.name = name

    class Db:
        def replay_starts_by_match(self):  # nur für entry(1) (Duell-ID "1") gibt es einen Mitschnitt
            return {"1": 1001}

        def replay_at(self, start):
            return Replay(start, "1", 0, 0, [], [], [{"cards": [], "lp": {}, "turn": "", "phase": "", "t": 0}])

        def konami_passcodes(self):
            return {}

        def infos(self, ids):
            names = {"1": "Elfnote Lucina", "2": "Elfnote Fortuna", "3": "Droll", "5": "Ash Blossom", "9": "Fusion"}
            return {cid: Info(names[cid]) for cid in ids if cid in names}
    return Db()


if __name__ == "__main__":
    unittest.main()
