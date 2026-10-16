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
        self.assertIsNone(self.tick(header("Main Deck")))  # neues Bild: erst sehen, ob es stillsteht
        self.assertTrue(self.tick(header("Main Deck")))
        for _ in range(20):
            self.assertTrue(self.tick(header("Main Deck")))
        self.assertEqual(self.reads, 1)  # danach nur noch Bildvergleich

    def test_leaving_the_editor_hides_after_two_checks(self):
        self.answers = [True, False]
        self.tick(header("Main Deck"))
        self.tick(header("Main Deck"))
        self.tick(header("Duel Pass", shade=90), seconds=1.0)  # neues Bild, noch nicht gelesen
        self.assertTrue(self.tick(header("Duel Pass", shade=90)))  # gelesen: einmal "nein" → noch nicht weg
        self.assertFalse(self.tick(header("Duel Pass", shade=90)))  # bestätigt (aus dem Zwischenspeicher)
        self.assertEqual(self.reads, 2)
        self.assertTrue(self.tick(header("Main Deck")))  # zurück im Editor → sofort wieder da, ohne Lesen
        self.assertEqual(self.reads, 2)

    def test_start_outside_the_editor_hides_at_once(self):
        self.answers = [False]
        self.tick(header("Home"))
        self.assertFalse(self.tick(header("Home")))

    def test_moving_image_is_never_read_but_counts_as_no_editor(self):
        self.answers = [True] * 10
        for i in range(30):  # Duell: jedes Bild anders
            self.tick(header("Duel", shade=8 * (i % 30)), seconds=0.4)
        self.assertEqual(self.reads, 0)
        self.assertFalse(self.detector.visible)

    def test_still_image_is_read_exactly_once(self):
        self.answers = [False]
        for _ in range(10):
            self.tick(header("Home"), seconds=0.4)
        self.assertEqual(self.reads, 1)

    def test_text_recognition_is_throttled_for_screens_that_keep_changing_and_stopping(self):
        self.answers = [False] * 10
        for i in range(10):  # je zwei gleiche Bilder hintereinander, dann ein neues
            self.tick(header("Home", shade=20 * (i // 2)), seconds=0.4)
        self.assertLessEqual(self.reads, 3)  # 4 s → höchstens etwa einmal pro Sekunde

    def test_not_checkable_means_show(self):
        self.answers = [False]
        self.tick(header("Home"))
        self.tick(header("Home"))
        self.assertIsNone(self.tick(None))  # z.B. Master Duel nicht im 16:9-Format
        self.detector.read_text = lambda: (_ for _ in ()).throw(OSError("Tesseract fehlt"))
        self.tick(header("Neu", shade=200), seconds=2)
        self.assertIsNone(self.tick(header("Neu", shade=200), seconds=2))


class GraceAfterFocusTest(unittest.TestCase):
    def test_transition_image_after_switching_back_is_ignored(self):
        watcher = editor_watch.EditorWatcher("tesseract")
        answers = [True, False]
        watcher.detector = editor_watch.EditorDetector(lambda: answers.pop(0), clock=lambda: now)
        now = 100.0
        watcher.md_front = True
        self.assertFalse(watcher._should_check(now))  # erste Sekunden nach dem Start: Schonzeit
        now += editor_watch.GRACE_AFTER_FOCUS
        self.assertTrue(watcher._should_check(now))
        watcher.detector.update(header("Main Deck"))
        self.assertTrue(watcher.detector.update(header("Main Deck")))
        watcher.md_front = False                       # Alt-Tab weg
        self.assertFalse(watcher._should_check(now + 60))
        now += 60
        watcher.md_front = True                        # zurück: Spiel zeichnet noch schwarz
        for _ in range(3):
            now += editor_watch.CHECK_INTERVAL
            if watcher._should_check(now):             # (so wie _run) nur dann das Bild prüfen
                watcher.detector.update(header("", shade=0))
        self.assertEqual(answers, [False])             # Übergangsbild wurde nicht gelesen
        self.assertTrue(watcher.visible)               # alter Wert bleibt, kein Ausblenden
        now += editor_watch.GRACE_AFTER_FOCUS
        self.assertTrue(watcher._should_check(now))    # danach normal
        watcher.paused = True
        self.assertFalse(watcher._should_check(now))


class ShouldShowTest(unittest.TestCase):
    def decide(self, own, md, editor, running=False, updating=False):
        fake = type("App", (), {})()
        fake.is_running = running
        fake._update_running = updating
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
        self.assertTrue(self.decide(own=False, md=False, editor=False, updating=True))  # Update lädt: Fortschritt zeigen


if __name__ == "__main__":
    unittest.main()
