"""
App-Icon: zwei aufgefächerte Spielkarten im Stil einer Monsterkarte (oranger Rahmen,
dunkles Bildfeld, goldener Level-Stern). Eigene Zeichnung, kein Konami-Logo.
Wird zur Laufzeit für das Tray-Icon gezeichnet; `python app_icon.py` erzeugt zusätzlich
assets/app_icon.ico für die .exe (PyInstaller).
"""

import math
import os

from PIL import Image, ImageDraw

BASE = 256
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

FRAME = (120, 70, 25, 255)        # dunkler Goldrand
CARD = (214, 120, 50, 255)        # Orange wie eine Effektmonster-Karte
NAME_BAR = (245, 228, 190, 255)   # helle Namensleiste
ART = (28, 52, 105, 255)          # dunkelblaues Bildfeld
STAR = (255, 205, 40, 255)        # Level-Stern
STAR_EDGE = (150, 90, 10, 255)
BACK_CARD = (150, 75, 35, 255)    # Karte dahinter, dunkler


def _star(cx: float, cy: float, outer: float, inner: float):
    points = []
    for i in range(10):
        radius = outer if i % 2 == 0 else inner
        angle = math.radians(-90 + i * 36)
        points.append((cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
    return points


def _card(width: int, height: int, body, with_face: bool) -> Image.Image:
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    radius = width // 10
    draw.rounded_rectangle([0, 0, width - 1, height - 1], radius=radius, fill=FRAME)
    border = max(4, width // 14)
    draw.rounded_rectangle([border, border, width - 1 - border, height - 1 - border],
                           radius=radius - border // 2, fill=body)
    if with_face:
        pad = border * 2
        bar_h = height // 8
        draw.rectangle([pad, pad, width - 1 - pad, pad + bar_h], fill=NAME_BAR)
        art_top = pad + bar_h + border
        art_bottom = height - pad - border
        draw.rectangle([pad, art_top, width - 1 - pad, art_bottom], fill=ART)
        cx, cy = width / 2, (art_top + art_bottom) / 2
        outer = min(width - 2 * pad, art_bottom - art_top) * 0.42
        draw.polygon(_star(cx, cy, outer + 4, outer * 0.42 + 2), fill=STAR_EDGE)
        draw.polygon(_star(cx, cy, outer, outer * 0.42), fill=STAR)
    return layer


def create_icon_image(size: int = BASE) -> Image.Image:
    """Icon als RGBA-Bild in beliebiger Größe (Zeichnung in 256 px, dann verkleinert)."""
    canvas = Image.new("RGBA", (BASE, BASE), (0, 0, 0, 0))
    card_w, card_h = 150, 210

    back = _card(card_w, card_h, BACK_CARD, with_face=False).rotate(14, resample=Image.BICUBIC, expand=True)
    canvas.alpha_composite(back, (BASE - back.width - 6, (BASE - back.height) // 2 - 4))

    front = _card(card_w, card_h, CARD, with_face=True).rotate(-8, resample=Image.BICUBIC, expand=True)
    canvas.alpha_composite(front, (6, (BASE - front.height) // 2 + 6))

    return canvas if size == BASE else canvas.resize((size, size), Image.LANCZOS)


def save_ico(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    create_icon_image().save(path, format="ICO", sizes=ICO_SIZES)


if __name__ == "__main__":
    target = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "app_icon.ico")
    save_ico(target)
    create_icon_image().save(target.replace(".ico", ".png"))
    print(f"Gespeichert: {target}")
