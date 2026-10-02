# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['overlay.py'],
    pathex=[],
    binaries=[],
    datas=[('Tesseract-OCR', 'Tesseract-OCR')],
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
