"""
Tray-Icon im Infobereich der Taskleiste mit Fehlerbericht, Neustart, Beenden und (bei einer neueren Version) dem
Update-Hinweis.

pystray hat eine eigene Nachrichtenschleife; sie läuft in einem Daemon-Thread, damit ein
vergessenes stop() das Programm nie am Beenden hindert. Die Menü-Aktionen kommen aus diesem
Thread – der Aufrufer muss sie selbst in den Tk-Thread weiterreichen.

Rechtsklick: Das native Windows-Menü lässt sich weder dunkel färben noch in der Zeilenhöhe ändern. Mit `on_menu`
fängt eine pystray.Icon-Unterklasse WM_RBUTTONUP ab und ruft on_menu(x, y) (Tray-Thread) statt das native Menü zu
zeigen; das Overlay ruft dann im Tk-Thread show_menu() auf (DarkMenu). Ohne on_menu oder auf einem anderen
pystray-Backend bleibt das native Menü.
"""

import threading
from typing import Callable, Optional

import win_api
from app_icon import create_icon_image
from dark_menu import DarkMenu

try:
    import pystray
except ImportError:  # Ohne pystray läuft das Programm einfach ohne Tray-Icon
    pystray = None

WM_RBUTTONUP = 0x0205
QUIT_HOVER = "#6b2a2a"  # "Beenden" hovert rötlich


def _icon_class(on_menu: Callable[[int, int], None]):
    """pystray.Icon, die den Rechtsklick an on_menu(x, y) gibt, statt das native Menü zu zeigen."""
    base = pystray.Icon
    if not hasattr(base, "_on_notify"):  # nicht das Win32-Backend → natives Menü behalten
        return base

    class Icon(base):
        def _on_notify(self, wparam, lparam):
            if lparam == WM_RBUTTONUP:
                on_menu(*win_api.get_cursor_pos())
            else:
                super()._on_notify(wparam, lparam)
    return Icon


class TrayIcon:
    def __init__(self, title: str, on_restart: Callable[[], None], on_quit: Callable[[], None],
                 on_update: Optional[Callable[[], None]] = None, on_report: Optional[Callable[[], None]] = None,
                 on_menu: Optional[Callable[[int, int], None]] = None):
        self._icon = None
        self._menu: Optional[DarkMenu] = None
        self._title, self._on_restart, self._on_quit = title, on_restart, on_quit
        self._on_update, self._on_report = on_update, on_report
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
        icon_class = _icon_class(on_menu) if on_menu else pystray.Icon
        self._icon = icon_class("md_importer", create_icon_image(64), title, pystray.Menu(*items))
        threading.Thread(target=self._icon.run, daemon=True, name="tray").start()

    def show_menu(self, master, x: int, y: int) -> None:
        """Dunkles Tray-Menü mit der unteren rechten Ecke bei (x, y). Nur im Tk-Thread aufrufen."""
        if self._menu is not None:
            self._menu.close()
        menu = self._menu = DarkMenu(master, font=("Segoe UI", 10), row_pady=8)
        menu.add_label(self._title)
        if self._update_version is not None and self._on_update:
            menu.add_command(f"Update auf {self._update_version} installieren", self._on_update)
        menu.add_separator()
        if self._on_report is not None:
            menu.add_command("Fehlerbericht erstellen", self._on_report)
        menu.add_command("Neustart", self._on_restart)
        menu.add_command("Beenden", self._on_quit, hover=QUIT_HOVER)
        menu.popup_at(x, y)

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
        if self._menu is not None:
            self._menu.close()
        if self._icon is None:
            return
        try:
            self._icon.stop()
        except Exception:
            pass  # Icon war evtl. noch nicht fertig gestartet; der Daemon-Thread endet mit dem Programm
        self._icon = None
