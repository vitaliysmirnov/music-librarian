import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QObject, Signal


def setup_logger(data_dir: Path) -> logging.Logger:
    logger = logging.getLogger("music_librarian")
    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Rotate daily, keep 10 days of backups
    fh = TimedRotatingFileHandler(
        data_dir / "music_librarian.log",
        when="midnight",
        interval=1,
        backupCount=10,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(formatter)

    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger("music_librarian")


class _QtLogSignals(QObject):
    record_emitted = Signal(str, str)  # (level, formatted_message)


class QtLogHandler(logging.Handler):
    """
    Logging handler that emits each record as a Qt signal.
    Safe to add from the main thread; the signal crosses thread boundaries.
    """

    def __init__(self):
        super().__init__()
        self.signals = _QtLogSignals()
        self.setFormatter(logging.Formatter(
            "%(asctime)s  %(levelname)-8s  %(message)s",
            datefmt="%H:%M:%S",
        ))

    def emit(self, record: logging.LogRecord):
        try:
            msg = self.format(record)
            self.signals.record_emitted.emit(record.levelname, msg)
        except Exception:
            self.handleError(record)
