"""Matchup: Störkarten der Gegner und die eigene Winrate gegen Decks mit bzw. ohne sie."""

import os
import tempfile
import time
import unittest
from unittest import mock

import match_history as mh
import matchup_analysis as ma
import replay_recorder as rr
from card_stats import CardInfo, CardStatsDB

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAS_TK = True
except Exception:
    HAS_TK = False

ASH = CardInfo("14558127", "Ash Blossom & Joyous Spring", "Effect Monster", "effect", "Zombie", 3, "",
               "When a card or effect is activated that includes any of these effects (Quick Effect): You can discard "
               "this card; negate that effect.")
ASH_ALT = ASH._replace(cid="14558128")  # Alternativ-Artwork
IMPERM = CardInfo("10045474", "Infinite Impermanence", "Trap Card", "normal", "Normal", None, "",
                  "Target 1 face-up monster your opponent controls; negate its effects (until the end of this turn).")
CROWN = CardInfo("98829635", "Forbidden Crown", "Spell Card", "spell", "Quick-Play", None, "",
                 "Apply these effects to 1 face-up monster on the field: Negate its effects.")
DROPLET = CardInfo("24299458", "Forbidden Droplet", "Spell Card", "spell", "Quick-Play", None, "",
                   "Send any number of other cards from your hand and/or field to the GY; those cards' effects are "
                   "negated.")
POT = CardInfo("55144522", "Pot of Greed", "Spell Card", "spell", "Normal", None, "", "Draw 2 cards.")
GOBLIN = CardInfo("11111111", "Goblin", "Normal Monster", "normal", "Fiend", 4, "", "A goblin.")


KONAMI = {ASH: 1001, ASH_ALT: 1001, IMPERM: 1002, CROWN: 1003, DROPLET: 1004, POT: 1005, GOBLIN: 1006}


