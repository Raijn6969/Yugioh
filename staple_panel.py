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
from card_stats import name_key
from extras_panel import BG, GOLD, MUTED, PANEL, TEXT
from history_panel import when
from hover_card import AMBER, GREEN, NEON, RED
from rounded_button import RoundedButton
from window_style import apply_frame

POLL_MS = 100
COLUMNS = 5             # Karten pro Reihe
CARD_SIZE = (74, 108)   # Bildgröße bei 1080p (Seitenverhältnis wie die Karten)


def share_text(share: float) -> str:
    return f"{share * 100:.0f} %"


def avg_text(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


class StaplePanel:
    def __init__(self, master: tk.Misc, db, deck: Dict[str, int], archetypes: List[str],
                 on_close: Callable[[], None], anchor: Optional[tk.Misc] = None, session=None,
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
        self.font = ("Helvetica", int(10 * s))
        self.font_bold = ("Helvetica", int(10 * s), "bold")
        self.font_small = ("Helvetica", int(8 * s))
        self.font_small_bold = ("Helvetica", int(8 * s), "bold")
        self.font_head = ("Helvetica", int(12 * s), "bold")
        self.font_big = ("Consolas", int(18 * s), "bold")
        self.photos = card_images.PhotoCache(master, (int(CARD_SIZE[0] * s), int(CARD_SIZE[1] * s)), image_folder)
        self._tip: Optional[tk.Toplevel] = None

        self.win = tk.Toplevel(master, bg=BG)
        self.win.overrideredirect(True)
        self.win.wm_attributes("-topmost", True)
        self._build()
        self._place(anchor)
        apply_frame(self.win)
        self.win.bind("<Escape>", lambda e: self.close())
        self.load()

    # ── Aufbau ──
    def _build(self) -> None:
        s = self.s
        header = tk.Frame(self.win, bg=PANEL)
        header.pack(fill=tk.X)
        tk.Label(header, text="◆ STAPLES", fg=GOLD, bg=PANEL, font=self.font_head).pack(
            side=tk.LEFT, padx=(int(12 * s), int(6 * s)), pady=int(8 * s))
        tk.Label(header, text="Vergleich mit Top-Listen", fg=MUTED, bg=PANEL, font=self.font).pack(side=tk.LEFT)
        RoundedButton(header, text="✕", command=self.close, bg="#cc0000", border="#ff8a80",
                      font=("Helvetica", int(9 * s), "bold"), padx=int(9 * s), pady=int(2 * s),
                      radius=int(6 * s)).pack(side=tk.RIGHT, padx=int(8 * s))
        self.refresh_btn = RoundedButton(header, text="Aktualisieren", command=lambda: self.load(refresh=True),
                                         bg="#444444", font=self.font_small, padx=int(8 * s), pady=int(3 * s),
                                         radius=int(6 * s))
        self.refresh_btn.pack(side=tk.RIGHT)

        body = tk.Frame(self.win, bg=BG, padx=int(12 * s), pady=int(8 * s))
        body.pack(fill=tk.BOTH, expand=True)
        self.status_label = tk.Label(body, text="", fg=NEON, bg=BG, font=self.font_small, anchor="w",
                                     justify=tk.LEFT, wraplength=int(500 * s))
        self.status_label.pack(fill=tk.X)
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
        tk.Label(body, text="Quelle: Master Duel Meta (aktuelle Top-Listen in Master Duel). Wenige Listen = nur "
                            "eine grobe Richtung.", fg=MUTED, bg=BG, font=self.font_small, anchor="w",
                 justify=tk.LEFT, wraplength=int(480 * s)).pack(fill=tk.X, pady=(int(4 * s), 0))

    def _place(self, anchor: Optional[tk.Misc]) -> None:
        """Unter dem Button "Staples", rechtsbündig mit dem Deck-Fenster (liegt über dessen Liste)."""
        s = self.s
        w, h = int(520 * s), int(620 * s)
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

    # ── Laden (Hintergrund) ──
    def load(self, refresh: bool = False) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if not self.archetypes:
            self.status_label.config(text="Keine Archetypen im Deck erkannt – nichts zum Vergleichen.", fg=AMBER)
            return
        self.status_label.config(text=f"Lade Top-Listen zu {', '.join(self.archetypes)} …", fg=NEON)
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

    def _poll(self) -> None:
        self._poll_job = None
        if self.closed:
            return
        if self._result is None:
            self._poll_job = self.win.after(POLL_MS, self._poll)
            return
        result, self._result = self._result, None
        if isinstance(result, str):
            self.status_label.config(text=result, fg=RED)
            return
        self.comparisons, self.shown = result, 0
        if not result:
            self.status_label.config(text=f"Master Duel Meta kennt keine Top-Listen zu {', '.join(self.archetypes)}.",
                                     fg=AMBER)
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
        self.status_label.config(text=f"{stats.deck_type}: {stats.lists} Top-Liste(n) · Stand {when(stats.fetched_at)}"
                                      f" · {comparison.overlap * 100:.0f} % deiner Karten spielen auch die Top-Listen",
                                 fg=MUTED)
        for child in self.type_row.winfo_children():
            child.destroy()
        if len(self.comparisons) > 1:
            tk.Label(self.type_row, text="Vergleichen mit:", fg=MUTED, bg=BG, font=self.font_small).pack(side=tk.LEFT)
            for i, other in enumerate(self.comparisons):
                RoundedButton(self.type_row, text=other.stats.deck_type, command=lambda i=i: self.show(i),
                              bg="#007acc" if i == self.shown else "#444444", font=self.font_small,
                              padx=int(8 * s), pady=int(2 * s), radius=int(6 * s)).pack(side=tk.LEFT,
                                                                                       padx=(int(6 * s), 0))
        counts = {"matching": comparison.matching, "different": len(comparison.different),
                  "missing": len(comparison.missing), "only_mine": len(comparison.only_mine)}
        for key, value in counts.items():
            color = GREEN if key == "matching" else (AMBER if value else TEXT)
            self.tiles[key].config(text=str(value), fg=color)
        for child in self.inner.winfo_children():
            child.destroy()
        if comparison.different:
            self._grid("ANDERE KOPIENZAHL ALS ÜBLICH", [
                (d.card.name, f"{d.mine}× → {d.card.usual}×", f"{share_text(d.card.usual_share)} spielen "
                 f"{d.card.usual}×", f"du {d.mine}× · {share_text(d.card.usual_share)} der Listen {d.card.usual}× "
                 f"(Ø {avg_text(d.card.avg)})", AMBER) for d in comparison.different])
        if comparison.missing:
            self._grid("FEHLT DIR", [
                (d.card.name, share_text(d.card.share), f"meist {d.card.usual}×",
                 f"{share_text(d.card.share)} der Listen spielen sie · meist {d.card.usual}×", AMBER)
                for d in comparison.missing])
        if comparison.only_mine:
            self._grid("NUR BEI DIR", [
                (d.card.name, share_text(d.card.share) if d.card.share else "nur du", f"du {d.mine}×",
                 f"du {d.mine}× · " + (f"in {share_text(d.card.share)} der Top-Listen" if d.card.share
                                        else "in keiner Top-Liste"), MUTED) for d in comparison.only_mine])
        if not (comparison.different or comparison.missing or comparison.only_mine):
            tk.Label(self.inner, text="Dein Deck spielt die Karten wie die Top-Listen.", fg=GREEN, bg=BG,
                     font=self.font).pack(pady=int(20 * s))
        self.canvas.yview_moveto(0)

    def _grid(self, title: str, cards) -> None:
        """Abschnitt mit Kartenbildern: [(Name, Kurzfassung, zweite Zeile, Details, Farbe)]."""
        s = self.s
        tk.Label(self.inner, text=title, fg=GOLD, bg=BG, font=self.font_small_bold, anchor="w").pack(
            fill=tk.X, pady=(int(8 * s), int(2 * s)))
        grid = tk.Frame(self.inner, bg=BG)
        grid.pack(fill=tk.X)
        for i, (name, short, second, detail, color) in enumerate(cards):
            tile = tk.Frame(grid, bg=BG, padx=int(3 * s), pady=int(3 * s))
            tile.grid(row=i // COLUMNS, column=i % COLUMNS, sticky="n")
            photo = self.photos.get(self.passcodes.get(name_key(name)))
            width, height = self.photos.size
            if photo is not None:
                picture = tk.Label(tile, image=photo, bg=BG, bd=0)
                hover = [picture]
            else:  # kein Bild (offline/unbekannt): Name auf einer kartengroßen Fläche
                picture = tk.Frame(tile, width=width, height=height, bg=PANEL)
                picture.pack_propagate(False)
                text = tk.Label(picture, text=name, fg=TEXT, bg=PANEL, font=self.font_small,
                                wraplength=width - int(8 * s), justify=tk.CENTER)
                text.pack(expand=True)
                hover = [picture, text]
            picture.pack()
            caption = tk.Label(tile, text=short, fg=color, bg=BG, font=self.font_small_bold)
            caption.pack()
            sub = tk.Label(tile, text=second, fg=MUTED, bg=BG, font=self.font_small)
            sub.pack()
            for widget in (tile, *hover, caption, sub):
                widget.bind("<Enter>", lambda e, n=name, d=detail: self._show_tip(e, n, d))
                widget.bind("<Leave>", lambda e: self._hide_tip())

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
        self._hide_tip()
        if self._poll_job is not None:
            try:
                self.win.after_cancel(self._poll_job)
            except tk.TclError:
                pass
        try:
            self.canvas.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass
        self.win.destroy()
        self.on_close()
