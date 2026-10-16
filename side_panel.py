"""
Side-Profile (Button "Side" im Deck-Fenster): Karten für ein Match tauschen, z.B. als Zweiter Handtraps raus und
Board-Breaker rein. Oben die Profile des angezeigten Decks mit "Tauschen" bzw. "Zurück", darunter ein neues Profil:
links Karten aus dem Deck anklicken (raus), rechts Karten suchen und anklicken (rein). Rechtsklick nimmt eine Kopie
wieder weg. Getauscht wird im Deck-Editor wie bei einem kleinen Import (side_profiles).
"""

import tkinter as tk
from typing import Callable, Dict, List, Optional

import side_profiles
from card_stats import SideProfile
from dark_scrollbar import DarkScrollbar
from rounded_button import RoundedButton
from starter_rules import EXTRA_FRAMES
from window_style import (SIZE_SMALL, SIZE_TEXT, SIZE_TITLE, font, BG, PANEL, TEXT, MUTED, GOLD, INFO, GOOD, WARN, BAD,
                          ROW_ALT, HIGHLIGHT, NEUTRAL, allow_typing)

DEFAULT_NAME = "Zweiter"
SEARCH_RESULTS = 8
MAIN_MIN, MAIN_MAX, EXTRA_MAX = 40, 60, 15


