"""Extras-Menü: Ziehchancen, Starter-Einstufung, lokale Karten-Datenbank, Deck-Raster, Deck-Überwachung, UI."""

import os
import random
import tempfile
import time
import unittest
from unittest import mock

from PIL import Image, ImageDraw

import deck_analysis
import deck_export
import md_layout
from card_db import CardMatch
from card_stats import CardInfo, CardStatsDB, HandtrapInfo, StarterInfo
from draw_odds import p_at_least, p_exactly
from handtrap_rules import classify_handtrap
from starter_rules import classify

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAS_TK = True
except Exception:
    HAS_TK = False

BACKGROUND = (18, 30, 48)
FRAME = md_layout.Frame(0, 0, 1920, 1080)

# Kartentexte (gekürzt) von YGOPRODeck
TEXTS = {
    "lukias": ("Effect Monster", "effect", 4, "Spellcaster",
               'If this card is Normal or Special Summoned: You can add 1 "Dracotail" monster from your Deck to '
               'your hand, except "Dracotail Lukias". If this card is sent to the GY as material for a Fusion '
               'Summon: You can Set 1 "Dracotail" Spell/Trap from your Deck.'),
    "ash": ("Tuner Monster", "effect", 3, "Zombie",
            "When a card or effect is activated that includes any of these effects (Quick Effect): You can discard "
            "this card; negate that effect.\n● Add a card from the Deck to the hand.\n● Special Summon from the Deck."),
    "diabellstar": ("Effect Monster", "effect", 7, "Spellcaster",
                    "You can Special Summon this card (from your hand) by sending 1 card from your hand or field to "
                    "the GY. If this card is Normal or Special Summoned: You can Set 1 \"Sinful Spoils\" Spell/Trap "
                    "directly from your Deck."),
    "spright_blue": ("Effect Monster", "effect", 2, "Thunder",
                     "If you control a Level/Rank 2 monster, you can Special Summon this card (from your hand). If "
                     "this card is Special Summoned: You can add 1 \"Spright\" monster from your Deck to your hand."),
    "terraforming": ("Spell Card", "spell", None, "Normal", "Add 1 Field Spell from your Deck to your hand."),
    "flame": ("Trap Card", "trap", None, "Normal", "Target 1 face-up Spell on the field; negate its effects."),
    "faimena": ("Effect Monster", "effect", 5, "Spellcaster",
                "During the Main Phase (Quick Effect): You can discard this card; Fusion Summon 1 Dragon or "
                "Spellcaster Fusion Monster from your Extra Deck, using monsters from your hand or field."),
    "pan": ("Effect Monster", "effect", 7, "Dragon",
            "If this card is sent to the GY as material for a Fusion Summon: You can Set 1 \"Dracotail\" "
            "Spell/Trap from your Deck, then you can destroy 1 monster on the field."),
    "arthalion": ("Fusion Monster", "fusion", 8, "Dragon", "2 \"Dracotail\" monsters"),
    "saji": ("Effect Monster", "effect", 4, "Reptile",
             "If this card is Normal or Special Summoned, or if this card is Tributed: You can add 1 \"Mitsurugi\" "
             "Spell/Trap from your Deck to your hand."),
    "fortuna": ("Effect Monster", "effect", 6, "Fairy",
                "You can Special Summon this card (from your hand) to your center Main Monster Zone. During your Main "
                "Phase: You can place 1 \"Elfnote\" Continuous Trap from your hand or Deck, face-up on your field."),
    "medius": ("Effect Monster", "effect", 4, "Fairy",
               "If this card is Normal or Special Summoned: You can add to your hand, or Special Summon, 1 \"Power "
               "Patron\" monster from your Deck."),
    "noroi": ("Effect Monster", "effect", 5, "Machine",
              "If your opponent has 2 or more cards in their hand, you can Normal Summon this card without Tributing. "
              "If this card is Normal Summoned: You can Special Summon 1 non-Machine \"K9\" monster from your Deck."),
    "raye": ("Effect Monster", "effect", 4, "Machine",
             "(Quick Effect): You can Tribute this card; Special Summon 1 \"Sky Striker Ace\" monster from your Extra "
             "Deck to the Extra Monster Zone."),
    "gandora": ("Effect Monster", "effect", 8, "Dragon",
                "If you control \"Shining Sarcophagus\": You can Special Summon this card from your hand. You can pay "
                "half your LP; destroy as many other cards on the field as possible, then Special Summon 1 Level 7 or "
                "lower monster that mentions \"Shining Sarcophagus\" from your Deck."),
    "jj": ("Spell Card", "spell", None, "Continuous",
           "You can Tribute 1 Tuner; add to your hand or Special Summon, 1 \"Kewl Tune\" monster from your Deck."),
    "mitsurugi_ritual": ("Spell Card", "spell", None, "Ritual",
                         "Activate 1 of these effects;\n● Ritual Summon 1 Reptile Ritual Monster from your Deck, by "
                         "Tributing Reptile monsters from your hand or field whose total Levels equal the Level of the "
                         "Ritual Monster.\n● Ritual Summon 1 Reptile Ritual Monster from your hand, by Tributing up to 2 "
                         "Reptile monsters from your hand, Deck, or field, whose total Levels equal the Level of the "
                         "Ritual Monster."),
    "branded_fusion": ("Spell Card", "spell", None, "Normal",
                       "Fusion Summon 1 Fusion Monster that mentions \"Fallen of Albaz\" as material from your Extra "
                       "Deck, using 2 monsters from your hand, Deck, or field as material."),
    "murakumo": ("Ritual Effect Monster", "effect", 8, "Reptile",
                 "If this card is Tributed: You can add 1 \"Mitsurugi\" card from your Deck to your hand, except "
                 "\"Ame no Murakumo no Mitsurugi\", then you can Special Summon this card."),
}


def guess(key):
    card_type, frame, level, race, desc = TEXTS[key]
    return classify(card_type, frame, desc, level, race)


class DrawOddsTest(unittest.TestCase):
    def test_known_values(self):
        self.assertAlmostEqual(p_at_least(40, 3, 5), 0.3376, places=4)
        self.assertAlmostEqual(p_at_least(40, 3, 6), 0.3943, places=4)
        self.assertAlmostEqual(p_at_least(40, 1, 5), 5 / 40)

    def test_probabilities_sum_to_one(self):
        self.assertAlmostEqual(sum(p_exactly(40, 3, 5, k) for k in range(4)), 1.0)

    def test_edge_cases(self):
        self.assertEqual(p_at_least(40, 0, 5), 0.0)
        self.assertEqual(p_at_least(5, 5, 5), 1.0)
        self.assertEqual(p_at_least(0, 0, 5), 0.0)
        self.assertEqual(p_at_least(40, 3, 5, 4), 0.0)


