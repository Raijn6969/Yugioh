"""
Einheitlicher Fensterrahmen für Overlay, Meldungen und Kalibrierung: runde Ecken und ein
dünner Rand in Gold (wie die Rückseite der Karten). Unter Windows 11 zeichnet Windows selbst
(DWM) – gestochen scharf und auch bei halbtransparenten Fenstern. Unter Windows 10 gibt
es keine runden Ecken; dann zeichnet Tk einen gleichfarbigen 1-px-Rand.
"""

import tkinter as tk
from typing import Optional, Tuple

import win_api

# Gemeinsame Palette (nur Konstanten); Szenenfarben von duel_view/hover_card bleiben dort.
# Stil "Sammelalbum": warme Brauntöne und Gold der Kartenrückseite.
BG = "#22160f"          # Hintergrund
PANEL = "#2d1e14"       # Fläche (Kopfzeile, Kachel, Menü, Popup)
LINE = "#4a3322"        # Rand, Trenner, Scrollgriff
TEXT = "#f1e6d3"
MUTED = "#b19a80"       # Text gedämpft
GOLD = "#e2b25b"        # Akzent (Titel, Fokus, aktiv)
GOLD_HOVER = "#4f3718"  # dunkles Gold für Hover/aktive Fläche (Gold darauf auch im Hover ≥ 4,5:1)
AMBER = "#d08a35"       # Akzent: Anzahl ×N, Häkchen-Füllung, Hauptknopf
INFO = "#8cc8e8"
GOOD = "#9fd37f"
WARN = "#ffb347"
BAD = "#f08a7a"
HAND = "#f08a7a"        # Handtrap
DIM = "#7d6650"         # nicht zutreffende Einstufung (3,3:1 auf BG)
ROW_ALT = "#2a1c13"     # Zeilenton (jede zweite Zeile)
HIGHLIGHT = "#4d3520"   # hervorgehobene Zeile
PRIMARY = "#8a5019"     # Knopffüllungen: Hauptaktion, neutral, Export, Gefahr/Schließen (weiß darauf ≥ 4,5:1)
NEUTRAL = "#3d2a1c"
SUCCESS = "#2e7d32"
DANGER = "#cc0000"
DECK = "#5e35b1"        # Deck-Knopf der Hauptleiste (weiß darauf 8:1)
# Rahmenfarben der Karten (Bildrand): Schlüssel = CardInfo.frame klein, Präfix genügt; sonst Monster
FRAME_COLORS = {"monster": "#c4733a", "spell": "#18897b", "trap": "#9c3f78", "fusion": "#8a63c9",
                "synchro": "#d8d8d8", "xyz": "#5a5a5a", "link": "#2a5d9c", "ritual": "#4f7fc4"}
# Knopfrollen für RoundedButton(role=…): (Füllung, Schrift, Rand). "ruhig + Akzent": Navigation neutral, der aktive
# Knopf gold, Farbe nur für Hauptaktion (primary/success/accent) und Gefahr.
# Der Rand ist heller als die Füllung und hat gegen PANEL und BG mindestens 3:1 (WCAG für Bedienelemente);
# "nav" und "link" sind flacher Text ohne sichtbaren Rand (Füllung = Rand = Untergrund).
ROLES = {"primary": (PRIMARY, "white", AMBER), "neutral": (NEUTRAL, TEXT, "#8a6a4a"),
         "success": (SUCCESS, "white", "#6fbf73"), "danger": (DANGER, "white", BAD),
         "active": (GOLD_HOVER, GOLD, GOLD), "deck": (DECK, "white", "#a58be0"),
         "nav": (PANEL, MUTED, PANEL), "link": (BG, MUTED, BG), "accent": (AMBER, BG, "#e9b06a")}

# Schrift: Segoe UI, Überschriften Sitka Text (Serife), Kennzahlen Consolas; Größen in pt bei 1080p (× Maßstab s)
FONT = "Segoe UI"
FONT_SERIF = "Sitka Text"
FONT_MONO = "Consolas"
SIZE_TITLE = 12
SIZE_TEXT = 10
SIZE_SMALL = 9         # nie kleiner
SIZE_NUM = 18          # Kennzahl


