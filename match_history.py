"""
Winrate-Tracker: erfasst die eigenen Duelle aus Master Duel (nur Speicher-Modus, nur lesend).

Master Duel hält Server-Antworten im Speicher (ClientWork, siehe md_memory). Der Wächter schaut regelmäßig nur,
welches Menü vorne ist, und liest Daten ausschließlich auf Menü-Bildschirmen – nie während eines Duells:
  - Deck-Auswahl: Beim Durchblättern der Decks liegen ihre Karten in "DeckList", Namen in "Deck/list". Jedes Deck
    wird mit seiner ID gemerkt (Datenbank, md_decks) – welches gewählt ist, steht nur als ID ("Deck/maindeck_id").
    Ein Deck muss also nur einmal in der Deck-Auswahl angesehen werden; danach reicht die ID.
  - Münzwurf (DuelStartViewController): "Duel" sagt, wer den Münzwurf gewonnen hat ("choice"); nach der Wahl kommt
    "pvp_choice" aus eigener Sicht: 1 = man ist Zweiter, 0 = man fängt an – egal, wer gewählt hat und welche
    Spieler-Nummer man hat (live geprüft: gewonnen/verloren, myid 0 und 1). Kurz vor dem Duell (oft nur Bruchteile
    einer Sekunde, nicht immer zu sehen) ersetzt das Spiel
    "Duel" durch die Duell-Einstellungen: "FirstPlayer" (wer anfängt – auch wenn man selbst gewählt hat), "Choice"
    (Münzwurf-Gewinner), "MyID", "did" (Duell-ID) und das eigene Deck ("Deck"[MyID], Konami-IDs). Darum wird dort
    häufiger nachgesehen.
  - Ergebnis-Bildschirm: "DuelResult" mit Duell-ID, Modus und Ergebnis → Match wird gespeichert ("live").
  - Match History (falls man sie öffnet): die letzten Duelle vollständig (Zugzahl, beide Decks, wer angefangen hat).
    Ergänzt fehlende Duelle und ersetzt Live-Einträge derselben Duell-ID.
Das eigene Deck wird nach seinen Archetypen benannt wie im Verlauf, dazu der Name in Master Duel.
"""

import threading
import time
from typing import Callable, Dict, List, NamedTuple, Optional

from debug_log import dlog

HISTORY_VIEW = "ColosseumHistoryViewController"  # Menü "Match History"
COIN_TOSS_VIEW = "DuelStartViewController"        # Münzwurf vor dem Duell (noch Menü)
RESULT_VIEW = "DuelResultViewController"          # Ergebnis nach dem Duell
DUEL_VIEWS = ("DuelClient",)                      # das Duell selbst: dann nichts lesen
CHECK_INTERVAL = 1.5   # Sekunden zwischen zwei Blicken auf die offenen Menüs
COIN_TOSS_INTERVAL = 0.2  # … solange der Münzwurf vorne ist (nach der Wahl bleibt er manchmal < 1 s)
REREAD_INTERVAL = 6.0  # So oft wird die Match History bei offenem Menü erneut gelesen (Server-Antwort kommt später)
PENDING_MAX_AGE = 3 * 3600.0  # Münzwurf-Info gilt höchstens so lange für das nächste Ergebnis
SAME_TOSS = 120.0  # Sekunden: so lange gehört eine schon gelesene Wahl noch zum selben Münzwurf

# Spielmodi (Enum GameMode des Spiels)
FREE, RANKED, ROOM, RATING = 1, 3, 10, 19
GAME_MODES = {1: "Frei", 3: "Ranked", 4: "Turnier", 10: "Raum", 11: "Exhibition", 12: "Duelist Cup", 13: "Event",
              14: "Team", 15: "Duel Trial", 16: "WCS", 17: "Versus", 18: "WCS-Finale", 19: "Rating", 20: "RDC",
              21: "Dice Rally"}
MAIN_DECK_MODES = (FREE, RANKED, ROOM)  # Modi mit dem normal gewählten Deck (andere: Event-/Leih-Decks)
# Ergebnis (Enum ResultType) und Spielende (Enum FinishType, Auswahl)
WIN, LOSS, DRAW = 1, 2, 3
FINISH_SURRENDER = 4
PVP_SECOND = 1  # pvp_choice.choice aus eigener Sicht: 1 = Zweiter, 0 = Erster


