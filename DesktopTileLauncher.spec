# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['tile_launcher.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
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
    console=False,
    upx=True,
    upx_exclude=[],
    debug=False,
    strip=False,
)
