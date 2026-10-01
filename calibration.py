"""
Kalibrierungs-Assistent: nimmt die Bildschirmpunkte auf, die der Import anklickt bzw. ausliest.
"""

import tkinter as tk
import pyautogui


class CalibrationWizard:
    # (Config-Schlüssel, Beschreibung, optional)
    STEPS = [
        ("SEARCH_BAR", "Suchleiste (Mitte)", False),
        ("FIRST_CARD", "Erste Karte (Slot ganz oben links)", False),
        ("TRASH_BTN", "Mülleimer-Icon", False),
        ("TRASH_CONFIRM", "Löschen Bestätigen", False),
        ("UNOWNED_BTN", "Crafting-Button", False),
        # Optional: Zahl der Karten im Main Deck. Damit prüft der Import nach jedem Einfügen,
        # ob die Karte wirklich angekommen ist, und klickt verlorene Kopien nach.
        ("DECK_COUNT", "Deck-Kartenzahl (Main Deck)", True),
    ]

    def __init__(self, master, config, on_complete_callback, on_cancel_callback):
        self.top = tk.Toplevel(master)
        self.top.overrideredirect(True)
        self.top.wm_attributes("-topmost", True)

        w, h = 300, 420
        self.top.geometry(f"{w}x{h}+{master.winfo_x()}+{max(0, master.winfo_y() - h - 10)}")
        self.top.configure(bg="#1e1e1e")

        # Auf einer Kopie arbeiten: Bei "Abbrechen" bleibt die alte Kalibrierung unverändert
        self.config = dict(config)
        self.on_complete = on_complete_callback
        self.on_cancel = on_cancel_callback
        self.current_step = 0

        self.steps = [(key, desc) for key, desc, _ in self.STEPS]
        self.optional = {key for key, _, optional in self.STEPS if optional}
        self.labels = []
        self._build_ui()

    def _build_ui(self):
        header = tk.Frame(self.top, bg="#007acc", height=35)
        header.pack(fill=tk.X)
        tk.Label(header, text="Kalibrierungs-Assistent", fg="white", bg="#007acc", font=("Helvetica", 11, "bold")).pack(pady=8)

        list_frame = tk.Frame(self.top, bg="#1e1e1e", padx=15, pady=10)
        list_frame.pack(fill=tk.BOTH, expand=True)

        for key, desc in self.steps:
            suffix = "  (optional)" if key in self.optional else ""
            lbl = tk.Label(list_frame, text=f"✗  {desc}{suffix}", fg="#ff4444", bg="#1e1e1e",
                           font=("Helvetica", 11), wraplength=260, justify="left")
            lbl.pack(anchor="w", pady=3)
            self.labels.append(lbl)

        self.info_lbl = tk.Label(self.top, text="Klicke Start und zeige mit der Maus\nauf das Ziel. (3s Timer)", fg="#aaaaaa", bg="#1e1e1e", font=("Helvetica", 10))
        self.info_lbl.pack(pady=2)

        self.action_btn = tk.Button(self.top, text="Start: Suchleiste", bg="#007acc", fg="white", bd=0, font=("Helvetica", 11, "bold"), command=self.start_timer)
        self.action_btn.pack(pady=8, ipadx=10, ipady=5)

        # Nur bei optionalen Schritten sichtbar
        self.skip_btn = tk.Button(self.top, text="Überspringen", bg="#444444", fg="white", bd=0,
                                  font=("Helvetica", 9), command=self.skip_step)

        tk.Button(self.top, text="Abbrechen", bg="#cc0000", fg="white", bd=0, font=("Helvetica", 9), command=self.cancel_wizard).pack(pady=5)
        self._highlight_current_step()

    def _highlight_current_step(self):
        for i, lbl in enumerate(self.labels):
            if i == self.current_step:
                lbl.config(fg="white", font=("Helvetica", 11, "bold"))
            elif i > self.current_step:
                lbl.config(fg="#ff4444", font=("Helvetica", 11))
        self._update_skip_button()

    def _update_skip_button(self):
        is_optional = self.current_step < len(self.steps) and self.steps[self.current_step][0] in self.optional
        if is_optional:
            self.skip_btn.pack(before=self.action_btn, pady=2)
            self.info_lbl.config(text="Optional: Zahl der Karten im Main Deck.\n"
                                      "Damit prüft der Import jede eingefügte Karte.")
        else:
            self.skip_btn.pack_forget()

    def start_timer(self):
        self.action_btn.config(state=tk.DISABLED)
        self.skip_btn.config(state=tk.DISABLED)
        self.countdown(3)

    def countdown(self, count):
        if count > 0:
            self.action_btn.config(text=f"Maus bewegen... {count}", bg="#cc8800")
            self.top.after(1000, self.countdown, count - 1)
        else:
            self.capture_point()

    def capture_point(self):
        x, y = pyautogui.position()
        key, desc = self.steps[self.current_step]
        self.config[key] = [x, y]
        self.labels[self.current_step].config(text=f"✓  {desc}", fg="#00ff00", font=("Helvetica", 11))
        self._next_step()

    def skip_step(self):
        key, desc = self.steps[self.current_step]
        self.config.pop(key, None)
        self.labels[self.current_step].config(text=f"–  {desc} (übersprungen)", fg="#888888",
                                              font=("Helvetica", 11))
        self._next_step()

    def _next_step(self):
        self.current_step += 1
        self.skip_btn.config(state=tk.NORMAL)
        if self.current_step < len(self.steps):
            next_desc = self.steps[self.current_step][1]
            self._highlight_current_step()
            self.action_btn.config(text=f"Start: {next_desc}", bg="#007acc", state=tk.NORMAL)
        else:
            self.skip_btn.pack_forget()
            self.action_btn.config(text="Speichern & Beenden", bg="#00ff00", fg="black", state=tk.NORMAL, command=self.finish)
            self.info_lbl.config(text="Alle Punkte erfolgreich erfasst!", fg="#00ff00")

    def cancel_wizard(self):
        self.top.destroy()
        if self.on_cancel:
            self.on_cancel()

    def finish(self):
        self.on_complete(self.config)
        self.top.destroy()
