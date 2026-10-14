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
import zlib
from typing import Callable, Optional

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


def _long_path(folder: str) -> str:
    """Windows: Langpfad-Präfix, damit auch Pfade über 260 Zeichen gehen (zip-Einträge sind bis ~105 Zeichen lang,
    der Temp-Ordner kann bei langen Benutzernamen schon lang sein). shortcut: UNC-Pfade (Netzlaufwerk als Temp)
    bekommen es nicht richtig – der Temp-Ordner liegt lokal; sonst das UNC-Präfix ergänzen."""
    prefix = r"\\?" + "\\"
    if os.name != "nt" or folder.startswith(prefix):
        return folder
    return prefix + os.path.abspath(folder)


def _crc32(path: str) -> int:
    crc = 0
    with open(path, "rb") as f:
        while block := f.read(1024 * 1024):
            crc = zlib.crc32(block, crc)
    return crc


def extract(zip_path: str, folder: str, app_dir: Optional[str] = None) -> str:
    """Zip prüfen (Zip-Slip) und entpacken. Mit `app_dir` bleiben Dateien weg, die dort schon gleich sind (Größe und
    CRC32). Zurück: der Ordner (ohne Langpfad-Präfix) mit EXE_NAME – er existiert auch, wenn nichts entpackt wurde."""
    target = _long_path(folder)
    try:
        with zipfile.ZipFile(zip_path) as archive:
            infos = archive.infolist()
            paths = [info.filename.replace("\\", "/") for info in infos]
            for name, path in zip((info.filename for info in infos), paths):
                if path.startswith("/") or ":" in path or ".." in path.split("/"):
                    raise UpdateError(f"Unzulässiger Pfad in der zip: {name}")
            if EXE_NAME in paths:
                prefix = ""
            else:
                tops = [path[:-len(EXE_NAME)] for path in paths
                        if path.count("/") == 1 and path.endswith("/" + EXE_NAME)]
                if len(tops) != 1:
                    raise UpdateError(f"In der zip fehlt {EXE_NAME}.")
                prefix = tops[0]
            for info, path in zip(infos, paths):
                if path.endswith("/"):
                    continue
                if app_dir and path.startswith(prefix):  # unveränderte Dateien nicht neu schreiben (kein Defender-Scan)
                    installed = os.path.join(_long_path(app_dir), *path[len(prefix):].split("/"))
                    try:
                        if os.path.getsize(installed) == info.file_size and _crc32(installed) == info.CRC:
                            continue
                    except OSError:
                        pass
                archive.extract(info, target)  # prüft die CRC der Datei (falsch → BadZipFile)
    except zipfile.BadZipFile:
        raise UpdateError("Die heruntergeladene zip ist beschädigt.")
    source = os.path.join(folder, prefix.rstrip("/")) if prefix else folder
    os.makedirs(_long_path(source), exist_ok=True)
    return source


def write_script(source: str, app_dir: str, pid: int, exe_name: str, folder: str) -> str:
    """update.cmd in `folder` schreiben (UTF-8 ohne BOM, chcp 65001 wegen Umlauten in Pfaden). Zurück: Pfad."""
    def q(path):  # normpath: kein Schluss-\ vor dem "; % verdoppeln (sonst liest cmd es als Variable)
        return os.path.normpath(path).replace("%", "%%")

    log = q(os.path.join(folder, "update.log"))
    lines = [
        "@echo off",
        "chcp 65001 >nul",
        # Warten bis der alte Prozess weg ist (höchstens ~2 min). Kein "&& (… goto)" hinter der Pipe (läuft in eigener
        # cmd, das goto ginge ins Leere) und kein timeout (bricht ohne Konsole sofort ab) → ping als Pause.
        "set /a n=0",
        ":wait",
        # find mit vollem Pfad: liegt Git (usr\bin) im PATH, wäre "find" sonst das GNU-find
        f'tasklist /FI "PID eq {pid}" 2>nul | "%SystemRoot%\\System32\\find.exe" "{pid}" >nul',
        "if errorlevel 1 goto copy",
        "set /a n+=1",
        "if %n% geq 120 goto copy",
        "ping -n 2 127.0.0.1 >nul",
        "goto wait",
        ":copy",
        f'robocopy "{q(source)}" "{q(app_dir)}" /E /R:20 /W:1 /NFL /NDL /NJH /NJS /NP >> "{log}"',
        f'if errorlevel 8 echo FEHLER beim Kopieren (robocopy %errorlevel%) >> "{log}"',
        # /D: die neue exe läuft im Programmordner (sonst erbt sie den Temp-Ordner als Arbeitsordner und sperrt ihn)
        f'start "" /D "{q(app_dir)}" "{q(os.path.join(app_dir, exe_name))}"',
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
            opener: Callable = urllib.request.urlopen, app_dir: Optional[str] = None) -> str:
    """Laden + Entpacken in `update_dir`. Mit `app_dir`: unveränderte Dateien nicht entpacken (siehe extract).
    Zurück: Ordner mit den neuen Dateien (Quelle für das Skript)."""
    if not release.zip_url.startswith(DOWNLOAD_PREFIXES) or release.zip_size <= 0:
        raise UpdateError("Der Release hat keine zip zum Herunterladen.")
    folder = update_dir(temp_root, release.version)
    shutil.rmtree(folder, ignore_errors=True)
    os.makedirs(folder)
    zip_path = os.path.join(folder, "update.zip")
    download(release.zip_url, release.zip_size, zip_path, progress, opener)
    return extract(zip_path, os.path.join(folder, "neu"), app_dir)
