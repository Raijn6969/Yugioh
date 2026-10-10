"""
Duell-Übersicht (Klick auf ein Match im Winrate-Fenster): ein gespeichertes Duell auf einem Spielfeld wie in der
Schattenarena der Factory – Gegner oben (rötlich), du unten, in der Mitte das Ergebnis. Ohne die Spalten links und
rechts, damit das Feld selbst groß ist.

Gibt es zum Match einen Replay-Mitschnitt (replay_recorder: einmal das Replay in Master Duel angesehen), zeigt das
Feld das Duell Schritt für Schritt: Karten in ihren Zonen (Gegner gedreht, Verteidigung quer, verdeckt als Rückseite),
Hände, oberste Karten von Friedhof/Deck/Extra Deck/Verbannt mit Anzahl, Lebenspunkte und Zug/Phase. Bedienung:
Anfang, Schritt zurück/vor, Abspielen, Ende; "Decks" schaltet auf die Deck-Ansicht; "Als HTML" exportiert das
Replay als eine Datei zum Verschicken (replay_export, Gegnername standardmäßig ausgeblendet).

Ohne Mitschnitt stehen auf jeder Hälfte das Deck des Spielers (jede Karte einmal, mit Anzahl; das Gegner-Deck kommt
aus der Match History), an den Seiten Deck- und Extra-Deck-Stapel mit Kartenzahl. Mit ◀/▶ blättert man durch die
Matches. Alles wird auf eine Zeichenfläche gemalt (wie card_grid) – schnell, kein Ruckeln beim Blättern.
"""

import os
import subprocess
import threading
import tkinter as tk
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import card_images
import replay_recorder as rr
from card_stats import MatchEntry, Replay
from extras_panel import BG, GOLD, MUTED, PANEL, TEXT
from history_panel import when
from hover_card import AMBER, GREEN, RED
from match_history import DRAW, FINISH_SURRENDER, WIN, mode_name
from rounded_button import RoundedButton
from window_style import apply_frame, no_activate

POLL_MS = 100
CARD = (50, 73)          # Kartengröße bei 1080p (wie die Karten in Master Duel: 59 × 86 mm)
MIN_SCALE = 0.55         # so weit werden die Karten verkleinert, wenn ein Deck viele verschiedene hat …
MAX_SCALE = 1.6          # … und so weit vergrößert, wenn Platz ist
FIELD = "#141e38"        # Spielfeld (Schattenarena: (20, 30, 56))
FIELD_EDGE = "#3c508c"
OPP_HALF = "#281622"     # Hälfte des Gegners rötlich
ZONE, ZONE_EDGE, ZONE_TEXT = "#1a284c", "#30468a", "#5a6eaa"
OPP_ZONE, OPP_ZONE_EDGE = "#2c1a28", "#823c50"
CARD_BACK = "#7a4828"
PLAY_MS = 700            # Abspielen: so lange steht jeder Schritt


def ydk_parts(code: str) -> Tuple[List[str], List[str]]:
    """YDK-Text → (Main, Extra) als Passcodes in der Reihenfolge der Datei."""
    main, extra, part = [], [], None
    for line in (code or "").splitlines():
        line = line.strip()
        if line == "#main":
            part = main
        elif line == "#extra":
            part = extra
        elif line.startswith("!") or line.startswith("#"):
            part = None if line.startswith("!") else part
        elif line.isdigit() and part is not None:
            part.append(line)
    return main, extra


def unique(cards: Sequence[str]) -> List[Tuple[str, int]]:
    """[(Passcode, Anzahl)] – jede Karte einmal, in der Reihenfolge des Decks."""
    counts: Dict[str, int] = {}
    for cid in cards:
        counts[cid] = counts.get(cid, 0) + 1
    return list(counts.items())


def result_text(match: MatchEntry) -> Tuple[str, str]:
    if match.result == WIN:
        return "SIEG", GREEN
    if match.result == DRAW:
        return "REMIS", AMBER
    return "NIEDERLAGE", RED


def details(match: MatchEntry) -> str:
    """'Zweiter · Münzwurf verloren · 9 Züge · Ranked · 08.10.2026 21:17'"""
    parts = []
    if match.first is not None:
        parts.append("Erster" if match.first else "Zweiter")
    if match.coin is not None:
        parts.append("Münzwurf gewonnen" if match.coin else "Münzwurf verloren")
    if match.turns:
        parts.append("1 Zug" if match.turns == 1 else f"{match.turns} Züge")
    parts.append(mode_name(match.mode))
    if match.finish == FINISH_SURRENDER:
        parts.append("Aufgabe")
    parts.append(when(match.played_at))
    return "  ·  ".join(parts)


