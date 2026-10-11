"""Update-Hinweis: Versionsvergleich, Abruf (ohne echtes Netz), Hinweis nur einmal je Version, Tray-Menüpunkt."""

import io
import unittest
import urllib.error
from types import SimpleNamespace
from unittest import mock

import Overlay
import tray
import update_check as uc

SOURCE = '"""Zentrale Pfade."""\n\nimport os\n\nAPP_VERSION = "9.3"\n\n\ndef _app_dir():\n    pass\n'


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def opener_returning(text):
    calls = []

    def opener(request, timeout=None):
        calls.append((request, timeout))
        return FakeResponse(text.encode("utf-8"))

    opener.calls = calls
    return opener


def failing_opener(error):
    def opener(request, timeout=None):
        raise error
    return opener


class VersionTest(unittest.TestCase):
    def test_numeric_comparison(self):
        self.assertTrue(uc.is_newer("9.10", "9.2"))      # nicht als Text verglichen
        self.assertTrue(uc.is_newer("9.3", "9.2"))
        self.assertTrue(uc.is_newer("10.0", "9.99"))
        self.assertFalse(uc.is_newer("9.2", "9.2"))
        self.assertFalse(uc.is_newer("9.2.0", "9.2"))    # gleiche Version
        self.assertFalse(uc.is_newer("9.1", "9.2"))      # Repo älter als die eigene Version
        self.assertTrue(uc.is_newer("9.2.1", "9.2"))

    def test_unreadable_versions_never_count_as_newer(self):
        for bad in ("", "abc", "9.x", "9..2", None, "v9.3"):
            self.assertFalse(uc.is_newer(bad, "9.2"), bad)
            self.assertFalse(uc.is_newer("9.3", bad), bad)

    def test_version_from_source(self):
        self.assertEqual(uc.version_from_source(SOURCE), "9.3")
        self.assertEqual(uc.version_from_source("APP_VERSION = '10.1'\r\n"), "10.1")
        self.assertIsNone(uc.version_from_source("# APP_VERSION = 9.3 steht nur im Kommentar\nx = 1\n"))
        self.assertIsNone(uc.version_from_source("<html>404: Not Found</html>"))


class FindUpdateTest(unittest.TestCase):
    def setUp(self):
        self.log = []

    def find(self, opener, current="9.2"):
        return uc.find_update(current, log=self.log.append, opener=opener)

    def test_newer_version_is_returned_and_request_is_bounded(self):
        opener = opener_returning(SOURCE)
        self.assertEqual(self.find(opener), "9.3")
        (request, timeout), = opener.calls
        self.assertEqual(request.full_url, uc.RAW_URL)
        self.assertEqual(timeout, 5.0)                      # Zeitlimit ca. 5 s
        self.assertIn("raw.githubusercontent.com/Raijn6969/Yugioh/main/app_paths.py", request.full_url)
        self.assertIn("9.3", self.log[-1])

    def test_same_or_older_version_is_silent(self):
        self.assertIsNone(self.find(opener_returning(SOURCE), current="9.3"))
        self.assertIsNone(self.find(opener_returning(SOURCE), current="9.10"))

    def test_network_errors_do_nothing_but_log(self):
        http_error = urllib.error.HTTPError(uc.RAW_URL, 404, "Not Found", {}, io.BytesIO(b""))
        self.addCleanup(http_error.close)
        for error in (urllib.error.URLError("kein Netz"), TimeoutError("zu langsam"), OSError("weg"), http_error,
                      ValueError("kaputt")):
            self.log.clear()
            self.assertIsNone(self.find(failing_opener(error)), error)
            self.assertEqual(len(self.log), 1)
            self.assertIn("[UPDATE]", self.log[0])

    def test_garbage_answer_does_nothing(self):
        self.assertIsNone(self.find(opener_returning("<html>Rate limit</html>")))
        self.assertIn("Keine Version", self.log[-1])

    def test_never_uses_the_real_network(self):
        # urlopen ist die einzige Netz-Stelle: ohne Opener-Parameter würde sie benutzt – hier gemockt
        with mock.patch.object(uc.urllib.request, "urlopen", side_effect=OSError("kein Netz im Test")) as urlopen:
            self.assertIsNone(uc.find_update("9.2", log=self.log.append, opener=uc.urllib.request.urlopen))
        urlopen.assert_called_once()


