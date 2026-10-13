"""
Dunkle Scrollleiste für alle Fenster.

tk.Scrollbar ist unter Windows ein natives Element und ignoriert Farben – es bliebe die helle Standardleiste
(weißer Balken, grauer Griff). DarkScrollbar ist ein Canvas mit derselben Schnittstelle (set/command), also ein
1:1-Ersatz: schmale Laufbahn im Fensterhintergrund, abgerundeter Griff, der beim Darüberfahren heller wird und
beim Ziehen golden. Ist alles sichtbar, fehlt der Griff – die Leiste behält aber ihre Breite (Layouts rechnen
mit winfo_reqwidth()).
"""

import tkinter as tk
from typing import Callable, Optional

HANDLE = "#4a4a4a"
HANDLE_HOVER = "#6a6a6a"
HANDLE_DRAG = "#c9a02f"  # wie GOLD in extras_panel (nicht importiert: Zirkelimport)


class DarkScrollbar(tk.Canvas):
    def __init__(self, parent: tk.Misc, command: Callable, bg: str, scale: float = 1.0):
        self.width = max(6, int(8 * scale))
        super().__init__(parent, width=self.width, bg=bg, highlightthickness=0, bd=0)
        self.command = command
        self.min_len = int(24 * scale)
        self.first, self.last = 0.0, 1.0
        self.y0 = self.y1 = 0.0  # Griff in Pixeln (y0 == y1: kein Griff)
        self.hover = self.dragging = False
        self.grab = 0.0  # Abstand zwischen Griff-Oberkante und Mauszeiger beim Ziehen
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Motion>", self._on_motion)
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)

    # ── Schnittstelle von tk.Scrollbar ──
    def set(self, first, last) -> None:
        self.first, self.last = float(first), float(last)
        self._draw()

    # ── Zeichnen ──
    def _height(self) -> int:
        return max(self.winfo_height(), 1)

    def _layout(self) -> Optional[tuple]:
        """(Griffhöhe, Laufweg in Pixeln, größter first-Wert) oder None, wenn alles sichtbar ist."""
        span = self.last - self.first
        if self.first <= 0 and self.last >= 1 or span <= 0:
            return None
        height = self._height()
        length = min(max(span * height, self.min_len), height)
        return length, height - length, 1.0 - span

    def _draw(self) -> None:
        self.delete("all")
        layout = self._layout()
        if layout is None:
            self.y0 = self.y1 = 0.0
            return
        length, travel, first_max = layout
        self.y0 = self.first / first_max * travel if first_max > 0 else 0.0
        self.y0 = min(max(self.y0, 0.0), travel)
        self.y1 = self.y0 + length
        thick = self.width - 2
        r = thick / 2
        x = self.width / 2
        self.create_line(x, self.y0 + r, x, self.y1 - r, width=thick, capstyle=tk.ROUND, tags="griff",
                         fill=self._color())

    def _color(self) -> str:
        return HANDLE_DRAG if self.dragging else HANDLE_HOVER if self.hover else HANDLE

    def _on_handle(self, y: float) -> bool:
        return self.y0 < self.y1 and self.y0 <= y <= self.y1

    def _set_hover(self, hover: bool) -> None:
        if hover != self.hover:
            self.hover = hover
            self.itemconfigure("griff", fill=self._color())

    # ── Maus ──
    def _on_motion(self, event) -> None:
        self._set_hover(self._on_handle(event.y))

    def _on_press(self, event) -> None:
        if self._on_handle(event.y):
            self.dragging, self.grab = True, event.y - self.y0
            self.itemconfigure("griff", fill=self._color())
        elif self.y0 < self.y1:  # Klick in die Laufbahn: seitenweise
            self.command("scroll", -1 if event.y < self.y0 else 1, "pages")

    def _on_drag(self, event) -> None:
        layout = self._layout()
        if not self.dragging or layout is None or layout[1] <= 0:
            return
        _, travel, first_max = layout
        top = min(max(event.y - self.grab, 0.0), travel)
        self.command("moveto", top / travel * first_max)

    def _on_release(self, event) -> None:
        self.dragging = False
        self.hover = self._on_handle(event.y)
        self.itemconfigure("griff", fill=self._color())
