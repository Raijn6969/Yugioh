"""
Futuristisches Info-Fenster neben einer Deck-Karte (Extras-Menü).

Erst läuft eine kurze "Analyse…"-Animation (Name wird "entschlüsselt", Ladebalken, Scanlinie),
dann erscheinen Ziehchance und Stats. Das Fenster nimmt Master Duel nie den Fokus und lässt
Mausklicks durch. Es wird nur einmal erzeugt und über die Transparenz ein-/ausgeblendet, weil
das Neu-Anzeigen eines Fensters unter Windows den Fokus stehlen kann.
"""

import random
import time
import tkinter as tk
from typing import Callable, List, NamedTuple, Optional, Tuple

import win_api

BG = "#070d18"
NEON = "#00e5ff"
NEON_DIM = "#0b4f5c"
SCAN = "#0f5f6d"
TEXT = "#e6f7ff"
MUTED = "#6f8fa3"
GOLD = "#e8c25a"
GREEN = "#39ff9f"
AMBER = "#ffb347"
RED = "#ff5c7a"

ANALYSIS_TIME = 1.0   # Sekunden "Analyse…" bevor die Stats erscheinen
FRAME_MS = 30         # Animation ~33 Bilder/s, nur solange sie läuft
SEGMENTS = 24         # Ladebalken-Segmente
ALPHA = 0.99  # < 1: Fenster bleibt 'layered' – nur dann gehen Mausklicks hindurch (WS_EX_TRANSPARENT)
GLYPHS = "ABCDEF0123456789#$%&*<>/\\=+"


class HoverRow(NamedTuple):
    label: str
    value: str
    color: str = TEXT
    bar: Optional[float] = None  # 0–1: Balken für Wahrscheinlichkeiten


class HoverContent(NamedTuple):
    title: str
    subtitle: str
    rows: List[HoverRow]
    badge: Optional[Tuple[str, str]] = None   # (Text, Farbe), z.B. ("STARTER ✓", GREEN)
    badge_note: str = ""
    warning: str = ""


