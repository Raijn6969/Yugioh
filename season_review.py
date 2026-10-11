"""
Monatsrückblick (Knopf "Rückblick" im Winrate-Fenster): die Bilanz einer Season, also eines Kalendermonats – wie der
Zeitraum-Filter "Saison" im Winrate-Fenster.

Inhalt: Bilanz und Winrate gesamt, als Erster und als Zweiter; die meistgespielten Decks; das beste und das schlechteste
Matchup gegen ein Gegner-Deck (mindestens MIN_MATCHUP Duelle); der Münzwurf; die längste Sieg- und Niederlagenserie; die
Zahl der Duelle und der aktivste Tag. Gegner kommen nur als Deck-Namen vor (aus den Archetypen) – der Importer
speichert in den Matches keine Spielernamen.

Hier steht nur die Auswertung; angezeigt wird sie von review_panel, als HTML-Datei von review_export. Dazu der einmalige
Hinweis beim ersten Start in einem neuen Monat (pending_notice).
"""

import datetime
import time
from typing import List, NamedTuple, Optional, Tuple

from card_stats import DeckWinStats, OpponentStats, WinStats
from match_history import DRAW, LOSS, WIN

DAY_START_HOUR = 5     # ein Spieltag beginnt um 5 Uhr: Wer nach Mitternacht weiterspielt, bleibt im selben Tag
MIN_MATCHUP = 3        # so viele Duelle braucht ein Gegner-Deck für "bestes/schlechtestes Matchup"
DECK_LIMIT = 5         # so viele Decks (die meistgespielten) stehen im Rückblick
EARLY_DAYS = 3         # in den ersten Tagen eines Monats zeigt der Rückblick den Vormonat
MATCH_LIMIT = 50000
NOTICE_KEY = "REVIEW_NOTIFIED"  # Einstellung: Monat ("2026-09"), für den der Hinweis "Rückblick ist fertig" kam
MONTHS = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November",
          "Dezember")
WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")


class Day(NamedTuple):
    date: datetime.date
    duels: int
    wins: int
    draws: int

    @property
    def losses(self) -> int:
        return self.duels - self.wins - self.draws


class Review(NamedTuple):
    year: int
    month: int
    total: WinStats
    decks: List[DeckWinStats] = []        # die meistgespielten (höchstens DECK_LIMIT)
    deck_count: int = 0                   # so viele verschiedene Decks insgesamt
    best: Optional[OpponentStats] = None  # bestes Matchup (mindestens MIN_MATCHUP Duelle)
    worst: Optional[OpponentStats] = None  # schlechtestes (nie dasselbe Deck wie das beste)
    win_streak: int = 0
    loss_streak: int = 0
    busiest: Optional[Day] = None


def month_name(year: int, month: int) -> str:
    return f"{MONTHS[month - 1]} {year}"


def month_key(year: int, month: int) -> str:
    return f"{year}-{month:02d}"


def previous_month(year: int, month: int) -> Tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def next_month(year: int, month: int) -> Tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def month_bounds(year: int, month: int) -> Tuple[float, float]:
    """(Anfang, Ende) des Kalendermonats als Unix-Zeit der lokalen Uhr; das Ende gehört schon zum nächsten Monat."""
    start = datetime.datetime(year, month, 1)
    end = datetime.datetime(*next_month(year, month), 1)
    return start.timestamp(), end.timestamp()


def default_month(now: Optional[float] = None) -> Tuple[int, int]:
    """Der aktuelle Monat; in den ersten EARLY_DAYS Tagen der Vormonat (der ist dann gerade fertig geworden)."""
    current = datetime.datetime.fromtimestamp(time.time() if now is None else now)
    return previous_month(current.year, current.month) if current.day <= EARLY_DAYS else (current.year, current.month)


def game_day(played_at: float) -> datetime.date:
    return (datetime.datetime.fromtimestamp(played_at) - datetime.timedelta(hours=DAY_START_HOUR)).date()


