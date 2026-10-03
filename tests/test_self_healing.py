"""Start-Prüfung, automatische Tempo-Anpassung, Fortsetzen-Speicher, Deck-Zählung."""

import itertools
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


class PauseAfterInsertTest(unittest.TestCase):
    def pause(self, confirmed, speed="normal"):
        core = make_core(speed)
        core._insert_confirmed = confirmed
        slept = []
        with mock.patch.object(ie.time, "sleep", slept.append):
            core._pause_after_insert()
        return round(slept[0], 4)

    def test_shorter_only_when_deck_count_confirmed(self):
        self.assertEqual(self.pause(False), ie.POST_ADD_PAUSE)
        self.assertEqual(self.pause(True), ie.POST_ADD_PAUSE_CONFIRMED)
        self.assertEqual(self.pause(True, "slow"), round(ie.POST_ADD_PAUSE_CONFIRMED * 1.5, 4))


class LagDetectionTest(unittest.TestCase):
    def read_twice(self, first, second):
        core = make_core()
        reads = iter([("", first), ("", second)])
        core._capture_and_ocr_slot = lambda sct, monitor: next(reads)
        automator = mock.Mock()
        with mock.patch.object(ie.time, "sleep"):
            _, s_c = core._read_slot(None, automator, {}, 0, 0, prev_text=first)
        return s_c, core._lag_events

    def test_other_card_after_reclick_is_lag(self):
        self.assertEqual(self.read_twice("bonfire", "seventhtachyon"), ("seventhtachyon", 1))

    def test_same_card_read_slightly_differently_is_no_lag(self):
        # Echter Fehlalarm: ein zusätzliches 't' von der Texterkennung
        self.assertEqual(self.read_twice("tearlamentskashtira", "ttearlamentskashtira"),
                         ("tearlamentskashtira", 0))


class FocusTest(unittest.TestCase):
    def setUp(self):
        import input_utils
        self.iu = input_utils
        self.patches = [mock.patch("window_automation.find_md_window", return_value=42),
                        mock.patch.object(input_utils.win_api, "window_pid", lambda hwnd: {42: 100}.get(hwnd, 200)),
                        mock.patch.object(input_utils.time, "sleep")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_switches_only_when_master_duel_is_not_in_front(self):
        with mock.patch.object(self.iu.win_api, "set_foreground_window") as set_fg:
            with mock.patch.object(self.iu.win_api, "get_foreground_window", return_value=42):
                self.iu.focus_master_duel()
            set_fg.assert_not_called()
            with mock.patch.object(self.iu.win_api, "get_foreground_window", side_effect=[7, 42]):
                self.iu.focus_master_duel()
            set_fg.assert_called_once_with(42)

    def test_stops_when_master_duel_does_not_come_to_front(self):
        with mock.patch.object(self.iu.win_api, "set_foreground_window") as set_fg, \
                mock.patch.object(self.iu.win_api, "get_foreground_window", return_value=7), \
                mock.patch.object(self.iu, "FOCUS_TIMEOUT", 0.05):
            with self.assertRaises(self.iu.FocusLostError):
                self.iu.focus_master_duel()
        set_fg.assert_called_with(42)

    def test_other_window_of_master_duel_counts(self):
        with mock.patch.object(self.iu.win_api, "get_foreground_window", return_value=43), \
                mock.patch.object(self.iu.win_api, "window_pid", lambda hwnd: 100):  # gleicher Prozess
            self.assertTrue(self.iu.md_in_front(42))
        with mock.patch.object(self.iu.win_api, "get_foreground_window", return_value=43):
            self.assertFalse(self.iu.md_in_front(42))  # fremder Prozess

    def test_no_shortcuts_when_a_window_jumps_in_front_of_the_search_bar(self):
        automator = mock.Mock()
        # Vor dem Klick ist Master Duel vorne, danach ein anderes Fenster (z.B. Benachrichtigung)
        front = itertools.chain([42], itertools.repeat(7))
        with mock.patch.object(self.iu.win_api, "get_foreground_window", lambda: next(front)), \
                mock.patch.object(self.iu.win_api, "set_foreground_window"), \
                mock.patch.object(self.iu, "FOCUS_TIMEOUT", 0.05), \
                mock.patch.object(self.iu, "pyperclip"), \
                mock.patch.object(self.iu.pyautogui, "hotkey") as hotkey:
            with self.assertRaises(self.iu.FocusLostError):
                self.iu.type_card_name(automator, "bonfire", {"SEARCH_BAR": [10, 20]})
        automator.iron_grip_click.assert_called_once_with(10, 20)
        hotkey.assert_not_called()


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


class SlowDeckCountTest(unittest.TestCase):
    """Schwacher PC: Die Deck-Zahl springt erst verspätet hoch."""

    def verify(self, readings):
        core = make_core()
        core.card_types = {"1": "Spell Card"}
        values = iter(readings)
        core.deck_counter = mock.Mock(value=20, wait_for=lambda expected, timeout: next(values))
        automator = mock.Mock()
        unsure = core._verify_insert(automator, 0, 0, 1, "Karte", "1", stalled=False)
        return unsure, automator.add_card_to_deck.call_count

    def test_late_count_does_not_click_an_extra_copy(self):
        self.assertEqual(self.verify([20, 21]), (False, 0))

    def test_really_missing_copy_is_clicked_again(self):
        self.assertEqual(self.verify([20, 20, 21]), (False, 1))


class HangingOcrTest(unittest.TestCase):
    def test_hanging_tesseract_counts_as_empty_read(self):
        import vision_engine
        from PIL import Image
        timeout = RuntimeError("Tesseract process timeout")
        with mock.patch.object(vision_engine.pytesseract, "image_to_string", side_effect=timeout) as ocr:
            self.assertEqual(vision_engine.do_ocr(Image.new("L", (100, 20)), "tesseract.exe"), "")
        self.assertEqual(ocr.call_args.kwargs["timeout"], vision_engine.OCR_TIMEOUT)


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
