"""
Verlauf (Button "Verlauf" im Deck-Fenster): alle importierten Decks, das letzte oben, mit Suche.

Gespeichert wird in der lokalen Datenbank (card_stats, Tabelle deck_history) – bleibt also auch nach
einem Neustart. Gesucht wird in Deck-Name, Archetypen und Kartennamen (englisch und in der Spielsprache);
mehrere Wörter müssen alle passen. Klick auf ein Deck: im Deck-Fenster anzeigen (Analyse aus dem Deck-Code).
Pro Deck außerdem: erneut importieren, Deck-Code kopieren oder aus dem Verlauf löschen.
"""

import time
import tkinter as tk
from typing import Callable, List, Optional

import md_layout
from card_db import CardMatch
from card_stats import CardStatsDB, HistoryEntry
from deck_export import DeckScan, ExportedCard
from extras_panel import BG, GOLD, HIGHLIGHT, MUTED, PANEL, ROW_ALT, TEXT
from hover_card import AMBER, NEON
from rounded_button import RoundedButton
from starter_rules import EXTRA_FRAMES
from utils import parse_deck_code
from window_style import allow_typing, apply_frame, no_activate

PLACEHOLDER = "Suchen: Deck, Archetyp oder Karte …"
SEARCH_DELAY_MS = 150  # Erst suchen, wenn kurz nicht getippt wurde


def deck_from_history(code: str, db: CardStatsDB) -> DeckScan:
    """
    Deck aus einem gespeicherten Deck-Code wie einen Scan aufbauen (für die Analyse im Deck-Fenster).
    Main/Extra nach den Kartendaten, Reihenfolge wie im Code. Ohne Bildschirm: keine Positionen im Spiel.
    """
    ids = parse_deck_code(code)
    if not ids:
        raise ValueError("kein Deck-Code")
    zones = {"Main": [], "Extra": []}
    for cid in ids:
        info = db.info(cid)
        extra = info is not None and (info.frame or "").lower().startswith(EXTRA_FRAMES)
        name = info.name if info is not None else f"Karte {cid}"
        zone = "Extra" if extra else "Main"
        zones[zone].append(ExportedCard(zone, len(zones[zone]) + 1, name, CardMatch(cid, name, True)))
    layout = [(zone, [(0, 0)] * len(cards), md_layout.MIN_COLUMNS) for zone, cards in zones.items()]
    return DeckScan(None, layout, zones["Main"] + zones["Extra"], {})


def when(timestamp: float) -> str:
    """'heute 14:32', 'gestern 09:10' oder '28.09.2026 18:05'"""
    day = time.strftime("%Y-%m-%d", time.localtime(timestamp))
    today = time.strftime("%Y-%m-%d")
    yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    clock = time.strftime("%H:%M", time.localtime(timestamp))
    if day == today:
        return f"heute {clock}"
    if day == yesterday:
        return f"gestern {clock}"
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(timestamp))


