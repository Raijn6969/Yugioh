"""Replay-Mitschnitt: nur während eines Replays aus der Match History lesen, Schritte speichern, abspielen."""

import os
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

import match_history as mh
import replay_recorder as rr
from card_stats import CardStatsDB, MatchEntry
from md_memory import BoardCard, DuelLog

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAS_TK = True
except Exception:
    HAS_TK = False

LIVE_DUEL = ["HomeViewController", "ColosseumInfoViewController", "DuelClient"]
REPLAY = ["ColosseumInfoViewController", "ColosseumHistoryViewController", "DuelClient"]
DUEL = {"MyID": 0, "FirstPlayer": 0, "time": 1791623726, "did": 6424468568766821879, "name": ["Raijn", "WXGY"],
        "Deck": [{"Main": {"CardIds": [22116, 22117]}, "Extra": {"CardIds": [12321]}},
                 {"Main": {"CardIds": [4007]}, "Extra": {"CardIds": []}}]}


def card(uid, team, zone, index=0, kid=0, face=1, attack=1, atk=0, def_=0):
    return BoardCard(uid, team, zone, index, kid, face, attack, atk, def_)


class FakeMemory:
    def __init__(self):
        self.views, self.board, self.lp, self.phase = ["HomeViewController"], [], {0: 8000, 1: 8000}, ("Turn 1", "Main 1")
        self.reads = []
        self.log = []          # alle Protokollzeilen des Spiels (eine Zeile = ein Eintrag)
        self.log_error = None

    def open_views(self):
        return self.views

    def client_work(self, *path):
        self.reads.append("/".join(path))
        return DUEL if path == ("Duel",) else None

    def duel_worker(self):
        self.reads.append("worker")
        return 1

    def duel_board(self, worker):
        return list(self.board)

    def duel_lp(self, worker):
        return dict(self.lp)

    def duel_phase(self, worker):
        return self.phase

    def reset_log_search(self):
        self.resets = getattr(self, "resets", 0) + 1

    def duel_log_controller(self, worker):
        self.reads.append("log")
        if self.log_error:
            raise self.log_error
        return 7

    def duel_log(self, ctl, start=0):
        out = DuelLog(len(self.log))
        out.extend(self.log[start:])
        return out


def make_db():
    return CardStatsDB(os.path.join(tempfile.mkdtemp(), "stats.db"), base_path=None, fetch=lambda ids: {})


def add_match(db, did="8385", played_at=1791623727):
    """Das Match zum Mitschnitt (DUEL) – ohne gespeichertes Match gibt es keine Zuordnung."""
    db.add_matches([(mh.MatchRecord(did, played_at, 3, mh.WIN, True, 3, 1, [], [], [], [], "", "history", True),
                     "Elfnote", "", "", "")])


