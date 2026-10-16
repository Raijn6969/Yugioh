"""
Replay als HTML exportieren (Button in der Duell-Übersicht): eine einzige Datei zum Verschicken, die in jedem Browser
läuft – auch am Handy, ohne Master Duel und ohne Importer.

Enthält das Spielfeld wie in der Duell-Übersicht (Zonen, Hände, Friedhof/Extra Deck/Verbannt mit Anzahl, LP, Zug und
Phase), Bedienung (Anfang, zurück, abspielen, vor, Ende, Schieberegler) und darunter beide Decks. Die Kartenbilder
sind eingebettet (offline lesbar, ~1–2 MB). Der Name des Gegners wird auf Wunsch ausgeblendet (Standard).
"""

import base64
import contextlib
import json
import os
import re
import time
from typing import Dict, List, Optional

import card_images
from card_stats import MatchEntry, Replay
from deck_export import downloads_dir


BLUE_EYES_PASSCODES = {
    "89631139", "89631140", "89631141", "89631142",
    "89631143", "89631144", "89631145", "89631146",
}


def _passcodes(replay: Replay, kid_to_passcode: Dict[int, str], match: Optional[MatchEntry] = None) -> List[str]:
    revealed_kids = {c[4] for f in replay.frames for c in f["cards"]
                     if c[4] and (c[5] == 1 or c[2] in (13, 16, 17) or c[2] <= 11)}
    kids = set()
    for f in replay.frames:
        for c in f["cards"]:
            if c[4]:
                kids.add(c[4])
    for deck in replay.decks:
        for part in ("main", "extra"):
            for k in deck.get(part, []):
                if k:
                    if k == 4007 and kid_to_passcode.get(k) in BLUE_EYES_PASSCODES and 4007 not in revealed_kids:
                        continue
                    kids.add(k)
    p_codes = {kid_to_passcode[k] for k in kids if k in kid_to_passcode}
    if match and getattr(match, "deck_code", None):
        from duel_view import ydk_parts
        p_main, p_extra = ydk_parts(match.deck_code)
        p_codes.update(p_main)
        p_codes.update(p_extra)
    return sorted(p_codes)


_card_back_cache: Dict[str, str] = {}


def build_data(replay: Replay, match: Optional[MatchEntry], db, hide_opponent: bool = True,
               folder: str = card_images.FOLDER, download: bool = True) -> dict:
    """Alles, was die HTML-Seite braucht (Karten mit Namen und eingebettetem Bild)."""
    kid_to_passcode = db.konami_passcodes()
    passcodes = _passcodes(replay, kid_to_passcode, match)
    if download:
        try:
            card_images.download(passcodes, folder=folder)
        except Exception:
            pass  # offline: Karten ohne Bild (Name auf der Fläche)
    infos = db.infos(passcodes)

    card_back_b64 = ""
    try:
        back_path = card_images.ensure_card_back(folder)
        if back_path and os.path.exists(back_path):
            if back_path in _card_back_cache:
                card_back_b64 = _card_back_cache[back_path]
            else:
                with open(back_path, "rb") as bf:
                    card_back_b64 = "data:image/jpeg;base64," + base64.b64encode(bf.read()).decode()
                _card_back_cache[back_path] = card_back_b64
    except Exception:
        pass

    cards = {}
    for p in passcodes:
        entry = {"name": infos[p].name if p in infos else p}
        try:
            with open(card_images.image_path(p, folder), "rb") as f:
                entry["img"] = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode()
        except OSError:
            pass
        cards[p] = entry

    cards["unknown"] = {
        "name": "Unbekannte Karte",
        "img": card_back_b64,
        "unknown": True,
    }

    names = list(replay.names) + [""] * (2 - len(replay.names))
    opp = 1 - replay.my_id
    if hide_opponent:
        names[opp] = "Gegner"

    revealed_kids = {c[4] for f in replay.frames for c in f["cards"]
                     if c[4] and (c[5] == 1 or c[2] in (13, 16, 17) or c[2] <= 11)}
    revealed_passcodes = {kid_to_passcode[k] for k in revealed_kids if k in kid_to_passcode}

    player_ydk_main, player_ydk_extra = [], []
    if match and getattr(match, "deck_code", None):
        from duel_view import ydk_parts
        player_ydk_main, player_ydk_extra = ydk_parts(match.deck_code)

    def resolve_card(k: int) -> str:
        p = kid_to_passcode.get(k)
        if p in BLUE_EYES_PASSCODES and p not in revealed_passcodes:
            return "unknown"
        return p or "unknown"

    decks = []
    for team, deck in enumerate(replay.decks):
        if team == replay.my_id and player_ydk_main:
            decks.append({"main": list(player_ydk_main), "extra": list(player_ydk_extra)})
            continue

        resolved_main = [resolve_card(k) for k in deck.get("main", []) if k]
        resolved_extra = [resolve_card(k) for k in deck.get("extra", []) if k]

        # Bekannte Karten zuerst sortieren, Unbekannte (Kartenrückseite) am Ende
        sorted_main = [p for p in resolved_main if p != "unknown"] + [p for p in resolved_main if p == "unknown"]
        sorted_extra = [p for p in resolved_extra if p != "unknown"] + [p for p in resolved_extra if p == "unknown"]
        decks.append({"main": sorted_main, "extra": sorted_extra})

    used_kids = {c[4] for f in replay.frames for c in f["cards"] if c[4]}
    title = "Replay"
    if match is not None:
        title = f"{match.deck_name or match.md_deck or 'Deck'} vs {match.opp_name or 'Gegner'}"
    result = ""
    if match is not None:
        from duel_view import details, result_text
        result = f"{result_text(match)[0]} · {details(match)}"
    return {"title": title, "result": result, "names": names, "my": replay.my_id,
            "start": time.strftime("%d.%m.%Y %H:%M", time.localtime(replay.start)),
            "kid": {str(k): kid_to_passcode[k] for k in used_kids if k in kid_to_passcode},
            "frames": [{"c": f["cards"], "lp": f.get("lp", {}), "turn": f.get("turn", ""),
                        "phase": f.get("phase", "")} for f in replay.frames],
            "decks": decks, "cards": cards}


