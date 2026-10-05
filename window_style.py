"""
Einheitlicher Fensterrahmen für Overlay, Meldungen und Kalibrierung: runde Ecken und ein
dünner Goldrand (wie die Rahmen der Karten). Unter Windows 11 zeichnet Windows selbst
(DWM) – gestochen scharf und auch bei halbtransparenten Fenstern. Unter Windows 10 gibt
es keine runden Ecken; dann zeichnet Tk einen gleichfarbigen 1-px-Rand.
"""

import tkinter as tk

import win_api

GOLD = "#c9a02f"


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


def no_activate(window: tk.Misc) -> None:
    """
    Klicks in dieses Fenster nehmen dem Spiel nicht den Fokus (WS_EX_NOACTIVATE). Sonst wird beim Klick das Fenster
    des Importers aktiv, Master Duel ist nicht mehr das aktive Vollbild-Programm – und Windows blendet die Taskleiste
    über dem Spiel ein. Buttons funktionieren weiter; tippen lässt sich nur in Feldern mit allow_typing().
    """
    window.update_idletasks()
    try:
        win_api.set_ex_style(int(window.wm_frame(), 16), win_api.WS_EX_NOACTIVATE)
    except (tk.TclError, ValueError, OSError):
        pass  # ohne den Stil funktioniert alles, nur evtl. mit Taskleiste


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
    Schließt ein Menü (Fenster mit no_activate()), sobald irgendwo außerhalb geklickt wird. Ersetzt das Schließen
    bei Fokusverlust – ohne Fokus gibt es keinen. Klicks auf `anchor` (den Button, der das Menü öffnet) zählen nicht:
    der schließt es selbst.
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

