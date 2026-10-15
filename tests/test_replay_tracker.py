"""Tests für ReplayStateTracker und Replay-Erweiterungen."""

import unittest
from duel_view import ReplayStateTracker
import replay_recorder as rr


class TestReplayTracker(unittest.TestCase):
    def test_empty_frames(self):
        tracker = ReplayStateTracker([])
        self.assertEqual(len(tracker.frame_states), 0)
        st = tracker.get(0)
        self.assertEqual(st["activating"], None)
        self.assertEqual(st["gy"][0], [])
        self.assertEqual(st["chain"], [])

    def test_graveyard_accumulation(self):
        # Frame 0: Card on field (uid 1, team 0, zone 2, index 0, card 101, face 1, atk 1000, def 1000)
        # Frame 1: Card 1 sent to GY (zone 16)
        # Frame 2: Card 2 (uid 2, team 0) sent to GY (zone 16) while card 1 is no longer in frame["cards"]
        # Both cards must be tracked in gy[0]!
        f0 = {"cards": [[1, 0, 2, 0, 101, 1, 1, 1000, 1000]]}
        f1 = {"cards": [[1, 0, rr.GRAVE, 0, 101, 1, 1, 1000, 1000]]}
        f2 = {"cards": [[2, 0, rr.GRAVE, 1, 102, 1, 1, 1500, 1500]]}
        tracker = ReplayStateTracker([f0, f1, f2])

        self.assertEqual(len(tracker.get(0)["gy"][0]), 0)
        self.assertEqual(len(tracker.get(1)["gy"][0]), 1)
        self.assertEqual(tracker.get(1)["gy"][0][0]["card"], 101)

        # In Frame 2: Both cards are in graveyard
        gy2 = tracker.get(2)["gy"][0]
        self.assertEqual(len(gy2), 2)
        self.assertEqual(gy2[0]["card"], 101)
        self.assertEqual(gy2[1]["card"], 102)

    def test_despawned_moving_card_enters_graveyard(self):
        # Card activates (zone 18 = MOVING), then despawns from Unity GameObjects in the next frame
        f0 = {"cards": [[5, 1, rr.HAND, 0, 201, 1, 0, 0, 0]]}
        f1 = {"cards": [[5, 1, rr.MOVING, 0, 201, 1, 0, 0, 0]]}
        f2 = {"cards": []} # despawned
        tracker = ReplayStateTracker([f0, f1, f2])

        self.assertEqual(tracker.get(1)["activating"]["uid"], 5)
        # In frame 2, card 5 must be in team 1's graveyard
        self.assertEqual(len(tracker.get(2)["gy"][1]), 1)
        self.assertEqual(tracker.get(2)["gy"][1][0]["card"], 201)

    def test_banished_and_resurrection(self):
        # Card 1 sent to banished (zone 17)
        # Card 2 sent to GY, then resurrected back to field (zone 2)
        f0 = {"cards": [[1, 0, rr.BANISHED, 0, 301, 1, 0, 0, 0],
                        [2, 0, rr.GRAVE, 0, 302, 1, 0, 0, 0]]}
        f1 = {"cards": [[2, 0, 2, 0, 302, 1, 1, 2000, 2000]]} # reborn to field
        tracker = ReplayStateTracker([f0, f1])

        self.assertEqual(len(tracker.get(0)["banished"][0]), 1)
        self.assertEqual(len(tracker.get(0)["gy"][0]), 1)

        # In frame 1: Card 2 left graveyard
        self.assertEqual(len(tracker.get(1)["gy"][0]), 0)
        self.assertEqual(len(tracker.get(1)["banished"][0]), 1)

    def test_chain_links_progression(self):
        # Frame 0: Card 1 activates (CL1)
        # Frame 1: Card 2 chains in response (CL2)
        # Frame 2: Card 3 chains (CL3)
        # Frame 3: Resolved
        f0 = {"cards": [[1, 0, rr.MOVING, 0, 100, 1, 1, 0, 0]]}
        f1 = {"cards": [[2, 1, rr.MOVING, 0, 200, 1, 1, 0, 0]]}
        f2 = {"cards": [[3, 0, rr.MOVING, 0, 300, 1, 1, 0, 0]]}
        f3 = {"cards": []}
        tracker = ReplayStateTracker([f0, f1, f2, f3])

        ch0 = tracker.get(0)["chain"]
        self.assertEqual(len(ch0), 1)
        self.assertEqual(ch0[0]["cl"], 1)
        self.assertEqual(ch0[0]["card"], 100)

        ch1 = tracker.get(1)["chain"]
        self.assertEqual(len(ch1), 2)
        self.assertEqual(ch1[1]["cl"], 2)
        self.assertEqual(ch1[1]["card"], 200)

        ch2 = tracker.get(2)["chain"]
        self.assertEqual(len(ch2), 3)
        self.assertEqual(ch2[2]["cl"], 3)
        self.assertEqual(ch2[2]["card"], 300)

        self.assertEqual(len(tracker.get(3)["chain"]), 0)

    def test_banished_to_graveyard_transition(self):
        # Card 1 is banished, then returned to Graveyard (e.g. Burial from a Different Dimension)
        f0 = {"cards": [[1, 0, rr.BANISHED, 0, 401, 1, 0, 0, 0]]}
        f1 = {"cards": [[1, 0, rr.GRAVE, 0, 401, 1, 0, 0, 0]]}
        tracker = ReplayStateTracker([f0, f1])

        self.assertEqual(len(tracker.get(0)["banished"][0]), 1)
        self.assertEqual(len(tracker.get(0)["gy"][0]), 0)

        # In frame 1: Removed from banished, placed in graveyard
        self.assertEqual(len(tracker.get(1)["banished"][0]), 0)
        self.assertEqual(len(tracker.get(1)["gy"][0]), 1)
        self.assertEqual(tracker.get(1)["gy"][0][0]["card"], 401)

    def test_corrupt_team_id_handled_gracefully(self):
        # Frame with unexpected team ID like 99 or -1 must not crash
        f0 = {"cards": [[1, 99, rr.GRAVE, 0, 501, 1, 0, 0, 0]]}
        tracker = ReplayStateTracker([f0])
        self.assertEqual(len(tracker.get(0)["gy"][0]), 0)
        self.assertEqual(len(tracker.get(0)["gy"][1]), 0)

    def test_is_discard_cost_helper(self):
        from duel_view import is_discard_cost
        from card_stats import CardInfo
        # Handtraps with discard / send to GY cost
        ash = CardInfo("1", "Ash Blossom", "Effect Monster", "effect", "Zombie", 3, "",
                       "When a card or effect is activated (Quick Effect): You can discard this card; negate that effect.")
        self.assertTrue(is_discard_cost(ash))

        maxx_c = CardInfo("2", "Maxx \"C\"", "Effect Monster", "effect", "Insect", 2, "",
                          "During either player's turn (Quick Effect): You can send this card from your hand to the GY; this turn...")
        self.assertTrue(is_discard_cost(maxx_c))

        # Zauber und Fallen werden auf das Spielfeld gelegt, nicht abgeworfen!
        spell = CardInfo("3", "Raigeki", "Spell Card", "spell", "Normal", None, "", "Destroy all monsters your opponent controls.")
        self.assertFalse(is_discard_cost(spell))

        trap = CardInfo("4", "Mirror Force", "Trap Card", "trap", "Normal", None, "", "When an opponent's monster declares an attack...")
        self.assertFalse(is_discard_cost(trap))

        # Monster that activates in hand without discard cost (reveals itself)
        danger = CardInfo("5", "Danger! Bigfoot!", "Effect Monster", "effect", "Beast", 8, "",
                          "You can reveal this card in your hand; your opponent randomly chooses 1 card from your entire hand...")
        self.assertFalse(is_discard_cost(danger))

    def test_handtrap_discard_cost_enters_gy_on_activation(self):
        from unittest import mock
        from card_stats import CardInfo
        # Ash Blossom activated (zone 18 = MOVING)
        # Should be in GY immediately on activation (Frame 1), not wait until despawn!
        ash = CardInfo("1", "Ash Blossom", "Effect Monster", "effect", "Zombie", 3, "",
                       "You can discard this card; negate that effect.")
        mock_db = mock.Mock()
        mock_db.info.return_value = ash

        f0 = {"cards": [[10, 1, rr.HAND, 0, 14553, 0, 0, 0, 0]]}
        f1 = {"cards": [[10, 1, rr.MOVING, 0, 14553, 1, 0, 0, 0]]}  # Ash activates
        tracker = ReplayStateTracker([f0, f1], db=mock_db, passcode_func=lambda kid: "14553")

        # In Frame 1: Activation is tracked AND card is in opponent's Graveyard as cost!
        self.assertEqual(tracker.get(1)["activating"]["uid"], 10)
        self.assertEqual(len(tracker.get(1)["gy"][1]), 1)
        self.assertEqual(tracker.get(1)["gy"][1][0]["card"], 14553)

    def test_discard_cost_card_on_way_to_field_not_in_gy(self):
        from unittest import mock
        from card_stats import CardInfo
        # Kostentext mit "banish" vor dem Semikolon, aber Ziel ist die Monsterzone: darf nie im Friedhof landen
        aleister = CardInfo("22964", "Aleister the Invoker", "Effect Monster", "effect", "Spellcaster", 4, "",
                            "You can banish 1 Spellcaster monster from your GY; Special Summon this card.")
        mock_db = mock.Mock()
        mock_db.info.return_value = aleister

        f0 = {"cards": [[22, 0, rr.HAND, 0, 22964, 0, 0, 0, 0]]}
        f1 = {"cards": [[22, 0, rr.MOVING, 0, 22964, 1, 0, 0, 0]]}
        f2 = {"cards": [[22, 0, 1, 0, 22964, 1, 1, 1800, 1000]]}
        tracker = ReplayStateTracker([f0, f1, f2], db=mock_db, passcode_func=lambda kid: "22964")

        for idx in range(3):
            self.assertEqual(tracker.get(idx)["gy"][0], [])

    def test_spell_stays_on_field_until_resolved(self):
        from unittest import mock
        from card_stats import CardInfo
        raigeki = CardInfo("3", "Raigeki", "Spell Card", "spell", "Normal", None, "", "Destroy all monsters your opponent controls.")
        mock_db = mock.Mock()
        mock_db.info.return_value = raigeki

        # Frame 0: Raigeki in der Hand
        # Frame 1: Raigeki aufs Spielfeld gelegt (Zone 9) -> Aktivierung (CL 1), noch NICHT im Friedhof!
        # Frame 2: Raigeki in MOVING (Effekt wird aufgelöst) -> Feld-Zone wird gemerkt, noch NICHT im Friedhof!
        # Frame 3: Raigeki despawnt -> Effekt aufgelöst, JETZT im Friedhof!
        f0 = {"cards": [[10, 1, rr.HAND, 0, 100, 0, 0, 0, 0]]}
        f1 = {"cards": [[10, 1, 9, 0, 100, 1, 0, 0, 0]]}
        f2 = {"cards": [[10, 1, rr.MOVING, 0, 100, 1, 0, 0, 0]]}
        f3 = {"cards": []}

        tracker = ReplayStateTracker([f0, f1, f2, f3], db=mock_db, passcode_func=lambda kid: "100")

        # Frame 1: Aktiviert auf dem Spielfeld (CL 1)
        st1 = tracker.get(1)
        self.assertEqual(st1["activating"]["uid"], 10)
        self.assertEqual(st1["chain"][0]["cl"], 1)
        self.assertEqual(len(st1["gy"][1]), 0)

        # Frame 2: Löst in MOVING auf, bleibt Feld-Zone 9 zugeordnet, noch nicht im Friedhof
        st2 = tracker.get(2)
        self.assertEqual(st2["field_zones"].get(10), 9)
        self.assertEqual(len(st2["gy"][1]), 0)

        # Frame 3: Despawnt -> Aufgelöst und im Friedhof
        st3 = tracker.get(3)
        self.assertEqual(len(st3["gy"][1]), 1)
        self.assertEqual(st3["gy"][1][0]["card"], 100)

    def test_monster_destroyed_from_field_enters_gy_not_activating(self):
        from unittest import mock
        from card_stats import CardInfo
        aleister = CardInfo("22964", "Aleister the Reminiscent", "Effect Monster", "effect", "Spellcaster", 4, "", "Special summon...")
        mock_db = mock.Mock()
        mock_db.info.return_value = aleister

        # Frame 0: Monster auf Spielfeld (Zone 1)
        # Frame 1: Monster wird zerstört (Zone 18 = MOVING) und geht Richtung Friedhof
        # Frame 2: Monster despawnt / im Friedhof
        f0 = {"cards": [[22, 0, 1, 0, 22964, 1, 1, 1800, 1000]]}
        f1 = {"cards": [[22, 0, rr.MOVING, 0, 22964, 1, 1, 1800, 1000]]}
        f2 = {"cards": []}

        tracker = ReplayStateTracker([f0, f1, f2], db=mock_db, passcode_func=lambda kid: "22964")

        # In Frame 1: Zerstörtes Monster darf NICHT als aktivierend markiert werden und keine Kette starten!
        st1 = tracker.get(1)
        self.assertIsNone(st1["activating"])
        self.assertEqual(st1["chain"], [])
        # Monster landet direkt im Friedhof (nicht in der Hand!)
        self.assertEqual(len(st1["gy"][0]), 1)
        self.assertEqual(st1["gy"][0][0]["card"], 22964)

    def test_extra_deck_monster_banished_enters_banished_not_activating(self):
        from unittest import mock
        from card_stats import CardInfo
        raidjin = CardInfo("12845", "Invoked Raidjin", "Fusion Monster", "fusion", "Warrior", 5, "", "Target 1 monster...")
        mock_db = mock.Mock()
        mock_db.info.return_value = raidjin

        # Frame 0: Raidjin im Extra Deck (Zone 14)
        # Frame 1: Raidjin wird verbannt (Zone 18 = MOVING)
        # Frame 2: Raidjin landet in Verbannt (Zone 17)
        f0 = {"cards": [[84, 0, rr.EXTRA_DECK, 0, 12845, 0, 0, 0, 0]]}
        f1 = {"cards": [[84, 0, rr.MOVING, 0, 12845, 1, 0, 0, 0]]}
        f2 = {"cards": [[84, 0, rr.BANISHED, 0, 12845, 1, 0, 0, 0]]}

        tracker = ReplayStateTracker([f0, f1, f2], db=mock_db, passcode_func=lambda kid: "12845")

        # In Frame 1: Raidjin darf NICHT als aktivierend markiert werden!
        st1 = tracker.get(1)
        self.assertIsNone(st1["activating"])
        self.assertEqual(st1["chain"], [])
        # Raidjin landet direkt im Verbannt-Stapel (nicht in der Hand!)
        self.assertEqual(len(st1["banished"][0]), 1)
        self.assertEqual(st1["banished"][0][0]["card"], 12845)

    def test_field_spell_activation_and_chain_resolution(self):
        from unittest import mock
        from card_stats import CardInfo
        meltdown = CardInfo("3763", "Magical Meltdown", "Spell Card", "spell", "Field", None, "", "When this card is activated: You can add 1 'Aleister'...")
        aleister = CardInfo("22964", "Aleister the Invoker", "Effect Monster", "effect", "Spellcaster", 4, "", "When normal summoned...")
        mock_db = mock.Mock()
        mock_db.info.side_effect = lambda p: meltdown if p == "3763" else aleister

        # Frame 0: Meltdown in hand
        # Frame 1: Meltdown activated in Field Zone (rr.FIELD_ZONE) -> CL 1
        # Frame 2: Resolution frame (meltdown stays in rr.FIELD_ZONE, no longer activating)
        # Frame 3: Aleister summoned and activates effect in Monster Zone 1 -> MUST be CL 1, not CL 2!
        f0 = {"cards": [[1, 0, rr.HAND, 0, 3763, 0, 0, 0, 0]]}
        f1 = {"cards": [[1, 0, rr.FIELD_ZONE, 0, 3763, 1, 0, 0, 0]]}
        f2 = {"cards": [[1, 0, rr.FIELD_ZONE, 0, 3763, 1, 0, 0, 0]]}
        f3 = {"cards": [
            [1, 0, rr.FIELD_ZONE, 0, 3763, 1, 0, 0, 0],
            [2, 0, 1, 0, 22964, 1, 1, 1000, 1800],
            [99, 0, rr.MOVING, 0, 22964, 1, 0, 0, 0],
        ]}

        passcodes = {3763: "3763", 22964: "22964"}
        tracker = ReplayStateTracker([f0, f1, f2, f3], db=mock_db, passcode_func=lambda kid: passcodes.get(kid))

        # Frame 1: Meltdown CL 1
        st1 = tracker.get(1)
        self.assertEqual(len(st1["chain"]), 1)
        self.assertEqual(st1["chain"][0]["cl"], 1)
        self.assertEqual(st1["chain"][0]["card"], 3763)

        # Frame 2: Chain resolved (Field spell stays on field)
        st2 = tracker.get(2)
        self.assertEqual(len(st2["chain"]), 0)

        # Frame 3: Aleister activates as new chain link 1!
        st3 = tracker.get(3)
        self.assertEqual(len(st3["chain"]), 1)
        self.assertEqual(st3["chain"][0]["cl"], 1)
        self.assertEqual(st3["chain"][0]["card"], 22964)

    def test_continuous_trap_stays_on_field_and_chain_clears(self):
        from unittest import mock
        from card_stats import CardInfo
        skill_drain = CardInfo("82732", "Skill Drain", "Trap Card", "trap", "Continuous", None, "", "Pay 1000 LP; negate...")
        mock_db = mock.Mock()
        mock_db.info.return_value = skill_drain

        # Frame 0: Set on field (face 0)
        # Frame 1: Flipped face up (face 1) -> activates (CL 1)
        # Frame 2: Remains on field (zone 9, face 1, activating is None) -> Chain must clear!
        f0 = {"cards": [[5, 0, 9, 0, 82732, 0, 0, 0, 0]]}
        f1 = {"cards": [[5, 0, 9, 0, 82732, 1, 0, 0, 0]]}
        f2 = {"cards": [[5, 0, 9, 0, 82732, 1, 0, 0, 0]]}

        tracker = ReplayStateTracker([f0, f1, f2], db=mock_db, passcode_func=lambda kid: "82732")

        st1 = tracker.get(1)
        self.assertEqual(len(st1["chain"]), 1)
        self.assertEqual(st1["chain"][0]["cl"], 1)

        # In Frame 2: Continuous Trap stays on field, chain resolves and clears
        st2 = tracker.get(2)
        self.assertEqual(len(st2["chain"]), 0)


    def test_hand_reveal_nur_beim_gegner(self):
        # Mit my_id=1 ist Team 1 man selbst: neu gezogene eigene Handkarte ist keine Aktivierung,
        # eine neu aufgedeckte Gegner-Handkarte (Team 0) dagegen schon.
        f0 = {"cards": [[9, 1, rr.DECK, 0, 900, 0, 0, 0, 0]]}
        f_own = {"cards": [[9, 1, rr.DECK, 0, 900, 0, 0, 0, 0], [1, 1, rr.HAND, 0, 901, 1, 0, 0, 0]]}
        f_opp = {"cards": [[9, 1, rr.DECK, 0, 900, 0, 0, 0, 0], [2, 0, rr.HAND, 0, 902, 1, 0, 0, 0]]}
        self.assertIsNone(ReplayStateTracker([f0, f_own], my_id=1).get(1)["activating"])
        self.assertEqual(ReplayStateTracker([f0, f_opp], my_id=1).get(1)["activating"]["uid"], 2)


if __name__ == "__main__":
    unittest.main()


