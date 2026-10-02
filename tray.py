"""
Tray-Icon im Infobereich der Taskleiste mit Neustart und Beenden.

pystray hat eine eigene Nachrichtenschleife; sie läuft in einem Daemon-Thread, damit ein
vergessenes stop() das Programm nie am Beenden hindert. Die Menü-Aktionen kommen aus diesem
Thread – der Aufrufer muss sie selbst in den Tk-Thread weiterreichen.
"""

import threading
from typing import Callable

from app_icon import create_icon_image

try:
    import pystray
except ImportError:  # Ohne pystray läuft das Programm einfach ohne Tray-Icon
    pystray = None


class TrayIcon:
    def __init__(self, title: str, on_restart: Callable[[], None], on_quit: Callable[[], None]):
        self._icon = None
        if pystray is None:
            return
        menu = pystray.Menu(
            pystray.MenuItem(title, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Neustart", lambda icon, item: on_restart()),
            pystray.MenuItem("Beenden", lambda icon, item: on_quit()),
        )
        self._icon = pystray.Icon("md_importer", create_icon_image(64), title, menu)
        threading.Thread(target=self._icon.run, daemon=True, name="tray").start()

    @property
    def available(self) -> bool:
        return self._icon is not None

    def stop(self) -> None:
        if self._icon is None:
            return
        try:
            self._icon.stop()
        except Exception:
            pass  # Icon war evtl. noch nicht fertig gestartet; der Daemon-Thread endet mit dem Programm
        self._icon = None
