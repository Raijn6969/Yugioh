"""Deck-Export (.ydk), Kartenliste, Bildschirm-Layout und automatische Kalibrierung."""

import os
import tempfile
import unittest
from unittest import mock

from PIL import Image, ImageDraw, ImageFont

import card_db
import deck_export
import md_layout
from auto_calibration import auto_calibrate
from utils import parse_clipboard

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESSERACT = os.path.join(ROOT, "Tesseract-OCR", "tesseract.exe")
BACKGROUND = (18, 30, 48)  # dunkelblauer Deck-Hintergrund


class FakeScreen:
    """Ersetzt mss: liefert Ausschnitte aus einem gemalten Bildschirmbild."""

    def __init__(self, image):
        self.image = image

    def grab(self, m):
        crop = self.image.crop((m["left"], m["top"], m["left"] + m["width"], m["top"] + m["height"]))

        class Shot:
            size = crop.size
            bgra = crop.convert("RGBA").tobytes("raw", "BGRA")
        return Shot()


def paint_deck_editor(frame, main_count, extra_count, screen=(2700, 1600)):
    """Malt die wichtigen Teile des Deck-Editors (Überschrift, Zahlen, Karten) in ein Bild."""
    image = Image.new("RGB", screen, BACKGROUND)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype("arialbd.ttf", int(26 * frame.scale_y))
    x, y = frame.point(530, 180)
    draw.text((x, y), "Main Deck", fill=(240, 240, 240), font=font)
    for count, (cx, cy) in ((main_count, md_layout.REFERENCE_POINTS["DECK_COUNT"]),
                            (extra_count, md_layout.EXTRA_COUNT_POINT)):
        px, py = frame.point(cx - 14, cy - 15)
        draw.text((px, py), str(count), fill=(240, 240, 240), font=font)
    w, h = md_layout.CARD_SIZE
    for count, first_y, rows in ((main_count, md_layout.MAIN_FIRST_CARD[1], md_layout.MAIN_ROWS),
                                 (extra_count, md_layout.EXTRA_FIRST_Y, md_layout.EXTRA_ROWS)):
        for rx, ry in md_layout.deck_slot_positions(count, first_y, rows):
            draw.rectangle((*frame.point(rx - w / 2 + 3, ry - h / 2), *frame.point(rx + w / 2 - 3, ry + h / 2)),
                           fill=(200, 150, 90))
    return image


