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
