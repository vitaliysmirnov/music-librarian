import json
import unicodedata
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QRect
from PySide6.QtGui import QPainter, QPen, QColor, QPixmap
from PySide6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QDialogButtonBox,
    QLabel, QMessageBox, QVBoxLayout, QHBoxLayout,
    QSizePolicy, QFileDialog, QWidget,
)

from src.database.db import Database
from src.scanner.mask import DEFAULT_MASK, get_custom_tokens
from src.utils import covers as _covers
from src.utils.logger import get_logger

log = get_logger()


def _build_folder_name(fields: dict, mask: str) -> str:
    """Reconstruct folder name from fields using the current mask."""
    result = mask
    for token, value in fields.items():
        if not value:
            result = result.replace(f"[{{{token}}}]", "")
            result = result.replace(f"({{{token}}})", "")
            result = result.replace(f"{{{token}}}", "")
        else:
            result = result.replace(f"{{{token}}}", value)
    while "  " in result:
        result = result.replace("  ", " ")
    return result.strip()


def _same_inode(a: Path, b: Path) -> bool:
    """True if both paths refer to the same filesystem object (handles NFC/NFD aliases on macOS)."""
    try:
        return a.stat().st_ino == b.stat().st_ino
    except OSError:
        return False


def _load_extras(release: dict) -> dict:
    try:
        return json.loads(release.get("extras") or "{}")
    except Exception:
        return {}