class LayoutTest(unittest.TestCase):
    def test_columns_grow_above_50_cards(self):
        self.assertEqual([md_layout.deck_columns(n, 5) for n in (40, 41, 50, 51, 60, 65)], [10, 10, 10, 11, 12, 13])
        self.assertEqual(md_layout.deck_columns(15, md_layout.EXTRA_ROWS), 10)

    def test_first_and_last_column_stay_in_place(self):
        for count in (41, 60, 65):
            positions = md_layout.deck_slot_positions(count, 267, 5)
            columns = md_layout.deck_columns(count, 5)
            self.assertAlmostEqual(positions[0][0], md_layout.MAIN_FIRST_CARD[0])
            self.assertAlmostEqual(positions[columns - 1][0], md_layout.MAIN_FIRST_CARD[0] + 9 * md_layout.COLUMN_PITCH)
            self.assertAlmostEqual(positions[-1][1], 267 + (-(-count // columns) - 1) * md_layout.ROW_PITCH)

    def test_frame_scales_and_offsets(self):
        frame = md_layout.Frame(100, 50, 2560, 1440)
        self.assertEqual(frame.point(1920, 1080), (2660, 1490))
        self.assertEqual(frame.region(15, 115, 380, 155), {"left": 120, "top": 203, "width": 487, "height": 54})
        self.assertTrue(frame.is_16_9)
        self.assertFalse(md_layout.Frame(0, 0, 3440, 1440).is_16_9)


class CardDbTest(unittest.TestCase):
    def setUp(self):
        self.db = card_db.CardDB([
            ("1", "Bonfire", "spell"),
            ("2", "Mitsurugi no Mikoto, Aramasa", "effect"),
            ("3", "Mitsurugi no Mikoto, Saji", "effect"),
            ("4", "Clockwork Knight", "effect"),
            ("5", "Clockwork Night", "spell"),
            ("6", "Cyber Dragon Infinity", "xyz"),
            ("7", "Number 39: Utopia", "xyz"),
            ("8", "Number 39: Utopia Beyond", "xyz"),
            ("9", "Token", "token"),
        ])

    def test_exact_and_leading_l(self):
        self.assertEqual(self.db.match("bonfire", extra=False)[:3], ("1", "Bonfire", True))
        self.assertEqual(self.db.match("lbonfire", extra=False).cid, "1")

    def test_knight_and_night_stay_apart(self):
        self.assertEqual(self.db.match("clockworknight", extra=False).cid, "5")
        self.assertEqual(self.db.match("clockworkknight", extra=False).cid, "4")

    def test_truncated_name(self):
        match = self.db.match("mitsuruginomikotoaram", extra=False)
        self.assertEqual((match.cid, match.sure), ("2", True))

    def test_truncated_ambiguous_is_flagged(self):
        match = self.db.match("number39utopia"[:13], extra=True)
        self.assertFalse(match.sure)
        self.assertIn("könnte auch", match.note)

    def test_extra_deck_cards_only_from_extra_pool(self):
        self.assertEqual(self.db.match("cyberdragoninfinity", extra=True).cid, "6")
        self.assertIsNone(self.db.match("bonfire", extra=True).cid)

    def test_misread_letter(self):
        match = self.db.match("cyberdragoninfinty", extra=True)
        self.assertEqual(match.cid, "6")

    def test_nothing_read(self):
        self.assertIsNone(self.db.match("", extra=False).cid)

    def test_card_list_is_cached(self):
        folder = tempfile.mkdtemp()
        with mock.patch.object(card_db, "APP_DIR", folder), \
                mock.patch.object(card_db, "_download", return_value=[("1", "Bonfire", "spell")]) as download:
            card_db.load_card_list("en")
            card_db.load_card_list("en")
        download.assert_called_once()


class YdkTest(unittest.TestCase):
    def test_ydk_format_can_be_imported_again(self):
        text = deck_export.build_ydk(["1", "1", "2"], ["3"])
        lines = text.splitlines()
        self.assertEqual(lines[1:], ["#main", "1", "1", "2", "#extra", "3", "!side"])
        with mock.patch("utils.pyperclip.paste", return_value=text):
            self.assertEqual(parse_clipboard(), ["1", "1", "2", "3"])

    def test_save_never_overwrites(self):
        folder = tempfile.mkdtemp()
        first = deck_export.save_ydk("a", folder)
        second = deck_export.save_ydk("b", folder)
        self.assertNotEqual(first, second)
        self.assertTrue(first.endswith(".ydk") and os.path.dirname(first) == folder)
        with open(first, encoding="utf-8") as f:
            self.assertEqual(f.read(), "a")

    def test_more_than_three_copies_is_reported(self):
        match = card_db.CardMatch("1", "Bonfire", True)
        cards = [deck_export.ExportedCard("Main", i, "Bonfire", match) for i in range(1, 5)]
        result = deck_export.ExportResult("", 4, 0, cards)
        self.assertTrue(any("4x gelesen" in p for p in result.problems))


class LayoutCheckTest(unittest.TestCase):
    frame = md_layout.Frame(0, 0, 1920, 1080)

    def mask_for(self, image):
        return deck_export.background_mask(image.crop((*self.frame.point(*md_layout.DECK_PANEL[:2]),
                                                       *self.frame.point(*md_layout.DECK_PANEL[2:]))))

    def zones(self, main, extra):
        return [("Main", md_layout.deck_slot_positions(main, md_layout.MAIN_FIRST_CARD[1], 5),
                 md_layout.deck_columns(main, 5)),
                ("Extra", md_layout.deck_slot_positions(extra, md_layout.EXTRA_FIRST_Y, 2),
                 md_layout.deck_columns(extra, 2))]

    def test_matching_layout_passes(self):
        for main in (40, 41, 60):
            mask = self.mask_for(paint_deck_editor(self.frame, main, 15, screen=(1920, 1080)))
            self.assertIsNone(deck_export.check_layout(mask, self.frame, self.zones(main, 15)), main)

    def test_wrong_count_is_detected(self):
        # Zahl falsch gelesen (41 statt 44): hinter der letzten erwarteten Karte liegt noch eine
        mask = self.mask_for(paint_deck_editor(self.frame, 44, 15, screen=(1920, 1080)))
        self.assertIn("hinter der letzten Karte", deck_export.check_layout(mask, self.frame, self.zones(41, 15)))
        mask = self.mask_for(paint_deck_editor(self.frame, 30, 15, screen=(1920, 1080)))
        self.assertIn("keine Karte", deck_export.check_layout(mask, self.frame, self.zones(41, 15)))


@unittest.skipUnless(os.path.exists(TESSERACT), "Tesseract nicht vorhanden")
class AutoCalibrationTest(unittest.TestCase):
    def test_points_scaled_to_window_with_offset(self):
        frame = md_layout.Frame(100, 50, 2560, 1440)  # 1440p im Fenster, nicht bei (0, 0)
        screen = FakeScreen(paint_deck_editor(frame, 41, 15))
        config, message = auto_calibrate({"SPEED_PROFILE": "slow"}, TESSERACT, frame=frame, sct=screen)
        self.assertIsNotNone(config, message)
        self.assertEqual(config["SEARCH_BAR"], list(frame.point(*md_layout.REFERENCE_POINTS["SEARCH_BAR"])))
        self.assertIn("DECK_COUNT", config)
        self.assertEqual(config["SPEED_PROFILE"], "slow")  # übrige Einstellungen bleiben

    def test_refuses_without_deck_editor(self):
        frame = md_layout.Frame(0, 0, 1920, 1080)
        screen = FakeScreen(Image.new("RGB", (1920, 1080), BACKGROUND))
        config, message = auto_calibrate({}, TESSERACT, frame=frame, sct=screen)
        self.assertIsNone(config)
        self.assertIn("Deck-Editor", message)

    def test_refuses_other_aspect_ratio(self):
        config, message = auto_calibrate({}, TESSERACT, frame=md_layout.Frame(0, 0, 3440, 1440), sct=object())
        self.assertIsNone(config)
        self.assertIn("16:9", message)


class ExportFlowTest(unittest.TestCase):
    def test_reads_every_slot_and_builds_ydk(self):
        db = card_db.CardDB([("1", "Bonfire", "spell"), ("2", "Ash Blossom & Joyous Spring", "effect"),
                             ("6", "Cyber Dragon Infinity", "xyz")])
        texts = iter(["bonfire", "bonfire", "ashblossomjoyousspring", "cyberdragoninfinity", "xxxx"])
        exporter = deck_export.DeckExporter({}, "", lambda *a: None, lambda **k: None,
                                            frame=md_layout.Frame(0, 0, 1920, 1080))
        exporter.reader._read_slot = lambda sct, automator, monitor, x, y, prev: ("raw", next(texts))
        exporter.reader._capture_and_ocr_slot = lambda sct, monitor: ("", "")
        zones = [("Main", md_layout.deck_slot_positions(3, 267, 5), 10),
                 ("Extra", md_layout.deck_slot_positions(2, 845, 2), 10)]
        cards = exporter._read_cards(None, exporter.frame, mock.Mock(), db, zones)
        self.assertEqual([c.match.cid for c in cards], ["1", "1", "2", "6", None])
        ids = [c.match.cid for c in cards]
        self.assertEqual(deck_export.build_ydk(ids[:3], [i for i in ids[3:] if i]).count("\n"), 8)

    def test_countdown_before_reading_then_timer_starts(self):
        events = []
        exporter = deck_export.DeckExporter({}, "", lambda text, color="white": events.append(text),
                                            lambda **k: None, frame=md_layout.Frame(0, 0, 1920, 1080),
                                            start_callback=lambda: events.append("TIMER"))
        zones = [("Main", [], 10), ("Extra", [], 10)]
        with mock.patch.object(deck_export.vision_engine, "check_tesseract", return_value=None), \
                mock.patch.object(deck_export, "load_card_db", return_value=None), \
                mock.patch.object(deck_export.mss, "MSS"), \
                mock.patch.object(deck_export, "WindowAutomator"), \
                mock.patch.object(deck_export, "focus_master_duel"), \
                mock.patch.object(deck_export.pyperclip, "copy"), \
                mock.patch.object(deck_export.time, "sleep"), \
                mock.patch.object(deck_export.win_api, "get_cursor_pos", return_value=(0, 0)), \
                mock.patch.object(deck_export.win_api, "set_cursor_pos",
                                  side_effect=lambda x, y: events.append(("MAUS", x, y))), \
                mock.patch.object(exporter, "_plan", side_effect=lambda *a: events.append("PLAN") or zones), \
                mock.patch.object(exporter, "_read_cards", side_effect=lambda *a: events.append("READ") or []):
            exporter._run()
        start = events.index("Maus loslassen! (3s)")
        park = ("MAUS", *md_layout.PARK_POINT)  # Maus neben dem Deck (1920×1080 = Referenz)
        self.assertEqual(events[start:start + 8], ["Maus loslassen! (3s)", "Maus loslassen! (2s)",
                                                   "Maus loslassen! (1s)", "TIMER", park, "PLAN", "READ", park])

    def test_unchanged_deck_is_exported_from_last_scan(self):
        cards = [deck_export.ExportedCard("Main", 1, "bonfire", card_db.CardMatch("1", "Bonfire", True)),
                 deck_export.ExportedCard("Extra", 1, "cdi", card_db.CardMatch("6", "Cyber Dragon Infinity", True))]
        zones = [("Main", md_layout.deck_slot_positions(1, 267, 5), 10),
                 ("Extra", md_layout.deck_slot_positions(1, 845, 2), 10)]
        scan = deck_export.DeckScan(md_layout.Frame(0, 0, 1920, 1080), zones, cards, {})
        exporter = deck_export.DeckExporter({}, "", lambda *a: None, lambda **k: None, scan=scan)
        with mock.patch.object(exporter, "scan", side_effect=AssertionError("darf nicht neu lesen")), \
                mock.patch.object(deck_export.pyperclip, "copy") as copy:
            result = exporter._run()
        self.assertTrue(result.reused)
        self.assertIs(result.scan, scan)
        self.assertEqual(result.ydk, deck_export.build_ydk(["1"], ["6"]))
        copy.assert_called_once_with(result.ydk)


if __name__ == "__main__":
    unittest.main()
