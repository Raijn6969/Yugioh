"""
Tray-Icon im Infobereich der Taskleiste mit Fehlerbericht, Neustart, Beenden und (bei einer neueren Version) dem
Update-Hinweis.

pystray hat eine eigene Nachrichtenschleife; sie läuft in einem Daemon-Thread, damit ein
vergessenes stop() das Programm nie am Beenden hindert. Die Menü-Aktionen kommen aus diesem
Thread – der Aufrufer muss sie selbst in den Tk-Thread weiterreichen.
"""

import threading
from typing import Callable, Optional

from app_icon import create_icon_image

try:
    import pystray
except ImportError:  # Ohne pystray läuft das Programm einfach ohne Tray-Icon
    pystray = None


class TrayIcon:
    def __init__(self, title: str, on_restart: Callable[[], None], on_quit: Callable[[], None],
                 on_update: Optional[Callable[[], None]] = None, on_report: Optional[Callable[[], None]] = None):
        self._icon = None
        self._update_version: Optional[str] = None  # neuere Version gefunden → Menüpunkt "Update installieren"
        if pystray is None:
            return
        items = [pystray.MenuItem(title, None, enabled=False),
                 # Nur sichtbar, wenn der Update-Hinweis (update_check) eine neuere Version gefunden hat
                 pystray.MenuItem(lambda item: f"Update auf {self._update_version} installieren",
                                  lambda icon, item: on_update and on_update(),
                                  visible=lambda item: self._update_version is not None),
                 pystray.Menu.SEPARATOR]
        if on_report is not None:
            items.append(pystray.MenuItem("Fehlerbericht erstellen", lambda icon, item: on_report()))
        items += [pystray.MenuItem("Neustart", lambda icon, item: on_restart()),
                  pystray.MenuItem("Beenden", lambda icon, item: on_quit())]
        self._icon = pystray.Icon("md_importer", create_icon_image(64), title, pystray.Menu(*items))
        threading.Thread(target=self._icon.run, daemon=True, name="tray").start()

    @property
    def available(self) -> bool:
        return self._icon is not None

    def set_update(self, version: str) -> None:
        """Menüpunkt "Update auf <version> installieren" einblenden."""
        self._update_version = version
        if self._icon is None:
            return
        try:
            self._icon.update_menu()
        except Exception:
            pass  # Icon noch nicht fertig gestartet – der Punkt erscheint beim nächsten Öffnen des Menüs

    def notify(self, text: str, title: str = "") -> None:
        """Windows-Benachrichtigung am Tray-Icon (z.B. neue Matches übernommen)."""
        if self._icon is None:
            return
        try:
            self._icon.notify(text, title)
        except Exception:
            pass  # Benachrichtigungen aus/nicht unterstützt – nur Zusatz

    def stop(self) -> None:
        if self._icon is None:
            return
        try:
            self._icon.stop()
        except Exception:
            pass  # Icon war evtl. noch nicht fertig gestartet; der Daemon-Thread endet mit dem Programm
        self._icon = None
