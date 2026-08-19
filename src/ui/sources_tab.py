from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QFileDialog,
    QAbstractItemView, QHeaderView, QMessageBox,
)

from src.scanner.scanner import scan_source
from src.ui.style import ROW_HEIGHT, TABLE_STYLE, build_table_style
from src.utils.logger import get_logger

log = get_logger()


class SourcesTab(QWidget):
    sources_changed = Signal()

    def __init__(self, db):
        super().__init__()
        self._db = db
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Path", "Available", "Last Scan"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setShowGrid(False)
        self._table.setAlternatingRowColors(True)
        self._table.setStyleSheet(build_table_style())
        hdr = self._table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        vhdr = self._table.verticalHeader()
        vhdr.setVisible(False)
        vhdr.setDefaultSectionSize(ROW_HEIGHT)
        vhdr.setMinimumSectionSize(ROW_HEIGHT)
        layout.addWidget(self._table)

        bottom_bar = QWidget()
        bottom_bar.setObjectName("_BottomBar")
        bottom_bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        bottom_bar.setStyleSheet("#_BottomBar { background: palette(window); border-top: 1px solid palette(mid); }")
        bb = QHBoxLayout(bottom_bar)
        bb.setContentsMargins(8, 4, 8, 4)
        bb.setSpacing(4)

        add_btn = QPushButton("Add Source…")
        add_btn.clicked.connect(self._add_source)
        bb.addWidget(add_btn)

        remove_btn = QPushButton("Remove Source")
        remove_btn.clicked.connect(self._remove_source)
        bb.addWidget(remove_btn)

        scan_btn = QPushButton("Scan Selected")
        scan_btn.clicked.connect(self._scan_selected)
        bb.addWidget(scan_btn)

        bb.addStretch()
        layout.addWidget(bottom_bar)

    def refresh(self):
        sources = self._db.get_sources()
        self._table.setRowCount(0)
        for src in sources:
            row = self._table.rowCount()
            self._table.insertRow(row)

            path_item = QTableWidgetItem(src["path"])
            path_item.setData(Qt.UserRole, src["id"])
            self._table.setItem(row, 0, path_item)

            avail = "Yes" if src["is_available"] else "No"
            self._table.setItem(row, 1, QTableWidgetItem(avail))
            self._table.setItem(row, 2, QTableWidgetItem(src["last_scan"] or "—"))

    def _add_source(self):
        path = QFileDialog.getExistingDirectory(self, "Select Music Folder")
        if not path:
            return
        self._db.add_source(path)
        log.info("Source added: %s", path)
        self.refresh()
        self.sources_changed.emit()

    def _remove_source(self):
        selected = self._table.selectedItems()
        if not selected:
            return
        row = self._table.currentRow()
        source_id = self._table.item(row, 0).data(Qt.UserRole)
        path = self._table.item(row, 0).text()
        reply = QMessageBox.question(
            self,
            "Remove Source",
            f"Remove source '{path}'?\n"
            "All associated releases will be removed from the library.",
            QMessageBox.Yes | QMessageBox.No,
        )
        if reply == QMessageBox.Yes:
            self._db.delete_source(source_id)
            log.info("Source removed: id=%d", source_id)
            self.refresh()
            self.sources_changed.emit()

    def _scan_selected(self):
        if not self._table.selectedItems():
            return
        row = self._table.currentRow()
        if row < 0:
            return
        source_id = self._table.item(row, 0).data(Qt.UserRole)
        path = self._table.item(row, 0).text()
        a, u, r = scan_source(self._db, source_id, path)
        QMessageBox.information(
            self,
            "Scan Complete",
            f"Releases added: {a}  |  updated: {u}  |  removed: {r}",
        )
        self.refresh()
        self.sources_changed.emit()
