import ctypes
import tkinter as tk
from tkinter import messagebox
import pyautogui
import pyperclip
import time
import threading
import concurrent.futures
import os
import sys
import json
import hashlib  # NEU FÜR DEN BILD-HASH
from collections import Counter
from PIL import ImageGrab, ImageOps
import pytesseract

from utils import clean_text, sanitize_name, parse_clipboard, fetch_name_from_api, is_exact_match, get_cached_ocr, CardMatcher, GhostTracker
from calibration import CalibrationWizard


try:
    # Zwingt Windows zu echten Hardware-Pixeln. Macht manuelles DPI-Scaling überflüssig!
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

pyautogui.PAUSE = 0.001


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


pytesseract.pytesseract.tesseract_cmd = resource_path(os.path.join("Tesseract-OCR", "tesseract.exe"))
CONFIG_FILE = "md_config.json"


class MasterDuelImporter:
    def __init__(self, root):
        self.root = root
        self.is_running = False
        self.last_bot_pos = (0, 0)
        self.config = self.load_config()
        self._build_main_ui()

    def load_config(self):
        base_config = {
            "SEARCH_BAR": [1404, 245], "FIRST_CARD": [1390, 400],
            "TRASH_BTN": [1246, 127], "TRASH_CONFIRM": [1167, 665],
            "UNOWNED_BTN": [1786, 209],
            "OFFSET_X": 88, "OFFSET_Y": 135,
            "CLICK_SPEED": 0.03, "SEARCH_DELAY": 0.7,  # Du kannst versuchen, das mit dem Cache auf 0.7 zu senken!
            "LANGUAGE": "en",
            "IS_CALIBRATED": False
        }
        loaded = base_config.copy()
        needs_save = True

        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    file_data = json.load(f)
                    for key, val in file_data.items():
                        loaded[key] = val
                needs_save = any(k not in file_data for k in base_config)
            except Exception:
                pass

        self.config = loaded
        if needs_save:
            self.save_config()
        return loaded

    def save_config(self):
        with open(CONFIG_FILE, "w") as f:
            json.dump(self.config, f, indent=4)

    def _build_main_ui(self):
        self.root.title("MD Importer V18.4 (Turbo Scan)")
        self.root.overrideredirect(True)
        self.root.wm_attributes("-topmost", True)
        self.root.wm_attributes("-alpha", 0.95)
        self.root.configure(bg='#1e1e1e')
        self.root.geometry("280x85+1343+900")

        self.root.bind("<ButtonPress-1>", self.start_move)
        self.root.bind("<B1-Motion>", self.do_move)

        self.status_label = tk.Label(self.root, text="Bereit für Import", fg="#00ff00", bg="#1e1e1e",
                                     font=("Helvetica", 10, "bold"))
        self.status_label.pack(pady=4)

        btn_frame = tk.Frame(self.root, bg="#1e1e1e")
        btn_frame.pack()

        self.start_btn = tk.Button(btn_frame, text="Start Import", command=self.start_import_thread, bg="#007acc",
                                   fg="white", bd=0, width=12, font=("Helvetica", 10, "bold"))
        self.start_btn.pack(side=tk.LEFT, padx=5, ipady=3)

        self.calib_btn = tk.Button(btn_frame, text="Kalibrieren", command=self.open_calibration, bg="#444444",
                                   fg="white", bd=0, width=12, font=("Helvetica", 10))
        self.calib_btn.pack(side=tk.LEFT, padx=5, ipady=3)

        tk.Button(self.root, text="X", command=self.root.destroy, bg="#cc0000", fg="white", bd=0,
                  font=("Helvetica", 9, "bold")).place(x=245, y=0, width=35, height=22)

    def start_move(self, event):
        # Nur wenn das angeklickte Element das Hauptfenster (Hintergrund) selbst ist, erlauben wir das Draggen
        if event.widget == self.root:
            self.root.x, self.root.y = event.x, event.y
        else:
            self.root.x = None

    def do_move(self, event):
        # Nur verschieben, wenn der Klick auf dem Hintergrund gestartet wurde
        if getattr(self.root, 'x', None) is not None:
            self.root.geometry(f"+{self.root.winfo_pointerx() - self.root.x}+{self.root.winfo_pointery() - self.root.y}")

    def update_status(self, text, color="white"):
        self.status_label.config(text=text, fg=color)

    def open_calibration(self):
        self.start_btn.config(state=tk.DISABLED)
        self.calib_btn.config(state=tk.DISABLED)
        CalibrationWizard(self.root, self.config, self.on_calibration_done, self.on_calibration_cancel)

    def on_calibration_cancel(self):
        self.update_status("Kalibrierung abgebrochen", "yellow")
        self.start_btn.config(state=tk.NORMAL)
        self.calib_btn.config(state=tk.NORMAL)

    def on_calibration_done(self, new_config):
        self.config = new_config
        self.config["IS_CALIBRATED"] = True
        self.save_config()
        self.update_status("Kalibrierung aktiv!", "#00ff00")
        self.start_btn.config(state=tk.NORMAL)
        self.calib_btn.config(state=tk.NORMAL)

    def check_user_interruption(self):
        curr_x, curr_y = pyautogui.position()
        # Erhöht von 5 auf 20 Pixel, damit extrem schnelle Klicks keinen Fehl-Abbruch auslösen
        if abs(curr_x - self.last_bot_pos[0]) > 20 or abs(curr_y - self.last_bot_pos[1]) > 20:
            raise Exception("Manuelle Mausbewegung erkannt! Abbruch.")

    def iron_grip_click(self, x, y, button='left', hover_time=0.03):
        self.check_user_interruption()
        pyautogui.moveTo(x, y, duration=hover_time)
        time.sleep(0.05)
        pyautogui.mouseDown(button=button)
        time.sleep(0.03)
        pyautogui.mouseUp(button=button)
        self.last_bot_pos = (x, y)

    def is_crafting_active(self):
        try:
            r, g, b = pyautogui.pixel(self.config["UNOWNED_BTN"][0], self.config["UNOWNED_BTN"][1])
            return g > 100 and g > r + 20 and g > b + 20
        except Exception:
            return False

    def prefetch_all_cards(self, counts):
        self.update_status("API Check...", "cyan")
        cards = []
        lang = self.config.get("LANGUAGE", "en")
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as exe:
            futures = [exe.submit(fetch_name_from_api, cid, amt, lang) for cid, amt in counts.items()]
            for f in concurrent.futures.as_completed(futures):
                res = f.result()
                if res: cards.append(res)
        return cards

    # --- HILFSFUNKTION FÜR DEN CACHE ---
    def do_ocr(self, img):
        return pytesseract.image_to_string(img, config='--psm 7').strip()

    def run_import(self):
        self.is_running = True
        self.start_btn.config(state=tk.DISABLED)
        failed_cards = []
        successfully_added = []

        log_path = "md_debug.log"
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(f"=== DEEP MODULAR DIAGNOSTIC RUN: {time.strftime('%Y-%m-%d %H:%M:%S')} (V21.0 MODULAR) ===\n")

        try:
            raw_clip = pyperclip.paste()
            card_ids = parse_clipboard()
            if not card_ids:
                self.update_status("Fehler: Kein YDKE/YDK!", "red")
                return

            original_counts = Counter(card_ids)
            self.update_status("API Check...", "cyan")
            cards_ready = []
            id_to_name_map = {}

            lang = self.config.get("LANGUAGE", "en")
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as exe:
                future_to_id = {exe.submit(fetch_name_from_api, cid, amt, lang): (cid, amt) for cid, amt in
                                original_counts.items()}
                for future in concurrent.futures.as_completed(future_to_id):
                    cid, amt = future_to_id[future]
                    try:
                        res = future.result()
                        if res:
                            raw_name, _ = res
                            cards_ready.append(res)
                            id_to_name_map[cid] = raw_name
                    except Exception:
                        pass

            for i in range(3, 0, -1):
                self.update_status(f"Loslassen! ({i}s)", "yellow")
                time.sleep(1)

            self.last_bot_pos = pyautogui.position()

            self.update_status("Leere Deck...", "yellow")
            self.iron_grip_click(*self.config["TRASH_BTN"], hover_time=0.05)
            time.sleep(0.5)
            self.iron_grip_click(*self.config["TRASH_CONFIRM"], hover_time=0.05)
            time.sleep(0.6)

            if not self.is_crafting_active():
                self.iron_grip_click(*self.config["UNOWNED_BTN"])
                time.sleep(0.2)

            screen_w, screen_h = pyautogui.size()
            scale_x = screen_w / 1920
            scale_y = screen_h / 1080

            for raw_name, amount in cards_ready:
                clean_name = sanitize_name(raw_name)
                self.update_status(f"-> {clean_name[:12]}", "cyan")
                pyperclip.copy(clean_name)

                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"\n[SUCHE] '{clean_name}' (Erwartet: {amount}x)\n")

                self.iron_grip_click(*self.config["SEARCH_BAR"])
                time.sleep(0.15)
                pyautogui.hotkey('ctrl', 'a')
                time.sleep(0.1)
                pyautogui.press('backspace')
                time.sleep(0.1)
                pyautogui.hotkey('ctrl', 'v')
                time.sleep(0.1)
                pyautogui.press('enter')

                time.sleep(self.config.get("SEARCH_DELAY", 0.85))

                found = False
                best_scan = ""

                # Instanziierung des isolierten GhostTrackers für diese Suche
                tracker = GhostTracker()

                for slot in range(24):
                    row, col = slot // 6, slot % 6
                    target_x = self.config["FIRST_CARD"][0] + int(col * self.config.get("OFFSET_X", 88) * scale_x)
                    target_y = self.config["FIRST_CARD"][1] + int(row * self.config.get("OFFSET_Y", 125) * scale_y)

                    self.iron_grip_click(target_x, target_y)
                    time.sleep(0.15)

                    x1 = int(15 * scale_x)
                    y1 = int(115 * scale_y)
                    x2 = int(550 * scale_x)
                    y2 = int(155 * scale_y)

                    img = ImageGrab.grab(bbox=(x1, y1, x2, y2)).convert('L')
                    img_inverted = ImageOps.invert(img)
                    img_processed = img_inverted.point(lambda p: 0 if p < 150 else 255)

                    img_hash = hashlib.md5(img_processed.tobytes()).hexdigest()
                    raw_ocr = get_cached_ocr(img_hash, self.do_ocr, img_processed)
                    s_c = clean_text(raw_ocr)

                    img.close()
                    img_inverted.close()
                    img_processed.close()

                    # Zustand via GhostTracker Modul bestimmen
                    is_ghost = tracker.track_slot(slot, s_c)
                    origin_slot = tracker.get_origin()

                    with open(log_path, "a", encoding="utf-8") as f:
                        type_str = f" (Ghost von Slot {origin_slot:02d})" if is_ghost else " (Echter Inhalt)"
                        f.write(f"  Slot {slot:02d} -> Gelesen: '{raw_ocr.strip()}'{type_str}\n")

                    if not s_c:
                        break

                    if len(s_c) > len(best_scan):
                        best_scan = raw_ocr

                    # Aufruf des dedizierten CardMatchers
                    if CardMatcher.is_exact_match(clean_name, raw_ocr):
                        found = True

                        # Klick-Koordinaten auf Basis des ermittelten Ursprungs ausrichten
                        orig_row, orig_col = origin_slot // 6, origin_slot % 6
                        click_x = self.config["FIRST_CARD"][0] + int(
                            orig_col * self.config.get("OFFSET_X", 88) * scale_x)
                        click_y = self.config["FIRST_CARD"][1] + int(
                            orig_row * self.config.get("OFFSET_Y", 125) * scale_y)

                        with open(log_path, "a", encoding="utf-8") as f:
                            if is_ghost:
                                f.write(
                                    f"    ==> MATCH BESTÄTIGT! Umleitung von Ghost-Slot {slot:02d} auf Real-Slot {origin_slot:02d}\n")
                            else:
                                f.write(f"    ==> MATCH BESTÄTIGT! Klicke Direkt-Slot {slot:02d}\n")

                        self.iron_grip_click(click_x, click_y)
                        time.sleep(0.05)

                        self.check_user_interruption()
                        c_speed = max(self.config.get("CLICK_SPEED", 0.035), 0.025)

                        for _ in range(amount):
                            pyautogui.mouseDown(button='right')
                            time.sleep(0.02)
                            pyautogui.mouseUp(button='right')
                            time.sleep(c_speed)

                        time.sleep(0.1)
                        self.last_bot_pos = (click_x, click_y)

                        successfully_added.append({
                            "expected_clean": clean_text(clean_name),
                            "expected_raw": clean_name,
                            "actual_ocr": raw_ocr,
                            "amount": amount
                        })
                        break

                if not found:
                    failed_cards.append((clean_name, best_scan))

            # Endabgleich
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("\n" + "=" * 85 + "\n")
                f.write(f"{' ' * 20}ULTRA-DIAGNOSE MATRIX (MODULARER ENDABGLEICH)\n")
                f.write("=" * 85 + "\n\n")
                has_errors = False
                for req_id, req_amount in original_counts.items():
                    req_name = id_to_name_map.get(req_id, f"Unbekannte ID {req_id}")
                    req_clean = sanitize_name(req_name)
                    actual_amount = sum(item["amount"] for item in successfully_added if
                                        item["expected_clean"] == clean_text(req_clean))

                    status = "[OK]" if actual_amount == req_amount else "[DECK-LÜCKE]"
                    if actual_amount != req_amount: has_errors = True
                    f.write(f"{status:<15} | {req_clean[:40]:<40} | Soll: {req_amount:<2} | Ist: {actual_amount:<2}\n")

            if failed_cards or has_errors:
                self.update_status("Mit Fehlern fertig!", "#ffaa00")
                messagebox.showwarning("Deck-Audit", "Abweichungen im modularen Lauf erkannt!")
            else:
                self.update_status("Import Erfolgreich!", "#00ff00")

        except Exception as e:
            self.update_status("Abbruch / Fehler", "red")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"\n[CRITICAL ERROR]: {str(e)}\n")
        finally:
            self.is_running = False
            self.start_btn.config(state=tk.NORMAL)

    def start_import_thread(self):
        if not self.config.get("IS_CALIBRATED", False):
            self.update_status("Kalibrierung nötig!", "yellow")
            self.open_calibration()
            return

        if not self.is_running:
            threading.Thread(target=self.run_import, daemon=True).start()


if __name__ == "__main__":
    root = tk.Tk()
    app = MasterDuelImporter(root)
    root.mainloop()