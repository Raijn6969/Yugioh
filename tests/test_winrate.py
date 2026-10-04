"""Winrate-Tracker: Match History aus dem Speicher lesen, Matches speichern, Winrate anzeigen."""

import os
import struct
import tempfile
import time
import unittest

import match_history as mh
import md_memory
from card_stats import CardInfo, CardStatsDB
from tests.test_memory import FakeReader
from winrate_panel import summary

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAS_TK = True
except Exception:
    HAS_TK = False

# Konami-ID → (Passcode, Name, Archetyp, Rahmen)
CARDS = {
    101: ("1001", "Ryzeal Detonator", "Ryzeal", "effect"),
    102: ("1002", "Mitsurugi no Saji", "Mitsurugi", "effect"),
    103: ("1003", "Ash Blossom & Joyous Spring", "", "effect"),
    104: ("1004", "Ryzeal Duo Drive", "Ryzeal", "xyz"),
    201: ("2001", "Snake-Eye Ash", "Snake-Eye", "effect"),
    202: ("2002", "Snake-Eyes Doomed Dragon", "Snake-Eye", "fusion"),
}
MY_MAIN = [101, 101, 101, 102, 102, 102, 103]
MY_EXTRA = [104]
OPP_MAIN = [201, 201, 201]
OPP_EXTRA = [202]


def deck(main, extra):
    return {"Main": {"CardIds": main, "Rare": [1] * len(main)}, "Extra": {"CardIds": extra, "Rare": [1] * len(extra)}}


def entry(did, time_, res, myid=1, first_player=1, turn=2, mode=3, finish=1, **extra):
    decks = [deck(OPP_MAIN, OPP_EXTRA), deck(MY_MAIN, MY_EXTRA)]
    if myid == 0:
        decks.reverse()
    return {"mode": mode, "did": did, "time": time_, "myid": myid, "deck": decks, "res": res, "turn": turn,
            "finish": finish, "first_player": first_player, "invalid": False, **extra}


def make_db():
    db = CardStatsDB(os.path.join(tempfile.mkdtemp(), "stats.db"), base_path=None, fetch=lambda ids: {})
    with db._connect() as con:
        con.executemany("INSERT INTO konami VALUES (?, ?, ?)",
                        [(code, kid, name) for kid, (code, name, _, _) in CARDS.items()])
        con.executemany("INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        [(*CardInfo(code, name, "Effect Monster", frame, "Warrior", 4, arch, ""), 0)
                         for code, name, arch, frame in CARDS.values()])
    return db


class ParseHistoryTest(unittest.TestCase):
    def test_first_or_second_and_decks_of_both_players(self):
        data = {"58": {"1": entry(7001, 300, mh.WIN, myid=1, first_player=1),
                       "2": entry(7000, 200, mh.LOSS, myid=0, first_player=1, finish=mh.FINISH_SURRENDER)},
                "replay_limit_ts": 123}
        first, second = mh.parse_history(data)  # ältestes zuerst
        self.assertEqual((second.did, second.result, second.first, second.turns), ("7001", mh.WIN, True, 2))
        self.assertEqual((first.did, first.result, first.first, first.finish), ("7000", mh.LOSS, False, 4))
        # Eigenes Deck je nach myid – auch wenn man Spieler 0 war
        self.assertEqual((first.my_main, first.my_extra, first.opp_main), (MY_MAIN, MY_EXTRA, OPP_MAIN))
        self.assertEqual((second.my_main, second.opp_extra), (MY_MAIN, OPP_EXTRA))

    def test_same_duel_in_two_groups_once_and_broken_entries_skipped(self):
        data = {"58": {"1": entry(1, 100, mh.WIN)}, "Cup": {"1": entry(1, 100, mh.WIN), "2": entry(2, 110, mh.WIN,
                invalid=True), "3": {"did": 3, "myid": 5}, "4": "kaputt"}}
        self.assertEqual([r.did for r in mh.parse_history(data)], ["1"])
        self.assertEqual(mh.parse_history(None), [])

    def test_unknown_first_player(self):
        record, = mh.parse_history({"1": {"1": entry(1, 100, mh.WIN, first_player=None)}})
        self.assertIsNone(record.first)


