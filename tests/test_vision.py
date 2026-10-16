import unittest

import numpy as np
from PIL import Image, ImageDraw

import vision_engine


class VorverarbeitenTest(unittest.TestCase):
    def test_groesse_und_schwarzweiss(self):
        bild = Image.new("RGB", (40, 12), (20, 20, 30))
        ImageDraw.Draw(bild).rectangle((5, 3, 30, 8), fill=(235, 235, 235))
        ergebnis = vision_engine._vorverarbeiten(bild)
        self.assertEqual(ergebnis.size, (100, 30))
        self.assertLessEqual(set(np.unique(ergebnis)), {0, 255})

    def test_kubisch_haelt_flaechen(self):
        flaeche = np.full((6, 8), 90, dtype=np.uint8)
        self.assertTrue((vision_engine._kubisch(flaeche, 2.5) == 90).all())


if __name__ == "__main__":
    unittest.main()
