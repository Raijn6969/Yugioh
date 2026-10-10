# -*- mode: python ; coding: utf-8 -*-
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
