"""
Kompletter Import gegen ein nachgebautes Master Duel (Suche, Raster, Detail-Panel, Deck,
Deck-Zählung). Keine echten Klicks, keine echte Zwischenablage, kein Netzwerk.
Das Spiel kann gezielt Fehler einbauen, um die Selbstheilung zu prüfen.
"""

import os
import tempfile
import unittest
from collections import Counter
from unittest import mock

import import_engine as ie
import resume_state
from utils import clean_text, sanitize_name
from window_automation import UserInterrupt

DECK = {  # ID → (Name, Anzahl, Typ)
    "1": ("Artmage Power Patron", 2, "Effect Monster"),
    "2": ("Artmage Finmel", 3, "Effect Monster"),
    "3": ("Artmage Litera", 2, "Effect Monster"),
    "4": ("Ash Blossom & Joyous Spring", 3, "Tuner Monster"),
    "5": ('Maxx "C"', 1, "Effect Monster"),
    "6": ("The Fallen & The Virtuous", 3, "Trap Card"),
    "7": ("Nerva the Power Patron of Creation", 1, "Fusion Monster"),
}
CARD_IDS = [cid for cid, (_, amount, _) in DECK.items() for _ in range(amount)]
ARTMAGE_RESULTS = ["artmagepowerpatron", "lnervathepowerpatronof", "artmagefinmel",
                   "artmagelitera", "artmagediactorus"]
NERVA_OCR = "lnervathepowerpatronof"  # so liest die Texterkennung Nerva im Raster
EXTRA_DECK_TEXTS = {NERVA_OCR}
EXPECTED_DECK = Counter({clean_text(sanitize_name(n)): a for n, a, _ in DECK.values() if "Nerva" not in n})
EXPECTED_DECK[NERVA_OCR] = 1


