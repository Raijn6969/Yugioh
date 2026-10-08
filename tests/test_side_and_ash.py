"""Side-Deck-Profile (Tausch per kleinem Import) und Ash-Prio-Spickzettel (aus der Factory-Datenbank)."""

import json
import os
import sqlite3
import tempfile
import time
import unittest
from unittest import mock

import ash_prio
import side_profiles
from card_stats import CardInfo, CardStatsDB, SideProfile

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAS_TK = True
except Exception:
    HAS_TK = False

DROLL, ASH, STORM, MAIN, FUSION = "94145021", "14558127", "14532163", "1000", "2000"
DECK = {DROLL: 3, ASH: 3, MAIN: 34, FUSION: 1}  # 40 Main + 1 Extra (Kopienzahl beim Test egal)


def make_db():
    db = CardStatsDB(os.path.join(tempfile.mkdtemp(), "stats.db"), base_path=None, fetch=lambda ids: {})
    cards = [CardInfo(DROLL, "Droll & Lock Bird", "Effect Monster", "effect", "Spellcaster", 1, "", ""),
             CardInfo(ASH, "Ash Blossom & Joyous Spring", "Effect Monster", "effect", "Zombie", 3, "", ""),
             CardInfo(STORM, "Lightning Storm", "Spell Card", "spell", "Quick-Play", 0, "", ""),
             CardInfo("14532164", "Lightning Storm", "Spell Card", "spell", "Quick-Play", 0, "", ""),  # Artwork
             CardInfo(FUSION, "Some Fusion", "Fusion Monster", "fusion", "Dragon", 8, "", "")]
    with db._connect() as con:
        con.executemany("INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [(*c, 0) for c in cards])
        con.executemany("INSERT INTO konami VALUES (?, ?, ?)", [(c.cid, i, c.name) for i, c in enumerate(cards)])
    return db


class SideProfileTest(unittest.TestCase):
    def setUp(self):
        self.profile = SideProfile("Deck", "Zweiter", {DROLL: 3}, {STORM: 3})

    def test_swap_and_back(self):
        target = side_profiles.swap(DECK, self.profile.cards_out, self.profile.cards_in)
        self.assertEqual((target.count(DROLL), target.count(STORM), len(target)), (0, 3, 41))
        swapped = {cid: target.count(cid) for cid in set(target)}
        self.assertTrue(side_profiles.can_apply(DECK, self.profile))
        self.assertFalse(side_profiles.can_revert(DECK, self.profile))
        self.assertTrue(side_profiles.is_active(swapped, self.profile))
        self.assertFalse(side_profiles.can_apply(swapped, self.profile))
        back = side_profiles.swap(swapped, self.profile.cards_in, self.profile.cards_out)
        self.assertEqual(sorted(back), sorted(side_profiles.swap(DECK, {}, {})))

    def test_not_more_than_three_copies(self):
        self.assertFalse(side_profiles.can_apply(DECK, SideProfile("Deck", "x", {}, {ASH: 1})))
        self.assertTrue(side_profiles.can_apply(DECK, SideProfile("Deck", "x", {ASH: 1}, {ASH: 1})))

    def test_profiles_are_stored_per_deck(self):
        db = make_db()
        db.save_side_profile(self.profile)
        db.save_side_profile(SideProfile("Anderes", "Erster", {ASH: 1}, {}))
        self.assertEqual(db.side_profiles("Deck"), [self.profile])
        db.save_side_profile(self.profile._replace(cards_in={STORM: 2}))  # gleicher Name ersetzt
        self.assertEqual(db.side_profiles("Deck")[0].cards_in, {STORM: 2})
        db.delete_side_profile("Deck", "Zweiter")
        self.assertEqual(db.side_profiles("Deck"), [])
        self.assertEqual(len(db.side_profiles("Anderes")), 1)

    def test_card_search_only_once_per_name(self):
        db = make_db()
        self.assertEqual(db.search_cards("storm"), [(STORM, "Lightning Storm")])
        # Namensanfang zuerst, sonst die kürzeren Namen
        self.assertEqual([name for _, name in db.search_cards("lo")], ["Droll & Lock Bird",
                                                                       "Ash Blossom & Joyous Spring"])
        self.assertEqual(db.search_cards("ash")[0], (ASH, "Ash Blossom & Joyous Spring"))
        self.assertEqual(db.search_cards("a"), [])  # erst ab 2 Zeichen


class EngineTest(unittest.TestCase):
    def test_import_engine_uses_preset_deck_instead_of_clipboard(self):
        import import_engine
        core = import_engine.DeckImporterCore({}, "tesseract", lambda *a: None, lambda **k: None,
                                              card_ids=["1", "2"], resume={"done": {"1": 1, "2": 1}})
        with mock.patch.object(import_engine, "parse_clipboard", side_effect=AssertionError("Zwischenablage")), \
                mock.patch.object(core, "_preflight", return_value="Stopp für den Test"):
            core._run_import()
        self.assertEqual(core._card_ids, ["1", "2"])
        # Kein Fortschritt fürs "Fortsetzen" (überschriebe den eines abgebrochenen normalen Imports)
        with mock.patch.object(import_engine.resume_state, "save_progress") as save:
            core._mark_done("3", 1)
        save.assert_not_called()


def make_factory(path, hands=2):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE variants (id INTEGER PRIMARY KEY, order_id INTEGER, rank INTEGER, key TEXT, "
                "name TEXT, source TEXT, meta TEXT, main TEXT, extra TEXT)")
    con.execute("CREATE TABLE combo_results (deck TEXT, hand TEXT, score REAL, robust REAL, pieces INTEGER, "
                "data TEXT, worker TEXT, found_at REAL, PRIMARY KEY (deck, hand))")
    main = ",".join([DROLL] * 3 + [ASH] * 3 + [MAIN] * 34)
    con.execute("INSERT INTO variants (key, name, main, extra) VALUES ('v1', 'Meine Liste', ?, ?)", (main, FUSION))
    con.execute("INSERT INTO variants (key, name, main, extra) VALUES ('v2', 'Fremd', '5,6,7', '')")
    steps = ["Branded Fusion: Fusionsbeschwörung von Albion", "Albion: Fusionsbeschwörung  [Kette 1]", "Zug beenden"]
    stress = {"ash": {"score": 0.5, "step": 0, "text": "Ash Blossom als Antwort auf Schritt 1: Branded Fusion",
                      "cont_steps": ["Zug beenden"], "points": 2},
              "nibiru": {"score": 3.0, "step": 1, "text": "Nibiru nach Schritt 2: Albion", "cont_steps": []},
              "droll": {"score": 6.0, "step": None, "text": "Droll & Lock findet keinen Angriffspunkt"}}
    for i in range(hands):
        con.execute("INSERT INTO combo_results VALUES ('v1', ?, ?, 0, 2, ?, 'Ayu', 0)",
                    (f"Hand {i}", 6.0 - i, json.dumps({"steps": steps, "stress": stress})))
    con.execute("INSERT INTO combo_results VALUES ('v1', 'Ohne Prüfung', 9, 0, 2, ?, 'Ayu', 0)",
                (json.dumps({"steps": steps}),))
    con.commit()
    con.close()


class AshPrioTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "factory.db")
        make_factory(self.path)

    def test_deck_is_matched_to_variant_and_hands_are_read(self):
        deck = dict(DECK, **{DROLL: 2})  # eine Karte anders
        variant, sheets, hint = ash_prio.load(deck, self.path)
        self.assertEqual((variant.key, variant.name, hint), ("v1", "Meine Liste", ""))
        self.assertGreater(variant.overlap, 0.95)
        self.assertEqual([s.hand for s in sheets], ["Hand 0", "Hand 1"])  # nur geprüfte, die stärkste zuerst
        sheet = sheets[0]
        self.assertEqual(ash_prio.advice(sheet, "ash"),
                         "als Antwort auf Schritt 1: Branded Fusion: Fusionsbeschwörung von Albion → Board 0,5 "
                         "statt 6,0")
        self.assertEqual(ash_prio.advice(sheet, "nibiru"),
                         "nach Schritt 2: Albion: Fusionsbeschwörung → Board 3,0 statt 6,0")
        self.assertEqual(ash_prio.advice(sheet, "droll"), "Droll & Lock findet keinen Angriffspunkt")
        self.assertEqual(ash_prio.advice(sheet, "ogre"), "nicht geprüft")
        self.assertEqual(sheet.damage("ash"), 5.5)
        self.assertEqual(sheet.damage("droll"), 0.0)

    def test_alt_art_passcodes_are_matched_by_name(self):
        con = sqlite3.connect(self.path)
        con.execute("CREATE TABLE cards (id TEXT PRIMARY KEY, name TEXT)")
        con.execute("INSERT INTO cards VALUES (?, 'Ash Blossom & Joyous Spring')", (ASH,))
        con.commit()
        con.close()
        alt = dict(DECK)
        alt["14558128"] = alt.pop(ASH)  # Alt-Art von Ash
        plain = ash_prio.load(alt, self.path)[0].overlap
        named = ash_prio.load(alt, self.path, {"14558128": "Ash Blossom & Joyous Spring"})[0].overlap
        self.assertLess(plain, 1.0)
        self.assertEqual(named, 1.0)

    def test_unknown_deck_and_missing_factory(self):
        variant, sheets, hint = ash_prio.load({"999": 40}, self.path)
        self.assertIsNone(variant)
        self.assertIn("noch nicht durchgerechnet", hint)
        self.assertIn("nicht gefunden", ash_prio.load(DECK, self.path + ".fehlt")[2])
        # Passende Liste, aber noch ohne Handtrap-Prüfung
        con = sqlite3.connect(self.path)
        con.execute("UPDATE combo_results SET data = '{\"steps\": []}'")
        con.commit()
        con.close()
        variant, _, hint = ash_prio.load(DECK, self.path)
        self.assertIsNone(variant)
        self.assertIn("„Meine Liste“, 100 % gleiche Karten", hint)

    def test_factory_db_is_opened_read_only(self):
        con = ash_prio.connect(self.path)
        with self.assertRaises(sqlite3.OperationalError):
            con.execute("DELETE FROM variants")
        con.close()


