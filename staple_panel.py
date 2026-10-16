"""
Staples (Button "Staples" im Deck-Fenster): das angezeigte Deck mit den Top-Listen in Master Duel vergleichen.

Für die häufigsten Archetypen des Decks holt staple_analysis die Auswertung von Master Duel Meta (im Hintergrund,
danach offline aus der Datenbank) und vergleicht mit dem passendsten Deck-Typ; bei Hybrid-Decks lässt sich
umschalten. Gezeigt werden als Kartenbilder (card_images, einmal geladen): andere Kopienzahl als üblich, Karten,
die dir fehlen, und Karten, die kaum eine Top-Liste spielt. Unter jedem Bild die Kurzfassung, beim Darüberfahren
Name und Details.
"""

import threading
import tkinter as tk
from typing import Callable, Dict, List, Optional

import card_images
import staple_analysis
from card_grid import CardGrid
from card_stats import name_key
from history_panel import when
from rounded_button import RoundedButton
from window_style import (SIZE_NUM, SIZE_SMALL, SIZE_TEXT, SIZE_TITLE, font, BG, PANEL, TEXT, MUTED, GOLD, INFO, GOOD,
                          WARN, BAD)

POLL_MS = 100
COLUMNS = 5             # Karten pro Reihe
CARD_SIZE = (74, 108)   # Bildgröße bei 1080p (Seitenverhältnis wie die Karten)


def share_text(share: float) -> str:
    return f"{share * 100:.0f} %"


