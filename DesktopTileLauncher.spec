# -*- mode: python ; coding: utf-8 -*-
block_cipher = None

a = Analysis(
    ['tile_launcher.py'],
    pathex=['.'],
    binaries=[],
    datas=[('LICENSE','.'), ('LICENSE-MIT','.'), ('NOTICE','.')],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,          # onedir pattern (DLLs next to the EXE)
    name='DesktopTileLauncher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    console=False,                   # GUI app: hide console
    disable_windowed_traceback=False,
    icon='DesktopTileLauncher.ico',
    version='version_info.txt',
    upx=False,                       # disable UPX here
    uac_admin=False
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,                      # disable UPX here too
    name='DesktopTileLauncher'
)
