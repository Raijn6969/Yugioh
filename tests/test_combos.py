"""Combos von Master Duel Meta: Antwort lesen, laden, mit dem Deck abgleichen, Fenster – ohne Internet."""

import io
import tempfile
import time
import unittest
import unittest.mock
from itertools import combinations

import card_images
from dark_scrollbar import DarkScrollbar
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


class ImageSession(FakeSession):
    """Wie FakeSession, aber Kartenbilder gibt es erst, wenn ready gesetzt ist."""

    ready = False
    gate = None  # Event: Bilder kommen erst, wenn es gesetzt ist

    def get(self, url, params=None, timeout=None):
        if "images.ygoprodeck.com" in url and self.ready:
            if self.gate is not None:
                self.gate.wait(5)
            from PIL import Image
            data = io.BytesIO()
            Image.new("RGB", (20, 30), "red").save(data, "JPEG")
            return type("R", (), {"status_code": 200, "content": data.getvalue()})()
        return super().get(url, params, timeout)


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


class AttributesTest(unittest.TestCase):
    def test_attributes_in_order_without_duplicates(self):
        from combo_panel import attributes
        self.assertEqual(attributes("Any Fire monster"), [("FIRE", False)])
        self.assertEqual(attributes("Any Non-FIRE Monster"), [("FIRE", True)])
        self.assertEqual(attributes("Fire or Wind"), [("FIRE", False), ("WIND", False)])
        self.assertEqual(attributes("any non-wind monster"), [("WIND", True)])
        self.assertEqual(attributes("WIND, wind and Fire"), [("WIND", False), ("FIRE", False)])
        self.assertEqual(attributes("nonfire DARK"), [("FIRE", True), ("DARK", False)])
        self.assertEqual(attributes("Any monster"), [])
        self.assertEqual(attributes("Firewall Lightning"), [])  # nur ganze Wörter


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
        """Texte aller Labels und Zeichenflächen darin."""
        result = []
        for child in widget.winfo_children():
            if isinstance(child, tk.Label):
                result.append(child.cget("text"))
            elif isinstance(child, tk.Canvas):
                result += self.canvas_texts(child)
            result += self.texts(child)
        return result

    @staticmethod
    def canvas_texts(canvas):
        return [canvas.itemcget(i, "text") for i in canvas.find_all() if canvas.type(i) == "text"]

    @staticmethod
    def canvas_types(canvas):
        return [canvas.type(i) for i in canvas.find_all()]

    @staticmethod
    def canvases(widget):
        return [c for c in widget.winfo_children() if isinstance(c, tk.Canvas)]

    def test_mausrad_scrollt_combo_liste_auch_ueber_zeilen(self):
        panel = self.open(["Elfnote"], FakeSession())
        self.root.attributes("-alpha", 0)  # unsichtbar, aber eingeblendet: nur dann kommen Tk-Ereignisse an
        self.root.deiconify()
        self.root.geometry("500x150+0+0")
        panel.win.pack(fill=tk.BOTH, expand=True)
        self.root.update()
        for _ in range(20):  # Liste länger als das Fenster
            tk.Frame(panel.inner, height=60, bg="#222").pack(fill=tk.X)
        self.root.update()
        zeile = panel.inner.winfo_children()[0]  # Kind der Liste, nicht der Canvas selbst
        zeile.event_generate("<MouseWheel>", delta=-120, x=5, y=5)
        self.assertGreater(panel.canvas.yview()[0], 0)

    def test_list_filter_detail_and_back(self):
        panel = self.open(["Elfnote"], FakeSession())
        self.assertIn("Elfnote: 3 Combos, 2 mit deinem Deck spielbar", panel.status_label.cget("text"))
        rows = panel.inner.winfo_children()
        self.assertEqual(len(rows), 2)  # nur spielbare
        texts = self.texts(panel.inner)
        self.assertIn("Tinia Loop", texts)
        self.assertIn("Elfnote Tinia + beliebig (Non-FIRE)", texts)
        self.assertNotIn("Braucht Ash", texts)
        # je Zeile eine Zeichenfläche; "beliebig" als graue Fläche (hier mit Attribut statt "?"), Karte ohne Bild leer
        first, second = self.canvases(panel.inner)
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.canvas_texts(first).count("?"), 0)  # Tinia Loop: Tinia + beliebig (Non-FIRE)
        self.assertEqual(self.canvas_types(first).count("rectangle"), 2)
        self.assertEqual(self.canvas_texts(second).count("?"), 0)  # Regina
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
        self.assertFalse([t for t in texts if "×" in t])  # keine Kopienzahl unter der Starthand
        self.assertNotIn("1. Elfnote Tinia – Special Summon → Elfnotes: Welcome Home – Place", texts)  # kein Text
        # je Schritt eine Zeichenfläche (Schritte 1, 2, dann Zweig "Add Lucina" und "Tief"), dazu das Endboard
        steps = self.canvases(panel.inner)
        self.assertEqual(len(steps), 5)
        one, two = self.canvas_texts(steps[0]), self.canvas_texts(steps[1])
        self.assertEqual((one[0], "→" in one, "Special Summon" in one, "Place" in one), ("1.", True, True, True))
        self.assertEqual(("+" in two, "→" in two), (True, False))  # Verbinder "plus"
        self.assertEqual(self.canvas_types(steps[0]).count("rectangle"), 2)  # ohne Bilder: Platzhalter je Karte
        self.assertIn("Elfnote Tinia", one)
        self.assertIn("Omni Negate", self.canvas_texts(steps[-1]))  # Endboard mit Label unter dem Bild
        self.assertIn("Zalen the Shackled Dragon", self.canvas_texts(steps[-1]))
        self.assertIn("◆ Wenn Ash kommt", texts)
        self.assertIn("▸ Add Lucina", texts)
        self.assertIn("Nibiru, the Primal Being, Ash Blossom & Joyous Spring", texts)
        self.assertIn("von Raijn · ▲ 2 · Stand 02.10.2026", texts)
        self.assertIn("beliebig: Any Non-FIRE Monster", texts)
        self.assertIn("Elfnote Tinia", panel.grid.texts())  # ohne Bild: Name als Platzhalter
        self.assertFalse([w for w in panel.grid.canvas.master.winfo_children() if isinstance(w, DarkScrollbar)])
        self.assertEqual([w.cget("text") for w in panel.bar.winfo_children()],
                         ["← Zurück", "Im Browser öffnen"])
        panel.bar.winfo_children()[0].invoke()
        self.assertIsNone(panel.detail)
        self.assertEqual(len(panel.inner.winfo_children()), 2)
        panel.close()

    def start_panel(self, *generics):
        """Panel mit einer Combo, deren Starthand Elfnote Regina + je ein Platzhalter-Text ist."""
        start = [card("Elfnote Regina")] + [{"generic": g} for g in generics]
        db = make_db()
        data = mc.parse_combo({**COMBO_C, "startingCards": start}, "Elfnote")
        db.store_combos("Elfnote", mc.ComboSet("Elfnote", [data], time.time()).to_json())
        return self.open(["Elfnote"], FakeSession(), db)

    def test_attribute_symbols_in_list_and_detail(self):
        panel = self.start_panel("Any FIRE monster")
        self.root.update()
        row = self.canvases(panel.inner)[0]
        self.assertEqual(self.canvas_texts(row).count("?"), 0)
        ovals = [i for i in row.find_all() if row.type(i) == "oval"]
        self.assertEqual([row.itemcget(i, "fill") for i in ovals], ["#d32f2f"])
        self.assertEqual(self.canvas_types(row).count("line"), 0)  # nicht verneint: kein Schrägstrich
        self.assertIn("FIRE", self.canvas_texts(row))  # 30 px breit: Name oben im Symbol
        # Detail: Symbol vor der Zeile "beliebig: …"
        panel.show_combo(0)
        self.root.update()
        frames = [f for f in panel.inner.winfo_children() if isinstance(f, tk.Frame) and self.canvases(f)]
        self.assertEqual(len(frames), 1)
        symbol = self.canvases(frames[0])[0]
        self.assertEqual(self.canvas_types(symbol).count("oval"), 1)
        self.assertIn("beliebig: Any FIRE monster", self.texts(frames[0]))
        self.assertLess(symbol.winfo_x(), frames[0].winfo_children()[1].winfo_x())  # Symbol links vom Text
        panel.close()

    def test_negated_two_and_many_attributes(self):
        panel = self.start_panel("Any Non-WIND monster")
        row = self.canvases(panel.inner)[0]
        self.assertEqual(self.canvas_types(row).count("oval"), 1)
        lines = [i for i in row.find_all() if row.type(i) == "line"]  # dunkle Unterlage, darüber der weiße Strich
        self.assertEqual([row.itemcget(i, "fill") for i in lines], ["#111111", "#ffffff"])
        panel.close()
        panel = self.start_panel("Fire or Wind monster")
        row = self.canvases(panel.inner)[0]
        ovals = [i for i in row.find_all() if row.type(i) == "oval"]
        self.assertEqual([row.itemcget(i, "fill") for i in ovals], ["#d32f2f", "#2e7d32"])
        self.assertNotIn("FIRE", self.canvas_texts(row))  # zwei kleine Symbole: ohne Namen
        panel.show_combo(0)
        frame = next(f for f in panel.inner.winfo_children() if isinstance(f, tk.Frame) and self.canvases(f))
        self.assertEqual(self.canvas_types(self.canvases(frame)[0]).count("oval"), 2)
        panel.close()
        panel = self.start_panel("Fire, Water or Wind monster", "Any monster")  # drei bzw. keins → "?"
        row = self.canvases(panel.inner)[0]
        self.assertEqual((self.canvas_texts(row).count("?"), self.canvas_types(row).count("oval")), (2, 0))
        panel.show_combo(0)
        self.assertFalse([f for f in panel.inner.winfo_children() if isinstance(f, tk.Frame) and self.canvases(f)
                          and self.canvas_types(self.canvases(f)[0]).count("oval")])
        panel.close()

    def test_list_appears_before_the_images_and_is_redrawn(self):
        import threading
        session = ImageSession()
        session.ready, session.gate = True, threading.Event()
        db = make_db()
        db.ids_by_name = lambda names: {n: [str(100000 + sum(map(ord, n)))] for n in names}
        with unittest.mock.patch.object(card_images, "PAUSE", 0):
            try:
                panel = self.open(["Elfnote"], session, db)  # die Bilder warten noch auf das Signal
                self.assertTrue(panel.sets)
                self.assertTrue(panel._thread.is_alive())
                row = self.canvases(panel.inner)[0]
                self.assertEqual(self.canvas_types(row).count("image"), 0)  # graue Felder
                panel.canvas.yview_moveto(0.0)
                session.gate.set()
                end = time.time() + 5
                while time.time() < end and "image" not in self.canvas_types(self.canvases(panel.inner)[0]):
                    self.root.update()
                    time.sleep(0.02)
                self.assertEqual(self.canvas_types(self.canvases(panel.inner)[0]).count("image"), 1)  # Tinia
            finally:
                session.gate.set()
                panel.close()

    def test_tooltip_goes_away_when_hidden(self):
        panel = self.open(["Elfnote"], FakeSession())
        panel._show_tip(type("E", (), {"x_root": 5, "y_root": 5})(), "Karte", "Aktion")
        self.assertIsNotNone(panel._tip)
        panel.set_visible(False)
        self.assertIsNone(panel._tip)
        panel.close()

    def entries_canvas(self, panel, entries, indent=0):
        """Zeichnet die Einträge als Schritt; (Zeichenfläche, Rechtecke der Karten, [(Zeichen, x, y)] der Verbinder)."""
        panel._entries(entries, "1.", indent)
        canvas = self.canvases(panel.inner)[-1]
        rects = [canvas.coords(i) for i in canvas.find_all() if canvas.type(i) == "rectangle"]
        joints = [(canvas.itemcget(i, "text"), *canvas.coords(i)) for i in canvas.find_all()
                  if canvas.type(i) == "text" and canvas.itemcget(i, "text") in ("→", "+")]
        return canvas, rects, joints

    @staticmethod
    def entry(name, connector=""):
        return mc.Entry(name, "Aktion", connector)

    def test_connectors_sit_between_the_picture_edges(self):
        panel = self.open(["Elfnote"], FakeSession())
        entries = [self.entry("A", "arrow"), self.entry("B", "plus"), self.entry("C")]
        canvas, rects, joints = self.entries_canvas(panel, entries)
        self.assertEqual([j[0] for j in joints], ["→", "+"])
        for k, (_, jx, jy) in enumerate(joints):
            self.assertAlmostEqual(jx, (rects[k][2] + rects[k + 1][0]) / 2, delta=1)  # mittig zwischen den Bildern
            self.assertAlmostEqual(jy, (rects[k][1] + rects[k][3]) / 2, delta=1)       # auf Bildhöhe
        fonts = {canvas.itemcget(i, "font") for i in canvas.find_all() if canvas.type(i) == "text"
                 and canvas.itemcget(i, "text") in ("→", "+")}
        self.assertEqual(len(fonts), 1)  # "+" und "→" in derselben Schrift
        item_w = panel.step_photos.size[0] + int(20 * panel.s)
        labels = [i for i in canvas.find_all() if canvas.type(i) == "text" and canvas.itemcget(i, "text") == "Aktion"]
        self.assertEqual({int(float(canvas.itemcget(i, "width"))) for i in labels},
                         {item_w + int(18 * panel.s) - int(4 * panel.s)})  # Labels brechen erst weit unter dem Bild um
        panel.close()

    def test_connector_at_end_of_row_or_before_the_new_row(self):
        panel = self.open(["Elfnote"], FakeSession())
        s = panel.s
        entries = [self.entry(f"K{i}", "arrow") for i in range(9)]
        canvas, rects, joints = self.entries_canvas(panel, entries)
        self.assertEqual(len(joints), 8)
        _, jx, jy = joints[4]  # Umbruch nach dem fünften Bild: der Verbinder steht dahinter
        self.assertTrue(rects[4][2] < jx < rects[4][2] + 38 * s)
        self.assertAlmostEqual(jy, (rects[4][1] + rects[4][3]) / 2, delta=1)
        self.assertGreater(rects[5][1], rects[4][3])  # das sechste beginnt in der nächsten Reihe
        # zu schmal für den Verbinder hinter dem letzten Bild: er steht vor dem ersten Bild der neuen Reihe
        w = panel.step_photos.size[0]
        item_w, joint, left = w + int(20 * s), int(18 * s), int(24 * s)
        indent = int(460 * s) - (left + 3 * item_w + 2 * joint + int(8 * s))  # nur drei Einträge pro Reihe
        canvas, rects, joints = self.entries_canvas(panel, entries[:5], indent)
        _, jx, jy = joints[2]
        self.assertGreater(rects[3][1], rects[2][3])
        self.assertAlmostEqual(jx, (left + rects[3][0]) / 2, delta=1)  # mittig zwischen Nummernspalte und Bild
        self.assertAlmostEqual(jy, (rects[3][1] + rects[3][3]) / 2, delta=1)
        panel.close()

    def test_tooltip_on_card_in_step(self):
        panel = self.open(["Elfnote"], FakeSession())
        panel.show_combo(0)
        self.root.update()
        canvas = self.canvases(panel.inner)[0]
        x0, y0, x1, y1 = canvas.bbox("entry0")
        self.assertIsNone(panel._tip)
        canvas.event_generate("<Motion>", x=(x0 + x1) // 2, y=y0 + 5)
        self.root.update()
        self.assertIsNotNone(panel._tip)
        self.assertEqual(self.texts(panel._tip), ["Elfnote Tinia", "Special Summon"])
        canvas.event_generate("<Motion>", x=x1 + 400, y=y1 + 400)
        self.root.update()
        self.assertIsNone(panel._tip)
        panel._show_tip(type("E", (), {"x_root": 5, "y_root": 5})(), "X", "")
        panel.back()  # Wechsel der Ansicht entfernt den Tooltip
        self.assertIsNone(panel._tip)
        panel._show_tip(type("E", (), {"x_root": 5, "y_root": 5})(), "X", "")
        panel.close()
        self.assertIsNone(panel._tip)

    def test_twenty_steps_with_connectors_wrap_into_rows(self):
        entries = [{**card(f"Karte {i}"), "label": "Aktion", "connector": "arrow"} for i in range(9)]
        data = {**COMBO_C, "steps": [{"entries": entries[:3]}] * 19 + [{"entries": entries}]}
        db = make_db()
        db.store_combos("Elfnote", mc.ComboSet("Elfnote", [mc.parse_combo(data, "Elfnote")], time.time()).to_json())
        panel = self.open(["Elfnote"], FakeSession(), db)
        start = time.perf_counter()
        panel.show_combo(0)
        self.root.update()
        print(f"\n20 Schritte gezeichnet in {(time.perf_counter() - start) * 1000:.0f} ms", end=" ")
        steps = self.canvases(panel.inner)
        self.assertEqual(len(steps), 20)
        last = steps[-1]
        self.assertEqual(self.canvas_texts(last).count("→"), 8)
        self.assertGreater(int(last.cget("height")), 2 * 67)  # neun Karten brauchen mehr als eine Reihe
        self.assertLess(int(steps[0].cget("height")), 2 * 67 + 20)
        panel.close()

    def test_images_of_steps_are_loaded_when_opening_a_combo(self):
        session = ImageSession()
        db = make_db()
        db.ids_by_name = lambda names: {n: [str(100000 + sum(map(ord, n)))] for n in names}
        with unittest.mock.patch.object(card_images, "PAUSE", 0):
            panel = self.open(["Elfnote"], session, db)  # Startkarten: Bilder noch nicht da (404)
            panel._thread.join(5)  # der Worker ist mit den Bildern der Liste fertig
            session.ready = True
            panel.show_combo(0)
            self.root.update()
            first = self.canvases(panel.inner)[0]
            self.assertEqual(self.canvas_types(first).count("image"), 0)  # sofort mit Platzhaltern
            self.assertEqual(len(panel._image_threads), 1)
            end = time.time() + 5
            while (panel._image_threads or panel._image_job) and time.time() < end:
                self.root.update()
                time.sleep(0.02)
            self.root.update()
            first = self.canvases(panel.inner)[0]
            # "Welcome Home" ist neu geladen; Tinia wurde schon mit den Startkarten (vergeblich) gesucht
            self.assertEqual(self.canvas_types(first).count("image"), 1)
            self.assertEqual(self.canvas_types(first).count("rectangle"), 1)
            panel.back()
            panel.show_combo(0)
            self.assertEqual(panel._image_threads, [])  # alles versucht: kein neuer Thread
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