class OverlayHookTest(unittest.TestCase):
    """Overlay-Teil ohne Fenster: Hinweis nur einmal je Version, nur als exe, Tray-Menüpunkt."""

    def make_app(self, notified=None, tray_icon=True, uptime_ms=0):
        saved, later, toasts = [], [], []
        free = (Overlay.STARTED_TOAST_MS + Overlay.TOAST_GAP_MS) / 1000   # Startmeldung steht noch so lange
        app = SimpleNamespace(
            config={Overlay.UPDATE_NOTIFIED_KEY: notified} if notified else {}, update_version=None,
            tray=mock.Mock() if tray_icon else None, _after_ids={}, _toast_count=0,
            _toast_free_at=Overlay.time.monotonic() + free - uptime_ms / 1000,
            root=SimpleNamespace(after=lambda ms, func: later.append((ms, func)) or "id"),
            _save_config_safely=lambda: saved.append(dict(app.config)))
        app._queue_toast = lambda text: Overlay.MasterDuelImporter._queue_toast(app, text)
        app._show_queued_toast = lambda key, text: Overlay.MasterDuelImporter._show_queued_toast(app, key, text)
        app.saved, app.later, app.toasts = saved, later, toasts
        return app

    def found(self, app, version):
        with mock.patch.object(Overlay, "show_toast", lambda root, text, duration_ms: app.toasts.append(text)):
            Overlay.MasterDuelImporter._on_update_found(app, version)
            while app.later:
                app.later.pop(0)[1]()

    def test_toast_once_per_version_but_tray_item_stays(self):
        app = self.make_app()
        self.found(app, "9.3")
        self.assertEqual(len(app.toasts), 1)
        self.assertIn("MD-Importer 9.3 verfügbar", app.toasts[0])
        app.tray.set_update.assert_called_once_with("9.3")
        self.assertEqual(app.saved[-1][Overlay.UPDATE_NOTIFIED_KEY], "9.3")   # in der Config gemerkt
        # nächster Start (Config geladen): dieselbe Version → kein Toast mehr, Menüpunkt trotzdem
        again = self.make_app(notified="9.3")
        self.found(again, "9.3")
        self.assertEqual(again.toasts, [])
        again.tray.set_update.assert_called_once_with("9.3")
        self.assertEqual(again.saved, [])
        # noch neuere Version → wieder ein Hinweis
        newer = self.make_app(notified="9.3")
        self.found(newer, "9.10")
        self.assertEqual(len(newer.toasts), 1)
        self.assertEqual(newer.saved[-1][Overlay.UPDATE_NOTIFIED_KEY], "9.10")

    def test_toast_waits_for_the_start_message(self):
        app = self.make_app(uptime_ms=1000)
        with mock.patch.object(Overlay, "show_toast"):
            Overlay.MasterDuelImporter._on_update_found(app, "9.3")
        (wait, _), = app.later
        self.assertTrue(4000 <= wait <= Overlay.STARTED_TOAST_MS + Overlay.TOAST_GAP_MS, wait)   # Startmeldung steht 5 s
        late = self.make_app(uptime_ms=60000)
        with mock.patch.object(Overlay, "show_toast"):
            Overlay.MasterDuelImporter._on_update_found(late, "9.3")
        self.assertEqual(late.later[0][0], 0)

    def test_several_toasts_come_one_after_the_other(self):
        app = self.make_app(uptime_ms=60000)
        with mock.patch.object(Overlay, "show_toast"):
            for text in ("eins", "zwei", "drei"):
                Overlay.MasterDuelImporter._queue_toast(app, text)
        waits = [wait for wait, _ in app.later]
        step = Overlay.STARTED_TOAST_MS + Overlay.TOAST_GAP_MS
        self.assertEqual(waits[0], 0)
        self.assertTrue(all(abs((b - a) - step) <= 5 for a, b in zip(waits, waits[1:])), waits)   # nie übereinander

    def test_works_without_tray(self):
        app = self.make_app(tray_icon=False)
        self.found(app, "9.3")
        self.assertEqual(len(app.toasts), 1)

    def test_source_run_does_not_check(self):
        app = SimpleNamespace(_update_worker=lambda: None)
        with mock.patch.object(Overlay.sys, "frozen", False, create=True), \
                mock.patch.object(Overlay.threading, "Thread") as thread:
            Overlay.MasterDuelImporter._start_update_check(app)
        thread.assert_not_called()

    def test_exe_checks_once_in_background(self):
        app = SimpleNamespace(_update_worker=lambda: None)
        with mock.patch.object(Overlay.sys, "frozen", True, create=True), \
                mock.patch.object(Overlay.threading, "Thread") as thread:
            Overlay.MasterDuelImporter._start_update_check(app)
        thread.assert_called_once()
        self.assertTrue(thread.call_args.kwargs["daemon"])
        thread.return_value.start.assert_called_once()

    def test_worker_reports_only_a_newer_version(self):
        queued = []
        app = SimpleNamespace(_run_on_ui=lambda func, *args: queued.append((func, args)), _on_update_found=object())
        with mock.patch.object(Overlay.update_check, "find_update", return_value="9.3") as find:
            Overlay.MasterDuelImporter._update_worker(app)
        self.assertEqual(find.call_args.args[0], Overlay.APP_VERSION)
        self.assertEqual(queued, [(app._on_update_found, ("9.3",))])
        queued.clear()
        with mock.patch.object(Overlay.update_check, "find_update", return_value=None):
            Overlay.MasterDuelImporter._update_worker(app)
        self.assertEqual(queued, [])

    def test_menu_entry_opens_project_page(self):
        with mock.patch.object(Overlay.webbrowser, "open") as opened:
            Overlay.MasterDuelImporter._open_project_page(SimpleNamespace())
        opened.assert_called_once_with("https://github.com/Raijn6969/Yugioh")
        with mock.patch.object(Overlay.webbrowser, "open", side_effect=OSError("kein Browser")):
            Overlay.MasterDuelImporter._open_project_page(SimpleNamespace())   # kein Absturz


