"""
Optionen-Fenster (z.B. "Optionen ▾" im Deck-Fenster): dunkles Aufklapp-Fenster mit Goldrand, nach Themen
in Abschnitte gegliedert. Je Abschnitt eine Reihe Buttons nebeneinander:
  - Auswahl (add_choice): eine von mehreren Möglichkeiten, die aktive ist blau hinterlegt
  - Schalter (add_toggles): an/aus, an = grün mit ✓
  - Aktionen (add_buttons): führen etwas aus und schließen das Fenster
Auswahl und Schalter lassen das Fenster offen, damit man die Änderung sieht. invoke(Beschriftung) wie ein Klick.
"""

import time
import tkinter as tk
import tkinter.font as tkfont
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from dark_menu import FOCUS_GRACE
from rounded_button import RoundedButton
from window_style import GOLD, apply_frame

BG = "#2b2b2b"
MUTED = "#9e9e9e"
LINE = "#3d3d3d"
OFF = "#3a3a3a"        # Button aus / nicht gewählt
CHOSEN = "#007acc"     # gewählte Möglichkeit
ON = "#2e7d32"         # Schalter an
ACTION = "#5e35b1"     # Aktion


class OptionsPopup:
    def __init__(self, master: tk.Misc, font, font_small, font_head, scale: float = 1.0, gap: int = 4):
        self.master, self.font, self.font_small, self.font_head = master, font, font_small, font_head
        self.s, self.gap = scale, gap
        self.rows: List[dict] = []
        self.window: Optional[tk.Toplevel] = None
        self.buttons: Dict[str, RoundedButton] = {}  # Beschriftung → Button (solange offen)
        self._opened_at = 0.0

    # ── Inhalt ──
    def add_section(self, title: str, hint: str = "") -> None:
        self.rows.append({"kind": "section", "title": title, "hint": hint})

    def add_choice(self, options: Sequence[Tuple[str, object]], variable: tk.Variable,
                   command: Optional[Callable] = None) -> None:
        """Eine von mehreren Möglichkeiten: [(Beschriftung, Wert)]."""
        self.rows.append({"kind": "choice", "items": list(options), "variable": variable, "command": command})

    def add_toggles(self, toggles: Sequence[Tuple[str, tk.BooleanVar]], command: Optional[Callable] = None) -> None:
        """An/Aus-Schalter nebeneinander: [(Beschriftung, Variable)]."""
        self.rows.append({"kind": "toggles", "items": list(toggles), "command": command})

    def add_buttons(self, actions: Sequence[Tuple[str, Callable]]) -> None:
        """Aktionen nebeneinander: [(Beschriftung, Funktion)]."""
        self.rows.append({"kind": "buttons", "items": list(actions)})

    def invoke(self, label: str):
        """Wie ein Klick auf den Button mit dieser Beschriftung."""
        for row in self.rows:
            for item in row.get("items", []):
                if item[0] != label:
                    continue
                if row["kind"] == "choice":
                    row["variable"].set(item[1])
                elif row["kind"] == "toggles":
                    item[1].set(not item[1].get())
                else:
                    self.close()
                    return item[1]()
                self._refresh()
                return row["command"]() if row["command"] else None
        raise KeyError(label)

    # ── Anzeige ──
    @property
    def is_open(self) -> bool:
        return self.window is not None and bool(self.window.winfo_exists())

    def popup_below(self, widget: tk.Widget) -> None:
        """Öffnet das Fenster bündig unter `widget` (rechtsbündig); zweiter Klick schließt."""
        if self.is_open:
            self.close()
            return
        s = self.s
        win = tk.Toplevel(self.master, bg=BG)
        win.overrideredirect(True)
        win.wm_attributes("-topmost", True)
        body = tk.Frame(win, bg=BG, padx=int(12 * s), pady=int(8 * s))
        body.pack()
        self.window, self.buttons = win, {}
        for index, row in enumerate(self.rows):
            self._build_row(body, row, first=index == 0)
        self._refresh()
        win.update_idletasks()
        x = min(widget.winfo_rootx(), widget.winfo_rootx() + widget.winfo_width() - win.winfo_reqwidth())
        y = widget.winfo_rooty() + widget.winfo_height() + self.gap
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
        apply_frame(win)
        win.bind("<Escape>", lambda e: self.close())
        win.bind("<FocusOut>", self._on_focus_out)
        win.focus_force()
        self._opened_at = time.monotonic()

    def close(self) -> None:
        if self.is_open:
            self.window.destroy()
        self.window, self.buttons = None, {}

    def _build_row(self, body: tk.Frame, row: dict, first: bool) -> None:
        s = self.s
        if row["kind"] == "section":
            if not first:
                tk.Frame(body, bg=LINE, height=1).pack(fill=tk.X, pady=(int(8 * s), int(6 * s)))
            tk.Label(body, text=row["title"].upper(), fg=GOLD, bg=BG, font=self.font_head,
                     anchor="w").pack(fill=tk.X)
            if row["hint"]:
                tk.Label(body, text=row["hint"], fg=MUTED, bg=BG, font=self.font_small,
                         anchor="w").pack(fill=tk.X)
            return
        line = tk.Frame(body, bg=BG)
        line.pack(fill=tk.X, pady=(int(5 * s), 0))
        # Gleich breite Buttons je Reihe; Schalter mit Platz für ✓, damit nichts beim Umschalten springt
        measure = tkfont.Font(font=self.font).measure
        prefix = "✓ " if row["kind"] == "toggles" else ""
        width = max(measure(prefix + item[0]) for item in row["items"]) + int(24 * s)
        for item in row["items"]:
            label = item[0]
            button = RoundedButton(line, text=label, command=lambda l=label: self.invoke(l), bg=OFF,
                                   font=self.font, padx=int(12 * s), pady=int(4 * s), radius=int(7 * s),
                                   min_width=width)
            button.pack(side=tk.LEFT, padx=(0, int(6 * s)))
            self.buttons[label] = button

    def _refresh(self) -> None:
        """Farben und ✓ nach dem aktuellen Stand."""
        if not self.is_open:
            return
        for row in self.rows:
            for item in row.get("items", []):
                button = self.buttons.get(item[0])
                if button is None:
                    continue
                if row["kind"] == "choice":
                    button.config(bg=CHOSEN if row["variable"].get() == item[1] else OFF)
                elif row["kind"] == "toggles":
                    on = bool(item[1].get())
                    button.config(bg=ON if on else OFF, text=f"✓ {item[0]}" if on else item[0])
                else:
                    button.config(bg=ACTION)

    def _on_focus_out(self, _event):
        # Klick woanders hin (Deck-Fenster, Spiel) → zu. Fokuswechsel innerhalb des Fensters ignorieren.
        def check():
            if self.is_open and time.monotonic() - self._opened_at >= FOCUS_GRACE:
                focused = self.window.focus_get()
                if focused is None or focused.winfo_toplevel() is not self.window:
                    self.close()
        self.master.after(10, check)
