"""
Dunkles Auswahlmenü mit Goldrand (statt des nativen Windows-Menüs, dessen Rahmen Tk nicht färben kann).
Gleiche Schnittstelle wie tk.Menu für das, was das Overlay braucht: add_command, add_radiobutton,
add_checkbutton, invoke(index). RoundedButton(menu=…) klappt es über dem Button auf, popup_below darunter.
"""

import time
import tkinter as tk
from typing import Callable, List, Optional

from window_style import apply_frame

BG = "#2b2b2b"
FG = "white"
ACTIVE_BG = "#007acc"
CHECK = "#00ff00"
FOCUS_GRACE = 0.2  # So lange nach dem Öffnen zählt ein Fokuswechsel nicht als "woanders hingeklickt"


class DarkMenu:
    def __init__(self, master: tk.Misc, font=None, gap: int = 4):
        self.master = master
        self.font = font
        self.gap = gap  # Abstand zum Button
        self.items: List[dict] = []
        self.window: Optional[tk.Toplevel] = None
        self._opened_at = 0.0

    # ── tk.Menu-kompatibel ──
    def add_command(self, label: str, command: Callable):
        self.items.append({"label": label, "command": command})

    def add_radiobutton(self, label: str, value, variable: tk.Variable, command: Optional[Callable] = None):
        self.items.append({"label": label, "command": command, "value": value, "variable": variable})

    def add_checkbutton(self, label: str, variable: tk.BooleanVar, command: Optional[Callable] = None):
        """Ein/Aus-Eintrag: Klick schaltet `variable` um, ✓ wenn an."""
        self.items.append({"label": label, "command": command, "variable": variable, "toggle": True})

    def invoke(self, index: int):
        self.close()
        item = self.items[index]
        if item.get("toggle"):
            item["variable"].set(not item["variable"].get())
        elif "variable" in item:
            item["variable"].set(item["value"])
        if item["command"]:
            return item["command"]()
        return None

    # ── Anzeige ──
    @property
    def is_open(self) -> bool:
        return self.window is not None and bool(self.window.winfo_exists())

    def popup_above(self, widget: tk.Widget) -> None:
        """Öffnet das Menü bündig über `widget` (das Overlay sitzt am unteren Bildschirmrand)."""
        self._popup(widget, above=True)

    def popup_below(self, widget: tk.Widget) -> None:
        """Öffnet das Menü bündig unter `widget` (z.B. Optionen oben im Deck-Fenster)."""
        self._popup(widget, above=False)

    def _popup(self, widget: tk.Widget, above: bool) -> None:
        if self.is_open:
            self.close()  # zweiter Klick auf den Button schließt
            return
        win = tk.Toplevel(self.master, bg=BG)
        win.overrideredirect(True)
        win.wm_attributes("-topmost", True)
        body = tk.Frame(win, bg=BG, padx=3, pady=4)
        body.pack()
        for index, item in enumerate(self.items):
            self._add_row(body, index, item)
        win.update_idletasks()
        # Breiter als der Button → rechtsbündig, damit es nicht über den Bildschirmrand hinausragt
        x = min(widget.winfo_rootx(), widget.winfo_rootx() + widget.winfo_width() - win.winfo_reqwidth())
        if above:
            y = widget.winfo_rooty() - win.winfo_reqheight() - self.gap
        else:
            y = widget.winfo_rooty() + widget.winfo_height() + self.gap
        win.geometry(f"+{x}+{max(0, y)}")
        apply_frame(win)
        win.bind("<Escape>", lambda e: self.close())
        win.bind("<FocusOut>", self._on_focus_out)
        win.focus_force()
        self.window = win
        self._opened_at = time.monotonic()

    def close(self) -> None:
        if self.is_open:
            self.window.destroy()
        self.window = None

    def _add_row(self, parent: tk.Frame, index: int, item: dict) -> None:
        if item.get("toggle"):
            checked = bool(item["variable"].get())
        else:
            checked = "variable" in item and item["variable"].get() == item["value"]
        row = tk.Frame(parent, bg=BG, cursor="hand2")
        row.pack(fill=tk.X)
        mark = tk.Label(row, text="✓" if checked else "", fg=CHECK, bg=BG, font=self.font, width=2)
        mark.pack(side=tk.LEFT)
        text = tk.Label(row, text=item["label"], fg=FG, bg=BG, font=self.font, anchor="w", padx=4, pady=3)
        text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 10))
        widgets = (row, mark, text)

        def highlight(active: bool):
            for w in widgets:
                w.config(bg=ACTIVE_BG if active else BG)

        for w in widgets:
            w.bind("<Enter>", lambda e: highlight(True))
            w.bind("<Leave>", lambda e: highlight(False))
            w.bind("<ButtonRelease-1>", lambda e, i=index: self.invoke(i))

    def _on_focus_out(self, _event):
        # Klick woanders hin (Overlay, Spiel) → zu. Fokuswechsel innerhalb des Menüs ignorieren.
        def check():
            if self.is_open and time.monotonic() - self._opened_at >= FOCUS_GRACE:
                focused = self.window.focus_get()
                if focused is None or focused.winfo_toplevel() is not self.window:
                    self.close()
        self.master.after(10, check)