class FakeMasterDuel:
    """Suchbegriff → Raster; Klick auf Slot → Detail-Panel; Rechtsklick → Karte im Deck."""

    def __init__(self, slow_searches=None, lost_clicks=None, abort_after_adds=None, grids=None,
                 lag_after=None, lost_searches=None):
        self.grid = ["splittleknight"]
        self.grids = dict(grids or {})                  # Suchtext → eigenes Raster
        self.lag_after = dict(lag_after or {})          # Panel-Text → so viele Folgeklicks hängen
        self.lost_searches = dict(lost_searches or {})  # Suchtext → so oft kommt die Eingabe nicht an
        self.ignored_clicks = 0
        self.row_offset = 0                              # Gescrollte Zeilen (wie im Spiel: ¾ Zeile pro Raste)
        self.scrolls = []
        self.panel = ""
        self.deck = Counter()
        self.searches = []
        self.trash_clicks = 0
        self.adds = 0
        self.slow_searches = dict(slow_searches or {})   # Suchtext → so oft lädt die Suche "zu spät"
        self.lost_clicks = dict(lost_clicks or {})       # Panel-Text → so viele Klicks gehen verloren
        self.abort_after_adds = abort_after_adds         # Maus "bewegt" nach so vielen Einfügungen

    def search(self, automator, text):
        self.searches.append(text)
        if self.lost_searches.get(text, 0) > 0:  # Eingabe verloren: Raster zeigt weiter die alte Suche
            self.lost_searches[text] -= 1
            return
        self.row_offset = 0  # Neue Suche → Liste beginnt oben
        if self.slow_searches.get(text, 0) > 0:
            self.slow_searches[text] -= 1
            self.grid = ["splittleknight"]  # Ergebnis noch nicht da → Karte wird nicht gefunden
        elif text in self.grids:
            self.grid = list(self.grids[text])
        elif text == "artmage":
            self.grid = list(ARTMAGE_RESULTS)
        else:
            self.grid = [clean_text(text)]

    def slot_text(self, slot):
        index = slot + self.row_offset * 6
        return self.grid[index] if index < len(self.grid) else self.grid[-1]  # leere Slots zeigen die letzte Karte

    def scroll(self, notches):
        self.scrolls.append(notches)
        max_offset = max(0, (len(self.grid) - 1) // 6)
        rows = round(notches * 0.75)
        self.row_offset = min(max(self.row_offset - rows, 0), max_offset)

    def click(self, x, y, button="left"):
        if x < 100:  # Slots liegen bei x = Slot-Index; Buttons weiter rechts
            if self.ignored_clicks > 0:  # Spiel hängt: Klick kommt nicht an, Panel bleibt
                self.ignored_clicks -= 1
                return
            self.panel = self.slot_text(x)
            self.ignored_clicks = self.lag_after.pop(self.panel, 0)
        elif (x, y) == (500, 10):
            self.trash_clicks += 1
            self.deck.clear()

    def add(self, x, y, amount):
        if self.abort_after_adds is not None and self.adds >= self.abort_after_adds:
            raise UserInterrupt("Manuelle Mausbewegung erkannt! Abbruch.")
        self.adds += 1
        self.click(x, y)
        lost = min(self.lost_clicks.pop(self.panel, 0), amount)
        self.deck[self.panel] += amount - lost
        return False  # kein Ruckler erkannt

    def read(self, sct, monitor):
        return self.panel, self.panel

    def main_deck_count(self):
        return sum(n for text, n in self.deck.items() if text not in EXTRA_DECK_TEXTS)

    def extra_deck_count(self):
        return sum(n for text, n in self.deck.items() if text in EXTRA_DECK_TEXTS)


def distinct_name(i):
    """Deutlich verschiedene Kartennamen (sonst hält die Ghost-Erkennung sie für dieselbe Karte)."""
    return "".join(chr(97 + (i * 11 + k * 3) % 26) for k in range(12)) + str(i)


class FakeAutomator:
    def __init__(self, game):
        self.game = game
        self.scale_x = self.scale_y = 1.0
        self.last_bot_pos = (0, 0)

    def iron_grip_click(self, x, y, button="left"):
        self.game.click(x, y)

    def add_card_to_deck(self, x, y, amount):
        return self.game.add(x, y, amount)

    def scroll(self, x, y, notches):
        self.game.scroll(notches)

    def is_crafting_active(self):
        return True


def make_fake_counter(game):
    class FakeDeckCounter:
        def __init__(self, *args, point=None, **kwargs):
            self.value = None
            self.enabled = True
            self.extra = point is not None  # mit eigenem Punkt = Zahl des Extra Decks

        def read(self):
            return game.extra_deck_count() if self.extra else game.main_deck_count()

        def wait_for(self, expected, timeout):
            return self.read()
    return FakeDeckCounter


# Echte Wartezeiten (< 1 s) bleiben erhalten, nur der 3-Sekunden-Countdown wird übersprungen
_real_sleep = ie.time.sleep


class ImportFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.resume_file = os.path.join(self.tmp, "md_resume.json")
        self.patches = [mock.patch.object(resume_state, "RESUME_FILE", self.resume_file)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def run_import(self, game, deck_count=False, resume=None, deck=DECK, row_height=10, card_db=None):
        log_file = os.path.join(self.tmp, "md_debug.log")
        clipboard = mock.Mock()
        clipboard.paste.return_value = "ydke://DECKCODE"
        finished = {}
        config = {"SPEED_PROFILE": "fast", "TRASH_BTN": [500, 10], "TRASH_CONFIRM": [500, 20],
                  "UNOWNED_BTN": [500, 30], "FIRST_CARD": [0, 100]}
        if deck_count:
            config["DECK_COUNT"] = [700, 50]

        core = ie.DeckImporterCore(config, "", lambda *a: None, lambda **k: finished.update(k),
                                   resume=resume)
        core._preflight = lambda: None
        core._type_search_term = game.search
        # row_height=450 → nur 3 Zeilen (18 Slots) passen auf den "Bildschirm"
        core._get_slot_geometry = lambda slot, automator: ({"slot": slot}, slot, 100 + (slot // 6) * row_height)
        core._capture_and_ocr_slot = game.read

        card_ids = [cid for cid, (_, amount, _) in deck.items() for _ in range(amount)]
        with mock.patch.object(ie, "parse_clipboard", return_value=card_ids), \
                mock.patch.object(ie, "fetch_card_names", return_value={c: n for c, (n, _, _) in deck.items()}), \
                mock.patch.object(ie, "fetch_card_types", return_value={c: t for c, (_, _, t) in deck.items()}), \
                mock.patch.object(ie, "WindowAutomator", lambda config: FakeAutomator(game)), \
                mock.patch.object(ie, "DeckCounter", make_fake_counter(game)), \
                mock.patch.object(ie, "LOG_FILE", log_file), \
                mock.patch.object(ie, "load_card_db", return_value=card_db) if card_db else \
                mock.patch.object(ie, "load_card_db", side_effect=RuntimeError("offline")), \
                mock.patch.object(ie, "pyperclip", clipboard), \
                mock.patch.object(ie.time, "sleep", lambda s: None if s >= 1 else _real_sleep(s)):
            core.execute_import()

        with open(log_file, encoding="utf-8") as f:
            log = f.read()
        return finished, log, clipboard, core

    def test_damaged_deck_code_stops_before_anything_is_clicked(self):
        finished = {}
        core = ie.DeckImporterCore({}, "", lambda *a: None, lambda **k: finished.update(k))
        with mock.patch.object(ie, "parse_clipboard", side_effect=ie.DeckCodeError("Der YDKE-Code ist beschädigt.")), \
                mock.patch.object(ie, "WindowAutomator") as automator, \
                mock.patch.object(ie, "LOG_FILE", os.path.join(self.tmp, "md_debug.log")), \
                mock.patch.object(ie, "pyperclip"):
            core.execute_import()
        automator.assert_not_called()
        self.assertFalse(finished["success"])
        self.assertIn("beschädigt", finished["message"])

    def test_full_import(self):
        game = FakeMasterDuel()
        finished, log, clipboard, _ = self.run_import(game)

        self.assertEqual(+game.deck, +EXPECTED_DECK)
        self.assertEqual(finished, {"success": True, "has_errors": False, "failed_cards": [], "notes": [],
                                    "scan": None})  # ohne Kartenliste keine Kontrolle → kein Scan
        # Artmage-Gruppe + Nerva per Archetyp-Suche, Rest einzeln
        self.assertEqual(game.searches, ["artmage", sanitize_name("Ash Blossom & Joyous Spring"),
                                         sanitize_name('Maxx "C"'), "The Fallen & The Virtuous"])
        clipboard.copy.assert_called_with("ydke://DECKCODE")  # Deck-Code wiederhergestellt
        self.assertIn("[BATCH] Ergebnis: 3/3 gefunden (+1 opportunistisch)", log)
        self.assertIn("[DAUER] Import in", log)
        self.assertNotIn("CRITICAL", log)
        self.assertFalse(os.path.exists(self.resume_file))  # nach Erfolg kein Fortsetzen-Stand

    def test_second_pass_recovers_card(self):
        # Die erste Suche nach Maxx C lädt zu spät → Karte fehlt → zweiter Durchgang holt sie
        game = FakeMasterDuel(slow_searches={sanitize_name('Maxx "C"'): 1})
        finished, log, _, _ = self.run_import(game)

        self.assertEqual(+game.deck, +EXPECTED_DECK)
        self.assertTrue(finished["success"])
        self.assertEqual(finished["failed_cards"], [])
        self.assertIn("[NACHLAUF] 1 von 1 Karte(n) nachgeholt.", log)
        self.assertIn("1 Karte(n) wurden im zweiten Durchgang nachgeholt.", finished["notes"])

    def test_lost_clicks_are_detected_and_repeated(self):
        # Beim Einfügen von Ash Blossom gehen 2 von 3 Klicks verloren (Ruckler)
        game = FakeMasterDuel(lost_clicks={"ashblossomjoyousspring": 2})
        finished, log, _, _ = self.run_import(game, deck_count=True)

        self.assertEqual(+game.deck, +EXPECTED_DECK)  # genau 3x, keine Kopie zu viel
        self.assertEqual((finished["has_errors"], finished["failed_cards"]), (False, []))
        self.assertIn("[PRÜFUNG] Deck-Zählung aktiv, Startwert: 0.", log)
        self.assertIn("→ 2x nachklicken", log)

    def test_extra_deck_card_not_checked_against_main_count(self):
        # Nerva (Fusion) erhöht die Main-Deck-Zahl nicht → darf NICHT nachgeklickt werden
        game = FakeMasterDuel()
        finished, log, _, _ = self.run_import(game, deck_count=True)
        self.assertEqual(game.deck[NERVA_OCR], 1)
        self.assertNotIn("nachklicken", log)
        self.assertFalse(finished["has_errors"])

    def test_lost_click_on_extra_deck_card_is_repeated(self):
        # Arthalion-Fall: Beim Ruckler geht der Klick auf die Fusion verloren → Extra-Deck-Zahl prüft nach
        game = FakeMasterDuel(lost_clicks={NERVA_OCR: 1})
        finished, log, _, _ = self.run_import(game, deck_count=True)
        self.assertEqual(game.deck[NERVA_OCR], 1)
        self.assertIn("[PRÜFUNG] Extra-Deck-Zählung aktiv, Startwert: 0.", log)
        self.assertIn("→ 1x nachklicken", log)
        self.assertFalse(finished["has_errors"])

    def test_similar_name_shown_first_is_not_taken(self):
        # Suche "Clockwork Night" zeigt zuerst Clockwork Knight → muss Slot 1 nehmen
        deck = {"10": ("Clockwork Knight", 2, "Effect Monster"),
                "11": ("Clockwork Night", 1, "Spell Card")}
        grids = {"Clockwork Night": ["clockworkknight", "clockworknight"],
                 "Clockwork Knight": ["clockworkknight", "clockworknight"]}
        for order in (deck, dict(reversed(list(deck.items())))):
            with self.subTest(first=next(iter(order.values()))[0]):
                game = FakeMasterDuel(grids=grids)
                finished, log, _, _ = self.run_import(game, deck=order)
                self.assertEqual(+game.deck, Counter({"clockworkknight": 2, "clockworknight": 1}))
                self.assertEqual((finished["has_errors"], finished["failed_cards"]), (False, []))
                self.assertIn("Knight/Night", log)

    def test_batch_does_not_take_similar_name(self):
        # Archetyp-Suche "clockwork": Nach Knight zeigt der nächste Slot Knight in anderer
        # Seltenheit. Der darf NICHT als Clockwork Night eingefügt werden.
        deck = {"10": ("Clockwork Knight", 2, "Effect Monster"),
                "11": ("Clockwork Night", 1, "Spell Card"),
                "12": ("Clockwork Scorpion", 1, "Effect Monster")}
        grids = {"clockwork": ["clockworkknight", "clockworkknight", "clockworknight", "clockworkscorpion"]}
        game = FakeMasterDuel(grids=grids)
        finished, log, _, _ = self.run_import(game, deck=deck)
        self.assertEqual(+game.deck, Counter({"clockworkknight": 2, "clockworknight": 1, "clockworkscorpion": 1}))
        self.assertEqual((finished["has_errors"], finished["failed_cards"]), (False, []))
        self.assertIn("aber Knight/Night-Konflikt", log)

    def test_scrolls_when_card_not_visible(self):
        # 'Cyber Dragon' liefert mehr Ergebnisse als sichtbar (5 Zeilen wie im Spiel); die Karte
        # selbst steht in Zeile 7
        grid = [distinct_name(i) for i in range(36)] + ["cyberdragon"] + [distinct_name(i) for i in range(40, 50)]
        deck = {"20": ("Cyber Dragon", 1, "Effect Monster")}
        game = FakeMasterDuel(grids={"Cyber Dragon": grid})
        finished, log, _, _ = self.run_import(game, deck=deck, row_height=200)
        self.assertEqual(+game.deck, Counter({"cyberdragon": 1}))
        self.assertEqual((finished["has_errors"], finished["failed_cards"]), (False, []))
        self.assertIn("[SCROLL]", log)
        self.assertEqual(game.scrolls, [-4, 6])  # eine Seite (3 Zeilen) runter, danach sicher ganz hoch
        self.assertIn("[SCROLL] Raster um 3 Zeile(n) verschoben.", log)
        self.assertIn("[OK]", log)

    def test_scroll_stops_at_end_of_list(self):
        # Karte gibt es nicht: nach dem Scrollen Ende erkennen, wieder hochscrollen, als Lücke melden
        grid = [distinct_name(i) for i in range(20)]
        deck = {"20": ("Cyber Dragon", 1, "Effect Monster")}
        game = FakeMasterDuel(grids={"Cyber Dragon": grid})
        finished, log, _, _ = self.run_import(game, deck=deck, row_height=450)
        self.assertEqual(+game.deck, Counter())
        self.assertEqual(finished["failed_cards"], ["Cyber Dragon (1x fehlend)"])
        # Erster und zweiter Durchgang: je 4 Rasten runter, danach mit Reserve wieder ganz hoch
        self.assertEqual(game.scrolls, [-4, 6, -4, 6])

    def test_other_deck_card_in_results_is_taken_along(self):
        # Suche 'Cyber Dragon Nova' zeigt zuerst Cyber Dragon Infinity (auch im Deck) → gleich
        # mitnehmen, die eigene Suche nach Infinity entfällt
        deck = {"30": ("Cyber Dragon Nova", 1, "Xyz Monster"),
                "31": ("Cyber Dragon Infinity", 1, "Xyz Monster"),
                "32": ("Maxx C", 1, "Effect Monster")}
        grids = {"Cyber Dragon Nova": ["lcyberdragoninfinity", "lcyberdragoninfinity", "futurefusionnova",
                                       "lcyberdragonnova"]}
        game = FakeMasterDuel(grids=grids)
        finished, log, _, _ = self.run_import(game, deck=deck)
        self.assertEqual(+game.deck, Counter({"lcyberdragoninfinity": 1, "lcyberdragonnova": 1, "maxxc": 1}))
        self.assertEqual(game.searches, ["Cyber Dragon Nova", "Maxx C"])  # keine Suche nach Infinity
        self.assertIn("[NEBENBEI SKIP] 'Cyber Dragon Infinity'", log)
        self.assertEqual((finished["has_errors"], finished["failed_cards"]), (False, []))

    def test_lagging_panel_is_detected(self):
        # Echter Fall: Nach 'Future Fusion Nova' verarbeitet das Spiel den nächsten Klick nicht,
        # das Panel zeigt beim Nova-Slot noch die alte Karte. Nachklick erkennt das.
        deck = {"30": ("Cyber Dragon Nova", 1, "Xyz Monster")}
        grids = {"Cyber Dragon Nova": ["lcyberdragoninfinity", "lcyberdragoninfinity", "futurefusionnova",
                                       "lcyberdragonnova"]}
        game = FakeMasterDuel(grids=grids, lag_after={"futurefusionnova": 1})
        finished, log, _, _ = self.run_import(game, deck=deck)
        self.assertEqual(+game.deck, Counter({"lcyberdragonnova": 1}))
        self.assertIn("[LAG] Panel zeigte noch 'futurefusionnova'", log)
        self.assertNotIn("[NACHLAUF]", log)  # schon im ersten Durchgang gefunden
        self.assertTrue(any("verzögert auf Klicks" in n for n in finished["notes"]))

    def test_lost_search_is_typed_again(self):
        # Echter Fall: Die Suche nach Ash Blossom kam nicht an, das Raster zeigte weiter Maxx C
        deck = {"5": ('Maxx "C"', 1, "Effect Monster"), "4": ("Ash Blossom & Joyous Spring", 3, "Tuner Monster")}
        ash = sanitize_name("Ash Blossom & Joyous Spring")
        game = FakeMasterDuel(lost_searches={ash: 1})
        finished, log, _, _ = self.run_import(game, deck=deck)
        self.assertEqual(+game.deck, Counter({"maxxc": 1, "ashblossomjoyousspring": 3}))
        self.assertIn("[SUCHE NEU]", log)
        self.assertNotIn("[NACHLAUF]", log)  # im ersten Durchgang gefunden
        self.assertEqual(game.searches.count(ash), 2)

    def test_abort_and_resume_without_duplicates(self):
        game = FakeMasterDuel(abort_after_adds=3)
        finished, log, _, _ = self.run_import(game)
        self.assertFalse(finished["success"])
        self.assertIn("Fortschritt gespeichert (3 Karten eingefügt)", finished["message"])

        resume = resume_state.load_progress(CARD_IDS)
        self.assertEqual(len(resume["done"]), 3)

        game.abort_after_adds = None
        trash_before = game.trash_clicks
        finished, log, _, _ = self.run_import(game, resume=resume)

        self.assertEqual(game.trash_clicks, trash_before)  # Deck beim Fortsetzen NICHT geleert
        self.assertEqual(+game.deck, +EXPECTED_DECK)      # keine Karte doppelt
        self.assertEqual((finished["success"], finished["has_errors"]), (True, False))
        self.assertIn("[FORTSETZEN] 3 Karten waren schon eingefügt", log)
        self.assertFalse(os.path.exists(self.resume_file))


if __name__ == "__main__":
    unittest.main()
