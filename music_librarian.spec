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
        "certifi",
        # watchdog platform backends
        "watchdog.observers",
        "watchdog.observers.fsevents",
        "watchdog.observers.inotify",
        "watchdog.observers.winapi",
        "watchdog.observers.polling",
        "watchdog.events",
        # pyobjc (macOS only – ignored silently on other platforms)
        "AppKit",
        "Foundation",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Qt modules not used by this app
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtQuickWidgets",
        "PySide6.QtQuickControls2",
        "PySide6.QtPdf",
        "PySide6.QtPdfWidgets",
        "PySide6.QtMultimedia",
        "PySide6.QtMultimediaWidgets",
        "PySide6.Qt3DCore",
        "PySide6.Qt3DRender",
        "PySide6.Qt3DInput",
        "PySide6.Qt3DLogic",
        "PySide6.Qt3DAnimation",
        "PySide6.Qt3DExtras",
        "PySide6.QtCharts",
        "PySide6.QtDataVisualization",
        "PySide6.QtVirtualKeyboard",
        "PySide6.QtWebEngine",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtWebChannel",
        "PySide6.QtBluetooth",
        "PySide6.QtNfc",
        "PySide6.QtLocation",
        "PySide6.QtPositioning",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtTest",
        # stdlib modules not needed at runtime
        "tkinter",
        "unittest",
        "xmlrpc",
        "pydoc",
        "doctest",
        "difflib",
        "ftplib",
        "imaplib",
        "poplib",
        "smtplib",
        "telnetlib",
        "turtle",
        "curses",
    ],
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
    icon="assets/icon.icns" if IS_MAC else "assets/icon.ico",
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
        icon="assets/icon.icns",
        bundle_identifier="com.music-librarian.app",
        info_plist={
            "CFBundleShortVersionString": "1.0.0",
            "CFBundleVersion": "1",
            "NSHighResolutionCapable": True,
            "LSUIElement": False,
            "NSHumanReadableCopyright": "",
            # Suppress TSM diagnostic noise from Qt text-field focus events.
            # Set at launch by macOS before the process starts — no code needed.
            "LSEnvironment": {"OS_ACTIVITY_MODE": "disable"},
        },
    )