class HoverCard:
    def __init__(self, master: tk.Misc, scale: float = 1.0):
        self.s = scale
        self.width = int(300 * scale)
        self.pad = int(14 * scale)
        self.font_small = ("Consolas", int(8 * scale))
        self.font_mono = ("Consolas", int(9 * scale))
        self.font_mono_bold = ("Consolas", int(10 * scale), "bold")
        self.font_title = ("Segoe UI", int(11 * scale), "bold")

        self.win = tk.Toplevel(master, bg=BG)
        self.win.overrideredirect(True)
        self.win.wm_attributes("-topmost", True)
        # Transparenz nur einmal setzen: Ein-/Ausblenden über die Transparenz lässt Windows im Vollbild jedes Mal
        # umschalten, wie das Spielbild ausgegeben wird (ruckelt). Ausgeblendet wird durch Wegschieben.
        self.win.wm_attributes("-alpha", ALPHA)
        self.canvas = tk.Canvas(self.win, width=self.width, height=int(120 * scale), bg=BG,
                                highlightthickness=0, bd=0)
        self.canvas.pack()
        self.win.geometry("+-10000+-10000")
        self.win.update_idletasks()
        self._hwnd = None
        try:
            hwnd = self._hwnd = int(self.win.wm_frame(), 16)
            win_api.set_ex_style(hwnd, win_api.WS_EX_NOACTIVATE | win_api.WS_EX_TRANSPARENT | win_api.WS_EX_TOOLWINDOW)
        except (tk.TclError, ValueError, OSError):
            pass  # ohne die Stile funktioniert es trotzdem, nur evtl. mit Fokuswechsel

        self.visible = False
        self.hidden_at = 0.0                 # Zeitpunkt des letzten Ausblendens
        self.phase: Optional[str] = None     # "loading" oder "done"
        self.content: Optional[HoverContent] = None
        self._job = None
        self._name = ""
        self._compute: Optional[Callable[[], HoverContent]] = None
        self._start = 0.0
        self._duration = ANALYSIS_TIME
        self._anchor = (0, 0, 0, 0)
        self._geometry = ""                  # zuletzt gesetzte Position/Größe (nur bei Änderung neu setzen)
        self._loading_items: dict = {}       # Elemente der Lade-Animation (einmal anlegen, dann nur ändern)
        self._filled = -1

    # ── Steuerung ──
    def show(self, anchor: Tuple[int, int, int, int], name: str, compute: Callable[[], HoverContent],
             duration: float = ANALYSIS_TIME) -> None:
        """anchor: Bildschirm-Rechteck der Karte. compute() liefert die Stats am Ende der Analyse."""
        self._cancel()
        self._anchor, self._name, self._compute, self._duration = anchor, name, compute, duration
        self._start = time.monotonic()
        self.phase, self.content = "loading", None
        if duration > 0:
            self._build_loading()
        self._animate()
        self.visible = True
        self._raise()

    def _raise(self) -> None:
        """Vor das Deck-Fenster/Overlay holen: Unter "immer oben"-Fenstern liegt vorne, was zuletzt aktiv war."""
        if self._hwnd:
            try:
                win_api.raise_topmost(self._hwnd)
            except OSError:
                pass

    def hide(self) -> None:
        self._cancel()
        self.phase = None
        if self.visible:
            self.win.geometry("+-10000+-10000")
            self._geometry = ""
            self.visible = False
            self.hidden_at = time.monotonic()

    def idle_for(self, seconds: float) -> bool:
        """Seit mindestens `seconds` unsichtbar? (dann liegt es sicher nicht mehr über dem Deck)"""
        return not self.visible and time.monotonic() - self.hidden_at >= seconds

    def destroy(self) -> None:
        self._cancel()
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def _cancel(self) -> None:
        if self._job is not None:
            try:
                self.win.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None

    # ── Ablauf ──
    def _animate(self) -> None:
        self._job = None
        progress = min(1.0, (time.monotonic() - self._start) / self._duration) if self._duration > 0 else 1.0
        if progress >= 1.0:
            self.phase = "done"
            try:
                self.content = self._compute()
            except Exception as e:  # Stats kaputt → trotzdem etwas Sinnvolles zeigen
                self.content = HoverContent(self._name, "", [], warning=f"Analyse fehlgeschlagen: {e}")
            self._draw_result(self.content)
            return
        self._draw_loading(progress)
        self._job = self.win.after(FRAME_MS, self._animate)

    def _place(self, height: int) -> None:
        left, top, right, _ = self._anchor
        gap = int(10 * self.s)
        vx, vy, vw, vh = win_api.virtual_screen_rect()
        x = right + gap
        if x + self.width > vx + vw:
            x = left - gap - self.width  # rechts kein Platz → links neben die Karte
        y = max(vy, min(top - int(4 * self.s), vy + vh - height))
        geometry = f"{self.width}x{height}+{x}+{y}"
        if geometry != self._geometry:  # Fenster verschieben kostet über einem laufenden Spiel FPS
            self.canvas.config(height=height)
            self.win.geometry(geometry)
            self._geometry = geometry

    # ── Zeichnen ──
    def _frame(self, height: int, color: str = NEON) -> None:
        c, w, s = self.canvas, self.width, self.s
        c.create_rectangle(1, 1, w - 2, height - 2, outline=NEON_DIM)
        arm, t = int(12 * s), max(2, int(2 * s))
        for x, y, dx, dy in ((1, 1, 1, 1), (w - 2, 1, -1, 1), (1, height - 2, 1, -1), (w - 2, height - 2, -1, -1)):
            c.create_line(x, y, x + dx * arm, y, fill=color, width=t)
            c.create_line(x, y, x, y + dy * arm, fill=color, width=t)

    def _build_loading(self) -> None:
        """Lade-Animation einmal aufbauen; pro Bild werden nur Texte, Farben und die Scanlinie geändert."""
        c, w, s, pad = self.canvas, self.width, self.s, self.pad
        height = int(118 * s)
        self._place(height)
        c.delete("all")
        items = {
            "title": c.create_text(pad, int(14 * s), text="◢ ANALYSE.", fill=NEON, font=self.font_mono_bold,
                                   anchor="w"),
            "percent": c.create_text(w - pad, int(14 * s), text="  0%", fill=NEON, font=self.font_mono_bold,
                                     anchor="e"),
            "name": c.create_text(pad, int(40 * s), text="", fill=TEXT, font=self.font_title, anchor="w",
                                  width=w - 2 * pad),
            "code": c.create_text(pad, int(64 * s), text="", fill=MUTED, font=self.font_small, anchor="w"),
            "segments": [],
        }
        y1, y2 = int(80 * s), int(90 * s)
        seg_w = (w - 2 * pad) / SEGMENTS
        for i in range(SEGMENTS):
            x1 = pad + i * seg_w
            items["segments"].append(c.create_rectangle(x1, y1, x1 + seg_w - max(1, int(2 * s)), y2, width=0,
                                                        fill=NEON_DIM))
        c.create_text(pad, int(104 * s), text="ZIEHCHANCE · STARTER · KOPIEN", fill=NEON_DIM, font=self.font_small,
                      anchor="w")
        items["scan"] = c.create_line(2, 0, w - 3, 0, fill=SCAN)
        self._frame(height)
        self._loading_items = items
        self._loading_height = height
        self._filled = 0

    def _draw_loading(self, progress: float) -> None:
        c, items, now = self.canvas, self._loading_items, time.monotonic()
        c.itemconfigure(items["title"], text="◢ ANALYSE" + "." * (1 + int(now * 6) % 3))
        c.itemconfigure(items["percent"], text=f"{int(progress * 100):3d}%")
        # Name wird von links nach rechts "entschlüsselt"
        reveal = int(len(self._name) * progress)
        c.itemconfigure(items["name"], text=self._name[:reveal] + "".join(
            ch if ch == " " else random.choice(GLYPHS) for ch in self._name[reveal:]))
        c.itemconfigure(items["code"], text=f"SCAN 0x{random.getrandbits(32):08X}  ·  DECK-ABGLEICH")
        filled = int(SEGMENTS * progress)
        for i in range(self._filled, filled):  # nur neu gefüllte Segmente umfärben
            c.itemconfigure(items["segments"][i], fill=NEON)
        self._filled = max(self._filled, filled)
        scan_y = int((now * 160 * self.s) % self._loading_height)
        c.coords(items["scan"], 2, scan_y, self.width - 3, scan_y)

    def _draw_result(self, content: HoverContent) -> None:
        c, w, s, pad = self.canvas, self.width, self.s, self.pad
        c.delete("all")
        y = int(14 * s)
        c.create_text(pad, y, text="◢ ANALYSE ABGESCHLOSSEN", fill=NEON, font=self.font_mono_bold, anchor="w")
        c.create_text(w - pad, y, text="✓", fill=GREEN, font=self.font_mono_bold, anchor="e")
        y += int(16 * s)
        title = c.create_text(pad, y, text=content.title, fill=GOLD, font=self.font_title, anchor="nw",
                              width=w - 2 * pad)
        y = c.bbox(title)[3] + int(2 * s)
        if content.subtitle:
            sub = c.create_text(pad, y, text=content.subtitle, fill=MUTED, font=self.font_small, anchor="nw",
                                width=w - 2 * pad)
            y = c.bbox(sub)[3] + int(2 * s)
        y += int(6 * s)
        c.create_line(pad, y, w - pad, y, fill=NEON_DIM)
        y += int(10 * s)

        label_w = int(118 * s)
        for row in content.rows:
            c.create_text(pad, y, text=row.label, fill=MUTED, font=self.font_small, anchor="w")
            c.create_text(w - pad, y, text=row.value, fill=row.color, font=self.font_mono_bold, anchor="e")
            if row.bar is not None:
                x1, x2 = pad + label_w, w - pad - int(64 * s)
                h = max(2, int(3 * s))
                c.create_rectangle(x1, y - h, x2, y + h, fill=NEON_DIM, width=0)
                c.create_rectangle(x1, y - h, x1 + (x2 - x1) * max(0.0, min(1.0, row.bar)), y + h, fill=row.color,
                                   width=0)
            y += int(19 * s)

        if content.badge:
            text, color = content.badge
            y += int(4 * s)
            label = c.create_text(pad + int(8 * s), y, text=text, fill=color, font=self.font_mono_bold, anchor="w")
            x1, y1, x2, y2 = c.bbox(label)
            box = c.create_rectangle(x1 - int(8 * s), y1 - int(3 * s), x2 + int(8 * s), y2 + int(3 * s),
                                     outline=color, width=max(1, int(s)))
            c.tag_lower(box)
            y = y2 + int(8 * s)
            if content.badge_note:  # Begründung darunter in voller Breite
                note = c.create_text(pad, y, text=content.badge_note, fill=MUTED, font=self.font_small,
                                     anchor="nw", width=w - 2 * pad)
                y = c.bbox(note)[3] + int(6 * s)
        if content.warning:
            warn = c.create_text(pad, y, text=f"⚠ {content.warning}", fill=AMBER, font=self.font_small, anchor="nw",
                                 width=w - 2 * pad)
            y = c.bbox(warn)[3] + int(8 * s)
        height = y + int(4 * s)
        self._place(height)
        self._frame(height, GREEN if not content.warning else AMBER)