class RecorderTest(unittest.TestCase):
    def setUp(self):
        self.db, self.memory, self.saved = make_db(), FakeMemory(), []
        self.now = [100.0]
        self.rec = rr.ReplayRecorder(lambda: self.db, connected=lambda: True, memory=lambda: self.memory,
                                     on_saved=lambda info, steps: self.saved.append((info, steps)),
                                     clock=lambda: self.now[0])

    def step(self, views=None, board=None, **changes):
        if views is not None:
            self.memory.views = views
        if board is not None:
            self.memory.board = board
        for key, value in changes.items():
            setattr(self.memory, key, value)
        self.memory.reads.clear()
        self.now[0] += 1
        self.rec.check()

    def test_live_duel_is_never_read(self):
        self.step(LIVE_DUEL, [card(1, 0, rr.HAND, kid=22116)])
        self.assertEqual(self.memory.reads, [])
        self.assertFalse(self.rec.recording)

    def test_new_replay_resets_the_log_search_pause(self):
        self.step(REPLAY, [card(1, 0, rr.HAND, kid=22116)])
        self.step(REPLAY)
        self.assertEqual(self.memory.resets, 1)  # einmal beim Start des Mitschnitts, nicht bei jedem Schritt

    def test_records_changes_and_saves_when_replay_ends(self):
        self.step(REPLAY, [card(1, 0, rr.HAND, kid=22116)])
        self.assertTrue(self.rec.recording)
        self.step(REPLAY)  # unverändert → kein neuer Schritt
        self.step(REPLAY, [card(1, 0, 2, kid=22116, atk=2500, def_=1800)])
        self.step(REPLAY, lp={0: 8000, 1: 5500})
        self.step(REPLAY, phase=("Turn 2", "Battle"))
        self.assertEqual(len(self.rec.frames), 4)
        self.step(["HomeViewController", "ColosseumHistoryViewController"])  # Replay beendet
        self.assertFalse(self.rec.recording)
        (info, steps), = self.saved
        self.assertEqual((info.start, info.my_id, info.names, steps), (1791623726, 0, ["Raijn", "WXGY"], 4))
        self.assertEqual(info.decks[0], {"main": [22116, 22117], "extra": [12321]})
        replay = self.db.replay_at(1791623726)
        self.assertEqual(len(replay.frames), 4)
        self.assertEqual(replay.frames[1]["cards"], [[1, 0, 2, 0, 22116, 1, 1, 2500, 1800]])
        self.assertEqual((replay.frames[2]["lp"], replay.frames[3]["turn"]), ({"0": 8000, "1": 5500}, "Turn 2"))

    def test_stop_saves_the_recording_so_far(self):
        self.step(REPLAY, [card(1, 0, rr.HAND, kid=22116)])
        self.step(REPLAY, [card(1, 0, 2, kid=22116)])
        self.step(REPLAY, lp={0: 8000, 1: 5500})
        self.rec.stop()                                  # Programm wird beendet, Replay läuft noch
        self.assertFalse(self.rec.recording)
        (info, steps), = self.saved
        self.assertEqual(steps, 3)
        self.assertEqual(len(self.db.replay_at(info.start).frames), 3)

    def test_stop_discards_a_too_short_recording_and_survives_errors(self):
        self.step(REPLAY, [card(1, 0, rr.HAND, kid=22116)])
        self.rec.stop()
        self.assertEqual(self.saved, [])                 # nur 1 Schritt (< MIN_STEPS)
        self.step(REPLAY, [card(1, 0, rr.HAND, kid=22116)])
        self.step(REPLAY, [card(1, 0, 2, kid=22116)])
        self.step(REPLAY, lp={0: 8000, 1: 5500})
        self.rec.get_db = lambda: (_ for _ in ()).throw(OSError("gesperrt"))
        self.rec.stop()                                  # Fehler beim Speichern bricht das Beenden nicht ab

    def test_failed_save_is_retried_until_it_works(self):
        for kid in (22116, 22117, 22118):
            self.step(REPLAY, [card(1, 0, rr.HAND, kid=kid)])
        real, tries = self.db.save_replay, []

        def flaky(info, frames):
            tries.append(1)
            if len(tries) == 1:
                raise sqlite3.OperationalError("database is locked")
            return real(info, frames)
        self.db.save_replay = flaky
        with self.assertRaises(sqlite3.OperationalError):
            self.step(["HomeViewController"])                # Replay zu Ende, Speichern scheitert
        self.assertEqual(self.saved, [])
        self.step(["HomeViewController"])                    # nächster Durchgang versucht es noch einmal
        self.step(["HomeViewController"])                    # und nur einmal
        self.assertEqual(len(tries), 2)
        self.assertEqual([steps for _, steps in self.saved], [3])
        self.assertEqual(len(self.db.replay_at(1791623726).frames), 3)

    def test_protocol_entries_land_in_the_frame_they_were_read_in(self):
        act = {"k": "act", "a": 11, "c": 22116, "t": 0, "f": 13, "z": 0, "cost": 0}
        res = {"k": "chain", "c": 22116, "n": 1, "t": 0, "res": 1}
        self.memory.log = [act]
        self.step(REPLAY, [card(1, 0, rr.HAND, kid=22116)])          # erster Blick: alles bisherige
        self.step(REPLAY)                                              # nichts Neues → kein Schritt
        self.memory.log = [act, res]
        self.step(REPLAY)                                              # Feld gleich, aber neue Zeile → Schritt
        self.step(REPLAY, [card(1, 0, 2, kid=22116)])                  # Feld neu, Protokoll nicht → ohne "log"
        self.assertEqual([f.get("log") for f in self.rec.frames], [[act], [res], None])
        self.step(["HomeViewController", "ColosseumHistoryViewController"])
        frames = self.db.replay_at(1791623726).frames
        self.assertEqual([f.get("log") for f in frames], [[act], [res], None])

    def test_protocol_error_never_stops_the_recording(self):
        self.memory.log_error = OSError("nicht lesbar")
        with mock.patch.object(rr, "dlog") as dlog:
            self.step(REPLAY, [card(1, 0, rr.HAND, kid=22116)])
            self.step(REPLAY, [card(1, 0, 2, kid=22116)])
        self.assertEqual(len(self.rec.frames), 2)
        self.assertTrue(all("log" not in f for f in self.rec.frames))
        self.assertEqual(sum("Protokoll" in c.args[0] for c in dlog.call_args_list), 1)   # nur einmal gemeldet

    def test_protocol_is_not_read_outside_a_replay(self):
        self.memory.log = [{"k": "phase", "p": 1}]
        self.step(LIVE_DUEL, [card(1, 0, rr.HAND, kid=22116)])
        self.assertNotIn("log", self.memory.reads)

    def test_log_has_no_player_names(self):
        with mock.patch.object(rr, "dlog") as dlog:
            self.step(REPLAY, [card(1, 0, rr.HAND, kid=22116)])
        self.assertTrue(dlog.called)
        for call in dlog.call_args_list:
            for name in DUEL["name"]:
                self.assertNotIn(name, call.args[0])

    def test_too_short_recordings_are_dropped(self):
        self.step(REPLAY, [card(1, 0, rr.HAND, kid=22116)])
        self.step(LIVE_DUEL)
        self.assertEqual(self.saved, [])

    def test_memory_mode_off(self):
        self.rec.enabled = False
        self.step(REPLAY, [card(1, 0, rr.HAND)])
        self.assertEqual(self.memory.reads, [])


