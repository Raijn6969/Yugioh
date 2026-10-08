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
from extras_panel import BG, GOLD, HIGHLIGHT, MUTED, PANEL, ROW_ALT, TEXT
from hover_card import AMBER, GREEN, NEON, RED
from rounded_button import RoundedButton
from starter_rules import EXTRA_FRAMES
from window_style import allow_typing, apply_frame, no_activate

DEFAULT_NAME = "Zweiter"
SEARCH_RESULTS = 8
MAIN_MIN, MAIN_MAX, EXTRA_MAX = 40, 60, 15


class SidePanel:
    def __init__(self, master: tk.Misc, db, deck_name: str, deck: Dict[str, int], zones: Dict[str, str],
                 names: Dict[str, str], on_swap: Callable[[List[str], str], None], on_close: Callable[[], None],
                 anchor: Optional[tk.Misc] = None, unknown: int = 0, memory: bool = True):
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
        self.s = s = max(1.0, master.winfo_screenheight() / 1080.0)
        self.font = ("Helvetica", int(10 * s))
        self.font_bold = ("Helvetica", int(10 * s), "bold")
        self.font_small = ("Helvetica", int(8 * s))
        self.font_small_bold = ("Helvetica", int(8 * s), "bold")
        self.font_head = ("Helvetica", int(12 * s), "bold")

        if anchor is not None:
            anchor.update_idletasks()  # vor dem Anlegen: sonst blitzt das Fenster oben links auf (siehe Winrate)
        self.win = tk.Toplevel(master, bg=BG)
        self.win.overrideredirect(True)
        self.win.wm_attributes("-topmost", True)
        self._build()
        self._place(anchor)
        apply_frame(self.win)
        no_activate(self.win)  # Klicks lassen Master Duel aktiv; Eingabefelder siehe allow_typing
        self.win.bind("<Escape>", lambda e: self.close())
        self.refresh()

    # ── Aufbau ──
    def _build(self) -> None:
        s = self.s
        header = tk.Frame(self.win, bg=PANEL)
        header.pack(fill=tk.X)
        tk.Label(header, text="◆ SIDE-PROFILE", fg=GOLD, bg=PANEL, font=self.font_head).pack(
            side=tk.LEFT, padx=(int(12 * s), int(6 * s)), pady=int(8 * s))
        tk.Label(header, text=self.deck_name, fg=MUTED, bg=PANEL, font=self.font).pack(side=tk.LEFT)
        RoundedButton(header, text="✕", command=self.close, bg="#cc0000", border="#ff8a80",
                      font=("Helvetica", int(9 * s), "bold"), padx=int(9 * s), pady=int(2 * s),
                      radius=int(6 * s)).pack(side=tk.RIGHT, padx=int(8 * s))

        body = tk.Frame(self.win, bg=BG, padx=int(12 * s), pady=int(8 * s))
        body.pack(fill=tk.BOTH, expand=True)
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
        self.save_btn = RoundedButton(name_row, text="Profil speichern", command=self.save, bg="#00796b",
                                      font=self.font_small, padx=int(9 * s), pady=int(3 * s), radius=int(6 * s))
        self.save_btn.pack(side=tk.RIGHT)
        RoundedButton(name_row, text="Leeren", command=self.clear, bg="#444444", font=self.font_small,
                      padx=int(9 * s), pady=int(3 * s), radius=int(6 * s)).pack(side=tk.RIGHT, padx=(0, int(6 * s)))
        self.feedback = tk.Label(body, text="", fg=MUTED, bg=BG, font=self.font_small, anchor="w",
                                 justify=tk.LEFT, wraplength=int(480 * s))
        self.feedback.pack(fill=tk.X)

        columns = tk.Frame(body, bg=BG)
        columns.pack(fill=tk.BOTH, expand=True, pady=(int(4 * s), 0))
        columns.columnconfigure(0, weight=1, uniform="col")
        columns.columnconfigure(1, weight=1, uniform="col")
        columns.rowconfigure(1, weight=1)
        tk.Label(columns, text="RAUS · Klick auf eine Karte deines Decks", fg=RED, bg=BG,
                 font=self.font_small_bold, anchor="w").grid(row=0, column=0, sticky="ew")
        tk.Label(columns, text="REIN · Karte suchen und anklicken", fg=GREEN, bg=BG,
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
        bar = tk.Scrollbar(frame, command=canvas.yview)
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

    def _place(self, anchor: Optional[tk.Misc]) -> None:
        """Über dem Deck-Fenster, rechtsbündig (wie Winrate/Staples)."""
        s = self.s
        w, h = int(520 * s), int(640 * s)
        screen_w, screen_h = self.master.winfo_screenwidth(), self.master.winfo_screenheight()
        if anchor is not None:
            top = anchor.winfo_toplevel()
            x = top.winfo_rootx() + top.winfo_width() - w - int(8 * s)
            y = top.winfo_rooty() + int(40 * s)
        else:
            x, y = screen_w - w - int(60 * s), screen_h - h - int(120 * s)
        x = min(max(0, x), screen_w - w)
        y = min(max(0, y), screen_h - h)
        self.win.geometry(f"{w}x{h}+{x}+{y}")

    # ── Inhalt ──
    def refresh(self) -> None:
        if self.closed:
            return
        if self.unknown:
            self.info_label.config(text=f"{self.unknown} Karte(n) im Deck nicht erkannt – erst „Neu scannen“, dann "
                                        f"lässt sich tauschen.", fg=AMBER)
        elif not self.memory:
            self.info_label.config(text="Tipp: Am zuverlässigsten mit „Speicher lesen“ (Optionen). Mit Texterkennung "
                                        "tauscht die Kontrolle am Ende nicht, wenn sie eine Karte unsicher liest – "
                                        "dann meldet sie nur, was zu ändern ist.", fg=AMBER)
        else:
            self.info_label.config(text="Tauschen ändert das Deck im Deck-Editor wie ein kleiner Import (Maus kurz "
                                        "loslassen). „Zurück“ macht es wieder rückgängig.", fg=MUTED)
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
            tk.Label(self.profiles_frame, text="Noch keins – unten anlegen, z.B. „Zweiter“: Handtraps raus, "
                                               "Board-Breaker rein.", fg=MUTED, bg=BG, font=self.font_small,
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
                RoundedButton(buttons, text="Tauschen", bg="#007acc", command=lambda p=profile: self.apply(p),
                              **small).pack(side=tk.LEFT, padx=(0, int(4 * s)))
            if ready and side_profiles.can_revert(self.deck, profile):
                RoundedButton(buttons, text="Zurück", bg="#ef6c00", command=lambda p=profile: self.revert(p),
                              **small).pack(side=tk.LEFT, padx=(0, int(4 * s)))
            RoundedButton(buttons, text="Löschen", bg="#444444", command=lambda p=profile: self.delete(p),
                          **small).pack(side=tk.LEFT)
            text = tk.Frame(row, bg=bg)
            text.pack(side=tk.LEFT, fill=tk.X, expand=True)
            title = profile.name + ("  ·  angewendet" if active else "")
            tk.Label(text, text=title, fg=NEON if active else TEXT, bg=bg, font=self.font_bold, anchor="w").pack(
                fill=tk.X)
            for label, cards, color in (("raus", profile.cards_out, RED), ("rein", profile.cards_in, GREEN)):
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
        for i, cid in enumerate(self._ordered_deck()):
            chosen = self.cards_out.get(cid, 0)
            bg = HIGHLIGHT if chosen else ROW_ALT if i % 2 else BG
            row = tk.Frame(self.out_list, bg=bg, padx=int(6 * s), pady=int(2 * s), cursor="hand2")
            row.pack(fill=tk.X)
            tk.Label(row, text=f"−{chosen}" if chosen else "", fg=RED, bg=bg, font=self.font_small_bold,
                     width=3, anchor="e").pack(side=tk.RIGHT)
            tk.Label(row, text=f"{self.deck[cid]}×", fg=MUTED, bg=bg, font=self.font_small).pack(side=tk.RIGHT)
            extra = "  (Extra)" if self.zones.get(cid) == "Extra" else ""
            tk.Label(row, text=self.names.get(cid, cid) + extra, fg=TEXT, bg=bg, font=self.font_small,
                     anchor="w").pack(side=tk.LEFT, fill=tk.X, expand=True)
            for widget in (row, *row.winfo_children()):
                widget.bind("<Button-1>", lambda e, c=cid: self.take_out(c))
                widget.bind("<Button-3>", lambda e, c=cid: self.take_out(c, -1))

    def _show_in(self) -> None:
        s = self.s
        for child in self.in_list.winfo_children():
            child.destroy()
        if not self.cards_in:
            tk.Label(self.in_list, text="–", fg=MUTED, bg=BG, font=self.font_small, anchor="w").pack(fill=tk.X)
        for cid, count in self.cards_in.items():
            row = tk.Frame(self.in_list, bg=HIGHLIGHT, padx=int(6 * s), pady=int(2 * s), cursor="hand2")
            row.pack(fill=tk.X, pady=(0, 1))
            tk.Label(row, text=f"+{count}", fg=GREEN, bg=HIGHLIGHT, font=self.font_small_bold, width=3,
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
        self._show_out()
        self._check()

    def put_in(self, cid: str, step: int = 1) -> None:
        room = side_profiles.MAX_COPIES - self.deck.get(cid, 0) + self.cards_out.get(cid, 0)
        self.cards_in[cid] = min(room, max(0, self.cards_in.get(cid, 0) + step))
        self.cards_in = side_profiles.clean(self.cards_in)
        if step > 0 and cid not in self.cards_in:
            self.feedback.config(text=f"{self.names.get(cid, cid)}: schon 3× im Deck.", fg=AMBER)
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
            return "Links Karten raus und rechts Karten rein wählen."
        too_many = [cid for cid in self.cards_in
                     if self.deck.get(cid, 0) - self.cards_out.get(cid, 0) + self.cards_in[cid] > side_profiles.MAX_COPIES]
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
        if problem:
            self.feedback.config(text=problem, fg=MUTED if not (self.cards_out or self.cards_in) else AMBER)
        else:
            sizes = self._sizes()
            self.feedback.config(text=f"Danach: Main {sizes['Main']}, Extra {sizes['Extra']} Karten", fg=GREEN)
        self.save_btn.config(bg="#00796b" if problem is None else "#444444")

    def save(self) -> None:
        name = self.name_var.get().strip()
        problem = self._problem() or (None if name else "Bitte einen Namen eingeben.")
        if problem:
            self.feedback.config(text=problem, fg=AMBER)
            return
        self.db.save_side_profile(SideProfile(self.deck_name, name, dict(self.cards_out), dict(self.cards_in)))
        self.clear()
        self.feedback.config(text=f"„{name}“ gespeichert.", fg=GREEN)
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

    # ── Zustand von außen ──
    def set_visible(self, visible: bool) -> None:
        if not self.closed:
            self.win.wm_attributes("-alpha", 1.0 if visible else 0.0)

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
