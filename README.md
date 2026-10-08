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

Only one importer can run at a time; starting it again shows a hint instead of a
second overlay.

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
so open it every now and then (it keeps the last 20 duels).

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
Imperm/Veiler, Nibiru, Droll & Lock and Ghost Ogre. Click a hand to see the whole
line with the critical step marked and the best way to continue after the hand
trap. The data comes from the Factory (read-only); your deck is matched to the
Factory variant with the most cards in common.
