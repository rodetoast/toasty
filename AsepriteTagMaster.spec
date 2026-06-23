# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import wordninja

wordninja_words = Path(wordninja.__file__).with_name('wordninja') / 'wordninja_words.txt.gz'

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[(str(wordninja_words), 'wordninja')],
    hiddenimports=[],
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
    a.datas,
    [],
    name='AsepriteTagMaster',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
