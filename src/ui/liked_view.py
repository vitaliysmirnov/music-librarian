"""Liked tracks table — model and view widget for the Liked sidebar section."""

import json
from pathlib import Path

from PySide6.QtCore import (
    Qt, QAbstractTableModel, QByteArray, QEvent, QModelIndex, QMimeData,
    QPoint, QSortFilterProxyModel, QUrl, Signal,
)
from PySide6.QtGui import QColor, QDrag, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QHeaderView,
    QLabel, QMenu, QPushButton, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QTableView, QVBoxLayout, QWidget,
)

from src.ui.style import ROW_HEIGHT, TABLE_STYLE
from src.utils import fmt_ms

COL_NUM    = 0
COL_ARTIST = 1
COL_TITLE  = 2
COL_ALBUM  = 3
COL_CATNO  = 4
COL_DATE   = 5
COL_DUR    = 6
COL_LIKE   = 7

_HEADERS = ["#", "Artist", "Track", "Release", "Cat. No.", "Date Liked", "Duration", "♥"]
_WIDTHS  = [32, 160, 200, 160, 90, 100, 60, 28]


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
            if col == COL_CATNO:  return row.get("catalog_number", "")
            if col == COL_DATE:   return (row.get("date_liked") or "")[:10]
            if col == COL_DUR:    return fmt_ms(row.get("duration_ms", 0))
            if col == COL_LIKE:   return ""
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
        if col == COL_LIKE:
            return False
        if col == COL_DUR:
            lms = (src.get_row(left.row())  or {}).get("duration_ms", 0)
            rms = (src.get_row(right.row()) or {}).get("duration_ms", 0)
            return lms < rms
        lv = (src.data(left,  Qt.DisplayRole) or "").lower()
        rv = (src.data(right, Qt.DisplayRole) or "").lower()
        return lv < rv


class _LikeDelegate(QStyledItemDelegate):
    """Renders ♥/♡ in COL_LIKE and fires a callback on click."""

    def __init__(self, like_col: int, toggle_cb, parent=None):
        super().__init__(parent)
        self._like_col  = like_col
        self._toggle_cb = toggle_cb

    def paint(self, painter: QPainter, option, index: QModelIndex):
        if index.column() != self._like_col:
            super().paint(painter, option, index)
            return
        # Draw standard cell background (selection, hover, alternating).
        super().paint(painter, option, index)
        row      = index.data(Qt.UserRole)
        is_liked = bool((row or {}).get("is_liked", False))
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        color    = (QColor(255, 255, 255) if selected
                    else QColor("#e0405a") if is_liked
                    else option.palette.placeholderText().color())
        painter.save()
        painter.setPen(color)
        painter.drawText(option.rect, Qt.AlignCenter, "♥" if is_liked else "♡")
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if (index.column() == self._like_col
                and event.type() == QEvent.Type.MouseButtonRelease):
            if self._toggle_cb:
                self._toggle_cb(index)
            return True
        return super().editorEvent(event, model, option, index)


