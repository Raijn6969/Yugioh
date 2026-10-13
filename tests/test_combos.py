"""Combos von Master Duel Meta: Antwort lesen, laden, mit dem Deck abgleichen, Fenster – ohne Internet."""

import tempfile
import time
import unittest
import unittest.mock
from itertools import combinations

import mdm_combos as mc
from tests.test_staples import HAS_TK, FakeResponse, make_db

if HAS_TK:
    import tkinter as tk


def card(name):
    return {"card": {"_id": "x" + name, "name": name, "description": "..."}}


def entry(name, label, connector=None):
    return {**card(name), "label": label, **({"connector": connector} if connector else {})}


# Combo A: Platzhalter in der Starthand, Karte im Extra Deck, Verzweigung mit verschachtelter Verzweigung
COMBO_A = {
    "_id": "a", "title": "Tinia Loop", "author": {"username": "Raijn"}, "featured": True,
    "createdAt": "2026-09-01T10:00:00.000Z", "updatedAt": "2026-10-02T12:30:00.000Z",
    "upvotes": ["u1", "u2", "u3"], "downvotes": ["d1"],
    "startingCards": [card("Elfnote Tinia"), {"generic": "Any Non-FIRE Monster"}],
    "requiredCards": [card("Selene")],
    "steps": [
        {"entries": [entry("Elfnote Tinia", "Special Summon", "arrow"), entry("Elfnotes: Welcome Home", "Place")],
         "condition": {"branches": []}},
        {"entries": [entry("Elfnote Regina", "Quick Effect", "plus"), entry("Elfnote Lucina", "Add")],
         "condition": {"label": "Wenn Ash kommt", "branches": [
             {"label": "Add Lucina", "steps": [
                 {"entries": [entry("Selene", "Link Summon")],
                  "condition": {"label": "Danach", "branches": [{"label": "Tief", "steps": [
                      {"entries": [entry("Tiefer", "x")], "condition": {"branches": [{"label": "Zu tief", "steps": [
                          {"entries": [entry("Viel zu tief", "x")]}]}]}}]}]}}]}]}},
    ],
    "endBoard": [entry("Zalen the Shackled Dragon", "Omni Negate")],
    "vsHandTraps": [{"name": "Nibiru, the Primal Being"}, {"name": "Ash Blossom & Joyous Spring"}],
}
# Combo B: braucht eine Karte, die das Deck nicht spielt
COMBO_B = {"_id": "b", "title": "Braucht Ash", "upvotes": [], "downvotes": [], "featured": False,
           "startingCards": [card("Elfnote Tinia"), card("Ash Blossom & Joyous Spring")]}
# Combo C: spielbar, viele Stimmen, nicht empfohlen; fast nichts ausgefüllt
COMBO_C = {"_id": "c", "title": "Regina", "upvotes": ["1"] * 9, "startingCards": [card("Elfnote Regina")]}
ELFNOTE = {"_id": "dt1", "name": "Elfnote"}

MAIN = {"Elfnote Tinia": 3, "Elfnote Regina": 3, **{f"Karte {i}": 1 for i in range(34)}}
EXTRA = {"Selene": 1}


class FakeSession:
    """Antwortet wie die API von Master Duel Meta (Deck-Typen und Combos)."""

    def __init__(self, offline=False, as_object=False):
        self.calls, self.offline, self.as_object = [], offline, as_object

    def get(self, url, params=None, timeout=None):
        self.calls.append((url.rsplit("/", 1)[-1], dict(params or {})))
        if self.offline:
            raise ConnectionError("offline")
        if "images.ygoprodeck.com" in url:
            return FakeResponse(None, 404)
        if url.endswith("/deck-types"):
            if params["name"] != "Elfnote":
                return FakeResponse([])
            return FakeResponse(ELFNOTE if self.as_object else [{"_id": "dt0", "name": "Anderes"}, ELFNOTE])
        return FakeResponse([COMBO_A, COMBO_B, COMBO_C] if params["deckType"] == "dt1" else [])


