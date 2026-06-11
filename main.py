import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

DATA_DIR = Path.home() / ".music-librarian"
DATA_DIR.mkdir(parents=True, exist_ok=True)

from src.utils.logger import setup_logger
setup_logger(DATA_DIR)

from src.database.db import Database
from src.ui.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Music Librarian")
    app.setOrganizationName("music-librarian")

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
