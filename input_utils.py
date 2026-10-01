"""
Suchbegriff in die Master-Duel-Suchleiste schreiben (per Zwischenablage, ohne Backspace-Tippen).
"""
import time
import pyperclip
import pyautogui

import win_api


def focus_master_duel():
    # Import hier, weil window_automation beim Laden pyautogui konfiguriert
    from window_automation import find_md_window
    hwnd = find_md_window()
    if hwnd:
        win_api.set_foreground_window(hwnd)
        time.sleep(0.05)


def type_card_name(automator, clean_name: str, config: dict):
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
    time.sleep(0.05)

    # 4. Alles markieren (STRG+A)
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.02)

    # 5. Einfügen (STRG+V) - Überschreibt den markierten Text direkt
    pyautogui.hotkey('ctrl', 'v')

    # 6. Render-Delay (Minimal gehalten)
    time.sleep(0.01)