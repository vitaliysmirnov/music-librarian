import json
import os
import platform
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QByteArray, QSortFilterProxyModel, QUrl, QMimeData, QPoint
from PySide6.QtGui import QColor, QDrag
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel,
    QPushButton, QTableView, QHeaderView, QAbstractItemView, QMenu, QApplication,
)

from src.scanner.mask import DEFAULT_MASK, get_custom_tokens
from src.ui.edit_release_dialog import EditReleaseDialog

# Fixed columns that always appear (in this order)
_FIXED_HEADERS = ["Artist", "Rec. Year", "Release", "Media", "Cat. No.", "Rel. Year"]
_TAIL_HEADERS  = ["Source", "Available", "Path"]

_N_FIXED = len(_FIXED_HEADERS)   # 6
_N_TAIL  = len(_TAIL_HEADERS)    # 3

# Stable logical indices for fixed columns (never change)
COL_ARTIST   = 0
COL_YEAR_REC = 1
COL_TITLE    = 2
COL_MEDIA    = 3
COL_CATALOG  = 4
COL_YEAR_REL = 5

# Tiebreaker columns are always among the fixed ones
_TIEBREAKER = [COL_ARTIST, COL_YEAR_REC, COL_TITLE]

SETTINGS_KEY = "releases_header_state"

# Default widths for fixed + tail columns
_FIXED_WIDTHS = [160, 72, 220, 60, 180, 70]
_TAIL_WIDTHS  = [130, 70, 300]
_EXTRA_DEFAULT_WIDTH = 90


def _extras_from_row(row) -> dict:
    try:
        return json.loads(row["extras"] or "{}")
    except Exception:
        return {}


class ReleasesModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self._rows: list = []
        self._extra_tokens: list[str] = []   # custom token names, in mask order

    # ── column helpers ────────────────────────────────────────────────────

    def _all_headers(self) -> list[str]:
        extra_labels = [t.replace("_", " ").title() for t in self._extra_tokens]
        return _FIXED_HEADERS + extra_labels + _TAIL_HEADERS

    def col_avail(self) -> int:
        return _N_FIXED + len(self._extra_tokens) + 1   # Source=+0, Available=+1, Path=+2

    def col_source(self) -> int:
        return _N_FIXED + len(self._extra_tokens)

    def col_path(self) -> int:
        return _N_FIXED + len(self._extra_tokens) + 2

    # ── QAbstractTableModel interface ─────────────────────────────────────

    def load(self, rows, extra_tokens: list[str]):
        self.beginResetModel()
        self._rows = list(rows)
        self._extra_tokens = extra_tokens
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return _N_FIXED + len(self._extra_tokens) + _N_TAIL

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            headers = self._all_headers()
            if section < len(headers):
                return headers[section]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()
        n_extra = len(self._extra_tokens)

        if role == Qt.DisplayRole:
            if col < _N_FIXED:
                return _fixed_value(row, col)
            if col < _N_FIXED + n_extra:
                token = self._extra_tokens[col - _N_FIXED]
                return _extras_from_row(row).get(token, "")
            tail = col - _N_FIXED - n_extra
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
        # Collect unique rows, only available releases
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


def _fixed_value(row, col: int) -> str:
    if col == COL_ARTIST:   return row["artist"]
    if col == COL_YEAR_REC: return row["year_recorded"]
    if col == COL_TITLE:    return row["title"]
    if col == COL_MEDIA:    return row["media"] or ""
    if col == COL_CATALOG:  return row["catalog_number"] or ""
    if col == COL_YEAR_REL: return row["year_released"] or ""
    return ""


class _MultiSortProxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self._primary_col: int | None = None
        self._primary_order = Qt.AscendingOrder

    def _src(self) -> ReleasesModel:
        return self.sourceModel()

    def sort(self, column: int, order=Qt.AscendingOrder):
        self._primary_col = column if column >= 0 else None
        self._primary_order = order
        super().sort(column, order)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        src = self._src()
        avail_col = src.col_avail()

        def val(index: QModelIndex, col: int) -> tuple:
            if col == avail_col:
                row = src.data(src.index(index.row(), col), Qt.UserRole)
                # (0, 0)=Yes < (0, 1)=No  →  ascending puts Yes first
                return (0, 0 if (row and row["is_available"]) else 1, "")
            raw = (src.data(src.index(index.row(), col)) or "").strip()
            if not raw:
                return (2, 0.0, "")        # empty → always last
            try:
                return (0, float(raw), "")  # numeric → sort by value
            except ValueError:
                return (1, 0.0, raw.lower())  # text → sort alphabetically

        primary = self._primary_col if self._primary_col is not None else 0
        lv, rv = val(left, primary), val(right, primary)
        if lv != rv:
            return lv < rv

        for tb in _TIEBREAKER:
            if primary == tb:
                continue
            lv, rv = val(left, tb), val(right, tb)
            if lv != rv:
                return lv < rv

        return False


_AUDIO_EXTENSIONS = {
    ".flac", ".mp3", ".wav", ".aiff", ".aif", ".m4a", ".alac",
    ".ogg", ".opus", ".ape", ".wv", ".wma", ".aac", ".dsf", ".dff",
}


def _audio_urls(folder_path: str) -> list[QUrl]:
    """Return sorted file:// URLs for all audio files in folder_path."""
    folder = Path(folder_path)
    files = sorted(
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in _AUDIO_EXTENSIONS
    )
    return [QUrl.fromLocalFile(str(f)) for f in files]


