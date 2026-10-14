"""Update mit einem Klick: Download, Entpacken (Zip-Slip), Hilfsskript, Start, Aufräumen – alles im Temp-Ordner,
ohne Netz und ohne echtes cmd/Popen."""

import io
import os
import shutil
import subprocess
import tempfile
import time
import unittest
import zipfile
from unittest import mock

import update_check
import updater

URL = "https://github.com/Raijn6969/Yugioh/releases/download/v9.4/MD_ImporterV9.4.zip"


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def opener_returning(data):
    requests = []

    def opener(request, timeout=None):
        requests.append((request, timeout))
        return FakeResponse(data)

    opener.requests = requests
    return opener


def make_zip(path, entries):
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


class TempTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(self._remove_tmp)

    def _remove_tmp(self):
        """Mit Wiederholung (bis ~10 s): cmd/wscript laufen nach dem Marker evtl. noch kurz. Bleibt der Ordner
        trotzdem stehen, scheitert der Test deswegen nicht."""
        for _ in range(20):
            shutil.rmtree(updater._long_path(self.tmp), ignore_errors=True)   # auch tief verschachteltes
            if not os.path.exists(self.tmp):
                return
            time.sleep(0.5)


class DownloadTest(TempTest):
    def test_download_reports_progress_and_renames_the_part_file(self):
        data = os.urandom(updater.CHUNK * 2 + 10)
        target = os.path.join(self.tmp, "u.zip")
        seen = []
        opener = opener_returning(data)
        updater.download(URL, len(data), target, lambda done, total: seen.append((done, total)), opener)
        with open(target, "rb") as f:
            self.assertEqual(f.read(), data)
        self.assertEqual([done for done, _ in seen], [updater.CHUNK, updater.CHUNK * 2, len(data)])
        self.assertTrue(all(total == len(data) for _, total in seen))
        self.assertFalse(os.path.exists(target + ".part"))
        (request, timeout), = opener.requests
        self.assertEqual((request.full_url, timeout), (URL, 30))

    def test_wrong_size_fails_and_leaves_no_file(self):
        target = os.path.join(self.tmp, "u.zip")
        with self.assertRaisesRegex(updater.UpdateError, "unvollständig"):
            updater.download(URL, 100, target, opener=opener_returning(b"x" * 99))
        self.assertEqual(os.listdir(self.tmp), [])

    def test_too_many_bytes_abort(self):
        target = os.path.join(self.tmp, "u.zip")
        with self.assertRaisesRegex(updater.UpdateError, "größer"):
            updater.download(URL, 100, target, opener=opener_returning(b"x" * 101))
        self.assertEqual(os.listdir(self.tmp), [])

    def test_network_error_leaves_no_file(self):
        def opener(request, timeout=None):
            raise OSError("Netz weg")
        with self.assertRaises(OSError):
            updater.download(URL, 100, os.path.join(self.tmp, "u.zip"), opener=opener)
        self.assertEqual(os.listdir(self.tmp), [])