def streaks(results: List[int]) -> Tuple[int, int]:
    """(längste Siegserie, längste Niederlagenserie) einer Ergebnisliste in zeitlicher Reihenfolge; ein Unentschieden
    unterbricht beide Serien."""
    best_win = best_loss = win = loss = 0
    for result in results:
        win, loss = (win + 1, 0) if result == WIN else (0, loss + 1) if result == LOSS else (0, 0)
        best_win, best_loss = max(best_win, win), max(best_loss, loss)
    return best_win, best_loss


def busiest_day(matches) -> Optional[Day]:
    """Der Spieltag mit den meisten Duellen (bei Gleichstand der früheste)."""
    days = {}
    for match in matches:
        day = game_day(match.played_at)
        duels, wins, draws = days.get(day, (0, 0, 0))
        days[day] = (duels + 1, wins + (match.result == WIN), draws + (match.result == DRAW))
    if not days:
        return None
    date = min(days, key=lambda d: (-days[d][0], d))
    return Day(date, *days[date])


def matchups(opponents: List[OpponentStats]) -> Tuple[Optional[OpponentStats], Optional[OpponentStats]]:
    """(bestes, schlechtestes) Matchup unter den Gegner-Decks mit mindestens MIN_MATCHUP Duellen. Gleiche Winrate:
    das mit mehr Duellen zählt stärker. Gibt es nur ein solches Deck, ist es das beste."""
    enough = [o for o in opponents if o.stats.matches >= MIN_MATCHUP]
    if not enough:
        return None, None
    rate = lambda o: o.stats.wins / o.stats.matches  # noqa: E731
    best = max(enough, key=lambda o: (rate(o), o.stats.matches, o.opp_name))
    rest = [o for o in enough if o is not best]
    worst = min(rest, key=lambda o: (rate(o), -o.stats.matches, o.opp_name)) if rest else None
    return best, worst


def build(db, year: int, month: int, mode: Optional[int] = None) -> Review:
    """Rückblick auf einen Monat (mode: Ranked-Kennung bzw. None = alle Modi, wie im Winrate-Fenster)."""
    start, end = month_bounds(year, month)
    total = db.win_stats(None, mode, start, end)
    if not total.matches:
        return Review(year, month, total)
    decks = sorted(db.deck_win_stats(mode, start, end), key=lambda d: (-d.stats.matches, d.deck_name))
    matches = db.matches(None, mode, MATCH_LIMIT, start, end)           # das letzte zuerst
    win_streak, loss_streak = streaks([m.result for m in reversed(matches)])
    best, worst = matchups(db.opponent_stats(None, mode, start, end))
    return Review(year, month, total, decks[:DECK_LIMIT], len(decks), best, worst, win_streak, loss_streak,
                  busiest_day(matches))


def pending_notice(db, notified: Optional[str], now: Optional[float] = None) -> Optional[Tuple[int, int]]:
    """Gibt es einen Rückblick, auf den noch nicht hingewiesen wurde? Der Vormonat, falls es darin Duelle gab und
    `notified` (der zuletzt gemeldete Monat, "2026-09") nicht schon er oder neuer ist."""
    current = datetime.datetime.fromtimestamp(time.time() if now is None else now)
    year, month = previous_month(current.year, current.month)
    if notified and notified >= month_key(year, month):
        return None
    start, end = month_bounds(year, month)
    return (year, month) if db.win_stats(None, None, start, end).matches else None


def rate_text(wins: int, matches: int) -> str:
    return f"{wins / matches * 100:.0f} %" if matches else "–"


def record_text(stats) -> str:
    """'13–8' bzw. '13–8–1' mit Unentschieden (WinStats oder Day)."""
    wins, losses = stats.wins, stats.losses
    draws = stats.draws
    return f"{wins}–{losses}" + (f"–{draws}" if draws else "")


def day_text(day: Day) -> str:
    """'Sa 03.10.' """
    return f"{WEEKDAYS[day.date.weekday()]} {day.date.strftime('%d.%m.')}"
