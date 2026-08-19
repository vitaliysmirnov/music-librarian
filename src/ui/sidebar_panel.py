import json
import sys as _sys

from PySide6.QtCore import QEvent, Qt, QByteArray, QMimeData, QPoint, QPointF, QRect, QRectF, QSize, Signal
from PySide6.QtGui import QBrush, QColor, QDrag, QFont, QIcon, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMenu, QPushButton, QScrollArea, QSizePolicy, QStyle, QStyleOptionButton, QVBoxLayout, QWidget, QWidgetAction

_ICON_PX      = 14   # logical icon size (points)
_REORDER_MIME = "application/x-sidebar-playlist-id"
_IS_WIN       = _sys.platform == "win32"

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

_PLAYLIST_MENU_STYLE = """
QMenu {
    background: palette(window);
    border: 1px solid palette(mid);
    border-radius: 6px;
    padding: 4px 2px;
}
QMenu::item {
    padding: 5px 20px 5px 10px;
    border-radius: 4px;
    font-size: 13px;
    color: palette(buttonText);
}
QMenu::item:selected {
    background: rgba(128, 128, 128, 0.15);
    color: palette(buttonText);
}
QMenu::separator {
    height: 1px;
    background: palette(mid);
    margin: 3px 6px;
}
"""


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


class _NavButton(QPushButton):
    """QPushButton that draws icon and text with precise shared vertical center.

    Qt's built-in CE_PushButtonLabel positions text by centering fm.height()
    (ascent + descent + leading).  On Windows the leading is larger than on
    macOS, so the *visual* text center ends up below the icon center.  We draw
    both manually, anchoring each element to the same (h - size) // 2 midpoint.
    """

    def __init__(self, label: str, pix_off: QPixmap, pix_on: QPixmap, parent=None):
        super().__init__(label, parent)
        self._pix_off = pix_off
        self._pix_on  = pix_on
        # macOS uses super().paintEvent() which needs setIcon() to draw the icon.
        if not _IS_WIN:
            self.setIcon(QIcon(pix_off))
            self.setIconSize(QSize(_ICON_PX, _ICON_PX))

    def paintEvent(self, event):
        # On macOS Qt's built-in CE_PushButtonLabel centres icon + text
        # correctly with the system font metrics — no adjustment needed.
        if not _IS_WIN:
            super().paintEvent(event)
            return

        # Windows: Fusion's leading is larger than macOS, causing text to
        # sit visually lower than the icon.  Draw both manually so they share
        # the exact same (h - size) // 2 vertical midpoint.
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        opt = QStyleOptionButton()
        self.initStyleOption(opt)
        self.style().drawControl(QStyle.ControlElement.CE_PushButtonBevel, opt, p, self)

        h = self.height()
        x = 8

        is_playlist = bool(self.property("isPlaylist"))
        pix = (self._pix_on if (self.isChecked() and not is_playlist)
               else self._pix_off)
        if pix and not pix.isNull():
            iy = (h - _ICON_PX) // 2
            p.drawPixmap(x, iy, _ICON_PX, _ICON_PX, pix)
            x += _ICON_PX + 6

        font = QFont(self.font())
        font.setBold(self.isChecked())
        p.setFont(font)
        fm = p.fontMetrics()
        # Exclude leading from centering: Windows Fusion adds extra leading that
        # would push the visual text centre below the icon centre.
        text_h = fm.ascent() + fm.descent()
        ty = (h - text_h) // 2
        p.setPen(QColor(255, 255, 255) if (self.isChecked() and not is_playlist)
                 else self.palette().color(QPalette.ColorRole.WindowText))
        p.drawText(QRect(x, ty, self.width() - x - 4, text_h),
                   Qt.AlignmentFlag.AlignLeft, self.text())


def _icon_color_off() -> QColor:
    is_dark = QApplication.palette().window().color().lightness() < 128
    return QColor(185, 185, 193) if is_dark else QColor(105, 105, 115)


