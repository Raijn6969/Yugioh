"""
Hänger-Wächter: Der Haupt-Thread (Tk) meldet sich regelmäßig mit beat(). Bleibt der Herzschlag länger als `threshold`
aus, schreibt ein Hintergrund-Thread einmal pro Hänger den Stack des Haupt-Threads (und kurz die der anderen) ins Log –
so sieht man beim nächsten „reagiert nicht mehr“, wo er hing. Der Thread ruft nichts von Tk auf.
"""

import sys
import threading
import time
import traceback
from typing import Callable


class HangWatch:
    def __init__(self, log: Callable[[str], None], threshold: float = 2.0, check: float = 0.5,
                 clock: Callable[[], float] = time.monotonic):
        self._log = log
        self._threshold = threshold
        self._check = check
        self._clock = clock
        self._last = clock()
        self._hang_since = None   # Zeitpunkt des letzten Beats vor dem laufenden Hänger (None = kein Hänger)
        self._stop = threading.Event()
        self._thread = None

    def beat(self) -> None:
        """Vom Haupt-Thread regelmäßig aufrufen."""
        self._last = self._clock()

    def start(self) -> None:
        self._last = self._clock()
        self._thread = threading.Thread(target=self._run, daemon=True, name="hang-watch")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._check):
            try:
                self._tick()
            except Exception:
                pass  # Der Wächter darf nie selbst stören

    def _tick(self) -> None:
        now = self._clock()
        age = now - self._last
        if self._hang_since is None and age > self._threshold:
            self._hang_since = self._last
            self._log(f"[HÄNGER] Fenster reagiert seit {age:.1f} s nicht – Haupt-Thread:\n" + self._stacks())
        elif self._hang_since is not None and age <= self._threshold:
            self._log(f"[HÄNGER] vorbei nach {now - self._hang_since:.1f} s")
            self._hang_since = None

    @staticmethod
    def _stacks() -> str:
        main_id = threading.main_thread().ident
        frames = sys._current_frames()
        main = frames.get(main_id)
        parts = ["".join(traceback.format_stack(main)).rstrip() if main else "(kein Stack)"]
        for thread in threading.enumerate():
            frame = frames.get(thread.ident)
            if frame is None or thread.ident == main_id or thread.name == "hang-watch":
                continue
            lines = "".join(traceback.format_stack(frame)).rstrip().splitlines()[-3:]
            parts.append(f"Thread {thread.name}:\n" + "\n".join(lines))
        return "\n".join(parts)
