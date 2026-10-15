"""
Replay-Schritte (reine Logik, ohne Tk): was ändert sich zwischen zwei Mitschnitt-Frames, welche Art Schritt ist das
("aktiviert", "beschworen", "gesetzt" …), wie lange dauert seine Animation und wie lautet der Schritt-Text.
Vorlage ist die Spielfeld-Ansicht der Factory (field_view: PHASES/STEP_TIME/SET_FLIP, seq.steps); die Anzeige selbst
(Zeichnen, Animation) macht duel_view.
"""

from typing import Callable, Dict, List, NamedTuple, Optional, Sequence

import replay_recorder as rr

MONSTER = (*rr.MONSTER_ZONES, *rr.EXTRA_MONSTER_ZONES)
SPELL = (*rr.SPELL_ZONES, rr.FIELD_ZONE)
FIELD = (*MONSTER, *SPELL)

# Dauer der Animation je Art in Sekunden bei 1× (wie Factory PHASES), "still" = kein sichtbarer Unterschied
DURATION = {"aktiviert": 0.85, "beschworen": 0.7, "gesetzt": 0.6, "umgedreht": 0.35, "friedhof": 0.7,
            "verbannt": 0.7, "hand": 0.8, "zug": 0.6, "still": 0.0}
SET_FLIP = 0.35      # gesetzte Karte: erst offen gezeigt, dann umgedreht (kommt zur Dauer dazu)
LP_TIME = 0.5        # Schritt ohne Kartenänderung, aber mit LP-Änderung
STEP_PAUSE = 1.0     # Pause nach einem Schritt beim Abspielen (bei 1×, wie Factory STEP_TIME)
ORDER = ("beschworen", "gesetzt", "umgedreht", "friedhof", "verbannt", "hand", "zug")   # Rangfolge (nach "aktiviert")
SUMMON = (("link", "Linkbeschwörung"), ("xyz", "Xyz-Beschwörung"), ("synchro", "Synchrobeschwörung"),
          ("fusion", "Fusionsbeschwörung"), ("ritual", "Ritualbeschwörung"))


class Move(NamedTuple):
    uid: int
    team: int
    card: int                      # Konami-ID (0 = unbekannt/verdeckt)
    from_zone: Optional[int]       # None = neu im Bild
    to_zone: Optional[int]         # None = verschwunden
    face_before: Optional[int]
    face_after: Optional[int]
    index_before: Optional[int]
    index_after: Optional[int]


class Step(NamedTuple):
    kind: str
    text: str
    moves: tuple
    focus: Optional[Move]          # die Karte, um die es im Schritt geht
    lp: Dict[int, int]             # Spieler → Änderung der Lebenspunkte
    label: str                     # Schild über der Karte ("BESCHWOREN", "LINKBESCHWÖRUNG" …)
    dur: float                     # Animation in Sekunden bei 1×


def ease(t: float) -> float:
    return 1 - (1 - t) ** 3


def blend(color: str, background: str, alpha: float) -> str:
    """'#rrggbb' zu alpha (0…1) über dem Hintergrund – das Tk-Canvas kennt keine Durchsichtigkeit."""
    a = max(0.0, min(1.0, alpha))
    rgb = lambda c: [int(c[i:i + 2], 16) for i in (1, 3, 5)]  # noqa: E731
    return "#%02x%02x%02x" % tuple(round(f * a + b * (1 - a)) for f, b in zip(rgb(color), rgb(background)))


def moves(prev_frame: Optional[dict], frame: dict) -> List[Move]:
    """Karten, deren Zone, Platz, Karte oder Aufdeckung sich geändert hat (auch neue und verschwundene)."""
    old = {row[0]: row for row in (prev_frame or {}).get("cards", [])}
    new = {row[0]: row for row in frame.get("cards", [])}
    out = []
    for uid in sorted(old.keys() | new.keys()):
        a, b = old.get(uid), new.get(uid)
        if a and b and a[2:6] == b[2:6]:
            continue
        ref = b or a
        out.append(Move(uid, ref[1], ref[4], a[2] if a else None, b[2] if b else None, a[5] if a else None,
                        b[5] if b else None, a[3] if a else None, b[3] if b else None))
    return out


def _dest(uid: int, zone: Optional[int], team: int, state: Optional[dict]) -> Optional[int]:
    """Wohin eine Karte wirklich geht: in Zone 18 ("unterwegs") das Ziel des Trackers; verschwundene Karten liegen im
    Friedhof bzw. verbannt, wenn der Tracker sie dort führt."""
    state = state or {}
    if zone == rr.MOVING:
        return state.get("dest_zones", {}).get(uid, zone)
    if zone is None:
        for z, key in ((rr.GRAVE, "gy"), (rr.BANISHED, "banished")):
            if any(x["uid"] == uid for x in state.get(key, {}).get(team, ())):
                return z
    return zone


def _kind_of(m: Move, state: Optional[dict], prev_state: Optional[dict]) -> Optional[str]:
    """Art der Bewegung einer Karte; None = nichts Sichtbares (Karte erscheint im Deck, Platz im Stapel …)."""
    src = _dest(m.uid, m.from_zone, m.team, prev_state) if m.from_zone == rr.MOVING else m.from_zone
    dst = _dest(m.uid, m.to_zone, m.team, state)
    hidden = (rr.DECK, rr.EXTRA_DECK)
    if src == dst:   # an Ort und Stelle: nur Aufdecken/Umdrehen ist zu sehen
        return "umgedreht" if dst in FIELD and m.face_before != m.face_after else None
    if src in (None, *hidden) and dst in (None, *hidden):
        return None
    if dst in MONSTER and src not in MONSTER:
        return "gesetzt" if m.face_after == 0 else "beschworen"
    if dst in SPELL and src not in SPELL and m.face_after == 0:
        return "gesetzt"
    if dst == rr.GRAVE:
        return "friedhof"
    if dst == rr.BANISHED:
        return "verbannt"
    if dst == rr.HAND and src != rr.HAND:
        return "hand"
    return "zug"


