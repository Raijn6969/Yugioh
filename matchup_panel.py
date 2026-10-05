"""
Matchup (Button "Matchup" im Deck-Fenster): die Störkarten deiner Gegner und wie du gegen sie abschneidest.

Aus den erfassten Duellen (matchup_analysis): Kartenbilder der Störkarten, darunter wie viele Gegner-Decks sie
spielen und deine Winrate gegen diese Decks (grün = mindestens so gut wie sonst, rot = deutlich schlechter).
Beim Darüberfahren die Details (als Erster/Zweiter, ohne die Karte). Umschaltbar: nur das angezeigte Deck oder
alle Decks, Ranked oder alle Modi (wie im Winrate-Fenster).

Damit das Fenster nicht ruckelt: Auswertung und Bilder (laden, verkleinern) laufen im Hintergrund, die Karten werden
auf eine Zeichenfläche gemalt (card_grid). Fehlende Bilder werden einmal online geladen und dann nachgezeigt.
"""

import queue
import threading
import tkinter as tk
from typing import Callable, Optional

import card_images
import matchup_analysis
from card_grid import CardGrid
from extras_panel import BG, GOLD, MUTED, PANEL, TEXT
from hover_card import AMBER, GREEN, RED
from match_history import RANKED
from rounded_button import RoundedButton
from window_style import apply_frame, no_activate
from winrate_panel import MODE_KEY

COLUMNS = 5
CARD_SIZE = (74, 108)
MAX_THREATS = 30
WORSE = 0.1     # so viel schlechter als sonst → rot
POLL_MS = 50
IMAGES_LOADED = object()  # Meldung des Hintergrund-Threads: fehlende Bilder sind jetzt da


def rate(wins: int, matches: int) -> Optional[float]:
    return wins / matches if matches else None


def rate_text(wins: int, matches: int) -> str:
    return f"{wins / matches * 100:.0f} %" if matches else "–"


def record_text(record: matchup_analysis.Record) -> str:
    text = f"{rate_text(record.wins, record.matches)} ({record.wins}–{record.losses}"
    return text + (f"–{record.draws})" if record.draws else ")")


