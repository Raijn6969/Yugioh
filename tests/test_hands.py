"""Starthände: Starthand aus den Schritten eines Replays, Auswertung pro Karte und Brick-Quote, Abschnitt im Winrate-Fenster."""

import os
import tempfile
import unittest
from unittest import mock

import hand_stats
import match_history as mh
import replay_recorder as rr
import starthand
from card_stats import CardStatsDB

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAS_TK = True
except Exception:
    HAS_TK = False

HAND = rr.HAND
# Konami-ID → (Passcode, Name)
CARDS = {1: ("1001", "Starter A"), 2: ("1002", "Starter B"), 3: ("1003", "Ash Blossom"), 4: ("1004", "Brickcard"),
         5: ("1005", "Pot"), 6: ("1006", "Pot"), 7: ("1007", "Unbekannte Karte")}  # 5 und 6: zwei Artworks derselben Karte


def frame(turn="Turn 1", mine=(), theirs=0, my_id=0, t=0, board=()):
    """Ein Schritt: meine Hand (Konami-IDs), die Hand des Gegners (verdeckt) und weitere Karten."""
    cards = [[10 + i, my_id, HAND, i, kid, 1, 1, 0, 0] for i, kid in enumerate(mine)]
    cards += [[50 + i, 1 - my_id, HAND, i, 0, 0, 1, 0, 0] for i in range(theirs)]
    cards += list(board)
    return {"cards": sorted(cards), "lp": {"0": 8000, "1": 8000}, "turn": turn, "phase": "Main 1", "t": t}


class StartingHandTest(unittest.TestCase):
    def test_first_frame_with_exactly_five_known_cards(self):
        frames = [frame("", mine=[1]), frame("Turn 1", mine=[1, 2, 3]),            # Karten werden noch ausgeteilt
                  frame("Turn 1", mine=[4, 1, 3, 2, 2], theirs=5),
                  frame("Turn 1", mine=[1, 3], board=[[99, 0, 0, 0, 2, 1, 1, 1000, 1000]])]   # danach gespielt
        self.assertEqual(starthand.starting_hand(frames, 0), [1, 2, 2, 3, 4])

    def test_second_player_draw_in_turn_two_is_not_part_of_the_hand(self):
        frames = [frame("Turn 1", mine=[1, 2, 3, 4, 5], theirs=5), frame("Turn 1", mine=[1, 2, 3, 4, 5], theirs=3),
                  frame("Turn 2", mine=[1, 2, 3, 4, 5, 6], theirs=3)]
        self.assertEqual(starthand.starting_hand(frames, 0), [1, 2, 3, 4, 5])

    def test_recording_that_starts_late_has_no_reliable_hand(self):
        frames = [frame("Turn 2", mine=[1, 2, 3, 4, 5, 6]), frame("Turn 3", mine=[1, 2, 3, 4, 5])]
        self.assertIsNone(starthand.starting_hand(frames, 0))
        self.assertIsNone(starthand.starting_hand([frame("Zug 3", mine=[1, 2, 3, 4, 5])], 0))   # Sprache des Spiels

    def test_unknown_cards_and_wrong_sizes_are_skipped(self):
        self.assertIsNone(starthand.starting_hand([frame(mine=[1, 2, 3, 4, 0])], 0))      # eine Karte unbekannt
        self.assertIsNone(starthand.starting_hand([frame(mine=[1, 2, 3, 4]), frame(mine=[1, 2, 3, 4, 5, 6])], 0))
        self.assertIsNone(starthand.starting_hand([], 0))
        frames = [frame(mine=[1, 2, 3, 4, 0]), frame(mine=[1, 2, 3, 4, 7])]               # später vollständig bekannt
        self.assertEqual(starthand.starting_hand(frames, 0), [1, 2, 3, 4, 7])

    def test_only_my_hand_counts_whichever_player_number_i_have(self):
        frames = [frame(mine=[1, 2, 3, 4, 5], theirs=5, my_id=1)]
        self.assertEqual(starthand.starting_hand(frames, 1), [1, 2, 3, 4, 5])
        self.assertIsNone(starthand.starting_hand(frames, 0))   # Hand des Gegners: verdeckt (Konami-ID 0)

    def test_hand_after_playing_a_card_is_not_the_starting_hand(self):
        grave, field = [99, 0, 16, 0, 2, 1, 1, 0, 0], [98, 0, 3, 0, 2, 1, 1, 1000, 1000]
        for played in (grave, field, [97, 0, 17, 0, 2, 1, 1, 0, 0], [96, 0, 18, 0, 2, 1, 1, 0, 0]):
            self.assertIsNone(starthand.starting_hand([frame(mine=[1, 2, 3, 4, 5], board=[played])], 0))
        # Später im selben Zug ein echter Fall (z. B. nach einem Neustart des Mitschnitts): zählt
        frames = [frame(mine=[1, 2, 3, 4, 5], board=[grave]), frame(mine=[1, 2, 3, 4, 6])]
        self.assertEqual(starthand.starting_hand(frames, 0), [1, 2, 3, 4, 6])

    def test_deck_extra_deck_and_opponent_cards_do_not_disturb(self):
        mine = [[90, 0, rr.DECK, 0, 0, 0, 1, 0, 0], [91, 0, rr.EXTRA_DECK, 0, 7, 0, 1, 0, 0]]
        theirs = [[80, 1, 2, 0, 3, 1, 1, 1500, 1000], [81, 1, 16, 0, 4, 1, 1, 0, 0]]   # Gegner war in Zug 1 dran
        frames = [frame(mine=[1, 2, 3, 4, 5], theirs=3, board=mine + theirs)]
        self.assertEqual(starthand.starting_hand(frames, 0), [1, 2, 3, 4, 5])
        self.assertIsNone(starthand.starting_hand(frames, 1))   # aus Sicht des Gegners: dessen Hand verdeckt

    def test_turn_number(self):
        self.assertEqual((starthand.turn_number("Turn 12"), starthand.turn_number("Zug 3")), (12, 3))
        self.assertIsNone(starthand.turn_number(""))
        self.assertIsNone(starthand.turn_number(None))


