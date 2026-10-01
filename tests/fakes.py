"""Gemeinsame Test-Helfer: nachgebautes Master Duel ohne echte Maus und ohne Bildschirm."""

import json
import os
import time
import types

import import_engine as ie

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def load_golden():
    with open(os.path.join(DATA_DIR, "golden.json"), encoding="utf-8") as f:
        return json.load(f)


def make_core(speed="normal"):
    return ie.DeckImporterCore({"SPEED_PROFILE": speed}, "", lambda *a: None, lambda **k: None)


class FakeSearchGame:
    """
    Suchergebnis wechselt nach `load_at` Sekunden vom alten zum neuen Raster.
    Wie im echten Spiel ändert sich das Detail-Panel NUR durch einen Klick.
    """

    def __init__(self, load_at, old_slot0, new_slot0, panel_start):
        self.t0 = time.perf_counter()
        self.load_at = load_at
        self.old_slot0, self.new_slot0 = old_slot0, new_slot0
        self.panel = panel_start

    def click(self, x, y, button="left"):
        loaded = time.perf_counter() - self.t0 >= self.load_at
        self.panel = self.new_slot0 if loaded else self.old_slot0

    def read(self, sct, monitor):
        time.sleep(0.02)  # Dauer einer (gecachten) Texterkennung
        return self.panel, self.panel

    def automator(self):
        return types.SimpleNamespace(iron_grip_click=self.click)


class FakeGrid:
    """Statisches Such-Raster für die Archetyp-Suche: Slot-Index → gelesener Text."""

    def __init__(self, texts):
        self.texts = texts
        self.reads = 0
        self.added = []  # (slot, amount)

    def geometry(self, slot, automator):
        # y wächst pro Zeile um 10, das Raster-Limit (1080*0.93) wird nie erreicht
        return {"slot": slot}, slot, 100 + (slot // 6) * 10

    def read(self, sct, monitor):
        self.reads += 1
        slot = monitor["slot"]
        return "", self.texts[slot] if slot < len(self.texts) else self.texts[-1]

    def automator(self):
        return types.SimpleNamespace(
            scale_y=1.0,
            iron_grip_click=lambda x, y: None,
            add_card_to_deck=lambda x, y, amount: self.added.append((x, amount)) and False,
        )
