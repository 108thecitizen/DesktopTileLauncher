# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all
pyside6_datas, pyside6_binaries, pyside6_hiddenimports = collect_all("PySide6")


a = Analysis(
    ['tile_launcher.py'],
    pathex=[],
    binaries=pyside6_binaries,   # <— was []
    datas=pyside6_datas,         # <— was []
    hiddenimports=pyside6_hiddenimports,  # <— was []
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='DesktopTileLauncher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,            # fine to leave True; it’s ignored if UPX isn’t installed
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='DesktopTileLauncher.ico',     # <— was ['DesktopTileLauncher.ico']
    version='version_info.txt',         # <— add this line
)
