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
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.player_engine import _audio_paths, _read_track_tags
from src.utils import fmt_ms as _fmt_ms

_MAX_ARTIST = 20
_MAX_TITLE  = 33

_ROW_H = 22


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


class TracklistPopup(QDialog):
    play_track    = Signal(list, dict)
    enqueue_track = Signal(list, dict)
    liked_changed = Signal()

    def __init__(self, release_row: dict, db=None, parent=None):
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        self._db          = db
        self._release_row = release_row
        self._album       = release_row.get("title", "")
        self._folder_path = release_row.get("folder_path", "")

        artist = release_row.get("artist", "")
        title  = release_row.get("title", "")
        self.setWindowTitle(f"{artist} – {title}" if artist else title)

        paths: list[str] = _audio_paths(release_row["folder_path"])
        if not paths and db is not None and release_row.get("is_multi_disc"):
            for disc in db.get_disc_entries(release_row["folder_path"]):
                paths += _audio_paths(disc["folder_path"])

        self._paths  = paths
        self._tracks = [_read_track_tags(p) for p in paths]

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Header ───────────────────────────────────────────────────────────
        hdr_text = (
            f"{_trunc(artist, 40)}  —  {_trunc(title, 50)}"
            if artist else _trunc(title, 60)
        )
        total_ms   = sum(ms for _, _, ms in self._tracks)
        total_s    = total_ms // 1000
        mins, secs = divmod(total_s, 60)
        n = len(self._tracks)

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

        # ── Track list ────────────────────────────────────────────────────────
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
                padding: 0px;
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

        self._like_buttons: list[QPushButton] = []

        for i, (path, (track_artist, track_title, ms)) in enumerate(
            zip(paths, self._tracks), 1
        ):
            item = QListWidgetItem()
            item.setSizeHint(item.sizeHint().__class__(-1, _ROW_H))
            self._lw.addItem(item)

            row_w = QWidget()
            row_w.setStyleSheet("background: transparent;")
            rl = QHBoxLayout(row_w)
            rl.setContentsMargins(4, 0, 4, 0)
            rl.setSpacing(0)

            art = _trunc(track_artist, _MAX_ARTIST).ljust(_MAX_ARTIST)
            ttl = _trunc(track_title,  _MAX_TITLE).ljust(_MAX_TITLE)
            info_lbl = QLabel(f"{i:>2}  {art} - {ttl}  {_fmt_ms(ms)}")
            info_lbl.setFont(mono)
            info_lbl.setStyleSheet("background: transparent; border: none; padding: 1px 0;")

            like_btn = QPushButton()
            like_btn.setCheckable(True)
            like_btn.setFixedSize(20, 20)
            like_btn.setStyleSheet("""
                QPushButton {
                    border: none; background: transparent;
                    font-size: 13px; padding: 0; color: palette(placeholderText);
                }
                QPushButton:checked { color: #e0405a; }
                QPushButton:hover   { color: palette(buttonText); }
                QPushButton:checked:hover { color: #e0405a; }
            """)
            is_liked = db is not None and db.is_track_liked(path)
            like_btn.setText("♥" if is_liked else "♡")
            like_btn.setChecked(is_liked)
            like_btn.setToolTip("Like / Unlike")

            def _make_toggle(p, artist_=track_artist, title_=track_title, btn=like_btn):
                def _toggle(checked: bool):
                    btn.setText("♥" if checked else "♡")
                    if self._db is None:
                        return
                    if checked:
                        _, _, dur = _read_track_tags(p)
                        self._db.like_track(
                            p, artist_, title_, self._album,
                            self._folder_path, dur,
                        )
                    else:
                        self._db.unlike_track(p)
                    self.liked_changed.emit()
                return _toggle

            like_btn.toggled.connect(_make_toggle(path))
            self._like_buttons.append(like_btn)

            rl.addWidget(info_lbl, 1)
            rl.addWidget(like_btn)
            self._lw.setItemWidget(item, row_w)

        if not self._tracks:
            item = QListWidgetItem("  No audio files found")
            self._lw.addItem(item)

        self._lw.itemDoubleClicked.connect(self._on_double_click)
        self._lw.customContextMenuRequested.connect(self._on_context_menu)

        QShortcut(QKeySequence.StandardKey.SelectAll, self._lw).activated.connect(
            self._lw.selectAll
        )

        visible = min(max(len(self._tracks), 1), 20)
        self._lw.setFixedHeight(visible * _ROW_H + 6)
        self._lw.setMinimumWidth(560)

        layout.addWidget(self._lw)
        self.adjustSize()
        self.setFixedSize(self.sizeHint())

    def sync_like(self, path: str, liked: bool) -> None:
        """Update the like button for *path* without touching the database."""
        try:
            idx = self._paths.index(path)
        except ValueError:
            return
        btn = self._like_buttons[idx]
        btn.blockSignals(True)
        btn.setChecked(liked)
        btn.setText("♥" if liked else "♡")
        btn.blockSignals(False)

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
