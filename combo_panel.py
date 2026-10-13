"""
Combos (Button "Combos" im Deck-Fenster): die Community-Combos von Master Duel Meta zum angezeigten Deck.

Die Liste zeigt je Combo die Starthand, die Chance darauf (als Erster, 5 Karten) und fehlende Karten; standardmäßig nur
die Combos, die das Deck spielen kann. Klick auf eine Combo zeigt die ganze Linie: Starthand als Kartenbilder, die
Schritte mit Verzweigungen, das Endboard und die Handtraps, gegen die sie durchspielt. Geladen wird im Hintergrund
(mdm_combos: einmal online, danach aus der Datenbank), bei Hybrid-Decks lässt sich zwischen den Deck-Typen umschalten.
"""

import threading
import tkinter as tk
import webbrowser
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import quote

import card_images
import mdm_combos
from card_grid import CardGrid
from card_stats import name_key
from extras_panel import BG, GOLD, MUTED, PANEL, ROW_ALT, TEXT
from history_panel import when
from hover_card import AMBER, GREEN, NEON, RED
from rounded_button import RoundedButton
from window_style import apply_frame, no_activate

POLL_MS = 100
COLUMNS = 6             # Startkarten pro Reihe in der Detailansicht
CARD_SIZE = (66, 96)    # Bildgröße bei 1080p
INDENT = 14             # Einrückung je Ebene bei 1080p
GOOD_ODDS = 0.5         # ab dieser Chance grün
MDM_DECK_URL = "https://www.masterduelmeta.com/tier-list/deck-types/{name}"


def hand_text(combo: mdm_combos.Combo) -> str:
    """'Elfnote Tinia + beliebig (Non-FIRE)' – Platzhalter mit dem gekürzten Text von Master Duel Meta."""
    parts = []
    for card in combo.start:
        generic = card.generic.removeprefix("Any ").removesuffix(" Monster").strip() or "Karte"
        parts.append(card.name or f"beliebig ({generic})")
    return " + ".join(parts) or "–"


def odds_text(odds: Optional[float]) -> Tuple[str, str]:
    """(Text, Farbe) der Chance auf die Starthand."""
    if odds is None:
        return "–", MUTED
    return f"{odds * 100:.0f} % als Erster", GREEN if odds >= GOOD_ODDS else NEON


def entry_text(entry: mdm_combos.Entry) -> str:
    return f"{entry.card} – {entry.label}" if entry.card and entry.label else entry.card or entry.label


def step_text(step: mdm_combos.Step) -> str:
    """Einträge eines Schritts, verbunden mit → bzw. + (der Verbinder steht am Eintrag davor)."""
    text = ""
    for i, entry in enumerate(step.entries):
        text += entry_text(entry)
        if i < len(step.entries) - 1:
            text += " + " if entry.connector == "plus" else " → "
    return text


def step_lines(steps: List[mdm_combos.Step], level: int = 0) -> List[Tuple[int, str, str]]:
    """[(Einrückung, Text, Art)] mit Art "step", "cond" (Verzweigung) oder "branch" (Zweig); Schritte nummeriert."""
    lines: List[Tuple[int, str, str]] = []
    for i, step in enumerate(steps, 1):
        lines.append((level, f"{i}. {step_text(step)}", "step"))
        if step.branches:
            lines.append((level, f"◆ {step.branch_label or 'Verzweigung'}", "cond"))
            for branch in step.branches:
                lines.append((level + 1, f"▸ {branch.label}", "branch"))
                lines += step_lines(branch.steps, level + 2)
    return lines


def date_text(iso: str) -> str:
    """'2026-10-02T12:30:00.000Z' → '02.10.2026'."""
    parts = iso[:10].split("-")
    return ".".join(reversed(parts)) if len(parts) == 3 else iso


