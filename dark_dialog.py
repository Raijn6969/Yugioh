"""
Dunkle Hinweis- und Auswahlfenster im Stil des Overlays (statt der hellen Windows-Messagebox).
Ohne Windows-Titelleiste wie das Overlay; verschiebbar an der Kopfzeile. show_message schließt mit OK,
Enter oder Escape; ask_choice wartet auf einen der Buttons (Escape = abbrechen). show_toast ist ein kurzer Hinweis
ohne Button über der Taskleiste, der von selbst verschwindet.
"""

import tkinter as tk
from typing import Any, Callable, List, Optional, Sequence, Tuple

from dark_scrollbar import DarkScrollbar
from rounded_button import RoundedButton
from window_style import BAD, BG, GOOD, INFO, MUTED, NEUTRAL, PANEL, PRIMARY, TEXT, WARN, apply_frame, \
    no_activate
import win_api


# Art → (Akzentfarbe, Symbol)
KINDS = {
    "info": (GOOD, "✓"),
    "warning": (WARN, "!"),
    "error": (BAD, "✕"),
    "question": (INFO, "?"),
}
MAX_LIST_LINES = 10  # Längere Listen bekommen eine Scrollleiste
TOAST_MS = 5000      # so lange bleibt show_toast stehen


def show_message(master: tk.Misc, title: str, message: str, kind: str = "info",
                 items: Optional[List[str]] = None, notes: Optional[List[str]] = None,
                 wait: bool = True,
                 actions: Sequence[Tuple[str, Callable[[], Optional[str]]]] = ()) -> tk.Toplevel:
    """
    Zeigt ein dunkles Hinweisfenster. `items` (z.B. fehlende Karten) erscheinen als Liste,
    `notes` als "Hinweise" darunter. Mit wait=True blockiert der Aufruf bis zum Schließen
    (wie messagebox), der Rest des Programms bleibt dabei bedienbar.
    `actions`: zusätzliche Buttons [(Text, Funktion)] links neben OK/Schließen. Die Funktion darf
    einen Text zurückgeben (z.B. "Gespeichert: …"), der unter den Buttons erscheint.
    """
    accent = KINDS.get(kind, KINDS["info"])[0]
    win, body, scale, font, font_bold, wrap = _window(master, title, message, kind)

    if items:
        _bullet_list(body, items, fg=TEXT, font=font, scale=scale)
    if notes:
        tk.Label(body, text="Hinweise", fg=accent, bg=BG, font=font_bold,
                 anchor="w").pack(fill=tk.X, pady=(int(10 * scale), 2))
        for note in notes:
            tk.Label(body, text=f"• {note}", fg=MUTED, bg=BG, font=font, wraplength=wrap,
                     justify=tk.LEFT, anchor="w").pack(fill=tk.X, pady=1)

    buttons = tk.Frame(win, bg=BG)
    buttons.pack(pady=(0, int(12 * scale)))
    button_size = dict(font=font_bold, padx=int(30 * scale) if not actions else int(16 * scale),
                       pady=int(5 * scale), radius=int(7 * scale))
    feedback = tk.Label(win, text="", fg=MUTED, bg=BG, font=font, wraplength=wrap, justify=tk.LEFT)
    win.action_buttons = []
    for label, func in actions:
        def run(func=func):
            text = func()
            if text:
                feedback.config(text=text)
                feedback.pack(padx=int(16 * scale), pady=(0, int(12 * scale)))
        button = RoundedButton(buttons, text=label, command=run, bg="#2e7d32", **button_size)
        button.pack(side=tk.LEFT, padx=int(5 * scale))
        win.action_buttons.append(button)
    ok = RoundedButton(buttons, text="Schließen" if actions else "OK", command=win.destroy,
                       bg=PRIMARY if not actions else NEUTRAL, **button_size)
    ok.pack(side=tk.LEFT, padx=int(5 * scale))
    win.ok_button = ok  # für Tests
    win.feedback_label = feedback
    win.bind("<Return>", lambda e: win.destroy())
    win.bind("<Escape>", lambda e: win.destroy())

    _show(master, win, wait)
    return win


def ask_choice(master: tk.Misc, title: str, message: str,
               choices: Sequence[Tuple[str, Any, str]], kind: str = "question") -> Any:
    """
    Dunkles Auswahlfenster (statt messagebox.askyesnocancel). `choices`: [(Button-Text, Rückgabewert, Farbe)],
    der erste ist der Standard (Enter). Returns: Wert des gedrückten Buttons, None bei Escape/Abbrechen.
    """
    win, _, scale, _, font_bold, _ = _window(master, title, message, kind)
    result = {"value": None}

    def choose(value):
        result["value"] = value
        win.destroy()

    buttons = tk.Frame(win, bg=BG)
    buttons.pack(pady=(0, int(12 * scale)))
    win.choice_buttons = []
    for label, value, color in choices:
        button = RoundedButton(buttons, text=label, command=lambda v=value: choose(v), bg=color, font=font_bold,
                               padx=int(16 * scale), pady=int(5 * scale), radius=int(7 * scale))
        button.pack(side=tk.LEFT, padx=int(5 * scale))
        win.choice_buttons.append(button)  # für Tests
    if choices:
        win.bind("<Return>", lambda e: choose(choices[0][1]))
    win.bind("<Escape>", lambda e: choose(None))
    _show(master, win, wait=True)
    return result["value"]


