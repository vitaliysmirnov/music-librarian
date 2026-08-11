"""Shared visual constants for the iTunes-style UI."""

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import (
    QApplication, QLabel, QSizePolicy, QStyle, QStyledItemDelegate, QToolTip,
)

ROW_HEIGHT = 20


class ElidedTooltipDelegate(QStyledItemDelegate):
    """Character-level text elision + tooltip for table cells.

    macOS QMacStyle passes text through CoreText which truncates at word
    boundaries.  Overriding paint() pre-elides the text at character level
    so the native style never sees an overlong string.
    The same _MARGIN constant drives both the paint threshold and the
    tooltip detection in helpEvent.
    """

    # Total horizontal cell margin on macOS Qt: 4px CSS padding each side
    # + ~4px internal Qt style margin each side (empirically measured).
    _MARGIN = 16

    def paint(self, painter, option, index):
        from PySide6.QtWidgets import QStyleOptionViewItem
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        if opt.text and opt.widget is not None:
            cell_w = opt.widget.columnWidth(index.column())
            if cell_w > 0:
                fm = QFontMetrics(opt.font)
                opt.text = fm.elidedText(
                    opt.text, Qt.TextElideMode.ElideRight, cell_w - self._MARGIN
                )
        # Call style.drawControl directly — super().paint() would call
        # initStyleOption() again internally, overwriting our pre-elided text.
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)

    def helpEvent(self, event, view, option, index):
        if event and event.type() == QEvent.Type.ToolTip:
            text = (index.data(Qt.ItemDataRole.DisplayRole) or "").strip()
            if not text:
                QToolTip.hideText()
                return False
            # columnWidth() is the authoritative column width from the header;
            # option.rect.width() can be 0 or stale in some Qt/platform combos.
            cell_w = view.columnWidth(index.column())
            if cell_w <= 0:
                return False
            # initStyleOption fills option.font with the font Qt actually uses
            # to paint the cell — more accurate than view.fontMetrics().
            self.initStyleOption(option, index)
            fm = QFontMetrics(option.font)
            if fm.horizontalAdvance(text) >= cell_w - self._MARGIN:
                QToolTip.showText(event.globalPos(), text, view)
                return True
            QToolTip.hideText()
            return False
        return super().helpEvent(event, view, option, index)

class ElidedLabel(QLabel):
    """QLabel that elides text with '…' when it doesn't fit the available width."""

    def __init__(self, text="", parent=None):
        super().__init__(parent)
        self._full_text = text
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)
        super().setText(self._elided())

    def setText(self, text):
        self._full_text = text
        super().setText(self._elided())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        super().setText(self._elided())

    def _elided(self):
        if not self._full_text:
            return self._full_text
        fm = QFontMetrics(self.font())
        return fm.elidedText(self._full_text, Qt.TextElideMode.ElideRight, max(0, self.width()))


TABLE_STYLE = """
QTableView, QTableWidget {
    border: none;
    font-size: 12px;
    background: palette(alternateBase);
    selection-background-color: #3875d7;
    selection-color: white;
    show-decoration-selected: 1;
}
QTableView::item, QTableWidget::item {
    padding: 0 4px;
    background: palette(alternateBase);
}
QTableView::item:selected, QTableWidget::item:selected {
    background: #3875d7;
    color: white;
}
QTableView::item:selected:!active, QTableWidget::item:selected:!active {
    background: #8ab4d4;
    color: palette(text);
}
QHeaderView {
    border: none;
}
QHeaderView::section {
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
        stop:0 palette(button), stop:1 palette(mid));
    color: palette(buttonText);
    border: none;
    border-right: 1px solid palette(midlight);
    border-bottom: 1px solid palette(shadow);
    padding: 1px 6px;
    font-size: 11px;
    font-weight: 500;
}
QHeaderView::up-arrow, QHeaderView::down-arrow {
    width: 0px;
    height: 0px;
    image: none;
}
"""

SEARCH_STYLE = """
QLineEdit {
    border: 1px solid palette(mid);
    border-radius: 9px;
    padding: 1px 8px;
    font-size: 12px;
    background: palette(base);
    max-height: 20px;
}
QLineEdit:focus {
    border-color: #3875d7;
}
"""

GROUPBOX_STYLE = """
QGroupBox {
    font-size: 11px;
    font-weight: 600;
    color: palette(windowText);
    border: none;
    border-top: 1px solid palette(mid);
    margin-top: 16px;
    padding-top: 10px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    top: -1px;
    padding: 0;
}
"""
