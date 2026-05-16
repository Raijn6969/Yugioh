import ctypes
import tkinter as tk
from tkinter import messagebox
import pyautogui
import time
import pytesseract
from PIL import ImageGrab
import os
import sys

# --- DPI FIX ---
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


# OCR Pfad laden
tess_path = resource_path(os.path.join("Tesseract-OCR", "tesseract.exe"))
pytesseract.pytesseract.tesseract_cmd = tess_path


class MDDebugger:
    def __init__(self, root):
        self.root = root
        self.root.title("MD Ultimate Debugger")
        self.root.geometry("400x450")
        self.root.attributes("-topmost", True)
        self.root.configure(bg="#1e1e1e")

        tk.Label(root, text="Master Duel Diagnose-Tool", font=("Helvetica", 14, "bold"), bg="#1e1e1e", fg="white").pack(
            pady=10)

        # --- TEST 1: OCR BLINDHEIT ---
        tk.Button(root, text="1. OCR-Sichtfeld Testen", command=self.test_ocr, bg="#007acc", fg="white",
                  font=("Helvetica", 11, "bold")).pack(pady=5, fill=tk.X, padx=20)
        tk.Label(root, text="Macht ein Foto der hartcodierten Koordinaten\nund speichert es auf dem PC.", bg="#1e1e1e",
                 fg="#aaaaaa").pack(pady=(0, 15))

        # --- TEST 2: ENGINE KLICK BLOCKADE ---
        tk.Button(root, text="2. Rechten Maus-Klick Testen", command=self.test_click, bg="#cc8800", fg="white",
                  font=("Helvetica", 11, "bold")).pack(pady=5, fill=tk.X, padx=20)
        tk.Label(root,
                 text="Simuliert den 'Iron Grip' Klick. Zeige im\nSpiel auf eine Karte und schau, ob sie ins Deck geht.",
                 bg="#1e1e1e", fg="#aaaaaa").pack()

        self.log_text = tk.Text(root, height=10, width=45, bg="#2d2d2d", fg="#00ff00", font=("Consolas", 9))
        self.log_text.pack(pady=15, padx=10)
        self.log("Debugger bereit.\n")

    def log(self, msg):
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.root.update()

    def test_ocr(self):
        self.log("START: OCR Test in 3 Sekunden...")
        self.log("-> Gehe ins Spiel, zeige auf eine Karte")
        self.log("   sodass ihr Text links groß erscheint!")
        time.sleep(3)

        try:
            # Das ist DEINE originale, fehleranfällige Bounding Box
            bbox = (45, 110, 410, 160)
            img = ImageGrab.grab(bbox=bbox).convert('L')

            img_path = os.path.join(os.path.abspath("."), "debug_vision.png")
            img.save(img_path)

            raw = pytesseract.image_to_string(img, config='--psm 7').strip()

            self.log("\n--- BILD GESPEICHERT ---")
            self.log(f"-> Gelesener Text: '{raw}'")

            messagebox.showinfo(
                "OCR Kamera-Test",
                f"Ich habe exakt das fotografiert, was der Bot sieht.\n\nGelesener Text: '{raw}'\n\nDas Bild liegt hier:\n{img_path}\n\nBITTE SCHAU DIR DAS BILD JETZT AN! Zeigt es den ganzen Namen?"
            )
        except Exception as e:
            self.log(f"FEHLER: {e}")

    def test_click(self):
        self.log("START: Klick Test in 3 Sekunden...")
        self.log("-> Gehe ins Spiel und zeige auf eine Karte")
        time.sleep(3)

        x, y = pyautogui.position()
        self.log(f"Feuere Iron Grip Klick auf X:{x} Y:{y} ab...")

        # Der V13.9 Klick
        pyautogui.mouseDown(button='right')
        time.sleep(0.02)
        pyautogui.mouseUp(button='right')

        self.log("Klick gefeuert! Ist die Karte im Deck?")


if __name__ == "__main__":
    root = tk.Tk()
    app = MDDebugger(root)
    root.mainloop()