def make_db():
    db = CardStatsDB(os.path.join(tempfile.mkdtemp(), "stats.db"), base_path=None, fetch=lambda ids: {})
    with db._connect() as con:
        con.executemany("INSERT INTO konami VALUES (?, ?, ?)", [(code, kid, name) for kid, (code, name) in CARDS.items()])
    for kid in (1, 2):
        db.set_starter(CARDS[kid][0], True)
    for kid in (3, 4, 5, 6):
        db.set_starter(CARDS[kid][0], False)      # Karte 7 bleibt ohne Einstufung (keine Kartendaten)
    return db


HOUR = 3600


class Duels:
    """Legt Matches mit Replays an, ein Duell pro Stunde (so ist die Zuordnung eindeutig)."""

    def __init__(self, db):
        self.db, self.n = db, 0

    def add(self, hand, win, deck="Ryzeal", first=True, replay=True, turn="Turn 1", base=1_700_000_000, mode=mh.RANKED,
            my_id=0):
        self.n += 1
        at = base + self.n * HOUR
        record = mh.MatchRecord(str(self.n), at, mode, mh.WIN if win else mh.LOSS, first, 4, 1, [], [], [], [], "",
                                "history", True)
        self.db.add_matches([(record, deck, "#main\n", "Snake-Eye", "")])
        if replay:
            info = rr.ReplayInfo(at - 600, f"r{self.n}", my_id, 0, ["Ich", "Er"], [{"main": [1], "extra": []}] * 2)
            frames = [frame("", mine=[], my_id=my_id), frame(turn, mine=hand, theirs=5, my_id=my_id, t=1),
                      frame("Turn 2", mine=list(hand)[:3], theirs=4, my_id=my_id, t=2)]
            self.db.save_replay(info, frames)
        return at


