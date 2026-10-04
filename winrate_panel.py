"""
Winrate (Button "Winrate" im Deck-Fenster): Siege und Niederlagen der eigenen Duelle in Master Duel.

Oben die Winrate gesamt, als Erster und als Zweiter, darunter die Decks (nach Archetypen benannt, dazu der Name in
Master Duel) und die letzten Matches mit dem Deck des Gegners (sobald bekannt). Klick auf ein Deck: nur dieses Deck
(noch ein Klick: wieder alle); „Anzeigen“ zeigt das Deck im Deck-Fenster. Erfasst werden die Duelle von
match_history im Speicher-Modus.
"""

import tkinter as tk
from typing import Callable, Optional

from card_stats import CardStatsDB, DeckWinStats, MatchEntry, WinStats
from extras_panel import BG, GOLD, HIGHLIGHT, MUTED, PANEL, ROW_ALT, TEXT
from history_panel import when
from hover_card import AMBER, GREEN, NEON, RED
from match_history import DRAW, FINISH_SURRENDER, RANKED, WIN, mode_name
from rounded_button import RoundedButton
from window_style import apply_frame

MODE_KEY = "WINRATE_MODE"  # Einstellung: "ranked" oder "all"
MATCH_LIMIT = 50
UNKNOWN_DECK = "Unbekanntes Deck"


def rate_text(wins: int, matches: int) -> str:
    return f"{wins / matches * 100:.0f} %" if matches else "–"


def record_text(stats: WinStats) -> str:
    """'13–8' bzw. '13–8–1' mit Unentschieden"""
    text = f"{stats.wins}–{stats.losses}"
    return text + f"–{stats.draws}" if stats.draws else text


def summary(stats: WinStats) -> str:
    """'62 % (13–8) · Erster 70 % · Zweiter 55 %' – für das Deck-Fenster und die Deck-Zeilen."""
    parts = [f"{rate_text(stats.wins, stats.matches)} ({record_text(stats)})"]
    if stats.first:
        parts.append(f"Erster {rate_text(stats.first_wins, stats.first)}")
    if stats.second:
        parts.append(f"Zweiter {rate_text(stats.second_wins, stats.second)}")
    return " · ".join(parts)


