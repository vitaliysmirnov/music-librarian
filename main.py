import sys
from pathlib import Path

# Fix app name when running from source (not a bundle).
# In a PyInstaller bundle the name comes from CFBundleName in Info.plist.
if sys.platform == "darwin":
    try:
        from AppKit import NSBundle  # pyobjc-framework-Cocoa
        _info = NSBundle.mainBundle().infoDictionary()
        _info["CFBundleName"] = "Music Librarian"        # menu bar
        _info["CFBundleDisplayName"] = "Music Librarian" # Dock / Finder
    except Exception:
        pass
    try:
        import ctypes
        ctypes.cdll.LoadLibrary(None).setprogname(b"Music Librarian")  # ps / Activity Monitor
    except Exception:
        pass

from PySide6.QtCore import QEvent
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication


class _App(QApplication):
    """QApplication subclass that forwards macOS Dock re-open clicks."""

    def event(self, e: QEvent) -> bool:
        if e.type() == QEvent.Type.ApplicationActivate:
            # Emitted on macOS when the user clicks the Dock icon while no
            # window is visible (applicationShouldHandleReopen).
            for widget in self.topLevelWidgets():
                from src.ui.main_window import MainWindow
                if isinstance(widget, MainWindow):
                    widget._show_window()
                    break
        return super().event(e)

_in_bundle = getattr(sys, "frozen", False)
DATA_DIR = Path.home() / (".music-librarian" if _in_bundle else ".music-librarian-dev")
DATA_DIR.mkdir(parents=True, exist_ok=True)

from src.utils.logger import setup_logger
setup_logger(DATA_DIR)

from src.database.db import Database
from src.ui.main_window import MainWindow


def main():
    app = _App(sys.argv)
    app.setApplicationName("Music Librarian")
    app.setApplicationDisplayName("Music Librarian")
    app.setOrganizationName("music-librarian")

    # In a macOS bundle the dock icon comes from .icns automatically;
    # calling setWindowIcon would override it with a raw PNG that macOS
    # renders at the wrong logical size.  When running from source
    # (PyCharm, terminal) sys.frozen is not set, so we still set the icon.
    _in_bundle = sys.platform == "darwin" and getattr(sys, "frozen", False)
    if not _in_bundle:
        icon_path = Path(__file__).parent / "assets" / "icon.png"
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))

    from src.utils.logger import get_logger, QtLogHandler
    log = get_logger()
    qt_handler = QtLogHandler()
    log.addHandler(qt_handler)

    log.info("Music Librarian starting")

    db = Database(DATA_DIR / "music_library.db")

    from src.ui.theme import apply_theme
    apply_theme(db.get_setting("theme", "system"))

    window = MainWindow(db, qt_handler, data_dir=DATA_DIR)
    window.show()
    code = app.exec()
    log.info("Music Librarian stopped")
    sys.exit(code)


if __name__ == "__main__":
    main()
