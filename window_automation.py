import ctypes
import ctypes.wintypes
import time
import pyautogui

pyautogui.PAUSE = 0.001

class WindowAutomator:
    def __init__(self, config):
        self.config = config
        self.last_bot_pos = (0, 0)
        self.scale_x, self.scale_y = self.get_md_scale()

    def get_md_scale(self):
        try:
            for title in ["MASTER DUEL", "Master Duel", "master duel"]:
                hwnd = ctypes.windll.user32.FindWindowW(None, title)
                if hwnd:
                    rect = ctypes.wintypes.RECT()
                    ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    win_w = rect.right - rect.left
                    win_h = rect.bottom - rect.top
                    if win_w > 0 and win_h > 0:
                        return win_w / 1920, win_h / 1080
        except Exception:
            pass

        screen_w, screen_h = pyautogui.size()
        return screen_w / 1920, screen_h / 1080

    def check_user_interruption(self):
        point = ctypes.wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
        if abs(point.x - self.last_bot_pos[0]) > 20 or abs(point.y - self.last_bot_pos[1]) > 20:
            raise Exception("Manuelle Mausbewegung erkannt! Abbruch.")

    def iron_grip_click(self, x: int, y: int, button: str = 'left'):
        self.check_user_interruption()
        ctypes.windll.user32.SetCursorPos(x, y)
        time.sleep(0.015)

        if button == 'left':
            ctypes.windll.user32.mouse_event(2, 0, 0, 0, 0)
            time.sleep(0.005)
            ctypes.windll.user32.mouse_event(4, 0, 0, 0, 0)
        elif button == 'right':
            ctypes.windll.user32.mouse_event(8, 0, 0, 0, 0)
            time.sleep(0.005)
            ctypes.windll.user32.mouse_event(16, 0, 0, 0, 0)

        self.last_bot_pos = (x, y)

    def add_card_to_deck(self, click_x: int, click_y: int, amount: int):
        self.iron_grip_click(click_x, click_y)
        time.sleep(0.03)

        self.check_user_interruption()
        c_speed = max(self.config.get("CLICK_SPEED", 0.03), 0.02)

        for _ in range(amount):
            ctypes.windll.user32.mouse_event(8, 0, 0, 0, 0)
            time.sleep(0.01)
            ctypes.windll.user32.mouse_event(16, 0, 0, 0, 0)
            time.sleep(c_speed)

        time.sleep(0.03)
        self.last_bot_pos = (click_x, click_y)

    def is_crafting_active(self):
        try:
            hdc = ctypes.windll.user32.GetDC(0)
            pixel = ctypes.windll.gdi32.GetPixel(hdc, self.config["UNOWNED_BTN"][0], self.config["UNOWNED_BTN"][1])
            ctypes.windll.user32.ReleaseDC(0, hdc)
            r = pixel & 0xff
            g = (pixel >> 8) & 0xff
            b = (pixel >> 16) & 0xff
            return g > 100 and g > r + 20 and g > b + 20
        except Exception:
            return False