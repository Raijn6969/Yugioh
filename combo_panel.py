"""
Combos (Button "Combos" im Deck-Fenster): die Community-Combos von Master Duel Meta zum angezeigten Deck.

Die Liste zeigt je Combo die Startkarten als kleine Bilder, die Starthand, die Chance darauf (als Erster, 5 Karten) und
fehlende Karten; standardmäßig nur die Combos, die das Deck spielen kann. Klick auf eine Combo zeigt die ganze Linie:
Starthand, Schritte (jeder Schritt eine Bildzeile, dazwischen die Verbinder) mit Verzweigungen, das Endboard als Bilder
und die Handtraps, gegen die sie durchspielt. Geladen wird im Hintergrund (mdm_combos: einmal online, danach aus der
Datenbank), bei Hybrid-Decks lässt sich zwischen den Deck-Typen umschalten.

Gezeichnet wird auf Zeichenflächen (eine je Zeile bzw. Schritt) statt aus vielen Widgets (siehe card_grid). Die Bilder
der Startkarten lädt der Worker mit den Combos; die der Schritte und des Endboards erst beim Öffnen einer Combo (die
Ansicht erscheint sofort mit Platzhaltern und wird neu gezeichnet, sobald die Bilder da sind).
"""

import re
import threading
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from typing import Callable, Dict, Iterator, List, Optional, Tuple
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
CARD_SIZE = (66, 96)    # Bildgröße bei 1080p: Starthand in der Detailansicht
LIST_SIZE = (34, 50)    # … Startkarten in der Liste
STEP_SIZE = (46, 67)    # … Karten der Schritte und des Endboards
MAX_LIST_CARDS = 5      # Startkarten, die die Zeile der Liste als Bilder zeigt
INDENT = 14             # Einrückung je Ebene bei 1080p
GOOD_ODDS = 0.5         # ab dieser Chance grün
# Attribute für die Platzhalter ("Any FIRE monster"): Farbe und Kanji wie das Symbol im Spiel (selbst gezeichnet)
ATTRIBUTES = {"FIRE": ("#d32f2f", "炎"), "WATER": ("#1e88e5", "水"), "EARTH": ("#795548", "地"),
              "WIND": ("#2e7d32", "風"), "LIGHT": ("#f9a825", "光"), "DARK": ("#6a1b9a", "闇"),
              "DIVINE": ("#ff8f00", "神")}
ATTRIBUTE_RE = re.compile(r"\b(non[- ]?)?(fire|water|earth|wind|light|dark|divine)\b", re.I)
KANJI_FONTS = ("Yu Gothic UI", "Meiryo", "MS Gothic")
_kanji_font: List[Optional[str]] = []   # einmal ermittelt (None = keine passende Schrift)
MDM_DECK_URL = "https://www.masterduelmeta.com/tier-list/deck-types/{name}"


def hand_text(combo: mdm_combos.Combo) -> str:
    """'Elfnote Tinia + beliebig (Non-FIRE)' – Platzhalter mit dem gekürzten Text von Master Duel Meta."""
    parts = []
    for card in combo.start:
        generic = re.sub(r"\s+monster$", "", re.sub(r"^any\s+", "", card.generic.strip(), flags=re.I),
                         flags=re.I) or "Karte"
        parts.append(card.name or f"beliebig ({generic})")
    return " + ".join(parts) or "–"


def attributes(text: str) -> List[Tuple[str, bool]]:
    """Attribute im Platzhalter-Text in Reihenfolge, ohne Doppelte: "Non-FIRE" → [("FIRE", True)] (True = verneint)."""
    found: Dict[str, bool] = {}
    for match in ATTRIBUTE_RE.finditer(text):
        found.setdefault(match.group(2).upper(), bool(match.group(1)))
    return list(found.items())


def kanji_font() -> Optional[str]:
    """Erste vorhandene Schrift mit Kanji (None: keine – dann stehen zwei Buchstaben im Symbol)."""
    if not _kanji_font:
        families = set(tkfont.families())
        _kanji_font.append(next((f for f in KANJI_FONTS if f in families), None))
    return _kanji_font[0]


