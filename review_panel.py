"""
Rückblick (Knopf "Rückblick" im Winrate-Fenster): die Bilanz eines Monats (season_review) im Stil des Winrate-Fensters.

Der Monat ist mit ◀/▶ wählbar (Standard: der aktuelle, in den ersten Tagen eines Monats der Vormonat); Ranked bzw. alle
Modi wie im Winrate-Fenster. „Als HTML“ speichert den Rückblick als eine Datei im Downloads-Ordner (review_export),
die man verschicken kann. Das Fenster liegt über dem Winrate-Fenster.
"""

import datetime
import os
import threading
import time
import tkinter as tk
from typing import Callable, List, Optional, Tuple

import review_export
import season_review as sr
from bug_report import reveal
from dark_scrollbar import DarkScrollbar
from match_history import RANKED
from winrate_panel import matches_text
from rounded_button import RoundedButton
from window_style import (SIZE_NUM, SIZE_SMALL, SIZE_TEXT, SIZE_TITLE, font, BG, PANEL, TEXT, MUTED, GOLD, GOOD, WARN,
                          BAD, ROW_ALT, NEUTRAL)

MODE_KEY = "WINRATE_MODE"  # wie im Winrate-Fenster: "ranked" oder "all"
POLL_MS = 100


def rate_color(wins: int, matches: int) -> str:
    return TEXT if not matches else GOOD if wins / matches >= 0.5 else WARN


