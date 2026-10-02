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
