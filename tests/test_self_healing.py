"""Start-Prüfung, automatische Tempo-Anpassung, Fortsetzen-Speicher, Deck-Zählung."""

import os
import tempfile
import unittest
from unittest import mock

import import_engine as ie
import resume_state
from deck_counter import DeckCounter
from tests.fakes import make_core

GOOD_CONFIG = {"SEARCH_BAR": [1404, 245], "FIRST_CARD": [1390, 400], "TRASH_BTN": [1246, 127],
               "TRASH_CONFIRM": [1167, 665], "UNOWNED_BTN": [1786, 209],
               "CALIBRATED_SIZE": [1920, 1080]}


class PreflightTest(unittest.TestCase):
    def preflight(self, config=None, tesseract=None, size=(1920, 1080), screen=(0, 0, 1920, 1080)):
        core = make_core()
        core.config = dict(GOOD_CONFIG if config is None else config)
        with mock.patch.object(ie.vision_engine, "check_tesseract", return_value=tesseract), \
                mock.patch.object(ie, "get_md_window_size", return_value=size), \
                mock.patch.object(ie.win_api, "virtual_screen_rect", return_value=screen):
            return core._preflight(), core

    def test_all_good(self):
        problem, _ = self.preflight()
        self.assertIsNone(problem)

    def test_tesseract_missing(self):
        problem, _ = self.preflight(tesseract="Texterkennung fehlt: X")
        self.assertEqual(problem, "Texterkennung fehlt: X")

    def test_master_duel_not_running(self):
        problem, _ = self.preflight(size=None)
        self.assertIn("Master Duel wurde nicht gefunden", problem)

    def test_resolution_changed(self):
        problem, _ = self.preflight(size=(2560, 1440))
        self.assertIn("1920×1080 → 2560×1440", problem)

    def test_old_config_remembers_size(self):
        config = {k: v for k, v in GOOD_CONFIG.items() if k != "CALIBRATED_SIZE"}
        problem, core = self.preflight(config=config, size=(1280, 720))
        self.assertIsNone(problem)
        self.assertEqual(core.config["CALIBRATED_SIZE"], [1280, 720])

    def test_calibration_point_off_screen(self):
        config = dict(GOOD_CONFIG, SEARCH_BAR=[3000, 245])  # zweiter Monitor abgesteckt
        problem, _ = self.preflight(config=config)
        self.assertIn("SEARCH_BAR", problem)


class AdaptiveTempoTest(unittest.TestCase):
    def test_slows_down_step_by_step(self):
        core = make_core("fast")
        steps = [core.speed_mult]
        for _ in range(8):
            core._report_trouble("Test")
            steps.append(core.speed_mult)
        # alle 2 Probleme eine Stufe langsamer, bis maximal ×2.0
        self.assertEqual(steps, [0.85, 0.85, 1.0, 1.0, 1.5, 1.5, 2.0, 2.0, 2.0])
        self.assertTrue(core._auto_slowed)

    def test_single_hiccup_does_not_slow_down(self):
        core = make_core("normal")
        core._report_trouble("Test")
        self.assertEqual(core.speed_mult, 1.0)
        self.assertFalse(core._auto_slowed)


class ResumeStateTest(unittest.TestCase):
    def setUp(self):
        self.path = os.path.join(tempfile.mkdtemp(), "md_resume.json")

    def test_roundtrip_same_deck_any_order(self):
        resume_state.save_progress(["1", "2", "2", "3"], {"1": 1}, self.path)
        data = resume_state.load_progress(["2", "3", "1", "2"], self.path)
        self.assertEqual(data["done"], {"1": 1})

    def test_other_deck_is_ignored(self):
        resume_state.save_progress(["1", "2"], {"1": 1}, self.path)
        self.assertIsNone(resume_state.load_progress(["1", "2", "2"], self.path))

    def test_clear_and_missing_file(self):
        resume_state.save_progress(["1"], {"1": 1}, self.path)
        resume_state.clear_progress(self.path)
        resume_state.clear_progress(self.path)  # zweimal löschen ist ok
        self.assertIsNone(resume_state.load_progress(["1"], self.path))


class DeckCounterTest(unittest.TestCase):
    def test_disabled_without_calibration(self):
        counter = DeckCounter(sct=None, config={}, tesseract_cmd="")
        self.assertFalse(counter.enabled)
        self.assertIsNone(counter.read())

    def test_region_centered_on_calibration_point(self):
        counter = DeckCounter(sct=None, config={"DECK_COUNT": [500, 100]}, tesseract_cmd="")
        self.assertEqual(counter.region, {"left": 465, "top": 83, "width": 70, "height": 34})

    def test_reads_real_digits(self):
        # Echte Texterkennung auf einem gemalten "Main Deck 37"-Ausschnitt
        from PIL import Image, ImageDraw, ImageFont
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        cmd = os.path.join(root, "Tesseract-OCR", "tesseract.exe")
        if not os.path.exists(cmd):
            self.skipTest("Tesseract nicht vorhanden")
        image = Image.new("RGB", (70, 34), (20, 20, 30))
        ImageDraw.Draw(image).text((14, 2), "37", fill=(240, 240, 240), font=ImageFont.truetype("arialbd.ttf", 26))

        class FakeShot:
            size = image.size
            bgra = image.convert("RGBA").tobytes("raw", "BGRA")

        sct = mock.Mock()
        sct.grab.return_value = FakeShot()
        counter = DeckCounter(sct=sct, config={"DECK_COUNT": [500, 100]}, tesseract_cmd=cmd)
        self.assertEqual(counter.read(), 37)


if __name__ == "__main__":
    unittest.main()