class SidePanel:
    def __init__(self, master: tk.Misc, db, deck_name: str, deck: Dict[str, int], zones: Dict[str, str],
                 names: Dict[str, str], on_swap: Callable[[List[str], str], None], on_close: Callable[[], None],
                 unknown: int = 0, memory: bool = True):
        self.master = master
        self.db = db
        self.deck_name = deck_name
        self.deck = dict(deck)            # {Passcode: Kopien} des angezeigten Decks (Main + Extra)
        self.zones = dict(zones)          # {Passcode: "Main"/"Extra"}
        self.names = dict(names)          # {Passcode: Name}
        self.on_swap = on_swap            # (Ziel-Deck, Name des Tauschs)
        self.on_close = on_close
        self.unknown = unknown            # nicht erkannte Karten im Deck → kein Tausch möglich
        self.memory = memory              # Lesemethode "Speicher" (sonst Texterkennung: Tausch weniger sicher)
        self.closed = False
        self.cards_out: Dict[str, int] = {}
        self.cards_in: Dict[str, int] = {}
        self._out_rows: Dict[str, tuple] = {}  # Passcode -> (Zeile, Minus-Anzeige, Grundfarbe)
        self.s = s = max(1.0, master.winfo_screenheight() / 1080.0)
        self.font = font(SIZE_TEXT, s)
        self.font_bold = font(SIZE_TEXT, s, bold=True)
        self.font_small = font(SIZE_SMALL, s)
        self.font_small_bold = font(SIZE_SMALL, s, bold=True)
        self.font_head = font(SIZE_TITLE, s, bold=True)

        self.win = tk.Frame(master, bg=BG)  # Ansicht im Deck-Fenster (extras_panel packt sie)
        self._build()
        self.refresh()

    # ── Aufbau ──
    def _build(self) -> None:
        s = self.s
        body = tk.Frame(self.win, bg=BG, padx=int(12 * s), pady=int(8 * s))
        body.pack(fill=tk.BOTH, expand=True)
        if self.deck_name:  # Side gehört zu einem Deck
            tk.Label(body, text=self.deck_name, fg=TEXT, bg=BG, font=self.font_bold, anchor="w", justify=tk.LEFT,
                     wraplength=int(480 * s)).pack(fill=tk.X)
        self.info_label = tk.Label(body, text="", fg=MUTED, bg=BG, font=self.font_small, anchor="w",
                                   justify=tk.LEFT, wraplength=int(480 * s))
        self.info_label.pack(fill=tk.X)
        self._section(body, "DEINE PROFILE")
        self.profiles_frame = tk.Frame(body, bg=BG)
        self.profiles_frame.pack(fill=tk.X)

        self._section(body, "NEUES PROFIL")
        name_row = tk.Frame(body, bg=BG)
        name_row.pack(fill=tk.X, pady=(0, int(4 * s)))
        tk.Label(name_row, text="Name", fg=MUTED, bg=BG, font=self.font_small).pack(side=tk.LEFT)
        self.name_var = tk.StringVar(master=self.win, value=DEFAULT_NAME)
        self.name_entry = tk.Entry(name_row, textvariable=self.name_var, bg=PANEL, fg=TEXT, insertbackground=TEXT,
                                   relief=tk.FLAT, font=self.font, width=18)
        self.name_entry.pack(side=tk.LEFT, padx=(int(6 * s), 0), ipady=int(2 * s))
        allow_typing(self.name_entry)
        self.save_btn = RoundedButton(name_row, text="Profil speichern", command=self.save, role="primary",
                                      font=self.font_small, padx=int(9 * s), pady=int(3 * s), radius=int(6 * s))
        self.save_btn.pack(side=tk.RIGHT)
        RoundedButton(name_row, text="Leeren", command=self.clear, bg=NEUTRAL, font=self.font_small,
                      padx=int(9 * s), pady=int(3 * s), radius=int(6 * s)).pack(side=tk.RIGHT, padx=(0, int(6 * s)))
        self.feedback = tk.Label(body, text="", fg=MUTED, bg=BG, font=self.font_small, anchor="w",
                                 justify=tk.LEFT, wraplength=int(480 * s))
        self.feedback.pack(fill=tk.X)

        columns = tk.Frame(body, bg=BG)
        columns.pack(fill=tk.BOTH, expand=True, pady=(int(4 * s), 0))
        columns.columnconfigure(0, weight=1, uniform="col")
        columns.columnconfigure(1, weight=1, uniform="col")
        columns.rowconfigure(1, weight=1)
        tk.Label(columns, text="RAUS · Klick auf eine Karte deines Decks", fg=BAD, bg=BG,
                 font=self.font_small_bold, anchor="w").grid(row=0, column=0, sticky="ew")
        tk.Label(columns, text="REIN · Karte suchen und anklicken", fg=GOOD, bg=BG,
                 font=self.font_small_bold, anchor="w").grid(row=0, column=1, sticky="ew", padx=(int(8 * s), 0))
        self.out_list = self._scroll_list(columns, row=1, column=0)
        right = tk.Frame(columns, bg=BG)
        right.grid(row=1, column=1, sticky="nsew", padx=(int(8 * s), 0))
        self.search_var = tk.StringVar(master=self.win)
        self.search_entry = tk.Entry(right, textvariable=self.search_var, bg=PANEL, fg=TEXT, insertbackground=TEXT,
                                     relief=tk.FLAT, font=self.font)
        self.search_entry.pack(fill=tk.X, ipady=int(2 * s))
        allow_typing(self.search_entry)
        self.search_var.trace_add("write", lambda *a: self._show_results())
        self.results = tk.Frame(right, bg=BG)
        self.results.pack(fill=tk.X, pady=(int(2 * s), int(4 * s)))
        tk.Label(right, text="Kommt rein (Rechtsklick: eine weniger)", fg=MUTED, bg=BG, font=self.font_small,
                 anchor="w").pack(fill=tk.X)
        self.in_list = tk.Frame(right, bg=BG)
        self.in_list.pack(fill=tk.BOTH, expand=True)

    def _section(self, parent: tk.Misc, title: str) -> None:
        tk.Label(parent, text=title, fg=GOLD, bg=BG, font=self.font_small_bold, anchor="w").pack(
            fill=tk.X, pady=(int(8 * self.s), int(2 * self.s)))

    def _scroll_list(self, parent: tk.Misc, row: int, column: int) -> tk.Frame:
        frame = tk.Frame(parent, bg=BG)
        frame.grid(row=row, column=column, sticky="nsew")
        canvas = tk.Canvas(frame, bg=BG, highlightthickness=0, bd=0)
        bar = DarkScrollbar(frame, command=canvas.yview, bg=BG, scale=self.s)
        canvas.config(yscrollcommand=bar.set)
        bar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        inner = tk.Frame(canvas, bg=BG)
        item = canvas.create_window(0, 0, window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.config(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(item, width=e.width))
        wheel = lambda e: canvas.yview_scroll(int(-e.delta / 120) * 2, "units")  # noqa: E731
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", wheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))
        self._out_canvas = canvas
        return inner

    # ── Inhalt ──
    def refresh(self) -> None:
        if self.closed:
            return
        if self.unknown:
            self.info_label.config(text=f"{self.unknown} Karte(n) im Deck nicht erkannt – erst „Neu scannen“, dann "
                                        f"lässt sich tauschen.", fg=WARN)
        elif not self.memory:
            self.info_label.config(text="Tipp: Mit „Speicher lesen“ (Optionen) tauscht die Kontrolle am Ende "
                                        "zuverlässiger.", fg=WARN)
        else:
            self.info_label.config(text="Tauschen ändert das Deck wie ein kleiner Import, „Zurück“ macht es "
                                        "rückgängig.", fg=MUTED)
        self._show_profiles()
        self._show_out()
        self._show_in()
        self._show_results()
        self._check()

    def _profiles(self) -> List[SideProfile]:
        """Profile dieses Decks und jedes, das gerade auf dieses Deck angewendet ist (ein Tausch kann den
        Decknamen ändern, z.B. viele Karten eines anderen Archetyps – „Zurück“ muss trotzdem erreichbar sein)."""
        try:
            return [p for p in self.db.side_profiles(None)
                    if p.deck_name == self.deck_name or side_profiles.is_active(self.deck, p)]
        except Exception:  # Datenbank gerade gesperrt
            return []

    def _show_profiles(self) -> None:
        s = self.s
        for child in self.profiles_frame.winfo_children():
            child.destroy()
        profiles = self._profiles()
        self._learn_names([cid for p in profiles for cid in (*p.cards_out, *p.cards_in)])
        if not profiles:
            tk.Label(self.profiles_frame, text="Noch keins – unten anlegen.", fg=MUTED, bg=BG, font=self.font_small,
                     anchor="w").pack(fill=tk.X)
            return
        for i, profile in enumerate(profiles):
            bg = ROW_ALT if i % 2 else PANEL
            active = side_profiles.is_active(self.deck, profile)
            row = tk.Frame(self.profiles_frame, bg=HIGHLIGHT if active else bg, padx=int(8 * s), pady=int(4 * s))
            row.pack(fill=tk.X, pady=(0, int(2 * s)))
            bg = row.cget("bg")
            buttons = tk.Frame(row, bg=bg)
            buttons.pack(side=tk.RIGHT)
            small = dict(font=self.font_small, padx=int(8 * s), pady=int(3 * s), radius=int(6 * s))
            ready = not self.unknown
            if ready and side_profiles.can_apply(self.deck, profile):
                RoundedButton(buttons, text="Tauschen", role="primary", command=lambda p=profile: self.apply(p),
                              **small).pack(side=tk.LEFT, padx=(0, int(4 * s)))
            if ready and side_profiles.can_revert(self.deck, profile):
                RoundedButton(buttons, text="Zurück", role="neutral", command=lambda p=profile: self.revert(p),
                              **small).pack(side=tk.LEFT, padx=(0, int(4 * s)))
            RoundedButton(buttons, text="Löschen", role="danger", command=lambda p=profile: self.delete(p),
                          **small).pack(side=tk.LEFT)
            text = tk.Frame(row, bg=bg)
            text.pack(side=tk.LEFT, fill=tk.X, expand=True)
            title = profile.name + ("  ·  angewendet" if active else "")
            tk.Label(text, text=title, fg=INFO if active else TEXT, bg=bg, font=self.font_bold, anchor="w").pack(
                fill=tk.X)
            for label, cards, color in (("raus", profile.cards_out, BAD), ("rein", profile.cards_in, GOOD)):
                if cards:
                    tk.Label(text, text=f"{label}: {side_profiles.describe(cards, self.names)}", fg=color, bg=bg,
                             font=self.font_small, anchor="w", justify=tk.LEFT,
                             wraplength=int(300 * s)).pack(fill=tk.X)

    def _ordered_deck(self) -> List[str]:
        return sorted(self.deck, key=lambda cid: (self.zones.get(cid) == "Extra", self.names.get(cid, cid)))

    def _show_out(self) -> None:
        s = self.s
        for child in self.out_list.winfo_children():
            child.destroy()
        self._out_rows = {}
        for i, cid in enumerate(self._ordered_deck()):
            chosen = self.cards_out.get(cid, 0)
            bg = HIGHLIGHT if chosen else ROW_ALT if i % 2 else BG
            row = tk.Frame(self.out_list, bg=bg, padx=int(6 * s), pady=int(2 * s), cursor="hand2")
            row.pack(fill=tk.X)
            minus = tk.Label(row, text=f"−{chosen}" if chosen else "", fg=BAD, bg=bg, font=self.font_small_bold,
                             width=3, anchor="e")
            minus.pack(side=tk.RIGHT)
            self._out_rows[cid] = (row, minus, ROW_ALT if i % 2 else BG)
            tk.Label(row, text=f"{self.deck[cid]}×", fg=MUTED, bg=bg, font=self.font_small).pack(side=tk.RIGHT)
            extra = "  (Extra)" if self.zones.get(cid) == "Extra" else ""
            tk.Label(row, text=self.names.get(cid, cid) + extra, fg=TEXT, bg=bg, font=self.font_small,
                     anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)
            for widget in (row, *row.winfo_children()):
                widget.bind("<Button-1>", lambda e, c=cid: self.take_out(c))
                widget.bind("<Button-3>", lambda e, c=cid: self.take_out(c, -1))

    def _paint_out(self, cid: str) -> None:
        """Nur die angeklickte Zeile umfärben (die Liste ganz neu zu bauen dauert bei 60 Karten über 100 ms)."""
        if cid not in self._out_rows:
            return
        row, minus, base = self._out_rows[cid]
        chosen = self.cards_out.get(cid, 0)
        bg = HIGHLIGHT if chosen else base
        for widget in (row, *row.winfo_children()):
            widget.config(bg=bg)
        minus.config(text=f"−{chosen}" if chosen else "")

    def _show_in(self) -> None:
        s = self.s
        for child in self.in_list.winfo_children():
            child.destroy()
        if not self.cards_in:
            tk.Label(self.in_list, text="–", fg=MUTED, bg=BG, font=self.font_small, anchor="w").pack(fill=tk.X)
        for cid, count in self.cards_in.items():
            row = tk.Frame(self.in_list, bg=HIGHLIGHT, padx=int(6 * s), pady=int(2 * s), cursor="hand2")
            row.pack(fill=tk.X, pady=(0, 1))
            tk.Label(row, text=f"+{count}", fg=GOOD, bg=HIGHLIGHT, font=self.font_small_bold, width=3,
                     anchor="w").pack(side=tk.LEFT)
            tk.Label(row, text=self.names.get(cid, cid), fg=TEXT, bg=HIGHLIGHT, font=self.font_small,
                     anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)
            for widget in (row, *row.winfo_children()):
                widget.bind("<Button-1>", lambda e, c=cid: self.put_in(c))
                widget.bind("<Button-3>", lambda e, c=cid: self.put_in(c, -1))

    def _show_results(self) -> None:
        if self.closed:
            return
        s = self.s
        for child in self.results.winfo_children():
            child.destroy()
        text = self.search_var.get()
        try:
            found = self.db.search_cards(text, SEARCH_RESULTS) if len(text.strip()) >= 2 else []
        except Exception:
            found = []
        for i, (cid, name) in enumerate(found):
            self.names.setdefault(cid, name)
            bg = ROW_ALT if i % 2 else BG
            label = tk.Label(self.results, text=name, fg=TEXT, bg=bg, font=self.font_small, anchor="w",
                             padx=int(6 * s), pady=int(1 * s), cursor="hand2")
            label.pack(fill=tk.X)
            label.bind("<Button-1>", lambda e, c=cid: self.put_in(c))
        if len(text.strip()) >= 2 and not found:
            tk.Label(self.results, text="Keine Karte gefunden", fg=MUTED, bg=BG, font=self.font_small,
                     anchor="w").pack(fill=tk.X)

    # ── Bearbeiten ──
    def take_out(self, cid: str, step: int = 1) -> None:
        self.cards_out[cid] = min(self.deck.get(cid, 0), max(0, self.cards_out.get(cid, 0) + step))
        self.cards_out = side_profiles.clean(self.cards_out)
        self._paint_out(cid)
        self._check()

    def put_in(self, cid: str, step: int = 1) -> None:
        room = side_profiles.MAX_COPIES - self.deck.get(cid, 0) + self.cards_out.get(cid, 0)
        self.cards_in[cid] = min(room, max(0, self.cards_in.get(cid, 0) + step))
        self.cards_in = side_profiles.clean(self.cards_in)
        if step > 0 and cid not in self.cards_in:
            self.feedback.config(text=f"{self.names.get(cid, cid)}: schon 3× im Deck.", fg=WARN)
            return
        self._show_in()
        self._check()

    def clear(self) -> None:
        self.cards_out, self.cards_in = {}, {}
        self._show_out()
        self._show_in()
        self._check()

    def _zone(self, cid: str) -> str:
        if cid in self.zones:
            return self.zones[cid]
        info = self.db.info(cid)
        self.zones[cid] = "Extra" if info and (info.frame or "").lower().startswith(EXTRA_FRAMES) else "Main"
        return self.zones[cid]

    def _sizes(self) -> Dict[str, int]:
        """Kartenzahl von Main und Extra nach dem Tausch."""
        sizes = {"Main": 0, "Extra": 0}
        for cid in side_profiles.swap(self.deck, self.cards_out, self.cards_in):
            sizes[self._zone(cid)] += 1
        return sizes

    def _problem(self) -> Optional[str]:
        if not self.cards_out and not self.cards_in:
            return "Nichts gewählt."
        too_many = [cid for cid in self.cards_in
                    if self.deck.get(cid, 0) - self.cards_out.get(cid, 0) + self.cards_in[cid]
                    > side_profiles.MAX_COPIES]
        if too_many:
            return f"{self.names.get(too_many[0], too_many[0])} wäre danach mehr als 3× im Deck."
        sizes = self._sizes()
        if not MAIN_MIN <= sizes["Main"] <= MAIN_MAX:
            return f"Main Deck hätte danach {sizes['Main']} Karten (erlaubt {MAIN_MIN}–{MAIN_MAX})."
        if sizes["Extra"] > EXTRA_MAX:
            return f"Extra Deck hätte danach {sizes['Extra']} Karten (höchstens {EXTRA_MAX})."
        return None

    def _check(self) -> None:
        problem = self._problem()
        if not (self.cards_out or self.cards_in):  # Spaltenköpfe sagen schon, was zu tun ist
            self.feedback.config(text="")
        elif problem:
            self.feedback.config(text=problem, fg=WARN)
        else:
            sizes = self._sizes()
            self.feedback.config(text=f"Danach: Main {sizes['Main']}, Extra {sizes['Extra']} Karten", fg=GOOD)
        self.save_btn.config(role="primary" if problem is None else "neutral")

    def save(self) -> None:
        name = self.name_var.get().strip()
        problem = self._problem() or (None if name else "Bitte einen Namen eingeben.")
        if problem:
            self.feedback.config(text=problem, fg=WARN)
            return
        self.db.save_side_profile(SideProfile(self.deck_name, name, dict(self.cards_out), dict(self.cards_in)))
        self.clear()
        self.feedback.config(text=f"„{name}“ gespeichert.", fg=GOOD)
        self._show_profiles()

    def delete(self, profile: SideProfile) -> None:
        self.db.delete_side_profile(profile.deck_name, profile.name)
        self._show_profiles()

    def apply(self, profile: SideProfile) -> None:
        self._swap(side_profiles.swap(self.deck, profile.cards_out, profile.cards_in), profile.name)

    def revert(self, profile: SideProfile) -> None:
        self._swap(side_profiles.swap(self.deck, profile.cards_in, profile.cards_out), f"{profile.name} zurück")

    def _swap(self, target: List[str], label: str) -> None:
        self.close()  # der Tausch klickt im Deck-Editor; das Deck-Fenster schließt sich ohnehin
        self.on_swap(target, label)

    def _learn_names(self, ids: List[str]) -> None:
        todo = [cid for cid in ids if cid not in self.names]
        if todo:
            try:
                self.names.update({cid: info.name for cid, info in self.db.infos(todo).items()})
            except Exception:
                pass

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self._out_canvas.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass
        self.win.destroy()
        self.on_close()