class ExtractTest(TempTest):
    def test_exe_in_a_single_subfolder(self):
        zip_path = make_zip(os.path.join(self.tmp, "u.zip"), {
            "MD_ImporterV9.4/MD_Importer.exe": b"exe", "MD_ImporterV9.4/_internal/lib.dll": b"dll"})
        source = updater.extract(zip_path, os.path.join(self.tmp, "neu"))
        self.assertEqual(source, os.path.join(self.tmp, "neu", "MD_ImporterV9.4"))
        self.assertTrue(os.path.isfile(os.path.join(source, "_internal", "lib.dll")))

    def test_exe_on_the_top_level(self):
        zip_path = make_zip(os.path.join(self.tmp, "u.zip"), {"MD_Importer.exe": b"exe", "a/b.txt": b"b"})
        folder = os.path.join(self.tmp, "neu")
        self.assertEqual(updater.extract(zip_path, folder), folder)

    def test_paths_over_260_characters_are_extracted(self):
        deep = "/".join(["d" * 40] * 6) + "/COPYING"                  # 240 Zeichen + Temp-Ordner > 260
        zip_path = make_zip(os.path.join(self.tmp, "u.zip"), {"MD/MD_Importer.exe": b"exe", "MD/" + deep: b"lang"})
        folder = os.path.join(self.tmp, "neu")
        source = updater.extract(zip_path, folder)
        self.assertEqual(source, os.path.join(folder, "MD"))
        self.assertFalse(source.startswith("\\\\?"))                    # zurückgegeben wird der Pfad ohne Präfix
        full = os.path.join(updater._long_path(source), deep.replace("/", os.sep))
        self.assertGreater(len(full), 260)
        with open(full, "rb") as f:
            self.assertEqual(f.read(), b"lang")

    def test_zip_slip_is_refused_and_nothing_is_extracted(self):
        for evil in ("../evil.txt", "MD/../../evil.txt", "/evil.txt", "C:/evil.txt", "..\\evil.txt"):
            zip_path = make_zip(os.path.join(self.tmp, "u.zip"), {"MD/MD_Importer.exe": b"exe", evil: b"x"})
            folder = os.path.join(self.tmp, "sub", "neu")
            with self.assertRaisesRegex(updater.UpdateError, "Unzulässig", msg=evil):
                updater.extract(zip_path, folder)
            self.assertFalse(os.path.exists(folder), evil)
            self.assertFalse(os.path.exists(os.path.join(self.tmp, "evil.txt")), evil)
            self.assertFalse(os.path.exists(os.path.join(self.tmp, "sub", "evil.txt")), evil)

    def test_missing_exe_is_an_error(self):
        zip_path = make_zip(os.path.join(self.tmp, "u.zip"), {"MD/readme.txt": b"x"})
        with self.assertRaisesRegex(updater.UpdateError, "fehlt"):
            updater.extract(zip_path, os.path.join(self.tmp, "neu"))

    def test_broken_zip_is_an_error(self):
        path = os.path.join(self.tmp, "u.zip")
        with open(path, "wb") as f:
            f.write(b"das ist keine zip")
        with self.assertRaises(updater.UpdateError):
            updater.extract(path, os.path.join(self.tmp, "neu"))


    def test_crc_error_is_an_error(self):
        path = os.path.join(self.tmp, "u.zip")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
            archive.writestr("MD/MD_Importer.exe", b"exe-inhalt")
        with open(path, "rb") as f:
            data = bytearray(f.read())
        data[data.index(b"exe-inhalt")] ^= 1                      # gespeichert, also liegt der Inhalt unverändert vor
        with open(path, "wb") as f:
            f.write(data)
        with self.assertRaisesRegex(updater.UpdateError, "beschädigt"):
            updater.extract(path, os.path.join(self.tmp, "neu"))


