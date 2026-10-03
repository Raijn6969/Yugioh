"""Overlay nur im Deck-Editor: Erkennung über die Überschrift "Main Deck" mit möglichst wenig Texterkennung."""

import unittest

from PIL import Image, ImageDraw

import editor_watch


def header(text: str, shade: int = 30) -> Image.Image:
    image = Image.new("RGB", (200, 50), (shade, shade, shade))
    ImageDraw.Draw(image).text((10, 15), text, fill=(240, 240, 240))
    return image


class EditorDetectorTest(unittest.TestCase):
    def setUp(self):
        self.now = [100.0]
        self.answers = []   # nächste Ergebnisse der Texterkennung
        self.reads = 0

        def read_text():
            self.reads += 1
            return self.answers.pop(0)

        self.detector = editor_watch.EditorDetector(read_text, clock=lambda: self.now[0])

    def tick(self, image, seconds=0.4):
        self.now[0] += seconds
        return self.detector.update(image)

    def test_editor_is_read_once_then_recognised_by_image(self):
        self.answers = [True]
        self.assertTrue(self.tick(header("Main Deck")))
        for _ in range(20):
            self.assertTrue(self.tick(header("Main Deck")))
        self.assertEqual(self.reads, 1)  # danach nur noch Bildvergleich

    def test_leaving_the_editor_hides_after_two_checks(self):
        self.answers = [True, False]
        self.tick(header("Main Deck"))
        self.assertTrue(self.tick(header("Duel Pass", shade=90), seconds=1.0))  # einmal "nein" → noch nicht weg
        self.assertFalse(self.tick(header("Duel Pass", shade=90)))  # bestätigt (aus dem Zwischenspeicher)
        self.assertEqual(self.reads, 2)
        self.assertTrue(self.tick(header("Main Deck")))  # zurück im Editor → sofort wieder da, ohne Lesen
        self.assertEqual(self.reads, 2)

    def test_start_outside_the_editor_hides_at_once(self):
        self.answers = [False]
        self.assertFalse(self.tick(header("Home")))

    def test_text_recognition_is_throttled_for_changing_screens(self):
        self.answers = [False] * 10
        for i in range(10):  # animierter Hintergrund: jedes Bild neu
            self.tick(header("Home", shade=20 * i), seconds=0.4)
        self.assertLessEqual(self.reads, 4)  # 4 s → höchstens etwa einmal pro Sekunde

    def test_not_checkable_means_show(self):
        self.answers = [False]
        self.tick(header("Home"))
        self.assertIsNone(self.tick(None))  # z.B. Master Duel nicht im 16:9-Format
        self.detector.read_text = lambda: (_ for _ in ()).throw(OSError("Tesseract fehlt"))
        self.assertIsNone(self.tick(header("Neu", shade=200), seconds=2))


class ShouldShowTest(unittest.TestCase):
    def decide(self, own, md, editor, running=False):
        fake = type("App", (), {})()
        fake.is_running = running
        fake.editor_watch = type("W", (), {"visible": editor})()
        import Overlay
        return Overlay.MasterDuelImporter._should_show(fake, own, md)

    def test_rules(self):
        self.assertTrue(self.decide(own=False, md=True, editor=True))
        self.assertFalse(self.decide(own=False, md=True, editor=False))   # Master Duel, aber nicht im Editor
        self.assertFalse(self.decide(own=False, md=False, editor=True))   # anderes Programm vorne
        self.assertTrue(self.decide(own=False, md=True, editor=None))     # unbekannt → zeigen
        self.assertTrue(self.decide(own=False, md=True, editor=False, running=True))  # Import läuft
        self.assertTrue(self.decide(own=True, md=False, editor=False))    # Importer selbst angeklickt


if __name__ == "__main__":
    unittest.main()
