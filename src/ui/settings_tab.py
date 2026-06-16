from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtGui import QTextCharFormat, QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGroupBox,
    QRadioButton, QHBoxLayout, QLabel,
    QButtonGroup, QGraphicsOpacityEffect, QPlainTextEdit, QPushButton,
    QLineEdit, QFileDialog,
)

from src.database.db import Database
from src.scanner.mask import DEFAULT_MASK, validate_mask, mask_to_regex, parse_with_mask
from src.utils.logger import QtLogHandler, get_logger  # noqa: F401 (QtLogHandler used in type hints)

log = get_logger()

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
    mask_changed = Signal()  # emitted when user applies a new mask

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

        # ── Folder name mask ──────────────────────────────────────────────
        mask_box = QGroupBox("Folder Name Mask")
        mask_layout = QVBoxLayout(mask_box)
        mask_layout.setContentsMargins(12, 8, 12, 12)
        mask_layout.setSpacing(6)

        mask_layout.addWidget(_hint(
            "Required tokens: {artist}  {year_recorded}  {title}   "
            "Optional (standard): [{catalog_number}]  [{media}]  ({year_released})   "
            "Custom tokens: bare {token} matches one word; wrap in [] or () for multi-word values."
        ))

        input_row = QHBoxLayout()
        self._mask_edit = QLineEdit()
        self._mask_edit.setPlaceholderText(DEFAULT_MASK)
        self._mask_reset_btn = QPushButton("Reset")
        self._mask_reset_btn.setFixedWidth(55)
        input_row.addWidget(self._mask_edit)
        input_row.addWidget(self._mask_reset_btn)
        mask_layout.addLayout(input_row)

        self._mask_valid_label = QLabel("")
        mask_layout.addWidget(self._mask_valid_label)

        preview_row = QHBoxLayout()
        preview_lbl = QLabel("Preview:")
        preview_lbl.setFixedWidth(55)
        self._mask_preview_input = QLineEdit()
        self._mask_preview_input.setPlaceholderText("Type a folder name to test the mask…")
        preview_row.addWidget(preview_lbl)
        preview_row.addWidget(self._mask_preview_input)
        mask_layout.addLayout(preview_row)

        self._mask_preview_result = QLabel("")
        self._mask_preview_result.setWordWrap(True)
        mask_layout.addWidget(self._mask_preview_result)

        self._mask_apply_btn = QPushButton("Apply Mask")
        self._mask_apply_btn.setEnabled(False)
        mask_layout.addWidget(self._mask_apply_btn, alignment=Qt.AlignRight)

        layout.addWidget(mask_box)

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
        self._interval_edit = QLineEdit()
        self._interval_edit.setFixedWidth(60)
        self._interval_edit.setPlaceholderText("60")
        interval_row.addWidget(self._interval_label)
        interval_row.addWidget(self._interval_edit)
        interval_row.addWidget(QLabel("min"))
        self._interval_saved_label = QLabel("")
        self._interval_saved_label.setStyleSheet("color: #4caf50; font-size: 11px;")
        self._interval_saved_timer = QTimer(self)
        self._interval_saved_timer.setSingleShot(True)
        self._interval_saved_timer.timeout.connect(lambda: self._interval_saved_label.setText(""))
        interval_row.addWidget(self._interval_saved_label)
        interval_row.addStretch()
        mode_layout.addLayout(interval_row)

        layout.addWidget(mode_box)

        # ── Audio Player ──────────────────────────────────────────────────
        player_box = QGroupBox("Audio Player")
        player_layout = QVBoxLayout(player_box)
        player_layout.setContentsMargins(12, 8, 12, 12)
        player_layout.setSpacing(6)

        player_layout.addWidget(_hint(
            "Path to your audio player executable. Used when clicking ▶ in the Releases table. "
            "Leave empty to use the system default."
        ))

        player_row = QHBoxLayout()
        self._player_edit = QLineEdit()
        self._player_edit.setPlaceholderText("e.g. /Applications/VLC.app/Contents/MacOS/VLC")
        self._player_edit.returnPressed.connect(self._save_player)
        self._player_edit.editingFinished.connect(self._save_player)
        self._player_browse_btn = QPushButton("Browse…")
        self._player_browse_btn.setFixedWidth(75)
        self._player_browse_btn.clicked.connect(self._browse_player)
        player_row.addWidget(self._player_edit)
        player_row.addWidget(self._player_browse_btn)
        player_layout.addLayout(player_row)

        self._player_saved_label = QLabel("")
        self._player_saved_label.setStyleSheet("color: #4caf50; font-size: 11px;")
        self._player_saved_timer = QTimer(self)
        self._player_saved_timer.setSingleShot(True)
        self._player_saved_timer.timeout.connect(lambda: self._player_saved_label.setText(""))
        player_layout.addWidget(self._player_saved_label)

        layout.addWidget(player_box)

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

        self._mask_timer = QTimer(self)
        self._mask_timer.setSingleShot(True)
        self._mask_timer.setInterval(300)
        self._mask_timer.timeout.connect(self._validate_and_preview)

        self._mask_edit.textChanged.connect(self._mask_timer.start)
        self._mask_preview_input.textChanged.connect(self._update_mask_preview)
        self._mask_reset_btn.clicked.connect(self._reset_mask)
        self._mask_apply_btn.clicked.connect(self._apply_mask)

        self._manual_rb.toggled.connect(self._on_change)
        self._auto_rb.toggled.connect(self._on_change)
        self._interval_edit.returnPressed.connect(self._on_interval_changed)

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
        self._save()

    def _on_interval_changed(self):
        try:
            value = max(1, int(self._interval_edit.text().strip()))
        except ValueError:
            return
        self._interval_edit.setText(str(value))
        self._db.set_setting("scan_interval_min", str(value))
        self.settings_changed.emit()
        self._interval_edit.clearFocus()
        self._interval_saved_label.setText("Saved")
        self._interval_saved_timer.start(2000)
        log.info("Settings: full scan interval set to %d min", value)

    def _save_player(self):
        path = self._player_edit.text().strip()
        self._db.set_setting("audio_player_path", path)
        self._player_edit.clearFocus()
        self._player_saved_label.setText("Saved")
        self._player_saved_timer.start(2000)
        log.info("Settings: audio player set to %r", path)

    def _browse_player(self):
        import platform
        if platform.system() == "Darwin":
            path, _ = QFileDialog.getOpenFileName(
                self, "Select Audio Player", "/Applications"
            )
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Select Audio Player")
        if path:
            # On macOS, if user picks a .app bundle, point to the executable inside
            if path.endswith(".app"):
                import os
                from pathlib import Path
                macos_dir = Path(path) / "Contents" / "MacOS"
                executables = [
                    f for f in macos_dir.iterdir()
                    if f.is_file() and os.access(str(f), os.X_OK)
                ] if macos_dir.exists() else []
                if executables:
                    path = str(executables[0])
            self._player_edit.setText(path)
            self._save_player()

    def _validate_and_preview(self):
        mask = self._mask_edit.text().strip() or DEFAULT_MASK
        saved = self._db.get_setting("folder_mask", DEFAULT_MASK)
        error = validate_mask(mask)
        if error:
            self._mask_valid_label.setText(f'<span style="color:#e05555">{error}</span>')
            self._mask_apply_btn.setEnabled(False)
        else:
            self._mask_valid_label.setText("")
            self._mask_apply_btn.setEnabled(mask != saved)
        self._update_mask_preview()

    def _update_mask_preview(self):
        folder_name = self._mask_preview_input.text().strip()
        if not folder_name:
            self._mask_preview_result.setText("")
            return
        mask = self._mask_edit.text().strip() or DEFAULT_MASK
        if validate_mask(mask):
            self._mask_preview_result.setText("")
            return
        try:
            pattern = mask_to_regex(mask)
            groups = parse_with_mask(folder_name, pattern)
        except Exception:
            groups = None
        if groups:
            parts = [f"<b>{k}</b>: {v}" for k, v in groups.items()]
            self._mask_preview_result.setText("  |  ".join(parts))
        else:
            self._mask_preview_result.setText(
                '<span style="color:#e5a450">No match</span>'
            )

    def _reset_mask(self):
        self._mask_edit.setText(DEFAULT_MASK)

    def _apply_mask(self):
        mask = self._mask_edit.text().strip() or DEFAULT_MASK
        self._db.set_setting("folder_mask", mask)
        self._mask_apply_btn.setEnabled(False)
        self.mask_changed.emit()

    def _load(self):
        for w in (self._manual_rb, self._auto_rb):
            w.blockSignals(True)

        mode = self._db.get_setting("scan_mode", MODE_MANUAL)
        if mode == MODE_AUTO:
            self._auto_rb.setChecked(True)
        else:
            self._manual_rb.setChecked(True)

        for w in (self._manual_rb, self._auto_rb):
            w.blockSignals(False)

        self._interval_edit.setText(self._db.get_setting("scan_interval_min", "60"))

        self._player_edit.blockSignals(True)
        self._player_edit.setText(self._db.get_setting("audio_player_path", ""))
        self._player_edit.blockSignals(False)

        saved_mask = self._db.get_setting("folder_mask", DEFAULT_MASK)
        self._mask_edit.blockSignals(True)
        self._mask_edit.setText(saved_mask)
        self._mask_edit.blockSignals(False)
        self._mask_apply_btn.setEnabled(False)

    def _save(self):
        mode = MODE_AUTO if self._auto_rb.isChecked() else MODE_MANUAL
        self._db.set_setting("scan_mode", mode)
        self.settings_changed.emit()

    @property
    def scan_mode(self) -> str:
        return MODE_AUTO if self._auto_rb.isChecked() else MODE_MANUAL

    @property
    def scan_interval_min(self) -> int:
        try:
            return max(1, int(self._interval_edit.text().strip()))
        except ValueError:
            return 60


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setWordWrap(True)
    lbl.setContentsMargins(20, 2, 0, 0)
    effect = QGraphicsOpacityEffect(lbl)
    effect.setOpacity(0.45)
    lbl.setGraphicsEffect(effect)
    return lbl
