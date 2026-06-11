from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QDialogButtonBox,
    QLabel, QMessageBox, QVBoxLayout,
)

from src.database.db import Database
from src.utils.logger import get_logger

log = get_logger()


def _build_folder_name(artist, year_recorded, title, catalog, media, year_released) -> str:
    name = f"{artist} - {year_recorded} - {title}"
    if catalog:
        name += f" [{catalog}]"
    if media:
        name += f" [{media}]"
    if year_released:
        name += f" ({year_released})"
    return name


class EditReleaseDialog(QDialog):
    def __init__(self, db: Database, release: dict, parent=None):
        super().__init__(parent)
        self._db = db
        self._release = release
        self.setWindowTitle("Edit Release")
        self.setMinimumWidth(480)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

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
        layout.addLayout(form)

        self._preview = QLabel()
        self._preview.setWordWrap(True)
        self._preview.setStyleSheet("color: palette(placeholderText); margin-top: 8px;")
        layout.addWidget(self._preview)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for w in (self._artist, self._year_recorded, self._title,
                  self._catalog, self._media, self._year_released):
            w.textChanged.connect(self._update_preview)
        self._update_preview()

    def _update_preview(self):
        name = _build_folder_name(
            self._artist.text().strip(),
            self._year_recorded.text().strip(),
            self._title.text().strip(),
            self._catalog.text().strip(),
            self._media.text().strip(),
            self._year_released.text().strip(),
        )
        self._preview.setText(f"Folder: {name}")

    def _on_save(self):
        artist = self._artist.text().strip()
        year_recorded = self._year_recorded.text().strip()
        title = self._title.text().strip()
        catalog = self._catalog.text().strip() or None
        media = self._media.text().strip() or None
        year_released = self._year_released.text().strip() or None

        if not artist or not year_recorded or not title:
            QMessageBox.warning(self, "Error", "Artist, recording year, and title are required.")
            return

        if len(year_recorded) != 4 or not year_recorded.isdigit():
            QMessageBox.warning(self, "Error", "Recording year must be a 4-digit number.")
            return

        if year_released and (len(year_released) != 4 or not year_released.isdigit()):
            QMessageBox.warning(self, "Error", "Release year must be a 4-digit number.")
            return

        old_path = Path(self._release["folder_path"])
        new_name = _build_folder_name(artist, year_recorded, title, catalog, media, year_released)
        new_path = old_path.parent / new_name

        # Rename on disk if available and name changed
        if self._release["is_available"] and old_path.name != new_name:
            if new_path.exists():
                QMessageBox.warning(
                    self, "Error",
                    f"A folder with that name already exists:\n{new_name}"
                )
                return
            try:
                old_path.rename(new_path)
                log.info("Folder renamed: %s → %s", old_path, new_path)
            except OSError as e:
                QMessageBox.warning(self, "Rename Error", str(e))
                return
        elif not self._release["is_available"]:
            # Disk offline — update only DB, path stays as last_seen
            new_path = old_path

        self._db.rename_release(
            str(old_path),
            str(new_path),
            artist=artist,
            year_recorded=year_recorded,
            title=title,
            catalog_number=catalog,
            media=media,
            year_released=year_released,
        )
        self.accept()
