"""Playlist content view — sortable table with drag-reorder and URL drop-to-add."""

import json
from pathlib import Path

from PySide6.QtCore import (
    Qt, QAbstractTableModel, QByteArray, QEvent, QMimeData,
    QModelIndex, QPoint, QSize, QUrl, Signal,
)
from PySide6.QtGui import QColor, QDrag, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QHBoxLayout, QHeaderView,
    QLabel, QMenu, QPushButton, QStyle, QStyledItemDelegate,
    QStyleOptionViewItem, QTableView, QVBoxLayout, QWidget,
)

from src.ui.style import ROW_HEIGHT, TABLE_STYLE
from src.utils import fmt_ms
from src.utils.audio import AUDIO_EXTENSIONS

_MIME_ROWS  = "application/x-playlist-rows"
_LINE_COLOR = QColor("#3875d7")

COL_NUM    = 0
COL_ARTIST = 1
COL_TITLE  = 2
COL_ALBUM  = 3
COL_CATNO  = 4
COL_DATE   = 5
COL_DUR    = 6
COL_LIKE   = 7

_HEADERS = ["#", "Artist", "Track", "Release", "Cat. No.", "Date Added", "Duration", "♥"]
_WIDTHS  = [32, 160, 200, 160, 90, 100, 60, 28]


# ── Drop-line overlay ─────────────────────────────────────────────────────────

class _DropLine(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._y = -1

    def set_y(self, y: int):
        self._y = y
        self.update()

    def clear(self):
        if self._y >= 0:
            self._y = -1
            self.update()

    def paintEvent(self, _event):
        if self._y < 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        x1, x2, y, r = 8, self.width() - 8, self._y, 3
        p.setPen(QPen(_LINE_COLOR, 2))
        p.drawLine(x1, y, x2, y)
        p.setBrush(_LINE_COLOR)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(x1 - r, y - r, r * 2, r * 2)
        p.drawEllipse(x2 - r, y - r, r * 2, r * 2)


# ── Model ─────────────────────────────────────────────────────────────────────

class PlaylistModel(QAbstractTableModel):
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
            if col == COL_DATE:   return (row.get("date_added") or "")[:10]
            if col == COL_DUR:    return fmt_ms(row.get("duration_ms", 0))
            if col == COL_LIKE:   return ""
        if role == Qt.UserRole:
            return row
        return None

    def get_row(self, row_index: int) -> dict | None:
        if 0 <= row_index < len(self._rows):
            return self._rows[row_index]
        return None

    def paths(self) -> list[str]:
        return [r["path"] for r in self._rows]

    def move_row(self, from_row: int, to_row: int) -> None:
        if from_row == to_row:
            return
        n = len(self._rows)
        if not (0 <= from_row < n and 0 <= to_row <= n):
            return
        dest = to_row if to_row < from_row else to_row - 1
        self.beginResetModel()
        row = self._rows.pop(from_row)
        self._rows.insert(dest, row)
        self.endResetModel()


# ── Like delegate ─────────────────────────────────────────────────────────────

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


# ── Table view with drag-reorder + URL drop ───────────────────────────────────

class _PlaylistTableView(QTableView):
    row_moved    = Signal(int, int)   # from_row, to_row (model rows)
    urls_dropped = Signal(list)       # list[QUrl]

    def __init__(self):
        super().__init__()
        self._drag_start: QPoint | None = None
        self.setAcceptDrops(True)
        self.setMouseTracking(True)
        self._drop_line = _DropLine(self.viewport())
        self._drop_line.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._drop_line.setGeometry(self.viewport().rect())

    # ── Initiate drag ─────────────────────────────────────────────────────

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
        selected = {idx.row() for idx in self.selectionModel().selectedRows()}
        if not selected:
            return
        mime = QMimeData()
        # Internal reorder payload
        mime.setData(_MIME_ROWS, QByteArray(
            ",".join(str(r) for r in sorted(selected)).encode()
        ))
        # URL payload so the queue panel can accept the drop
        urls: list[QUrl] = []
        meta: dict[str, dict] = {}
        for row_i in sorted(selected):
            row = self.model().get_row(row_i)
            if row and Path(row["path"]).is_file():
                urls.append(QUrl.fromLocalFile(row["path"]))
                meta[row["path"]] = {
                    "folder_path":    row.get("folder_path", ""),
                    "title":          row.get("album", ""),
                    "catalog_number": row.get("catalog_number", ""),
                    "artist":         row.get("artist", ""),
                }
        if urls:
            mime.setUrls(urls)
            mime.setData("application/x-release-meta",
                         QByteArray(json.dumps(meta).encode()))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction | Qt.DropAction.MoveAction)

    # ── Receive drop ──────────────────────────────────────────────────────

    def _insert_row_at(self, pos: QPoint) -> int:
        idx = self.indexAt(pos)
        if not idx.isValid():
            return self.model().rowCount()
        rect = self.visualRect(idx)
        return idx.row() if pos.y() < rect.center().y() else idx.row() + 1

    def _update_drop_line(self, insert_row: int):
        n = self.model().rowCount()
        if n == 0:
            self._drop_line.clear()
            return
        if insert_row < n:
            y = self.visualRect(self.model().index(insert_row, 0)).top()
        else:
            y = self.visualRect(self.model().index(n - 1, 0)).bottom()
        self._drop_line.setGeometry(self.viewport().rect())
        self._drop_line.set_y(y)
        self._drop_line.show()
        self._drop_line.raise_()

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(_MIME_ROWS) or event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(_MIME_ROWS) or event.mimeData().hasUrls():
            insert_row = self._insert_row_at(event.position().toPoint())
            self._update_drop_line(insert_row)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._drop_line.clear()
        self._drop_line.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._drop_line.clear()
        self._drop_line.hide()
        pos = event.position().toPoint()

        if event.mimeData().hasFormat(_MIME_ROWS):
            raw = bytes(event.mimeData().data(_MIME_ROWS)).decode()
            proxy_rows = [int(x) for x in raw.split(",") if x]
            if not proxy_rows:
                event.ignore()
                return
            from_proxy = proxy_rows[0]
            to_proxy   = self._insert_row_at(pos)
            if from_proxy == to_proxy or from_proxy + 1 == to_proxy:
                event.ignore()
                return
            self.row_moved.emit(from_proxy, to_proxy)
            event.acceptProposedAction()

        elif event.mimeData().hasUrls():
            self.urls_dropped.emit(event.mimeData().urls())
            event.acceptProposedAction()
        else:
            event.ignore()


