"""Liked tracks table — model and view widget for the Liked sidebar section."""

from pathlib import Path

from PySide6.QtCore import (
    Qt, QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Signal,
)
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QMenu,
    QTableView, QVBoxLayout, QWidget,
)

from src.ui.style import ROW_HEIGHT, TABLE_STYLE
from src.utils import fmt_ms

COL_NUM    = 0
COL_ARTIST = 1
COL_TITLE  = 2
COL_ALBUM  = 3
COL_DATE   = 4
COL_DUR    = 5

_HEADERS = ["#", "Artist", "Title", "Album", "Date Liked", "Duration"]
_WIDTHS  = [32, 160, 200, 160, 100, 60]


class LikedTracksModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self._rows: list[dict] = []

    def load(self, rows) -> None:
        self.beginResetModel()
        self._rows = [dict(r) for r in rows]
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(_HEADERS)

    def headerData(self, section: int, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return _HEADERS[section]
        return None

    def data(self, index: QModelIndex, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self._rows):
            return None
        row = self._rows[index.row()]
        col = index.column()
        if role == Qt.DisplayRole:
            if col == COL_NUM:    return str(index.row() + 1)
            if col == COL_ARTIST: return row.get("artist", "")
            if col == COL_TITLE:  return row.get("title", "")
            if col == COL_ALBUM:  return row.get("album", "")
            if col == COL_DATE:   return (row.get("date_liked") or "")[:10]
            if col == COL_DUR:    return fmt_ms(row.get("duration_ms", 0))
        if role == Qt.UserRole:
            return row
        return None

    def get_row(self, source_row: int) -> dict | None:
        if 0 <= source_row < len(self._rows):
            return self._rows[source_row]
        return None


class _LikedSortProxy(QSortFilterProxyModel):
    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        col = left.column()
        src = self.sourceModel()
        if col == COL_NUM:
            return left.row() < right.row()
        if col == COL_DUR:
            lms = (src.get_row(left.row())  or {}).get("duration_ms", 0)
            rms = (src.get_row(right.row()) or {}).get("duration_ms", 0)
            return lms < rms
        lv = (src.data(left,  Qt.DisplayRole) or "").lower()
        rv = (src.data(right, Qt.DisplayRole) or "").lower()
        return lv < rv


class LikedTracksView(QWidget):
    """Table of liked tracks with play / enqueue / unlike actions."""

    play_track_requested    = Signal(list, dict)
    enqueue_track_requested = Signal(list, dict)
    track_unliked           = Signal()

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._model = LikedTracksModel()
        self._proxy = _LikedSortProxy()
        self._proxy.setSourceModel(self._model)

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.setShowGrid(False)
        self._table.setStyleSheet(TABLE_STYLE)
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)

        vhdr = self._table.verticalHeader()
        vhdr.setVisible(False)
        vhdr.setDefaultSectionSize(ROW_HEIGHT)
        vhdr.setMinimumSectionSize(ROW_HEIGHT)

        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionsMovable(False)
        hdr.setSortIndicatorShown(True)
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        for col, w in enumerate(_WIDTHS):
            if col == COL_TITLE or col == COL_ALBUM:
                hdr.setSectionResizeMode(col, QHeaderView.Stretch)
            else:
                hdr.setSectionResizeMode(col, QHeaderView.Fixed)
                hdr.resizeSection(col, w)

        # Default sort: by date liked, newest first
        self._proxy.sort(COL_DATE, Qt.DescendingOrder)
        hdr.setSortIndicator(COL_DATE, Qt.DescendingOrder)

        layout.addWidget(self._table)

        # Bottom bar
        bb_widget = QWidget()
        bb = QHBoxLayout(bb_widget)
        bb.setContentsMargins(8, 4, 8, 4)
        bb.setSpacing(4)

        unlike_sc = QShortcut(QKeySequence("Ctrl+Backspace"), self._table)
        unlike_sc.setContext(Qt.WidgetWithChildrenShortcut)
        unlike_sc.activated.connect(self._unlike_selected)

        self._count_label = QLabel("")
        self._count_label.setStyleSheet("font-size: 11px;")
        bb.addWidget(self._count_label)
        bb.addStretch()
        layout.addWidget(bb_widget)

    def refresh(self) -> None:
        rows = self._db.get_liked_tracks()
        self._model.load(rows)
        self._count_label.setText(f"Liked: {len(rows)}")

    # ── Selection ──────────────────────────────────────────────────────────

    def _selected_row(self) -> dict | None:
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            return None
        src_row = self._proxy.mapToSource(indexes[0]).row()
        return self._model.get_row(src_row)

    def _selected_rows(self) -> list[dict]:
        rows = []
        for idx in self._table.selectionModel().selectedRows():
            src_row = self._proxy.mapToSource(idx).row()
            r = self._model.get_row(src_row)
            if r:
                rows.append(r)
        return rows

    # ── Actions ────────────────────────────────────────────────────────────

    def _on_double_click(self, proxy_index: QModelIndex):
        row = self._selected_row()
        if row and Path(row["path"]).is_file():
            release_row = {"folder_path": row["folder_path"],
                           "title": row["album"], "artist": row["artist"]}
            self.play_track_requested.emit([row["path"]], release_row)

    def _play_selected(self):
        row = self._selected_row()
        if row and Path(row["path"]).is_file():
            release_row = {"folder_path": row["folder_path"],
                           "title": row["album"], "artist": row["artist"]}
            self.play_track_requested.emit([row["path"]], release_row)

    def _enqueue_selected(self):
        rows = self._selected_rows()
        paths = [r["path"] for r in rows if Path(r["path"]).is_file()]
        if paths:
            first = rows[0]
            release_row = {"folder_path": first["folder_path"],
                           "title": first["album"], "artist": first["artist"]}
            self.enqueue_track_requested.emit(paths, release_row)

    def _unlike_selected(self):
        for row in self._selected_rows():
            self._db.unlike_track(row["path"])
        self.refresh()
        self.track_unliked.emit()

    def _show_context_menu(self, pos):
        proxy_index = self._table.indexAt(pos)
        if not proxy_index.isValid():
            return
        self._table.selectionModel().setCurrentIndex(
            proxy_index,
            self._table.selectionModel().SelectionFlag.ClearAndSelect |
            self._table.selectionModel().SelectionFlag.Rows,
        )
        row = self._selected_row()
        if not row:
            return
        available = Path(row["path"]).is_file()
        menu = QMenu(self)
        act_play    = menu.addAction("Play Now")
        act_enqueue = menu.addAction("Add to Queue")
        act_play.setEnabled(available)
        act_enqueue.setEnabled(available)
        menu.addSeparator()
        act_unlike = menu.addAction("Remove from Liked")
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen == act_play:
            self._play_selected()
        elif chosen == act_enqueue:
            self._enqueue_selected()
        elif chosen == act_unlike:
            self._unlike_selected()
