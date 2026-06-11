from pathlib import Path

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QByteArray, QSortFilterProxyModel
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel,
    QPushButton, QTableView, QHeaderView, QAbstractItemView, QMenu,
)

from src.ui.edit_release_dialog import EditReleaseDialog
import os
import platform
import subprocess

COLUMNS = ["Artist", "Rec. Year", "Release", "Media",
           "Cat. No.", "Rel. Year", "Source", "Available", "Path"]
COL_ARTIST = 0
COL_YEAR_REC = 1
COL_TITLE = 2
COL_MEDIA = 3
COL_CATALOG = 4
COL_YEAR_REL = 5
COL_SOURCE = 6
COL_AVAIL = 7
COL_PATH = 8

SETTINGS_KEY = "releases_header_state"

# Natural tiebreaker order applied after the user-selected primary column
_TIEBREAKER = [COL_ARTIST, COL_YEAR_REC, COL_TITLE]


class ReleasesModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self._rows: list = []

    def load(self, rows):
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self._rows[index.row()]
        col = index.column()

        if role == Qt.DisplayRole:
            mapping = [
                row["artist"],
                row["year_recorded"],
                row["title"],
                row["media"] or "",
                row["catalog_number"] or "",
                row["year_released"] or "",
                Path(row["source_path"]).name,
                "Yes" if row["is_available"] else "No",
                row["folder_path"],
            ]
            return mapping[col]

        if role == Qt.ForegroundRole and not row["is_available"]:
            return QColor("#888888")

        if role == Qt.UserRole:
            return row

        return None

    def get_row(self, row_index) -> dict | None:
        r = self._rows[row_index] if row_index < len(self._rows) else None
        return dict(r) if r else None


class _MultiSortProxy(QSortFilterProxyModel):
    """
    Sorts by the user-selected column first, then falls back to
    artist → year_recorded → title as natural tiebreakers.
    """

    def __init__(self):
        super().__init__()
        self._primary_col: int | None = None
        self._primary_order = Qt.AscendingOrder

    def sort(self, column: int, order=Qt.AscendingOrder):
        self._primary_col = column if column >= 0 else None
        self._primary_order = order
        super().sort(column, order)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        src = self.sourceModel()

        def val(index: QModelIndex, col: int):
            if col == COL_AVAIL:
                row = src.data(src.index(index.row(), col), Qt.UserRole)
                # 0=Yes, 1=No so ascending puts Yes first
                return 0 if (row and row["is_available"]) else 1
            return (src.data(src.index(index.row(), col)) or "").lower()

        # Primary column: Qt handles direction by swapping args, so always compare ascending
        lv, rv = val(left, self._primary_col if self._primary_col is not None else 0), \
                 val(right, self._primary_col if self._primary_col is not None else 0)
        if lv != rv:
            return lv < rv

        # Tiebreakers always ascending
        for tb in _TIEBREAKER:
            if self._primary_col == tb:
                continue
            lv, rv = val(left, tb), val(right, tb)
            if lv != rv:
                return lv < rv

        return False


