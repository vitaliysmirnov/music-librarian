import json
import os
import platform
import re
import subprocess
import tempfile
from pathlib import Path

from PySide6.QtCore import Qt, QAbstractTableModel, QModelIndex, QByteArray, QIdentityProxyModel, QSortFilterProxyModel, QUrl, QMimeData, QPoint, QSize, Signal
from PySide6.QtGui import QColor, QDrag, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QLabel,
    QPushButton, QTableView, QHeaderView, QAbstractItemView, QMenu,
    QApplication, QStyledItemDelegate, QStyle, QMessageBox,
    QSplitter, QStackedWidget,
)

from src.scanner.mask import DEFAULT_MASK, KNOWN_TOKENS, get_custom_tokens
from src.ui.edit_release_dialog import EditReleaseDialog
from src.ui.sidebar_panel import SidebarPanel
from src.ui.style import ROW_HEIGHT, TABLE_STYLE, SEARCH_STYLE

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

SETTINGS_KEY = "releases_header_state"

_PLAY_WIDTH          = 38
_EXTRA_DEFAULT_WIDTH = 90

_AUDIO_EXTENSIONS = {
    ".flac", ".mp3", ".wav", ".aiff", ".aif", ".m4a", ".alac",
    ".ogg", ".opus", ".ape", ".wv", ".wma", ".aac", ".dsf", ".dff",
}

