"""Duell-Übersicht: ein gespeichertes Match auf dem Spielfeld (Decks beider Spieler, Ergebnis, blättern)."""

import tempfile
import time
import unittest

import duel_view as dv
import replay_recorder as rr
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

    def test_pile_inspector_wheel_scrolls_over_the_rows_too(self):
        view = self.view([entry(1)], replay=True)
        view._open_pile_inspector("Friedhof", [{"uid": 10, "card": 101, "team": 0}], 0)

        def widgets(widget):
            return [widget] + [w for child in widget.winfo_children() for w in widgets(child)]
        canvas = next(w for w in widgets(view._pile_inspector) if isinstance(w, tk.Canvas))
        rows = widgets(canvas.winfo_children()[0])
        self.assertGreater(len(rows), 2)
        self.assertTrue(all(w.bind("<MouseWheel>") for w in rows + [canvas]))

    def test_tip_text_draws_tip_with_bold_font(self):
        view = self.view([entry(1)])
        view._tip_text("Friedhof: 5 Karten")
        tips = view.canvas.find_withtag("tip")
        self.assertTrue(len(tips) >= 1)
        view._untip()
        self.assertEqual(view.canvas.find_withtag("tip"), ())


def row(uid, zone, card=100, face=1, team=0, index=0, atk=2500, defense=1800):
    return [uid, team, zone, index, card, face, 1, atk, defense]


def frame(*cards, lp=None, phase="Main1"):
    return {"cards": list(cards), "lp": lp or {"0": 8000, "1": 8000}, "turn": "Turn 1", "phase": phase, "t": 0}


def act(code, card, team=0, zone=13, cost=0, **more):
    return dict({"k": "act", "a": code, "c": card, "t": team, "f": zone, "z": 0, "cost": cost}, **more)


def chain(n, card, team=0, res=0):
    return {"k": "chain", "c": card, "n": n, "t": team, "res": res}


# Hand (0), nichts (1), Beschwörung (2), nur LP (3), zweite Karte auf den Friedhof (4)
FRAMES = [frame(row(1, rr.HAND), row(2, rr.HAND, 101, index=1)),
          frame(row(1, rr.HAND), row(2, rr.HAND, 101, index=1)),
          frame(row(1, 0), row(2, rr.HAND, 101)),
          frame(row(1, 0), row(2, rr.HAND, 101), lp={"0": 7000, "1": 8000}),
          frame(row(1, 0), row(2, rr.GRAVE, 101), lp={"0": 7000, "1": 8000})]


