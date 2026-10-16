"""
Winrate (Button "Winrate" im Deck-Fenster): Siege und Niederlagen der eigenen Duelle in Master Duel.

Oben die Winrate gesamt, als Erster und als Zweiter, darunter der Münzwurf (wie oft gewonnen, Winrate nach gewonnenem
bzw. verlorenem Münzwurf, aktuelle Serie). In der Liste: der Verlauf (Winrate der jeweils letzten 10 Matches), die
Bilanz gegen die Gegner-Decks, die eigenen Decks (nach Archetypen benannt, dazu der Name in Master Duel) und die
letzten Matches mit dem Deck des Gegners (sobald bekannt). Filter: Ranked/alle Modi und Zeitraum (heute, 7 Tage,
Saison, alle). Klick auf ein Deck: nur dieses Deck (noch ein Klick: wieder alle); „Anzeigen“ zeigt das Deck im
Deck-Fenster. Erfasst werden die Duelle von match_history im Speicher-Modus. Die Abschnitte (Münzwurf, Verlauf,
Gegner-Decks, eigene Decks, letzte Matches) lassen sich ein- und ausklappen.
Knopf „Rückblick“: die Bilanz eines Monats (review_panel).
"""

import datetime
import time
import tkinter as tk
from typing import Callable, List, Optional

from card_stats import CardStatsDB, DeckWinStats, MatchEntry, OpponentStats, WinStats
from dark_scrollbar import DarkScrollbar
from history_panel import when
from match_history import DRAW, FINISH_SURRENDER, HISTORY_SIZE, RANKED, REMIND_AT, WIN, history_reminder, mode_name
from rounded_button import RoundedButton
from season_review import DAY_START_HOUR  # "Heute" beginnt um 5 Uhr: Wer nach Mitternacht weiterspielt, bleibt im Tag
from window_style import (SIZE_NUM, SIZE_SMALL, SIZE_TEXT, SIZE_TITLE, font, BG, PANEL, TEXT, MUTED, GOLD, INFO, GOOD,
                          WARN, BAD, ROW_ALT, HIGHLIGHT)

MODE_KEY = "WINRATE_MODE"  # Einstellung: "ranked" oder "all"
PERIOD_KEY = "WINRATE_PERIOD"  # Einstellung: Schlüssel aus PERIODS
COLLAPSED_KEY = "WINRATE_COLLAPSED"  # Einstellung: {section_key: bool}
PERIODS = (("today", "Heute"), ("week", "7 Tage"), ("season", "Saison"), ("all", "Alle"))
MATCH_LIMIT = 20
OPPONENT_LIMIT = 12
TREND_WINDOW = 10   # Verlauf: Winrate der jeweils letzten 10 Matches
TREND_MIN = 3       # so viele Matches braucht der Verlauf mindestens
UNKNOWN_DECK = "Unbekanntes Deck"


def period_start(period: str, now: Optional[float] = None) -> Optional[float]:
    """Beginn des Zeitraums (Unix-Zeit, lokale Uhr); None = alle Matches. Saison = Ranked-Saison in Master Duel
    (ein Kalendermonat)."""
    now = time.time() if now is None else now
    if period == "week":
        return now - 7 * 86400
    current = datetime.datetime.fromtimestamp(now)
    if period == "today":
        start = current.replace(hour=DAY_START_HOUR, minute=0, second=0, microsecond=0)
        if current < start:
            start -= datetime.timedelta(days=1)
        return start.timestamp()
    if period == "season":
        return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp()
    return None


def day_summary(db: CardStatsDB, mode: Optional[int], now: Optional[float] = None) -> str:
    """'Heute 5–2' für die Statuszeile ("" ohne Match heute)."""
    stats = db.win_stats(mode=mode, since=period_start("today", now))
    return f"Heute {record_text(stats)}" if stats.matches else ""


