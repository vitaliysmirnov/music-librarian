from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from src.ui.player_engine import PlayerEngine

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


class QueuePanel(QFrame):
    def __init__(self, engine: PlayerEngine, parent=None):
        super().__init__(parent)
        self._engine = engine
        self.setObjectName("QueuePanel")
        self.setStyleSheet(_PANEL_STYLE)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedWidth(310)
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

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._list = QWidget()
        self._list_lay = QVBoxLayout(self._list)
        self._list_lay.setContentsMargins(8, 4, 8, 4)
        self._list_lay.setSpacing(2)
        self._list_lay.addStretch()

        self._scroll.setWidget(self._list)
        layout.addWidget(self._scroll)

    # ── Refresh ───────────────────────────────────────────────────────────

    def _refresh(self):
        # Remove all items except the trailing stretch
        while self._list_lay.count() > 1:
            item = self._list_lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        queue = self._engine.queue
        current_idx = self._engine.current_release_idx

        if not queue:
            lbl = QLabel("Queue is empty")
            lbl.setStyleSheet(
                "color: palette(placeholderText); font-size: 12px; padding: 12px 14px;"
            )
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._list_lay.insertWidget(0, lbl)
        else:
            for i, qrel in enumerate(queue):
                self._list_lay.insertWidget(i, self._make_item(i, qrel, i == current_idx))

        # Auto-resize height to content (max 400)
        n = max(1, len(queue))
        self.setFixedHeight(min(400, 50 + n * 38 + 8))

    def _make_item(self, idx: int, qrel, is_current: bool) -> QWidget:
        row = qrel.row
        item = QWidget()
        if is_current:
            item.setStyleSheet(
                "background: rgba(56,117,215,15%); border-radius: 4px;"
            )
        hl = QHBoxLayout(item)
        hl.setContentsMargins(6, 3, 6, 3)
        hl.setSpacing(6)

        indicator = QLabel("▶" if is_current else "")
        indicator.setFixedWidth(12)
        indicator.setStyleSheet(
            "color: #3875d7; font-size: 9px;" if is_current else ""
        )
        hl.addWidget(indicator)

        artist = (row.get("artist") or "").strip()
        title  = (row.get("title")  or "").strip()
        n_tr   = len(qrel.audio_paths)
        text   = f"{artist} — {title}" if artist and title else (
            title or row.get("folder_path", "")
        )
        lbl = QLabel(text)
        lbl.setStyleSheet("font-size: 12px;")
        lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        lbl.setToolTip(f"{n_tr} track{'s' if n_tr != 1 else ''}")
        hl.addWidget(lbl)

        rm = QPushButton("✕")
        rm.setStyleSheet(_REMOVE_STYLE)
        rm.setToolTip("Remove from queue")
        rm.clicked.connect(lambda _c, i=idx: self._engine.remove_from_queue(i))
        hl.addWidget(rm)

        return item

    # ── Show / hide ───────────────────────────────────────────────────────

    def showEvent(self, event):
        self._refresh()
        super().showEvent(event)
