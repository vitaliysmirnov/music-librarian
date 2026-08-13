import json
from pathlib import Path

from PySide6.QtCore import QMimeData, QPoint, QRect, QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QMenu, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from src.ui.player_engine import PlayerEngine, QueueTrack
from src.ui.style import ElidedLabel
from src.utils.audio import AUDIO_EXTENSIONS

_PANEL_STYLE = """
QueuePanel {
    background: palette(window);
    border: 1px solid palette(mid);
    border-radius: 8px;
}
QueuePanel QLabel#title_lbl {
    font-size: 13px;
    font-weight: 600;
    padding: 10px 14px 4px 14px;
    color: palette(windowText);
}
QueuePanel QLabel#footer_lbl {
    font-size: 11px;
    font-weight: 600;
    color: palette(placeholderText);
    padding: 4px 14px 2px 14px;
}
QueuePanel QListWidget {
    background: transparent;
    border: none;
    outline: none;
}
QueuePanel QListWidget::item {
    border-radius: 4px;
}
QueuePanel QListWidget::item:selected {
    background: transparent;
}
"""

_CLEAR_STYLE = """
QPushButton {
    border: none;
    background: transparent;
    color: palette(placeholderText);
    font-size: 11px;
    padding: 1px 4px;
    border-radius: 3px;
}
QPushButton:hover {
    color: white;
    background: #cc3333;
}
"""

_REMOVE_STYLE = """
QPushButton {
    border: none;
    background: transparent;
    color: palette(placeholderText);
    font-size: 11px;
    padding: 0;
    min-width: 18px;
    max-width: 18px;
    min-height: 18px;
    max-height: 18px;
    border-radius: 9px;
}
QPushButton:hover {
    color: white;
    background: #cc3333;
}
"""

_ITEM_H     = 40
_LINE_COLOR = QColor("#3875d7")


