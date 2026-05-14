import tkinter as tk
import pyautogui
import pyperclip
import requests
import time
import base64
import struct
import threading
import concurrent.futures
import difflib

# === OCR VISION IMPORTS ===
USE_VISION = True
if USE_VISION:
    import pytesseract
    from PIL import ImageGrab

    # Tesseract Pfad anpassen falls nötig!
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# === KONFIGURATION ===
SEARCH_BAR_X = 1404
SEARCH_BAR_Y = 20<8

# Start-Position der ersten Karte und Scanner-Settings
FIRST_CARD_X = 1390
FIRST_CARD_Y = 400
CARD_OFFSET_X = 88  # Pixel-Abstand zur nächsten Karte nach rechts (ggf. auf 105 erhöhen, falls er daneben klickt)
MAX_SEARCH_SLOTS = 6  # Wie viele Karten sollen maximal gescannt werden?

UNOWNED_BTN_X = 1786
UNOWNED_BTN_Y = 209

# Mülleimer
TRASH_BTN_X = 1246
TRASH_BTN_Y = 127
TRASH_CONFIRM_X = 1167
TRASH_CONFIRM_Y = 665

# OCR
CARD_NAME_REGION = (45, 110, 410, 160)

OVERLAY_X = 1343
OVERLAY_Y = 1005

# --- DAS ÜBERGÖTTLICHE SPEED-TIMING ---
DELAY_CLEAR_FOCUS = 0.15
DELAY_MICRO = 0.05
DELAY_UI = 0.05
DELAY_SEARCH = 0.8
DELAY_SCAN_SLOT = 0.15
DELAY_MULTI_CLICK = 0.065

pyautogui.PAUSE = 0.0
pyautogui.FAILSAFE = True


# ========================================

