import json
import os
import platform
import re
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QByteArray, QSortFilterProxyModel, QUrl, QMimeData, QPoint, QSize, Signal
from PySide6.QtGui import QColor, QDrag, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel,
    QPushButton, QTableView, QHeaderView, QAbstractItemView, QMenu,
    QApplication, QStyledItemDelegate, QStyle, QMessageBox,
)

from src.scanner.mask import DEFAULT_MASK, KNOWN_TOKENS, get_custom_tokens
from src.ui.edit_release_dialog import EditReleaseDialog

# Column 0 is always the play button.
COL_PLAY = 0

# Human-readable headers and default widths for each known token, in
# the order they are looked up (actual display order follows the mask).
_TOKEN_HEADER: dict[str, str] = {
    "artist":         "Artist",
    "year_recorded":  "Rec. Year",
    "title":          "Release",
    "catalog_number": "Cat. No.",
    "media":          "Media",
    "year_released":  "Rel. Year",
}
_TOKEN_WIDTH: dict[str, int] = {
    "artist":         160,
    "year_recorded":  72,
    "title":          220,
    "catalog_number": 180,
    "media":          60,
    "year_released":  70,
}
_TOKEN_DB_KEY: dict[str, str] = {
    "artist":         "artist",
    "year_recorded":  "year_recorded",
    "title":          "title",
    "catalog_number": "catalog_number",
    "media":          "media",
    "year_released":  "year_released",
}

_TAIL_HEADERS = ["Source", "Available", "Path"]
_TAIL_WIDTHS  = [130, 70, 300]

_TIEBREAKER_TOKENS = ["artist", "year_recorded", "title"]

SETTINGS_KEY = "releases_header_state"

_PLAY_WIDTH          = 64
_EXTRA_DEFAULT_WIDTH = 90

_AUDIO_EXTENSIONS = {
    ".flac", ".mp3", ".wav", ".aiff", ".aif", ".m4a", ".alac",
    ".ogg", ".opus", ".ape", ".wv", ".wma", ".aac", ".dsf", ".dff",
}


def _known_token_order(mask: str) -> list[str]:
    """Return KNOWN_TOKENS in the order they appear in the mask.
    Any known token absent from the mask is appended at the end."""
    seen: set[str] = set()
    ordered: list[str] = []
    for tok in re.findall(r"\{(\w+)\}", mask):
        if tok in KNOWN_TOKENS and tok not in seen:
            seen.add(tok)
            ordered.append(tok)
    for tok in _TOKEN_HEADER:          # stable fallback order
        if tok not in seen:
            ordered.append(tok)
    return ordered


def _extras_from_row(row) -> dict:
    try:
        return json.loads(row["extras"] or "{}")
    except Exception:
        return {}


def _audio_files(folder_path: str) -> list[Path]:
    folder = Path(folder_path)
    return sorted(
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in _AUDIO_EXTENSIONS
    )


def _audio_urls(folder_path: str) -> list[QUrl]:
    return [QUrl.fromLocalFile(str(f)) for f in _audio_files(folder_path)]


def _move_to_trash(path: str):
    """Move *path* to the system Trash (recoverable). Raises on failure."""
    if platform.system() == "Darwin":
        from AppKit import NSFileManager, NSURL  # pyobjc-framework-Cocoa
        url = NSURL.fileURLWithPath_(path)
        ok, _, err = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
            url, None, None
        )
        if not ok:
            raise OSError(str(err))
    elif platform.system() == "Windows":
        import ctypes
        # SHFileOperation with FO_DELETE + FOF_ALLOWUNDO moves to Recycle Bin
        from ctypes import wintypes
        class SHFILEOPSTRUCT(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                ("fFlags", wintypes.WORD), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR),
            ]
        FO_DELETE = 3
        FOF_ALLOWUNDO = 0x0040
        FOF_NOCONFIRMATION = 0x0010
        FOF_SILENT = 0x0004
        op = SHFILEOPSTRUCT()
        op.wFunc = FO_DELETE
        op.pFrom = path + "\0\0"
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if result:
            raise OSError(f"SHFileOperation failed: {result}")
    else:
        result = subprocess.run(["gio", "trash", path], capture_output=True)
        if result.returncode != 0:
            raise OSError(result.stderr.decode())


