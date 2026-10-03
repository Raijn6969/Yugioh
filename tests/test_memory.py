"""Speicher-Modus: Speicher lesen und Import mit Karten-IDs statt Texterkennung."""

import os
import struct
import tempfile
import unittest
from unittest import mock

import import_engine as ie
import md_memory
from md_memory import ListScroll, SearchEntry
from tests.fakes import make_core


class FakeReader:
    """Simulierter Prozess-Speicher: {Adresse: Bytes}."""

    def __init__(self):
        self.mem = {}

    def put(self, addr, data):
        self.mem[addr] = bytes(data)

    def read(self, addr, size):
        for base, data in self.mem.items():
            if base <= addr and addr + size <= base + len(data):
                return data[addr - base:addr - base + size]
        raise OSError(f"{addr:#x}")

    def u64(self, addr):
        return struct.unpack("<Q", self.read(addr, 8))[0]

    def i32(self, addr):
        return struct.unpack("<i", self.read(addr, 4))[0]


class FakeIl2Cpp:
    """Klassen mit festen Feldern; Objekte zeigen bei +0 auf ihre Klasse."""

    FIELDS = {
        0x100: {"m_MainDeckCards": 0x10, "m_ExtraDeckCards": 0x18, "<m_CardCollection>k__BackingField": 0x20,
                "m_DetailView": 0x28, "m_DeckView": 0x30},
        0x200: {"_items": 0x10, "_size": 0x18},                       # List`1
        0x300: {"<CardID>k__BackingField": 0x10, "<PremiumID>k__BackingField": 0x14,
                "<Inventory>k__BackingField": 0x20,
                "<IsAutoBuild>k__BackingField": 0x29},                 # CardBaseData (Struct, 0x1C groß)
        0x400: {"<m_CardID>k__BackingField": 0x34, "<m_Premium>k__BackingField": 0x38,
                "m_TitleArea": 0x40},                                   # CardDetailView
        0x500: {"mainCardDataList": 0x10, "extraCardDataList": 0x18},  # DeckView (angezeigtes Deck)
        0x600: {"<m_CardName>k__BackingField": 0x10},                  # TitleArea
        0x700: {"m_text": 0x10},                                        # Text (TextMeshPro)
    }

    def __init__(self, reader):
        self.r = reader

    def fields(self, klass):
        return self.FIELDS[klass]

    def field(self, obj, name):
        return self.FIELDS[self.r.u64(obj)][name]


def card_list(reader, addr, ids):
    """List<CardBaseData> an Adresse addr anlegen: Konami-IDs oder (Konami-ID, Ausführung, Besitz)."""
    ids = [cid if isinstance(cid, tuple) else (cid, 1, 0) for cid in ids]
    items = addr + 0x100
    reader.put(addr, struct.pack("<QQQi", 0x200, 0, items, len(ids)))
    reader.put(0x300 + 0x40, struct.pack("<Q", 0x300))  # (Klasse 0x300 dient auch als Array-Klasse)
    entries = b"".join(struct.pack("<iiiiiii", cid, premium, 1, 0, owned, 4, 0)
                       for cid, premium, owned in ids)  # je 0x1C
    reader.put(items, struct.pack("<QQQQ", 0x300, 0, 0, len(ids)) + entries)


