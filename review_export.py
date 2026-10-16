"""
Monatsrückblick als HTML exportieren (Knopf "Als HTML" im Rückblick): eine einzige Datei zum Verschicken, die in jedem
Browser läuft – auch am Handy, offline, ohne Master Duel und ohne Importer. Dunkler Look wie der Replay-Export.

Gegner kommen nur als Deck-Namen vor (z.B. "Sky Striker"); Spielernamen kennt die Auswertung gar nicht.
"""

import html
import os
from typing import Optional

from app_paths import APP_VERSION
from deck_export import downloads_dir
from season_review import Review, day_text, month_name, rate_text, record_text

GOOD, WARN = "var(--good)", "var(--warn)"


def file_name(review: Review) -> str:
    return f"MD-Importer_Rueckblick_{review.year}-{review.month:02d}.html"


def _color(wins: int, matches: int) -> str:
    return WARN if not matches or wins / matches < 0.5 else GOOD


def _tile(value: str, caption: str, note: str = "", color: str = "var(--text)") -> str:
    return (f'<div class="tile"><b style="color:{color}">{html.escape(value)}</b>'
            f'<span>{html.escape(caption)}</span><em>{html.escape(note)}</em></div>')


def _rate_tile(caption: str, wins: int, matches: int) -> str:
    note = f"{wins}–{matches - wins} · {matches} Matches" if matches else "keine Matches"
    return _tile(rate_text(wins, matches), caption, note, _color(wins, matches) if matches else "var(--text)")


def _bar(wins: int, matches: int) -> str:
    width = round(wins / matches * 100) if matches else 0
    return f'<div class="bar"><i style="width:{width}%;background:{_color(wins, matches)}"></i></div>'


def _matchup(label: str, opponent) -> str:
    stats = opponent.stats
    return (f'<tr><td>{html.escape(label)}</td><td><b>{html.escape(opponent.opp_name)}</b></td>'
            f'<td>{html.escape(record_text(stats))} · {stats.matches} Matches</td>'
            f'<td style="color:{_color(stats.wins, stats.matches)}">{rate_text(stats.wins, stats.matches)}</td></tr>')


def render(review: Review, mode_label: str = "") -> str:
    """Die komplette HTML-Seite (mode_label: z.B. "Ranked" bzw. "alle Modi")."""
    title = f"Rückblick {month_name(review.year, review.month)}"
    total = review.total
    sub = " · ".join(part for part in (mode_label, "Season = ein Kalendermonat") if part)
    body = []
    if not total.matches:
        body.append('<p class="empty">In diesem Monat gibt es keine erfassten Matches.</p>')
    else:
        body.append('<section><h2>Bilanz</h2><div class="tiles">'
                    + _tile(str(total.matches), "Matches", f"{record_text(total)} (Siege–Niederlagen)")
                    + _rate_tile("Winrate gesamt", total.wins, total.matches)
                    + _rate_tile("als Erster", total.first_wins, total.first)
                    + _rate_tile("als Zweiter", total.second_wins, total.second) + "</div></section>")
        if total.coin_known:
            coin = ('<div class="tiles">' + _rate_tile("Münzwurf gewonnen", total.coin_won, total.coin_known)
                    + _rate_tile("Siege nach gewonnenem Wurf", total.coin_won_wins, total.coin_won)
                    + _rate_tile("Siege nach verlorenem Wurf", total.coin_lost_wins, total.coin_lost) + "</div>")
        else:
            coin = '<p class="note">Der Münzwurf wurde in diesem Monat nicht erfasst.</p>'
        body.append("<section><h2>Münzwurf</h2>" + coin + "</section>")
        rows = "".join(
            f'<tr><td><b>{html.escape(deck.deck_name or "Unbekanntes Deck")}</b></td><td>{deck.stats.matches}</td>'
            f'<td>{html.escape(record_text(deck.stats))}</td>'
            f'<td style="color:{_color(deck.stats.wins, deck.stats.matches)}">'
            f'{rate_text(deck.stats.wins, deck.stats.matches)}</td>'
            f'<td class="w">{_bar(deck.stats.wins, deck.stats.matches)}'
            f'</td></tr>' for deck in review.decks)
        more = (f' (die {len(review.decks)} meistgespielten von {review.deck_count})'
                if review.deck_count > len(review.decks) else "")
        body.append(f'<section><h2>Meistgespielte Decks{more}</h2><table><tr><th>Deck</th><th>Matches</th>'
                    f'<th>Bilanz</th><th>Winrate</th><th></th></tr>{rows}</table></section>')
        if review.best is None:
            matchups = '<p class="note">Noch zu wenige Matches: ein Gegner-Deck braucht mindestens 3.</p>'
        else:
            matchups = ("<table>" + _matchup("Bestes Matchup", review.best)
                        + (_matchup("Schlechtestes Matchup", review.worst) if review.worst else "") + "</table>"
                        '<p class="note">Gegner-Decks mit mindestens 3 Matches.</p>')
        body.append(f"<section><h2>Gegner-Decks</h2>{matchups}</section>")
        facts = [("Längste Siegserie", f"{review.win_streak} in Folge"),
                 ("Längste Niederlagenserie", f"{review.loss_streak} in Folge")]
        if review.busiest:
            day = review.busiest
            facts.append(("Aktivster Tag", f"{day_text(day)} · {day.duels} Matches ({record_text(day)})"))
        body.append("<section><h2>Serien und aktivster Tag</h2><table>" + "".join(
            f"<tr><td>{html.escape(name)}</td><td><b>{html.escape(value)}</b></td></tr>" for name, value in facts)
            + "</table></section>")
    return (PAGE.replace("__TITLE__", html.escape(title)).replace("__SUB__", html.escape(sub))
            .replace("__BODY__", "\n".join(body)).replace("__VERSION__", html.escape(APP_VERSION)))