class MatchRecord(NamedTuple):
    """Ein Duell (Karten als Konami-IDs)."""
    did: str
    played_at: float
    mode: int
    result: int                # WIN, LOSS, DRAW
    first: Optional[bool]      # selbst angefangen? None = unbekannt
    turns: int                 # 0 = unbekannt (live erfasst)
    finish: int
    my_main: List[int]
    my_extra: List[int]
    opp_main: List[int]        # leer = unbekannt (live erfasst)
    opp_extra: List[int]
    md_deck: str = ""          # Name des eigenen Decks in Master Duel
    source: str = "history"    # "live" oder "history"
    coin: Optional[bool] = None  # Münzwurf gewonnen? None = unbekannt (nur live erfasst, nicht in der Match History)


def mode_name(mode: int) -> str:
    return GAME_MODES.get(mode, f"Modus {mode}")


def _ints(values) -> List[int]:
    return [int(v) for v in values or [] if isinstance(v, int) and v > 0]


def _cards(deck, part: str) -> List[int]:
    return _ints(((deck or {}).get(part) or {}).get("CardIds"))


def parse_history(data) -> List[MatchRecord]:
    """ClientWork["DuelHistory"] = {Saison/Event: {Nr.: Duell}} → Matches, jedes Duell einmal."""
    records: Dict[str, MatchRecord] = {}
    for group in (data or {}).values():
        if not isinstance(group, dict):
            continue  # z.B. "replay_limit_ts"
        for entry in group.values():
            try:
                did, me = entry.get("did"), entry.get("myid")
                decks = entry.get("deck") or []
                if did is None or me not in (0, 1) or len(decks) != 2 or entry.get("invalid"):
                    continue
                first_player = entry.get("first_player")
                records[str(did)] = MatchRecord(
                    str(did), float(entry.get("time") or 0), int(entry.get("mode") or 0), int(entry.get("res") or 0),
                    first_player == me if first_player in (0, 1) else None, int(entry.get("turn") or 0),
                    int(entry.get("finish") or 0), _cards(decks[me], "Main"), _cards(decks[me], "Extra"),
                    _cards(decks[1 - me], "Main"), _cards(decks[1 - me], "Extra"))
            except (AttributeError, TypeError, ValueError):
                continue  # unerwarteter Eintrag (Spiel-Update?) → überspringen
    return sorted(records.values(), key=lambda r: r.played_at)


class CoinToss(NamedTuple):
    coin: Optional[bool]               # Münzwurf gewonnen?
    first: Optional[bool]              # selbst angefangen? (None = noch nicht gewählt)
    did: Optional[str] = None          # Duell-ID (erst in den Duell-Einstellungen)
    main: Optional[List[int]] = None   # eigenes Deck (Konami-IDs, aus den Duell-Einstellungen)
    extra: Optional[List[int]] = None


def coin_toss(duel) -> Optional[CoinToss]:
    """
    ClientWork["Duel"] beim Münzwurf → wer gewonnen hat und wer anfängt; None ohne Münzwurf.
    Duell-Einstellungen (kurz vor dem Duell): "FirstPlayer", "Choice", "MyID", "did", "Deck".
    Vorher: "choice" = Münzwurf-Gewinner, "pvp_choice"/"choice" = ob man selbst Zweiter ist (1) oder anfängt (0).
    """
    if not isinstance(duel, dict):
        return None
    me = duel.get("MyID")
    if me in (0, 1) and duel.get("FirstPlayer") in (0, 1):
        chooser = duel.get("Choice")
        decks = duel.get("Deck") if isinstance(duel.get("Deck"), list) else []
        mine = decks[me] if len(decks) == 2 and isinstance(decks[me], dict) else {}
        main, extra = _cards(mine, "Main"), _cards(mine, "Extra")
        did = duel.get("did")
        return CoinToss(chooser == me if chooser in (0, 1) else None, duel["FirstPlayer"] == me,
                        str(did) if did is not None else None, main or None, extra if main else None)
    me, chooser = duel.get("myid"), duel.get("choice")
    if me not in (0, 1) or chooser not in (0, 1):
        return None
    pick = (duel.get("pvp_choice") or {}).get("choice")
    if not isinstance(pick, int):
        return CoinToss(chooser == me, None)
    return CoinToss(chooser == me, pick != PVP_SECOND)


