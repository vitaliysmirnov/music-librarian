from PySide6.QtCore import Qt, QPointF, QRectF, QSize, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget

_ICON_PX = 14   # logical icon size (points)

_NAV_ITEMS = [
    (None,       "Library",  "section"),
    ("releases", "Releases", None),
    ("liked",    "Liked",    None),
]

_SIDEBAR_STYLE = """
SidebarPanel QPushButton {
    text-align: left;
    padding: 5px 8px 5px 8px;
    border: none;
    border-radius: 5px;
    margin: 0 4px;
    font-size: 13px;
    background: transparent;
    min-height: 26px;
}
SidebarPanel QPushButton:hover:!checked {
    background: rgba(128, 128, 128, 40);
}
SidebarPanel QPushButton:checked {
    background: #3875d7;
    color: white;
    font-weight: 700;
}
SidebarPanel QPushButton[isPlaylist="true"]:checked {
    background: transparent;
    color: palette(windowText);
}
SidebarPanel QPushButton#add_playlist_btn {
    text-align: center;
    padding: 0;
    margin: 0 0 0 0;
    font-size: 16px;
    font-weight: 400;
    min-width: 22px;
    max-width: 22px;
    min-height: 22px;
    max-height: 22px;
    border-radius: 4px;
}
SidebarPanel QPushButton[dragOver="true"] {
    background: rgba(56, 117, 215, 0.30);
    border: 1px solid rgba(56, 117, 215, 0.55);
}
"""

_SECTION_STYLE = (
    "font-size: 10px; font-weight: 700; "
    "color: palette(placeholderText); "
    "padding: 0;"
)


# ── Icon drawing ───────────────────────────────────────────────────────────────

def _make_pix(draw_fn, px: int, color: QColor) -> QPixmap:
    pix = QPixmap(px * 2, px * 2)
    pix.setDevicePixelRatio(2.0)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    draw_fn(p, float(px), color)
    p.end()
    return pix


def _draw_disc(p: QPainter, s: float, c: QColor):
    p.setBrush(QBrush(c))
    p.setPen(Qt.PenStyle.NoPen)
    path = QPainterPath()
    path.setFillRule(Qt.FillRule.OddEvenFill)
    pad = s * 0.04
    path.addEllipse(QRectF(pad, pad, s - 2 * pad, s - 2 * pad))
    hole = s * 0.30
    path.addEllipse(QRectF(s / 2 - hole / 2, s / 2 - hole / 2, hole, hole))
    p.drawPath(path)


def _draw_heart(p: QPainter, s: float, c: QColor):
    p.setBrush(QBrush(c))
    p.setPen(Qt.PenStyle.NoPen)
    path = QPainterPath()
    path.moveTo(s * 0.50, s * 0.88)
    path.cubicTo(QPointF(s * 0.15, s * 0.65), QPointF(s * 0.00, s * 0.42), QPointF(s * 0.13, s * 0.25))
    path.cubicTo(QPointF(s * 0.23, s * 0.08), QPointF(s * 0.44, s * 0.10), QPointF(s * 0.50, s * 0.30))
    path.cubicTo(QPointF(s * 0.56, s * 0.10), QPointF(s * 0.77, s * 0.08), QPointF(s * 0.87, s * 0.25))
    path.cubicTo(QPointF(s * 1.00, s * 0.42), QPointF(s * 0.85, s * 0.65), QPointF(s * 0.50, s * 0.88))
    path.closeSubpath()
    p.drawPath(path)


def _draw_list(p: QPainter, s: float, c: QColor):
    p.setBrush(QBrush(c))
    p.setPen(Qt.PenStyle.NoPen)
    h = s * 0.12
    r = h / 2
    for cy in (s * 0.22, s * 0.50, s * 0.78):
        p.drawRoundedRect(QRectF(s * 0.07, cy - h / 2, s * 0.86, h), r, r)


_ICON_DRAW = {
    "releases": _draw_disc,
    "liked":    _draw_heart,
    "playlist": _draw_list,
}


def _icon_color_off() -> QColor:
    is_dark = QApplication.palette().window().color().lightness() < 128
    return QColor(185, 185, 193) if is_dark else QColor(105, 105, 115)


# ── Playlist button with drop support ─────────────────────────────────────────