class DuelView:
    def __init__(self, master: tk.Misc, db, matches: List[MatchEntry], index: int = 0,
                 on_close: Callable[[], None] = lambda: None, image_folder: str = card_images.FOLDER):
        self.master = master
        self.db = db
        self.matches = matches            # das letzte zuerst (wie in der Winrate-Liste)
        self.index = index
        self.on_close = on_close
        self.closed = False
        self.names: Dict[str, str] = {}
        self._loaded = None               # Index, dessen Bilder der Hintergrund-Thread fertig geladen hat
        self._poll_job = None
        self.s = s = max(1.0, master.winfo_screenheight() / 1080.0)
        self.font = ("Helvetica", int(10 * s))
        self.font_small = ("Helvetica", int(8 * s))
        self.font_small_bold = ("Helvetica", int(8 * s), "bold")
        self.font_head = ("Helvetica", int(12 * s), "bold")
        self.font_result = ("Helvetica", int(20 * s), "bold")
        self.folder = image_folder
        self.photos: Dict[float, card_images.PhotoCache] = {}  # je Kartengröße ein Cache
        self.replay: Optional[Replay] = None
        self.frame_no = 0
        self.mode = "decks"               # "replay" (Mitschnitt vorhanden) oder "decks"
        self._play_job = None
        self._passcodes: Optional[Dict[int, str]] = None   # Konami-ID → Passcode (fürs Bild)
        self._art: Dict[tuple, object] = {}                # gedrehte Kartenbilder

        self.win = tk.Toplevel(master, bg=BG)
        self.win.overrideredirect(True)
        self.win.wm_attributes("-topmost", True)
        self._build()
        self._place()
        apply_frame(self.win)
        no_activate(self.win)  # Klicks lassen Master Duel aktiv (sonst Taskleiste über dem Spiel)
        self.win.bind("<Escape>", lambda e: self.close())
        self.show(index)
        self._poll()

    # ── Aufbau ──
    def _build(self) -> None:
        s = self.s
        header = tk.Frame(self.win, bg=PANEL)
        header.pack(fill=tk.X)
        tk.Label(header, text="◆ DUELL", fg=GOLD, bg=PANEL, font=self.font_head).pack(
            side=tk.LEFT, padx=(int(12 * s), int(6 * s)), pady=int(8 * s))
        self.title_label = tk.Label(header, text="", fg=MUTED, bg=PANEL, font=self.font)
        self.title_label.pack(side=tk.LEFT)
        RoundedButton(header, text="✕", command=self.close, bg="#cc0000", border="#ff8a80",
                      font=("Helvetica", int(9 * s), "bold"), padx=int(9 * s), pady=int(2 * s),
                      radius=int(6 * s)).pack(side=tk.RIGHT, padx=int(8 * s))
        small = dict(font=self.font_small, padx=int(9 * s), pady=int(3 * s), radius=int(6 * s))
        self.newer_btn = RoundedButton(header, text="neuer ▶", command=lambda: self.step(-1), **small)
        self.newer_btn.pack(side=tk.RIGHT, padx=(0, int(4 * s)))
        self.older_btn = RoundedButton(header, text="◀ älter", command=lambda: self.step(1), **small)
        self.older_btn.pack(side=tk.RIGHT, padx=(0, int(4 * s)))
        # Bedienung des Replays (nur sichtbar, wenn es einen Mitschnitt gibt)
        self.controls = tk.Frame(self.win, bg=PANEL)
        self.ctrl_buttons = {}
        for key, text, command in (("first", "⏮", lambda: self.goto(0)), ("back", "◀", lambda: self.goto(self.frame_no - 1)),
                                   ("play", "▶ Abspielen", self.toggle_play),
                                   ("next", "▶", lambda: self.goto(self.frame_no + 1)),
                                   ("last", "⏭", lambda: self.goto(len(self.replay.frames) - 1))):
            button = RoundedButton(self.controls, text=text, command=command, **small)
            button.pack(side=tk.LEFT, padx=(int(8 * s) if key == "first" else 0, int(4 * s)), pady=int(4 * s))
            self.ctrl_buttons[key] = button
        self.step_label = tk.Label(self.controls, text="", fg=TEXT, bg=PANEL, font=self.font)
        self.step_label.pack(side=tk.LEFT, padx=int(8 * s))
        self.mode_btn = RoundedButton(self.controls, text="Decks", command=self.toggle_mode, **small)
        self.mode_btn.pack(side=tk.RIGHT, padx=int(8 * s))
        self.hide_opponent = True
        self.export_btn = RoundedButton(self.controls, text="Als HTML", command=self.export_html, bg="#00796b", **small)
        self.export_btn.pack(side=tk.RIGHT)
        self.hide_btn = RoundedButton(self.controls, text="Gegnername: aus", command=self.toggle_hide, **small)
        self.hide_btn.pack(side=tk.RIGHT, padx=(0, int(4 * s)))
        self.canvas = tk.Canvas(self.win, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

    def _place(self) -> None:
        s = self.s
        screen_w, screen_h = self.master.winfo_screenwidth(), self.master.winfo_screenheight()
        w, h = min(int(1000 * s), screen_w - 40), min(int(860 * s), screen_h - 80)
        self.size = (w, h - int(76 * s))  # Zeichenfläche ohne Kopfzeile und Replay-Bedienung
        self.canvas.config(width=self.size[0], height=self.size[1])
        self.win.geometry(f"{w}x{h}+{(screen_w - w) // 2}+{max(0, (screen_h - h) // 2 - int(20 * s))}")

    # ── Inhalt ──
    @property
    def match(self) -> MatchEntry:
        return self.matches[self.index]

    def step(self, delta: int) -> None:
        if 0 <= self.index + delta < len(self.matches):
            self.show(self.index + delta)

    def show(self, index: int) -> None:
        self.index = index
        self._stop_play()
        match = self.match
        try:
            self.replay = self.db.replay_for(match.played_at)
        except Exception:  # Datenbank gesperrt, alte Datenbank-Fassung …
            self.replay = None
        self.mode, self.frame_no = ("replay" if self.replay else "decks"), 0
        if self.replay:
            self.controls.pack(fill=tk.X, before=self.canvas)
        else:
            self.controls.pack_forget()
        self.title_label.config(text=f"{match.deck_name or match.md_deck or 'Unbekanntes Deck'}  vs  "
                                     f"{match.opp_name or 'unbekannt'}  ·  {index + 1} / {len(self.matches)}")
        self.older_btn.config(bg="#444444" if index + 1 < len(self.matches) else "#2a2a2a")
        self.newer_btn.config(bg="#444444" if index > 0 else "#2a2a2a")
        ids = set(ydk_parts(match.deck_code)[0] + ydk_parts(match.deck_code)[1] + ydk_parts(match.opp_code)[0]
                  + ydk_parts(match.opp_code)[1])
        if self.replay:
            kids = {c[4] for f in self.replay.frames for c in f["cards"] if c[4]}
            ids |= {self.passcode(kid) for kid in kids} - {None}
        try:
            self.names.update({cid: info.name for cid, info in self.db.infos(ids - set(self.names)).items()})
        except Exception:
            pass
        self.draw()
        if self._loaded != index:
            threading.Thread(target=self._load_images, args=(index, sorted(ids)), daemon=True,
                             name="duel-images").start()

    def _load_images(self, index: int, ids: List[str]) -> None:
        """Fehlende Kartenbilder laden (offline: Platzhalter mit Namen) und vorbereiten."""
        try:
            card_images.download(ids, folder=self.folder)
        except Exception:
            pass
        self._loaded = index

    def _poll(self) -> None:
        if self.closed:
            return
        if self._loaded == self.index and getattr(self, "_drawn_with_images", None) != self.index:
            self._drawn_with_images = self.index
            for cache in self.photos.values():
                cache.forget_missing()
            self._art = {k: v for k, v in self._art.items() if v is not None}  # inzwischen geladene Bilder
            self.draw()
        self._poll_job = self.win.after(POLL_MS, self._poll)

    # ── Replay ──
    def passcode(self, kid: int) -> Optional[str]:
        if self._passcodes is None:
            try:
                self._passcodes = self.db.konami_passcodes()
            except Exception:
                self._passcodes = {}
        return self._passcodes.get(kid)

    def goto(self, frame_no: int) -> None:
        if self.replay and 0 <= frame_no < len(self.replay.frames):
            self.frame_no = frame_no
            self.draw()
        elif self.replay and frame_no >= len(self.replay.frames):
            self._stop_play()  # Ende erreicht

    def toggle_play(self) -> None:
        if self._play_job is not None:
            self._stop_play()
            return
        if self.replay and self.frame_no >= len(self.replay.frames) - 1:
            self.frame_no = 0
        self.mode = "replay"
        self.ctrl_buttons["play"].config(text="⏸ Pause")
        self._tick()

    def _tick(self) -> None:
        if self.closed or not self.replay:
            return
        self.goto(self.frame_no + 1 if self._play_job is not None else self.frame_no)
        if self.frame_no < len(self.replay.frames) - 1:
            self._play_job = self.win.after(PLAY_MS, self._tick)
        else:
            self._stop_play()

    def _stop_play(self) -> None:
        if self._play_job is not None:
            try:
                self.win.after_cancel(self._play_job)
            except tk.TclError:
                pass
        self._play_job = None
        if "play" in getattr(self, "ctrl_buttons", {}):
            self.ctrl_buttons["play"].config(text="▶ Abspielen")

    def toggle_hide(self) -> None:
        self.hide_opponent = not self.hide_opponent
        self.hide_btn.config(text="Gegnername: aus" if self.hide_opponent else "Gegnername: an")

    def export_html(self) -> None:
        """Replay als HTML in den Downloads-Ordner (im Hintergrund: fehlende Kartenbilder werden geladen)."""
        if not self.replay or getattr(self, "_exporting", False):
            return
        self._exporting = True
        self.step_label.config(text="Exportiere als HTML …")
        replay, match, hide = self.replay, self.match, self.hide_opponent

        def work():
            import replay_export
            try:
                result = ("ok", replay_export.export(replay, match, self.db, hide, image_folder=self.folder))
            except Exception as e:  # Ordner nicht beschreibbar …
                result = ("error", str(e))
            self._export_result = result

        self._export_result = None
        threading.Thread(target=work, daemon=True, name="replay-export").start()
        self._wait_export()

    def _wait_export(self) -> None:
        if self.closed:
            return
        if self._export_result is None:
            self.win.after(POLL_MS, self._wait_export)
            return
        kind, value = self._export_result
        self._exporting = False
        if kind == "ok":
            self.step_label.config(text=f"Gespeichert: {os.path.basename(value)} (Downloads)")
            self._reveal(value)
        else:
            self.step_label.config(text=f"Export fehlgeschlagen: {value}")

    @staticmethod
    def _reveal(path: str) -> None:
        """Explorer mit der Datei markiert öffnen (zum Verschicken)."""
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
        except OSError:
            pass

    def toggle_mode(self) -> None:
        self._stop_play()
        self.mode = "decks" if self.mode == "replay" else "replay"
        self.draw()

    # ── Zeichnen ──
    def draw(self) -> None:
        if self.mode == "replay" and self.replay:
            self.mode_btn.config(text="Decks")
            self._draw_replay()
            return
        if self.replay:
            self.mode_btn.config(text="Replay")
        c, s = self.canvas, self.s
        c.delete("all")
        w, h = self.size
        m = int(8 * s)
        band = int(78 * s)
        c.create_rectangle(m, m, w - m, h - m, fill=FIELD, outline=FIELD_EDGE)
        half = (h - 2 * m - band) // 2
        opp_box = (m + int(6 * s), m + int(6 * s), w - m - int(6 * s), m + half)
        my_box = (m + int(6 * s), m + half + band, w - m - int(6 * s), h - m - int(6 * s))
        c.create_rectangle(*opp_box, fill=OPP_HALF, outline="")
        match = self.match
        mine, theirs = ydk_parts(match.deck_code), ydk_parts(match.opp_code)
        self._half(opp_box, theirs, opp=True, label=match.opp_name or "Gegner")
        self._half(my_box, mine, opp=False, label=match.deck_name or match.md_deck or "Du")
        # Mitte: Ergebnis wie am Ende eines Duells
        text, color = result_text(match)
        mid = m + half + band // 2
        c.create_text(w // 2, mid - int(12 * s), text=text, fill=color, font=self.font_result)
        c.create_text(w // 2, mid + int(16 * s), text=details(match), fill=TEXT, font=self.font)

    def _half(self, box, deck: Tuple[List[str], List[str]], opp: bool, label: str) -> None:
        """Eine Hälfte: Stapel an den Seiten (wie in der Arena), dazwischen das Deck als Kartenraster."""
        c, s = self.canvas, self.s
        x0, y0, x1, y1 = box
        main, extra = deck
        pile_w = int(CARD[0] * s * 1.25)
        title_color = "#dc96a0" if opp else "#96beff"
        c.create_text(x0 + int(8 * s), y0 + int(6 * s), text=("GEGNER · " if opp else "DU · ") + label,
                      fill=title_color, font=self.font_small_bold, anchor="nw")
        # Stapel: Gegner Deck links / Extra rechts, du Extra links / Deck rechts (wie in Master Duel)
        left, right = (("Deck", len(main)), ("Extra Deck", len(extra))) if opp else \
            (("Extra Deck", len(extra)), ("Deck", len(main)))
        top = y0 + int(26 * s)
        for (name, count), px in ((left, x0 + int(8 * s)), (right, x1 - int(8 * s) - pile_w)):
            self._pile(px, top, pile_w, name, count, opp)
        grid = (x0 + pile_w + int(20 * s), top, x1 - pile_w - int(20 * s), y1 - int(6 * s))
        if not main and not extra:
            c.create_text((grid[0] + grid[2]) // 2, (grid[1] + grid[3]) // 2, fill=MUTED, font=self.font,
                          text="Deck unbekannt – das Gegner-Deck kommt mit der Match History" if opp else
                          "Deck unbekannt (einmal die Deck-Auswahl in Master Duel öffnen)")
            return
        self._grid(grid, unique(main), unique(extra))

    def _pile(self, x: int, y: int, w: int, name: str, count: int, opp: bool) -> None:
        c, s = self.canvas, self.s
        h = int(w / CARD[0] * CARD[1])
        zone, edge = (OPP_ZONE, OPP_ZONE_EDGE) if opp else (ZONE, ZONE_EDGE)
        c.create_rectangle(x, y, x + w, y + h, fill=zone, outline=edge)
        if count:
            pad = int(5 * s)
            c.create_rectangle(x + pad, y + pad, x + w - pad, y + h - pad, fill=CARD_BACK, outline="#3a2010")
            c.create_text(x + w // 2, y + h // 2, text=str(count), fill=TEXT, font=self.font_head)
        c.create_text(x + w // 2, y + h + int(4 * s), text=name, fill=ZONE_TEXT, font=self.font_small, anchor="n")

    def _fit(self, grid, rows_needed) -> Tuple[float, int]:
        """Größte Kartengröße (Faktor), bei der alle Karten in die Fläche passen; (Faktor, Spalten)."""
        x0, y0, x1, y1 = grid
        factor = MAX_SCALE
        while True:
            tw, th, gap = int(CARD[0] * self.s * factor), int(CARD[1] * self.s * factor), int(6 * self.s)
            columns = max(1, (x1 - x0 + gap) // (tw + gap))
            height = rows_needed(columns) * (th + gap) + int(16 * self.s)
            if height <= y1 - y0 or factor <= MIN_SCALE:
                return factor, columns
            factor -= 0.05

    def _grid(self, grid, main: List[Tuple[str, int]], extra: List[Tuple[str, int]]) -> None:
        c, s = self.canvas, self.s
        x0, y0, x1, y1 = grid
        rows = lambda cols: -(-len(main) // cols) + (-(-len(extra) // cols) if extra else 0)  # noqa: E731
        factor, columns = self._fit(grid, rows)
        tw, th, gap = int(CARD[0] * s * factor), int(CARD[1] * s * factor), int(6 * s)
        photos = self.photos.setdefault(round(factor, 2), card_images.PhotoCache(self.win, (tw, th), self.folder))
        used = min(columns, max(len(main), len(extra), 1)) * (tw + gap) - gap
        left = x0 + (x1 - x0 - used) // 2  # Raster mittig zwischen den Stapeln
        y = y0
        for block_no, block in enumerate((main, extra)):
            if not block:
                continue
            if block_no:
                y += int(10 * s)  # kleiner Abstand vor dem Extra Deck
            for i, (cid, count) in enumerate(block):
                if i and i % columns == 0:
                    y += th + gap
                self._card(left + (i % columns) * (tw + gap), y, tw, th, cid, count, photos)
            y += th + gap

    def _card(self, x: int, y: int, w: int, h: int, cid: str, count: int, photos) -> None:
        c, s = self.canvas, self.s
        tag = f"card{cid}_{x}_{y}"
        photo = photos.get(cid)
        if photo is not None:
            c.create_image(x, y, image=photo, anchor="nw", tags=(tag,))
        else:
            c.create_rectangle(x, y, x + w, y + h, fill=PANEL, outline=ZONE_EDGE, tags=(tag,))
            c.create_text(x + w // 2, y + h // 2, text=self.names.get(cid, cid), fill=TEXT, font=self.font_small,
                          width=w - int(6 * s), justify=tk.CENTER, tags=(tag,))
        if count > 1:
            bw, bh = int(20 * s), int(14 * s)
            c.create_rectangle(x + w - bw, y + h - bh, x + w, y + h, fill="#101018", outline=GOLD, tags=(tag,))
            c.create_text(x + w - bw // 2, y + h - bh // 2, text=f"{count}×", fill=GOLD, font=self.font_small_bold,
                          tags=(tag,))
        c.tag_bind(tag, "<Enter>", lambda e, cid=cid, x=x, y=y, h=h: self._tip(cid, count, x, y + h))
        c.tag_bind(tag, "<Leave>", lambda e: c.delete("tip"))

    def _tip(self, cid: str, count: int, x: int, y: int, plain: bool = False) -> None:
        c, s = self.canvas, self.s
        c.delete("tip")
        text = self.names.get(cid, cid) if plain else f"{count}× {self.names.get(cid, cid)}"
        label = c.create_text(x, y + int(6 * s), text=text, fill=TEXT, font=self.font, anchor="nw", tags=("tip",))
        x0, y0, x1, y1 = c.bbox(label)
        if x1 > self.size[0] - int(10 * s):  # rechts nicht aus dem Feld hinaus
            c.move(label, self.size[0] - int(10 * s) - x1, 0)
            x0, y0, x1, y1 = c.bbox(label)
        pad = int(4 * s)
        c.tag_lower(c.create_rectangle(x0 - pad, y0 - pad, x1 + pad, y1 + pad, fill=PANEL, outline=GOLD,
                                       tags=("tip",)), label)

    # ── Replay: Spielfeld wie in der Schattenarena ──
    def _geometry(self):
        """7 Spalten × 7 Reihen (Hand Gegner, Z/F, Monster, Extra-Monsterzonen, Monster, Z/F, deine Hand)."""
        w, h = self.size
        gap = int(6 * self.s)
        card_h = (h - 2 * gap - 8 * gap) // 7
        card_w = int(card_h * CARD[0] / CARD[1])
        if 7 * card_w + 8 * gap > w - 2 * gap:  # schmales Fenster: nach der Breite
            card_w = (w - 2 * gap - 8 * gap) // 7
            card_h = int(card_w * CARD[1] / CARD[0])
        left = (w - (7 * card_w + 6 * gap)) // 2
        top = (h - (7 * card_h + 6 * gap)) // 2
        cols = [left + i * (card_w + gap) for i in range(7)]
        rows = {name: top + i * (card_h + gap) for i, name in
                enumerate(("opp_hand", "opp_s", "opp_m", "emz", "my_m", "my_s", "my_hand"))}
        return card_w, card_h, cols, rows

    @staticmethod
    def place(zone: int, mine: bool) -> Optional[Tuple[str, int]]:
        """Zone → (Reihe, Spalte) aus eigener Sicht; das Feld des Gegners ist gespiegelt. None = nicht auf dem Feld."""
        if zone in rr.MONSTER_ZONES:
            return ("my_m", 1 + zone) if mine else ("opp_m", 5 - zone)
        if zone in rr.EXTRA_MONSTER_ZONES:
            left = zone == rr.EXTRA_MONSTER_ZONES[0]
            return "emz", (2 if left else 4) if mine else (4 if left else 2)
        if zone in rr.SPELL_ZONES:
            return ("my_s", 1 + zone - 7) if mine else ("opp_s", 5 - (zone - 7))
        piles = {rr.FIELD_ZONE: (("my_m", 0), ("opp_m", 6)), rr.GRAVE: (("my_m", 6), ("opp_m", 0)),
                 rr.DECK: (("my_s", 6), ("opp_s", 0)), rr.EXTRA_DECK: (("my_s", 0), ("opp_s", 6)),
                 rr.BANISHED: (("emz", 6), ("emz", 0))}
        if zone in piles:
            return piles[zone][0 if mine else 1]
        return None

    def _draw_replay(self) -> None:
        c, s = self.canvas, self.s
        c.delete("all")
        replay = self.replay
        frame = replay.frames[self.frame_no]
        w, h = self.size
        cw, ch, cols, rows = self._geometry()
        c.create_rectangle(int(4 * s), int(4 * s), w - int(4 * s), h - int(4 * s), fill=FIELD, outline=FIELD_EDGE)
        c.create_rectangle(cols[0] - int(4 * s), rows["opp_hand"] - int(4 * s), cols[6] + cw + int(4 * s),
                           rows["emz"] - int(3 * s), fill=OPP_HALF, outline="")
        labels = {(r, k): "Monster" for r in ("opp_m", "my_m") for k in range(1, 6)}
        labels.update({(r, k): "Zauber/Falle" for r in ("opp_s", "my_s") for k in range(1, 6)})
        labels.update({("emz", 2): "Extra-MZ", ("emz", 4): "Extra-MZ", ("my_m", 0): "Feld", ("opp_m", 6): "Feld",
                       ("my_m", 6): "Friedhof", ("opp_m", 0): "Friedhof", ("my_s", 6): "Deck", ("opp_s", 0): "Deck",
                       ("my_s", 0): "Extra Deck", ("opp_s", 6): "Extra Deck", ("emz", 6): "Verbannt",
                       ("emz", 0): "Verbannt"})
        for (row, col), text in labels.items():
            opp = row.startswith("opp") or (row == "emz" and col == 0)
            x, y = cols[col], rows[row]
            c.create_rectangle(x, y, x + cw, y + ch, fill=OPP_ZONE if opp else ZONE,
                               outline=OPP_ZONE_EDGE if opp else ZONE_EDGE)
            c.create_text(x + cw // 2, y + ch // 2, text=text, fill=ZONE_TEXT, font=self.font_small, width=cw - 4,
                          justify=tk.CENTER)
        cards = [dict(zip(("uid", "team", "zone", "index", "card", "face", "attack", "atk", "def"), row))
                 for row in frame["cards"]]
        piles: Dict[Tuple[str, int], List[dict]] = {}
        hands: Dict[bool, List[dict]] = {True: [], False: []}
        for card in cards:
            mine = card["team"] == replay.my_id
            if card["zone"] == rr.HAND:
                hands[mine].append(card)
                continue
            spot = self.place(card["zone"], mine)
            if spot is None:
                continue
            if card["zone"] in (rr.GRAVE, rr.DECK, rr.EXTRA_DECK, rr.BANISHED, rr.FIELD_ZONE):
                piles.setdefault(spot, []).append(card)
                continue
            row, col = spot
            self._board_card(cols[col], rows[row], cw, ch, card, mine, stats=True)
        for (row, col), stack in piles.items():
            top = max(stack, key=lambda k: k["index"])
            self._board_card(cols[col], rows[row], cw, ch, top, top["team"] == replay.my_id)
            if top["zone"] in (rr.GRAVE, rr.EXTRA_DECK, rr.BANISHED):  # Deck: nur die oberste Karte, keine Anzahl
                self._badge(cols[col] + cw, rows[row] + ch, str(top["index"] + 1))
        for mine, hand in hands.items():
            hand.sort(key=lambda k: k["index"])
            row = rows["my_hand" if mine else "opp_hand"]
            step = min(cw + int(4 * s), (cols[6] - cols[0]) // max(1, len(hand)))  # viele Karten: überlappen
            left = (w - (step * (len(hand) - 1) + cw)) // 2
            for i, card in enumerate(hand):
                self._board_card(left + i * step, row, cw, ch, card, mine, hand=True)
        self._lp_and_phase(frame, cols, rows, cw, ch)
        self.step_label.config(text=f"Schritt {self.frame_no + 1} / {len(replay.frames)}"
                                    + (f"  ·  {frame.get('turn')}" if frame.get("turn") else "")
                                    + (f"  ·  {frame.get('phase')}" if frame.get("phase") else ""))

    def _lp_and_phase(self, frame: dict, cols, rows, cw: int, ch: int) -> None:
        c, s = self.canvas, self.s
        replay = self.replay
        names = replay.names if len(replay.names) == 2 else ["", ""]
        lp = frame.get("lp", {})
        me, opp = replay.my_id, 1 - replay.my_id
        for team, row, color in ((opp, "opp_hand", "#dc96a0"), (me, "my_hand", "#96beff")):
            x, y = cols[0], rows[row] + ch // 2
            c.create_text(x, y - int(10 * s), text=names[team] if team < len(names) else "", fill=color,
                          font=self.font_small_bold, anchor="w")
            c.create_text(x, y + int(10 * s), text=f"LP {lp.get(str(team), '–')}", fill=TEXT, font=self.font_head,
                          anchor="w")
        if frame.get("turn") or frame.get("phase"):  # wie der Phasen-Knopf in Master Duel
            x, y = cols[3] + cw // 2, rows["emz"] + ch // 2
            r = int(min(cw, ch) * 0.48)
            c.create_oval(x - r, y - r, x + r, y + r, fill="#8a1c1c", outline=GOLD, width=2)
            c.create_text(x, y - int(8 * s), text=frame.get("turn") or "", fill=TEXT, font=self.font_small)
            c.create_text(x, y + int(8 * s), text=frame.get("phase") or "", fill=TEXT, font=self.font_small_bold)

    def _board_card(self, x: int, y: int, w: int, h: int, card: dict, mine: bool, stats: bool = False,
                    hand: bool = False) -> None:
        """Karte im Feld: Gegner um 180° gedreht, Verteidigung quer, verdeckt als Rückseite."""
        c, s = self.canvas, self.s
        defense = not hand and card["zone"] in (*rr.MONSTER_ZONES, *rr.EXTRA_MONSTER_ZONES) and not card["attack"]
        angle = (0 if mine else 180) + (90 if defense else 0)
        passcode = self.passcode(card["card"]) if card["card"] else None
        shown = passcode if card["face"] or (mine and hand) else None
        image = self._card_image(shown, w, h, angle) if shown else None
        cx, cy = x + w // 2, y + h // 2
        if image is not None:
            c.create_image(cx, cy, image=image)
        else:
            bw, bh = (h, w) if defense else (w, h)
            c.create_rectangle(cx - bw // 2, cy - bh // 2, cx + bw // 2, cy + bh // 2, fill=CARD_BACK,
                               outline="#3a2010")
            if shown:
                c.create_text(cx, cy, text=self.names.get(shown, ""), fill=TEXT, font=self.font_small,
                              width=bw - 4, justify=tk.CENTER)
        tag = f"bc{card['uid']}_{x}_{y}"
        c.create_rectangle(x, y, x + w, y + h, outline="", fill="", tags=(tag,))
        if shown:
            c.tag_bind(tag, "<Enter>", lambda e, p=shown, x=x, y=y, h=h: self._tip(p, 1, x, y + h, plain=True))
            c.tag_bind(tag, "<Leave>", lambda e: c.delete("tip"))
        if stats and card["face"] and card["zone"] in (*rr.MONSTER_ZONES, *rr.EXTRA_MONSTER_ZONES) and card["atk"] >= 0:
            c.create_text(cx, y + h - int(2 * s), text=f"{card['atk']} / {card['def']}", fill=GOLD,
                          font=self.font_small_bold, anchor="s")

    def _card_image(self, passcode: str, w: int, h: int, angle: int):
        key = (passcode, w, h, angle)
        if key not in self._art:
            from PIL import Image, ImageTk
            try:
                with Image.open(card_images.image_path(passcode, self.folder)) as image:
                    art = image.convert("RGB").resize((w, h), Image.LANCZOS)
                if angle % 360:
                    art = art.rotate(angle, expand=True)
                self._art[key] = ImageTk.PhotoImage(art, master=self.win)
            except (OSError, ValueError):
                self._art[key] = None
        return self._art[key]

    def _badge(self, right: int, bottom: int, text: str) -> None:
        c, s = self.canvas, self.s
        bw, bh = int(22 * s), int(14 * s)
        c.create_rectangle(right - bw, bottom - bh, right, bottom, fill="#101018", outline=GOLD)
        c.create_text(right - bw // 2, bottom - bh // 2, text=text, fill=GOLD, font=self.font_small_bold)

    # ── Zustand von außen ──
    def set_visible(self, visible: bool) -> None:
        if not self.closed:
            self.win.wm_attributes("-alpha", 1.0 if visible else 0.0)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._stop_play()
        if self._poll_job is not None:
            try:
                self.win.after_cancel(self._poll_job)
            except tk.TclError:
                pass
        self.win.destroy()
        self.on_close()
