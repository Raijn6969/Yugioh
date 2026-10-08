"""
Ash-Prio (Button "Ash-Prio" im Deck-Fenster): Spickzettel je Starthand – an welcher Stelle der Combo eine Handtrap
am meisten schadet und was danach noch steht (aus der Factory, siehe ash_prio). Oben die Handtrap wählen (Ash,
Imperm, …), Klick auf eine Hand zeigt die ganze Linie mit der gefährlichen Stelle und wie man danach weiterspielt.
Weiß man, wo Ash trifft, kann man vorher einen anderen Effekt anbieten oder genau diesen Schritt schützen.
"""

import threading
import tkinter as tk
from typing import Callable, Dict, List, Optional

import ash_prio
from extras_panel import BG, GOLD, HIGHLIGHT, MUTED, PANEL, ROW_ALT, TEXT
from hover_card import AMBER, GREEN, NEON, RED
from rounded_button import RoundedButton
from window_style import apply_frame, no_activate

POLL_MS = 100


class AshPrioPanel:
    def __init__(self, master: tk.Misc, deck_name: str, deck: Dict[str, int], on_close: Callable[[], None],
                 anchor: Optional[tk.Misc] = None, path: str = ash_prio.FACTORY_DB,
                 names: Optional[Dict[str, str]] = None):
        self.master = master
        self.deck_name = deck_name
        self.deck = dict(deck)
        self.names = dict(names or {})         # {Passcode: Name}: Alt-Arts der Factory-Variante zuordnen
        self.on_close = on_close
        self.path = path
        self.closed = False
        self.handtrap = "ash"
        self.open_hand: Optional[str] = None   # aufgeklappte Hand
        self.variant: Optional[ash_prio.Variant] = None
        self.sheets: List[ash_prio.HandSheet] = []
        self._result = None                    # vom Hintergrund-Thread
        self._poll_job = None
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
        no_activate(self.win)
        self.win.bind("<Escape>", lambda e: self.close())
        self.info_label.config(text="Lese die Ergebnisse der Factory…", fg=MUTED)
        threading.Thread(target=self._load, daemon=True, name="ash-prio").start()
        self._poll()

    # ── Aufbau ──
    def _build(self) -> None:
        s = self.s
        header = tk.Frame(self.win, bg=PANEL)
        header.pack(fill=tk.X)
        tk.Label(header, text="◆ ASH-PRIO", fg=GOLD, bg=PANEL, font=self.font_head).pack(
            side=tk.LEFT, padx=(int(12 * s), int(6 * s)), pady=int(8 * s))
        tk.Label(header, text="wo Handtraps am meisten schaden", fg=MUTED, bg=PANEL, font=self.font).pack(
            side=tk.LEFT)
        RoundedButton(header, text="✕", command=self.close, bg="#cc0000", border="#ff8a80",
                      font=("Helvetica", int(9 * s), "bold"), padx=int(9 * s), pady=int(2 * s),
                      radius=int(6 * s)).pack(side=tk.RIGHT, padx=int(8 * s))

        body = tk.Frame(self.win, bg=BG, padx=int(12 * s), pady=int(8 * s))
        body.pack(fill=tk.BOTH, expand=True)
        self.info_label = tk.Label(body, text="", fg=MUTED, bg=BG, font=self.font_small, anchor="w",
                                   justify=tk.LEFT, wraplength=int(480 * s))
        self.info_label.pack(fill=tk.X)
        bar = tk.Frame(body, bg=BG)
        bar.pack(fill=tk.X, pady=(int(6 * s), int(4 * s)))
        self.buttons = {}
        for key, name in ash_prio.HANDTRAPS:
            button = RoundedButton(bar, text=name, command=lambda k=key: self.choose(k), font=self.font_small,
                                   padx=int(8 * s), pady=int(3 * s), radius=int(6 * s))
            button.pack(side=tk.LEFT, padx=(0, int(4 * s)))
            self.buttons[key] = button

        list_frame = tk.Frame(body, bg=BG)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(int(4 * s), 0))
        self.canvas = tk.Canvas(list_frame, bg=BG, highlightthickness=0, bd=0)
        scroll = tk.Scrollbar(list_frame, command=self.canvas.yview)
        self.canvas.config(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self._inner_id = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.config(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._inner_id, width=e.width))
        wheel = lambda e: self.canvas.yview_scroll(int(-e.delta / 120) * 2, "units")  # noqa: E731
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))

    def _place(self, anchor: Optional[tk.Misc]) -> None:
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

    # ── Laden (Factory-Datenbank, im Hintergrund) ──
    def _load(self) -> None:
        self._result = ash_prio.load(self.deck, self.path, self.names)

    def _poll(self) -> None:
        if self.closed:
            return
        if self._result is None:
            self._poll_job = self.win.after(POLL_MS, self._poll)
            return
        self.variant, self.sheets, hint = self._result
        if self.variant is None:
            self.info_label.config(text=hint, fg=AMBER)
        else:
            self.info_label.config(
                text=f"Aus der Factory: „{self.variant.name}“ ({self.variant.overlap * 100:.0f} % gleiche Karten), "
                     f"{len(self.sheets)} Starthände, die stärksten zuerst. Board = Unterbrechungen am Ende des "
                     f"Zuges. Klick auf eine Hand zeigt die ganze Linie.", fg=MUTED)
        self.render()

    # ── Anzeige ──
    def choose(self, key: str) -> None:
        self.handtrap = key
        self.render()

    def toggle(self, hand: str) -> None:
        self.open_hand = None if self.open_hand == hand else hand
        self.render()

    def render(self) -> None:
        if self.closed:
            return
        for key, button in self.buttons.items():
            button.config(bg="#007acc" if key == self.handtrap else "#444444")
        for child in self.inner.winfo_children():
            child.destroy()
        for i, sheet in enumerate(self.sheets):
            self._hand_row(sheet, ROW_ALT if i % 2 else BG)
            if sheet.hand == self.open_hand:
                self._line(sheet)

    def _hand_row(self, sheet: ash_prio.HandSheet, bg: str) -> None:
        s = self.s
        bg = HIGHLIGHT if sheet.hand == self.open_hand else bg
        row = tk.Frame(self.inner, bg=bg, padx=int(8 * s), pady=int(4 * s), cursor="hand2")
        row.pack(fill=tk.X)
        threat = sheet.threats.get(self.handtrap)
        after = threat.after if threat is not None and threat.step is not None else sheet.score
        loss = sheet.score - after
        color = GREEN if loss < 1 else AMBER if after >= 2 else RED
        head = tk.Frame(row, bg=bg)
        head.pack(fill=tk.X)
        tk.Label(head, text=f"Board {ash_prio.number(sheet.score)} → {ash_prio.number(after)}", fg=color, bg=bg,
                 font=self.font_small_bold).pack(side=tk.RIGHT)
        tk.Label(head, text=sheet.hand, fg=TEXT, bg=bg, font=self.font_bold, anchor="w", justify=tk.LEFT,
                 wraplength=int(340 * s)).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(row, text=ash_prio.advice(sheet, self.handtrap), fg=MUTED, bg=bg, font=self.font_small,
                 anchor="w", justify=tk.LEFT, wraplength=int(440 * s)).pack(fill=tk.X)
        for widget in (row, head, *row.winfo_children(), *head.winfo_children()):
            widget.bind("<Button-1>", lambda e, h=sheet.hand: self.toggle(h))

    def _line(self, sheet: ash_prio.HandSheet) -> None:
        """Die ganze Linie; die Stelle der gewählten Handtrap markiert, danach die beste Fortsetzung."""
        s = self.s
        box = tk.Frame(self.inner, bg=PANEL, padx=int(10 * s), pady=int(6 * s))
        box.pack(fill=tk.X, pady=(0, int(4 * s)))
        threat = sheet.threats.get(self.handtrap)
        hit = threat.step if threat is not None else None
        for index, step in enumerate(sheet.steps):
            marked = index == hit
            text = f"{index + 1}. {ash_prio.short_step(step)}" + (f"   ◀ {threat.name}" if marked else "")
            tk.Label(box, text=text, fg=RED if marked else TEXT, bg=PANEL,
                     font=self.font_small_bold if marked else self.font_small, anchor="w", justify=tk.LEFT,
                     wraplength=int(420 * s)).pack(fill=tk.X)
        if threat is not None and threat.step is not None and threat.continuation:
            tk.Label(box, text=f"Nach {threat.name} am besten weiter:", fg=NEON, bg=PANEL,
                     font=self.font_small_bold, anchor="w").pack(fill=tk.X, pady=(int(6 * s), 0))
            for step in threat.continuation:
                tk.Label(box, text=f"• {ash_prio.short_step(step)}", fg=TEXT, bg=PANEL, font=self.font_small,
                         anchor="w", justify=tk.LEFT, wraplength=int(420 * s)).pack(fill=tk.X)

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
        try:
            self.canvas.unbind_all("<MouseWheel>")
        except tk.TclError:
            pass
        self.win.destroy()
        self.on_close()