def trend(matches: List[MatchEntry]) -> List[tuple]:
    """Verlauf: [(Match, Winrate der letzten TREND_WINDOW Matches bis hierhin)], das älteste zuerst (Matches: das
    letzte zuerst, wie aus der Datenbank). Am Anfang über alle bisherigen Matches."""
    ordered = list(reversed(matches))
    points = []
    for i, match in enumerate(ordered):
        window = ordered[max(0, i - TREND_WINDOW + 1):i + 1]
        points.append((match, sum(m.result == WIN for m in window) / len(window)))
    return points


def rate_text(wins: int, matches: int) -> str:
    return f"{wins / matches * 100:.0f} %" if matches else "–"


def matches_text(count: int) -> str:
    return f"{count} Match" if count == 1 else f"{count} Matches"


def record_text(stats: WinStats) -> str:
    """'13–8' bzw. '13–8–1' mit Unentschieden"""
    text = f"{stats.wins}–{stats.losses}"
    return text + f"–{stats.draws}" if stats.draws else text


def summary(stats: WinStats) -> str:
    """'62 % · 13–8 · Erster 70 % · Zweiter 55 %' – für das Deck-Fenster und die Deck-Zeilen."""
    parts = [f"{rate_text(stats.wins, stats.matches)} · {record_text(stats)}"]
    if stats.first:
        parts.append(f"Erster {rate_text(stats.first_wins, stats.first)}")
    if stats.second:
        parts.append(f"Zweiter {rate_text(stats.second_wins, stats.second)}")
    return " · ".join(parts)


def coin_streak(matches: List[MatchEntry]) -> Optional[tuple]:
    """(gewonnen?, Anzahl) der letzten Münzwürfe mit gleichem Ausgang (Matches: das letzte zuerst; unbekannte
    Münzwürfe zählen nicht); None ohne bekannten Münzwurf."""
    known = [m.coin for m in matches if m.coin is not None]
    if not known:
        return None
    count = next((i for i, coin in enumerate(known) if coin != known[0]), len(known))
    return known[0], count


def coin_note(stats: WinStats, streak: Optional[tuple]) -> str:
    """Satz unter den Münzwurf-Kacheln: aktuelle Serie bzw. Hinweis ohne Daten."""
    if not stats.coin_known:
        return ("Münzwurf: für diese Matches nicht erfasst – wird seit V9.2 bei jedem Duell mitgeschrieben."
                if stats.matches else "")
    if streak is not None and streak[1] >= 2:
        return f"Münzwurf zuletzt {streak[1]}× in Folge {'gewonnen' if streak[0] else 'verloren'}"
    return ""


