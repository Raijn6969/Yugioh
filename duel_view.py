"""
Duell-Übersicht (Klick auf ein Match im Winrate-Fenster): ein gespeichertes Duell auf einem Spielfeld wie in der
Schattenarena der Factory – Gegner oben (rötlich), du unten, in der Mitte das Ergebnis. Ohne die Spalten links und
rechts, damit das Feld selbst groß ist.

Gibt es zum Match einen Replay-Mitschnitt (replay_recorder: einmal das Replay in Master Duel angesehen) und wurde
die Ansicht mit replay=True geöffnet ("▶ Replay" in der Match-Zeile), zeigt das Feld das Duell Schritt für Schritt:
Karten in ihren Zonen (Gegner gedreht, Verteidigung quer, verdeckt als Rückseite), Hände, oberste Karten von
Friedhof/Deck/Extra Deck/Verbannt mit Anzahl, Lebenspunkte und Zug/Phase. Bedienung:
Anfang, Schritt zurück/vor, Abspielen, Ende; Mitschnitte mit Duell-Protokoll (frame["log"]) zeigen echte Ketten,
Kosten, Ziele, Negationen, Zerstörungen und Angriffe (replay_steps), ältere die Vermutung des Trackers;
"Decks" schaltet auf die Deck-Ansicht; "Als HTML" exportiert das Replay als eine Datei zum Verschicken
(replay_export, Gegnername standardmäßig ausgeblendet).

Ohne Mitschnitt stehen auf jeder Hälfte das Deck des Spielers (jede Karte einmal, mit Anzahl; das Gegner-Deck kommt
aus der Match History), an den Seiten Deck- und Extra-Deck-Stapel mit Kartenzahl. Mit ◀/▶ blättert man durch die
Matches. Alles wird auf eine Zeichenfläche gemalt (wie card_grid) – schnell, kein Ruckeln beim Blättern.
"""

import math
import os
import subprocess
import threading
import time
import tkinter as tk
from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple
from PIL import Image, ImageTk

import card_images
import replay_recorder as rr
import replay_steps as rs
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
OPP_HAND = 0.62          # Gegnerhand: so hoch (und breit) wie eine Karte, wie in der Factory
CARD = (50, 73)          # Kartengröße bei 1080p (wie die Karten in Master Duel: 59 × 86 mm)
MIN_SCALE = 0.55         # so weit werden die Karten verkleinert, wenn ein Deck viele verschiedene hat …
MAX_SCALE = 1.6          # … und so weit vergrößert, wenn Platz ist
FIELD = "#141e38"        # Spielfeld (Schattenarena: (20, 30, 56))
FIELD_EDGE = "#3c508c"
OPP_HALF = "#281622"     # Hälfte des Gegners rötlich
ZONE, ZONE_EDGE, ZONE_TEXT = "#1a284c", "#30468a", "#5a6eaa"
OPP_ZONE, OPP_ZONE_EDGE = "#2c1a28", "#823c50"
CARD_BACK = "#7a4828"
SHIELD_BG = "#101018"    # Schilder über den Karten ("AKTIVIERT", "gesetzt" …)
ACT_GOLD, ACT_RED = "#ffd66e", "#ff5a5a"     # Aktivierung; rot: Gegner antwortet in eine Kette
SUMMON_BLUE, LIGHT_BLUE, ADD_BLUE = "#aadcff", "#a0e6ff", "#96e1ff"   # Beschwörung, gesetzt, zur Hand hinzugefügt
PLAQUE = "#f0c450"       # Kettenglied-Nummer auf der Karte
RESOLVING = "#ff7a7a"    # Kettenglied, das gerade auflöst ("KETTE LÖST AUF"), Rahmen von Ziel/Negation/Zerstörung
LP_DOWN, LP_UP = "#ff6464", "#78e68c"
FRAME_MS = 33            # Animation: ein Bild alle 33 ms
LEFT_ROOM = 220          # Platz links vom Feld für Schritt-Text und Ketten-Leiste (bei 1080p)
SPEEDS = (0.5, 1.0, 1.5, 2.0, 4.0)   # Wiedergabe-Tempo, der Knopf schaltet der Reihe nach weiter
STEP_ROW = "#463a18"     # aktuelle Zeile der Schrittliste

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
NO_STATE = {"gy": {0: [], 1: []}, "banished": {0: [], 1: []}, "activating": None, "chain": []}
# Stapel mit Klick-Fenster: Schlüssel im Tracker, Tag-Vorspann, Titel, "Dein"/"Gegner-" davor, Bezeichnung im Hinweis
PILE_KINDS = {"gy": ("gy_card", "Friedhof", "Dein ", "Friedhof"),
    "banished": ("ban_card", "Verbannt", "Deine ", "Verbannten")}


