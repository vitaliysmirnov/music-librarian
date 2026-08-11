from PySide6.QtCore import Qt, QPointF, QRectF, QSize, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QLabel, QPushButton, QVBoxLayout, QWidget

_ICON_PX = 14   # logical icon size (points)

_NAV_ITEMS = [
    (None,        "Library",        "section"),
    ("releases",  "Releases",       None),
    ("liked",     "Liked",          None),
    (None,        "Playlists",      "section"),
    ("playlists", "All Playlists",  None),
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
}
"""

_SECTION_STYLE = (
    "font-size: 10px; font-weight: 700; "
    "color: palette(placeholderText); "
    "padding: 10px 14px 2px 14px;"
)


# ── Icon drawing ───────────────────────────────────────────────────────────────

def _make_pix(draw_fn, px: int, color: QColor) -> QPixmap:
    """Render draw_fn onto a hi-DPI px×px pixmap."""
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
    path.cubicTo(
        QPointF(s * 0.15, s * 0.65),
        QPointF(s * 0.00, s * 0.42),
        QPointF(s * 0.13, s * 0.25),
    )
    path.cubicTo(
        QPointF(s * 0.23, s * 0.08),
        QPointF(s * 0.44, s * 0.10),
        QPointF(s * 0.50, s * 0.30),
    )
    path.cubicTo(
        QPointF(s * 0.56, s * 0.10),
        QPointF(s * 0.77, s * 0.08),
        QPointF(s * 0.87, s * 0.25),
    )
    path.cubicTo(
        QPointF(s * 1.00, s * 0.42),
        QPointF(s * 0.85, s * 0.65),
        QPointF(s * 0.50, s * 0.88),
    )
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
    "releases":  _draw_disc,
    "liked":     _draw_heart,
    "playlists": _draw_list,
}


def _icon_color_off() -> QColor:
    """Mid-gray that reads in both light and dark themes."""
    is_dark = QApplication.palette().window().color().lightness() < 128
    return QColor(185, 185, 193) if is_dark else QColor(105, 105, 115)


# ── Sidebar widget ─────────────────────────────────────────────────────────────

class SidebarPanel(QWidget):
    nav_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._buttons: dict[str, QPushButton] = {}
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
                _fade = QGraphicsOpacityEffect(sec)
                _fade.setOpacity(0.45)
                sec.setGraphicsEffect(_fade)
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

        layout.addStretch()

    def _on_click(self, key: str):
        self.set_current(key)
        self.nav_changed.emit(key)

    def set_current(self, key: str):
        if self._current and self._current in self._buttons:
            btn = self._buttons[self._current]
            btn.setChecked(False)
            if self._current in self._pix_off:
                btn.setIcon(QIcon(self._pix_off[self._current]))
        self._current = key
        if key in self._buttons:
            btn = self._buttons[key]
            btn.setChecked(True)
            if key in self._pix_on:
                btn.setIcon(QIcon(self._pix_on[key]))
