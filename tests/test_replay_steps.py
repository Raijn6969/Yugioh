"""Replay-Schritte: Bewegungen zwischen Frames, Art des Schritts, Dauer, Schritt-Text (reine Logik, ohne Tk)."""

import unittest

import replay_recorder as rr
import replay_steps as rs

NAMES = {100: "Ash Blossom", 200: "Accesscode Talker", 300: "Triple Tactics Talent"}
TYPES = {100: "Effect Monster", 200: "Link Monster", 300: "Spell Card"}
name_of = NAMES.get
type_of = lambda card: TYPES.get(card, "")  # noqa: E731


def row(uid, team, zone, card=0, face=1, index=0):
    return [uid, team, zone, index, card, face, 1, 0, 0]


def frame(*cards, lp=None, phase="Main1"):
    return {"cards": sorted(cards), "lp": lp or {}, "turn": "Turn 1", "phase": phase}


def state(activating=None, dest=None, gy=(), ban=()):
    return {"activating": activating, "dest_zones": dest or {}, "gy": {0: list(gy), 1: []},
            "banished": {0: list(ban), 1: []}, "chain": []}


def kind_of(a, b, st=None, prev=None):
    return rs.kind(rs.moves(a, b), st or state(), prev or state())


class MovesTest(unittest.TestCase):
    def test_new_vanished_zone_face_and_index(self):
        a = frame(row(1, 0, rr.HAND), row(2, 0, rr.DECK), row(3, 0, 0, 100, 0), row(4, 0, rr.HAND, index=1))
        b = frame(row(1, 0, rr.HAND), row(3, 0, 0, 100, 1), row(4, 0, rr.HAND, index=0), row(5, 1, rr.DECK))
        moved = {m.uid: m for m in rs.moves(a, b)}
        self.assertEqual(sorted(moved), [2, 3, 4, 5])                       # uid 1 blieb unverändert
        self.assertEqual((moved[2].from_zone, moved[2].to_zone), (rr.DECK, None))      # verschwunden
        self.assertEqual((moved[5].from_zone, moved[5].to_zone), (None, rr.DECK))      # neu
        self.assertEqual((moved[3].face_before, moved[3].face_after), (0, 1))
        self.assertEqual((moved[4].index_before, moved[4].index_after), (1, 0))
        self.assertEqual(rs.moves(None, frame()), [])

    def test_ease_and_blend(self):
        self.assertEqual((rs.ease(0), rs.ease(1)), (0, 1))
        self.assertGreater(rs.ease(0.5), 0.5)
        self.assertEqual(rs.blend("#ffffff", "#000000", 0.5), "#808080")
        self.assertEqual(rs.blend("#ff0000", "#0000ff", 0), "#0000ff")
        self.assertEqual(rs.blend("#ff0000", "#0000ff", 7), "#ff0000")