@unittest.skipUnless(HAS_TK, "kein Tk")
class PanelTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)  # nach dem Schließen der Fenster (Cleanups laufen rückwärts)

    def pump(self, seconds=0.3):
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            time.sleep(0.01)

    def test_side_panel_builds_profile_and_swaps(self):
        from side_panel import SidePanel
        db, swaps = make_db(), []
        zones = {DROLL: "Main", ASH: "Main", MAIN: "Main", FUSION: "Extra"}
        names = {DROLL: "Droll & Lock Bird", ASH: "Ash Blossom & Joyous Spring", MAIN: "Karte", FUSION: "Fusion"}
        panel = SidePanel(self.root, db, "Deck", DECK, zones, names, on_swap=lambda t, l: swaps.append((t, l)),
                          on_close=lambda: None)
        self.addCleanup(panel.close)
        panel.save()
        self.assertIn("raus", panel.feedback.cget("text"))  # noch nichts gewählt
        for _ in range(4):
            panel.take_out(DROLL)  # höchstens so oft wie im Deck
        self.assertEqual(panel.cards_out, {DROLL: 3})
        self.assertIn("37 Karten", panel.feedback.cget("text"))  # Main wäre zu klein
        panel.search_var.set("storm")
        self.pump(0.1)
        panel.put_in(STORM)
        panel.put_in(STORM)
        panel.put_in(STORM)
        panel.put_in(STORM, -1)
        panel.put_in(STORM)
        self.assertEqual(panel.cards_in, {STORM: 3})
        self.assertEqual(panel.feedback.cget("text"), "Danach: Main 40, Extra 1 Karten")
        panel.put_in(ASH)  # Ash ist schon 3× drin
        self.assertIn("schon 3×", panel.feedback.cget("text"))
        panel.take_out(ASH)
        panel.put_in(ASH)
        panel.take_out(ASH, -1)  # Ash doch nicht raus → wäre 4×
        self.assertIn("mehr als 3×", panel.feedback.cget("text"))
        panel.put_in(ASH, -1)
        panel.name_var.set("Zweiter")
        panel.save()
        self.assertEqual(db.side_profiles("Deck"), [SideProfile("Deck", "Zweiter", {DROLL: 3}, {STORM: 3})])
        panel.apply(db.side_profiles("Deck")[0])
        target, label = swaps[0]
        self.assertEqual((target.count(DROLL), target.count(STORM), label), (0, 3, "Zweiter"))
        self.assertTrue(panel.closed)
        # Nach dem Tausch heißt das Deck anders: das angewendete Profil bleibt erreichbar ("Zurück")
        swapped = {cid: target.count(cid) for cid in set(target)}
        other = SidePanel(self.root, db, "Neuer Name", swapped, zones, names, on_swap=lambda t, l: None,
                          on_close=lambda: None)
        self.addCleanup(other.close)
        self.assertEqual([p.name for p in other._profiles()], ["Zweiter"])
        self.assertEqual(db.side_profiles(None)[0].deck_name, "Deck")

    def test_ash_panel_loads_in_background(self):
        from ash_panel import AshPrioPanel
        path = os.path.join(tempfile.mkdtemp(), "factory.db")
        make_factory(path)
        panel = AshPrioPanel(self.root, "Deck", DECK, on_close=lambda: None, path=path)
        self.addCleanup(panel.close)
        for _ in range(100):
            self.pump(0.05)
            if panel.sheets:
                break
        self.assertEqual(len(panel.sheets), 2)
        self.assertIn("Meine Liste", panel.info_label.cget("text"))
        panel.toggle("Hand 0")
        texts = [w.cget("text") for w in panel.inner.winfo_children()[1].winfo_children()
                 if isinstance(w, tk.Label)]
        self.assertTrue(texts[0].endswith("◀ Ash Blossom"))
        self.assertIn("Nach Ash Blossom am besten weiter:", texts)
        panel.choose("nibiru")
        self.assertEqual(panel.buttons["nibiru"].cget("bg"), "#007acc")