class HistoryPanel:
    def __init__(self, master: tk.Misc, db: CardStatsDB, on_import: Callable[[str], None],
                 on_copy: Callable[[str], None], on_close: Callable[[], None],
                 on_open: Callable[[HistoryEntry], None] = lambda entry: None, anchor: Optional[tk.Misc] = None):
        self.master = master
        self.db = db
        self.on_open = on_open
        self.on_import = on_import
        self.on_copy = on_copy
        self.on_close = on_close
        self.closed = False
        self.entries: List[HistoryEntry] = []
        self._search_job = None
        self.s = s = max(1.0, master.winfo_screenheight() / 1080.0)
        self.font = ("Helvetica", int(10 * s))
        self.font_bold = ("Helvetica", int(10 * s), "bold")
        self.font_small = ("Helvetica", int(8 * s))
        self.font_head = ("Helvetica", int(12 * s), "bold")

        if anchor is not None:
            # Lage des Buttons jetzt bestimmen: Ein update_idletasks() nach dem Anlegen würde das neue Fenster schon
            # zeigen, bevor es platziert ist – kurz oben links in der Ecke, und Windows blendet im Vollbild die
            # Taskleiste ein.
            anchor.update_idletasks()
        self.win = tk.Toplevel(master, bg=BG)
        self.win.overrideredirect(True)
        self.win.wm_attributes("-topmost", True)
        self._build()
        self._place(anchor)
        apply_frame(self.win)
        no_activate(self.win)  # Klicks lassen Master Duel aktiv (sonst Taskleiste über dem Spiel)
        self.win.bind("<Escape>", lambda e: self.close())
        self.refresh()

    # ── Aufbau ──
    def _build(self) -> None:
        s = self.s
        header = tk.Frame(self.win, bg=PANEL)
        header.pack(fill=tk.X)
        tk.Label(header, text="◆ VERLAUF", fg=GOLD, bg=PANEL, font=self.font_head).pack(
            side=tk.LEFT, padx=(int(12 * s), int(6 * s)), pady=int(8 * s))
        tk.Label(header, text="Importierte Decks", fg=MUTED, bg=PANEL, font=self.font).pack(side=tk.LEFT)
        RoundedButton(header, text="✕", command=self.close, bg="#cc0000", border="#ff8a80",
                      font=("Helvetica", int(9 * s), "bold"), padx=int(9 * s), pady=int(2 * s),
                      radius=int(6 * s)).pack(side=tk.RIGHT, padx=int(8 * s))

        body = tk.Frame(self.win, bg=BG, padx=int(12 * s), pady=int(8 * s))
        body.pack(fill=tk.BOTH, expand=True)
        self.query = tk.StringVar(master=self.win)
        self.search = tk.Entry(body, textvariable=self.query, font=self.font, bg=PANEL, fg=TEXT,
                               insertbackground=TEXT, relief=tk.FLAT, highlightthickness=1,
                               highlightbackground="#444444", highlightcolor=NEON)
        self.search.pack(fill=tk.X, ipady=int(4 * s))
        self.placeholder = tk.Label(self.search, text=PLACEHOLDER, fg=MUTED, bg=PANEL, font=self.font)
        self.placeholder.place(x=int(4 * s), rely=0.5, anchor="w")
        allow_typing(self.search)
        # Klick auf den Platzhaltertext = Klick ins Suchfeld
        self.placeholder.bind("<Button-1>", lambda e: self.search.event_generate("<Button-1>"))
        self.query.trace_add("write", lambda *_: self._on_query_changed())

        self.count_label = tk.Label(body, text="", fg=MUTED, bg=BG, font=self.font_small, anchor="w")
        self.count_label.pack(fill=tk.X, pady=(int(4 * s), int(2 * s)))

        list_frame = tk.Frame(body, bg=BG)
        list_frame.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(list_frame, bg=BG, highlightthickness=0, bd=0)
        bar = tk.Scrollbar(list_frame, command=self.canvas.yview)
        self.canvas.config(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self._inner_id = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.config(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._inner_id, width=e.width))
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _place(self, anchor: Optional[tk.Misc]) -> None:
        """Unter dem Button "Verlauf", rechtsbündig mit dem Deck-Fenster (liegt über dessen Liste)."""
        s = self.s
        w, h = int(480 * s), int(460 * s)
        screen_w, screen_h = self.master.winfo_screenwidth(), self.master.winfo_screenheight()
        if anchor is not None:
            top = anchor.winfo_toplevel()
            x = top.winfo_rootx() + top.winfo_width() - w - int(8 * s)
            y = anchor.winfo_rooty() + anchor.winfo_height() + int(6 * s)
        else:
            x, y = screen_w - w - int(60 * s), screen_h - h - int(120 * s)
        x = min(max(0, x), screen_w - w)
        y = min(max(0, y), screen_h - h)
        self.win.geometry(f"{w}x{h}+{x}+{y}")

    # ── Liste ──
    def _on_query_changed(self) -> None:
        if self.query.get():
            self.placeholder.place_forget()
        else:
            self.placeholder.place(x=int(4 * self.s), rely=0.5, anchor="w")
        if self._search_job is not None:
            self.win.after_cancel(self._search_job)
        self._search_job = self.win.after(SEARCH_DELAY_MS, self.refresh)

    def refresh(self) -> None:
        self._search_job = None
        if self.closed:
            return
        query = self.query.get().strip()
        try:
            self.entries = self.db.history(query)
        except Exception as e:  # Datenbank gesperrt/defekt → Fenster bleibt benutzbar
            self.entries = []
            self.count_label.config(text=f"Verlauf nicht lesbar: {e}", fg=AMBER)
        else:
            if query:
                self.count_label.config(text=f"{len(self.entries)} Treffer für „{query}“", fg=MUTED)
            else:
                self.count_label.config(text=f"{len(self.entries)} Deck(s) – das letzte oben", fg=MUTED)
        for child in self.inner.winfo_children():
            child.destroy()
        if not self.entries:
            text = "Nichts gefunden" if query else "Noch keine Decks importiert"
            tk.Label(self.inner, text=text, fg=MUTED, bg=BG, font=self.font).pack(pady=int(20 * self.s))
        self.rows = []
        for i, entry in enumerate(self.entries):
            self.rows.append(self._add_row(entry, ROW_ALT if i % 2 else BG, latest=(i == 0 and not query)))
        self.canvas.yview_moveto(0)

    def _add_row(self, entry: HistoryEntry, bg: str, latest: bool) -> None:
        s = self.s
        row = tk.Frame(self.inner, bg=bg, padx=int(8 * s), pady=int(5 * s))
        row.pack(fill=tk.X)
        buttons = tk.Frame(row, bg=bg)
        buttons.pack(side=tk.RIGHT)
        small = dict(font=self.font_small, padx=int(8 * s), pady=int(3 * s), radius=int(6 * s))
        RoundedButton(buttons, text="Importieren", command=lambda: self._import(entry), bg="#007acc",
                      **small).pack(side=tk.LEFT, padx=(0, int(4 * s)))
        RoundedButton(buttons, text="Kopieren", command=lambda: self.on_copy(entry.code), bg="#444444",
                      **small).pack(side=tk.LEFT, padx=(0, int(4 * s)))
        RoundedButton(buttons, text="✕", command=lambda: self._delete(entry), bg="#5a1f1f", border="#8a4a4a",
                      **small).pack(side=tk.LEFT)

        text = tk.Frame(row, bg=bg)
        text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        top = tk.Frame(text, bg=bg)
        top.pack(fill=tk.X)
        if latest:
            tk.Label(top, text="ZULETZT", fg=BG, bg=GOLD, font=("Helvetica", int(7 * s), "bold"),
                     padx=int(4 * s)).pack(side=tk.LEFT, padx=(0, int(6 * s)))
        tk.Label(top, text=entry.name, fg=TEXT, bg=bg, font=self.font_bold, anchor="w").pack(side=tk.LEFT)
        details = [when(entry.imported_at), f"{entry.main} Main · {entry.extra} Extra"]
        if entry.times > 1:
            details.append(f"{entry.times}× importiert")
        if entry.result != "ok":
            details.append("⚠ mit Lücken")
        tk.Label(text, text="  ·  ".join(details), fg=AMBER if entry.result != "ok" else MUTED, bg=bg,
                 font=self.font_small, anchor="w").pack(fill=tk.X)
        # Klick auf das Deck (nicht auf die Buttons) → im Deck-Fenster anzeigen
        clickable = [row, text, top] + [w for w in (*text.winfo_children(), *top.winfo_children())
                                        if isinstance(w, tk.Label)]
        for widget in clickable:
            widget.config(cursor="hand2")
            widget.bind("<Button-1>", lambda e: self._open(entry))
            widget.bind("<Enter>", lambda e, ws=clickable: self._shade(ws, HIGHLIGHT))
            widget.bind("<Leave>", lambda e, ws=clickable, c=bg: self._shade(ws, c))
        return row

    @staticmethod
    def _shade(widgets, color: str) -> None:
        for widget in widgets:
            if widget.cget("bg") != GOLD:  # "ZULETZT" bleibt gold
                widget.config(bg=color)

    def _open(self, entry: HistoryEntry) -> None:
        self.close()
        self.on_open(entry)

    def _import(self, entry: HistoryEntry) -> None:
        code = entry.code
        self.close()
        self.on_import(code)

    def _delete(self, entry: HistoryEntry) -> None:
        self.db.delete_history(entry.id)
        self.refresh()

    def _on_wheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120) * 2, "units")

    # ── Zustand von außen ──
    def set_visible(self, visible: bool) -> None:
        """Ausblenden, wenn weder Master Duel noch der Importer vorne ist (wie das Overlay)."""
        if not self.closed:
            self.win.wm_attributes("-alpha", 1.0 if visible else 0.0)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.canvas.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass
        self.win.destroy()
        self.on_close()
