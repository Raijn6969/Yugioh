"""Monatsrückblick: Auswertung eines Kalendermonats, Fenster mit Monatswahl, HTML-Export, Hinweis im neuen Monat."""

import datetime
import os
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

import match_history as mh
import Overlay
import replay_recorder as rr
import review_export
import season_review as sr
from card_stats import CardStatsDB

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAS_TK = True
except Exception:
    HAS_TK = False

W, L, D = mh.WIN, mh.LOSS, mh.DRAW


def at(month, day, hour=20, year=2026):
    return datetime.datetime(year, month, day, hour).timestamp()


def make_db():
    return CardStatsDB(os.path.join(tempfile.mkdtemp(), "stats.db"), base_path=None, fetch=lambda ids: {})


class Duels:
    def __init__(self, db):
        self.db, self.n = db, 0

    def add(self, when, result, first=True, coin=None, deck="Ryzeal", opp="Snake-Eye", mode=mh.RANKED, turns=5):
        self.n += 1
        record = mh.MatchRecord(str(self.n), when, mode, result, first, turns, 1, [], [], [], [], "", "history", coin)
        self.db.add_matches([(record, deck, "#main\n", opp, "")])


def september(db) -> Duels:
    """Ein Monat Duelle (September 2026), dazu je eins am letzten Tag davor und am ersten danach (zählen nicht)."""
    duels = Duels(db)
    duels.add(at(8, 31, 23), W)                                   # August
    duels.add(at(10, 1, 0), L)                                    # Oktober (00:00 gehört schon zum nächsten Monat)
    duels.add(at(9, 1, 20), W, True, True)                        # 1
    duels.add(at(9, 1, 21), W, True, False)                       # 2
    duels.add(at(9, 1, 22), W, False, True)                       # 3
    duels.add(at(9, 2, 2), W, False, False)                       # 4: 02:00 gehört noch zum Spieltag 1.9.
    duels.add(at(9, 2, 20), L, True, True, opp="Sky Striker")     # 5
    duels.add(at(9, 3, 20), L, True, False, opp="Sky Striker")    # 6
    duels.add(at(9, 3, 21), L, False, True, opp="Sky Striker")    # 7
    duels.add(at(9, 4, 20), W, True, True, deck="Snake-Eye", opp="Labrynth")   # 8
    duels.add(at(9, 5, 20), D, None, None, deck="Snake-Eye", opp="Mitsurugi")  # 9
    duels.add(at(9, 6, 20), W, True, True, mode=mh.FREE)          # nur in "alle Modi"
    return duels


class CalendarTest(unittest.TestCase):
    def test_month_bounds_and_names(self):
        start, end = sr.month_bounds(2026, 9)
        self.assertEqual((start, end), (at(9, 1, 0), at(10, 1, 0)))
        self.assertEqual(sr.month_bounds(2026, 12)[1], at(1, 1, 0, year=2027))     # Jahreswechsel
        self.assertEqual(sr.month_name(2026, 3), "März 2026")
        self.assertEqual(sr.month_key(2026, 3), "2026-03")
        self.assertEqual((sr.previous_month(2026, 1), sr.next_month(2026, 12)), ((2025, 12), (2027, 1)))

    def test_default_month_is_the_previous_one_in_the_first_three_days(self):
        self.assertEqual(sr.default_month(at(10, 11)), (2026, 10))
        self.assertEqual(sr.default_month(at(10, 4, 0)), (2026, 10))
        self.assertEqual(sr.default_month(at(10, 3, 23)), (2026, 9))
        self.assertEqual(sr.default_month(at(10, 1, 0)), (2026, 9))
        self.assertEqual(sr.default_month(at(1, 2, 12, year=2027)), (2026, 12))

    def test_game_day_starts_at_five(self):
        self.assertEqual(sr.game_day(at(9, 2, 4)), datetime.date(2026, 9, 1))
        self.assertEqual(sr.game_day(at(9, 2, 5)), datetime.date(2026, 9, 2))