class MatchupPanel:
    def __init__(self, master: tk.Misc, db, settings: dict, save_settings: Callable[[], None],
                 on_close: Callable[[], None], deck_name: Optional[str] = None, anchor: Optional[tk.Misc] = None,
                 image_folder: str = card_images.FOLDER):
        self.master = master
        self.db = db
        self.settings = settings
        self.save_settings = save_settings
        self.on_close = on_close
        self.deck_name = deck_name        # Deck im Deck-Fenster (None = keins)
        self.only_deck = deck_name is not None
        self.closed = False
        self.matchup: Optional[matchup_analysis.Matchup] = None
        self.s = s = max(1.0, master.winfo_screenheight() / 1080.0)
        self.font = ("Helvetica", int(10 * s))
        self.font_bold = ("Helvetica", int(10 * s), "bold")
        self.font_small = ("Helvetica", int(8 * s))
        self.font_small_bold = ("Helvetica", int(8 * s), "bold")
        self.font_head = ("Helvetica", int(12 * s), "bold")
        self.font_big = ("Consolas", int(18 * s), "bold")
        self.photos = card_images.PhotoCache(master, (int(CARD_SIZE[0] * s), int(CARD_SIZE[1] * s)), image_folder)
        self.image_folder = image_folder
        self._tip: Optional[tk.Toplevel] = None
        self._results: "queue.Queue" = queue.Queue()  # vom Hintergrund-Thread: (Durchgang, Ergebnis)
        self._thread: Optional[threading.Thread] = None
        self._generation = 0                           # Filter umgestellt → ältere Ergebnisse verwerfen
        self._poll_job = None

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

    @property
    def mode(self) -> Optional[int]:
        return None if self.settings.get(MODE_KEY) == "all" else RANKED

    # ── Aufbau ──
    def _build(self) -> None:
        s = self.s
        header = tk.Frame(self.win, bg=PANEL)
        header.pack(fill=tk.X)
        tk.Label(header, text="◆ MATCHUP", fg=GOLD, bg=PANEL, font=self.font_head).pack(
            side=tk.LEFT, padx=(int(12 * s), int(6 * s)), pady=int(8 * s))
        tk.Label(header, text="Störkarten deiner Gegner", fg=MUTED, bg=PANEL, font=self.font).pack(side=tk.LEFT)
        RoundedButton(header, text="✕", command=self.close, bg="#cc0000", border="#ff8a80",
                      font=("Helvetica", int(9 * s), "bold"), padx=int(9 * s), pady=int(2 * s),
                      radius=int(6 * s)).pack(side=tk.RIGHT, padx=int(8 * s))

        body = tk.Frame(self.win, bg=BG, padx=int(12 * s), pady=int(8 * s))
        body.pack(fill=tk.BOTH, expand=True)
        filters = tk.Frame(body, bg=BG)
        filters.pack(fill=tk.X)
        small = dict(font=self.font_small, padx=int(8 * s), pady=int(2 * s), radius=int(6 * s))
        self.filter_buttons = {}
        options = [("ranked", "Ranked"), ("all", "Alle Modi")]
        if self.deck_name is not None:
            options = [("deck", "Dieses Deck"), ("decks", "Alle Decks")] + options
        for key, label in options:
            button = RoundedButton(filters, text=label, command=lambda k=key: self._set_filter(k), **small)
            button.pack(side=tk.LEFT, padx=(0, int(4 * s)))
            self.filter_buttons[key] = button
        self.info_label = tk.Label(body, text="Werte die Duelle aus …", fg=MUTED, bg=BG, font=self.font_small,
                                   anchor="w", justify=tk.LEFT, wraplength=int(490 * s))
        self.info_label.pack(fill=tk.X, pady=(int(4 * s), 0))

        tiles = tk.Frame(body, bg=BG)
        tiles.pack(fill=tk.X, pady=(int(6 * s), int(4 * s)))
        self.tiles = {}
        for key, caption in (("opponents", "GEGNER-DECKS"), ("per_deck", "STÖRKARTEN Ø"),
                             ("winrate", "DEINE WINRATE")):
            tile = tk.Frame(tiles, bg=PANEL, padx=int(8 * s), pady=int(4 * s))
            tile.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, int(6 * s)))
            value = tk.Label(tile, text="–", fg=TEXT, bg=PANEL, font=self.font_big)
            value.pack()
            tk.Label(tile, text=caption, fg=MUTED, bg=PANEL, font=self.font_small).pack()
            self.tiles[key] = value

        # Hinweis unten zuerst packen, damit ihn das Raster (füllt den Rest) nicht verdrängt
        tk.Label(body, text="Unter der Karte: so viele Gegner-Decks spielen sie · deine Winrate gegen diese Decks "
                            "(rot = deutlich schlechter als sonst). Wenige Duelle = nur eine grobe Richtung.",
                 fg=MUTED, bg=BG, font=self.font_small, anchor="w", justify=tk.LEFT,
                 wraplength=int(480 * s)).pack(side=tk.BOTTOM, fill=tk.X, pady=(int(4 * s), 0))
        self.grid = CardGrid(body, BG, PANEL, TEXT, self.photos, s, COLUMNS, self.font_small_bold, GOLD,
                             self.font_small)

    def _place(self, anchor: Optional[tk.Misc]) -> None:
        """Unter dem Button "Matchup", rechtsbündig mit dem Deck-Fenster (liegt über dessen Liste)."""
        s = self.s
        w, h = int(520 * s), int(620 * s)
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

    # ── Auswerten (Hintergrund) ──
    def _set_filter(self, key: str) -> None:
        if key in ("ranked", "all"):
            self.settings[MODE_KEY] = key
            self.save_settings()
        else:
            self.only_deck = key == "deck"
        self.refresh()

    def refresh(self) -> None:
        """Neu auswerten – im Hintergrund, damit das Fenster nicht hängt (Ergebnis kommt über _poll)."""
        if self.closed:
            return
        active = {"ranked": self.mode == RANKED, "all": self.mode is None, "deck": self.only_deck,
                  "decks": not self.only_deck}
        for key, button in self.filter_buttons.items():
            button.config(bg="#007acc" if active[key] else "#444444")
        self._generation += 1
        self._thread = threading.Thread(target=self._worker, daemon=True, args=(
            self._generation, self.deck_name if self.only_deck else None, self.mode))
        self._thread.start()
        if self._poll_job is None:
            self._poll()

    def _worker(self, generation: int, deck_name: Optional[str], mode: Optional[int]) -> None:
        try:
            matchup = matchup_analysis.analyse(self.db, deck_name, mode)
        except Exception as e:  # Datenbank gesperrt/defekt → Fenster bleibt benutzbar
            self._results.put((generation, f"Duelle nicht lesbar: {e}"))
            return
        passcodes = [t.passcode for t in matchup.threats[:MAX_THREATS]]
        self.photos.prepare(passcodes)
        self._results.put((generation, matchup))
        missing = self.photos.missing(passcodes)
        if missing:  # einmal online laden, dann nachträglich zeigen
            card_images.download(missing, folder=self.image_folder)
            self.photos.prepare(missing)
            self._results.put((generation, IMAGES_LOADED))

    def _poll(self) -> None:
        self._poll_job = None
        if self.closed:
            return
        while not self._results.empty():
            generation, result = self._results.get()
            if generation != self._generation:
                continue  # Filter inzwischen umgestellt
            if result is IMAGES_LOADED:
                self.photos.forget_missing()
                position = self.grid.canvas.yview()[0]
                self._render_threats()
                self.grid.canvas.yview_moveto(position)  # Scroll-Stand bleibt
            elif isinstance(result, str):
                self.info_label.config(text=result, fg=RED)
            else:
                self.matchup = result
                self._show()
        if (self._thread is not None and self._thread.is_alive()) or not self._results.empty():
            self._poll_job = self.win.after(POLL_MS, self._poll)

    # ── Anzeige ──
    def _show(self) -> None:
        matchup = self.matchup
        whose = f"mit „{self.deck_name}“" if self.only_deck else "aller Decks"
        if not matchup.opponents:
            text, color = (f"Noch keine Gegner-Decks ({whose}). Die Decks deiner Gegner kommen aus der Match History "
                           f"von Master Duel – öffne sie ab und zu, sie hält die letzten 20 Duelle.", AMBER)
        else:
            text, color = (f"{matchup.opponents} Duelle {whose} mit bekanntem Gegner-Deck", MUTED)
            if matchup.unknown:
                text += (f" · {matchup.unknown} weitere ohne Gegner-Deck (Match History in Master Duel öffnen, "
                         f"dann kommen sie dazu)")
        self.info_label.config(text=text, fg=color)
        overall = matchup.overall
        self.tiles["opponents"].config(text=str(matchup.opponents))
        self.tiles["per_deck"].config(text=f"{matchup.per_deck:.1f}".replace(".", ","))
        self.tiles["winrate"].config(text=rate_text(overall.wins, overall.matches))
        self._render_threats()

    def _render_threats(self) -> None:
        self._hide_tip()
        self.grid.clear()
        threats = self.matchup.threats[:MAX_THREATS]
        if threats:
            self.grid.section("STÖRKARTEN DEINER GEGNER (HÄUFIGSTE ZUERST)")
        overall = rate(self.matchup.overall.wins, self.matchup.overall.matches)
        for threat in threats:
            against = rate(threat.against.wins, threat.against.matches)
            color = (TEXT if against is None or overall is None else GREEN if against >= overall
                     else RED if against <= overall - WORSE else AMBER)
            self.grid.card(threat.name, threat.passcode, [
                (f"{threat.share * 100:.0f} %", AMBER if threat.share >= 0.5 else TEXT, self.font_small_bold),
                (f"Win {rate_text(threat.against.wins, threat.against.matches)}", color, self.font_small)],
                on_enter=lambda e, t=threat: self._show_tip(e, t), on_leave=lambda e: self._hide_tip())
        self.grid.finish()

    def _show_tip(self, event, threat: matchup_analysis.Threat) -> None:
        self._hide_tip()
        s = self.s
        against, without = threat.against, threat.without
        lines = [f"{threat.kind} · in {threat.decks} von {self.matchup.opponents} Gegner-Decks",
                 f"Gegen Decks mit der Karte: {record_text(against)}"]
        order = []
        if against.first:
            order.append(f"als Erster {rate_text(against.first_wins, against.first)}")
        if against.second:
            order.append(f"als Zweiter {rate_text(against.second_wins, against.second)}")
        if order:
            lines.append("   " + " · ".join(order))
        lines.append(f"Ohne die Karte: {record_text(without)}" if without.matches else "Ohne die Karte: keine Duelle")
        tip = tk.Toplevel(self.win, bg=GOLD)
        tip.overrideredirect(True)
        tip.wm_attributes("-topmost", True)
        box = tk.Frame(tip, bg=PANEL, padx=int(8 * s), pady=int(4 * s))
        box.pack(padx=1, pady=1)
        tk.Label(box, text=threat.name, fg=TEXT, bg=PANEL, font=self.font_bold, anchor="w").pack(fill=tk.X)
        tk.Label(box, text="\n".join(lines), fg=MUTED, bg=PANEL, font=self.font_small, anchor="w",
                 justify=tk.LEFT).pack(fill=tk.X)
        tip.geometry(f"+{event.x_root + int(14 * s)}+{event.y_root + int(14 * s)}")
        self._tip = tip

    def _hide_tip(self) -> None:
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    # ── Zustand von außen ──
    def set_visible(self, visible: bool) -> None:
        if not self.closed:
            self.win.wm_attributes("-alpha", 1.0 if visible else 0.0)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._hide_tip()
        if self._poll_job is not None:
            try:
                self.win.after_cancel(self._poll_job)
            except tk.TclError:
                pass
        self.grid.release()
        self.win.destroy()
        self.on_close()