class ParseTest(unittest.TestCase):
    def test_full_combo(self):
        combo = mc.parse_combo(COMBO_A, "Elfnote")
        self.assertEqual((combo.mdm_id, combo.title, combo.author, combo.votes, combo.featured),
                         ("a", "Tinia Loop", "Raijn", 2, True))  # 3 − 1 Stimmen
        self.assertEqual(combo.start, [mc.HandCard("Elfnote Tinia", "", ""),
                                       mc.HandCard("", "Any Non-FIRE Monster", "")])
        self.assertEqual(combo.required, ["Selene"])
        self.assertEqual(combo.updated, "2026-10-02T12:30:00.000Z")
        self.assertEqual(combo.end, [mc.Entry("Zalen the Shackled Dragon", "Omni Negate", "")])
        self.assertEqual(combo.vs_handtraps, ["Nibiru, the Primal Being", "Ash Blossom & Joyous Spring"])
        first, second = combo.steps
        self.assertEqual(first.entries[0], mc.Entry("Elfnote Tinia", "Special Summon", "arrow"))
        self.assertEqual((second.branch_label, second.branches[0].label), ("Wenn Ash kommt", "Add Lucina"))

    def test_named_card_with_any_label_is_only_an_example(self):
        # echter Fall "Tinia 1.5cc Crimson Dragon": Purulia steht nur beispielhaft für "any non-fire monster"
        example = {**card("Mulcharmy Purulia"), "label": "Can be any non-fire monster"}
        combo = mc.parse_combo({"startingCards": [card("Elfnote Tinia"), example,
                                                  {**card("Company"), "label": "Company, anyone"}]}, "X")
        self.assertEqual(combo.start, [mc.HandCard("Elfnote Tinia", "", ""),
                                       mc.HandCard("", "any non-fire monster", "z. B. Mulcharmy Purulia"),
                                       mc.HandCard("Company", "", "Company, anyone")])  # "anyone" ist nicht "any"
        result = mc.match(combo._replace(start=combo.start[:2]), MAIN, EXTRA)
        self.assertEqual(result.missing, [])  # Purulia fehlt im Deck, zählt aber nicht
        only_tinia = mc.match(mc.parse_combo({"startingCards": [card("Elfnote Tinia")]}, "X"), MAIN, EXTRA)
        self.assertEqual(result.odds, only_tinia.odds)  # auch nicht für die Chance
        from combo_panel import hand_text
        self.assertEqual(hand_text(combo._replace(start=combo.start[:2])), "Elfnote Tinia + beliebig (non-fire)")

    def test_nested_branches_are_cut_at_max_depth(self):
        step = mc.parse_combo(COMBO_A, "Elfnote").steps[1]
        level2 = step.branches[0].steps[0]
        level3 = level2.branches[0].steps[0]
        self.assertEqual(level2.branches[0].label, "Tief")
        self.assertEqual(level3.entries[0].card, "Tiefer")
        self.assertEqual(level3.branches, [])  # Tiefe 4 wird nicht mehr gelesen

    def test_missing_fields_raise_nothing(self):
        combo = mc.parse_combo({}, "X")
        self.assertEqual((combo.title, combo.author, combo.votes, combo.start, combo.steps, combo.end),
                         ("", "", 0, [], [], []))
        combo = mc.parse_combo({"steps": [{"entries": [{"label": "Nur Text"}], "condition": None}],
                                "upvotes": None, "author": None}, "X")
        self.assertEqual(combo.steps[0].entries, [mc.Entry("", "Nur Text", "")])

    def test_json_roundtrip(self):
        combos = [mc.parse_combo(c, "Elfnote") for c in (COMBO_A, COMBO_C)]
        combo_set = mc.ComboSet("Elfnote", combos, 5.0)
        import json
        self.assertEqual(mc.ComboSet.from_json(json.loads(json.dumps(combo_set.to_json()))), combo_set)