class DeckEditorMemoryTest(unittest.TestCase):
    def setUp(self):
        r = FakeReader()
        editor, detail = 0x1000, 0x2000
        # m_MainDeckCards/m_ExtraDeckCards (Deck beim Öffnen) absichtlich anders als die Deck-Ansicht
        r.put(editor, struct.pack("<QQQQQQQ", 0x100, 0, 0x7000, 0x7000, 0x5000, detail, 0x6000))
        r.put(0x6000, struct.pack("<QQQQ", 0x500, 0, 0x3000, 0x4000))
        card_list(r, 0x7000, [1, 2, 3])
        r.put(detail, struct.pack("<Q", 0x400) + bytes(0x2C) + struct.pack("<iiiQ", 12950, 3, 0, 0x8000))
        r.put(0x8000, struct.pack("<QQQ", 0x600, 0, 0x8100))       # TitleArea → Name-Text
        r.put(0x8100, struct.pack("<QQQ", 0x700, 0, 0x8200))       # Text → System.String
        name = "Ash Blossom & Joyous Spring"
        r.put(0x8200, struct.pack("<QQi", 0, 0, len(name)) + name.encode("utf-16-le"))
        card_list(r, 0x3000, [9455, 12950, 12950])
        card_list(r, 0x4000, [14958])
        card_list(r, 0x5000, [(11413, 1, 2), (11413, 3, 1), 21453])
        self.memory = md_memory.DeckEditorMemory(r, FakeIl2Cpp(r), klass=0x100)
        self.memory.editor = editor
        self.memory.alive = lambda: True

    def test_reads_shown_card_deck_and_search_results(self):
        self.assertEqual(self.memory.shown_card(), 12950)
        self.assertEqual(self.memory.deck(), ([9455, 12950, 12950], [14958]))
        self.assertEqual(self.memory.search_results(), [11413, 11413, 21453])

    def test_reads_finish_ownership_and_shown_name(self):
        # Jede Ausführung ist ein eigener Eintrag mit eigener Besitzzahl
        self.assertEqual(self.memory.search_entries(),
                         [SearchEntry(11413, 1, 2), SearchEntry(11413, 3, 1), SearchEntry(21453, 1, 0)])
        self.assertEqual(self.memory.shown_premium(), 3)
        self.assertEqual(self.memory.shown_name(), "Ash Blossom & Joyous Spring")


class KonamiAliasTest(unittest.TestCase):
    def test_learned_artwork_counts_as_the_card_next_time(self):
        from card_stats import CardStatsDB
        db = CardStatsDB(os.path.join(tempfile.mkdtemp(), "stats.db"), fetch=mock.Mock(), base_path=None)
        with db._connect() as con:
            con.execute("INSERT INTO konami VALUES ('24094653', 4837, 'Polymerization')")
        db.learn_konami_alias(23490, "24094653")
        self.assertEqual(db.konami_passcodes()[23490], "24094653")
        self.assertEqual(db.konami_names()[23490], "Polymerization")


class MemoryImportTest(unittest.TestCase):
    def make_core(self, shown):
        core = make_core()
        core.memory = mock.Mock(shown_card=mock.Mock(side_effect=shown))
        core._kid_names = {11413: "Number F0: Utopic Future", 21453: "Number F0: Utopic Future Zexal"}
        return core

    def test_full_name_from_memory_instead_of_ocr(self):
        core = self.make_core([21453])
        raw, s_c = core._capture_and_ocr_slot(None, {})  # sct=None: kein Bildschirmfoto nötig
        self.assertEqual((raw, s_c), ("Number F0: Utopic Future Zexal", "numberf0utopicfuturezexal"))

    def test_only_exact_names_match(self):
        # Genau der Fall aus dem echten Import: Zexal darf nicht als "Utopic Future" eingefügt werden
        core = self.make_core([21453])
        self.assertEqual(core._check_match_with_overrule("Number F0: Utopic Future", "numberf0utopicfuturezexal"),
                         (False, "NONE"))
        self.assertEqual(core._check_match_with_overrule("Number F0: Utopic Future", "numberf0utopicfuture"),
                         (True, "EXACT"))
        self.assertEqual(core._plausible_candidates(["Number F0: Utopic Future"], "numberf0utopicfuturezexal", 0.5),
                         [])

    def test_falls_back_to_ocr_when_memory_fails(self):
        core = self.make_core(OSError("Spiel beendet"))
        memory = core.memory
        sct = mock.Mock()
        sct.grab.side_effect = RuntimeError("Texterkennung")  # zeigt: es wird auf den Bildschirm umgeschaltet
        with self.assertRaises(RuntimeError):
            core._capture_and_ocr_slot(sct, {})
        self.assertIsNone(core.memory)
        memory.close.assert_not_called()  # gemeinsame Verbindung: shared() prüft sie beim nächsten Mal selbst
        self.assertTrue(any("ausgefallen" in n for n in core.notes))

    def test_unknown_artwork_is_named_from_the_panel(self):
        core = self.make_core([23490])
        core.memory.shown_name.return_value = "Polymerization"
        core._name_passcodes = {"polymerization": "24094653"}
        self.assertEqual(core._capture_and_ocr_slot(None, {}), ("Polymerization", "polymerization"))
        self.assertEqual(core._kid_passcodes[23490], "24094653")

    def test_setup_failure_keeps_normal_import(self):
        core = make_core()
        with mock.patch.object(md_memory.DeckEditorMemory, "attach",
                               side_effect=md_memory.MemoryUnavailable("Deck-Editor nicht gefunden")), \
                mock.patch("card_stats.CardStatsDB") as db:
            db.return_value.konami_ids.return_value = {}
            core._setup_memory([], {})
        self.assertIsNone(core.memory)
        self.assertTrue(any("Speicher-Modus nicht möglich" in n for n in core.notes))


