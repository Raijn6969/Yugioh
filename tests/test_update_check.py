"""Update-Hinweis: Versionsvergleich, Release-Abfrage (ohne echtes Netz), Frage nur einmal je Version, Ein-Klick-Ablauf
in UpdateFlow (alles gemockt), Tray-Menüpunkt."""

import io
import json
import os
import tempfile
import unittest
import urllib.error
from types import SimpleNamespace
from unittest import mock

import Overlay
import tray
import update_check as uc
import update_flow
import updater
from update_flow import UpdateFlow

ZIP_URL = "https://github.com/Raijn6969/Yugioh/releases/download/v9.4/MD_ImporterV9.4.zip"


def release_json(tag="v9.4", assets=None):
    if assets is None:
        assets = [{"name": "MD_ImporterV9.4.rar", "size": 5, "browser_download_url": "https://github.com/x.rar"},
                  {"name": "MD_ImporterV9.4.zip", "size": 1234, "browser_download_url": ZIP_URL}]
    return {"tag_name": tag, "html_url": "https://github.com/Raijn6969/Yugioh/releases/tag/" + tag, "assets": assets}


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
        self.assertFalse(uc.is_newer("9.1", "9.2"))      # Release älter als die eigene Version
        self.assertTrue(uc.is_newer("9.2.1", "9.2"))

    def test_unreadable_versions_never_count_as_newer(self):
        for bad in ("", "abc", "9.x", "9..2", None, "v9.3"):
            self.assertFalse(uc.is_newer(bad, "9.2"), bad)
            self.assertFalse(uc.is_newer("9.3", bad), bad)


class ParseReleaseTest(unittest.TestCase):
    def test_tag_with_v_and_zip_asset(self):
        release = uc.parse_release(release_json())
        self.assertEqual(release, uc.Release("9.4", "https://github.com/Raijn6969/Yugioh/releases/tag/v9.4",
                                             ZIP_URL, 1234))

    def test_tag_without_v(self):
        self.assertEqual(uc.parse_release(release_json(tag="9.10")).version, "9.10")

    def test_zip_on_a_foreign_host_is_ignored(self):
        for url in ("https://evil.example/MD.zip", "http://github.com/MD.zip", "https://github.com.evil.example/a.zip"):
            asset = {"name": "MD.zip", "size": 10, "browser_download_url": url}
            self.assertEqual(uc.parse_release(release_json(assets=[asset])).zip_url, "", url)
        ok = {"name": "MD.zip", "size": 10, "browser_download_url": "https://objects.githubusercontent.com/a/MD.zip"}
        self.assertTrue(uc.parse_release(release_json(assets=[ok])).zip_url)

    def test_no_zip_means_no_one_click(self):
        for assets in ([], [{"name": "MD.rar", "size": 5, "browser_download_url": "https://github.com/MD.rar"}],
                       [{"name": "MD.zip"}], ["kaputt", None]):
            release = uc.parse_release(release_json(assets=assets))
            self.assertEqual((release.zip_url, release.zip_size), ("", 0), assets)

    def test_missing_page_falls_back_to_the_releases_url(self):
        data = release_json()
        del data["html_url"]
        self.assertEqual(uc.parse_release(data).page, uc.RELEASES_URL)

    def test_garbage_gives_none(self):
        for data in (None, [], {}, {"tag_name": None}, {"tag_name": "nightly"}, {"tag_name": "v9.x"}):
            self.assertIsNone(uc.parse_release(data), data)


