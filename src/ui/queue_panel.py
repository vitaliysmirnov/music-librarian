from pathlib import Path

from PySide6.QtCore import QSize, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QFrame, QHBoxLayout, QLabel,
    QListWidget, QListWidgetItem, QPushButton,
    QSizePolicy, QVBoxLayout, QWidget,
)

from src.ui.player_engine import PlayerEngine, QueueTrack

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

_ITEM_H     = 24
_LINE_COLOR = QColor("#3875d7")


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
    """QListWidget with Spotify-style drop indicator and reliable move semantics."""

    move_requested = Signal(int, int)  # from_idx, to_idx

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
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

    def dragMoveEvent(self, event):
        self._insert_row = self._insert_row_at(event.position().toPoint())
        self._update_drop_line(self._insert_row)
        event.accept()

    def dragLeaveEvent(self, event):
        self._insert_row = -1
        self._drop_line.clear()
        self._drop_line.hide()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._drop_line.clear()
        self._drop_line.hide()

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


class QueuePanel(QFrame):
    def __init__(self, engine: PlayerEngine, parent=None):
        super().__init__(parent)
        self._engine = engine
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

        title = QLabel("Queue")
        title.setObjectName("title_lbl")
        layout.addWidget(title)

        self._list = _QueueList()
        self._list.setSpacing(0)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.itemDoubleClicked.connect(self._on_double_click)
        self._list.move_requested.connect(self._on_move_requested)
        layout.addWidget(self._list)

    # ── Refresh ───────────────────────────────────────────────────────────

    def _refresh(self):
        self._list.clear()
        queue = self._engine.queue
        cur   = self._engine.current_track_idx

        if not queue:
            item = QListWidgetItem("Queue is empty")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self._list.addItem(item)
            self.setFixedHeight(100)
            return

        for i, track in enumerate(queue):
            item = QListWidgetItem()
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled |
                Qt.ItemFlag.ItemIsSelectable |
                Qt.ItemFlag.ItemIsDragEnabled |
                Qt.ItemFlag.ItemIsDropEnabled
            )
            item.setSizeHint(QSize(self._list.width() - 4, _ITEM_H))
            self._list.addItem(item)
            self._list.setItemWidget(item, self._make_row(item, track, i == cur))

        n = len(queue)
        self.setFixedHeight(min(400, 50 + n * (_ITEM_H + 1) + 8))

    def _make_row(self, item: QListWidgetItem, track: QueueTrack, is_current: bool) -> QWidget:
        if track.artist and track.title:
            text = f"{track.artist}  —  {track.title}"
        elif track.title:
            text = track.title
        else:
            text = Path(track.path).stem

        w  = QWidget()
        hl = QHBoxLayout(w)
        hl.setContentsMargins(14, 0, 14, 0)
        hl.setSpacing(3)

        lbl = QLabel(text)
        lbl.setStyleSheet(
            "font-size: 11px; font-weight: 700;" if is_current else "font-size: 11px;"
        )
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        hl.addWidget(lbl)

        rm = QPushButton("✕")
        rm.setStyleSheet(_REMOVE_STYLE)
        rm.setToolTip("Remove")
        rm.clicked.connect(lambda _c, it=item: self._remove_item(it))
        hl.addWidget(rm)

        return w

    # ── Interactions ──────────────────────────────────────────────────────

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

    # ── Show / hide ───────────────────────────────────────────────────────

    def showEvent(self, event):
        self._refresh()
        super().showEvent(event)
