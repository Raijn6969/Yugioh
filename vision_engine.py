"""
VISION ENGINE
Bildvorverarbeitung und Tesseract-Schnittstelle: entfernt Glitzereffekte (Holo-Karten),
vergrößert die Schrift und erhöht den Kontrast bei schwierigen Hintergründen (z.B. Synchro/Link).
"""

import os
from typing import Optional

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
import pytesseract
from PIL import Image

from debug_log import clear_changed, dlog_changed

# Erlaubte Zeichen für Tesseract (Kartennamen). Für Deutsch kommen Umlaute und ß hinzu.
_WHITELIST = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-':&,.!?/@"
_WHITELIST_DE = _WHITELIST + "äöüÄÖÜß"
DIGITS = "0123456789"
OCR_TIMEOUT = 8  # Sekunden; normal dauert ein Aufruf ~0,1 s


class TimedOut(str):
    """Leeres Ergebnis nach einem Timeout: utils.get_cached_ocr merkt es sich nicht (sonst bliebe das Bild leer)."""
    timed_out = True


def check_tesseract(tesseract_cmd: str, lang: str = "eng") -> Optional[str]:
    """Prüft, ob Tesseract samt Sprachdaten lauffähig ist. Returns: Fehlermeldung oder None."""
    if not os.path.exists(tesseract_cmd):
        return f"Texterkennung fehlt: {tesseract_cmd} wurde nicht gefunden."
    tessdata = os.path.join(os.path.dirname(tesseract_cmd), "tessdata", f"{lang}.traineddata")
    if not os.path.exists(tessdata):
        return f"Sprachdaten für die Texterkennung fehlen: {tessdata}"
    try:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        pytesseract.get_tesseract_version()
    except Exception as e:
        return f"Texterkennung (Tesseract) startet nicht: {e}"
    return None


def _kubisch(a: np.ndarray, faktor: float) -> np.ndarray:
    """Bikubisch vergrößern mit a = -0,75 wie OpenCV (Pillow nimmt -0,5, das gäbe andere Kantenpixel)."""
    k = -0.75
    for achse in (0, 1):
        n_neu = int(a.shape[achse] * faktor)
        pos = (np.arange(n_neu) + 0.5) * a.shape[achse] / n_neu - 0.5
        i0 = np.floor(pos).astype(int)
        t = pos - i0
        gewichte = np.stack([((k * (t + 1) - 5 * k) * (t + 1) + 8 * k) * (t + 1) - 4 * k,
                             ((k + 2) * t - (k + 3)) * t * t + 1,
                             ((k + 2) * (1 - t) - (k + 3)) * (1 - t) * (1 - t) + 1,
                             ((k * (2 - t) - 5 * k) * (2 - t) + 8 * k) * (2 - t) - 4 * k], axis=1).astype(np.float32)
        index = np.clip(i0[:, None] + np.arange(-1, 3), 0, a.shape[achse] - 1)
        a = a.astype(np.float32)
        a = (np.einsum("ik,ikj->ij", gewichte, a[index]) if achse == 0
             else np.einsum("jk,ijk->ij", gewichte, a[:, index]))
    return a.clip(0, 255).round().astype(np.uint8)


def _median3(a: np.ndarray) -> np.ndarray:
    """Median 3x3 (Rand wiederholt) per Sortiernetz aus min/max: schneller als Pillows MedianFilter."""
    h, w = a.shape
    p = np.pad(a, 1, mode="edge")
    v = [p[y:y + h, x:x + w] for y in range(3) for x in range(3)]
    for i, j in ((1, 2), (4, 5), (7, 8), (0, 1), (3, 4), (6, 7), (1, 2), (4, 5), (7, 8), (0, 3), (5, 8), (4, 7),
                 (3, 6), (1, 4), (2, 5), (4, 7), (4, 2), (6, 4), (4, 2)):
        v[i], v[j] = np.minimum(v[i], v[j]), np.maximum(v[i], v[j])
    return v[4]


def _vorverarbeiten(image: Image.Image) -> Image.Image:
    """Graustufen, 250 % vergrößern (Kanten weich), Median 3x3 gegen Holo-Glitzern, adaptive Binarisierung
    (gaußgewichteter Mittelwert 31x31 minus 2; passt sich der lokalen Helligkeit an, wichtig für Synchro/Link)."""
    gray = image.convert("L")
    gray = _median3(_kubisch(np.asarray(gray), 2.5))
    # Gauß-Kern wie OpenCV (Fenster 31, sigma = 0,3*((31-1)*0,5-1)+0,8 = 5), Rand wird wiederholt
    x = np.arange(-15, 16)
    kern = np.exp(-x * x / (2 * 5.0 ** 2))
    kern = (kern / kern.sum()).astype(np.float32)
    a = np.pad(gray.astype(np.float32), 15, mode="edge")
    a = sliding_window_view(a, 31, axis=1) @ kern
    mean = sliding_window_view(a, 31, axis=0) @ kern
    return Image.fromarray(np.where(gray > np.round(mean) - 2, 255, 0).astype(np.uint8))


def do_ocr(image: Image.Image, tesseract_cmd: str, lang: str = "eng", whitelist: Optional[str] = None) -> str:
    # Setze den Pfad zu Tesseract
    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    thresh = _vorverarbeiten(image)

    if whitelist is None:
        whitelist = _WHITELIST_DE if "deu" in lang else _WHITELIST
    custom_config = f"--oem 3 --psm 7 -c tessedit_char_whitelist={whitelist}"

    # Den aufbereiteten Scan an Tesseract übergeben. Hängt Tesseract (PC völlig
    # ausgelastet, Virenscanner), zählt der Read als leer statt den Import einzufrieren.
    try:
        text = pytesseract.image_to_string(thresh, lang=lang, config=custom_config, timeout=OCR_TIMEOUT)
    except RuntimeError as e:
        if "timeout" not in str(e).lower():
            raise
        # Nur einmal je Hänger loggen: Der Editor-Wächter liest sonst bei dauerhaft hängendem Tesseract im Takt
        dlog_changed("ocr-timeout", f"[OCR] Texterkennung brauchte länger als {OCR_TIMEOUT}s → als leer gewertet.")
        return TimedOut("")
    clear_changed("ocr-timeout")
    return text


def read_screen_text(sct, monitor: dict, tesseract_cmd: str, lang: str = "eng",
                     whitelist: Optional[str] = None) -> str:
    """Helle Schrift auf dunklem Grund (Master-Duel-Oberfläche) aus einem Bildschirmausschnitt lesen."""
    shot = sct.grab(monitor)
    gray = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX").convert("L")
    inverted = Image.eval(gray, lambda p: 255 - p)
    timed_out = False
    for image in (inverted.point(lambda p: 0 if p < 150 else 255), inverted):
        text = do_ocr(image, tesseract_cmd, lang, whitelist)
        if text.strip():
            return text
        timed_out = timed_out or getattr(text, "timed_out", False)
    return TimedOut("") if timed_out else ""
