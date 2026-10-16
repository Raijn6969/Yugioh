"""
Verlauf (Button "Verlauf" im Deck-Fenster): alle importierten Decks, das letzte oben, mit Suche.

Gespeichert wird in der lokalen Datenbank (card_stats, Tabelle deck_history) – bleibt also auch nach
einem Neustart. Gesucht wird in Deck-Name, Archetypen und Kartennamen (englisch und in der Spielsprache);
mehrere Wörter müssen alle passen. Klick auf eine Zeile wählt das Deck; die Knöpfe oben wirken auf das gewählte
Deck: laden (im Deck-Fenster anzeigen, Analyse aus dem Deck-Code), erneut importieren, Deck-Code kopieren oder aus
dem Verlauf löschen.
"""

import time
import tkinter as tk
from typing import Callable, List, Optional

import md_layout
from card_db import CardMatch
from card_stats import CardStatsDB, HistoryEntry
from dark_dialog import ask_choice
from dark_scrollbar import DarkScrollbar
from deck_export import DeckScan, ExportedCard
from rounded_button import RoundedButton
from starter_rules import EXTRA_FRAMES
from utils import parse_deck_code
from window_style import (SIZE_SMALL, SIZE_TEXT, SIZE_TITLE, font, BG, PANEL, TEXT, MUTED, GOLD, INFO, WARN, ROW_ALT,
                          HIGHLIGHT, LINE, NEUTRAL, DANGER, allow_typing)

PLACEHOLDER = "Suchen: Deck, Archetyp oder Karte …"
SHOWN = 50  # mehr Zeilen kosten spürbar Zeit; die Suche geht über alle
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


def when(timestamp: float, short: bool = False) -> str:
    """'heute 14:32', 'gestern 09:10' oder '28.09.2026 18:05' (short: '28.09. 18:05')"""
    day = time.strftime("%Y-%m-%d", time.localtime(timestamp))
    today = time.strftime("%Y-%m-%d")
    yesterday = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    clock = time.strftime("%H:%M", time.localtime(timestamp))
    if day == today:
        return f"heute {clock}"
    if day == yesterday:
        return f"gestern {clock}"
    return time.strftime("%d.%m. %H:%M" if short else "%d.%m.%Y %H:%M", time.localtime(timestamp))


