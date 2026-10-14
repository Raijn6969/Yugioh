"""
Replay-Mitschnitt: Schaut man in Master Duel ein Replay aus der Match History an, nimmt der Importer jede Änderung
am Spielfeld auf (nur lesend) und speichert sie. Danach lässt sich das Duell beliebig oft in der Duell-Übersicht
abspielen – ohne die Grenze der Replay-Speicherplätze im Spiel.

Gelesen wird NUR während eines Replays aus der Match History: oberstes Menü "DuelClient" und direkt darunter
"ColosseumHistoryViewController". In einem echten Duell liegt darunter das Menü des Modus (z.B. Ranked) – dann liest
der Rekorder außer der Menüliste nichts (wie match_history).

Je Schritt: alle Karten, die das Spiel zeigt (md_memory.duel_board: Spieler, Zone, Platz, Karte, offen, Position,
ATK/DEF), Lebenspunkte und Zug/Phase vom Phasen-Knopf. Zu Beginn aus ClientWork["Duel"]: Startzeit (ordnet das
Replay dem Match zu), eigene Spieler-Nummer, wer anfängt, Namen und beide Decks.
"""

import threading
import time
from typing import Callable, Dict, List, NamedTuple, Optional

from debug_log import dlog

REPLAY_PARENTS = ("ColosseumHistoryViewController",)  # Replay aus der Match History
DUEL_VIEW = "DuelClient"
POLL = 0.25          # Sekunden zwischen zwei Blicken aufs Feld während eines Replays
IDLE = 1.5           # … sonst (nur die Menüliste)
MIN_STEPS = 3        # kürzere Mitschnitte (sofort abgebrochen) werden nicht gespeichert

# Zonen (Konstanten YgomGame.Duel.Engine.Pos*, aus den Metadaten des Spiels)
MONSTER_ZONES = range(0, 5)
EXTRA_MONSTER_ZONES = (5, 6)
SPELL_ZONES = range(7, 12)
FIELD_ZONE, HAND, EXTRA_DECK, DECK, GRAVE, BANISHED, MOVING = 12, 13, 14, 15, 16, 17, 18


class ReplayInfo(NamedTuple):
    start: int                 # Startzeit des Duells (wie "time" der Match History) → Zuordnung zum Match
    replay_did: str            # ID des Replays (nicht die Duell-ID der Match History)
    my_id: int                 # eigene Spieler-Nummer (team der eigenen Karten)
    first_player: Optional[int]
    names: List[str]
    decks: List[dict]          # je Spieler {"main": [Konami-IDs], "extra": [...]}


def is_replay(views: List[str]) -> bool:
    return len(views) >= 2 and views[-1] == DUEL_VIEW and views[-2] in REPLAY_PARENTS


def replay_info(duel) -> Optional[ReplayInfo]:
    """ClientWork["Duel"] während des Replays → ReplayInfo (None, solange noch nicht geladen)."""
    if not isinstance(duel, dict) or duel.get("MyID") not in (0, 1) or not duel.get("time"):
        return None
    decks = []
    for deck in duel.get("Deck") if isinstance(duel.get("Deck"), list) else []:
        part = lambda key: [int(c) for c in ((deck or {}).get(key) or {}).get("CardIds") or []  # noqa: E731
                            if isinstance(c, int) and c > 0]
        decks.append({"main": part("Main"), "extra": part("Extra")})
    first = duel.get("FirstPlayer")
    return ReplayInfo(int(duel["time"]), str(duel.get("did") or ""), duel["MyID"],
                      first if first in (0, 1) else None, [str(n) for n in duel.get("name") or []], decks)


def frame(board, lp: Dict[int, int], turn: str, phase: str) -> dict:
    """Ein Schritt: Karten (nach uid sortiert, als Listen – kompakt gespeichert), LP, Zug/Phase."""
    cards = sorted([c.uid, c.team, c.zone, c.index, c.card, c.face, c.attack, c.atk, c.def_] for c in board)
    return {"cards": cards, "lp": {str(k): v for k, v in sorted(lp.items())}, "turn": turn, "phase": phase}


class ReplayRecorder:
    """
    Hintergrund-Thread wie match_history.MatchWatcher. on_saved(ReplayInfo, Schritte) kommt aus diesem Thread.
    """

    def __init__(self, get_db: Callable[[], object], connected: Callable[[], bool], memory: Callable[[], object],
                 on_saved: Callable[[ReplayInfo, int], None] = lambda info, steps: None,
                 on_started: Callable[[], None] = lambda: None, clock: Callable[[], float] = time.monotonic):
        self.get_db = get_db
        self.connected = connected
        self.memory = memory
        self.on_saved = on_saved
        self.on_started = on_started
        self.clock = clock
        self.enabled = True
        self.info: Optional[ReplayInfo] = None
        self.frames: List[dict] = []
        self._last = None
        self._t0 = 0.0
        self._stop = threading.Event()

    @property
    def recording(self) -> bool:
        return self.info is not None

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="replay").start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(POLL if self.recording else IDLE):
            try:
                self.check()
            except Exception as e:  # Replay lädt gerade, Objekte im Umbau … → beim nächsten Mal
                dlog(f"[REPLAY] nicht gelesen: {e}")

    def check(self) -> None:
        if not (self.enabled and self.connected()):
            self.finish()
            return
        memory = self.memory()
        if not is_replay(memory.open_views()):
            self.finish()  # Replay zu Ende (oder nie eins) – im Duell selbst wird hier nichts gelesen
            return
        if self.info is None:
            info = replay_info(memory.client_work("Duel"))
            if info is None:
                return
            self.info, self.frames, self._last, self._t0 = info, [], None, self.clock()
            dlog(f"[REPLAY] Mitschnitt gestartet (Start {info.start}).")  # keine Spielernamen ins Log (Fehlerbericht)
            self.on_started()
        worker = memory.duel_worker()
        if not worker:
            return
        turn, phase = memory.duel_phase(worker)
        step = frame(memory.duel_board(worker), memory.duel_lp(worker), turn, phase)
        if step != self._last:
            self._last = step
            self.frames.append(dict(step, t=round(self.clock() - self._t0, 2)))

    def finish(self) -> None:
        """Mitschnitt beenden und speichern (zu kurze verwerfen)."""
        info, frames = self.info, self.frames
        self.info, self.frames, self._last = None, [], None
        if info is None or len(frames) < MIN_STEPS:
            return
        db = self.get_db()
        if db is None:
            return
        if db.save_replay(info, frames):
            dlog(f"[REPLAY] Mitschnitt gespeichert: {len(frames)} Schritte.")
            self.on_saved(info, len(frames))