# ── Destructive QWidgetAction (red label with hover) ──────────────────────────

class _DestructiveAction(QWidgetAction):
    def __init__(self, text: str, parent: QMenu):
        super().__init__(parent)
        lbl = QLabel(text)
        lbl.setContentsMargins(10, 5, 20, 5)
        lbl.setAttribute(Qt.WidgetAttribute.WA_Hover)
        lbl.installEventFilter(self)
        self._lbl = lbl
        self.setDefaultWidget(lbl)
        self._set_hovered(False)

    def _set_hovered(self, hovered: bool):
        bg = "rgba(208,64,64,0.15)" if hovered else "transparent"
        self._lbl.setStyleSheet(
            f"color:#d04040; background:{bg}; font-size:13px; border-radius:4px;"
        )

    def eventFilter(self, obj, event):
        if obj is self._lbl:
            t = event.type()
            if t in (QEvent.Type.HoverEnter, QEvent.Type.Enter):
                self._set_hovered(True)
            elif t in (QEvent.Type.HoverLeave, QEvent.Type.Leave):
                self._set_hovered(False)
        return super().eventFilter(obj, event)


# ── Playlist button with drop support ─────────────────────────────────────────

class _PlaylistButton(_NavButton):
    """Playlist nav button that also accepts URL drops to add tracks."""
    tracks_dropped   = Signal(int, list, list)  # playlist_id, list[QUrl], cue_meta
    rename_requested = Signal(int, str)         # playlist_id, current_name
    delete_requested = Signal(int)              # playlist_id

    def __init__(self, playlist_id: int, name: str, pix_off: QPixmap, parent=None):
        super().__init__(name, pix_off, pix_off, parent)  # same pix in both states
        self._playlist_id  = playlist_id
        self._drag_start: QPoint | None = None
        self.setAcceptDrops(True)
        self.setProperty("isPlaylist", "true")
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)

    def _on_context_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet(_PLAYLIST_MENU_STYLE)
        act_rename = menu.addAction("Rename Playlist")
        menu.addSeparator()
        act_delete = _DestructiveAction("Delete Playlist", menu)
        menu.addAction(act_delete)
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen == act_rename:
            self.rename_requested.emit(self._playlist_id, self.text())
        elif chosen == act_delete:
            self.delete_requested.emit(self._playlist_id)

    # ── Drag initiation ───────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self._drag_start is not None
                and (event.buttons() & Qt.MouseButton.LeftButton)
                and (event.pos() - self._drag_start).manhattanLength()
                    >= QApplication.startDragDistance()):
            self._drag_start = None
            mime = QMimeData()
            mime.setData(_REORDER_MIME, QByteArray(str(self._playlist_id).encode()))
            drag = QDrag(self)
            drag.setMimeData(mime)
            drag.exec(Qt.DropAction.MoveAction)
            return  # button may be deleted by the time drag.exec() returns
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        super().mouseReleaseEvent(event)

    # ── URL drop target (tracks → playlist) ───────────────────────────────

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
            cue_meta = []
            if event.mimeData().hasFormat("application/x-cue-track-meta"):
                try:
                    cue_meta = json.loads(
                        bytes(event.mimeData().data("application/x-cue-track-meta")).decode()
                    )
                except Exception:
                    pass
            self.tracks_dropped.emit(self._playlist_id, event.mimeData().urls(), cue_meta)
            event.acceptProposedAction()
        else:
            event.ignore()


# ── Playlist reorder container ────────────────────────────────────────────────

