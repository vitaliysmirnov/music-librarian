from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QTableWidget, QTableWidgetItem, QFileDialog,
    QAbstractItemView, QHeaderView, QMessageBox, QCheckBox,
)

from src.scanner.scanner import scan_source
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
        layout.setContentsMargins(8, 8, 8, 8)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Path", "Enabled", "Available", "Last Scan"])
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

        btn_row = QHBoxLayout()
        add_btn = QPushButton("Add Source…")
        add_btn.clicked.connect(self._add_source)
        btn_row.addWidget(add_btn)

        remove_btn = QPushButton("Remove Source")
        remove_btn.clicked.connect(self._remove_source)
        btn_row.addWidget(remove_btn)

        scan_btn = QPushButton("Scan Selected")
        scan_btn.clicked.connect(self._scan_selected)
        btn_row.addWidget(scan_btn)

        btn_row.addStretch()
        layout.addLayout(btn_row)

    def refresh(self):
        sources = self._db.get_sources()
        self._table.setRowCount(0)
        for src in sources:
            row = self._table.rowCount()
            self._table.insertRow(row)

            path_item = QTableWidgetItem(src["path"])
            path_item.setData(Qt.UserRole, src["id"])
            self._table.setItem(row, 0, path_item)

            enabled_cb = QCheckBox()
            enabled_cb.setChecked(bool(src["enabled"]))
            enabled_cb.stateChanged.connect(
                lambda state, sid=src["id"]: self._toggle_enabled(sid, state)
            )
            self._table.setCellWidget(row, 1, enabled_cb)

            avail = "Yes" if src["is_available"] else "No"
            self._table.setItem(row, 2, QTableWidgetItem(avail))
            self._table.setItem(row, 3, QTableWidgetItem(src["last_scan"] or "—"))

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

    def _toggle_enabled(self, source_id: int, state: int):
        enabled = state == Qt.Checked.value if hasattr(Qt.Checked, "value") else bool(state)
        self._db.set_source_enabled(source_id, enabled)
        self.sources_changed.emit()
