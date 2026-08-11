from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from src.ui.player_engine import _audio_paths, _read_track_tags
from src.utils import fmt_ms as _fmt_ms

_MAX_ARTIST = 22
_MAX_TITLE  = 35


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


class TracklistPopup(QDialog):
    play_track    = Signal(list, dict)
    enqueue_track = Signal(list, dict)

    def __init__(self, release_row: dict, db=None, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        artist = release_row.get("artist", "")
        title  = release_row.get("title", "")
        self.setWindowTitle(f"{artist} – {title}" if artist else title)

        paths: list[str] = _audio_paths(release_row["folder_path"])
        if not paths and db is not None and release_row.get("is_multi_disc"):
            for disc in db.get_disc_entries(release_row["folder_path"]):
                paths += _audio_paths(disc["folder_path"])

        self._release_row = release_row
        self._paths = paths
        tracks = [_read_track_tags(p) for p in paths]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        hdr_text = (
            f"{_trunc(artist, 40)}  —  {_trunc(title, 50)}"
            if artist else _trunc(title, 60)
        )

        total_ms = sum(ms for _, _, ms in tracks)
        total_s  = total_ms // 1000
        mins, secs = divmod(total_s, 60)
        n = len(tracks)

        hdr_widget = QWidget()
        hdr_widget.setStyleSheet("border-bottom: 1px solid palette(mid);")
        hdr_layout = QHBoxLayout(hdr_widget)
        hdr_layout.setContentsMargins(8, 6, 8, 5)

        title_lbl = QLabel(hdr_text)
        title_lbl.setStyleSheet("font-size: 12px; font-weight: 600; border: none;")
        hdr_layout.addWidget(title_lbl, 1)

        stats_lbl = QLabel(f"{n} {'track' if n == 1 else 'tracks'},  {mins} min {secs:02d} sec")
        stats_lbl.setStyleSheet("font-size: 11px; border: none;")
        hdr_layout.addWidget(stats_lbl, 0)

        layout.addWidget(hdr_widget)

        self._lw = QListWidget()
        self._lw.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._lw.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._lw.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._lw.setAlternatingRowColors(True)
        self._lw.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._lw.setStyleSheet("""
            QListWidget {
                border: none;
                background: palette(base);
                outline: none;
            }
            QListWidget::item {
                padding: 1px 8px;
            }
            QListWidget::item:selected {
                background: #3875d7;
                color: white;
            }
            QListWidget::item:alternate {
                background: palette(alternateBase);
            }
            QListWidget::item:selected:alternate {
                background: #3875d7;
            }
        """)

        mono = QFont("Menlo")
        if not mono.exactMatch():
            mono = QFont("Courier New")
        mono.setPointSize(11)
        self._lw.setFont(mono)

        for i, (track_artist, track_title, ms) in enumerate(tracks, 1):
            art = _trunc(track_artist, _MAX_ARTIST).ljust(_MAX_ARTIST)
            ttl = _trunc(track_title,  _MAX_TITLE).ljust(_MAX_TITLE)
            self._lw.addItem(QListWidgetItem(f"{i:>2}  {art} - {ttl}  {_fmt_ms(ms)}"))

        if not tracks:
            self._lw.addItem(QListWidgetItem("  No audio files found"))

        self._lw.itemDoubleClicked.connect(self._on_double_click)
        self._lw.customContextMenuRequested.connect(self._on_context_menu)

        QShortcut(QKeySequence.StandardKey.SelectAll, self._lw).activated.connect(
            self._lw.selectAll
        )

        row_h   = 18
        visible = min(max(len(tracks), 1), 20)
        self._lw.setFixedHeight(visible * row_h + 6)
        self._lw.setMinimumWidth(530)

        layout.addWidget(self._lw)
        self.adjustSize()
        self.setFixedSize(self.sizeHint())

    def _selected_paths(self) -> list[str]:
        return [
            self._paths[self._lw.row(item)]
            for item in self._lw.selectedItems()
            if 0 <= self._lw.row(item) < len(self._paths)
        ]

    def _on_double_click(self, item: QListWidgetItem):
        idx = self._lw.row(item)
        if 0 <= idx < len(self._paths):
            self.play_track.emit([self._paths[idx]], self._release_row)

    def _on_context_menu(self, pos):
        if self._lw.itemAt(pos) is None:
            return
        paths = self._selected_paths()
        if not paths:
            return
        menu = QMenu(self)
        act_play    = menu.addAction("Play Now")
        act_enqueue = menu.addAction("Add to Queue")
        chosen = menu.exec(self._lw.viewport().mapToGlobal(pos))
        if chosen == act_play:
            self.play_track.emit(paths, self._release_row)
        elif chosen == act_enqueue:
            self.enqueue_track.emit(paths, self._release_row)
