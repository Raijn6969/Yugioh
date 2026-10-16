# -*- mode: python ; coding: utf-8 -*-
import fnmatch
import sys

# Karten-Grunddatenbank für die Extras-Stats: fehlt sie oder ist sie älter als 14 Tage, wird sie
# jetzt neu von YGOPRODeck gebaut und in die exe gepackt (Stats gehen dann ab dem ersten Start offline)
sys.path.insert(0, SPECPATH)
from build_card_db import TARGET as CARD_DB, ensure_fresh
ensure_fresh()

# Ash-Prio für PCs ohne Factory: Schnappschuss der Factory-Ergebnisse (nur lesend, ~150 KB). Ohne Factory auf diesem
# PC bleibt ein vorhandener alter Schnappschuss; gibt es keinen, zeigt Ash-Prio die Einschätzung aus den Kartentexten.
import os
from ash_prio import build_snapshot, snapshot_file
print(f"[ash_prio] Schnappschuss: {build_snapshot()} Varianten")
EXTRA_DATAS = [(snapshot_file(), '.')] if os.path.exists(snapshot_file()) else []

a = Analysis(
    ['overlay.py'],
    pathex=[],
    binaries=[],
    datas=[('Tesseract-OCR', 'Tesseract-OCR'), (CARD_DB, '.')] + EXTRA_DATAS,
    hiddenimports=['pystray._win32'],  # pystray lädt sein Windows-Backend erst zur Laufzeit
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
# --- Paketgröße (befunde.md B92): Unnötiges aus dem Paket lassen ---
# PyInstaller 6 behandelt die DLLs/exes aus 'Tesseract-OCR' als Binärdateien und legt deren Abhängigkeiten
# zusätzlich in den Wurzelordner _internal. tesseract.exe findet seine DLLs im eigenen Ordner, die Kopien im Wurzel-
# ordner braucht nur, was unten in WURZEL_BEHALTEN steht (Laufzeit-DLLs; libffi für _ctypes, zlib1 für Tcl – deren
# eigene Fassungen hat PyInstaller wegen gleichem Namen durch die aus dem Tesseract-Ordner ersetzt).
# Ausgeschlossen sind außerdem: Tesseract-Trainingswerkzeuge/Handbuch/Java, osd/eng_backup (nur --psm 7, Englisch/
# Deutsch), AVIF von Pillow.
AUSSCHLUSS = ['tesseract-ocr/*.exe', 'tesseract-ocr/*.html', 'tesseract-ocr/doc/*', '*.jar',
              'tesseract-ocr/tessdata/osd.*', 'tesseract-ocr/tessdata/eng_backup.*',
              'pil/_avif*']
WURZEL_BEHALTEN = ('vcruntime140.dll', 'msvcp140.dll', 'libffi-8.dll', 'zlib1.dll')


def behalten(eintrag):
    ziel, quelle = eintrag[0].replace('\\', '/').lower(), eintrag[1]
    if ziel == 'tesseract-ocr/tesseract.exe':
        return True
    aus_tesseract = os.path.basename(os.path.dirname(quelle)) == 'Tesseract-OCR'
    if '/' not in ziel and aus_tesseract and ziel not in WURZEL_BEHALTEN:
        return False
    return not any(fnmatch.fnmatch(ziel, muster) for muster in AUSSCHLUSS)


a.binaries = [e for e in a.binaries if behalten(e)]
a.datas = [e for e in a.datas if behalten(e)]
# --- Ende Paketgröße ---

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MD_Importer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon='assets/app_icon.ico',
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='MD_Importer',
)
