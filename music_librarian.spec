import sys
from PyInstaller.building.api import PYZ, EXE, COLLECT
from PyInstaller.building.build_main import Analysis
from PyInstaller.building.osx import BUNDLE

APP_NAME = "Music Librarian"
IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # watchdog platform backends
        "watchdog.observers",
        "watchdog.observers.fsevents",
        "watchdog.observers.inotify",
        "watchdog.observers.winapi",
        "watchdog.observers.polling",
        "watchdog.events",
        # PySide6 extras that get missed by the hook
        "PySide6.QtSvg",
        "PySide6.QtDBus",
        # pyobjc (macOS only – ignored silently on other platforms)
        "AppKit",
        "Foundation",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # no terminal window
    disable_windowed_traceback=False,
    # icon="assets/icon.icns" if IS_MAC else "assets/icon.ico",  # uncomment when icon is ready
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=APP_NAME,
)

if IS_MAC:
    app = BUNDLE(
        coll,
        name=f"{APP_NAME}.app",
        # icon="assets/icon.icns",  # uncomment when icon is ready
        bundle_identifier="com.music-librarian.app",
        info_plist={
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1",
            "NSHighResolutionCapable": True,
            "LSUIElement": False,
            "NSHumanReadableCopyright": "",
        },
    )
