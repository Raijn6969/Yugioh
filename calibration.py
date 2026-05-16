import tkinter as tk
import pyautogui

class CalibrationWizard:
    def __init__(self, master, config, on_complete_callback, on_cancel_callback):
        self.top = tk.Toplevel(master)
        self.top.overrideredirect(True)
        self.top.wm_attributes("-topmost", True)

        w, h = 280, 360
        self.top.geometry(f"{w}x{h}+{master.winfo_x()}+{master.winfo_y() - h - 10}")
        self.top.configure(bg="#1e1e1e")

        self.config = config
        self.on_complete = on_complete_callback
        self.on_cancel = on_cancel_callback
        self.current_step = 0

        self.steps = [
            ("SEARCH_BAR", "Suchleiste (Mitte)"),
            ("FIRST_CARD", "Erste Karte (Slot ganz oben links)"),
            ("TRASH_BTN", "Mülleimer-Icon"),
            ("TRASH_CONFIRM", "Löschen Bestätigen"),
            ("UNOWNED_BTN", "Crafting-Button")
        ]
        self.labels = []
        self._build_ui()

    def _build_ui(self):
        header = tk.Frame(self.top, bg="#007acc", height=35)
        header.pack(fill=tk.X)
        tk.Label(header, text="Kalibrierungs-Assistent", fg="white", bg="#007acc", font=("Helvetica", 11, "bold")).pack(pady=8)

        list_frame = tk.Frame(self.top, bg="#1e1e1e", padx=15, pady=10)
        list_frame.pack(fill=tk.BOTH, expand=True)

        for _, desc in self.steps:
            lbl = tk.Label(list_frame, text=f"✗  {desc}", fg="#ff4444", bg="#1e1e1e", font=("Helvetica", 11))
            lbl.pack(anchor="w", pady=3)
            self.labels.append(lbl)

        self.info_lbl = tk.Label(self.top, text="Klicke Start und zeige mit der Maus\nauf das Ziel. (3s Timer)", fg="#aaaaaa", bg="#1e1e1e", font=("Helvetica", 10))
        self.info_lbl.pack(pady=2)

        self.action_btn = tk.Button(self.top, text="Start: Suchleiste", bg="#007acc", fg="white", bd=0, font=("Helvetica", 11, "bold"), command=self.start_timer)
        self.action_btn.pack(pady=8, ipadx=10, ipady=5)

        tk.Button(self.top, text="Abbrechen", bg="#cc0000", fg="white", bd=0, font=("Helvetica", 9), command=self.cancel_wizard).pack(pady=5)
        self._highlight_current_step()

    def _highlight_current_step(self):
        for i, lbl in enumerate(self.labels):
            if i == self.current_step:
                lbl.config(fg="white", font=("Helvetica", 11, "bold"))
            elif i > self.current_step:
                lbl.config(fg="#ff4444", font=("Helvetica", 11))

    def start_timer(self):
        self.action_btn.config(state=tk.DISABLED)
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

        self.current_step += 1
        if self.current_step < len(self.steps):
            next_desc = self.steps[self.current_step][1]
            self._highlight_current_step()
            self.action_btn.config(text=f"Start: {next_desc}", bg="#007acc", state=tk.NORMAL)
        else:
            self.action_btn.config(text="Speichern & Beenden", bg="#00ff00", fg="black", state=tk.NORMAL, command=self.finish)
            self.info_lbl.config(text="Alle Punkte erfolgreich erfasst!", fg="#00ff00")

    def cancel_wizard(self):
        self.top.destroy()
        if self.on_cancel:
            self.on_cancel()

    def finish(self):
        self.on_complete(self.config)
        self.top.destroy()