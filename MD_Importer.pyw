"""
Starter für den Doppelklick: .pyw-Dateien startet Windows mit pythonw.exe, also ohne
Konsolenfenster. Weil es dann keine Konsole für Fehlermeldungen gibt, wird ein Startfehler
in einem Fenster angezeigt und in md_startup_error.log gespeichert.
"""

import os
import sys
import traceback

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

try:
    from Overlay import main
    main()
except Exception:
    details = traceback.format_exc()
    try:
        with open(os.path.join(APP_DIR, "md_startup_error.log"), "w", encoding="utf-8") as f:
            f.write(details)
    except OSError:
        pass
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror("MD Importer konnte nicht starten",
                         f"{details.strip().splitlines()[-1]}\n\nDetails: md_startup_error.log")
    root.destroy()