class WinratePanel:
    def __init__(self, master: tk.Misc, db: CardStatsDB, settings: dict, save_settings: Callable[[], None],
                 on_open: Callable[[str, str], None], on_close: Callable[[], None], tracking: bool):
        self.master = master
        self.db = db
        self.settings = settings
        self.save_settings = save_settings
        self.on_open = on_open        # (Deck-Name, Deck-Code) → im Deck-Fenster anzeigen
        self.on_close = on_close
        self.tracking = tracking      # Speicher-Modus an → Matches werden übernommen
        self.closed = False
        self.duel = None                       # Duell-Übersicht (duel_view), None = zu
        self.review = None                     # Monatsrückblick (review_panel), None = zu
        self._shown_matches: List[MatchEntry] = []
        self.selected: Optional[str] = None   # gewähltes Deck, None = alle
        self.s = s = max(1.0, master.winfo_screenheight() / 1080.0)
        self.font = font(SIZE_TEXT, s)
        self.font_bold = font(SIZE_TEXT, s, bold=True)
        self.font_small = font(SIZE_SMALL, s)
        self.font_small_bold = font(SIZE_SMALL, s, bold=True)
        self.font_head = font(SIZE_TITLE, s, bold=True)
        self.font_big = font(SIZE_NUM, s, bold=True, mono=True)
        self._section_headers = {}   # key -> (header_frame, arrow_label)
        self._section_items = {}     # key -> [(widget, pack_kwargs), ...]

        self.win = tk.Frame(master, bg=BG)  # Ansicht im Deck-Fenster (extras_panel packt sie)
        self._build()
        self.refresh()

    def is_collapsed(self, key: str) -> bool:
        collapsed = self.settings.get(COLLAPSED_KEY)
        if isinstance(collapsed, dict):
            return bool(collapsed.get(key, False))
        return False

    def set_collapsed(self, key: str, value: bool) -> None:
        collapsed = self.settings.setdefault(COLLAPSED_KEY, {})
        if not isinstance(collapsed, dict):
            collapsed = self.settings[COLLAPSED_KEY] = {}
        collapsed[key] = value
        self.save_settings()

    def _hover_header(self, arrow: tk.Label, title: tk.Label, hover: bool) -> None:
        fg = HIGHLIGHT if hover else GOLD
        arrow.config(fg=fg)
        title.config(fg=fg)

    def toggle_section(self, key: str) -> None:
        collapsed = not self.is_collapsed(key)
        self.set_collapsed(key, collapsed)
        if key == "coin":
            if collapsed:
                self.coin_arrow.config(text="▶")
                self.coin_body.pack_forget()
            else:
                self.coin_arrow.config(text="▼")
                self.coin_body.pack(fill=tk.X, pady=(0, int(4 * self.s)), before=self.list_frame)
            return

        header_info = self._section_headers.get(key)
        if not header_info:
            return
        header, arrow = header_info
        items = self._section_items.get(key, [])
        if collapsed:
            arrow.config(text="▶")
            for item, _ in items:
                item.pack_forget()
        else:
            arrow.config(text="▼")
            last = header
            for item, pack_opts in items:
                item.pack(**pack_opts, after=last)
                last = item

    @property
    def mode(self) -> Optional[int]:
        return None if self.settings.get(MODE_KEY) == "all" else RANKED

    @property
    def period(self) -> str:
        period = self.settings.get(PERIOD_KEY)
        return period if period in dict(PERIODS) else "all"

    # ── Aufbau ──
    def _build(self) -> None:
        s = self.s
        body = tk.Frame(self.win, bg=BG, padx=int(12 * s), pady=int(8 * s))
        body.pack(fill=tk.BOTH, expand=True)
        small = dict(font=self.font_small, padx=int(8 * s), pady=int(3 * s), radius=int(6 * s))
        filters = tk.Frame(body, bg=BG)
        filters.pack(fill=tk.X, pady=(0, int(6 * s)))
        self.mode_buttons = {}
        for key, label in (("ranked", "Ranked"), ("all", "Alle Modi")):
            button = RoundedButton(filters, text=label, command=lambda k=key: self._set_mode(k), **small)
            button.pack(side=tk.LEFT, padx=(0, int(4 * s)))
            self.mode_buttons[key] = button
        self.review_btn = RoundedButton(filters, text="Rückblick", command=self.toggle_review, **small)
        self.review_btn.pack(side=tk.RIGHT)
        periods = tk.Frame(body, bg=BG)
        periods.pack(fill=tk.X, pady=(0, int(6 * s)))
        tk.Label(periods, text="Zeitraum", fg=MUTED, bg=BG, font=self.font_small).pack(
            side=tk.LEFT, padx=(0, int(6 * s)))
        self.period_buttons = {}
        for key, label in PERIODS:
            button = RoundedButton(periods, text=label, command=lambda k=key: self._set_period(k), **small)
            button.pack(side=tk.LEFT, padx=(0, int(4 * s)))
            self.period_buttons[key] = button
        self.info_label = tk.Label(body, text="", fg=MUTED, bg=BG, font=self.font_small, anchor="w",
                                   justify=tk.LEFT, wraplength=int(500 * s))
        self.info_label.pack(fill=tk.X)

        self.tiles = {}
        # Oberste Kacheln: Gesamt, Als Erster, Als Zweiter
        top_tiles = tk.Frame(body, bg=BG)
        top_tiles.pack(fill=tk.X, pady=(0, int(6 * s)))
        self._tile_row(top_tiles, (("all", "GESAMT"), ("first", "ALS ERSTER"), ("second", "ALS ZWEITER")))

        # Münzwurf (einklappbar)
        self.coin_header = tk.Frame(body, bg=BG, cursor="hand2")
        self.coin_header.pack(fill=tk.X, pady=(int(8 * s), int(2 * s)))
        self.coin_arrow = tk.Label(self.coin_header, text="▼" if not self.is_collapsed("coin") else "▶",
                                   fg=GOLD, bg=BG, font=self.font_small_bold, cursor="hand2")
        self.coin_arrow.pack(side=tk.LEFT, padx=(0, int(4 * s)))
        self.coin_title = tk.Label(self.coin_header, text="MÜNZWURF", fg=GOLD, bg=BG,
                                   font=self.font_small_bold, anchor="w", cursor="hand2")
        self.coin_title.pack(side=tk.LEFT, fill=tk.X, expand=True)

        for w in (self.coin_header, self.coin_arrow, self.coin_title):
            w.bind("<Button-1>", lambda e: self.toggle_section("coin"))
            w.bind("<Enter>", lambda e: self._hover_header(self.coin_arrow, self.coin_title, True))
            w.bind("<Leave>", lambda e: self._hover_header(self.coin_arrow, self.coin_title, False))

        self.coin_body = tk.Frame(body, bg=BG)
        if not self.is_collapsed("coin"):
            self.coin_body.pack(fill=tk.X, pady=(0, int(4 * s)))
        coin_tiles = tk.Frame(self.coin_body, bg=BG)
        coin_tiles.pack(fill=tk.X)
        self._tile_row(coin_tiles, (("coin", "GEWONNEN"), ("coin_won", "SIEGE WENN GEWONNEN"),
                                    ("coin_lost", "SIEGE WENN VERLOREN")))
        self.coin_label = tk.Label(self.coin_body, text="", fg=TEXT, bg=BG, font=self.font_small, anchor="w",
                                   justify=tk.LEFT, wraplength=int(500 * s))
        self.coin_label.pack(fill=tk.X, pady=(int(2 * s), 0))

        self.filter_label = tk.Label(body, text="", fg=INFO, bg=BG, font=self.font_small, anchor="w")

        self.list_frame = list_frame = tk.Frame(body, bg=BG)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(int(4 * s), 0))
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

    def _tile_row(self, parent: tk.Frame, captions) -> None:
        s = self.s
        for column, (key, caption) in enumerate(captions):  # gleich breite Spalten: beide Reihen untereinander
            parent.columnconfigure(column, weight=1, uniform="tile")
            tile = tk.Frame(parent, bg=PANEL, padx=int(8 * s), pady=int(4 * s))
            tile.grid(row=0, column=column, sticky="ew", padx=(0, int(6 * s)))
            value = tk.Label(tile, text="–", fg=TEXT, bg=PANEL, font=self.font_big)
            value.pack()
            record = tk.Label(tile, text="", fg=MUTED, bg=PANEL, font=self.font_small)
            record.pack()
            tk.Label(tile, text=caption, fg=MUTED, bg=PANEL, font=self.font_small).pack()
            self.tiles[key] = (value, record)

    # ── Inhalt ──
    def _set_mode(self, key: str) -> None:
        self.settings[MODE_KEY] = key
        self.save_settings()
        self.refresh()

    def _set_period(self, key: str) -> None:
        self.settings[PERIOD_KEY] = key
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
            button.config(role="active" if active else "neutral")
        for key, button in self.period_buttons.items():
            button.config(role="active" if key == self.period else "neutral")
        since = period_start(self.period)
        try:
            decks = self.db.deck_win_stats(self.mode, since)
            if self.selected not in {d.deck_name for d in decks}:
                self.selected = None
            stats = self.db.win_stats(self.selected, self.mode, since)
            matches = self.db.matches(self.selected, self.mode, MATCH_LIMIT, since)
            self._shown_matches = matches
            try:
                self._replay_by_match = self.db.replay_starts_by_match()
            except Exception:  # ältere Datenbank ohne Mitschnitte
                self._replay_by_match = {}
            opponents = self.db.opponent_stats(self.selected, self.mode, since)
        except Exception as e:  # Datenbank gesperrt/defekt → Fenster bleibt benutzbar
            self.info_label.config(text=f"Matches nicht lesbar: {e}", fg=BAD)
            return
        self._show_info(stats)
        self._show_tiles(stats)
        note = coin_note(stats, coin_streak(matches))
        self.coin_label.config(text=note)
        if note:
            self.coin_label.pack(fill=tk.X, pady=(int(2 * self.s), 0))
        else:
            self.coin_label.pack_forget()
        if self.is_collapsed("coin"):
            self.coin_body.pack_forget()
            self.coin_arrow.config(text="▶")
        else:
            self.coin_body.pack(fill=tk.X, pady=(0, int(4 * self.s)), before=self.list_frame)
            self.coin_arrow.config(text="▼")
        if self.selected:  # ohne Auswahl wiederholte die Zeile nur die Filterknöpfe
            self.filter_label.config(text=f"Nur „{self.selected}“ – Klick aufs Deck zeigt wieder alle")
            self.filter_label.pack(fill=tk.X, before=self.list_frame)
        else:
            self.filter_label.config(text="")
            self.filter_label.pack_forget()
        self._section_headers.clear()
        self._section_items.clear()
        for child in self.inner.winfo_children():
            child.destroy()
        if len(matches) >= TREND_MIN:
            self._section("trend", f"VERLAUF · Winrate der jeweils letzten {TREND_WINDOW} Matches")
            chart = self._trend_chart(trend(matches))
            self._register_item("trend", chart, {"anchor": "w", "pady": (0, int(4 * self.s))})
        if opponents:
            shown = opponents[:OPPONENT_LIMIT]
            more = f", {len(shown)} häufigste" if len(opponents) > len(shown) else ""
            self._section("opponents", f"GEGEN GEGNER-DECKS ({len(opponents)}{more})")
            for i, opponent in enumerate(shown):
                row = self._opponent_row(opponent, ROW_ALT if i % 2 else BG)
                self._register_item("opponents", row, {"fill": tk.X})
        if decks:
            self._section("decks", f"DECKS ({len(decks)})")
            for i, deck in enumerate(decks):
                row = self._deck_row(deck, ROW_ALT if i % 2 else BG)
                self._register_item("decks", row, {"fill": tk.X})
        if matches:
            self._section("matches", "LETZTE MATCHES")
            for i, match in enumerate(matches):
                row = self._match_row(match, ROW_ALT if i % 2 else BG, i)
                self._register_item("matches", row, {"fill": tk.X})
        self.canvas.yview_moveto(0)

    def _show_info(self, stats: WinStats) -> None:
        if not self.tracking:
            text, color = ("Matches werden nur mit „Speicher lesen“ übernommen (Deck → Optionen → Karten lesen).",
                           WARN)
        elif not stats.matches and self.period != "all":
            text, color = (f"Keine Matches im Zeitraum „{dict(PERIODS)[self.period]}“.", MUTED)
        elif not stats.matches:
            text, color = ("Noch keine Matches. Jedes Duell wird am Ergebnis-Bildschirm erfasst. Damit dein Deck "
                           "erkannt wird, in Master Duel einmal die Deck-Auswahl öffnen.", MUTED)
        else:
            text, color = (f"{matches_text(stats.matches)} · zuletzt {when(stats.last_played, short=True)}", MUTED)
            try:
                unconfirmed = self.db.unconfirmed_matches()
            except Exception:
                unconfirmed = 0
            if unconfirmed >= REMIND_AT[0]:  # im Fenster durchgehend, nicht nur bei 15/18 wie die Meldung
                text = history_reminder(unconfirmed) or (
                    f"{unconfirmed} Matches seit dem letzten Blick in die Match History – bitte bald öffnen "
                    f"(sie zeigt nur die letzten {HISTORY_SIZE}, sonst fehlen die Gegner-Decks).")
                color = WARN
        self.info_label.config(text=text, fg=color)

    def _show_tiles(self, stats: WinStats) -> None:
        coin, coin_record = self.tiles["coin"]
        coin.config(text=rate_text(stats.coin_won, stats.coin_known), fg=TEXT)
        coin_record.config(text=f"{stats.coin_won} von {stats.coin_known}" if stats.coin_known else "keine Daten")
        for key, wins, matches in (("all", stats.wins, stats.matches), ("first", stats.first_wins, stats.first),
                                    ("second", stats.second_wins, stats.second),
                                    ("coin_won", stats.coin_won_wins, stats.coin_won),
                                    ("coin_lost", stats.coin_lost_wins, stats.coin_lost)):
            value, record = self.tiles[key]
            rate = wins / matches if matches else None
            color = TEXT if rate is None else GOOD if rate >= 0.5 else WARN
            value.config(text=rate_text(wins, matches), fg=color)
            record.config(text=f"{wins}–{matches - wins} · {matches_text(matches)}" if matches else "keine Matches")

    def _section(self, key_or_title: str, title: Optional[str] = None) -> None:
        s = self.s
        if title is None:
            key = key_or_title.split()[0].lower()
            title = key_or_title
        else:
            key = key_or_title
        collapsed = self.is_collapsed(key)
        header = tk.Frame(self.inner, bg=BG, cursor="hand2")
        header.pack(fill=tk.X, pady=(int(8 * s), int(2 * s)))
        arrow = tk.Label(header, text="▼" if not collapsed else "▶", fg=GOLD, bg=BG,
                         font=self.font_small_bold, cursor="hand2")
        arrow.pack(side=tk.LEFT, padx=(0, int(4 * s)))
        title_lbl = tk.Label(header, text=title, fg=GOLD, bg=BG, font=self.font_small_bold,
                             anchor="w", cursor="hand2")
        title_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)

        for w in (header, arrow, title_lbl):
            w.bind("<Button-1>", lambda e, k=key: self.toggle_section(k))
            w.bind("<Enter>", lambda e, a=arrow, t=title_lbl: self._hover_header(a, t, True))
            w.bind("<Leave>", lambda e, a=arrow, t=title_lbl: self._hover_header(a, t, False))

        self._section_headers[key] = (header, arrow)
        self._section_items[key] = []

    def _register_item(self, key: str, widget: tk.Widget, pack_opts: dict) -> None:
        if key in self._section_items:
            self._section_items[key].append((widget, pack_opts))
            if not self.is_collapsed(key):
                widget.pack(**pack_opts)

    def _deck_row(self, deck: DeckWinStats, bg: str) -> tk.Frame:
        s = self.s
        bg = HIGHLIGHT if deck.deck_name == self.selected else bg
        row = tk.Frame(self.inner, bg=bg, padx=int(8 * s), pady=int(4 * s))
        if deck.deck_code:
            RoundedButton(row, text="Anzeigen", command=lambda: self._open(deck),
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
        return row

    def _opponent_row(self, opponent: OpponentStats, bg: str) -> tk.Frame:
        s = self.s
        row = tk.Frame(self.inner, bg=bg, padx=int(8 * s), pady=int(3 * s))
        stats = opponent.stats  # feste Spalten, rechtsbündig: die Zahlen stehen untereinander
        mono = font(SIZE_SMALL, s, mono=True)
        for width, text in ((12, f"Zweiter {rate_text(stats.second_wins, stats.second)}" if stats.second else ""),
                            (11, f"Erster {rate_text(stats.first_wins, stats.first)}" if stats.first else ""),
                            (8, record_text(stats)), (4, rate_text(stats.wins, stats.matches))):
            tk.Label(row, text=text, fg=MUTED, bg=bg, font=mono, width=width, anchor="e").pack(side=tk.RIGHT)
        tk.Label(row, text=f"vs {opponent.opp_name}", fg=TEXT, bg=bg, font=self.font_small_bold, anchor="w").pack(
            side=tk.LEFT, fill=tk.X, expand=True)
        return row

    def _trend_chart(self, points: List[tuple]) -> tk.Canvas:
        """Linie: Winrate der jeweils letzten TREND_WINDOW Matches (älteste links); Hover zeigt das Match."""
        s = self.s
        w, h = int(470 * s), int(96 * s)
        left, right, top, bottom = int(44 * s), int(12 * s), int(10 * s), int(18 * s)
        chart = tk.Canvas(self.inner, width=w, height=h, bg=BG, highlightthickness=0, bd=0)
        plot_w, plot_h = w - left - right, h - top - bottom
        x_of = lambda i: left + plot_w * i / max(1, len(points) - 1)  # noqa: E731
        y_of = lambda rate: top + plot_h * (1 - rate)  # noqa: E731
        for rate, label in ((1.0, "100 %"), (0.5, "50 %"), (0.0, "0 %")):  # zurückhaltendes Raster
            y = y_of(rate)
            chart.create_line(left, y, w - right, y, fill=PANEL, dash=(2, 3) if rate == 0.5 else ())
            chart.create_text(left - int(5 * s), y, text=label, anchor="e", fill=MUTED, font=self.font_small)
        chart.create_text(left, h - int(2 * s), text="älter", anchor="sw", fill=MUTED, font=self.font_small)
        chart.create_text(w - right, h - int(2 * s), text="neuestes", anchor="se", fill=MUTED, font=self.font_small)
        coords = [value for i, (_, rate) in enumerate(points) for value in (x_of(i), y_of(rate))]
        chart.create_line(*coords, fill=INFO, width=2, capstyle=tk.ROUND, joinstyle=tk.ROUND)
        radius = int(4 * s)
        last_x, last_y = x_of(len(points) - 1), y_of(points[-1][1])
        chart.create_oval(last_x - radius, last_y - radius, last_x + radius, last_y + radius, fill=INFO,
                          outline=BG, width=2)
        chart.create_text(last_x - int(7 * s), last_y - int(7 * s), text=rate_text(round(points[-1][1] * 100), 100),
                          anchor="se" if points[-1][1] < 0.85 else "ne", fill=TEXT, font=self.font_small_bold)

        def hover(event):
            chart.delete("hover")
            i = min(len(points) - 1, max(0, round((event.x - left) / plot_w * (len(points) - 1))))
            match, rate = points[i]
            x, y = x_of(i), y_of(rate)
            chart.create_line(x, top, x, top + plot_h, fill=MUTED, tags="hover")
            chart.create_oval(x - radius, y - radius, x + radius, y + radius, fill=INFO, outline=BG, width=2,
                              tags="hover")
            result = "Sieg" if match.result == WIN else "Remis" if match.result == DRAW else "Niederlage"
            text = f"{result}{f' vs {match.opp_name}' if match.opp_name else ''} · {when(match.played_at)}\n" \
                   f"Winrate letzte {min(i + 1, TREND_WINDOW)}: {rate_text(round(rate * 100), 100)}"
            label = chart.create_text(x + int(8 * s) if x < w / 2 else x - int(8 * s), top + int(2 * s), text=text,
                                      anchor="nw" if x < w / 2 else "ne", fill=TEXT, font=self.font_small,
                                      tags="hover")
            x0, y0, x1, y1 = chart.bbox(label)
            pad = int(4 * s)
            chart.tag_lower(chart.create_rectangle(x0 - pad, y0 - pad, x1 + pad, y1 + pad, fill=PANEL, outline=MUTED,
                                                   tags="hover"), label)

        chart.bind("<Motion>", hover)
        chart.bind("<Leave>", lambda e: chart.delete("hover"))
        return chart

    def _match_row(self, match: MatchEntry, bg: str, index: int = 0) -> tk.Frame:
        s = self.s
        row = tk.Frame(self.inner, bg=bg, padx=int(8 * s), pady=int(3 * s), cursor="hand2")
        result, color = (("SIEG", GOOD) if match.result == WIN else ("REMIS", WARN) if match.result == DRAW
                         else ("NIEDERLAGE", BAD))
        tk.Label(row, text=result, fg=color, bg=bg, font=self.font_small_bold, width=11, anchor="w").pack(
            side=tk.LEFT)
        order = {True: "Erster", False: "Zweiter"}.get(match.first, "?")
        tk.Label(row, text=order, fg=TEXT, bg=bg, font=self.font_small, width=7, anchor="w").pack(side=tk.LEFT)
        replay_btn = None
        if self._has_replay(match):  # vor dem Textbereich packen, sonst wird er bei schmaler Breite abgeschnitten
            replay_btn = RoundedButton(row, text="▶ Replay", command=lambda i=index: self.view_duel(i, replay=True),
                                        font=self.font_small, padx=int(8 * s), pady=int(3 * s),
                                        radius=int(6 * s))
            replay_btn.pack(side=tk.RIGHT)
        text = tk.Frame(row, bg=bg)
        text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        title = match.deck_name or match.md_deck or UNKNOWN_DECK
        if match.opp_name:  # bei gewähltem Deck steht es schon oben
            title = f"vs  {match.opp_name}" if self.selected else f"{title}  vs  {match.opp_name}"
        tk.Label(text, text=title, fg=TEXT, bg=bg, font=self.font_small, anchor="w").pack(fill=tk.X)
        details = [when(match.played_at, short=True)]
        if match.coin is not None:
            details.append("Münzwurf gewonnen" if match.coin else "Münzwurf verloren")
        if match.turns:
            details.append(f"{match.turns} Züge" if match.turns != 1 else "1 Zug")
        if self.mode is None:  # sonst zeigt der Filter oben den Modus
            details.append(mode_name(match.mode))
        if match.finish == FINISH_SURRENDER:
            details.append("Aufgabe")
        tk.Label(text, text="  ·  ".join(details), fg=MUTED, bg=bg, font=self.font_small, anchor="w").pack(fill=tk.X)
        for widget in (row, text, *row.winfo_children(), *text.winfo_children()):  # Klick: Decklisten ansehen
            if widget is replay_btn:  # der Knopf hat sein eigenes Klick-Binding
                continue
            widget.config(cursor="hand2")
            widget.bind("<Button-1>", lambda e, i=index: self.view_duel(i))
        return row

    def _has_replay(self, match: MatchEntry) -> bool:
        """Gibt es einen Mitschnitt? (genaue Zuordnung: jeder Mitschnitt gehört zu genau einem Match)"""
        return match.did in getattr(self, "_replay_by_match", {})

    def view_duel(self, index: int, replay: bool = False) -> None:
        """Duell-Übersicht für dieses Match (◀/▶ blättern); replay: mit Replay-Mitschnitt, sonst nur die Decklisten."""
        from duel_view import DuelView
        if self.duel is not None:
            self.duel.close()
        self.duel = DuelView(self.win, self.db, list(self._shown_matches), index, on_close=self._on_duel_closed,
                             replay=replay, settings=self.settings, save_settings=self.save_settings)

    def _on_duel_closed(self) -> None:
        self.duel = None

    def toggle_review(self) -> None:
        """Monatsrückblick öffnen bzw. schließen (liegt über diesem Fenster)."""
        if self.review is not None:
            self.review.close()  # ruft _on_review_closed
            return
        from review_panel import ReviewPanel
        self.review = ReviewPanel(self.win, self.db, self.settings, on_close=self._on_review_closed)
        self.review.win.place(x=0, y=0, relwidth=1, relheight=1)  # deckt die Winrate-Ansicht

    def _on_review_closed(self) -> None:
        self.review = None

    def _open(self, deck: DeckWinStats) -> None:
        self.on_open(deck.deck_name, deck.deck_code)

    def _on_wheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120) * 2, "units")

    # ── Zustand von außen ──
    def set_visible(self, visible: bool) -> None:
        if not self.closed:
            if self.duel is not None:
                self.duel.set_visible(visible)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self.duel is not None:
            self.duel.close()
        if self.review is not None:
            self.review.close()
        try:
            self.canvas.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass
        self.win.destroy()
        self.on_close()
