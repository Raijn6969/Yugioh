"""
Abgerundete Buttons mit dünnem Goldrand (passend zu den Fensterrahmen).

Tk-Buttons können keine runden Ecken. Deshalb zeichnet Pillow den Button-Körper geglättet
(4-fach vergrößert, dann verkleinert) als Bild, und Tk legt den Text scharf darüber.
Verhält sich nach außen wie ein tk.Button: config(text=, bg=, fg=, state=, command=),
cget(...), invoke(). Mit menu=… öffnet ein Klick stattdessen ein Auswahlmenü (nach oben).
"""

import tkinter as tk
import tkinter.font as tkfont
from typing import Optional

from PIL import Image, ImageDraw, ImageTk

from window_style import GOLD

SUPERSAMPLE = 4  # Zeichen-Vergrößerung für glatte Rundungen


def _shade(hex_color: str, factor: float) -> str:
    """factor > 1 heller (Richtung Weiß), < 1 dunkler."""
    channels = [int(hex_color[i:i + 2], 16) for i in (1, 3, 5)]
    if factor >= 1:
        channels = [c + (255 - c) * (factor - 1) for c in channels]
    else:
        channels = [c * factor for c in channels]
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in channels)


def render_body(width: int, height: int, radius: int, fill: str, border: str, background: str) -> Image.Image:
    """Button-Körper: abgerundetes Rechteck mit 1-px-Rand, geglättet, auf dem Hintergrund des Fensters."""
    s = SUPERSAMPLE
    image = Image.new("RGB", (width * s, height * s), background)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle([0, 0, width * s - 1, height * s - 1], radius=radius * s, fill=border)
    draw.rounded_rectangle([s, s, width * s - 1 - s, height * s - 1 - s], radius=max(0, (radius - 1) * s), fill=fill)
    return image.resize((width, height), Image.LANCZOS)


class RoundedButton(tk.Label):
    def __init__(self, master, text: str = "", command=None, bg: str = "#444444", fg: str = "white",
                 font=None, border: str = GOLD, padx: int = 12, pady: int = 4, radius: int = 7,
                 menu=None, min_width: int = 0, **_ignored):
        self._background = master.cget("bg")
        super().__init__(master, text=text, compound="center", bd=0, highlightthickness=0, padx=0, pady=0,
                         bg=self._background, cursor="hand2", font=font)
        self._opts = {"text": text, "fill": bg, "fg": fg, "border": border, "command": command,
                      "menu": menu, "state": "normal"}
        self._padx, self._pady, self._radius, self._min_width = padx, pady, radius, min_width
        self._hover = self._pressed = False
        self._images = {}  # Cache: Größe/Farben → PhotoImage (Referenzen halten!)

        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self._redraw()

    # ── tk.Button-kompatible Schnittstelle ──
    def configure(self, cnf=None, **kw):
        if cnf:
            kw.update(cnf)
        if not kw:
            return super().configure()
        redraw = False
        for key in list(kw):
            if key in ("bg", "background"):
                self._opts["fill"] = kw.pop(key)
            elif key in ("fg", "foreground"):
                self._opts["fg"] = kw.pop(key)
            elif key in ("text", "border"):
                self._opts[key] = kw.pop(key)
            elif key == "state":
                self._opts["state"] = str(kw.pop(key))
            elif key in ("command", "menu"):
                self._opts[key] = kw.pop(key)
                continue
            elif key in ("activebackground", "activeforeground", "relief", "direction", "bd"):
                kw.pop(key)  # beim gezeichneten Button ohne Bedeutung
                continue
            else:
                continue
            redraw = True
        if kw:
            super().configure(**kw)
        if redraw:
            self._redraw()

    config = configure

    def cget(self, key):
        if key in ("bg", "background"):
            return self._opts["fill"]
        if key in ("fg", "foreground"):
            return self._opts["fg"]
        if key in ("text", "state", "command"):
            return self._opts[key]
        if key == "menu":
            return str(self._opts["menu"]) if self._opts["menu"] is not None else ""
        return super().cget(key)

    def invoke(self):
        if self._opts["state"] == "disabled":
            return None
        if self._opts["menu"] is not None:
            return self._post_menu()
        if self._opts["command"]:
            return self._opts["command"]()
        return None

    # ── Zeichnen und Maus ──
    def _redraw(self):
        fill, border, fg = self._opts["fill"], self._opts["border"], self._opts["fg"]
        if self._opts["state"] == "disabled":
            fill, border, fg = _shade(fill, 0.55), _shade(border, 0.45), "#8a8a8a"
        elif self._pressed:
            fill = _shade(fill, 0.8)
        elif self._hover:
            fill, border = _shade(fill, 1.15), _shade(border, 1.3)

        font = tkfont.Font(font=super().cget("font"))
        width = max(self._min_width, font.measure(self._opts["text"]) + 2 * self._padx)
        height = font.metrics("linespace") + 2 * self._pady
        key = (width, height, fill, border)
        if key not in self._images:
            body = render_body(width, height, self._radius, fill, border, self._background)
            self._images[key] = ImageTk.PhotoImage(body, master=self)
        super().configure(image=self._images[key], text=self._opts["text"], fg=fg,
                          width=width, height=height, cursor="arrow" if self._opts["state"] == "disabled" else "hand2")

    def _set_hover(self, hover: bool):
        self._hover = hover
        if not hover:
            self._pressed = False
        self._redraw()

    def _on_press(self, _event):
        if self._opts["state"] != "disabled":
            self._pressed = True
            self._redraw()

    def _on_release(self, event):
        was_pressed, self._pressed = self._pressed, False
        self._redraw()
        inside = 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height()
        if was_pressed and inside:
            self.invoke()

    def _post_menu(self):
        menu = self._opts["menu"]
        if hasattr(menu, "popup_above"):  # DarkMenu (dunkel, Goldrand)
            return menu.popup_above(self)
        menu.update_idletasks()
        x, y = self.winfo_rootx(), self.winfo_rooty() - menu.winfo_reqheight()  # nach oben aufklappen
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()
