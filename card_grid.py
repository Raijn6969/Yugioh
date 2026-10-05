"""
Kartenraster für die Fenster Staples und Matchup: Kartenbilder mit kurzen Zeilen darunter, in Abschnitten.

Alles wird auf eine einzige Zeichenfläche (Canvas) gemalt statt aus vielen kleinen Widgets gebaut – das ist rund
zwanzigmal schneller (30 Karten: ~5 ms statt ~90 ms), das Fenster ruckelt beim Öffnen nicht. Beim Darüberfahren
ruft jede Karte on_enter/on_leave auf (für das Info-Fenster mit den Details).
"""

import tkinter as tk
from typing import Callable, List, Optional, Sequence, Tuple

Line = Tuple[str, str, tuple]  # (Text, Farbe, Schrift)


class CardGrid:
    def __init__(self, parent: tk.Misc, bg: str, placeholder_bg: str, text_color: str, photos, scale: float,
                 columns: int, title_font: tuple, title_color: str, small_font: tuple):
        self.bg, self.placeholder_bg, self.text_color = bg, placeholder_bg, text_color
        self.photos, self.s, self.columns = photos, scale, columns
        self.title_font, self.title_color, self.small_font = title_font, title_color, small_font
        frame = tk.Frame(parent, bg=bg)
        frame.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(frame, bg=bg, highlightthickness=0, bd=0)
        bar = tk.Scrollbar(frame, command=self.canvas.yview)
        self.canvas.config(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))
        self.clear()

    # ── Aufbau ──
    def clear(self) -> None:
        self.canvas.delete("all")
        self.y = 0              # nächste freie Höhe
        self.count = 0          # Karten im aktuellen Abschnitt
        self.row_height = 0
        self.tiles = 0
        self.canvas.config(scrollregion=(0, 0, 1, 1))
        self.canvas.yview_moveto(0)

    def section(self, title: str) -> None:
        s = self.s
        self._end_row()
        self.y += int(8 * s)
        self.canvas.create_text(int(2 * s), self.y, text=title, fill=self.title_color, font=self.title_font,
                                anchor="nw")
        self.y += int(18 * s)
        self.count = 0

    def card(self, name: str, passcode: Optional[str], lines: Sequence[Line],
             on_enter: Optional[Callable] = None, on_leave: Optional[Callable] = None) -> None:
        """Eine Karte: Bild (ohne Bild: Name auf einer kartengroßen Fläche) und Zeilen darunter."""
        s = self.s
        width, height = self.photos.size
        pad = int(6 * s)
        x = int(3 * s) + (self.count % self.columns) * (width + pad)
        if self.count and self.count % self.columns == 0:
            self.y += self.row_height
        top = self.y + int(3 * s)
        tag = f"tile{self.tiles}"
        self.tiles += 1
        line_height = int(15 * s)
        total = height + int(4 * s) + line_height * len(lines)
        # unsichtbare Fläche über die ganze Kachel: Darüberfahren auch zwischen Bild und Text
        self.canvas.create_rectangle(x, top, x + width, top + total, fill=self.bg, outline="", tags=(tag,))
        photo = self.photos.get(passcode)
        if photo is not None:
            self.canvas.create_image(x, top, image=photo, anchor="nw", tags=(tag,))
        else:
            self.canvas.create_rectangle(x, top, x + width, top + height, fill=self.placeholder_bg, outline="",
                                         tags=(tag,))
            self.canvas.create_text(x + width // 2, top + height // 2, text=name, fill=self.text_color,
                                    font=self.small_font, width=width - int(8 * s), justify=tk.CENTER, tags=(tag,))
        for i, (text, color, font) in enumerate(lines):
            self.canvas.create_text(x + width // 2, top + height + int(4 * s) + i * line_height, text=text,
                                    fill=color, font=font, anchor="n", tags=(tag,))
        if on_enter is not None:
            self.canvas.tag_bind(tag, "<Enter>", on_enter)
        if on_leave is not None:
            self.canvas.tag_bind(tag, "<Leave>", on_leave)
        self.row_height = total + int(6 * s)
        self.count += 1

    def message(self, text: str, color: str, font: tuple) -> None:
        self._end_row()
        self.y += int(20 * self.s)
        self.canvas.create_text(int(self.canvas.winfo_width() or 400) // 2, self.y, text=text, fill=color,
                                font=font, anchor="n")
        self.y += int(30 * self.s)

    def finish(self) -> None:
        """Nach dem letzten Abschnitt: Scrollbereich setzen."""
        self._end_row()
        self.canvas.config(scrollregion=(0, 0, 1, self.y + int(4 * self.s)))

    def _end_row(self) -> None:
        if self.count:
            self.y += self.row_height
            self.count = 0

    # ── Für Tests / Prüfung ──
    def texts(self) -> List[str]:
        return [self.canvas.itemcget(item, "text") for item in self.canvas.find_all()
                if self.canvas.type(item) == "text"]

    def _on_wheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120) * 2, "units")

    def release(self) -> None:
        try:
            self.canvas.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass
