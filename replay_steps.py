"""
Replay-Schritte (reine Logik, ohne Tk): was ändert sich zwischen zwei Mitschnitt-Frames, welche Art Schritt ist das
("aktiviert", "beschworen", "gesetzt" …), wie lange dauert seine Animation und wie lautet der Schritt-Text.
Vorlage ist die Spielfeld-Ansicht der Factory (field_view: PHASES/STEP_TIME/SET_FLIP, seq.steps); die Anzeige selbst
(Zeichnen, Animation) macht duel_view.

Enthält ein Frame "log" (replay_recorder: Zeilen des echten Duell-Protokolls von Master Duel, siehe
md_memory.log_entry), bestimmen diese Einträge Art, Text und Schild des Schritts; ältere Mitschnitte laufen mit der
Bewegungs-Heuristik.
"""

from typing import Callable, Dict, List, NamedTuple, Optional, Sequence

import replay_recorder as rr

MONSTER = (*rr.MONSTER_ZONES, *rr.EXTRA_MONSTER_ZONES)
SPELL = (*rr.SPELL_ZONES, rr.FIELD_ZONE)
FIELD = (*MONSTER, *SPELL)

# Dauer der Animation je Art in Sekunden bei 1× (wie Factory PHASES), "still" = kein sichtbarer Unterschied
DURATION = {"aktiviert": 0.85, "beschworen": 0.7, "gesetzt": 0.6, "umgedreht": 0.35, "friedhof": 0.7,
            "verbannt": 0.7, "hand": 0.8, "zug": 0.6, "still": 0.0,
            "kosten": 0.7, "material": 0.6, "ziel": 0.6, "kontrolle": 0.6, "negiert": 0.7, "zerstoert": 0.7,
            "angriff": 0.8, "kette": 0.6}
SET_FLIP = 0.35      # gesetzte Karte: erst offen gezeigt, dann umgedreht (kommt zur Dauer dazu)
LP_TIME = 0.5        # Schritt ohne Kartenänderung, aber mit LP-Änderung
STEP_PAUSE = 1.0     # Pause nach einem Schritt beim Abspielen (bei 1×, wie Factory STEP_TIME)
ORDER = ("beschworen", "gesetzt", "umgedreht", "friedhof", "verbannt", "hand", "zug")   # Rangfolge (nach "aktiviert")
SUMMON = (("link", "Linkbeschwörung"), ("xyz", "Xyz-Beschwörung"), ("synchro", "Synchrobeschwörung"),
          ("fusion", "Fusionsbeschwörung"), ("ritual", "Ritualbeschwörung"))
# Aktionscodes des Duell-Protokolls (aus echten Replays entschlüsselt); unbekannte Codes sind neutral, nie ein Fehler
CODE_SUMMON = (2, 3, 5, 7, 9)  # Normal-, Spezial-, Extra-Deck-, Xyz-, Linkbeschwörung (die Art kommt vom Kartentyp)
CODE_DESTROY = {20: "durch Kampf", 21: "durch Effekt"}
CODE_COST = {46: ("verbannst", "verbannt", "als Kosten"), 49: ("schickst", "schickt", "als Kosten auf den Friedhof"),
             51: ("wirfst", "wirft", "als Kosten ab"), 31: ("zeigst", "zeigt", "als Kosten vor"),
             50: ("schickst", "schickt", "als Kosten auf den Friedhof")}
LOG_ORDER = ("aktiviert", "beschworen", "zerstoert", "kosten", "material", "negiert", "angriff", "ziel", "kontrolle",
             "kette", "gesetzt", "hand")
# diese nennt der Text zusätzlich zum Hauptereignis
EXTRA_KINDS = ("kosten", "material", "ziel", "kontrolle", "negiert", "zerstoert", "kette")
MARK_KINDS = ("kosten", "ziel", "negiert", "zerstoert", "angriff")  # diese bekommen ein Schild auf der Karte
LOG_LABELS = {"kosten": "KOSTEN", "ziel": "ZIEL", "negiert": "NEGIERT", "zerstoert": "ZERSTÖRT", "angriff": "ANGRIFF",
              "gesetzt": "GESETZT"}


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
    marks: tuple = ()              # Mark: Kosten, Ziel, Negation, Zerstörung, Angriff (nur mit Protokoll)
    chain: Optional[dict] = None   # {"links": [{"cl", "uid", "card", "team"}], "res": auflösendes Glied} (Protokoll)


class Mark(NamedTuple):
    kind: str                      # "kosten" | "ziel" | "negiert" | "zerstoert" | "angriff"
    focus: Move                    # uid -1: Karte im Bild nicht gefunden → nur im Text
    other: Optional[Move] = None   # Angriff: das Ziel (None = direkter Angriff)


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


def glides(kind_: str, focus: Optional[Move]) -> bool:
    """Aktivierung einer Karte, die dabei aufs Feld kommt: erst gleitet sie hin, dann wird sie aktiviert."""
    return (kind_ == "aktiviert" and focus is not None and focus.from_zone is not None and focus.to_zone in FIELD
            and focus.from_zone != focus.to_zone)


def duration(kind_: str, lp_changed: bool = False, glide: bool = False) -> float:
    """Animation eines Schritts in Sekunden bei 1× (glide: Gleiten vor der Aktivierung, siehe glides)."""
    if kind_ == "still":
        return LP_TIME if lp_changed else 0.0
    return DURATION[kind_] + (SET_FLIP if kind_ == "gesetzt" else 0.0) + (DURATION["zug"] if glide else 0.0)


def glide_share(step: Step) -> float:
    """Anteil (0…1) der Schrittdauer, in dem die aktivierte Karte noch gleitet; 0 = keine Gleitphase."""
    return DURATION["zug"] / step.dur if glides(step.kind, step.focus) else 0.0


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
    if kind_ == "umgedreht":   # "aufdecken" nur beim Aufdecken (verdeckt → offen), nie beim Zurückdrehen
        return (f"{who} {verb('deckst', 'deckt')} {card} auf" if focus.face_after == 1
                else f"{who} {verb('drehst', 'dreht')} {card} um")
    if kind_ in ("friedhof", "verbannt"):
        return f"{who}: {card} → {'Friedhof' if kind_ == 'friedhof' else 'Verbannt'}"
    if kind_ == "hand":
        if name and phase != "Draw":
            return f"{who} {verb('fügst', 'fügt')} {name} {verb('deiner', 'seiner')} Hand hinzu"
        return f"{who} {verb('ziehst', 'zieht')} eine Karte"
    return f"{who} {verb('bewegst', 'bewegt')} {card}"


def chain_states(frames: List[dict]) -> List[dict]:
    """
    Kette je Frame aus dem Protokoll: {"links": [{"cl", "uid", "card", "team"}], "res": Gliednummer, die in diesem
    Schritt auflöst (0 = keine)}. Das erste Glied steht nicht als Kette im Protokoll, nur seine Aktivierung; ein
    aufgelöstes Glied leuchtet im Schritt des Auflösens und ist im nächsten weg.
    """
    links: List[dict] = []
    closing = 0
    out = []

    def link(n: int, e: dict) -> dict:
        return {"cl": n, "uid": -1, "card": e.get("c", 0), "team": e.get("t", 0)}

    for f in frames:
        if closing:
            links = [x for x in links if x["cl"] < closing]
        closing = 0
        for e in f.get("log") or ():
            if e.get("k") == "chain" and e.get("res"):
                n = e.get("n", 1)
                links = [x for x in links if x["cl"] <= n]
                if not any(x["cl"] == n for x in links):
                    links.append(link(n, e))
                closing = n
            elif e.get("k") == "chain":
                n = e.get("n", 1)
                links = [x for x in links if x["cl"] < n] + [link(n, e)]
            elif e.get("k") == "act" and e.get("a") == 11 and (not links or closing == 1):
                links, closing = [link(1, e)], 0
        out.append({"links": list(links), "res": closing})
    return out


def event_kind(e: dict) -> Optional[str]:
    """Art eines Protokolleintrags; None = nichts Eigenes (LP, Zug, Phase, unbekannte Codes)."""
    if e.get("k") == "chain":
        return "kette" if e.get("res") else None
    if e.get("k") != "act":
        return None
    code = e.get("a")
    if code == 11:
        return "aktiviert"
    if e.get("cost") or code in CODE_COST:
        return "kosten"
    if code in CODE_SUMMON:
        return "beschworen"
    if code in CODE_DESTROY:
        return "zerstoert"
    return {28: "material", 12: "negiert", 10: "angriff", 23: "ziel", 22: "kontrolle", 13: "gesetzt", 14: "gesetzt",
            26: "hand", 1: "hand"}.get(code)