def md_decks(deck_list, deck_names) -> list:
    """ClientWork["DeckList"] (Karten) + ["Deck"]["list"] (Namen) → [MdDeck]."""
    from card_stats import MdDeck
    names = {str(k): (v or {}).get("name", "") for k, v in (deck_names or {}).items() if isinstance(v, dict)}
    result = []
    for deck_id, deck in (deck_list or {}).items():
        if isinstance(deck, dict) and isinstance(deck.get("m"), dict):
            result.append(MdDeck(str(deck_id), names.get(str(deck_id), ""), _ints(deck["m"].get("ids")),
                                 _ints((deck.get("e") or {}).get("ids"))))
    return result


def live_record(result, deck, first: Optional[bool], now: float,
                coin: Optional[bool] = None) -> Optional[MatchRecord]:
    """ClientWork["DuelResult"] + gewähltes Deck (MdDeck oder None) → Match; None ohne Duell-ID/Ergebnis."""
    if not isinstance(result, dict):
        return None
    did = (result.get("replayButton") or {}).get("did")
    outcome = (result.get("resultInfo") or {}).get("result")
    if did is None or outcome not in (WIN, LOSS, DRAW):
        return None
    return MatchRecord(str(did), now, int(result.get("mode") or 0), outcome, first, 0, 0,
                       deck.main if deck else [], deck.extra if deck else [], [], [],
                       deck.name if deck else "", "live", coin)


def store_matches(db, records: List[MatchRecord]) -> List[MatchRecord]:
    """
    Matches speichern (Decks als Passcodes, benannt nach ihren Archetypen). Neue Duell-IDs kommen dazu; ein
    Live-Eintrag wird durch denselben Eintrag aus der Match History ersetzt. Returns: die neuen Duelle.
    """
    from deck_export import build_ydk  # erst hier: zieht Bildschirm-Module nach
    unique: Dict[str, MatchRecord] = {}
    for record in records:  # dasselbe Duell live und aus der Match History → die vollständige Fassung
        if record.did not in unique or record.source == "history":
            live = unique.get(record.did, record)  # Münzwurf und MD-Deckname kennt nur der Live-Eintrag
            unique[record.did] = record._replace(md_deck=record.md_deck or live.md_deck,
                                                 coin=live.coin if record.coin is None else record.coin)
    records = list(unique.values())
    known = db.match_sources(r.did for r in records)
    todo = [r for r in records if r.did not in known or (known[r.did] == "live" and r.source == "history")]
    if not todo:
        return []
    passcodes = db.konami_passcodes()

    def describe(main: List[int], extra: List[int]):
        main, extra = [passcodes[k] for k in main if k in passcodes], [passcodes[k] for k in extra if k in passcodes]
        if not main + extra:
            return "", ""
        db.ensure(main + extra)
        return db.deck_name(main + extra), build_ydk(main, extra)

    rows = [(r, *describe(r.my_main, r.my_extra), *describe(r.opp_main, r.opp_extra)) for r in todo]
    db.add_matches(rows)
    return [r for r in todo if r.did not in known]


