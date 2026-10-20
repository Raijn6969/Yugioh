"""Replay-Schritte: Bewegungen zwischen Frames, Art des Schritts, Dauer, Schritt-Text (reine Logik, ohne Tk)."""

import unittest

import replay_recorder as rr
import replay_steps as rs

NAMES = {100: "Ash Blossom", 200: "Accesscode Talker", 300: "Triple Tactics Talent", 400: "Number 39"}
TYPES = {100: "Effect Monster", 200: "Link Monster", 300: "Spell Card", 400: "Xyz Monster"}
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
    return rs._classify(rs.moves(a, b), st or state(), prev or state())[0]


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
        flip = lambda team, face: rs.describe(self.move(team)._replace(face_after=face),  # noqa: E731
                                              "umgedreht", name_of, 0)
        self.assertEqual(flip(1, 1), "Gegner deckt Ash Blossom auf")
        self.assertEqual(flip(0, 0), "Du drehst Ash Blossom um")                        # Zurückdrehen: nie "aufdecken"
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

    def test_flip_belongs_to_set_or_activation_and_has_no_row_of_its_own(self):
        z = rr.SPELL_ZONES[0]
        cards = [(row(1, 0, rr.HAND, 100),), (row(1, 0, z, 100, 0),), (row(1, 0, z, 100, 1),), (row(1, 0, z, 100, 0),),
                 (row(1, 0, z, 100, 1),)]
        act = {"uid": 1, "team": 0, "card": 100}
        steps = rs.build_steps([frame(*c) for c in cards], [state()] * 4 + [state(act)], 0, name_of, type_of)
        self.assertEqual([s.kind for s in steps], ["still", "gesetzt", "still", "still", "aktiviert"])
        self.assertEqual([s.text for s in steps if s.text], ["Du setzt eine Karte", "Du aktivierst Ash Blossom"])

    def test_plain_flip_back_never_says_reveal(self):
        z = rr.SPELL_ZONES[0]
        steps = rs.build_steps([frame(row(1, 0, z, 100, 1)), frame(row(1, 0, z, 100, 0))], [state()] * 2, 0, name_of)
        self.assertEqual((steps[1].kind, steps[1].text), ("umgedreht", "Du drehst Ash Blossom um"))

    def test_activation_without_a_move_still_has_a_focus(self):
        frames = [frame(), frame()]
        act = {"uid": 7, "team": 1, "card": 100}
        steps = rs.build_steps(frames, [state(), state(act)], 0, name_of, type_of)
        self.assertEqual((steps[1].kind, steps[1].text, steps[1].label),
                         ("aktiviert", "Gegner aktiviert Ash Blossom", "EFFEKT"))

    def test_opening_hand_before_the_first_phase_has_no_step(self):
        deck, hand = row(1, 0, rr.DECK, 100), row(1, 0, rr.HAND, 100)
        steps = rs.build_steps([frame(deck, phase=""), frame(hand, phase=""), frame(hand, phase="Main1")],
                               [state()] * 3, 0, name_of, type_of)
        self.assertEqual((steps[1].kind, steps[1].text, steps[1].label, steps[1].dur), ("still", "", "", 0.0))
        for phase in ("Draw", "Main1"):   # ab der ersten Phase bleibt es ein Zug in die Hand
            steps = rs.build_steps([frame(deck, phase=phase), frame(hand, phase=phase)], [state()] * 2, 0, name_of,
                                   type_of)
            self.assertEqual(steps[1].kind, "hand")
        logged = [frame(deck, phase=""), dict(frame(hand, phase=""), log=[{"k": "phase", "p": 0}])]
        self.assertEqual(rs.build_steps(logged, [state()] * 2, 0, name_of, type_of)[1].kind, "hand")



def act(code, card, team=0, zone=13, cost=0, **more):
    return dict({"k": "act", "a": code, "c": card, "t": team, "f": zone, "z": 0, "cost": cost}, **more)


def chain_entry(n, card, team=0, res=0):
    return {"k": "chain", "c": card, "n": n, "t": team, "res": res}


def log_steps(*entries, cards=(row(1, 0, rr.HAND, 100), row(2, 1, 0, 200)), prev=None):
    """Mitschnitt aus zwei Frames; der zweite trägt die Protokollzeilen (Schritt 1)."""
    frames = [frame(*(prev if prev is not None else cards)), dict(frame(*cards), log=list(entries))]
    return rs.build_steps(frames, [state(), state()], 0, name_of, type_of)