class FakePystray:
    """Nur das, was tray.py benutzt: Menü mit Einträgen, die man auswerten kann."""
    class Menu:
        SEPARATOR = object()

        def __init__(self, *items):
            self.items = items

    class MenuItem:
        def __init__(self, text, action, checked=None, radio=False, default=False, visible=True, enabled=True):
            self.text, self.action, self.visible, self.enabled = text, action, visible, enabled

        def label(self):
            return self.text(self) if callable(self.text) else self.text

        def shown(self):
            return self.visible(self) if callable(self.visible) else self.visible

    class Icon:
        def __init__(self, name, image, title, menu):
            self.menu, self.updated = menu, 0

        def run(self):
            pass

        def update_menu(self):
            self.updated += 1

        def stop(self):
            pass


class TrayMenuTest(unittest.TestCase):
    def make(self, **callbacks):
        with mock.patch.object(tray, "pystray", FakePystray), mock.patch.object(tray.threading, "Thread"):
            icon = tray.TrayIcon("MD Importer 9.2", on_restart=lambda: None, on_quit=lambda: None, **callbacks)
        items = [i for i in icon._icon.menu.items if isinstance(i, FakePystray.MenuItem)]
        return icon, items

    def test_update_item_only_visible_after_a_newer_version_was_found(self):
        opened = []
        icon, items = self.make(on_update=lambda: opened.append(True))
        update = items[1]
        self.assertFalse(update.shown())
        icon.set_update("9.10")
        self.assertTrue(update.shown())
        self.assertEqual(update.label(), "MD-Importer 9.10 verfügbar – Seite öffnen")
        self.assertEqual(icon._icon.updated, 1)           # Menü neu aufgebaut
        update.action(icon._icon, update)
        self.assertEqual(opened, [True])
        self.assertEqual([i.label() for i in items if i.shown()],
                         ["MD Importer 9.2", "MD-Importer 9.10 verfügbar – Seite öffnen", "Neustart", "Beenden"])

    def test_without_pystray_nothing_happens(self):
        with mock.patch.object(tray, "pystray", None):
            icon = tray.TrayIcon("x", on_restart=lambda: None, on_quit=lambda: None)
            icon.set_update("9.3")   # kein Absturz
        self.assertFalse(icon.available)


if __name__ == "__main__":
    unittest.main()