class PartsTest(unittest.TestCase):
    def test_streaks(self):
        self.assertEqual(sr.streaks([W, W, L, W, W, W, D, L, L]), (3, 2))
        self.assertEqual(sr.streaks([W, D, W]), (1, 0))      # Unentschieden unterbricht
        self.assertEqual(sr.streaks([]), (0, 0))

    def test_best_and_worst_matchup_need_three_duels(self):
        from card_stats import OpponentStats, WinStats
        opp = lambda name, matches, wins: OpponentStats(name, WinStats(matches, wins))  # noqa: E731
        best, worst = sr.matchups([opp("A", 10, 6), opp("B", 3, 0), opp("C", 2, 2), opp("D", 4, 3), opp("E", 5, 3)])
        self.assertEqual((best.opp_name, worst.opp_name), ("D", "B"))      # C hat nur 2 Duelle
        only, nothing = sr.matchups([opp("A", 3, 1), opp("B", 2, 1)])
        self.assertEqual((only.opp_name, nothing), ("A", None))              # eines allein ist das beste
        self.assertEqual(sr.matchups([opp("A", 2, 1)]), (None, None))
        # gleiche Winrate: das mit mehr Duellen zählt stärker, nie dasselbe Deck zweimal
        best, worst = sr.matchups([opp("A", 4, 2), opp("B", 6, 3)])
        self.assertEqual((best.opp_name, worst.opp_name), ("B", "A"))


