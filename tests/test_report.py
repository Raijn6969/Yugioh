"""Abschluss-Report und Kalibrierung abbrechen."""

import tkinter as tk
import unittest
from collections import Counter
from unittest import mock

import import_engine as ie
from calibration import CalibrationWizard
from tests.fakes import make_core
from utils import clean_text


def added(name, amount, fallback=False, stall=False):
    return {"expected_clean": clean_text(name), "expected_raw": name, "actual_ocr": name,
            "amount": amount, "is_fallback": fallback, "stall_suspect": stall}


class FinalAuditTest(unittest.TestCase):
    def audit(self, counts, names, items):
        core = make_core()
        lines = []
        with mock.patch.object(ie, "dlog", lines.append):
            has_errors, popup = core._write_final_audit(Counter(counts), names, items)
        return has_errors, popup, "\n".join(lines)

    def test_every_card_is_logged(self):
        has_errors, popup, log = self.audit({"1": 3, "2": 1}, {"1": "Ash Blossom", "2": "Maxx C"},
                                            [added("Ash Blossom", 3), added("Maxx C", 1, fallback=True)])
        self.assertEqual((has_errors, popup), (False, []))
        self.assertIn("[OK]", log)
        self.assertIn("[FALLBACK]", log)

    def test_alt_art_ids_are_counted_together(self):
        # Zwei IDs, gleicher Name (Alternativ-Artwork) → im Spiel dieselbe Karte
        has_errors, popup, _ = self.audit({"46986414": 2, "38033121": 1},
                                          {"46986414": "Dark Magician", "38033121": "Dark Magician"},
                                          [added("Dark Magician", 2), added("Dark Magician", 1)])
        self.assertEqual((has_errors, popup), (False, []))

    def test_too_many_and_missing(self):
        has_errors, popup, log = self.audit({"1": 1, "2": 2, "3": 1},
                                            {"1": "Clockwork Knight", "2": "Clockwork Night"},
                                            [added("Clockwork Knight", 2), added("Clockwork Night", 1)])
        self.assertTrue(has_errors)
        self.assertEqual(popup, ["Clockwork Knight (1x zu viel – bitte entfernen)",
                                 "Clockwork Night (1x fehlend)",
                                 "Unbekannte ID 3 (1x fehlend)"])
        self.assertIn("[ZU VIELE]", log)


class CalibrationCancelTest(unittest.TestCase):
    def test_cancel_keeps_old_points(self):
        root = tk.Tk()
        root.withdraw()
        try:
            config = {"SEARCH_BAR": [1, 2], "DECK_COUNT": [5, 6]}
            wizard = CalibrationWizard(root, config, lambda c: None, lambda: None)
            with mock.patch("calibration.pyautogui.position", return_value=(999, 999)):
                wizard.capture_point()   # Suchleiste neu erfasst …
            wizard.cancel_wizard()       # … dann abgebrochen
            self.assertEqual(config, {"SEARCH_BAR": [1, 2], "DECK_COUNT": [5, 6]})
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