class MatchWatcher:
    """
    Hintergrund-Thread: Solange der Speicher-Modus verbunden ist (connected), schaut er alle CHECK_INTERVAL
    Sekunden, welches Menü vorne ist, und liest dann die passenden Daten (siehe oben). on_new(neue Matches) kommt aus
    diesem Thread. Bewusst ohne Verweis aufs Overlay (wie EditorWatcher).
    """

    def __init__(self, get_db: Callable[[], object], on_new: Callable[[List[MatchRecord]], None],
                 connected: Callable[[], bool], memory: Callable[[], object], clock: Callable[[], float] = time.time):
        self.get_db = get_db
        self.on_new = on_new
        self.connected = connected   # Speicher-Verbindung steht (md_memory.is_ready)
        self.memory = memory         # md_memory.shared
        self.clock = clock
        self.enabled = True
        self._started = False        # Daten vom Programmstart (letzte Match History, letztes Ergebnis) übernommen?
        self._views: List[str] = []
        self._last_history = 0.0
        self._pending: Optional[CoinToss] = None  # vom Münzwurf, bis zum Ergebnis
        self._pending_deck = None                  # gewählte Deck-ID beim Münzwurf
        self._pending_at = 0.0
        self._stop = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="matches").start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(COIN_TOSS_INTERVAL if self._views[-1:] == [COIN_TOSS_VIEW] else CHECK_INTERVAL):
            try:
                self.check()
            except Exception as e:  # Spiel lädt gerade, Menü im Aufbau … → beim nächsten Mal
                dlog(f"[MATCHES] nicht gelesen: {e}")

    def check(self) -> int:
        """Ein Durchgang. Returns: Anzahl neu erfasster Matches."""
        if not (self.enabled and self.connected()):
            return 0
        memory = self.memory()
        views = memory.open_views()
        top = views[-1] if views else ""
        changed, self._views = views != self._views, views
        if not top or top in DUEL_VIEWS:
            return 0  # im Duell bzw. Ladebildschirm: nichts lesen
        if top == COIN_TOSS_VIEW:
            toss = coin_toss(memory.client_work("Duel"))
            if toss is not None:
                old = self._pending
                if old is not None and self.clock() - self._pending_at <= SAME_TOSS and old.did in (None, toss.did):
                    # derselbe Münzwurf (nicht der eines abgebrochenen Duells): Bekanntes nicht wieder vergessen
                    toss = toss._replace(coin=old.coin if toss.coin is None else toss.coin,
                                         first=old.first if toss.first is None else toss.first)
                if old is None or old[:3] != toss[:3]:
                    dlog(f"[MATCHES] Münzwurf {({True: 'gewonnen', False: 'verloren'}).get(toss.coin, '?')}"
                         + {True: ", Erster", False: ", Zweiter"}.get(toss.first, "")
                         + (f" (Duell {toss.did})" if toss.did else ""))
                self._pending, self._pending_at = toss, self.clock()
                self._pending_deck = memory.client_work("Deck", "maindeck_id")
            return 0
        db = self.get_db()
        if db is None:
            return 0
        if changed or not self._started:
            decks = md_decks(memory.client_work("DeckList"), memory.client_work("Deck", "list"))
            if decks:
                db.remember_md_decks(decks)
        records = []
        if top == RESULT_VIEW or changed or not self._started:
            record = self._live_record(memory, db)
            if record is not None:
                records.append(record)
        now = time.monotonic()
        if not self._started or (top == HISTORY_VIEW and (changed or now - self._last_history >= REREAD_INTERVAL)):
            self._last_history = now
            records += parse_history(memory.client_work("DuelHistory"))
        self._started = True
        new = store_matches(db, records) if records else []
        if new:
            dlog(f"[MATCHES] {len(new)} neue(s) Match(es) erfasst.")
            self.on_new(new)
        return len(new)

    def _live_record(self, memory, db) -> Optional[MatchRecord]:
        result = memory.client_work("DuelResult")
        did = ((result or {}).get("replayButton") or {}).get("did") if isinstance(result, dict) else None
        if did is None or str(did) in db.match_sources([str(did)]):
            return None
        mode = int(result.get("mode") or 0)
        toss, deck_id = CoinToss(None, None), None
        pending = self._pending
        if (pending is not None and self.clock() - self._pending_at <= PENDING_MAX_AGE
                and pending.did in (None, str(did))):  # mit Duell-ID: sicher dieses Duell
            toss, deck_id = pending, self._pending_deck
        self._pending = None  # gehört zu diesem Duell (bzw. zu einem abgebrochenen)
        if toss.coin is None or toss.first is None:
            dlog(f"[MATCHES] Münzwurf für Duell {did} nicht vollständig gelesen "
                 f"(Münzwurf {toss.coin}, Erster {toss.first})")
        if mode == RATING:
            deck_id = memory.client_work("Deck", "ratedeck_id")
        elif mode in MAIN_DECK_MODES and deck_id is None:
            deck_id = memory.client_work("Deck", "maindeck_id")
        deck = db.md_deck(str(deck_id)) if deck_id and mode in MAIN_DECK_MODES + (RATING,) else None
        if toss.main:  # Karten aus den Duell-Einstellungen: auch ohne Blick in die Deck-Auswahl bekannt
            from card_stats import MdDeck
            deck = MdDeck(str(deck_id or ""), deck.name if deck else "", toss.main, toss.extra or [])
        return live_record(result, deck, toss.first, self.clock(), toss.coin)