def export(review: Review, mode_label: str = "", folder: Optional[str] = None) -> str:
    """HTML-Datei schreiben (Downloads-Ordner, nichts überschreiben). Returns: Pfad."""
    folder = folder or downloads_dir()
    os.makedirs(folder, exist_ok=True)
    base = file_name(review)[:-5]
    path, n = os.path.join(folder, base + ".html"), 2
    while os.path.exists(path):
        path, n = os.path.join(folder, f"{base}_{n}.html"), n + 1
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(review, mode_label))
    return path


PAGE = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#0e0f17;--panel:#1a1c28;--edge:#2a2d40;--text:#e8eaf5;--muted:#969cba;--gold:#f0be46;
--good:#5fd38d;--warn:#f0be46}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:Helvetica,Arial,sans-serif;line-height:1.4}
.wrap{max-width:720px;margin:0 auto;padding:0 16px 24px}
header{padding:22px 0 6px}
h1{margin:0;font-size:24px;color:var(--gold)}
header p{margin:4px 0 0;color:var(--muted);font-size:13px}
h2{font-size:13px;letter-spacing:.06em;text-transform:uppercase;color:var(--gold);margin:22px 0 8px}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.tile{background:var(--panel);border:1px solid var(--edge);border-radius:10px;padding:12px 8px;text-align:center}
.tile b{display:block;font-size:28px}
.tile span{display:block;color:var(--muted);font-size:12px}
.tile em{display:block;color:var(--muted);font-size:12px;font-style:normal}
table{width:100%;border-collapse:collapse;background:var(--panel);border:1px solid var(--edge);border-radius:10px}
th,td{padding:8px 10px;text-align:left;font-size:14px;border-bottom:1px solid var(--edge)}
th{color:var(--muted);font-size:12px;font-weight:normal}
tr:last-child td{border-bottom:0}
td.w{width:90px}
.bar{height:8px;background:var(--edge);border-radius:4px;overflow:hidden}
.bar i{display:block;height:100%}
.note{color:var(--muted);font-size:12px;margin:6px 0 0}
.empty{color:var(--muted);padding:30px 0;text-align:center}
footer{color:var(--muted);font-size:11px;padding:18px 0 0;text-align:center}
</style>
</head>
<body>
<div class="wrap">
<header><h1>__TITLE__</h1><p>__SUB__</p></header>
__BODY__
<footer>Erstellt mit dem MD-Importer __VERSION__ · eine Season ist ein Kalendermonat ·
Gegner nur als Deck-Namen</footer>
</div>
</body>
</html>
"""