class _PlaylistsContainer(QWidget):
    """VBox container for playlist buttons; accepts internal reorder drops."""
    reordered = Signal(list)   # new list of playlist ids in order

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self._drop_y = -1

    def _buttons(self) -> list[_PlaylistButton]:
        lo = self.layout()
        return [lo.itemAt(i).widget() for i in range(lo.count())
                if isinstance(lo.itemAt(i).widget(), _PlaylistButton)]

    def _insert_idx_at(self, y: int) -> int:
        for i, btn in enumerate(self._buttons()):
            if y < btn.geometry().center().y():
                return i
        return len(self._buttons())

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(_REORDER_MIME):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasFormat(_REORDER_MIME):
            self._drop_y = int(event.position().y())
            self.update()
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._drop_y = -1
        self.update()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._drop_y = -1
        self.update()
        if not event.mimeData().hasFormat(_REORDER_MIME):
            event.ignore()
            return
        try:
            src_id = int(event.mimeData().data(_REORDER_MIME).toStdString())
        except Exception:
            event.ignore()
            return
        buttons = self._buttons()
        insert_at = self._insert_idx_at(int(event.position().y()))
        old_ids = [b._playlist_id for b in buttons]
        if src_id not in old_ids:
            event.ignore()
            return
        old_idx = old_ids.index(src_id)
        new_ids = [pid for pid in old_ids if pid != src_id]
        dest = insert_at if insert_at <= old_idx else insert_at - 1
        new_ids.insert(dest, src_id)
        if new_ids != old_ids:
            self.reordered.emit(new_ids)
        event.acceptProposedAction()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_y < 0:
            return
        buttons = self._buttons()
        if not buttons:
            return
        insert_at = self._insert_idx_at(self._drop_y)
        if insert_at < len(buttons):
            y = buttons[insert_at].geometry().top()
        else:
            y = buttons[-1].geometry().bottom()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(QColor("#3875d7"), 2))
        r = 3
        x1, x2 = 8, self.width() - 8
        p.drawLine(x1, y, x2, y)
        p.setBrush(QColor("#3875d7"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(x1 - r, y - r, r * 2, r * 2)
        p.drawEllipse(x2 - r, y - r, r * 2, r * 2)


# ── Sidebar widget ─────────────────────────────────────────────────────────────

class SidebarPanel(QWidget):
    nav_changed                 = Signal(str)
    add_playlist_requested      = Signal()
    rename_playlist_requested   = Signal(int, str)     # playlist_id, current_name
    delete_playlist_requested   = Signal(int)          # playlist_id
    reorder_playlists_requested = Signal(list)         # new ordered list of playlist ids
    tracks_dropped_on_playlist  = Signal(int, list, list)  # playlist_id, list[QUrl], cue_meta

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
                btn = _NavButton(label, self._pix_off[key], self._pix_on[key])
                btn.setFlat(True)
                btn.setCheckable(True)
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

        # ── Playlist buttons container (scrollable, reorderable) ─────────
        self._playlists_container = _PlaylistsContainer()
        self._playlists_container.reordered.connect(self.reorder_playlists_requested)
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
            btn  = _PlaylistButton(pid, name, self._pix_off["playlist"])
            btn.setFlat(True)
            btn.setCheckable(True)
            btn.clicked.connect(lambda _c, k=f"playlist:{pid}": self._on_click(k))
            btn.tracks_dropped.connect(lambda pid_, urls, cue: self.tracks_dropped_on_playlist.emit(pid_, urls, cue))
            btn.rename_requested.connect(self.rename_playlist_requested)
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
                if not _IS_WIN and self._current in self._pix_off:
                    btn.setIcon(QIcon(self._pix_off[self._current]))
            elif self._current.startswith("playlist:"):
                pid = int(self._current.split(":")[1])
                if pid in self._playlist_buttons:
                    self._playlist_buttons[pid].setChecked(False)

        self._current = key

        if key in self._buttons:
            btn = self._buttons[key]
            btn.setChecked(True)
            if not _IS_WIN and key in self._pix_on:
                btn.setIcon(QIcon(self._pix_on[key]))
        elif key.startswith("playlist:"):
            pid = int(key.split(":")[1])
            if pid in self._playlist_buttons:
                self._playlist_buttons[pid].setChecked(True)