def _fmt_dur(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


class _DropLine(QWidget):
    """Transparent overlay that draws the Spotify-style insertion line."""

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
        x1, x2, y, r = 14, self.width() - 14, self._y, 3
        p.setPen(QPen(_LINE_COLOR, 2))
        p.drawLine(x1, y, x2, y)
        p.setBrush(_LINE_COLOR)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(x1 - r, y - r, r * 2, r * 2)
        p.drawEllipse(x2 - r, y - r, r * 2, r * 2)


class _QueueList(QListWidget):
    """QListWidget with Spotify-style drop indicator and reliable move semantics.

    Accepts both internal reorder drags and external URL drops (from Finder
    or the releases table).  External drops are forwarded to `enqueue_cb`.
    """

    move_requested = Signal(int, int)  # from_idx, to_idx

    def __init__(self, enqueue_cb=None, parent=None):
        super().__init__(parent)
        self._enqueue_cb = enqueue_cb
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._insert_row = -1
        self._drop_line  = _DropLine(self.viewport())
        self._drop_line.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._drop_line.setGeometry(self.viewport().rect())

    # ── Drag helpers ──────────────────────────────────────────────────────

    def _insert_row_at(self, pos) -> int:
        """Row index before which the dragged item will be inserted."""
        target = self.indexAt(pos)
        if not target.isValid():
            return self.count()
        rect = self.visualItemRect(self.item(target.row()))
        return target.row() if pos.y() < rect.center().y() else target.row() + 1

    def _update_drop_line(self, insert_row: int):
        n = self.count()
        if n == 0:
            self._drop_line.clear()
            return
        if insert_row < n:
            y = self.visualItemRect(self.item(insert_row)).top()
        else:
            y = self.visualItemRect(self.item(n - 1)).bottom()
        self._drop_line.setGeometry(self.viewport().rect())
        self._drop_line.set_y(y)
        self._drop_line.show()
        self._drop_line.raise_()

    # ── Qt drag events ────────────────────────────────────────────────────

    def startDrag(self, supported_actions):
        selected = self.selectedItems()
        if not selected:
            super().startDrag(supported_actions)
            return

        text = selected[0].data(Qt.ItemDataRole.UserRole) or ""
        if not text:
            super().startDrag(supported_actions)
            return

        font = QFont()
        font.setPixelSize(10)
        fm      = QFontMetrics(font)
        text_w  = fm.horizontalAdvance(text)
        text_h  = fm.height()
        gap     = 12          # distance between cursor and text
        pad_y   = 4
        pix_w   = gap + text_w + 4
        pix_h   = text_h + pad_y * 2

        pixmap = QPixmap(pix_w, pix_h)
        pixmap.fill(Qt.GlobalColor.transparent)

        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(0.60)
        p.setFont(font)
        p.setPen(self.palette().color(self.palette().ColorRole.WindowText))
        p.drawText(QRect(gap, pad_y, text_w + 4, text_h), Qt.AlignmentFlag.AlignLeft, text)
        p.end()

        drag = QDrag(self)
        drag.setMimeData(self.model().mimeData(self.selectedIndexes()))
        drag.setPixmap(pixmap)
        drag.setHotSpot(QPoint(0, pix_h // 2))
        drag.exec(supported_actions)

    def dragEnterEvent(self, event):
        if event.source() is self:
            event.accept()
        elif event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.source() is self:
            self._insert_row = self._insert_row_at(event.position().toPoint())
            self._update_drop_line(self._insert_row)
            event.accept()
        elif event.mimeData().hasUrls():
            self._drop_line.clear()
            self._drop_line.hide()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._insert_row = -1
        self._drop_line.clear()
        self._drop_line.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._drop_line.clear()
        self._drop_line.hide()

        if event.source() is self:
            # Internal reorder
            selected = self.selectedItems()
            if not selected:
                event.ignore()
                return
            from_row = self.row(selected[0])
            raw_to   = self._insert_row if self._insert_row >= 0 \
                else self._insert_row_at(event.position().toPoint())
            to_row   = raw_to - 1 if raw_to > from_row else raw_to
            self._insert_row = -1
            event.accept()
            if from_row != to_row:
                self.move_requested.emit(from_row, to_row)
        elif event.mimeData().hasUrls() and self._enqueue_cb:
            self._enqueue_cb(event.mimeData())
            event.acceptProposedAction()
        else:
            event.ignore()


class QueuePanel(QFrame):
    go_to_release             = Signal(str)        # folder_path
    add_to_playlist_requested = Signal(int, int)   # track_idx, playlist_id

    def __init__(self, engine: PlayerEngine, parent=None):
        super().__init__(parent)
        self._engine    = engine
        self._playlists: list[dict] = []
        self.setObjectName("QueuePanel")
        self.setStyleSheet(_PANEL_STYLE)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedWidth(322)
        self._setup_ui()
        self._engine.queue_changed.connect(self._refresh)
        self._engine.track_changed.connect(lambda *_: self._refresh())
        self.hide()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 8)
        layout.setSpacing(0)

        header = QWidget()
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 14, 0)
        hl.setSpacing(0)

        title = QLabel("Queue")
        title.setObjectName("title_lbl")
        title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        hl.addWidget(title)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.setStyleSheet(_CLEAR_STYLE)
        self._clear_btn.setToolTip("Clear queue")
        self._clear_btn.clicked.connect(self._engine.clear_queue)
        hl.addWidget(self._clear_btn, 0, Qt.AlignmentFlag.AlignVCenter)

        layout.addWidget(header)

        self._list = _QueueList(enqueue_cb=self._enqueue_from_urls)
        self._list.setSpacing(0)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.itemDoubleClicked.connect(self._on_double_click)
        self._list.customContextMenuRequested.connect(self._on_context_menu)
        self._list.move_requested.connect(self._on_move_requested)
        layout.addWidget(self._list)

        self._footer_lbl = QLabel("")
        self._footer_lbl.setObjectName("footer_lbl")
        layout.addWidget(self._footer_lbl)

    # ── Refresh ───────────────────────────────────────────────────────────

    def _refresh(self):
        scroll = self._list.verticalScrollBar()
        saved_scroll = scroll.value() if scroll else 0

        self._list.clear()
        queue = self._engine.queue
        cur   = self._engine.current_track_idx

        if not queue:
            item = QListWidgetItem("Queue is empty")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(item)
            self._footer_lbl.setText("")
            self.setFixedHeight(100)
            return

        for i, track in enumerate(queue):
            if track.artist and track.title:
                label_text = f"{track.artist}  —  {track.title}"
            elif track.title:
                label_text = track.title
            else:
                label_text = Path(track.path).stem

            item = QListWidgetItem()
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled |
                Qt.ItemFlag.ItemIsSelectable |
                Qt.ItemFlag.ItemIsDragEnabled |
                Qt.ItemFlag.ItemIsDropEnabled
            )
            item.setData(Qt.ItemDataRole.UserRole, label_text)
            item.setSizeHint(QSize(self._list.width() - 4, _ITEM_H))
            self._list.addItem(item)
            self._list.setItemWidget(item, self._make_row(item, track, i == cur))

        n = len(queue)
        total_s = sum(t.duration_ms for t in queue) // 1000
        mins, secs = divmod(total_s, 60)
        self._footer_lbl.setText(f"{n} tracks,  {mins} min {secs:02d} sec")
        self.setFixedHeight(min(500, 50 + n * (_ITEM_H + 1) + 26 + 8))

        if saved_scroll and scroll:
            scroll.setValue(saved_scroll)

    def _make_row(self, item: QListWidgetItem, track: "QueueTrack", is_current: bool) -> QWidget:
        w  = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(14, 3, 14, 3)
        hl.setSpacing(3)

        info = QWidget()
        vl = QVBoxLayout(info)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)

        artist_text = track.artist or ""
        title_text  = track.title or Path(track.path).stem

        if artist_text:
            artist_lbl = ElidedLabel(artist_text)
            artist_lbl.setStyleSheet(
                "font-size: 10px; font-weight: 700; color: palette(placeholderText);"
                if is_current else
                "font-size: 10px; color: palette(placeholderText);"
            )
            vl.addWidget(artist_lbl)

        title_lbl = ElidedLabel(title_text)
        title_lbl.setStyleSheet(
            "font-size: 11px; font-weight: 700;" if is_current else "font-size: 11px;"
        )
        vl.addWidget(title_lbl)

        if not artist_text:
            vl.setContentsMargins(0, 4, 0, 4)

        info.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        hl.addWidget(info)

        if track.duration_ms:
            dur_lbl = QLabel(_fmt_dur(track.duration_ms))
            dur_lbl.setStyleSheet("font-size: 10px; color: palette(placeholderText);")
            dur_lbl.setContentsMargins(4, 0, 6, 0)
            hl.addWidget(dur_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        rm = QPushButton("✕")
        rm.setStyleSheet(_REMOVE_STYLE)
        rm.setToolTip("Remove")
        rm.clicked.connect(lambda _c, it=item: self._remove_item(it))
        hl.addWidget(rm, 0, Qt.AlignmentFlag.AlignVCenter)

        return w

    # ── Interactions ──────────────────────────────────────────────────────

    def _on_context_menu(self, pos):
        item = self._list.itemAt(pos)
        if item is None:
            return
        idx = self._list.row(item)
        if not (0 <= idx < len(self._engine.queue)):
            return
        track = self._engine.queue[idx]
        folder_path = (track.row or {}).get("folder_path") or ""
        if not folder_path:
            folder_path = str(Path(track.path).parent)

        menu = QMenu(self)
        act_go = menu.addAction("Go to Release")

        pl_actions: dict = {}
        if track.is_library and self._playlists:
            menu.addSeparator()
            pl_menu = menu.addMenu("Add to Playlist")
            for pl in self._playlists:
                act = pl_menu.addAction(pl["name"])
                pl_actions[act] = pl["id"]

        chosen = menu.exec(self._list.viewport().mapToGlobal(pos))
        if chosen == act_go:
            self.go_to_release.emit(folder_path)
        elif chosen in pl_actions:
            self.add_to_playlist_requested.emit(idx, pl_actions[chosen])

    def _enqueue_from_urls(self, mime: QMimeData):
        """Enqueue dropped URLs: folders add their full audio content,
        individual audio files are added as-is (not the whole folder)."""
        raw_meta = mime.data("application/x-release-meta")
        path_meta: dict[str, dict] = {}
        if raw_meta and not raw_meta.isEmpty():
            try:
                path_meta = json.loads(bytes(raw_meta).decode())
            except Exception:
                pass

        seen_folders: set[str] = set()
        seen_tracks:  list[tuple[str, dict | None]] = []
        seen_track_paths: set[str] = set()

        for url in mime.urls():
            local = url.toLocalFile()
            if not local:
                continue
            p = Path(local)
            if p.is_dir():
                fp = str(p)
                if fp not in seen_folders:
                    seen_folders.add(fp)
                    self._engine.enqueue_release({"folder_path": fp})
            elif p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS:
                tp = str(p)
                if tp not in seen_track_paths:
                    seen_track_paths.add(tp)
                    seen_tracks.append((tp, path_meta.get(tp)))

        if seen_tracks:
            for tp, meta in seen_tracks:
                self._engine.enqueue_tracks([tp], release_row=meta)

    def _on_double_click(self, item: QListWidgetItem):
        idx = self._list.row(item)
        if 0 <= idx < len(self._engine.queue):
            self._engine.play_track_at(idx)

    def _remove_item(self, item: QListWidgetItem):
        idx = self._list.row(item)
        if idx >= 0:
            self._engine.remove_track(idx)

    def _on_move_requested(self, from_idx: int, to_idx: int):
        self._engine.move_track(from_idx, to_idx)
        QTimer.singleShot(0, self._refresh)

    def set_playlists(self, playlists: list[dict]):
        self._playlists = playlists

    # ── Show / hide ───────────────────────────────────────────────────────

    def showEvent(self, event):
        self._refresh()
        super().showEvent(event)