class FetchLoadTest(unittest.TestCase):
    def test_deck_type_as_object_and_as_list(self):
        for as_object in (True, False):
            session = FakeSession(as_object=as_object)
            combo_set = mc.fetch("Elfnote", session)
            self.assertEqual((combo_set.deck_type, [c.mdm_id for c in combo_set.combos]), ("Elfnote", ["a", "b", "c"]))
            self.assertEqual(session.calls[0][1]["limit"], 3)  # mit limit=1 käme ein einzelnes Objekt

    def test_exact_deck_type_name_wins_over_longer_names(self):
        session = FakeSession()
        get = session.get

        def snake_eye_first(url, params=None, timeout=None):
            if url.endswith("/deck-types"):
                session.calls.append(("deck-types", dict(params)))
                return FakeResponse([{"_id": "dt9", "name": "Elfnote Snake-Eye"}, ELFNOTE])
            return get(url, params, timeout)

        session.get = snake_eye_first
        self.assertEqual(mc.fetch("elfnote", session).deck_type, "Elfnote")
        self.assertEqual(session.calls[-1][1]["deckType"], "dt1")

    def test_unknown_deck_type(self):
        self.assertIsNone(mc.fetch("Gibt es nicht", FakeSession()))

    def test_cache_refresh_offline_and_unknown(self):
        db = make_db()
        session = FakeSession()
        first = mc.load(db, "Elfnote", session=session)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(mc.load(db, "elfnote", session=session), first._replace(fetched_at=first.fetched_at))
        self.assertEqual(len(session.calls), 2)  # zweiter Aufruf aus dem Cache
        mc.load(db, "Elfnote", refresh=True, session=session)
        self.assertEqual(len(session.calls), 4)
        # zu alt, aber offline → alter Stand
        data = db.combos("Elfnote")
        data["fetched_at"] = time.time() - (mc.CACHE_DAYS + 1) * 86400
        db.store_combos("Elfnote", data)
        self.assertEqual(len(mc.load(db, "Elfnote", session=FakeSession(offline=True)).combos), 3)
        with self.assertRaises(ConnectionError):  # nie geladen und offline
            mc.load(make_db(), "Elfnote", session=FakeSession(offline=True))
        # unbekannt wird gemerkt
        session = FakeSession()
        self.assertIsNone(mc.load(db, "Gibt es nicht", session=session))
        self.assertIsNone(mc.load(db, "Gibt es nicht", session=session))
        self.assertEqual(len(session.calls), 1)


class MatchTest(unittest.TestCase):
    def combo(self, start, required=()):
        data = {"startingCards": start, "requiredCards": [card(n) for n in required]}
        return mc.parse_combo(data, "X")

    def test_missing_cards_extra_deck_counts_for_required(self):
        result = mc.match(mc.parse_combo(COMBO_B, "X"), MAIN, EXTRA)
        self.assertEqual((result.missing, result.odds), (["Ash Blossom & Joyous Spring"], None))
        result = mc.match(self.combo([card("Elfnote Tinia")], ["Selene", "Fehlt", "Fehlt"]), MAIN, EXTRA)
        self.assertEqual(result.missing, ["Fehlt"])  # Selene liegt im Extra Deck; ohne Doppelte
        result = mc.match(self.combo([card("Selene")]), MAIN, EXTRA)  # Startkarte nur im Extra Deck: nie auf der Hand
        self.assertEqual(result.missing, ["Selene"])
        result = mc.match(self.combo([card("elfnote tinia")]), MAIN, EXTRA)  # Schreibweise egal
        self.assertEqual(result.missing, [])

    def test_odds_match_brute_force(self):
        main = {"A": 3, "B": 2, **{f"X{i}": 1 for i in range(5)}}
        deck = [name for name, copies in main.items() for _ in range(copies)]
        hands = list(combinations(range(len(deck)), 5))
        good = sum(1 for h in hands if {"A", "B"} <= {deck[i] for i in h})
        result = mc.match(self.combo([card("A"), card("B"), {"generic": "Any Monster"}]), main, {})
        self.assertAlmostEqual(result.odds, good / len(hands))
        only_a = sum(1 for h in hands if "A" in {deck[i] for i in h})
        self.assertAlmostEqual(mc.match(self.combo([card("A")]), main, {}).odds, only_a / len(hands))

    def test_only_placeholders_or_missing_card_give_no_odds(self):
        self.assertIsNone(mc.match(self.combo([{"generic": "Any"}]), MAIN, EXTRA).odds)
        self.assertIsNone(mc.match(self.combo([card("Elfnote Tinia")], ["Fehlt"]), MAIN, EXTRA).odds)

    def test_ranked_playable_then_featured_then_votes(self):
        combos = [mc.parse_combo(c, "X") for c in (COMBO_B, COMBO_C, COMBO_A)]
        self.assertEqual([m.combo.mdm_id for m in mc.ranked(combos, MAIN, EXTRA)], ["a", "c", "b"])
        better = mc.parse_combo({**COMBO_C, "_id": "d", "upvotes": ["1"] * 20}, "X")
        self.assertEqual([m.combo.mdm_id for m in mc.ranked(combos + [better], MAIN, EXTRA)], ["a", "d", "c", "b"])