# ── Playlist view widget ──────────────────────────────────────────────────────

class PlaylistView(QWidget):
    play_track_requested    = Signal(list, dict)
    enqueue_track_requested = Signal(list, dict)
    tracks_changed          = Signal(int)   # playlist_id
    liked_changed           = Signal()
    go_to_release           = Signal(str)   # folder_path

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db          = db
        self._playlist_id: int | None = None
        self._name        = ""
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._model = PlaylistModel()

        self._table = _PlaylistTableView()
        self._table.setModel(self._model)
        self._table.setSortingEnabled(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.setShowGrid(False)
        self._table.setStyleSheet(TABLE_STYLE)
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        self._table.row_moved.connect(self._on_row_moved)
        self._table.urls_dropped.connect(self._on_urls_dropped)

        vhdr = self._table.verticalHeader()
        vhdr.setVisible(False)
        vhdr.setDefaultSectionSize(ROW_HEIGHT)
        vhdr.setMinimumSectionSize(ROW_HEIGHT)

        hdr = self._table.horizontalHeader()
        hdr.setStretchLastSection(False)
        hdr.setSectionsMovable(False)
        hdr.setSortIndicatorShown(False)
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

        layout.addWidget(self._table)

        # ── Bottom bar ────────────────────────────────────────────────────
        bb_widget = QWidget()
        bb = QHBoxLayout(bb_widget)
        bb.setContentsMargins(8, 4, 8, 4)
        bb.setSpacing(4)

        del_sc = QShortcut(QKeySequence("Ctrl+Backspace"), self._table)
        del_sc.setContext(Qt.WidgetWithChildrenShortcut)
        del_sc.activated.connect(self._remove_selected)

        self._play_all_btn = QPushButton("▶ Play All")
        self._play_all_btn.setEnabled(False)
        self._play_all_btn.clicked.connect(self._play_all)
        bb.addWidget(self._play_all_btn)

        self._count_label = QLabel("")
        self._count_label.setStyleSheet("font-size: 11px; color: palette(placeholderText);")
        bb.addWidget(self._count_label)
        bb.addStretch()

        self._stats_label = QLabel("")
        self._stats_label.setStyleSheet(
            "font-size: 11px; font-weight: 600; color: palette(placeholderText);"
        )
        bb.addWidget(self._stats_label)
        layout.addWidget(bb_widget)

    # ── Load / refresh ────────────────────────────────────────────────────

    def load(self, playlist_id: int, name: str) -> None:
        self._playlist_id = playlist_id
        self._name        = name
        self._refresh_model()

    def refresh(self) -> None:
        if self._playlist_id is not None:
            self._refresh_model()

    def _refresh_model(self):
        rows = []
        for r in self._db.get_playlist_tracks(self._playlist_id):
            row = dict(r) | {"is_liked": self._db.is_track_liked(r["path"])}
            release = self._db.get_release_by_path(row.get("folder_path", ""))
            row["catalog_number"] = (dict(release).get("catalog_number") or "") if release else ""
            rows.append(row)
        self._model.load(rows)
        n = len(rows)
        self._count_label.setText(self._name)
        total_s = sum(r.get("duration_ms", 0) for r in rows) // 1000
        mins, secs = divmod(total_s, 60)
        self._stats_label.setText(
            f"{n} tracks,  {mins} min {secs:02d} sec" if n else ""
        )
        self._play_all_btn.setEnabled(n > 0)

    # ── Selection ─────────────────────────────────────────────────────────

    def _selected_row(self) -> dict | None:
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            return None
        return self._model.get_row(indexes[0].row())

    def _selected_rows(self) -> list[dict]:
        rows = []
        for idx in self._table.selectionModel().selectedRows():
            r = self._model.get_row(idx.row())
            if r:
                rows.append(r)
        return rows

    # ── Actions ───────────────────────────────────────────────────────────

    def _on_double_click(self, index: QModelIndex):
        row = self._model.get_row(index.row())
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
        rows = [r for r in self._model._rows if Path(r["path"]).is_file()]
        if not rows:
            return
        release_row = {"folder_path": "", "title": self._name, "artist": "", "catalog_number": ""}
        self.play_track_requested.emit([r["path"] for r in rows], release_row)

    def _enqueue_selected(self):
        rows = self._selected_rows()
        paths = [r["path"] for r in rows if Path(r["path"]).is_file()]
        if paths:
            first = rows[0]
            release_row = {"folder_path": first["folder_path"],
                           "title": first["album"], "artist": first["artist"],
                           "catalog_number": first.get("catalog_number", "")}
            self.enqueue_track_requested.emit(paths, release_row)

    def _toggle_like_at(self, model_index: QModelIndex):
        row = self._model.get_row(model_index.row())
        if not row:
            return
        if row.get("is_liked"):
            self._db.unlike_track(row["path"])
        else:
            self._like_row(row)
        self._refresh_model()
        self.liked_changed.emit()

    def _like_row(self, row: dict):
        from src.ui.player_engine import _read_full_tags
        artist, title, album, duration_ms = _read_full_tags(row["path"])
        if not album:
            album = row.get("album", "")
        self._db.like_track(
            row["path"], artist or row.get("artist", ""), title or row.get("title", ""),
            album, row.get("folder_path", ""), duration_ms or row.get("duration_ms", 0),
        )

    def _remove_selected(self):
        if self._playlist_id is None:
            return
        for row in self._selected_rows():
            self._db.remove_track_from_playlist(self._playlist_id, row["path"])
        self._refresh_model()
        self.tracks_changed.emit(self._playlist_id)

    def _on_row_moved(self, from_row: int, to_row: int):
        self._model.move_row(from_row, to_row)
        if self._playlist_id is not None:
            self._db.reorder_playlist_tracks(self._playlist_id, self._model.paths())

    def _on_urls_dropped(self, urls: list):
        if self._playlist_id is None:
            return
        from src.ui.player_engine import _read_full_tags
        added = False
        for url in urls:
            local = url.toLocalFile()
            if not local:
                continue
            p = Path(local)
            if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS:
                artist, title, album, duration_ms = _read_full_tags(str(p))
                self._db.add_track_to_playlist(
                    self._playlist_id, str(p), artist, title,
                    album, str(p.parent), duration_ms,
                )
                added = True
            elif p.is_dir():
                for f in sorted(p.iterdir()):
                    if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS:
                        artist, title, album, duration_ms = _read_full_tags(str(f))
                        self._db.add_track_to_playlist(
                            self._playlist_id, str(f), artist, title,
                            album, str(p), duration_ms,
                        )
                        added = True
        if added:
            self._refresh_model()
            self.tracks_changed.emit(self._playlist_id)

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
        is_liked  = self._db is not None and self._db.is_track_liked(row["path"])
        menu = QMenu(self)
        act_play    = menu.addAction("Play Now")
        act_enqueue = menu.addAction("Add to Queue")
        act_play.setEnabled(available)
        act_enqueue.setEnabled(available)
        menu.addSeparator()
        act_like       = menu.addAction("Unlike" if is_liked else "Like")
        act_go_release = menu.addAction("Go to Release")
        act_go_release.setEnabled(bool(row.get("folder_path")))
        menu.addSeparator()
        act_remove = menu.addAction("Remove from Playlist")
        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen == act_play:
            self._play_selected()
        elif chosen == act_enqueue:
            self._enqueue_selected()
        elif chosen == act_like:
            if is_liked:
                self._db.unlike_track(row["path"])
            else:
                self._like_row(row)
            self.liked_changed.emit()
        elif chosen == act_go_release:
            self.go_to_release.emit(row["folder_path"])
        elif chosen == act_remove:
            self._remove_selected()