class BuildTest(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        september(self.db)

    def test_one_calendar_month_in_ranked(self):
        review = sr.build(self.db, 2026, 9, mh.RANKED)
        total = review.total
        self.assertEqual((total.matches, total.wins, total.draws, total.losses), (9, 5, 1, 3))
        self.assertEqual((total.first, total.first_wins, total.second, total.second_wins), (5, 3, 3, 2))
        self.assertEqual((total.coin_won, total.coin_won_wins, total.coin_lost, total.coin_lost_wins), (5, 3, 3, 2))
        self.assertEqual([(d.deck_name, d.stats.matches) for d in review.decks], [("Ryzeal", 7), ("Snake-Eye", 2)])
        self.assertEqual(review.deck_count, 2)
        self.assertEqual((review.best.opp_name, review.best.stats.wins, review.best.stats.matches), ("Snake-Eye", 4, 4))
        self.assertEqual((review.worst.opp_name, review.worst.stats.wins, review.worst.stats.matches),
                         ("Sky Striker", 0, 3))
        self.assertEqual((review.win_streak, review.loss_streak), (4, 3))
        day = review.busiest
        self.assertEqual((day.date, day.duels, day.wins, day.losses), (datetime.date(2026, 9, 1), 4, 4, 0))

    def test_all_modes_and_other_months(self):
        self.assertEqual(sr.build(self.db, 2026, 9, None).total.matches, 10)
        self.assertEqual(sr.build(self.db, 2026, 8, mh.RANKED).total.matches, 1)
        empty = sr.build(self.db, 2026, 7, mh.RANKED)
        self.assertEqual((empty.total.matches, empty.decks, empty.best, empty.busiest), (0, [], None, None))

    def test_top_five_decks_only(self):
        duels = Duels(self.db)
        for i in range(8):
            duels.add(at(11, 1 + i), W, deck=f"Deck {i}")
        review = sr.build(self.db, 2026, 11, mh.RANKED)
        self.assertEqual((len(review.decks), review.deck_count), (5, 8))

    def test_db_until_filter_and_first_played(self):
        self.assertEqual(self.db.first_played(), at(8, 31, 23))
        self.assertIsNone(make_db().first_played())
        start, end = sr.month_bounds(2026, 9)
        self.assertEqual(len(self.db.matches(None, mh.RANKED, 100, start, end)), 9)
        self.assertEqual(self.db.win_stats(None, mh.RANKED, start, end).matches, 9)
        self.assertEqual(self.db.win_stats(None, mh.RANKED, None, start).matches, 1)   # bis September: der August
        self.assertEqual(sum(d.stats.matches for d in self.db.deck_win_stats(mh.RANKED, start, end)), 9)
        self.assertEqual(sum(o.stats.matches for o in self.db.opponent_stats(None, mh.RANKED, start, end)), 9)


class NoticeTest(unittest.TestCase):
    def test_once_for_the_previous_month_if_it_had_duels(self):
        db = make_db()
        self.assertIsNone(sr.pending_notice(db, None, at(10, 11)))               # keine Duelle
        september(db)
        self.assertEqual(sr.pending_notice(db, None, at(10, 11)), (2026, 9))
        self.assertEqual(sr.pending_notice(db, "2026-08", at(10, 1, 8)), (2026, 9))
        self.assertIsNone(sr.pending_notice(db, "2026-09", at(10, 11)))          # schon gemeldet
        self.assertEqual(sr.pending_notice(db, None, at(9, 20)), (2026, 8))      # im August gab es ein Duell
        self.assertIsNone(sr.pending_notice(db, None, at(12, 3)))                # November: keine Duelle


class OverlayNoticeTest(unittest.TestCase):
    def make_app(self, **config):
        app = SimpleNamespace(config=dict(config), saved=[], toasts=[], queued=[], _after_ids={"review_notice": 1})
        app._save_config_safely = lambda: app.saved.append(dict(app.config))
        app._queue_toast = lambda text: app.toasts.append(text)
        app._run_on_ui = lambda func, *args: app.queued.append((func, args))
        app._stats_db = lambda: "db"
        app._on_review_ready = object()
        return app

    def test_toast_names_the_month_and_is_remembered(self):
        app = self.make_app()
        Overlay.MasterDuelImporter._on_review_ready(app, 2026, 9)
        self.assertEqual(app.toasts, ["Rückblick für September ist fertig – Deck → Winrate → Rückblick"])
        self.assertEqual(app.saved[-1][sr.NOTICE_KEY], "2026-09")

    def test_worker_only_reports_a_pending_month(self):
        app = self.make_app(**{sr.NOTICE_KEY: "2026-08"})
        with mock.patch.object(sr, "pending_notice", return_value=(2026, 9)) as pending:
            Overlay.MasterDuelImporter._review_worker(app)
        pending.assert_called_once_with("db", "2026-08")
        self.assertEqual(app.queued, [(app._on_review_ready, (2026, 9))])
        app.queued.clear()
        with mock.patch.object(sr, "pending_notice", return_value=None):
            Overlay.MasterDuelImporter._review_worker(app)
        self.assertEqual(app.queued, [])
        with mock.patch.object(sr, "pending_notice", side_effect=Exception("database is locked")), \
                mock.patch.object(Overlay, "append") as log:
            Overlay.MasterDuelImporter._review_worker(app)                       # kein Absturz
        self.assertEqual(app.queued, [])
        self.assertIn("database is locked", log.call_args.args[1])

    def test_check_runs_in_background_after_the_start_message(self):
        app = self.make_app()
        app._review_worker = lambda: None
        with mock.patch.object(Overlay.threading, "Thread") as thread:
            Overlay.MasterDuelImporter._start_review_notice(app)
        self.assertTrue(thread.call_args.kwargs["daemon"])
        thread.return_value.start.assert_called_once()
        self.assertGreater(Overlay.REVIEW_NOTICE_DELAY_MS, Overlay.STARTED_TOAST_MS)
        self.assertNotIn("review_notice", app._after_ids)


class ExportTest(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        september(self.db)
        self.folder = tempfile.mkdtemp()

    def test_single_self_explanatory_dark_page(self):
        review = sr.build(self.db, 2026, 9, mh.RANKED)
        page = review_export.render(review, "Ranked")
        self.assertIn("<title>Rückblick September 2026</title>", page)
        self.assertIn('name="viewport"', page)                      # Handy
        self.assertIn("--bg:#0e0f17", page)                         # Dark-Look wie der Replay-Export
        self.assertNotIn("<script", page)                           # nichts nachzuladen, nichts auszuführen
        self.assertNotIn("http", page)                              # offline, keine Verweise ins Netz
        for text in ("Bilanz", "Münzwurf", "Meistgespielte Decks", "Gegner-Decks", "Bestes Matchup", "Snake-Eye",
                     "Schlechtestes Matchup", "Sky Striker", "Längste Siegserie", "4 in Folge", "Aktivster Tag",
                     "Di 01.09.", "Ranked", "56 %"):
            self.assertIn(text, page)

    def test_empty_month_and_missing_coin_toss(self):
        page = review_export.render(sr.build(self.db, 2026, 7, mh.RANKED), "Ranked")
        self.assertIn("keine erfassten Matches", page)
        duels = Duels(make_db())
        duels.add(at(6, 3), W)
        page = review_export.render(sr.build(duels.db, 2026, 6, None))
        self.assertIn("Münzwurf wurde in diesem Monat nicht erfasst", page)
        self.assertIn("Noch zu wenige Matches", page)                # kein Gegner-Deck mit 3 Matches

    def test_game_texts_cannot_break_the_page(self):
        duels = Duels(make_db())
        for _ in range(3):
            duels.add(at(6, 3), W, deck="<b>Deck</b>", opp="</td><script>alert(1)</script>")
        page = review_export.render(sr.build(duels.db, 2026, 6, None))
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("<b>Deck</b>", page)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page)

    def test_no_opponent_player_names(self):
        info = rr.ReplayInfo(at(9, 1, 20) - 600, "r1", 0, 0, ["Raijn", "WXGY"], [{"main": [1], "extra": []}] * 2)
        self.db.save_replay(info, [{"cards": [], "lp": {}, "turn": "", "phase": "", "t": i} for i in range(4)])
        page = review_export.render(sr.build(self.db, 2026, 9, mh.RANKED), "Ranked")
        self.assertNotIn("WXGY", page)

    def test_file_in_downloads_never_overwritten(self):
        review = sr.build(self.db, 2026, 9, mh.RANKED)
        first = review_export.export(review, "Ranked", self.folder)
        second = review_export.export(review, "Ranked", self.folder)
        self.assertEqual(os.path.basename(first), "MD-Importer_Rueckblick_2026-09.html")
        self.assertEqual(os.path.basename(second), "MD-Importer_Rueckblick_2026-09_2.html")
        with open(first, encoding="utf-8") as f:
            self.assertIn("Rückblick September 2026", f.read())

    def test_default_folder_is_downloads(self):
        with mock.patch.object(review_export, "downloads_dir", return_value=self.folder):
            path = review_export.export(sr.build(self.db, 2026, 9, None))
        self.assertEqual(os.path.dirname(path), self.folder)


@unittest.skipUnless(HAS_TK, "kein Tk")
class PanelTest(unittest.TestCase):
    def setUp(self):
        from review_panel import ReviewPanel
        self.ReviewPanel = ReviewPanel
        self.root = tk.Tk()
        self.root.withdraw()
        self.db = make_db()
        september(self.db)
        self.settings, self.revealed, self.closed = {}, [], []
        self.addCleanup(self.root.destroy)

    def open(self, now=at(10, 11)):
        panel = self.ReviewPanel(self.root, self.db, self.settings, on_close=lambda: self.closed.append(True), now=now,
                                 reveal_file=self.revealed.append)
        self.addCleanup(panel.close)   # vor dem Zerstören von root
        return panel

    def texts(self, widget):
        result = []
        for child in widget.winfo_children():
            if isinstance(child, tk.Label):
                result.append(child.cget("text"))
            result += self.texts(child)
        return result

    def test_current_month_is_the_default_and_empty_months_say_so(self):
        panel = self.open(now=at(11, 10))
        self.assertEqual(panel.month_label.cget("text"), "November 2026")
        self.assertTrue(any("keine erfassten Matches" in t for t in self.texts(panel.inner)))

    def test_first_days_of_a_month_show_the_previous_one(self):
        panel = self.open(now=at(10, 2))
        self.assertEqual(panel.month_label.cget("text"), "September 2026")

    def test_stats_of_the_month(self):
        panel = self.open(now=at(10, 2))
        texts = self.texts(panel.inner)
        self.assertIn("56 %", texts)                              # 5 Siege aus 9
        self.assertIn("5–4 · 9 Matches", texts)
        for expected in ("MÜNZWURF", "Ryzeal", "57 % · 4–3 · 7 Matches", "Bestes Matchup: Snake-Eye",
                         "Schlechtestes: Sky Striker", "4 in Folge", "3 in Folge", "Di 01.09. · 4–0 · 4 Matches",
                         "Matches im Monat"):
            self.assertTrue(any(expected in t for t in texts), (expected, texts))
        self.assertEqual(panel.mode_label_widget.cget("text"), "Ranked")
        self.assertEqual(panel.status_label.winfo_manager(), "")  # keine leere Statuszeile
        self.settings["WINRATE_MODE"] = "all"
        panel.refresh()
        self.assertEqual(panel.mode_label_widget.cget("text"), "alle Modi")
        self.assertIn("60 %", self.texts(panel.inner))             # 6 Siege aus 10 Duellen

    def test_browse_months_but_not_into_the_future_or_before_the_first_duel(self):
        panel = self.open()                         # Oktober
        panel.next_btn.invoke()
        self.assertEqual(panel.month_label.cget("text"), "Oktober 2026")      # Zukunft: nein
        panel.prev_btn.invoke()
        self.assertEqual(panel.month_label.cget("text"), "September 2026")
        panel.prev_btn.invoke()
        self.assertEqual(panel.month_label.cget("text"), "August 2026")       # hier liegt das älteste Duell
        panel.prev_btn.invoke()
        self.assertEqual(panel.month_label.cget("text"), "August 2026")
        panel.next_btn.invoke()
        self.assertEqual(panel.month_label.cget("text"), "September 2026")

    def test_html_button_writes_the_file_and_shows_it(self):
        panel = self.open(now=at(10, 2))
        folder = tempfile.mkdtemp()
        with mock.patch.object(review_export, "downloads_dir", return_value=folder):
            panel.export_btn.invoke()
            end = time.time() + 5
            while not self.revealed and time.time() < end:
                self.root.update()
                time.sleep(0.02)
        self.assertEqual(len(self.revealed), 1)
        self.assertEqual(os.path.dirname(self.revealed[0]), folder)
        self.assertIn("Gespeichert: MD-Importer_Rueckblick_2026-09.html", panel.status_label.cget("text"))

    def test_html_error_is_shown(self):
        panel = self.open(now=at(10, 2))
        with mock.patch.object(review_export, "export", side_effect=OSError("kein Zugriff")):
            panel.export_btn.invoke()
            end = time.time() + 5
            while "fehlgeschlagen" not in panel.status_label.cget("text") and time.time() < end:
                self.root.update()
                time.sleep(0.02)
        self.assertIn("Export fehlgeschlagen: kein Zugriff", panel.status_label.cget("text"))
        self.assertEqual(self.revealed, [])

    def test_database_errors_do_not_break_the_window(self):
        panel = self.open()
        with mock.patch.object(sr, "build", side_effect=Exception("database is locked")):
            panel.refresh()
        self.assertTrue(any("nicht lesbar" in t for t in self.texts(panel.inner)))

    def test_button_in_winrate_window_opens_and_closes_it(self):
        from winrate_panel import WinratePanel
        winrate = WinratePanel(self.root, self.db, self.settings, lambda: None, on_open=lambda *a: None,
                               on_close=lambda: None, tracking=True)
        self.addCleanup(winrate.close)
        self.root.update()
        winrate.review_btn.invoke()
        self.assertIsNotNone(winrate.review)
        self.assertEqual(winrate.review.month_label.cget("text"), sr.month_name(*sr.default_month()))
        self.assertEqual(winrate.review.win.winfo_manager(), "place")          # deckt die Winrate-Ansicht
        winrate.review_btn.invoke()
        self.assertIsNone(winrate.review)
        winrate.review_btn.invoke()
        winrate.close()                                        # Winrate zu → Rückblick auch
        self.assertIsNone(winrate.review)


if __name__ == "__main__":
    unittest.main()