class WinratePanel:
    def __init__(self, master: tk.Misc, db: CardStatsDB, settings: dict, save_settings: Callable[[], None],
                 on_open: Callable[[str, str], None], on_close: Callable[[], None], tracking: bool,
                 anchor: Optional[tk.Misc] = None):
        self.master = master
        self.db = db
        self.settings = settings
        self.save_settings = save_settings
        self.on_open = on_open        # (Deck-Name, Deck-Code) → im Deck-Fenster anzeigen
        self.on_close = on_close
        self.tracking = tracking      # Speicher-Modus an → Matches werden übernommen
        self.closed = False
        self.selected: Optional[str] = None   # gewähltes Deck, None = alle
        self.s = s = max(1.0, master.winfo_screenheight() / 1080.0)
        self.font = ("Helvetica", int(10 * s))
        self.font_bold = ("Helvetica", int(10 * s), "bold")
        self.font_small = ("Helvetica", int(8 * s))
        self.font_small_bold = ("Helvetica", int(8 * s), "bold")
        self.font_head = ("Helvetica", int(12 * s), "bold")
        self.font_big = ("Consolas", int(18 * s), "bold")

        self.win = tk.Toplevel(master, bg=BG)
        self.win.overrideredirect(True)
        self.win.wm_attributes("-topmost", True)
        self._build()
        self._place(anchor)
        apply_frame(self.win)
        self.win.bind("<Escape>", lambda e: self.close())
        self.refresh()

    @property
    def mode(self) -> Optional[int]:
        return None if self.settings.get(MODE_KEY) == "all" else RANKED

    # ── Aufbau ──
    def _build(self) -> None:
        s = self.s
        header = tk.Frame(self.win, bg=PANEL)
        header.pack(fill=tk.X)
        tk.Label(header, text="◆ WINRATE", fg=GOLD, bg=PANEL, font=self.font_head).pack(
            side=tk.LEFT, padx=(int(12 * s), int(6 * s)), pady=int(8 * s))
        tk.Label(header, text="deine Duelle", fg=MUTED, bg=PANEL, font=self.font).pack(side=tk.LEFT)
        RoundedButton(header, text="✕", command=self.close, bg="#cc0000", border="#ff8a80",
                      font=("Helvetica", int(9 * s), "bold"), padx=int(9 * s), pady=int(2 * s),
                      radius=int(6 * s)).pack(side=tk.RIGHT, padx=int(8 * s))
        small = dict(font=self.font_small, padx=int(8 * s), pady=int(3 * s), radius=int(6 * s))
        self.mode_buttons = {}
        for key, label in (("all", "Alle Modi"), ("ranked", "Ranked")):  # rechts nach links
            button = RoundedButton(header, text=label, command=lambda k=key: self._set_mode(k), **small)
            button.pack(side=tk.RIGHT, padx=(0, int(4 * s)))
            self.mode_buttons[key] = button

        body = tk.Frame(self.win, bg=BG, padx=int(12 * s), pady=int(8 * s))
        body.pack(fill=tk.BOTH, expand=True)
        self.info_label = tk.Label(body, text="", fg=MUTED, bg=BG, font=self.font_small, anchor="w",
                                   justify=tk.LEFT, wraplength=int(500 * s))
        self.info_label.pack(fill=tk.X)

        tiles = tk.Frame(body, bg=BG)
        tiles.pack(fill=tk.X, pady=(int(6 * s), int(4 * s)))
        self.tiles = {}
        for key, caption in (("all", "GESAMT"), ("first", "ALS ERSTER"), ("second", "ALS ZWEITER")):
            tile = tk.Frame(tiles, bg=PANEL, padx=int(8 * s), pady=int(4 * s))
            tile.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, int(6 * s)))
            value = tk.Label(tile, text="–", fg=TEXT, bg=PANEL, font=self.font_big)
            value.pack()
            record = tk.Label(tile, text="", fg=MUTED, bg=PANEL, font=self.font_small)
            record.pack()
            tk.Label(tile, text=caption, fg=MUTED, bg=PANEL, font=self.font_small).pack()
            self.tiles[key] = (value, record)
        self.filter_label = tk.Label(body, text="", fg=NEON, bg=BG, font=self.font_small, anchor="w")
        self.filter_label.pack(fill=tk.X)

        list_frame = tk.Frame(body, bg=BG)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(int(4 * s), 0))
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
        """Unter dem Button "Winrate", rechtsbündig mit dem Deck-Fenster (liegt über dessen Liste)."""
        s = self.s
        w, h = int(520 * s), int(600 * s)
        screen_w, screen_h = self.master.winfo_screenwidth(), self.master.winfo_screenheight()
        if anchor is not None:
            anchor.update_idletasks()
            top = anchor.winfo_toplevel()
            x = top.winfo_rootx() + top.winfo_width() - w - int(8 * s)
            y = anchor.winfo_rooty() + anchor.winfo_height() + int(6 * s)
        else:
            x, y = screen_w - w - int(60 * s), screen_h - h - int(120 * s)
        x = min(max(0, x), screen_w - w)
        y = min(max(0, y), screen_h - h)
        self.win.geometry(f"{w}x{h}+{x}+{y}")

    # ── Inhalt ──
    def _set_mode(self, key: str) -> None:
        self.settings[MODE_KEY] = key
        self.save_settings()
        self.refresh()

    def select(self, deck_name: Optional[str]) -> None:
        self.selected = None if deck_name == self.selected else deck_name
        self.refresh()

    def refresh(self) -> None:
        if self.closed:
            return
        for key, button in self.mode_buttons.items():
            active = (key == "all") == (self.mode is None)
            button.config(bg="#007acc" if active else "#444444")
        try:
            decks = self.db.deck_win_stats(self.mode)
            if self.selected not in {d.deck_name for d in decks}:
                self.selected = None
            stats = self.db.win_stats(self.selected, self.mode)
            matches = self.db.matches(self.selected, self.mode, MATCH_LIMIT)
        except Exception as e:  # Datenbank gesperrt/defekt → Fenster bleibt benutzbar
            self.info_label.config(text=f"Matches nicht lesbar: {e}", fg=RED)
            return
        self._show_info(stats)
        self._show_tiles(stats)
        mode = "Ranked" if self.mode == RANKED else "alle Modi"
        self.filter_label.config(text=f"Nur „{self.selected}“ · {mode} – Klick aufs Deck zeigt wieder alle"
                                 if self.selected else f"Alle Decks · {mode}")
        for child in self.inner.winfo_children():
            child.destroy()
        if decks:
            self._section(f"DECKS ({len(decks)})")
            for i, deck in enumerate(decks):
                self._deck_row(deck, ROW_ALT if i % 2 else BG)
        if matches:
            self._section("LETZTE MATCHES")
            for i, match in enumerate(matches):
                self._match_row(match, ROW_ALT if i % 2 else BG)
        self.canvas.yview_moveto(0)

    def _show_info(self, stats: WinStats) -> None:
        if not self.tracking:
            text, color = ("Matches werden nur mit „Speicher lesen“ übernommen (Deck → Optionen → Karten lesen).",
                           AMBER)
        elif not stats.matches:
            text, color = ("Noch keine Matches. Jedes Duell wird am Ergebnis-Bildschirm erfasst. Damit dein Deck "
                           "erkannt wird, in Master Duel einmal die Deck-Auswahl öffnen.", MUTED)
        else:
            text, color = (f"{stats.matches} Match(es), zuletzt {when(stats.last_played)} · Duelle werden am "
                           f"Ergebnis-Bildschirm erfasst; ein neues Deck einmal in der Deck-Auswahl ansehen.", MUTED)
        self.info_label.config(text=text, fg=color)

    def _show_tiles(self, stats: WinStats) -> None:
        for key, wins, matches in (("all", stats.wins, stats.matches), ("first", stats.first_wins, stats.first),
                                   ("second", stats.second_wins, stats.second)):
            value, record = self.tiles[key]
            rate = wins / matches if matches else None
            color = TEXT if rate is None else GREEN if rate >= 0.5 else AMBER
            value.config(text=rate_text(wins, matches), fg=color)
            record.config(text=f"{wins}–{matches - wins} · {matches} Match(es)" if matches else "keine Matches")

    def _section(self, title: str) -> None:
        tk.Label(self.inner, text=title, fg=GOLD, bg=BG, font=self.font_small_bold, anchor="w").pack(
            fill=tk.X, pady=(int(8 * self.s), int(2 * self.s)))

    def _deck_row(self, deck: DeckWinStats, bg: str) -> None:
        s = self.s
        bg = HIGHLIGHT if deck.deck_name == self.selected else bg
        row = tk.Frame(self.inner, bg=bg, padx=int(8 * s), pady=int(4 * s))
        row.pack(fill=tk.X)
        if deck.deck_code:
            RoundedButton(row, text="Anzeigen", command=lambda: self._open(deck), bg="#00796b",
                          font=self.font_small, padx=int(8 * s), pady=int(3 * s), radius=int(6 * s)).pack(side=tk.RIGHT)
        text = tk.Frame(row, bg=bg)
        text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        title = deck.deck_name or UNKNOWN_DECK
        if deck.md_deck:
            title += f"  ·  „{deck.md_deck}“"
        tk.Label(text, text=title, fg=TEXT, bg=bg, font=self.font_bold, anchor="w").pack(fill=tk.X)
        tk.Label(text, text=f"{summary(deck.stats)} · zuletzt {when(deck.stats.last_played)}", fg=MUTED, bg=bg,
                 font=self.font_small, anchor="w").pack(fill=tk.X)
        for widget in (row, text, *text.winfo_children()):
            widget.config(cursor="hand2")
            widget.bind("<Button-1>", lambda e: self.select(deck.deck_name))

    def _match_row(self, match: MatchEntry, bg: str) -> None:
        s = self.s
        row = tk.Frame(self.inner, bg=bg, padx=int(8 * s), pady=int(3 * s))
        row.pack(fill=tk.X)
        result, color = (("SIEG", GREEN) if match.result == WIN else ("REMIS", AMBER) if match.result == DRAW
                         else ("NIEDERLAGE", RED))
        tk.Label(row, text=result, fg=color, bg=bg, font=self.font_small_bold, width=11, anchor="w").pack(
            side=tk.LEFT)
        order = {True: "Erster", False: "Zweiter"}.get(match.first, "?")
        tk.Label(row, text=order, fg=TEXT, bg=bg, font=self.font_small, width=7, anchor="w").pack(side=tk.LEFT)
        text = tk.Frame(row, bg=bg)
        text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        title = match.deck_name or match.md_deck or UNKNOWN_DECK
        if match.opp_name:
            title += f"  vs  {match.opp_name}"
        tk.Label(text, text=title, fg=TEXT, bg=bg, font=self.font_small, anchor="w").pack(fill=tk.X)
        details = [when(match.played_at)]
        if match.turns:
            details.append(f"{match.turns} Züge" if match.turns != 1 else "1 Zug")
        details.append(mode_name(match.mode))
        if match.finish == FINISH_SURRENDER:
            details.append("Aufgabe")
        tk.Label(text, text="  ·  ".join(details), fg=MUTED, bg=bg, font=self.font_small, anchor="w").pack(fill=tk.X)

    def _open(self, deck: DeckWinStats) -> None:
        self.on_open(deck.deck_name, deck.deck_code)

    def _on_wheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120) * 2, "units")

    # ── Zustand von außen ──
    def set_visible(self, visible: bool) -> None:
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
