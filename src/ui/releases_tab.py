import json
import platform
import re
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QByteArray, QIdentityProxyModel, QSortFilterProxyModel, QUrl, QMimeData, QPoint, QSize, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel,
    QPushButton, QTableView, QHeaderView, QAbstractItemView, QMenu,
    QApplication, QStyledItemDelegate, QStyleOptionViewItem, QStyle, QMessageBox,
    QInputDialog, QSplitter, QStackedWidget, QFileDialog,
)

from src.scanner.mask import DEFAULT_MASK, KNOWN_TOKENS, get_custom_tokens
from src.utils import open_path
from src.utils.audio import AUDIO_EXTENSIONS
from src.ui.edit_release_dialog import EditReleaseDialog
from src.ui.liked_view import LikedTracksView
from src.ui.playlist_view import PlaylistView
from src.ui.tracklist_popup import TracklistPopup
from src.ui.sidebar_panel import SidebarPanel
from src.ui.style import ElidedTooltipDelegate, ROW_HEIGHT, TABLE_STYLE, SEARCH_STYLE

# Column 0 is always the play button.
COL_PLAY = 0

# Human-readable headers and default widths for each known token, in
# the order they are looked up (actual display order follows the mask).
_TOKEN_HEADER: dict[str, str] = {
    "artist":         "Artist",
    "year_recorded":  "Rec. Year",
    "title":          "Release",
    "catalog_number": "Cat. No.",
    "media":          "Media",
    "year_released":  "Rel. Year",
}
_TOKEN_WIDTH: dict[str, int] = {
    "artist":         160,
    "year_recorded":  72,
    "title":          220,
    "catalog_number": 180,
    "media":          60,
    "year_released":  70,
}
_TOKEN_DB_KEY: dict[str, str] = {
    "artist":         "artist",
    "year_recorded":  "year_recorded",
    "title":          "title",
    "catalog_number": "catalog_number",
    "media":          "media",
    "year_released":  "year_released",
}

_TAIL_HEADERS = ["Disc", "Source", "Available", "Path"]
_TAIL_WIDTHS  = [40, 130, 70, 300]

_TIEBREAKER_TOKENS = ["artist", "year_recorded", "title"]

SETTINGS_KEY = "releases_header_state_v2"

_PLAY_WIDTH          = 38
_EXTRA_DEFAULT_WIDTH = 90

_NAV_PAGE = {
    "releases":  0,
    "liked":     1,
    "playlists": 2,
}


def _known_token_order(mask: str) -> list[str]:
    """Return KNOWN_TOKENS in the order they appear in the mask.
    Any known token absent from the mask is appended at the end."""
    seen: set[str] = set()
    ordered: list[str] = []
    for tok in re.findall(r"\{(\w+)\}", mask):
        if tok in KNOWN_TOKENS and tok not in seen:
            seen.add(tok)
            ordered.append(tok)
    for tok in _TOKEN_HEADER:          # stable fallback order
        if tok not in seen:
            ordered.append(tok)
    return ordered


def _extras_from_row(row) -> dict:
    try:
        return json.loads(row["extras"] or "{}")
    except Exception:
        return {}


def _audio_files(folder_path: str) -> list[Path]:
    folder = Path(folder_path)
    if not folder.is_dir():
        return []
    return sorted(
        f for f in folder.iterdir()
        if f.is_file()
        and not f.name.startswith("._")
        and f.suffix.lower() in AUDIO_EXTENSIONS
    )


def _audio_urls(folder_path: str) -> list[QUrl]:
    return [QUrl.fromLocalFile(str(f)) for f in _audio_files(folder_path)]


def _move_to_trash(path: str):
    """Move *path* to the system Trash (recoverable). Raises on failure."""
    if platform.system() == "Darwin":
        from AppKit import NSFileManager, NSURL  # pyobjc-framework-Cocoa
        url = NSURL.fileURLWithPath_(path)
        ok, _, err = NSFileManager.defaultManager().trashItemAtURL_resultingItemURL_error_(
            url, None, None
        )
        if not ok:
            raise OSError(str(err))
    elif platform.system() == "Windows":
        import ctypes
        # SHFileOperation with FO_DELETE + FOF_ALLOWUNDO moves to Recycle Bin
        from ctypes import wintypes
        class SHFILEOPSTRUCT(ctypes.Structure):
            _fields_ = [
                ("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT),
                ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                ("fFlags", wintypes.WORD), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR),
            ]
        FO_DELETE = 3
        FOF_ALLOWUNDO = 0x0040
        FOF_NOCONFIRMATION = 0x0010
        FOF_SILENT = 0x0004
        op = SHFILEOPSTRUCT()
        op.wFunc = FO_DELETE
        op.pFrom = path + "\0\0"
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        if result:
            raise OSError(f"SHFileOperation failed: {result}")
    else:
        result = subprocess.run(["gio", "trash", path], capture_output=True)
        if result.returncode != 0:
            raise OSError(result.stderr.decode())



class ReleasesModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self._rows: list = []
        self._token_order: list[str] = list(_TOKEN_HEADER)  # known tokens in mask order
        self._extra_tokens: list[str] = []

    # ── column layout ─────────────────────────────────────────────────────
    # Logical columns:
    #   0              → COL_PLAY (play button)
    #   1 … N_KNOWN    → known tokens in mask order
    #   N_KNOWN+1 …    → custom (extra) tokens
    #   … tail         → Disc, Source, Available, Path  (offsets 0-3)

    def _n_known(self) -> int:
        return len(self._token_order)

    def col_for_token(self, token: str) -> int:
        """Return the logical column index for a known token (1-based after COL_PLAY)."""
        return 1 + self._token_order.index(token)

    def _tail_base(self) -> int:
        return 1 + self._n_known() + len(self._extra_tokens)

    def _col_disc(self) -> int:
        return self._tail_base()

    def _col_source(self) -> int:
        return self._tail_base() + 1

    def _col_avail(self) -> int:
        return self._tail_base() + 2

    def _col_path(self) -> int:
        return self._tail_base() + 3

    def _all_headers(self) -> list[str]:
        known_hdrs  = [_TOKEN_HEADER[t] for t in self._token_order]
        extra_hdrs  = [t.replace("_", " ").title() for t in self._extra_tokens]
        return [""] + known_hdrs + extra_hdrs + _TAIL_HEADERS

    # ── QAbstractTableModel interface ─────────────────────────────────────

    def load(self, rows, token_order: list[str], extra_tokens: list[str]):
        self.beginResetModel()
        self._rows = [r if isinstance(r, dict) else dict(r) for r in rows]
        self._token_order = token_order
        self._extra_tokens = extra_tokens
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 1 + self._n_known() + len(self._extra_tokens) + len(_TAIL_HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            headers = self._all_headers()
            if section < len(headers):
                return headers[section]

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row  = self._rows[index.row()]
        col  = index.column()
        n_kn = self._n_known()
        n_ex = len(self._extra_tokens)

        if role == Qt.DisplayRole:
            if col == COL_PLAY:
                return None
            if col <= n_kn:                      # known token columns (1-based)
                token = self._token_order[col - 1]
                return row.get(_TOKEN_DB_KEY[token]) or ""
            extra_i = col - 1 - n_kn
            if extra_i < n_ex:                   # custom token columns
                token = self._extra_tokens[extra_i]
                return _extras_from_row(row).get(token, "")
            tail = col - 1 - n_kn - n_ex
            if tail == 0:                        # Disc
                if row.get("is_multi_disc"):
                    return ""
                dn = row.get("disc_number") or 1
                return str(dn)
            if tail == 1:
                return Path(row["source_path"]).name
            if tail == 2:
                return "Yes" if row["is_available"] else "No"
            if tail == 3:
                return row["folder_path"]

        if role == Qt.ForegroundRole and not row["is_available"]:
            return QColor("#888888")

        if role == Qt.BackgroundRole and row.get("_is_disc_child"):
            from PySide6.QtWidgets import QApplication
            from PySide6.QtGui import QPalette
            base = QApplication.palette().color(QPalette.ColorRole.Base)
            delta = -15 if base.lightness() > 128 else 15
            return QColor(
                max(0, min(255, base.red()   + delta)),
                max(0, min(255, base.green() + delta)),
                max(0, min(255, base.blue()  + delta)),
            )

        if role == Qt.UserRole:
            return row

        return None

    def get_row(self, row_index) -> dict | None:
        r = self._rows[row_index] if row_index < len(self._rows) else None
        return dict(r) if r else None

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction

    def mimeData(self, indexes):
        seen_rows = set()
        urls = []
        for index in indexes:
            row_i = index.row()
            if row_i in seen_rows:
                continue
            seen_rows.add(row_i)
            row = self._rows[row_i]
            if row["is_available"] and not row.get("is_multi_disc"):
                urls.append(QUrl.fromLocalFile(row["folder_path"]))
        mime = QMimeData()
        mime.setUrls(urls)
        return mime


class _PlayButtonDelegate(ElidedTooltipDelegate):
    def __init__(self, db, toggle_expand_cb=None, proxy=None, artist_col_fn=None,
                 play_cb=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._toggle_expand_cb = toggle_expand_cb
        self._proxy = proxy
        self._artist_col_fn = artist_col_fn
        self._play_cb = play_cb

    def _proxy_artist(self, proxy_row: int) -> str:
        if self._proxy is None or self._artist_col_fn is None:
            return ""
        try:
            col = self._artist_col_fn()
        except (ValueError, IndexError):
            return ""
        idx = self._proxy.index(proxy_row, col)
        return (idx.data(Qt.DisplayRole) or "").strip()

    def _is_group_start(self, proxy_row: int) -> bool:
        """True when this row begins a new artist group (different artist than the row above)."""
        if self._proxy is None or self._artist_col_fn is None:
            return False
        if proxy_row == 0:
            return True
        return self._proxy_artist(proxy_row) != self._proxy_artist(proxy_row - 1)

    def paint(self, painter, option, index):
        proxy_row = index.row()
        is_start = self._is_group_start(proxy_row)

        if index.column() == COL_PLAY:
            # Paint the standard cell background (alternating rows, selection).
            super().paint(painter, option, index)

            row = index.data(Qt.UserRole)
            if row:
                if row.get("is_multi_disc"):
                    icon = "•"
                elif row["is_available"]:
                    icon = "▶"
                else:
                    icon = None

                if icon is not None:
                    painter.save()
                    if option.state & QStyle.State_MouseOver:
                        painter.fillRect(option.rect, option.palette.highlight().color().lighter(175))
                    painter.setPen(option.palette.text().color())
                    painter.drawText(option.rect, Qt.AlignCenter, icon)
                    painter.restore()

            return

        try:
            artist_col = self._artist_col_fn() if self._artist_col_fn else -1
        except (ValueError, IndexError):
            artist_col = -1

        if artist_col >= 0 and index.column() == artist_col and not is_start:
            # Same artist as the row above — draw the cell without text.
            opt = QStyleOptionViewItem(option)
            self.initStyleOption(opt, index)
            opt.text = ""
            QApplication.style().drawControl(QStyle.CE_ItemViewItem, opt, painter)
        else:
            super().paint(painter, option, index)

    def sizeHint(self, option, index):
        if index.column() == COL_PLAY:
            return QSize(_PLAY_WIDTH, ROW_HEIGHT)
        return super().sizeHint(option, index)

    def helpEvent(self, event, view, option, index):
        from PySide6.QtCore import QEvent
        if event and event.type() == QEvent.Type.ToolTip:
            if index.column() == COL_PLAY:
                return False
            try:
                artist_col = self._artist_col_fn() if self._artist_col_fn else -1
            except (ValueError, IndexError):
                artist_col = -1
            if artist_col >= 0 and index.column() == artist_col \
                    and not self._is_group_start(index.row()):
                return False
        return super().helpEvent(event, view, option, index)

    def editorEvent(self, event, model, option, index):
        from PySide6.QtCore import QEvent
        if index.column() == COL_PLAY and event.type() == QEvent.Type.MouseButtonRelease:
            row = index.data(Qt.UserRole)
            if row:
                if row.get("is_multi_disc"):
                    if self._toggle_expand_cb:
                        self._toggle_expand_cb(row["folder_path"])
                elif row["is_available"]:
                    if self._play_cb:
                        self._play_cb(row)
            return True
        return super().editorEvent(event, model, option, index)


class _MultiSortProxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self._primary_col: int | None = None
        self._primary_order: Qt.SortOrder = Qt.AscendingOrder

    def _src(self) -> ReleasesModel:
        return self.sourceModel()

    def sort(self, column: int, order=Qt.AscendingOrder):
        if column == COL_PLAY:
            return
        self._primary_col = column if column >= 0 else None
        self._primary_order = order
        super().sort(column, order)

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:
        src = self._src()

        left_row  = src.data(src.index(left.row(),  COL_PLAY), Qt.UserRole)
        right_row = src.data(src.index(right.row(), COL_PLAY), Qt.UserRole)

        avail_col = src._col_avail()

        def val(index: QModelIndex, col: int) -> tuple:
            if col == avail_col:
                row = src.data(src.index(index.row(), col), Qt.UserRole)
                return (0, 0 if (row and row["is_available"]) else 1, "")
            raw = (src.data(src.index(index.row(), col)) or "").strip()
            if not raw:
                return (2, 0.0, "")
            try:
                return (0, float(raw), "")
            except ValueError:
                return (1, 0.0, raw.lower())

        default_primary = src.col_for_token("artist")
        primary = self._primary_col if self._primary_col is not None else default_primary
        lv, rv = val(left, primary), val(right, primary)
        if lv != rv:
            return lv < rv

        for tok in _TIEBREAKER_TOKENS:
            tb_col = src.col_for_token(tok)
            if primary == tb_col:
                continue
            lv, rv = val(left, tb_col), val(right, tb_col)
            if lv != rv:
                return lv < rv

        # Final tiebreaker: multi-disc container sorts before its disc children.
        # Use is_multi_disc flag (not disc_number) so this holds even if disc_number
        # was not yet updated to 0 in an older DB entry.
        def _sort_dn(row) -> int:
            if row is None:
                return 0
            if row.get("is_multi_disc"):
                return -1  # container always before disc children
            return row.get("disc_number") or 0

        return _sort_dn(left_row) < _sort_dn(right_row)


class _DragTableView(QTableView):
    def __init__(self, disc_entries_fn=None):
        super().__init__()
        self._drag_start: QPoint | None = None
        self._disc_entries_fn = disc_entries_fn

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_start is None or not (event.buttons() & Qt.MouseButton.LeftButton):
            super().mouseMoveEvent(event)
            return

        pressed_index = self.indexAt(self._drag_start)
        if pressed_index.isValid() and pressed_index.column() == COL_PLAY:
            row = pressed_index.data(Qt.UserRole)
            if not (row and row.get("is_multi_disc")):
                super().mouseMoveEvent(event)
                return

        if (event.pos() - self._drag_start).manhattanLength() < QApplication.startDragDistance():
            return

        press_pos = self._drag_start
        self._drag_start = None
        self._exec_drag(press_pos)

    def _exec_drag(self, press_pos: QPoint):
        proxy_index = self.indexAt(press_pos)
        if not proxy_index.isValid():
            return

        selected_proxy_rows = {
            idx.row() for idx in self.selectionModel().selectedRows()
        }
        if proxy_index.row() not in selected_proxy_rows:
            selected_proxy_rows = {proxy_index.row()}

        source_model = self.model().sourceModel()
        urls: list[QUrl] = []
        for proxy_row in sorted(selected_proxy_rows):
            source_index = self.model().mapToSource(
                self.model().index(proxy_row, 0)
            )
            row = source_model.get_row(source_index.row())
            if not row:
                continue
            if row.get("is_multi_disc") and self._disc_entries_fn:
                for child in self._disc_entries_fn(row["folder_path"]):
                    if child["is_available"]:
                        urls.extend(_audio_urls(child["folder_path"]))
            elif row["is_available"]:
                urls.extend(_audio_urls(row["folder_path"]))

        if not urls:
            return

        mime = QMimeData()
        mime.setUrls(urls)
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def mouseReleaseEvent(self, event):
        self._drag_start = None
        super().mouseReleaseEvent(event)


class _SeparatorHeader(QHeaderView):
    """Horizontal header that draws a 1-px right-side separator after a specified column."""

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self._sep_col: int = -1

    def set_separator_column(self, logical: int):
        self._sep_col = logical


def _make_stub(title: str) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lbl = QLabel(title)
    lbl.setAlignment(Qt.AlignCenter)
    lbl.setStyleSheet("font-size: 24px; font-weight: 600; color: palette(placeholderText);")
    lay.addWidget(lbl)
    sub = QLabel("Coming soon")
    sub.setAlignment(Qt.AlignCenter)
    sub.setStyleSheet("font-size: 13px; color: palette(placeholderText);")
    lay.addWidget(sub)
    return w


class _ReleasesView(QWidget):
    """Unified releases table — shared by Recently Added and Releases."""

    play_requested            = Signal(dict)
    enqueue_requested         = Signal(dict)
    play_track_requested      = Signal(list, dict)
    enqueue_track_requested   = Signal(list, dict)
    release_trashed           = Signal()
    release_moved             = Signal(str)  # dest_dir chosen by the user
    column_visibility_changed = Signal(int, bool)  # logical_idx, hidden
    liked_changed             = Signal()
    playlist_track_added      = Signal(int)  # playlist_id

    def __init__(self, db, query_fn, *,
                 sortable: bool = True,
                 expandable: bool = True,
                 show_bottom_bar: bool = True,
                 count_label_fn=None,
                 settings_key: str = SETTINGS_KEY,
                 parent=None):
        super().__init__(parent)
        self._db             = db
        self._query_fn       = query_fn
        self._sortable       = sortable
        self._expandable     = expandable
        self._settings_key   = settings_key
        self._expanded: set[str] = set()
        self._header_state: QByteArray | None = None
        self._tracklist_popup: "TracklistPopup | None" = None
        self._count_label_fn = count_label_fn or (lambda n: f"Releases: {n}")
        self._setup_ui(show_bottom_bar)

    # ── UI setup ──────────────────────────────────────────────────────────────

    def _setup_ui(self, show_bottom_bar: bool):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._model = ReleasesModel()
        self._proxy = _MultiSortProxy() if self._sortable else QIdentityProxyModel()
        self._proxy.setSourceModel(self._model)

        self._table = _DragTableView(
            disc_entries_fn=self._db.get_disc_entries if self._expandable else None,
        )
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(self._sortable)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(False)
        self._table.setShowGrid(False)
        self._table.setStyleSheet(TABLE_STYLE)
        self._table.doubleClicked.connect(self._on_double_click)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)
        vhdr = self._table.verticalHeader()
        vhdr.setVisible(False)
        vhdr.setDefaultSectionSize(ROW_HEIGHT)
        vhdr.setMinimumSectionSize(ROW_HEIGHT)
        self._table.setDragEnabled(True)
        self._table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self._table.setDefaultDropAction(Qt.DropAction.CopyAction)
        self._table.setMouseTracking(True)

        self._delegate = _PlayButtonDelegate(
            self._db,
            toggle_expand_cb=self._toggle_expand if self._expandable else None,
            proxy=self._proxy,
            artist_col_fn=lambda: self._model.col_for_token("artist"),
            play_cb=self._on_play_button,
            parent=self._table,
        )
        self._table.setItemDelegate(self._delegate)

        self._sep_header = _SeparatorHeader(self._table)
        self._table.setHorizontalHeader(self._sep_header)
        hdr = self._sep_header
        hdr.setSectionsMovable(True)
        hdr.setSectionsClickable(self._sortable)
        hdr.setSortIndicatorShown(False)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setSectionResizeMode(COL_PLAY, QHeaderView.Interactive)
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hdr.sectionResized.connect(self._save_header_state)
        hdr.setContextMenuPolicy(Qt.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._show_header_menu)
        if self._sortable:
            hdr.sectionMoved.connect(self._on_section_moved)
            hdr.sectionClicked.connect(self._on_header_clicked)
        else:
            hdr.sectionMoved.connect(self._save_header_state)

        layout.addWidget(self._table)

        if show_bottom_bar:
            trash_sc = QShortcut(QKeySequence("Ctrl+Backspace"), self._table)
            trash_sc.setContext(Qt.WidgetWithChildrenShortcut)
            trash_sc.activated.connect(self._trash_release)

        bottom_bar = QWidget()
        bb = QHBoxLayout(bottom_bar)
        bb.setContentsMargins(8, 4, 8, 4)
        bb.setSpacing(4)

        if show_bottom_bar:
            edit_btn = QPushButton("Release Info")
            edit_btn.setToolTip("View/edit release info (double-click)")
            edit_btn.clicked.connect(self._edit_release)
            bb.addWidget(edit_btn)
            open_btn = QPushButton("Open Folder")
            open_btn.clicked.connect(self._open_release)
            bb.addWidget(open_btn)
            bb.addStretch()

        self._count_label = QLabel("")
        self._count_label.setStyleSheet("font-size: 11px;")
        bb.addWidget(self._count_label)
        bb.addStretch()

        if show_bottom_bar:
            drag_hint = QLabel("▶ to play or drag to player")
            drag_hint.setStyleSheet("color: palette(placeholderText); font-size: 11px;")
            bb.addWidget(drag_hint)

        reset_btn = QPushButton("Reset View")
        reset_btn.setToolTip("Restore default column order and widths")
        reset_btn.clicked.connect(self._reset_header)
        bb.addWidget(reset_btn)

        layout.addWidget(bottom_bar)

    # ── Play button ───────────────────────────────────────────────────────────

    def _on_play_button(self, row: dict):
        self.play_requested.emit(row)

    # ── Selection ─────────────────────────────────────────────────────────────

    def _selected_row(self) -> dict | None:
        indexes = self._table.selectionModel().selectedRows()
        if not indexes:
            return None
        source_row = self._proxy.mapToSource(indexes[0]).row()
        return self._model.get_row(source_row)

    # ── Double-click ──────────────────────────────────────────────────────────

    def _on_double_click(self, proxy_index):
        if proxy_index.column() == COL_PLAY:
            return
        self._show_tracklist()

    # ── Tracklist ─────────────────────────────────────────────────────────────

    def _show_tracklist(self):
        row = self._selected_row()
        if not row:
            return
        if not row.get("is_available", True):
            artist = row.get("artist", "")
            title  = row.get("title", "")
            label  = f"«{artist} — {title}»" if artist and title else f"«{title or artist}»"
            QMessageBox.information(
                self, "Source Disconnected",
                f"{label}\n\nThis release's source drive is currently disconnected.\n"
                "Reconnect it to view the tracklist.",
            )
            return
        if self._tracklist_popup is not None:
            self._tracklist_popup.close()
        self._tracklist_popup = TracklistPopup(row, self._db, self.window())
        self._tracklist_popup.play_track.connect(self.play_track_requested)
        self._tracklist_popup.enqueue_track.connect(self.enqueue_track_requested)
        self._tracklist_popup.liked_changed.connect(self.liked_changed)
        self._tracklist_popup.playlist_track_added.connect(self.playlist_track_added)
        self._tracklist_popup.finished.connect(lambda: setattr(self, "_tracklist_popup", None))
        self._tracklist_popup.show()

    def sync_popup_like(self, path: str, liked: bool) -> None:
        if self._tracklist_popup is not None:
            self._tracklist_popup.sync_like(path, liked)

    def refresh_popup_likes(self) -> None:
        if self._tracklist_popup is not None:
            self._tracklist_popup.refresh_likes()

    def clear_selection(self) -> None:
        self._table.selectionModel().clearSelection()

    def collapse_all(self) -> None:
        if self._expanded:
            self._expanded.clear()
            self.refresh()

    def _select_src_row(self, src_row: int) -> None:
        src_idx   = self._model.index(src_row, 0)
        proxy_idx = self._proxy.mapFromSource(src_idx)
        if proxy_idx.isValid():
            self._table.selectionModel().select(
                proxy_idx,
                self._table.selectionModel().SelectionFlag.ClearAndSelect |
                self._table.selectionModel().SelectionFlag.Rows,
            )
            self._table.scrollTo(proxy_idx, QAbstractItemView.ScrollHint.PositionAtCenter)

    def select_release(self, folder_path: str, allow_expand: bool = True) -> None:
        # Direct match — regular releases, already-expanded disc children, or multi-disc parents.
        for src_row, row in enumerate(self._model._rows):
            if row.get("folder_path") == folder_path:
                if (allow_expand and self._expandable and row.get("is_multi_disc")
                        and folder_path not in self._expanded):
                    # Collapsed multi-disc container — expand it first, then re-find.
                    self._expanded.add(folder_path)
                    self.refresh()
                    for src_row2, row2 in enumerate(self._model._rows):
                        if row2.get("folder_path") == folder_path:
                            self._select_src_row(src_row2)
                            return
                    return
                self._select_src_row(src_row)
                return

        # Not found — folder_path is a disc child inside a collapsed multi-disc parent.
        # Only re-expand when allow_expand is True (explicit navigation, not selection restore).
        if not self._expandable or not allow_expand:
            return
        for src_row, row in enumerate(self._model._rows):
            if not row.get("is_multi_disc"):
                continue
            disc_entries = self._db.get_disc_entries(row["folder_path"])
            if any(d["folder_path"] == folder_path for d in disc_entries):
                self._expanded.add(row["folder_path"])
                self.refresh()
                for src_row2, row2 in enumerate(self._model._rows):
                    if row2.get("folder_path") == folder_path:
                        self._select_src_row(src_row2)
                        return
                break

    # ── Row context menu ───────────────────────────────────────────────────────

    def _show_context_menu(self, pos):
        proxy_index = self._table.indexAt(pos)
        if not proxy_index.isValid():
            return
        self._table.selectionModel().setCurrentIndex(
            proxy_index,
            self._table.selectionModel().SelectionFlag.ClearAndSelect |
            self._table.selectionModel().SelectionFlag.Rows,
        )
        row = self._selected_row()
        if not row:
            return

        available    = bool(row["is_available"])
        is_container = bool(row.get("is_multi_disc"))

        menu = QMenu(self)

        act_play_now = menu.addAction("Play Now")
        act_play_now.setEnabled(available)
        act_enqueue = menu.addAction("Add to Queue")
        act_enqueue.setEnabled(available)

        menu.addSeparator()
        act_open = menu.addAction("Open Folder")
        act_open.setEnabled(available)

        menu.addSeparator()
        act_edit = menu.addAction("Release Info")
        act_edit.setEnabled(available)
        act_tracklist = menu.addAction("Tracklist")
        act_tracklist.setEnabled(available)

        is_disc_child = bool(row.get("_is_disc_child"))
        menu.addSeparator()
        act_move = menu.addAction("Move Release…")
        act_move.setEnabled(available and not is_disc_child)
        act_move_artist = menu.addAction("Move Artist's Releases…")
        act_move_artist.setEnabled(available and not is_disc_child)

        menu.addSeparator()
        act_delete = menu.addAction("Move to Trash")

        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_play_now:
            if is_container:
                self._play_or_enqueue_container(row, play=True)
            else:
                self.play_requested.emit(row)
        elif chosen == act_enqueue:
            if is_container:
                self._play_or_enqueue_container(row, play=False)
            else:
                self.enqueue_requested.emit(row)
        elif chosen == act_open:
            self._open_release()
        elif chosen == act_edit:
            self._edit_release()
        elif chosen == act_tracklist:
            self._show_tracklist()
        elif chosen == act_move:
            self._move_release()
        elif chosen == act_move_artist:
            self._move_artist_releases()
        elif chosen == act_delete:
            self._trash_release()

    def _play_or_enqueue_container(self, row: dict, play: bool):
        paths = []
        for child in self._db.get_disc_entries(row["folder_path"]):
            if child["is_available"]:
                paths.extend(_audio_files(child["folder_path"]))
        if not paths:
            return
        paths = [str(p) for p in paths]
        if play:
            self.play_track_requested.emit(paths, row)
        else:
            self.enqueue_track_requested.emit(paths, row)

    # ── Header context menu ────────────────────────────────────────────────────

    def _show_header_menu(self, pos):
        hdr = self._table.horizontalHeader()
        headers = self._model._all_headers()
        menu = QMenu(self)
        for logical_idx, name in enumerate(headers):
            if logical_idx == COL_PLAY:
                continue
            label = name if name else f"Column {logical_idx}"
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not hdr.isSectionHidden(logical_idx))
            action.setData(logical_idx)
        chosen = menu.exec(hdr.mapToGlobal(pos))
        if chosen is not None:
            logical_idx = chosen.data()
            hidden = not chosen.isChecked()
            hdr.setSectionHidden(logical_idx, hidden)
            self._save_header_state()
            self.column_visibility_changed.emit(logical_idx, hidden)

    def _apply_column_visibility(self, logical_idx: int, hidden: bool):
        hdr = self._sep_header
        if logical_idx < self._model.columnCount() and hdr.isSectionHidden(logical_idx) != hidden:
            hdr.setSectionHidden(logical_idx, hidden)
            self._save_header_state()

    # ── Header state ───────────────────────────────────────────────────────────

    def _on_section_moved(self, logical, old_visual, new_visual):
        hdr = self._sep_header
        try:
            artist_col = self._model.col_for_token("artist")
        except (ValueError, IndexError):
            artist_col = -1

        fixed = {COL_PLAY, artist_col} if artist_col >= 0 else {COL_PLAY}

        correction = None
        if logical == COL_PLAY and new_visual != 1:
            correction = (new_visual, 1)
        elif artist_col >= 0 and logical == artist_col and new_visual != 0:
            correction = (new_visual, 0)
        elif new_visual in (0, 1) and logical not in fixed:
            correction = (new_visual, old_visual)

        if correction is not None:
            hdr.blockSignals(True)
            try:
                hdr.moveSection(*correction)
            finally:
                hdr.blockSignals(False)
            return
        self._save_header_state()

    def _on_header_clicked(self, logical: int):
        if logical == COL_PLAY:
            col = self._proxy._primary_col
            if col is None or col == COL_PLAY:
                col = COL_PLAY + 1
            self._table.horizontalHeader().setSortIndicator(col, self._proxy._primary_order)

    def _save_header_state(self, *_):
        state: QByteArray = self._table.horizontalHeader().saveState()
        if self._header_state is not None and state == self._header_state:
            return
        self._header_state = state
        self._db.set_setting(self._settings_key, state.toBase64().data().decode())

    def _restore_header_state(self):
        if self._header_state is None:
            raw = self._db.get_setting(self._settings_key, "")
            if not raw:
                return
            try:
                self._header_state = QByteArray.fromBase64(raw.encode())
            except Exception:
                return
        try:
            self._sep_header.restoreState(self._header_state)
        except Exception:
            pass
        hdr = self._sep_header
        hdr.setSectionResizeMode(COL_PLAY, QHeaderView.Interactive)
        hdr.resizeSection(COL_PLAY, _PLAY_WIDTH)
        try:
            artist_col = self._model.col_for_token("artist")
            if hdr.visualIndex(artist_col) != 0 or hdr.visualIndex(COL_PLAY) != 1:
                self._apply_canonical_visual_order()
                self._save_header_state()
            else:
                self._sep_header.set_separator_column(artist_col)
        except (ValueError, IndexError):
            pass
        if self._sortable and hdr.sortIndicatorSection() == COL_PLAY:
            hdr.setSortIndicator(COL_PLAY + 1, Qt.AscendingOrder)
            self._save_header_state()

    def sync_header(self):
        """Re-apply header state without reloading data (call when switching to this view)."""
        if self._model.columnCount() == 0:
            return
        self._restore_header_state()

    def invalidate_header_cache(self):
        self._header_state = None

    def invalidate_header_state(self):
        self._header_state = None
        self._db.set_setting(self._settings_key, "")

    def _reset_header(self):
        self._apply_default_widths()
        hdr = self._sep_header
        for i in range(self._model.columnCount()):
            hdr.setSectionHidden(i, False)
        self._save_header_state()

    def _apply_canonical_visual_order(self):
        """Set Artist at visual 0, PLAY at visual 1 without triggering section-moved constraints."""
        hdr = self._sep_header
        try:
            artist_col = self._model.col_for_token("artist")
        except (ValueError, IndexError):
            return
        if self._sortable:
            hdr.sectionMoved.disconnect(self._on_section_moved)
        try:
            n = self._model.columnCount()
            for logical in range(n):
                vis = hdr.visualIndex(logical)
                if vis != logical:
                    hdr.moveSection(vis, logical)
            hdr.moveSection(hdr.visualIndex(artist_col), 0)
        finally:
            if self._sortable:
                hdr.sectionMoved.connect(self._on_section_moved)
        self._sep_header.set_separator_column(artist_col)

    def _apply_default_widths(self):
        hdr = self._sep_header
        self._apply_canonical_visual_order()
        hdr.resizeSection(COL_PLAY, _PLAY_WIDTH)
        hdr.setSectionResizeMode(COL_PLAY, QHeaderView.Interactive)
        for i, tok in enumerate(self._model._token_order):
            hdr.resizeSection(1 + i, _TOKEN_WIDTH.get(tok, 100))
        n_kn = self._model._n_known()
        for i in range(len(self._model._extra_tokens)):
            hdr.resizeSection(1 + n_kn + i, _EXTRA_DEFAULT_WIDTH)
        for i, w in enumerate(_TAIL_WIDTHS):
            hdr.resizeSection(1 + n_kn + len(self._model._extra_tokens) + i, w)

    # ── Expand / collapse ─────────────────────────────────────────────────────

    def _toggle_expand(self, folder_path: str):
        if folder_path in self._expanded:
            self._expanded.discard(folder_path)
        else:
            self._expanded.add(folder_path)
        self.refresh()
        QTimer.singleShot(0, lambda: self.select_release(folder_path, allow_expand=False))

    # ── Release actions ────────────────────────────────────────────────────────

    def _edit_release(self, *_):
        row = self._selected_row()
        if not row or not row["is_available"]:
            return
        dlg = EditReleaseDialog(self._db, row, self)
        if dlg.exec() == EditReleaseDialog.Accepted:
            self.refresh()

    def _open_release(self, *_):
        row = self._selected_row()
        if not row or not row["is_available"]:
            return
        open_path(row["folder_path"])

    def _trash_release(self):
        row = self._selected_row()
        if not row:
            return
        if row.get("_is_disc_child"):
            QMessageBox.information(
                self, "Move to Trash",
                "Select the parent release to delete a multi-disc release."
            )
            return
        folder_path   = row["folder_path"]
        folder_exists = Path(folder_path).exists()
        artist = row.get("artist", "")
        title  = row.get("title", "")
        label  = f"{artist} — {title}" if artist and title else folder_path

        if folder_exists:
            msg = QMessageBox(self)
            msg.setWindowTitle("Move to Trash")
            msg.setText(f"Move to Trash:\n{label}")
            msg.setInformativeText("The folder will be moved to the Trash. This can be undone.")
            msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
            msg.setDefaultButton(QMessageBox.Cancel)
            if msg.exec() != QMessageBox.Ok:
                return
            try:
                _move_to_trash(folder_path)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not move to Trash:\n{e}")
                return

        self._db.delete_release_by_path(folder_path)
        self.refresh()
        self.release_trashed.emit()

    def _move_release(self):
        row = self._selected_row()
        if not row or row.get("_is_disc_child"):
            return

        folder_path = row["folder_path"]
        dest_dir = QFileDialog.getExistingDirectory(
            self, "Move Release To…", str(Path(folder_path).parent)
        )
        if not dest_dir:
            return

        folder_name = Path(folder_path).name
        new_folder = str(Path(dest_dir) / folder_name)
        if Path(new_folder) == Path(folder_path):
            return

        if Path(new_folder).exists():
            QMessageBox.warning(self, "Move Release",
                f"'{folder_name}' already exists at the destination.")
            return

        try:
            shutil.move(folder_path, new_folder)
        except Exception as e:
            QMessageBox.warning(self, "Move Release", f"Could not move:\n{e}")
            return

        self._db.move_release_path(folder_path, new_folder)
        self.release_moved.emit(dest_dir)
        self.refresh()

    def _move_artist_releases(self):
        row = self._selected_row()
        if not row:
            return

        artist = row.get("artist", "")
        if not artist:
            return

        releases = [dict(r) for r in self._db.get_releases_by_artist(artist)]
        if not releases:
            return

        parents = {str(Path(r["folder_path"]).parent) for r in releases}

        if len(parents) == 1:
            # All releases share one directory → move the whole artist directory.
            artist_dir = next(iter(parents))
            dest_parent = QFileDialog.getExistingDirectory(
                self, f"Move {artist}'s Releases To…",
                str(Path(artist_dir).parent),
            )
            if not dest_parent:
                return

            artist_dir_name = Path(artist_dir).name
            new_artist_dir = str(Path(dest_parent) / artist_dir_name)
            if Path(new_artist_dir) == Path(artist_dir):
                return

            if Path(new_artist_dir).exists():
                QMessageBox.warning(self, "Move Artist's Releases",
                    f"'{artist_dir_name}' already exists at the destination.")
                return

            try:
                shutil.move(artist_dir, new_artist_dir)
            except Exception as e:
                QMessageBox.warning(self, "Move Artist's Releases",
                    f"Could not move:\n{e}")
                return

            for rel in releases:
                old = rel["folder_path"]
                new = str(Path(new_artist_dir) / Path(old).name)
                self._db.move_release_path(old, new)

            self.release_moved.emit(dest_parent)
            self.refresh()
        else:
            # Releases spread across multiple directories → move each folder
            # individually into dest_parent/artist/.
            dest_parent = QFileDialog.getExistingDirectory(
                self, f"Move {artist}'s Releases To…",
            )
            if not dest_parent:
                return

            artist_dest = Path(dest_parent) / artist
            errors: list[str] = []
            moved_any = False

            for rel in releases:
                old = rel["folder_path"]
                new = str(artist_dest / Path(old).name)
                if Path(new).exists():
                    errors.append(f"'{Path(old).name}' already exists, skipped.")
                    continue
                try:
                    artist_dest.mkdir(parents=True, exist_ok=True)
                    shutil.move(old, new)
                    self._db.move_release_path(old, new)
                    moved_any = True
                except Exception as e:
                    errors.append(f"'{Path(old).name}': {e}")

            if errors:
                QMessageBox.warning(self, "Move Artist's Releases",
                    "Some releases could not be moved:\n" + "\n".join(errors))
            if moved_any:
                self.release_moved.emit(dest_parent)
                self.refresh()

    # ── Data ──────────────────────────────────────────────────────────────────

    def refresh(self, token_order: list | None = None, extra_tokens: list | None = None):
        if token_order is None:
            mask = self._db.get_setting("folder_mask", DEFAULT_MASK)
            token_order  = _known_token_order(mask)
            extra_tokens = get_custom_tokens(mask)
        extra_tokens = extra_tokens or []

        top_rows = list(self._query_fn())

        if self._expandable:
            flat: list[dict] = []
            for raw in top_rows:
                row = dict(raw)
                if row.get("is_multi_disc"):
                    expanded = row["folder_path"] in self._expanded
                    row["_is_expanded"] = expanded
                    flat.append(row)
                    if expanded:
                        for child_raw in self._db.get_disc_entries(row["folder_path"]):
                            child = dict(child_raw)
                            child["_is_disc_child"] = True
                            flat.append(child)
                else:
                    row["_is_expanded"] = False
                    flat.append(row)
        else:
            flat = [dict(r) for r in top_rows]

        selected = self._selected_row()
        selected_path = selected.get("folder_path") if selected else None

        prev_n = self._model.columnCount()
        self._model.load(flat, token_order, extra_tokens)
        if self._model.columnCount() != prev_n:
            self._apply_default_widths()
        else:
            self._restore_header_state()
        hdr = self._table.horizontalHeader()
        hdr.resizeSection(COL_PLAY, _PLAY_WIDTH)
        hdr.setSectionResizeMode(COL_PLAY, QHeaderView.Interactive)
        self._count_label.setText(self._count_label_fn(len(top_rows)))

        if selected_path:
            self.select_release(selected_path, allow_expand=False)