class Item(NamedTuple):
    """Eine Karte im Replay-Bild: wo sie in einem Schritt liegt und zu welcher Gruppe sie gehört."""
    card: dict
    x: int
    y: int
    mine: bool
    group: str            # "board" (Monster-/Zauber-/Fallenzone), "hand", "pile" (Deck, Extra Deck, Feldzauber),
                          # "grave", "banished"


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
                    elif dest == rr.GRAVE and (is_discard_cost(info) or not is_spell_trap):
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
        self.speed = 1.0                  # Wiedergabe-Tempo (0,5× … 4×)
        self.steps: List[rs.Step] = []    # Art/Text/Dauer je Frame (replay_steps)
        self.playing = False
        self._job = None                  # der einzige after-Job für Animation und Abspielen
        self._anim: Optional[dict] = None  # laufende Schritt-Animation: prev, t0, dur
        self._next_at = 0.0               # Abspielen: ab dieser Zeit beginnt der nächste Schritt
        self._scenes: Dict[int, tuple] = {}  # Frame → (Karten mit Position, Stapel)
        self._bg_ok = False               # Zonen sind gezeichnet (ändern sich zwischen Schritten nicht)
        self._bg_max = 0                  # Canvas-Elemente darüber sind je Bild neu gemalt
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
        for key in ("Left", "Right", "space", "Home", "End"):  # nur mit Tastaturfokus (das Fenster nimmt ihn nicht)
            self.win.bind(f"<{key}>", lambda e, k=key: self.key(k))
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
        for key, text, command in (("first", "⏮", lambda: self.manual(0, False)),
                                   ("back", "◀", lambda: self.manual(self.frame_no - 1)),
                                   ("play", "▶ Abspielen", self.toggle_play),
                                   ("next", "▶", lambda: self.manual(self.frame_no + 1)),
                                   ("last", "⏭", lambda: self.manual(len(self.replay.frames) - 1, False)),
                                   ("speed", "1×", self._cycle_speed)):
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
        self.body = tk.Frame(self.win, bg=BG)
        self.body.pack(fill=tk.BOTH, expand=True)
        self.side_w = int(260 * s) if self.want_replay else 0
        self.side = tk.Frame(self.body, bg=PANEL, width=self.side_w)   # Schrittliste (wie die Factory)
        self.steplist = tk.Canvas(self.side, bg=PANEL, highlightthickness=0, bd=0)
        self.step_scroll = DarkScrollbar(self.side, command=self.steplist.yview, bg=PANEL, scale=s)
        self.steplist.config(yscrollcommand=self.step_scroll.set)
        if self.want_replay:
            self.side.pack(side=tk.RIGHT, fill=tk.Y)
            self.side.pack_propagate(False)
            self.step_scroll.pack(side=tk.RIGHT, fill=tk.Y)
            self.steplist.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.steplist.bind("<Button-1>", self._click_step)
        self.steplist.bind("<MouseWheel>", lambda e: self.steplist.yview_scroll(int(-e.delta / 120) * 2, "units"))
        self.canvas = tk.Canvas(self.body, bg=BG, highlightthickness=0, bd=0)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

    def _place(self) -> None:
        s = self.s
        screen_w, screen_h = self.master.winfo_screenwidth(), self.master.winfo_screenheight()
        h = screen_h - 80
        # Zeichenfläche ohne Kopfzeile, Replay-Bedienung und Schrittliste
        h_canvas, w_max = h - int(76 * s), screen_w - 40 - self.side_w
        left = self._field_fit(w_max, h_canvas)[4]
        w = min(w_max, int(LEFT_ROOM * s) + w_max - left)   # links Platz für Text, das Feld nutzt den Rest
        self.size = (w, h_canvas)
        w += self.side_w
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
        self._stop_play(draw=False)
        self._scenes, self._bg_ok, self.steps = {}, False, []
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
            self.controls.pack(fill=tk.X, before=self.body)
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
        if self.replay:
            self.steps = rs.build_steps(self.replay.frames, self.tracker.frame_states, self.replay.my_id,
                                        lambda kid: self.names.get(self.passcode(kid)), self._card_type)
        self._build_steplist()
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

    def _card_type(self, kid: int) -> str:
        info = self._card_info(kid)
        return (info.card_type or "") if info else ""

    def manual(self, frame_no: int, animate: bool = True) -> None:
        """Knopf/Taste: Abspielen anhalten und springen (ein Schritt vor wird animiert)."""
        self._stop_play(draw=False)
        self.goto(frame_no, animate)

    def goto(self, frame_no: int, animate: bool = True) -> None:
        """Zu einem Schritt: nur "ein Schritt vor" wird animiert, alles andere springt (wie Factory goto)."""
        if not self.replay:
            return
        if frame_no >= len(self.replay.frames):
            self._stop_play()  # Ende erreicht
            return
        if frame_no < 0:
            return
        self._finish_anim(draw=False)
        before, self.frame_no = self.frame_no, frame_no
        if animate and self.mode == "replay" and frame_no == before + 1 and self.steps[frame_no].dur > 0:
            self._anim = {"prev": before, "t0": time.perf_counter(), "dur": self.steps[frame_no].dur / self.speed}
            self._set_label(frame_no)
            self._ensure_job()
            self.draw()
        else:
            if not self.playing:
                self._cancel_job()
            self.draw()

    def key(self, name: str) -> None:
        """← / → Schritt, Leertaste Abspielen/Pause, Pos1/Ende springen."""
        if self.mode != "replay" or not self.replay:
            return
        if name == "space":
            self.toggle_play()
        else:
            target = {"Left": self.frame_no - 1, "Right": self.frame_no + 1, "Home": 0,
                      "End": len(self.replay.frames) - 1}[name]
            self.manual(target, animate=name in ("Left", "Right"))

    def _cycle_speed(self) -> None:
        """0,5× → 1× → 1,5× → 2× → 4× (wie Factory _cycle_speed); eine laufende Animation wird mit umgerechnet."""
        new = SPEEDS[(SPEEDS.index(self.speed) + 1) % len(SPEEDS)] if self.speed in SPEEDS else 1.0
        a = self._anim
        if a is not None:
            now = time.perf_counter()
            done = (now - a["t0"]) / a["dur"]
            a["dur"] = self.steps[self.frame_no].dur / new
            a["t0"] = now - done * a["dur"]
        self.speed = new
        self.ctrl_buttons["speed"].config(text=f"{new:g}×".replace(".", ","))

    def _build_steplist(self) -> None:
        """Schrittliste rechts: nur Schritte mit sichtbarer Änderung, je Zeile Zug/Phase klein und der Schritt-Text."""
        c, s = self.steplist, self.s
        c.delete("all")
        self._rows: List[Tuple[int, int, int]] = []   # (Frame, oberer, unterer Rand)
        self._row_bg: Dict[int, int] = {}
        self._marked: Optional[int] = None
        c.yview_moveto(0)
        width = self.side_w - int(12 * s)
        y, pad = int(4 * s), int(6 * s)
        for n, step in enumerate(self.steps if self.replay else ()):
            if not step.text:   # auch gleitende Schritte ohne Text (Beschwörung steht schon im Protokoll)
                continue
            frame = self.replay.frames[n]
            when_ = "  ·  ".join(x for x in (frame.get("turn"), frame.get("phase")) if x)
            head = c.create_text(pad, y + int(3 * s), text=when_, anchor="nw", fill=MUTED, font=self.font_small)
            opp = step.focus is not None and step.focus.team != self.replay.my_id
            body = c.create_text(pad, c.bbox(head)[3], text=step.text, anchor="nw", width=width - 2 * pad,
                                 fill="#dc96a0" if opp else TEXT, font=self.font_small_bold)
            bottom = c.bbox(body)[3] + int(4 * s)
            bg = c.create_rectangle(0, y, width, bottom, fill=PANEL, outline="")
            c.tag_lower(bg)
            self._rows.append((n, y, bottom))
            self._row_bg[n] = bg
            y = bottom + int(2 * s)
        c.config(scrollregion=(0, 0, width, y))

    def _click_step(self, event) -> None:
        """Klick in die Schrittliste: dorthin springen (ohne Animation)."""
        if not self.replay:
            return
        y = self.steplist.canvasy(event.y)
        for n, top, bottom in self._rows:
            if top <= y <= bottom:
                self.manual(n, animate=False)
                return

    def _mark_step(self, n: int) -> None:
        """Zeile des Schritts n (ohne eigene Zeile: die letzte davor) markieren und ins Bild holen."""
        row = None
        for entry in self._rows:
            if entry[0] > n:
                break
            row = entry
        key = row[0] if row else None
        if key == self._marked:
            return
        c = self.steplist
        if self._marked in self._row_bg:
            c.itemconfigure(self._row_bg[self._marked], fill=PANEL)
        self._marked = key
        if row is None:
            return
        c.itemconfigure(self._row_bg[key], fill=STEP_ROW)
        height, total = c.winfo_height(), int(c.cget("scrollregion").split()[3])
        if row[1] < c.canvasy(0) or row[2] > c.canvasy(height):
            c.yview_moveto(max(0, row[1] - height // 3) / max(1, total))

    def toggle_play(self) -> None:
        if self.playing:
            self._stop_play()
            return
        if not self.replay:
            return
        if self.frame_no >= len(self.replay.frames) - 1:
            self._finish_anim(draw=False)
            self.frame_no = 0
        self.mode = "replay"
        self.playing = True
        self._next_at = 0.0
        self.ctrl_buttons["play"].config(text="⏸ Pause")
        self.draw()
        self._ensure_job()

    def _ensure_job(self) -> None:
        """Genau ein Job: läuft schon einer, wird kein zweiter angelegt."""
        if self._job is None and not self.closed:
            self._job = self.win.after(FRAME_MS, self._loop)

    def _loop(self) -> None:
        """Ein Takt: Animation weiterzeichnen oder beenden, beim Abspielen den nächsten Schritt starten."""
        self._job = None
        if self.closed or not self.replay:
            return
        now = time.perf_counter()
        if self._anim is not None:
            if now - self._anim["t0"] >= self._anim["dur"]:
                self._finish_anim(draw=True)
                last = self.frame_no >= len(self.replay.frames) - 1
                if self.playing and last:
                    self._stop_play()
                elif self.playing:
                    still = not self.steps[self.frame_no].text
                    self._next_at = now + (0.0 if still else rs.STEP_PAUSE / self.speed)
            else:
                self._draw_replay()
        if self._anim is None and self.playing and now >= self._next_at:
            self._advance()
        if self._anim is not None or self.playing:
            self._ensure_job()

    def _advance(self) -> None:
        """Abspielen: der nächste sichtbare Schritt (Schritte ohne sichtbare Änderung werden übersprungen)."""
        last = len(self.replay.frames) - 1
        i = self.frame_no + 1
        while i <= last and self.steps[i].dur == 0:
            i += 1
        if i > last:
            self.frame_no = last
            self._stop_play()
            return
        self.frame_no = i - 1
        self.goto(i)

    def _finish_anim(self, draw: bool) -> None:
        """Laufende Animation beenden: der Endzustand des Schritts steht (gezeichnet, wenn draw)."""
        if self._anim is None:
            return
        self._anim = None
        if draw and not self.closed and self.mode == "replay" and self.replay:
            self.draw()

    def _cancel_job(self) -> None:
        if self._job is not None:
            try:
                self.win.after_cancel(self._job)
            except tk.TclError:
                pass
        self._job = None

    def _stop_play(self, draw: bool = True) -> None:
        self.playing = False
        self._cancel_job()
        self._finish_anim(draw)
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
        self._stop_play(draw=False)
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
        self._bg_ok = False
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
        """7 Spalten × 7 Reihen (Hand Gegner – nur OPP_HAND so hoch –, Z/F, Monster, Extra-Monsterzonen, Monster, Z/F,
        deine Hand)."""
        w, h = self.size
        card_w, card_h, gap_x, gap, left = self._field_fit(w, h)
        top = (h - (int(card_h * OPP_HAND) + 6 * card_h + 6 * gap)) // 2
        cols = [left + i * (card_w + gap_x) for i in range(7)]
        rows = {name: top + int(card_h * OPP_HAND) + gap + i * (card_h + gap) for i, name in
                enumerate(("opp_s", "opp_m", "emz", "my_m", "my_s", "my_hand"))}
        rows["opp_hand"] = top
        return card_w, card_h, cols, rows

    def _field_fit(self, w: int, h: int) -> Tuple[int, int, int, int, int]:
        """Kartengröße für eine Zeichenfläche w × h (das Kleinere aus Höhe und Breite gewinnt):
        (Breite, Höhe, Spaltenabstand, Reihenabstand, linker Rand des Feldes). Der Spaltenabstand lässt einer
        quer liegenden Karte (ch breit) Platz; links bleibt Raum für Text und Ketten-Leiste, rechts nur ein Rand."""
        s = self.s
        gap, pad, k = max(3, int(6 * s)), int(24 * s), CARD[1] / CARD[0]
        card_h = int((h - 10 * gap) / (6 + OPP_HAND))
        card_w = int(card_h * CARD[0] / CARD[1])
        room = w - min(int(LEFT_ROOM * s), w // 5) - pad
        span = lambda cw, ch: 7 * cw + 6 * (ch - cw + int(4 * s))  # noqa: E731
        if span(card_w, card_h) > room:  # schmales Fenster: nach der Breite
            card_w = min(card_w, int((room - 24 * s) / (6 * k + 1)) + 1)
            while card_w > 1 and span(card_w, int(card_w * k)) > room:
                card_w -= 1
            card_h = int(card_w * k)
        return card_w, card_h, card_h - card_w + int(4 * s), gap, w - pad - span(card_w, card_h)

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

    def _draw_bg(self) -> None:
        """Spielfeld, Zonen und Beschriftung – ändern sich von Schritt zu Schritt nicht und werden einmal gemalt."""
        c, s = self.canvas, self.s
        c.delete("all")
        w, h = self.size
        cw, ch, cols, rows = self._geometry()
        c.create_rectangle(int(4 * s), int(4 * s), w - int(4 * s), h - int(4 * s), fill=FIELD, outline=FIELD_EDGE)
        c.create_rectangle(cols[0] - int(4 * s), rows["opp_hand"] - int(4 * s), cols[6] + cw + int(4 * s),
                           rows["emz"] - int(3 * s), fill=OPP_HALF, outline="")
        me, other = self.replay.my_id, 1 - self.replay.my_id
        piles = {("my_m", 6): ("gy", me), ("opp_m", 0): ("gy", other),
                 ("emz", 6): ("banished", me), ("emz", 0): ("banished", other)}
        for (row, col), text in BOARD_LABELS.items():
            opp = row.startswith("opp") or (row == "emz" and col == 0)
            x, y = cols[col], rows[row]
            c.create_rectangle(x, y, x + cw, y + ch, fill=OPP_ZONE if opp else ZONE,
                               outline=OPP_ZONE_EDGE if opp else ZONE_EDGE)
            c.create_text(x + cw // 2, y + ch // 2, text=text, fill=ZONE_TEXT, font=self.font_small, width=cw - 4,
                          justify=tk.CENTER)
            # Klick-Bereiche für leeren Friedhof / Verbannt binden
            if (row, col) in piles:
                key, team = piles[(row, col)]
                pile_tag = f"bg_{key}_{team}"
                c.create_rectangle(x, y, x + cw, y + ch, outline="", fill="", tags=(pile_tag,))
                c.tag_bind(pile_tag, "<Button-1>", lambda e, k=key, t=team: self._open_pile(k, t))
        self._bg_max = max(c.find_all())
        self._bg_ok = True

    def _clear_dyn(self) -> None:
        """Alles über dem Hintergrund löschen (Karten, Hinweise …)."""
        old = [i for i in self.canvas.find_all() if i > self._bg_max]
        if old:
            self.canvas.delete(*old)

    def _open_pile(self, key: str, team: int) -> None:
        """Friedhof/Verbannt eines Spielers im aktuellen Schritt öffnen."""
        self._open_pile_inspector(PILE_KINDS[key][1], self.tracker.get(self.frame_no)[key][team], team)

    def _moving_zone(self, card: dict, state: dict) -> Optional[int]:
        """Wo eine Karte in Zone 18 ("unterwegs") gezeigt wird: Zone, in der sie liegt bzw. landet; None = nirgends."""
        uid = card["uid"]
        info = self._card_info(card["card"]) if card.get("card") else None
        card_type = (info.card_type or "").lower() if info else ""
        is_extra = any(t in card_type for t in ("fusion", "synchro", "xyz", "link"))
        prev_z = state.get("last_zones", {}).get(uid, state.get("field_zones", {}).get(uid))
        dest_z = state.get("dest_zones", {}).get(uid)
        if dest_z in rs.MONSTER:
            return dest_z  # Beschwörung: die Karte gleitet schon jetzt zu ihrem Platz
        # Extra-Deck-Karten und Karten vom Spielfeld dürfen NIEMALS auf der Hand landen!
        if is_discard_cost(info) or dest_z == rr.GRAVE or prev_z in rs.MONSTER:
            return None
        if dest_z == rr.BANISHED or (prev_z == rr.EXTRA_DECK and is_extra):
            return None
        if any(t in card_type for t in ("spell", "trap")) or prev_z in rs.SPELL:
            # Normale Zauber und Fallen liegen auf dem Spielfeld, bis der Effekt resolved ist!
            if prev_z in rs.SPELL:
                return prev_z
            if "field" in card_type:
                return rr.FIELD_ZONE
            idx = card.get("index", 0)
            return (7 + idx) if 0 <= idx < 5 else 9
        # Nur Monster, die tatsächlich aus der Hand kommen und nicht abgeworfen/zerstört werden, bleiben in der Hand
        return rr.HAND if prev_z == rr.HAND and not is_extra else None

    def _arrival_attack(self, card: dict, n: int) -> int:
        """Angriffs-Flag einer unterwegs gemeldeten Karte: Master Duel dreht sie oft erst nach der Ankunft."""
        for later in self.replay.frames[n + 1:n + 4]:  # shortcut: nur 3 Frames voraus, bei längerem Zwischenstand
            nxt = next((r for r in later["cards"] if r[0] == card["uid"] and r[2] != rr.MOVING), None)
            if nxt is not None:
                return nxt[6] if nxt[2] == card["zone"] else card["attack"]
        return card["attack"]

    def _scene(self, n: int) -> Tuple[Dict[int, Item], Dict[Tuple[int, int], List[int]]]:
        """Schritt n: ({uid: Item} – wo jede Karte liegt, {(x, y): [uid]} – Stapel von unten nach oben)."""
        if n in self._scenes:
            return self._scenes[n]
        replay, s = self.replay, self.s
        state = self.tracker.get(n) if self.tracker else NO_STATE
        cw, ch, cols, rows = self._geometry()
        items: Dict[int, Item] = {}
        stacks: Dict[Tuple[int, int], List[int]] = {}
        hands: Dict[bool, List[dict]] = {True: [], False: []}

        def put(card: dict, zone: int, mine: bool, group: str) -> None:
            row, col = self.place(zone, mine)
            items[card["uid"]] = Item(card, cols[col], rows[row], mine, group)
            if group != "board":
                stacks.setdefault((cols[col], rows[row]), []).append(card["uid"])

        for row in replay.frames[n]["cards"]:
            card = dict(zip(CARD_KEYS, row))
            mine, zone = card["team"] == replay.my_id, card["zone"]
            if zone == rr.MOVING:
                zone = self._moving_zone(card, state)
                card = dict(card, zone=zone)
                if zone in rs.MONSTER:  # Beschwörung: gleich so ausgerichtet gleiten, wie die Karte ankommt
                    card["attack"] = self._arrival_attack(card, n)
            if zone is None or zone in (rr.GRAVE, rr.BANISHED):  # Friedhof/Verbannt kommen kumulativ vom Tracker
                continue
            if zone == rr.HAND:
                hands[mine].append(card)
            elif self.place(zone, mine) is not None:
                put(card, zone, mine, "pile" if zone in (rr.DECK, rr.EXTRA_DECK, rr.FIELD_ZONE) else "board")
        for team in (0, 1):
            for zone, key, group in ((rr.GRAVE, "gy", "grave"), (rr.BANISHED, "banished", "banished")):
                for card in state[key][team]:
                    put(card, zone, team == replay.my_id, group)
        for stack in stacks.values():  # Deck, Extra Deck, Feldzauber: die Karte mit dem höchsten Platz liegt oben
            if items[stack[0]].group == "pile":
                stack.sort(key=lambda uid: items[uid].card["index"])
        mid = cols[3] + cw // 2
        for mine, hand in hands.items():
            hand.sort(key=lambda k: k["index"])
            hw = cw if mine else int(cw * OPP_HAND)
            step = min(hw + int(4 * s), (cols[6] - cols[0]) // max(1, len(hand)))
            left = mid - (step * (len(hand) - 1) + hw) // 2
            for i, card in enumerate(hand):
                items[card["uid"]] = Item(card, left + i * step, rows["my_hand" if mine else "opp_hand"], mine, "hand")
        self._scenes[n] = (items, stacks)
        return self._scenes[n]

    def _draw_replay(self) -> None:
        if not self._bg_ok:
            self._draw_bg()
        else:
            self._clear_dyn()
        a = self._anim
        if a is None:
            self._draw_frame(self.frame_no)
            self._set_label(self.frame_no)
        else:  # Animation läuft: vom Schritt davor zum aktuellen, Klick-/Hinweis-Flächen gibt es erst im Standbild
            progress = min(1.0, (time.perf_counter() - a["t0"]) / a["dur"])
            self._draw_frame(self.frame_no, a["prev"], progress, bind=False)

    def _set_label(self, n: int) -> None:
        self._mark_step(n)
        frame, state = self.replay.frames[n], self.tracker.get(n)
        text = f"Schritt {n + 1} / {len(self.replay.frames)}"
        if frame.get("turn"):
            text += f"  ·  {frame.get('turn')}"
        if frame.get("phase"):
            text += f"  ·  {frame.get('phase')}"
        step = self.steps[n] if n < len(self.steps) else None
        act_card = state["activating"]
        if step is not None and step.chain is not None:   # echtes Protokoll: keine Vermutung des Trackers
            act_card = {"card": step.focus.card} if step.kind == "aktiviert" and step.focus is not None else None
        if act_card is not None:
            text += f"  ·  ⚡ {self.names.get(self.passcode(act_card['card'])) or 'Karte #' + str(act_card['card'])}"
        self.step_label.config(text=text)

    def _draw_frame(self, n: int, prev: Optional[int] = None, p: float = 1.0, bind: bool = True) -> None:
        """Schritt n malen; mit prev gleiten die Karten, die sich bewegt haben, dorthin (p = Fortschritt 0…1)."""
        replay, s = self.replay, self.s
        frame = replay.frames[n]
        state = self.tracker.get(n) if self.tracker else NO_STATE
        cw, ch, cols, rows = self._geometry()
        items, stacks = self._scene(n)
        old = self._scene(prev)[0] if prev is not None else {}
        moved = sorted(u for u, it in items.items() if u in old and (old[u].x, old[u].y) != (it.x, it.y))
        step = self.steps[n] if prev is not None and n < len(self.steps) else None   # Schritt, der gerade animiert wird
        glide = rs.glide_share(step) if step is not None else 0.0   # Aktivierung mit Platzwechsel: erst gleiten
        t = rs.ease(min(1.0, p / glide) if glide else p)

        def where(uid: int) -> Tuple[int, int]:
            it = items[uid]
            if uid in moved:
                return round(old[uid].x + (it.x - old[uid].x) * t), round(old[uid].y + (it.y - old[uid].y) * t)
            return it.x, it.y

        def dims(it: Item) -> Tuple[int, int]:  # Gegnerhand ist kleiner
            k = OPP_HAND if it.group == "hand" and not it.mine else 1.0
            return int(cw * k), int(ch * k)

        def draw(uid: int) -> None:
            it = items[uid]
            x, y = where(uid)
            w, h = dims(it)
            if uid in moved:  # Größe gleitet mit (Gegnerhand → Feld)
                ow, oh = dims(old[uid])
                w, h = round(ow + (w - ow) * t), round(oh + (h - oh) * t)
            card, squeeze, face = it.card, 1.0, None
            if step is not None and step.focus is not None and step.focus.uid == uid and \
                    step.kind in ("gesetzt", "umgedreht"):
                card, squeeze, face = self._flip(step, p, it, old.get(uid))
            tag = {"grave": "gy_card", "banished": "ban_card"}.get(it.group)
            self._board_card(x, y, w, h, card, it.mine, stats=it.group == "board", hand=it.group == "hand",
                             pile_tag=f"{tag}_{card['team']}" if tag else None, bind=bind, squeeze=squeeze, face=face)
            if it.group == "board" and it.mine and not it.card["face"] and face is None:
                self._shield(x + cw // 2, y + ch - int(10 * s), "gesetzt", LIGHT_BLUE, small=True)

        for stack in stacks.values():  # Stapel: oberste Karte, die nicht gerade unterwegs ist, mit Anzahl
            rest = [u for u in stack if u not in moved]
            if not rest:
                continue
            top = items[rest[-1]]
            draw(rest[-1])
            if top.group == "pile" and top.card["zone"] == rr.EXTRA_DECK:
                self._badge(top.x + cw, top.y + ch, str(top.card["index"] + 1))
            elif top.group in ("grave", "banished"):
                self._badge(top.x + cw, top.y + ch, str(len(stack)))
        for group in ("board", "hand"):
            for uid, it in items.items():
                if it.group == group and uid not in moved:
                    draw(uid)
        for uid in moved:  # bewegte Karten liegen oben
            draw(uid)
        if bind:
            self._bind_piles(state)
        now = self.steps[n] if n < len(self.steps) else None
        chain, resolving = (now.chain["links"], now.chain["res"]) if now is not None and now.chain is not None \
            else (state.get("chain") or [], 0)
        self._draw_chain(chain, resolving, items, where, cw, cols, rows)
        self._lp_and_phase(frame, cols, rows, cw, ch)
        if n < len(self.steps):
            self._draw_step_box(self.steps[n], cols, rows, ch)
        if step is not None and p >= glide:   # die Aktivierung läuft erst an, wenn die Karte am neuen Platz liegt
            self._draw_effects(step, (p - glide) / (1 - glide), state, frame, items, old, where, cw, ch, cols, rows)

    def _flip(self, step: rs.Step, p: float, it: Item, before: Optional[Item]):
        """Setzen/Aufdecken: (Karte, Breite 0…1, offen?) – Breite 1 → 0 → 1 wie das Umdrehen in der Factory.
        Eine gesetzte Karte wird erst offen gezeigt (wenn bekannt) und am Ende umgedreht."""
        f = step.focus
        if step.kind == "gesetzt":
            start = rs.DURATION["gesetzt"] / rs.duration("gesetzt")
            q = (p - start) / (1 - start)
            if q <= 0:
                return it.card, 1.0, bool(f.card)
            return it.card, max(0.125, round(abs(math.cos(math.pi * q)) * 8) / 8), bool(f.card) and q < 0.5
        card = it.card if p >= 0.5 or before is None else dict(it.card, card=before.card["card"] or it.card["card"])
        up = f.face_after if p >= 0.5 else f.face_before
        return card, max(0.125, round(abs(math.cos(math.pi * p)) * 8) / 8), bool(up)

    def _shield(self, cx: int, cy: int, text: str, color: str, alpha: float = 1.0, small: bool = False) -> None:
        """Schild mit Text (Hintergrund-Mischung statt Alpha: das Canvas kennt keine Durchsichtigkeit)."""
        c, s = self.canvas, self.s
        label = c.create_text(cx, cy, text=text, fill=rs.blend(color, SHIELD_BG, alpha),
                              font=self.font_small_bold if small else self.font_bold)
        x0, y0, x1, y1 = c.bbox(label)
        pad = int(3 * s)
        c.tag_lower(c.create_rectangle(x0 - pad, y0 - pad // 2, x1 + pad, y1 + pad // 2, fill=SHIELD_BG,
                                       outline=rs.blend(color, SHIELD_BG, alpha * 0.7)), label)

    def _draw_effects(self, step: rs.Step, p: float, state: dict, frame: dict, items: dict, old: dict, where,
                      cw: int, ch: int, cols, rows) -> None:
        """Was im Schritt passiert: Aktivierung, Beschwörung, zur Hand hinzugefügt, LP-Änderung."""
        s, f = self.s, step.focus
        spot = where(f.uid) if f is not None and f.uid in items else \
            ((old[f.uid].x, old[f.uid].y) if f is not None and f.uid in old else None)
        if spot is not None and step.kind == "aktiviert":
            # rot: eine Gegner-Karte antwortet in eine offene Kette des anderen
            links = step.chain["links"] if step.chain is not None else state.get("chain", [])
            red = f.team != self.replay.my_id and any(x["team"] != f.team and x["uid"] != f.uid for x in links)
            fi = items.get(f.uid) or old.get(f.uid)
            if fi is not None and fi.group == "hand" and not fi.mine:  # kleine Gegnerhand-Karte: Mitte behalten
                spot = (spot[0] - int(cw * (1 - OPP_HAND)) // 2, spot[1] - int(ch * (1 - OPP_HAND)) // 2)
            self._draw_activation(spot, p, step.label, red, f.card, cw, ch)
        elif spot is not None and step.kind == "beschworen":
            self._shield(spot[0] + cw // 2, max(int(12 * s), spot[1] - int(10 * s)), step.label, SUMMON_BLUE,
                         min(1.0, p * 4))
        elif spot is not None and step.kind == "kette":   # das Glied löst auf (rot, wie "KETTE LÖST AUF" der Factory)
            a = min(1.0, p * 4)
            self.canvas.create_rectangle(spot[0], spot[1], spot[0] + cw, spot[1] + ch, width=max(2, int(3 * s)),
                                         outline=rs.blend(RESOLVING, FIELD, a))
            self._shield(spot[0] + cw // 2, max(int(12 * s), spot[1] - int(10 * s)), step.label, RESOLVING, a)
        elif f is not None and step.kind == "hand" and frame.get("phase") != "Draw" and f.card and \
                self.passcode(f.card) in self.names:
            a = math.sin(math.pi * p)
            if a > 0.12:  # zur Hand hinzugefügt: Karte groß in der Feldmitte
                bw = int(cw * 1.7)
                bh = int(bw * ch / cw)
                cx, cy = cols[3] + cw // 2, rows["emz"] + ch // 2
                image = self._card_image(self.passcode(f.card), bw, bh, 0)
                if image is not None:
                    self.canvas.create_image(cx, cy, image=image)
                else:
                    self.canvas.create_rectangle(cx - bw // 2, cy - bh // 2, cx + bw // 2, cy + bh // 2,
                                                 fill=CARD_BACK, outline="#3a2010")
                self.canvas.create_rectangle(cx - bw // 2, cy - bh // 2, cx + bw // 2, cy + bh // 2,
                                             outline=rs.blend(ADD_BLUE, FIELD, a), width=int(2 * s))
                self._shield(cx, cy + bh // 2 + int(14 * s), "ZUR HAND HINZUGEFÜGT", ADD_BLUE, a)
        self._draw_marks(step, p, items, old, where, cw, ch)
        for team, delta in step.lp.items():  # LP-Änderung schwebt neben der Anzeige
            mine = team == self.replay.my_id
            y = rows["my_hand" if mine else "opp_hand"] + (ch if mine else int(ch * OPP_HAND)) // 2 + int(10 * s)
            self.canvas.create_text(cols[0] + int(95 * s), y - int(24 * s) * rs.ease(p),
                                    text=f"{'−' if delta < 0 else '+'}{abs(delta)}", anchor="w",
                                    fill=rs.blend(LP_DOWN if delta < 0 else LP_UP, FIELD, min(1.0, (1 - p) * 3)),
                                    font=self.font_head)

    @staticmethod
    def _spot(move: Optional[rs.Move], items: dict, old: dict, where) -> Optional[Tuple[int, int]]:
        """Oben links die Karte eines Ereignisses, jetzt oder im Schritt davor; None = nicht im Bild (nur Text)."""
        if move is None:
            return None
        if move.uid in items:
            return where(move.uid)
        return (old[move.uid].x, old[move.uid].y) if move.uid in old else None

    def _draw_marks(self, step: rs.Step, p: float, items: dict, old: dict, where, cw: int, ch: int) -> None:
        """Ereignisse aus dem Protokoll auf den Karten: KOSTEN (gold), ZIEL (Fadenkreuz), NEGIERT, ZERSTÖRT, ANGRIFF."""
        c, s = self.canvas, self.s
        a = min(1.0, p * 4)
        for mark in step.marks:
            spot = self._spot(mark.focus, items, old, where)
            if spot is None:
                continue
            x, y = spot
            cx, cy = x + cw // 2, y + ch // 2
            color = ACT_GOLD if mark.kind == "kosten" else ACT_RED
            tone, width = rs.blend(color, FIELD, a), max(2, int(3 * s))
            if mark.kind in ("ziel", "negiert", "zerstoert"):
                c.create_rectangle(x, y, x + cw, y + ch, outline=tone, width=width)
            if mark.kind == "ziel":   # Fadenkreuz
                r, g = int(cw * 0.38), int(cw * 0.12)
                c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=tone, width=2)
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    c.create_line(cx + dx * (r - g), cy + dy * (r - g), cx + dx * (r + g), cy + dy * (r + g),
                                  fill=tone, width=2)
            elif mark.kind == "negiert":   # durchgestrichen
                c.create_line(x, y + ch, x + cw, y, fill=tone, width=width)
            elif mark.kind == "angriff":
                target = self._spot(mark.other, items, old, where)
                if target is not None:   # Pfeil vom Angreifer zum Ziel, wächst mit dem Fortschritt
                    q = rs.ease(min(1.0, p * 1.5))
                    tx, ty = target[0] + cw // 2, target[1] + ch // 2
                    c.create_line(cx, cy, round(cx + (tx - cx) * q), round(cy + (ty - cy) * q), fill=tone,
                                  width=max(3, int(4 * s)), arrow=tk.LAST, arrowshape=(16, 20, 6))
                    c.create_rectangle(target[0], target[1], target[0] + cw, target[1] + ch, outline=tone, width=width)
            self._shield(cx, max(int(12 * s), y - int(10 * s)), rs.LOG_LABELS[mark.kind], color, a)

    def _draw_activation(self, spot: Tuple[int, int], p: float, label: str, red: bool, card_id: int, cw: int,
                         ch: int) -> None:
        """Aktivierung: Karte hebt sich vergrößert an, goldener Rahmen pulsiert, eine Lichtwelle breitet sich aus."""
        c, s = self.canvas, self.s
        color = ACT_RED if red else ACT_GOLD
        lift = math.sin(math.pi * min(1.0, p * 1.15))
        grow = 1 + 0.35 * round(lift * 4) / 4  # nur 5 feste Stufen: die vergrößerten Bilder bleiben im Cache
        bw, bh = int(cw * grow), int(ch * grow)
        cx, cy = spot[0] + cw // 2, spot[1] + ch // 2 - int(ch * 0.12 * lift)
        cy = max(cy, bh // 2 + int(30 * s))  # Karte aus der flachen Gegnerhand + Beschriftung bleiben im Bild
        cy = min(cy, self.size[1] - bh // 2 - int(17 * s))  # unten: der äußerste Ring bleibt im Bild
        cx = max(bw // 2 + int(17 * s), min(cx, self.size[0] - bw // 2 - int(17 * s)))  # seitlich ebenso
        r = int(cw * (0.6 + 1.4 * p))
        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline=rs.blend(color, FIELD, 0.63 * (1 - p)),
                      width=max(2, int(6 * s * (1 - p)) + 1))
        for k in range(5, 0, -1):  # pulsierender Rahmen: 5 Ringe, nach außen zur Hintergrundfarbe gemischt
            d = int((k * 3 + 2) * s)
            c.create_rectangle(cx - bw // 2 - d, cy - bh // 2 - d, cx + bw // 2 + d, cy + bh // 2 + d,
                               outline=rs.blend(color, FIELD, lift * (1.1 - k * 0.18)), width=2)
        passcode = self.passcode(card_id) if card_id else None
        image = self._card_image(passcode, bw, bh, 0) if passcode else None
        if image is not None:
            c.create_image(cx, cy, image=image)
        else:
            c.create_rectangle(cx - bw // 2, cy - bh // 2, cx + bw // 2, cy + bh // 2, fill=CARD_BACK, outline="")
            if passcode:
                c.create_text(cx, cy, text=self.names.get(passcode, ""), fill=TEXT, font=self.font_small,
                              width=bw - 4, justify=tk.CENTER)
        c.create_rectangle(cx - bw // 2, cy - bh // 2, cx + bw // 2, cy + bh // 2, outline=color, width=int(2 * s))
        self._shield(cx, cy - bh // 2 - int(14 * s), label, color, min(1.0, 0.1 + lift))

    def _side_box(self, cols) -> Tuple[int, int]:
        """Linker Rand neben dem Feld (Kette, Schritt-Text): (x, Breite)."""
        return int(12 * self.s), max(int(120 * self.s), min(int(220 * self.s), cols[0] - int(24 * self.s)))

    def _draw_step_box(self, step: rs.Step, cols, rows, ch: int) -> None:
        """Schritt-Text als Kasten links auf Höhe der Gegner-Monster (gold, bei Aktionen des Gegners rötlich)."""
        if not step.text:
            return
        c, s = self.canvas, self.s
        opp = step.focus is not None and step.focus.team != self.replay.my_id
        x, width = self._side_box(cols)
        label = c.create_text(x + int(10 * s), rows["opp_m"] + ch // 2, text=step.text, anchor="w", font=self.font_bold,
                              fill="#ff8c8c" if opp else "#ffecbe", width=width - int(20 * s))
        x0, y0, x1, y1 = c.bbox(label)
        pad = int(6 * s)
        c.tag_lower(c.create_rectangle(x, y0 - pad, max(x1 + pad, x + width // 2), y1 + pad, fill="#10121e",
                                       outline="#a03c3c" if opp else "#78643c"), label)

    def _draw_chain(self, chain: List[dict], resolving: int, items: dict, where, cw: int, cols, rows) -> None:
        """Kette wie in Master Duel: Nummer auf jeder Kettenkarte, links die Ketten-Leiste (oberstes Glied oben).
        Das auflösende Glied (resolving) leuchtet rot."""
        if not chain:
            return
        c, s = self.canvas, self.s
        r = max(9, cw // 6)
        for link in chain:
            uid = link["uid"] if link["uid"] in items else next(
                (u for u, it in items.items() if it.group in ("board", "pile") and it.card["card"] == link["card"]
                 and it.card["team"] == link["team"]), None)
            if uid is None:
                continue
            x, y = where(uid)
            cx, cy = x + r // 2 + 4, y + r // 2 + 4
            c.create_oval(cx - r, cy - r, cx + r, cy + r, fill=RESOLVING if link["cl"] == resolving else PLAQUE,
                          outline="#28140a", width=2)
            c.create_text(cx, cy, text=str(link["cl"]), fill="#140e06", font=self.font_small_bold)
        x, width = self._side_box(cols)
        top, row = rows["emz"], int(22 * s)
        c.create_rectangle(x, top, x + width, top + int(26 * s) + row * len(chain), fill="#0e101c", outline="#96783c")
        c.create_text(x + int(8 * s), top + int(6 * s), text=f"KETTE ({len(chain)})", fill=GOLD,
                      font=self.font_small_bold, anchor="nw")
        for k, link in enumerate(reversed(chain)):
            y = top + int(22 * s) + k * row
            hot = link["cl"] == resolving
            c.create_rectangle(x + int(4 * s), y, x + width - int(4 * s), y + row - 2,
                               fill="#5a2020" if hot else "#20243a", outline=RESOLVING if hot else "")
            c.create_text(x + int(14 * s), y + row // 2 - 1, text=str(link["cl"]),
                          fill=RESOLVING if hot else "#ffc85a", font=self.font_small_bold)
            name = self.names.get(self.passcode(link["card"])) or "Karte"
            room = int((width - int(36 * s)) / (5.5 * s))
            c.create_text(x + int(26 * s), y + row // 2 - 1, anchor="w", font=self.font_small, fill=TEXT,
                          text=name if len(name) <= room else name[:max(1, room - 1)] + "…")

    def _bind_piles(self, state: dict) -> None:
        """Klick und Hinweis für Friedhof und Verbannt beider Spieler (die Tags tragen die Karten der Stapel)."""
        c = self.canvas
        for team in (0, 1):
            mine = team == self.replay.my_id
            for key, (prefix, title, own, noun) in PILE_KINDS.items():
                tag = f"{prefix}_{team}"
                text = f"{own if mine else 'Gegner-'}{noun}: {len(state[key][team])} Karte(n) – Klick für Details"
                c.tag_bind(tag, "<Button-1>", lambda e, k=key, t=team: self._open_pile(k, t))
                c.tag_bind(tag, "<Enter>", lambda e, text=text: self._tip_text(text))
                c.tag_bind(tag, "<Leave>", lambda e: self._untip())

    def _lp_and_phase(self, frame: dict, cols, rows, cw: int, ch: int) -> None:
        c, s = self.canvas, self.s
        replay = self.replay
        names = replay.names if len(replay.names) == 2 else ["", ""]
        lp = frame.get("lp", {})
        me, opp = replay.my_id, 1 - replay.my_id
        for team, row, color in ((opp, "opp_hand", "#dc96a0"), (me, "my_hand", "#96beff")):
            x, y = cols[0], rows[row] + (ch if row == "my_hand" else int(ch * OPP_HAND)) // 2
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
                    hand: bool = False, pile_tag: Optional[str] = None, bind: bool = True, squeeze: float = 1.0,
                    face: Optional[bool] = None) -> None:
        """Karte im Feld: Gegner um 180° gedreht, Verteidigung quer, verdeckt als Rückseite.
        face erzwingt offen/verdeckt, squeeze < 1 quetscht die Karte (Umdrehen: Breite 1 → 0 → 1)."""
        c, s = self.canvas, self.s
        defense = not hand and card["zone"] in (*rr.MONSTER_ZONES, *rr.EXTRA_MONSTER_ZONES) and not card["attack"]
        angle = (0 if mine else 180) + (90 if defense else 0)
        passcode = self.passcode(card["card"]) if card["card"] else None
        up = (card["face"] or (mine and hand) or pile_tag) if face is None else face
        shown = passcode if up else None
        iw, ih = (w, max(2, int(h * squeeze))) if defense else (max(2, int(w * squeeze)), h)  # Bild vor dem Drehen
        image = self._card_image(shown, iw, ih, angle) if shown else None
        cx, cy = x + w // 2, y + h // 2
        if image is not None:
            c.create_image(cx, cy, image=image)
        else:
            bw, bh = (ih, iw) if defense else (iw, ih)
            c.create_rectangle(cx - bw // 2, cy - bh // 2, cx + bw // 2, cy + bh // 2, fill=CARD_BACK,
                               outline="#3a2010")
            if shown:
                c.create_text(cx, cy, text=self.names.get(shown, ""), fill=TEXT, font=self.font_small,
                              width=bw - 4, justify=tk.CENTER)
        tag = f"bc{card['uid']}_{x}_{y}"
        tags = (tag, pile_tag) if pile_tag else (tag,)
        if bind:  # in Animationsbildern keine Klick-/Hinweis-Flächen (sonst Enter/Leave-Gewitter beim Neuzeichnen)
            c.create_rectangle(x, y, x + w, y + h, outline="", fill="", tags=tags)
        if bind and shown and not pile_tag:
            c.tag_bind(tag, "<Enter>", lambda e, p=shown, x=x, y=y, h=h: self._tip(p, 1, x, y, h, plain=True))
            c.tag_bind(tag, "<Leave>", lambda e: self._untip())
        if stats and card["face"] and card["zone"] in (*rr.MONSTER_ZONES, *rr.EXTRA_MONSTER_ZONES) \
                and card["atk"] >= 0 and face is None:
            c.create_text(cx, y + h - int(2 * s), text=f"{card['atk']} / {card['def']}", fill=GOLD,
                          font=self.font_small_bold, anchor="s")

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

            def wheel(e):
                canvas.yview_scroll(int(-e.delta / 120) * 2, "units")
                return "break"  # sonst scrollt ein Mausrad-Binding des Toplevels ein zweites Mal
            canvas.bind("<MouseWheel>", wheel)

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

            def bind_wheel(widget):  # das Mausrad soll auch über den Zeilen scrollen, nicht nur über der Canvas
                widget.bind("<MouseWheel>", wheel)
                for child in widget.winfo_children():
                    bind_wheel(child)
            bind_wheel(inner)

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
            if not visible:
                self._stop_play()             # Master Duel nicht mehr vorne: pausieren, kein automatisches Weiter
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
        self._stop_play(draw=False)
        if self._poll_job is not None:
            try:
                self.win.after_cancel(self._poll_job)
            except tk.TclError:
                pass
        self.win.destroy()
        self.on_close()
