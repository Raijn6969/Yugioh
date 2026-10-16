"""
Ash-Prio (Button "Ash-Prio" im Deck-Fenster): Spickzettel je Starthand – an welcher Stelle der Combo eine Handtrap
am meisten schadet und was danach noch steht (aus der Factory, siehe ash_prio). Oben die Handtrap wählen (Ash,
Imperm, …), Klick auf eine Hand zeigt die ganze Linie mit der gefährlichen Stelle und wie man danach weiterspielt.
Weiß man, wo Ash trifft, kann man vorher einen anderen Effekt anbieten oder genau diesen Schritt schützen.

Gibt es für das Deck keine Starthände (keine Factory und nicht im mitgelieferten Schnappschuss), zeigt das Fenster
eine Einschätzung aus den Kartentexten (handtrap_targets): welche Karten des Decks die gewählte Handtrap treffen
kann und mit welchem Effekt, Starter zuerst – ohne die Reihenfolge der Combo.

Oben die Ash-Prio auf einen Blick als Kartenbilder: die Karten, die die gewählte Handtrap am häufigsten trifft (in
wie vielen Starthänden, Ø Schaden am Board). Klick auf eine Karte zeigt darunter nur die Hände, in denen sie es ist.
Die Bildleiste wird auf eine Zeichenfläche gemalt (wie card_grid); fehlende Bilder werden einmal online geladen.
"""

import threading
import tkinter as tk
from typing import Callable, Dict, List, Optional

import ash_prio
import card_images
import handtrap_targets
from dark_scrollbar import DarkScrollbar, bind_wheel
from debug_log import dlog
from rounded_button import RoundedButton
from window_style import (SIZE_SMALL, SIZE_TEXT, SIZE_TITLE, font, BG, PANEL, TEXT, MUTED, GOLD, INFO, GOOD, WARN, BAD,
                          ROW_ALT, HIGHLIGHT, show_tip)

POLL_MS = 100
CARD_SIZE = (58, 85)   # Bildleiste
COLUMNS = 7
MAX_CARDS = 14         # zwei Reihen – die Liste darunter braucht auch noch Platz


