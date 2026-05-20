"""
INPUT UTILS (V1.5 - NO BACKSPACE)
"""
import time
import ctypes
import pyperclip
import pyautogui

pyautogui.PAUSE = 0.0

def focus_master_duel():
    hwnd = ctypes.windll.user32.FindWindowW(None, "MASTER DUEL")
    if hwnd:
        ctypes.windll.user32.SetForegroundWindow(hwnd)
        time.sleep(0.05)


def type_card_name(automator, clean_name: str, config: dict, speed_buffer: float):
    # 1. Zwischenablage laden
    for _ in range(3):
        try:
            pyperclip.copy(clean_name)
            break
        except Exception:
            time.sleep(0.02)

    # 2. Fenster fokussieren
    focus_master_duel()

    # 3. Klick in Suchleiste
    x, y = config["SEARCH_BAR"]
    automator.iron_grip_click(x, y)
    time.sleep(0.05 + speed_buffer)

    # 4. Alles markieren (STRG+A)
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.02)

    # 5. Einfügen (STRG+V) - Überschreibt den markierten Text direkt
    pyautogui.hotkey('ctrl', 'v')

    # 6. Render-Delay (Minimal gehalten)
    time.sleep(0.01 + speed_buffer)