def draw_attribute(canvas: tk.Canvas, x: float, y: float, size: float, attribute: str, crossed: bool = False,
                   scale: float = 1.0) -> None:
    """Attributsymbol (farbiger Kreis, Kanji, ab 28·scale oben der Name) in das Quadrat bei (x, y); crossed = "Non-"."""
    color, kanji = ATTRIBUTES[attribute]
    cx, cy = x + size / 2, y + size / 2
    canvas.create_oval(x, y, x + size, y + size, fill=color, outline="#f5f5f5", width=2 if size >= 20 * scale else 1)
    family, named = kanji_font(), size >= 28 * scale
    glyph = kanji if family else attribute[:2]
    canvas.create_text(cx, cy + (size * 0.08 if named else 0), text=glyph, fill="white",
                       font=(family or "Helvetica", -int(size * (0.42 if family else 0.34)), "bold"))
    if named:
        canvas.create_text(cx, y + size * 0.2, text=attribute, fill="white",
                           font=("Helvetica", -max(6, int(size * 0.17)), "bold"))
    if crossed:  # dunkel unterlegt, damit der weiße Strich auf jeder Attributfarbe zu sehen ist
        for color, width in (("#111111", max(4, int(4 * scale))), ("#ffffff", max(2, int(2 * scale)))):
            canvas.create_line(x + size * 0.88, y + size * 0.12, x + size * 0.12, y + size * 0.88, fill=color,
                               width=width, capstyle=tk.ROUND)


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


def walk_steps(steps: List[mdm_combos.Step], level: int = 0) -> Iterator[Tuple[int, str, object, int]]:
    """(Einrückung, Art, Inhalt, Nummer): "step" = Step (nummeriert), "cond"/"branch" = Textzeile zur Verzweigung."""
    for i, step in enumerate(steps, 1):
        yield level, "step", step, i
        if step.branches:
            yield level, "cond", f"◆ {step.branch_label or 'Verzweigung'}", 0
            for branch in step.branches:
                yield level + 1, "branch", f"▸ {branch.label}", 0
                yield from walk_steps(branch.steps, level + 2)


def step_lines(steps: List[mdm_combos.Step]) -> List[Tuple[int, str, str]]:
    """Textfassung: [(Einrückung, Text, Art)] mit Art "step", "cond" (Verzweigung) oder "branch" (Zweig)."""
    return [(level, f"{number}. {step_text(item)}" if kind == "step" else item, kind)
            for level, kind, item, number in walk_steps(steps)]


