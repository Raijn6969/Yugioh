"""
Starter-Einstufungen aus den Deck-Guides von Master Duel Meta nachladen.

Die Textregeln (starter_rules) erkennen nur, was auf der Karte steht. Ob eine Karte in der Praxis allein eine
Combo startet, steht in den Guides der Spieler: In jedem Abschnitt zu einer Karte (Karte als Bild darüber
oder Überschrift mit ihrem Namen) wird nach Sätzen wie "is the best starter", "making it a 1 card combo"
oder "isn't a starter on her own" gesucht.
"""

import re
from typing import Dict, Iterable, List, NamedTuple, Optional

import requests

API_URL = "https://www.masterduelmeta.com/api/v1"
GUIDES_PER_ARCHETYPE = 2   # neueste Guides je Archetyp
TIMEOUT = 15

_KEYWORD = r"(?:starters?|(?:1|one)[- ]card combos?)"
_WORDS = r"(?:\s+[^\s.!?,]+){0,3}?"   # bis zu 3 Wörter ("is debatably the best starter", "makes Hat a …")
_ARTICLE = rf"\s+(?:a|an|the|another|our|one of the){_WORDS}\s+"
# Der Satz muss die Karte selbst so nennen ("X is the best starter", "makes X a 1 card combo",
# "The best starter of the trio, X …") – "…normal summon starters" o.ä. zählt nicht
POSITIVE = re.compile(rf"(?i)\b(?:is|are|['’]s|as|becomes?|making|makes?|be){_WORDS}{_ARTICLE}{_KEYWORD}\b|"
                      rf"^\s*(?:the|our|one of the){_WORDS}\s+{_KEYWORD}\b|"
                      rf"\b(?:perform|do|start)s?\s+(?:a|an)\s+(?:1|one)[- ]card combo")
NEGATIVE = re.compile(rf"(?i)\b(?:is|are|was)\s*(?:n['’]t|not)\b{_WORDS}\s+(?:(?:a|an|the){_WORDS}\s+)?{_KEYWORD}\b|"
                      rf"\bnot\s+(?:a|an)\s+(?:\w+\s+)?starter\b")
# Sätze mit "starter", die nichts über die Karte selbst sagen
IGNORE = re.compile(r"(?i)need(?: of)? an? starter|your starters?\b|get (?:to |into )?(?:a |your )?starter|"
                    r"search(?:es|ing)? (?:a |your |for )?starter|variety of starters|"
                    r"starters? (?:and|or|\+|/) (?:an )?extenders?|extenders? (?:and|or|\+|/) (?:an? )?starters?")
INLINE_TAGS = {"span", "strong", "em", "b", "i", "u", "a", "code", "br", "mark", "small", "sup", "sub"}
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


class Verdict(NamedTuple):
    name: str          # Kartenname (englisch, wie bei YGOPRODeck)
    starter: bool
    quote: str         # Satz aus dem Guide
    guide: str         # Titel des Guides


def find_guides(archetype: str, session=requests) -> List[dict]:
    """Die neuesten Guides, deren Titel den Archetyp nennt: [{_id, title, date}]"""
    resp = session.get(f"{API_URL}/articles", params={"search": archetype, "limit": 20,
                                                      "fields": "title,url,date"}, timeout=TIMEOUT)
    resp.raise_for_status()
    guides = [a for a in resp.json() if str(a.get("url", "")).startswith("/guides/")
              and archetype.lower() in str(a.get("title", "")).lower()]
    return sorted(guides, key=lambda a: a.get("date", ""), reverse=True)[:GUIDES_PER_ARCHETYPE]