class MasterDuelImporter:
    def __init__(self, root):
        self.root = root
        self.is_running = False
        self.last_bot_pos = (0, 0)
        self.setup_ui()

    def setup_ui(self):
        self.root.title("MD Importer V8 (Archetype-Fix)")
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        self.root.wm_attributes("-alpha", 0.9)
        self.root.configure(bg='#1e1e1e')
        self.root.geometry(f"220x80+{OVERLAY_X}+{OVERLAY_Y}")

        self.root.bind("<ButtonPress-1>", self.start_move)
        self.root.bind("<B1-Motion>", self.do_move)

        self.status_label = tk.Label(self.root, text="Bereit für Import", fg="#00ff00", bg="#1e1e1e",
                                     font=("Helvetica", 10, "bold"))
        self.status_label.pack(pady=5)

        self.btn_frame = tk.Frame(self.root, bg="#1e1e1e")
        self.btn_frame.pack()

        self.start_btn = tk.Button(self.btn_frame, text="Start Import", command=self.start_import_thread, bg="#007acc",
                                   fg="white", bd=0, font=("Helvetica", 10, "bold"), width=12)
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.close_btn = tk.Button(self.btn_frame, text="X", command=self.root.destroy, bg="#cc0000", fg="white", bd=0,
                                   font=("Helvetica", 10, "bold"))
        self.close_btn.pack(side=tk.LEFT)

    def start_move(self, event):
        self.root.x = event.x
        self.root.y = event.y

    def do_move(self, event):
        x = self.root.winfo_pointerx() - self.root.x
        y = self.root.winfo_pointery() - self.root.y
        self.root.geometry(f"+{x}+{y}")

    def update_status(self, text, color="white"):
        self.status_label.config(text=text, fg=color)

    # ==========================================
    # SICHERHEITS-WRAPPER (Maus-Tracking)
    # ==========================================
    def check_user_interruption(self):
        curr_x, curr_y = pyautogui.position()
        bot_x, bot_y = self.last_bot_pos
        if abs(curr_x - bot_x) > 5 or abs(curr_y - bot_y) > 5:
            raise Exception("Manuelle Mausbewegung erkannt!")

    def bot_click(self, x, y, clicks=1, button='left'):
        self.check_user_interruption()
        pyautogui.click(x=x, y=y, clicks=clicks, button=button)
        self.last_bot_pos = pyautogui.position()

    # ==========================================
    # TITANEN KLICK (Speed-Version)
    # ==========================================
    def heavy_click(self, x, y, hover_wait=0.15, press_time=0.1):
        """Noch schnellerer Titan-Klick für den Start"""
        self.check_user_interruption()
        pyautogui.moveTo(x, y, duration=0.15)
        time.sleep(hover_wait)
        pyautogui.mouseDown()
        time.sleep(press_time)
        pyautogui.mouseUp()
        self.last_bot_pos = pyautogui.position()

    def is_crafting_active(self):
        try:
            r, g, b = pyautogui.pixel(UNOWNED_BTN_X, UNOWNED_BTN_Y)
            if g > 100 and g > r + 20 and g > b + 20:
                return True
            return False
        except:
            return False

    def fetch_name_from_api(self, card_id, amount):
        url = f"https://db.ygoprodeck.com/api/v7/cardinfo.php?id={card_id}"
        try:
            res = requests.get(url, timeout=5)
            if res.status_code == 200:
                return (res.json()['data'][0]['name'], amount)
        except:
            pass
        return None

    def prefetch_all_cards(self, counts):
        self.update_status("Lade API Daten...", "cyan")
        cards_to_add = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(self.fetch_name_from_api, cid, amt) for cid, amt in counts.items()]
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                if result:
                    cards_to_add.append(result)
        return cards_to_add

    def parse_clipboard(self):
        clipboard = pyperclip.paste().strip()
        card_ids = []
        if clipboard.startswith("ydke://"):
            raw_data = clipboard.replace("ydke://", "")
            for section in raw_data.split('!'):
                if not section: continue
                try:
                    section += '=' * (4 - len(section) % 4)
                    decoded = base64.b64decode(section)
                    for i in range(len(decoded) // 4):
                        cid = struct.unpack('<I', decoded[i * 4: (i + 1) * 4])[0]
                        card_ids.append(str(cid))
                except:
                    pass
        else:
            for line in clipboard.split('\n'):
                line = line.strip()
                if line.isdigit(): card_ids.append(line)
        return card_ids

    def run_import(self):
        self.is_running = True
        self.start_btn.config(state=tk.DISABLED)

        try:
            card_ids = self.parse_clipboard()
            if not card_ids:
                self.update_status("Kein YDKE/YDK!", "red")
                return

            from collections import Counter
            counts = Counter(card_ids)
            cards_ready = self.prefetch_all_cards(counts)

            # --- 2-SEKUNDEN SPEED-GNADENFRIST ---
            for i in range(2, 0, -1):
                self.update_status(f"Loslassen! ({i}s)", "yellow")
                time.sleep(1)

            self.last_bot_pos = pyautogui.position()

            # --- PHASE 1: DECK WIPE ---
            self.update_status("Leere Deck...", "yellow")
            self.heavy_click(TRASH_BTN_X, TRASH_BTN_Y, hover_wait=0.15, press_time=0.1)
            time.sleep(0.4)  # Schnelleres Warten auf das Popup
            self.heavy_click(TRASH_CONFIRM_X, TRASH_CONFIRM_Y, hover_wait=0.15, press_time=0.1)
            time.sleep(0.6)

            # --- PHASE 2: CRAFTING CHECK ---
            self.update_status("Prüfe Crafting...", "yellow")
            if not self.is_crafting_active():
                self.heavy_click(UNOWNED_BTN_X, UNOWNED_BTN_Y, hover_wait=0.15, press_time=0.1)
                time.sleep(0.4)

            # --- PHASE 3: HIGHSPEED KARTEN-LOOP ---
            for name, amount in cards_ready:
                self.update_status(f"Suche: {name[:12]}", "cyan")
                pyperclip.copy(name)

                self.bot_click(SEARCH_BAR_X, SEARCH_BAR_Y)
                time.sleep(DELAY_CLEAR_FOCUS)

                self.check_user_interruption()
                pyautogui.hotkey('ctrl', 'a')
                time.sleep(DELAY_MICRO)

                pyautogui.press('backspace')
                time.sleep(DELAY_MICRO)

                pyautogui.hotkey('ctrl', 'v')
                time.sleep(DELAY_UI)

                pyautogui.press('enter')

                time.sleep(DELAY_SEARCH)

                card_found = False
                slots_to_check = MAX_SEARCH_SLOTS if USE_VISION else 1

                for slot in range(slots_to_check):
                    current_x = FIRST_CARD_X + (slot * CARD_OFFSET_X)

                    if USE_VISION:
                        self.bot_click(current_x, FIRST_CARD_Y)
                        time.sleep(DELAY_SCAN_SLOT)

                        screen = ImageGrab.grab(bbox=CARD_NAME_REGION)
                        scanned_raw = pytesseract.image_to_string(screen, config='--psm 6').strip()

                        # 1. Säuberung (Nur Buchstaben und Zahlen)
                        def quick_clean(t):
                            return "".join(filter(str.isalnum, t.lower()))

                        s_clean = quick_clean(scanned_raw)
                        n_clean = quick_clean(name)

                        # 2. Smart Anchor Logik (Gottes-Update V10)
                        # Wir suchen das längste Wort, das NICHT an erster Stelle steht.
                        # Das ignoriert den Archetyp, findet aber den Kern (ZEUS).
                        words = name.split()
                        if len(words) > 1:
                            # Nimm das längste Wort aus dem Rest des Namens
                            anchor = quick_clean(max(words[1:], key=len))
                        else:
                            anchor = n_clean

                        anchor_match = (len(anchor) > 2 and anchor in s_clean)

                        # 3. Substring-Check für abgeschnittene Namen (wie bei Zeus)
                        # Wenn der Scan ein großer Teil des echten Namens ist, lassen wir es durch.
                        is_substring = (len(s_clean) > 6 and s_clean in n_clean)

                        similarity = difflib.SequenceMatcher(None, n_clean, s_clean).ratio()

                        # Treffer wenn: Anker passt ODER Substring-Match ODER Ähnlichkeit > 0.8
                        if anchor_match or is_substring or similarity > 0.8:
                            similarity = 1.0

                        if similarity < 0.8:
                            print(f"[Slot {slot + 1}] Falsch ('{scanned_raw}'). Scanne nächste...")
                            continue

                            # Richtiges Ziel gefunden!
                    card_found = True
                    for _ in range(amount):
                        self.bot_click(current_x, FIRST_CARD_Y, button='right')
                        time.sleep(DELAY_MULTI_CLICK)

                    break

                if not card_found:
                    print(f"[!!!] FEHLER: '{name}' nach {MAX_SEARCH_SLOTS} Versuchen nicht gefunden.")

            self.update_status("Import Erfolgreich!", "#00ff00")

        except Exception as e:
            self.update_status("Abgebrochen!", "red")
            print(f"Import gestoppt: {e}")
        finally:
            self.is_running = False
            self.start_btn.config(state=tk.NORMAL)

    def start_import_thread(self):
        if not self.is_running:
            threading.Thread(target=self.run_import, daemon=True).start()


if __name__ == "__main__":
    root = tk.Tk()
    app = MasterDuelImporter(root)
    root.mainloop()