def card_names(combo: mdm_combos.Combo) -> List[str]:
    """Alle Kartennamen der Combo (Startkarten, Schritte, Endboard) ohne Doppelte."""
    entries = [e for _, kind, item, _ in walk_steps(combo.steps) if kind == "step" for e in item.entries]
    names = [c.name for c in combo.start] + [e.card for e in entries + combo.end]
    return list(dict.fromkeys(n for n in names if n))


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
        self._tip: Optional[tk.Toplevel] = None
        self._tried: set = set()          # name_key der Karten, deren Bild schon gesucht wurde (auch ohne Erfolg)
        self._image_threads: List[threading.Thread] = []
        self._images_done: List[str] = []                # Combos, deren Bilder inzwischen geladen sind
        self._image_job = None
        self._result = None               # vom Hintergrund-Thread: Combos oder Fehlertext
        self._thread: Optional[threading.Thread] = None
        self._poll_job = None
        self.s = s = max(1.0, master.winfo_screenheight() / 1080.0)
        self.font = ("Helvetica", int(10 * s))
        self.font_bold = ("Helvetica", int(10 * s), "bold")
        self.font_small = ("Helvetica", int(8 * s))
        self.font_small_bold = ("Helvetica", int(8 * s), "bold")
        self.font_head = ("Helvetica", int(12 * s), "bold")
        self.photos, self.small, self.step_photos = (
            card_images.PhotoCache(master, (int(w * s), int(h * s)), image_folder)
            for w, h in (CARD_SIZE, LIST_SIZE, STEP_SIZE))

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
        """Passcodes der Karten bestimmen und fehlende Bilder laden (offline: Platzhalter). Läuft im Hintergrund."""
        names = list(dict.fromkeys(names))
        self._tried.update(name_key(n) for n in names)
        unknown = [n for n in names if name_key(n) not in self.passcodes]
        for name, ids in (self.db.ids_by_name(unknown).items() if unknown else ()):
            self.passcodes[name_key(name)] = min(ids, key=lambda cid: (len(cid), cid))  # das Original
        codes = [self.passcodes.get(name_key(n)) for n in names]
        session = {"session": self.session} if self.session is not None else {}
        card_images.download(codes, folder=self.image_folder, **session)
        for cache in (self.photos, self.small, self.step_photos):
            cache.prepare(codes)  # Anzeige ohne Ruckler

    def _image_worker(self, mdm_id: str, names: List[str]) -> None:
        try:
            self._load_images(names)
        except Exception:
            pass  # ohne Bilder bleiben die Platzhalter
        finally:
            self._images_done.append(mdm_id)

    def _fetch_images(self, combo: mdm_combos.Combo) -> None:
        """Fehlende Bilder der Combo im Hintergrund laden; danach wird die Ansicht neu gezeichnet."""
        need = [n for n in card_names(combo) if name_key(n) not in self._tried
                and (self.passcodes.get(name_key(n)) is None or self.photos.missing([self.passcodes[name_key(n)]]))]
        if not need:
            return
        self._tried.update(name_key(n) for n in need)
        thread = threading.Thread(target=self._image_worker, args=(combo.mdm_id, need), daemon=True)
        self._image_threads.append(thread)
        thread.start()
        if self._image_job is None:
            self._poll_images()

    def _poll_images(self) -> None:
        self._image_job = None
        if self.closed:
            return
        done, self._images_done = self._images_done, []
        if done:
            for cache in (self.photos, self.small, self.step_photos):
                cache.forget_missing()  # inzwischen geladen
            if self.detail is not None and self.detail.combo.mdm_id in done:
                top = self.canvas.yview()[0]
                self._draw_detail()
                self.win.update_idletasks()  # Scrollbereich neu berechnen, dann die Stelle wieder ansteuern
                self.canvas.yview_moveto(top)
        self._image_threads = [t for t in self._image_threads if t.is_alive()]
        if self._image_threads or self._images_done:
            self._image_job = self.win.after(POLL_MS, self._poll_images)

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
        self._hide_tip()
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
        """Eine Zeile auf einer Zeichenfläche: links die Startkarten als Bilder, rechts Titel, Chance und Fehlendes."""
        s = self.s
        combo = match.combo
        pad = int(6 * s)
        width = int(466 * s)
        w, h = self.small.size
        canvas = tk.Canvas(self.inner, bg=ROW_ALT if index % 2 else BG, highlightthickness=0, bd=0, width=width,
                           height=1, cursor="hand2")
        canvas.pack(fill=tk.X)
        x = pad
        for card in combo.start[:MAX_LIST_CARDS]:
            photo = self.small.get(self.passcodes.get(name_key(card.name))) if card.name else None
            if photo is not None:
                canvas.create_image(x, pad, image=photo, anchor="nw")
            else:  # Platzhalter ("beliebig …") bzw. Bild fehlt noch
                canvas.create_rectangle(x, pad, x + w, pad + h, fill=PANEL, outline="")
                attrs = attributes(card.generic) if not card.name else []
                if 1 <= len(attrs) <= 2:  # Attributsymbol(e) mittig, bei zwei übereinander
                    size = min(w - int(4 * s), (h - int(6 * s)) // len(attrs))
                    top = pad + (h - size * len(attrs)) // 2
                    for i, (attribute, crossed) in enumerate(attrs):
                        draw_attribute(canvas, x + (w - size) // 2, top + i * size, size, attribute, crossed, s)
                else:
                    canvas.create_text(x + w // 2, pad + h // 2, text="?" if not card.name else "", fill=MUTED,
                                       font=self.font_bold)
            x += w + int(3 * s)
        left = x + int(5 * s) if combo.start else pad
        right = width - pad

        def text(x, y, value, color, font, anchor="nw", wrap=0):
            item = canvas.create_text(x, y, text=value, fill=color, font=font, anchor=anchor, width=wrap)
            return canvas.bbox(item)

        votes = text(right, pad, f"▲ {combo.votes}", MUTED, self.font_small, "ne")
        title = text(left, pad, combo.title, TEXT, self.font_bold, wrap=votes[0] - left - int(24 * s))
        if combo.featured:
            text(title[2] + int(4 * s), pad, "★", GOLD, self.font_bold)
        y = title[3] + int(2 * s)
        odds, color = odds_text(match.odds)
        chance = text(right, y, odds, color, self.font_small_bold, "ne")
        hand = text(left, y, hand_text(combo), MUTED, self.font_small, wrap=chance[0] - left - pad)
        y = max(hand[3], chance[3]) + int(2 * s)
        if match.missing:
            y = text(left, y, "Fehlt: " + ", ".join(match.missing), AMBER, self.font_small, wrap=right - left)[3]
        canvas.config(height=max(y, pad + h) + pad)
        canvas.bind("<Button-1>", lambda e, i=index: self.show_combo(i))

    def show_combo(self, index: int) -> None:
        """Detailansicht der Combo (Index in der angezeigten Liste); fehlende Bilder kommen danach nach."""
        if self.closed or not 0 <= index < len(self.visible):
            return
        self.detail = self.visible[index]
        self._draw_detail()
        self._fetch_images(self.detail.combo)

    def _draw_detail(self) -> None:
        s = self.s
        match = self.detail
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
            self._entries(combo.end, connectors=False)
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
                                        self.font_small, scrollable=False)
            for card in cards:
                grid.card(card.name, self.passcodes.get(name_key(card.name)), [])
            grid.finish()
            grid.canvas.config(height=grid.y + int(4 * s))
        for card in combo.start:
            if not card.name:
                self._placeholder(card)

    def _placeholder(self, card: mdm_combos.HandCard) -> None:
        """Zeile "beliebig: …"; mit ein oder zwei Attributen steht davor das Symbol."""
        text = f"beliebig: {card.generic}" + (f" ({card.label})" if card.label else "")
        attrs = attributes(card.generic)
        if not 1 <= len(attrs) <= 2:
            self._label(text, self.font_small, MUTED)
            return
        s = self.s
        size, gap = int(22 * s), int(4 * s)
        row = tk.Frame(self.inner, bg=BG)
        row.pack(fill=tk.X, pady=(0, int(2 * s)))
        canvas = tk.Canvas(row, bg=BG, highlightthickness=0, bd=0, width=len(attrs) * (size + gap), height=size)
        canvas.pack(side=tk.LEFT)
        for i, (attribute, crossed) in enumerate(attrs):
            draw_attribute(canvas, i * (size + gap), 0, size, attribute, crossed, s)
        tk.Label(row, text=text, fg=MUTED, bg=BG, font=self.font_small, anchor="w", justify=tk.LEFT,
                 wraplength=int(430 * s) - len(attrs) * (size + gap)).pack(side=tk.LEFT, fill=tk.X)

    def _steps(self, steps: List[mdm_combos.Step]) -> None:
        colors = {"cond": NEON, "branch": GOLD}
        for level, kind, item, number in walk_steps(steps):
            indent = int(INDENT * self.s) * level
            if kind == "step":
                self._entries(item.entries, f"{number}.", indent)
            else:
                self._label(item, self.font_small_bold, colors[kind], indent=indent)

    def _entries(self, entries: List[mdm_combos.Entry], number: str = "", indent: int = 0,
                 connectors: bool = True) -> None:
        """
        Einträge als Bildzeile auf einer Zeichenfläche: Kartenbild, darunter die Aktion; dazwischen → bzw. + (der
        Verbinder des Eintrags davor). Passt nicht alles in eine Reihe, geht es darunter weiter. Einträge ohne Karte
        sind nur Text. Beim Darüberfahren erscheint der Kartenname mit der Aktion.
        """
        s = self.s
        w, h = self.step_photos.size
        item_w, joint = w + int(20 * s), int(18 * s) if connectors else 0
        left = int(24 * s) if number else 0
        total = int(460 * s) - indent
        gap = int(6 * s)
        canvas = tk.Canvas(self.inner, bg=BG, highlightthickness=0, bd=0, width=total, height=1)
        canvas.pack(fill=tk.X, padx=(indent, 0), pady=(0, int(4 * s)))
        if number:
            canvas.create_text(0, int(2 * s), text=number, fill=TEXT, font=self.font_small_bold, anchor="nw")
        x, y, bottom = left, 0, 0
        for i, entry in enumerate(entries):
            if i and x + joint + item_w > total:  # neue Reihe
                x, y = left, bottom + gap
                bottom = y
            if i and connectors:
                sign = "+" if entries[i - 1].connector == "plus" else "→"
                canvas.create_text(x + joint // 2, y + (h // 2 if entry.card else int(8 * s)), text=sign, fill=NEON,
                                   font=self.font_bold)
            x += joint if i else 0
            tag = f"entry{i}"
            top = y
            if entry.card:
                photo = self.step_photos.get(self.passcodes.get(name_key(entry.card)))
                px = x + (item_w - w) // 2
                if photo is not None:
                    canvas.create_image(px, y, image=photo, anchor="nw", tags=(tag,))
                else:
                    canvas.create_rectangle(px, y, px + w, y + h, fill=PANEL, outline="", tags=(tag,))
                    canvas.create_text(px + w // 2, y + h // 2, text=entry.card, fill=TEXT, font=self.font_small,
                                       width=w - int(6 * s), justify=tk.CENTER, tags=(tag,))
                top = y + h + int(3 * s)
                bottom = max(bottom, y + h)
                canvas.tag_bind(tag, "<Enter>", lambda e, en=entry: self._show_tip(e, en.card, en.label))
                canvas.tag_bind(tag, "<Leave>", lambda e: self._hide_tip())
            if entry.label:
                label = canvas.create_text(x + item_w // 2, top, text=entry.label, fill=MUTED if entry.card else TEXT,
                                           font=self.font_small, anchor="n", width=item_w, justify=tk.CENTER,
                                           tags=(tag,))
                bottom = max(bottom, canvas.bbox(label)[3])
            x += item_w
        canvas.config(height=bottom + int(2 * s))

    def _show_tip(self, event, name: str, detail: str) -> None:
        """Kleines Fenster mit Kartenname und Aktion neben der Maus."""
        self._hide_tip()
        s = self.s
        tip = tk.Toplevel(self.win, bg=GOLD)
        tip.overrideredirect(True)
        tip.wm_attributes("-topmost", True)
        box = tk.Frame(tip, bg=PANEL, padx=int(8 * s), pady=int(4 * s))
        box.pack(padx=1, pady=1)
        tk.Label(box, text=name, fg=TEXT, bg=PANEL, font=self.font_bold, anchor="w").pack(fill=tk.X)
        if detail:
            tk.Label(box, text=detail, fg=MUTED, bg=PANEL, font=self.font_small, anchor="w").pack(fill=tk.X)
        tip.geometry(f"+{event.x_root + int(14 * s)}+{event.y_root + int(14 * s)}")
        self._tip = tip

    def _hide_tip(self) -> None:
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None

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
        self._hide_tip()
        for job in (self._poll_job, self._image_job):
            if job is not None:
                try:
                    self.win.after_cancel(job)
                except tk.TclError:
                    pass
        self.win.destroy()
        self.on_close()