class HistoryPanel:
    def __init__(self, master: tk.Misc, db: CardStatsDB, on_import: Callable[[str], None],
                 on_copy: Callable[[str], None], on_close: Callable[[], None],
                 on_open: Callable[[HistoryEntry], None] = lambda entry: None, state: Optional[dict] = None):
        self.master = master
        self.db = db
        self.on_open = on_open
        self.on_import = on_import
        self.on_copy = on_copy
        self.on_close = on_close
        self.closed = False
        self.entries: List[HistoryEntry] = []
        self._search_job = None
        self.selected: Optional[int] = None  # id des gewählten Decks
        self.state: dict = {}                # Auswahl, Suche, Scrollstand beim Schließen (für "← Übersicht")
        self._rows_by_id = {}                # id → (Zeile, Grundfarbe, Widgets)
        self.s = s = max(1.0, master.winfo_screenheight() / 1080.0)
        self.font = font(SIZE_TEXT, s)
        self.font_bold = font(SIZE_TEXT, s, bold=True)
        self.font_small = font(SIZE_SMALL, s)
        self.font_head = font(SIZE_TITLE, s, bold=True)

        self.win = tk.Frame(master, bg=BG)  # Ansicht im Deck-Fenster (extras_panel packt sie)
        self._build()
        if state:
            self.query.set(state.get("query", ""))
            self.selected = state.get("selected")
        self.refresh(scroll=(state or {}).get("scroll", 0.0))

    # ── Aufbau ──
    def _build(self) -> None:
        s = self.s
        body = tk.Frame(self.win, bg=BG, padx=int(12 * s), pady=int(8 * s))
        body.pack(fill=tk.BOTH, expand=True)
        self.query = tk.StringVar(master=self.win)
        self.search = tk.Entry(body, textvariable=self.query, font=self.font, bg=PANEL, fg=TEXT,
                               insertbackground=TEXT, relief=tk.FLAT, highlightthickness=1,
                               highlightbackground=NEUTRAL, highlightcolor=INFO)
        self.search.pack(fill=tk.X, ipady=int(4 * s))
        self.placeholder = tk.Label(self.search, text=PLACEHOLDER, fg=MUTED, bg=PANEL, font=self.font)
        self.placeholder.place(x=int(4 * s), rely=0.5, anchor="w")
        allow_typing(self.search)
        # Klick auf den Platzhaltertext = Klick ins Suchfeld
        self.placeholder.bind("<Button-1>", lambda e: self.search.event_generate("<Button-1>"))
        self.query.trace_add("write", lambda *_: self._on_query_changed())

        # Knöpfe wirken auf das gewählte Deck (Klick auf eine Zeile)
        bar = tk.Frame(body, bg=BG)
        bar.pack(fill=tk.X, pady=(int(6 * s), 0))
        small = dict(font=self.font_small, padx=int(9 * s), pady=int(3 * s), radius=int(6 * s))
        self.action_buttons = {}
        for label, role, action in (("Laden", "neutral", self._open), ("Importieren", "primary", self._import),
                                    ("Kopieren", "neutral", self._copy), ("Löschen", "danger", self._delete)):
            button = RoundedButton(bar, text=label, command=lambda a=action: self._act(a), role=role, **small)
            button.pack(side=tk.LEFT, padx=(0, int(6 * s)))
            self.action_buttons[label] = button

        self.count_label = tk.Label(body, text="", fg=MUTED, bg=BG, font=self.font_small, anchor="w")
        self.count_label.pack(fill=tk.X, pady=(int(4 * s), int(2 * s)))

        list_frame = tk.Frame(body, bg=BG)
        list_frame.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(list_frame, bg=BG, highlightthickness=0, bd=0)
        bar = DarkScrollbar(list_frame, command=self.canvas.yview, bg=BG, scale=self.s)
        self.canvas.config(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self._inner_id = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.config(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._inner_id, width=e.width))
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    # ── Liste ──
    def _on_query_changed(self) -> None:
        if self.query.get():
            self.placeholder.place_forget()
        else:
            self.placeholder.place(x=int(4 * self.s), rely=0.5, anchor="w")
        if self._search_job is not None:
            self.win.after_cancel(self._search_job)
        self._search_job = self.win.after(SEARCH_DELAY_MS, self.refresh)

    def _show_count(self) -> None:
        query = self.query.get().strip()
        if query:
            self.count_label.config(text=f"{len(self.entries)} Treffer für „{query}“", fg=MUTED)
        else:
            count = len(self.entries)
            self.count_label.config(text=f"{count} Deck{'' if count == 1 else 's'} – das letzte oben", fg=MUTED)

    def refresh(self, scroll: float = 0.0) -> None:
        self._search_job = None
        if self.closed:
            return
        query = self.query.get().strip()
        try:
            self.entries = self.db.history(query)
        except Exception as e:  # Datenbank gesperrt/defekt → Fenster bleibt benutzbar
            self.entries = []
            self.count_label.config(text=f"Verlauf nicht lesbar: {e}", fg=WARN)
        else:
            self._show_count()
        for child in self.inner.winfo_children():
            child.destroy()
        if not self.entries:
            text = "Nichts gefunden" if query else "Noch keine Decks importiert"
            tk.Label(self.inner, text=text, fg=MUTED, bg=BG, font=self.font).pack(pady=int(20 * self.s))
        self.rows = []
        self._rows_by_id = {}
        for i, entry in enumerate(self.entries[:SHOWN]):
            self.rows.append(self._add_row(entry, ROW_ALT if i % 2 else BG, latest=(i == 0 and not query)))
        if self.selected not in self._rows_by_id:  # gewähltes Deck nicht mehr sichtbar (Suche): Auswahl aufheben
            self.selected = None
        if len(self.entries) > SHOWN:
            tk.Label(self.inner, text=f"+ {len(self.entries) - SHOWN} weitere – Suche nutzen", fg=MUTED, bg=BG,
                     font=self.font_small).pack(pady=int(8 * self.s))
        self._paint_all()
        if scroll:  # erst nach dem Layout (der Scrollbereich kennt die Zeilen dann)
            self.win.after(20, lambda: None if self.closed else self.canvas.yview_moveto(scroll))
        else:
            self.canvas.yview_moveto(0)

    def _add_row(self, entry: HistoryEntry, bg: str, latest: bool) -> tk.Frame:
        s = self.s
        row = tk.Frame(self.inner, bg=bg, padx=int(8 * s), pady=int(5 * s), highlightthickness=1,
                       highlightbackground=bg)
        row.pack(fill=tk.X)
        text = tk.Frame(row, bg=bg)
        text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        top = tk.Frame(text, bg=bg)
        top.pack(fill=tk.X)
        if latest:
            tk.Label(top, text="ZULETZT", fg=BG, bg=GOLD, font=font(SIZE_SMALL, s, bold=True),
                     padx=int(4 * s)).pack(side=tk.LEFT, padx=(0, int(6 * s)))
        tk.Label(top, text=entry.name, fg=TEXT, bg=bg, font=self.font_bold, anchor="w").pack(side=tk.LEFT)
        details = [when(entry.imported_at), f"{entry.main} Main · {entry.extra} Extra"]
        if entry.times > 1:
            details.append(f"{entry.times}× importiert")
        if entry.result != "ok":
            details.append("⚠ mit Lücken")
        tk.Label(text, text=" · ".join(details), fg=WARN if entry.result != "ok" else MUTED, bg=bg,
                 font=self.font_small, anchor="w").pack(fill=tk.X)
        # Klick auf die Zeile wählt das Deck (die Knöpfe oben wirken darauf)
        clickable = [row, text, top] + [w for w in (*text.winfo_children(), *top.winfo_children())
                                        if isinstance(w, tk.Label)]
        self._rows_by_id[entry.id] = (row, bg, clickable)
        for widget in clickable:
            widget.config(cursor="hand2")
            widget.bind("<Button-1>", lambda e, i=entry.id: self._select(i))
            widget.bind("<Enter>", lambda e, i=entry.id: self._paint(i, hover=True))
            widget.bind("<Leave>", lambda e, i=entry.id: self._paint(i))
        return row

    def _paint(self, entry_id: int, hover: bool = False) -> None:
        """Zeile einfärben: gewählt = HIGHLIGHT mit Goldrand, unter der Maus = LINE, sonst Grundfarbe."""
        row, base, widgets = self._rows_by_id[entry_id]
        chosen = entry_id == self.selected
        color = HIGHLIGHT if chosen else LINE if hover else base
        row.config(highlightbackground=GOLD if chosen else base)
        for widget in widgets:
            if widget.cget("bg") != GOLD:  # "ZULETZT" bleibt gold
                widget.config(bg=color)

    def _paint_all(self) -> None:
        for entry_id in self._rows_by_id:
            self._paint(entry_id)

    def _select(self, entry_id: int) -> None:
        self.selected = entry_id
        self._paint_all()
        self._show_count()  # nimmt eine Meldung "Bitte Deck auswählen" zurück

    def _act(self, action: Callable[[HistoryEntry], None]) -> None:
        """Knopf oben: auf das gewählte Deck anwenden, ohne Auswahl Hinweis in der Statuszeile."""
        entry = next((e for e in self.entries if e.id == self.selected), None)
        if entry is None:
            self.count_label.config(text="Bitte Deck auswählen", fg=WARN)
            return
        action(entry)

    def get_state(self) -> dict:
        return {"query": self.query.get(), "selected": self.selected, "scroll": self.canvas.yview()[0]}

    def _open(self, entry: HistoryEntry) -> None:
        self.close()
        self.on_open(entry)

    def _import(self, entry: HistoryEntry) -> None:
        code = entry.code
        self.close()
        self.on_import(code)

    def _copy(self, entry: HistoryEntry) -> None:
        self.on_copy(entry.code)

    def _delete(self, entry: HistoryEntry) -> None:
        if not ask_choice(self.master, "Deck löschen", f"„{entry.name}“ ({when(entry.imported_at)}) löschen?",
                          [("Löschen", True, DANGER), ("Abbrechen", None, NEUTRAL)], kind="warning"):
            return
        self.db.delete_history(entry.id)
        self.selected = None
        self.refresh()

    def _on_wheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120) * 2, "units")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.state = self.get_state()
            self.canvas.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass
        self.win.destroy()
        self.on_close()