class ReleasesTab(QWidget):
    release_trashed         = Signal()
    release_moved           = Signal(str)  # dest_dir chosen by the user
    play_requested          = Signal(dict)
    enqueue_requested       = Signal(dict)
    play_track_requested    = Signal(list, dict)
    enqueue_track_requested = Signal(list, dict)
    liked_changed           = Signal()
    go_to_release           = Signal(str)   # folder_path
    playlist_track_added    = Signal(int)   # playlist_id
    playlists_changed       = Signal(list)  # list[dict] — id, name
    playlist_renamed        = Signal(int, str)  # playlist_id, new_name

    def __init__(self, db):
        super().__init__()
        self._db = db
        self._setup_ui()

    def _setup_ui(self):
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet("QSplitter::handle { background: palette(mid); }")
        outer.addWidget(splitter)

        # ── Left column: search bar + sidebar nav ─────────────────────────
        left_col = QWidget()
        left_col.setObjectName("LeftCol")
        left_col.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        left_col.setStyleSheet("#LeftCol { background: palette(window); }")
        left_col.setMinimumWidth(140)
        left_col.setMaximumWidth(260)
        lc_layout = QVBoxLayout(left_col)
        lc_layout.setContentsMargins(0, 0, 0, 0)
        lc_layout.setSpacing(0)

        search_wrap = QWidget()
        sw = QHBoxLayout(search_wrap)
        sw.setContentsMargins(8, 8, 8, 4)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search…")
        self._search.setClearButtonEnabled(True)
        self._search.setStyleSheet(SEARCH_STYLE)
        self._search.textChanged.connect(self._on_search_changed)
        sw.addWidget(self._search)
        lc_layout.addWidget(search_wrap)

        self._sidebar = SidebarPanel()
        lc_layout.addWidget(self._sidebar, 1)

        splitter.addWidget(left_col)
        splitter.setCollapsible(0, False)

        # ── Right content stack ───────────────────────────────────────────
        self._stack = QStackedWidget()
        splitter.addWidget(self._stack)
        splitter.setCollapsible(1, False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([170, 900])

        self._releases_view = _ReleasesView(
            self._db,
            lambda: self._db.get_releases(search=self._search.text().strip()),
            sortable=True,
            expandable=True,
            show_bottom_bar=True,
            count_label_fn=lambda n: f"Releases: {n}",
        )
        self._releases_view.play_requested.connect(self.play_requested)
        self._releases_view.enqueue_requested.connect(self.enqueue_requested)
        self._releases_view.play_track_requested.connect(self.play_track_requested)
        self._releases_view.enqueue_track_requested.connect(self.enqueue_track_requested)
        self._releases_view.release_trashed.connect(self.release_trashed)
        self._releases_view.release_moved.connect(self.release_moved)
        self._releases_view.liked_changed.connect(self._on_liked_changed)
        self._releases_view.playlist_track_added.connect(self._on_popup_playlist_track_added)

        self._liked_view = LikedTracksView(self._db)
        self._liked_view.play_track_requested.connect(self.play_track_requested)
        self._liked_view.enqueue_track_requested.connect(self.enqueue_track_requested)
        self._liked_view.track_unliked.connect(self._on_liked_changed)
        self._liked_view.go_to_release.connect(self.navigate_to_release)
        self._liked_view.playlist_track_added.connect(self._on_popup_playlist_track_added)

        self._playlist_view = PlaylistView(self._db)
        self._playlist_view.play_track_requested.connect(self.play_track_requested)
        self._playlist_view.enqueue_track_requested.connect(self.enqueue_track_requested)
        self._playlist_view.liked_changed.connect(self._on_liked_changed)
        self._playlist_view.go_to_release.connect(self.navigate_to_release)

        self._stack.addWidget(self._releases_view)   # 0
        self._stack.addWidget(self._liked_view)      # 1
        self._stack.addWidget(self._playlist_view)   # 2

        self._sidebar.nav_changed.connect(self._on_nav)
        self._sidebar.add_playlist_requested.connect(self._on_add_playlist)
        self._sidebar.rename_playlist_requested.connect(self._on_rename_playlist)
        self._sidebar.delete_playlist_requested.connect(self._on_delete_playlist)
        self._sidebar.reorder_playlists_requested.connect(self._on_reorder_playlists)
        self._sidebar.tracks_dropped_on_playlist.connect(self._on_tracks_dropped_on_playlist)
        self._sidebar.set_current("releases")
        self._stack.setCurrentIndex(_NAV_PAGE["releases"])
        self._refresh_playlists()

    # ── Sidebar navigation ────────────────────────────────────────────────────

    def navigate_to(self, kind: str, value: str):
        if kind == "liked":
            self._on_nav("liked")
            return
        if kind == "playlist":
            self._on_nav(f"playlist:{value}")
            return
        self._sidebar.set_current("releases")
        self._stack.setCurrentIndex(_NAV_PAGE["releases"])
        self._search.setText(value)

    def _on_liked_changed(self):
        self._liked_view.refresh()
        self._playlist_view._refresh_model()
        self._releases_view.refresh_popup_likes()
        self.liked_changed.emit()

    def _on_popup_playlist_track_added(self, playlist_id: int):
        self.on_playlist_track_added(playlist_id)

    def on_playlist_track_added(self, playlist_id: int):
        if (self._stack.currentIndex() == 2 and
                self._playlist_view._playlist_id == playlist_id):
            self._playlist_view.refresh()
        self.playlist_track_added.emit(playlist_id)

    def _on_add_playlist(self):
        existing = self._db.get_playlists()
        if len(existing) >= 99:
            QMessageBox.information(self, "Playlists", "Maximum 99 playlists reached.")
            return
        name, ok = QInputDialog.getText(self, "New Playlist", "Playlist name:")
        if not ok or not name.strip():
            return
        self._db.create_playlist(name.strip())
        self._refresh_playlists()

    def _on_rename_playlist(self, playlist_id: int, current_name: str):
        name, ok = QInputDialog.getText(
            self, "Rename Playlist", "New name:", text=current_name
        )
        if not ok or not name.strip() or name.strip() == current_name:
            return
        new_name = name.strip()
        self._db.rename_playlist(playlist_id, new_name)
        self.playlist_renamed.emit(playlist_id, new_name)
        if self._playlist_view._playlist_id == playlist_id:
            self._playlist_view.rename(new_name)
        self._refresh_playlists()

    def _on_reorder_playlists(self, ordered_ids: list) -> None:
        self._db.reorder_playlists(ordered_ids)
        self._refresh_playlists()

    def _on_delete_playlist(self, playlist_id: int):
        pl = self._db.get_playlist(playlist_id)
        if not pl:
            return
        reply = QMessageBox.question(
            self, "Delete Playlist",
            f"Delete playlist \"{pl['name']}\"?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._db.delete_playlist(playlist_id)
        if self._sidebar._current == f"playlist:{playlist_id}":
            self._sidebar.set_current("releases")
            self._stack.setCurrentIndex(0)
        self._refresh_playlists()

    def _refresh_playlists(self):
        playlists = self._db.get_playlists()
        self._sidebar.refresh_playlists(playlists)
        self.playlists_changed.emit(playlists)

    def _on_tracks_dropped_on_playlist(self, playlist_id: int, urls: list, cue_meta: list = None) -> None:
        if cue_meta:
            for entry in cue_meta:
                path = entry.get("path", "")
                if not Path(path).is_file():
                    continue
                self._db.add_track_to_playlist(
                    playlist_id,
                    path,
                    entry.get("artist", ""),
                    entry.get("title", ""),
                    entry.get("album", ""),
                    entry.get("folder_path", str(Path(path).parent)),
                    entry.get("duration_ms", 0),
                    entry.get("start_ms", 0),
                    entry.get("end_ms", 0),
                )
        else:
            from src.ui.player_engine import _read_full_tags
            for url in urls:
                local = url.toLocalFile()
                if not local:
                    continue
                p = Path(local)
                if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS:
                    artist, title, album, duration_ms = _read_full_tags(str(p))
                    self._db.add_track_to_playlist(
                        playlist_id, str(p), artist, title, album, str(p.parent), duration_ms,
                    )
        self._on_popup_playlist_track_added(playlist_id)

    def _on_nav(self, key: str):
        if key != "releases":
            self._releases_view.clear_selection()
            self._releases_view.collapse_all()
        self._sidebar.set_current(key)
        if key.startswith("playlist:"):
            pid = int(key.split(":")[1])
            pl = self._db.get_playlist(pid)
            if pl:
                self._playlist_view.load(pid, pl["name"])
            self._stack.setCurrentIndex(2)
            return
        self._stack.setCurrentIndex(_NAV_PAGE.get(key, _NAV_PAGE["releases"]))
        if key == "releases":
            self._releases_view.invalidate_header_cache()
            self._releases_view.sync_header()
        elif key == "liked":
            self._liked_view.refresh()

    def _on_search_changed(self, text: str):
        if text.strip():
            self._sidebar.set_current("releases")
            self._stack.setCurrentIndex(_NAV_PAGE["releases"])
        self.refresh()

    # ── External API ──────────────────────────────────────────────────────────

    def refresh(self):
        mask = self._db.get_setting("folder_mask", DEFAULT_MASK)
        token_order  = _known_token_order(mask)
        extra_tokens = get_custom_tokens(mask)
        self._releases_view.refresh(token_order, extra_tokens)
        self._liked_view.refresh()
        self._playlist_view._refresh_model()

    def navigate_to_release(self, folder_path: str) -> None:
        self._search.clear()
        self._sidebar.set_current("releases")
        self._stack.setCurrentIndex(_NAV_PAGE["releases"])
        self._releases_view.refresh()
        self._releases_view.select_release(folder_path)
        self.go_to_release.emit(folder_path)

    def refresh_liked(self):
        self._liked_view.refresh()
        self._playlist_view._refresh_model()
        self._releases_view.refresh_popup_likes()

    def sync_popup_like(self, path: str, liked: bool) -> None:
        self._releases_view.sync_popup_like(path, liked)

    def invalidate_header_state(self):
        self._releases_view.invalidate_header_state()

    def refresh_playlists(self):
        self._refresh_playlists()

    def clear_releases_selection(self):
        self._releases_view.clear_selection()

    def collapse_releases(self):
        self._releases_view.collapse_all()
