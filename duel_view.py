"""
Duell-Übersicht (Klick auf ein Match im Winrate-Fenster): ein gespeichertes Duell auf einem Spielfeld wie in der
Schattenarena der Factory – Gegner oben (rötlich), du unten, in der Mitte das Ergebnis. Ohne die Spalten links und
rechts, damit das Feld selbst groß ist.

Gibt es zum Match einen Replay-Mitschnitt (replay_recorder: einmal das Replay in Master Duel angesehen) und wurde
die Ansicht mit replay=True geöffnet ("▶ Replay" in der Match-Zeile), zeigt das Feld das Duell Schritt für Schritt:
Karten in ihren Zonen (Gegner gedreht, Verteidigung quer, verdeckt als Rückseite), Hände, oberste Karten von
Friedhof/Deck/Extra Deck/Verbannt mit Anzahl, Lebenspunkte und Zug/Phase. Bedienung:
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
from PIL import Image, ImageTk

import card_images
import replay_recorder as rr
from card_stats import CardInfo, MatchEntry, Replay
from dark_dialog import _make_draggable
from dark_scrollbar import DarkScrollbar
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

PILES = {
    rr.FIELD_ZONE: (("my_m", 0), ("opp_m", 6)),
    rr.GRAVE: (("my_m", 6), ("opp_m", 0)),
    rr.DECK: (("my_s", 6), ("opp_s", 0)),
    rr.EXTRA_DECK: (("my_s", 0), ("opp_s", 6)),
    rr.BANISHED: (("emz", 6), ("emz", 0)),
}

BOARD_LABELS = {(r, k): "Monster" for r in ("opp_m", "my_m") for k in range(1, 6)}
BOARD_LABELS.update({(r, k): "Zauber/Falle" for r in ("opp_s", "my_s") for k in range(1, 6)})
BOARD_LABELS.update({
    ("emz", 2): "Extra-MZ", ("emz", 4): "Extra-MZ", ("my_m", 0): "Feld", ("opp_m", 6): "Feld",
    ("my_m", 6): "Friedhof", ("opp_m", 0): "Friedhof", ("my_s", 6): "Deck", ("opp_s", 0): "Deck",
    ("my_s", 0): "Extra Deck", ("opp_s", 6): "Extra Deck", ("emz", 6): "Verbannt",
    ("emz", 0): "Verbannt"
})
CARD_KEYS = ("uid", "team", "zone", "index", "card", "face", "attack", "atk", "def")


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


def is_discard_cost(info: Optional[CardInfo]) -> bool:
    """Prüft, ob die Aktivierungskosten einer Karte ein Abwerfen/Senden auf den Friedhof verlangen."""
    if info is None:
        return False
    card_type = (info.card_type or "").lower()
    # Zauber und Fallen werden auf das Spielfeld gelegt, nicht als Kosten abgeworfen!
    if any(t in card_type for t in ("spell", "trap")):
        return False
    text = (info.desc or "").lower()
    # Kosten stehen im PSCT vor dem ersten Semikolon
    cost = text.partition(";")[0] if ";" in text else ""
    return any(k in cost for k in ("discard", "send this card", "to the gy", "to the graveyard", "tribute", "banish"))


def must_leave_field_on_resolution(zone: int, info: Optional[CardInfo]) -> bool:
    """Normale Zauber, Schnellzauber, Ritualzauber, normale Fallen und Konterfallen gehen nach der
    Auflösung in den Friedhof. Permanente Zauber/Fallen, Spielfeldzauber, Ausrüstungszauber und
    Monster bleiben auf dem Spielfeld liegen."""
    if zone == rr.FIELD_ZONE:
        return False
    if info is None:
        return zone in (*rr.SPELL_ZONES, rr.MOVING)
    card_type = (info.card_type or "").lower()
    race = (info.race or "").lower()
    if any(t in card_type for t in ("fusion", "synchro", "xyz", "link", "monster")):
        return False
    if any(r in race for r in ("continuous", "field", "equip")):
        return False
    return True


class ReplayStateTracker:
    """
    Verfolgt den kumulativen Zustand eines Replays über alle Frames:
    - Friedhof (gy): Welche Karten liegen in welchem Schritt im Friedhof jedes Spielers.
    - Verbannung (banished): Welche Karten liegen in der Verbannungszone.
    - Aktivierungen (activating): Welche Karte führt im aktuellen Schritt einen Effekt aus.
    - Ketten-Sequenzen (chain): Verfolgt Kettenglieder (CL1, CL2 ...) wie in Master Duel.
    - Feld-Zonen (field_zones): Merkt sich die Position von Zauber-/Fallenkarten auf dem Spielfeld.
    """
    def __init__(self, frames: List[dict], db=None, passcode_func=None, my_id: int = 0):
        self.frames = frames or []
        self.my_id = my_id
        self.db = db
        self.passcode_func = passcode_func
        self._info_cache: Dict[int, Optional[CardInfo]] = {}
        self.frame_states: List[dict] = []
        self._compute()

    def _card_info(self, kid: int) -> Optional[CardInfo]:
        if not self.db or not self.passcode_func or not kid:
            return None
        if kid in self._info_cache:
            return self._info_cache[kid]
        passcode = self.passcode_func(kid)
        if not passcode:
            self._info_cache[kid] = None
            return None
        info = self.db.info(passcode)
        self._info_cache[kid] = info
        return info

    def _compute(self) -> None:
        # Vorausschau: Für jedes uid die Liste aller (frame_index, zone) mit zone != rr.MOVING
        non_moving_appearances: Dict[int, List[Tuple[int, int]]] = {}
        for idx, f in enumerate(self.frames):
            for row in f.get("cards", []):
                uid, zone = row[0], row[2]
                if zone != rr.MOVING:
                    non_moving_appearances.setdefault(uid, []).append((idx, zone))

        def get_next_zone(uid: int, f_idx: int) -> Optional[int]:
            for fut_idx, fut_zone in non_moving_appearances.get(uid, []):
                if fut_idx > f_idx:
                    return fut_zone
            return None

        gy = {0: [], 1: []}
        ban = {0: [], 1: []}
        gy_uids = {0: set(), 1: set()}
        ban_uids = {0: set(), 1: set()}
        prev_cards: Dict[int, dict] = {}
        last_zones: Dict[int, int] = {}
        last_field_zones: Dict[int, int] = {}
        current_chain: List[dict] = []

        for f_idx, f in enumerate(self.frames):
            cards = [dict(zip(CARD_KEYS, row)) for row in f.get("cards", [])]
            current_uids = {c["uid"] for c in cards}
            dest_zones: Dict[int, Optional[int]] = {}

            # 1. Zonen-Updates
            for c in cards:
                uid, team, zone = c["uid"], c["team"], c["zone"]
                if team not in gy:
                    continue
                if zone != rr.MOVING:
                    last_zones[uid] = zone
                else:
                    prev_z = last_zones.get(uid)
                    next_z = get_next_zone(uid, f_idx)
                    if next_z is None:
                        dest_z = rr.GRAVE if prev_z in (*rr.MONSTER_ZONES, *rr.EXTRA_MONSTER_ZONES,
                                                         *rr.SPELL_ZONES, rr.FIELD_ZONE, rr.HAND) else None
                    else:
                        dest_z = next_z
                    dest_zones[uid] = dest_z

                if zone in (*rr.MONSTER_ZONES, *rr.EXTRA_MONSTER_ZONES, *rr.SPELL_ZONES, rr.FIELD_ZONE):
                    last_field_zones[uid] = zone
                    if uid in gy_uids[team]:
                        gy_uids[team].remove(uid)
                        gy[team] = [x for x in gy[team] if x["uid"] != uid]
                    if uid in ban_uids[team]:
                        ban_uids[team].remove(uid)
                        ban[team] = [x for x in ban[team] if x["uid"] != uid]
                elif zone == rr.GRAVE:
                    if uid in ban_uids[team]:
                        ban_uids[team].remove(uid)
                        ban[team] = [x for x in ban[team] if x["uid"] != uid]
                    if uid not in gy_uids[team]:
                        gy_uids[team].add(uid)
                        gy[team].append(c)
                elif zone == rr.BANISHED:
                    if uid in gy_uids[team]:
                        gy_uids[team].remove(uid)
                        gy[team] = [x for x in gy[team] if x["uid"] != uid]
                    if uid not in ban_uids[team]:
                        ban_uids[team].add(uid)
                        ban[team].append(c)
                elif zone in (rr.HAND, rr.EXTRA_DECK, rr.DECK):
                    if uid in gy_uids[team]:
                        gy_uids[team].remove(uid)
                        gy[team] = [x for x in gy[team] if x["uid"] != uid]
                    if uid in ban_uids[team]:
                        ban_uids[team].remove(uid)
                        ban[team] = [x for x in ban[team] if x["uid"] != uid]
                elif zone == rr.MOVING and c["card"] > 0:
                    dest = dest_zones.get(uid)
                    info = self._card_info(c["card"])
                    card_type = (info.card_type or "").lower() if info else ""
                    is_spell_trap = any(t in card_type for t in ("spell", "trap")) or (prev_z in (*rr.SPELL_ZONES, rr.FIELD_ZONE))
                    if dest == rr.BANISHED:
                        if uid not in ban_uids[team]:
                            if uid in gy_uids[team]:
                                gy_uids[team].remove(uid)
                                gy[team] = [x for x in gy[team] if x["uid"] != uid]
                            ban_uids[team].add(uid)
                            ban[team].append(dict(c, zone=rr.BANISHED))
                    elif is_discard_cost(info) or (dest == rr.GRAVE and not is_spell_trap):
                        if uid not in gy_uids[team] and uid not in ban_uids[team]:
                            entry = dict(c, zone=rr.GRAVE)
                            gy_uids[team].add(uid)
                            gy[team].append(entry)

            # 2. Despawnte Karten (MOVING / Spielfeld ins Grab nach Auflösung)
            for uid, last_c in prev_cards.items():
                if uid not in current_uids:
                    last_zone = last_c["zone"]
                    team = last_c["team"]
                    if team in gy:
                        if last_zone in (rr.MOVING, rr.FIELD_ZONE, *rr.SPELL_ZONES,
                                         *rr.MONSTER_ZONES, *rr.EXTRA_MONSTER_ZONES, rr.HAND):
                            if uid not in gy_uids[team] and uid not in ban_uids[team]:
                                entry = dict(last_c, zone=rr.GRAVE)
                                gy_uids[team].add(uid)
                                gy[team].append(entry)

            # 3. Aktivierungserkennung
            activating = None
            # 3a. Zauber/Falle auf dem Spielfeld aktiviert (neu aufs Feld gelegt oder aufgedeckt)
            for c in cards:
                if c["zone"] in (*rr.SPELL_ZONES, rr.FIELD_ZONE) and c["face"] == 1 and c["card"] > 0:
                    prev = prev_cards.get(c["uid"])
                    if prev is None or prev["zone"] != c["zone"] or prev["face"] == 0:
                        activating = c
                        break

            # 3b. Monster auf dem Spielfeld aktiviert (neu aufgedeckt/Flip)
            if activating is None:
                for c in cards:
                    if c["zone"] in (*rr.MONSTER_ZONES, *rr.EXTRA_MONSTER_ZONES) and c["face"] == 1 and c["card"] > 0:
                        prev = prev_cards.get(c["uid"])
                        if prev is not None and prev["zone"] == c["zone"] and prev["face"] == 0:
                            activating = c
                            break

            # 3c. Moving cards (Handtraps, Trigger-Effekte etc.)
            if activating is None:
                moving = [c for c in cards if c["zone"] == rr.MOVING and c["card"] > 0]
                if len(moving) == 1:
                    m = moving[0]
                    prev_z = last_zones.get(m["uid"])
                    dest_z = dest_zones.get(m["uid"])
                    # Keine Aktivierung bei Beschwörungs-Animationen, Extra-Deck-Karten oder
                    # Monstern/Karten, die das Spielfeld in den Friedhof / Verbannt verlassen (Zerstörung / Material)!
                    is_summon = (dest_z in (*rr.MONSTER_ZONES, *rr.EXTRA_MONSTER_ZONES))
                    from_extra = (prev_z == rr.EXTRA_DECK)
                    destroyed_from_field = (prev_z in (*rr.MONSTER_ZONES, *rr.EXTRA_MONSTER_ZONES,
                                                       *rr.SPELL_ZONES, rr.FIELD_ZONE)
                                           and dest_z in (rr.GRAVE, rr.BANISHED, None))
                    if not (is_summon or from_extra or destroyed_from_field):
                        activating = m

            # 3d. Hand reveal (z. B. Vorzeigen in der Hand)
            if activating is None:
                revealed = [c for c in cards if c["zone"] == rr.HAND and c["face"] == 1
                            and c["team"] != self.my_id and c["card"] > 0]
                if revealed:
                    prev = prev_cards.get(revealed[0]["uid"])
                    if prev is None or prev["face"] == 0:
                        activating = revealed[0]

            # 4. Ketten-Tracking (Master Duel Chain Links)
            if activating is not None:
                act_uid = activating["uid"]
                act_cid = activating["card"]
                act_team = activating["team"]
                if not any(x["uid"] == act_uid for x in current_chain):
                    cl_num = len(current_chain) + 1
                    current_chain.append({
                        "cl": cl_num,
                        "uid": act_uid,
                        "card": act_cid,
                        "team": act_team,
                        "start_frame": f_idx,
                    })
            else:
                cards_by_uid = {c["uid"]: c for c in cards}

                def is_item_resolved(x: dict) -> bool:
                    uid = x["uid"]
                    if uid not in current_uids:
                        return True
                    card_obj = cards_by_uid[uid]
                    zone = card_obj.get("zone", 0)
                    info = self._card_info(x.get("card", 0))
                    if must_leave_field_on_resolution(zone, info):
                        if zone not in (*rr.SPELL_ZONES, rr.FIELD_ZONE, rr.MOVING):
                            return True
                        return f_idx > x.get("start_frame", 0) + 15
                    return f_idx > x.get("start_frame", 0)

                if all(is_item_resolved(x) for x in current_chain):
                    current_chain = []

            self.frame_states.append({
                "gy": {0: list(gy[0]), 1: list(gy[1])},
                "banished": {0: list(ban[0]), 1: list(ban[1])},
                "activating": activating,
                "chain": list(current_chain),
                "field_zones": dict(last_field_zones),
                "last_zones": dict(last_zones),
                "dest_zones": dict(dest_zones),
            })
            prev_cards = {c["uid"]: c for c in cards}

    def get(self, frame_no: int) -> dict:
        if 0 <= frame_no < len(self.frame_states):
            return self.frame_states[frame_no]
        return {
            "gy": {0: [], 1: []},
            "banished": {0: [], 1: []},
            "activating": None,
            "chain": [],
            "field_zones": {},
            "last_zones": {},
            "dest_zones": {},
        }


class DuelView:
    def __init__(self, master: tk.Misc, db, matches: List[MatchEntry], index: int = 0,
                 on_close: Callable[[], None] = lambda: None, image_folder: str = card_images.FOLDER,
                 replay: bool = False):
        self.master = master
        self.want_replay = replay         # nur dann wird der Mitschnitt gezeigt, sonst nur die Decklisten
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
        self.font_bold = ("Helvetica", int(10 * s), "bold")
        self.font_small = ("Helvetica", int(8 * s))
        self.font_small_bold = ("Helvetica", int(8 * s), "bold")
        self.font_head = ("Helvetica", int(12 * s), "bold")
        self.font_result = ("Helvetica", int(20 * s), "bold")
        self.folder = image_folder
        self.photos: Dict[float, card_images.PhotoCache] = {}  # je Kartengröße ein Cache
        self.replay: Optional[Replay] = None
        self.tracker: Optional[ReplayStateTracker] = None
        self._pile_inspector: Optional[tk.Toplevel] = None
        self._starts: Optional[Dict[str, int]] = None      # Duell-ID → Start des Mitschnitts (erst bei replay=True)
        self.frame_no = 0
        self.mode = "decks"               # "replay" (Mitschnitt vorhanden) oder "decks"
        self._play_job = None
        self._passcodes: Optional[Dict[int, str]] = None   # Konami-ID → Passcode (fürs Bild)
        self._art: Dict[tuple, object] = {}                # gedrehte Kartenbilder
        self._tip_key: Optional[tuple] = None              # Karte, für die der Hinweis gerade steht
        self._info_cache: Dict[int, Optional[CardInfo]] = {}

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
            if self.want_replay and self._starts is None:  # einmal je Ansicht: jeder Mitschnitt gehört zu einem Match
                self._starts = self.db.replay_starts_by_match()
            start = (self._starts or {}).get(match.did)
            self.replay = self.db.replay_at(start) if start is not None else None
        except Exception:  # Datenbank gesperrt, alte Datenbank-Fassung …
            self.replay = None
        self._close_inspector()
        self.mode, self.frame_no = ("replay" if self.replay else "decks"), 0
        if self.replay:
            self.tracker = ReplayStateTracker(self.replay.frames, db=self.db, passcode_func=self.passcode,
                                          my_id=self.replay.my_id)
            self.controls.pack(fill=tk.X, before=self.canvas)
        else:
            self.tracker = None
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

    def _card_info(self, kid: int) -> Optional[CardInfo]:
        if not self.db or not kid:
            return None
        if kid in self._info_cache:
            return self._info_cache[kid]
        p = self.passcode(kid)
        info = self.db.info(p) if (self.db and p) else None
        self._info_cache[kid] = info
        return info

    def _is_activating(self, card: dict, state: dict, field_uids: set) -> bool:
        act = state.get("activating")
        if not act:
            return False
        if card["uid"] == act.get("uid"):
            return True
        if act.get("uid") not in field_uids:
            if card.get("card", 0) > 0 and card["card"] == act.get("card") and card.get("team") == act.get("team"):
                return True
        return False

    def _chain_link(self, card: dict, state: dict, field_uids: set) -> Optional[int]:
        chain = state.get("chain", [])
        for x in chain:
            if x.get("uid") == card["uid"]:
                return x.get("cl")
        for x in chain:
            if x.get("uid") not in field_uids:
                if card.get("card", 0) > 0 and card["card"] == x.get("card") and card.get("team") == x.get("team"):
                    return x.get("cl")
        return None

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
        s = self.s
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
        c.tag_bind(tag, "<Enter>", lambda e, cid=cid, x=x, y=y, h=h: self._tip(cid, count, x, y, h))
        c.tag_bind(tag, "<Leave>", lambda e: self._untip())

    def _untip(self) -> None:
        self._tip_key = None
        self.canvas.delete("tip")

    def _tip(self, cid: str, count: int, x: int, y: int, h: int, plain: bool = False) -> None:
        """Namenshinweis unter (sonst über) der Karte (x, y, Höhe h); nie auf der Karte und nie anklickbar."""
        c, s = self.canvas, self.s
        key = (cid, count, x, y)
        if key == self._tip_key and c.find_withtag("tip"):
            return  # steht schon für diese Karte (kein Neuzeichnen bei wiederholtem <Enter>)
        c.delete("tip")
        self._tip_key = key
        text = self.names.get(cid, cid) if plain else f"{count}× {self.names.get(cid, cid)}"
        # state="disabled": der Hinweis wird nie "current item" – sonst Enter/Leave-Endlosschleife unter dem Zeiger
        label = c.create_text(x, y + h + int(6 * s), text=text, fill=TEXT, font=self.font, anchor="nw",
                              tags=("tip",), state="disabled")
        x0, y0, x1, y1 = c.bbox(label)
        if y1 + int(4 * s) > self.size[1]:  # unten kein Platz: über die Karte
            c.move(label, 0, y - int(6 * s) - y1)
            x0, y0, x1, y1 = c.bbox(label)
        if x1 > self.size[0] - int(10 * s):  # rechts nicht aus dem Feld hinaus
            c.move(label, self.size[0] - int(10 * s) - x1, 0)
            x0, y0, x1, y1 = c.bbox(label)
        pad = int(4 * s)
        c.tag_lower(c.create_rectangle(x0 - pad, y0 - pad, x1 + pad, y1 + pad, fill=PANEL, outline=GOLD,
                                       tags=("tip",), state="disabled"), label)

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
        if zone in PILES:
            return PILES[zone][0 if mine else 1]
        return None

    def _draw_replay(self) -> None:
        c, s = self.canvas, self.s
        c.delete("all")
        replay = self.replay
        frame = replay.frames[self.frame_no]
        state = self.tracker.get(self.frame_no) if self.tracker else {
            "gy": {0: [], 1: []}, "banished": {0: [], 1: []}, "activating": None, "chain": []
        }
        w, h = self.size
        cw, ch, cols, rows = self._geometry()
        c.create_rectangle(int(4 * s), int(4 * s), w - int(4 * s), h - int(4 * s), fill=FIELD, outline=FIELD_EDGE)
        c.create_rectangle(cols[0] - int(4 * s), rows["opp_hand"] - int(4 * s), cols[6] + cw + int(4 * s),
                           rows["emz"] - int(3 * s), fill=OPP_HALF, outline="")
        for (row, col), text in BOARD_LABELS.items():
            opp = row.startswith("opp") or (row == "emz" and col == 0)
            x, y = cols[col], rows[row]
            c.create_rectangle(x, y, x + cw, y + ch, fill=OPP_ZONE if opp else ZONE,
                               outline=OPP_ZONE_EDGE if opp else ZONE_EDGE)
            c.create_text(x + cw // 2, y + ch // 2, text=text, fill=ZONE_TEXT, font=self.font_small, width=cw - 4,
                          justify=tk.CENTER)
            # Klick-Bereiche für leeren Friedhof / Verbannt binden
            if (row, col) in (("my_m", 6), ("opp_m", 0)):
                pile_team = replay.my_id if (row, col) == ("my_m", 6) else 1 - replay.my_id
                pile_tag = f"bg_gy_{pile_team}"
                c.create_rectangle(x, y, x + cw, y + ch, outline="", fill="", tags=(pile_tag,))
                c.tag_bind(pile_tag, "<Button-1>", lambda e, t=pile_team: self._open_pile_inspector("Friedhof", state["gy"][t], t))
            elif (row, col) in (("emz", 6), ("emz", 0)):
                pile_team = replay.my_id if (row, col) == ("emz", 6) else 1 - replay.my_id
                pile_tag = f"bg_ban_{pile_team}"
                c.create_rectangle(x, y, x + cw, y + ch, outline="", fill="", tags=(pile_tag,))
                c.tag_bind(pile_tag, "<Button-1>", lambda e, t=pile_team: self._open_pile_inspector("Verbannt", state["banished"][t], t))

        cards = [dict(zip(CARD_KEYS, row)) for row in frame["cards"]]
        field_uids = {c["uid"] for c in cards if c["zone"] in (*rr.MONSTER_ZONES, *rr.EXTRA_MONSTER_ZONES, *rr.SPELL_ZONES, rr.FIELD_ZONE)}
        piles: Dict[Tuple[str, int], List[dict]] = {}
        hands: Dict[bool, List[dict]] = {True: [], False: []}
        field_zones = state.get("field_zones", {})
        for card in cards:
            mine = card["team"] == replay.my_id
            if card["zone"] == rr.HAND:
                hands[mine].append(card)
                continue
            if card["zone"] == rr.MOVING:
                info = self._card_info(card["card"]) if card.get("card") else None
                card_type = (info.card_type or "").lower() if info else ""
                is_extra = any(t in card_type for t in ("fusion", "synchro", "xyz", "link"))
                prev_z = state.get("last_zones", {}).get(card["uid"], field_zones.get(card["uid"]))
                dest_z = state.get("dest_zones", {}).get(card["uid"])

                # Extra-Deck-Karten und Karten vom Spielfeld dürfen NIEMALS auf der Hand landen!
                if is_discard_cost(info) or dest_z == rr.GRAVE or prev_z in (*rr.MONSTER_ZONES, *rr.EXTRA_MONSTER_ZONES):
                    continue
                if dest_z == rr.BANISHED or (prev_z == rr.EXTRA_DECK and is_extra):
                    continue

                if any(t in card_type for t in ("spell", "trap")) or (prev_z in (*rr.SPELL_ZONES, rr.FIELD_ZONE)):
                    # Normale Zauber und Fallen liegen auf dem Spielfeld, bis der Effekt resolved ist!
                    if prev_z in (*rr.SPELL_ZONES, rr.FIELD_ZONE):
                        spot_zone = prev_z
                    elif "field" in card_type:
                        spot_zone = rr.FIELD_ZONE
                    else:
                        idx = card.get("index", 0)
                        spot_zone = (7 + idx) if 0 <= idx < 5 else 9
                    spot = self.place(spot_zone, mine)
                    if spot is not None:
                        piles.setdefault(spot, []).append(card)
                    continue

                # Nur Monster, die tatsächlich aus der Hand kommen und nicht abgeworfen/zerstört werden, bleiben in der Hand
                if prev_z == rr.HAND and not is_extra:
                    hands[mine].append(card)
                continue
            spot = self.place(card["zone"], mine)
            if spot is None:
                continue
            if card["zone"] in (rr.GRAVE, rr.DECK, rr.EXTRA_DECK, rr.BANISHED, rr.FIELD_ZONE):
                piles.setdefault(spot, []).append(card)
                continue
            row, col = spot
            act = self._is_activating(card, state, field_uids)
            cl_info = self._chain_link(card, state, field_uids)
            self._board_card(cols[col], rows[row], cw, ch, card, mine, stats=True, activation=act, chain_link=cl_info)

        # Stapel (Deck, Extra Deck, Feldzauber)
        for (row, col), stack in piles.items():
            top = max(stack, key=lambda k: k["index"])
            if top["zone"] in (rr.GRAVE, rr.BANISHED):
                continue  # Friedhof und Verbannt werden kumulativ gezeichnet
            act = self._is_activating(top, state, field_uids)
            cl_info = self._chain_link(top, state, field_uids)
            self._board_card(cols[col], rows[row], cw, ch, top, top["team"] == replay.my_id, activation=act, chain_link=cl_info)
            if top["zone"] == rr.EXTRA_DECK:
                self._badge(cols[col] + cw, rows[row] + ch, str(top["index"] + 1))

        # Friedhof & Verbannt (kumulativ über alle Frames verfolgt)
        for team in (0, 1):
            mine = (team == replay.my_id)
            # Friedhof
            gy_stack = state["gy"][team]
            gy_spot = self.place(rr.GRAVE, mine)
            if gy_spot is not None:
                grow, gcol = gy_spot
                gx, gy_y = cols[gcol], rows[grow]
                gy_tag = f"gy_card_{team}"
                if gy_stack:
                    top_gy = gy_stack[-1]
                    act = (state["activating"] is not None and top_gy["uid"] == state["activating"]["uid"])
                    cl_info = next((x["cl"] for x in state["chain"] if x["uid"] == top_gy["uid"]), None)
                    self._board_card(gx, gy_y, cw, ch, top_gy, mine, pile_tag=gy_tag, activation=act, chain_link=cl_info)
                    self._badge(gx + cw, gy_y + ch, str(len(gy_stack)))
                c.tag_bind(gy_tag, "<Button-1>", lambda e, t=team, s_list=list(gy_stack): self._open_pile_inspector("Friedhof", s_list, t))
                c.tag_bind(gy_tag, "<Enter>", lambda e, n=len(gy_stack), m=mine: self._tip_text(f"{'Dein' if m else 'Gegner-'}Friedhof: {n} Karte(n) – Klick für Details"))
                c.tag_bind(gy_tag, "<Leave>", lambda e: self._untip())

            # Verbannt
            ban_stack = state["banished"][team]
            ban_spot = self.place(rr.BANISHED, mine)
            if ban_spot is not None:
                brow, bcol = ban_spot
                bx, by_y = cols[bcol], rows[brow]
                ban_tag = f"ban_card_{team}"
                if ban_stack:
                    top_ban = ban_stack[-1]
                    act = (state["activating"] is not None and top_ban["uid"] == state["activating"]["uid"])
                    cl_info = next((x["cl"] for x in state["chain"] if x["uid"] == top_ban["uid"]), None)
                    self._board_card(bx, by_y, cw, ch, top_ban, mine, pile_tag=ban_tag, activation=act, chain_link=cl_info)
                    self._badge(bx + cw, by_y + ch, str(len(ban_stack)))
                c.tag_bind(ban_tag, "<Button-1>", lambda e, t=team, s_list=list(ban_stack): self._open_pile_inspector("Verbannt", s_list, t))
                c.tag_bind(ban_tag, "<Enter>", lambda e, n=len(ban_stack), m=mine: self._tip_text(f"{'Deine' if m else 'Gegner-'}Verbannten: {n} Karte(n) – Klick für Details"))
                c.tag_bind(ban_tag, "<Leave>", lambda e: self._untip())

        # Handkarten
        for mine, hand in hands.items():
            hand.sort(key=lambda k: k["index"])
            row = rows["my_hand" if mine else "opp_hand"]
            step = min(cw + int(4 * s), (cols[6] - cols[0]) // max(1, len(hand)))
            left = (w - (step * (len(hand) - 1) + cw)) // 2
            for i, card in enumerate(hand):
                act = (state["activating"] is not None and card["uid"] == state["activating"]["uid"])
                cl_info = next((x["cl"] for x in state["chain"] if x["uid"] == card["uid"]), None)
                self._board_card(left + i * step, row, cw, ch, card, mine, hand=True, activation=act, chain_link=cl_info)

        # Master Duel Ketten-Leiste (Chain Sequence)
        chain = state["chain"]
        if chain:
            links = []
            for item in chain:
                cl_num = item["cl"]
                cid = item["card"]
                p = self.passcode(cid)
                name = self.names.get(p) or f"Karte #{cid}"
                is_mine = (item["team"] == replay.my_id)
                links.append(f"[{cl_num}] {name} ({'Du' if is_mine else 'Gegner'})")
            chain_str = "  ➔  ".join(links)
            bw = int(min(w - 40, max(260 * s, len(chain_str) * 7.5 * s + 30 * s)))
            bx = (w - bw) // 2
            by = rows["opp_s"] + ch + int(4 * s)
            bh = int(22 * s)
            c.create_rectangle(bx, by, bx + bw, by + bh, fill="#0d1b2a", outline=GOLD, width=int(1.5 * s))
            c.create_text(bx + bw // 2, by + bh // 2, text=f"⛓ KETTE:  {chain_str}", fill=GOLD, font=self.font_small_bold)

        self._lp_and_phase(frame, cols, rows, cw, ch)
        act_card = state["activating"]
        step_text = f"Schritt {self.frame_no + 1} / {len(replay.frames)}"
        if frame.get("turn"):
            step_text += f"  ·  {frame.get('turn')}"
        if frame.get("phase"):
            step_text += f"  ·  {frame.get('phase')}"
        if act_card is not None:
            act_p = self.passcode(act_card["card"])
            act_name = self.names.get(act_p) or f"Karte #{act_card['card']}"
            step_text += f"  ·  ⚡ {act_name}"
        self.step_label.config(text=step_text)

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
                    hand: bool = False, activation: bool = False, chain_link: Optional[int] = None,
                    pile_tag: Optional[str] = None) -> None:
        """Karte im Feld: Gegner um 180° gedreht, Verteidigung quer, verdeckt als Rückseite."""
        c, s = self.canvas, self.s
        defense = not hand and card["zone"] in (*rr.MONSTER_ZONES, *rr.EXTRA_MONSTER_ZONES) and not card["attack"]
        angle = (0 if mine else 180) + (90 if defense else 0)
        passcode = self.passcode(card["card"]) if card["card"] else None
        shown = passcode if card["face"] or (mine and hand) or activation or pile_tag else None
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
        tags = (tag, pile_tag) if pile_tag else (tag,)
        c.create_rectangle(x, y, x + w, y + h, outline="", fill="", tags=tags)
        if shown and not pile_tag:
            c.tag_bind(tag, "<Enter>", lambda e, p=shown, x=x, y=y, h=h: self._tip(p, 1, x, y, h, plain=True))
            c.tag_bind(tag, "<Leave>", lambda e: self._untip())
        if stats and card["face"] and card["zone"] in (*rr.MONSTER_ZONES, *rr.EXTRA_MONSTER_ZONES) and card["atk"] >= 0:
            c.create_text(cx, y + h - int(2 * s), text=f"{card['atk']} / {card['def']}", fill=GOLD,
                          font=self.font_small_bold, anchor="s")

        # Aktivierungseffekt: Leuchtrahmen und "⚡ AKTIV" Badge
        if activation:
            c.create_rectangle(x - int(3 * s), y - int(3 * s), x + w + int(3 * s), y + h + int(3 * s),
                               outline=GOLD, width=int(2 * s))
            c.create_rectangle(x - int(1 * s), y - int(1 * s), x + w + int(1 * s), y + h + int(1 * s),
                               outline="#00e5ff", width=1)
            badgew, badgeh = int(48 * s), int(15 * s)
            c.create_rectangle(x, y, x + badgew, y + badgeh, fill="#8a1c1c", outline=GOLD)
            c.create_text(x + badgew // 2, y + badgeh // 2, text="⚡ AKTIV", fill=GOLD, font=self.font_small_bold)

        # Kettenglied-Badge (Master Duel Style): z.B. [CL 1], [CL 2]
        if chain_link is not None:
            clw, clh = int(36 * s), int(16 * s)
            rx, ry = x + w - clw, y
            c.create_rectangle(rx, ry, rx + clw, ry + clh, fill="#0d1b2a", outline="#00e5ff", width=int(1.5 * s))
            c.create_text(rx + clw // 2, ry + clh // 2, text=f"CL {chain_link}", fill="#00e5ff", font=self.font_small_bold)

    def _tip_text(self, text: str) -> None:
        """Einfacher Text-Hinweis (z. B. für Friedhof / Verbannt)."""
        c, s = self.canvas, self.s
        c.delete("tip")
        label = c.create_text(self.size[0] // 2, self.size[1] - int(20 * s), text=text,
                              fill=GOLD, font=self.font_bold, anchor="s", tags=("tip",), state="disabled")
        x0, y0, x1, y1 = c.bbox(label)
        pad = int(4 * s)
        c.tag_lower(c.create_rectangle(x0 - pad, y0 - pad, x1 + pad, y1 + pad, fill=PANEL, outline=GOLD,
                                       tags=("tip",), state="disabled"), label)

    def _close_inspector(self) -> None:
        if self._pile_inspector is not None:
            try:
                self._pile_inspector.destroy()
            except Exception:
                pass
            self._pile_inspector = None

    def _open_pile_inspector(self, title: str, card_list: List[dict], team: int) -> None:
        self._close_inspector()
        s = self.s
        replay = self.replay
        is_mine = (team == replay.my_id)
        team_name = replay.names[team] if team < len(replay.names) and replay.names[team] else ("Du" if is_mine else "Gegner")

        win = tk.Toplevel(self.win, bg="#101018")
        win.overrideredirect(True)
        win.wm_attributes("-topmost", True)
        self._pile_inspector = win

        w = min(int(460 * s), self.size[0] - int(40 * s))
        h = min(int(520 * s), self.size[1] - int(20 * s))
        rx = max(0, self.win.winfo_rootx() + (self.win.winfo_width() - w) // 2)
        ry = max(0, self.win.winfo_rooty() + (self.win.winfo_height() - h) // 2)
        win.geometry(f"{w}x{h}+{rx}+{ry}")

        header = tk.Frame(win, bg=PANEL, padx=int(10 * s), pady=int(6 * s))
        header.pack(fill=tk.X)
        _make_draggable(win, header)
        tk.Label(header, text=f"◆ {title.upper()} ({len(card_list)})  ·  {team_name}",
                 fg=GOLD, bg=PANEL, font=self.font_head).pack(side=tk.LEFT)
        RoundedButton(header, text="✕", command=self._close_inspector, bg="#cc0000", border="#ff8a80",
                      font=("Helvetica", int(9 * s), "bold"), padx=int(9 * s), pady=int(2 * s),
                      radius=int(6 * s)).pack(side=tk.RIGHT)

        body = tk.Frame(win, bg=FIELD)
        body.pack(fill=tk.BOTH, expand=True, padx=int(6 * s), pady=int(6 * s))

        if not card_list:
            tk.Label(body, text=f"Keine Karten im {title}.", fg=MUTED, bg=FIELD,
                     font=self.font).pack(expand=True)
        else:
            canvas = tk.Canvas(body, bg=FIELD, highlightthickness=0, bd=0)
            scroll = DarkScrollbar(body, command=canvas.yview, bg=FIELD, scale=s)
            canvas.config(yscrollcommand=scroll.set)
            scroll.pack(side=tk.RIGHT, fill=tk.Y)
            canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

            inner = tk.Frame(canvas, bg=FIELD)
            inner_id = canvas.create_window(0, 0, window=inner, anchor="nw")
            inner.bind("<Configure>", lambda e: canvas.config(scrollregion=canvas.bbox("all")))
            canvas.bind("<Configure>", lambda e: canvas.itemconfigure(inner_id, width=e.width))
            canvas.bind("<MouseWheel>", lambda e: canvas.yview_scroll(int(-e.delta / 120) * 2, "units"))

            # Zuletzt hineingelegte Karte oben
            for idx, c_dict in enumerate(reversed(card_list)):
                cid = c_dict["card"]
                p = self.passcode(cid)
                name = self.names.get(p) or f"Karte #{cid}"
                card_num = len(card_list) - idx

                row_frame = tk.Frame(inner, bg=PANEL, bd=1, relief=tk.SOLID, padx=int(6 * s), pady=int(4 * s))
                row_frame.pack(fill=tk.X, pady=int(3 * s), padx=int(4 * s))

                tk.Label(row_frame, text=f"#{card_num}", fg=GOLD, bg=PANEL, font=self.font_small_bold, width=4).pack(side=tk.LEFT)

                thumb_w, thumb_h = int(28 * s), int(41 * s)
                img = self._card_image(p, thumb_w, thumb_h, 0) if p else None
                if img:
                    lbl_img = tk.Label(row_frame, image=img, bg=PANEL)
                    lbl_img.image = img
                    lbl_img.pack(side=tk.LEFT, padx=(0, int(6 * s)))

                info_frame = tk.Frame(row_frame, bg=PANEL)
                info_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
                tk.Label(info_frame, text=name, fg=TEXT, bg=PANEL, font=self.font_bold, anchor="w").pack(fill=tk.X)

                info_text = ""
                if self.db is not None and p:
                    try:
                        c_info = self.db.info(p)
                        if c_info:
                            parts = [c_info.card_type]
                            if c_info.level:
                                parts.append(f"Stufe {c_info.level}")
                            if c_info.atk is not None and c_info.atk >= 0:
                                parts.append(f"{c_info.atk} / {c_info.def_}")
                            info_text = " · ".join(parts)
                    except Exception:
                        pass
                if info_text:
                    tk.Label(info_frame, text=info_text, fg=MUTED, bg=PANEL, font=self.font_small, anchor="w").pack(fill=tk.X)

        apply_frame(win)
        no_activate(win)
        win.bind("<Escape>", lambda e: self._close_inspector())

    def _card_image(self, passcode: str, w: int, h: int, angle: int):
        key = (passcode, w, h, angle)
        if key not in self._art:
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
            alpha = 1.0 if visible else 0.0
            self.win.wm_attributes("-alpha", alpha)
            if self._pile_inspector is not None:
                try:
                    self._pile_inspector.wm_attributes("-alpha", alpha)
                except tk.TclError:
                    pass

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._close_inspector()
        self._stop_play()
        if self._poll_job is not None:
            try:
                self.win.after_cancel(self._poll_job)
            except tk.TclError:
                pass
        self.win.destroy()
        self.on_close()