class DropUnchangedTest(TempTest):
    def write(self, folder, name, data):
        path = os.path.join(self.tmp, folder, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_only_identical_files_are_dropped(self):
        same = self.write("neu", "gleich.dll", b"abc")
        self.write("app", "gleich.dll", b"abc")
        sub_same = self.write("neu", "_internal/lib.dll", b"lib")
        self.write("app", "_internal/lib.dll", b"lib")
        other = self.write("neu", "anders.txt", b"abc")
        self.write("app", "anders.txt", b"abd")                 # gleiche Größe, anderer Inhalt
        bigger = self.write("neu", "groesser.txt", b"abcd")
        self.write("app", "groesser.txt", b"abc")
        new = self.write("neu", "neu.txt", b"x")                # fehlt im Programmordner
        count = updater.drop_unchanged(os.path.join(self.tmp, "neu"), os.path.join(self.tmp, "app"))
        self.assertEqual(count, 2)
        self.assertFalse(os.path.exists(same) or os.path.exists(sub_same))
        self.assertTrue(all(os.path.exists(path) for path in (other, bigger, new)))
        with open(os.path.join(self.tmp, "app", "gleich.dll"), "rb") as f:
            self.assertEqual(f.read(), b"abc")                  # die installierte Datei bleibt unberührt


class ScriptTest(TempTest):
    def test_script_content(self):
        app_dir = os.path.join(self.tmp, "Programm Ordner ü")
        source = os.path.join(self.tmp, "neu", "MD_ImporterV9.4")
        script = updater.write_script(source, app_dir + "\\", 4711, updater.EXE_NAME, self.tmp)
        self.assertEqual(script, os.path.join(self.tmp, "update.cmd"))
        with open(script, "rb") as f:
            raw = f.read()
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))          # UTF-8 ohne BOM
        text = raw.decode("utf-8")
        lines = text.split("\r\n")
        self.assertEqual(lines[:2], ["@echo off", "chcp 65001 >nul"])
        self.assertIn('tasklist /FI "PID eq 4711"', text)
        self.assertIn('| "%SystemRoot%\\System32\\find.exe" "4711"', text)    # nicht das GNU-find aus Git
        # Warteschleife: Zähler, kein "&&" hinter der Pipe, kein timeout, %n% bleibt unverdoppelt
        crlf = "\r\n".join
        self.assertIn(crlf(["set /a n=0", ":wait", "tasklist"]), text)
        self.assertIn(crlf(["if errorlevel 1 goto copy", "set /a n+=1", "if %n% geq 120 goto copy",
                            "ping -n 2 127.0.0.1 >nul", "goto wait", ":copy", "robocopy"]), text)
        self.assertNotIn("timeout", text.replace("kein timeout", ""))
        robocopy, = [line for line in lines if line.startswith("robocopy")]
        self.assertIn(f'robocopy "{source}" "{app_dir}" /E', robocopy)      # ohne Schluss-\ vor dem "
        self.assertIn(f'>> "{os.path.join(self.tmp, "update.log")}"', robocopy)
        self.assertIn("if errorlevel 8", text)
        self.assertEqual(lines[-2], f'start "" /D "{app_dir}" "{os.path.join(app_dir, "MD_Importer.exe")}"')

    def test_percent_signs_in_paths_are_doubled(self):
        script = updater.write_script(os.path.join(self.tmp, "a%PATH%"), os.path.join(self.tmp, "b"), 1, "x.exe",
                                      self.tmp)
        with open(script, encoding="utf-8") as f:
            self.assertIn("a%%PATH%%", f.read())

    def test_launch_uses_cmd_without_window_and_detached(self):
        popen = mock.Mock()
        script = os.path.join(self.tmp, "update.cmd")
        updater.launch(script, popen=popen)
        popen.assert_called_once_with(
            ["cmd", "/c", script],
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True, cwd=self.tmp)