_NAV_PAGE = {
    "home":      0,
    "recent":    1,
    "releases":  2,
    "liked":     3,
    "playlists": 4,
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
    return sorted(
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in _AUDIO_EXTENSIONS
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


def _play_release(folder_path: str, player_path: str):
    files = _audio_files(folder_path)
    if not files:
        return

    m3u_path = Path(tempfile.gettempdir()) / "music_librarian_play.m3u"
    m3u_path.write_text(
        "#EXTM3U\n" + "\n".join(str(f) for f in files),
        encoding="utf-8",
    )
    target = str(m3u_path)

    if player_path:
        clean = player_path.rstrip("/")
        if platform.system() == "Darwin":
            # Use `open -a <bundle.app>` regardless of whether the stored path
            # is the .app itself or a binary nested inside it — this lets macOS
            # hand the file to the already-running instance, which replaces its
            # playlist (the behaviour that originally fixed the enqueue bug).
            app_bundle = next(
                (str(p) for p in [Path(clean)] + list(Path(clean).parents)
                 if str(p).endswith(".app")),
                None,
            )
            if app_bundle:
                subprocess.Popen(["open", "-a", app_bundle, target])
            else:
                subprocess.Popen([clean, target])
        else:
            subprocess.Popen([clean, target])
    elif platform.system() == "Darwin":
        subprocess.Popen(["open", target])
    elif platform.system() == "Windows":
        os.startfile(target)
    else:
        subprocess.Popen(["xdg-open", target])


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


class _PlayButtonDelegate(QStyledItemDelegate):
    def __init__(self, db, toggle_expand_cb=None, parent=None):
        super().__init__(parent)
        self._db = db
        self._toggle_expand_cb = toggle_expand_cb

    def paint(self, painter, option, index):
        if index.column() != COL_PLAY:
            super().paint(painter, option, index)
            return

        row = index.data(Qt.UserRole)
        if not row:
            return

        if row.get("is_multi_disc"):
            icon = "▾" if row.get("_is_expanded") else "▸"
        elif row["is_available"]:
            icon = "▶"
        else:
            return

        painter.save()
        if option.state & QStyle.State_MouseOver:
            painter.fillRect(option.rect, option.palette.highlight().color().lighter(175))
        painter.setPen(option.palette.text().color())
        painter.drawText(option.rect, Qt.AlignCenter, icon)
        painter.restore()

    def sizeHint(self, option, index):
        if index.column() == COL_PLAY:
            return QSize(_PLAY_WIDTH, ROW_HEIGHT)
        return super().sizeHint(option, index)

    def editorEvent(self, event, model, option, index):
        from PySide6.QtCore import QEvent
        if index.column() == COL_PLAY and event.type() == QEvent.Type.MouseButtonRelease:
            row = index.data(Qt.UserRole)
            if row:
                if row.get("is_multi_disc"):
                    if self._toggle_expand_cb:
                        self._toggle_expand_cb(row["folder_path"])
                elif row["is_available"]:
                    player = self._db.get_setting("audio_player_path", "")
                    _play_release(row["folder_path"], player)
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
        avail_col = src._col_avail()

        left_row  = src.data(src.index(left.row(),  COL_PLAY), Qt.UserRole)
        right_row = src.data(src.index(right.row(), COL_PLAY), Qt.UserRole)

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
    def __init__(self):
        super().__init__()
        self._drag_start: QPoint | None = None

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
            if row and row["is_available"] and not row.get("is_multi_disc"):
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


class _RecentlyAddedPage(QWidget):
    def __init__(self, db, parent=None):
        super().__init__(parent)
        self._db = db
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QLabel("Recently Added")
        header.setStyleSheet(
            "font-size: 18px; font-weight: 600; padding: 10px 14px 6px 14px;"
        )
        layout.addWidget(header)

        self._model = ReleasesModel()
        self._proxy = QIdentityProxyModel()
        self._proxy.setSourceModel(self._model)

        self._table = _DragTableView()
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setStyleSheet(TABLE_STYLE)
        vhdr = self._table.verticalHeader()
        vhdr.setVisible(False)
        vhdr.setDefaultSectionSize(ROW_HEIGHT)
        vhdr.setMinimumSectionSize(ROW_HEIGHT)
        self._table.setDragEnabled(True)
        self._table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self._table.setDefaultDropAction(Qt.DropAction.CopyAction)
        self._table.setMouseTracking(True)

        self._delegate = _PlayButtonDelegate(self._db, parent=self._table)
        self._table.setItemDelegate(self._delegate)

        hdr = self._table.horizontalHeader()
        hdr.setSectionsMovable(False)
        hdr.setSectionsClickable(False)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        layout.addWidget(self._table)

        self._count_label = QLabel("")
        self._count_label.setStyleSheet(
            "font-size: 11px; color: palette(placeholderText); padding: 4px 12px;"
        )
        layout.addWidget(self._count_label)

    def refresh(self, token_order: list | None = None, extra_tokens: list | None = None):
        if token_order is None:
            mask = self._db.get_setting("folder_mask", DEFAULT_MASK)
            token_order = _known_token_order(mask)
            extra_tokens = get_custom_tokens(mask)
        extra_tokens = extra_tokens or []

        rows = self._db.get_recent_releases(50)
        flat = [dict(r) for r in rows]
        self._model.load(flat, token_order, extra_tokens)

        hdr = self._table.horizontalHeader()
        hdr.resizeSection(COL_PLAY, _PLAY_WIDTH)
        hdr.setSectionResizeMode(COL_PLAY, QHeaderView.Interactive)
        for i, tok in enumerate(token_order):
            hdr.resizeSection(1 + i, _TOKEN_WIDTH.get(tok, 100))
        n_kn = len(token_order)
        for i in range(len(extra_tokens)):
            hdr.resizeSection(1 + n_kn + i, _EXTRA_DEFAULT_WIDTH)
        for i, w in enumerate(_TAIL_WIDTHS):
            hdr.resizeSection(1 + n_kn + len(extra_tokens) + i, w)

        self._count_label.setText(
            f"Showing {len(rows)} most recently added release{'s' if len(rows) != 1 else ''}"
        )


class ReleasesTab(QWidget):
    release_trashed = Signal()

    def __init__(self, db):
        super().__init__()
        self._db = db
        self._expanded: set[str] = set()
        self._header_state: QByteArray | None = None  # in-memory cache
        self._clamping_section = False
        self._setup_ui()
        self._restore_header_state()

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

        self._stack.addWidget(_make_stub("Home"))                  # 0

        self._recent_page = _RecentlyAddedPage(self._db)
        self._stack.addWidget(self._recent_page)                   # 1

        self._stack.addWidget(self._create_albums_widget())        # 2  releases
        self._stack.addWidget(_make_stub("Liked"))                 # 3
        self._stack.addWidget(_make_stub("All Playlists"))         # 4

        self._sidebar.nav_changed.connect(self._on_nav)
        self._sidebar.set_current("releases")
        self._stack.setCurrentIndex(_NAV_PAGE["releases"])

    def _create_albums_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # ── Table ─────────────────────────────────────────────────────────
        self._model = ReleasesModel()
        self._proxy = _MultiSortProxy()
        self._proxy.setSourceModel(self._model)

        self._table = _DragTableView()
        self._table.setModel(self._proxy)
        self._table.setSortingEnabled(True)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setShowGrid(False)
        self._table.setStyleSheet(TABLE_STYLE)
        self._table.doubleClicked.connect(self._on_double_click)
        vhdr = self._table.verticalHeader()
        vhdr.setVisible(False)
        vhdr.setDefaultSectionSize(ROW_HEIGHT)
        vhdr.setMinimumSectionSize(ROW_HEIGHT)
        self._table.setDragEnabled(True)
        self._table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self._table.setDefaultDropAction(Qt.DropAction.CopyAction)
        self._table.setMouseTracking(True)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._show_context_menu)

        self._delegate = _PlayButtonDelegate(
            self._db, toggle_expand_cb=self._toggle_expand, parent=self._table
        )
        self._table.setItemDelegate(self._delegate)

        hdr = self._table.horizontalHeader()
        hdr.setSectionsMovable(True)
        hdr.setSectionsClickable(True)
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        hdr.setSectionResizeMode(COL_PLAY, QHeaderView.Interactive)
        hdr.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        hdr.setContextMenuPolicy(Qt.CustomContextMenu)
        hdr.customContextMenuRequested.connect(self._show_header_menu)
        hdr.sectionMoved.connect(self._on_section_moved)
        hdr.sectionResized.connect(self._enforce_min_section_width)
        hdr.sectionResized.connect(self._save_header_state)
        hdr.sectionClicked.connect(self._on_header_clicked)

        layout.addWidget(self._table)

        # Cmd+Backspace / Ctrl+Backspace → move to Trash
        trash_sc = QShortcut(QKeySequence("Ctrl+Backspace"), self._table)
        trash_sc.setContext(Qt.WidgetWithChildrenShortcut)
        trash_sc.activated.connect(self._trash_release)

        # ── Bottom bar ────────────────────────────────────────────────────
        bottom_bar = QWidget()
        bb = QHBoxLayout(bottom_bar)
        bb.setContentsMargins(8, 4, 8, 4)
        bb.setSpacing(4)

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

        drag_hint = QLabel("▶ to play · drag to player")
        drag_hint.setStyleSheet("color: palette(placeholderText); font-size: 11px;")
        bb.addWidget(drag_hint)

        reset_btn = QPushButton("Reset View")
        reset_btn.setToolTip("Restore default column order and widths")
        reset_btn.clicked.connect(self._reset_header)
        bb.addWidget(reset_btn)

        layout.addWidget(bottom_bar)

        return widget

    # ── Sidebar navigation ────────────────────────────────────────────────

    def _on_nav(self, key: str):
        self._stack.setCurrentIndex(_NAV_PAGE.get(key, _NAV_PAGE["releases"]))

    def _on_search_changed(self, text: str):
        if text.strip():
            self._sidebar.set_current("releases")
            self._stack.setCurrentIndex(_NAV_PAGE["releases"])
        self.refresh()

    # ── Header click ──────────────────────────────────────────────────────

    def _on_header_clicked(self, logical: int):
        if logical == COL_PLAY:
            # Qt already moved the indicator to COL_PLAY — put it back.
            col = self._proxy._primary_col
            if col is None or col == COL_PLAY:
                col = COL_PLAY + 1
            self._table.horizontalHeader().setSortIndicator(col, self._proxy._primary_order)

    # ── Double-click ───────────────────────────────────────────────────────

    def _on_double_click(self, proxy_index):
        if proxy_index.column() == COL_PLAY:
            return
        self._edit_release()

    # ── Row context menu ───────────────────────────────────────────────────

    def _show_context_menu(self, pos):
        proxy_index = self._table.indexAt(pos)
        if not proxy_index.isValid():
            return
        # Ensure the clicked row is selected
        self._table.selectionModel().setCurrentIndex(
            proxy_index, self._table.selectionModel().SelectionFlag.ClearAndSelect |
            self._table.selectionModel().SelectionFlag.Rows,
        )
        row = self._selected_row()
        if not row:
            return

        available = bool(row["is_available"])
        is_container = bool(row.get("is_multi_disc"))
        player_path = self._db.get_setting("audio_player_path", "").strip()

        menu = QMenu(self)

        if player_path:
            player_name = Path(player_path.rstrip("/")).stem or player_path
            act_play = menu.addAction(f"Play with {player_name}")
            act_play.setEnabled(available and not is_container)
        else:
            act_play = None

        act_open = menu.addAction("Open Folder")
        act_open.setEnabled(available)

        menu.addSeparator()

        act_edit = menu.addAction("Release Info")
        act_edit.setEnabled(available)

        menu.addSeparator()

        act_delete = menu.addAction("Move to Trash")

        chosen = menu.exec(self._table.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen == act_play:
            _play_release(row["folder_path"], player_path)
        elif chosen == act_open:
            self._open_release()
        elif chosen == act_edit:
            self._edit_release()
        elif chosen == act_delete:
            self._trash_release()

    # ── Header context menu ────────────────────────────────────────────────

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
            hdr.setSectionHidden(chosen.data(), not chosen.isChecked())
            self._save_header_state()

    # ── Header state ───────────────────────────────────────────────────────

    def _header_min_width(self, logical: int) -> int:
        if logical == COL_PLAY:
            return _PLAY_WIDTH
        text = str(self._model.headerData(logical, Qt.Horizontal, Qt.DisplayRole) or "")
        if not text:
            return 20
        fm = self._table.horizontalHeader().fontMetrics()
        # text width + padding (6px each side) + sort-indicator allowance (14px)
        return fm.horizontalAdvance(text) + 26

    def _enforce_min_section_width(self, logical: int, _old: int, new_size: int):
        if self._clamping_section:
            return
        min_w = self._header_min_width(logical)
        if new_size < min_w:
            self._clamping_section = True
            self._table.horizontalHeader().resizeSection(logical, min_w)
            self._clamping_section = False

    def _on_section_moved(self, logical, old_visual, new_visual):
        if logical == COL_PLAY and new_visual != 0:
            self._table.horizontalHeader().moveSection(new_visual, 0)
            return
        if new_visual == 0 and logical != COL_PLAY:
            self._table.horizontalHeader().moveSection(0, old_visual)
            return
        self._save_header_state()

    def _save_header_state(self, *_):
        state: QByteArray = self._table.horizontalHeader().saveState()
        if self._header_state is not None and state == self._header_state:
            return  # nothing changed — skip the DB write
        self._header_state = state
        self._db.set_setting(SETTINGS_KEY, state.toBase64().data().decode())

    def _restore_header_state(self):
        if self._header_state is None:
            raw = self._db.get_setting(SETTINGS_KEY, "")
            if not raw:
                return
            try:
                self._header_state = QByteArray.fromBase64(raw.encode())
            except Exception:
                return
        try:
            self._table.horizontalHeader().restoreState(self._header_state)
        except Exception:
            pass
        hdr = self._table.horizontalHeader()
        # restoreState re-applies saved resize mode and width; always override.
        hdr.setSectionResizeMode(COL_PLAY, QHeaderView.Interactive)
        hdr.resizeSection(COL_PLAY, _PLAY_WIDTH)
        # If saved state had sort indicator on COL_PLAY, move it to Artist.
        if hdr.sortIndicatorSection() == COL_PLAY:
            hdr.setSortIndicator(COL_PLAY + 1, Qt.AscendingOrder)
            self._save_header_state()

    def invalidate_header_state(self):
        self._header_state = None
        self._db.set_setting(SETTINGS_KEY, "")

    def _reset_header(self):
        hdr = self._table.horizontalHeader()
        n = self._model.columnCount()
        for logical in range(n):
            visual = hdr.visualIndex(logical)
            if visual != logical:
                hdr.moveSection(visual, logical)
        self._apply_default_widths()
        for i in range(n):
            hdr.setSectionHidden(i, False)
        self._save_header_state()

    # ── Data ───────────────────────────────────────────────────────────────

    def _toggle_expand(self, folder_path: str):
        if folder_path in self._expanded:
            self._expanded.discard(folder_path)
        else:
            self._expanded.add(folder_path)
        self.refresh()

    def refresh(self):
        mask = self._db.get_setting("folder_mask", DEFAULT_MASK)
        token_order  = _known_token_order(mask)
        extra_tokens = get_custom_tokens(mask)
        self._recent_page.refresh(token_order, extra_tokens)
        top_rows = self._db.get_releases(search=self._search.text().strip())

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

        prev_n = self._model.columnCount()
        self._model.load(flat, token_order, extra_tokens)
        if self._model.columnCount() != prev_n:
            self._apply_default_widths()
        else:
            self._restore_header_state()
        hdr = self._table.horizontalHeader()
        hdr.resizeSection(COL_PLAY, _PLAY_WIDTH)
        hdr.setSectionResizeMode(COL_PLAY, QHeaderView.Interactive)
        self._count_label.setText(f"Releases: {len(top_rows)}")

    def _apply_default_widths(self):
        hdr = self._table.horizontalHeader()
        hdr.resizeSection(COL_PLAY, _PLAY_WIDTH)
        hdr.setSectionResizeMode(COL_PLAY, QHeaderView.Interactive)
        for i, tok in enumerate(self._model._token_order):
            hdr.resizeSection(1 + i, _TOKEN_WIDTH.get(tok, 100))
        n_kn = self._model._n_known()
        for i in range(len(self._model._extra_tokens)):
            hdr.resizeSection(1 + n_kn + i, _EXTRA_DEFAULT_WIDTH)
        for i, w in enumerate(_TAIL_WIDTHS):
            hdr.resizeSection(1 + n_kn + len(self._model._extra_tokens) + i, w)

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
        if not row["is_available"]:
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

        folder_path = row["folder_path"]
        folder_exists = Path(folder_path).exists()
        artist = row.get("artist", "")
        title = row.get("title", "")
        label = f"{artist} — {title}" if artist and title else folder_path

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