class FindUpdateTest(unittest.TestCase):
    def setUp(self):
        self.log = []

    def find(self, opener, current="9.3"):
        return uc.find_update(current, log=self.log.append, opener=opener)

    def test_newer_release_is_returned_and_request_is_bounded(self):
        opener = opener_returning(json.dumps(release_json()))
        release = self.find(opener)
        self.assertEqual((release.version, release.zip_url), ("9.4", ZIP_URL))
        (request, timeout), = opener.calls
        self.assertEqual(request.full_url, uc.RELEASE_API)
        self.assertEqual(request.get_header("User-agent"), "MD-Importer-Update")
        self.assertEqual(request.get_header("Accept"), "application/vnd.github+json")
        self.assertEqual(timeout, 5.0)                      # Zeitlimit ca. 5 s
        self.assertIn("api.github.com/repos/Raijn6969/Yugioh/releases/latest", request.full_url)
        self.assertIn("9.4", self.log[-1])

    def test_same_or_older_release_is_silent(self):
        opener = opener_returning(json.dumps(release_json()))
        self.assertIsNone(self.find(opener, current="9.4"))
        self.assertIsNone(self.find(opener_returning(json.dumps(release_json())), current="9.10"))

    def test_network_errors_do_nothing_but_log(self):
        http_error = urllib.error.HTTPError(uc.RELEASE_API, 404, "Not Found", {}, io.BytesIO(b""))
        self.addCleanup(http_error.close)
        for error in (urllib.error.URLError("kein Netz"), TimeoutError("zu langsam"), OSError("weg"), http_error,
                      ValueError("kaputt")):
            self.log.clear()
            self.assertIsNone(self.find(failing_opener(error)), error)
            self.assertEqual(len(self.log), 1)
            self.assertIn("[UPDATE]", self.log[0])

    def test_garbage_answer_does_nothing(self):
        for text in ("<html>Rate limit</html>", "{}", json.dumps({"tag_name": "nightly"})):
            self.log.clear()
            self.assertIsNone(self.find(opener_returning(text)), text)
            self.assertIn("Keine Version", self.log[-1])

    def test_never_uses_the_real_network(self):
        # urlopen ist die einzige Netz-Stelle: ohne Opener-Parameter würde sie benutzt – hier gemockt
        with mock.patch.object(uc.urllib.request, "urlopen", side_effect=OSError("kein Netz im Test")) as urlopen:
            self.assertIsNone(uc.find_update("9.3", log=self.log.append, opener=uc.urllib.request.urlopen))
            with self.assertRaises(OSError):   # wer will, erfährt den Fehler (Knopf „Nach Update suchen“)
                uc.find_update("9.3", log=self.log.append, opener=uc.urllib.request.urlopen, raise_errors=True)
        self.assertEqual(urlopen.call_count, 2)


def make_release(version="9.4", zip_url=ZIP_URL):
    return uc.Release(version, "https://github.com/Raijn6969/Yugioh/releases/tag/v" + version, zip_url, 1234)


