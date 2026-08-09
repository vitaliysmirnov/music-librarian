"""Shared visual constants for the iTunes-style UI."""

ROW_HEIGHT = 20

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