class _CoverWidget(QWidget):
    """Square cover art widget. Supports drag-and-drop and click-to-browse."""

    cover_changed = Signal(str)   # emits path to newly selected image file

    _HINT_TEXT = "Drop image here\nor click to browse"
    _BORDER_COLOR = QColor(120, 120, 120)
    _TEXT_COLOR = QColor(140, 140, 140)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self.setAcceptDrops(True)
        self.setMinimumSize(120, 120)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    # ── Geometry ──────────────────────────────────────────────────────────

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return width

    def sizeHint(self):
        from PySide6.QtCore import QSize
        w = self.width() if self.width() > 0 else 160
        return QSize(w, w)

    # ── Public API ────────────────────────────────────────────────────────

    def set_pixmap(self, pixmap: QPixmap | None):
        self._pixmap = pixmap
        self.update()

    def pixmap_loaded(self) -> bool:
        return self._pixmap is not None

    # ── Paint ─────────────────────────────────────────────────────────────

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        side = min(self.width(), self.height())
        x = (self.width() - side) // 2
        y = (self.height() - side) // 2
        square = QRect(x, y, side, side)

        if self._pixmap:
            scaled = self._pixmap.scaled(
                side, side,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            px = x + (side - scaled.width()) // 2
            py = y + (side - scaled.height()) // 2
            painter.drawPixmap(px, py, scaled)
        else:
            pen = QPen(self._BORDER_COLOR, 1, Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(square.adjusted(1, 1, -2, -2))
            painter.setPen(self._TEXT_COLOR)
            painter.drawText(square, Qt.AlignmentFlag.AlignCenter, self._HINT_TEXT)

    # ── Interaction ───────────────────────────────────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._browse()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            url = event.mimeData().urls()[0]
            if url.isLocalFile():
                event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if path:
                self._load_from_path(path)

    def set_browse_root(self, directory: str):
        self._browse_root = directory

    def _browse(self):
        root = getattr(self, "_browse_root", "")
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Cover Image", root,
            "Images (*.jpg *.jpeg *.png *.bmp *.tiff *.tif *.webp)",
        )
        if path:
            self._load_from_path(path)

    def _load_from_path(self, path: str):
        from src.utils.covers import preview_from_file
        pix = preview_from_file(path, 600)
        if pix is None:
            QMessageBox.warning(self, "Error", f"Cannot load image:\n{path}")
            return
        self.set_pixmap(pix)
        self.cover_changed.emit(path)


class EditReleaseDialog(QDialog):
    def __init__(self, db: Database, release: dict, parent=None):
        super().__init__(parent)
        self._db = db
        self._release = release
        self._mask = db.get_setting("folder_mask", DEFAULT_MASK)
        self._extra_tokens = get_custom_tokens(self._mask)
        self._extras_current = _load_extras(release)
        self._is_disc_child = bool(release.get("parent_path"))
        self._cover_source_path: str | None = None  # set when user picks a new cover

        # Each release (including disc children) stores its cover by its own folder_path.
        # Disc children fall back to the parent's cover when their own is absent.
        self._cover_key = release["folder_path"]

        self.setWindowTitle("Release Info")
        self.setMinimumWidth(560)
        self._setup_ui()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # ── Main row: cover | form ─────────────────────────────────────────
        row = QHBoxLayout()
        row.setSpacing(16)

        self._cover = _CoverWidget()
        self._cover.cover_changed.connect(self._on_cover_changed)
        existing = _covers.load_cover_for_widget(self._db.covers_dir, self._cover_key, 600)
        if existing:
            self._cover.set_pixmap(existing)
        self._cover.set_browse_root(self._release.get("source_path") or "")
        row.addWidget(self._cover, stretch=2)

        # Form column
        form_col = QVBoxLayout()
        form_col.setSpacing(4)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._artist = QLineEdit(self._release["artist"])
        self._year_recorded = QLineEdit(self._release["year_recorded"])
        self._year_recorded.setMaxLength(4)
        self._year_recorded.setFixedWidth(70)
        self._title = QLineEdit(self._release["title"])
        self._catalog = QLineEdit(self._release["catalog_number"] or "")
        self._media = QLineEdit(self._release["media"] or "")
        self._media.setFixedWidth(100)
        self._year_released = QLineEdit(self._release["year_released"] or "")
        self._year_released.setMaxLength(4)
        self._year_released.setFixedWidth(70)

        form.addRow("Artist:", self._artist)
        form.addRow("Rec. Year:", self._year_recorded)
        form.addRow("Title:", self._title)
        form.addRow("Cat. No.:", self._catalog)
        form.addRow("Media:", self._media)
        form.addRow("Rel. Year:", self._year_released)

        if not self._release.get("is_multi_disc"):
            disc_num = self._release.get("disc_number") or 1
            self._disc_number: QLineEdit | None = QLineEdit(str(disc_num))
            self._disc_number.setMaxLength(2)
            self._disc_number.setFixedWidth(50)
            form.addRow("Disc #:", self._disc_number)
        else:
            self._disc_number = None

        for token in self._extra_tokens:
            edit = QLineEdit(self._extras_current.get(token, ""))
            label = token.replace("_", " ").title() + ":"
            form.addRow(label, edit)
            if not hasattr(self, "_extra_edits"):
                self._extra_edits: dict[str, QLineEdit] = {}
            self._extra_edits[token] = edit

        if not hasattr(self, "_extra_edits"):
            self._extra_edits = {}

        form_col.addLayout(form)
        form_col.addStretch()

        self._preview = QLabel()
        self._preview.setWordWrap(True)
        self._preview.setStyleSheet("color: palette(placeholderText); margin-top: 4px;")
        form_col.addWidget(self._preview)

        row.addLayout(form_col, stretch=3)
        root.addLayout(row)

        # ── Buttons ───────────────────────────────────────────────────────
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        for w in (self._artist, self._year_recorded, self._title,
                  self._catalog, self._media, self._year_released):
            w.textChanged.connect(self._update_preview)
        for edit in self._extra_edits.values():
            edit.textChanged.connect(self._update_preview)

        self._update_preview()

    def _on_cover_changed(self, path: str):
        self._cover_source_path = path

    def _all_fields(self) -> dict:
        fields = {
            "artist": self._artist.text().strip(),
            "year_recorded": self._year_recorded.text().strip(),
            "title": self._title.text().strip(),
            "catalog_number": self._catalog.text().strip(),
            "media": self._media.text().strip(),
            "year_released": self._year_released.text().strip(),
        }
        for token, edit in self._extra_edits.items():
            fields[token] = edit.text().strip()
        return fields

    def _update_preview(self):
        fields = self._all_fields()
        parent_name = _build_folder_name(fields, self._mask)
        if self._is_disc_child:
            child_name = Path(self._release["folder_path"]).name
            self._preview.setText(f"Folder: {parent_name}/{child_name}")
        else:
            self._preview.setText(f"Folder: {parent_name}")

    def _save_cover(self, new_cover_key: str):
        """Persist cover after folder rename (key may have changed)."""
        if self._cover_source_path:
            _covers.save_cover(self._db.covers_dir, new_cover_key, self._cover_source_path)
        elif new_cover_key != self._cover_key:
            # Folder renamed — rename the stored cover to match new key
            _covers.rename_cover(self._db.covers_dir, self._cover_key, new_cover_key)

    def _maybe_apply_cover_to_discs(self, parent_path: str):
        """For multi-disc containers: ask whether to propagate the cover to disc children."""
        if not self._release.get("is_multi_disc") or not self._cover_source_path:
            return
        children = self._db.get_disc_entries(parent_path)
        if not children:
            return

        from PySide6.QtWidgets import QPushButton
        box = QMessageBox(self)
        box.setWindowTitle("Cover Art")
        box.setText("Apply this cover to disc children as well?")
        box.setInformativeText(
            'Choose "All discs" to use the same cover for every disc.\n'
            'Choose "This release only" to keep disc covers independent.'
        )
        btn_all = box.addButton("All discs", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("This release only", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is btn_all:
            for child in children:
                _covers.save_cover(
                    self._db.covers_dir, child["folder_path"], self._cover_source_path
                )

    def _on_save(self):
        fields = self._all_fields()
        artist = fields["artist"]
        year_recorded = fields["year_recorded"]
        title = fields["title"]
        catalog = fields["catalog_number"] or None
        media = fields["media"] or None
        year_released = fields["year_released"] or None

        if not artist or not year_recorded or not title:
            QMessageBox.warning(self, "Error", "Artist, recording year, and title are required.")
            return

        if len(year_recorded) != 4 or not year_recorded.isdigit():
            QMessageBox.warning(self, "Error", "Recording year must be a 4-digit number.")
            return

        if year_released and (len(year_released) != 4 or not year_released.isdigit()):
            QMessageBox.warning(self, "Error", "Release year must be a 4-digit number.")
            return

        extras = {token: fields[token] for token in self._extra_tokens if fields.get(token)}
        extras_json = json.dumps(extras)

        disc_number = 1
        if self._disc_number is not None:
            try:
                disc_number = max(1, int(self._disc_number.text().strip() or "1"))
            except ValueError:
                pass

        if self._is_disc_child:
            self._save_disc_child(artist, year_recorded, title, catalog, media,
                                  year_released, extras_json, disc_number)
        else:
            self._save_regular(artist, year_recorded, title, catalog, media,
                               year_released, extras_json, disc_number)

    def _save_regular(self, artist, year_recorded, title, catalog, media,
                      year_released, extras_json, disc_number):
        old_path = Path(self._release["folder_path"])
        new_name = _build_folder_name(
            {"artist": artist, "year_recorded": year_recorded, "title": title,
             "catalog_number": catalog or "", "media": media or "",
             "year_released": year_released or "",
             **{t: self._extra_edits[t].text().strip() for t in self._extra_edits}},
            self._mask,
        )
        new_path = old_path.parent / new_name

        if self._release["is_available"] and unicodedata.normalize("NFC", old_path.name) != new_name:
            if new_path.exists() and not _same_inode(old_path, new_path):
                QMessageBox.warning(self, "Error",
                                    f"A folder with that name already exists:\n{new_name}")
                return
            try:
                old_path.rename(new_path)
                log.info("Folder renamed: %s → %s", old_path, new_path)
            except OSError as e:
                QMessageBox.warning(self, "Rename Error", str(e))
                return
        elif not self._release["is_available"]:
            new_path = old_path

        self._db.rename_release(
            str(old_path), str(new_path),
            artist=artist, year_recorded=year_recorded, title=title,
            catalog_number=catalog, media=media, year_released=year_released,
            extras=extras_json, disc_number=disc_number,
        )
        self._save_cover(str(new_path))
        self._maybe_apply_cover_to_discs(str(new_path))
        self.accept()

    def _save_disc_child(self, artist, year_recorded, title, catalog, media,
                         year_released, extras_json, disc_number):
        parent_path_str = self._release["parent_path"]
        parent_row = self._db.get_release_by_path(parent_path_str)
        if not parent_row:
            self.accept()
            return

        old_parent = Path(parent_path_str)
        new_parent_name = _build_folder_name(
            {"artist": artist, "year_recorded": year_recorded, "title": title,
             "catalog_number": catalog or "", "media": media or "",
             "year_released": year_released or "",
             **{t: self._extra_edits[t].text().strip() for t in self._extra_edits}},
            self._mask,
        )
        new_parent = old_parent.parent / new_parent_name

        if parent_row["is_available"] and unicodedata.normalize("NFC", old_parent.name) != new_parent_name:
            if new_parent.exists() and not _same_inode(old_parent, new_parent):
                QMessageBox.warning(self, "Error",
                                    f"A folder with that name already exists:\n{new_parent_name}")
                return
            try:
                old_parent.rename(new_parent)
                log.info("Folder renamed: %s → %s", old_parent, new_parent)
            except OSError as e:
                QMessageBox.warning(self, "Rename Error", str(e))
                return
        else:
            new_parent = old_parent

        self._db.rename_release(
            str(old_parent), str(new_parent),
            artist=artist, year_recorded=year_recorded, title=title,
            catalog_number=catalog, media=media, year_released=year_released,
            extras=extras_json, disc_number=0,
        )
        self._db.update_disc_children_metadata(
            str(new_parent),
            artist=artist, year_recorded=year_recorded, title=title,
            catalog_number=catalog, media=media, year_released=year_released,
            extras=extras_json,
        )
        child_name = Path(self._release["folder_path"]).name
        new_child_path = str(new_parent / child_name)
        self._db.rename_release(new_child_path, new_child_path, disc_number=disc_number)

        self._save_cover(str(new_parent))
        self.accept()
