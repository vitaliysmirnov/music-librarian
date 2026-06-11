from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QTextCharFormat, QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox,
    QRadioButton, QSpinBox, QHBoxLayout, QLabel,
    QButtonGroup, QGraphicsOpacityEffect, QPlainTextEdit, QPushButton,
)

from src.database.db import Database
from src.utils.logger import QtLogHandler, get_logger  # noqa: F401 (QtLogHandler used in type hints)

MODE_MANUAL = "manual"
MODE_AUTO = "auto"

_LEVEL_COLORS = {
    "DEBUG":    "#888888",
    "INFO":     None,       # default text color
    "WARNING":  "#e5a450",
    "ERROR":    "#e05555",
    "CRITICAL": "#e05555",
}

_MAX_LOG_LINES = 500


class SettingsTab(QWidget):
    settings_changed = Signal()

    def __init__(self, db: Database, qt_log_handler: QtLogHandler | None = None):
        super().__init__()
        self._db = db
        self._setup_ui()
        self._setup_log_handler(qt_log_handler)
        self._load()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        # ── Monitoring ────────────────────────────────────────────────────
        mode_box = QGroupBox("Change Monitoring")
        mode_layout = QVBoxLayout(mode_box)
        mode_layout.setSpacing(0)
        mode_layout.setContentsMargins(12, 8, 12, 12)

        self._mode_group = QButtonGroup(self)

        self._manual_rb = QRadioButton("Manual")
        self._manual_rb.setFocusPolicy(Qt.NoFocus)
        self._mode_group.addButton(self._manual_rb, 0)
        mode_layout.addWidget(self._manual_rb)
        mode_layout.addWidget(_hint("Updates only when 'Scan Now' is clicked."))
        mode_layout.addSpacing(8)

        self._auto_rb = QRadioButton("Automatic")
        self._auto_rb.setFocusPolicy(Qt.NoFocus)
        self._mode_group.addButton(self._auto_rb, 1)
        mode_layout.addWidget(self._auto_rb)
        mode_layout.addWidget(_hint(
            "Monitors filesystem events in real time and runs periodic full scans. "
            "Recommended when using external drives."
        ))
        mode_layout.addSpacing(6)

        interval_row = QHBoxLayout()
        interval_row.setContentsMargins(20, 0, 0, 0)
        self._interval_label = QLabel("Full scan interval:")
        self._interval_spin = QSpinBox()
        self._interval_spin.setRange(5, 1440)
        self._interval_spin.setSuffix(" min")
        self._interval_spin.setFixedWidth(90)
        interval_row.addWidget(self._interval_label)
        interval_row.addWidget(self._interval_spin)
        interval_row.addStretch()
        mode_layout.addLayout(interval_row)

        layout.addWidget(mode_box)

        # ── Log viewer ────────────────────────────────────────────────────
        log_box = QGroupBox("Log")
        log_layout = QVBoxLayout(log_box)
        log_layout.setContentsMargins(8, 8, 8, 8)
        log_layout.setSpacing(6)

        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(_MAX_LOG_LINES)
        font = QFont("Menlo, Monaco, Courier New")
        font.setStyleHint(QFont.Monospace)
        font.setPointSize(11)
        self._log_view.setFont(font)
        log_layout.addWidget(self._log_view)

        clear_btn = QPushButton("Clear")
        clear_btn.setFixedWidth(70)
        clear_btn.clicked.connect(self._log_view.clear)
        log_layout.addWidget(clear_btn, alignment=Qt.AlignRight)

        layout.addWidget(log_box)

        self._manual_rb.toggled.connect(self._on_change)
        self._auto_rb.toggled.connect(self._on_change)
        self._interval_spin.valueChanged.connect(self._on_interval_changed)

    def _setup_log_handler(self, handler: QtLogHandler | None):
        if handler is None:
            handler = QtLogHandler()
            handler.setLevel(0)
            get_logger().addHandler(handler)
        self._log_handler = handler
        self._log_handler.signals.record_emitted.connect(self._append_log_line)

    def _append_log_line(self, level: str, message: str):
        color = _LEVEL_COLORS.get(level)
        if color:
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(color))
            cursor = self._log_view.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            cursor.insertText(message + "\n", fmt)
        else:
            self._log_view.appendPlainText(message)
        self._log_view.verticalScrollBar().setValue(
            self._log_view.verticalScrollBar().maximum()
        )

    # ── Settings ──────────────────────────────────────────────────────────

    def _on_change(self):
        auto = self._auto_rb.isChecked()
        self._interval_label.setEnabled(auto)
        self._interval_spin.setEnabled(auto)
        self._save()

    def _on_interval_changed(self):
        self._db.set_setting("scan_interval_min", str(self._interval_spin.value()))
        self.settings_changed.emit()

    def _load(self):
        for w in (self._manual_rb, self._auto_rb, self._interval_spin):
            w.blockSignals(True)

        mode = self._db.get_setting("scan_mode", MODE_MANUAL)
        if mode == MODE_AUTO:
            self._auto_rb.setChecked(True)
        else:
            self._manual_rb.setChecked(True)

        self._interval_spin.setValue(int(self._db.get_setting("scan_interval_min", "60")))

        for w in (self._manual_rb, self._auto_rb, self._interval_spin):
            w.blockSignals(False)

        auto = self._auto_rb.isChecked()
        self._interval_label.setEnabled(auto)
        self._interval_spin.setEnabled(auto)

    def _save(self):
        mode = MODE_AUTO if self._auto_rb.isChecked() else MODE_MANUAL
        self._db.set_setting("scan_mode", mode)
        self._db.set_setting("scan_interval_min", str(self._interval_spin.value()))
        self.settings_changed.emit()

    @property
    def scan_mode(self) -> str:
        return MODE_AUTO if self._auto_rb.isChecked() else MODE_MANUAL

    @property
    def scan_interval_min(self) -> int:
        return self._interval_spin.value()


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setContentsMargins(20, 2, 0, 0)
    effect = QGraphicsOpacityEffect(lbl)
    effect.setOpacity(0.45)
    lbl.setGraphicsEffect(effect)
    return lbl
