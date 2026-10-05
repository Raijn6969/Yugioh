"""Staple-Analyse: eigenes Deck mit den Top-Listen (Master Duel Meta) vergleichen – ohne Internet."""

import os
import tempfile
import time
import unittest
import unittest.mock

import staple_analysis as sa
from card_stats import CardStatsDB

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAS_TK = True
except Exception:
    HAS_TK = False


def card(cid, at, per, total, avg):
    return {"card": cid, "at": at, "per": per, "totalPer": total, "avgAt": avg}


# Deck-Typ "Mitsurugi" mit 9 Top-Listen
MITSURUGI = {"name": "Mitsurugi", "deckBreakdown": {"total": 9, "cards": [
    card("m1", 3, 100, 100, 3), card("m2", 3, 67, 100, 2.7), card("ash", 3, 89, 100, 2.9),
    card("herald", 1, 89, 89, 1), card("side", 1, 30, 30, 1), card("rare", 1, 11, 11, 1)]}}
RYZEAL = {"name": "Ryzeal", "deckBreakdown": {"total": 5, "cards": [
    card("r1", 3, 100, 100, 3), card("ash", 3, 100, 100, 3)]}}
NAMES = {"m1": "Mitsurugi no Mikoto, Saji", "m2": "Mitsurugi Ritual", "ash": "Ash Blossom & Joyous Spring",
         "herald": "Herald of the Arc Light", "side": "Droll & Lock Bird", "rare": "Bonfire", "r1": "Ice Ryzeal"}


class FakeResponse:
    def __init__(self, data, status_code=200):
        self.data, self.status_code, self.content = data, status_code, b""

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


class FakeSession:
    """Antwortet wie die API von Master Duel Meta (Deck-Typen und Kartennamen)."""

    def __init__(self, offline=False):
        self.calls, self.offline = [], offline

    def get(self, url, params=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[-1], dict(params or {})))
        if self.offline:
            raise ConnectionError("offline")
        if "images.ygoprodeck.com" in url:
            self.calls.append(("image", {}))
            return FakeResponse(None, 404)
        if url.endswith("/deck-types"):
            known = {"Mitsurugi": MITSURUGI, "Ryzeal": RYZEAL}
            return FakeResponse([known[params["name"]]] if params["name"] in known else [])
        ids = params["_id[$in]"].split(",")
        return FakeResponse([{"_id": i, "name": NAMES[i]} for i in ids if i in NAMES])


# Eigenes Deck: Ritual 2× statt 3×, Herald fehlt, Bonfire spielt kaum jemand, Ice Ryzeal kommt aus dem Hybrid
DECK = {"Mitsurugi no Mikoto, Saji": 3, "Mitsurugi Ritual": 2, "Ash Blossom & Joyous Spring": 3, "Bonfire": 1,
        "Ice Ryzeal": 3, "Selbstgebaut": 1}


def make_db():
    return CardStatsDB(os.path.join(tempfile.mkdtemp(), "stats.db"), base_path=None, fetch=lambda ids: {})


class CompareTest(unittest.TestCase):
    def setUp(self):
        names = {k: (v, "") for k, v in NAMES.items()}
        self.mitsurugi = sa.parse_breakdown(MITSURUGI, names, now=1)
        self.ryzeal = sa.parse_breakdown(RYZEAL, names, now=1)

    def test_breakdown(self):
        self.assertEqual(self.mitsurugi.lists, 9)
        ritual = next(c for c in self.mitsurugi.cards if c.name == "Mitsurugi Ritual")
        self.assertEqual((ritual.share, ritual.usual, ritual.usual_share, ritual.avg), (1.0, 3, 0.67, 2.7))
        self.assertIsNone(sa.parse_breakdown({"name": "X", "deckBreakdown": {"total": 0}}, {}))

    def test_different_missing_and_only_mine(self):
        result = sa.compare(DECK, self.mitsurugi)
        self.assertEqual([(d.card.name, d.mine, d.card.usual) for d in result.different], [("Mitsurugi Ritual", 2, 3)])
        self.assertEqual([d.card.name for d in result.missing], ["Herald of the Arc Light"])  # Droll: nur 30 %
        self.assertEqual([(d.card.name, d.mine) for d in result.only_mine],
                         [("Ice Ryzeal", 3), ("Selbstgebaut", 1), ("Bonfire", 1)])
        self.assertEqual(result.matching, 2)  # Saji, Ash

    def test_names_match_regardless_of_spelling(self):
        result = sa.compare({"ash blossom & joyous spring": 3}, self.mitsurugi)
        self.assertNotIn("Ash Blossom & Joyous Spring", [d.card.name for d in result.only_mine])

    def test_hybrid_best_match_first_and_partner_cards_are_not_only_mine(self):
        best, other = sa.best_match(DECK, [self.ryzeal, self.mitsurugi])
        self.assertEqual((best.stats.deck_type, other.stats.deck_type), ("Mitsurugi", "Ryzeal"))
        self.assertNotIn("Ice Ryzeal", [d.card.name for d in best.only_mine])
        self.assertNotIn("Mitsurugi Ritual", [d.card.name for d in other.only_mine])