def _play_release(folder_path: str, player_path: str):
    files = _audio_files(folder_path)
    if not files:
        return

    m3u_path = Path(tempfile.gettempdir()) / "music_librarian_play.m3u"
    m3u_path.write_text(
        "#EXTM3U\n" + "\n".join(str(f) for f in files),
        encoding="utf-8",
    )
    target = str(m3u_path)

    if player_path:
        clean = player_path.rstrip("/")
        if platform.system() == "Darwin":
            # Use `open -a <bundle.app>` regardless of whether the stored path
            # is the .app itself or a binary nested inside it — this lets macOS
            # hand the file to the already-running instance, which replaces its
            # playlist (the behaviour that originally fixed the enqueue bug).
            app_bundle = next(
                (str(p) for p in [Path(clean)] + list(Path(clean).parents)
                 if str(p).endswith(".app")),
                None,
            )
            if app_bundle:
                subprocess.Popen(["open", "-a", app_bundle, target])
            else:
                subprocess.Popen([clean, target])
        else:
            subprocess.Popen([clean, target])
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", target])
    elif platform.system() == "Windows":
        os.startfile(target)
    else:
        subprocess.Popen(["xdg-open", target])


class ReleasesModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self._rows: list = []
        self._token_order: list[str] = list(_TOKEN_HEADER)  # known tokens in mask order
        self._extra_tokens: list[str] = []

    # ── column layout ─────────────────────────────────────────────────────
    # Logical columns:
    #   0              → COL_PLAY (play button)
    #   1 … N_KNOWN    → known tokens in mask order
    #   N_KNOWN+1 …    → custom (extra) tokens
    #   … tail         → Source, Available, Path

    def _n_known(self) -> int:
        return len(self._token_order)

    def col_for_token(self, token: str) -> int:
        """Return the logical column index for a known token (1-based after COL_PLAY)."""
        return 1 + self._token_order.index(token)

    def _col_avail(self) -> int:
        return 1 + self._n_known() + len(self._extra_tokens) + 1

    def _col_source(self) -> int:
        return 1 + self._n_known() + len(self._extra_tokens)

    def _col_path(self) -> int:
        return 1 + self._n_known() + len(self._extra_tokens) + 2

    def _all_headers(self) -> list[str]:
        known_hdrs  = [_TOKEN_HEADER[t] for t in self._token_order]
        extra_hdrs  = [t.replace("_", " ").title() for t in self._extra_tokens]
        return [""] + known_hdrs + extra_hdrs + _TAIL_HEADERS

    # ── QAbstractTableModel interface ─────────────────────────────────────

    def load(self, rows, token_order: list[str], extra_tokens: list[str]):
        self.beginResetModel()
        self._rows = [dict(r) for r in rows]
        self._token_order = token_order
        self._extra_tokens = extra_tokens
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 1 + self._n_known() + len(self._extra_tokens) + len(_TAIL_HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            headers = self._all_headers()
            if section < len(headers):
                return headers[section]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row  = self._rows[index.row()]
        col  = index.column()
        n_kn = self._n_known()
        n_ex = len(self._extra_tokens)

        if role == Qt.DisplayRole:
            if col == COL_PLAY:
                return None
            if col <= n_kn:                      # known token columns (1-based)
                token = self._token_order[col - 1]
                return row.get(_TOKEN_DB_KEY[token]) or ""
            extra_i = col - 1 - n_kn
            if extra_i < n_ex:                   # custom token columns
                token = self._extra_tokens[extra_i]
                return _extras_from_row(row).get(token, "")
            tail = col - 1 - n_kn - n_ex
            if tail == 0:
                return Path(row["source_path"]).name
            if tail == 1:
                return "Yes" if row["is_available"] else "No"
            if tail == 2:
                return row["folder_path"]

        if role == Qt.ForegroundRole and not row["is_available"]:
            return QColor("#888888")

        if role == Qt.UserRole:
            return row

        return None

    def get_row(self, row_index) -> dict | None:
        r = self._rows[row_index] if row_index < len(self._rows) else None
        return dict(r) if r else None

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction

    def mimeData(self, indexes):
        seen_rows = set()
        urls = []
        for index in indexes:
            row_i = index.row()
            if row_i in seen_rows:
                continue
            seen_rows.add(row_i)
            row = self._rows[row_i]
            if row["is_available"]:
                urls.append(QUrl.fromLocalFile(row["folder_path"]))
        mime = QMimeData()
        mime.setUrls(urls)
        return mime


class _PlayButtonDelegate(QStyledItemDelegate):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db

    def paint(self, painter, option, index):
        if index.column() != COL_PLAY:
            super().paint(painter, option, index)
            return

        row = index.data(Qt.UserRole)
        if not row or not row["is_available"]:
            return

        painter.save()
        if option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, option.palette.highlight().color().lighter(175))
        painter.setPen(option.palette.text().color())
        painter.drawText(option.rect, Qt.AlignCenter, "▶")
        painter.restore()

    def sizeHint(self, option, index):
        if index.column() == COL_PLAY:
            return QSize(_PLAY_WIDTH, 24)
        return super().sizeHint(option, index)

    def editorEvent(self, event, model, option, index):
        from PySide6.QtCore import QEvent
        if index.column() == COL_PLAY and event.type() == QEvent.Type.MouseButtonRelease:
            row = index.data(Qt.UserRole)
            if row and row["is_available"]:
                player = self._db.get_setting("audio_player_path", "")
                _play_release(row["folder_path"], player)
            return True
        return super().editorEvent(event, model, option, index)


