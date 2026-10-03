"""
Ist in Master Duel gerade der Deck-Editor offen (Menü "Deck" oder im Duell "Edit Deck")?

Das Overlay soll nur dort zu sehen sein. Erkannt wird der Editor wie bei der automatischen Kalibrierung an der
Überschrift "Main Deck" über dem Deck (Texterkennung) – für beide Lesemethoden gleich, ohne Zugriff aufs Spiel.
Damit das im Hintergrund kaum Rechenzeit kostet, merkt sich der Wächter, wie der Ausschnitt aussah
(kleines Graustufen-Abbild): Sieht er aus wie ein schon gelesener, gilt dessen Ergebnis; gelesen wird nur bei
einem neuen Bild und höchstens alle OCR_INTERVAL Sekunden. Erst nach NEGATIVE_CONFIRM Treffern "kein Editor"
hintereinander wird ausgeblendet (kurze Störungen wie Einblendungen lassen das Overlay nicht flackern).
"""

import threading
import time
from typing import Callable, List, Optional, Tuple

import mss
from PIL import Image

import md_layout
from auto_calibration import deck_editor_visible

CHECK_INTERVAL = 0.4    # Sekunden zwischen zwei Prüfungen
OCR_INTERVAL = 1.0      # höchstens so oft Texterkennung (nur bei neuem Bild)
SIGNATURE_SIZE = (48, 12)
SAME_IMAGE = 6.0        # mittlere Abweichung (0–255), bis zu der zwei Ausschnitte als gleich gelten
CACHE_SIZE = 12
NEGATIVE_CONFIRM = 2


def header_signature(image: Image.Image) -> bytes:
    return image.convert("L").resize(SIGNATURE_SIZE).tobytes()


def signature_distance(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / max(1, len(a))


class EditorDetector:
    """Entscheidet aus dem Ausschnitt über dem Deck. Ohne Spiel/Bildschirm testbar (grab/read austauschbar)."""

    def __init__(self, read_text: Callable[[], bool], clock: Callable[[], float] = time.monotonic):
        self.read_text = read_text            # Texterkennung: steht dort "…deck"?
        self.clock = clock
        self.cache: List[Tuple[bytes, bool]] = []
        self.visible: Optional[bool] = None   # None = noch unbekannt (bzw. nicht prüfbar) → Overlay zeigen
        self._negatives = 0
        self._last_ocr = -OCR_INTERVAL

    def update(self, image: Optional[Image.Image]) -> Optional[bool]:
        """Neues Bild des Ausschnitts (None = nicht prüfbar, z.B. kein 16:9). Returns: Editor sichtbar?"""
        if image is None:
            self.visible, self._negatives = None, 0
            return None
        signature = header_signature(image)
        known = next((result for sig, result in self.cache if signature_distance(sig, signature) <= SAME_IMAGE),
                     None)
        if known is None:
            if self.clock() - self._last_ocr < OCR_INTERVAL:
                return self.visible  # neues Bild, aber gerade erst gelesen → beim nächsten Mal
            self._last_ocr = self.clock()
            try:
                known = bool(self.read_text())
            except Exception:  # Texterkennung nicht verfügbar → nicht ausblenden
                self.visible, self._negatives = None, 0
                return None
            self.cache = [(signature, known)] + self.cache[:CACHE_SIZE - 1]
        if known:
            self.visible, self._negatives = True, 0
        else:
            self._negatives += 1
            if self._negatives >= NEGATIVE_CONFIRM or self.visible is None:
                self.visible = False
        return self.visible


class EditorWatcher:
    """
    Hintergrund-Thread: prüft, solange Master Duel vorne ist (md_front) und nichts darüber liegt (paused),
    ob der Deck-Editor zu sehen ist. Beide Schalter setzt das Overlay. Bewusst ohne Verweis aufs Overlay:
    Sonst könnte dessen Tk-Fenster beim Beenden in diesem Thread aufgeräumt werden (Tk-Absturz).
    """

    def __init__(self, tesseract_cmd: str):
        self.tesseract_cmd = tesseract_cmd
        self.md_front = False   # Master Duel ist das Fenster vorne
        self.paused = False     # z.B. Mouseover-Fenster des Deck-Fensters offen (kann über der Überschrift liegen)
        self.detector: Optional[EditorDetector] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def visible(self) -> Optional[bool]:
        return self.detector.visible if self.detector else None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        with mss.MSS() as sct:  # mss-Instanzen gehören zu ihrem Thread
            state = {"frame": None}

            def read_text() -> bool:
                return deck_editor_visible(sct, state["frame"], self.tesseract_cmd)

            self.detector = EditorDetector(read_text)
            while not self._stop.wait(CHECK_INTERVAL):
                try:
                    if not self.md_front or self.paused:
                        continue
                    frame = md_layout.md_frame()
                    state["frame"] = frame
                    image = None
                    if frame is not None and frame.is_16_9:
                        shot = sct.grab(frame.region(*md_layout.MAIN_HEADER_REGION))
                        image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
                    self.detector.update(image)
                except Exception:  # Fenster gerade zu o.Ä. → beim nächsten Mal
                    continue
