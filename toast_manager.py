"""
Toast-Manager: Verwaltet und staffelt Benachrichtigungen über der Taskleiste.

Verhindert, dass sich mehrere Benachrichtigungen (Startmeldung, Rückblick, Updates)
überlagern, indem sie zeitlich hintereinander mit einem definierten Abstand abgespielt werden.
"""

import time
import tkinter as tk
from typing import Dict, Optional

from dark_dialog import show_toast

DEFAULT_TOAST_MS = 5000
DEFAULT_GAP_MS = 300


class ToastManager:
    """Verwaltet eine Warteschlange von Toasts für ein Tkinter-Fenster."""

    def __init__(self, root: tk.Misc, duration_ms: int = DEFAULT_TOAST_MS, gap_ms: int = DEFAULT_GAP_MS):
        self.root = root
        self.duration_ms = duration_ms
        self.gap_ms = gap_ms
        # Startzeitpunkt reservieren (Startmeldung hat Vorrang)
        self._toast_free_at = time.monotonic() + (duration_ms + gap_ms) / 1000.0
        self._toast_count = 0
        self._active_timers: Dict[str, str] = {}

    @property
    def toast_free_at(self) -> float:
        return self._toast_free_at

    @toast_free_at.setter
    def toast_free_at(self, value: float) -> None:
        self._toast_free_at = value

    @property
    def toast_count(self) -> int:
        return self._toast_count

    def queue_toast(self, text: str, duration_ms: Optional[int] = None) -> str:
        """
        Fügt eine Nachricht zur Warteschlange hinzu.
        Mehrere Nachrichten erscheinen nacheinander, nicht übereinander.
        Returns: Key des gespeicherten Timers.
        """
        dur = duration_ms or self.duration_ms
        now = time.monotonic()
        start = max(now, self._toast_free_at)
        self._toast_free_at = start + (dur + self.gap_ms) / 1000.0
        self._toast_count += 1
        key = f"toast{self._toast_count}"
        delay_ms = max(0, int((start - now) * 1000))
        timer_id = self.root.after(delay_ms, lambda: self._show_queued_toast(key, text, dur))
        self._active_timers[key] = timer_id
        return key

    def _show_queued_toast(self, key: str, text: str, duration_ms: int) -> None:
        self._active_timers.pop(key, None)
        try:
            show_toast(self.root, text, duration_ms=duration_ms)
        except tk.TclError:
            pass  # Fenster wurde zwischenzeitlich geschlossen

    def cancel_all(self) -> None:
        """Bricht alle noch ausstehenden Timer ab."""
        for timer_id in list(self._active_timers.values()):
            try:
                self.root.after_cancel(timer_id)
            except Exception:
                pass
        self._active_timers.clear()
