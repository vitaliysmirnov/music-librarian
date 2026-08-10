from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from src.ui.player_engine import PlayerEngine

_SIDE_W    = 110   # fixed width of transport block and right block
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


def _fmt_ms(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 60}:{s % 60:02d}"


class PlayerBar(QWidget):
    queue_toggled = Signal()

    def __init__(self, engine: PlayerEngine, parent=None):
        super().__init__(parent)
        self._engine      = engine
        self._seeking     = False
        self._duration_ms = 0
        self.setFixedHeight(_BAR_H)
        self.setStyleSheet(_BAR_STYLE)
        self._setup_ui()
        self._connect_engine()
        self._update_enabled(False)

    # ── Build ─────────────────────────────────────────────────────────────

    def _setup_ui(self):
        # No layout on self — all control blocks are positioned via resizeEvent.

        # ── Info row (full-width, top) ────────────────────────────────────
        self._track_lbl = QLabel("—")
        self._track_lbl.setObjectName("track_lbl")
        self._track_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._track_lbl.setTextFormat(Qt.TextFormat.PlainText)

        self._meta_lbl = QLabel("")
        self._meta_lbl.setObjectName("meta_lbl")
        self._meta_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._meta_lbl.setTextFormat(Qt.TextFormat.PlainText)

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

        self._transport = QWidget(self)
        tl = QHBoxLayout(self._transport)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(2)
        tl.addStretch()
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

        # Info row: full width, top
        info_h = self._info_row.sizeHint().height()
        self._info_row.setGeometry(0, 0, w, info_h)

        # Horizontal bounds of the centred control group
        g_w = min(w, _GROUP_MAX)
        g_x = (w - g_w) // 2
        inner_w = g_w - 2 * (_SIDE_W + 14)

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

    def queue_button(self) -> QPushButton:
        return self._btn_queue

    def set_queue_checked(self, checked: bool):
        self._btn_queue.setChecked(checked)

    # ── Engine signals ────────────────────────────────────────────────────

    def _on_track_changed(self, row: dict, path: str, track_idx: int, total: int):
        # Line 1: placeholder until tag metadata arrives via metadata_changed
        self._track_lbl.setText(Path(path).stem)

        # Line 2: Release — Cat. No. (from database row)
        album  = (row.get("title")          or "").strip()
        cat_no = (row.get("catalog_number") or "").strip()
        parts  = [p for p in (album, cat_no) if p]
        self._meta_lbl.setText("  —  ".join(parts))

        self._update_enabled(True)

    def _on_metadata_changed(self, artist: str, title: str):
        line1 = f"{artist}  —  {title}" if artist else title
        self._track_lbl.setText(line1)

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
        for w in (self._btn_prev, self._btn_play, self._btn_next, self._progress):
            w.setEnabled(enabled)
