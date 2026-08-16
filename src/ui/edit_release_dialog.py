import json
import unicodedata
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QRect, QEvent, QMimeData, QPoint, QSize, QUrl, QByteArray
from PySide6.QtGui import QPainter, QPen, QColor, QPixmap, QDrag, QFont, QFontMetrics, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView, QAbstractScrollArea, QApplication,
    QDialog, QFormLayout, QFrame, QLineEdit,
    QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QScrollArea, QVBoxLayout, QHBoxLayout,
    QSizePolicy, QFileDialog, QWidget, QPushButton,
)

_ROW_H = 22

from src.database.db import Database
from src.scanner.mask import DEFAULT_MASK, get_custom_tokens
from src.utils import covers as _covers
from src.utils import fmt_ms as _fmt_ms
from src.utils.logger import get_logger

log = get_logger()


def _build_folder_name(fields: dict, mask: str) -> str:
    """Reconstruct folder name from fields using the current mask."""
    result = mask
    for token, value in fields.items():
        if not value:
            result = result.replace(f"[{{{token}}}]", "")
            result = result.replace(f"({{{token}}})", "")
            result = result.replace(f"{{{token}}}", "")
        else:
            result = result.replace(f"{{{token}}}", value)
    while "  " in result:
        result = result.replace("  ", " ")
    return result.strip()


def _same_inode(a: Path, b: Path) -> bool:
    """True if both paths refer to the same filesystem object (handles NFC/NFD aliases on macOS)."""
    try:
        return a.stat().st_ino == b.stat().st_ino
    except OSError:
        return False


def _load_extras(release: dict) -> dict:
    try:
        return json.loads(release.get("extras") or "{}")
    except Exception:
        return {}


class _ElidedLabel(QLabel):
    """QLabel that elides text with '…' and shows a tooltip when clipped."""
    def paintEvent(self, event):
        painter = QPainter(self)
        cr = self.contentsRect()
        elided = self.fontMetrics().elidedText(
            self.text(), Qt.TextElideMode.ElideRight, cr.width()
        )
        new_tip = self.text() if elided != self.text() else ""
        if self.toolTip() != new_tip:
            self.setToolTip(new_tip)
        painter.setPen(self.palette().color(self.foregroundRole()))
        painter.drawText(cr, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, elided)


class _BoundedListWidget(QListWidget):
    """QListWidget whose sizeHint height is capped so enclosing layouts size correctly."""

    def __init__(self, max_h: int, parent=None):
        super().__init__(parent)
        self._max_h = max_h

    def sizeHint(self):
        sh = super().sizeHint()
        return sh.__class__(sh.width(), self._max_h)

    def minimumSizeHint(self):
        sh = super().minimumSizeHint()
        return sh.__class__(sh.width(), min(sh.height(), self._max_h))


