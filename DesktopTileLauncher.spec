# -*- mode: python ; coding: utf-8 -*-

# No manual "collect_all" — rely on PyInstaller's PySide6 hook for the minimal set.
a = Analysis(
    ['tile_launcher.py'],
    pathex=[],
    binaries=[],         # let the hook add what’s really needed
    datas=[],            # "
    hiddenimports=[],    # "
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='DesktopTileLauncher',
    icon='DesktopTileLauncher.ico',
    version='version_info.txt', 
    console=False,                 # no console window
    version='version_info.txt',    # stamped File Properties
    upx=True,                      # harmless if UPX isn't installed
    upx_exclude=[],
    debug=False,
    strip=False,
    bootloader_ignore_signals=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
