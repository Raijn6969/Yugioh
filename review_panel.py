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
from extras_panel import BG, GOLD, MUTED, PANEL, ROW_ALT, TEXT
from hover_card import AMBER, GREEN, RED
from match_history import RANKED
from rounded_button import RoundedButton
from window_style import apply_frame, no_activate

MODE_KEY = "WINRATE_MODE"  # wie im Winrate-Fenster: "ranked" oder "all"
POLL_MS = 100


def rate_color(wins: int, matches: int) -> str:
    return TEXT if not matches else GREEN if wins / matches >= 0.5 else AMBER


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
        self._place()
        apply_frame(self.win)
        no_activate(self.win)  # Klicks lassen Master Duel aktiv (sonst Taskleiste über dem Spiel)
        self.win.bind("<Escape>", lambda e: self.close())
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
        header = tk.Frame(self.win, bg=PANEL)
        header.pack(fill=tk.X)
        tk.Label(header, text="◆ RÜCKBLICK", fg=GOLD, bg=PANEL, font=self.font_head).pack(
            side=tk.LEFT, padx=(int(12 * s), int(6 * s)), pady=int(8 * s))
        tk.Label(header, text="Season = ein Kalendermonat", fg=MUTED, bg=PANEL, font=self.font).pack(side=tk.LEFT)
        RoundedButton(header, text="✕", command=self.close, bg="#cc0000", border="#ff8a80",
                      font=("Helvetica", int(9 * s), "bold"), padx=int(9 * s), pady=int(2 * s),
                      radius=int(6 * s)).pack(side=tk.RIGHT, padx=int(8 * s))
        small = dict(font=self.font_small, padx=int(9 * s), pady=int(3 * s), radius=int(6 * s))
        self.export_btn = RoundedButton(header, text="Als HTML", command=self.export_html, bg="#00796b", **small)
        self.export_btn.pack(side=tk.RIGHT, padx=(0, int(6 * s)))

        body = tk.Frame(self.win, bg=BG, padx=int(12 * s), pady=int(8 * s))
        body.pack(fill=tk.BOTH, expand=True)
        nav = tk.Frame(body, bg=BG)
        nav.pack(fill=tk.X)
        self.prev_btn = RoundedButton(nav, text="◀", command=lambda: self.step(-1), **small)
        self.prev_btn.pack(side=tk.LEFT)
        self.month_label = tk.Label(nav, text="", fg=TEXT, bg=BG, font=self.font_head, width=16)
        self.month_label.pack(side=tk.LEFT, padx=int(6 * s))
        self.next_btn = RoundedButton(nav, text="▶", command=lambda: self.step(1), **small)
        self.next_btn.pack(side=tk.LEFT)
        self.mode_label_widget = tk.Label(nav, text="", fg=MUTED, bg=BG, font=self.font_small)
        self.mode_label_widget.pack(side=tk.RIGHT)
        self.status_label = tk.Label(body, text="", fg=MUTED, bg=BG, font=self.font_small, anchor="w",
                                     justify=tk.LEFT, wraplength=int(490 * s))
        self.status_label.pack(fill=tk.X, pady=(int(4 * s), 0))

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

    def _place(self) -> None:
        """Genau über dem Winrate-Fenster (dem Master); ohne sichtbaren Master unten rechts wie das Winrate-Fenster."""
        s = self.s
        self.master.update_idletasks()
        w, h = self.master.winfo_width(), self.master.winfo_height()
        if w < 100 or h < 100:
            w, h = int(520 * s), int(660 * s)
            x = self.master.winfo_screenwidth() - w - int(60 * s)
            y = self.master.winfo_screenheight() - h - int(120 * s)
        else:
            x, y = self.master.winfo_x(), self.master.winfo_y()
        self.win.geometry(f"{w}x{h}+{x}+{y}")

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
        self.prev_btn.config(bg="#444444" if earliest is not None and (self.year, self.month) > earliest else "#2a2a2a")
        self.next_btn.config(bg="#444444" if (self.year, self.month) < self._current_month() else "#2a2a2a")
        for child in self.inner.winfo_children():
            child.destroy()
        try:
            self.review = sr.build(self.db, self.year, self.month, self.mode)
        except Exception as e:  # Datenbank gesperrt/defekt → Fenster bleibt benutzbar
            self.review = None
            self._note(f"Rückblick nicht lesbar: {e}", RED)
            return
        review = self.review
        total = review.total
        if not total.matches:
            self._note(f"In diesem Monat gibt es keine erfassten Duelle ({self.mode_label}).")
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
                      f"{stats.matches} Duelle · {sr.record_text(stats)} · {sr.rate_text(stats.wins, stats.matches)}",
                      ROW_ALT if i % 2 else BG, color=rate_color(stats.wins, stats.matches))
        self._section(f"GEGNER-DECKS (mindestens {sr.MIN_MATCHUP} Duelle)")
        if review.best is None:
            self._note("Noch zu wenige Duelle: ein Gegner-Deck braucht mindestens 3.")
        for i, (label, opponent) in enumerate((("Bestes Matchup", review.best), ("Schlechtestes", review.worst))):
            if opponent is not None:
                stats = opponent.stats
                self._row(f"{label}: {opponent.opp_name}",
                          f"{sr.rate_text(stats.wins, stats.matches)} ({sr.record_text(stats)}) · {stats.matches} Duelle",
                          ROW_ALT if i % 2 else BG, color=GREEN if i == 0 else RED)
        self._section("SERIEN UND AKTIVSTER TAG")
        facts: List[Tuple[str, str]] = [("Längste Siegserie", f"{review.win_streak} in Folge"),
                                        ("Längste Niederlagenserie", f"{review.loss_streak} in Folge"),
                                        ("Duelle im Monat", str(total.matches))]
        if review.busiest:
            day = review.busiest
            facts.append(("Aktivster Tag", f"{sr.day_text(day)} · {day.duels} Duelle ({sr.record_text(day)})"))
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
            tk.Label(tile, text=f"{wins}–{matches - wins} · {matches} Duell(e)" if matches else "keine Duelle",
                     fg=MUTED, bg=PANEL, font=self.font_small).pack()
            tk.Label(tile, text=caption, fg=MUTED, bg=PANEL, font=self.font_small).pack()

    # ── Als HTML ──
    def export_html(self) -> None:
        """Rückblick als HTML in den Downloads-Ordner (im Hintergrund) und im Explorer markieren."""
        if self.review is None or self._exporting:
            return
        self._exporting = True
        self.status_label.config(text="Exportiere als HTML …", fg=MUTED)
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

    def _wait_export(self) -> None:
        if self.closed:
            return
        if self._export_result is None:
            self.win.after(POLL_MS, self._wait_export)
            return
        kind, value = self._export_result
        self._exporting = False
        if kind == "ok":
            self.status_label.config(text=f"Gespeichert: {os.path.basename(value)} (Downloads)", fg=GREEN)
            self.reveal_file(value)
        else:
            self.status_label.config(text=f"Export fehlgeschlagen: {value}", fg=RED)

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
