# -*- mode: python ; coding: utf-8 -*-
block_cipher = None

a = Analysis(
    ['tile_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[],
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
    exclude_binaries=True,          # onedir pattern (puts DLLs next to the EXE)
    name='DesktopTileLauncher',
    console=True,                   # set False to hide console window
    icon='DesktopTileLauncher.ico',
    version='version_info.txt',
    upx=False                       # disable UPX here
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