class StorageTest(unittest.TestCase):
    def test_hand_is_stored_when_saving_and_loaded_without_reading_frames(self):
        db = make_db()
        duels = Duels(db)
        duels.add([1, 2, 3, 4, 5], True)
        start = db.replay_starts()[0]
        with db._connect() as con:
            self.assertEqual(con.execute("SELECT hand FROM replay_hands").fetchall(), [("[1, 2, 3, 4, 5]",)])
        with mock.patch("zlib.decompress", side_effect=AssertionError("Schritte gelesen")):
            self.assertEqual(db.starting_hands([start]), {start: [1, 2, 3, 4, 5]})

    def test_older_recordings_are_evaluated_once_and_remembered(self):
        db = make_db()
        duels = Duels(db)
        duels.add([1, 2, 3, 4, 5], True)
        duels.add([1, 2, 3, 4, 5], True, turn="Turn 3")                       # keine verlässliche Starthand
        starts = db.replay_starts()
        with db._connect() as con:
            con.execute("DELETE FROM replay_hands")                           # wie vor dieser Version
        self.assertEqual(db.starting_hands(starts), {starts[0]: [1, 2, 3, 4, 5], starts[1]: None})
        with db._connect() as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM replay_hands").fetchone()[0], 2)
        with mock.patch("zlib.decompress", side_effect=AssertionError("noch einmal gelesen")):
            self.assertEqual(db.starting_hands(starts), {starts[0]: [1, 2, 3, 4, 5], starts[1]: None})
        self.assertEqual(db.starting_hands([12345]), {})                       # unbekannter Mitschnitt

    def test_hand_stored_with_older_rules_is_evaluated_again(self):
        db = make_db()
        duels = Duels(db)
        duels.add([1, 2, 3, 4, 5], True)
        start = db.replay_starts()[0]
        with db._connect() as con:   # wie mit der alten Regel gespeichert: andere Hand, keine Regelversion
            con.execute("UPDATE replay_hands SET hand = '[7, 7, 7, 7, 7]', rules = NULL")
        self.assertEqual(db.starting_hands([start]), {start: [1, 2, 3, 4, 5]})
        with db._connect() as con:
            self.assertEqual(con.execute("SELECT rules FROM replay_hands").fetchone()[0], starthand.HAND_RULES)
        with mock.patch("zlib.decompress", side_effect=AssertionError("noch einmal gelesen")):
            self.assertEqual(db.starting_hands([start]), {start: [1, 2, 3, 4, 5]})

    def test_table_without_rules_column_is_extended(self):
        db = make_db()
        with db._connect() as con:
            con.execute("DROP TABLE replay_hands")
            con.execute("CREATE TABLE replay_hands (start INTEGER PRIMARY KEY, steps INTEGER, hand TEXT)")
            con.execute("INSERT INTO replay_hands VALUES (1, 4, '[1]')")
        db2 = CardStatsDB(db.path, base_path=None, fetch=lambda ids: {})
        with db2._connect() as con:
            self.assertEqual(con.execute("SELECT start, rules FROM replay_hands").fetchall(), [(1, None)])

    def test_longer_recording_replaces_the_stored_hand(self):
        db = make_db()
        duels = Duels(db)
        duels.add([1, 2, 3, 4, 5], True)
        start = db.replay_starts()[0]
        info = rr.ReplayInfo(start, "r1", 0, 0, ["Ich", "Er"], [{"main": [1], "extra": []}] * 2)
        db.save_replay(info, [frame("Turn 1", mine=[7, 2, 3, 4, 5], t=i) for i in range(5)])
        self.assertEqual(db.starting_hands([start]), {start: [2, 3, 4, 5, 7]})

    def test_each_replay_belongs_to_one_match(self):
        db = make_db()
        duels = Duels(db)
        duels.add([1, 2, 3, 4, 5], True)
        duels.add([1, 2, 3, 4, 5], True, replay=False)
        self.assertEqual(list(db.replay_starts_by_match()), ["1"])