class ReviewPanel:
    def __init__(self, master: tk.Misc, db, settings: dict, on_close: Callable[[], None],
                 now: Optional[float] = None, reveal_file: Callable[[str], None] = reveal):
        self.master = master
        self.db = db
        self.settings = settings
        self.on_close = on_close
        self.reveal_file = reveal_file
        self.closed = False
        self.now = now                     # nur für Tests: "heute"
        self.year, self.month = sr.default_month(now)
        self.review: Optional[sr.Review] = None
        self._export_result = None
        self._exporting = False
        self.s = s = max(1.0, master.winfo_screenheight() / 1080.0)
        self.font = font(SIZE_TEXT, s)
        self.font_bold = font(SIZE_TEXT, s, bold=True)
        self.font_small = font(SIZE_SMALL, s)
        self.font_small_bold = font(SIZE_SMALL, s, bold=True)
        self.font_head = font(SIZE_TITLE, s, bold=True)
        self.font_big = font(SIZE_NUM, s, bold=True, mono=True)

        self.win = tk.Frame(master, bg=BG)  # deckt die Winrate-Ansicht (winrate_panel platziert es)
        self._build()
        self.refresh()

    @property
    def mode(self) -> Optional[int]:
        return None if self.settings.get(MODE_KEY) == "all" else RANKED

    @property
    def mode_label(self) -> str:
        return "Ranked" if self.mode == RANKED else "alle Modi"

    # ── Aufbau ──
    def _build(self) -> None:
        s = self.s
        small = dict(font=self.font_small, padx=int(9 * s), pady=int(3 * s), radius=int(6 * s))
        body = tk.Frame(self.win, bg=BG, padx=int(12 * s), pady=int(8 * s))
        body.pack(fill=tk.BOTH, expand=True)
        nav = tk.Frame(body, bg=BG)
        nav.pack(fill=tk.X, pady=(0, int(6 * s)))
        self.prev_btn = RoundedButton(nav, text="◀", command=lambda: self.step(-1), **small)
        self.prev_btn.pack(side=tk.LEFT)
        self.month_label = tk.Label(nav, text="", fg=TEXT, bg=BG, font=self.font_head, width=16)
        self.month_label.pack(side=tk.LEFT, padx=int(6 * s))
        self.next_btn = RoundedButton(nav, text="▶", command=lambda: self.step(1), **small)
        self.next_btn.pack(side=tk.LEFT)
        RoundedButton(nav, text="Zurück", command=self.close, role="neutral", **small).pack(side=tk.RIGHT)
        self.export_btn = RoundedButton(nav, text="Als HTML", command=self.export_html, **small)
        self.export_btn.pack(side=tk.RIGHT, padx=(0, int(6 * s)))
        self.mode_label_widget = tk.Label(nav, text="", fg=MUTED, bg=BG, font=self.font_small)
        self.mode_label_widget.pack(side=tk.RIGHT, padx=(0, int(6 * s)))
        self.status_label = tk.Label(body, text="", fg=MUTED, bg=BG, font=self.font_small, anchor="w",
                                     justify=tk.LEFT, wraplength=int(490 * s))  # nur beim Export gepackt

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

    # ── Inhalt ──
    def _today(self) -> float:
        return time.time() if self.now is None else self.now

    def _current_month(self) -> Tuple[int, int]:
        today = datetime.datetime.fromtimestamp(self._today())
        return today.year, today.month

    def _earliest_month(self) -> Optional[Tuple[int, int]]:
        try:
            first = self.db.first_played()
        except Exception:
            return None
        if not first:
            return None
        moment = datetime.datetime.fromtimestamp(first)
        return moment.year, moment.month

    def step(self, delta: int) -> None:
        """Einen Monat vor/zurück – nicht in die Zukunft und nicht vor den ältesten gespeicherten Monat."""
        target = sr.next_month(self.year, self.month) if delta > 0 else sr.previous_month(self.year, self.month)
        earliest = self._earliest_month()
        if target > self._current_month() or (delta < 0 and earliest is not None and target < earliest):
            return
        self.year, self.month = target
        self.refresh()

    def refresh(self) -> None:
        if self.closed:
            return
        earliest = self._earliest_month()
        self.month_label.config(text=sr.month_name(self.year, self.month))
        self.mode_label_widget.config(text=self.mode_label)
        self.prev_btn.config(bg=NEUTRAL if earliest is not None and (self.year, self.month) > earliest else PANEL)
        self.next_btn.config(bg=NEUTRAL if (self.year, self.month) < self._current_month() else PANEL)
        for child in self.inner.winfo_children():
            child.destroy()
        try:
            self.review = sr.build(self.db, self.year, self.month, self.mode)
        except Exception as e:  # Datenbank gesperrt/defekt → Fenster bleibt benutzbar
            self.review = None
            self._note(f"Rückblick nicht lesbar: {e}", BAD)
            return
        review = self.review
        total = review.total
        if not total.matches:
            self._note(f"In diesem Monat gibt es keine erfassten Matches ({self.mode_label}).")
            self.canvas.yview_moveto(0)
            return
        self._tiles([("GESAMT", total.wins, total.matches), ("ALS ERSTER", total.first_wins, total.first),
                     ("ALS ZWEITER", total.second_wins, total.second)])
        self._section("MÜNZWURF")
        if total.coin_known:
            self._tiles([("GEWONNEN", total.coin_won, total.coin_known),
                         ("SIEGE WENN GEWONNEN", total.coin_won_wins, total.coin_won),
                         ("SIEGE WENN VERLOREN", total.coin_lost_wins, total.coin_lost)], colored=(False, True, True))
        else:
            self._note("Der Münzwurf wurde in diesem Monat nicht erfasst (wird seit V9.2 mitgeschrieben).")
        more = f" · die {len(review.decks)} meistgespielten" if review.deck_count > len(review.decks) else ""
        self._section(f"DECKS ({review.deck_count}{more})")
        for i, deck in enumerate(review.decks):
            stats = deck.stats
            self._row(deck.deck_name or "Unbekanntes Deck",
                      f"{sr.rate_text(stats.wins, stats.matches)} · {sr.record_text(stats)} · "
                      f"{matches_text(stats.matches)}",
                      ROW_ALT if i % 2 else BG, color=rate_color(stats.wins, stats.matches))
        self._section(f"GEGNER-DECKS (mindestens {sr.MIN_MATCHUP} Matches)")
        if review.best is None:
            self._note("Noch zu wenige Matches: ein Gegner-Deck braucht mindestens 3.")
        for i, (label, opponent) in enumerate((("Bestes Matchup", review.best), ("Schlechtestes", review.worst))):
            if opponent is not None:
                stats = opponent.stats
                self._row(f"{label}: {opponent.opp_name}",
                          f"{sr.rate_text(stats.wins, stats.matches)} · {sr.record_text(stats)} "
                          f"· {matches_text(stats.matches)}",
                          ROW_ALT if i % 2 else BG, color=GOOD if i == 0 else BAD)
        self._section("SERIEN UND AKTIVSTER TAG")
        facts: List[Tuple[str, str]] = [("Längste Siegserie", f"{review.win_streak} in Folge"),
                                        ("Längste Niederlagenserie", f"{review.loss_streak} in Folge"),
                                        ("Matches im Monat", str(total.matches))]
        if review.busiest:
            day = review.busiest
            facts.append(("Aktivster Tag", f"{sr.day_text(day)} · {sr.record_text(day)} · {matches_text(day.duels)}"))
        for i, (name, value) in enumerate(facts):
            self._row(name, value, ROW_ALT if i % 2 else BG)
        self.canvas.yview_moveto(0)

    def _section(self, title: str) -> None:
        tk.Label(self.inner, text=title, fg=GOLD, bg=BG, font=self.font_small_bold, anchor="w").pack(
            fill=tk.X, pady=(int(8 * self.s), int(2 * self.s)))

    def _note(self, text: str, color: str = MUTED) -> None:
        tk.Label(self.inner, text=text, fg=color, bg=BG, font=self.font_small, anchor="w", justify=tk.LEFT,
                 wraplength=int(480 * self.s)).pack(fill=tk.X, pady=(int(4 * self.s), 0))

    def _row(self, left: str, right: str, bg: str, color: str = TEXT) -> None:
        s = self.s
        row = tk.Frame(self.inner, bg=bg, padx=int(8 * s), pady=int(3 * s))
        row.pack(fill=tk.X)
        tk.Label(row, text=right, fg=color if color != TEXT else MUTED, bg=bg, font=self.font_small).pack(side=tk.RIGHT)
        tk.Label(row, text=left, fg=TEXT, bg=bg, font=self.font_small_bold, anchor="w").pack(
            side=tk.LEFT, fill=tk.X, expand=True)

    def _tiles(self, tiles, colored=(True, True, True)) -> None:
        """Eine Reihe gleich breiter Kacheln: (Beschriftung, Siege, Matches) → Winrate groß, Bilanz klein."""
        s = self.s
        row = tk.Frame(self.inner, bg=BG)
        row.pack(fill=tk.X, pady=(int(2 * s), int(4 * s)))
        for column, (caption, wins, matches) in enumerate(tiles):
            row.columnconfigure(column, weight=1, uniform="tile")
            tile = tk.Frame(row, bg=PANEL, padx=int(8 * s), pady=int(4 * s))
            tile.grid(row=0, column=column, sticky="ew", padx=(0, int(6 * s)))
            color = rate_color(wins, matches) if colored[column] else TEXT
            tk.Label(tile, text=sr.rate_text(wins, matches), fg=color, bg=PANEL, font=self.font_big).pack()
            tk.Label(tile, text=f"{wins}–{matches - wins} · {matches_text(matches)}" if matches else "keine Matches",
                     fg=MUTED, bg=PANEL, font=self.font_small).pack()
            tk.Label(tile, text=caption, fg=MUTED, bg=PANEL, font=self.font_small).pack()

    # ── Als HTML ──
    def export_html(self) -> None:
        """Rückblick als HTML in den Downloads-Ordner (im Hintergrund) und im Explorer markieren."""
        if self.review is None or self._exporting:
            return
        self._exporting = True
        self._status("Exportiere als HTML …", MUTED)
        review, mode_label = self.review, self.mode_label
        self._export_result = None

        def work():
            try:
                result = ("ok", review_export.export(review, mode_label))
            except Exception as e:  # Ordner nicht beschreibbar …
                result = ("error", str(e))
            self._export_result = result

        threading.Thread(target=work, daemon=True, name="review-export").start()
        self._wait_export()

    def _status(self, text: str, color: str) -> None:
        self.status_label.config(text=text, fg=color)
        self.status_label.pack(fill=tk.X, pady=(int(4 * self.s), 0), before=self.list_frame)

    def _wait_export(self) -> None:
        if self.closed:
            return
        if self._export_result is None:
            self.win.after(POLL_MS, self._wait_export)
            return
        kind, value = self._export_result
        self._exporting = False
        if kind == "ok":
            self._status(f"Gespeichert: {os.path.basename(value)} (Downloads)", GOOD)
            self.reveal_file(value)
        else:
            self._status(f"Export fehlgeschlagen: {value}", BAD)

    def _on_wheel(self, event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120) * 2, "units")

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
