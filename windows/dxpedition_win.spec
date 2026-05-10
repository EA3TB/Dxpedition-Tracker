# -*- mode: python ; coding: utf-8 -*-
import os, sys
from PyInstaller.utils.hooks import collect_all, collect_submodules
block_cipher = None

datas = []
hiddenimports = []

for pkg in ['fastapi', 'uvicorn', 'starlette', 'httpx', 'anyio', 'h11',
            'pydantic', 'pydantic_core', 'email_validator',
            'python_multipart', 'aiofiles', 'lxml']:
    d, b, h = collect_all(pkg)
    datas += d
    hiddenimports += h

hiddenimports += collect_submodules('uvicorn')
hiddenimports += collect_submodules('starlette')
hiddenimports += collect_submodules('serial')

hiddenimports += [
    'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto',
    'uvicorn.protocols', 'uvicorn.protocols.http',
    'uvicorn.protocols.http.auto',
    'uvicorn.protocols.websockets',
    'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan', 'uvicorn.lifespan.on',
    'asyncio', 'signal', 'threading', 'webbrowser',
    'xml.etree.ElementTree', 'glob', 'math', 're',
    'ctypes', 'string', 'winreg', 'subprocess',
    'comtypes', 'comtypes.client', 'sqlite3', 'csv',
    'serial', 'serial.tools', 'serial.tools.list_ports',
    'tkinter', 'tkinter.ttk', 'tkinter.messagebox',
]

datas += [
    ('frontend/index_win.html', 'frontend'),
    ('backend',             'backend'),   # incluye main_win.py
    ('rotor',               'rotor'),
    ('dxp_icon.ico',        '.'),
]

# ── Ejecutable principal: DXpeditionTracker.exe ───────────────────────────────
a = Analysis(
    ['run_win.py'],
    pathex=['.'],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'scipy', 'PIL', 'cv2'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='DXpeditionTracker',
    icon=os.path.join(SPECPATH, 'dxp_icon.ico'),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# ── Ejecutable del tray: rotor_tray_exe.exe ───────────────────────────────────
tray_hiddenimports = [
    'pystray', 'PIL', 'PIL.Image', 'PIL.ImageDraw',
    'serial', 'serial.tools', 'serial.tools.list_ports',
    'tkinter', 'tkinter.ttk',
    'rotor', 'rotor.gs232a', 'rotor.dummy',
    'threading', 'subprocess', 'json', 'logging',
]

tray_datas = [
    ('rotor',        'rotor'),
    ('dxp_icon.ico', '.'),
]

b = Analysis(
    ['rotor_tray_win.py'],
    pathex=['.'],
    binaries=[],
    datas=tray_datas,
    hiddenimports=tray_hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'scipy'],
    cipher=block_cipher,
    noarchive=False,
)

pyz_tray = PYZ(b.pure, b.zipped_data, cipher=block_cipher)

exe_tray = EXE(
    pyz_tray,
    b.scripts,
    b.binaries,
    b.zipfiles,
    b.datas,
    [],
    name='rotor_tray_exe',
    icon=os.path.join(SPECPATH, 'dxp_icon.ico'),
    debug=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    target_arch=None,
)