class StorageTest(unittest.TestCase):
    def test_matched_by_start_time_and_longer_recording_kept(self):
        db = make_db()
        info = rr.replay_info(DUEL)
        frames = [{"cards": [], "lp": {}, "turn": "", "phase": "", "t": i} for i in range(5)]
        self.assertTrue(db.save_replay(info, frames))
        self.assertFalse(db.save_replay(info, frames[:3]))  # kürzerer Mitschnitt ersetzt nicht
        self.assertEqual(len(db.replay_at(1791623726).frames), 5)
        self.assertIsNone(db.replay_at(1791623727))                      # nur genau diese Startzeit

    def test_recording_with_log_replaces_old_one_without(self):
        info = rr.replay_info(DUEL)
        plain = lambda n: [{"cards": [], "lp": {}, "turn": "", "phase": "", "t": i} for i in range(n)]  # noqa: E731
        logged = lambda n: [dict(f, log=[]) for f in plain(n)]  # noqa: E731
        for old, new, saved in ((plain(10), logged(8), True), (plain(10), logged(7), False),
                                (logged(10), logged(9), False)):
            db = make_db()
            db.save_replay(info, old)
            self.assertEqual(db.save_replay(info, new), saved)
            self.assertEqual(len(db.replay_at(info.start).frames), len(new if saved else old))

    def test_equal_length_never_drops_the_log(self):
        info = rr.replay_info(DUEL)
        plain = [{"cards": [], "lp": {}, "turn": "", "phase": "", "t": i} for i in range(10)]
        logged = [dict(f, log=[{"k": "phase", "p": 1}]) for f in plain]
        for old, new, saved in ((logged, plain, False), (plain, plain, True), (logged, logged, True),
                                (plain, logged, True)):
            db = make_db()
            db.save_replay(info, old)
            self.assertEqual(db.save_replay(info, new), saved)
            kept = db.replay_at(info.start).frames
            self.assertEqual(any("log" in f for f in kept), (new if saved else old) is logged)

    def test_each_replay_belongs_to_one_match(self):
        db = make_db()
        add_match(db, "1")
        add_match(db, "2", 1791623730)
        db.save_replay(rr.replay_info(DUEL), [])
        self.assertEqual(list(db.replay_starts_by_match()), ["1"])

    def test_replay_info_needs_loaded_duel(self):
        self.assertIsNone(rr.replay_info(None))
        self.assertIsNone(rr.replay_info({"MyID": 0}))


