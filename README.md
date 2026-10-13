# Master Duel Deck Importer

A tool that automatically builds Yu-Gi-Oh! decks for you in Master Duel.
Paste a deck code, hit Start, and watch it click together your deck card by card.

## What it does

### 1. One-Click Deck Import
Copy a deck code (YDKE link or YDK file) from any deck-builder website
(like MasterDuelMeta or YGOPRODeck), then start the program. It clears
your current deck and builds the new one automatically — no manual searching,
no manual clicking. A 60-card deck takes about a minute.

### 2. It Reads the Screen to Verify Each Card
Before adding any card to your deck, the program looks at the card's name
on screen and checks that it's actually the right one. Even if a card name
is misspelled by the screen reader (which happens with weird fonts), the
program is smart enough to recognize it correctly. This means you don't
end up with wrong cards in your deck.

### 3. Smart Batch Mode (Saves Time)
When your deck has many cards from the same family — say, 12 Hecahands
cards or 9 Swordsoul cards — the program searches for them all at once
instead of one by one. This makes importing big themed decks much faster.
It even catches plural variations automatically (so "Exosisters" cards
get grouped with "Exosister" cards).

### 4. Handles Tricky Cards Gracefully
Some Yu-Gi-Oh! card names are really long and get cut off on screen, or
have nearly identical names (like two cards both starting with "Varuroon").
The program has built-in safeguards:
- If a card name is too long to read fully, it matches based on the readable part.
- If two cards look the same from the screen reader's view, it falls back to
  a more careful per-card search to make sure the right one gets added.
- If a single search result has a slightly garbled name (e.g., the screen
  reader misread one letter), it still gets recognized.

### 5. Works on Any PC and Any Screen Size
**First-time setup:** A simple wizard pops up and asks you to point at 5
things on your Master Duel screen (the search bar, the first card slot,
the trash icon, etc.). One-time, two minutes. After that, the program
knows where everything is — no matter your screen resolution.

**Slow PC?** In the config file `md_config.json`, change `"SPEED_PROFILE"`
to `"slow"`. This gives Master Duel more time to load between actions, so
the program won't click ahead too fast. Options are `"fast"`, `"normal"`,
or `"slow"`.

**Where your data lives:** settings, the card database and logs are stored next to
the program. If that folder is read-only (for example under `C:\Program Files`),
they go to `%APPDATA%\MD_Importer` instead.

### 6. Win-Rate Tracker
With "Read memory" enabled (Deck → Options), every duel you play is recorded
automatically when the result screen appears: win or loss, whether you went first
or second, and which deck you used. Nothing to click and nothing to type.
- **Your deck** is recognised from the deck you selected in the game. Open the deck
  selection once per deck so the program learns which cards belong to it. After
  that, switching decks in the game is picked up on its own.
- **Opening the Match History** is optional. If you open it, the program adds the
  turn count and your opponent's deck, plus any duels it missed (for example while
  it wasn't running).
Everything is read-only. Data is only read from menu screens (deck selection, coin
toss, result screen); during a duel it only checks which screen is open.
Deck → **Winrate** shows your win rate overall, going first and going second, per deck,
and lists recent matches. A small notification appears for every recorded match.
It also tracks the coin toss: how often you won it, your win rate after winning or
losing it, and your current streak (e.g. "lost the last 3"). Only for duels recorded
live from version 9.2 on; the Match History doesn't include the coin toss.
- **Time range:** Today (from 5 am, so a late session counts as one day), 7 days,
  Season (this month's ranked season) or all.
- **Trend:** a line of your win rate over the last 10 matches at each point; hover
  over it to see the match.
- **Against opponent decks:** your record against each deck you faced (once the
  Match History has been opened).
- After each duel the status line shows today's record, e.g. "Win · Second · Coin
  lost · Today 5–2".

If "Read memory" can't connect for a few minutes (usually after a Master Duel
update), you get a one-time notice: imports then use text recognition, and duels
aren't recorded until it works again.

Only one importer can run at a time. On start, a short note "MD-Importer <version>
läuft jetzt." appears above the taskbar for 5 seconds; starting it again only shows
that note instead of a second overlay.

You don't need to open the Match History after every duel: it shows the last 20, so
once every 20 duels is enough. After 15 duels without it you get a reminder (again at
18, and after every duel from 20 on, because then opponent decks start getting lost).
Duels without a single turn (opponent left before the start) are kept but not counted.

Your own database (matches, coin tosses, side profiles, deck history) is backed up
once a day to `backups/`; the last 7 copies are kept.

### 7. Staple Check
Deck → **Staples** compares the deck shown with the current top lists in
Master Duel (from Master Duel Meta) for its archetypes:
- **Different copy count:** "you play Mitsurugi Ritual 2×, 67 % of lists play 3×"
- **Missing:** cards that at least half of the top lists play
- **Only you:** cards that hardly any top list plays
For hybrid decks (e.g. Ryzeal/Mitsurugi) it picks the closest deck type, and you
can switch to the others. The data is saved locally and refreshed every few days,
or on demand with "Refresh".

### 8. Matchup Against Disruptions
Deck → **Matchup** shows which disruption cards (hand traps, negating quick-play
spells and traps) your opponents actually play, based on the duels the program
has recorded, and how you do against them: for each card, the share of opponent
decks that run it and your win rate against those decks (red = clearly worse than
usual). Hover over a card for details: going first/second, and your win rate
against decks without it. You can switch between "this deck" and "all decks", and
between Ranked and all modes. Opponent decks come from Master Duel's Match History,
so open it every now and then (it keeps the last 20 duels). If you also recorded a
replay of a duel (see "Duel View and Replays"), the complete opponent decklist from that
replay is used for it instead, so you see which disruptions they really had in the deck;
each duel is counted only once. A line at the top tells you for how many duels the full
list came from replays (e.g. "12 Gegnerdecks vollständig aus Replays").