class StarterRulesTest(unittest.TestCase):
    def test_starters(self):
        for key in ("lukias", "diabellstar", "terraforming", "faimena", "saji", "fortuna", "medius", "noroi", "raye",
                    "branded_fusion"):
            with self.subTest(key):
                self.assertTrue(guess(key).starter)

    def test_not_starters(self):
        # Mitsurugi Ritual: Tribute nur aus Hand/Feld bzw. Ritualmonster muss auf der Hand sein → kein 1-Karten-Starter
        for key in ("ash", "spright_blue", "flame", "pan", "murakumo", "gandora", "jj", "mitsurugi_ritual"):
            with self.subTest(key):
                self.assertFalse(guess(key).starter)

    def test_extra_deck_is_not_drawn(self):
        self.assertIsNone(guess("arthalion").starter)

    def test_reason_is_given(self):
        self.assertEqual(guess("lukias").reason, 'sucht 1 Karte aus dem Deck ("Dracotail" monster)')
        self.assertEqual(guess("raye").reason, 'beschwört per Effekt aus dem Extra Deck ("Sky Striker Ace" monster)')
        self.assertIn("Falle", guess("flame").reason)

    def test_counts_every_searched_card(self):
        desc = ("Add 1 Ritual Spell from your Deck to your hand, and add 1 Ritual Monster from your Deck or GY to "
                "your hand whose name is listed on that Ritual Spell.")
        self.assertEqual(classify("Spell Card", "spell", desc, None, "Normal").reason,
                         "sucht 2 Karten aus dem Deck (Ritual Spell + Ritual Monster)")
        desc = 'Special Summon up to 2 "Clown Crew" monsters from your Deck and/or Extra Deck.'
        self.assertEqual(classify("Spell Card", "spell", desc, None, "Normal").reason,
                         'beschwört 2 Monster aus dem Deck ("Clown Crew" monsters)')


# Handtraps (Kartentexte gekürzt von YGOPRODeck): (Typ, Frame, Text)
HANDTRAPS = {
    "Maxx \"C\"": ("Effect Monster", "effect",
                   "(Quick Effect): You can send this card from your hand to the GY; this turn, each time your "
                   "opponent Special Summons a monster(s), immediately draw 1 card."),
    "Effect Veiler": ("Tuner Monster", "effect",
                      "During your opponent's Main Phase (Quick Effect): You can send this card from your hand to the "
                      "GY, then target 1 Effect Monster your opponent controls; negate the effects of that monster."),
    "Ghost Mourner & Moonlit Chill": ("Tuner Monster", "effect",
                                      "If your opponent Special Summons a monster(s) face-up (except during the Damage "
                                      "Step): You can discard this card, then target 1 of those face-up monsters; "
                                      "negate its effects until the end of this turn."),
    "Infinite Impermanence": ("Trap Card", "trap",
                              "Target 1 face-up monster your opponent controls; negate its effects. If you control no "
                              "cards, you can activate this card from your hand."),
    "Bystial Magnamhut": ("Effect Monster", "effect",
                          "You can target 1 LIGHT or DARK monster in either GY; banish it, and if you do, Special "
                          "Summon this card from your hand. This is a Quick Effect if your opponent controls a "
                          "monster."),
}
NO_HANDTRAPS = {
    "Kuriboh": ("Effect Monster", "effect",
                "During damage calculation, if your opponent's monster attacks (Quick Effect): You can discard this "
                "card; you take no battle damage from that battle."),
    "Tenyi Spirit - Mapura": ("Effect Monster", "effect",
                              "When your opponent activates a card or effect that targets a face-up non-Effect "
                              "Monster(s) you control (Quick Effect): You can banish this card from your hand or GY; "
                              "negate the activation."),
    "Contact \"C\"": ("Effect Monster", "effect",
                      "When your opponent Normal or Special Summons a monster(s): You can Special Summon this card "
                      "from your hand to the opponent's field in Defense Position."),
    "Mystical Space Typhoon": ("Spell Card", "spell", "Target 1 Spell/Trap on the field; destroy that target."),
    "Solemn Judgment": ("Trap Card", "trap", "When a monster(s) would be Summoned: Pay half your LP; negate it."),
}


class HandtrapRulesTest(unittest.TestCase):
    def test_handtraps(self):
        for name, (card_type, frame, desc) in HANDTRAPS.items():
            with self.subTest(name):
                self.assertTrue(classify_handtrap(card_type, frame, desc, name).handtrap)

    def test_no_handtraps(self):
        # Kampf (Kuriboh), schützt nur eigene Karten (Tenyi), beschwört sich zum Gegner (Contact "C"), Zauber,
        # normal gesetzte Falle
        for name, (card_type, frame, desc) in NO_HANDTRAPS.items():
            with self.subTest(name):
                self.assertFalse(classify_handtrap(card_type, frame, desc, name).handtrap)
        self.assertIsNone(guess_handtrap("arthalion").handtrap)
        self.assertFalse(guess_handtrap("lukias").handtrap)

    def test_reason(self):
        self.assertEqual(guess_handtrap("ash").reason, "im Zug des Gegners aus der Hand (abwerfen) – negiert")
        self.assertEqual(classify_handtrap(*HANDTRAPS["Infinite Impermanence"]).reason,
                         "Falle, aus der Hand aktivierbar")


def guess_handtrap(key):
    card_type, frame, _, _, desc = TEXTS[key]
    return classify_handtrap(card_type, frame, desc)


def info(cid, key):
    card_type, frame, level, race, desc = TEXTS[key]
    return CardInfo(cid, key, card_type, frame, race, level, "", desc)


class CardStatsDbTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "stats.db")
        self.fetched = []

    def make_db(self, offline=False):
        def fetch(ids):
            if offline:
                raise ConnectionError("offline")
            self.fetched.append(list(ids))
            return {cid: info(cid, key) for cid, key in (("1", "lukias"), ("2", "ash")) if cid in ids}
        return CardStatsDB(self.path, fetch=fetch, base_path=None)

    def test_looks_up_once_and_works_offline_afterwards(self):
        self.assertEqual(self.make_db().ensure(["1", "2", "99"]), 1)  # 99 gibt es nicht
        self.assertEqual(self.fetched, [["1", "2", "99"]])
        offline = self.make_db(offline=True)
        self.assertEqual(offline.ensure(["1", "2"]), 0)  # alles lokal → keine Anfrage
        self.assertTrue(offline.starter("1").starter)
        self.assertFalse(offline.starter("2").starter)
        self.assertEqual(offline.info("1").level, 4)

    def test_offline_without_data_is_unknown(self):
        db = self.make_db(offline=True)
        self.assertEqual(db.ensure(["1"]), 1)
        self.assertIsNone(db.starter("1").starter)

    def test_bundled_base_database_works_offline(self):
        import sqlite3
        from card_stats import create_tables
        base = os.path.join(tempfile.mkdtemp(), "base.db")
        con = sqlite3.connect(base)
        create_tables(con)
        con.execute("INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (*info("1", "lukias"), 0))
        con.commit()
        con.close()
        db = CardStatsDB(self.path, fetch=mock.Mock(side_effect=ConnectionError("offline")), base_path=base)
        self.assertEqual(db.ensure(["1"]), 0)     # aus der mitgelieferten Datenbank, keine Anfrage
        self.assertTrue(db.starter("1").starter)
        db.set_starter("1", False)                 # eigene Korrektur landet in der eigenen Datei
        self.assertEqual(db.starter("1"), (False, "von dir festgelegt", True))
        self.assertEqual(db.ensure(["1", "5"]), 1)  # neue Karte: nicht in der Grunddatenbank, offline

    def test_fixed_starter_list_beats_the_text_rule(self):
        import sqlite3
        import build_card_db
        from card_stats import create_tables
        cards = {"1": info("1", "lukias"), "2": info("2", "ash"), "3": info("3", "ash")._replace(name="ash")}
        listing = os.path.join(tempfile.mkdtemp(), "list.json")
        with open(listing, "w", encoding="utf-8") as f:
            f.write('{"starter": {"ash": "fester Grund"}, "kein_starter": {"lukias": "nein", "Gibt es nicht": "x"}}')
        rows = build_card_db.curated_rows(cards, listing)
        self.assertEqual(sorted(rows), [("1", 0, "nein"), ("2", 1, "fester Grund"), ("3", 1, "fester Grund")])
        base = os.path.join(tempfile.mkdtemp(), "base.db")
        con = sqlite3.connect(base)
        create_tables(con)
        con.executemany("INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [(*c, 0) for c in cards.values()])
        con.executemany("INSERT INTO starter_curated VALUES (?, ?, ?)", rows)
        con.commit()
        con.close()
        db = CardStatsDB(self.path, fetch=mock.Mock(), base_path=base)
        self.assertEqual(db.starter("2"), (True, "fester Grund", False))
        self.assertFalse(db.starter("1").starter)
        db.set_starter("1", True)                   # eigene Korrektur hat Vorrang
        self.assertTrue(db.starter("1").starter)

    def test_stored_results_are_used_only_for_the_current_rules(self):
        import sqlite3
        from card_stats import create_tables
        from starter_rules import RULES_VERSION
        base = os.path.join(tempfile.mkdtemp(), "base.db")
        con = sqlite3.connect(base)
        create_tables(con)
        con.execute("INSERT INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (*info("1", "lukias"), 0))
        con.execute("INSERT INTO starter_auto VALUES ('1', 0, 'gespeichert')")
        con.execute("INSERT INTO meta VALUES ('rules_version', ?)", (str(RULES_VERSION),))
        con.commit()
        db = CardStatsDB(self.path, fetch=mock.Mock(), base_path=base)
        self.assertEqual(db.starter("1"), (False, "gespeichert", False))
        con.execute("UPDATE meta SET value = '0'")  # alte Regeln → neu berechnen
        con.commit()
        con.close()
        self.assertTrue(db.starter("1").starter)

    def test_online_cards_are_classified_and_stored(self):
        db = self.make_db()
        db.ensure(["1"])
        import sqlite3
        con = sqlite3.connect(self.path)
        self.assertEqual(con.execute("SELECT starter FROM starter_auto WHERE id = '1'").fetchone(), (1,))
        con.close()

    def test_own_rating_is_saved_and_can_be_reset(self):
        db = self.make_db()
        db.ensure(["2"])
        db.set_starter("2", True)
        again = self.make_db()
        self.assertEqual(again.starter("2"), (True, "von dir festgelegt", True))
        again.set_starter("2", None)
        self.assertFalse(again.starter("2").starter)

    def test_handtrap_and_own_rating(self):
        db = self.make_db()
        db.ensure(["1", "2"])
        self.assertEqual(db.handtrap("2").handtrap, True)   # Ash
        self.assertEqual(db.handtrap("1").handtrap, False)  # Lukias
        db.set_handtrap("1", True)
        self.assertEqual(self.make_db().handtrap("1"), (True, "von dir festgelegt", True))
        db.set_handtrap("1", None)
        self.assertFalse(db.handtrap("1").handtrap)

    def test_history_latest_first_and_search(self):
        db = CardStatsDB(self.path, base_path=None, fetch=lambda ids: {
            "1": info("1", "lukias")._replace(name="Dracotail Lukias", archetype="Dracotail"),
            "2": info("2", "ash")._replace(name="Ash Blossom & Joyous Spring"),
            "3": info("3", "arthalion")._replace(name="Dracotail Arthalion", archetype="Dracotail"),
            "4": info("4", "saji")._replace(name="Mitsurugi no Saji", archetype="Mitsurugi")})
        db.ensure(["1", "2", "3", "4"])
        db.add_history("ydke://draco", ["1", "1", "1", "2", "3"], names=["Dracoschwanz Lukias"], now=100)
        db.add_history("ydke://mitsu", ["4", "4", "4", "2"], result="Lücken", now=200)
        first, second = db.history()
        self.assertEqual((first.name, first.code, first.result), ("Mitsurugi", "ydke://mitsu", "Lücken"))
        self.assertEqual((second.name, second.main, second.extra), ("Dracotail", 4, 1))
        # Dasselbe Deck (andere Reihenfolge) noch einmal → derselbe Eintrag, wieder oben
        db.add_history("ydke://draco2", ["3", "2", "1", "1", "1"], names=["Dracoschwanz Lukias"], now=300)
        latest = db.history()
        self.assertEqual([(e.name, e.times) for e in latest], [("Dracotail", 2), ("Mitsurugi", 1)])
        self.assertEqual(latest[0].code, "ydke://draco2")
        # Suche: Name, Archetyp, Karten (englisch und Spielsprache), alle Wörter; Sonderzeichen wörtlich
        self.assertEqual([e.name for e in db.history("ash")], ["Dracotail", "Mitsurugi"])
        self.assertEqual([e.name for e in db.history("ASH saji")], ["Mitsurugi"])
        self.assertEqual([e.name for e in db.history("dracoschwanz")], ["Dracotail"])
        self.assertEqual(db.history("100%"), [])
        db.delete_history(latest[0].id)
        self.assertEqual([e.name for e in db.history()], ["Mitsurugi"])

    def test_guide_rating_beats_rules_but_not_own_rating(self):
        from starter_guides import Verdict
        db = CardStatsDB(self.path, fetch=lambda ids: {"1": info("1", "lukias")._replace(name="Maliss <P> Dormouse")},
                         base_path=None)
        db.ensure(["1"])
        self.assertTrue(db.starter("1").starter)  # Textregeln: sucht aus dem Deck
        # Guides schreiben Namen etwas anders ("Maliss P Dormouse")
        stored = db.store_guide([Verdict("Maliss P Dormouse", False, "isn't a starter on its own", "Maliss Guide")])
        self.assertEqual(stored, {"1": False})
        self.assertEqual(db.starter("1"), (False, "laut Guide „Maliss Guide“", False))
        db.set_starter("1", True)
        self.assertTrue(db.starter("1").manual)


class StarterGuidesTest(unittest.TestCase):
    def test_sentences(self):
        from starter_guides import sentence_verdict
        for text in ["Clown Crew Flair is the best starter in the deck, she allows you to reveal herself.",
                     "Habakiri effect makes Mitsurugi a 1 card combo, by gaining access to Mitsurugi Ritual.",
                     "The best starter of the trio, Lucina allows you to add an Elfnote Monster.",
                     "If you draw her without Concours, you can still perform a 1 card combo with her.",
                     "Nadir Servant is a one-card combo, though less common in modern decklists."]:
            self.assertIs(sentence_verdict(text), True, text)
        self.assertIs(sentence_verdict("While she isn't a starter on her own, Fortuna might be important."), False)
        for text in ["It is nice against cards like Aleister the Invoker and similar normal summon starters.",
                     "A smaller deck allows you to see power cards and starters more often.",
                     "If you are in need of a starter, you can send Herald of the Arc Light.",
                     "Ratios for the card are flexible, starters are very important to draw into.",
                     "Since Blue-Eyes is often starved for starters, a weaker copy is still great."]:
            self.assertIsNone(sentence_verdict(text), text)

    def test_statements_belong_to_the_card_of_the_section(self):
        from starter_guides import guide_verdicts
        text = lambda t: {"type": "text", "content": t}
        tag = lambda name, *children: {"type": "tag", "name": name, "children": list(children)}
        comp = lambda i: {"type": "tag", "name": "component", "attrs": {"id": i}, "children": []}
        markdown = {
            "customComponents": {
                "0": {"type": "CardContainer", "props": {"cards": [{"card": {"name": "Clown Crew Flair"}}]}},
                "1": {"type": "CardContainer", "props": {"cards": [{"card": {"name": "Clown Crew Cappello"}},
                                                                   {"card": {"name": "Clown Crew Finale"}}]}},
                "2": {"type": "CardLink", "props": {"name": "Clown Crew Rehearsal"}},
            },
            "htmlTree": [
                comp("0"), tag("p", text("Clown Crew Flair is the best starter in the deck.")),
                comp("1"), tag("p", text("This is the best starter.")),  # mehrere Karten → wem gehört's?
                tag("h3", text("Elfnote Fortuna")),
                tag("div", text("While she isn't a starter on her own, she sends "), comp("2"), text(".")),
                tag("h2", text("Deck Building")), tag("p", text("Our Lucina is the best starter.")),
            ]}
        verdicts = {v.name: v.starter for v in guide_verdicts(markdown, "Guide", ["Elfnote Fortuna"])}
        self.assertEqual(verdicts, {"Clown Crew Flair": True, "Elfnote Fortuna": False})

    def test_lookup_reads_newest_guide_of_the_archetype(self):
        import starter_guides
        markdown = {"customComponents": {"0": {"type": "CardContainer",
                                               "props": {"cards": [{"card": {"name": "Elfnote Lucina"}}]}}},
                    "htmlTree": [{"type": "tag", "name": "component", "attrs": {"id": "0"}, "children": []},
                                 {"type": "tag", "name": "p",
                                  "children": [{"type": "text", "content": "Lucina is our best starter."}]}]}
        articles = [{"_id": "a", "title": "Elfnote Guide", "url": "/guides/elfnote/", "date": "2026-05-08"},
                    {"_id": "b", "title": "Tier List", "url": "/tier-list/", "date": "2026-09-01"}]

        def get(url, params, timeout):
            body = articles if "search" in params else [{"parsedMarkdown": markdown}]
            assert params.get("_id", "a") == "a"  # nur der Guide wird geladen
            return mock.Mock(json=lambda: body, raise_for_status=lambda: None)

        verdicts = starter_guides.lookup(["Elfnote"], session=mock.Mock(get=get))
        self.assertEqual(verdicts, [starter_guides.Verdict("Elfnote Lucina", True, "Lucina is our best starter.",
                                                           "Elfnote Guide")])


def make_scan(main_names, extra_names=(), signatures=None):
    cards = []
    for zone, names in (("Main", main_names), ("Extra", extra_names)):
        for i, name in enumerate(names):
            cid = None if name is None else str(1000 + sorted(set(filter(None, main_names + list(extra_names)))).index(name))
            cards.append(deck_export.ExportedCard(zone, i + 1, name or "", CardMatch(cid, name or "", cid is not None)))
    zones = [("Main", md_layout.deck_slot_positions(len(main_names), md_layout.MAIN_FIRST_CARD[1], 5),
              md_layout.deck_columns(len(main_names), 5)),
             ("Extra", md_layout.deck_slot_positions(len(extra_names), md_layout.EXTRA_FIRST_Y, 2),
              md_layout.deck_columns(len(extra_names), 2))]
    return deck_export.DeckScan(FRAME, zones, cards, signatures or {})


class DeckAnalysisTest(unittest.TestCase):
    def setUp(self):
        names = ["Lukias"] * 3 + ["Ash"] * 3 + [f"Karte {i}" for i in range(33)] + [None]
        self.scan = make_scan(names, ["Arthalion", "Arthalion"])
        self.db = mock.Mock()
        lukias = self.scan.cards[0].match.cid
        self.db.starter.side_effect = lambda cid: StarterInfo(cid == lukias, "", False)
        ash = self.scan.cards[3].match.cid
        self.db.handtrap.side_effect = lambda cid: HandtrapInfo(cid == ash, "", False)
        self.analysis = deck_analysis.DeckAnalysis(self.scan, self.db)

    def test_copies_are_grouped_in_deck_order(self):
        entries = self.analysis.entries("Main")
        self.assertEqual([(e.name, e.copies) for e in entries[:2]], [("Lukias", 3), ("Ash", 3)])
        self.assertEqual(entries[-1].name, "Nicht erkannt")
        self.assertEqual(self.analysis.entry_at("Main", 4).name, "Ash")
        self.assertEqual(self.analysis.entry_at("Extra", 1).copies, 2)

    def test_stats_and_starter_odds(self):
        stats = self.analysis.stats(self.analysis.entry_at("Main", 0))
        self.assertEqual(stats.deck_size, 40)
        self.assertAlmostEqual(stats.first, 0.3376, places=4)
        self.assertEqual(self.analysis.starter_copies(), (3, 1))  # 1 nicht erkannte Karte
        self.assertAlmostEqual(self.analysis.starter_odds()[0], 0.3376, places=4)
        self.assertIsNone(self.analysis.starter(self.analysis.entry_at("Extra", 0)).starter)

    def test_handtrap_odds(self):
        self.assertEqual(self.analysis.handtrap_copies(), (3, 1))
        self.assertAlmostEqual(self.analysis.handtrap_odds()[0], 0.3376, places=4)
        self.assertAlmostEqual(self.analysis.handtrap_odds()[1], 0.3943, places=4)
        self.assertTrue(self.analysis.stats(self.analysis.entry_at("Main", 3)).handtrap.handtrap)


class SlotGeometryTest(unittest.TestCase):
    def test_card_under_mouse(self):
        scan = make_scan(["x"] * 41, ["y"] * 15)
        frame = md_layout.Frame(100, 50, 2560, 1440)
        for zone, index in (("Main", 0), ("Main", 37), ("Main", 40), ("Extra", 14)):
            positions = next(p for name, p, _ in scan.zones if name == zone)
            point = frame.point(*positions[index])
            self.assertEqual(deck_analysis.slot_at(frame, scan.zones, *point), (zone, index))
            left, top, right, bottom = deck_analysis.slot_rect(frame, scan.zones, zone, index)
            self.assertTrue(left < point[0] < right and top < point[1] < bottom)

    def test_gaps_and_empty_slots_are_no_card(self):
        scan = make_scan(["x"] * 41)
        x, y = md_layout.MAIN_FIRST_CARD
        self.assertIsNone(deck_analysis.slot_at(FRAME, scan.zones, x + md_layout.COLUMN_PITCH / 2, y))  # Lücke
        self.assertIsNone(deck_analysis.slot_at(FRAME, scan.zones, x + 2 * md_layout.COLUMN_PITCH, y + 4 * 106.7))
        self.assertIsNone(deck_analysis.slot_at(FRAME, scan.zones, 1500, 500))  # Kartenliste

    def test_compressed_columns_with_60_cards(self):
        scan = make_scan(["x"] * 60)
        positions = scan.zones[0][1]
        for index in (0, 11, 12, 59):
            self.assertEqual(deck_analysis.slot_at(FRAME, scan.zones, *positions[index]), ("Main", index))


def paint_deck(main_count, extra_count=0, seed=1, extra_card_after=False):
    """
    Deck-Bereich mit unterschiedlichen 'Kartenbildern' (Zufallsmuster je Karte).
    extra_card_after: eine Karte mehr direkt hinter der letzten (Raster bleibt gleich).
    """
    image = Image.new("RGB", (1920, 1080), BACKGROUND)
    draw = ImageDraw.Draw(image)
    w, h = md_layout.CARD_SIZE
    rng = random.Random(seed)
    main = md_layout.deck_slot_positions(main_count, md_layout.MAIN_FIRST_CARD[1], 5)
    if extra_card_after:
        main.append((main[0][0] + (main_count % 10) * md_layout.COLUMN_PITCH,
                     main[0][1] + (main_count // 10) * md_layout.ROW_PITCH))
    for positions in (main, md_layout.deck_slot_positions(extra_count, md_layout.EXTRA_FIRST_Y, 2)):
        for rx, ry in positions:
            x1, y1 = rx - w / 2 + 3, ry - h / 2
            for by in range(6):
                for bx in range(4):
                    color = tuple(rng.randrange(60, 255) for _ in range(3))
                    draw.rectangle((x1 + bx * 14.5, y1 + by * 15.7, x1 + (bx + 1) * 14.5, y1 + (by + 1) * 15.7),
                                   fill=color)
    return image


def md_memory_unavailable():
    import md_memory
    return md_memory.MemoryUnavailable("Deck-Editor zu")


def scan_of(image, main_count, extra_count=0):
    scan = make_scan(["x"] * main_count, ["y"] * extra_count)
    panel = FRAME.region(*md_layout.DECK_PANEL)
    crop = image.crop((panel["left"], panel["top"], panel["left"] + panel["width"], panel["top"] + panel["height"]))
    return scan._replace(signatures=deck_export.slot_signatures(crop, (panel["left"], panel["top"]), FRAME, scan.zones))


class DeckChangeTest(unittest.TestCase):
    def check(self, scan, image, cursor=None):
        return deck_analysis.deck_changes(scan, FRAME, image, (0, 0), cursor)

    def test_same_deck(self):
        image = paint_deck(40, 15)
        self.assertIsNone(self.check(scan_of(image, 40, 15), image))

    def test_swapped_card_is_noticed(self):
        image = paint_deck(40, 15)
        scan = scan_of(image, 40, 15)
        swapped = image.copy()
        swapped.paste(paint_deck(40, 15, seed=7).crop((570, 300, 650, 400)), (570, 300))  # 2. Karte, 2. Reihe
        self.assertIn("anders", self.check(scan, swapped))
        # … außer die Maus liegt darauf (Master Duel hebt die Karte dann hervor)
        self.assertIsNone(self.check(scan, swapped, cursor=("Main", 11)))

    def test_added_card_is_noticed(self):
        scan = scan_of(paint_deck(41), 41)
        self.assertIn("Kartenzahl", self.check(scan, paint_deck(41, extra_card_after=True)))

    def test_resized_window(self):
        image = paint_deck(40)
        self.assertIn("Fenstergröße", deck_analysis.deck_changes(
            scan_of(image, 40), md_layout.Frame(0, 0, 2560, 1440), image, (0, 0)))


class FakeSct:
    def __init__(self, image):
        self.image = image

    def grab(self, m):
        crop = self.image.crop((m["left"], m["top"], m["left"] + m["width"], m["top"] + m["height"]))

        class Shot:
            size = crop.size
            bgra = crop.convert("RGBA").tobytes("raw", "BGRA")
            rgb = crop.tobytes()
        return Shot()


class DeckWatcherTest(unittest.TestCase):
    def make(self, image, counts):
        reads = []

        def read_count(sct, frame, point, cmd):
            reads.append(point)
            return counts["Main" if point == md_layout.REFERENCE_POINTS["DECK_COUNT"] else "Extra"]
        watcher = deck_analysis.DeckWatcher(scan_of(image, 40, 15), "", lambda: None, frame_source=lambda: FRAME,
                                            read_count=read_count)
        return watcher, reads

    def test_count_change_is_sticky_and_ocr_only_runs_when_pixels_change(self):
        image = paint_deck(40, 15)
        counts = {"Main": 40, "Extra": 15}
        watcher, reads = self.make(image, counts)
        sct = FakeSct(image)
        self.assertIsNone(watcher.check(sct))
        self.assertIsNone(watcher.check(sct))
        self.assertEqual(len(reads), 2)  # 2. Runde: Zahlen unverändert → keine Texterkennung
        counts["Main"] = 41
        ImageDraw.Draw(image).text(FRAME.point(*md_layout.REFERENCE_POINTS["DECK_COUNT"]), "41", fill="white")
        self.assertIsNone(watcher.check(sct))  # einmal gelesen kann ein Lesefehler sein
        self.assertIn("41 statt 40", watcher.check(sct))
        counts["Main"] = 40
        self.assertIn("41 statt 40", watcher.check(sct))  # bleibt bis zum neuen Scan

    def test_single_misread_count_is_ignored(self):
        image = paint_deck(40, 15)
        counts = {"Main": 40, "Extra": 15}
        watcher, _ = self.make(image, counts)
        sct = FakeSct(image)
        watcher.check(sct)
        counts["Extra"] = 23  # z.B. Zahl aus einem Fenster darüber gelesen
        ImageDraw.Draw(image).text(FRAME.point(*md_layout.EXTRA_COUNT_POINT), "23", fill="white")
        self.assertIsNone(watcher.check(sct))
        counts["Extra"] = 15
        self.assertIsNone(watcher.check(sct))
        self.assertIsNone(watcher.check(sct))

    def test_memory_scan_is_checked_exactly_without_screen(self):
        # Lesemethode "Speicher": Deck aus dem Speicher vergleichen, kein Bildvergleich (keine Fehlalarme)
        image = paint_deck(40, 15)
        watcher, reads = self.make(image, {"Main": 40, "Extra": 15})
        watcher.scan = watcher.scan._replace(kids=([1, 2, 3], [9]))
        deck = [[1, 2, 3], [9]]
        selected = image.copy()  # z.B. Auswahlrahmen um die zuletzt angeklickte Karte
        selected.paste(paint_deck(40, 15, seed=9).crop((570, 300, 650, 400)), (570, 300))
        with mock.patch.object(deck_analysis.md_memory, "is_ready", return_value=True), \
                mock.patch.object(deck_analysis.md_memory, "shared") as shared:
            shared.return_value.deck = lambda: (list(deck[0]), list(deck[1]))
            self.assertIsNone(watcher.check(FakeSct(selected)))
            self.assertIsNone(watcher.check(FakeSct(selected)))
            self.assertEqual(reads, [])  # keine Texterkennung
            deck[0] = [1, 2, 3, 4]
            self.assertEqual(watcher.check(FakeSct(image)), "Main Deck hat jetzt 4 statt 3 Karten")
            deck[0] = [1, 5, 3]
            self.assertEqual(watcher.check(FakeSct(image)), "Main #2 ist eine andere Karte")
            deck[0] = [3, 2, 1]
            self.assertEqual(watcher.check(FakeSct(image)), "Reihenfolge im Main Deck geändert")
            deck[0] = [1, 2, 3]
            self.assertIsNone(watcher.check(FakeSct(image)))  # wieder wie beim Scan
            shared.return_value.deck = mock.Mock(side_effect=md_memory_unavailable())
            watcher.check(FakeSct(image))
        self.assertEqual(len(reads), 2)  # Speicher nicht lesbar → wieder über den Bildschirm

    def test_card_swap_needs_two_checks_and_clears_again(self):
        image = paint_deck(40, 15)
        watcher, _ = self.make(image, {"Main": 40, "Extra": 15})
        swapped = image.copy()
        swapped.paste(paint_deck(40, 15, seed=9).crop((570, 300, 650, 400)), (570, 300))
        self.assertIsNone(watcher.check(FakeSct(swapped)))   # einmal kann ein Glanzeffekt sein
        self.assertIn("anders", watcher.check(FakeSct(swapped)))
        watcher.check(FakeSct(image))
        self.assertIsNone(watcher.check(FakeSct(image)))     # wieder wie beim Scan


@unittest.skipUnless(HAS_TK, "Tk nicht verfügbar")
class ImmediateThread:
    """Ersatz für threading.Thread: läuft sofort (Tests ohne Warten)."""
    def __init__(self, target, args=(), daemon=None):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


class ExtrasPanelTest(unittest.TestCase):
    def setUp(self):
        import extras_panel
        self.root = tk.Tk()
        self.root.withdraw()
        self.db = CardStatsDB(os.path.join(tempfile.mkdtemp(), "s.db"), base_path=None,
                              fetch=lambda ids: {cid: info(cid, "lukias" if cid == "1" else "ash") for cid in ids})
        names = ["Lukias"] * 3 + ["Ash"] * 2 + [f"K{i}" for i in range(35)]
        scan = make_scan(names, ["Arthalion"])
        self.scan = scan._replace(cards=[c._replace(match=c.match._replace(cid="1")) if c.raw_ocr == "Lukias" else c
                                         for c in scan.cards])
        self.db.ensure(c.match.cid for c in self.scan.cards if c.match.cid)
        self.closed = []
        self.cursor = (0, 0)
        self.patches = [mock.patch.object(extras_panel.DeckWatcher, "start", lambda self: None),
                        mock.patch.object(extras_panel.md_layout, "md_frame", return_value=FRAME),
                        mock.patch.object(extras_panel.win_api, "get_cursor_pos", lambda: self.cursor)]
        for p in self.patches:
            p.start()
        self.panel = extras_panel.ExtrasPanel(self.root, self.db, "", on_rescan=lambda: None,
                                              on_close=lambda: self.closed.append(True))

    def tearDown(self):
        self.panel.close()
        for p in self.patches:
            p.stop()
        self.root.destroy()

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self.root.update()
            time.sleep(0.01)

    def test_opens_over_card_list_and_shows_deck(self):
        self.panel.set_scan(self.scan)
        self.root.update()
        area = FRAME.region(*md_layout.CLICK_AREAS["die Kartenliste"])
        self.assertEqual((self.panel.win.winfo_x(), self.panel.win.winfo_y()), (area["left"], area["top"]))
        self.assertEqual(len(self.panel._rows), 1 + 1 + 35 + 1)  # Lukias, Ash, 35 andere, Arthalion
        self.assertEqual(self.panel.tiles["main"].cget("text"), "40")
        self.assertEqual(self.panel.tiles["starter"].cget("text"), "3")
        self.assertEqual(self.panel.tiles["handtrap"].cget("text"), "37")  # Ash ×2 + 35 andere (Text von Ash)
        self.assertEqual(self.panel.starter_odds.cget("text"), "Mind. 1 Starter auf der Hand:  33,8 %")
        self.assertEqual(self.panel.handtrap_odds.cget("text"), "Mind. 1 Handtrap:  100,0 %")
        # rechts neben der Starter-Chance
        self.root.update()
        self.assertGreaterEqual(self.panel.handtrap_odds.winfo_x(),  # rechts daneben, ohne Überlappung
                           self.panel.starter_odds.winfo_x() + self.panel.starter_odds.winfo_width())

    def test_handtrap_can_be_changed_by_click(self):
        self.panel.set_scan(self.scan)
        self.root.update()
        self.panel._rows["1"].winfo_children()[-2].event_generate("<Button-1>")  # Handtrap "Nein" → "Ja"
        self.root.update()
        self.assertTrue(self.db.handtrap("1").handtrap)
        self.assertEqual([w.cget("text") for w in self.panel._rows["1"].winfo_children()[-2:]], ["Ja", "✎"])
        self.assertEqual(self.panel.tiles["handtrap"].cget("text"), "40")
        self.assertEqual(self.panel._hover_content(self.panel.analysis.entry_at("Main", 0)).badge2[0], "HANDTRAP ✓")
        self.panel._rows["1"].winfo_children()[-1].event_generate("<Button-3>")
        self.root.update()
        self.assertFalse(self.db.handtrap("1").handtrap)

    def test_history_button_left_of_options_opens_deck_without_game(self):
        import extras_panel
        imported = []
        self.panel.on_import_code = imported.append
        self.root.update()
        # links neben "Optionen"
        self.assertLess(self.panel.history_btn.winfo_x() + self.panel.history_btn.winfo_width(),
                        self.panel.options_btn.winfo_x() + 1)
        ydk = "#main\n" + "1\n" * 3 + "2\n" * 2 + "#extra\n9\n!side\n"
        self.db.add_history(ydk, ["1", "1", "1", "2", "2", "9"], now=1)
        self.panel.history_btn.invoke()
        history = self.panel.history
        self.assertEqual([e.name for e in history.entries], ["lukias"])
        with mock.patch.object(extras_panel.threading, "Thread", ImmediateThread):
            history._open(history.entries[0])  # Klick auf das Deck
        self.pump(0.1)
        self.assertIsNone(self.panel.history)
        analysis = self.panel.analysis
        self.assertEqual((analysis.main_size, [(e.key, e.copies) for e in analysis.entries()]), (6, [("1", 3), ("2", 2), ("9", 1)]))
        self.assertTrue(self.panel.offline)
        self.assertIsNone(self.panel.watcher)  # nicht das Deck im Spiel → keine Prüfung, kein Mouseover
        self.assertIn("Aus dem Verlauf", self.panel.status_label.cget("text"))
        self.assertEqual(self.panel.change_reason(), "Deck aus dem Verlauf angezeigt")  # Export liest neu
        self.cursor = FRAME.point(*self.scan.zones[0][1][0])
        self.pump(0.3)
        self.assertFalse(self.panel.hover.visible)
        # Importieren aus dem Verlauf
        self.panel.toggle_history()
        self.panel.history._import(self.panel.history.entries[0])
        self.assertEqual(imported, [ydk.strip()])
        # Neu scannen → wieder das Deck im Spiel
        self.panel.set_scan(self.scan)
        self.assertFalse(self.panel.offline)
        self.assertIsNotNone(self.panel.watcher)

    def test_staples_need_a_scan_and_share_the_place_with_winrate(self):
        self.panel.toggle_staples()
        self.assertIsNone(self.panel.staples)
        self.assertIn("Erst das Deck scannen", self.panel.status_label.cget("text"))
        self.panel.set_scan(self.scan)
        self.panel.staples_btn.invoke()
        staples = self.panel.staples
        self.assertEqual(staples.deck["lukias"], 3)
        self.assertEqual(staples.archetypes, [])  # Testkarten ohne Archetyp
        self.assertEqual(staples.passcodes["lukias"], "1")  # fürs Kartenbild
        self.panel.winrate_btn.invoke()
        self.assertTrue(staples.closed)
        self.assertIsNone(self.panel.staples)

    def test_side_profiles_only_swap_the_scanned_unchanged_deck(self):
        import extras_panel
        swaps = []
        self.panel.on_side_swap = lambda target, label: swaps.append((target, label))
        self.panel.side_btn.invoke()
        self.assertIsNone(self.panel.side)
        self.assertIn("erst das Deck scannen", self.panel.status_label.cget("text"))
        self.panel.set_scan(self.scan)
        self.panel.side_btn.invoke()
        side = self.panel.side
        self.assertEqual((side.deck["1"], side.zones["1"], side.unknown), (3, "Main", 0))
        with mock.patch.object(extras_panel, "current_changes", return_value=None):
            self.panel._side_swap(["1"], "Zweiter")
        self.assertEqual(swaps, [(["1"], "Zweiter")])
        # Deck im Editor inzwischen geändert → nicht tauschen (die Kontrolle würde die Änderung rückgängig machen)
        with mock.patch.object(extras_panel, "current_changes", return_value="Main #1 ist eine andere Karte"):
            self.panel._side_swap(["1"], "Zweiter")
        self.assertEqual(len(swaps), 1)
        self.assertIn("Neu scannen", self.panel.status_label.cget("text"))
        # Unsicher erkannte Karte → kein Tausch möglich
        self.panel.ash_btn.invoke()  # schließt Side (gleicher Platz)
        self.assertTrue(side.closed)
        unsure = self.scan._replace(cards=[self.scan.cards[0]._replace(
            match=self.scan.cards[0].match._replace(sure=False))] + self.scan.cards[1:])
        self.panel.set_scan(unsure)
        self.panel.side_btn.invoke()
        self.assertEqual(self.panel.side.unknown, 1)

    def test_ash_prio_for_the_shown_deck(self):
        import ash_prio
        self.panel.set_scan(self.scan)
        with mock.patch.object(ash_prio, "load", return_value=(None, [], "nicht durchgerechnet")) as load:
            self.panel.ash_btn.invoke()
            self.pump(0.3)
        self.assertEqual(load.call_args.args[0]["1"], 3)  # Deck mit Kopien
        self.assertEqual(self.panel.ash_prio.info_label.cget("text"), "nicht durchgerechnet")
        self.panel.ash_btn.invoke()
        self.assertIsNone(self.panel.ash_prio)

    def test_matchup_for_the_shown_deck(self):
        self.panel.set_scan(self.scan)
        self.panel.matchup_btn.invoke()
        matchup = self.panel.matchup
        from tests.test_matchup import wait_for_matchup
        wait_for_matchup(self.root, matchup)
        self.assertEqual(matchup.deck_name, self.db.deck_name(c.match.cid for c in self.scan.cards))
        self.assertIn("Match History", matchup.info_label.cget("text"))  # noch keine Gegner-Decks
        self.panel.history_btn.invoke()
        self.assertTrue(matchup.closed)

    def test_hover_is_raised_above_other_topmost_windows(self):
        import hover_card
        self.panel.set_scan(self.scan)
        with mock.patch.object(hover_card.win_api, "raise_topmost") as raise_topmost:
            self.cursor = FRAME.point(*self.scan.zones[0][1][1])
            self.pump(0.3)
        raise_topmost.assert_called_once_with(self.panel.hover._hwnd)

    def test_hover_shows_analysis_then_stats(self):
        import hover_card
        self.panel.set_scan(self.scan)
        with mock.patch.object(hover_card, "ANALYSIS_TIME", 0.3), \
                mock.patch.object(self.panel.hover, "show", wraps=self.panel.hover.show) as show:
            self.cursor = FRAME.point(*self.scan.zones[0][1][1])  # 2. Karte: Lukias
            self.pump(0.3)
            show.assert_called_once()
            self.assertEqual(self.panel.hover.phase, "loading")
            self.assertEqual(self.panel._highlighted, "1")
            self.pump(1.2)
        self.assertEqual(self.panel.hover.phase, "done")
        content = self.panel.hover.content
        self.assertEqual(content.title, "Lukias")
        self.assertIn("33,8 %", [row.value for row in content.rows])
        self.assertNotIn("2. ZUG · 6 KARTEN", [row.label for row in content.rows])  # nur mit Option
        self.assertEqual(content.badge[0], "STARTER ✓")
        self.assertIn("sucht 1 Karte", content.badge_note)
        self.assertFalse(self.panel.watcher.active())  # Hover-Fenster liegt evtl. über dem Deck → keine Prüfung
        self.cursor = (1500, 500)  # weg vom Deck
        self.pump(0.15)
        self.assertTrue(self.panel.hover.visible)  # kurze Lücke: bleibt noch stehen
        self.pump(0.4)
        self.assertFalse(self.panel.hover.visible)

    def test_animation_is_not_skipped_when_the_ui_hangs(self):
        # Beim ersten Anzeigen über dem Vollbild-Spiel kann die Oberfläche kurz hängen: das darf die Analyse-
        # Animation nicht "verbrauchen" (sonst springt das Fenster gleich zu "Analyse abgeschlossen")
        import hover_card
        clock = [100.0]
        with mock.patch.object(hover_card.time, "monotonic", lambda: clock[0]):
            card = self.panel.hover
            card.show((10, 10, 50, 80), "Lukias", lambda: hover_card.HoverContent("Lukias", "", []), duration=1.0)
            clock[0] += 2.0          # Hänger direkt nach dem Zeigen
            card._animate()
            self.assertEqual(card.phase, "loading")
            for _ in range(40):      # danach normale Bilder (30 ms)
                clock[0] += 0.03
                card._animate()
            self.assertEqual(card.phase, "done")
            card.hide()

    def test_quick_sweeps_do_not_open_the_window(self):
        import extras_panel
        self.panel.set_scan(self.scan)
        clock = [100.0]
        with mock.patch.object(self.panel.hover, "show") as show,                 mock.patch.object(self.panel.hover, "hide") as hide,                 mock.patch.object(extras_panel.time, "monotonic", lambda: clock[0]):
            for i in range(8):  # schnell über die erste Reihe wischen: 50 ms pro Karte
                self.cursor = FRAME.point(*self.scan.zones[0][1][i])
                self.panel._update_hover()
                clock[0] += 0.05
                self.panel._update_hover()
            show.assert_not_called()
            clock[0] += 0.1  # liegen geblieben → jetzt erst
            self.panel._update_hover()
            show.assert_called_once()
            # über die Lücke zur nächsten Karte: Fenster bleibt, springt nur um
            x, y = self.scan.zones[0][1][7]
            self.cursor = FRAME.point(x + md_layout.COLUMN_PITCH / 2, y)
            clock[0] += 0.1
            self.panel._update_hover()
            hide.assert_not_called()

    def test_no_reason_for_non_starters(self):
        self.panel.set_scan(self.scan)
        ash = self.panel.analysis.entry_at("Main", 3)
        content = self.panel._hover_content(ash)
        self.assertEqual((content.badge[0], content.badge_note), ("KEIN STARTER", ""))
        self.assertEqual(content.badge2[0], "HANDTRAP ✓")
        self.assertIn("abwerfen", content.badge2_note)
        self.assertIsNone(self.panel._hover_content(self.panel.analysis.entry_at("Main", 0)).badge2)

    def test_second_turn_column_is_an_option(self):
        saved = []
        self.panel.save_settings = lambda: saved.append(dict(self.panel.settings))
        self.panel.set_scan(self.scan)
        header = [w.cget("text") for w in self.panel.table_head.winfo_children()]
        self.assertEqual(header, ["Karte", "×", "1. Zug (5)", "Starter", "", "Handtrap", ""])
        self.assertNotIn("2. Zug", self.panel.starter_odds.cget("text"))
        self.panel.options_menu.invoke("Spalte „2. Zug“")
        header = [w.cget("text") for w in self.panel.table_head.winfo_children()]
        self.assertEqual(header, ["Karte", "×", "1. Zug (5)", "2. Zug (6)", "Starter", "", "Handtrap", ""])
        self.assertEqual(len(self.panel._rows["1"].winfo_children()), 8)
        self.assertIn("2. Zug", self.panel.starter_odds.cget("text"))
        self.assertIn("2. Zug", self.panel.handtrap_odds.cget("text"))
        self.assertEqual(saved, [{"DECK_SHOW_SECOND_TURN": True, "DECK_HOVER_ANIMATION": True}])

    def test_read_method_can_be_switched_in_options(self):
        saved = []
        self.panel.save_settings = lambda: saved.append(dict(self.panel.settings))
        self.panel.options_menu.invoke("Speicher lesen")
        self.assertEqual(saved[-1]["READ_METHOD"], "memory")
        self.panel.options_menu.invoke("Texterkennung")
        self.assertEqual(saved[-1]["READ_METHOD"], "ocr")

    def test_options_are_grouped_buttons_and_stay_open_while_switching(self):
        import options_popup
        self.panel.save_settings = lambda: None
        self.panel._open_options()
        self.root.update()
        menu = self.panel.options_menu
        self.assertTrue(menu.is_open)
        self.assertEqual(set(menu.buttons), {"Speicher lesen", "Texterkennung", "Spalte „2. Zug“",
                                             "Analyse-Animation", "Aus Guides nachladen (online)"})
        # Buttons einer Reihe nebeneinander
        self.assertIs(menu.buttons["Speicher lesen"].master, menu.buttons["Texterkennung"].master)
        menu.invoke("Speicher lesen")
        self.assertTrue(menu.is_open)  # Umschalten lässt das Fenster offen
        self.assertEqual(menu.buttons["Speicher lesen"]._opts["fill"], options_popup.CHOSEN)
        self.assertEqual(menu.buttons["Texterkennung"]._opts["fill"], options_popup.OFF)
        menu.invoke("Analyse-Animation")  # war an → aus
        self.assertEqual(menu.buttons["Analyse-Animation"]._opts["fill"], options_popup.OFF)
        menu.close()
        self.assertFalse(menu.is_open)

    def test_speed_option_only_when_reading_memory(self):
        saved = []
        self.panel.save_settings = lambda: saved.append(dict(self.panel.settings))
        self.panel._open_options()
        self.root.update()
        menu = self.panel.options_menu
        self.assertNotIn("Langsam", menu.buttons)  # Texterkennung: Tempo sitzt im Overlay
        menu.invoke("Speicher lesen")
        self.assertTrue(menu.is_open)
        self.assertIn("Langsam", menu.buttons)
        menu.invoke("Langsam")
        self.assertEqual(saved[-1]["SPEED_PROFILE"], "slow")
        self.assertEqual(menu.buttons["Langsam"]._opts["fill"], "#007acc")
        menu.invoke("Texterkennung")
        self.assertNotIn("Langsam", menu.buttons)
        menu.close()

    def test_animation_can_be_switched_off(self):
        self.panel.set_scan(self.scan)
        self.panel.options_menu.invoke("Analyse-Animation")  # aus
        self.assertFalse(self.panel.settings["DECK_HOVER_ANIMATION"])
        self.cursor = FRAME.point(*self.scan.zones[0][1][1])
        self.pump(0.3)
        self.assertEqual(self.panel.hover.phase, "done")  # Stats sofort, ohne "Analyse…"
        self.assertEqual(self.panel.hover.content.title, "Lukias")

    def test_numbers_stay_aligned_with_manual_marker(self):
        # Echter Fall: Zeilen mit "Nein ✎" schoben Anzahl und Prozent nach links
        self.panel.set_scan(self.scan)
        self.root.update()
        self.panel._toggle_starter(self.panel.analysis.entry_at("Main", 0))
        self.panel._toggle_handtrap(self.panel.analysis.entry_at("Main", 0))
        self.root.update()
        rights = {key: [w.winfo_x() + w.winfo_width() for w in row.winfo_children()[1:]]
                  for key, row in self.panel._rows.items()}
        self.assertEqual(len({tuple(r) for r in rights.values()}), 1)  # alle Spalten enden überall gleich
        # Überschriften stehen genau über den Werten (Kopfzeile ohne Scrollleiste)
        head = [w.winfo_rootx() + w.winfo_width() for w in self.panel.table_head.winfo_children()[1:]]
        row = [w.winfo_rootx() + w.winfo_width() for w in self.panel._rows["1"].winfo_children()[1:]]
        self.assertEqual(head, row)

    def test_starter_can_be_changed_by_click(self):
        self.panel.set_scan(self.scan)
        self.root.update()
        row = self.panel._rows["1"]
        row.winfo_children()[-4].event_generate("<Button-1>")  # Starter "Ja"/"Nein"
        self.root.update()
        self.assertFalse(self.db.starter("1").starter)
        self.assertEqual([w.cget("text") for w in self.panel._rows["1"].winfo_children()[-4:-2]], ["Nein", "✎"])
        self.assertEqual(self.panel.tiles["starter"].cget("text"), "0")
        self.panel._rows["1"].winfo_children()[-3].event_generate("<Button-3>")
        self.root.update()
        self.assertTrue(self.db.starter("1").starter)

    def test_change_warning(self):
        self.panel.set_scan(self.scan)
        self.panel.watcher.count_reason = "Main Deck hat jetzt 41 statt 40 Karten"
        self.pump(0.1)
        self.assertIn("bitte neu scannen", self.panel.status_label.cget("text"))

    def test_calculator(self):
        for key, value in (("deck", 40), ("copies", 3), ("hand", 5), ("min", 1)):
            self.panel.calc_vars[key].set(value)
        self.panel._update_calculator()
        self.assertEqual(self.panel.calc_result.cget("text"), "→ 33,8 %")

    def test_starters_can_be_loaded_from_guides(self):
        import extras_panel
        from starter_guides import Verdict
        self.db._fetch = lambda ids: {cid: info(cid, "lukias")._replace(name="Lukias", archetype="Dracotail")
                                      for cid in ids if cid == "1"}
        self.panel.set_scan(self.scan)
        lukias = next(e for e in self.panel.analysis.entries() if e.key == "1")
        self.assertTrue(self.panel.analysis.starter(lukias).starter)
        found = Verdict("Lukias", False, "Lukias isn't a starter on his own.", "Dracotail Guide")
        with mock.patch.object(extras_panel.starter_guides, "lookup", return_value=[found]) as lookup:
            self.panel.options_menu.invoke("Aus Guides nachladen (online)")
            self.panel._guide_thread.join(5)
            self.pump(0.15)
        self.assertEqual(lookup.call_args.args[0], ["Dracotail"])  # Archetyp mit 3 Karten im Deck
        self.assertFalse(self.panel.analysis.starter(lukias).starter)
        self.assertIn("Dracotail Guide", self.panel.status_label.cget("text"))

    def test_guides_need_a_scan_first(self):
        self.panel.load_guides()
        self.assertIsNone(self.panel._guide_thread)
        self.assertIn("Erst das Deck scannen", self.panel.status_label.cget("text"))

    def test_close_button(self):
        self.panel.close()
        self.assertEqual(self.closed, [True])


if __name__ == "__main__":
    unittest.main()