class _DragTableView(QTableView):
    """QTableView that starts a file drag after the system drag-distance
    threshold, without letting Qt extend the row selection on mouse-move."""

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

        if (event.pos() - self._drag_start).manhattanLength() < QApplication.startDragDistance():
            # Below threshold — swallow the event so Qt never starts rubber-band selection.
            return

        # Threshold reached — build drag manually and execute.
        press_pos = self._drag_start
        self._drag_start = None
        self._exec_drag(press_pos)

    def _exec_drag(self, press_pos: QPoint):
        proxy_index = self.indexAt(press_pos)
        if not proxy_index.isValid():
            return

        source_index = self.model().mapToSource(proxy_index)
        row = self.model().sourceModel().get_row(source_index.row())
        if row is None or not row["is_available"]:
            return

        urls = _audio_urls(row["folder_path"])
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
        self._table.doubleClicked.connect(self._edit_release)
        self._table.verticalHeader().setVisible(False)
        self._table.setDragEnabled(True)
        self._table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self._table.setDefaultDropAction(Qt.DropAction.CopyAction)

        hdr = self._table.horizontalHeader()
        hdr.setSectionsMovable(True)
        hdr.setSectionsClickable(True)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setContextMenuPolicy(Qt.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._show_header_menu)
        hdr.sectionMoved.connect(self._save_header_state)
        hdr.sectionResized.connect(self._save_header_state)

        layout.addWidget(self._table)

        btn_row = QHBoxLayout()

        edit_btn = QPushButton("Edit…")
        edit_btn.setToolTip("Edit selected release metadata (double-click)")
        edit_btn.clicked.connect(self._edit_release)
        btn_row.addWidget(edit_btn)

        open_btn = QPushButton("Open Folder")
        open_btn.clicked.connect(self._open_release)
        btn_row.addWidget(open_btn)

        drag_hint = QLabel("Drag a release to your audio player to enqueue it")
        drag_hint.setStyleSheet("color: palette(placeholderText); font-size: 11px;")
        btn_row.addWidget(drag_hint)

        btn_row.addStretch()

        reset_btn = QPushButton("Reset View")
        reset_btn.setToolTip("Restore default column order and widths")
        reset_btn.clicked.connect(self._reset_header)
        btn_row.addWidget(reset_btn)

        layout.addLayout(btn_row)

    # ── Header context menu ────────────────────────────────────────────────

    def _show_header_menu(self, pos):
        hdr = self._table.horizontalHeader()
        headers = self._model._all_headers()
        menu = QMenu(self)
        for logical_idx, name in enumerate(headers):
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(not hdr.isSectionHidden(logical_idx))
            action.setData(logical_idx)
        chosen = menu.exec(hdr.mapToGlobal(pos))
        if chosen is not None:
            hdr.setSectionHidden(chosen.data(), not chosen.isChecked())
            self._save_header_state()

    # ── Header state ───────────────────────────────────────────────────────

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

    def invalidate_header_state(self):
        """Call when column count changes (e.g. mask changed) so stale state is not restored."""
        self._db.set_setting(SETTINGS_KEY, "")

    def _reset_header(self):
        hdr = self._table.horizontalHeader()
        n = self._model.columnCount()
        for logical in range(n):
            visual = hdr.visualIndex(logical)
            if visual != logical:
                hdr.moveSection(visual, logical)
        for i, w in enumerate(_FIXED_WIDTHS):
            hdr.setSectionHidden(i, False)
            hdr.resizeSection(i, w)
        n_extra = len(self._model._extra_tokens)
        for i in range(n_extra):
            col = _N_FIXED + i
            hdr.setSectionHidden(col, False)
            hdr.resizeSection(col, _EXTRA_DEFAULT_WIDTH)
        for i, w in enumerate(_TAIL_WIDTHS):
            col = _N_FIXED + n_extra + i
            hdr.setSectionHidden(col, False)
            hdr.resizeSection(col, w)
        self._save_header_state()

    # ── Data ───────────────────────────────────────────────────────────────

    def refresh(self):
        mask = self._db.get_setting("folder_mask", DEFAULT_MASK)
        extra_tokens = get_custom_tokens(mask)
        rows = self._db.get_releases(search=self._search.text().strip())
        prev_n = self._model.columnCount()
        self._model.load(rows, extra_tokens)
        # If column count changed, apply default widths for new extras
        if self._model.columnCount() != prev_n:
            self._apply_default_widths()
        self._count_label.setText(f"Releases: {len(rows)}")

    def _apply_default_widths(self):
        hdr = self._table.horizontalHeader()
        for i, w in enumerate(_FIXED_WIDTHS):
            hdr.resizeSection(i, w)
        n_extra = len(self._model._extra_tokens)
        for i in range(n_extra):
            hdr.resizeSection(_N_FIXED + i, _EXTRA_DEFAULT_WIDTH)
        for i, w in enumerate(_TAIL_WIDTHS):
            hdr.resizeSection(_N_FIXED + n_extra + i, w)

    def _selected_row(self) -> dict | None:
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            return None
        source_row = self._proxy.mapToSource(indexes[0]).row()
        return self._model.get_row(source_row)

    def _edit_release(self, *_):
        row = self._selected_row()
        if not row:
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