class StatsTest(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.duels = Duels(self.db)
        # 6 Duelle mit "Ryzeal": Starter A in 4 Händen (3 Siege), Ash in 3 (1 Sieg), Pot (2 Artworks) in 3 (zusammen)
        self.duels.add([1, 2, 3, 4, 4], True)
        self.duels.add([1, 3, 4, 4, 5], True)
        self.duels.add([1, 4, 4, 5, 6], False, first=False)    # Pot zweimal (zwei Artworks) = ein Duell
        self.duels.add([1, 3, 4, 4, 4], True)
        self.duels.add([3, 4, 4, 4, 4], False)                 # Brick: kein Starter
        self.duels.add([4, 4, 4, 5, 3], False, first=False)    # Brick

    def card(self, stats, name):
        return next(c for c in stats.cards if c.name == name)

    def test_cards_with_win_rate_with_and_without(self):
        stats = hand_stats.analyse(self.db, "Ryzeal")
        self.assertEqual((stats.deck, stats.replays, stats.duels, stats.needs_deck), ("Ryzeal", 6, 6, False))
        a = self.card(stats, "Starter A")
        self.assertEqual((a.hands, a.with_card.matches, a.with_card.wins, a.without_card.matches, a.without_card.wins),
                         (4, 4, 3, 2, 0))
        ash = self.card(stats, "Ash Blossom")
        self.assertEqual((ash.hands, ash.with_card.wins, ash.without_card.matches), (5, 3, 1))
        pot = self.card(stats, "Pot")
        self.assertEqual((pot.hands, pot.with_card.matches, pot.with_card.wins), (3, 3, 1))   # Artworks zusammen, Duell einmal
        self.assertEqual((pot.with_card.second, pot.with_card.second_wins), (2, 0))
        self.assertEqual([c.name for c in stats.cards][:2], ["Brickcard", "Ash Blossom"])      # häufigste zuerst

    def test_only_cards_with_at_least_three_hands(self):
        names = [c.name for c in hand_stats.analyse(self.db, "Ryzeal").cards]
        self.assertNotIn("Starter B", names)          # nur in einer Hand
        self.assertEqual(hand_stats.MIN_DUELS, 3)

    def test_brick_rate_uses_the_starter_rating(self):
        brick = hand_stats.analyse(self.db, "Ryzeal").brick
        self.assertEqual((brick.hands, brick.bricks), (6, 2))
        self.assertEqual((brick.brick.matches, brick.brick.wins, brick.playable.matches, brick.playable.wins), (2, 0, 4, 3))
        # Starter-Einstufung ändern (wie im Deck-Fenster): Ash als Starter → nur noch die Hand ohne ihn ist Brick
        self.db.set_starter("1003", True)
        self.assertEqual(hand_stats.analyse(self.db, "Ryzeal").brick.bricks, 0)
        self.db.set_starter("1003", False)

    def test_hands_with_unrated_cards_do_not_count_for_brick(self):
        self.duels.add([4, 4, 4, 4, 7], False)         # Karte 7 ohne Einstufung → unklar, weder Brick noch spielbar
        brick = hand_stats.analyse(self.db, "Ryzeal").brick
        self.assertEqual((brick.hands, brick.bricks), (6, 2))
        self.assertEqual(hand_stats.analyse(self.db, "Ryzeal").duels, 7)

    def test_replays_without_reliable_hand_and_matches_without_replay_are_skipped(self):
        self.duels.add([1, 2, 3, 4, 5], True, turn="Turn 4")     # Mitschnitt, aber keine verlässliche Starthand
        self.duels.add([1, 2, 3, 4, 5], True, replay=False)      # gar kein Mitschnitt
        stats = hand_stats.analyse(self.db, "Ryzeal")
        self.assertEqual((stats.replays, stats.duels), (7, 6))

    def test_filters_deck_mode_and_period(self):
        self.duels.add([1, 2, 3, 4, 5], True, deck="Snake-Eye", mode=mh.FREE)
        self.assertEqual(hand_stats.analyse(self.db, "Ryzeal", mh.RANKED).duels, 6)
        self.assertEqual(hand_stats.analyse(self.db, "Snake-Eye", mh.RANKED).duels, 0)
        self.assertEqual(hand_stats.analyse(self.db, "Snake-Eye", None).duels, 1)
        self.assertEqual(hand_stats.analyse(self.db, "Ryzeal", None, since=1_700_000_000 + 4 * HOUR + 1).duels, 2)
        self.assertEqual(hand_stats.analyse(self.db, "Gibt es nicht").duels, 0)

    def test_without_selected_deck_the_only_deck_is_used_else_a_choice_is_needed(self):
        stats = hand_stats.analyse(self.db, None, mh.RANKED)
        self.assertEqual((stats.deck, stats.duels, stats.needs_deck), ("Ryzeal", 6, False))
        self.duels.add([1, 2, 3, 4, 5], True, deck="Snake-Eye")
        stats = hand_stats.analyse(self.db, None, mh.RANKED)
        self.assertTrue(stats.needs_deck)
        self.assertEqual((stats.cards, stats.brick, stats.decks), ([], None, ["Ryzeal", "Snake-Eye"]))

    def test_no_data(self):
        empty = hand_stats.analyse(make_db(), None, None)
        self.assertEqual((empty.duels, empty.replays, empty.cards, empty.brick), (0, 0, [], None))

    def test_second_player_number_works_too(self):
        duels = Duels(make_db())
        for _ in range(3):
            duels.add([1, 2, 3, 4, 5], True, my_id=1)
        stats = hand_stats.analyse(duels.db, "Ryzeal")
        self.assertEqual(stats.duels, 3)
        self.assertEqual(self.card(stats, "Starter A").hands, 3)


@unittest.skipUnless(HAS_TK, "kein Tk")
class SectionTest(unittest.TestCase):
    def setUp(self):
        from winrate_panel import WinratePanel
        self.WinratePanel = WinratePanel
        self.root = tk.Tk()
        self.root.withdraw()
        self.db = make_db()
        self.duels = Duels(self.db)
        self.addCleanup(self.root.destroy)

    def open(self):
        panel = self.WinratePanel(self.root, self.db, {"WINRATE_PERIOD": "all"}, lambda: None,
                                  on_open=lambda *a: None, on_close=lambda: None, tracking=True)
        self.addCleanup(panel.close)   # vor dem Zerstören von root
        return panel

    def texts(self, widget):
        result = []
        for child in widget.winfo_children():
            if isinstance(child, tk.Label):
                result.append(child.cget("text"))
            result += self.texts(child)
        return result

    def test_hint_while_there_are_too_few_replays(self):
        self.duels.add([1, 2, 3, 4, 5], True, replay=False)
        panel = self.open()
        texts = self.texts(panel.inner)
        self.assertTrue(any(t.startswith("STARTHÄNDE") for t in texts))
        self.assertTrue(any("Noch zu wenige Replays: 0 von 3" in t for t in texts), texts)
        self.duels.add([1, 2, 3, 4, 5], True)
        self.duels.add([1, 2, 3, 4, 5], False)
        panel.refresh()
        self.assertTrue(any("Noch zu wenige Replays: 2 von 3" in t for t in self.texts(panel.inner)))

    def test_cards_and_brick_quote_are_listed(self):
        for hand, win in (([1, 2, 3, 4, 4], True), ([1, 3, 4, 4, 5], True), ([1, 4, 4, 5, 6], False),
                          ([4, 4, 4, 4, 4], False)):
            self.duels.add(hand, win)
        panel = self.open()
        texts = self.texts(panel.inner)
        self.assertIn("STARTHÄNDE · Ryzeal", texts)
        self.assertTrue(any(t.startswith("4 Starthände aus 4 Replay(s)") for t in texts), texts)
        self.assertIn("Brick-Quote 25 %", texts)
        self.assertIn("1 von 4 Händen ohne Starter · Win 0 % bei Brick, sonst 67 %", texts)
        self.assertIn("Starter A", texts)
        self.assertIn("3 von 4 Händen · Win 67 % (ohne 0 %)", texts)
        self.assertIn("Brickcard", texts)
        self.assertNotIn("Starter B", texts)       # nur in einer Hand

    def test_several_decks_ask_for_a_choice_and_clicking_a_deck_evaluates_it(self):
        for deck in ("Ryzeal", "Snake-Eye"):
            for _ in range(3):
                self.duels.add([1, 2, 3, 4, 5], True, deck=deck)
        panel = self.open()
        texts = self.texts(panel.inner)
        self.assertTrue(any("Starthände aus 2 Decks" in t for t in texts), texts)
        panel.select("Snake-Eye")
        texts = self.texts(panel.inner)
        self.assertIn("STARTHÄNDE · Snake-Eye", texts)
        self.assertTrue(any(t.startswith("3 Starthände aus 3 Replay(s)") for t in texts))

    def test_no_section_without_matches_and_errors_do_not_break_the_window(self):
        panel = self.open()
        self.assertFalse(any(t.startswith("STARTHÄNDE") for t in self.texts(panel.inner)))
        self.duels.add([1, 2, 3, 4, 5], True)
        with mock.patch("hand_stats.analyse", side_effect=Exception("database is locked")):
            panel.refresh()
        texts = self.texts(panel.inner)
        self.assertTrue(any("Nicht lesbar" in t for t in texts))
        self.assertIn("LETZTE MATCHES", texts)


if __name__ == "__main__":
    unittest.main()