@unittest.skipUnless(os.name == "nt", "update.cmd gibt es nur unter Windows")
class RealScriptRunTest(TempTest):
    """Echter Durchlauf von update.cmd im Temp-Ordner: alter Prozess = ping, "exe" = marker.vbs (wscript, ohne
    Fenster). Nichts außerhalb des Temp-Ordners, kein Netz, keine Eingaben."""

    VBS = "\r\n".join([
        'Set fso = CreateObject("Scripting.FileSystemObject")',
        # Unicode-Datei (True, True): der Arbeitsordner enthält ein "ü"
        r'Set f = fso.CreateTextFile(fso.GetParentFolderName(WScript.ScriptFullName) & "\marker.txt", True, True)',
        'f.Write CreateObject("WScript.Shell").CurrentDirectory', "f.Close", ""])

    def test_script_waits_for_the_old_process_then_copies_and_starts(self):
        root = os.path.join(self.tmp, "Upd ü %NOVAR_X% Test")
        app_dir, source, folder = (os.path.join(root, name) for name in ("Programm", "neu", "skript"))
        for path in (app_dir, source, folder):
            os.makedirs(path)
        config = os.path.join(app_dir, "md_config.json")
        with open(config, "w", encoding="utf-8") as f:
            f.write('{"eigene": "Daten"}')
        with open(os.path.join(source, "neu.txt"), "w", encoding="utf-8") as f:
            f.write("neue Datei")
        with open(os.path.join(source, "marker.vbs"), "w", encoding="utf-16") as f:   # wscript braucht BOM
            f.write(self.VBS)
        waiter = subprocess.Popen(["ping", "-n", "4", "127.0.0.1"], stdout=subprocess.DEVNULL)   # "alter Importer"
        self.addCleanup(waiter.kill)
        self.addCleanup(waiter.wait)
        script = updater.write_script(source, app_dir, waiter.pid, "marker.vbs", folder)
        marker = os.path.join(app_dir, "marker.txt")
        t0 = time.monotonic()
        updater.launch(script)
        old_ended = None
        while not (os.path.exists(marker) and os.path.getsize(marker) > 2):    # erst da, wenn auch beschrieben
            if old_ended is None and waiter.poll() is not None:
                old_ended = time.monotonic() - t0
            self.assertLess(time.monotonic() - t0, 30, "Skript hat die neue 'exe' nicht gestartet")
            time.sleep(0.05)
        waited = time.monotonic() - t0
        self.assertIsNotNone(waiter.poll(), "Marker erschien, bevor der alte Prozess beendet war")
        print(f"[Test] update.cmd: Marker nach {waited:.1f} s (alter Prozess ~3 s)")
        self.assertGreater(waited, 2.0)
        with open(marker, encoding="utf-16") as f:        # Arbeitsordner der "exe" = Programmordner, nicht Temp
            self.assertEqual(os.path.normcase(f.read().strip()), os.path.normcase(app_dir))
        with open(os.path.join(app_dir, "neu.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "neue Datei")
        with open(config, encoding="utf-8") as f:
            self.assertEqual(f.read(), '{"eigene": "Daten"}')


class CleanupTest(TempTest):
    def test_only_old_update_folders_are_removed(self):
        old = os.path.join(self.tmp, "MD_Importer_update_9.3")
        fresh = os.path.join(self.tmp, "MD_Importer_update_9.4")
        other = os.path.join(self.tmp, "anderer_Ordner")
        for folder in (old, fresh, other):
            os.makedirs(folder)
        long_ago = time.time() - 2 * 24 * 3600
        for folder in (old, other):
            os.utime(folder, (long_ago, long_ago))
        updater.cleanup_old(self.tmp)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))
        self.assertTrue(os.path.exists(other))        # fremde Ordner bleiben tabu

    def test_missing_temp_folder_is_no_error(self):
        updater.cleanup_old(os.path.join(self.tmp, "gibt-es-nicht"))


class PrepareTest(TempTest):
    def release(self, data, url=URL):
        return update_check.Release("9.4", "https://github.com/x", url, len(data))

    def test_prepare_downloads_and_extracts_into_a_clean_folder(self):
        source_zip = make_zip(os.path.join(self.tmp, "src.zip"), {"MD_ImporterV9.4/MD_Importer.exe": b"exe"})
        with open(source_zip, "rb") as f:
            data = f.read()
        root = os.path.join(self.tmp, "temp")
        stale = os.path.join(updater.update_dir(root, "9.4"), "alt.txt")
        os.makedirs(os.path.dirname(stale))
        with open(stale, "w") as f:
            f.write("Rest eines früheren Versuchs")
        progress = mock.Mock()
        source = updater.prepare(self.release(data), root, progress, opener_returning(data))
        self.assertEqual(source, os.path.join(updater.update_dir(root, "9.4"), "neu", "MD_ImporterV9.4"))
        self.assertTrue(os.path.isfile(os.path.join(source, "MD_Importer.exe")))
        self.assertFalse(os.path.exists(stale))
        progress.assert_called_with(len(data), len(data))

    def test_prepare_with_app_dir_drops_unchanged_files(self):
        source_zip = make_zip(os.path.join(self.tmp, "src.zip"),
                              {"MD/MD_Importer.exe": b"neu", "MD/lib.dll": b"lib"})
        with open(source_zip, "rb") as f:
            data = f.read()
        app_dir = os.path.join(self.tmp, "app")
        os.makedirs(app_dir)
        with open(os.path.join(app_dir, "lib.dll"), "wb") as f:
            f.write(b"lib")
        source = updater.prepare(self.release(data), os.path.join(self.tmp, "temp"), opener=opener_returning(data),
                                 app_dir=app_dir)
        self.assertEqual(os.listdir(source), ["MD_Importer.exe"])

    def test_prepare_refuses_foreign_hosts_and_missing_zip(self):
        for url in ("", "https://evil.example/a.zip"):
            with self.assertRaises(updater.UpdateError):
                updater.prepare(self.release(b"x", url), self.tmp, opener=mock.Mock(side_effect=AssertionError))


if __name__ == "__main__":
    unittest.main()