def show_toast(master: tk.Misc, text: str, duration_ms: int = TOAST_MS) -> tk.Toplevel:
    """
    Dezenter Hinweis ohne Button, mittig direkt über der Taskleiste (bzw. am unteren Rand, wenn keine da ist).
    Verschwindet nach duration_ms, ein Klick schließt ihn sofort. Nimmt dem Spiel nicht den Fokus.
    """
    scale = max(1.0, master.winfo_screenheight() / 1080.0)
    win = tk.Toplevel(master, bg=BG)
    win.overrideredirect(True)
    win.wm_attributes("-topmost", True)
    win.wm_attributes("-alpha", 0.95)
    label = tk.Label(win, text=text, fg=KINDS["info"][0], bg=BG, font=("Helvetica", int(10 * scale), "bold"),
                     padx=int(16 * scale), pady=int(6 * scale))
    label.pack()
    win.update_idletasks()
    width, height = win.winfo_reqwidth(), win.winfo_reqheight()
    left, _top, right, bottom = win_api.work_area() or (0, 0, win.winfo_screenwidth(), win.winfo_screenheight())
    gap = int(8 * scale)  # etwas Luft zur Taskleiste
    win.geometry(f"{width}x{height}+{(left + right - width) // 2}+{bottom - height - gap}")
    apply_frame(win)
    no_activate(win)
    for widget in (win, label):
        widget.bind("<Button-1>", lambda _event: win.destroy())
    win.after(duration_ms, win.destroy)
    return win


def _window(master: tk.Misc, title: str, message: str, kind: str):
    """Fenster mit Kopfzeile (Farbleiste, Symbol, Titel) und Text. Returns: win, body, scale, Schriften, Umbruch."""
    accent, symbol = KINDS.get(kind, KINDS["info"])
    scale = max(1.0, master.winfo_screenheight() / 1080.0)
    font = ("Helvetica", int(10 * scale))
    font_bold = ("Helvetica", int(11 * scale), "bold")
    wrap = int(440 * scale)

    win = tk.Toplevel(master, bg=BG)
    win.overrideredirect(True)
    win.wm_attributes("-topmost", True)
    win.title(title)

    # Kopfzeile: Farbleiste, Symbol, Titel – zum Verschieben anfassen
    header = tk.Frame(win, bg=PANEL)
    header.pack(fill=tk.X)
    tk.Frame(header, bg=accent, width=int(5 * scale)).pack(side=tk.LEFT, fill=tk.Y)
    tk.Label(header, text=symbol, fg=accent, bg=PANEL, font=font_bold).pack(side=tk.LEFT, padx=(int(10 * scale), 4),
                                                                          pady=int(6 * scale))
    tk.Label(header, text=title, fg=TEXT, bg=PANEL, font=font_bold).pack(side=tk.LEFT, pady=int(6 * scale))
    _make_draggable(win, header)

    body = tk.Frame(win, bg=BG, padx=int(16 * scale), pady=int(12 * scale))
    body.pack(fill=tk.BOTH, expand=True)
    tk.Label(body, text=message, fg=TEXT, bg=BG, font=font, wraplength=wrap,
             justify=tk.LEFT, anchor="w").pack(fill=tk.X)
    return win, body, scale, font, font_bold, wrap


def _show(master: tk.Misc, win: tk.Toplevel, wait: bool) -> None:
    _center_on_screen(win)
    apply_frame(win)  # runde Ecken + Goldrand wie das Overlay
    win.focus_force()
    if wait:
        win.grab_set()
        master.wait_window(win)


def _bullet_list(parent: tk.Misc, items: List[str], fg: str, font, scale: float) -> None:
    frame = tk.Frame(parent, bg=PANEL)
    frame.pack(fill=tk.BOTH, expand=True, pady=(int(8 * scale), 0))
    lines = min(len(items), MAX_LIST_LINES)
    text = tk.Text(frame, height=lines, width=48, bg=PANEL, fg=fg, font=font, bd=0,
                   highlightthickness=0, padx=int(8 * scale), pady=int(6 * scale), wrap=tk.WORD)
    text.insert("1.0", "\n".join(f"• {item}" for item in items))
    text.config(state=tk.DISABLED)
    text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    if len(items) > MAX_LIST_LINES:
        bar = DarkScrollbar(frame, command=text.yview, bg=PANEL, scale=scale)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        text.config(yscrollcommand=bar.set)


def _make_draggable(win: tk.Toplevel, handle: tk.Widget) -> None:
    offset = {}

    def start(event):
        offset["x"], offset["y"] = event.x_root - win.winfo_x(), event.y_root - win.winfo_y()

    def move(event):
        win.geometry(f"+{event.x_root - offset['x']}+{event.y_root - offset['y']}")

    for widget in [handle] + list(handle.winfo_children()):
        widget.bind("<ButtonPress-1>", start)
        widget.bind("<B1-Motion>", move)


def _center_on_screen(win: tk.Toplevel) -> None:
    win.update_idletasks()
    width, height = win.winfo_reqwidth(), win.winfo_reqheight()
    x = (win.winfo_screenwidth() - width) // 2
    y = (win.winfo_screenheight() - height) // 3
    win.geometry(f"+{x}+{y}")
