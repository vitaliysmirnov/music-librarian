import json
from pathlib import Path

from PySide6.QtCore import Qt, QByteArray, QEvent, QMimeData, QPoint, QUrl, Signal
from PySide6.QtGui import QCursor, QDrag, QFont, QFontMetrics, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from src.ui.player_engine import _audio_paths, _duration_from_file, _read_track_tags
from src.utils import fmt_ms as _fmt_ms
from src.utils.cue import find_cue_for_folder, parse_cue

_MAX_ARTIST = 20
_MAX_TITLE  = 33

_ROW_H = 22


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def _confirm_add_duplicates(parent, duplicates: list) -> bool:
    """Ask the user whether to add already-present tracks again. Returns True if confirmed."""
    if len(duplicates) == 1:
        _, artist, title, *_ = duplicates[0]
        label = f"«{artist} – {title}»" if artist else f"«{title}»"
        msg = f"{label} is already in this playlist.\n\nAdd it again?"
    else:
        msg = f"{len(duplicates)} tracks are already in this playlist.\n\nAdd them again?"
    return QMessageBox.question(
        parent, "Already in Playlist", msg,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    ) == QMessageBox.StandardButton.Yes


class TracklistPopup(QDialog):
    play_track           = Signal(list, dict)
    enqueue_track        = Signal(list, dict)
    liked_changed        = Signal()
    playlist_track_added = Signal(int)  # playlist_id

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
        self._cue_offsets: list[tuple[int, int]] = [(0, 0)] * len(paths)
        self._is_cue = False

        # Detect single-file CUE album and expand to virtual tracks.
        if len(paths) == 1:
            from pathlib import Path as _Path
            cue_path = find_cue_for_folder(_Path(release_row["folder_path"]))
            if cue_path:
                audio_file, album_artist, _album_title, cue_tracks = parse_cue(cue_path)
                if audio_file and cue_tracks:
                    total_ms = _duration_from_file(str(audio_file))
                    self._paths = [str(audio_file)] * len(cue_tracks)
                    self._tracks = [
                        (t.artist or album_artist, t.title,
                         t.end_ms - t.start_ms if t.end_ms else max(0, total_ms - t.start_ms))
                        for t in cue_tracks
                    ]
                    self._cue_offsets = [(t.start_ms, t.end_ms) for t in cue_tracks]
                    self._is_cue = True

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
        fm = QFontMetrics(mono)

        self._like_buttons: list[QPushButton] = []
        self._tooltips: dict[int, str] = {}  # row → full "artist — title" for truncated rows
        max_row_w = 0  # widest row text in pixels — used to size the dialog correctly

        for i, (path, (track_artist, track_title, ms)) in enumerate(
            zip(self._paths, self._tracks), 1
        ):
            item = QListWidgetItem()
            item.setSizeHint(item.sizeHint().__class__(-1, _ROW_H))
            self._lw.addItem(item)

            row_w = QWidget()
            # setAutoFillBackground(False) makes the widget transparent without
            # setting background:transparent in the stylesheet; the latter causes
            # tooltip popups to render as a black rectangle on Windows.
            row_w.setAutoFillBackground(False)
            rl = QHBoxLayout(row_w)
            rl.setContentsMargins(4, 0, 4, 0)
            rl.setSpacing(0)

            art = _trunc(track_artist, _MAX_ARTIST).ljust(_MAX_ARTIST)
            ttl = _trunc(track_title,  _MAX_TITLE).ljust(_MAX_TITLE)
            label_text = f"{i:>2}  {art} - {ttl}  {_fmt_ms(ms)}"
            info_lbl = QLabel(label_text)
            info_lbl.setFont(mono)
            info_lbl.setStyleSheet("border: none; padding: 1px 0;")
            if len(track_artist) > _MAX_ARTIST or len(track_title) > _MAX_TITLE:
                # Don't call setToolTip() here — on Windows, widget-level tooltips
                # inside a QListWidget custom item widget bypass the stylesheet and
                # render with a black background.  Instead we intercept the propagated
                # ToolTip event in the viewport eventFilter and use QToolTip.showText().
                self._tooltips[i - 1] = f"{track_artist} — {track_title}"
            max_row_w = max(max_row_w, fm.horizontalAdvance(label_text))

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
            track_start_ms, track_end_ms = self._cue_offsets[i - 1]
            is_liked = db is not None and db.is_track_liked(path, track_start_ms)
            like_btn.setText("♥" if is_liked else "♡")
            like_btn.setChecked(is_liked)
            # Tooltip omitted: widget-level tooltips on buttons with
            # background:transparent render as black rectangles on Windows.

            def _make_toggle(p, idx_=i - 1, artist_=track_artist, title_=track_title,
                              dur_=ms, btn=like_btn):
                def _toggle(checked: bool):
                    btn.setText("♥" if checked else "♡")
                    if self._db is None:
                        return
                    s_ms, e_ms = self._cue_offsets[idx_]
                    if checked:
                        self._db.like_track(
                            p, artist_, title_, self._album,
                            self._folder_path, dur_, s_ms, e_ms,
                        )
                    else:
                        self._db.unlike_track(p, s_ms)
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

        self._drag_start_pos: QPoint | None = None
        self._lw.viewport().installEventFilter(self)

        QShortcut(QKeySequence.StandardKey.SelectAll, self._lw).activated.connect(
            self._lw.selectAll
        )

        visible = min(max(len(self._tracks), 1), 20)
        self._lw.setFixedHeight(visible * _ROW_H + 6)

        layout.addWidget(self._lw)
        # QListWidget.sizeHint() ignores setMinimumWidth when items use custom
        # widgets, so adjustSize() alone produces a narrow dialog.  Resize the
        # dialog explicitly after adjustSize() to guarantee the full text fits:
        # +80 covers like-btn (20) + row margins (8) + scrollbar (15-17 on
        # Windows Fusion) + breathing room so text is never clipped.
        self.adjustSize()
        needed_w = max(560, max_row_w + 80)
        if self.width() < needed_w:
            self.resize(needed_w, self.height())
        self.setMinimumSize(self.width(), self.height())

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

    def refresh_likes(self) -> None:
        """Re-read liked state from DB for every track and update buttons."""
        if self._db is None:
            return
        for idx, btn in enumerate(self._like_buttons):
            path = self._paths[idx]
            start_ms = self._cue_offsets[idx][0]
            liked = self._db.is_track_liked(path, start_ms)
            btn.blockSignals(True)
            btn.setChecked(liked)
            btn.setText("♥" if liked else "♡")
            btn.blockSignals(False)

    def eventFilter(self, obj, event):
        if obj is self._lw.viewport():
            t = event.type()
            if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_start_pos = event.pos()
            elif t == QEvent.Type.MouseMove:
                if self._drag_start_pos is not None and (event.buttons() & Qt.MouseButton.LeftButton):
                    if (event.pos() - self._drag_start_pos).manhattanLength() >= QApplication.startDragDistance():
                        start = self._drag_start_pos
                        self._drag_start_pos = None
                        self._exec_drag(start)
                        return True
            elif t == QEvent.Type.MouseButtonRelease:
                self._drag_start_pos = None
            elif t == QEvent.Type.ToolTip:
                # Tooltip events from child widgets (info_lbl, like_btn) propagate
                # here because those widgets have no toolTip set.  Using
                # QToolTip.showText() bypasses the widget-level tooltip mechanism
                # that renders as a black rectangle on Windows (Fusion + custom items).
                vp = self._lw.viewport()
                global_pos = QCursor.pos()
                local_pos = vp.mapFromGlobal(global_pos)
                idx = self._lw.indexAt(local_pos)
                if idx.isValid():
                    tip = self._tooltips.get(idx.row(), "")
                    if tip:
                        QToolTip.showText(global_pos, tip, vp)
                        return True
                QToolTip.hideText()
                return True
        return super().eventFilter(obj, event)

    def _exec_drag(self, press_pos: QPoint):
        item = self._lw.itemAt(press_pos)
        if item is None:
            return
        selected = self._lw.selectedItems()
        if not selected:
            selected = [item]
        indices = [
            self._lw.row(i)
            for i in selected
            if 0 <= self._lw.row(i) < len(self._paths)
        ]
        live_indices = [i for i in indices if Path(self._paths[i]).is_file()]
        if not live_indices:
            return
        urls = [QUrl.fromLocalFile(self._paths[i]) for i in live_indices]
        meta = {
            self._paths[i]: {
                "folder_path":    self._release_row.get("folder_path", ""),
                "title":          self._release_row.get("title", ""),
                "catalog_number": self._release_row.get("catalog_number", ""),
                "artist":         self._release_row.get("artist", ""),
            }
            for i in live_indices
        }
        mime = QMimeData()
        mime.setUrls(urls)
        mime.setData("application/x-release-meta",
                     QByteArray(json.dumps(meta).encode()))
        if self._is_cue:
            cue_meta = []
            for i in live_indices:
                artist, title, dur = self._tracks[i]
                s_ms, e_ms = self._cue_offsets[i]
                cue_meta.append({
                    "path":        self._paths[i],
                    "start_ms":    s_ms,
                    "end_ms":      e_ms,
                    "artist":      artist,
                    "title":       title,
                    "duration_ms": dur,
                    "album":       self._album,
                    "folder_path": self._folder_path,
                })
            mime.setData("application/x-cue-track-meta",
                         QByteArray(json.dumps(cue_meta).encode()))
        drag = QDrag(self._lw)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def _selected_indices(self) -> list[int]:
        return [
            self._lw.row(item)
            for item in self._lw.selectedItems()
            if 0 <= self._lw.row(item) < len(self._paths)
        ]

    def _selected_paths(self) -> list[str]:
        return [self._paths[i] for i in self._selected_indices()]

    def _build_release_row(self, indices: list[int]) -> dict:
        if not self._is_cue:
            return self._release_row
        meta = []
        for i in indices:
            artist, title, dur = self._tracks[i]
            s_ms, e_ms = self._cue_offsets[i]
            meta.append({"start_ms": s_ms, "end_ms": e_ms,
                         "artist": artist, "title": title, "duration_ms": dur})
        rr = dict(self._release_row)
        rr["_track_meta"] = meta
        return rr

    def _on_double_click(self, item: QListWidgetItem):
        idx = self._lw.row(item)
        if 0 <= idx < len(self._paths):
            self.play_track.emit([self._paths[idx]], self._build_release_row([idx]))

    def _on_context_menu(self, pos):
        if self._lw.itemAt(pos) is None:
            return
        indices = self._selected_indices()
        paths   = [self._paths[i] for i in indices]
        if not paths:
            return
        menu = QMenu(self)
        act_play    = menu.addAction("Play Now")
        act_enqueue = menu.addAction("Add to Queue")

        pl_actions: dict = {}
        if self._db is not None:
            playlists = self._db.get_playlists()
            if playlists:
                menu.addSeparator()
                pl_menu = menu.addMenu("Add to Playlist")
                for pl in playlists:
                    act = pl_menu.addAction(pl["name"])
                    pl_actions[act] = pl["id"]

        chosen = menu.exec(self._lw.viewport().mapToGlobal(pos))
        rr = self._build_release_row(indices)
        if chosen == act_play:
            self.play_track.emit(paths, rr)
        elif chosen == act_enqueue:
            self.enqueue_track.emit(paths, rr)
        elif chosen in pl_actions:
            pid = pl_actions[chosen]
            duplicates = []
            for i in indices:
                path = self._paths[i]
                artist, title, ms = self._tracks[i]
                s_ms, e_ms = self._cue_offsets[i]
                added = self._db.add_track_to_playlist(
                    pid, path, artist, title,
                    self._album, self._folder_path, ms, s_ms, e_ms,
                )
                if not added:
                    duplicates.append((path, artist, title, ms, s_ms, e_ms))
            if duplicates and _confirm_add_duplicates(self, duplicates):
                for (path, artist, title, ms, s_ms, e_ms) in duplicates:
                    self._db.add_track_to_playlist_again(
                        pid, path, artist, title,
                        self._album, self._folder_path, ms, s_ms, e_ms,
                    )
            self.playlist_track_added.emit(pid)