def file_name(data: dict) -> str:
    stem = re.sub(r"[^\w\-]+", "_", data["title"]).strip("_")[:60] or "Replay"
    return f"Replay_{stem}_{data['start'][:10].replace('.', '-')}.html"


def render(data: dict) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    title = data["title"].replace("&", "&amp;").replace("<", "&lt;")
    return PAGE.replace("__TITLE__", title).replace("__DATA__", payload)


def export(replay: Replay, match: Optional[MatchEntry], db, hide_opponent: bool = True,
           folder: Optional[str] = None, image_folder: str = card_images.FOLDER) -> str:
    """HTML-Datei schreiben (Downloads-Ordner, nichts überschreiben). Returns: Pfad."""
    data = build_data(replay, match, db, hide_opponent, image_folder)
    folder = folder or downloads_dir()
    os.makedirs(folder, exist_ok=True)
    base = file_name(data)[:-5]
    path, n = os.path.join(folder, base + ".html"), 2
    while os.path.exists(path):
        path, n = os.path.join(folder, f"{base}_{n}.html"), n + 1
    page = render(data)   # vor dem Öffnen: ein Fehler hier hinterlässt keine leere Datei im Downloads-Ordner
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(page)
    except Exception:   # Platte voll, nicht kodierbares Zeichen …: keine halbe Datei zum Verschicken liegen lassen
        with contextlib.suppress(OSError):
            os.remove(path)
        raise
    return path