class ComboPanel:
    def __init__(self, master: tk.Misc, db, main: Dict[str, int], extra: Dict[str, int], archetypes: List[str],
                 on_close: Callable[[], None], anchor: Optional[tk.Misc] = None, session=None,
                 image_folder: str = card_images.FOLDER):
        self.master = master
        self.db = db
        self.main = main                  # {englischer Kartenname: Kopien}
        self.extra = extra
        self.archetypes = archetypes      # häufigste Archetypen des Decks
        self.on_close = on_close
        self.session = session            # für Tests (requests-kompatibel)
        self.image_folder = image_folder
        self.closed = False
        self.sets: List[Tuple[mdm_combos.ComboSet, List[mdm_combos.ComboMatch]]] = []
        self.shown_type = 0               # Index des angezeigten Deck-Typs
        self.only_playable = True
        self.visible: List[mdm_combos.ComboMatch] = []   # Combos der Liste (nach Filter)
        self.detail: Optional[mdm_combos.ComboMatch] = None
        self.passcodes: Dict[str, str] = {}              # {name_key: Passcode} der Startkarten
        self.grid: Optional[CardGrid] = None             # Startkarten der Detailansicht
        self._result = None               # vom Hintergrund-Thread: Combos oder Fehlertext
        self._thread: Optional[threading.Thread] = None
        self._poll_job = None
        self.s = s = max(1.0, master.winfo_screenheight() / 1080.0)
        self.font = ("Helvetica", int(10 * s))
        self.font_bold = ("Helvetica", int(10 * s), "bold")
        self.font_small = ("Helvetica", int(8 * s))
        self.font_small_bold = ("Helvetica", int(8 * s), "bold")
        self.font_head = ("Helvetica", int(12 * s), "bold")
        self.photos = card_images.PhotoCache(master, (int(CARD_SIZE[0] * s), int(CARD_SIZE[1] * s)), image_folder)

        if anchor is not None:
            anchor.update_idletasks()  # Lage jetzt bestimmen (siehe StaplePanel)
        self.win = tk.Toplevel(master, bg=BG)
        self.win.overrideredirect(True)
        self.win.wm_attributes("-topmost", True)
        self._build()
        self._place(anchor)
        apply_frame(self.win)
        no_activate(self.win)  # Klicks lassen Master Duel aktiv (sonst Taskleiste über dem Spiel)
        self.win.bind("<Escape>", lambda e: self.close())
        self.win.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120) * 2, "units"))
        self.load()

    # ── Aufbau ──
    def _build(self) -> None:
        s = self.s
        header = tk.Frame(self.win, bg=PANEL)
        header.pack(fill=tk.X)
        tk.Label(header, text="◆ COMBOS", fg=GOLD, bg=PANEL, font=self.font_head).pack(
            side=tk.LEFT, padx=(int(12 * s), int(6 * s)), pady=int(8 * s))
        tk.Label(header, text="von Master Duel Meta", fg=MUTED, bg=PANEL, font=self.font).pack(side=tk.LEFT)
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
                                     justify=tk.LEFT, wraplength=int(490 * s))
        self.status_label.pack(fill=tk.X)
        self.type_row = tk.Frame(body, bg=BG)  # Umschalten zwischen den Deck-Typen (Hybrid-Decks)
        self.type_row.pack(fill=tk.X, pady=(int(4 * s), 0))
        self.bar = tk.Frame(body, bg=BG)       # Liste: Filter · Detail: Zurück / Master Duel Meta
        self.bar.pack(fill=tk.X, pady=(int(4 * s), int(4 * s)))
        # Hinweis unten zuerst packen, damit ihn die Liste (füllt den Rest) nicht verdrängt
        tk.Label(body, text="Quelle: Master Duel Meta (Community-Combos). Chance = alle genannten Startkarten in 5 "
                            "Karten.", fg=MUTED, bg=BG, font=self.font_small, anchor="w", justify=tk.LEFT,
                 wraplength=int(490 * s)).pack(side=tk.BOTTOM, fill=tk.X, pady=(int(4 * s), 0))

        frame = tk.Frame(body, bg=BG)
        frame.pack(fill=tk.BOTH, expand=True)
        self.canvas = tk.Canvas(frame, bg=BG, highlightthickness=0, bd=0)
        scroll = tk.Scrollbar(frame, command=self.canvas.yview)
        self.canvas.config(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self._inner_id = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.config(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._inner_id, width=e.width))

    def _place(self, anchor: Optional[tk.Misc]) -> None:
        """Unter dem Button "Combos", rechtsbündig mit dem Deck-Fenster (liegt über dessen Liste)."""
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

    # ── Laden (Hintergrund) ──
    def load(self, refresh: bool = False) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        if not self.archetypes:
            self.status_label.config(text="Keine Archetypen im Deck erkannt – keine Combos zu suchen.", fg=AMBER)
            return
        self.status_label.config(text=f"Lade Combos zu {', '.join(self.archetypes)} …", fg=NEON)
        self._thread = threading.Thread(target=self._worker, args=(refresh,), daemon=True)
        self._thread.start()
        self._poll()

    def _worker(self, refresh: bool) -> None:
        kwargs = {"session": self.session} if self.session is not None else {}
        try:
            found: Dict[str, mdm_combos.ComboSet] = {}
            for archetype in self.archetypes:
                combo_set = mdm_combos.load(self.db, archetype, refresh, **kwargs)
                if combo_set and combo_set.combos:
                    found.setdefault(combo_set.deck_type, combo_set)  # zwei Archetypen, ein Deck-Typ
            sets = [(c, mdm_combos.ranked(c.combos, self.main, self.extra)) for c in found.values()]
            self._load_images([card.name for c, _ in sets for combo in c.combos for card in combo.start if card.name])
            self._result = sets
        except Exception as e:  # offline, Seite geändert …
            self._result = f"Combos nicht geladen: {e}"

    def _load_images(self, names: List[str]) -> None:
        """Passcodes der Startkarten bestimmen und fehlende Bilder laden (offline: Platzhalter)."""
        names = list(dict.fromkeys(names))
        for name, ids in self.db.ids_by_name(names).items():
            self.passcodes[name_key(name)] = min(ids, key=lambda cid: (len(cid), cid))  # das Original
        codes = [self.passcodes.get(name_key(n)) for n in names]
        session = {"session": self.session} if self.session is not None else {}
        card_images.download(codes, folder=self.image_folder, **session)
        self.photos.prepare(codes)  # Anzeige ohne Ruckler

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
        self.sets, self.shown_type, self.detail = result, 0, None
        if not result:
            self.status_label.config(text=f"Keine Combos gefunden zu {', '.join(self.archetypes)}.", fg=AMBER)
            self._clear()
            return
        self.render()

    # ── Anzeige ──
    def _clear(self) -> None:
        for frame in (self.inner, self.bar):
            for child in frame.winfo_children():
                child.destroy()
        self.grid = None
        self.canvas.yview_moveto(0)

    def _button(self, parent: tk.Misc, text: str, command, bg: str = "#444444") -> RoundedButton:
        s = self.s
        button = RoundedButton(parent, text=text, command=command, bg=bg, font=self.font_small, padx=int(8 * s),
                               pady=int(2 * s), radius=int(6 * s))
        button.pack(side=tk.LEFT, padx=(0, int(6 * s)))
        return button

    def _click(self, widget: tk.Misc, command) -> None:
        """Klick auf das Widget und alles darin."""
        widget.bind("<Button-1>", lambda e: command())
        widget.config(cursor="hand2")
        for child in widget.winfo_children():
            self._click(child, command)

    def render(self) -> None:
        """Liste der Combos des gewählten Deck-Typs."""
        if self.closed or not self.sets:
            return
        s = self.s
        self.detail = None
        combo_set, matches = self.sets[self.shown_type]
        playable = sum(1 for m in matches if not m.missing)
        self.status_label.config(text=f"{combo_set.deck_type}: {len(matches)} Combos, {playable} mit deinem Deck "
                                      f"spielbar · Stand {when(combo_set.fetched_at)}", fg=MUTED)
        for child in self.type_row.winfo_children():
            child.destroy()
        if len(self.sets) > 1:
            tk.Label(self.type_row, text="Combos zu:", fg=MUTED, bg=BG, font=self.font_small).pack(side=tk.LEFT)
            for i, (other, _) in enumerate(self.sets):
                RoundedButton(self.type_row, text=other.deck_type, command=lambda i=i: self.show_type(i),
                              bg="#007acc" if i == self.shown_type else "#444444", font=self.font_small,
                              padx=int(8 * s), pady=int(2 * s), radius=int(6 * s)).pack(side=tk.LEFT,
                                                                                       padx=(int(6 * s), 0))
        self._clear()
        self.filter_btn = self._button(self.bar, "Nur spielbare" if self.only_playable else "Alle", self.toggle_filter)
        self.visible = [m for m in matches if not (self.only_playable and m.missing)]
        if not self.visible:
            tk.Label(self.inner, text="Keine Combo mit deinem Deck spielbar – „Alle“ zeigt auch die anderen.",
                     fg=AMBER, bg=BG, font=self.font_small, anchor="w", justify=tk.LEFT,
                     wraplength=int(460 * s)).pack(fill=tk.X, pady=int(6 * s))
        for i, match in enumerate(self.visible):
            self._row(i, match)

    def _row(self, index: int, match: mdm_combos.ComboMatch) -> None:
        s = self.s
        combo = match.combo
        bg = ROW_ALT if index % 2 else BG
        row = tk.Frame(self.inner, bg=bg, padx=int(8 * s), pady=int(4 * s))
        row.pack(fill=tk.X)
        head = tk.Frame(row, bg=bg)
        head.pack(fill=tk.X)
        tk.Label(head, text=f"▲ {combo.votes}", fg=MUTED, bg=bg, font=self.font_small).pack(side=tk.RIGHT)
        tk.Label(head, text=combo.title, fg=TEXT, bg=bg, font=self.font_bold, anchor="w", justify=tk.LEFT,
                 wraplength=int(340 * s)).pack(side=tk.LEFT)
        if combo.featured:
            tk.Label(head, text="★", fg=GOLD, bg=bg, font=self.font_bold).pack(side=tk.LEFT, padx=(int(4 * s), 0))
        line = tk.Frame(row, bg=bg)
        line.pack(fill=tk.X)
        text, color = odds_text(match.odds)
        tk.Label(line, text=text, fg=color, bg=bg, font=self.font_small_bold).pack(side=tk.RIGHT)
        tk.Label(line, text=hand_text(combo), fg=MUTED, bg=bg, font=self.font_small, anchor="w", justify=tk.LEFT,
                 wraplength=int(340 * s)).pack(side=tk.LEFT)
        if match.missing:
            tk.Label(row, text="Fehlt: " + ", ".join(match.missing), fg=AMBER, bg=bg, font=self.font_small,
                     anchor="w", justify=tk.LEFT, wraplength=int(460 * s)).pack(fill=tk.X)
        self._click(row, lambda i=index: self.show_combo(i))

    def show_combo(self, index: int) -> None:
        """Detailansicht der Combo (Index in der angezeigten Liste)."""
        if self.closed or not 0 <= index < len(self.visible):
            return
        s = self.s
        match = self.detail = self.visible[index]
        combo = match.combo
        self._clear()
        self._button(self.bar, "← Zurück", self.back)
        self._button(self.bar, "Auf Master Duel Meta öffnen", self.open_site, "#007acc")
        self._label(combo.title, self.font_head, TEXT)
        info = [f"von {combo.author}"] if combo.author else []
        self._label(" · ".join(info + [f"▲ {combo.votes}"] + ([f"Stand {date_text(combo.updated)}"]
                                                              if combo.updated else [])), self.font_small, MUTED)
        odds, color = odds_text(match.odds)
        self._label(f"Starthand: {hand_text(combo)}  ·  {odds}", self.font_small_bold, color, (int(6 * s), 0))
        if match.missing:
            self._label("Fehlt: " + ", ".join(match.missing), self.font_small, AMBER)
        self._hand(combo)
        if combo.steps:
            self._section("SCHRITTE")
            self._steps(combo.steps)
        if combo.end:
            self._section("ENDBOARD")
            for entry in combo.end:
                self._label(entry_text(entry), self.font_small, TEXT)
        if combo.vs_handtraps:
            self._section("SPIELT DURCH")
            self._label(", ".join(combo.vs_handtraps), self.font_small, TEXT)

    def _label(self, text: str, font: tuple, color: str, pady=(0, 0), indent: int = 0) -> None:
        s = self.s
        tk.Label(self.inner, text=text, fg=color, bg=BG, font=font, anchor="w", justify=tk.LEFT,
                 wraplength=int(460 * s) - indent).pack(fill=tk.X, padx=(indent, 0), pady=pady)

    def _section(self, title: str) -> None:
        self._label(title, self.font_small_bold, GOLD, (int(10 * self.s), int(2 * self.s)))

    def _hand(self, combo: mdm_combos.Combo) -> None:
        """Benannte Startkarten als Kartenbilder, Platzhalter als Text darunter."""
        s = self.s
        cards = [c for c in combo.start if c.name]
        if cards:
            self._section("STARTHAND")
            holder = tk.Frame(self.inner, bg=BG)
            holder.pack(fill=tk.X)
            grid = self.grid = CardGrid(holder, BG, PANEL, TEXT, self.photos, s, COLUMNS, self.font_small_bold, GOLD,
                                        self.font_small)
            have = {name_key(n): c for n, c in self.main.items()}
            for card in cards:
                grid.card(card.name, self.passcodes.get(name_key(card.name)),
                          [(f"×{have.get(name_key(card.name), 0)}", MUTED, self.font_small)])
            grid.finish()
            grid.canvas.config(height=grid.y + int(4 * s))
            grid.canvas.unbind("<Enter>")  # das Mausrad scrollt immer die Detailansicht
            grid.canvas.unbind("<Leave>")
        for card in combo.start:
            if not card.name:
                self._label(f"beliebig: {card.generic}" + (f" ({card.label})" if card.label else ""),
                            self.font_small, MUTED)

    def _steps(self, steps: List[mdm_combos.Step]) -> None:
        colors = {"step": TEXT, "cond": NEON, "branch": GOLD}
        for level, text, kind in step_lines(steps):
            font = self.font_small if kind == "step" else self.font_small_bold
            self._label(text, font, colors[kind], indent=int(INDENT * self.s) * level)

    def back(self) -> None:
        self.render()

    def toggle_filter(self) -> None:
        self.only_playable = not self.only_playable
        self.render()

    def show_type(self, index: int) -> None:
        self.shown_type = index
        self.render()

    def open_site(self) -> None:
        """Deck-Typ auf Master Duel Meta im Browser (nur auf Klick)."""
        if self.sets:
            webbrowser.open(MDM_DECK_URL.format(name=quote(self.sets[self.shown_type][0].deck_type, safe="")))

    # ── Zustand von außen ──
    def set_visible(self, visible: bool) -> None:
        if not self.closed:
            self.win.wm_attributes("-alpha", 1.0 if visible else 0.0)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        if self._poll_job is not None:
            try:
                self.win.after_cancel(self._poll_job)
            except tk.TclError:
                pass
        self.win.destroy()
        self.on_close()
