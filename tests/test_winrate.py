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
from winrate_panel import coin_note, coin_streak, day_summary, period_start, summary, trend

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


entry_ = entry


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


def add(db, did, played_at, result, opp="", deck="Ryzeal", mode=mh.RANKED, first=None):
    record = mh.MatchRecord(str(did), played_at, mode, result, first, 0, 0, [], [], [], [], "", "live")
    db.add_matches([(record, deck, "#main", opp, "")])


class PeriodTest(unittest.TestCase):
    def test_period_start(self):
        import datetime
        at = lambda *args: datetime.datetime(*args).timestamp()  # noqa: E731
        night, day = at(2026, 10, 8, 3, 0), at(2026, 10, 8, 18, 30)
        self.assertEqual(period_start("today", day), at(2026, 10, 8, 5, 0))
        self.assertEqual(period_start("today", night), at(2026, 10, 7, 5, 0))  # nach Mitternacht: noch "gestern"
        self.assertEqual(period_start("season", day), at(2026, 10, 1))
        self.assertEqual(period_start("week", day), day - 7 * 86400)
        self.assertIsNone(period_start("all", day))

    def test_time_filter_opponents_and_day_summary(self):
        db = make_db()
        now = time.time()
        add(db, 1, now - 10 * 86400, mh.WIN, "Snake-Eye")  # vor 10 Tagen
        add(db, 2, now - 60, mh.LOSS, "Snake-Eye", first=True)
        add(db, 3, now - 50, mh.WIN, "Snake-Eye", first=False)
        add(db, 4, now - 40, mh.WIN, "Yubel")
        add(db, 5, now - 30, mh.WIN)  # Gegner unbekannt
        add(db, 6, now - 20, mh.LOSS, "Yubel", mode=mh.FREE)
        week = now - 7 * 86400
        self.assertEqual(db.win_stats(mode=mh.RANKED).matches, 5)
        self.assertEqual(db.win_stats(mode=mh.RANKED, since=week).matches, 4)
        self.assertEqual([m.did for m in db.matches(mode=mh.RANKED, since=week)], ["5", "4", "3", "2"])
        self.assertEqual(len(db.deck_win_stats(mh.RANKED, now + 1)), 0)
        opponents = db.opponent_stats(mode=mh.RANKED, since=week)
        self.assertEqual([(o.opp_name, o.stats.matches, o.stats.wins) for o in opponents],
                         [("Snake-Eye", 2, 1), ("Yubel", 1, 1)])
        self.assertEqual(summary(opponents[0].stats), "50 % (1–1) · Erster 0 % · Zweiter 100 %")
        self.assertEqual(len(db.opponent_stats()), 2)  # alle Modi, alle Zeit: Yubel 2×
        start = period_start("today", now)
        db = make_db()
        add(db, 6, start - 60, mh.WIN)  # vor 5 Uhr: zählt zu gestern
        self.assertEqual(day_summary(db, mh.RANKED, start + 3600), "")
        add(db, 7, start + 60, mh.WIN)
        add(db, 8, start + 120, mh.LOSS, mode=mh.FREE)
        self.assertEqual(day_summary(db, mh.RANKED, start + 3600), "Heute 1–0")
        self.assertEqual(day_summary(db, None, start + 3600), "Heute 1–1")
        self.assertEqual(day_summary(make_db(), mh.RANKED, now), "")

    def test_trend_is_rolling_win_rate_oldest_first(self):
        db = make_db()
        results = [mh.WIN, mh.LOSS] * 6 + [mh.WIN, mh.WIN]  # 14 Matches, das älteste zuerst
        for i, result in enumerate(results):
            add(db, i, 1000 + i, result)
        points = trend(db.matches())
        self.assertEqual([m.did for m, _ in points][:3], ["0", "1", "2"])
        self.assertEqual([round(rate, 2) for _, rate in points[:3]], [1.0, 0.5, 0.67])
        self.assertEqual(points[-1][1], 0.6)  # letzte 10: 6 Siege


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
        self.assertEqual((old.did, old.md_deck, old.opp_name, old.coin), ("1", "", "Snake-Eye", None))
        self.assertEqual(latest.coin, None)


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
    def test_coin_toss_and_who_goes_first(self):
        # (Münzwurf gewonnen?, selbst angefangen?) – beide Fälle live gesehen:
        # Münzwurf verloren (Gegner = Spieler 0 wählt), Gegner fängt an → Zweiter
        self.assertEqual(mh.coin_toss({"myid": 1, "choice": 0, "pvp_choice": {"choice": 1}})[:2], (False, False))
        # Münzwurf gewonnen, selbst "Erster" gewählt
        self.assertEqual(mh.coin_toss({"myid": 1, "choice": 1, "pvp_choice": {"choice": 0, "cnt": 0}})[:2],
                         (True, True))
        # Als Spieler 0 gewonnen und "Erster" gewählt: pvp_choice gilt aus eigener Sicht
        self.assertEqual(mh.coin_toss({"myid": 0, "choice": 0, "pvp_choice": {"choice": 0, "cnt": 0}})[:2],
                         (True, True))
        self.assertEqual(mh.coin_toss({"myid": 0, "choice": 1, "pvp_choice": {"choice": 1, "cnt": 5}})[:2],
                         (False, False))
        # Noch nicht gewählt: Münzwurf schon bekannt; kein Münzwurf bzw. kein Duell
        self.assertEqual(mh.coin_toss({"myid": 1, "choice": 0})[:2], (False, None))
        self.assertIsNone(mh.coin_toss({"myid": 1}))
        self.assertIsNone(mh.coin_toss(None))

    def test_duel_settings_before_the_duel(self):
        # Wie live gelesen (Münzwurf verloren, Gegner fängt an): Duell-Einstellungen mit FirstPlayer und eigenem Deck
        toss = mh.coin_toss(setup(did=79, me=1, chooser=0, first=0))
        self.assertEqual(toss, mh.CoinToss(False, False, "79", MY_MAIN, MY_EXTRA))
        # Münzwurf gewonnen und selbst gewählt: steht nur hier (kein pvp_choice)
        self.assertEqual(mh.coin_toss(setup(did=80, me=1, chooser=1, first=1))[:3], (True, True, "80"))
        self.assertEqual(mh.coin_toss(setup(did=81, me=0, chooser=0, first=1))[:3], (True, False, "81"))

    def test_coin_stats_and_streak(self):
        db = make_db()
        coins = [(1, True, mh.WIN), (2, True, mh.LOSS), (3, False, mh.WIN), (4, False, mh.LOSS), (5, None, mh.WIN),
                 (6, False, mh.LOSS)]
        db.add_matches([(mh.MatchRecord(str(did), did, mh.RANKED, result, None, 0, 0, [], [], [], [], "", "live",
                                        coin), "", "", "", "") for did, coin, result in coins])
        stats = db.win_stats()
        self.assertEqual((stats.coin_won, stats.coin_won_wins, stats.coin_lost, stats.coin_lost_wins), (2, 1, 3, 1))
        matches = db.matches()
        self.assertEqual([m.coin for m in matches], [False, None, False, False, True, True])
        streak = coin_streak(matches)  # das unbekannte Match dazwischen unterbricht die Serie nicht
        self.assertEqual(streak, (False, 3))
        self.assertEqual(coin_note(stats, streak), "Münzwurf zuletzt 3× in Folge verloren")
        self.assertEqual(coin_note(stats, (True, 1)), "")  # keine Serie
        self.assertIsNone(coin_streak([]))
        self.assertEqual(coin_note(make_db().win_stats(), None), "")  # gar keine Matches


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


