import html
import re
import urllib.parse
from pathlib import Path

from PySide6.QtCore import Qt, QEvent, Signal
from PySide6.QtGui import QMouseEvent, QPalette
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QMenu, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from src.ui.player_engine import PlayerEngine
from src.utils import fmt_ms as _fmt_ms


class _LinkLabel(QLabel):
    """QLabel that shows PointingHandCursor only over actual link text.

    QLabel defaults to NoTextInteraction, which means QTextControl never
    tracks link hovers and linkHovered never fires. Setting
    LinksAccessibleByMouse makes QTextControl emit linkHovered correctly
    from the very first hover, without needing a focus round-trip.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._has_links = False
        self._over_link = False
        self.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.linkHovered.connect(lambda url: setattr(self, '_over_link', bool(url)))

    def set_has_links(self, has_links: bool) -> None:
        self._has_links = has_links
        self._over_link = False
        if not has_links:
            self.unsetCursor()
        else:
            self.repaint()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        super().mouseMoveEvent(event)
        if self._has_links:
            if self._over_link:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.unsetCursor()


_SIDE_W    = 130   # fixed width of transport block and right block
_GROUP_MAX = 780   # controls group never grows wider than this

# ── Independent vertical positions (centre-y from bar top, px) ──────────
_BAR_H        =  58   # total bar height
_TRANSPORT_CY =  30   # ⏮ ▶ ⏭
_VOL_CY       =  30   # 🔊 (volume slider)
_QUEUE_CY     =  30   # ☰  (queue button, independent)
_PROGRESS_CY  =  47   # progress bar

_BAR_STYLE = """
PlayerBar {
    background: palette(window);
    border-bottom: 1px solid palette(mid);
}

/* ── Transport ── */
PlayerBar QPushButton#btn_prev,
PlayerBar QPushButton#btn_play,
PlayerBar QPushButton#btn_next {
    border: none;
    background: transparent;
    color: palette(buttonText);
    border-radius: 5px;
    padding: 2px 5px;
}
PlayerBar QPushButton#btn_prev { font-size: 13px; }
PlayerBar QPushButton#btn_next { font-size: 13px; }
PlayerBar QPushButton#btn_play { font-size: 19px; }
PlayerBar QPushButton#btn_prev:hover:!disabled,
PlayerBar QPushButton#btn_next:hover:!disabled,
PlayerBar QPushButton#btn_play:hover:!disabled  { background: rgba(128,128,128,45); }
PlayerBar QPushButton#btn_prev:pressed:!disabled,
PlayerBar QPushButton#btn_next:pressed:!disabled,
PlayerBar QPushButton#btn_play:pressed:!disabled { background: rgba(128,128,128,75); }
PlayerBar QPushButton#btn_prev:disabled,
PlayerBar QPushButton#btn_next:disabled,
PlayerBar QPushButton#btn_play:disabled { color: palette(mid); }

/* ── Track label (Artist — Track Name) ── */
PlayerBar QLabel#track_lbl {
    font-size: 12px;
    font-weight: 600;
    color: palette(windowText);
}

/* ── Meta label (Release — Cat. No.) ── */
PlayerBar QLabel#meta_lbl {
    font-size: 11px;
    color: palette(placeholderText);
}

/* ── Time labels ── */
PlayerBar QLabel#time_lbl {
    font-size: 10px;
    color: palette(placeholderText);
    min-width: 30px;
    max-width: 38px;
}

/* ── Progress slider ── */
PlayerBar QSlider#progress_slider::groove:horizontal {
    height: 3px;
    background: rgba(128,128,128,90);
    border-radius: 2px;
}
PlayerBar QSlider#progress_slider::sub-page:horizontal {
    background: #3875d7;
    border-radius: 2px;
}
PlayerBar QSlider#progress_slider::handle:horizontal {
    width: 10px;
    height: 10px;
    margin: -4px 0;
    border-radius: 5px;
    background: palette(buttonText);
}
PlayerBar QSlider#progress_slider:disabled::groove:horizontal {
    background: rgba(128,128,128,90);
}
PlayerBar QSlider#progress_slider:disabled::sub-page:horizontal {
    background: transparent;
}
PlayerBar QSlider#progress_slider:disabled::handle:horizontal {
    background: transparent;
}