class AshPrioPanel:
    def __init__(self, master: tk.Misc, deck_name: str, deck: Dict[str, int], on_close: Callable[[], None],
                 path: str = ash_prio.FACTORY_DB, names: Optional[Dict[str, str]] = None, db=None,
                 image_folder: str = card_images.FOLDER):
        self.master = master
        self.deck_name = deck_name
        self.deck = dict(deck)
        self.names = dict(names or {})         # {Passcode: Name}: Alt-Arts der Factory-Variante zuordnen
        self.db = db                           # CardStatsDB: Kartentexte für die Einschätzung ohne Factory
        self._tip: Optional[tk.Toplevel] = None  # Tooltip an der Board-Angabe
        self.text_rows: List[tuple] = []       # ohne Starthände: (Passcode, Name, Kopien, Starter?, {Handtrap: Effekt})
        self.on_close = on_close
        self.path = path
        self.closed = False
        self.handtrap = "ash"
        self.open_hand: Optional[str] = None   # aufgeklappte Hand
        self._hand_rows: Dict[str, tuple] = {}  # Hand -> (Zeile, Grundfarbe, Starthand) der angezeigten Liste
        self._line_box: Optional[tk.Frame] = None  # Linie der aufgeklappten Hand
        self.variant: Optional[ash_prio.Variant] = None
        self.sheets: List[ash_prio.HandSheet] = []
        self.focus: Optional[str] = None       # angeklickte Karte der Bildleiste (filtert die Liste)
        self.card_ids: Dict[str, str] = {}     # {Kartenname: Passcode} für die Bilder
        self._result = None                    # vom Hintergrund-Thread
        self._shown = False
        self._images_ready = False             # fehlende Bilder sind inzwischen geladen
        self._poll_job = None
        self.s = s = max(1.0, master.winfo_screenheight() / 1080.0)
        self.image_folder = image_folder
        self.photos = card_images.PhotoCache(master, (int(CARD_SIZE[0] * s), int(CARD_SIZE[1] * s)), image_folder)
        self.font = font(SIZE_TEXT, s)
        self.font_bold = font(SIZE_TEXT, s, bold=True)
        self.font_small = font(SIZE_SMALL, s)
        self.font_small_bold = font(SIZE_SMALL, s, bold=True)
        self.font_head = font(SIZE_TITLE, s, bold=True)

        self.win = tk.Frame(master, bg=BG)  # Ansicht im Deck-Fenster (extras_panel packt sie)
        self._build()
        self.info_label.config(text="Lese die Ergebnisse der Factory…", fg=MUTED)
        self._thread = threading.Thread(target=self._load, daemon=True, name="ash-prio")
        self._thread.start()
        self._poll()

    # ── Aufbau ──
    def _build(self) -> None:
        s = self.s
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
        self.strip = tk.Canvas(body, bg=BG, highlightthickness=0, bd=0, height=1)
        self.strip.pack(fill=tk.X)

        list_frame = tk.Frame(body, bg=BG)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(int(4 * s), 0))
        self.canvas = tk.Canvas(list_frame, bg=BG, highlightthickness=0, bd=0)
        scroll = DarkScrollbar(list_frame, command=self.canvas.yview, bg=BG, scale=self.s)
        self.canvas.config(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.inner = tk.Frame(self.canvas, bg=BG)
        self._inner_id = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.config(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._inner_id, width=e.width))
        bind_wheel(self.canvas)

    # ── Laden (Factory-Datenbank, im Hintergrund) ──
    def _load(self) -> None:
        try:
            result = ash_prio.load(self.deck, self.path, self.names)
            if result[0] is None:
                self.text_rows = self._text_rows()
                passcodes = [row[0] for row in self.text_rows if row[4]]
            else:
                wanted = {t.name for key, _ in ash_prio.HANDTRAPS
                          for t in ash_prio.targets(result[1], key, self.names.values())[:MAX_CARDS]}
                self.card_ids = self._card_ids(wanted)
                passcodes = [self.card_ids[name] for name in wanted if name in self.card_ids]
            self.photos.prepare(passcodes)  # verkleinern im Hintergrund, dann zeigt das Fenster sie sofort
            self._result = result
            missing = self.photos.missing(passcodes)
            if missing:  # einmal online laden, dann nachträglich zeigen
                card_images.download(missing, folder=self.image_folder)
                self.photos.prepare(missing)
                self._images_ready = True
        except Exception as e:  # unerwartete Daten (z.B. alter Schnappschuss): sonst bliebe das Fenster bei "Lese …"
            dlog(f"[ASH-PRIO] Laden fehlgeschlagen: {type(e).__name__}: {e}")
            if self._result is None:
                self._result = (None, [], f"Ash-Prio nicht lesbar: {e}")

    def _card_ids(self, names) -> Dict[str, str]:
        """{Kartenname: Passcode} – Artwork wie im Deck; Karten, die nicht im Deck sind, aus der Kartendatenbank."""
        ids: Dict[str, str] = {}
        for cid, name in sorted(self.names.items()):
            if cid in self.deck:
                ids.setdefault(name, cid)
        missing = [name for name in names if name not in ids]
        if missing and self.db is not None:
            try:
                for name, found in self.db.ids_by_name(missing).items():
                    ids[name] = min(found, key=lambda cid: (len(cid), cid))  # das Original (kleinster Passcode)
            except Exception:
                pass  # ohne Bild: die Leiste zeigt dann den Namen
        return ids

    def _text_rows(self) -> List[tuple]:
        """Einschätzung aus den Kartentexten (läuft im Hintergrund-Thread)."""
        if self.db is None:
            return []
        try:
            infos = self.db.infos(self.deck)
        except Exception:
            return []
        rows = []
        for cid, copies in self.deck.items():
            info = infos.get(cid)
            if info is None:
                continue
            try:
                starter = bool(self.db.starter(cid).starter)
            except Exception:
                starter = False
            rows.append((cid, info.name, copies, starter, handtrap_targets.card_targets(info)))
        return sorted(rows, key=lambda r: (not r[3], r[1]))

    def _poll(self) -> None:
        self._poll_job = None
        if self.closed:
            return
        if self._result is not None and not self._shown:
            self._shown = True
            self._show()
        if self._images_ready:
            self._images_ready = False
            self.photos.forget_missing()
            self._render_strip()
        if not self._shown or self._thread.is_alive() or self._images_ready:
            self._poll_job = self.win.after(POLL_MS, self._poll)

    def _show(self) -> None:
        self.variant, self.sheets, hint = self._result
        if self.variant is None and self.text_rows:
            self.info_label.config(
                text=f"{hint} Einschätzung aus den Kartentexten: welche Karten die Handtrap treffen kann.",
                fg=MUTED)
        elif self.variant is None:
            self.info_label.config(text=hint, fg=WARN)
        else:
            source = f" · {hint}" if hint else ""
            self.info_label.config(
                text=f"„{self.variant.name}“ ({self.variant.overlap * 100:.0f} % gleiche Karten{source}) · "
                     f"{len(self.sheets)} Starthände, stärkste zuerst · Klick = ganze Linie", fg=MUTED)
        self.render()

    # ── Anzeige ──
    def choose(self, key: str) -> None:
        self.handtrap = key
        self.focus = None  # andere Handtrap trifft andere Karten
        self.render()

    def toggle(self, hand: str) -> None:
        """Hand auf-/zuklappen: nur ihre Zeile und die Linie ändern (alles neu zu bauen dauert über 100 ms)."""
        previous, self.open_hand = self.open_hand, (None if self.open_hand == hand else hand)
        if self._line_box is not None:
            self._line_box.destroy()
            self._line_box = None
        for key in (previous, self.open_hand):
            if key in self._hand_rows:
                self._paint_hand(key)
        if self.open_hand in self._hand_rows:
            row, _, sheet = self._hand_rows[self.open_hand]
            self._line(sheet, after=row)

    def _paint_hand(self, hand: str) -> None:
        row, base, _ = self._hand_rows[hand]
        bg = HIGHLIGHT if hand == self.open_hand else base
        for widget in (row, *row.winfo_children(), *[w for child in row.winfo_children()
                                                     for w in child.winfo_children()]):
            widget.config(bg=bg)

    def pick(self, card: str) -> None:
        """Klick auf ein Bild: nur die Hände, in denen die Handtrap diese Karte trifft (nochmal: alle)."""
        self.focus = None if self.focus == card else card
        self.render()
        self.canvas.yview_moveto(0)

    def render(self) -> None:
        if self.closed:
            return
        for key, button in self.buttons.items():
            button.config(role="active" if key == self.handtrap else "neutral")
        self._hide_tip()
        for child in self.inner.winfo_children():
            child.destroy()
        self._hand_rows, self._line_box = {}, None
        self._render_strip()
        if self.variant is None and self.text_rows:
            self._render_text()
            return
        names = list(self.names.values())
        for i, sheet in enumerate(s for s in self.sheets
                                  if self.focus is None or ash_prio.hits(s, self.handtrap, self.focus, names)):
            self._hand_row(sheet, ROW_ALT if i % 2 else BG)
            if sheet.hand == self.open_hand:
                self._line(sheet)

    def strip_cards(self) -> List[tuple]:
        """Bildleiste: [(Name, Passcode, [(Text, Farbe, Schrift)])], die wichtigsten zuerst."""
        if self.variant is None:
            if self.handtrap == "nibiru":
                return []
            return [(name, cid, [(f"×{copies}", MUTED, self.font_small)]
                     + ([("STARTER", INFO, self.font_small_bold)] if starter else []))
                    for cid, name, copies, starter, hit in self.text_rows if self.handtrap in hit][:MAX_CARDS]
        cards = []
        for target in ash_prio.targets(self.sheets, self.handtrap, self.names.values())[:MAX_CARDS]:
            color = BAD if target.loss >= 3 else WARN if target.loss >= 1 else GOOD
            cards.append((target.name, self.card_ids.get(target.name),
                          [(f"{target.hands} von {len(self.sheets)}", TEXT, self.font_small_bold),
                           (f"−{ash_prio.number(target.loss)} Board", color, self.font_small)]))
        return cards

    def _render_strip(self) -> None:
        """Die Karten mit Ash-Prio als Bilder (auf einer Zeichenfläche – schnell, ruckelt nicht)."""
        s, strip = self.s, self.strip
        strip.delete("all")
        cards = self.strip_cards() if self._shown else []
        if not cards:
            strip.config(height=1)
            return
        name = dict(ash_prio.HANDTRAPS)[self.handtrap]
        title = (f"{name} trifft hier am häufigsten (Starthände, Ø Schaden)" if self.variant
                 else f"Diese Karten kann {name} treffen")
        strip.create_text(int(2 * s), int(4 * s), text=title + " · Klick filtert", fill=MUTED,
                          font=self.font_small, anchor="nw")
        width, height = self.photos.size
        pad, line = int(6 * s), int(14 * s)
        lines = max(len(c[2]) for c in cards)
        row_height = height + int(4 * s) + line * lines + pad
        top0 = int(22 * s)
        for i, (card, passcode, texts) in enumerate(cards):
            x = int(2 * s) + (i % COLUMNS) * (width + pad)
            top = top0 + (i // COLUMNS) * row_height
            tag = f"card{i}"
            strip.create_rectangle(x, top, x + width, top + row_height - pad, fill=BG, outline="", tags=(tag,))
            photo = self.photos.get(passcode)
            if photo is not None:
                strip.create_image(x, top, image=photo, anchor="nw", tags=(tag,))
            else:
                strip.create_rectangle(x, top, x + width, top + height, fill=PANEL, outline="", tags=(tag,))
                strip.create_text(x + width // 2, top + height // 2, text=card, fill=TEXT, font=self.font_small,
                                  width=width - int(6 * s), justify=tk.CENTER, tags=(tag,))
            if card == self.focus:
                strip.create_rectangle(x - 2, top - 2, x + width + 1, top + height + 1, outline=GOLD, width=3,
                                       tags=(tag,))
            for j, (text, color, font) in enumerate(texts):
                strip.create_text(x + width // 2, top + height + int(3 * s) + j * line, text=text, fill=color,
                                  font=font, anchor="n", tags=(tag,))
            strip.tag_bind(tag, "<Button-1>", lambda e, c=card: self.pick(c))
            strip.tag_bind(tag, "<Enter>", lambda e: strip.config(cursor="hand2"))
            strip.tag_bind(tag, "<Leave>", lambda e: strip.config(cursor=""))
        rows = (len(cards) + COLUMNS - 1) // COLUMNS
        strip.config(height=top0 + rows * row_height)

    def strip_texts(self) -> List[str]:
        """Texte der Bildleiste (für Tests)."""
        return [self.strip.itemcget(i, "text") for i in self.strip.find_all() if self.strip.type(i) == "text"]

    def _render_text(self) -> None:
        """Ohne Starthände: Karten, die die gewählte Handtrap treffen kann (mit dem Effekt)."""
        s = self.s
        if self.handtrap == "nibiru":
            tk.Label(self.inner, text="Nibiru trifft ab der 5. Beschwörung im Zug. Aus den Kartentexten lässt sich "
                                      "das nicht abschätzen – nur die Starthände der Factory zeigen, wo er zuschlägt.",
                     fg=MUTED, bg=BG, font=self.font_small, anchor="w", justify=tk.LEFT,
                     wraplength=int(440 * s)).pack(fill=tk.X, pady=int(6 * s))
            return
        hit = [row for row in self.text_rows if self.handtrap in row[4] and self.focus in (None, row[1])]
        safe = [row[1] for row in self.text_rows if self.handtrap not in row[4]]
        name = dict(ash_prio.HANDTRAPS)[self.handtrap]
        if not hit:
            tk.Label(self.inner, text=f"Keine Karte deines Decks bietet {name} eine erkennbare Angriffsfläche.",
                     fg=GOOD, bg=BG, font=self.font_small, anchor="w").pack(fill=tk.X, pady=int(6 * s))
        for i, (cid, card, copies, starter, targets) in enumerate(hit):
            bg = ROW_ALT if i % 2 else BG
            row = tk.Frame(self.inner, bg=bg, padx=int(8 * s), pady=int(4 * s))
            row.pack(fill=tk.X)
            head = tk.Frame(row, bg=bg)
            head.pack(fill=tk.X)
            if starter:
                tk.Label(head, text="STARTER", fg=INFO, bg=bg, font=self.font_small_bold).pack(side=tk.RIGHT)
            tk.Label(head, text=f"{card}  ×{copies}", fg=TEXT, bg=bg, font=self.font_bold, anchor="w").pack(
                side=tk.LEFT, fill=tk.X, expand=True)
            tk.Label(row, text=f"{name} trifft: {targets[self.handtrap]}", fg=MUTED, bg=bg, font=self.font_small,
                     anchor="w", justify=tk.LEFT, wraplength=int(440 * s)).pack(fill=tk.X)
        if safe:
            tk.Label(self.inner, text=f"Ohne Angriffsfläche für {name}: " + ", ".join(sorted(safe)), fg=MUTED, bg=BG,
                     font=self.font_small, anchor="w", justify=tk.LEFT, wraplength=int(460 * s)).pack(
                fill=tk.X, pady=(int(8 * s), 0))

    def _hand_row(self, sheet: ash_prio.HandSheet, bg: str) -> None:
        s = self.s
        base, bg = bg, HIGHLIGHT if sheet.hand == self.open_hand else bg
        row = tk.Frame(self.inner, bg=bg, padx=int(8 * s), pady=int(4 * s), cursor="hand2")
        row.pack(fill=tk.X)
        self._hand_rows[sheet.hand] = (row, base, sheet)
        threat = sheet.threats.get(self.handtrap)
        after = threat.after if threat is not None and threat.step is not None else sheet.score
        loss = sheet.score - after
        color = GOOD if loss < 1 else WARN if after >= 2 else BAD
        head = tk.Frame(row, bg=bg)
        head.pack(fill=tk.X)
        board = tk.Label(head, text=f"Board {ash_prio.number(sheet.score)} → {ash_prio.number(after)}", fg=color,
                         bg=bg, font=self.font_small_bold)
        board.pack(side=tk.RIGHT)
        board.bind("<Enter>", self._show_tip)
        board.bind("<Leave>", self._hide_tip)
        tk.Label(head, text=sheet.hand, fg=TEXT, bg=bg, font=self.font_bold, anchor="w", justify=tk.LEFT,
                 wraplength=int(340 * s)).pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(row, text=ash_prio.advice(sheet, self.handtrap), fg=MUTED, bg=bg, font=self.font_small,
                 anchor="w", justify=tk.LEFT, wraplength=int(440 * s)).pack(fill=tk.X)
        for widget in (row, head, *row.winfo_children(), *head.winfo_children()):
            widget.bind("<Button-1>", lambda e, h=sheet.hand: self.toggle(h))

    def _show_tip(self, event) -> None:
        self._hide_tip()
        self._tip = show_tip(self.win, "Board = Unterbrechungen am Zugende",
                             event.x_root - int(200 * self.s), event.y_root + int(18 * self.s), self.s)

    def _hide_tip(self, event=None) -> None:
        tip, self._tip = self._tip, None
        if tip is not None:
            tip.destroy()

    def _line(self, sheet: ash_prio.HandSheet, after: Optional[tk.Frame] = None) -> None:
        """Die ganze Linie (unter `after`, sonst am Ende); die Stelle der gewählten Handtrap markiert, danach die beste
        Fortsetzung."""
        s = self.s
        box = self._line_box = tk.Frame(self.inner, bg=PANEL, padx=int(10 * s), pady=int(6 * s))
        box.pack(fill=tk.X, pady=(0, int(4 * s)), **({"after": after} if after is not None else {}))
        threat = sheet.threats.get(self.handtrap)
        hit = threat.step if threat is not None else None
        for index, step in enumerate(sheet.steps):
            marked = index == hit
            text = f"{index + 1}. {ash_prio.short_step(step)}" + (f"   ◀ {threat.name}" if marked else "")
            tk.Label(box, text=text, fg=BAD if marked else TEXT, bg=PANEL,
                     font=self.font_small_bold if marked else self.font_small, anchor="w", justify=tk.LEFT,
                     wraplength=int(420 * s)).pack(fill=tk.X)
        if threat is not None and threat.step is not None and threat.continuation:
            tk.Label(box, text=f"Nach {threat.name} am besten weiter:", fg=INFO, bg=PANEL,
                     font=self.font_small_bold, anchor="w").pack(fill=tk.X, pady=(int(6 * s), 0))
            for step in threat.continuation:
                tk.Label(box, text=f"• {ash_prio.short_step(step)}", fg=TEXT, bg=PANEL, font=self.font_small,
                         anchor="w", justify=tk.LEFT, wraplength=int(420 * s)).pack(fill=tk.X)

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