def _locate(card: int, team: int, zone: Optional[int], frame: dict, prev_frame: Optional[dict],
            moves_: Sequence[Move]) -> Move:
    """Die Karte eines Protokolleintrags im Bild suchen (Karte + Spieler, gleiche Zone zuerst): erst im Frame, dann im
    Frame davor. Nicht gefunden: uid -1 (die Anzeige zeigt dann nur den Text)."""
    for source in (frame, prev_frame):
        cards = (source or {}).get("cards", ())
        rows = [r for r in cards if r[1] == team and r[4] == card] if card else []
        if not rows and zone is not None:   # verdeckt (Karte im Bild unbekannt): Spieler + Zone
            rows = [r for r in cards if r[1] == team and r[4] == 0 and r[2] == zone]
        rows.sort(key=lambda r: r[2] != zone)
        if rows:
            r = rows[0]
            move = next((m for m in moves_ if m.uid == r[0]), None)
            if move is not None:
                return move._replace(card=card or move.card)
            if source is frame:
                return Move(r[0], team, card or r[4], r[2], r[2], r[5], r[5], r[3], r[3])
            return Move(r[0], team, card or r[4], r[2], None, r[5], None, r[3], None)
    return Move(-1, team, card, None, None, None, None, None, None)


def _attacks_card(e: dict) -> bool:
    """Angriff auf eine Karte? Bei direktem Angriff steht in rc der angegriffene Spieler (0/1), keine Konami-ID."""
    return e.get("rc", 0) > 1


def describe_event(kind_: str, e: dict, name_of: Callable[[int], Optional[str]], my_id: int,
                   type_of: Callable[[int], str] = lambda card: "", chain: Optional[dict] = None) -> str:
    """Text eines Protokolleintrags: "Gegner aktiviert Ash Blossom (Kette 2)", "X wird durch Kampf zerstört" …"""
    if kind_ == "kette":
        return f"Kette {e.get('n', 1)} löst auf"
    team, code = e.get("t", 0), e.get("a")
    me = team == my_id
    who = "Du" if me else "Gegner"
    name = (name_of(e["c"]) if e.get("c") else None) or "eine Karte"
    if kind_ == "kosten":
        du, er, tail = CODE_COST.get(code, ("zahlst", "zahlt", "als Kosten"))
        return f"{who} {du if me else er} {name} {tail}"
    if kind_ == "material":
        return f"{name} wird Xyz-Material"
    if kind_ == "zerstoert":
        return f"{name} wird {CODE_DESTROY[code]} zerstört"
    if kind_ == "negiert":
        return f"{name} wird negiert"
    if kind_ == "ziel":
        return f"{name} wird zum Ziel"
    if kind_ == "kontrolle":   # "zt" = neuer Besitzer
        who = "Du übernimmst" if e.get("zt") == my_id else "Gegner übernimmt"
        return f"{who} die Kontrolle über {name}"
    if kind_ == "angriff":
        if not _attacks_card(e):
            return f"{name} greift direkt an"
        return f"{name} greift {name_of(e['rc']) or 'eine Karte'} an"
    focus = Move(-1, team, 0 if code == 1 else e.get("c", 0), None, None, None, None, None, None)
    text = describe(focus, kind_, name_of, my_id, "" if code != 1 else "Draw", type_of)
    if kind_ == "aktiviert":   # Gliednummer ab dem zweiten Glied: "(Kette 2)"
        nums = [x["cl"] for x in (chain or {}).get("links", ()) if x["card"] == e.get("c") and x["team"] == team]
        if nums and max(nums) >= 2:
            text += f" (Kette {max(nums)})"
    return text


def _log_step(frame: dict, prev_frame: Optional[dict], chain: dict, moves_: Sequence[Move],
              name_of: Callable[[int], Optional[str]], type_of: Callable[[int], str], my_id: int):
    """(Art, Karte im Mittelpunkt, Text, Schild, Marken) aus den Protokollzeilen eines Frames; None = nichts Eigenes."""
    found = [(k, e) for e in frame.get("log") or () for k in [event_kind(e)] if k]
    if not found:
        return None

    def where(e: dict) -> Move:
        return _locate(e.get("c", 0), e.get("t", 0), e.get("f"), frame, prev_frame, moves_)

    kind_ = min((k for k, _ in found), key=LOG_ORDER.index)
    main = next(e for k, e in found if k == kind_)
    focus = where(main)
    parts = [describe_event(kind_, main, name_of, my_id, type_of, chain)]
    for extra in EXTRA_KINDS:
        if extra != kind_ and len(parts) < 3:
            e = next((e for k, e in found if k == extra), None)
            if e is not None:
                parts.append(describe_event(extra, e, name_of, my_id, type_of, chain))
    card_type = type_of(focus.card) if focus.card else ""
    label = LOG_LABELS.get(kind_) or {"aktiviert": activation_label(card_type),
                                      "beschworen": summon_label(card_type).upper() or "BESCHWOREN",
                                      "kette": f"KETTE {main.get('n', 1)} LÖST AUF"}.get(kind_, "")
    marks = tuple(Mark(k, where(e), _locate(e["rc"], e.get("zt", 0), e.get("z"), frame, prev_frame, moves_)
                       if k == "angriff" and _attacks_card(e) else None)
                  for k, e in found if k in MARK_KINDS)[:8]
    return kind_, focus, " · ".join(parts), label, marks