def avg_text(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


class StaplePanel:
    def __init__(self, master: tk.Misc, db, deck: Dict[str, int], archetypes: List[str],
                 on_close: Callable[[], None], session=None,
                 passcodes: Optional[Dict[str, str]] = None, image_folder: str = card_images.FOLDER):
        self.master = master
        self.db = db
        self.deck = deck                  # {englischer Kartenname: Kopien}
        self.archetypes = archetypes      # häufigste Archetypen des Decks
        self.on_close = on_close
        self.session = session            # für Tests (requests-kompatibel)
        # {name_key: Passcode} fürs Kartenbild – eigene Karten vom Deck-Fenster, die übrigen von Master Duel Meta
        self.passcodes = {name_key(name): code for name, code in (passcodes or {}).items()}
        self.image_folder = image_folder
        self.closed = False
        self.comparisons: List[staple_analysis.Comparison] = []
        self.shown = 0                    # Index des angezeigten Vergleichs
        self._result = None               # vom Hintergrund-Thread: Vergleiche oder Fehlertext
        self._thread: Optional[threading.Thread] = None
        self._poll_job = None
        self.s = s = max(1.0, master.winfo_screenheight() / 1080.0)
        self.font = font(SIZE_TEXT, s)
        self.font_bold = font(SIZE_TEXT, s, bold=True)
        self.font_small = font(SIZE_SMALL, s)
        self.font_small_bold = font(SIZE_SMALL, s, bold=True)
        self.font_head = font(SIZE_TITLE, s, bold=True)
        self.font_big = font(SIZE_NUM, s, bold=True, mono=True)
        self.photos = card_images.PhotoCache(master, (int(CARD_SIZE[0] * s), int(CARD_SIZE[1] * s)), image_folder)
        self._tip: Optional[tk.Toplevel] = None

        self.win = tk.Frame(master, bg=BG)  # Ansicht im Deck-Fenster (extras_panel packt sie)
        self._build()
        self.load()

    # ── Aufbau ──
    def _build(self) -> None:
        s = self.s
        body = tk.Frame(self.win, bg=BG, padx=int(12 * s), pady=int(8 * s))
        body.pack(fill=tk.BOTH, expand=True)
        top = tk.Frame(body, bg=BG)
        top.pack(fill=tk.X)
        self.refresh_btn = RoundedButton(top, text="Aktualisieren", command=lambda: self.load(refresh=True),
                                         role="neutral", font=self.font_small, padx=int(8 * s),
                                         pady=int(3 * s), radius=int(6 * s))
        self.refresh_btn.pack(side=tk.RIGHT)
        self.status_label = tk.Label(top, text="", fg=INFO, bg=BG, font=self.font_small, anchor="w",
                                     justify=tk.LEFT, wraplength=int(390 * s))
        self.status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.type_row = tk.Frame(body, bg=BG)  # Umschalten zwischen den Deck-Typen (Hybrid-Decks)
        self.type_row.pack(fill=tk.X, pady=(int(4 * s), 0))

        tiles = tk.Frame(body, bg=BG)
        tiles.pack(fill=tk.X, pady=(int(6 * s), int(4 * s)))
        self.tiles = {}
        for key, caption in (("matching", "WIE TOP"), ("different", "ANDERE ZAHL"), ("missing", "FEHLT DIR"),
                             ("only_mine", "NUR BEI DIR")):
            tile = tk.Frame(tiles, bg=PANEL, padx=int(6 * s), pady=int(4 * s))
            tile.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, int(6 * s)))
            value = tk.Label(tile, text="–", fg=TEXT, bg=PANEL, font=self.font_big)
            value.pack()
            tk.Label(tile, text=caption, fg=MUTED, bg=PANEL, font=self.font_small).pack()
            self.tiles[key] = value

        # Hinweis unten zuerst packen, damit ihn das Raster (füllt den Rest) nicht verdrängt
        tk.Label(body, text="Wenige Listen = nur eine grobe Richtung.", fg=MUTED, bg=BG, font=self.font_small,
                 anchor="w", justify=tk.LEFT,
                 wraplength=int(480 * s)).pack(side=tk.BOTTOM, fill=tk.X, pady=(int(4 * s), 0))
        self.grid = CardGrid(body, BG, PANEL, TEXT, self.photos, s, COLUMNS, self.font_small_bold, GOLD,
                             self.font_small)

    # ── Laden (Hintergrund) ──
    def load(self, refresh: bool = False) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if not self.archetypes:
            self.status_label.config(text="Keine Archetypen im Deck erkannt – nichts zum Vergleichen.", fg=WARN)
            return
        self.status_label.config(text=f"Lade Top-Listen zu {', '.join(self.archetypes)} …", fg=INFO)
        self._thread = threading.Thread(target=self._worker, args=(refresh,), daemon=True)
        self._thread.start()
        self._poll()

    def _worker(self, refresh: bool) -> None:
        kwargs = {"session": self.session} if self.session is not None else {}
        try:
            found = [s for s in (staple_analysis.load(self.db, a, refresh, **kwargs) for a in self.archetypes) if s]
            comparisons = staple_analysis.best_match(self.deck, found)
            self._load_images(comparisons)
            self._result = comparisons
        except Exception as e:  # offline, Seite geändert …
            self._result = f"Top-Listen nicht geladen: {e}"

    def _load_images(self, comparisons) -> None:
        """Passcodes aller angezeigten Karten bestimmen und fehlende Bilder laden (offline: Platzhalter)."""
        cards = [d.card for c in comparisons for d in c.different + c.missing + c.only_mine]
        for card in cards:
            if card.passcode:
                self.passcodes.setdefault(name_key(card.name), card.passcode)
        unknown = [card.name for card in cards if name_key(card.name) not in self.passcodes]
        if unknown:
            for name, ids in self.db.ids_by_name(unknown).items():
                self.passcodes.setdefault(name_key(name), min(ids, key=int))
        session = {"session": self.session} if self.session is not None else {}
        card_images.download((self.passcodes.get(name_key(card.name)) for card in cards), folder=self.image_folder,
                             **session)
        self.photos.prepare(self.passcodes.get(name_key(card.name)) for card in cards)  # Anzeige ohne Ruckler

    def _poll(self) -> None:
        self._poll_job = None
        if self.closed:
            return
        if self._result is None:
            self._poll_job = self.win.after(POLL_MS, self._poll)
            return
        result, self._result = self._result, None
        if isinstance(result, str):
            self.status_label.config(text=result, fg=BAD)
            return
        self.comparisons, self.shown = result, 0
        if not result:
            self.status_label.config(text=f"Master Duel Meta kennt keine Top-Listen zu {', '.join(self.archetypes)}.",
                                     fg=WARN)
            return
        self.render()

    # ── Anzeige ──
    def show(self, index: int) -> None:
        self.shown = index
        self.render()

    def render(self) -> None:
        s = self.s
        comparison = self.comparisons[self.shown]
        stats = comparison.stats
        self.status_label.config(text=f"Master Duel Meta · {stats.deck_type}: {stats.lists} Top-Liste(n) · "
                                      f"Stand {when(stats.fetched_at)}"
                                      f" · {comparison.overlap * 100:.0f} % deiner Karten spielen auch die Top-Listen",
                                 fg=MUTED)
        for child in self.type_row.winfo_children():
            child.destroy()
        if len(self.comparisons) > 1:
            tk.Label(self.type_row, text="Vergleichen mit:", fg=MUTED, bg=BG, font=self.font_small).pack(side=tk.LEFT)
            for i, other in enumerate(self.comparisons):
                RoundedButton(self.type_row, text=other.stats.deck_type, command=lambda i=i: self.show(i),
                              role="active" if i == self.shown else "neutral", font=self.font_small,
                              padx=int(8 * s), pady=int(2 * s), radius=int(6 * s)).pack(side=tk.LEFT,
                                                                                       padx=(int(6 * s), 0))
        counts = {"matching": comparison.matching, "different": len(comparison.different),
                  "missing": len(comparison.missing), "only_mine": len(comparison.only_mine)}
        for key, value in counts.items():
            color = GOOD if key == "matching" else (WARN if value else TEXT)
            self.tiles[key].config(text=str(value), fg=color)
        self._hide_tip()
        self.grid.clear()
        if comparison.different:
            self._grid("ANDERE KOPIENZAHL ALS ÜBLICH", [
                (d.card.name, f"{d.mine}× → {d.card.usual}×", f"{share_text(d.card.usual_share)} · {d.card.usual}×",
                 f"du {d.mine}× · {share_text(d.card.usual_share)} der Listen {d.card.usual}× "
                 f"(Ø {avg_text(d.card.avg)})", WARN) for d in comparison.different])
        if comparison.missing:
            self._grid("FEHLT DIR", [
                (d.card.name, share_text(d.card.share), f"meist {d.card.usual}×",
                 f"{share_text(d.card.share)} der Listen spielen sie · meist {d.card.usual}×", WARN)
                for d in comparison.missing])
        if comparison.only_mine:
            self._grid("NUR BEI DIR", [
                (d.card.name, share_text(d.card.share) if d.card.share else "nur du", f"du {d.mine}×",
                 f"du {d.mine}× · " + (f"in {share_text(d.card.share)} der Top-Listen" if d.card.share
                                        else "in keiner Top-Liste"), MUTED) for d in comparison.only_mine])
        if not (comparison.different or comparison.missing or comparison.only_mine):
            self.grid.message("Dein Deck spielt die Karten wie die Top-Listen.", GOOD, self.font)
        self.grid.finish()

    def _grid(self, title: str, cards) -> None:
        """Abschnitt mit Kartenbildern: [(Name, Kurzfassung, zweite Zeile, Details, Farbe)]."""
        self.grid.section(title)
        for name, short, second, detail, color in cards:
            self.grid.card(name, self.passcodes.get(name_key(name)),
                           [(short, color, self.font_small_bold), (second, MUTED, self.font_small)],
                           on_enter=lambda e, n=name, d=detail: self._show_tip(e, n, d),
                           on_leave=lambda e: self._hide_tip())

    def _show_tip(self, event, name: str, detail: str) -> None:
        """Kleines Fenster mit Name und Details neben der Maus."""
        self._hide_tip()
        s = self.s
        tip = tk.Toplevel(self.win, bg=GOLD)
        tip.overrideredirect(True)
        tip.wm_attributes("-topmost", True)
        box = tk.Frame(tip, bg=PANEL, padx=int(8 * s), pady=int(4 * s))
        box.pack(padx=1, pady=1)
        tk.Label(box, text=name, fg=TEXT, bg=PANEL, font=self.font_bold, anchor="w").pack(fill=tk.X)
        tk.Label(box, text=detail, fg=MUTED, bg=PANEL, font=self.font_small, anchor="w").pack(fill=tk.X)
        tip.geometry(f"+{event.x_root + int(14 * s)}+{event.y_root + int(14 * s)}")
        self._tip = tip

    def _hide_tip(self) -> None:
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

    # ── Zustand von außen ──
    def set_visible(self, visible: bool) -> None:
        if not self.closed:
            if not visible:
                self._hide_tip()  # sonst bleibt er als eigenes Fenster über anderen Programmen stehen

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
        self.win.destroy()
        self.on_close()