def frame_color(frame: Optional[str]) -> str:
    """Rahmenfarbe einer Karte aus CardInfo.frame (z.B. "fusion_pendulum" → Fusion, "effect_pendulum" → Monster)."""
    frame = (frame or "").lower()
    return next((color for key, color in FRAME_COLORS.items() if key != "monster" and frame.startswith(key)),
                FRAME_COLORS["monster"])


def font(size: int, scale: float = 1.0, bold: bool = False, mono: bool = False, serif: bool = False) -> tuple:
    """Tk-Schrift (Familie, Größe, [fett]); die Größe wird wie bisher mit int(Basis * Maßstab) gerundet."""
    return (FONT_MONO if mono else FONT_SERIF if serif else FONT, int(size * scale)) + (("bold",) if bold else ())


def _colorref(hex_color: str) -> int:
    """'#RRGGBB' → Windows-COLORREF (0x00BBGGRR)."""
    r, g, b = int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16)
    return (b << 16) | (g << 8) | r


def apply_frame(window: tk.Misc, color: str = GOLD) -> bool:
    """
    Gibt einem rahmenlosen Fenster (overrideredirect) runde Ecken und einen farbigen Rand.
    Aufrufen, nachdem das Fenster angezeigt wurde. Returns: True, wenn Windows den Rahmen
    zeichnet; False, wenn der Tk-Ersatzrand verwendet wird.
    """
    window.update_idletasks()
    try:
        hwnd = int(window.wm_frame(), 16)
        native = (win_api.set_dwm_attribute(hwnd, win_api.DWMWA_WINDOW_CORNER_PREFERENCE, win_api.DWMWCP_ROUND)
                  and win_api.set_dwm_attribute(hwnd, win_api.DWMWA_BORDER_COLOR, _colorref(color)))
    except (tk.TclError, ValueError, OSError):
        native = False
    if not native:
        window.configure(highlightthickness=1, highlightbackground=color, highlightcolor=color)
    return native


def place_in_work_area(x: int, y: int, width: int, height: int, anchor: Optional[tk.Misc] = None) -> Tuple[int, int]:
    """
    Fensterposition so klemmen, dass das Fenster im Arbeitsbereich (ohne Taskleiste) des Monitors liegt, auf dem der
    Anker (Mitte seines Fensters, sonst x/y) liegt – nicht nur auf dem Hauptmonitor, auch bei Monitoren links davon.
    """
    if anchor is not None:
        top = anchor.winfo_toplevel()
        point = top.winfo_rootx() + top.winfo_width() // 2, top.winfo_rooty() + top.winfo_height() // 2
    else:
        point = x, y
    area = win_api.monitor_work_area(*point)
    if area is None:
        return x, y
    left, top_edge, right, bottom = area
    return min(max(left, x), right - width), min(max(top_edge, y), bottom - height)


def no_activate(window: tk.Misc) -> None:
    """
    Klicks in dieses Fenster nehmen dem Spiel nicht den Fokus (WS_EX_NOACTIVATE). Sonst wird beim Klick das Fenster
    des Importers aktiv, Master Duel ist nicht mehr das aktive Vollbild-Programm – und Windows blendet die Taskleiste
    über dem Spiel ein. Buttons funktionieren weiter; tippen lässt sich nur in Feldern mit allow_typing().
    """
    window.update_idletasks()
    try:
        # Kein Eintrag in Taskleiste/Alt+Tab: über Tk setzen, nicht nur am Fenster – Tk merkt es sich und verliert es
        # beim Ändern der Deckkraft nicht (sonst taucht z. B. das Deck-Fenster in der Taskleiste auf)
        window.wm_attributes("-toolwindow", True)
        win_api.set_ex_style(int(window.wm_frame(), 16), win_api.WS_EX_NOACTIVATE)
        window._no_activate = True  # für set_alpha
    except (tk.TclError, ValueError, OSError):
        pass  # ohne den Stil funktioniert alles, nur evtl. mit Taskleiste


