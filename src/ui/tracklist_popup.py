import json
from pathlib import Path

from PySide6.QtCore import Qt, QByteArray, QEvent, QMimeData, QPoint, QRect, QSize, QTimer, QUrl, Signal
from PySide6.QtGui import QCursor, QDrag, QFont, QFontMetrics, QKeySequence, QPainter, QShortcut
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
    QSizePolicy,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from src.ui.player_engine import _audio_paths, _duration_from_file, _read_track_tags
from src.utils import fmt_ms as _fmt_ms
from src.utils.cue import find_cue_for_folder, parse_cue

_ROW_H = 22


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


class _ElidedLabel(QLabel):
    """QLabel that elides its text with '…' when narrower than the full text.

    Elision is computed in paintEvent using the widget's actual width at draw time,
    so it is immune to the QListWidget item-geometry timer race on Windows where
    WM_PAINT can arrive before the delayed doItemsLayout() timer fires.
    """

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(0)

    def sizeHint(self):
        fm = self.fontMetrics()
        return QSize(fm.horizontalAdvance(self._full_text), super().sizeHint().height())

    def minimumSizeHint(self):
        return QSize(0, super().minimumSizeHint().height())

    def paintEvent(self, event):
        w = self.width()
        if w <= 0:
            return
        painter = QPainter(self)
        elided = self.fontMetrics().elidedText(self._full_text, Qt.TextElideMode.ElideRight, w)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setFont(self.font())
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)


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
        # Maps each _ElidedLabel → its full (un-elided) text so the eventFilter can
        # show a tooltip via QToolTip.showText() when text is actually clipped.
        self._label_tooltips: dict = {}
        max_artist_w = 0  # widest artist name in pixels
        max_title_w  = 0  # widest title name in pixels

        # Fixed-width column sizes based on the actual monospace font.
        num_w = fm.horizontalAdvance(f"{len(self._tracks):>2}  ")
        sep_w = fm.horizontalAdvance(" - ")
        dur_w = fm.horizontalAdvance("  99:99")

        for i, (path, (track_artist, track_title, ms)) in enumerate(
            zip(self._paths, self._tracks), 1
        ):
            item = QListWidgetItem()
            item.setSizeHint(item.sizeHint().__class__(-1, _ROW_H))
            self._lw.addItem(item)

            row_w = QWidget()
            row_w.setAutoFillBackground(False)
            rl = QHBoxLayout(row_w)
            rl.setContentsMargins(4, 0, 4, 0)
            rl.setSpacing(0)

            # Track number (fixed width, right-aligned)
            num_lbl = QLabel(f"{i:>2} ")
            num_lbl.setFont(mono)
            num_lbl.setFixedWidth(num_w)
            num_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

            # Artist — elides when the dialog is narrow, full text via tooltip
            art_lbl = _ElidedLabel(track_artist)
            art_lbl.setFont(mono)
            art_lbl.installEventFilter(self)
            self._label_tooltips[art_lbl] = track_artist

            # Separator
            sep_lbl = QLabel(" - ")
            sep_lbl.setFont(mono)
            sep_lbl.setFixedWidth(sep_w)

            # Title — same adaptive behaviour as artist
            ttl_lbl = _ElidedLabel(track_title)
            ttl_lbl.setFont(mono)
            ttl_lbl.installEventFilter(self)
            self._label_tooltips[ttl_lbl] = track_title

            # Duration (fixed width, right-aligned)
            dur_lbl = QLabel(f"  {_fmt_ms(ms)}")
            dur_lbl.setFont(mono)
            dur_lbl.setFixedWidth(dur_w)
            dur_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

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

            rl.addWidget(num_lbl)
            rl.addWidget(art_lbl, 1)
            rl.addWidget(sep_lbl)
            rl.addWidget(ttl_lbl, 1)
            rl.addWidget(dur_lbl)
            rl.addWidget(like_btn)
            self._lw.setItemWidget(item, row_w)

            max_artist_w = max(max_artist_w, fm.horizontalAdvance(track_artist))
            max_title_w  = max(max_title_w,  fm.horizontalAdvance(track_title))

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
        # Compute preferred width from actual pixel widths of all track names.
        # Fixed overhead: num + sep + dur + like-btn + row margins + scrollbar + safety.
        fixed_overhead = num_w + sep_w + dur_w + 20 + 8 + 17 + 40
        needed_w = max(560, min(900, fixed_overhead + max_artist_w + max_title_w))
        # adjustSize() under-estimates width (QListWidget.sizeHint ignores custom item
        # widget widths); resize the dialog explicitly afterwards.
        self.adjustSize()
        if self.width() < needed_w:
            self.resize(needed_w, self.height())
        # The doItemsLayout() timer started by setItemWidget() calls reads the
        # viewport width when it fires -- which may be before the dialog layout
        # has applied _lw's correct width.  Pre-sizing _lw here ensures the timer
        # sees the right viewport width whenever it fires.
        self._lw.resize(needed_w, self._lw.height())
        self.setMinimumSize(self.width(), self.height())

    def showEvent(self, event: QEvent) -> None:
        super().showEvent(event)
        # By the time this 0-ms timer fires the dialog layout has activated and
        # _lw.viewport() has its final width.  Directly set each item widget's
        # geometry (the same calculation Qt uses in updateEditorGeometries on
        # resize) so the first visible frame is already correct on Windows.
        QTimer.singleShot(0, self._fix_item_geometry)

    def _fix_item_geometry(self) -> None:
        vp = self._lw.viewport()
        w = vp.width()
        if w <= 0:
            return
        for i in range(self._lw.count()):
            widget = self._lw.itemWidget(self._lw.item(i))
            if widget is not None:
                widget.setGeometry(QRect(0, i * _ROW_H, w, _ROW_H))

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
        # Per-label tooltip: intercept ToolTip events on _ElidedLabel widgets before
        # they reach Qt's default tooltip path.  QToolTip.showText() always uses Qt's
        # own renderer (QTipLabel), which respects the app-level QToolTip stylesheet
        # set in theme.py — bypassing the native Windows tooltip that ignores it and
        # renders with a black background when the parent widget has any stylesheet.
        if event.type() == QEvent.Type.ToolTip and obj in self._label_tooltips:
            full_text = self._label_tooltips[obj]
            if obj.fontMetrics().horizontalAdvance(full_text) > obj.width():
                QToolTip.showText(QCursor.pos(), full_text, obj)
            else:
                QToolTip.hideText()
            return True

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