class _MultiSortProxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self._primary_col: int | None = None
        self._primary_order: Qt.SortOrder = Qt.AscendingOrder

    def _src(self) -> ReleasesModel:
        return self.sourceModel()

    def sort(self, column: int, order=Qt.AscendingOrder):
        if column == COL_PLAY:
            return
        self._primary_col = column if column >= 0 else None
        self._primary_order = order
        super().sort(column, order)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        src = self._src()
        avail_col = src._col_avail()

        def val(index: QModelIndex, col: int) -> tuple:
            if col == avail_col:
                row = src.data(src.index(index.row(), col), Qt.UserRole)
                return (0, 0 if (row and row["is_available"]) else 1, "")
            raw = (src.data(src.index(index.row(), col)) or "").strip()
            if not raw:
                return (2, 0.0, "")
            try:
                return (0, float(raw), "")
            except ValueError:
                return (1, 0.0, raw.lower())

        default_primary = src.col_for_token("artist")
        primary = self._primary_col if self._primary_col is not None else default_primary
        lv, rv = val(left, primary), val(right, primary)
        if lv != rv:
            return lv < rv

        for tok in _TIEBREAKER_TOKENS:
            tb_col = src.col_for_token(tok)
            if primary == tb_col:
                continue
            lv, rv = val(left, tb_col), val(right, tb_col)
            if lv != rv:
                return lv < rv

        return False


class _DragTableView(QTableView):
    def __init__(self):
        super().__init__()
        self._drag_start: QPoint | None = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return

        pressed_index = self.indexAt(self._drag_start)
        if pressed_index.isValid() and pressed_index.column() == COL_PLAY:
            super().mouseMoveEvent(event)
            return

        if (event.pos() - self._drag_start).manhattanLength() < QApplication.startDragDistance():
            return

        press_pos = self._drag_start
        self._drag_start = None
        self._exec_drag(press_pos)

    def _exec_drag(self, press_pos: QPoint):
        proxy_index = self.indexAt(press_pos)
        if not proxy_index.isValid():
            return

        selected_proxy_rows = {
            idx.row() for idx in self.selectionModel().selectedRows()
        }
        if proxy_index.row() not in selected_proxy_rows:
            selected_proxy_rows = {proxy_index.row()}

        source_model = self.model().sourceModel()
        urls: list[QUrl] = []
        for proxy_row in sorted(selected_proxy_rows):
            source_index = self.model().mapToSource(
                self.model().index(proxy_row, 0)
            )
            row = source_model.get_row(source_index.row())
            if row and row["is_available"]:
                urls.extend(_audio_urls(row["folder_path"]))

        if not urls:
            return

        mime = QMimeData()
        mime.setUrls(urls)
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        super().mouseReleaseEvent(event)