class StoreMatchesTest(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        data = {"58": {"1": entry(1, 100, mh.WIN, first_player=1), "2": entry(2, 200, mh.LOSS, first_player=0),
                       "3": entry(3, 300, mh.WIN, first_player=0), "4": entry(4, 400, mh.WIN, mode=1)}}
        self.records = mh.parse_history(data)

    def test_stores_new_matches_once_with_deck_names_and_codes(self):
        self.assertEqual(len(mh.store_matches(self.db, self.records)), 4)
        self.assertEqual(mh.store_matches(self.db, self.records), [])  # Match History noch einmal gelesen
        latest = self.db.matches()[0]
        self.assertEqual((latest.did, latest.deck_name, latest.opp_name), ("4", "Ryzeal / Mitsurugi", "Snake-Eye"))
        self.assertIn("#main\n1001\n1001\n1001\n1002", latest.deck_code)
        self.assertIn("#extra\n1004\n!side", latest.deck_code)

    def test_win_rates_overall_first_second_and_per_deck(self):
        mh.store_matches(self.db, self.records)
        ranked = self.db.win_stats(mode=mh.RANKED)
        self.assertEqual((ranked.matches, ranked.wins, ranked.losses), (3, 2, 1))
        self.assertEqual((ranked.first, ranked.first_wins, ranked.second, ranked.second_wins), (1, 1, 2, 1))
        self.assertEqual(summary(ranked), "67 % (2–1) · Erster 100 % · Zweiter 50 %")
        self.assertEqual(self.db.win_stats().matches, 4)  # alle Modi
        decks = self.db.deck_win_stats(mh.RANKED)
        self.assertEqual([(d.deck_name, d.stats.matches) for d in decks], [("Ryzeal / Mitsurugi", 3)])
        self.assertEqual(decks[0].stats.last_played, 300)
        self.assertEqual(self.db.win_stats("Snake-Eye").matches, 0)


class OldTableTest(unittest.TestCase):
    def test_table_of_first_version_is_extended(self):
        import sqlite3
        path = os.path.join(tempfile.mkdtemp(), "stats.db")
        with sqlite3.connect(path) as con:
            con.execute("""CREATE TABLE matches (did TEXT PRIMARY KEY, played_at REAL, mode INTEGER, result INTEGER,
                           first INTEGER, turns INTEGER, finish INTEGER, deck_name TEXT, deck_code TEXT,
                           opp_name TEXT, opp_code TEXT, added_at REAL)""")
            con.execute("INSERT INTO matches VALUES ('1', 100, 3, 1, 1, 2, 1, 'Ryzeal', '#main', 'Snake-Eye', '', 5)")
        con.close()
        db = CardStatsDB(path, base_path=None, fetch=lambda ids: {})
        self.assertEqual(db.match_sources(["1"]), {"1": "history"})
        record = mh.MatchRecord("2", 200, 3, mh.LOSS, False, 0, 0, [], [], [], [], "Ryzeal Mitsu", "live")
        db.add_matches([(record, "Ryzeal", "#main", "", "")])
        latest, old = db.matches()
        self.assertEqual((latest.did, latest.md_deck, latest.first), ("2", "Ryzeal Mitsu", False))
        self.assertEqual((old.did, old.md_deck, old.opp_name), ("1", "", "Snake-Eye"))


class FakeIl2Cpp:
    NAMES = {0x10: "Dictionary`2", 0x20: "List`1", 0x30: "String", 0x40: "Int64", 0x50: "Boolean", 0x60: "ClientWork"}
    FIELDS = {0x10: {"_entries": 0x18, "_count": 0x20}, 0x20: {"_items": 0x10, "_size": 0x18}, 0x60: {"s_data": 0}}

    def __init__(self, reader):
        self.r = reader

    def class_name(self, klass):
        return self.NAMES[klass]

    def fields(self, klass):
        return self.FIELDS[klass]

    def field(self, obj, name):
        return self.FIELDS[self.r.u64(obj)][name]


class FakeHeap:
    """Legt .NET-Objekte (Dictionary<string, object>, List, String, Int64, Boolean) im FakeReader an."""

    def __init__(self, reader):
        self.r, self.next = reader, 0x10000

    def alloc(self, data: bytes) -> int:
        addr, self.next = self.next, self.next + (len(data) + 0x1F) // 0x10 * 0x10 + 0x10
        self.r.put(addr, data)
        return addr

    def obj(self, value) -> int:
        if isinstance(value, bool):
            return self.alloc(struct.pack("<QQ?", 0x50, 0, value))
        if isinstance(value, int):
            return self.alloc(struct.pack("<QQq", 0x40, 0, value))
        if isinstance(value, str):
            return self.alloc(struct.pack("<QQi", 0x30, 0, len(value)) + value.encode("utf-16-le"))
        if isinstance(value, list):
            items = self.alloc(bytes(md_memory.ARRAY_DATA) + b"".join(struct.pack("<Q", self.obj(v)) for v in value))
            return self.alloc(struct.pack("<QQQi", 0x20, 0, items, len(value)))
        if isinstance(value, dict):
            entries = [(0, -1, self.obj(k), self.obj(v)) for k, v in value.items()]
            entries.insert(1, (-1, -1, 0, 0))  # gelöschter Eintrag
            data = bytes(md_memory.ARRAY_DATA) + b"".join(struct.pack("<iiQQ", *e) for e in entries)
            # Klasse, Monitor, _buckets (+0x10), _entries (+0x18), _count (+0x20)
            return self.alloc(struct.pack("<QQQQi", 0x10, 0, 0, self.alloc(data), len(entries)))
        raise TypeError(value)


class ClientWorkTest(unittest.TestCase):
    def test_reads_nested_server_data(self):
        r = FakeReader()
        heap = FakeHeap(r)
        history = {"58": {"1": {"did": 7709189150730376408, "res": 1, "invalid": False, "date": "10 4 2026",
                                "deck": [{"Main": {"CardIds": [9279, 12950]}}]}}}
        root = heap.obj({"DuelHistory": history, "User": {"name": "Raijn"}})
        r.put(0x60, struct.pack("<Q", 0x60))                                   # Klasse ClientWork
        r.put(0x60 + md_memory.CLASS_STATIC_FIELDS, struct.pack("<Q", 0x70))   # → statische Felder
        r.put(0x70, struct.pack("<Q", root))                                   # s_data
        memory = md_memory.DeckEditorMemory(r, FakeIl2Cpp(r))
        memory.client_work_klass = 0x60
        self.assertEqual(memory.client_work("DuelHistory"), history)
        self.assertEqual(memory.client_work("User", "name"), "Raijn")
        self.assertIsNone(memory.client_work("Fehlt"))


class CoinTossTest(unittest.TestCase):
    def test_who_goes_first(self):
        # Münzwurf verloren (Gegner = Spieler 0 wählt), Gegner fängt an → Zweiter
        self.assertFalse(mh.first_from_coin_toss({"myid": 1, "choice": 0, "pvp_choice": {"choice": 1}}))
        self.assertTrue(mh.first_from_coin_toss({"myid": 1, "choice": 1, "pvp_choice": {"choice": 1}}))
        # Münzwurf gewonnen, aber "Zweiter" gewählt
        self.assertFalse(mh.first_from_coin_toss({"myid": 0, "choice": 0, "pvp_choice": {"choice": 2}}))
        # Noch nicht gewählt bzw. kein Duell
        self.assertIsNone(mh.first_from_coin_toss({"myid": 1, "choice": 0}))
        self.assertIsNone(mh.first_from_coin_toss(None))


class FakeMemory:
    """ClientWork als dict; merkt sich, was gelesen wurde."""

    def __init__(self, views, data):
        self.views, self.data, self.reads = views, data, []

    def open_views(self):
        return self.views

    def client_work(self, *path):
        self.reads.append("/".join(path))
        node = self.data
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        return node


def duel_result(did, result):
    return {"mode": mh.RANKED, "replayButton": {"did": did}, "resultInfo": {"result": result}}


class MatchWatcherTest(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        self.memory = FakeMemory(["HomeViewController"], {"Deck": {"maindeck_id": 13, "list": {
            "13": {"name": "Ryzeal Mitsu"}, "14": {"name": "Snake-Eye"}}}})
        self.new = []
        self.watcher = mh.MatchWatcher(lambda: self.db, self.new.append, connected=lambda: True,
                                       memory=lambda: self.memory, clock=lambda: 1000.0)

    def step(self, views, **data):
        self.memory.views = views
        self.memory.data.update(data)
        self.memory.reads.clear()
        return self.watcher.check()

    def test_deck_from_deck_selection_first_from_coin_toss_result_from_result_screen(self):
        menu = ["HomeViewController", "ColosseumViewController"]
        self.assertEqual(self.step(menu), 0)
        # Deck-Auswahl: Decks werden mit ID gemerkt (auch über einen Neustart hinweg – Datenbank)
        self.step(menu + ["DeckSelectViewController2"], DeckList={
            "13": {"m": {"ids": MY_MAIN}, "e": {"ids": MY_EXTRA}}, "14": {"m": {"ids": OPP_MAIN}, "e": {"ids": []}}})
        self.assertEqual(self.db.md_deck("13").name, "Ryzeal Mitsu")
        # Münzwurf verloren, Gegner fängt an
        self.step(menu + [mh.COIN_TOSS_VIEW], Duel={"myid": 1, "choice": 0, "pvp_choice": {"choice": 1}})
        # Im Duell: nur die Menüs, keine Daten
        self.step(menu + ["DuelClient"], Duel={"myid": 1, "choice": 0})
        self.assertEqual(self.memory.reads, [])
        # Ergebnis-Bildschirm: Daten kommen erst etwas später
        self.assertEqual(self.step(menu + [mh.RESULT_VIEW]), 0)
        self.assertEqual(self.step(menu + [mh.RESULT_VIEW], DuelResult=duel_result(77, mh.WIN)), 1)
        match = self.new[0][0]
        self.assertEqual((match.did, match.result, match.first, match.md_deck, match.source),
                         ("77", mh.WIN, False, "Ryzeal Mitsu", "live"))
        entry, = self.db.matches()
        self.assertEqual((entry.deck_name, entry.opp_name, entry.played_at), ("Ryzeal / Mitsurugi", "", 1000.0))
        self.assertEqual(self.step(menu), 0)  # dasselbe Ergebnis zählt nur einmal

    def test_match_history_completes_live_entry_without_counting_it_again(self):
        menu = ["HomeViewController"]
        self.step(menu, DuelResult=duel_result(77, mh.LOSS))  # beim Start: letztes Ergebnis
        self.assertEqual(len(self.new), 1)
        history = {"58": {"1": entry(77, 500, mh.LOSS, first_player=0, turn=4)}}
        self.assertEqual(self.step(menu + [mh.HISTORY_VIEW], DuelHistory=history), 0)
        match, = self.db.matches()
        self.assertEqual((match.first, match.turns, match.opp_name, match.played_at), (False, 4, "Snake-Eye", 500))
        self.assertEqual(len(self.new), 1)

    def test_same_duel_live_and_in_history_at_start_counts_once(self):
        self.memory.data["DeckList"] = {"13": {"m": {"ids": MY_MAIN}, "e": {"ids": MY_EXTRA}}}
        history = {"58": {"1": entry(77, 500, mh.LOSS, first_player=0, turn=4)}}
        self.assertEqual(self.step(["HomeViewController"], DuelResult=duel_result(77, mh.LOSS),
                                   DuelHistory=history), 1)
        match, = self.db.matches()
        self.assertEqual((match.turns, match.md_deck, match.opp_name), (4, "Ryzeal Mitsu", "Snake-Eye"))

    def test_nothing_without_memory_mode(self):
        self.watcher.enabled = False
        self.assertEqual(self.watcher.check(), 0)
        self.assertEqual(self.memory.reads, [])


@unittest.skipUnless(HAS_TK, "kein Tk")
class WinratePanelTest(unittest.TestCase):
    def setUp(self):
        from winrate_panel import WinratePanel
        self.root = tk.Tk()
        self.root.withdraw()
        self.db = make_db()
        now = time.time()
        mh.store_matches(self.db, mh.parse_history({"58": {
            "1": entry(1, now - 30, mh.WIN), "2": entry(2, now - 20, mh.LOSS, first_player=0),
            "3": entry(3, now - 10, mh.WIN, mode=1)}}))
        self.settings, self.opened = {}, []
        self.panel = WinratePanel(self.root, self.db, self.settings, lambda: None,
                                  on_open=lambda *a: self.opened.append(a), on_close=lambda: None, tracking=True)

    def tearDown(self):
        self.panel.close()
        self.root.destroy()

    def texts(self, widget):
        result = []
        for child in widget.winfo_children():
            if isinstance(child, tk.Label):
                result.append(child.cget("text"))
            result += self.texts(child)
        return result

    def test_ranked_rates_decks_and_matches(self):
        value, record = self.panel.tiles["all"]
        self.assertEqual((value.cget("text"), record.cget("text")), ("50 %", "1–1 · 2 Match(es)"))
        self.assertEqual(self.panel.tiles["first"][0].cget("text"), "100 %")
        self.assertEqual(self.panel.tiles["second"][0].cget("text"), "0 %")
        texts = self.texts(self.panel.inner)
        self.assertIn("Ryzeal / Mitsurugi", texts)
        self.assertIn("Ryzeal / Mitsurugi  vs  Snake-Eye", texts)
        self.assertEqual(texts.count("NIEDERLAGE"), 1)
        # Alle Modi
        self.panel.mode_buttons["all"].invoke()
        self.assertEqual(self.settings["WINRATE_MODE"], "all")
        self.assertEqual(self.panel.tiles["all"][1].cget("text"), "2–1 · 3 Match(es)")

    def test_show_deck_in_deck_window(self):
        self.panel._open(self.db.deck_win_stats(mh.RANKED)[0])
        name, code = self.opened[0]
        self.assertEqual(name, "Ryzeal / Mitsurugi")
        self.assertIn("#main", code)

    def test_hint_without_memory_mode(self):
        self.panel.tracking = False
        self.panel.refresh()
        self.assertIn("Speicher lesen", self.panel.info_label.cget("text"))


if __name__ == "__main__":
    unittest.main()