class FakeGame:
    """
    Master Duel im Speicher-Modus: Tippen → (erst beim Abschicken) Ergebnisliste, Klick → Detail-Panel,
    Einfügen → Deck. Wie im echten Spiel wird eine getippte Suche erst mit Enter oder einem Klick angewendet.
    Ergebnisse: Konami-IDs oder SearchEntry (Ausführung, Besitz).
    """
    current = None  # für das gepatchte submit_search
    # Namen, die das Detail-Panel anzeigt (Karten, die nicht im Deck sind)
    NAMES = {14958: "Number 39: Utopia", 21453: "Number F0: Utopic Future Zexal", 16472: "Number 39: Utopia Double",
             4007: "Blue-Eyes White Dragon", 7445: "Super Polymerization"}

    def __init__(self, core, searches, lose_first_copy=False, enter_works=True):
        self.core, self.searches, self.lose_first_copy = core, searches, lose_first_copy
        self.enter_works, self.pending, self.enters = enter_works, None, 0
        self.results, self.shown, self.deck, self.typed, self.clicked = [], (0, 1), [], [], []
        self.added = []  # (Konami-ID, Ausführung, Kopien) je Einfügen
        self.names = dict(self.NAMES)
        self.position, self.notches = 0.0, 0  # Scroll-Stand der Kartenliste (wie im Spiel: 143 je Reihe)
        # Scroll-Leiste: wo sie wirklich liegt (Test-Raster: FIRST_CARD 100/100, Abstand 88/140)
        self.bar_x, self.bar_top, self.bar_clicks = 100 + round(ie.SCROLLBAR_X * 88), 100 + ie.SCROLLBAR_TOP * 140, 0
        self.list_address = 0x1000  # das Spiel tauscht nach jeder Suche die Ergebnisliste
        core._type_search_term = lambda automator, term: self.search(term)
        FakeGame.current = self

    # md_memory.DeckEditorMemory
    def search_entries(self):
        return list(self.results)

    def search_results(self):
        return [entry.kid for entry in self.results]

    def shown_card(self):
        return self.shown[0]

    def list_scroll(self):
        rows = -(-len(self.results) // 6)
        return ListScroll(self.position, max(0.0, rows * 143.0 - 684.0), 143.0, 6, 100.0, 684.0)

    def scroll(self, x, y, notches):
        """Mausrad: negativ = nach unten, eine Raste = 100 (das Spiel bleibt am Listenende stehen)."""
        self.notches += notches
        self.position = min(max(0.0, self.position - notches * 100.0), self.list_scroll().maximum)

    def shown_premium(self):
        return self.shown[1]

    def shown_name(self):
        return self.names.get(self.shown[0], "")

    def deck_lists(self):
        return list(self.deck), []

    # Spiel
    def search(self, term):
        self.typed.append(term)
        self.pending = term

    def submit(self):
        self.enters += 1
        if self.enter_works:
            self._apply()

    def _apply(self):
        if self.pending is not None:
            self.results = [e if isinstance(e, SearchEntry) else SearchEntry(e, 1, 0)
                            for e in self.searches.get(self.pending, [])]
            self.list_address += 0x100
            self.pending = None
            self.position = 0.0  # neue Ergebnisse: Liste wieder oben

    def click(self, x, y, button="left"):
        scroll = self.list_scroll()
        if abs(x - self.bar_x) <= 4 and scroll.maximum:
            # Klick auf die Scroll-Leiste: Griff springt mit der Mitte dorthin
            length = ie.SCROLLBAR_LENGTH * 140
            handle = length * 684.0 / (scroll.maximum + 684.0)
            f = (y - self.bar_top - handle / 2) / (length - handle)
            self.position = min(max(0.0, f * scroll.maximum), scroll.maximum)
            self.bar_clicks += 1
            return
        self._apply()  # Klick in die Kartenliste schickt eine getippte Suche ab
        scroll = self.list_scroll()
        for slot, entry in enumerate(self.results):
            if self.core._memory_point(self.automator, slot, scroll) == (x, y):
                self.shown = (entry.kid, entry.premium)
                self.clicked.append(slot)

    def add(self, x, y, amount):
        if self.lose_first_copy:  # ein Klick geht verloren (Spiel ruckelt)
            self.lose_first_copy, amount = False, amount - 1
        self.deck += [self.shown[0]] * amount
        self.added.append((*self.shown, amount))
        return False


def memory_core(searches, deck_cards, lose_first_copy=False):
    core = make_core("fast")
    core.config.update({"FIRST_CARD": [100, 100], "OFFSET_X": 88, "OFFSET_Y": 140})
    game = FakeGame(core, searches, lose_first_copy)
    game.automator = mock.Mock(scale_x=1.0, scale_y=1.0, origin=(0, 0), iron_grip_click=game.click,
                               add_card_to_deck=game.add, scroll=game.scroll)
    core.memory = mock.Mock(search_results=game.search_results, search_entries=game.search_entries,
                            shown_card=game.shown_card, shown_premium=game.shown_premium,
                            shown_name=game.shown_name, deck=game.deck_lists, grid_count=lambda: len(game.results),
                            list_scroll=game.list_scroll,
                            search_list_address=lambda: game.list_address)
    core._deck_kids = {c.cid: kid for c, kid in deck_cards}
    core._kid_names = dict(FakeGame.NAMES)
    core._kid_names.update({kid: c.name for c, kid in deck_cards})
    core._kid_passcodes = {kid: c.cid for c, kid in deck_cards}
    core._name_passcodes = {ie.clean_text(c.name): c.cid for c, _ in deck_cards}
    core._stats_db = mock.Mock()
    core._open_pool = [c for c, _ in deck_cards]
    return core, game


class DirectJumpTest(unittest.TestCase):
    def setUp(self):
        # Nie echte Tasten drücken: Enter geht an das simulierte Spiel
        patcher = mock.patch.object(ie, "submit_search", lambda: FakeGame.current.submit())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.utopic = ie.DeckCard("65305468", "Number F0: Utopic Future", 1)
        self.ash = ie.DeckCard("14558127", "Ash Blossom & Joyous Spring", 3)

    def test_card_is_clicked_directly_at_its_position(self):
        core, game = memory_core({"Number F0: Utopic Future": [14958, 21453, 11413, 16472]},
                                 [(self.utopic, 11413)])
        added = []
        self.assertTrue(core._memory_search_card(game.automator, self.utopic, added))
        self.assertEqual(game.clicked[0], 2)       # sofort Platz 2, nicht Platz 0, 1, … durchprobiert
        self.assertNotIn(21453, game.deck)         # Zexal (Platz 1) nie eingefügt
        self.assertEqual(game.deck, [11413])
        self.assertEqual(core._progress, {"65305468": 1})

    def test_lost_copy_is_added_again(self):
        core, game = memory_core({"Ash Blossom & Joyous Spring": [12950]}, [(self.ash, 12950)],
                                 lose_first_copy=True)
        self.assertTrue(core._memory_search_card(game.automator, self.ash, []))
        self.assertEqual(game.deck, [12950] * 3)   # 2 angekommen → 1 nachgeklickt

    def test_one_group_search_inserts_all_visible_deck_cards(self):
        maxx = ie.DeckCard("23434538", 'Maxx "C"', 1)
        core, game = memory_core({"utopic": [14958, 11413, 9455, 21453]},
                                 [(self.utopic, 11413), (maxx, 9455)])
        done = core._memory_batch(game.automator, "utopic", [])
        self.assertEqual(done, {"65305468", "23434538"})
        self.assertEqual(game.typed, ["utopic"])   # nur eine Suche für beide Karten
        self.assertEqual(sorted(game.deck), [9455, 11413])

    def test_stale_grid_is_read_again_before_giving_up(self):
        # Erster Klick trifft noch das alte Raster (Panel zeigt eine andere Karte) → Position neu lesen
        core, game = memory_core({"Number F0: Utopic Future": [11413]}, [(self.utopic, 11413)])
        click = game.click
        stale = {"left": True}

        def first_click_misses(x, y, button="left"):
            if stale.pop("left", False):
                game.shown = (4007, 1)  # Blue-Eyes aus der vorigen Anzeige
                return
            click(x, y, button)

        game.automator.iron_grip_click = first_click_misses
        self.assertTrue(core._memory_search_card(game.automator, self.utopic, []))
        self.assertEqual(game.deck, [11413])
        self.assertEqual(game.typed, ["Number F0: Utopic Future"])  # nicht noch einmal gesucht

    def test_search_is_submitted_with_enter(self):
        core, game = memory_core({"Number F0: Utopic Future": [11413]}, [(self.utopic, 11413)])
        self.assertTrue(core._memory_search_card(game.automator, self.utopic, []))
        self.assertEqual(game.enters, 1)
        self.assertEqual(game.clicked, [0])  # nur die Karte selbst, kein zusätzlicher Klick

    def test_click_submits_when_enter_does_not(self):
        core, game = memory_core({"Number F0: Utopic Future": [14958, 11413]}, [(self.utopic, 11413)])
        game.enter_works = False
        self.assertTrue(core._memory_search_card(game.automator, self.utopic, []))
        self.assertEqual(game.clicked, [0, 1])  # Klick auf Platz 0 schickt ab, dann direkt Platz 1
        self.assertEqual(game.deck, [11413])

    def test_card_further_down_is_scrolled_to(self):
        # Echter Fall: "Cyber Dragon" lag auf Platz 36, knapp unter dem sichtbaren Raster
        core, game = memory_core({"Number F0: Utopic Future": [1] * 60 + [11413]}, [(self.utopic, 11413)])
        self.assertTrue(core._memory_search_card(game.automator, self.utopic, []))
        self.assertGreater(game.position, 0)      # nach unten gescrollt (per Scroll-Leiste)
        self.assertEqual(game.clicked, [60])      # direkt die Karte, kein Platz dazwischen
        self.assertEqual(game.deck, [11413])

    def test_far_down_in_a_huge_result_list(self):
        # Echter Fall: "One for One" findet 2391 Karten, die echte liegt auf Platz 276 (Reihe 46)
        results = [1] * 276 + [8197] + [1] * 2114
        one = ie.DeckCard("2295440", "One for One", 1)
        core, game = memory_core({"One for One": results}, [(one, 8197)])
        self.assertTrue(core._memory_search_card(game.automator, one, []))
        self.assertEqual(game.clicked, [276])
        self.assertEqual(game.deck, [8197])
        self.assertLessEqual(game.bar_clicks, 2)       # Klick auf die Scroll-Leiste springt sofort hin
        self.assertLessEqual(abs(game.notches), 2)     # höchstens ein, zwei Rasten nachjustieren
        self.assertIs(core._scrollbar_ok, True)

    def test_short_distance_clicks_next_to_the_handle(self):
        # Echter Fall: D/D-Sammelsuche, 115 Ergebnisse, Liste bei 1864, Platz 113 → 3 Rasten (0,8 s Gleiten).
        # Der Klick für das Ziel läge auf dem Griff selbst → knapp darunter klicken statt Mausrad
        dark = ie.DeckCard("1", "Dark Contract with the Eternal Darkness", 1)
        core, game = memory_core({"d/d": [1] * 113 + [12982, 1]}, [(dark, 12982)])
        core.config["FIRST_CARD"] = [1370, 379]  # dein Raster (1920×1080): Platz 113 liegt unter dem Bildrand
        game.bar_x, game.bar_top = 1370 + round(ie.SCROLLBAR_X * 88), 379 + ie.SCROLLBAR_TOP * 140
        game.search("d/d")
        game.submit()
        game.position = 1864.0
        self.assertIsNotNone(core._memory_scroll_to(game.automator, 113))
        self.assertEqual((game.bar_clicks, game.notches), (1, 0))

    def test_stale_position_after_a_short_search(self):
        # Echter Fall: Transaction Rollback (1 Ergebnis) nach einer gescrollten Sammelsuche – im Speicher stand
        # noch Position 315, die Liste ist aber gar nicht scrollbar → "division by zero"
        rollback = ie.DeckCard("6351147", "Transaction Rollback", 3)
        core, game = memory_core({"transaction rollback": [19049]}, [(rollback, 19049)])
        game.search("transaction rollback")
        game.submit()
        game.position = 315.0
        self.assertIsNotNone(core._memory_scroll_to(game.automator, 0))
        self.assertEqual((game.bar_clicks, game.notches), (0, 0))

    def test_unexpected_error_falls_back_but_mouse_abort_stops(self):
        # Ein Fehler im Speicher-Modus darf nicht den ganzen Import abbrechen – eine bewegte Maus aber schon
        rollback = ie.DeckCard("6351147", "Transaction Rollback", 3)
        core, game = memory_core({"transaction rollback": [19049]}, [(rollback, 19049)])
        with mock.patch.object(core, "_memory_plan", side_effect=ZeroDivisionError("division by zero")):
            self.assertIsNone(core._memory_search_card(game.automator, rollback, []))
        with mock.patch.object(core, "_memory_plan", side_effect=ie.UserInterrupt("Maus bewegt")):
            with self.assertRaises(ie.UserInterrupt):
                core._memory_search_card(game.automator, rollback, [])
        self.assertIsNotNone(core.memory)

    def test_wheel_when_the_scrollbar_is_missed(self):
        results = [1] * 276 + [8197] + [1] * 2114
        one = ie.DeckCard("2295440", "One for One", 1)
        core, game = memory_core({"One for One": results}, [(one, 8197)])
        game.bar_x = 9999  # Leiste liegt woanders: Klick bewegt nichts
        self.assertTrue(core._memory_search_card(game.automator, one, []))
        self.assertEqual(game.clicked, [276])
        self.assertIs(core._scrollbar_ok, False)       # gemerkt: ab jetzt nur Mausrad
        self.assertLessEqual(abs(game.notches), 70)

    def test_scrollbar_is_measured_more_exactly_on_a_second_click(self):
        # Leiste 15 px tiefer als angenommen: erster Klick daneben, zweiter korrigiert
        results = [1] * 276 + [8197] + [1] * 2114
        one = ie.DeckCard("2295440", "One for One", 1)
        core, game = memory_core({"One for One": results}, [(one, 8197)])
        game.bar_top += 15
        self.assertTrue(core._memory_search_card(game.automator, one, []))
        self.assertEqual(game.clicked, [276])
        self.assertLessEqual(abs(game.notches), 3)

    def test_group_search_scrolls_to_cards_further_down(self):
        maxx = ie.DeckCard("23434538", 'Maxx "C"', 1)
        core, game = memory_core({"utopic": [11413] + [14958] * 45 + [9455]}, [(self.utopic, 11413), (maxx, 9455)])
        done = core._memory_batch(game.automator, "utopic", [])
        self.assertEqual(done, {"65305468", "23434538"})
        self.assertEqual(game.typed, ["utopic"])  # keine zweite Suche für Maxx "C"
        self.assertEqual(game.clicked, [0, 46])

    def test_screen_position_follows_the_scroll_state(self):
        core, game = memory_core({}, [])
        scroll = ListScroll(6251.8, 56371.0, 143.0, 6, 100.0)  # gemessen: oberste Reihe ≈ 43,7
        self.assertIsNone(core._memory_point(game.automator, 43 * 6, scroll))      # oben angeschnitten
        self.assertEqual(core._memory_point(game.automator, 276, scroll), (100, 100 + round((46 * 143 - 6251.8) / 143 * 140)))
        self.assertEqual(core._memory_point(game.automator, 7, ListScroll(0, 0, 143.0, 6, 100.0)), (188, 240))


class FinishAndArtworkTest(unittest.TestCase):
    """Royal vor Shiny vor normal, besessene Alt-Arts vor dem Original – je nur so oft wie im Besitz."""

    def setUp(self):
        patcher = mock.patch.object(ie, "submit_search", lambda: FakeGame.current.submit())
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ash = ie.DeckCard("14558127", "Ash Blossom & Joyous Spring", 3)
        self.poly = ie.DeckCard("24094653", "Polymerization", 2)

    def test_owned_alt_art_beats_shiny_original(self):
        # Echter Fall: Shiny-Original (3 im Besitz) wurde statt des besessenen Alt-Arts genommen
        poly = ie.DeckCard("24094653", "Polymerization", 1)
        core, game = memory_core({"Polymerization": [SearchEntry(23490, 1, 1), SearchEntry(4837, 2, 3)]},
                                 [(poly, 4837)])
        game.names[23490] = "Polymerization"
        self.assertTrue(core._memory_search_card(game.automator, poly, []))
        self.assertEqual(game.added, [(23490, 1, 1)])

    def test_royal_then_shiny_then_normal(self):
        core, game = memory_core({self.ash.name: [SearchEntry(12950, 1, 3), SearchEntry(12950, 2, 1),
                                                  SearchEntry(12950, 3, 1)]}, [(self.ash, 12950)])
        self.assertTrue(core._memory_search_card(game.automator, self.ash, []))
        self.assertEqual(game.added, [(12950, 3, 1), (12950, 2, 1), (12950, 1, 1)])
        self.assertEqual(core._progress, {"14558127": 3})

    def test_missing_copies_come_as_normal_finish(self):
        core, game = memory_core({self.ash.name: [SearchEntry(12950, 1, 0), SearchEntry(12950, 3, 1)]},
                                 [(self.ash, 12950)])
        self.assertTrue(core._memory_search_card(game.automator, self.ash, []))
        self.assertEqual(game.added, [(12950, 3, 1), (12950, 1, 2)])

    def test_owned_alt_art_is_preferred_and_learned(self):
        core, game = memory_core({self.ash.name: [SearchEntry(12950, 1, 3), SearchEntry(99999, 1, 1)]},
                                 [(self.ash, 12950)])
        game.names[99999] = "Ash Blossom & Joyous Spring"
        self.assertTrue(core._memory_search_card(game.automator, self.ash, []))
        self.assertEqual(game.added, [(99999, 1, 1), (12950, 1, 2)])
        core._stats_db.learn_konami_alias.assert_called_once_with(99999, "14558127")

    def test_unowned_alt_art_is_not_clicked_when_the_original_is_visible(self):
        core, game = memory_core({self.ash.name: [SearchEntry(12950, 1, 3), SearchEntry(99999, 1, 0)]},
                                 [(self.ash, 12950)])
        game.names[99999] = "Ash Blossom & Joyous Spring"
        self.assertTrue(core._memory_search_card(game.automator, self.ash, []))
        self.assertEqual(game.clicked, [0])
        self.assertEqual(game.added, [(12950, 1, 3)])

    def test_only_unknown_alt_arts_are_all_named(self):
        core, game = memory_core({"Polymerization": [7445, SearchEntry(20386, 1, 0), SearchEntry(23490, 1, 0)]},
                                 [(self.poly, 4837)])
        game.names.update({20386: "Polymerization", 23490: "Polymerization"})
        self.assertTrue(core._memory_search_card(game.automator, self.poly, []))
        self.assertEqual(core._kids_of("24094653"), [4837, 20386, 23490])
        self.assertEqual(game.added, [(20386, 1, 2)])  # keins im Besitz: das erste Alt-Art

    def test_polymerization_alt_arts_on_the_first_page(self):
        # Echter Fall: Original (4837) erst auf Platz 96, auf Seite 1 zwei Alt-Arts, die YGOPRODeck nicht kennt
        results = ([7445] * 14 + [SearchEntry(20386, 1, 0), SearchEntry(23490, 1, 1)] + [7445] * 80
                   + [SearchEntry(4837, 1, 2)])
        core, game = memory_core({"Polymerization": results}, [(self.poly, 4837)])
        game.names.update({20386: "Polymerization", 23490: "Polymerization"})
        self.assertTrue(core._memory_search_card(game.automator, self.poly, []))
        # Erst das besessene Alt-Art, dann das besessene Original (Platz 96, wird hingescrollt) – beide im Besitz
        self.assertEqual(game.added, [(23490, 1, 1), (4837, 1, 1)])
        # Nur das besessene Alt-Art wird angeklickt und gelernt (das Original steht ja in den Ergebnissen)
        core._stats_db.learn_konami_alias.assert_called_once_with(23490, "24094653")
        self.assertEqual(core._kids_of("24094653"), [4837, 23490])


class SearchTermTest(unittest.TestCase):
    def test_names_with_angle_brackets(self):
        from utils import search_text
        # Echter Fall: "Maliss <P> Chessy Cat" findet die Suche von Master Duel nicht
        self.assertEqual(search_text("Maliss <P> Chessy Cat"), "Chessy Cat")
        self.assertEqual(search_text("Ash Blossom & Joyous Spring"), "Ash Blossom & Joyous Spring")

    def test_maliss_is_one_group(self):
        core = make_core()
        deck = [("1", "Maliss <P> Chessy Cat", 1), ("2", "Maliss <P> Dormouse", 1), ("3", "Maliss <P> March Hare", 3),
                ("4", "Maliss in Underground", 1), ("5", "Exosister Martha", 1), ("6", "Exosisters Magnifica", 1),
                ("7", "Exosister Elis", 1)]
        groups = core._compute_batch_groups(deck)
        # "maliss" (nicht "malis", nicht "maliss p"); bei Plural die kürzere Form, die in beiden steckt
        self.assertEqual({k: len(v) for k, v in groups.items()}, {"maliss": 4, "exosister": 3})

    def test_typed_text_has_no_angle_brackets(self):
        core = make_core()
        with mock.patch.object(ie, "type_card_name") as typed:
            core._begin_search(mock.Mock(), "Maliss <P> Dormouse")
        self.assertEqual(typed.call_args.args[1], "Dormouse")
        self.assertEqual(core._search_term, "dormouse")


if __name__ == "__main__":
    unittest.main()