def _quiet(state: Optional[dict]) -> dict:
    """Zustand ohne die geratene Aktivierung des Trackers (mit Protokoll kommt sie aus dem Protokoll)."""
    return dict(state or {}, activating=None)


def build_steps(frames: List[dict], states: Sequence[dict], my_id: int,
                name_of: Callable[[int], Optional[str]] = lambda card: None,
                type_of: Callable[[int], str] = lambda card: "") -> List[Step]:
    """steps[i] = der Übergang von Frame i-1 zu Frame i (steps[0] ist leer)."""
    out = []
    logged = any(f.get("log") for f in frames)   # Mitschnitt mit echtem Protokoll
    chains = chain_states(frames) if logged else []
    for i, frame in enumerate(frames):
        prev = frames[i - 1] if i else None
        ms = moves(prev, frame) if i else []
        state = states[i] if i < len(states) else None
        prev_state = states[i - 1] if i and i - 1 < len(states) else None
        if logged:
            state, prev_state = _quiet(state), _quiet(prev_state)
        kind_, focus = _classify(ms, state, prev_state)
        # Beschwörung steht schon im Protokoll der letzten Frames: die Karte gleitet nur noch (leerer "zug")
        seen = {e.get("c") for f in frames[max(0, i - 3):i] for e in f.get("log") or ()
                if e.get("k") == "act" and e.get("a") in CODE_SUMMON} if logged else ()
        again = kind_ == "beschworen" and bool(focus.card) and focus.card in seen
        from_log = _log_step(frame, prev, chains[i], ms, name_of, type_of, my_id) if logged and i else None
        lp = {}
        for team in (0, 1):
            before, after = ((prev or {}).get("lp") or {}).get(str(team)), (frame.get("lp") or {}).get(str(team))
            if i and before is not None and after is not None and before != after:
                lp[team] = after - before
        card_type = type_of(focus.card) if focus is not None and focus.card else ""
        label = {"aktiviert": activation_label, "beschworen": lambda t: summon_label(t).upper() or "BESCHWOREN",
                 "gesetzt": lambda t: "GESETZT"}.get(kind_, lambda t: "")(card_type)
        text, marks = describe(focus, kind_, name_of, my_id, frame.get("phase") or "", type_of), ()
        if from_log is not None:
            kind_, focus, text, label, marks = from_log
        elif again:
            kind_, text, label = "zug", "", ""
        out.append(Step(kind_, text, tuple(ms), focus, lp, label, duration(kind_, bool(lp), glides(kind_, focus)),
                        marks, chains[i] if logged else None))
    for i in range(len(out) - 1 if logged else 0):
        a, b = out[i], out[i + 1]
        f, g = a.focus, b.focus
        # das Protokoll meldet die Aktivierung einen Frame vor dem Platzwechsel (Hand → Zone): sie gehört in diesen
        # Schritt, damit erst die Karte aufs Feld kommt und dann aktiviert wird (der alte Schritt wird unsichtbar)
        # shortcut: nur ein direkter Wechsel in die Zone im nächsten Frame (nicht über "unterwegs"), sonst wie bisher
        if (a.kind == "aktiviert" and f.uid != -1 and f.from_zone == f.to_zone and f.to_zone not in FIELD
                and b.kind == "zug" and g is not None and g.uid == f.uid and glides("aktiviert", g)):
            out[i] = a._replace(kind="still", text="", focus=None, label="", marks=(),
                                dur=duration("still", bool(a.lp)))
            out[i + 1] = b._replace(kind="aktiviert", text=a.text, label=a.label, marks=a.marks,
                                    dur=duration("aktiviert", bool(b.lp), True))
    # Aufdecken ist keine eigene Zeile: nach dem Setzen zeigt MD die Karte und dreht sie zurück, vor dem Aktivieren
    # gehört es zur Aktivierung (die zeigt die Karte offen)
    for i, s in enumerate(out):
        near = (("gesetzt", out[max(0, i - 3):i]), ("aktiviert", out[i + 1:i + 4]))
        if s.kind == "umgedreht" and any(o.kind == k and o.focus is not None and o.focus.uid == s.focus.uid
                                         for k, os_ in near for o in os_):
            out[i] = s._replace(kind="still", text="", focus=None, label="", marks=(),
                                dur=duration("still", bool(s.lp)))
    return out