@unittest.skipUnless(HAS_TK, "kein Tk")
class ComboPanelTest(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.folder = tempfile.mkdtemp()
        self.opened = []

    def tearDown(self):
        self.root.destroy()

    def open(self, archetypes, session, db=None):
        from combo_panel import ComboPanel
        panel = ComboPanel(self.root, db or make_db(), MAIN, EXTRA, archetypes, on_close=lambda: None,
                           session=session, image_folder=self.folder)
        end = time.time() + 3
        while not panel.sets and time.time() < end and "Lade" in panel.status_label.cget("text"):
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

    def test_list_filter_detail_and_back(self):
        panel = self.open(["Elfnote"], FakeSession())
        self.assertIn("Elfnote: 3 Combos, 2 mit deinem Deck spielbar", panel.status_label.cget("text"))
        rows = panel.inner.winfo_children()
        self.assertEqual(len(rows), 2)  # nur spielbare
        texts = self.texts(panel.inner)
        self.assertIn("Tinia Loop", texts)
        self.assertIn("Elfnote Tinia + beliebig (Non-FIRE)", texts)
        self.assertNotIn("Braucht Ash", texts)
        self.assertEqual(panel.filter_btn.cget("text"), "Nur spielbare")
        panel.filter_btn.invoke()
        self.assertEqual(panel.filter_btn.cget("text"), "Alle")
        texts = self.texts(panel.inner)
        self.assertIn("Braucht Ash", texts)
        self.assertIn("Fehlt: Ash Blossom & Joyous Spring", texts)
        panel.filter_btn.invoke()
        self.root.update()  # erst anzeigen, sonst kommt der Klick nicht an
        # Klick auf die erste Zeile (Tinia Loop: empfohlen) → Detailansicht
        panel.inner.winfo_children()[0].event_generate("<Button-1>")
        self.root.update()
        self.assertEqual(panel.detail.combo.mdm_id, "a")
        texts = self.texts(panel.inner)
        self.assertIn("1. Elfnote Tinia – Special Summon → Elfnotes: Welcome Home – Place", texts)
        self.assertIn("2. Elfnote Regina – Quick Effect + Elfnote Lucina – Add", texts)
        self.assertIn("◆ Wenn Ash kommt", texts)
        self.assertIn("▸ Add Lucina", texts)
        self.assertIn("Zalen the Shackled Dragon – Omni Negate", texts)
        self.assertIn("Nibiru, the Primal Being, Ash Blossom & Joyous Spring", texts)
        self.assertIn("von Raijn · ▲ 2 · Stand 02.10.2026", texts)
        self.assertIn("beliebig: Any Non-FIRE Monster", texts)
        self.assertIn("Elfnote Tinia", panel.grid.texts())  # ohne Bild: Name als Platzhalter
        self.assertFalse([w for w in panel.grid.canvas.master.winfo_children() if isinstance(w, tk.Scrollbar)])
        self.assertEqual([w.cget("text") for w in panel.bar.winfo_children()],
                         ["← Zurück", "Auf Master Duel Meta öffnen"])
        panel.bar.winfo_children()[0].invoke()
        self.assertIsNone(panel.detail)
        self.assertEqual(len(panel.inner.winfo_children()), 2)
        panel.close()

    def test_browser_only_on_click(self):
        with unittest.mock.patch("combo_panel.webbrowser.open") as browser:
            panel = self.open(["Elfnote"], FakeSession())
            panel.show_combo(0)
            browser.assert_not_called()
            panel.bar.winfo_children()[1].invoke()
            browser.assert_called_once_with("https://www.masterduelmeta.com/tier-list/deck-types/Elfnote")
            panel.close()

    def test_offline_unknown_and_no_archetype(self):
        panel = self.open(["Elfnote"], FakeSession(offline=True))
        self.assertIn("nicht geladen", panel.status_label.cget("text"))
        panel.close()
        panel = self.open(["Gibt es nicht"], FakeSession())
        self.assertIn("Keine Combos gefunden", panel.status_label.cget("text"))
        panel.close()
        panel = self.open([], FakeSession())
        self.assertIn("Keine Archetypen", panel.status_label.cget("text"))
        panel.close()

    def test_switch_between_deck_types(self):
        db = make_db()
        db.store_combos("Hybrid", mc.ComboSet("Hybrid", [mc.parse_combo(COMBO_C, "Hybrid")], time.time()).to_json())
        panel = self.open(["Elfnote", "Hybrid"], FakeSession(), db)
        self.assertEqual(len(panel.sets), 2)
        panel.show_type(1)
        self.assertIn("Hybrid: 1 Combos", panel.status_label.cget("text"))
        panel.close()


if __name__ == "__main__":
    unittest.main()
