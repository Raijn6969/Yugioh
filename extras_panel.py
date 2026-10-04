"""
Deck-Fenster (Button "Deck" im Overlay): öffnet sich über der Kartenliste von Master Duel.

Draw-Chance-Rechner: Das Deck wird einmal gescannt (wie beim Export). Danach zeigt die Liste
für jede Karte die Chance, sie auf der Starthand zu haben, und ob sie ein Starter bzw. eine Handtrap ist
(dazu die Chance auf mind. 1 Starter und mind. 1 Handtrap auf der Starthand). Fährt man
im Deck mit der Maus über eine Karte, erscheint daneben ein kleines Analyse-Fenster mit den Stats.
Ändert sich das Deck (Kartenzahl oder Karten), meldet das Menü "bitte neu scannen".
Optionen → "Starter aus Guides nachladen": holt die Starter-Einstufungen für das Deck aus den Guides von
Master Duel Meta (falls die Textregeln danebenliegen) und speichert sie offline.
Optionen → "Karten lesen": Speicher oder Texterkennung – gilt für Import, Deck-Scan und Export. Bei "Speicher lesen"
steht hier auch das Tempo (nur noch Reserve für langsame PCs); bei Texterkennung sitzt es im Overlay.
"Verlauf" (links neben Optionen): importierte Decks mit Suche; Klick auf ein Deck zeigt seine Analyse hier an
(aus dem Deck-Code, ohne das Spiel zu lesen), "Neu scannen" liest wieder das Deck im Spiel.
"Winrate" (links neben Verlauf): Siege/Niederlagen aus der Match History von Master Duel (winrate_panel); unter den
Kacheln steht die Winrate des angezeigten Decks (Decks mit denselben Archetypen zählen zusammen).
"""

import threading
import time
import tkinter as tk
from collections import Counter
from types import SimpleNamespace
import tkinter.font as tkfont
from typing import Callable, Dict, List, Optional

import md_layout
import win_api
import starter_guides
from card_stats import CardInfo, CardStatsDB
from options_popup import OptionsPopup
from deck_analysis import DeckAnalysis, DeckEntry, DeckWatcher, current_changes, slot_at, slot_rect
from deck_export import DeckScan
from draw_odds import HAND_FIRST, HAND_SECOND, p_at_least
from hover_card import ANALYSIS_TIME, AMBER, GREEN, NEON, RED, HoverCard, HoverContent, HoverRow
from rounded_button import RoundedButton
from window_style import apply_frame

BG = "#1e1e1e"
PANEL = "#2b2b2b"
ROW_ALT = "#242424"
HIGHLIGHT = "#0d3440"
TEXT = "#e8e8e8"
MUTED = "#9a9a9a"
GOLD = "#c9a02f"
SHOW_SECOND_KEY = "DECK_SHOW_SECOND_TURN"  # Einstellung: Spalte "2. Zug" anzeigen
ANIMATION_KEY = "DECK_HOVER_ANIMATION"     # Einstellung: "Analyse…"-Animation (aus = Stats sofort)
# Einstellung: Wie Karten gelesen werden (Import, Deck-Scan, Export); fehlt sie, fragt das Overlay beim Start
READ_METHOD_KEY = "READ_METHOD"
READ_METHODS = {"memory": "Speicher lesen", "ocr": "Texterkennung"}
SPEED_KEY = "SPEED_PROFILE"
SPEEDS = {"fast": "Schnell", "normal": "Normal", "slow": "Langsam"}
WATCH_AFTER_HOVER = 1.5  # So lange nach dem Hover-Fenster keine Deck-Prüfung (liegt evtl. über dem Deck; schont FPS)
GUIDE_ARCHETYPES = 3   # Guides für die häufigsten Archetypen im Deck
GUIDE_MIN_COPIES = 3   # … mit mindestens so vielen Karten
POLL_MS = 50          # Wie oft die Maus geprüft wird
SHOW_DELAY = 0.12     # So lange muss die Maus auf einer Karte liegen, bevor das Info-Fenster kommt/wechselt
HIDE_DELAY = 0.3      # Lücken zwischen Karten überbrücken: erst nach so langer Zeit ohne Karte ausblenden
FRAME_REFRESH = 0.5   # Sekunden, bis die Fensterposition von Master Duel neu abgefragt wird

SPELL_KINDS = {"Normal": "Normal", "Quick-Play": "Schnell", "Field": "Feld", "Continuous": "Permanent",
               "Equip": "Ausrüstung", "Ritual": "Ritual", "Counter": "Konter"}


def pct(p: float) -> str:
    return f"{p * 100:.1f} %".replace(".", ",")


def type_line(info: Optional[CardInfo]) -> str:
    """'Monster · Stufe 4 · Dracotail' aus den gespeicherten Kartendaten."""
    if info is None:
        return ""
    parts = []
    card_type = info.card_type.lower()
    if "spell" in card_type:
        parts.append(f"Zauber · {SPELL_KINDS.get(info.race, info.race)}")
    elif "trap" in card_type:
        parts.append(f"Falle · {SPELL_KINDS.get(info.race, info.race)}")
    else:
        kind = next((k.capitalize() for k in ("fusion", "synchro", "xyz", "link") if k in card_type), "Monster")
        parts.append(kind)
        if info.level and kind != "Link":
            parts.append(f"{'Rang' if kind == 'Xyz' else 'Stufe'} {info.level}")
    if info.archetype:
        parts.append(info.archetype)
    return " · ".join(parts)


