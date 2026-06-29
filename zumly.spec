# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a_tray = Analysis(
    ['tray_app.py'],
    pathex=['zumly'],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

a_editor = Analysis(
    ['editor_app.py'],
    pathex=['zumly'],
    binaries=[],
    datas=[('zumly/app/icons/*.svg', 'zumly/app/icons')],
    hiddenimports=[
        'PySide6.QtSvg',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets'
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

a_export = Analysis(
    ['export_app.py'],
    pathex=['zumly'],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

MERGE(
    (a_tray, 'tray_app', 'tray_app'),
    (a_editor, 'editor_app', 'editor_app'),
    (a_export, 'export_app', 'export_app')
)

pyz_tray = PYZ(a_tray.pure, a_tray.zipped_data, cipher=block_cipher)
pyz_editor = PYZ(a_editor.pure, a_editor.zipped_data, cipher=block_cipher)
pyz_export = PYZ(a_export.pure, a_export.zipped_data, cipher=block_cipher)

exe_tray = EXE(
    pyz_tray,
    a_tray.scripts,
    [],
    exclude_binaries=True,
    name='tray_app',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

exe_editor = EXE(
    pyz_editor,
    a_editor.scripts,
    [],
    exclude_binaries=True,
    name='editor_app',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

exe_export = EXE(
    pyz_export,
    a_export.scripts,
    [],
    exclude_binaries=True,
    name='export_app',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe_tray,
    a_tray.binaries,
    a_tray.zipfiles,
    a_tray.datas,
    exe_editor,
    a_editor.binaries,
    a_editor.zipfiles,
    a_editor.datas,
    exe_export,
    a_export.binaries,
    a_export.zipfiles,
    a_export.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='zumly',
)