PAGE = r"""<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#0e0f17;--panel:#1a1c28;--field:#141e38;--edge:#3c508c;--opp:#281622;--zone:#1a284c;--zone-edge:#30468a;
--opp-zone:#2c1a28;--opp-edge:#823c50;--text:#e8eaf5;--muted:#969cba;--gold:#f0be46;--back:#7a4828}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font-family:Helvetica,Arial,sans-serif}
header{background:var(--panel);padding:10px 16px;border-bottom:1px solid #2a2d40}
header h1{margin:0;font-size:18px;color:var(--gold)}
header p{margin:4px 0 0;color:var(--muted);font-size:13px}
.controls{display:flex;flex-wrap:wrap;gap:6px;align-items:center;padding:8px 16px;background:var(--panel)}
.controls button{background:#2a2d40;color:var(--text);border:1px solid var(--gold);border-radius:6px;padding:5px 10px;
font-size:14px;cursor:pointer}
.controls input[type=range]{flex:1;min-width:140px}
#step{color:var(--muted);font-size:13px;min-width:170px}
#board{position:relative;margin:10px auto;background:var(--field);border:1px solid var(--edge);border-radius:12px}
.zone,.card{position:absolute;border-radius:4px}
.zone{display:flex;align-items:center;justify-content:center;text-align:center;font-size:10px;color:#5a6eaa}
.zone.me{background:var(--zone);border:1px solid var(--zone-edge)}
.zone.op{background:var(--opp-zone);border:1px solid var(--opp-edge)}
.card{background:var(--back) center/cover no-repeat;border:1px solid #3a2010;cursor:pointer;display:flex;
align-items:center;justify-content:center;font-size:9px;text-align:center;color:var(--text);overflow:hidden}
.badge{position:absolute;right:0;bottom:0;background:#101018;border:1px solid var(--gold);color:var(--gold);
font-size:10px;font-weight:bold;padding:0 3px}
.stats{position:absolute;left:0;right:0;bottom:-14px;text-align:center;font-size:10px;font-weight:bold;color:var(--gold)}
.player{position:absolute;font-size:12px;font-weight:bold}
.player b{display:block;font-size:17px;color:var(--text)}
.phase{position:absolute;border-radius:50%;background:#8a1c1c;border:2px solid var(--gold);display:flex;
flex-direction:column;align-items:center;justify-content:center;font-size:11px}
#tip{position:fixed;pointer-events:none;background:var(--panel);border:1px solid var(--gold);padding:4px 8px;
font-size:13px;display:none;z-index:9}
section{padding:6px 16px 20px}
section h2{font-size:13px;color:var(--gold);margin:14px 0 6px}
.deck{display:flex;flex-wrap:wrap;gap:4px}
.deck div{position:relative;width:52px;height:76px;background:var(--back) center/cover no-repeat;border-radius:3px;
font-size:8px;overflow:hidden}
footer{color:var(--muted);font-size:11px;padding:0 16px 20px}
</style>
</head>
<body>
<header><h1>__TITLE__</h1><p id="sub"></p></header>
<div class="controls">
<button id="first" title="Anfang">⏮</button><button id="back" title="Schritt zurück">◀</button>
<button id="play">▶ Abspielen</button><button id="next" title="Schritt vor">▶</button>
<button id="last" title="Ende">⏭</button><input id="slider" type="range" min="0" value="0"><span id="step"></span>
</div>
<div id="board"></div>
<section id="decks"></section>
<footer>Exportiert mit dem MD-Importer · Kartenbilder: YGOPRODeck</footer>
<div id="tip"></div>
<script>
const D = __DATA__;
const ROWS = ["opp_hand","opp_s","opp_m","emz","my_m","my_s","my_hand"];
const HAND = 13, GRAVE = 16, DECK = 15, EXTRA = 14, BANISHED = 17, FIELD = 12;
let i = 0, timer = null;
const board = document.getElementById("board"), tip = document.getElementById("tip");
document.getElementById("sub").textContent = [D.result, D.start].filter(Boolean).join(" · ");
const slider = document.getElementById("slider");
slider.max = D.frames.length - 1;
function place(zone, mine) {
  if (zone <= 4) return mine ? ["my_m", 1 + zone] : ["opp_m", 5 - zone];
  if (zone === 5 || zone === 6) { const left = zone === 5; return ["emz", mine ? (left ? 2 : 4) : (left ? 4 : 2)]; }
  if (zone >= 7 && zone <= 11) return mine ? ["my_s", zone - 6] : ["opp_s", 5 - (zone - 7)];
  const piles = {12: [["my_m",0],["opp_m",6]], 16: [["my_m",6],["opp_m",0]], 15: [["my_s",6],["opp_s",0]],
                 14: [["my_s",0],["opp_s",6]], 17: [["emz",6],["emz",0]]};
  return piles[zone] ? piles[zone][mine ? 0 : 1] : null;
}
function geometry() {
  // so groß wie möglich, aber ganz sichtbar (Breite und Höhe); am Handy notfalls scrollen statt winzig
  const maxW = Math.min(window.innerWidth - 16, 980), gap = Math.max(3, maxW / 160);
  const maxH = window.innerHeight - board.getBoundingClientRect().top - 12;
  const cw = Math.max(38, Math.min((maxW - 8 * gap) / 7, ((maxH - 8 * gap) / 7) * 50 / 73)), ch = cw * 73 / 50;
  const w = 7 * cw + 8 * gap;
  board.style.width = w + "px"; board.style.height = (7 * ch + 8 * gap) + "px";
  const col = k => gap + k * (cw + gap), row = r => gap + ROWS.indexOf(r) * (ch + gap);
  return {w, gap, cw, ch, col, row};
}
function el(cls, x, y, w, h, parent) {
  const e = document.createElement("div"); e.className = cls;
  Object.assign(e.style, {left: x + "px", top: y + "px", width: w + "px", height: h + "px"});
  (parent || board).appendChild(e); return e;
}
function showTip(e, name) { tip.textContent = name; tip.style.display = "block";
  tip.style.left = Math.min(e.clientX + 12, window.innerWidth - 220) + "px"; tip.style.top = (e.clientY + 12) + "px"; }
function cardEl(c, x, y, g, mine, hand) {
  const [uid, team, zone, index, kid, face, attack, atk, def] = c;
  const p = D.kid[kid], info = p && D.cards[p], shown = info && (face || (mine && hand));
  const defense = !hand && zone <= 6 && !attack;
  const e = el("card", x, y, g.cw, g.ch);
  if (shown && info.img) e.style.backgroundImage = `url(${info.img})`;
  else if (shown) e.textContent = info.name;
  const angle = (mine ? 0 : 180) + (defense ? 90 : 0);
  if (angle) e.style.transform = `rotate(${angle}deg)` + (defense ? " scale(0.8)" : "");
  if (shown) { e.onmousemove = ev => showTip(ev, info.name); e.onmouseleave = () => tip.style.display = "none";
               e.onclick = ev => showTip(ev, info.name); }
  if (shown && !hand && zone <= 6 && atk >= 0) { const s = el("stats", 0, 0, 0, 0, board);
    s.style.left = x + "px"; s.style.width = g.cw + "px"; s.style.top = (y + g.ch - 14) + "px";
    s.style.bottom = "auto"; s.textContent = `${atk} / ${def}`; }
  return e;
}
function draw() {
  const f = D.frames[i], g = geometry(); board.innerHTML = "";
  const opp = el("", 0, 0, g.w, ROWS.indexOf("emz") * (g.ch + g.gap), board);
  opp.style.background = "var(--opp)"; opp.style.borderRadius = "12px 12px 0 0";
  const labels = {};
  for (let k = 1; k <= 5; k++) { labels["opp_m," + k] = labels["my_m," + k] = "Monster";
                                 labels["opp_s," + k] = labels["my_s," + k] = "Zauber/Falle"; }
  Object.assign(labels, {"emz,2": "Extra-MZ", "emz,4": "Extra-MZ", "my_m,0": "Feld", "opp_m,6": "Feld",
    "my_m,6": "Friedhof", "opp_m,0": "Friedhof", "my_s,6": "Deck", "opp_s,0": "Deck", "my_s,0": "Extra Deck",
    "opp_s,6": "Extra Deck", "emz,6": "Verbannt", "emz,0": "Verbannt"});
  for (const key in labels) { const [r, k] = key.split(","); const op = r.startsWith("opp") || key === "emz,0";
    el("zone " + (op ? "op" : "me"), g.col(+k), g.row(r), g.cw, g.ch).textContent = labels[key]; }
  const piles = {}, hands = {true: [], false: []};
  for (const c of f.c) {
    const mine = c[1] === D.my, zone = c[2];
    if (zone === HAND) { hands[mine].push(c); continue; }
    const spot = place(zone, mine); if (!spot) continue;
    if ([GRAVE, DECK, EXTRA, BANISHED, FIELD].includes(zone)) { (piles[spot] = piles[spot] || []).push(c); continue; }
    cardEl(c, g.col(spot[1]), g.row(spot[0]), g, mine, false);
  }
  for (const key in piles) { const stack = piles[key], top = stack.reduce((a, b) => b[3] > a[3] ? b : a);
    const [r, k] = key.split(","); cardEl(top, g.col(+k), g.row(r), g, top[1] === D.my, false);
    if ([GRAVE, EXTRA, BANISHED].includes(top[2])) {  // Anzahl nicht in der (beim Gegner gedrehten) Karte
      const b = el("badge", 0, 0, 0, 0); b.style.width = b.style.height = b.style.right = b.style.bottom = "auto";
      b.style.left = (g.col(+k) + g.cw) + "px"; b.style.top = (g.row(r) + g.ch) + "px";
      b.style.transform = "translate(-100%, -100%)"; b.textContent = top[3] + 1; } }
  for (const mine of [true, false]) {
    const hand = hands[mine].sort((a, b) => a[3] - b[3]);
    const step = Math.min(g.cw + 4, (g.col(6) - g.col(0)) / Math.max(1, hand.length));
    const left = (g.w - (step * (hand.length - 1) + g.cw)) / 2;
    hand.forEach((c, n) => cardEl(c, left + n * step, g.row(mine ? "my_hand" : "opp_hand"), g, mine, true));
  }
  for (const [team, r, color] of [[1 - D.my, "opp_hand", "#dc96a0"], [D.my, "my_hand", "#96beff"]]) {
    const p = el("player", g.gap, g.row(r) + g.ch / 2 - 18, g.cw * 1.4, 40); p.style.color = color;
    p.innerHTML = ""; p.append(D.names[team] || ""); const b = document.createElement("b");
    b.textContent = "LP " + (f.lp[team] ?? "–"); p.appendChild(b); }
  if (f.turn || f.phase) { const r = Math.min(g.cw, g.ch) * 0.48;
    const ph = el("phase", g.col(3) + g.cw / 2 - r, g.row("emz") + g.ch / 2 - r, 2 * r, 2 * r);
    ph.innerHTML = ""; ph.append(f.turn || ""); const b = document.createElement("b"); b.textContent = f.phase || "";
    ph.appendChild(b); }
  slider.value = i;
  document.getElementById("step").textContent = `Schritt ${i + 1} / ${D.frames.length}` +
    (f.turn ? ` · ${f.turn}` : "") + (f.phase ? ` · ${f.phase}` : "");
}
function go(n) { i = Math.max(0, Math.min(D.frames.length - 1, n)); draw(); }
function stop() { clearInterval(timer); timer = null; document.getElementById("play").textContent = "▶ Abspielen"; }
document.getElementById("first").onclick = () => { stop(); go(0); };
document.getElementById("back").onclick = () => { stop(); go(i - 1); };
document.getElementById("next").onclick = () => { stop(); go(i + 1); };
document.getElementById("last").onclick = () => { stop(); go(D.frames.length - 1); };
slider.oninput = () => { stop(); go(+slider.value); };
document.getElementById("play").onclick = () => {
  if (timer) return stop();
  if (i >= D.frames.length - 1) go(0);
  document.getElementById("play").textContent = "⏸ Pause";
  timer = setInterval(() => { if (i >= D.frames.length - 1) return stop(); go(i + 1); }, 700);
};
document.addEventListener("keydown", e => { if (e.key === "ArrowRight") go(i + 1); if (e.key === "ArrowLeft") go(i - 1);
  if (e.key === " ") { e.preventDefault(); document.getElementById("play").click(); } });
window.addEventListener("resize", draw);
const decks = document.getElementById("decks");
D.decks.forEach((deck, team) => {
  const isMine = team === D.my;
  const knownMain = deck.main.filter(p => p !== "unknown").length;
  const knownExtra = deck.extra.filter(p => p !== "unknown").length;
  const totalMain = deck.main.length;
  const totalExtra = deck.extra.length;
  const hasUnknown = deck.main.includes("unknown") || deck.extra.includes("unknown");

  const headerDiv = document.createElement("div");
  headerDiv.style.display = "flex";
  headerDiv.style.alignItems = "center";
  headerDiv.style.justifyContent = "space-between";
  headerDiv.style.margin = "14px 0 6px";

  const h = document.createElement("h2");
  h.style.margin = "0";
  const nameStr = isMine ? "DEIN DECK" : "DECK " + (D.names[team] || "GEGNER").toUpperCase();
  if (hasUnknown) {
    h.textContent = `${nameStr} (${knownMain + knownExtra} bekannt · ${totalMain} + ${totalExtra})`;
  } else {
    h.textContent = `${nameStr} (${totalMain} + ${totalExtra})`;
  }
  headerDiv.appendChild(h);

  const box = document.createElement("div");
  box.className = "deck";

  if (hasUnknown) {
    const btn = document.createElement("button");
    btn.textContent = "Nur bekannte anzeigen";
    btn.style.background = "#2a2d40";
    btn.style.color = "var(--text)";
    btn.style.border = "1px solid var(--gold)";
    btn.style.borderRadius = "4px";
    btn.style.padding = "3px 8px";
    btn.style.fontSize = "11px";
    btn.style.cursor = "pointer";
    headerDiv.appendChild(btn);

    btn.onclick = () => {
      const isHiding = btn.dataset.hiding === "true";
      btn.dataset.hiding = isHiding ? "false" : "true";
      btn.textContent = isHiding ? "Nur bekannte anzeigen" : "Alle Karten anzeigen";
      box.querySelectorAll(".card-unknown").forEach(el => {
        el.style.display = isHiding ? "block" : "none";
      });
    };
  }

  decks.appendChild(headerDiv);
  decks.appendChild(box);

  for (const p of deck.main.concat(deck.extra)) {
    const c = document.createElement("div");
    const isUnknown = p === "unknown";
    if (isUnknown) c.className = "card-unknown";
    const info = D.cards[p] || (isUnknown ? {name: "Unbekannte Karte"} : {name: p});
    if (info.img) c.style.backgroundImage = `url(${info.img})`;
    else c.textContent = info.name;
    c.onmousemove = ev => showTip(ev, info.name);
    c.onmouseleave = () => tip.style.display = "none";
    c.onclick = ev => showTip(ev, info.name);
    box.appendChild(c);
  }
});
draw();
</script>
</body>
</html>
"""