class ChainStatesTest(unittest.TestCase):
    def test_chain_builds_up_and_resolves_link_by_link(self):
        logs = [None, [act(11, 100)], [chain_entry(2, 200, 1), act(11, 200, 1)], [chain_entry(2, 200, 1, res=1)],
                [chain_entry(1, 100, 0, res=1)], None, [act(11, 300)]]
        states = rs.chain_states([dict(frame(), log=log) if log else frame() for log in logs])
        links = lambda i: [(x["cl"], x["card"]) for x in states[i]["links"]]  # noqa: E731
        self.assertEqual([links(i) for i in range(7)], [[], [(1, 100)], [(1, 100), (2, 200)], [(1, 100), (2, 200)],
                                                         [(1, 100)], [], [(1, 300)]])
        self.assertEqual([s["res"] for s in states], [0, 0, 0, 2, 1, 0, 0])

    def test_new_chain_in_the_frame_where_the_old_one_ends(self):
        states = rs.chain_states([dict(frame(), log=[act(11, 100)]),
                                  dict(frame(), log=[chain_entry(1, 100, res=1), act(11, 300)])])
        self.assertEqual([(x["cl"], x["card"]) for x in states[1]["links"]], [(1, 300)])


class LogStepTest(unittest.TestCase):
    def one(self, *entries, **kw):
        step = log_steps(*entries, **kw)[1]
        return step.kind, step.text, step.label

    def test_every_event_kind(self):
        self.assertEqual(self.one(act(11, 100)), ("aktiviert", "Du aktivierst Ash Blossom", "EFFEKT"))
        self.assertEqual(self.one(act(11, 300)), ("aktiviert", "Du aktivierst Triple Tactics Talent", "AKTIVIERT"))
        self.assertEqual(self.one(act(5, 200, 1, 14)),
                         ("beschworen", "Gegner beschwört Accesscode Talker (Linkbeschwörung)", "LINKBESCHWÖRUNG"))
        self.assertEqual(self.one(act(3, 100)), ("beschworen", "Du beschwörst Ash Blossom", "BESCHWOREN"))
        self.assertEqual(self.one(act(49, 100, cost=1)),
                         ("kosten", "Du schickst Ash Blossom als Kosten auf den Friedhof", "KOSTEN"))
        self.assertEqual(self.one(act(46, 100, 1, cost=1))[1], "Gegner verbannt Ash Blossom als Kosten")
        self.assertEqual(self.one(act(51, 100, cost=1))[1], "Du wirfst Ash Blossom als Kosten ab")
        self.assertEqual(self.one(act(24, 100, cost=1))[:2], ("kosten", "Du zahlst Ash Blossom als Kosten"))
        self.assertEqual(self.one(act(23, 100)), ("ziel", "Ash Blossom wird zum Ziel", "ZIEL"))
        self.assertEqual(self.one(act(12, 100)), ("negiert", "Ash Blossom wird negiert", "NEGIERT"))
        self.assertEqual(self.one(act(20, 100)), ("zerstoert", "Ash Blossom wird durch Kampf zerstört", "ZERSTÖRT"))
        self.assertEqual(self.one(act(21, 100))[1], "Ash Blossom wird durch Effekt zerstört")
        self.assertEqual(self.one(act(10, 100, 0, 1, rc=200, zt=1)),
                         ("angriff", "Ash Blossom greift Accesscode Talker an", "ANGRIFF"))
        self.assertEqual(self.one(act(10, 100, 0, 1))[1], "Ash Blossom greift direkt an")
        self.assertEqual(self.one(act(26, 100)), ("hand", "Du fügst Ash Blossom deiner Hand hinzu", ""))
        self.assertEqual(self.one(act(1, 0)), ("hand", "Du ziehst eine Karte", ""))
        self.assertEqual(self.one(act(13, 0, 1, 13)), ("gesetzt", "Gegner setzt eine Karte", "GESETZT"))
        self.assertEqual(self.one(chain_entry(2, 200, 1, res=1)), ("kette", "Kette 2 löst auf", "KETTE 2 LÖST AUF"))

    def test_xyz_summon_material_and_showing_as_cost(self):
        self.assertEqual(self.one(act(7, 400)),
                         ("beschworen", "Du beschwörst Number 39 (Xyz-Beschwörung)", "XYZ-BESCHWÖRUNG"))
        self.assertEqual(self.one(act(28, 100)), ("material", "Ash Blossom wird Xyz-Material", ""))
        self.assertEqual(self.one(act(31, 100)), ("kosten", "Du zeigst Ash Blossom als Kosten vor", "KOSTEN"))
        self.assertEqual(self.one(act(31, 100, 1))[1], "Gegner zeigt Ash Blossom als Kosten vor")
        step = log_steps(act(28, 100), act(7, 400))[1]
        self.assertEqual((step.kind, step.text), ("beschworen", "Du beschwörst Number 39 (Xyz-Beschwörung)"
                                                                " · Ash Blossom wird Xyz-Material"))
        self.assertEqual(step.marks, ())

    def test_cost_sending_itself_to_the_graveyard(self):
        self.assertEqual(self.one(act(50, 100, cost=1)),
                         ("kosten", "Du schickst Ash Blossom als Kosten auf den Friedhof", "KOSTEN"))
        self.assertEqual(self.one(act(50, 100, 1, cost=1))[1],
                         "Gegner schickt Ash Blossom als Kosten auf den Friedhof")

    def test_control_change_text_from_the_view_of_the_new_owner(self):
        self.assertEqual(self.one(act(22, 200, 1, 0, zt=0)),
                         ("kontrolle", "Du übernimmst die Kontrolle über Accesscode Talker", ""))
        self.assertEqual(self.one(act(22, 100, 0, 1, zt=1))[1], "Gegner übernimmt die Kontrolle über Ash Blossom")
        step = log_steps(act(22, 200, 1, 0, zt=0), act(23, 100))[1]     # Rang wie "ziel": Ziel bleibt Hauptereignis
        self.assertEqual(step.kind, "ziel")

    def test_chain_number_in_the_activation_text(self):
        step = log_steps(chain_entry(2, 200, 1), act(11, 200, 1, 0))[1]
        self.assertEqual(step.text, "Gegner aktiviert Accesscode Talker (Kette 2)")
        self.assertEqual([x["cl"] for x in step.chain["links"]], [2])      # (das erste Glied fehlt in dieser Probe)

    def test_main_event_by_rank_and_the_rest_in_the_text(self):
        step = log_steps(act(23, 200, 1, 0), act(49, 100, cost=1), act(11, 100))[1]
        self.assertEqual(step.kind, "aktiviert")
        self.assertEqual(step.text, "Du aktivierst Ash Blossom · Du schickst Ash Blossom als Kosten auf den Friedhof"
                                    " · Accesscode Talker wird zum Ziel")
        self.assertEqual([(m.kind, m.focus.card) for m in step.marks], [("ziel", 200), ("kosten", 100)])

    def test_cards_are_found_by_card_team_and_zone_or_the_text_stays_alone(self):
        step = log_steps(act(23, 200, 1, 0), act(23, 100, 1, 0), act(23, 999, 1, 0))[1]
        self.assertEqual([m.focus.uid for m in step.marks], [2, -1, -1])   # Karte 100 gehört dem anderen Spieler
        self.assertEqual(step.focus.uid, 2)
        gone = log_steps(act(20, 200, 1, 0), cards=(row(1, 0, rr.HAND, 100),),
                         prev=(row(1, 0, rr.HAND, 100), row(2, 1, 0, 200)))[1]
        self.assertEqual((gone.focus.uid, gone.focus.to_zone), (2, None))     # nur noch im Frame davor
        hidden = log_steps(act(11, 100, 1, rr.HAND), cards=(row(5, 1, rr.HAND, 0),))[1]
        self.assertEqual((hidden.focus.uid, hidden.focus.card), (5, 100))     # verdeckt: über Spieler + Zone

    def test_attack_mark_knows_the_target(self):
        mark = log_steps(act(10, 100, 0, 1, rc=200, zt=1), cards=(row(1, 0, 1, 100), row(2, 1, 0, 200)))[1].marks[0]
        self.assertEqual((mark.kind, mark.focus.uid, mark.other.uid), ("angriff", 1, 2))

    def test_direct_attack_with_the_player_number_in_rc(self):
        self.assertEqual(self.one(act(10, 100, 0, 1, rc=1, zt=0))[1], "Ash Blossom greift direkt an")
        mark = log_steps(act(10, 100, 0, 1, rc=1, zt=0))[1].marks[0]
        self.assertEqual((mark.kind, mark.other), ("angriff", None))

    def test_unknown_codes_and_lp_are_neutral(self):
        for entry in (act(25, 100), act(30, 100), act(0, 0, value=-1000), {"k": "phase", "p": 2}):
            self.assertEqual(self.one(entry)[0], "still")
        moved = log_steps(act(25, 100), cards=(row(1, 0, rr.GRAVE, 100),), prev=(row(1, 0, rr.HAND, 100),))[1]
        self.assertEqual(moved.kind, "friedhof")                          # keine eigene Art → Bewegungen entscheiden

    def test_tracker_guess_is_ignored_with_a_protocol(self):
        guess = {"uid": 1, "team": 0, "card": 100}
        frames = [dict(frame(row(1, 0, rr.HAND, 100)), log=[act(25, 100)]), frame(row(1, 0, rr.MOVING, 100))]
        steps = rs.build_steps(frames, [state(), state(guess)], 0, name_of, type_of)
        self.assertNotEqual(steps[1].kind, "aktiviert")
        self.assertEqual(steps[1].chain["res"], 0)

    def test_without_protocol_nothing_changes(self):
        steps = rs.build_steps([frame(row(1, 0, rr.HAND, 100)), frame(row(1, 0, 0, 100))], [state(), state()], 0,
                               name_of, type_of)
        self.assertEqual((steps[1].kind, steps[1].marks, steps[1].chain), ("beschworen", (), None))

    def test_summon_in_the_protocol_is_not_derived_again_from_the_move(self):
        hand, field = (row(1, 0, rr.HAND, 100),), (row(1, 0, 0, 100),)
        logged = dict(frame(*hand), log=[act(3, 100)])
        steps = rs.build_steps([frame(*hand), logged, frame(*field), frame(*hand), frame(*hand), frame(*field)],
                               [state()] * 6, 0, name_of, type_of)
        self.assertEqual([s.kind for s in steps], ["still", "beschworen", "zug", "hand", "still", "beschworen"])
        self.assertEqual((steps[2].text, steps[2].label, steps[2].dur), ("", "", 0.6))   # gleitet, aber keine Zeile
        self.assertEqual(steps[5].text, "Du beschwörst Ash Blossom")   # mehr als 3 Frames später: wieder eine echte
        plain = rs.build_steps([frame(*hand), frame(*hand), frame(*field)], [state()] * 3, 0, name_of, type_of)
        self.assertEqual(plain[2].kind, "beschworen")

    def test_activation_that_moves_onto_the_field_glides_first(self):
        hand, spell = row(1, 0, rr.HAND, 300), row(1, 0, rr.FIELD_ZONE, 300)
        frames = [frame(hand), dict(frame(spell), log=[act(11, 300)])]
        step = rs.build_steps(frames, [state()] * 2, 0, name_of, type_of)[1]
        self.assertEqual((step.kind, step.dur), ("aktiviert", 0.85 + 0.6))
        self.assertAlmostEqual(rs.glide_share(step), 0.6 / 1.45)
        self.assertEqual(rs.duration("aktiviert"), 0.85)                      # ohne Platzwechsel wie bisher

    def test_activation_one_frame_before_the_move_is_carried_by_the_moving_step(self):
        hand, spell = row(1, 0, rr.HAND, 300), row(1, 0, rr.FIELD_ZONE, 300)
        frames = [frame(hand), dict(frame(hand), log=[act(11, 300)]), frame(spell)]
        steps = rs.build_steps(frames, [state()] * 3, 0, name_of, type_of)
        self.assertEqual([s.kind for s in steps], ["still", "still", "aktiviert"])
        self.assertEqual((steps[1].text, steps[1].dur), ("", 0.0))             # erscheint nicht in der Schrittliste
        self.assertEqual((steps[2].text, steps[2].label, steps[2].dur),
                         ("Du aktivierst Triple Tactics Talent", "AKTIVIERT", 0.85 + 0.6))
        again = rs.build_steps([frame(hand), dict(frame(hand), log=[act(11, 300)]), frame(hand)], [state()] * 3, 0,
                               name_of, type_of)
        self.assertEqual([s.kind for s in again], ["still", "aktiviert", "still"])   # Karte bleibt: unverändert

    def test_activation_in_the_graveyard_says_so(self):
        self.assertEqual(self.one(act(11, 300, 0, rr.GRAVE))[1], "Du aktivierst Triple Tactics Talent im Friedhof")
        self.assertEqual(self.one(act(11, 100, 1, rr.BANISHED))[1], "Gegner aktiviert Ash Blossom (verbannt)")
        self.assertEqual(self.one(act(6, 200))[0], "beschworen")                       # Synchrobeschwörung

    def test_activation_in_the_graveyard_waits_until_the_card_lies_there(self):
        field, moving, grave = row(1, 0, 9, 300), row(1, 0, rr.MOVING, 300), row(1, 0, rr.GRAVE, 300)
        frames = [frame(field), dict(frame(moving), log=[act(11, 300, 0, rr.GRAVE)]), frame(grave)]
        steps = rs.build_steps(frames, [state()] * 3, 0, name_of, type_of)
        self.assertEqual((steps[1].kind, steps[1].text), ("friedhof", ""))        # gleitet, ohne Zeile in der Liste
        self.assertGreater(steps[1].dur, 0)
        self.assertEqual((steps[2].kind, steps[2].text, steps[2].focus.to_zone),
                         ("aktiviert", "Du aktivierst Triple Tactics Talent im Friedhof", rr.GRAVE))

    def test_shuffled_back_into_the_deck_has_its_own_line(self):
        self.assertEqual(self.one(act(33, 300, 0, rr.GRAVE, z=rr.DECK))[:2],
                         ("deck", "Du mischst Triple Tactics Talent ins Deck"))
        self.assertEqual(self.one(act(33, 200, 1, rr.GRAVE, z=rr.EXTRA_DECK))[1],
                         "Gegner legt Accesscode Talker ins Extra Deck zurück")
        self.assertEqual(self.one(act(33, 100, 0, rr.BANISHED, z=rr.HAND))[0], "still")  # Hand: zeigt die Bewegung
        both = log_steps(act(33, 300, 0, rr.GRAVE, z=rr.DECK), act(33, 100, 0, rr.BANISHED, z=rr.HAND))[1]
        self.assertEqual(both.text, "Du mischst Triple Tactics Talent ins Deck")

    def test_card_reappearing_in_its_pile_is_no_new_move(self):
        entry = {"uid": 1, "card": 100}
        self.assertIsNone(rs._kind_of(rs.Move(1, 0, 100, None, rr.BANISHED, None, 1, None, 0), state(),
                                      state(ban=[entry])))
        self.assertEqual(kind_of(frame(), frame(row(1, 0, rr.BANISHED, 100))), "verbannt")   # wirklich neu

    def test_materials_of_an_extra_deck_summon_move_together(self):
        hand, banished, grave, fusion = (row(1, 0, rr.HAND, 100), row(1, 0, rr.BANISHED, 100),
                                         row(2, 0, rr.GRAVE, 300), row(3, 0, 4, 200))
        start = (hand, row(2, 0, rr.HAND, 300), row(3, 0, rr.EXTRA_DECK, 200))
        frames = [frame(*start), frame(banished, *start[1:]), frame(banished, grave, start[2]),
                  dict(frame(banished, grave, fusion), log=[act(5, 200, 0, rr.EXTRA_DECK)])]
        steps = rs.build_steps(frames, [state()] * 4, 0, name_of, type_of)
        self.assertEqual([s.kind for s in steps], ["still", "still", "friedhof", "beschworen"])
        self.assertEqual(steps[1].dur, 0.0)
        self.assertEqual((steps[2].text, steps[2].since),
                         ("Du: Ash Blossom → Verbannt · Triple Tactics Talent → Friedhof", 0))
        self.assertEqual(len(steps[2].moves), 2)
        alone = rs.build_steps(frames[1:], [state()] * 3, 0, name_of, type_of)    # nur ein Material: bleibt
        self.assertIsNone(alone[1].since)
        rival = [frame(*start), frame(row(1, 1, rr.GRAVE, 100), *start[1:]), frames[2], frames[3]]  # Gegnerkarte
        steps = rs.build_steps(rival, [state()] * 4, 0, name_of, type_of)
        self.assertEqual([s.since for s in steps], [None] * 4)                  # nicht zusammengefasst

    def test_durations_of_the_new_kinds(self):
        kinds = ("kosten", "ziel", "negiert", "zerstoert", "angriff", "kette", "material")
        self.assertEqual([rs.duration(k) for k in kinds],
                         [0.7, 0.6, 0.7, 0.7, 0.8, 0.6, 0.6])


if __name__ == "__main__":
    unittest.main()
