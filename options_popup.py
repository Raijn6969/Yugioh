"""
Optionen-Fenster (z.B. "Optionen ▾" im Deck-Fenster): dunkles Aufklapp-Fenster, nach Themen in Abschnitte
gegliedert. Je Eintrag eine Zeile, rechts ein Kästchen (Klick auf die ganze Zeile):
  - Auswahl (add_choice): eine von mehreren Möglichkeiten, die gewählte hat das gefüllte Kästchen
  - Schalter (add_toggles): an/aus, an = gefülltes Kästchen mit ✓
  - Aktionen (add_buttons): Text-Links nebeneinander, führen etwas aus und schließen das Fenster
Auswahl und Schalter lassen das Fenster offen, damit man die Änderung sieht. invoke(Beschriftung) wie ein Klick.
visible=Funktion: Zeile nur anzeigen, wenn sie True liefert (z.B. Tempo nur bei Texterkennung); rebuild()
baut ein offenes Fenster danach neu auf.
"""

import tkinter as tk
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from window_style import (AMBER, BG, GOLD, LINE, MUTED, PANEL, SIZE_SMALL, TEXT, ClickOutside, apply_frame, font,
                          no_activate, place_in_work_area)


class OptionsPopup:
    def __init__(self, master: tk.Misc, font, font_small, font_head, scale: float = 1.0, gap: int = 4):
        self.master, self.font, self.font_small, self.font_head = master, font, font_small, font_head
        self.s, self.gap = scale, gap
        self.rows: List[dict] = []
        self.window: Optional[tk.Toplevel] = None
        self.buttons: Dict[str, tk.Widget] = {}  # Beschriftung → anklickbare Zeile bzw. Link (solange offen)
        self.boxes: Dict[str, tk.Canvas] = {}    # Beschriftung → Kästchen (Auswahl, Schalter)

    # ── Inhalt ──
    def add_section(self, title: str, hint: str = "", visible: Optional[Callable[[], bool]] = None) -> None:
        self.rows.append({"kind": "section", "title": title, "hint": hint, "visible": visible})

    def add_choice(self, options: Sequence[Tuple[str, object]], variable: tk.Variable,
                   command: Optional[Callable] = None, visible: Optional[Callable[[], bool]] = None) -> None:
        """Eine von mehreren Möglichkeiten: [(Beschriftung, Wert)]."""
        self.rows.append({"kind": "choice", "items": list(options), "variable": variable, "command": command,
                          "visible": visible})

    def add_toggles(self, toggles: Sequence[Tuple[str, tk.BooleanVar]], command: Optional[Callable] = None,
                    visible: Optional[Callable[[], bool]] = None) -> None:
        """An/Aus-Schalter nebeneinander: [(Beschriftung, Variable)]."""
        self.rows.append({"kind": "toggles", "items": list(toggles), "command": command, "visible": visible})

    def add_buttons(self, actions: Sequence[Tuple[str, Callable]]) -> None:
        """Aktionen nebeneinander: [(Beschriftung, Funktion)]."""
        self.rows.append({"kind": "buttons", "items": list(actions)})

    def invoke(self, label: str):
        """Wie ein Klick auf den Button mit dieser Beschriftung."""
        for row in self.visible_rows():
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

    def visible_rows(self) -> List[dict]:
        return [row for row in self.rows if row.get("visible") is None or row["visible"]()]

    # ── Anzeige ──
    @property
    def is_open(self) -> bool:
        return self.window is not None and bool(self.window.winfo_exists())

    def popup_below(self, widget: tk.Widget) -> None:
        """Öffnet das Fenster bündig unter `widget` (rechtsbündig); zweiter Klick schließt."""
        if self.is_open:
            self.close()
            return
        win = tk.Toplevel(self.master, bg=PANEL)
        win.withdraw()  # erst zeigen, wenn es platziert ist (sonst kurz oben links → Taskleiste im Vollbild)
        win.overrideredirect(True)
        win.wm_attributes("-topmost", True)
        self.window, self._anchor = win, widget
        self._build_body()
        win.update_idletasks()
        x = min(widget.winfo_rootx(), widget.winfo_rootx() + widget.winfo_width() - win.winfo_reqwidth())
        y = widget.winfo_rooty() + widget.winfo_height() + self.gap
        x, y = place_in_work_area(x, y, win.winfo_reqwidth(), win.winfo_reqheight(), widget)
        win.geometry(f"+{x}+{y}")
        win.deiconify()
        apply_frame(win)
        no_activate(win)  # Master Duel bleibt aktiv (sonst Taskleiste über dem Spiel)
        ClickOutside(win, widget, self.close, lambda: self.is_open)

    def rebuild(self) -> None:
        """Offenes Fenster neu aufbauen (z.B. weil eine Zeile jetzt sichtbar/unsichtbar ist)."""
        if not self.is_open:
            return
        for child in self.window.winfo_children():
            child.destroy()
        self._build_body()
        self.window.update_idletasks()
        widget = self._anchor
        x = min(widget.winfo_rootx(), widget.winfo_rootx() + widget.winfo_width() - self.window.winfo_reqwidth())
        x, y = place_in_work_area(x, self.window.winfo_rooty(), self.window.winfo_reqwidth(),
                                  self.window.winfo_reqheight(), widget)
        self.window.geometry(f"+{x}+{y}")

    def _build_body(self) -> None:
        s = self.s
        body = tk.Frame(self.window, bg=PANEL, padx=int(12 * s), pady=int(8 * s))
        body.pack()
        self.buttons, self.boxes = {}, {}
        for index, row in enumerate(self.visible_rows()):
            self._build_row(body, row, first=index == 0)
        self._refresh()

    def close(self) -> None:
        if self.is_open:
            self.window.destroy()
        self.window, self.buttons, self.boxes = None, {}, {}

    def _build_row(self, body: tk.Frame, row: dict, first: bool) -> None:
        s = self.s
        if row["kind"] == "section":
            if not first:
                tk.Frame(body, bg=LINE, height=1).pack(fill=tk.X, pady=(int(8 * s), int(6 * s)))
            if row["title"]:  # ohne Titel: der Hinweis steht allein (der Titel würde nur den Knopf wiederholen)
                tk.Label(body, text=row["title"], fg=GOLD, bg=PANEL, font=self.font_head,
                         anchor="w").pack(fill=tk.X)
            if row["hint"]:
                tk.Label(body, text=row["hint"], fg=MUTED, bg=PANEL, font=self.font_small,
                         anchor="w").pack(fill=tk.X)
            return
        if row["kind"] == "buttons":
            line = tk.Frame(body, bg=PANEL)
            line.pack(fill=tk.X, pady=(int(5 * s), 0))
            for label, _ in row["items"]:
                link = tk.Label(line, text=label, fg=GOLD, bg=PANEL, font=self.font + ("underline",), cursor="hand2")
                link.pack(side=tk.LEFT, padx=(0, int(14 * s)))
                link.bind("<Button-1>", lambda e, l=label: self.invoke(l))
                self.buttons[label] = link
            return
        size = int(16 * s)
        for label, *_ in row["items"]:
            line = tk.Frame(body, bg=PANEL, cursor="hand2")
            line.pack(fill=tk.X, pady=(int(3 * s), 0))
            text = tk.Label(line, text=label, fg=TEXT, bg=PANEL, font=self.font, anchor="w", cursor="hand2")
            text.pack(side=tk.LEFT, padx=(0, int(24 * s)))
            box = tk.Canvas(line, width=size, height=size, bg=PANEL, highlightthickness=0, cursor="hand2")
            box.create_rectangle(1, 1, size - 2, size - 2, tags="box")
            box.create_text(size // 2, size // 2, text="✓", fill=BG, font=font(SIZE_SMALL, s, bold=True), tags="tick")
            box.pack(side=tk.RIGHT)
            for widget in (line, text, box):
                widget.bind("<Button-1>", lambda e, l=label: self.invoke(l))
            self.buttons[label], self.boxes[label] = line, box

    def _refresh(self) -> None:
        """Kästchen nach dem aktuellen Stand."""
        if not self.is_open:
            return
        for row in self.rows:
            for item in row.get("items", []):
                box = self.boxes.get(item[0])
                if box is None:
                    continue
                on = row["variable"].get() == item[1] if row["kind"] == "choice" else bool(item[1].get())
                box.itemconfigure("box", fill=AMBER if on else PANEL, outline=AMBER if on else MUTED)
                box.itemconfigure("tick", state="normal" if on else "hidden")