class KindTest(unittest.TestCase):
    def test_every_kind(self):
        hand = frame(row(1, 0, rr.HAND, 100))
        self.assertEqual(kind_of(hand, frame(row(1, 0, 0, 100))), "beschworen")
        self.assertEqual(kind_of(hand, frame(row(1, 0, 2, 100, 0))), "gesetzt")  # verdeckt ins Monsterfeld
        self.assertEqual(kind_of(hand, frame(row(1, 0, 9, 100, 0))), "gesetzt")                  # verdeckt in Z/F
        self.assertEqual(kind_of(frame(row(1, 0, 9, 100, 0)), frame(row(1, 0, 9, 100, 1))), "umgedreht")
        self.assertEqual(kind_of(hand, frame(row(1, 0, rr.GRAVE, 100))), "friedhof")
        self.assertEqual(kind_of(hand, frame(row(1, 0, rr.BANISHED, 100))), "verbannt")
        self.assertEqual(kind_of(frame(row(1, 0, rr.DECK)), frame(row(1, 0, rr.HAND, 100))), "hand")
        self.assertEqual(kind_of(hand, frame(row(1, 0, 9, 100, 1))), "zug")  # Zauber offen aufs Feld
        self.assertEqual(kind_of(frame(row(1, 0, 0, 100)), frame(row(1, 0, 2, 100))), "zug")   # Monster wechselt Zone

    def test_activation_comes_from_the_tracker_and_only_when_new(self):
        a, b = frame(row(1, 0, rr.HAND, 100)), frame(row(1, 0, rr.MOVING, 100))
        act = {"uid": 1, "team": 0, "card": 100}
        self.assertEqual(kind_of(a, b, state(act)), "aktiviert")
        self.assertEqual(kind_of(a, b, state(act), state(act)), "zug")              # dieselbe Aktivierung läuft weiter
        self.assertEqual(kind_of(a, b, state(act), state({"uid": 2, "team": 1, "card": 5})), "aktiviert")

    def test_still_steps(self):
        self.assertEqual(kind_of(frame(), frame()), "still")                                           # nur LP/Phase
        self.assertEqual(kind_of(frame(), frame(row(1, 0, rr.DECK))), "still")  # erscheint im Deck
        self.assertEqual(kind_of(frame(), frame(row(1, 1, rr.EXTRA_DECK))), "still")
        self.assertEqual(kind_of(frame(row(1, 0, rr.DECK, index=3)), frame(row(1, 0, rr.DECK, index=2))), "still")
        self.assertEqual(kind_of(frame(row(1, 0, rr.HAND, 100, index=1)), frame(row(1, 0, rr.HAND, 100))), "still")

    def test_moving_card_goes_to_its_destination_once(self):
        on_hand = frame(row(1, 0, rr.HAND, 100))
        moving = frame(row(1, 0, rr.MOVING, 100))
        landed = frame(row(1, 0, rr.GRAVE, 100))
        to_gy = state(dest={1: rr.GRAVE})
        self.assertEqual(kind_of(on_hand, moving, to_gy), "friedhof")                   # Ziel schon beim Aufbruch
        self.assertEqual(kind_of(moving, landed, state(), to_gy), "still")              # Ankunft: nichts mehr zu sehen
        self.assertEqual(kind_of(moving, moving, to_gy, to_gy), "still")
        summon = state(dest={1: 1})
        self.assertEqual(kind_of(on_hand, moving, summon), "beschworen")
        self.assertEqual(kind_of(moving, frame(row(1, 0, 1, 100)), state(), summon), "still")

    def test_vanished_card_in_tracker_pile(self):
        gone = frame()
        entry = {"uid": 1, "card": 100}
        self.assertEqual(kind_of(frame(row(1, 0, 2, 100)), gone, state(gy=[entry])), "friedhof")
        self.assertEqual(kind_of(frame(row(1, 0, 2, 100)), gone, state(ban=[entry])), "verbannt")
        self.assertEqual(kind_of(frame(row(1, 0, 2, 100)), gone), "zug")

    def test_rank_when_several_cards_move(self):
        a = frame(row(1, 0, rr.HAND, 100), row(2, 0, rr.DECK))
        b = frame(row(1, 0, rr.GRAVE, 100), row(2, 0, rr.HAND, 200))
        self.assertEqual(kind_of(a, b), "friedhof")                 # vor "hand"


class DurationTest(unittest.TestCase):
    def test_durations_like_the_factory(self):
        self.assertEqual([rs.duration(k) for k in ("aktiviert", "beschworen", "friedhof", "verbannt", "hand", "zug")],
                         [0.85, 0.7, 0.7, 0.7, 0.8, 0.6])
        self.assertAlmostEqual(rs.duration("gesetzt"), 0.6 + rs.SET_FLIP)
        self.assertEqual(rs.duration("umgedreht"), 0.35)
        self.assertEqual(rs.duration("still"), 0.0)
        self.assertEqual(rs.duration("still", lp_changed=True), 0.5)
        self.assertEqual(rs.STEP_PAUSE, 1.0)