/* ── Volume slider ── */
PlayerBar QSlider#vol_slider::groove:horizontal {
    height: 3px;
    background: rgba(128,128,128,90);
    border-radius: 2px;
}
PlayerBar QSlider#vol_slider::sub-page:horizontal {
    background: rgba(160,160,160,200);
    border-radius: 2px;
}
PlayerBar QSlider#vol_slider::handle:horizontal {
    width: 9px;
    height: 9px;
    margin: -3px 0;
    border-radius: 5px;
    background: palette(buttonText);
}

/* ── Like button ── */
PlayerBar QPushButton#btn_like {
    border: none;
    background: transparent;
    color: palette(placeholderText);
    font-size: 15px;
    padding: 0;
    border-radius: 5px;
}
PlayerBar QPushButton#btn_like:hover:!disabled   { background: rgba(128,128,128,45); color: palette(buttonText); }
PlayerBar QPushButton#btn_like:checked           { color: #e0405a; }
PlayerBar QPushButton#btn_like:checked:hover     { background: rgba(128,128,128,45); color: #e0405a; }
PlayerBar QPushButton#btn_like:disabled          { color: palette(mid); }

/* ── Queue button ── */
PlayerBar QPushButton#btn_queue {
    border: none;
    background: transparent;
    color: palette(placeholderText);
    font-size: 14px;
    padding: 0 6px 3px 6px;
    border-radius: 5px;
}
PlayerBar QPushButton#btn_queue:hover   { background: rgba(128,128,128,45); color: palette(buttonText); }
PlayerBar QPushButton#btn_queue:checked { color: #3875d7; }
"""



class PlayerBar(QWidget):
    queue_toggled           = Signal()
    navigate_requested      = Signal(str, str)   # kind, value
    like_toggled            = Signal(str, dict, bool)  # path, row, is_liked
    go_to_release_requested = Signal(str)        # folder_path
    add_to_playlist_requested = Signal(int, str, str, str, str, str, int)  # playlist_id, path, artist, title, album, folder_path, duration_ms

    def __init__(self, engine: PlayerEngine, parent=None):
        super().__init__(parent)
        self._engine         = engine
        self._seeking        = False
        self._duration_ms    = 0
        self._current_row: dict | None = None
        self._current_path   = ""
        self._current_artist = ""
        self._current_title  = ""
        self._playlists: list[dict] = []
        self._is_library_track = False
        self.setFixedHeight(_BAR_H)
        self.setStyleSheet(_BAR_STYLE)
        self._setup_ui()
        self._connect_engine()
        self._update_enabled(False)

    # ── Build ─────────────────────────────────────────────────────────────

    def _setup_ui(self):
        # No layout on self — all control blocks are positioned via resizeEvent.

        # ── Info row (full-width, top) ────────────────────────────────────
        self._track_lbl = _LinkLabel("—")
        self._track_lbl.setObjectName("track_lbl")
        self._track_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._track_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._track_lbl.setOpenExternalLinks(False)
        self._track_lbl.linkActivated.connect(self._on_link)

        self._meta_lbl = _LinkLabel("")
        self._meta_lbl.setObjectName("meta_lbl")
        self._meta_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._meta_lbl.setTextFormat(Qt.TextFormat.RichText)
        self._meta_lbl.setOpenExternalLinks(False)
        self._meta_lbl.linkActivated.connect(self._on_link)

        for lbl in (self._track_lbl, self._meta_lbl):
            lbl.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            lbl.customContextMenuRequested.connect(self._on_info_context_menu)

        self._info_row = QWidget(self)
        il = QVBoxLayout(self._info_row)
        il.setContentsMargins(12, 3, 12, 1)
        il.setSpacing(1)
        il.addWidget(self._track_lbl)
        il.addWidget(self._meta_lbl)

        # ── Transport block (⏮ ▶ ⏭) ──────────────────────────────────────
        self._btn_prev = QPushButton("⏮")
        self._btn_play = QPushButton("▶")
        self._btn_next = QPushButton("⏭")
        self._btn_prev.setObjectName("btn_prev")
        self._btn_play.setObjectName("btn_play")
        self._btn_next.setObjectName("btn_next")
        self._btn_prev.setToolTip("Previous")
        self._btn_play.setToolTip("Play / Pause")
        self._btn_next.setToolTip("Next")
        self._btn_prev.setFixedSize(28, 28)
        self._btn_play.setFixedSize(34, 28)
        self._btn_next.setFixedSize(28, 28)
        self._btn_prev.clicked.connect(self._engine.prev)
        self._btn_play.clicked.connect(self._engine.play_pause)
        self._btn_next.clicked.connect(self._engine.next)

        self._btn_like = QPushButton("♡")
        self._btn_like.setObjectName("btn_like")
        self._btn_like.setToolTip("Like / Unlike")
        self._btn_like.setCheckable(True)
        self._btn_like.setFixedSize(26, 28)
        self._btn_like.setEnabled(False)
        self._btn_like.toggled.connect(self._on_like_toggled)

        self._transport = QWidget(self)
        tl = QHBoxLayout(self._transport)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(2)
        tl.addStretch()
        tl.addWidget(self._btn_like)
        tl.addSpacing(4)
        tl.addWidget(self._btn_prev)
        tl.addWidget(self._btn_play)
        tl.addWidget(self._btn_next)

        # ── Progress block ────────────────────────────────────────────────
        self._elapsed_lbl = QLabel("0:00")
        self._elapsed_lbl.setObjectName("time_lbl")
        self._elapsed_lbl.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        self._progress = QSlider(Qt.Orientation.Horizontal)
        self._progress.setObjectName("progress_slider")
        self._progress.setRange(0, 1000)
        self._progress.setValue(0)
        self._progress.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._progress.sliderPressed.connect(self._seek_start)
        self._progress.sliderReleased.connect(self._seek_end)

        self._duration_lbl = QLabel("—:——")
        self._duration_lbl.setObjectName("time_lbl")
        self._duration_lbl.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )

        self._prog_block = QWidget(self)
        pl = QHBoxLayout(self._prog_block)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(6)
        pl.addWidget(self._elapsed_lbl)
        pl.addWidget(self._progress)
        pl.addWidget(self._duration_lbl)

        # ── Vol + queue block (🔊 ☰) ─────────────────────────────────────
        self._vol = QSlider(Qt.Orientation.Horizontal)
        self._vol.setObjectName("vol_slider")
        self._vol.setFixedWidth(76)
        self._vol.setRange(0, 100)
        self._vol.setValue(int(self._engine.volume() * 100))
        self._vol.setToolTip("Volume")
        self._vol.valueChanged.connect(lambda v: self._engine.set_volume(v / 100))

        self._btn_queue = QPushButton("☰", self)
        self._btn_queue.setObjectName("btn_queue")
        self._btn_queue.setToolTip("Show queue")
        self._btn_queue.setCheckable(True)
        self._btn_queue.setFixedSize(28, 26)
        self._btn_queue.clicked.connect(self.queue_toggled)

        self._vol_block = QWidget(self)
        rl = QHBoxLayout(self._vol_block)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self._vol)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        w = self.width()

        # Horizontal bounds of the centred control group
        g_w = min(w, _GROUP_MAX)
        g_x = (w - g_w) // 2
        inner_w = g_w - 2 * (_SIDE_W + 14)

        # Info row: same width as control group so labels don't extend into margins
        info_h = self._info_row.sizeHint().height()
        self._info_row.setGeometry(g_x, 0, g_w, info_h)

        # Transport — centred on _TRANSPORT_CY
        self._transport.setGeometry(g_x, _TRANSPORT_CY - 14, _SIDE_W, 28)

        # Progress — centred on _PROGRESS_CY
        self._prog_block.setGeometry(g_x + _SIDE_W + 14, _PROGRESS_CY - 11, inner_w, 22)

        # Vol slider — centred on _VOL_CY
        self._vol_block.setGeometry(g_x + g_w - _SIDE_W, _VOL_CY - 11, 76, 22)

        # Queue button — centred on _QUEUE_CY (independent)
        self._btn_queue.setGeometry(g_x + g_w - _SIDE_W + 76 + 8, _QUEUE_CY - 13, 28, 26)

    def _connect_engine(self):
        self._engine.track_changed.connect(self._on_track_changed)
        self._engine.metadata_changed.connect(self._on_metadata_changed)
        self._engine.state_changed.connect(self._on_state_changed)
        self._engine.position_changed.connect(self._on_position)
        self._engine.duration_changed.connect(self._on_duration)

    # ── Public ────────────────────────────────────────────────────────────

    def set_playlists(self, playlists: list[dict]):
        self._playlists = playlists

    def set_is_library_track(self, is_library: bool):
        self._is_library_track = is_library

    def queue_button(self) -> QPushButton:
        return self._btn_queue

    def current_path(self) -> str:
        return self._current_path

    def set_queue_checked(self, checked: bool):
        self._btn_queue.setChecked(checked)

    def set_liked(self, liked: bool):
        self._btn_like.blockSignals(True)
        self._btn_like.setChecked(liked)
        self._btn_like.setText("♥" if liked else "♡")
        self._btn_like.blockSignals(False)

    # ── Engine signals ────────────────────────────────────────────────────

    # ── Link helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _make_link(text: str, kind: str, value: str, color: str) -> str:
        href = f"{kind}://{urllib.parse.quote(value)}"
        return (
            f'<a href="{href}" style="color:{color}; text-decoration:none;">'
            f"{html.escape(text)}</a>"
        )

    def _track_search(self) -> str:
        parts = [self._current_artist, self._current_title]
        return " ".join(p for p in parts if p)

    def _album_search(self) -> str:
        if not self._current_row:
            return ""
        artist = (self._current_artist
                  or (self._current_row.get("artist") or "")).strip()
        album  = (self._current_row.get("title") or "").strip()
        return " ".join(p for p in [artist, album] if p)

    def _catno_search(self) -> str:
        if not self._current_row:
            return ""
        catno = (self._current_row.get("catalog_number") or "").strip()
        # Strip punctuation so "ABC-001" → "ABC 001"; search matches word-by-word
        return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", catno)).strip()

    _MAX_ARTIST = 30
    _MAX_TITLE  = 45
    _MAX_ALBUM  = 45
    _MAX_CATNO  = 28

    @staticmethod
    def _elide(text: str, max_chars: int) -> str:
        return text if len(text) <= max_chars else text[:max_chars - 1] + "…"

    def _rebuild_track_label(self):
        if not self._current_artist and not self._current_title:
            self._track_lbl.setText("—")
            self._track_lbl.set_has_links(False)
            self._track_lbl.setToolTip("")
            return
        c  = QApplication.palette().color(QPalette.ColorRole.WindowText).name()
        parts: list[str] = []
        if self._current_artist:
            parts.append(self._make_link(
                self._elide(self._current_artist, self._MAX_ARTIST),
                "artist", self._current_artist, c,
            ))
        if self._current_artist and self._current_title:
            parts.append(f'<span style="color:{c};">  —  </span>')
        if self._current_title:
            parts.append(self._make_link(
                self._elide(self._current_title, self._MAX_TITLE),
                "release", self._track_search(), c,
            ))
        self._track_lbl.setText("".join(parts))
        self._track_lbl.set_has_links(True)
        truncated = (len(self._current_artist) > self._MAX_ARTIST or
                     len(self._current_title)  > self._MAX_TITLE)
        tip = " — ".join(p for p in [self._current_artist, self._current_title] if p)
        self._track_lbl.setToolTip(tip if truncated else "")

    def _rebuild_meta_label(self):
        if not self._current_row:
            self._meta_lbl.setText("")
            self._meta_lbl.set_has_links(False)
            self._meta_lbl.setToolTip("")
            return
        c      = QApplication.palette().color(QPalette.ColorRole.PlaceholderText).name()
        as_    = self._album_search()
        album  = (self._current_row.get("title")          or "").strip()
        cat_no = (self._current_row.get("catalog_number") or "").strip()
        parts: list[str] = []
        if album:
            parts.append(self._make_link(
                self._elide(album, self._MAX_ALBUM),
                "release", as_, c,
            ))
        if album and cat_no:
            parts.append(f'<span style="color:{c};">  —  </span>')
        if cat_no:
            parts.append(self._make_link(
                self._elide(cat_no, self._MAX_CATNO),
                "release", self._catno_search(), c,
            ))
        self._meta_lbl.setText("".join(parts))
        self._meta_lbl.set_has_links(bool(parts))
        truncated = (len(album)  > self._MAX_ALBUM or
                     len(cat_no) > self._MAX_CATNO)
        tip = " — ".join(p for p in [album, cat_no] if p)
        self._meta_lbl.setToolTip(tip if truncated else "")

    def _on_info_context_menu(self, pos):
        if not self._current_path:
            return
        folder_path = (self._current_row or {}).get("folder_path") or ""
        if not folder_path:
            folder_path = str(Path(self._current_path).parent)
        album = (self._current_row or {}).get("title") or ""

        menu = QMenu(self)
        act_go = menu.addAction("Go to Release")

        pl_actions: dict = {}
        if self._is_library_track and self._playlists:
            menu.addSeparator()
            pl_menu = menu.addMenu("Add to Playlist")
            for pl in self._playlists:
                act = pl_menu.addAction(pl["name"])
                pl_actions[act] = pl["id"]

        chosen = menu.exec(self.sender().mapToGlobal(pos))
        if chosen == act_go:
            self.go_to_release_requested.emit(folder_path)
        elif chosen in pl_actions:
            self.add_to_playlist_requested.emit(
                pl_actions[chosen],
                self._current_path,
                self._current_artist,
                self._current_title,
                album,
                folder_path,
                self._duration_ms,
            )

    def _on_link(self, href: str):
        if "://" in href:
            kind, _, encoded = href.partition("://")
            value = urllib.parse.unquote(encoded)
            self.navigate_requested.emit(kind, value)

    # ── Engine signals ────────────────────────────────────────────────────

    def _on_like_toggled(self, checked: bool):
        self._btn_like.setText("♥" if checked else "♡")
        if self._current_path and self._current_row is not None:
            self.like_toggled.emit(self._current_path, self._current_row, checked)

    def _on_track_changed(self, row: dict, path: str, track_idx: int, total: int):
        self._current_row    = row
        self._current_path   = path
        self._current_artist = ""
        self._current_title  = ""
        self._track_lbl.setText(html.escape(Path(path).stem))
        self._track_lbl.set_has_links(False)
        self._rebuild_meta_label()
        self._update_enabled(True)

    def _on_metadata_changed(self, artist: str, title: str):
        self._current_artist = artist
        self._current_title  = title
        self._rebuild_track_label()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._rebuild_track_label()
            self._rebuild_meta_label()

    def _on_state_changed(self, playing: bool):
        self._btn_play.setText("⏸" if playing else "▶")

    def _on_position(self, ms: int):
        if self._seeking or self._duration_ms <= 0:
            return
        self._progress.setValue(int(ms / self._duration_ms * 1000))
        self._elapsed_lbl.setText(_fmt_ms(ms))

    def _on_duration(self, ms: int):
        self._duration_ms = ms
        self._duration_lbl.setText(_fmt_ms(ms))
        self._elapsed_lbl.setText("0:00")

    # ── Seek ──────────────────────────────────────────────────────────────

    def _seek_start(self):
        self._seeking = True

    def _seek_end(self):
        self._seeking = False
        if self._duration_ms > 0:
            self._engine.seek(int(self._progress.value() / 1000 * self._duration_ms))

    def _update_enabled(self, enabled: bool):
        for w in (self._btn_prev, self._btn_play, self._btn_next, self._progress, self._btn_like):
            w.setEnabled(enabled)
