import json
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QFormLayout, QLineEdit, QDialogButtonBox,
    QLabel, QMessageBox, QVBoxLayout,
)

from src.database.db import Database
from src.scanner.mask import DEFAULT_MASK, get_custom_tokens
from src.utils.logger import get_logger

log = get_logger()


def _build_folder_name(fields: dict, mask: str) -> str:
    """Reconstruct folder name from fields using the current mask."""
    result = mask
    for token, value in fields.items():
        if not value:
            # Remove optional bracketed tokens when empty
            result = result.replace(f"[{{{token}}}]", "")
            result = result.replace(f"({{{token}}})", "")
            result = result.replace(f"{{{token}}}", "")
        else:
            result = result.replace(f"{{{token}}}", value)
    # Collapse multiple spaces
    while "  " in result:
        result = result.replace("  ", " ")
    return result.strip()


def _load_extras(release: dict) -> dict:
    try:
        return json.loads(release.get("extras") or "{}")
    except Exception:
        return {}


class EditReleaseDialog(QDialog):
    def __init__(self, db: Database, release: dict, parent=None):
        super().__init__(parent)
        self._db = db
        self._release = release
        self._mask = db.get_setting("folder_mask", DEFAULT_MASK)
        self._extra_tokens = get_custom_tokens(self._mask)
        self._extras_current = _load_extras(release)
        self._is_disc_child = bool(release.get("parent_path"))
        self.setWindowTitle("Edit Release")
        self.setMinimumWidth(480)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)

        # Fixed known fields
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

        # Dynamic extra fields from current mask
        self._extra_edits: dict[str, QLineEdit] = {}
        for token in self._extra_tokens:
            edit = QLineEdit(self._extras_current.get(token, ""))
            label = token.replace("_", " ").title() + ":"
            form.addRow(label, edit)
            self._extra_edits[token] = edit

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
        for edit in self._extra_edits.values():
            edit.textChanged.connect(self._update_preview)

        self._update_preview()

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
             "year_released": year_released or "", **{t: self._extra_edits[t].text().strip()
                                                       for t in self._extra_edits}},
            self._mask,
        )
        new_path = old_path.parent / new_name

        if self._release["is_available"] and old_path.name != new_name:
            if new_path.exists():
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
             "year_released": year_released or "", **{t: self._extra_edits[t].text().strip()
                                                       for t in self._extra_edits}},
            self._mask,
        )
        new_parent = old_parent.parent / new_parent_name

        if parent_row["is_available"] and old_parent.name != new_parent_name:
            if new_parent.exists():
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

        # Update parent metadata + disc children paths in DB
        self._db.rename_release(
            str(old_parent), str(new_parent),
            artist=artist, year_recorded=year_recorded, title=title,
            catalog_number=catalog, media=media, year_released=year_released,
            extras=extras_json, disc_number=0,
        )
        # Propagate metadata to all disc children (artist, title, etc.)
        self._db.update_disc_children_metadata(
            str(new_parent),
            artist=artist, year_recorded=year_recorded, title=title,
            catalog_number=catalog, media=media, year_released=year_released,
            extras=extras_json,
        )
        # Update disc_number for this specific disc child (path may have changed)
        child_name = Path(self._release["folder_path"]).name
        new_child_path = str(new_parent / child_name)
        self._db.rename_release(new_child_path, new_child_path, disc_number=disc_number)

        self.accept()