class ExtrasPanel:
    def __init__(self, master: tk.Misc, stats_db: Optional[CardStatsDB], tesseract_cmd: str,
                 on_rescan: Callable[[], None], on_close: Callable[[], None],
                 is_active: Callable[[], bool] = lambda: True, settings: Optional[dict] = None,
                 save_settings: Callable[[], None] = lambda: None,
                 on_import_code: Optional[Callable[[str], None]] = None,
                 on_copy_code: Optional[Callable[[str], None]] = None):
        self.master = master
        self.settings = settings if settings is not None else {}  # Config des Overlays (Optionen)
        self.save_settings = save_settings
        self.stats_db = stats_db
        self.tesseract_cmd = tesseract_cmd
        self.on_rescan = on_rescan
        self.on_close = on_close
        self.is_active = is_active
        self.on_import_code = on_import_code  # Verlauf: Deck erneut importieren
        self.on_copy_code = on_copy_code      # Verlauf: Deck-Code kopieren
        self.s = scale = max(1.0, master.winfo_screenheight() / 1080.0)
        self.font = ("Helvetica", int(10 * scale))
        self.font_bold = ("Helvetica", int(10 * scale), "bold")
        self.font_small = ("Helvetica", int(8 * scale))
        self.font_head = ("Helvetica", int(12 * scale), "bold")
        self.font_num = ("Consolas", int(10 * scale), "bold")
        self.font_big = ("Consolas", int(18 * scale), "bold")

        self.analysis: Optional[DeckAnalysis] = None
        self.watcher: Optional[DeckWatcher] = None
        self.scanning = False
        self.closed = False
        self.offline = False                   # True = Deck aus dem Verlauf (nicht das Deck im Spiel)
        self.history = None                    # Verlauf-Fenster, None = zu
        self.winrate = None                    # Winrate-Fenster, None = zu
        self._deck_name: Optional[str] = None  # Name des angezeigten Decks (für dessen Winrate)
        self._history_result = None            # (Scan, Eintrag, fehlende Stats) bzw. Fehlertext vom Laden
        self._visible = True
        self._hover_slot = None
        self._candidate = None                 # Karte unter der Maus (noch nicht angezeigt)
        self._candidate_since = 0.0
        self._cursor_slot = None
        self._frame_cache = (0.0, None)
        self._rows: Dict[str, tk.Frame] = {}
        self._row_bg: Dict[str, str] = {}
        self._highlighted: Optional[str] = None
        self._shown_reason: Optional[str] = None
        self._poll_job = None
        self._guide_thread: Optional[threading.Thread] = None
        self._guide_result = None              # (Text, Farbe) vom Guide-Abruf, wird im UI-Thread angezeigt

        self.win = tk.Toplevel(master, bg=BG)
        self.win.overrideredirect(True)
        self.win.wm_attributes("-topmost", True)
        self.win.wm_attributes("-alpha", 1.0)
        self.show_second = tk.BooleanVar(master=self.win, value=bool(self.settings.get(SHOW_SECOND_KEY, False)))
        self.animation = tk.BooleanVar(master=self.win, value=bool(self.settings.get(ANIMATION_KEY, True)))
        self.read_method = tk.StringVar(master=self.win, value=self.settings.get(READ_METHOD_KEY, "ocr"))
        self.speed = tk.StringVar(master=self.win, value=self.settings.get(SPEED_KEY, "normal"))
        self._build()
        self._place_over_card_list()
        apply_frame(self.win)
        self.win.bind("<Escape>", lambda e: self.close())
        self.hover = HoverCard(master, scale)
        self._poll()

    # ── Aufbau ──
    def _build(self) -> None:
        s = self.s
        header = tk.Frame(self.win, bg=PANEL)
        header.pack(fill=tk.X)
        tk.Label(header, text="◆ DECK", fg=GOLD, bg=PANEL, font=self.font_head).pack(
            side=tk.LEFT, padx=(int(12 * s), int(6 * s)), pady=int(8 * s))
        tk.Label(header, text="Draw-Chance, Starter & Handtraps", fg=MUTED, bg=PANEL, font=self.font).pack(
            side=tk.LEFT)
        RoundedButton(header, text="✕", command=self.close, bg="#cc0000", border="#ff8a80",
                      font=("Helvetica", int(9 * s), "bold"), padx=int(9 * s), pady=int(2 * s),
                      radius=int(6 * s)).pack(side=tk.RIGHT, padx=int(8 * s))
        # Optionen links neben ✕
        self.options_btn = RoundedButton(header, text="Optionen ▾", command=self._open_options, bg="#444444",
                                         font=self.font_small, padx=int(9 * s), pady=int(3 * s), radius=int(6 * s))
        self.options_btn.pack(side=tk.RIGHT)
        if self.stats_db is not None:  # Verlauf liegt in der Datenbank
            self.history_btn = RoundedButton(header, text="Verlauf", command=self.toggle_history, bg="#00796b",
                                             font=self.font_small, padx=int(9 * s), pady=int(3 * s),
                                             radius=int(6 * s))
            self.history_btn.pack(side=tk.RIGHT, padx=(0, int(6 * s)))
            self.winrate_btn = RoundedButton(header, text="Winrate", command=self.toggle_winrate, bg="#b8860b",
                                             font=self.font_small, padx=int(9 * s), pady=int(3 * s),
                                             radius=int(6 * s))
            self.winrate_btn.pack(side=tk.RIGHT, padx=(0, int(6 * s)))
        self.options_menu = OptionsPopup(self.win, self.font, self.font_small, self.font_bold, scale=s)
        self.options_menu.add_section("Karten lesen", "gilt für Import, Deck-Scan und Export")
        self.options_menu.add_choice([(label, key) for key, label in READ_METHODS.items()], self.read_method,
                                     command=self._on_read_method_changed)
        memory = lambda: self.read_method.get() == "memory"  # noqa: E731
        self.options_menu.add_section("Tempo", "beim Speicher-Lesen nur Reserve: „Langsam“ für langsame PCs",
                                      visible=memory)
        self.options_menu.add_choice([(label, key) for key, label in SPEEDS.items()], self.speed,
                                     command=self._on_speed_changed, visible=memory)
        self.options_menu.add_section("Anzeige")
        self.options_menu.add_toggles([("Spalte „2. Zug“", self.show_second), ("Analyse-Animation", self.animation)],
                                      command=self._on_option_changed)
        self.options_menu.add_section("Starter", "Einstufung aus den Deck-Guides von Master Duel Meta")
        self.options_menu.add_buttons([("Aus Guides nachladen (online)", self.load_guides)])

        body = tk.Frame(self.win, bg=BG, padx=int(12 * s), pady=int(8 * s))
        body.pack(fill=tk.BOTH, expand=True)
        self.status_label = tk.Label(body, text="", fg=NEON, bg=BG, font=self.font_bold, anchor="w",
                                     justify=tk.LEFT, wraplength=int(480 * s))
        self.status_label.pack(fill=tk.X)

        # Kacheln: Main / Extra / Starter / Handtraps
        tiles = tk.Frame(body, bg=BG)
        tiles.pack(fill=tk.X, pady=(int(6 * s), int(4 * s)))
        self.tiles = {}
        for key, caption in (("main", "MAIN DECK"), ("extra", "EXTRA DECK"), ("starter", "STARTER"),
                             ("handtrap", "HANDTRAPS")):
            tile = tk.Frame(tiles, bg=PANEL, padx=int(8 * s), pady=int(4 * s))
            tile.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, int(6 * s)))
            value = tk.Label(tile, text="–", fg=TEXT, bg=PANEL, font=self.font_big)
            value.pack()
            tk.Label(tile, text=caption, fg=MUTED, bg=PANEL, font=self.font_small).pack()
            self.tiles[key] = value
        # Chance auf mind. 1 Starter, rechts daneben auf mind. 1 Handtrap
        odds = tk.Frame(body, bg=BG)
        odds.pack(fill=tk.X, pady=(int(2 * s), int(6 * s)))
        self.starter_odds = tk.Label(odds, text="", fg=TEXT, bg=BG, font=self.font, anchor="w", justify=tk.LEFT)
        self.starter_odds.pack(side=tk.LEFT, anchor="n")
        self.handtrap_odds = tk.Label(odds, text="", fg=TEXT, bg=BG, font=self.font, anchor="e", justify=tk.RIGHT)
        self.handtrap_odds.pack(side=tk.RIGHT, anchor="n", padx=(0, int(6 * s)))
        # Winrate des angezeigten Decks (aus der Match History; leer, solange es keine Matches dazu gibt)
        self.winrate_label = tk.Label(body, text="", fg=MUTED, bg=BG, font=self.font_small, anchor="w")
        self.winrate_label.pack(fill=tk.X, pady=(0, int(4 * s)))

        # Tabelle (Spalten je nach Option, siehe _rebuild_header)
        self.table_head = tk.Frame(body, bg=PANEL)
        self.table_head.pack(fill=tk.X)
        self._rebuild_header()
        list_frame = tk.Frame(body, bg=BG)
        list_frame.pack(fill=tk.BOTH, expand=True)
        self.list_canvas = tk.Canvas(list_frame, bg=BG, highlightthickness=0, bd=0)
        bar = tk.Scrollbar(list_frame, command=self.list_canvas.yview)
        self.list_canvas.config(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        # Kopfzeile so breit wie die Zeilen darunter (ohne Scrollleiste), sonst stehen die Überschriften versetzt
        self.table_head.pack_configure(padx=(0, bar.winfo_reqwidth()))
        self.list_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.list_inner = tk.Frame(self.list_canvas, bg=BG)
        self._inner_id = self.list_canvas.create_window(0, 0, window=self.list_inner, anchor="nw")
        self.list_inner.bind("<Configure>", lambda e: self.list_canvas.config(
            scrollregion=self.list_canvas.bbox("all")))
        self.list_canvas.bind("<Configure>", lambda e: self.list_canvas.itemconfigure(self._inner_id, width=e.width))
        self.list_canvas.bind("<Enter>", lambda e: self.list_canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.list_canvas.bind("<Leave>", lambda e: self.list_canvas.unbind_all("<MouseWheel>"))
        tk.Label(body, text="Klick auf Ja/Nein ändert die Einstufung · Rechtsklick: wieder automatisch",
                 fg=MUTED, bg=BG, font=self.font_small, anchor="w").pack(fill=tk.X, pady=(int(2 * s), 0))

        self._build_calculator(body)

        footer = tk.Frame(body, bg=BG)
        footer.pack(fill=tk.X, pady=(int(8 * s), 0))
        tk.Label(footer, text="Tipp: Im Deck mit der Maus über eine Karte fahren", fg=MUTED, bg=BG,
                 font=self.font_small).pack(side=tk.LEFT)
        self.rescan_btn = RoundedButton(footer, text="Neu scannen", command=self._rescan, bg="#5e35b1",
                                        font=self.font_bold, padx=int(12 * s), pady=int(4 * s), radius=int(7 * s))
        self.rescan_btn.pack(side=tk.RIGHT)

    def _build_calculator(self, parent: tk.Frame) -> None:
        s = self.s
        box = tk.Frame(parent, bg=PANEL, padx=int(10 * s), pady=int(6 * s))
        box.pack(fill=tk.X, pady=(int(8 * s), 0))
        tk.Label(box, text="RECHNER", fg=GOLD, bg=PANEL, font=self.font_small).grid(row=0, column=0, columnspan=8,
                                                                                   sticky="w")
        self.calc_vars = {}
        for col, (key, label, default, low, high) in enumerate((
                ("deck", "Deck", 40, 1, 90), ("copies", "Kopien", 3, 0, 60),
                ("hand", "Hand", HAND_FIRST, 1, 15), ("min", "mind.", 1, 1, 5))):
            var = tk.IntVar(value=default)
            tk.Label(box, text=label, fg=MUTED, bg=PANEL, font=self.font_small).grid(row=1, column=col * 2,
                                                                                    padx=(0, int(3 * s)))
            spin = tk.Spinbox(box, from_=low, to=high, textvariable=var, width=3, font=self.font_num, bg=BG,
                              fg=TEXT, buttonbackground=PANEL, insertbackground=TEXT, relief=tk.FLAT,
                              highlightthickness=1, highlightbackground="#444444", highlightcolor=NEON,
                              command=self._update_calculator)
            spin.grid(row=1, column=col * 2 + 1, padx=(0, int(8 * s)))
            spin.bind("<KeyRelease>", lambda e: self._update_calculator())
            self.calc_vars[key] = var
        self.calc_result = tk.Label(box, text="", fg=NEON, bg=PANEL, font=self.font_num)
        self.calc_result.grid(row=1, column=8, sticky="e")
        box.grid_columnconfigure(8, weight=1)
        self._update_calculator()

    def _rebuild_header(self) -> None:
        s = self.s
        # Feste Spaltenbreiten nach dem längsten möglichen Inhalt: keine Zeile darf eine Spalte verbreitern,
        # sonst stehen die Zahlen nicht mehr rechtsbündig untereinander. ✎ (selbst festgelegt) in eigener Spalte.
        percent = max(int(64 * s), self._label_width("100,0 %", self.font))
        columns = [("×", int(40 * s)), (f"1. Zug ({HAND_FIRST})", percent)]
        if self.show_second.get():
            columns.append((f"2. Zug ({HAND_SECOND})", percent))
        mark = self._label_width("✎", self.font_bold)
        columns += [("Starter", max(int(46 * s), self._label_width("Nein", self.font_bold))), ("", mark),
                    ("Handtrap", max(self._label_width("Handtrap", self.font_small),
                                     self._label_width("Nein", self.font_bold))), ("", mark)]
        # Auch die Überschrift muss hineinpassen (sonst ist die Spalte in der Kopfzeile breiter als darunter)
        columns = [(title, max(width, self._label_width(title, self.font_small))) for title, width in columns]
        self.columns = [width for _, width in columns]
        for child in self.table_head.winfo_children():
            child.destroy()
        for col in range(len(columns) + 2):
            self.table_head.grid_columnconfigure(col, minsize=0)
        self._table_row(self.table_head, PANEL, ["Karte"] + [title for title, _ in columns],
                        [MUTED] * (len(columns) + 1), self.font_small)

    def _label_width(self, text: str, font) -> int:
        """Breite einer Tabellenzelle mit diesem Text (am echten Label gemessen: ✎ kommt aus einer Ersatzschrift)."""
        label = tk.Label(self.win, text=text, font=font, padx=int(6 * self.s))
        width = label.winfo_reqwidth()
        label.destroy()
        return width

    def _open_options(self) -> None:
        self.options_menu.popup_below(self.options_btn)

    def _on_option_changed(self) -> None:
        self.settings[SHOW_SECOND_KEY] = bool(self.show_second.get())
        self.settings[ANIMATION_KEY] = bool(self.animation.get())
        self.save_settings()
        self._rebuild_header()
        if self.analysis is not None:
            self._render()

    def _on_read_method_changed(self) -> None:
        """Lesemethode für Import, Deck-Scan und Export (gilt ab dem nächsten Lesen)."""
        self.settings[READ_METHOD_KEY] = self.read_method.get()
        self.save_settings()
        self.options_menu.rebuild()  # Tempo nur bei "Speicher lesen" (sonst im Overlay)
        self.set_status(f"Karten lesen: {READ_METHODS[self.read_method.get()]} – gilt ab dem nächsten Scan/Import",
                        NEON)

    def _on_speed_changed(self) -> None:
        self.settings[SPEED_KEY] = self.speed.get()
        self.save_settings()
        self.set_status(f"Tempo: {SPEEDS[self.speed.get()]}", NEON)

    # ── Starter aus Guides (Master Duel Meta) ──
    def load_guides(self) -> None:
        """Starter-Einstufungen für das gescannte Deck online nachladen (im Hintergrund)."""
        if self.analysis is None or self.scanning or self.stats_db is None:
            self.set_status("Erst das Deck scannen, dann Starter nachladen", AMBER)
            return
        if self._guide_thread is not None and self._guide_thread.is_alive():
            return
        ids = [e.key for e in self.analysis.entries("Main") if not e.key.startswith("?")]
        copies = {e.key: e.copies for e in self.analysis.entries("Main")}
        self.set_status("Lade Starter aus den Deck-Guides (Master Duel Meta) …", NEON)
        self._guide_thread = threading.Thread(target=self._guide_worker, args=(self.stats_db, ids, copies),
                                              daemon=True)
        self._guide_thread.start()

    def _guide_worker(self, db: CardStatsDB, ids: List[str], copies: Dict[str, int]) -> None:
        try:
            db.refresh(ids)  # aktuelle Kartentexte (Errata, neue Karten)
            infos = {cid: db.info(cid) for cid in ids}
            infos = {cid: info for cid, info in infos.items() if info is not None}
            weight = Counter()
            for cid, info in infos.items():
                if info.archetype:
                    weight[info.archetype] += copies.get(cid, 1)
            archetypes = [name for name, count in weight.most_common(GUIDE_ARCHETYPES) if count >= GUIDE_MIN_COPIES]
            if not archetypes:
                self._guide_result = ("Keine Archetypen im Deck erkannt – keine Guides gefunden", AMBER)
                return
            verdicts = starter_guides.lookup(archetypes, [info.name for info in infos.values()])
            stored = db.store_guide(verdicts)
            in_deck = {cid: value for cid, value in stored.items() if cid in infos}
            guides = sorted({v.guide.strip() for v in verdicts})
            if not guides:
                self._guide_result = (f"Kein Guide zu {', '.join(archetypes)} gefunden", AMBER)
                return
            yes = sum(1 for v in in_deck.values() if v)
            self._guide_result = (f"Guides geladen ({', '.join(guides)}): {yes} Starter, "
                                  f"{len(in_deck) - yes} kein Starter in deinem Deck übernommen", GREEN)
        except Exception as e:  # offline, Seite geändert …
            self._guide_result = (f"Starter nachladen fehlgeschlagen: {e}", RED)

    def _show_guide_result(self) -> None:
        if self._guide_result is None:
            return
        text, color = self._guide_result
        self._guide_result = None
        if self.analysis is not None:
            self.analysis.reload_starters()
            self._render()
        self.set_status(text, color)

    # ── Verlauf der importierten Decks ──
    def toggle_history(self) -> None:
        if self.history is not None:
            self._close_history()
            return
        from history_panel import HistoryPanel  # importiert die Farben von hier
        self._close_winrate()  # beide lägen an derselben Stelle
        self.history =HistoryPanel(self.win, self.stats_db, on_import=self._import_code, on_copy=self._copy_code,
                                    on_close=self._on_history_closed, on_open=self.load_history_deck,
                                    anchor=self.history_btn)

    def _close_history(self) -> None:
        if self.history is not None:
            self.history.close()  # ruft _on_history_closed

    def _on_history_closed(self) -> None:
        self.history = None

    # ── Winrate (Match History von Master Duel) ──
    def toggle_winrate(self) -> None:
        if self.winrate is not None:
            self._close_winrate()
            return
        from winrate_panel import WinratePanel  # importiert die Farben von hier
        self._close_history()  # beide lägen an derselben Stelle
        self.winrate = WinratePanel(self.win, self.stats_db, self.settings, self.save_settings,
                                    on_open=self._open_winrate_deck, on_close=self._on_winrate_closed,
                                    tracking=self.read_method.get() == "memory", anchor=self.winrate_btn)

    def _close_winrate(self) -> None:
        if self.winrate is not None:
            self.winrate.close()  # ruft _on_winrate_closed

    def _on_winrate_closed(self) -> None:
        self.winrate = None

    def _open_winrate_deck(self, name: str, code: str) -> None:
        """Deck aus der Winrate hier anzeigen (wie aus dem Verlauf)."""
        self._close_winrate()
        self.load_history_deck(SimpleNamespace(name=name, code=code))

    def refresh_winrate(self) -> None:
        """Neue Matches übernommen → Winrate-Fenster und die Zeile zum Deck aktualisieren."""
        if self.closed:
            return
        if self.winrate is not None:
            self.winrate.refresh()
        self._show_deck_winrate()

    def _show_deck_winrate(self) -> None:
        if self.stats_db is None or self.analysis is None:
            self.winrate_label.config(text="")
            return
        from winrate_panel import summary
        from match_history import RANKED
        try:
            if self._deck_name is None:
                ids = [e.key for zone in ("Main", "Extra") for e in self.analysis.entries(zone)
                       for _ in range(e.copies) if not e.key.startswith("?")]
                self._deck_name = self.stats_db.deck_name(ids)
            stats = self.stats_db.win_stats(self._deck_name, RANKED)
            text = f"Winrate Ranked ({self._deck_name}): {summary(stats)}" if stats.matches else ""
        except Exception:  # Datenbank gesperrt o.Ä. – Winrate ist nur Zusatz
            text = ""
        self.winrate_label.config(text=text)

    def _import_code(self, code: str) -> None:
        if self.on_import_code is not None:
            self.on_import_code(code)

    def _copy_code(self, code: str) -> None:
        if self.on_copy_code is not None:
            self.on_copy_code(code)
            self.set_status("Deck-Code kopiert", GREEN)

    def load_history_deck(self, entry) -> None:
        """Deck aus dem Verlauf hier anzeigen (Analyse aus dem Deck-Code, im Hintergrund – evtl. Stats online)."""
        if self.scanning:
            return
        self.set_status(f"Lade „{entry.name}“ aus dem Verlauf …", NEON)
        threading.Thread(target=self._history_worker, args=(entry,), daemon=True).start()

    def _history_worker(self, entry) -> None:
        from history_panel import deck_from_history
        try:
            scan = deck_from_history(entry.code, self.stats_db)
            missing = self.stats_db.ensure(c.match.cid for c in scan.cards if c.match.cid)
            if missing:  # erst jetzt bekannt (online nachgeladen): Namen und Main/Extra stimmen dann
                scan = deck_from_history(entry.code, self.stats_db)
            self._history_result = (scan, entry, missing)
        except Exception as e:  # beschädigter Code, Datenbank gesperrt …
            self._history_result = f"Deck aus dem Verlauf nicht lesbar: {e}"

    def _show_history_result(self) -> None:
        if self._history_result is None:
            return
        result, self._history_result = self._history_result, None
        if isinstance(result, str):
            self.set_status(result, RED)
            return
        scan, entry, missing = result
        self.set_scan(scan, missing, live=False)
        note = f" · {missing} Karte(n) ohne Stats (offline?)" if missing else ""
        self.set_status(f"Aus dem Verlauf: {entry.name}{note} – „Neu scannen“ liest wieder das Deck im Spiel",
                        AMBER if missing else NEON)

    def _table_row(self, parent: tk.Frame, bg: str, texts: List[str], colors: List[str], font) -> List[tk.Label]:
        parent.grid_columnconfigure(0, weight=1)
        labels = []
        for col, (text, color) in enumerate(zip(texts, colors)):
            label = tk.Label(parent, text=text, fg=color, bg=bg, font=font, anchor="w" if col == 0 else "e",
                             padx=int(6 * self.s), pady=int(2 * self.s))
            label.grid(row=0, column=col, sticky="ew")
            if col:
                parent.grid_columnconfigure(col, minsize=self.columns[col - 1])
            labels.append(label)
        return labels

    def _place_over_card_list(self) -> None:
        """Genau über die Kartenliste rechts im Deck-Editor; ohne Spiel rechts am Bildschirm."""
        s = self.s
        frame = md_layout.md_frame()
        if frame is not None:
            area = frame.region(*md_layout.CLICK_AREAS["die Kartenliste"])
            x, y, w, h = area["left"], area["top"], area["width"], area["height"]
        else:
            w, h = int(540 * s), int(810 * s)
            x, y = self.master.winfo_screenwidth() - w - int(60 * s), int(180 * s)
        self.win.geometry(f"{w}x{h}+{x}+{y}")
        self._width = w
        self.status_label.config(wraplength=w - int(30 * s))

    # ── Zustand von außen ──
    def set_status(self, text: str, color: str = NEON) -> None:
        if not self.closed:
            self.status_label.config(text=text, fg=color)

    def set_scanning(self) -> None:
        self.scanning = True
        self._stop_watcher()
        self._set_hover(None)
        self.rescan_btn.config(state=tk.DISABLED)
        self.set_status("Deck wird gescannt …", NEON)

    def show_error(self, message: str) -> None:
        self.scanning = False
        self.rescan_btn.config(state=tk.NORMAL)
        self.set_status(f"Scan nicht möglich: {message}", RED)

    def set_scan(self, scan: DeckScan, missing_stats: int = 0, live: bool = True) -> None:
        """live=False: Deck aus dem Verlauf – kein Mouseover über dem Spiel und keine Prüfung auf Änderungen."""
        self.scanning = False
        self.offline = not live
        self._set_hover(None)
        self.rescan_btn.config(state=tk.NORMAL)
        self.analysis = DeckAnalysis(scan, self.stats_db)
        self._deck_name = None
        self._shown_reason = None
        note = f" · {missing_stats} Karte(n) ohne Stats (offline?)" if missing_stats else ""
        unsure = sum(1 for c in scan.cards if not (c.match.cid and c.match.sure))
        if unsure:
            note += f" · {unsure} Karte(n) unsicher erkannt"
        self.set_status(f"Deck gescannt{note}", GREEN if not note else AMBER)
        self.calc_vars["deck"].set(self.analysis.main_size or 40)
        self._render()
        if live:
            self._start_watcher(scan)
        else:
            self._stop_watcher()

    def try_reuse(self, scan: DeckScan) -> bool:
        """Liegt noch dasselbe Deck da wie beim letzten Scan? Dann ohne neuen Scan übernehmen."""
        if current_changes(scan):
            return False
        self.set_scan(scan)
        self.set_status("Deck unverändert – letzter Scan übernommen", GREEN)
        return True

    def change_reason(self) -> Optional[str]:
        """Hat der Wächter seit dem Scan eine Änderung am Deck bemerkt? (Grund oder None)"""
        if self.analysis is None or self.scanning:
            return "noch nicht gescannt"
        if self.offline:
            return "Deck aus dem Verlauf angezeigt"
        return self.watcher.reason if self.watcher else None

    def set_visible(self, visible: bool) -> None:
        """Ausblenden, wenn Master Duel nicht vorne ist. Unsichtbar lässt das Menü Klicks durch."""
        if self.closed or visible == self._visible:
            return
        self._visible = visible
        self.win.wm_attributes("-alpha", 1.0 if visible else 0.0)
        for panel in (self.history, self.winrate):  # eigene Fenster über diesem
            if panel is not None:
                panel.set_visible(visible)
        try:
            win_api.set_ex_style(int(self.win.wm_frame(), 16), win_api.WS_EX_TRANSPARENT, not visible)
        except (tk.TclError, ValueError, OSError):
            pass
        if not visible:
            self._set_hover(None)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._stop_watcher()
        self._close_history()
        self._close_winrate()
        if self._poll_job is not None:
            try:
                self.win.after_cancel(self._poll_job)
            except tk.TclError:
                pass
        try:
            self.list_canvas.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass
        self.hover.destroy()
        self.win.destroy()
        self.on_close()

    # ── Liste ──
    def _render(self) -> None:
        for child in self.list_inner.winfo_children():
            child.destroy()
        self._rows.clear()
        self._row_bg.clear()
        self._highlighted = None
        analysis = self.analysis
        starters, unknown = analysis.starter_copies()
        self.tiles["main"].config(text=str(analysis.main_size))
        self.tiles["extra"].config(text=str(analysis.sizes.get("Extra", 0)))
        self.tiles["starter"].config(text=str(starters), fg=GREEN if starters else TEXT)
        handtraps, _ = analysis.handtrap_copies()
        self.tiles["handtrap"].config(text=str(handtraps), fg=GREEN if handtraps else TEXT)
        text = self._odds_text("Mind. 1 Starter auf der Hand", *analysis.starter_odds())
        if unknown:
            text += f"\n({unknown} Karte(n) ohne Einstufung nicht mitgezählt)"
        self.starter_odds.config(text=text)
        self.handtrap_odds.config(text=self._odds_text("Mind. 1 Handtrap", *analysis.handtrap_odds()))
        self._show_deck_winrate()

        name_font = tkfont.Font(font=self.font)
        # Breite für den Namen: Fenster minus Ränder, Scrollleiste und die Zahlen-Spalten
        name_width = max(int(80 * self.s), self._width - sum(self.columns) - int(60 * self.s))
        for i, entry in enumerate(analysis.entries("Main")):
            self._add_row(entry, ROW_ALT if i % 2 else BG, name_font, name_width)
        extras = analysis.entries("Extra")
        if extras:
            tk.Label(self.list_inner, text=f"EXTRA DECK ({analysis.sizes.get('Extra', 0)}) – wird nicht gezogen",
                     fg=GOLD, bg=BG, font=self.font_small, anchor="w").pack(fill=tk.X, pady=(int(8 * self.s), 2))
            for i, entry in enumerate(extras):
                self._add_row(entry, ROW_ALT if i % 2 else BG, name_font, name_width)
        self.list_canvas.yview_moveto(0)

    def _odds_text(self, caption: str, first: float, second: float) -> str:
        """Mit Spalte „2. Zug“ zweizeilig, damit Starter und Handtrap nebeneinander passen."""
        if self.show_second.get():
            return f"{caption}:\n1. Zug {pct(first)}  ·  2. Zug {pct(second)}"
        return f"{caption}:  {pct(first)}"

    def _add_row(self, entry: DeckEntry, bg: str, name_font, name_width: int) -> None:
        analysis = self.analysis
        row = tk.Frame(self.list_inner, bg=bg)
        row.pack(fill=tk.X)
        name = self._fit(entry.name, name_font, name_width)
        if entry.zone == "Main":
            stats = analysis.stats(entry)
            starter = stats.starter
            starter_text = {True: "Ja", False: "Nein", None: "?"}[starter.starter]
            starter_color = {True: GREEN, False: MUTED, None: AMBER}[starter.starter]
            texts = [name, str(entry.copies), pct(stats.first)]
            colors = [TEXT, TEXT, NEON]
            if self.show_second.get():
                texts.append(pct(stats.second))
                colors.append(NEON)
            texts += [starter_text, "✎" if starter.manual else ""]
            colors += [starter_color, starter_color]
            handtrap = stats.handtrap
            texts += [{True: "Ja", False: "Nein", None: "?"}[handtrap.handtrap], "✎" if handtrap.manual else ""]
            colors += [{True: GREEN, False: MUTED, None: AMBER}[handtrap.handtrap]] * 2
        else:
            texts = [name, str(entry.copies)] + ["–"] * (len(self.columns) - 5) + [""] * 4
            colors = [MUTED] * len(texts)
        labels = self._table_row(row, bg, texts, colors, self.font)
        # Starter + ✎, Handtrap + ✎: Klick ändert die Einstufung, Rechtsklick → wieder automatisch
        for labels_of, toggle, reset in ((labels[-4:-2], self._toggle_starter, self._reset_starter),
                                         (labels[-2:], self._toggle_handtrap, self._reset_handtrap)):
            for label in labels_of:
                label.config(font=self.font_bold)
                if entry.zone == "Main" and not entry.key.startswith("?") and self.stats_db is not None:
                    label.config(cursor="hand2")
                    label.bind("<Button-1>", lambda e, en=entry, f=toggle: f(en))
                    label.bind("<Button-3>", lambda e, en=entry, f=reset: f(en))
        self._rows[entry.key] = row
        self._row_bg[entry.key] = bg

    @staticmethod
    def _fit(text: str, font: tkfont.Font, width: int) -> str:
        if font.measure(text) <= width:
            return text
        while text and font.measure(text + "…") > width:
            text = text[:-1]
        return text + "…"

    def _toggle_starter(self, entry: DeckEntry) -> None:
        current = self.analysis.starter(entry).starter
        self.analysis.set_starter(entry, not current)
        self._render()

    def _reset_starter(self, entry: DeckEntry) -> None:
        self.analysis.set_starter(entry, None)
        self._render()

    def _toggle_handtrap(self, entry: DeckEntry) -> None:
        self.analysis.set_handtrap(entry, not self.analysis.handtrap(entry).handtrap)
        self._render()

    def _reset_handtrap(self, entry: DeckEntry) -> None:
        self.analysis.set_handtrap(entry, None)
        self._render()

    def _highlight(self, key: Optional[str]) -> None:
        if self._highlighted == key:
            return
        for k, color in ((self._highlighted, None), (key, HIGHLIGHT)):
            row = self._rows.get(k) if k else None
            if row is None:
                continue
            bg = color or self._row_bg[k]
            row.config(bg=bg)
            for child in row.winfo_children():
                child.config(bg=bg)
        self._highlighted = key
        row = self._rows.get(key) if key else None
        if row is not None:
            self._scroll_into_view(row)

    def _scroll_into_view(self, row: tk.Frame) -> None:
        total = max(1, self.list_inner.winfo_height())
        top, bottom = row.winfo_y() / total, (row.winfo_y() + row.winfo_height()) / total
        view_top, view_bottom = self.list_canvas.yview()
        if top < view_top:
            self.list_canvas.yview_moveto(top)
        elif bottom > view_bottom:
            self.list_canvas.yview_moveto(bottom - (view_bottom - view_top))

    def _on_wheel(self, event) -> None:
        self.list_canvas.yview_scroll(int(-event.delta / 120) * 2, "units")

    def _update_calculator(self) -> None:
        try:
            deck, copies, hand, at_least = (int(self.calc_vars[k].get()) for k in ("deck", "copies", "hand", "min"))
        except (tk.TclError, ValueError):
            self.calc_result.config(text="–")
            return
        self.calc_result.config(text=f"→ {pct(p_at_least(deck, min(copies, deck), hand, at_least))}")

    def _rescan(self) -> None:
        if not self.scanning:
            self.on_rescan()

    # ── Maus über dem Deck ──
    def _poll(self) -> None:
        self._poll_job = None
        try:
            self._update_hover()
            self._update_change_warning()
            self._show_guide_result()
            self._show_history_result()
        finally:
            if not self.closed:
                self._poll_job = self.win.after(POLL_MS, self._poll)

    def _frame(self):
        checked_at, frame = self._frame_cache
        if time.monotonic() - checked_at > FRAME_REFRESH:
            frame = md_layout.md_frame()
            self._frame_cache = (time.monotonic(), frame)
        return frame

    def _update_hover(self) -> None:
        if (self.analysis is None or self.scanning or self.offline or not self._visible
                or not self.is_active()):
            self._cursor_slot = None
            self._set_hover(None)
            return
        frame = self._frame()
        if frame is None:
            self._set_hover(None)
            return
        slot = slot_at(frame, self.analysis.scan.zones, *win_api.get_cursor_pos())
        self._cursor_slot = slot
        now = time.monotonic()
        if slot != self._candidate:
            self._candidate, self._candidate_since = slot, now
        # Erst reagieren, wenn die Maus kurz liegen bleibt: Beim schnellen Hin- und Herfahren springt das
        # Fenster sonst ständig auf/zu bzw. umher, und das kostet im Vollbild deutlich FPS
        settled = now - self._candidate_since >= (SHOW_DELAY if slot else HIDE_DELAY)
        if slot != self._hover_slot and settled:
            self._set_hover(slot, frame)

    def _set_hover(self, slot, frame=None) -> None:
        self._hover_slot = slot
        entry = self.analysis.entry_at(*slot) if (slot and self.analysis) else None
        if entry is None:
            self.hover.hide()
            self._highlight(None)
            return
        self._highlight(entry.key)
        rect = slot_rect(frame, self.analysis.scan.zones, *slot)
        # Ohne Animation: Stats sofort (schont die FPS des Spiels)
        self.hover.show(rect, entry.name, lambda: self._hover_content(entry),
                        duration=ANALYSIS_TIME if self.animation.get() else 0)

    def _hover_content(self, entry: DeckEntry) -> HoverContent:
        stats = self.analysis.stats(entry)
        warning = "Deck geändert – bitte neu scannen" if self.watcher and self.watcher.reason else ""
        if entry.key.startswith("?"):
            return HoverContent("Karte nicht erkannt", "", [], warning=warning or "Bitte neu scannen")
        if entry.zone == "Extra":
            rows = [HoverRow("IM EXTRA DECK", f"{entry.copies}×"), HoverRow("ZIEHCHANCE", "wird nicht gezogen", MUTED)]
            return HoverContent(entry.name, type_line(stats.info), rows, warning=warning)
        rows = [HoverRow("IM DECK", f"{entry.copies}× / {stats.deck_size}"),
                HoverRow(f"1. ZUG · {HAND_FIRST} KARTEN", pct(stats.first), NEON, stats.first)]
        if self.show_second.get():
            rows.append(HoverRow(f"2. ZUG · {HAND_SECOND} KARTEN", pct(stats.second), NEON, stats.second))
        if entry.copies >= 2:
            rows.append(HoverRow("2+ KOPIEN (1. ZUG)", pct(stats.two_first), MUTED))
        starter = stats.starter
        badge = {True: ("STARTER ✓", GREEN), False: ("KEIN STARTER", MUTED), None: ("STARTER ?", AMBER)}[starter.starter]
        # Bei "kein Starter" keine Begründung (nur bei Ja bzw. unbekannt interessant)
        note = "" if starter.starter is False else ("von dir festgelegt" if starter.manual else starter.reason)
        # Handtrap nur anzeigen, wenn sie eine ist (mit Begründung)
        handtrap = stats.handtrap
        badge2, note2 = None, ""
        if handtrap.handtrap:
            badge2 = ("HANDTRAP ✓", GREEN)
            note2 = "Handtrap: " + ("von dir festgelegt" if handtrap.manual else handtrap.reason)
        return HoverContent(entry.name, type_line(stats.info), rows, badge, note, warning, badge2, note2)

    # ── Deck-Änderungen ──
    def _start_watcher(self, scan: DeckScan) -> None:
        self._stop_watcher()
        self.watcher = DeckWatcher(scan, self.tesseract_cmd, lambda: self._cursor_slot,
                                   active=lambda: (self._visible and not self.scanning and self.is_active()
                                                   and self.hover.idle_for(WATCH_AFTER_HOVER)))
        self.watcher.start()

    def _stop_watcher(self) -> None:
        if self.watcher is not None:
            self.watcher.stop()
            self.watcher = None

    def _update_change_warning(self) -> None:
        reason = self.watcher.reason if self.watcher else None
        if reason == self._shown_reason or self.scanning:
            return
        self._shown_reason = reason
        if reason:
            self.set_status(f"⚠ Deck geändert ({reason}) – bitte neu scannen", AMBER)
        else:
            self.set_status("Deck wieder wie beim Scan", GREEN)