class _CoverWidget(QWidget):
    """Square cover art widget. Supports drag-and-drop and click-to-browse."""

    cover_changed = Signal(str)   # emits path to newly selected image file

    _HINT_TEXT = "Drop image here\nor click to browse"
    _BORDER_COLOR = QColor(120, 120, 120)
    _TEXT_COLOR = QColor(140, 140, 140)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self.setAcceptDrops(True)
        self.setMinimumSize(120, 120)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ── Geometry ──────────────────────────────────────────────────────────

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return width

    def sizeHint(self):
        from PySide6.QtCore import QSize
        w = self.width() if self.width() > 0 else 160
        return QSize(w, w)

    # ── Public API ────────────────────────────────────────────────────────

    def set_pixmap(self, pixmap: QPixmap | None):
        self._pixmap = pixmap
        self.update()

    def pixmap_loaded(self) -> bool:
        return self._pixmap is not None

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        painter.fillRect(self.rect(), self.palette().window())

        side = min(self.width(), self.height())
        x = (self.width() - side) // 2
        y = (self.height() - side) // 2
        square = QRect(x, y, side, side)

        if self._pixmap:
            scaled = self._pixmap.scaled(
                side, side,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            px = x + (side - scaled.width()) // 2
            py = y + (side - scaled.height()) // 2
            painter.drawPixmap(px, py, scaled)
        else:
            pen = QPen(self._BORDER_COLOR, 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(square.adjusted(1, 1, -2, -2))
            painter.setPen(self._TEXT_COLOR)
            painter.drawText(square, Qt.AlignmentFlag.AlignCenter, self._HINT_TEXT)

    # ── Interaction ───────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            url = event.mimeData().urls()[0]
            if url.isLocalFile():
                event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self._load_from_path(path)

    def set_browse_root(self, directory: str):
        self._browse_root = directory

    def browse(self):
        self._browse()

    def _browse(self):
        root = getattr(self, "_browse_root", "")
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Cover Image", root,
            "Images (*.jpg *.jpeg *.png *.bmp *.tiff *.tif *.webp)",
        )
        if path:
            self._load_from_path(path)

    def _load_from_path(self, path: str):
        from src.utils.covers import preview_from_file
        pix = preview_from_file(path, 600)
        if pix is None:
            QMessageBox.warning(self, "Error", f"Cannot load image:\n{path}")
            return
        self.set_pixmap(pix)
        self.cover_changed.emit(path)


class EditReleaseDialog(QDialog):
    play_track           = Signal(list, dict)
    enqueue_track        = Signal(list, dict)
    liked_changed        = Signal()
    playlist_track_added = Signal(int)
    folder_renamed       = Signal(str, str)   # (old_folder_path, new_folder_path)

    def __init__(self, db: Database, release: dict, parent=None):
        super().__init__(parent)
        if parent is not None:
            parent.installEventFilter(self)
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.CustomizeWindowHint
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self._db = db
        self._release = release
        self._mask = db.get_setting("folder_mask", DEFAULT_MASK)
        self._extra_tokens = get_custom_tokens(self._mask)
        self._extras_current = _load_extras(release)
        self._is_disc_child = bool(release.get("parent_path"))
        self._cover_source_path: str | None = None
        self._cover_deleted = False

        self._cover_key = release["folder_path"]
        self._extra_edits: dict[str, QLineEdit] = {}

        # Tracklist state
        self._tl_paths:   list[str]              = []
        self._tl_tracks:  list[tuple]            = []
        self._tl_offsets: list[tuple[int, int]]  = []
        self._tl_is_cue   = False
        self._tl_album    = release.get("title", "")
        self._tl_folder   = release.get("folder_path", "")
        self._like_buttons: list[QPushButton]    = []
        self._drag_start_pos: QPoint | None      = None
        self._init_tracklist(release)

        self.setWindowTitle("Release Info")
        self.setMinimumWidth(660)
        self._setup_ui()
        self.resize(660, 480)

    def _init_tracklist(self, release: dict):
        if not release.get("is_available", True):
            stored = self._db.get_release_tracks(release["folder_path"])
            if stored:
                self._tl_paths   = [t["path"] for t in stored]
                self._tl_tracks  = [(t["artist"], t["title"], t["duration_ms"]) for t in stored]
                self._tl_offsets = [(t["start_ms"], t["end_ms"]) for t in stored]
            return

        from src.utils.audio import audio_paths as _audio_paths, duration_from_file as _duration_from_file, read_track_tags as _read_track_tags
        from src.utils.cue import find_cue_for_folder, parse_cue

        paths = _audio_paths(release["folder_path"])
        if not paths and release.get("is_multi_disc"):
            for disc in self._db.get_disc_entries(release["folder_path"]):
                paths += _audio_paths(disc["folder_path"])

        self._tl_paths   = paths
        self._tl_tracks  = [_read_track_tags(p) for p in paths]
        self._tl_offsets = [(0, 0)] * len(paths)

        if len(paths) == 1:
            cue_path = find_cue_for_folder(Path(release["folder_path"]))
            if cue_path:
                audio_file, album_artist, _, cue_tracks = parse_cue(cue_path)
                if audio_file and cue_tracks:
                    total_ms = _duration_from_file(str(audio_file))
                    self._tl_paths   = [str(audio_file)] * len(cue_tracks)
                    self._tl_tracks  = [
                        (t.artist or album_artist, t.title,
                         t.end_ms - t.start_ms if t.end_ms else max(0, total_ms - t.start_ms))
                        for t in cue_tracks
                    ]
                    self._tl_offsets = [(t.start_ms, t.end_ms) for t in cue_tracks]
                    self._tl_is_cue  = True


    def _tl_selected_indices(self) -> list[int]:
        return [
            self._lw.row(item)
            for item in self._lw.selectedItems()
            if 0 <= self._lw.row(item) < len(self._tl_paths)
        ]

    def _tl_build_release_row(self, indices: list[int]) -> dict:
        if not self._tl_is_cue:
            return self._release
        meta = []
        for i in indices:
            artist, title, dur = self._tl_tracks[i]
            s_ms, e_ms = self._tl_offsets[i]
            meta.append({"start_ms": s_ms, "end_ms": e_ms,
                         "artist": artist, "title": title, "duration_ms": dur})
        rr = dict(self._release)
        rr["_track_meta"] = meta
        return rr

    def _tl_on_double_click(self, item: QListWidgetItem):
        if not self._release.get("is_available", True):
            return
        idx = self._lw.row(item)
        if 0 <= idx < len(self._tl_paths):
            self.play_track.emit([self._tl_paths[idx]], self._tl_build_release_row([idx]))

    def _tl_on_context_menu(self, pos):
        if self._lw.itemAt(pos) is None:
            return
        indices = self._tl_selected_indices()
        paths   = [self._tl_paths[i] for i in indices]
        if not paths:
            return
        available = self._release.get("is_available", True)
        menu = QMenu(self)
        act_play    = menu.addAction("Play Now")
        act_play.setEnabled(available)
        act_enqueue = menu.addAction("Add to Queue")
        act_enqueue.setEnabled(available)

        pl_actions: dict = {}
        playlists = self._db.get_playlists()
        if playlists:
            menu.addSeparator()
            pl_menu = menu.addMenu("Add to Playlist")
            for pl in playlists:
                act = pl_menu.addAction(pl["name"])
                pl_actions[act] = pl["id"]

        chosen = menu.exec(self._lw.viewport().mapToGlobal(pos))
        rr = self._tl_build_release_row(indices)
        if chosen == act_play:
            self.play_track.emit(paths, rr)
        elif chosen == act_enqueue:
            self.enqueue_track.emit(paths, rr)
        elif chosen in pl_actions:
            from src.ui.tracklist_popup import _confirm_add_duplicates
            pid = pl_actions[chosen]
            duplicates = []
            for i in indices:
                path = self._tl_paths[i]
                artist, title, ms = self._tl_tracks[i]
                s_ms, e_ms = self._tl_offsets[i]
                added = self._db.add_track_to_playlist(
                    pid, path, artist, title,
                    self._tl_album, self._tl_folder, ms, s_ms, e_ms,
                )
                if not added:
                    duplicates.append((path, artist, title, ms, s_ms, e_ms))
            if duplicates and _confirm_add_duplicates(self, duplicates):
                for (path, artist, title, ms, s_ms, e_ms) in duplicates:
                    self._db.add_track_to_playlist_again(
                        pid, path, artist, title,
                        self._tl_album, self._tl_folder, ms, s_ms, e_ms,
                    )
            self.playlist_track_added.emit(pid)

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() == QEvent.Type.Close:
            self.close()
        if hasattr(self, "_lw") and obj is self._lw.viewport():
            t = event.type()
            if t == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._drag_start_pos = event.pos()
            elif t == QEvent.Type.MouseMove:
                if self._drag_start_pos is not None and (event.buttons() & Qt.MouseButton.LeftButton):
                    if (event.pos() - self._drag_start_pos).manhattanLength() >= QApplication.startDragDistance():
                        start = self._drag_start_pos
                        self._drag_start_pos = None
                        self._tl_exec_drag(start)
                        return True
            elif t == QEvent.Type.MouseButtonRelease:
                self._drag_start_pos = None
        return super().eventFilter(obj, event)

    def _tl_exec_drag(self, press_pos: QPoint):
        item = self._lw.itemAt(press_pos)
        if item is None:
            return
        selected = self._lw.selectedItems()
        if not selected:
            selected = [item]
        indices = [
            self._lw.row(i) for i in selected
            if 0 <= self._lw.row(i) < len(self._tl_paths)
        ]
        live = [i for i in indices if Path(self._tl_paths[i]).is_file()]
        if not live:
            return
        urls = [QUrl.fromLocalFile(self._tl_paths[i]) for i in live]
        meta = {
            self._tl_paths[i]: {
                "folder_path":    self._release.get("folder_path", ""),
                "title":          self._release.get("title", ""),
                "catalog_number": self._release.get("catalog_number", ""),
                "artist":         self._release.get("artist", ""),
            }
            for i in live
        }
        mime = QMimeData()
        mime.setUrls(urls)
        mime.setData("application/x-release-meta", QByteArray(json.dumps(meta).encode()))
        if self._tl_is_cue:
            cue_meta = []
            for i in live:
                artist, title, dur = self._tl_tracks[i]
                s_ms, e_ms = self._tl_offsets[i]
                cue_meta.append({"path": self._tl_paths[i], "start_ms": s_ms, "end_ms": e_ms,
                                  "artist": artist, "title": title, "duration_ms": dur,
                                  "album": self._tl_album, "folder_path": self._tl_folder})
            mime.setData("application/x-cue-track-meta", QByteArray(json.dumps(cue_meta).encode()))
        drag = QDrag(self._lw)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def sync_like(self, path: str, liked: bool) -> None:
        try:
            idx = self._tl_paths.index(path)
        except ValueError:
            return
        btn = self._like_buttons[idx]
        btn.blockSignals(True)
        btn.setChecked(liked)
        btn.setText("♥" if liked else "♡")
        btn.blockSignals(False)

    def refresh_likes(self) -> None:
        for idx, btn in enumerate(self._like_buttons):
            path     = self._tl_paths[idx]
            start_ms = self._tl_offsets[idx][0]
            liked    = self._db.is_track_liked(path, start_ms)
            btn.blockSignals(True)
            btn.setChecked(liked)
            btn.setText("♥" if liked else "♡")
            btn.blockSignals(False)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── Scrollable content area ────────────────────────────────────────
        scroll_w = QWidget()
        scroll_layout = QVBoxLayout(scroll_w)
        scroll_layout.setContentsMargins(12, 12, 12, 8)
        scroll_layout.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidget(scroll_w)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet("QScrollBar:vertical { width: 12px; }")
        root.addWidget(scroll)

        # ── Main row: cover | form ─────────────────────────────────────────
        row = QHBoxLayout()
        row.setSpacing(12)

        self._cover = _CoverWidget()
        self._cover.cover_changed.connect(self._on_cover_changed)
        existing_own = _covers.load_cover_for_widget(self._db.covers_dir, self._cover_key, 600)
        if existing_own:
            self._cover.set_pixmap(existing_own)
        self._cover.set_browse_root(self._release.get("folder_path") or "")

        cover_col = QVBoxLayout()
        cover_col.setSpacing(4)
        cover_col.addWidget(self._cover, 0, Qt.AlignmentFlag.AlignTop)

        cover_btns = QHBoxLayout()
        cover_btns.setSpacing(6)
        self._btn_set_cover = QPushButton("Set Cover")
        self._btn_remove_cover = QPushButton("Remove Cover")
        self._btn_set_cover.clicked.connect(self._cover.browse)
        self._btn_remove_cover.clicked.connect(self._on_remove_cover)
        self._btn_remove_cover.setEnabled(existing_own is not None)
        cover_btns.addWidget(self._btn_set_cover)
        cover_btns.addWidget(self._btn_remove_cover)
        cover_col.addLayout(cover_btns)
        cover_col.addStretch()

        row.addLayout(cover_col, stretch=1)

        # Form column
        form_col = QVBoxLayout()
        form_col.setSpacing(4)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._artist = QLineEdit(self._release["artist"])
        self._year_recorded = QLineEdit(self._release["year_recorded"])
        self._year_recorded.setMaxLength(4)
        self._year_recorded.setFixedWidth(70)
        self._title = QLineEdit(self._release["title"])
        self._catalog = QLineEdit(self._release["catalog_number"] or "")
        self._media = QLineEdit(self._release["media"] or "")
        self._media.setFixedWidth(100)
        self._year_released = QLineEdit(self._release["year_released"] or "")
        self._year_released.setMaxLength(4)
        self._year_released.setFixedWidth(70)

        for _le in (self._artist, self._year_recorded, self._title,
                    self._catalog, self._media, self._year_released):
            _le.setCursorPosition(0)

        form.addRow("Artist:", self._artist)
        form.addRow("Rec. Year:", self._year_recorded)
        form.addRow("Title:", self._title)
        form.addRow("Cat. No.:", self._catalog)
        form.addRow("Media:", self._media)
        form.addRow("Rel. Year:", self._year_released)

        if not self._release.get("is_multi_disc"):
            disc_num = self._release.get("disc_number") or 1
            self._disc_number: QLineEdit | None = QLineEdit(str(disc_num))
            self._disc_number.setMaxLength(2)
            self._disc_number.setFixedWidth(50)
            form.addRow("Disc #:", self._disc_number)
        else:
            self._disc_number = None

        for token in self._extra_tokens:
            edit = QLineEdit(self._extras_current.get(token, ""))
            edit.setCursorPosition(0)
            form.addRow(token.replace("_", " ").title() + ":", edit)
            self._extra_edits[token] = edit

        if not self._release.get("is_available", True):
            _fields = [self._artist, self._year_recorded, self._title,
                       self._catalog, self._media, self._year_released]
            if self._disc_number is not None:
                _fields.append(self._disc_number)
            _fields.extend(self._extra_edits.values())
            for _le in _fields:
                _le.setReadOnly(True)

        form_col.addLayout(form)

        form_col.addStretch()

        row.addLayout(form_col, stretch=3)
        scroll_layout.addLayout(row)

        # ── Stats (shown in bottom bar) ───────────────────────────────────
        n = len(self._tl_tracks)
        total_ms = sum(t[2] for t in self._tl_tracks)
        mins, secs = divmod(total_ms // 1000, 60)
        stats_lbl = QLabel(f"{n} {'track' if n == 1 else 'tracks'},  {mins} min {secs:02d} sec")
        stats_lbl.setStyleSheet(
            "font-size: 11px; color: palette(placeholderText);"
        )

        # ── Tracklist (no internal scroll — expands to all rows) ──────────
        self._lw = QListWidget()
        self._lw.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._lw.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._lw.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._lw.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._lw.setSizeAdjustPolicy(QAbstractScrollArea.SizeAdjustPolicy.AdjustToContents)
        self._lw.setAlternatingRowColors(True)
        self._lw.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._lw.setStyleSheet("""
            QListWidget {
                border: 1px solid palette(mid);
                background: palette(base);
                outline: none;
            }
            QListWidget::item { padding: 0px; }
            QListWidget::item:selected { background: #3875d7; color: white; }
            QListWidget::item:alternate { background: palette(alternateBase); }
            QListWidget::item:selected:alternate { background: #3875d7; }
        """)

        mono = QFont("Menlo")
        if not mono.exactMatch():
            mono = QFont("Courier New")
        mono.setPointSize(11)
        _dur_w = QFontMetrics(mono).horizontalAdvance("00:00:00") + 4

        for i, (path, (track_artist, track_title, ms)) in enumerate(
            zip(self._tl_paths, self._tl_tracks), 1
        ):
            item = QListWidgetItem()
            item.setSizeHint(QSize(1, _ROW_H))
            self._lw.addItem(item)

            row_w = QWidget()
            row_w.setFixedHeight(_ROW_H)
            row_w.setStyleSheet("background: transparent;")
            rl = QHBoxLayout(row_w)
            rl.setContentsMargins(4, 0, 2, 0)
            rl.setSpacing(3)

            _lbl_style = "background: transparent; border: none; padding: 0;"

            num_lbl = QLabel(f"{i:>2}")
            num_lbl.setFont(mono)
            num_lbl.setStyleSheet(_lbl_style)

            art_lbl = _ElidedLabel(track_artist)
            art_lbl.setFont(mono)
            art_lbl.setStyleSheet(_lbl_style)
            art_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

            ttl_lbl = _ElidedLabel(track_title)
            ttl_lbl.setFont(mono)
            ttl_lbl.setStyleSheet(_lbl_style)
            ttl_lbl.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

            dur_lbl = QLabel(_fmt_ms(ms))
            dur_lbl.setFont(mono)
            dur_lbl.setFixedWidth(_dur_w)
            dur_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            dur_lbl.setStyleSheet(_lbl_style)

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
            track_start_ms, track_end_ms = self._tl_offsets[i - 1]
            is_liked = self._db.is_track_liked(path, track_start_ms)
            like_btn.setText("♥" if is_liked else "♡")
            like_btn.setChecked(is_liked)
            like_btn.setToolTip("Like / Unlike")

            def _make_toggle(p, idx_=i - 1, artist_=track_artist, title_=track_title,
                              dur_=ms, btn=like_btn):
                def _toggle(checked: bool):
                    btn.setText("♥" if checked else "♡")
                    s_ms, e_ms = self._tl_offsets[idx_]
                    if checked:
                        self._db.like_track(
                            p, artist_, title_, self._tl_album,
                            self._tl_folder, dur_, s_ms, e_ms,
                        )
                    else:
                        self._db.unlike_track(p, s_ms)
                    self.liked_changed.emit()
                return _toggle

            like_btn.toggled.connect(_make_toggle(path))
            self._like_buttons.append(like_btn)

            rl.addWidget(num_lbl, 0)
            rl.addSpacing(8)
            rl.addWidget(art_lbl, 1)
            rl.addWidget(ttl_lbl, 1)
            rl.addWidget(dur_lbl, 0)
            rl.addSpacing(6)
            rl.addWidget(like_btn, 0)
            self._lw.setItemWidget(item, row_w)

        if not self._tl_tracks:
            no_audio = QListWidgetItem("  No audio files found")
            no_audio.setSizeHint(QSize(1, _ROW_H))
            no_audio.setFlags(Qt.ItemFlag.NoItemFlags)
            self._lw.addItem(no_audio)

        self._lw.itemDoubleClicked.connect(self._tl_on_double_click)
        self._lw.customContextMenuRequested.connect(self._tl_on_context_menu)
        self._lw.viewport().installEventFilter(self)
        QShortcut(QKeySequence.StandardKey.SelectAll, self._lw).activated.connect(
            self._lw.selectAll
        )

        # ── Tracklist column headers ───────────────────────────────────────
        # Left: 1px border + 4px item margin = 5; Right: 1px border + 2px item margin = 3
        _hs = "color: palette(placeholderText); background: transparent; border: none; padding: 0;"
        hdr_w = QWidget()
        hdr_w.setFixedHeight(18)
        hl = QHBoxLayout(hdr_w)
        hl.setContentsMargins(5, 0, 3, 0)
        hl.setSpacing(3)

        num_h = QLabel(" #")
        num_h.setFont(mono)
        num_h.setStyleSheet(_hs)

        art_h = QLabel("Artist")
        art_h.setStyleSheet(_hs)
        art_h.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        ttl_h = QLabel("Track")
        ttl_h.setStyleSheet(_hs)
        ttl_h.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)

        dur_h = QLabel("Time")
        dur_h.setFont(mono)
        dur_h.setFixedWidth(_dur_w)
        dur_h.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        dur_h.setStyleSheet(_hs)

        lkd_h = QLabel("♥")
        lkd_h.setFixedWidth(20)
        lkd_h.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lkd_h.setStyleSheet(_hs)

        hl.addWidget(num_h, 0)
        hl.addSpacing(8)
        hl.addWidget(art_h, 1)
        hl.addWidget(ttl_h, 1)
        hl.addWidget(dur_h, 0)
        hl.addSpacing(6)
        hl.addWidget(lkd_h, 0)

        tl_section = QVBoxLayout()
        tl_section.setSpacing(0)
        tl_section.setContentsMargins(0, 0, 0, 0)
        tl_section.addWidget(hdr_w)
        tl_section.addWidget(self._lw)
        scroll_layout.addLayout(tl_section)
        scroll_layout.addStretch()

        # ── Separator + bottom bar (fixed, not scrolling) ─────────────────
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: palette(mid);")
        root.addWidget(sep)

        btn_cancel = QPushButton("Cancel")
        btn_apply  = QPushButton("Apply")
        btn_save   = QPushButton("Save")
        btn_save.setDefault(True)
        btn_cancel.clicked.connect(self.reject)
        btn_apply.clicked.connect(self._on_apply)
        btn_save.clicked.connect(self._on_save)

        bottom_bar = QHBoxLayout()
        bottom_bar.setContentsMargins(12, 6, 12, 12)
        bottom_bar.addWidget(stats_lbl)
        bottom_bar.addStretch()
        bottom_bar.setSpacing(8)
        bottom_bar.addWidget(btn_cancel)
        bottom_bar.addWidget(btn_apply)
        bottom_bar.addWidget(btn_save)
        root.addLayout(bottom_bar)

    def showEvent(self, event):
        super().showEvent(event)
        self.setFocus()

    def _on_cover_changed(self, path: str):
        self._cover_source_path = path
        self._cover_deleted = False
        self._btn_remove_cover.setEnabled(True)

    def _on_remove_cover(self):
        self._cover.set_pixmap(None)
        self._cover_source_path = None
        self._cover_deleted = True
        self._btn_remove_cover.setEnabled(False)

    def _make_new_folder_name(self, artist: str, year_recorded: str, title: str,
                              catalog: str | None, media: str | None,
                              year_released: str | None) -> str:
        """Build and NFC-normalise the new folder name from current field values."""
        return unicodedata.normalize("NFC", _build_folder_name(
            {
                "artist": artist, "year_recorded": year_recorded, "title": title,
                "catalog_number": catalog or "", "media": media or "",
                "year_released": year_released or "",
                **{t: self._extra_edits[t].text().strip() for t in self._extra_edits},
            },
            self._mask,
        ))

    def _all_fields(self) -> dict:
        fields = {
            "artist": self._artist.text().strip(),
            "year_recorded": self._year_recorded.text().strip(),
            "title": self._title.text().strip(),
            "catalog_number": self._catalog.text().strip(),
            "media": self._media.text().strip(),
            "year_released": self._year_released.text().strip(),
        }
        for token, edit in self._extra_edits.items():
            fields[token] = edit.text().strip()
        return fields

    def _save_cover(self, new_cover_key: str):
        """Persist cover after folder rename (key may have changed)."""
        if self._cover_deleted:
            _covers.delete_cover(self._db.covers_dir, self._cover_key)
            if new_cover_key != self._cover_key:
                _covers.delete_cover(self._db.covers_dir, new_cover_key)
        elif self._cover_source_path:
            _covers.save_cover(self._db.covers_dir, new_cover_key, self._cover_source_path)
        elif new_cover_key != self._cover_key:
            # Folder renamed — rename the stored cover to match new key
            _covers.rename_cover(self._db.covers_dir, self._cover_key, new_cover_key)

    def _rename_disc_children_covers(self, old_parent: str, new_parent: str,
                                      skip_child: str | None = None):
        """Rename cover files for disc children after their parent folder is renamed."""
        if old_parent == new_parent:
            return
        for child in self._db.get_disc_entries(new_parent):
            child_name = Path(child["folder_path"]).name
            old_child_path = str(Path(old_parent) / child_name)
            if skip_child and child["folder_path"] == skip_child:
                continue
            _covers.rename_cover(self._db.covers_dir, old_child_path, child["folder_path"])

    def _maybe_apply_cover_to_discs(self, parent_path: str):
        """For multi-disc containers: ask whether to propagate the cover to disc children."""
        if not self._release.get("is_multi_disc") or not self._cover_source_path:
            return
        children = self._db.get_disc_entries(parent_path)
        if not children:
            return

        reply = QMessageBox.question(
            self, "Cover Art",
            "Apply this cover to all discs in this release too?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,  # default: No — safer choice
        )
        if reply == QMessageBox.StandardButton.Yes:
            for child in children:
                _covers.save_cover(
                    self._db.covers_dir, child["folder_path"], self._cover_source_path
                )

    def _on_apply(self):
        self._do_save(close=False)

    def _on_save(self):
        self._do_save(close=True)

    def _do_save(self, close: bool):
        fields = self._all_fields()
        artist = fields["artist"]
        year_recorded = fields["year_recorded"]
        title = fields["title"]
        catalog = fields["catalog_number"] or None
        media = fields["media"] or None
        year_released = fields["year_released"] or None

        if not artist or not year_recorded or not title:
            QMessageBox.warning(self, "Error", "Artist, recording year, and title are required.")
            return

        if len(year_recorded) != 4 or not year_recorded.isdigit():
            QMessageBox.warning(self, "Error", "Recording year must be a 4-digit number.")
            return

        if year_released and (len(year_released) != 4 or not year_released.isdigit()):
            QMessageBox.warning(self, "Error", "Release year must be a 4-digit number.")
            return

        extras = {token: fields[token] for token in self._extra_tokens if fields.get(token)}
        extras_json = json.dumps(extras)

        disc_number = 1
        if self._disc_number is not None:
            try:
                disc_number = max(1, int(self._disc_number.text().strip() or "1"))
            except ValueError:
                pass

        if self._is_disc_child:
            ok = self._save_disc_child(artist, year_recorded, title, catalog, media,
                                       year_released, extras_json, disc_number)
        else:
            ok = self._save_regular(artist, year_recorded, title, catalog, media,
                                    year_released, extras_json, disc_number)

        if ok and close:
            self.accept()

    def _save_regular(self, artist, year_recorded, title, catalog, media,
                      year_released, extras_json, disc_number) -> bool:
        old_path = Path(self._release["folder_path"])
        new_name = self._make_new_folder_name(artist, year_recorded, title, catalog, media, year_released)
        new_path = old_path.parent / new_name

        if self._release["is_available"]:
            same = new_path.exists() and _same_inode(old_path, new_path)
            if not same and unicodedata.normalize("NFC", old_path.name) != new_name:
                if new_path.exists():
                    QMessageBox.warning(self, "Error",
                                        f"A folder with that name already exists:\n{new_name}")
                    return False
                try:
                    old_path.rename(new_path)
                    log.info("Folder renamed: %s → %s", old_path, new_path)
                except OSError as e:
                    QMessageBox.warning(self, "Rename Error", str(e))
                    return False
        elif not self._release["is_available"]:
            new_path = old_path

        found = self._db.rename_release(
            str(old_path), str(new_path),
            artist=artist, year_recorded=year_recorded, title=title,
            catalog_number=catalog, media=media, year_released=year_released,
            extras=extras_json, disc_number=disc_number,
        )
        log.debug("Dialog rename_release: old=%r new=%r found=%s", str(old_path), str(new_path), found)
        if self._release.get("is_multi_disc"):
            self._db.update_disc_children_metadata(
                str(new_path),
                artist=artist, year_recorded=year_recorded, title=title,
                catalog_number=catalog, media=media, year_released=year_released,
                extras=extras_json,
            )
        self._save_cover(str(new_path))
        self._rename_disc_children_covers(str(old_path), str(new_path))
        self._maybe_apply_cover_to_discs(str(new_path))
        if str(old_path) != str(new_path):
            self.folder_renamed.emit(str(old_path), str(new_path))
        self._release = dict(self._release)
        self._release["folder_path"] = str(new_path)
        self._cover_key = str(new_path)
        return True

    def _save_disc_child(self, artist, year_recorded, title, catalog, media,
                         year_released, extras_json, disc_number) -> bool:
        parent_path_str = self._release["parent_path"]
        parent_row = self._db.get_release_by_path(parent_path_str)
        if not parent_row:
            return True

        old_parent = Path(parent_path_str)
        new_parent_name = self._make_new_folder_name(artist, year_recorded, title, catalog, media, year_released)
        new_parent = old_parent.parent / new_parent_name

        if parent_row["is_available"]:
            same = new_parent.exists() and _same_inode(old_parent, new_parent)
            if not same and unicodedata.normalize("NFC", old_parent.name) != new_parent_name:
                if new_parent.exists():
                    QMessageBox.warning(self, "Error",
                                        f"A folder with that name already exists:\n{new_parent_name}")
                    return False
                try:
                    old_parent.rename(new_parent)
                    log.info("Folder renamed: %s → %s", old_parent, new_parent)
                except OSError as e:
                    QMessageBox.warning(self, "Rename Error", str(e))
                    return False
        else:
            new_parent = old_parent

        self._db.rename_release(
            str(old_parent), str(new_parent),
            artist=artist, year_recorded=year_recorded, title=title,
            catalog_number=catalog, media=media, year_released=year_released,
            extras=extras_json, disc_number=0,
        )
        self._db.update_disc_children_metadata(
            str(new_parent),
            artist=artist, year_recorded=year_recorded, title=title,
            catalog_number=catalog, media=media, year_released=year_released,
            extras=extras_json,
        )
        child_name = Path(self._release["folder_path"]).name
        new_child_path = str(new_parent / child_name)
        self._db.rename_release(new_child_path, new_child_path, disc_number=disc_number)

        self._save_cover(new_child_path)
        self._rename_disc_children_covers(str(old_parent), str(new_parent),
                                          skip_child=new_child_path)
        if str(old_parent) != str(new_parent):
            self.folder_renamed.emit(str(old_parent), str(new_parent))
        self._release = dict(self._release)
        self._release["parent_path"] = str(new_parent)
        self._release["folder_path"] = new_child_path
        self._cover_key = new_child_path
        return True