class _LikedTableView(QTableView):
    """QTableView with drag-to-queue support for liked tracks."""

    def __init__(self):
        super().__init__()
        self._drag_start: QPoint | None = None
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            idx = self.indexAt(event.pos())
            if idx.isValid() and idx.column() == COL_LIKE:
                self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.viewport().unsetCursor()
        if self._drag_start is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return
        # Don't start a drag from the like column
        start_idx = self.indexAt(self._drag_start)
        if start_idx.isValid() and start_idx.column() == COL_LIKE:
            super().mouseMoveEvent(event)
            return
        if (event.pos() - self._drag_start).manhattanLength() < QApplication.startDragDistance():
            return
        press_pos = self._drag_start
        self._drag_start = None
        self._exec_drag(press_pos)

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def _exec_drag(self, press_pos: QPoint):
        proxy_index = self.indexAt(press_pos)
        if not proxy_index.isValid():
            return

        selected = {idx.row() for idx in self.selectionModel().selectedRows()}
        if proxy_index.row() not in selected:
            selected = {proxy_index.row()}

        proxy = self.model()
        src_model = proxy.sourceModel()
        urls: list[QUrl] = []
        meta: dict[str, dict] = {}
        for proxy_row in sorted(selected):
            src_row = proxy.mapToSource(proxy.index(proxy_row, 0)).row()
            row = src_model.get_row(src_row)
            if row and Path(row["path"]).is_file():
                urls.append(QUrl.fromLocalFile(row["path"]))
                meta[row["path"]] = {
                    "folder_path":    row.get("folder_path", ""),
                    "title":          row.get("album", ""),
                    "catalog_number": row.get("catalog_number", ""),
                    "artist":         row.get("artist", ""),
                }

        if not urls:
            return

        mime = QMimeData()
        mime.setUrls(urls)
        mime.setData("application/x-release-meta",
                     QByteArray(json.dumps(meta).encode()))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class LikedTracksView(QWidget):
    """Table of liked tracks with play / enqueue / unlike actions."""

    play_track_requested    = Signal(list, dict)
    enqueue_track_requested = Signal(list, dict)
    track_unliked           = Signal()
    go_to_release           = Signal(str)   # folder_path
    playlist_track_added    = Signal(int)   # playlist_id

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

        self._table = _LikedTableView()
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

        self._table.setItemDelegateForColumn(
            COL_LIKE, _LikeDelegate(COL_LIKE, self._toggle_like_at, self._table)
        )

        # Default sort: by date liked, newest first
        self._proxy.sort(COL_DATE, Qt.DescendingOrder)
        hdr.setSortIndicator(COL_DATE, Qt.DescendingOrder)

        layout.addWidget(self._table)

        # ── Bottom bar ────────────────────────────────────────────────────
        bb_widget = QWidget()
        bb = QHBoxLayout(bb_widget)
        bb.setContentsMargins(8, 4, 8, 4)
        bb.setSpacing(4)

        unlike_sc = QShortcut(QKeySequence("Ctrl+Backspace"), self._table)
        unlike_sc.setContext(Qt.WidgetWithChildrenShortcut)
        unlike_sc.activated.connect(self._unlike_selected)

        self._play_all_btn = QPushButton("▶ Play All")
        self._play_all_btn.setToolTip("Play all liked tracks in current order")
        self._play_all_btn.setEnabled(False)
        self._play_all_btn.clicked.connect(self._play_all)
        bb.addWidget(self._play_all_btn)
        bb.addStretch()

        self._stats_label = QLabel("")
        self._stats_label.setStyleSheet(
            "font-size: 11px; font-weight: 600; color: palette(placeholderText);"
        )
        bb.addWidget(self._stats_label)
        layout.addWidget(bb_widget)

    def refresh(self) -> None:
        rows = []
        for r in self._db.get_liked_tracks():
            row = dict(r) | {"is_liked": True}
            release = self._db.get_release_by_path(row.get("folder_path", ""))
            row["catalog_number"] = (dict(release).get("catalog_number") or "") if release else ""
            rows.append(row)
        self._model.load(rows)
        n = len(rows)
        total_s = sum(r.get("duration_ms", 0) for r in rows) // 1000
        mins, secs = divmod(total_s, 60)
        self._stats_label.setText(
            f"{n} tracks,  {mins} min {secs:02d} sec" if n else ""
        )
        self._play_all_btn.setEnabled(n > 0)

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

    def _all_rows_in_order(self) -> list[dict]:
        rows = []
        for proxy_row in range(self._proxy.rowCount()):
            src_row = self._proxy.mapToSource(self._proxy.index(proxy_row, 0)).row()
            r = self._model.get_row(src_row)
            if r:
                rows.append(r)
        return rows

    # ── Actions ────────────────────────────────────────────────────────────

    def _on_double_click(self, proxy_index: QModelIndex):
        row = self._selected_row()
        if row and Path(row["path"]).is_file():
            release_row = {"folder_path": row["folder_path"],
                           "title": row["album"], "artist": row["artist"],
                           "catalog_number": row.get("catalog_number", "")}
            self.play_track_requested.emit([row["path"]], release_row)

    def _play_selected(self):
        row = self._selected_row()
        if row and Path(row["path"]).is_file():
            release_row = {"folder_path": row["folder_path"],
                           "title": row["album"], "artist": row["artist"],
                           "catalog_number": row.get("catalog_number", "")}
            self.play_track_requested.emit([row["path"]], release_row)

    def _play_all(self):
        rows = [r for r in self._all_rows_in_order() if Path(r["path"]).is_file()]
        if not rows:
            return
        paths = [r["path"] for r in rows]
        release_row = {"folder_path": "", "title": "Liked", "artist": "", "catalog_number": ""}
        self.play_track_requested.emit(paths, release_row)

    def _enqueue_selected(self):
        rows = self._selected_rows()
        paths = [r["path"] for r in rows if Path(r["path"]).is_file()]
        if paths:
            first = rows[0]
            release_row = {"folder_path": first["folder_path"],
                           "title": first["album"], "artist": first["artist"],
                           "catalog_number": first.get("catalog_number", "")}
            self.enqueue_track_requested.emit(paths, release_row)

    def _unlike_selected(self):
        for row in self._selected_rows():
            self._db.unlike_track(row["path"])
        self.refresh()
        self.track_unliked.emit()

    def _toggle_like_at(self, proxy_index: QModelIndex):
        src_row = self._proxy.mapToSource(proxy_index).row()
        row = self._model.get_row(src_row)
        if row:
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
        act_play       = menu.addAction("Play Now")
        act_enqueue    = menu.addAction("Add to Queue")
        act_play.setEnabled(available)
        act_enqueue.setEnabled(available)

        pl_actions: dict = {}
        playlists = self._db.get_playlists() if self._db is not None else []
        if playlists:
            menu.addSeparator()
            pl_menu = menu.addMenu("Add to Playlist")
            for pl in playlists:
                act = pl_menu.addAction(pl["name"])
                pl_actions[act] = pl["id"]

        menu.addSeparator()
        act_go_release = menu.addAction("Go to Release")
        act_go_release.setEnabled(bool(row.get("folder_path")))
        menu.addSeparator()
        act_unlike     = menu.addAction("Remove from Liked")
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen == act_play:
            self._play_selected()
        elif chosen == act_enqueue:
            self._enqueue_selected()
        elif chosen in pl_actions:
            pid = pl_actions[chosen]
            for r in self._selected_rows():
                self._db.add_track_to_playlist(
                    pid, r["path"], r.get("artist", ""), r.get("title", ""),
                    r.get("album", ""), r.get("folder_path", ""), r.get("duration_ms", 0),
                )
            self.playlist_track_added.emit(pid)
        elif chosen == act_go_release:
            self.go_to_release.emit(row["folder_path"])
        elif chosen == act_unlike:
            self._unlike_selected()