def _classify(moves_: Sequence[Move], state: Optional[dict], prev_state: Optional[dict]):
    """(Art, Karte im Mittelpunkt) des Schritts; "aktiviert" kommt vom Tracker (state["activating"], neu im Schritt)."""
    act = (state or {}).get("activating")
    prev_act = (prev_state or {}).get("activating")
    if act and (not prev_act or prev_act["uid"] != act["uid"]):
        focus = next((m for m in moves_ if m.uid == act["uid"]), None)
        return "aktiviert", focus or Move(act["uid"], act["team"], act["card"], None, None, None, None, None, None)
    kinds = [(m, _kind_of(m, state, prev_state)) for m in moves_]
    for kind in ORDER:
        for m, k in kinds:
            if k == kind:
                return kind, m
    return "still", None


def kind(moves_: Sequence[Move], state: Optional[dict], prev_state: Optional[dict]) -> str:
    return _classify(moves_, state, prev_state)[0]


def duration(kind_: str, lp_changed: bool = False) -> float:
    """Animation eines Schritts in Sekunden bei 1×."""
    if kind_ == "still":
        return LP_TIME if lp_changed else 0.0
    return DURATION[kind_] + (SET_FLIP if kind_ == "gesetzt" else 0.0)


def summon_label(card_type: str) -> str:
    """'Link Monster' → 'Linkbeschwörung'; sonst 'beschwört'-Standard (leer)."""
    low = (card_type or "").lower()
    return next((text for key, text in SUMMON if key in low), "")


def activation_label(card_type: str) -> str:
    """Schild über der aktivierten Karte: Monster-Effekt → EFFEKT, sonst (auch unbekannt) AKTIVIERT."""
    low = (card_type or "").lower()
    return "EFFEKT" if "monster" in low and not any(t in low for t in ("spell", "trap")) else "AKTIVIERT"


def describe(focus: Optional[Move], kind_: str, name_of: Callable[[int], Optional[str]], my_id: int, phase: str = "",
             type_of: Callable[[int], str] = lambda card: "") -> str:
    """Schritt-Text wie die Factory: "Du aktivierst Ash Blossom", "Gegner beschwört X (Linkbeschwörung)"."""
    if focus is None or kind_ == "still":
        return ""
    me = focus.team == my_id
    who = "Du" if me else "Gegner"
    verb = lambda du, er: du if me else er  # noqa: E731
    name = name_of(focus.card) if focus.card else None
    card = name or "eine Karte"
    if kind_ == "aktiviert":
        return f"{who} {verb('aktivierst', 'aktiviert')} {card}"
    if kind_ == "beschworen":
        how = summon_label(type_of(focus.card)) if focus.card else ""
        return f"{who} {verb('beschwörst', 'beschwört')} {card}" + (f" ({how})" if how else "")
    if kind_ == "gesetzt":
        return f"{who} setzt eine Karte"
    if kind_ == "umgedreht":
        return f"{who} {verb('deckst', 'deckt')} {card} auf"
    if kind_ in ("friedhof", "verbannt"):
        return f"{who}: {card} → {'Friedhof' if kind_ == 'friedhof' else 'Verbannt'}"
    if kind_ == "hand":
        if name and phase != "Draw":
            return f"{who} {verb('fügst', 'fügt')} {name} {verb('deiner', 'seiner')} Hand hinzu"
        return f"{who} {verb('ziehst', 'zieht')} eine Karte"
    return f"{who} {verb('bewegst', 'bewegt')} {card}"


def build_steps(frames: List[dict], states: Sequence[dict], my_id: int,
                name_of: Callable[[int], Optional[str]] = lambda card: None,
                type_of: Callable[[int], str] = lambda card: "") -> List[Step]:
    """steps[i] = der Übergang von Frame i-1 zu Frame i (steps[0] ist leer)."""
    out = []
    for i, frame in enumerate(frames):
        prev = frames[i - 1] if i else None
        ms = moves(prev, frame) if i else []
        state = states[i] if i < len(states) else None
        prev_state = states[i - 1] if i and i - 1 < len(states) else None
        kind_, focus = _classify(ms, state, prev_state)
        lp = {}
        for team in (0, 1):
            before, after = ((prev or {}).get("lp") or {}).get(str(team)), (frame.get("lp") or {}).get(str(team))
            if i and before is not None and after is not None and before != after:
                lp[team] = after - before
        card_type = type_of(focus.card) if focus is not None and focus.card else ""
        label = {"aktiviert": activation_label, "beschworen": lambda t: summon_label(t).upper() or "BESCHWOREN",
                 "gesetzt": lambda t: "GESETZT"}.get(kind_, lambda t: "")(card_type)
        out.append(Step(kind_, describe(focus, kind_, name_of, my_id, frame.get("phase") or "", type_of), tuple(ms),
                        focus, lp, label, duration(kind_, bool(lp))))
    return out
