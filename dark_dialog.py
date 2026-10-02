"""
Dunkles Hinweisfenster im Stil des Overlays (statt der hellen Windows-Messagebox).
Ohne Windows-Titelleiste wie das Overlay; verschiebbar an der Kopfzeile, schließt mit OK,
Enter oder Escape.
"""

import tkinter as tk
from typing import List, Optional

from rounded_button import RoundedButton
from window_style import apply_frame

BG = "#1e1e1e"
PANEL = "#2b2b2b"
TEXT = "#e8e8e8"
MUTED = "#aaaaaa"
BUTTON = "#007acc"

# Art → (Akzentfarbe, Symbol)
KINDS = {
    "info": ("#00c853", "✓"),
    "warning": ("#ffaa00", "!"),
    "error": ("#e53935", "✕"),
}
MAX_LIST_LINES = 10  # Längere Listen bekommen eine Scrollleiste


def show_message(master: tk.Misc, title: str, message: str, kind: str = "info",
                 items: Optional[List[str]] = None, notes: Optional[List[str]] = None,
                 wait: bool = True) -> tk.Toplevel:
    """
    Zeigt ein dunkles Hinweisfenster. `items` (z.B. fehlende Karten) erscheinen als Liste,
    `notes` als "Hinweise" darunter. Mit wait=True blockiert der Aufruf bis zum Schließen
    (wie messagebox), der Rest des Programms bleibt dabei bedienbar.
    """
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

    if items:
        _bullet_list(body, items, fg=TEXT, font=font, scale=scale)
    if notes:
        tk.Label(body, text="Hinweise", fg=accent, bg=BG, font=font_bold,
                 anchor="w").pack(fill=tk.X, pady=(int(10 * scale), 2))
        for note in notes:
            tk.Label(body, text=f"• {note}", fg=MUTED, bg=BG, font=font, wraplength=wrap,
                     justify=tk.LEFT, anchor="w").pack(fill=tk.X, pady=1)

    ok = RoundedButton(win, text="OK", command=win.destroy, bg=BUTTON, font=font_bold,
                       padx=int(30 * scale), pady=int(5 * scale), radius=int(7 * scale))
    ok.pack(pady=(0, int(12 * scale)))
    win.ok_button = ok  # für Tests
    win.bind("<Return>", lambda e: win.destroy())
    win.bind("<Escape>", lambda e: win.destroy())

    _center_on_screen(win)
    apply_frame(win)  # runde Ecken + Goldrand wie das Overlay
    win.focus_force()
    if wait:
        win.grab_set()
        master.wait_window(win)
    return win


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
        bar = tk.Scrollbar(frame, command=text.yview)
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