class UpdateFlowTest(unittest.TestCase):
    """Update-Ablauf ohne Fenster (Fakes nur für die Konstruktor-Abhängigkeiten): Frage nur einmal je Version, nur als
    exe, Ein-Klick-Ablauf."""

    def setUp(self):
        md = mock.patch.object(update_flow, "find_md_window", return_value=None)   # Master Duel läuft nicht
        self.md_window = md.start()
        self.addCleanup(md.stop)

    def make_flow(self, notified=None, tray_icon=True, running=False, config=None):
        saved, later, shown, queued = [], [], [], []
        if config is None:
            config = {update_flow.UPDATE_NOTIFIED_KEY: notified} if notified else {}
        env = SimpleNamespace(busy=running, front=False, in_duel=None)   # vom Test veränderbarer Zustand des Overlays
        flow = UpdateFlow(
            root=SimpleNamespace(after=lambda ms, func: later.append((ms, func)) or "id"),
            get_config=lambda: config, save_config=lambda: saved.append(dict(config)),
            status=lambda text, color="white": shown.append((text, color)),
            run_on_ui=lambda func, *args: queued.append((func, args)), after_ids={},
            get_tray=lambda: flow.tray_icon, busy=lambda: env.busy, md_in_front=lambda: env.front, md_retry_ms=20,
            in_duel=lambda: env.in_duel, set_buttons=mock.Mock(), show_overlay=mock.Mock(), close=mock.Mock())
        flow.tray_icon = mock.Mock() if tray_icon else None
        flow.config, flow.env = config, env
        flow.saved, flow.later, flow.shown, flow.queued = saved, later, shown, queued
        return flow

    def found(self, flow, release, answer=None):
        with mock.patch.object(update_flow, "ask_choice", return_value=answer) as ask:
            flow.on_found(release)
            while flow.later:
                flow.later.pop(0)[1]()
        return ask

    def run_queue(self, flow):
        while flow.queued:
            func, args = flow.queued.pop(0)
            func(*args)

    def one_click_env(self):
        """Als exe in einem beschreibbaren Programmordner."""
        for patcher in (mock.patch.object(update_flow.sys, "frozen", True, create=True),
                        mock.patch.object(update_flow, "DATA_DIR", update_flow.APP_DIR)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_question_once_per_version_but_tray_item_stays(self):
        flow = self.make_flow()
        ask = self.found(flow, make_release("9.3"))
        ask.assert_called_once()
        self.assertEqual(ask.call_args.args[1], "Update verfügbar")
        self.assertIn("MD-Importer 9.3 ist da", ask.call_args.args[2])
        flow.tray_icon.set_update.assert_called_once_with("9.3")
        self.assertEqual(flow.saved[-1][update_flow.UPDATE_NOTIFIED_KEY], "9.3")   # in der Config gemerkt
        # nächster Start (Config geladen): dieselbe Version → keine Frage mehr, Menüpunkt trotzdem
        again = self.make_flow(notified="9.3")
        ask = self.found(again, make_release("9.3"))
        ask.assert_not_called()
        again.tray_icon.set_update.assert_called_once_with("9.3")
        self.assertEqual(again.saved, [])
        # noch neuere Version → wieder eine Frage
        newer = self.make_flow(notified="9.3")
        ask = self.found(newer, make_release("9.10"))
        ask.assert_called_once()
        self.assertEqual(newer.saved[-1][update_flow.UPDATE_NOTIFIED_KEY], "9.10")

    def test_works_without_tray(self):
        ask = self.found(self.make_flow(tray_icon=False), make_release())
        ask.assert_called_once()

    def test_note_release_keeps_a_known_release_and_tells_the_tray(self):
        flow = self.make_flow()
        flow.note_release(None)
        self.assertIsNone(flow.release)
        flow.note_release(make_release("9.3"))
        flow.note_release(make_release("9.4"))   # schon einer bekannt: bleibt
        self.assertEqual(flow.release.version, "9.3")
        flow.tray_icon.set_update.assert_called_once_with("9.3")
        self.make_flow(tray_icon=False).note_release(make_release())   # ohne Tray kein Absturz

    def test_one_click_choices_and_page_choice(self):
        self.one_click_env()
        flow = self.make_flow()
        release = make_release()
        with mock.patch.object(update_flow.webbrowser, "open") as opened:    # „Was ist neu?“ → Seite des Releases
            ask = self.found(flow, release, answer="page")
        opened.assert_called_once_with(release.page)
        labels = [label for label, _, _ in ask.call_args.args[3]]
        self.assertEqual(labels, ["Jetzt aktualisieren", "Was ist neu?", "Später"])
        self.assertIn("deine Daten bleiben", ask.call_args.args[2])

    def test_later_does_nothing(self):
        self.one_click_env()
        flow = self.make_flow()
        with mock.patch.object(update_flow.threading, "Thread") as thread:
            self.found(flow, make_release(), answer=None)
        thread.assert_not_called()
        self.assertFalse(flow.running)

    def test_no_one_click_from_source_unwritable_folder_or_without_zip(self):
        cases = {"Quellcode": (False, update_flow.APP_DIR, ZIP_URL),
                 "Ordner nicht beschreibbar": (True, update_flow.APP_DIR + "_appdata", ZIP_URL),
                 "ohne zip": (True, update_flow.APP_DIR, "")}
        for name, (frozen, data_dir, zip_url) in cases.items():
            with self.subTest(name), mock.patch.object(update_flow.sys, "frozen", frozen, create=True), \
                    mock.patch.object(update_flow, "DATA_DIR", data_dir):
                ask = self.found(self.make_flow(), make_release(zip_url=zip_url))
                labels = [label for label, _, _ in ask.call_args.args[3]]
                self.assertEqual(labels, ["Release-Seite öffnen", "Später"])
                self.assertIn("Release-Seite herunterladen", ask.call_args.args[2])

    def test_question_waits_while_an_import_is_running(self):
        flow = self.make_flow(running=True)
        with mock.patch.object(update_flow, "ask_choice", return_value=None) as ask:
            flow.on_found(make_release())
            ask.assert_not_called()
            (wait, retry), = flow.later
            self.assertEqual(wait, update_flow.UPDATE_RETRY_MS)
            flow.later.clear()
            retry()                                        # Import läuft noch → wieder verschoben
            ask.assert_not_called()
            self.assertEqual(len(flow.later), 1)
            flow.later.clear()
            flow.env.busy = False
            flow.ask_when_idle()                           # Import fertig → jetzt wird gefragt
            ask.assert_called_once()
            self.assertEqual(flow.later, [])

    def test_install_choice_starts_the_worker(self):
        self.one_click_env()
        flow = self.make_flow()
        with mock.patch.object(update_flow.threading, "Thread") as thread:
            self.found(flow, make_release(), answer="install")
        thread.assert_called_once()
        self.assertEqual(thread.call_args.kwargs["args"], (make_release(),))
        thread.return_value.start.assert_called_once()
        self.assertTrue(flow.running)
        flow.set_buttons.assert_called_once_with(update_flow.tk.DISABLED)
        flow.show_overlay.assert_called_once_with()
        self.assertEqual(flow.shown[-1][0], "Update wird geladen … 0 %")

    def test_worker_prepares_launches_and_closes(self):
        flow = self.make_flow()
        flow.running = True
        release = make_release()

        def prepare(rel, temp_root, progress, app_dir=None):
            for done in (10, 50, 52, 100):                 # Statuszeile höchstens alle 5 %
                progress(done, 100)
            return r"C:\Temp\MD_Importer_update_9.4\neu\MD_ImporterV9.4"

        with mock.patch.object(update_flow.updater, "prepare", side_effect=prepare) as prep, \
                mock.patch.object(update_flow.updater, "write_script", return_value="skript.cmd") as write, \
                mock.patch.object(update_flow.updater, "launch") as launch, \
                mock.patch.object(update_flow, "dlog"), mock.patch.object(update_flow, "append"):
            flow._install_worker(release)
            self.assertEqual(prep.call_args.args[0], release)
            source, app_dir, pid, exe, folder = write.call_args.args
            self.assertEqual((app_dir, pid, exe), (update_flow.APP_DIR, os.getpid(), updater.EXE_NAME))
            self.assertEqual(os.path.basename(folder), "MD_Importer_update_9.4")
            launch.assert_called_once_with("skript.cmd")
            flow.close.assert_not_called()                 # beendet wird erst im UI-Thread
            self.run_queue(flow)
        self.assertEqual([text for text, _ in flow.shown],
                         ["Update wird geladen … 10 %", "Update wird geladen … 50 %", "Update wird entpackt …"])
        flow.close.assert_called_once()

    def test_failed_download_closes_nothing_and_reports(self):
        flow = self.make_flow()
        flow.running = True
        with mock.patch.object(update_flow.updater, "prepare",
                               side_effect=updater.UpdateError("Download unvollständig")), \
                mock.patch.object(update_flow.updater, "write_script") as write, \
                mock.patch.object(update_flow.updater, "launch") as launch, \
                mock.patch.object(update_flow, "append") as log, \
                mock.patch.object(update_flow, "show_message") as message:
            flow._install_worker(make_release())
            self.run_queue(flow)
        write.assert_not_called()
        launch.assert_not_called()
        flow.close.assert_not_called()
        self.assertFalse(flow.running)
        flow.set_buttons.assert_called_once_with(update_flow.tk.NORMAL)
        self.assertEqual(flow.shown[-1][1], update_flow.BAD)
        self.assertIn("Update fehlgeschlagen", flow.shown[-1][0])
        self.assertIn("Download unvollständig", message.call_args.args[2])
        self.assertIn("Download unvollständig", log.call_args.args[1])

    def test_failed_launch_closes_nothing(self):
        flow = self.make_flow()
        with mock.patch.object(update_flow.updater, "prepare", return_value="quelle"), \
                mock.patch.object(update_flow.updater, "write_script", side_effect=OSError("Platte voll")), \
                mock.patch.object(update_flow.updater, "launch") as launch, \
                mock.patch.object(update_flow, "append"), mock.patch.object(update_flow, "show_message") as message:
            flow._install_worker(make_release())
            self.run_queue(flow)
        launch.assert_not_called()
        flow.close.assert_not_called()
        self.assertIn("Platte voll", message.call_args.args[2])

    def test_source_run_does_not_check(self):
        flow = self.make_flow()
        with mock.patch.object(update_flow.sys, "frozen", False, create=True), \
                mock.patch.object(update_flow.threading, "Thread") as thread:
            flow.start_check()
        thread.assert_not_called()

    def test_exe_checks_once_in_background(self):
        flow = self.make_flow()
        with mock.patch.object(update_flow.sys, "frozen", True, create=True), \
                mock.patch.object(update_flow.threading, "Thread") as thread:
            flow.start_check()
        thread.assert_called_once()
        self.assertTrue(thread.call_args.kwargs["daemon"])
        thread.return_value.start.assert_called_once()

    def test_worker_cleans_up_and_reports_only_a_newer_release(self):
        flow = self.make_flow()
        release = make_release()
        with mock.patch.object(update_flow.updater, "cleanup_old") as cleanup, \
                mock.patch.object(update_flow.update_check, "find_update", return_value=release) as find:
            flow._worker()
        cleanup.assert_called_once()
        self.assertEqual(find.call_args.args[0], update_flow.APP_VERSION)
        self.assertEqual(flow.queued, [(flow.on_found, (release, False))])
        flow.queued.clear()
        with mock.patch.object(update_flow.updater, "cleanup_old"), \
                mock.patch.object(update_flow.update_check, "find_update", return_value=None):
            flow._worker()
            self.assertEqual(flow.queued, [])
            flow._worker(manual=True)   # Knopf: Bescheid, dass nichts da ist
        self.assertEqual(flow.queued, [(flow.status, (f"Kein Update gefunden (installiert: {update_flow.APP_VERSION})",
                                                      update_flow.GOOD))])
        flow.queued.clear()
        with mock.patch.object(update_flow.updater, "cleanup_old"), \
                mock.patch.object(update_flow.update_check, "find_update", side_effect=OSError("offline")) as find:
            flow._worker(manual=True)   # offline: nicht „kein Update“ melden
        self.assertTrue(find.call_args.kwargs["raise_errors"])
        self.assertEqual(flow.queued, [(flow.status, ("Update-Prüfung nicht möglich (keine Verbindung?)",
                                                      update_flow.BAD))])

    def test_no_question_at_start_while_master_duel_runs_but_the_button_asks(self):
        self.md_window.return_value = 1234
        flow = self.make_flow()
        ask = self.found(flow, make_release("9.3"))
        ask.assert_not_called()                                   # nicht mitten im Spielen
        self.assertEqual(flow.saved, [])                          # nicht gemerkt: beim nächsten Start wird gefragt
        flow.tray_icon.set_update.assert_called_once_with("9.3")
        self.assertIn("Nach Update suchen", flow.shown[-1][0])
        with mock.patch.object(update_flow, "ask_choice", return_value=None) as ask:   # Optionen → Nach Update suchen
            self.make_flow(notified="9.3").on_found(make_release("9.3"), True)
        ask.assert_called_once()                                  # auch schon gefragt und mit Spiel: sofort

    def test_option_after_duel_asks_once_no_duel_runs(self):
        self.md_window.return_value = 1234                        # Master Duel läuft
        flow = self.make_flow(config={update_flow.UPDATE_AFTER_DUEL_KEY: True, update_flow.READ_METHOD_KEY: "memory"})
        flow.env.front, flow.env.in_duel = True, True             # im Menü, Spiel vorne: zählt mit der Option nicht
        with mock.patch.object(update_flow, "ask_choice", return_value=None) as ask:
            flow.on_found(make_release("9.3"))
            ask.assert_not_called()                               # im Duell: verschoben
            self.assertEqual(len(flow.later), 1)
            flow.env.in_duel = False
            flow.later.pop(0)[1]()                                # Duell vorbei → jetzt fragen
            ask.assert_called_once()

    def test_option_without_memory_connection_falls_back_to_md_in_front(self):
        # Kurz nach dem Start (Speicher noch nicht verbunden) zeigt in_duel nichts – das Duell könnte laufen
        self.md_window.return_value = 1234
        flow = self.make_flow(config={update_flow.UPDATE_AFTER_DUEL_KEY: True, update_flow.READ_METHOD_KEY: "memory"})
        flow.env.front, flow.env.in_duel = True, None
        with mock.patch.object(update_flow, "ask_choice", return_value=None) as ask:
            flow.on_found(make_release("9.3"))
        ask.assert_not_called()
        self.assertEqual(len(flow.later), 1)                      # wartet, bis Master Duel nicht mehr vorne liegt

    def test_option_counts_only_in_memory_mode_but_the_button_always_works(self):
        on = {update_flow.UPDATE_AFTER_DUEL_KEY: True}
        self.assertTrue(update_flow.update_after_duel(dict(on, **{update_flow.READ_METHOD_KEY: "memory"})))
        self.assertFalse(update_flow.update_after_duel(dict(on, **{update_flow.READ_METHOD_KEY: "ocr"})))
        self.md_window.return_value = 1234
        flow = self.make_flow(config=dict(on, **{update_flow.READ_METHOD_KEY: "ocr"}))
        ask = self.found(flow, make_release("9.3"))
        ask.assert_not_called()                                    # Texterkennung: nur beim (nächsten) Start
        with mock.patch.object(update_flow, "ask_choice", return_value=None) as ask:   # Knopf: auch mit Texterkennung
            flow.on_found(make_release("9.3"), True)
        ask.assert_called_once()

    def test_check_after_duel_only_with_the_option_as_exe_and_not_too_often(self):
        def flow(option, checked_ago, found=None):
            result = self.make_flow(config={update_flow.UPDATE_AFTER_DUEL_KEY: option,
                                            update_flow.READ_METHOD_KEY: "memory"})
            result.release, result.checked_at = found, update_flow.time.monotonic() - checked_ago
            return result
        long_ago = update_flow.UPDATE_AFTER_DUEL_S + 1
        cases = [(flow(True, long_ago), True, 1), (flow(False, long_ago), True, 0), (flow(True, long_ago), False, 0),
                 (flow(True, 60), True, 0)]
        for fake, frozen, calls in cases:
            with mock.patch.object(update_flow.sys, "frozen", frozen, create=True), \
                    mock.patch.object(update_flow.threading, "Thread") as thread:
                fake.check_after_duel()
            self.assertEqual(thread.call_count, calls)
        postponed = flow(True, 60, make_release())       # beim Start zurückgestellt, Option erst danach an
        postponed.on_found = mock.Mock()
        with mock.patch.object(update_flow.sys, "frozen", True, create=True), \
                mock.patch.object(update_flow.threading, "Thread") as thread:
            postponed.check_after_duel()
        thread.assert_not_called()                          # nicht neu laden: die Frage nachholen
        postponed.on_found.assert_called_once_with(postponed.release)

    def test_button_checks_in_the_background(self):
        flow = self.make_flow()
        with mock.patch.object(update_flow.threading, "Thread") as thread:
            flow.check_now()
        self.assertEqual(thread.call_args.kwargs["args"], (True,))
        thread.return_value.start.assert_called_once()
        self.assertEqual(flow.shown[-1][0], "Suche nach Update …")

    def test_project_page_opens_the_releases(self):
        flow = self.make_flow()
        with mock.patch.object(update_flow.webbrowser, "open") as opened:
            flow.open_page()
        opened.assert_called_once_with("https://github.com/Raijn6969/Yugioh/releases/latest")
        with mock.patch.object(update_flow.webbrowser, "open", side_effect=OSError("kein Browser")):
            flow.open_page()   # kein Absturz

    def test_failed_marker_gives_one_message_with_a_release_button_and_is_removed(self):
        folder = tempfile.mkdtemp()
        marker = os.path.join(folder, updater.FAILED_MARKER)
        with open(marker, "w", encoding="utf-8") as f:
            f.write("robocopy exit code 8")
        flow = self.make_flow()
        flow.env.front = True
        with mock.patch.object(update_flow, "APP_DIR", folder), \
                mock.patch.object(update_flow, "show_message") as show:
            flow.show_failed_marker()
            show.assert_not_called()                               # Master Duel vorne: später wieder
            (wait, retry), = flow.later
            self.assertEqual(wait, 20)
            flow.env.front = False
            retry()
            flow.show_failed_marker()                              # zweiter Start: nichts mehr da
        show.assert_called_once()
        self.assertEqual(show.call_args.args[1], "Update unvollständig")
        self.assertEqual(show.call_args.kwargs["actions"][0][0], "Release-Seite öffnen")
        self.assertFalse(os.path.exists(marker))


class ToastQueueTest(unittest.TestCase):
    """Hinweise über der Taskleiste (Overlay): einer nach dem anderen, nach der Startmeldung."""

    def make_app(self, uptime_ms):
        later = []
        free = (Overlay.STARTED_TOAST_MS + Overlay.TOAST_GAP_MS) / 1000   # Startmeldung steht noch so lange
        return SimpleNamespace(_after_ids={}, _toast_count=0,
                               _toast_free_at=Overlay.time.monotonic() + free - uptime_ms / 1000,
                               root=SimpleNamespace(after=lambda ms, func: later.append((ms, func)) or "id"),
                               later=later)

    def test_toast_waits_for_the_start_message(self):
        app = self.make_app(uptime_ms=1000)
        Overlay.MasterDuelImporter._queue_toast(app, "x")
        (wait, _), = app.later
        # Startmeldung steht 5 s
        self.assertTrue(4000 <= wait <= Overlay.STARTED_TOAST_MS + Overlay.TOAST_GAP_MS, wait)
        late = self.make_app(uptime_ms=60000)
        Overlay.MasterDuelImporter._queue_toast(late, "x")
        self.assertEqual(late.later[0][0], 0)

    def test_several_toasts_come_one_after_the_other(self):
        app = self.make_app(uptime_ms=60000)
        for text in ("eins", "zwei", "drei"):
            Overlay.MasterDuelImporter._queue_toast(app, text)
        waits = [wait for wait, _ in app.later]
        step = Overlay.STARTED_TOAST_MS + Overlay.TOAST_GAP_MS
        self.assertEqual(waits[0], 0)
        self.assertTrue(all(abs((b - a) - step) <= 5 for a, b in zip(waits, waits[1:])), waits)   # nie übereinander


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
            self.menu, self.updated, self.native = menu, 0, []

        def _on_notify(self, wparam, lparam):  # wie das Win32-Backend: Klick auf das Symbol
            self.native.append(lparam)

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
        self.assertEqual(update.label(), "Update auf 9.10 installieren")
        self.assertEqual(icon._icon.updated, 1)           # Menü neu aufgebaut
        update.action(icon._icon, update)
        self.assertEqual(opened, [True])
        self.assertEqual([i.label() for i in items if i.shown()],
                         ["MD Importer 9.2", "Update auf 9.10 installieren", "Mit Windows starten", "Neustart",
                          "Beenden"])

    def test_right_click_calls_on_menu_instead_of_native_menu(self):
        calls = []
        icon, _items = self.make(on_menu=lambda x, y: calls.append((x, y)))
        with mock.patch.object(tray.win_api, "get_cursor_pos", return_value=(11, 22)):
            icon._icon._on_notify(0, tray.WM_RBUTTONUP)
        icon._icon._on_notify(0, 0x0202)  # Linksklick bleibt wie er ist
        self.assertEqual((calls, icon._icon.native), ([(11, 22)], [0x0202]))

    def test_right_click_stays_native_without_on_menu_or_win32_backend(self):
        icon, _items = self.make()
        icon._icon._on_notify(0, tray.WM_RBUTTONUP)
        self.assertEqual(icon._icon.native, [tray.WM_RBUTTONUP])
        class Other:  # anderes Backend: keine _on_notify
            pass
        with mock.patch.object(tray, "pystray", SimpleNamespace(Icon=Other)):
            self.assertIs(tray._icon_class(lambda x, y: None), Other)

    def test_without_pystray_nothing_happens(self):
        with mock.patch.object(tray, "pystray", None):
            icon = tray.TrayIcon("x", on_restart=lambda: None, on_quit=lambda: None)
            icon.set_update("9.3")   # kein Absturz


if __name__ == "__main__":
    unittest.main()
