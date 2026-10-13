"""
Update mit einem Klick (kein Tk): neue Version aus dem GitHub-Release laden, entpacken und per kleinem Hilfsskript
(.cmd) über den Programmordner kopieren.

Die laufende exe kann ihre eigenen Dateien nicht überschreiben. Deshalb lädt und entpackt der Importer in einen
Temp-Ordner, startet das Skript und beendet sich. Das Skript wartet auf das Ende des alten Prozesses, kopiert die
neuen Dateien (robocopy /E – eigene Daten wie Config, Datenbank, Backups liegen nicht in der zip und bleiben
unberührt) und startet die neue exe. Geschrieben wird nur im eigenen Temp-Ordner und im Programmordner.
"""

import os
import shutil
import subprocess
import time
import urllib.request
import zipfile
from typing import Callable

from update_check import DOWNLOAD_PREFIXES, Release

EXE_NAME = "MD_Importer.exe"
CHUNK = 256 * 1024
FOLDER_PREFIX = "MD_Importer_update_"
OLD_AFTER = 24 * 3600  # Update-Ordner, die älter sind, räumt der nächste Start weg


class UpdateError(Exception):
    pass


def download(url: str, size: int, target: str, progress: Callable[[int, int], None] = lambda done, total: None,
             opener: Callable = urllib.request.urlopen, timeout: float = 30) -> None:
    """In Blöcken nach `target` laden (erst als *.part). Die Größe muss genau `size` sein, sonst UpdateError."""
    part = target + ".part"
    request = urllib.request.Request(url, headers={"User-Agent": "MD-Importer-Update"})
    done = 0
    try:
        with opener(request, timeout=timeout) as response, open(part, "wb") as out:
            while True:
                block = response.read(CHUNK)
                if not block:
                    break
                done += len(block)
                if done > size:
                    raise UpdateError(f"Download größer als angekündigt ({size} Bytes).")
                out.write(block)
                progress(done, size)
        if done != size:
            raise UpdateError(f"Download unvollständig ({done} von {size} Bytes).")
        os.replace(part, target)
    except BaseException:
        try:
            os.remove(part)
        except OSError:
            pass
        raise


def extract(zip_path: str, folder: str) -> str:
    """Zip prüfen (Zip-Slip!) und entpacken. Zurück: der Ordner, in dem EXE_NAME liegt."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            if archive.testzip() is not None:
                raise UpdateError("Die heruntergeladene zip ist beschädigt.")
            for name in archive.namelist():
                path = name.replace("\\", "/")
                if path.startswith("/") or ":" in path or ".." in path.split("/"):
                    raise UpdateError(f"Unzulässiger Pfad in der zip: {name}")
            archive.extractall(folder)
    except zipfile.BadZipFile as e:
        raise UpdateError(f"Keine gültige zip: {e}")
    if os.path.isfile(os.path.join(folder, EXE_NAME)):
        return folder
    subs = [os.path.join(folder, d) for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d))]
    if len(subs) == 1 and os.path.isfile(os.path.join(subs[0], EXE_NAME)):
        return subs[0]
    raise UpdateError(f"In der zip fehlt {EXE_NAME}.")


def write_script(source: str, app_dir: str, pid: int, exe_name: str, folder: str) -> str:
    """update.cmd in `folder` schreiben (UTF-8 ohne BOM, chcp 65001 wegen Umlauten in Pfaden). Zurück: Pfad."""
    def q(path):  # normpath: kein Schluss-\ vor dem "; % verdoppeln (sonst liest cmd es als Variable)
        return os.path.normpath(path).replace("%", "%%")

    log = q(os.path.join(folder, "update.log"))
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        ":wait",
        f'tasklist /FI "PID eq {pid}" 2>nul | find "{pid}" >nul && (timeout /t 1 /nobreak >nul & goto wait)',
        f'robocopy "{q(source)}" "{q(app_dir)}" /E /R:20 /W:1 /NFL /NDL /NJH /NJS /NP >> "{log}"',
        f'if errorlevel 8 echo FEHLER beim Kopieren (robocopy %errorlevel%) >> "{log}"',
        f'start "" "{q(os.path.join(app_dir, exe_name))}"',
    ]
    script = os.path.join(folder, "update.cmd")
    with open(script, "w", encoding="utf-8", newline="") as f:
        f.write("\r\n".join(lines) + "\r\n")
    return script


def launch(script: str, popen: Callable = subprocess.Popen) -> None:
    """Skript unsichtbar und vom Importer unabhängig starten (es überlebt dessen Ende)."""
    popen(["cmd", "/c", script], creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
          close_fds=True, cwd=os.path.dirname(script))


def cleanup_old(temp_root: str) -> None:
    """Alte Update-Ordner (MD_Importer_update_*) im Temp-Ordner löschen; Fehler sind egal."""
    try:
        for name in os.listdir(temp_root):
            path = os.path.join(temp_root, name)
            if (name.startswith(FOLDER_PREFIX) and os.path.isdir(path)
                    and time.time() - os.path.getmtime(path) > OLD_AFTER):
                shutil.rmtree(path, ignore_errors=True)
    except OSError:
        pass


def update_dir(temp_root: str, version: str) -> str:
    return os.path.join(temp_root, FOLDER_PREFIX + version)


def prepare(release: Release, temp_root: str, progress: Callable[[int, int], None] = lambda done, total: None,
            opener: Callable = urllib.request.urlopen) -> str:
    """Laden + Entpacken in `update_dir`. Zurück: Ordner mit den neuen Dateien (Quelle für das Skript)."""
    if not release.zip_url.startswith(DOWNLOAD_PREFIXES) or release.zip_size <= 0:
        raise UpdateError("Der Release hat keine zip zum Herunterladen.")
    folder = update_dir(temp_root, release.version)
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    zip_path = os.path.join(folder, "update.zip")
    download(release.zip_url, release.zip_size, zip_path, progress, opener)
    return extract(zip_path, os.path.join(folder, "neu"))