@unittest.skipUnless(HAS_TK, "kein Tk")
class PlaybackTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

    def test_place_mirrors_the_opponent(self):
        from duel_view import DuelView
        self.assertEqual(DuelView.place(0, True), ("my_m", 1))
        self.assertEqual(DuelView.place(0, False), ("opp_m", 5))   # sein linkes Feld liegt rechts
        self.assertEqual(DuelView.place(5, True), ("emz", 2))
        self.assertEqual(DuelView.place(5, False), ("emz", 4))
        self.assertEqual(DuelView.place(7, True), ("my_s", 1))
        self.assertEqual(DuelView.place(rr.GRAVE, True), ("my_m", 6))
        self.assertEqual(DuelView.place(rr.GRAVE, False), ("opp_m", 0))
        self.assertIsNone(DuelView.place(rr.MOVING, True))

    def test_view_plays_recording(self):
        import duel_view as dv
        db = make_db()
        frames = [{"cards": [[1, 0, rr.HAND, 0, 22116, 1, 1, 0, 0], [2, 1, rr.HAND, 0, 0, 0, 1, 0, 0]],
                   "lp": {"0": 8000, "1": 8000}, "turn": "Turn 1", "phase": "Main 1", "t": 0},
                  {"cards": [[1, 0, 2, 0, 22116, 1, 1, 2500, 1800], [2, 1, rr.HAND, 0, 0, 0, 1, 0, 0]],
                   "lp": {"0": 8000, "1": 8000}, "turn": "Turn 1", "phase": "Main 1", "t": 1},
                  {"cards": [[1, 0, rr.GRAVE, 0, 22116, 1, 1, 0, 0], [3, 1, 0, 0, 4007, 1, 0, 1000, 2000]],
                   "lp": {"0": 6000, "1": 8000}, "turn": "Turn 2", "phase": "Battle", "t": 2}]
        db.save_replay(rr.replay_info(DUEL), frames)
        add_match(db)
        match = MatchEntry("8385", 1791623727, 3, 1, True, 3, 1, "Elfnote", "", "Sky Striker", "", "Zwölfnote", True)
        with mock.patch.object(dv.card_images, "download", return_value=0):
            view = dv.DuelView(self.root, db, [match], image_folder=tempfile.mkdtemp(), replay=True)
        self.addCleanup(view.close)
        self.root.update()
        texts = lambda: [view.canvas.itemcget(i, "text") for i in view.canvas.find_all()  # noqa: E731
                         if view.canvas.type(i) == "text"]
        self.assertEqual(view.mode, "replay")
        self.assertIn("Schritt 1 / 3", view.step_label.cget("text"))
        view.ctrl_buttons["next"].invoke()
        view._finish_anim(draw=True)                      # ein Schritt vor gleitet; hier gleich der Endzustand
        self.assertIn("2500 / 1800", texts())
        view.ctrl_buttons["last"].invoke()
        self.assertIn("LP 6000", texts())
        self.assertIn("1", texts())                       # Friedhof: 1 Karte
        self.assertIn("Turn 2", view.step_label.cget("text"))
        view.ctrl_buttons["first"].invoke()
        view.toggle_play()
        self.assertIsNotNone(view._job)
        view.toggle_play()
        self.assertIsNone(view._job)
        view.mode_btn.invoke()                            # Deck-Ansicht
        self.assertEqual(view.mode, "decks")
        self.assertIn("SIEG", texts())


