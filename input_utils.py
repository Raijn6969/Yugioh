"""
Suchbegriff in die Master-Duel-Suchleiste schreiben (per Zwischenablage, ohne Backspace-Tippen).
"""
import time
import pyperclip
import pyautogui

import win_api
from debug_log import dlog


FOCUS_TIMEOUT = 1.0  # So lange wird versucht, Master Duel nach vorne zu holen


class FocusLostError(RuntimeError):
    """Master Duel ist nicht vorne: Klicks und Tastatureingaben würden in einem anderen Programm landen."""


def md_in_front(hwnd: int) -> bool:
    """Ist Master Duel (dieses Fenster oder ein anderes Fenster desselben Prozesses) im Vordergrund?"""
    front = win_api.get_foreground_window()
    if not front:
        return False
    if front == hwnd:
        return True
    pid = win_api.window_pid(hwnd)
    return pid != 0 and win_api.window_pid(front) == pid


def focus_master_duel() -> int:
    """
    Master Duel nach vorne holen und prüfen, dass es geklappt hat (Windows verweigert das manchmal).
    Returns: Fenster-Handle. Raises FocusLostError, wenn Master Duel nicht nach vorne kommt.
    """
    # Import hier, weil window_automation beim Laden pyautogui konfiguriert
    from window_automation import find_md_window
    hwnd = find_md_window()
    if not hwnd:
        raise FocusLostError("Master Duel wurde nicht gefunden.")
    deadline = time.monotonic() + FOCUS_TIMEOUT
    # Nur umschalten (inkl. kurzer Pause), wenn Master Duel nicht ohnehin schon vorne ist
    while not md_in_front(hwnd):
        if time.monotonic() >= deadline:
            raise FocusLostError("Master Duel ist nicht im Vordergrund (anderes Fenster davor?). Angehalten, "
                                 "damit keine Klicks oder Eingaben in einem anderen Programm landen.")
        win_api.set_foreground_window(hwnd)
        time.sleep(0.05)
    return hwnd


def type_card_name(automator, clean_name: str, config: dict):
    # 1. Zwischenablage laden (kann kurz von einem anderen Programm belegt sein)
    for attempt in range(3):
        try:
            pyperclip.copy(clean_name)
            break
        except Exception as e:
            if attempt == 2:
                dlog(f"[ZWISCHENABLAGE] Suchbegriff '{clean_name}' nicht kopierbar ({e}) → Suche wird "
                     f"bei Bedarf neu eingetippt.")
            time.sleep(0.05)

    # 2. Fenster fokussieren
    focus_master_duel()

    # 3. Klick in Suchleiste
    x, y = config["SEARCH_BAR"]
    automator.iron_grip_click(x, y)
    time.sleep(0.05)

    # Der Klick kann ein Fenster getroffen haben, das sich davor geschoben hat (Benachrichtigung o.Ä.):
    # Vor den Tastenkürzeln nochmal prüfen, sonst markiert/ersetzt Strg+A/Strg+V dort Text
    focus_master_duel()

    # 4. Alles markieren (STRG+A)
    pyautogui.hotkey('ctrl', 'a')
    time.sleep(0.02)

    # 5. Einfügen (STRG+V) - Überschreibt den markierten Text direkt
    pyautogui.hotkey('ctrl', 'v')

    # 6. Render-Delay (Minimal gehalten)
    time.sleep(0.01)


def submit_search() -> None:
    """Getippte Suche abschicken (Enter). Vorher prüfen, dass Master Duel vorne ist – Enter darf nie woanders landen."""
    focus_master_duel()
    pyautogui.press('enter')