class ReleasesTab(QWidget):
    def __init__(self, db):
        super().__init__()
        self._db = db
        self._setup_ui()
        self._restore_header_state()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # Search bar
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Search:"))
        self._search = QLineEdit()
        self._search.setPlaceholderText("Artist, title, or both words…")
        self._search.textChanged.connect(self.refresh)
        filter_row.addWidget(self._search)

        self._clear_btn = QPushButton("Clear")
        self._clear_btn.clicked.connect(self._search.clear)
        filter_row.addWidget(self._clear_btn)
        filter_row.addStretch()

        self._count_label = QLabel("")
        filter_row.addWidget(self._count_label)
        layout.addLayout(filter_row)

        # Table
        self._model = ReleasesModel()
        self._proxy = _MultiSortProxy()
        self._proxy.setSourceModel(self._model)

        self._table = QTableView()
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.doubleClicked.connect(self._edit_release)
        self._table.verticalHeader().setVisible(False)

        hdr = self._table.horizontalHeader()
        hdr.setSectionsMovable(True)
        hdr.setSectionsClickable(True)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.resizeSection(COL_ARTIST,   160)
        hdr.resizeSection(COL_YEAR_REC,  72)
        hdr.resizeSection(COL_TITLE,    220)
        hdr.resizeSection(COL_MEDIA,     60)
        hdr.resizeSection(COL_CATALOG,  180)
        hdr.resizeSection(COL_YEAR_REL,  70)
        hdr.resizeSection(COL_SOURCE,   130)
        hdr.resizeSection(COL_AVAIL,     70)
        hdr.resizeSection(COL_PATH,     300)
        hdr.setContextMenuPolicy(Qt.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._show_header_menu)
        hdr.sectionMoved.connect(self._save_header_state)
        hdr.sectionResized.connect(self._save_header_state)

        layout.addWidget(self._table)

        # Bottom actions
        btn_row = QHBoxLayout()

        edit_btn = QPushButton("Edit…")
        edit_btn.setToolTip("Edit selected release metadata (double-click)")
        edit_btn.clicked.connect(self._edit_release)
        btn_row.addWidget(edit_btn)

        open_btn = QPushButton("Open Folder")
        open_btn.clicked.connect(self._open_release)
        btn_row.addWidget(open_btn)

        btn_row.addStretch()

        reset_btn = QPushButton("Reset View")
        reset_btn.setToolTip("Restore default column order and widths")
        reset_btn.clicked.connect(self._reset_header)
        btn_row.addWidget(reset_btn)

        layout.addLayout(btn_row)

    # ── Header context menu ────────────────────────────────────────────────

    def _show_header_menu(self, pos):
        hdr = self._table.horizontalHeader()
        menu = QMenu(self)
        for logical_idx, name in enumerate(COLUMNS):
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(not hdr.isSectionHidden(logical_idx))
            action.setData(logical_idx)
        chosen = menu.exec(hdr.mapToGlobal(pos))
        if chosen is not None:
            idx = chosen.data()
            hdr.setSectionHidden(idx, not chosen.isChecked())
            self._save_header_state()

    # ── Header state ───────────────────────────────────────────────────────

    def _save_header_state(self, *_):
        state: QByteArray = self._table.horizontalHeader().saveState()
        self._db.set_setting(SETTINGS_KEY, state.toBase64().data().decode())

    def _restore_header_state(self):
        raw = self._db.get_setting(SETTINGS_KEY, "")
        if not raw:
            return
        try:
            data = QByteArray.fromBase64(raw.encode())
            self._table.horizontalHeader().restoreState(data)
        except Exception:
            pass

    def _reset_header(self):
        hdr = self._table.horizontalHeader()
        for logical in range(len(COLUMNS)):
            visual = hdr.visualIndex(logical)
            if visual != logical:
                hdr.moveSection(visual, logical)
        defaults = [160, 72, 220, 60, 180, 70, 130, 70, 300]
        for logical, width in enumerate(defaults):
            hdr.setSectionHidden(logical, False)
            hdr.resizeSection(logical, width)
        self._save_header_state()

    # ── Data ───────────────────────────────────────────────────────────────

    def refresh(self):
        rows = self._db.get_releases(search=self._search.text().strip())
        self._model.load(rows)
        self._count_label.setText(f"Releases: {len(rows)}")

    def _selected_row(self) -> dict | None:
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            return None
        source_row = self._proxy.mapToSource(indexes[0]).row()
        return self._model.get_row(source_row)

    def _edit_release(self, *_):
        row = self._selected_row()
        if not row:
            return
        dlg = EditReleaseDialog(self._db, row, self)
        if dlg.exec() == EditReleaseDialog.Accepted:
            self.refresh()

    def _open_release(self, *_):
        row = self._selected_row()
        if row and row["is_available"]:
            p = row["folder_path"]
            if platform.system() == "Darwin":
                subprocess.Popen(["open", p])
            elif platform.system() == "Windows":
                os.startfile(p)
            else:
                subprocess.Popen(["xdg-open", p])