def setup(did, me, chooser, first):
    """ClientWork["Duel"] kurz vor dem Duell (Duell-Einstellungen, wie live gelesen)."""
    decks = [deck([], []), deck([], [])]
    decks[me] = deck(MY_MAIN, MY_EXTRA)
    return {"Choice": chooser, "Deck": decks, "FirstPlayer": first, "GameMode": 3, "MyID": me, "did": did,
            "name": ["Gegner", "Raijn"]}


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
        self.assertEqual((match.did, match.result, match.coin, match.first, match.md_deck, match.source),
                         ("77", mh.WIN, False, False, "Ryzeal Mitsu", "live"))
        entry, = self.db.matches()
        self.assertEqual((entry.deck_name, entry.opp_name, entry.played_at, entry.coin),
                         ("Ryzeal / Mitsurugi", "", 1000.0, False))
        # Die Match History ergänzt den Eintrag, der Münzwurf bleibt
        history = {"58": {"1": entry_(77, 500, mh.WIN, first_player=0, turn=4)}}
        self.step(menu + [mh.HISTORY_VIEW], DuelHistory=history)
        entry, = self.db.matches()
        self.assertEqual((entry.turns, entry.opp_name, entry.coin), (4, "Snake-Eye", False))
        self.assertEqual(self.step(menu), 0)  # dasselbe Ergebnis zählt nur einmal

    def test_choice_read_after_the_coin_toss_result(self):
        menu = ["HomeViewController"]
        self.step(menu + [mh.COIN_TOSS_VIEW], Duel={"myid": 1, "choice": 1})  # gewonnen, wählt noch
        self.step(menu + [mh.COIN_TOSS_VIEW], Duel={"myid": 1, "choice": 1, "pvp_choice": {"choice": 0}})
        self.step(menu + [mh.COIN_TOSS_VIEW], Duel={"myid": 1, "choice": 1, "pvp_choice": {}})  # Wahl schon weg
        self.step(menu + ["DuelClient"], Duel={})
        self.memory.data.pop("Duel")
        self.step(menu + [mh.RESULT_VIEW], DuelResult=duel_result(78, mh.LOSS))
        match = self.new[0][0]
        self.assertEqual((match.coin, match.first), (True, True))

    def test_choice_of_an_aborted_duel_is_not_reused(self):
        now = [1000.0]
        self.watcher.clock = lambda: now[0]
        menu = ["HomeViewController"]
        self.step(menu + [mh.COIN_TOSS_VIEW], Duel={"myid": 1, "choice": 1, "pvp_choice": {"choice": 0}})
        self.step(menu + ["DuelClient"], Duel={})  # Duell bricht ab, kein Ergebnis-Bildschirm
        now[0] += 600
        self.step(menu + [mh.COIN_TOSS_VIEW], Duel={"myid": 1, "choice": 1})  # neues Duell, Wahl nicht lesbar
        self.memory.data.pop("Duel")
        self.step(menu + [mh.RESULT_VIEW], DuelResult=duel_result(81, mh.WIN))
        self.assertEqual((self.new[0][0].coin, self.new[0][0].first), (True, None))

    def test_duel_settings_give_first_player_and_deck_without_deck_selection(self):
        menu = ["HomeViewController"]
        self.step(menu)
        self.step(menu + [mh.COIN_TOSS_VIEW], Duel={"myid": 1, "choice": 1})  # gewonnen, eigene Wahl unsichtbar
        self.step(menu + [mh.COIN_TOSS_VIEW], Duel=setup(did=79, me=1, chooser=1, first=0))  # Zweiter gewählt
        self.step(menu + ["DuelClient"])
        self.step(menu + [mh.RESULT_VIEW], Duel={"name": ["Gegner", "Raijn"], "result": 1},
                  DuelResult=duel_result(79, mh.WIN))
        match = self.new[0][0]
        self.assertEqual((match.coin, match.first, match.my_main), (True, False, MY_MAIN))
        entry, = self.db.matches()
        self.assertEqual(entry.deck_name, "Ryzeal / Mitsurugi")  # Deck nie in der Deck-Auswahl angesehen

    def test_coin_toss_of_another_duel_is_not_used(self):
        menu = ["HomeViewController"]
        self.step(menu + [mh.COIN_TOSS_VIEW], Duel=setup(did=90, me=1, chooser=1, first=1))
        self.step(menu + ["DuelClient"])
        self.step(menu + [mh.RESULT_VIEW], DuelResult=duel_result(91, mh.WIN))  # anderes Duell
        self.assertEqual((self.new[0][0].coin, self.new[0][0].first), (None, None))

    def test_old_duel_data_at_start_is_not_used_for_the_coin_toss(self):
        self.step(["HomeViewController"], DuelResult=duel_result(80, mh.WIN),
                  Duel={"myid": 1, "choice": 1, "pvp_choice": {"choice": 1}})
        match = self.new[0][0]
        self.assertEqual((match.coin, match.first), (None, None))

    def test_coin_toss_screen_is_checked_more_often(self):
        waits = []

        class Stop:
            def wait(self, seconds):
                waits.append(seconds)
                return len(waits) > 2

        self.watcher._stop = Stop()
        self.memory.views = ["HomeViewController", mh.COIN_TOSS_VIEW]
        self.watcher._run()
        self.assertEqual(waits, [mh.CHECK_INTERVAL, mh.COIN_TOSS_INTERVAL, mh.COIN_TOSS_INTERVAL])

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

    def test_coin_toss_line_and_match_details(self):
        self.assertIn("nicht erfasst", self.panel.coin_label.cget("text"))  # Matches aus der Match History
        self.assertEqual(self.panel.tiles["coin"][0].cget("text"), "–")
        for did in ("8", "9"):
            record = mh.MatchRecord(did, time.time() + int(did), mh.RANKED, mh.WIN if did == "9" else mh.LOSS,
                                    False, 0, 0, [], [], [], [], "", "live", False)
            self.db.add_matches([(record, "Ryzeal / Mitsurugi", "#main", "", "")])
        self.panel.refresh()
        tile = lambda key: tuple(label.cget("text") for label in self.panel.tiles[key])  # noqa: E731
        self.assertEqual(tile("coin"), ("0 %", "0 von 2"))
        self.assertEqual(tile("coin_won"), ("–", "keine Matches"))
        self.assertEqual(tile("coin_lost"), ("50 %", "1–1 · 2 Match(es)"))
        self.assertEqual(self.panel.coin_label.cget("text"), "Münzwurf zuletzt 2× in Folge verloren")
        self.assertTrue(any("Münzwurf verloren" in text for text in self.texts(self.panel.inner)))

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

    def test_period_buttons_trend_and_opponents(self):
        self.panel.mode_buttons["all"].invoke()
        texts = self.texts(self.panel.inner)
        self.assertTrue(any(t.startswith("VERLAUF") for t in texts))
        self.assertIn("GEGEN GEGNER-DECKS (1)", texts)
        self.assertIn("vs Snake-Eye", texts)
        # Hover über dem Verlauf zeigt das Match
        chart = next(w for w in self.panel.inner.winfo_children() if isinstance(w, tk.Canvas))
        self.root.update()
        chart.event_generate("<Motion>", x=chart.winfo_width() - 5, y=20)
        tip = [chart.itemcget(i, "text") for i in chart.find_withtag("hover") if chart.type(i) == "text"]
        self.assertTrue(tip and tip[0].startswith("Sieg vs Snake-Eye"), tip)
        chart.event_generate("<Leave>")
        self.assertFalse(chart.find_withtag("hover"))
        # Zeitraum
        self.panel.period_buttons["today"].invoke()
        self.assertEqual(self.settings["WINRATE_PERIOD"], "today")
        self.assertIn("Heute", self.panel.filter_label.cget("text"))
        self.panel.settings["WINRATE_PERIOD"] = "week"
        mh.store_matches(self.db, mh.parse_history({"58": {"9": entry(9, time.time() - 30 * 86400, mh.WIN)}}))
        self.panel.refresh()
        self.assertEqual(self.panel.tiles["all"][1].cget("text"), "2–1 · 3 Match(es)")  # ohne das alte Match
        self.panel.period_buttons["all"].invoke()
        self.assertEqual(self.panel.tiles["all"][1].cget("text"), "3–1 · 4 Match(es)")

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