def make_db():
    db = CardStatsDB(os.path.join(tempfile.mkdtemp(), "stats.db"), base_path=None, fetch=lambda ids: {})
    with db._connect() as con:
        con.executemany("INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [(*info, 0) for info in (ASH, ASH_ALT, IMPERM, CROWN, DROPLET, POT, GOBLIN)])
        # Konami-IDs (Speicher von Master Duel) → Passcodes: Replay-Mitschnitte kennen die Karten nur so
        con.executemany("INSERT INTO konami VALUES (?, ?, ?)", [(info.cid, kid, info.name)
                                                                for info, kid in KONAMI.items()])
    return db


def kids(*cards):
    return [KONAMI[c] for c in cards]


def add_replay(db, start, opp_main, my_id=0, opp_extra=(), own_main=(1,)):
    """Mitschnitt, dessen Gegner (der Spieler, der nicht my_id ist) die Karten opp_main spielt."""
    decks = [{"main": list(own_main), "extra": []}, {"main": list(opp_main), "extra": list(opp_extra)}]
    if my_id == 1:
        decks.reverse()
    info = rr.ReplayInfo(start, f"replay{start}", my_id, 0, ["Ich", "Er"], decks)
    db.save_replay(info, [{"cards": [], "lp": {}, "turn": "", "phase": "", "t": i} for i in range(4)])


def add(db, did, result, first, opp_cards, deck="Ryzeal", mode=mh.RANKED, at=None):
    record = mh.MatchRecord(str(did), at or did, mode, result, first, 2, 1, [], [], [], [], "", "history")
    code = "#main\n" + "\n".join(c.cid for c in opp_cards) + "\n#extra\n!side\n" if opp_cards else ""
    db.add_matches([(record, deck, "#main\n", "Gegner" if opp_cards else "", code)])


class DisruptionTest(unittest.TestCase):
    def test_kinds(self):
        self.assertEqual(ma.disruption_kind(ASH), "Handtrap")
        self.assertEqual(ma.disruption_kind(IMPERM), "Falle")
        self.assertEqual(ma.disruption_kind(CROWN), "Schnellzauber")
        self.assertEqual(ma.disruption_kind(DROPLET), "Schnellzauber")  # "negated"
        self.assertIsNone(ma.disruption_kind(POT))
        self.assertIsNone(ma.disruption_kind(GOBLIN))
        self.assertIsNone(ma.disruption_kind(None))


class AnalyseTest(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        add(self.db, 1, mh.WIN, True, [ASH, ASH, IMPERM, POT])
        add(self.db, 2, mh.LOSS, False, [ASH_ALT, IMPERM])       # Alternativ-Artwork zählt als Ash
        add(self.db, 3, mh.WIN, False, [POT, GOBLIN])
        add(self.db, 4, mh.LOSS, True, [CROWN])
        add(self.db, 5, mh.WIN, None, [])                          # live erfasst: Gegner-Deck unbekannt
        add(self.db, 6, mh.WIN, True, [ASH], deck="Snake-Eye")
        add(self.db, 7, mh.LOSS, True, [ASH], mode=mh.FREE)

    def test_threats_with_and_without(self):
        result = ma.analyse(self.db, "Ryzeal", mh.RANKED)
        self.assertEqual((result.opponents, result.unknown), (4, 1))
        self.assertEqual((result.overall.wins, result.overall.losses), (2, 2))
        self.assertEqual([(t.name, t.decks) for t in result.threats],
                         [("Ash Blossom & Joyous Spring", 2), ("Infinite Impermanence", 2), ("Forbidden Crown", 1)])
        ash = result.threats[0]
        self.assertEqual((ash.kind, ash.share, ash.passcode), ("Handtrap", 0.5, "14558127"))
        self.assertEqual((ash.against.wins, ash.against.losses, ash.against.first, ash.against.first_wins,
                          ash.against.second, ash.against.second_wins), (1, 1, 1, 1, 1, 0))
        self.assertEqual((ash.without.wins, ash.without.losses), (1, 1))
        self.assertEqual(result.per_deck, 5 / 4)

    def test_all_decks_and_modes(self):
        self.assertEqual(ma.analyse(self.db, None, mh.RANKED).opponents, 5)
        self.assertEqual(ma.analyse(self.db).opponents, 6)
        self.assertEqual(ma.analyse(self.db, "Gibt es nicht").opponents, 0)


class ReplayDecksTest(unittest.TestCase):
    """Wo es einen Mitschnitt gibt, zählt dessen vollständige Gegner-Liste – jedes Duell nur einmal."""

    def setUp(self):
        self.db = make_db()

    def test_replay_gives_a_deck_to_a_live_match_without_one(self):
        add(self.db, 1, mh.WIN, True, [], at=100000)              # live erfasst: Gegner-Deck unbekannt
        before = ma.analyse(self.db)
        self.assertEqual((before.opponents, before.unknown, before.from_replays), (0, 1, 0))
        add_replay(self.db, 100000 - 600, kids(ASH, IMPERM, POT))  # 10 Min. vor dem Ergebnis gestartet
        result = ma.analyse(self.db)
        self.assertEqual((result.opponents, result.unknown, result.from_replays), (1, 0, 1))
        self.assertEqual([t.name for t in result.threats], ["Ash Blossom & Joyous Spring", "Infinite Impermanence"])
        self.assertEqual((result.overall.wins, result.overall.matches), (1, 1))

    def test_replay_list_replaces_match_history_list_without_double_count(self):
        add(self.db, 1, mh.WIN, True, [CROWN], at=100000)         # Match History kennt nur Forbidden Crown
        add_replay(self.db, 100000 - 3, kids(ASH, DROPLET))       # Match History: Startzeit ± wenige Sekunden
        result = ma.analyse(self.db)
        self.assertEqual((result.opponents, result.unknown, result.from_replays), (1, 0, 1))   # ein Duell, ein Deck
        self.assertEqual(sorted(t.name for t in result.threats), ["Ash Blossom & Joyous Spring", "Forbidden Droplet"])
        self.assertEqual(result.per_deck, 2.0)

    def test_without_replay_the_match_history_list_counts_as_before(self):
        add(self.db, 1, mh.WIN, True, [CROWN], at=100000)
        add(self.db, 2, mh.LOSS, False, [IMPERM], at=200000)
        add_replay(self.db, 200000 - 60, kids(ASH))
        result = ma.analyse(self.db)
        self.assertEqual((result.opponents, result.from_replays), (2, 1))
        self.assertEqual(sorted(t.name for t in result.threats), ["Ash Blossom & Joyous Spring", "Forbidden Crown"])

    def test_one_replay_belongs_to_one_match_only(self):
        add(self.db, 1, mh.WIN, True, [], at=100000)
        add(self.db, 2, mh.LOSS, True, [], at=100000 + 900)       # zweites Duell kurz danach, ohne eigenen Mitschnitt
        add_replay(self.db, 100000 - 500, kids(ASH))              # gehört zum ersten
        result = ma.analyse(self.db)
        self.assertEqual((result.opponents, result.unknown, result.from_replays), (1, 1, 1))
        self.assertEqual((result.overall.wins, result.overall.losses), (1, 0))
        self.assertEqual(self.db.replay_opponent_decks(), {"1": {"main": kids(ASH), "extra": []}})

    def test_opponent_is_the_other_player(self):
        add(self.db, 1, mh.WIN, True, [], at=100000)
        add_replay(self.db, 100000 - 60, kids(IMPERM), my_id=1, own_main=kids(ASH))   # ich war Spieler 1
        result = ma.analyse(self.db)
        self.assertEqual([t.name for t in result.threats], ["Infinite Impermanence"])

    def test_extra_deck_is_part_of_the_list_and_unknown_cards_fall_back(self):
        add(self.db, 1, mh.WIN, True, [IMPERM], at=100000)
        add_replay(self.db, 100000 - 60, [999999991, 999999992], opp_extra=[999999993])   # Konami-IDs unbekannt
        result = ma.analyse(self.db)
        self.assertEqual((result.opponents, result.from_replays), (1, 0))   # Match-History-Liste bleibt
        self.assertEqual([t.name for t in result.threats], ["Infinite Impermanence"])
        add(self.db, 2, mh.WIN, True, [], at=200000)
        add_replay(self.db, 200000 - 30, kids(GOBLIN), opp_extra=kids(ASH))
        self.assertEqual([t.name for t in ma.analyse(self.db).threats],
                         ["Ash Blossom & Joyous Spring", "Infinite Impermanence"])

    def test_filters_still_apply_and_old_databases_work(self):
        add(self.db, 1, mh.WIN, True, [], at=100000, deck="Snake-Eye")
        add_replay(self.db, 100000 - 60, kids(ASH))
        self.assertEqual(ma.analyse(self.db, "Ryzeal").opponents, 0)
        self.assertEqual(ma.analyse(self.db, "Snake-Eye").from_replays, 1)
        broken = mock.Mock(wraps=self.db)
        broken.replay_opponent_decks.side_effect = Exception("no such table: replays")
        self.assertEqual(ma.analyse(broken, "Snake-Eye").from_replays, 0)   # fällt auf die Match History zurück


def wait_for_matchup(root, panel, timeout=5.0):
    end = time.time() + timeout
    root.update()
    while panel._poll_job is not None and time.time() < end:
        root.update()
        time.sleep(0.01)
    root.update()


@unittest.skipUnless(HAS_TK, "kein Tk")
class MatchupPanelTest(unittest.TestCase):
    def setUp(self):
        from matchup_panel import MatchupPanel
        self.root = tk.Tk()
        self.root.withdraw()
        self.db = make_db()
        add(self.db, 1, mh.WIN, True, [ASH, IMPERM], at=time.time())
        add(self.db, 2, mh.LOSS, False, [ASH], at=time.time())
        add(self.db, 3, mh.WIN, True, [POT], at=time.time())
        add(self.db, 4, mh.WIN, None, [], at=time.time())
        add(self.db, 5, mh.LOSS, True, [IMPERM], deck="Snake-Eye", at=time.time())
        self.settings = {}
        self.patch = mock.patch("card_images.download", lambda *a, **k: 0)  # keine Bilder aus dem Internet
        self.patch.start()
        self.addCleanup(self.patch.stop)  # auch wenn setUp scheitert
        self.panel = MatchupPanel(self.root, self.db, self.settings, lambda: None, on_close=lambda: None,
                                  deck_name="Ryzeal", image_folder=tempfile.mkdtemp())
        self.wait()

    def tearDown(self):
        self.panel.close()
        self.root.destroy()

    def wait(self):
        """Bis die Auswertung im Hintergrund fertig und angezeigt ist."""
        wait_for_matchup(self.root, self.panel)

    def test_opens_at_once_and_evaluates_in_background(self):
        from matchup_panel import MatchupPanel
        with mock.patch("matchup_analysis.analyse", side_effect=lambda *a: time.sleep(0.3) or
                        ma.Matchup(0, 0, ma.Record(), [], 0.0)):
            start = time.perf_counter()
            panel = MatchupPanel(self.root, self.db, {}, lambda: None, on_close=lambda: None)
            self.assertLess(time.perf_counter() - start, 0.25)  # Fenster wartet nicht auf die Auswertung
            self.assertIn("Werte die Duelle aus", panel.info_label.cget("text"))
            wait_for_matchup(self.root, panel)
        self.assertIn("Noch keine Gegner-Decks", panel.info_label.cget("text"))
        panel.close()

    def test_deck_without_data_switches_to_all_decks_with_note(self):
        self.panel.close()
        from matchup_panel import MatchupPanel
        panel = MatchupPanel(self.root, self.db, {}, lambda: None, on_close=lambda: None, deck_name="Ohne Daten",
                             image_folder=tempfile.mkdtemp())
        wait_for_matchup(self.root, panel)
        self.assertFalse(panel.only_deck)
        text = panel.info_label.cget("text")
        self.assertIn("Noch keine Gegner-Decks mit „Ohne Daten“", text)
        self.assertIn("Gegner-Deck", text.splitlines()[1])
        self.assertTrue(panel.tiles_frame.winfo_manager())  # Kacheln der Alle-Decks-Ansicht da
        panel.close()
        self.panel = MatchupPanel(self.root, self.db, {}, lambda: None, on_close=lambda: None)

    def texts(self, widget):
        result = []
        for child in widget.winfo_children():
            if isinstance(child, tk.Label):
                result.append(child.cget("text"))
            result += self.texts(child)
        return result

    def test_threats_of_this_deck_then_all_decks(self):
        self.assertEqual({k: v.cget("text") for k, v in self.panel.tiles.items()},
                         {"opponents": "3", "per_deck": "1,0", "winrate": "67 %"})
        self.assertIn("1 weitere ohne Gegner-Deck", self.panel.info_label.cget("text"))
        texts = self.panel.grid.texts()
        self.assertIn("Ash Blossom & Joyous Spring", texts)  # ohne Bild: Name als Platzhalter
        self.assertIn("67 %", texts)       # Ash in 2 von 3 Gegner-Decks
        self.assertIn("Win 50 %", texts)
        self.panel.filter_buttons["decks"].invoke()
        self.wait()
        self.assertEqual(self.panel.tiles["opponents"].cget("text"), "4")

    def test_details_on_hover(self):
        self.assertTrue(self.panel.grid.canvas.tag_bind("tile0", "<Enter>"))  # Karte reagiert aufs Darüberfahren
        self.panel._show_tip(mock.Mock(x_root=10, y_root=10), self.panel.matchup.threats[0])
        self.root.update()
        text = "\n".join(self.texts(self.panel._tip))
        self.assertIn("Handtrap · in 2 von 3 Gegner-Decks", text)
        self.assertIn("Gegen Decks mit der Karte: 50 % · 1–1", text)
        self.assertIn("als Erster 100 % · als Zweiter 0 %", text)
        self.assertIn("Ohne die Karte: 100 % · 1–0", text)
        self.panel._hide_tip()
        self.assertIsNone(self.panel._tip)

    def test_hint_how_many_opponent_decks_come_from_replays(self):
        self.assertNotIn("Replays", self.panel.info_label.cget("text"))
        for did, ago, cards, expected in ((6, 5000, kids(ASH, POT), "4 Matches"), (7, 9000, kids(IMPERM), "5 Matches")):
            add(self.db, did, mh.WIN, True, [], at=time.time() - ago)    # live erfasst, ohne Gegner-Deck
            add_replay(self.db, int(time.time()) - ago - 120, cards)
            self.panel.refresh()
            self.wait()
            text = self.panel.info_label.cget("text")
            self.assertIn(expected, text)
            self.assertIn("1 Gegnerdeck vollständig aus Replays" if did == 6 else "2 Gegnerdecks vollständig aus "
                          "Replays", text)

    def test_no_opponent_decks_yet(self):
        self.panel.deck_name, self.panel.db = "Neu", make_db()  # auch alle Decks ohne Daten
        self.panel.refresh()
        self.wait()
        self.assertIn("Match History", self.panel.info_label.cget("text"))
        self.assertEqual((self.panel.tiles_frame.winfo_manager(), self.panel.footer.winfo_manager()), ("", ""))
        self.panel.deck_name, self.panel.db, self.panel.only_deck = "Ryzeal", self.db, True
        self.panel.refresh()
        self.wait()
        self.assertEqual((self.panel.tiles_frame.winfo_manager(), self.panel.footer.winfo_manager()),
                         ("pack", "pack"))


if __name__ == "__main__":
    unittest.main()
