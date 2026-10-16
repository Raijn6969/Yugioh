"""
Dunkles Auswahlmenü mit Goldrand (statt des nativen Windows-Menüs, dessen Rahmen Tk nicht färben kann).
Gleiche Schnittstelle wie tk.Menu für das, was das Overlay braucht: add_command, add_radiobutton,
add_checkbutton, invoke(index). RoundedButton(menu=…) klappt es über dem Button auf, popup_at(x, y) mit der unteren rechten Ecke an einem
Punkt (Tray-Menü). Zusätzlich: add_label (ausgegraut, nicht
klickbar) und add_separator. Die Zeile unter der Maus bekommt eine abgerundete, dunkelgoldene Fläche.
"""

import tkinter as tk
import tkinter.font as tkfont
from typing import Callable, Dict, List, Optional, Tuple

from PIL import ImageTk

import win_api
from rounded_button import render_body
from window_style import (GOLD_HOVER, GOOD, LINE, MUTED, PANEL, TEXT, ClickOutside, apply_frame,
                          no_activate)

RADIUS = 6


class DarkMenu:
    def __init__(self, master: tk.Misc, font=None, gap: int = 4, row_pady: int = 3):
        self.master = master
        self.font = font
        self.gap = gap  # Abstand zum Button
        self.row_pady = row_pady  # Luft über/unter dem Text je Eintrag (Tray-Menü: größer)
        self._images: Dict[tuple, ImageTk.PhotoImage] = {}  # Hover-Bilder (Referenz halten, sonst weg)
        self.items: List[dict] = []
        self.window: Optional[tk.Toplevel] = None

    # ── tk.Menu-kompatibel ──
    def add_command(self, label: str, command: Callable, hover: str = GOLD_HOVER):
        self.items.append({"label": label, "command": command, "hover": hover})

    def add_label(self, label: str):
        """Ausgegrauter Text ohne Klick (z.B. Titel)."""
        self.items.append({"label": label, "command": None, "disabled": True})

    def add_separator(self):
        self.items.append({"separator": True})

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
        if self.is_open:
            self.close()  # zweiter Klick auf den Button schließt
            return

        def place(win: tk.Toplevel) -> Tuple[int, int]:
            # Breiter als der Button → rechtsbündig, damit es nicht über den Bildschirmrand hinausragt
            x = min(widget.winfo_rootx(), widget.winfo_rootx() + widget.winfo_width() - win.winfo_reqwidth())
            return x, widget.winfo_rooty() - win.winfo_reqheight() - self.gap
        self._open(place, widget)

    def popup_at(self, x: int, y: int) -> None:
        """Öffnet das Menü mit der unteren rechten Ecke bei (x, y) – so gehen Tray-Menüs über der Taskleiste auf.
        Bleibt im Arbeitsbereich (ohne Taskleiste). Ein offenes Menü wird ersetzt."""
        self.close()

        def place(win: tk.Toplevel) -> Tuple[int, int]:
            width, height = win.winfo_reqwidth(), win.winfo_reqheight()
            left, top, right, bottom = win_api.work_area() or (0, 0, win.winfo_screenwidth(), win.winfo_screenheight())
            return max(left, min(x - width, right - width)), max(top, min(y - height, bottom - height))
        self._open(place, None)

    def _open(self, place: Callable[[tk.Toplevel], Tuple[int, int]], anchor: Optional[tk.Widget]) -> None:
        win = tk.Toplevel(self.master, bg=PANEL)
        win.withdraw()  # erst zeigen, wenn es platziert ist (sonst kurz oben links → Taskleiste im Vollbild)
        win.overrideredirect(True)
        win.wm_attributes("-topmost", True)
        body = tk.Frame(win, bg=PANEL, padx=3, pady=4)
        body.pack()
        font = tkfont.Font(root=self.master, font=self.font or "TkDefaultFont")
        marks = any("variable" in item for item in self.items)
        text_w = max((font.measure(item.get("label", "")) for item in self.items), default=0)
        width = text_w + 2 * font.measure("0") * marks + 30
        for index, item in enumerate(self.items):
            if item.get("separator"):
                tk.Frame(body, height=1, bg=LINE).pack(fill=tk.X, padx=8, pady=4)
            else:
                self._add_row(body, index, item, font, width, marks)
        win.update_idletasks()
        x, y = place(win)
        win.geometry(f"+{x}+{max(0, y)}")
        win.deiconify()
        apply_frame(win)
        no_activate(win)  # Master Duel bleibt aktiv (sonst Taskleiste über dem Spiel)
        self.window = win
        ClickOutside(win, anchor or win, self.close, lambda: self.is_open)

    def close(self) -> None:
        if self.is_open:
            self.window.destroy()
        self.window = None

    def _add_row(self, parent: tk.Frame, index: int, item: dict, font: tkfont.Font, width: int, marks: bool) -> None:
        """Eine Zeile als Canvas: Text darüber, darunter bei Hover die abgerundete Fläche."""
        if item.get("toggle"):
            checked = bool(item["variable"].get())
        else:
            checked = "variable" in item and item["variable"].get() == item["value"]
        disabled = item.get("disabled", False)
        height = font.metrics("linespace") + 2 * self.row_pady
        row = tk.Canvas(parent, width=width, height=height, bg=PANEL, highlightthickness=0, bd=0,
                        cursor="arrow" if disabled else "hand2")
        row.pack()
        mark_w = 2 * font.measure("0") if marks else 0
        hover_bg = row.create_image(0, 0, anchor="nw", state="hidden")
        if marks:
            row.create_text(mark_w // 2 + 4, height // 2, text="✓" if checked else "", fill=GOOD, font=font)
        row.create_text(mark_w + 8, height // 2, text=item["label"], anchor="w", font=font,
                        fill=MUTED if disabled else TEXT)
        if disabled:
            return
        color = item.get("hover", GOLD_HOVER)
        key = (color, width, height)
        if key not in self._images:
            self._images[key] = ImageTk.PhotoImage(render_body(width, height, RADIUS, color, color, PANEL), master=row)
        row.itemconfigure(hover_bg, image=self._images[key])
        row.bind("<Enter>", lambda e: row.itemconfigure(hover_bg, state="normal"))
        row.bind("<Leave>", lambda e: row.itemconfigure(hover_bg, state="hidden"))
        row.bind("<ButtonRelease-1>", lambda e, i=index: self.invoke(i))