def load_guide(guide_id: str, session=requests) -> dict:
    resp = session.get(f"{API_URL}/articles", params={"_id": guide_id, "limit": 1}, timeout=TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    return data[0] if isinstance(data, list) else data


def _blocks(markdown: dict):
    """Guide der Reihe nach: ("cards", [Namen]) für Kartenbilder, ("heading", Text), ("text", Absatz)."""
    components = markdown.get("customComponents", {})

    def inline(node) -> str:
        if isinstance(node, list):
            return "".join(inline(n) for n in node)
        if node.get("type") == "text":
            return node.get("content", "")
        if node.get("name") == "component":
            props = components.get(node.get("attrs", {}).get("id"), {}).get("props", {})
            card = props.get("card")
            return str(props.get("name") or (card.get("name") if isinstance(card, dict) else card) or "")
        return inline(node.get("children", []))

    def walk(node):
        if isinstance(node, list):
            for n in node:
                yield from walk(n)
            return
        name = node.get("name")
        if name == "component":
            comp = components.get(node.get("attrs", {}).get("id"), {})
            if comp.get("type") == "CardContainer":
                yield "cards", [c.get("card", {}).get("name", "") for c in comp.get("props", {}).get("cards", [])]
        elif name in ("h1", "h2", "h3", "h4", "h5"):
            yield "heading", inline(node).strip()
        else:
            # Text kann direkt in <p>, aber auch in <div> o.ä. stehen: Fließtext sammeln, Blöcke einzeln
            run = []
            for child in node.get("children", []):
                if _is_inline(child, components):
                    run.append(inline(child))
                    continue
                if "".join(run).strip():
                    yield "text", "".join(run).strip()
                run = []
                yield from walk(child)
            if "".join(run).strip():
                yield "text", "".join(run).strip()

    yield from walk(markdown.get("htmlTree", []))


def _is_inline(node: dict, components: dict) -> bool:
    if node.get("type") == "text":
        return True
    if node.get("name") == "component":
        return components.get(node.get("attrs", {}).get("id"), {}).get("type") == "CardLink"
    return node.get("name") in INLINE_TAGS


def _heading_card(heading: str, names: Iterable[str]) -> Optional[str]:
    """Überschrift → Karte, wenn sie genau eine bekannte Karte meint (voller Name oder eindeutiger Teil)."""
    text = heading.lower().strip(" :!")
    if len(text) < 4:
        return None
    hits = {n for n in names if n and (n.lower() == text or text in n.lower() or n.lower() in text)}
    return hits.pop() if len(hits) == 1 else None


def sentence_verdict(sentence: str) -> Optional[bool]:
    """True = Satz nennt die Karte einen Starter, False = ausdrücklich keiner, None = sagt nichts darüber."""
    if NEGATIVE.search(sentence):
        return False
    if not POSITIVE.search(sentence) or IGNORE.search(sentence):
        return None
    return True


def guide_verdicts(markdown: dict, title: str, deck_names: Iterable[str] = ()) -> List[Verdict]:
    """Aussagen eines Guides über Starter, je Karte zusammengefasst (mehr Ja- als Nein-Sätze → Starter)."""
    blocks = list(_blocks(markdown))
    names = set(deck_names)
    for kind, value in blocks:
        if kind == "cards":
            names.update(value)
    votes: Dict[str, List[tuple]] = {}
    subject = None
    for kind, value in blocks:
        if kind == "cards":
            subject = value[0] if len(value) == 1 else None
        elif kind == "heading":
            subject = _heading_card(value, names)
        elif subject:
            for sentence in _SENTENCE.split(value):
                verdict = sentence_verdict(sentence)
                if verdict is not None:
                    votes.setdefault(subject, []).append((verdict, " ".join(sentence.split())))
    result = []
    for name, found in votes.items():
        yes = [q for v, q in found if v]
        no = [q for v, q in found if not v]
        if len(yes) != len(no):
            starter = len(yes) > len(no)
            result.append(Verdict(name, starter, (yes if starter else no)[0], title))
    return result


def lookup(archetypes: Iterable[str], deck_names: Iterable[str] = (), session=requests) -> List[Verdict]:
    """Guides zu den Archetypen laden und auswerten. Neuere Guides haben Vorrang."""
    deck_names = list(deck_names)
    result: Dict[str, Verdict] = {}
    for archetype in archetypes:
        for guide in find_guides(archetype, session):
            markdown = load_guide(guide["_id"], session).get("parsedMarkdown") or {}
            for verdict in guide_verdicts(markdown, guide.get("title", archetype), deck_names):
                result.setdefault(verdict.name, verdict)  # neuester Guide zuerst
    return list(result.values())