def set_alpha(window: tk.Misc, alpha: float) -> None:
    """Deckkraft ändern, ohne no_activate() zu verlieren: Wird ein voll deckendes Fenster (1.0) durchsichtig, setzt Tk
    die erweiterten Fensterstile neu und verwirft WS_EX_NOACTIVATE."""
    window.wm_attributes("-alpha", alpha)
    if getattr(window, "_no_activate", False):
        try:
            win_api.set_ex_style(int(window.wm_frame(), 16), win_api.WS_EX_NOACTIVATE)
        except (tk.TclError, ValueError, OSError):
            pass


def show_tip(master: tk.Misc, text: str, x: int, y: int, scale: float = 1.0) -> tk.Toplevel:
    """Tooltip mit einer Zeile Text bei (x, y) in Bildschirmkoordinaten; der Aufrufer zerstört ihn beim Verlassen."""
    tip = tk.Toplevel(master)
    tip.overrideredirect(True)
    tip.attributes("-topmost", True)
    tk.Label(tip, text=text, fg=TEXT, bg=PANEL, font=font(SIZE_SMALL, scale), padx=int(8 * scale),
             pady=int(4 * scale), bd=1, relief=tk.SOLID, justify=tk.LEFT).pack()
    tip.geometry(f"+{x}+{y}")
    return tip


def allow_typing(field: tk.Widget) -> None:
    """
    Eingabefeld in einem Fenster mit no_activate(): Erst beim Hineinklicken darf das Fenster aktiv werden (sonst
    kommt keine Tastatur an). Verliert das Feld den Fokus wieder, nimmt das Fenster dem Spiel wieder nichts weg.
    """
    def frame() -> int:
        return int(field.winfo_toplevel().wm_frame(), 16)

    def on_click(_event) -> None:
        try:
            win_api.set_ex_style(frame(), win_api.WS_EX_NOACTIVATE, False)
            field.focus_force()
        except (tk.TclError, ValueError, OSError):
            pass

    def on_focus_out(_event) -> None:
        try:
            win_api.set_ex_style(frame(), win_api.WS_EX_NOACTIVATE)
        except (tk.TclError, ValueError, OSError):
            pass

    field.bind("<Button-1>", on_click, add="+")
    field.bind("<FocusOut>", on_focus_out, add="+")


class ClickOutside:
    """
    Schließt ein Menü (Fenster mit no_activate()), sobald irgendwo außerhalb geklickt oder Escape gedrückt wird.
    Ersetzt das Schließen bei Fokusverlust – ohne Fokus gibt es keinen. Klicks auf `anchor` (den Button, der das
    Menü öffnet) zählen nicht: der schließt es selbst.
    """

    POLL_MS = 40

    def __init__(self, window: tk.Misc, anchor: tk.Misc, on_outside, is_open):
        self.window, self.anchor, self.on_outside, self.is_open = window, anchor, on_outside, is_open
        self._was_down = win_api.mouse_button_down()  # Klick, der das Menü geöffnet hat, noch gedrückt
        self.window.after(self.POLL_MS, self._check)

    @staticmethod
    def _inside(widget: tk.Misc, x: int, y: int) -> bool:
        try:
            left, top = widget.winfo_rootx(), widget.winfo_rooty()
            return left <= x < left + widget.winfo_width() and top <= y < top + widget.winfo_height()
        except tk.TclError:
            return False

    def _check(self) -> None:
        if not self.is_open():
            return
        if win_api.escape_down():  # das Menü hat keinen Fokus → Escape lässt sich nur so abfangen
            self.on_outside()
            return
        down = win_api.mouse_button_down()
        if down and not self._was_down:  # neuer Klick
            x, y = win_api.get_cursor_pos()
            if not (self._inside(self.window, x, y) or self._inside(self.anchor, x, y)):
                self.on_outside()
                return
        self._was_down = down
        try:
            self.window.after(self.POLL_MS, self._check)
        except tk.TclError:
            pass