class ReleasesTab(QWidget):
    release_trashed = Signal()

    def __init__(self, db):
        super().__init__()
        self._db = db
        self._setup_ui()
        self._restore_header_state()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Search:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Artist, title, or both words…")
        self._search.textChanged.connect(self.refresh)
        filter_row.addWidget(self._search)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._search.clear)
        filter_row.addWidget(self._clear_btn)
        filter_row.addStretch()

        self._count_label = QLabel("")
        filter_row.addWidget(self._count_label)
        layout.addLayout(filter_row)

        self._model = ReleasesModel()
        self._proxy = _MultiSortProxy()
        self._proxy.setSourceModel(self._model)

        self._table = _DragTableView()
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.verticalHeader().setVisible(False)
        self._table.setDragEnabled(True)
        self._table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self._table.setDefaultDropAction(Qt.DropAction.CopyAction)
        self._table.setMouseTracking(True)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)

        self._delegate = _PlayButtonDelegate(self._db, self._table)
        self._table.setItemDelegate(self._delegate)

        hdr = self._table.horizontalHeader()
        hdr.setSectionsMovable(True)
        hdr.setSectionsClickable(True)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setSectionResizeMode(COL_PLAY, QHeaderView.Interactive)
        hdr.setContextMenuPolicy(Qt.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._show_header_menu)
        hdr.sectionMoved.connect(self._on_section_moved)
        hdr.sectionResized.connect(self._save_header_state)
        hdr.sectionClicked.connect(self._on_header_clicked)


        layout.addWidget(self._table)

        # Command+Backspace (macOS) / Ctrl+Backspace (other platforms) → move to Trash
        trash_sc = QShortcut(QKeySequence("Ctrl+Backspace"), self._table)
        trash_sc.setContext(Qt.WidgetWithChildrenShortcut)
        trash_sc.activated.connect(self._trash_release)

        btn_row = QHBoxLayout()

        edit_btn = QPushButton("Edit…")
        edit_btn.setToolTip("Edit selected release metadata (double-click)")
        edit_btn.clicked.connect(self._edit_release)
        btn_row.addWidget(edit_btn)

        open_btn = QPushButton("Open Folder")
        open_btn.clicked.connect(self._open_release)
        btn_row.addWidget(open_btn)

        drag_hint = QLabel("Click ▶ to play a release, or drag it to your audio player")
        drag_hint.setStyleSheet("color: palette(placeholderText); font-size: 11px;")
        btn_row.addWidget(drag_hint)

        btn_row.addStretch()

        reset_btn = QPushButton("Reset View")
        reset_btn.setToolTip("Restore default column order and widths")
        reset_btn.clicked.connect(self._reset_header)
        btn_row.addWidget(reset_btn)

        layout.addLayout(btn_row)

    # ── Header click ──────────────────────────────────────────────────────

    def _on_header_clicked(self, logical: int):
        if logical == COL_PLAY:
            # Qt already moved the indicator to COL_PLAY — put it back.
            col = self._proxy._primary_col
            if col is None or col == COL_PLAY:
                col = COL_PLAY + 1
            self._table.horizontalHeader().setSortIndicator(col, self._proxy._primary_order)

    # ── Double-click ───────────────────────────────────────────────────────

    def _on_double_click(self, proxy_index):
        if proxy_index.column() == COL_PLAY:
            return
        self._edit_release()

    # ── Row context menu ───────────────────────────────────────────────────

    def _show_context_menu(self, pos):
        proxy_index = self._table.indexAt(pos)
        if not proxy_index.isValid():
            return
        # Ensure the clicked row is selected
        self._table.selectionModel().setCurrentIndex(
            proxy_index, self._table.selectionModel().SelectionFlag.ClearAndSelect |
            self._table.selectionModel().SelectionFlag.Rows,
        )
        row = self._selected_row()
        if not row:
            return

        available = bool(row["is_available"])
        player_path = self._db.get_setting("audio_player_path", "").strip()

        menu = QMenu(self)

        if player_path:
            player_name = Path(player_path.rstrip("/")).stem or player_path
            act_play = menu.addAction(f"Play with {player_name}")
            act_play.setEnabled(available)
        else:
            act_play = None

        act_open = menu.addAction("Open Folder")
        act_open.setEnabled(available)

        menu.addSeparator()

        act_delete = menu.addAction("Move to Trash")

        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_play:
            _play_release(row["folder_path"], player_path)
        elif chosen == act_open:
            self._open_release()
        elif chosen == act_delete:
            self._trash_release()

    # ── Header context menu ────────────────────────────────────────────────

    def _show_header_menu(self, pos):
        hdr = self._table.horizontalHeader()
        headers = self._model._all_headers()
        menu = QMenu(self)
        for logical_idx, name in enumerate(headers):
            if logical_idx == COL_PLAY:
                continue
            label = name if name else f"Column {logical_idx}"
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not hdr.isSectionHidden(logical_idx))
            action.setData(logical_idx)
        chosen = menu.exec(hdr.mapToGlobal(pos))
        if chosen is not None:
            hdr.setSectionHidden(chosen.data(), not chosen.isChecked())
            self._save_header_state()

    # ── Header state ───────────────────────────────────────────────────────

    def _on_section_moved(self, logical, old_visual, new_visual):
        if logical == COL_PLAY and new_visual != 0:
            self._table.horizontalHeader().moveSection(new_visual, 0)
            return
        if new_visual == 0 and logical != COL_PLAY:
            self._table.horizontalHeader().moveSection(0, old_visual)
            return
        self._save_header_state()

    def _save_header_state(self, *_):
        state: QByteArray = self._table.horizontalHeader().saveState()
        self._db.set_setting(SETTINGS_KEY, state.toBase64().data().decode())

    def _restore_header_state(self):
        raw = self._db.get_setting(SETTINGS_KEY, "")
        if not raw:
            return
        try:
            data = QByteArray.fromBase64(raw.encode())
            self._table.horizontalHeader().restoreState(data)
        except Exception:
            pass
        hdr = self._table.horizontalHeader()
        # restoreState re-applies saved resize mode and width; always override.
        hdr.setSectionResizeMode(COL_PLAY, QHeaderView.Interactive)
        hdr.resizeSection(COL_PLAY, _PLAY_WIDTH)
        # If saved state had sort indicator on COL_PLAY, move it to Artist.
        if hdr.sortIndicatorSection() == COL_PLAY:
            hdr.setSortIndicator(COL_PLAY + 1, Qt.AscendingOrder)
            self._save_header_state()

    def invalidate_header_state(self):
        self._db.set_setting(SETTINGS_KEY, "")

    def _reset_header(self):
        hdr = self._table.horizontalHeader()
        n = self._model.columnCount()
        for logical in range(n):
            visual = hdr.visualIndex(logical)
            if visual != logical:
                hdr.moveSection(visual, logical)
        self._apply_default_widths()
        for i in range(n):
            hdr.setSectionHidden(i, False)
        self._save_header_state()

    # ── Data ───────────────────────────────────────────────────────────────

    def refresh(self):
        mask = self._db.get_setting("folder_mask", DEFAULT_MASK)
        token_order  = _known_token_order(mask)
        extra_tokens = get_custom_tokens(mask)
        rows = self._db.get_releases(search=self._search.text().strip())
        prev_n = self._model.columnCount()
        self._model.load(rows, token_order, extra_tokens)
        if self._model.columnCount() != prev_n:
            self._apply_default_widths()
        else:
            # beginResetModel resets header section sizes; restore saved state.
            self._restore_header_state()
        # Always enforce play column — restore may have overwritten it.
        hdr = self._table.horizontalHeader()
        hdr.resizeSection(COL_PLAY, _PLAY_WIDTH)
        hdr.setSectionResizeMode(COL_PLAY, QHeaderView.Interactive)
        self._count_label.setText(f"Releases: {len(rows)}")

    def _apply_default_widths(self):
        hdr = self._table.horizontalHeader()
        hdr.resizeSection(COL_PLAY, _PLAY_WIDTH)
        hdr.setSectionResizeMode(COL_PLAY, QHeaderView.Interactive)
        for i, tok in enumerate(self._model._token_order):
            hdr.resizeSection(1 + i, _TOKEN_WIDTH.get(tok, 100))
        n_kn = self._model._n_known()
        for i in range(len(self._model._extra_tokens)):
            hdr.resizeSection(1 + n_kn + i, _EXTRA_DEFAULT_WIDTH)
        for i, w in enumerate(_TAIL_WIDTHS):
            hdr.resizeSection(1 + n_kn + len(self._model._extra_tokens) + i, w)

    def _selected_row(self) -> dict | None:
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            return None
        source_row = self._proxy.mapToSource(indexes[0]).row()
        return self._model.get_row(source_row)

    def _edit_release(self, *_):
        row = self._selected_row()
        if not row or not row["is_available"]:
            return
        dlg = EditReleaseDialog(self._db, row, self)
        if dlg.exec() == EditReleaseDialog.Accepted:
            self.refresh()

    def _open_release(self, *_):
        row = self._selected_row()
        if row and row["is_available"]:
            p = row["folder_path"]
            if platform.system() == "Darwin":
                subprocess.Popen(["open", p])
            elif platform.system() == "Windows":
                os.startfile(p)
            else:
                subprocess.Popen(["xdg-open", p])

    def _trash_release(self):
        row = self._selected_row()
        if not row:
            return

        folder_path = row["folder_path"]
        folder_exists = Path(folder_path).exists()
        artist = row.get("artist", "")
        title = row.get("title", "")
        label = f"{artist} — {title}" if artist and title else folder_path

        if folder_exists:
            msg = QMessageBox(self)
            msg.setWindowTitle("Move to Trash")
            msg.setText(f"Move to Trash:\n{label}")
            msg.setInformativeText("The folder will be moved to the Trash. This can be undone.")
            msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
            msg.setDefaultButton(QMessageBox.Cancel)
            if msg.exec() != QMessageBox.Ok:
                return
            try:
                _move_to_trash(folder_path)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not move to Trash:\n{e}")
                return

        self._db.delete_release_by_path(folder_path)
        self.refresh()
        self.release_trashed.emit()