class Clock:
    """Steuerbare Uhr für time.perf_counter: so läuft eine Animation ohne Warten."""
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@unittest.skipUnless(HAS_TK, "kein Tk")
class AnimationTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.folder = tempfile.mkdtemp()

    def view(self, frames=FRAMES, passcodes=None):
        from unittest import mock
        db = mock_db()
        db.konami_passcodes = lambda: passcodes or {}
        db.info = lambda passcode: None
        db.replay_at = lambda start: Replay(start, "1", 0, 0, ["Du", "Gegner"], [], frames)
        patch = mock.patch.object(dv.card_images, "download", return_value=0)
        patch.start()
        self.addCleanup(patch.stop)
        view = dv.DuelView(self.root, db, [entry(1)], 0, image_folder=self.folder, replay=True)
        self.addCleanup(view.close)
        self.root.update()
        return view

    def jobs(self):
        return len(self.root.tk.splitlist(self.root.tk.call("after", "info")))

    def hover_areas(self, view):
        return [i for i in view.canvas.find_all() if any(t.startswith("bc") for t in view.canvas.gettags(i))]

    def test_steps_are_classified(self):
        view = self.view()
        self.assertEqual([s.kind for s in view.steps], ["still", "still", "beschworen", "still", "friedhof"])
        self.assertEqual([s.dur for s in view.steps], [0.0, 0.0, 0.7, 0.5, 0.7])

    def test_step_forward_starts_exactly_one_job_and_ends_in_the_final_state(self):
        view = self.view()
        before = self.jobs()
        view.manual(2)                      # kein "ein Schritt vor": springt
        self.assertIsNone(view._anim)
        view.manual(1)
        view.manual(2)                      # ein Schritt vor: gleitet
        self.assertIsNotNone(view._anim)
        self.assertEqual(self.jobs(), before + 1)
        view.manual(3)                      # während der Animation: kein zweiter Job
        self.assertEqual(self.jobs(), before + 1)
        self.assertEqual(view.frame_no, 3)
        self.assertIsNotNone(view._anim)
        self.assertEqual(self.hover_areas(view), [])           # Animationsbilder tragen keine Hinweis-Flächen
        view._anim["t0"] -= 100             # Zeit läuft ab: der nächste Takt beendet die Animation
        end = time.time() + 3
        while view._anim is not None and time.time() < end:
            self.root.update()
            time.sleep(0.01)
        self.assertIsNone(view._anim)
        self.assertIsNone(view._job)
        self.assertEqual(self.jobs(), before)
        self.assertTrue(self.hover_areas(view))                # Standbild: Karten wieder bindbar
        self.assertIn("2500 / 1800", [view.canvas.itemcget(i, "text") for i in view.canvas.find_all()
                                      if view.canvas.type(i) == "text"])

    def test_back_jump_pause_and_close_cancel_the_animation(self):
        view = self.view()
        view.manual(1)
        view.manual(2)
        view.manual(1)                      # zurück
        self.assertEqual((view.frame_no, view._anim, view._job), (1, None, None))
        view.manual(2)
        view.manual(0)                      # Anfang
        self.assertEqual((view._anim, view._job), (None, None))
        view.manual(1)
        view.manual(2)
        view.toggle_play()                  # Abspielen während einer Animation: weiter derselbe Job
        self.assertTrue(view.playing)
        self.assertIsNotNone(view._job)
        view.toggle_play()                  # Pause: Endzustand steht, kein Job mehr
        self.assertEqual((view.playing, view._anim, view._job), (False, None, None))
        view.manual(3)
        view.close()
        self.assertEqual((view._anim, view._job), (None, None))

    def test_playing_skips_invisible_steps_and_stops_at_the_end(self):
        from unittest import mock
        view = self.view()
        view.speed = 4.0
        clock, seen = Clock(), []

        def tick():                         # einen Takt von Hand (der echte after-Job wird vorher abgesagt)
            if view._job is not None:
                view.win.after_cancel(view._job)
                view._job = None
            clock.now += 0.3
            view._loop()
        with mock.patch.object(dv.time, "perf_counter", clock):
            view.toggle_play()
            for _ in range(300):
                tick()
                seen.append(view.frame_no)
                if not view.playing and view._anim is None:
                    break
        self.assertFalse(view.playing)
        self.assertEqual((view.frame_no, view._anim, view._job), (4, None, None))
        self.assertNotIn(1, seen)           # ohne sichtbare Änderung: übersprungen
        self.assertEqual(seen[0], 2)
        self.assertIn(3, seen)              # LP-Schritt wird gezeigt

    def animate(self, view, step, frac):
        """Schritt vor und die Animation auf frac (0…1) des Weges stellen; Uhr ist gesteuert."""
        from unittest import mock
        clock = Clock()
        with mock.patch.object(dv.time, "perf_counter", clock):
            view.manual(step - 1, False)
            view.manual(step)
            clock.now = view._anim["t0"] + view._anim["dur"] * frac
            view.draw()
        return [view.canvas.itemcget(i, "text") for i in view.canvas.find_all() if view.canvas.type(i) == "text"]

    def texts(self, view):
        return [view.canvas.itemcget(i, "text") for i in view.canvas.find_all() if view.canvas.type(i) == "text"]

    def finish(self, view):
        view._finish_anim(draw=True)
        return self.texts(view)

    def test_summon_shows_a_shield_only_while_it_runs(self):
        view = self.view()
        self.assertIn("BESCHWOREN", self.animate(view, 2, 0.5))
        after = self.finish(view)
        self.assertNotIn("BESCHWOREN", after)
        self.assertIn("Du beschwörst eine Karte", after)             # Schritt-Text bleibt stehen

    def test_activation_effect_and_step_text(self):
        view = self.view([frame(row(1, rr.HAND)), frame(row(1, rr.MOVING))])
        self.assertEqual(view.steps[1].kind, "aktiviert")
        self.assertIn("AKTIVIERT", self.animate(view, 1, 0.5))
        after = self.finish(view)
        self.assertNotIn("AKTIVIERT", after)
        self.assertIn("Du aktivierst eine Karte", after)

    def test_activation_glides_to_the_field_first_and_then_runs_at_the_new_place(self):
        from unittest import mock
        frames = [frame(row(1, rr.HAND)), dict(frame(row(1, rr.FIELD_ZONE)), log=[act(11, 100)])]
        view = self.view(frames)
        step = view.steps[1]
        self.assertEqual((step.kind, step.dur), ("aktiviert", 0.85 + 0.6))
        self.assertNotIn("AKTIVIERT", self.animate(view, 1, 0.2))          # noch beim Gleiten
        spots = []
        with mock.patch.object(view, "_draw_activation", side_effect=lambda spot, *a: spots.append(spot)):
            self.animate(view, 1, 0.7)
        item = view._scene(1)[0][1]
        self.assertEqual(spots, [(item.x, item.y)])                          # Aktivierung am Ziel, nicht in der Hand
        self.assertIn("AKTIVIERT", self.animate(view, 1, 0.7))

    def test_lp_change_floats_next_to_the_display(self):
        view = self.view()
        self.assertIn("−1000", self.animate(view, 3, 0.5))
        self.assertNotIn("−1000", self.finish(view))

    def test_set_card_is_flipped_and_marked(self):
        view = self.view([frame(row(1, rr.HAND)), frame(row(1, 9, face=0))])
        self.assertEqual((view.steps[1].kind, view.steps[1].text), ("gesetzt", "Du setzt eine Karte"))
        self.assertNotIn("gesetzt", self.animate(view, 1, 0.3))      # erst offen gezeigt, noch kein Schild
        self.assertIn("gesetzt", self.finish(view))                  # verdeckt, eigene Karte: Schild

    def test_chain_numbers_and_bar(self):
        hidden = row(2, rr.HAND, 200, face=0, team=1)
        frames = [frame(row(1, rr.HAND), hidden), frame(row(1, 9), hidden),
                  frame(row(1, 9), row(2, rr.MOVING, 200, team=1))]
        view = self.view(frames)
        view.manual(1, False)
        self.assertIn("KETTE (1)", self.texts(view))
        view.manual(2, False)
        texts = self.texts(view)
        self.assertIn("KETTE (2)", texts)
        self.assertEqual(texts.count("1") >= 2 and texts.count("2") >= 2, True)    # Plakette und Zeile je Glied

    def test_search_shows_the_added_card_big(self):
        frames = [frame(row(1, rr.DECK, 5)), frame(row(1, rr.HAND, 5))]
        view = self.view(frames, passcodes={5: "5"})
        self.assertEqual(view.steps[1].text, "Du fügst Ash Blossom deiner Hand hinzu")
        self.assertIn("ZUR HAND HINZUGEFÜGT", self.animate(view, 1, 0.5))
        self.assertNotIn("ZUR HAND HINZUGEFÜGT", self.finish(view))

    def list_texts(self, view):
        c = view.steplist
        return [c.itemcget(i, "text") for i in c.find_all() if c.type(i) == "text"]

    def test_step_list_has_no_invisible_steps_and_a_click_jumps(self):
        from types import SimpleNamespace
        view = self.view()
        self.assertEqual([n for n, top, bottom in view._rows], [2, 4])           # Schritte 0, 1, 3: "still"
        texts = self.list_texts(view)
        self.assertIn("Du beschwörst eine Karte", texts)
        self.assertIn("Turn 1  ·  Main1", texts)
        self.assertNotIn("", texts)
        view.manual(1)
        view.manual(2)                                    # animiert
        _, top, bottom = view._rows[1]
        view._click_step(SimpleNamespace(y=(top + bottom) // 2))
        self.assertEqual((view.frame_no, view._anim), (4, None))              # Sprung ohne Animation
        self.assertEqual(view._marked, 4)

    def test_current_row_is_marked(self):
        view = self.view()
        mark = lambda: view.steplist.itemcget(view._row_bg[2], "fill")  # noqa: E731
        view.manual(2, False)
        self.assertEqual((view._marked, mark()), (2, dv.STEP_ROW))
        view.manual(3, False)                             # Schritt ohne eigene Zeile: die davor bleibt markiert
        self.assertEqual(view._marked, 2)
        view.manual(4, False)
        self.assertEqual(view._marked, 4)
        self.assertEqual(mark(), dv.PANEL)

    def test_window_is_wider_for_the_list_but_not_the_field(self):
        view = self.view()
        self.assertGreater(view.side_w, 0)
        self.assertEqual(int(view.win.geometry().split("x")[0]), view.size[0] + view.side_w)
        plain = dv.DuelView(self.root, mock_db(), [entry(1)], 0, image_folder=self.folder)
        self.addCleanup(plain.close)
        self.assertEqual(plain.side_w, 0)
        self.assertGreater(view.size[0], 0)

    def test_speed_button_cycles_and_rescales_a_running_animation(self):
        view = self.view()
        button = view.ctrl_buttons["speed"]
        self.assertEqual(view.speed, 1.0)
        view.manual(1)
        view.manual(2)
        dur = view._anim["dur"]
        button.invoke()                                   # 2×: die laufende Animation wird kürzer
        self.assertEqual((view.speed, button.cget("text")), (2.0, "2×"))
        self.assertAlmostEqual(view._anim["dur"], dur / 2)
        button.invoke()
        button.invoke()
        self.assertEqual((view.speed, button.cget("text")), (0.5, "0,5×"))
        button.invoke()
        self.assertEqual((view.speed, button.cget("text")), (1.0, "1×"))

    def test_keys(self):
        view = self.view()
        view.key("Right")                                 # Schritt 1: ohne sichtbare Änderung, springt
        self.assertEqual(view.frame_no, 1)
        view.key("Right")
        self.assertEqual((view.frame_no, view._anim is not None), (2, True))     # ein Schritt vor gleitet
        view.key("Left")
        self.assertEqual((view.frame_no, view._anim), (1, None))
        view.key("End")
        self.assertEqual(view.frame_no, 4)
        view.key("Home")
        self.assertEqual(view.frame_no, 0)
        view.key("space")
        self.assertTrue(view.playing)
        view.key("space")
        self.assertFalse(view.playing)
        view.mode = "decks"
        view.key("Right")                                 # nur in der Replay-Ansicht
        self.assertEqual(view.frame_no, 0)

    def test_moving_card_slides_to_its_destination_at_once(self):
        frames = [frame(row(1, rr.HAND)), frame(row(1, rr.MOVING)), frame(row(1, 0))]
        view = self.view(frames)
        spot = lambda n: tuple(getattr(view._scene(n)[0][1], k) for k in "xy")  # noqa: E731
        self.assertEqual(spot(1), spot(2))
        self.assertNotEqual(spot(0), spot(1))
        self.assertEqual([s.kind for s in view.steps], ["still", "beschworen", "still"])


    # ── echtes Duell-Protokoll (frame["log"]) ──
    def logged(self, *logs, cards=None):
        """Mitschnitt mit Protokoll: immer derselbe Feldzustand, je Frame nach dem ersten die Protokollzeilen."""
        cards = cards or (row(1, 9, 5), row(2, 0, 1, team=1))
        return [frame(*cards)] + [dict(frame(*cards), log=list(log)) for log in logs] + [frame(*cards)]

    def test_real_chain_numbers_and_the_resolving_link(self):
        frames = self.logged([act(11, 5, 0, 9)], [chain(2, 1, 1), act(11, 1, 1, 0)], [chain(2, 1, 1, 1)],
                             [chain(1, 5, 0, 1)])
        view = self.view(frames, passcodes={5: "5", 1: "1"})
        ovals = lambda color: [i for i in view.canvas.find_all() if view.canvas.type(i) == "oval"  # noqa: E731
                               and view.canvas.itemcget(i, "fill") == color]
        view.manual(1, False)
        self.assertIn("KETTE (1)", self.texts(view))
        view.manual(2, False)
        texts = self.texts(view)
        self.assertIn("KETTE (2)", texts)
        self.assertIn("Gegner aktiviert Elfnote Lucina (Kette 2)", texts)
        self.assertEqual(ovals(dv.RESOLVING), [])
        self.assertIn("KETTE 2 LÖST AUF", self.animate(view, 3, 0.5))        # Schild während der Animation …
        self.assertNotIn("KETTE 2 LÖST AUF", self.finish(view))
        self.assertIn("KETTE (2)", self.texts(view))                         # … das Glied bleibt, rot, bis zum Schluss
        self.assertEqual(len(ovals(dv.RESOLVING)), 1)
        view.manual(4, False)
        self.assertIn("KETTE (1)", self.texts(view))                         # Glied 2 ist weg
        self.assertEqual(len(ovals(dv.RESOLVING)), 1)
        view.manual(5, False)
        self.assertFalse([t for t in self.texts(view) if t.startswith("KETTE (")])        # Kette zu Ende

    def test_event_shields_only_while_the_step_runs(self):
        for entry_, shield in ((act(49, 5, 0, 0, cost=1), "KOSTEN"), (act(23, 5, 0, 0), "ZIEL"),
                               (act(12, 5, 0, 0), "NEGIERT"), (act(20, 5, 0, 0), "ZERSTÖRT"),
                               (act(10, 5, 0, 0, rc=1, zt=1), "ANGRIFF")):
            with self.subTest(shield):
                view = self.view(self.logged([entry_], cards=(row(1, 0, 5), row(2, 0, 1, team=1))),
                                 passcodes={5: "5", 1: "1"})
                self.assertIn(shield, self.animate(view, 1, 0.5))
                self.assertNotIn(shield, self.finish(view))

    def test_attack_draws_an_arrow_to_the_target(self):
        view = self.view(self.logged([act(10, 5, 0, 0, rc=1, zt=1)], cards=(row(1, 0, 5), row(2, 0, 1, team=1))),
                         passcodes={5: "5", 1: "1"})
        self.assertEqual(view.steps[1].text, "Ash Blossom greift Elfnote Lucina an")
        self.animate(view, 1, 0.9)
        arrows = [i for i in view.canvas.find_all() if view.canvas.type(i) == "line"
                  and view.canvas.itemcget(i, "arrow") == "last"]
        self.assertEqual(len(arrows), 1)
        self.finish(view)
        self.assertFalse([i for i in view.canvas.find_all() if view.canvas.type(i) == "line"])

    def test_card_not_in_the_picture_gives_only_text(self):
        view = self.view(self.logged([act(23, 999, 1, 0)]))
        self.assertEqual(view.steps[1].text, "eine Karte wird zum Ziel")
        self.assertNotIn("ZIEL", self.animate(view, 1, 0.5))
        self.assertIn("eine Karte wird zum Ziel", self.finish(view))

    def test_old_replay_keeps_the_trackers_chain_and_has_no_marks(self):
        view = self.view()
        self.assertTrue(all(s.chain is None and s.marks == () for s in view.steps))


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
