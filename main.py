import sys
from pathlib import Path

# Fix app name in the macOS menu bar when running from source (not a bundle).
# In a PyInstaller bundle the name comes from CFBundleName in Info.plist.
if sys.platform == "darwin":
    try:
        from AppKit import NSBundle  # pyobjc-framework-Cocoa
        NSBundle.mainBundle().infoDictionary()["CFBundleName"] = "Music Librarian"
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

DATA_DIR = Path.home() / ".music-librarian"
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

    icon_path = Path(__file__).parent / "assets" / "icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    from src.utils.logger import get_logger, QtLogHandler
    log = get_logger()
    qt_handler = QtLogHandler()
    log.addHandler(qt_handler)

    log.info("Music Librarian starting")

    db = Database(DATA_DIR / "music_library.db")
    window = MainWindow(db, qt_handler)
    window.show()
    code = app.exec()
    log.info("Music Librarian stopped")
    sys.exit(code)


if __name__ == "__main__":
    main()
