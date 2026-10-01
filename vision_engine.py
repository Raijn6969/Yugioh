"""
VISION ENGINE
Bildvorverarbeitung und Tesseract-Schnittstelle: entfernt Glitzereffekte (Holo-Karten),
vergrößert die Schrift und erhöht den Kontrast bei schwierigen Hintergründen (z.B. Synchro/Link).
"""

import os
from typing import Optional

import cv2
import numpy as np
import pytesseract
from PIL import Image

# Erlaubte Zeichen für Tesseract (Kartennamen). Für Deutsch kommen Umlaute und ß hinzu.
_WHITELIST = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-':&,.!?/@"
_WHITELIST_DE = _WHITELIST + "äöüÄÖÜß"
DIGITS = "0123456789"


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


def do_ocr(image: Image.Image, tesseract_cmd: str, lang: str = "eng", whitelist: Optional[str] = None) -> str:
    # Setze den Pfad zu Tesseract
    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    # 1. PIL Image zu einem OpenCV NumPy Array konvertieren
    open_cv_image = np.array(image)

    # Sicherstellen, dass wir im Graustufen-Modus sind
    if len(open_cv_image.shape) == 3:
        gray = cv2.cvtColor(open_cv_image, cv2.COLOR_RGB2GRAY)
    else:
        gray = open_cv_image

    # 2. UPSCALING: Das Bild um 250% vergrößern.
    # CUBIC-Interpolation hält die Kanten der Buchstaben beim Vergrößern weich.
    scale_percent = 250
    width = int(gray.shape[1] * scale_percent / 100)
    height = int(gray.shape[0] * scale_percent / 100)
    dim = (width, height)
    resized = cv2.resize(gray, dim, interpolation=cv2.INTER_CUBIC)

    # 3. ANTI-GLARE FILTER: Median Blur gegen das Holo-Glitzern.
    # Zerstört kleine Störpixel ("Salz und Pfeffer Rauschen"), lässt Kanten aber intakt.
    blurred = cv2.medianBlur(resized, 3)

    # 4. ADAPTIVE BINARISIERUNG: Der Gamechanger für Synchro/Link-Karten.
    # Passt den Schwarz/Weiß-Schwellenwert dynamisch an die lokale Helligkeit an.
    thresh = cv2.adaptiveThreshold(
        blurred, 255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31, 2
    )

    if whitelist is None:
        whitelist = _WHITELIST_DE if "deu" in lang else _WHITELIST
    custom_config = f"--oem 3 --psm 7 -c tessedit_char_whitelist={whitelist}"

    # Den aufbereiteten OpenCV-Scan an Tesseract übergeben
    raw_text = pytesseract.image_to_string(thresh, lang=lang, config=custom_config)

    return raw_text