class ExportTest(unittest.TestCase):
    def setUp(self):
        import replay_export
        self.re = replay_export
        self.db = make_db()
        with self.db._connect() as con:  # Konami-IDs → Passcodes wie in der echten Datenbank
            con.executemany("INSERT INTO konami VALUES (?, ?, ?)",
                            [("1001", 22116, "Elfnote Regina"), ("1002", 4007, "Ash Blossom")])
        frames = [{"cards": [[1, 0, rr.HAND, 0, 22116, 1, 1, 0, 0]], "lp": {"0": 8000, "1": 8000}, "turn": "Turn 1",
                   "phase": "Main 1", "t": 0},
                  {"cards": [[1, 0, 2, 0, 22116, 1, 1, 2500, 1800]], "lp": {"0": 8000, "1": 7000}, "turn": "Turn 1",
                   "phase": "Main 1", "t": 1},
                  {"cards": [[1, 0, rr.GRAVE, 0, 22116, 1, 1, 0, 0]], "lp": {"0": 8000, "1": 7000},
                   "turn": "Turn 2", "phase": "</script><b>x", "t": 2}]
        self.db.save_replay(rr.replay_info(dict(DUEL, Deck=[{"Main": {"CardIds": [22116]}, "Extra": {}},
                                                            {"Main": {"CardIds": [4007]}, "Extra": {}}])), frames)
        self.replay = self.db.replay_at(1791623726)
        add_match(self.db)
        self.images = tempfile.mkdtemp()
        with open(os.path.join(self.images, "1001.jpg"), "wb") as f:
            f.write(bytes([0xFF, 0xD8]) + b"bild")

    def test_single_file_with_images_and_hidden_opponent(self):
        match = MatchEntry("8385", 1791623727, 3, 1, True, 3, 1, "Elfnote", "", "Sky Striker", "", "Zwölfnote", True)
        with mock.patch.object(self.re.card_images, "download", return_value=0) as download:
            path = self.re.export(self.replay, match, self.db, folder=tempfile.mkdtemp(), image_folder=self.images)
        download.assert_called_once()
        self.assertTrue(os.path.basename(path).startswith("Replay_Elfnote_vs_Sky_Striker_"))
        with open(path, encoding="utf-8") as f:
            html = f.read()
        self.assertEqual(html.count("</script>"), 1)                  # Text aus dem Spiel bricht die Seite nicht
        data = self.re.build_data(self.replay, match, self.db, folder=self.images, download=False)
        self.assertEqual(data["names"], ["Raijn", "Gegner"])           # Gegnername ausgeblendet
        self.assertEqual(data["kid"], {"22116": "1001"})
        self.assertTrue(data["cards"]["1001"]["img"].startswith("data:image/jpeg;base64,"))
        self.assertNotIn("img", data["cards"]["1002"])                # ohne Bild: nur der Name
        self.assertEqual(data["decks"], [{"main": ["1001"], "extra": []}, {"main": ["1002"], "extra": []}])
        self.assertIn("SIEG", data["result"])
        self.assertEqual(len(data["frames"]), 3)
        shown = self.re.build_data(self.replay, match, self.db, hide_opponent=False, folder=self.images,
                                   download=False)
        self.assertEqual(shown["names"], ["Raijn", "WXGY"])
        # Zweiter Export überschreibt nicht
        with mock.patch.object(self.re.card_images, "download", return_value=0):
            second = self.re.export(self.replay, match, self.db, folder=os.path.dirname(path),
                                    image_folder=self.images)
        self.assertNotEqual(path, second)

    def test_export_unknown_cards_as_card_backs_not_blue_eyes(self):
        # Wenn im Gegnerdeck unaufgedeckte 4007 (Blue-Eyes) liegen und im Duell kein Blue-Eyes vorkam:
        # Sie müssen als "unknown" markiert sein (Kartenrückseite), NIEMALS als Blue-Eyes!
        db = make_db()
        with db._connect() as con:
            con.execute("INSERT INTO konami VALUES ('1001', 22116, 'Elfnote Regina')")
            con.execute("INSERT INTO konami VALUES ('89631139', 4007, 'Blue-Eyes White Dragon')")

        # Replay mit unaufgedeckten Dummy-Karten
        replay_info = rr.replay_info(dict(DUEL, time=1791699999, Deck=[
            {"Main": {"CardIds": [22116, 4007, 4007]}, "Extra": {}},
            {"Main": {"CardIds": [4007, 22116, 4007]}, "Extra": {"CardIds": [4007]}}
        ]))
        # In frames kommt nur 22116 vor, 4007 wird NIE aufgedeckt
        frames = [{"cards": [[1, 0, rr.HAND, 0, 22116, 1, 1, 0, 0]]}]
        db.save_replay(replay_info, frames)
        replay = db.replay_at(1791699999)

        # Match mit eigenem vollständigen YDK Deck-Code
        match = MatchEntry("8385", 1791623727, 3, 1, True, 3, 1, "Elfnote",
                           "#main\n1001\n1001\n#extra\n!side\n", "Opponent", "", "Zwölfnote", True)

        data = self.re.build_data(replay, match, db, folder=self.images, download=False)

        # 1. Eigenes Deck nutzt den realen match.deck_code
        self.assertEqual(data["decks"][0], {"main": ["1001", "1001"], "extra": []})

        # 2. Gegnerdeck hat die bekannten Karten zuerst, Unbekannte als "unknown"
        # 22116 (Passcode 1001) ist bekannt, 4007 unaufgedeckt -> "unknown"
        opp_main = data["decks"][1]["main"]
        opp_extra = data["decks"][1]["extra"]
        self.assertEqual(opp_main, ["1001", "unknown", "unknown"])
        self.assertEqual(opp_extra, ["unknown"])

        # 3. cards["unknown"] ist definiert für die Kartenrückseite
        self.assertIn("unknown", data["cards"])
        self.assertEqual(data["cards"]["unknown"]["name"], "Unbekannte Karte")
        # 89631139 (Blue-Eyes) darf NICHT in cards sein!
        self.assertNotIn("89631139", data["cards"])

    @unittest.skipUnless(HAS_TK, "kein Tk")
    def test_export_button_in_duel_view(self):
        import duel_view as dv
        root = tk.Tk()
        root.withdraw()
        self.addCleanup(root.destroy)
        match = MatchEntry("8385", 1791623727, 3, 1, True, 3, 1, "Elfnote", "", "Sky Striker", "", "Zwölfnote", True)
        with mock.patch.object(dv.card_images, "download", return_value=0):
            view = dv.DuelView(root, self.db, [match], image_folder=self.images, replay=True)
        self.addCleanup(view.close)
        folder = tempfile.mkdtemp()
        with mock.patch.object(dv.card_images, "download", return_value=0), \
                mock.patch("replay_export.downloads_dir", return_value=folder), \
                mock.patch.object(dv.DuelView, "_reveal") as reveal:
            view.hide_btn.invoke()                      # Gegnername an
            view.export_btn.invoke()
            end = time.time() + 5
            while not reveal.called and time.time() < end:
                root.update()
                time.sleep(0.02)
        path = reveal.call_args.args[0]
        with open(path, encoding="utf-8") as f:
            self.assertIn("WXGY", f.read())
        self.assertIn("Gespeichert", view.step_label.cget("text"))