class LoadTest(unittest.TestCase):
    def setUp(self):
        self.db = make_db()

    def test_fetched_once_then_offline_from_database(self):
        session = FakeSession()
        stats = sa.load(self.db, "Mitsurugi", session=session)
        self.assertEqual((stats.deck_type, stats.lists, len(stats.cards)), ("Mitsurugi", 9, 6))
        self.assertEqual([call for call, _ in session.calls], ["deck-types", "cards"])
        again = sa.load(self.db, "mitsurugi", session=FakeSession(offline=True))
        self.assertEqual(again, stats)

    def test_unknown_deck_type_is_remembered(self):
        session = FakeSession()
        self.assertIsNone(sa.load(self.db, "Gibt es nicht", session=session))
        self.assertIsNone(sa.load(self.db, "Gibt es nicht", session=session))
        self.assertEqual(len(session.calls), 1)

    def test_old_data_is_refreshed_but_kept_when_offline(self):
        sa.load(self.db, "Ryzeal", session=FakeSession())
        data = self.db.staples("Ryzeal")
        data["fetched_at"] = time.time() - (sa.CACHE_DAYS + 1) * 86400
        self.db.store_staples("Ryzeal", data)
        old = sa.load(self.db, "Ryzeal", session=FakeSession(offline=True))  # zu alt, aber offline
        self.assertEqual(old.lists, 5)
        session = FakeSession()
        sa.load(self.db, "Ryzeal", session=session)
        self.assertEqual(session.calls[0][0], "deck-types")
        with self.assertRaises(ConnectionError):  # nie geladen und offline
            sa.load(self.db, "Mitsurugi", session=FakeSession(offline=True))


class CardImagesTest(unittest.TestCase):
    def test_downloaded_once_and_only_real_jpegs(self):
        import card_images

        class ImageSession:
            def __init__(self):
                self.urls = []

            def get(self, url, timeout=None):
                self.urls.append(url)
                return FakeResponse(None, 200) if "666" in url else type(
                    "R", (), {"status_code": 200, "content": b"\xff\xd8jpeg"})()

        folder, session = tempfile.mkdtemp(), ImageSession()
        with unittest.mock.patch.object(card_images, "PAUSE", 0):
            self.assertEqual(card_images.download(["14558127", "14558127", "666", "", "abc"], session, folder), 1)
            self.assertEqual(card_images.download(["14558127"], session, folder), 0)  # schon da
        self.assertTrue(os.path.exists(card_images.image_path("14558127", folder)))
        self.assertFalse(os.path.exists(card_images.image_path("666", folder)))  # keine Bilddatei
        self.assertEqual(len(session.urls), 2)


@unittest.skipUnless(HAS_TK, "kein Tk")
class StaplePanelTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.folder = tempfile.mkdtemp()

    def tearDown(self):
        self.root.destroy()

    def open(self, archetypes, session):
        from staple_panel import StaplePanel
        panel = StaplePanel(self.root, make_db(), DECK, archetypes, on_close=lambda: None, session=session,
                            passcodes={"Ice Ryzeal": "8633261"}, image_folder=self.folder)
        end = time.time() + 3
        while not panel.comparisons and time.time() < end and "Lade" in panel.status_label.cget("text"):
            self.root.update()
            time.sleep(0.02)
        self.root.update()
        return panel

    def texts(self, widget):
        result = []
        for child in widget.winfo_children():
            if isinstance(child, tk.Label):
                result.append(child.cget("text"))
            result += self.texts(child)
        return result

    def test_shows_best_match_and_switches_deck_type(self):
        panel = self.open(["Ryzeal", "Mitsurugi"], FakeSession())
        self.assertIn("Mitsurugi: 9 Top-Liste(n)", panel.status_label.cget("text"))
        self.assertEqual({k: v.cget("text") for k, v in panel.tiles.items()},
                         {"matching": "2", "different": "1", "missing": "1", "only_mine": "2"})
        texts = panel.grid.texts()
        self.assertIn("2× → 3×", texts)        # Mitsurugi Ritual
        self.assertIn("89 %", texts)           # Herald fehlt
        self.assertIn("nur du", texts)         # Selbstgebaut
        self.assertIn("Herald of the Arc Light", texts)  # ohne Bild: Name als Platzhalter
        panel.show(1)
        self.assertIn("Ryzeal: 5 Top-Liste(n)", panel.status_label.cget("text"))
        panel.close()

    def test_offline_and_unknown(self):
        panel = self.open(["Mitsurugi"], FakeSession(offline=True))
        self.assertIn("nicht geladen", panel.status_label.cget("text"))
        panel.close()
        panel = self.open(["Gibt es nicht"], FakeSession())
        self.assertIn("kennt keine Top-Listen", panel.status_label.cget("text"))
        panel.close()
        panel = self.open([], FakeSession())
        self.assertIn("Keine Archetypen", panel.status_label.cget("text"))
        panel.close()


if __name__ == "__main__":
    unittest.main()