### 9. Side-Deck Profiles
Master Duel is best-of-one, so you adjust your deck between matches. Deck → **Side**
lets you save profiles per deck, e.g. "Second: −3 Droll & Lock Bird, +3 Lightning
Storm": click cards of your deck to take them out, search cards to put in. One click
on **Swap** changes the deck in the deck editor (like a small import: it removes the
cards with a right-click and searches the new ones, then checks the deck), **Back**
undoes it. Swaps don't end up in the deck history.

### 10. Ash Priority Cheat Sheet
Deck → **Ash-Prio** shows, for each starting hand of your deck, where in the combo
a hand trap hurts most and what's left on board afterwards — for Ash Blossom,
Imperm/Veiler, Nibiru, Droll & Lock and Ghost Ogre. At the top, card images show at a
glance which of your cards the chosen hand trap hits most often (in how many starting
hands, and how much board it costs on average); click a card to list only those hands.
Click a hand to see the whole
line with the critical step marked and the best way to continue after the hand
trap. The data comes from the Factory (read-only); your deck is matched to the
Factory variant with the most cards in common. Without the Factory on the PC, the
importer uses a snapshot of its results that is packed into the .exe at build time.
For decks the Factory doesn't know, Ash-Prio reads the card texts instead and lists
which of your cards each hand trap can hit and with which effect (starters first) —
without the order of the combo.

### 11. Duel View and Replays
Click a match in the **Winrate** window to open it on a duel field (like the Factory's
Shadow Arena, without the side columns): your deck at the bottom, your opponent's at
the top, result, first/second, coin toss and turns in the middle. Use ◀/▶ to browse.

**Replays without the save limit:** watch a duel as a replay in Master Duel (Match
History → Replay) while the importer is running, and it records the field step by
step. The match then shows "▶ Replay" and plays back in the duel view: cards in their
zones, hands, graveyard/extra deck/banished with counts, life points, turn and phase.
The field is only read while a replay from the Match History is playing — never
during a real duel.

**Share a replay:** "Als HTML" in the duel view saves the replay as a single HTML file
in your Downloads folder (card images included, works offline in any browser, also on
phones). Your opponent's name is hidden by default ("Gegnername: aus").

### 12. Update Notice
Once per start (only in the .exe, never when run from source) the importer quietly
checks in the background whether a newer version exists. It reads `APP_VERSION` from
`app_paths.py` in the public GitHub repo (the GitHub releases are outdated) and compares
the numbers properly ("9.10" is newer than "9.2"). If there is one, a short note
"MD-Importer 9.3 verfügbar" appears once per version, and the tray icon menu gets an
entry that opens the project page in your browser. Nothing is downloaded or installed
automatically. No internet or any error: nothing happens except a line in the log.

### 13. Bug Report
Something doesn't work? Right-click the tray icon → **Fehlerbericht erstellen**. The
importer saves a ZIP file in your Downloads folder (`MD-Importer_Fehlerbericht_<date>_<time>.zip`,
nothing is overwritten) and shows it in Explorer, so you can send it along. It contains
the log files, your settings (`md_config.json`) and an `info.txt` with the app version,
Windows version, screen size and data folder. Your databases, backups and replays are
never included. Opponent player names that appear in the logs are replaced by
"<Gegner>" first (the names come from your recorded replays).

### 14. Starting Hands
From your recorded replays the importer knows your **starting hand** — the 5 cards you
hold before your first turn (as the second player, the card you draw in turn 2 doesn't
count). In Deck → Winrate, the **Starthände** section shows, for the deck you clicked
(or your only deck): how often each card was in your starting hand and your win rate in
those duels compared to the duels without it. Cards need at least 3 duels, otherwise it
says "Noch zu wenige Replays". Replays that don't show a reliable starting hand (for
example one that was only watched from the middle) are skipped. The **brick rate** is the
share of starting hands without a single starter, based on the same starter rating as the
Deck window (you can change it there), together with your win rate in those duels. It
follows the same filters as the rest of the window (mode, time range).

### 15. Monthly Review
In the **Winrate** window, **Rückblick** opens a review of one season (= one calendar
month, like the "Season" time range). It shows: record and win rate overall, going first
and going second; your most played decks with their records; your best and worst matchup
against an opponent deck (at least 3 duels against it); the coin toss (how often you won
it, your win rate after winning or losing it); your longest win and loss streaks; and the
number of duels and your busiest day (a day starts at 5 am). Use ◀/▶ to switch the month;
it opens on the current month, or on the previous one during the first 3 days of a month.
Ranked/all modes follow the Winrate window.

**Share it:** "Als HTML" saves the review as a single, self-explanatory HTML file in your
Downloads folder (dark look like the replay export, works offline, also on phones) and
shows it in Explorer. Opponents only appear as deck names, never as player names.

At the first start in a new month you get a one-time note "Rückblick für <Monat> ist fertig"
— but only if you played duels in the previous month.

### 16. Combos from Master Duel Meta
Deck → **Combos** (bottom row, next to Side) lists the community combos that Master Duel
Meta has for the archetypes of the deck shown. By default only combos you can play with
this deck are listed ("Nur spielbare"; "Alle" also shows the ones with missing cards, which
are named). Each row shows the starting hand and the chance to have all named starters in
your first 5 cards. Click a combo for the whole line: starting hand as card images, the
steps with their branches, the end board and the hand traps it plays through. The data
is saved locally and refreshed every few days, or on demand with "Aktualisieren".
