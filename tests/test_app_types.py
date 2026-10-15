"""Tests für app_types und validate_config."""

import unittest
from app_types import validate_config


class TestAppTypes(unittest.TestCase):
    def test_valid_config_passes_without_warnings(self):
        cfg = {
            "SPEED_PROFILE": "fast",
            "READ_METHOD": "memory",
            "SEARCH_BAR": [1400, 200],
            "FIRST_CARD": [1300, 300],
            "TRASH_BTN": [1200, 100],
            "TRASH_CONFIRM": [1100, 600],
            "UNOWNED_BTN": [1700, 200]
        }
        cleaned, warnings = validate_config(cfg)
        self.assertEqual(len(warnings), 0)
        self.assertEqual(cleaned["SPEED_PROFILE"], "fast")
        self.assertEqual(cleaned["READ_METHOD"], "memory")

    def test_invalid_speed_profile_fallback(self):
        cfg = {"SPEED_PROFILE": "turbo_ultra"}
        cleaned, warnings = validate_config(cfg)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(cleaned["SPEED_PROFILE"], "normal")

    def test_invalid_read_method_fallback(self):
        cfg = {"READ_METHOD": "telepathy"}
        cleaned, warnings = validate_config(cfg)
        self.assertEqual(len(warnings), 1)
        self.assertEqual(cleaned["READ_METHOD"], "memory")

    def test_malformed_coordinates_warning(self):
        cfg = {"SEARCH_BAR": "not_a_coordinate"}
        cleaned, warnings = validate_config(cfg)
        self.assertEqual(len(warnings), 1)
        self.assertIn("SEARCH_BAR", warnings[0])


if __name__ == "__main__":
    unittest.main()
