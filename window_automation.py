"""
Maus-Steuerung für Master Duel: Klicks, Karten einfügen, Ruckler-Erkennung, Pixel-Checks.
"""

import time
from typing import Optional, Tuple

import pyautogui
import win32gui
import win32process

import win_api

MD_WINDOW_TITLES = ("MASTER DUEL", "Master Duel", "master duel")
MD_PROCESS_NAME = "masterduel.exe"


_cached_hwnd: Optional[int] = None


def find_md_window() -> Optional[int]:
    """
    Master-Duel-Fenster finden: erst über den Titel, sonst über den Prozessnamen.
    Das Ergebnis wird gemerkt (Aufruf vor jeder Suche), solange das Fenster existiert.
    """
    global _cached_hwnd
    if _cached_hwnd and win_api.is_window(_cached_hwnd):
        return _cached_hwnd
    _cached_hwnd = _search_md_window()
    return _cached_hwnd


def _search_md_window() -> Optional[int]:
    for title in MD_WINDOW_TITLES:
        hwnd = win_api.find_window(title)
        if hwnd:
            return hwnd

    found = []

    def check(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            exe = win_api.process_image_name(pid)
            if exe and exe.lower().endswith(MD_PROCESS_NAME):
                found.append(hwnd)
                return False  # Suche beenden
        return True

    try:
        win32gui.EnumWindows(check, None)
    except Exception:
        pass  # EnumWindows meldet das vorzeitige Beenden als Fehler
    return found[0] if found else None


def get_md_window_size() -> Optional[Tuple[int, int]]:
    """Größe des Master-Duel-Fensters in Pixeln, None falls es nicht läuft."""
    hwnd = find_md_window()
    rect = win_api.get_window_rect(hwnd) if hwnd else None
    if not rect:
        return None
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    return (width, height) if width > 0 and height > 0 else None

# Pause, die pyautogui nach jedem eigenen Aufruf (z.B. Tastenkürzel) einlegt.
# Wird nur hier gesetzt, damit der Wert nicht von der Import-Reihenfolge abhängt.
pyautogui.PAUSE = 0.001


class UserInterrupt(Exception):
    """Der Spieler hat die Maus bewegt → Import sofort anhalten (darf nirgends abgefangen werden)."""


class WindowAutomator:
    def __init__(self, config):
        self.config = config
        self.last_bot_pos = (0, 0)
        # Linke obere Ecke des Spielbereichs: (0, 0) im Vollbild, sonst Fensterposition
        self.origin = (0, 0)
        self.scale_x, self.scale_y = self.get_md_scale()

    def get_md_scale(self):
        # Innenbereich (ohne Titelleiste/Rahmen im Fenstermodus): Daran richtet sich das Spiel aus
        hwnd = find_md_window()
        client = win_api.get_client_rect(hwnd) if hwnd else None
        if client and client[2] > 0 and client[3] > 0:
            self.origin = (client[0], client[1])
            return client[2] / 1920, client[3] / 1080
        size = get_md_window_size()
        if size:
            return size[0] / 1920, size[1] / 1080

        # Fenster nicht gefunden → Bildschirmgröße (Vollbild)
        screen_w, screen_h = pyautogui.size()
        return screen_w / 1920, screen_h / 1080

    def check_user_interruption(self):
        x, y = win_api.get_cursor_pos()
        if abs(x - self.last_bot_pos[0]) > 20 or abs(y - self.last_bot_pos[1]) > 20:
            raise UserInterrupt("Manuelle Mausbewegung erkannt! Abbruch.")

    def iron_grip_click(self, x: int, y: int, button: str = 'left'):
        self.check_user_interruption()
        win_api.set_cursor_pos(x, y)
        time.sleep(0.015)

        if button == 'left':
            win_api.mouse_event(win_api.MOUSE_LEFT_DOWN)
            time.sleep(0.005)
            win_api.mouse_event(win_api.MOUSE_LEFT_UP)
        elif button == 'right':
            win_api.mouse_event(win_api.MOUSE_RIGHT_DOWN)
            time.sleep(0.005)
            win_api.mouse_event(win_api.MOUSE_RIGHT_UP)

        self.last_bot_pos = (x, y)

    def scroll(self, x: int, y: int, notches: int):
        """Mausrad über (x, y) drehen: positiv = nach oben, negativ = nach unten (in Rasten)."""
        self.check_user_interruption()
        win_api.set_cursor_pos(x, y)
        time.sleep(0.015)
        step = win_api.WHEEL_DELTA if notches > 0 else -win_api.WHEEL_DELTA
        for _ in range(abs(notches)):
            win_api.mouse_wheel(step)
            time.sleep(0.03)  # Jede Raste einzeln, damit das Spiel keine verschluckt
        self.last_bot_pos = (x, y)

    # Ein Klick-Zyklus, der so viel länger dauert als geplant, deutet auf einen System-Ruckler hin.
    STALL_TOLERANCE = 0.10

    def add_card_to_deck(self, click_x: int, click_y: int, amount: int) -> bool:
        """Fügt die Karte `amount`-mal hinzu. Gibt True zurück, wenn dabei ein Ruckler erkannt wurde."""
        stalled = False

        t0 = time.perf_counter()
        self.iron_grip_click(click_x, click_y)
        time.sleep(0.03)
        if time.perf_counter() - t0 > 0.05 + self.STALL_TOLERANCE:
            stalled = True

        self.check_user_interruption()
        c_speed = max(self.config.get("CLICK_SPEED", 0.03), 0.02)

        for _ in range(amount):
            t_click = time.perf_counter()
            win_api.mouse_event(win_api.MOUSE_RIGHT_DOWN)
            time.sleep(0.01)
            win_api.mouse_event(win_api.MOUSE_RIGHT_UP)
            time.sleep(c_speed)
            if time.perf_counter() - t_click > 0.01 + c_speed + self.STALL_TOLERANCE:
                stalled = True

        time.sleep(0.03)
        self.last_bot_pos = (click_x, click_y)

        if stalled:
            self.wait_until_stable()
        return stalled

    def wait_until_stable(self, max_wait: float = 2.0):
        """Wartet nach einem Ruckler, bis 5 kurze Sleeps in Folge pünktlich zurückkommen."""
        deadline = time.perf_counter() + max_wait
        on_time = 0
        while on_time < 5 and time.perf_counter() < deadline:
            t = time.perf_counter()
            time.sleep(0.02)
            on_time = on_time + 1 if time.perf_counter() - t < 0.035 else 0
        time.sleep(0.25)  # Master Duel braucht nach einem Hänger meist noch ein paar Frames

    def is_crafting_active(self):
        """Ist der Crafting-Button aktiv (grün)? Liest die Pixelfarbe an UNOWNED_BTN."""
        try:
            rgb = win_api.get_pixel(*self.config["UNOWNED_BTN"])
            if rgb is None:
                return False
            r, g, b = rgb
            return g > 100 and g > r + 20 and g > b + 20
        except Exception:
            return False