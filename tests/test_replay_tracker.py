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


if __name__ == "__main__":
    unittest.main()