class DescribeTest(unittest.TestCase):
    def move(self, team=0, card=100):
        return rs.Move(1, team, card, None, None, None, None, None, None)

    def text(self, kind, team=0, card=100, phase=""):
        return rs.describe(self.move(team, card), kind, name_of, 0, phase, type_of)

    def test_texts_for_both_sides(self):
        self.assertEqual(self.text("aktiviert"), "Du aktivierst Ash Blossom")
        self.assertEqual(self.text("aktiviert", team=1), "Gegner aktiviert Ash Blossom")
        self.assertEqual(self.text("beschworen", team=1, card=200),
                         "Gegner beschwört Accesscode Talker (Linkbeschwörung)")
        self.assertEqual(self.text("beschworen"), "Du beschwörst Ash Blossom")           # Effektmonster: keine Art
        self.assertEqual(self.text("gesetzt", card=0), "Du setzt eine Karte")
        self.assertEqual(self.text("gesetzt", team=1, card=0), "Gegner setzt eine Karte")
        self.assertEqual(self.text("friedhof", team=1, card=300), "Gegner: Triple Tactics Talent → Friedhof")
        self.assertEqual(self.text("verbannt"), "Du: Ash Blossom → Verbannt")
        self.assertEqual(self.text("umgedreht", team=1), "Gegner deckt Ash Blossom auf")
        self.assertEqual(self.text("zug"), "Du bewegst Ash Blossom")

    def test_draw_search_and_unknown_names(self):
        self.assertEqual(self.text("hand", phase="Draw"), "Du ziehst eine Karte")
        self.assertEqual(self.text("hand", phase="Main1"), "Du fügst Ash Blossom deiner Hand hinzu")
        self.assertEqual(self.text("hand", team=1, card=0, phase="Main1"), "Gegner zieht eine Karte")   # Name unbekannt
        self.assertEqual(self.text("aktiviert", card=999), "Du aktivierst eine Karte")
        self.assertEqual(rs.describe(None, "still", name_of, 0), "")

    def test_summon_and_activation_labels(self):
        for card_type, label in (("Link Monster", "Linkbeschwörung"), ("XYZ Monster", "Xyz-Beschwörung"),
                                 ("Synchro Effect Monster", "Synchrobeschwörung"),
                                 ("Fusion Monster", "Fusionsbeschwörung"),
                                 ("Ritual Effect Monster", "Ritualbeschwörung"), ("Effect Monster", "")):
            self.assertEqual(rs.summon_label(card_type), label)
        self.assertEqual(rs.activation_label("Effect Monster"), "EFFEKT")
        self.assertEqual(rs.activation_label("Spell Card"), "AKTIVIERT")
        self.assertEqual(rs.activation_label("Trap Card"), "AKTIVIERT")
        self.assertEqual(rs.activation_label(""), "AKTIVIERT")


class BuildStepsTest(unittest.TestCase):
    def test_steps_over_a_small_duel(self):
        frames = [frame(row(1, 0, rr.HAND, 100), lp={"0": 8000, "1": 8000}),
                  frame(row(1, 0, rr.HAND, 100), lp={"0": 8000, "1": 8000}),                  # nichts passiert
                  frame(row(1, 0, 0, 100), lp={"0": 8000, "1": 8000}),                        # beschworen
                  frame(row(1, 0, 0, 100), lp={"0": 8000, "1": 7000})]                        # nur LP
        tracker_like = [state() for _ in frames]
        steps = rs.build_steps(frames, tracker_like, 0, name_of, type_of)
        self.assertEqual([s.kind for s in steps], ["still", "still", "beschworen", "still"])
        self.assertEqual(steps[2].text, "Du beschwörst Ash Blossom")
        self.assertEqual(steps[2].label, "BESCHWOREN")
        self.assertEqual(steps[3].lp, {1: -1000})
        self.assertEqual([s.dur for s in steps], [0.0, 0.0, 0.7, 0.5])       # LP-Schritt dauert kurz
        self.assertEqual(steps[0].text, "")

    def test_activation_without_a_move_still_has_a_focus(self):
        frames = [frame(), frame()]
        act = {"uid": 7, "team": 1, "card": 100}
        steps = rs.build_steps(frames, [state(), state(act)], 0, name_of, type_of)
        self.assertEqual((steps[1].kind, steps[1].text, steps[1].label),
                         ("aktiviert", "Gegner aktiviert Ash Blossom", "EFFEKT"))


if __name__ == "__main__":
    unittest.main()