class _PlaylistButton(QPushButton):
    """Playlist nav button that also accepts URL drops to add tracks."""
    tracks_dropped   = Signal(int, list)  # playlist_id, list[QUrl]
    delete_requested = Signal(int)        # playlist_id

    def __init__(self, playlist_id: int, name: str, parent=None):
        super().__init__(name, parent)
        self._playlist_id = playlist_id
        self.setAcceptDrops(True)
        self.setProperty("isPlaylist", "true")
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def _on_context_menu(self, pos):
        menu = QMenu(self)
        act_delete = menu.addAction("Delete Playlist")
        if menu.exec(self.mapToGlobal(pos)) == act_delete:
            self.delete_requested.emit(self._playlist_id)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.setProperty("dragOver", True)
            self.style().unpolish(self)
            self.style().polish(self)
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self.setProperty("dragOver", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self.setProperty("dragOver", False)
        self.style().unpolish(self)
        self.style().polish(self)
        if event.mimeData().hasUrls():
            self.tracks_dropped.emit(self._playlist_id, event.mimeData().urls())
            event.acceptProposedAction()
        else:
            event.ignore()


# ── Sidebar widget ─────────────────────────────────────────────────────────────

class SidebarPanel(QWidget):
    nav_changed                = Signal(str)
    add_playlist_requested     = Signal()
    delete_playlist_requested  = Signal(int)          # playlist_id
    tracks_dropped_on_playlist = Signal(int, list)    # playlist_id, list[QUrl]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buttons: dict[str, QPushButton]  = {}
        self._playlist_buttons: dict[int, QPushButton] = {}
        self._pix_off: dict[str, QPixmap] = {}
        self._pix_on:  dict[str, QPixmap] = {}
        self._current: str | None = None
        self._build_icons()
        self._setup_ui()

    def _build_icons(self):
        col_off = _icon_color_off()
        col_on  = QColor(255, 255, 255)
        for key, fn in _ICON_DRAW.items():
            self._pix_off[key] = _make_pix(fn, _ICON_PX, col_off)
            self._pix_on[key]  = _make_pix(fn, _ICON_PX, col_on)

    def _setup_ui(self):
        self.setObjectName("SidebarPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_SIDEBAR_STYLE)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 8)
        layout.setSpacing(1)

        for key, label, kind in _NAV_ITEMS:
            if kind == "section":
                sec = QLabel(label.upper())
                sec.setStyleSheet(_SECTION_STYLE)
                sec.setContentsMargins(14, 10, 14, 2)
                fx = QGraphicsOpacityEffect(sec)
                fx.setOpacity(0.45)
                sec.setGraphicsEffect(fx)
                layout.addWidget(sec)
            else:
                btn = QPushButton(label)
                btn.setFlat(True)
                btn.setCheckable(True)
                if key in self._pix_off:
                    btn.setIcon(QIcon(self._pix_off[key]))
                    btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
                btn.clicked.connect(lambda _c, k=key: self._on_click(k))
                layout.addWidget(btn)
                self._buttons[key] = btn

        # ── Playlists section header with "+" ─────────────────────────────
        pl_hdr = QWidget()
        pl_hdr_l = QHBoxLayout(pl_hdr)
        pl_hdr_l.setContentsMargins(14, 10, 8, 2)
        pl_hdr_l.setSpacing(4)

        pl_lbl = QLabel("PLAYLISTS")
        pl_lbl.setStyleSheet(_SECTION_STYLE)
        fx2 = QGraphicsOpacityEffect(pl_lbl)
        fx2.setOpacity(0.45)
        pl_lbl.setGraphicsEffect(fx2)
        pl_hdr_l.addWidget(pl_lbl)
        pl_hdr_l.addStretch()

        add_btn = QPushButton("+")
        add_btn.setObjectName("add_playlist_btn")
        add_btn.setToolTip("New playlist")
        add_btn.setFlat(True)
        add_btn.clicked.connect(self.add_playlist_requested)
        pl_hdr_l.addWidget(add_btn)
        layout.addWidget(pl_hdr)

        # ── Playlist buttons container (scrollable) ───────────────────────
        self._playlists_container = QWidget()
        self._playlists_layout = QVBoxLayout(self._playlists_container)
        self._playlists_layout.setContentsMargins(0, 0, 0, 0)
        self._playlists_layout.setSpacing(1)
        self._playlists_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        scroll = QScrollArea()
        scroll.setWidget(self._playlists_container)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        scroll.viewport().setStyleSheet("background: transparent;")
        layout.addWidget(scroll, 1)

    def refresh_playlists(self, playlists: list) -> None:
        # Remove old playlist buttons
        for btn in self._playlist_buttons.values():
            btn.setParent(None)
            btn.deleteLater()
        self._playlist_buttons.clear()

        for pl in playlists:
            pid  = pl["id"]
            name = pl["name"]
            btn  = _PlaylistButton(pid, name)
            btn.setFlat(True)
            btn.setCheckable(True)
            btn.setIcon(QIcon(self._pix_off["playlist"]))
            btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
            btn.clicked.connect(lambda _c, k=f"playlist:{pid}": self._on_click(k))
            btn.tracks_dropped.connect(lambda pid_, urls: self.tracks_dropped_on_playlist.emit(pid_, urls))
            btn.delete_requested.connect(self.delete_playlist_requested)
            self._playlists_layout.addWidget(btn)
            self._playlist_buttons[pid] = btn

        # Restore checked state
        if self._current and self._current.startswith("playlist:"):
            pid = int(self._current.split(":")[1])
            if pid in self._playlist_buttons:
                self._playlist_buttons[pid].setChecked(True)

    def _on_click(self, key: str):
        self.set_current(key)
        self.nav_changed.emit(key)

    def set_current(self, key: str):
        # Deselect previous
        if self._current:
            if self._current in self._buttons:
                btn = self._buttons[self._current]
                btn.setChecked(False)
                icon_key = self._current
                if icon_key in self._pix_off:
                    btn.setIcon(QIcon(self._pix_off[icon_key]))
            elif self._current.startswith("playlist:"):
                pid = int(self._current.split(":")[1])
                if pid in self._playlist_buttons:
                    self._playlist_buttons[pid].setChecked(False)
                    self._playlist_buttons[pid].setIcon(QIcon(self._pix_off["playlist"]))

        self._current = key

        # Select new
        if key in self._buttons:
            btn = self._buttons[key]
            btn.setChecked(True)
            if key in self._pix_on:
                btn.setIcon(QIcon(self._pix_on[key]))
        elif key.startswith("playlist:"):
            pid = int(key.split(":")[1])
            if pid in self._playlist_buttons:
                btn = self._playlist_buttons[pid]
                btn.setChecked(True)
