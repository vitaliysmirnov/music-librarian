import hashlib
import unicodedata
from pathlib import Path

from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QImageReader, QPixmap

_MAX_PX = 600  # max stored dimension


def _key(folder_path: str) -> str:
    # Always hash the NFC form so the key is stable across NFD/NFC path variants.
    return hashlib.sha256(unicodedata.normalize("NFC", folder_path).encode()).hexdigest()[:16]


def migrate_nfd_covers(covers_dir: Path, folder_paths: list) -> None:
    """Rename cover files that were keyed by NFD path hash to the NFC path hash.

    Needed once after the DB path-normalisation migration: old covers were saved
    before paths were NFC-normalised, so their on-disk filenames use NFD hashes.
    """
    for fp in folder_paths:
        nfc = unicodedata.normalize("NFC", fp)
        nfd = unicodedata.normalize("NFD", fp)
        if nfc == nfd:
            continue
        old = covers_dir / f"{hashlib.sha256(nfd.encode()).hexdigest()[:16]}.jpg"
        new = covers_dir / f"{hashlib.sha256(nfc.encode()).hexdigest()[:16]}.jpg"
        if old.exists() and not new.exists():
            old.rename(new)


def cover_path(covers_dir: Path, folder_path: str) -> Path:
    return covers_dir / f"{_key(folder_path)}.jpg"


def _read_scaled(source_path: str, max_px: int) -> "QPixmap | None":
    """Read an image scaled to ≤max_px on the longest side without decoding full resolution.

    For JPEG, QImageReader uses libjpeg's DCT-domain downscaling (1/2, 1/4, 1/8),
    keeping peak memory proportional to the *output* size, not the input file size.
    """
    reader = QImageReader(source_path)
    reader.setAutoTransform(True)  # respect EXIF orientation
    if not reader.canRead():
        return None

    orig = reader.size()  # only reads header — no pixel data yet
    if not orig.isValid():
        return None

    if orig.width() > max_px or orig.height() > max_px:
        scaled_size = orig.scaled(max_px, max_px, Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(scaled_size)

    img = reader.read()
    if img.isNull():
        return None
    return QPixmap.fromImage(img)


def save_cover(covers_dir: Path, folder_path: str, source_path: str) -> bool:
    """Read image scaled to ≤600 px and save as JPEG. Peak RAM is proportional to output, not input."""
    pix = _read_scaled(source_path, _MAX_PX)
    if pix is None:
        return False
    covers_dir.mkdir(parents=True, exist_ok=True)
    return pix.toImage().save(str(cover_path(covers_dir, folder_path)), "JPEG", 85)


def load_cover(covers_dir: Path, folder_path: str) -> "QPixmap | None":
    path = cover_path(covers_dir, folder_path)
    if not path.exists():
        return None
    pix = QPixmap(str(path))
    return None if pix.isNull() else pix


def load_cover_for_widget(covers_dir: Path, folder_path: str,
                          display_size: int) -> "QPixmap | None":
    """Load stored cover scaled to *display_size* px."""
    p = cover_path(covers_dir, folder_path)
    if not p.exists():
        return None
    return _read_scaled(str(p), display_size)


def preview_from_file(source_path: str, display_size: int) -> "QPixmap | None":
    """Load a user-supplied file scaled to *display_size* for widget preview."""
    return _read_scaled(source_path, display_size)


def delete_cover(covers_dir: Path, folder_path: str) -> None:
    p = cover_path(covers_dir, folder_path)
    if p.exists():
        p.unlink()


def rename_cover(covers_dir: Path, old_path: str, new_path: str) -> None:
    src = cover_path(covers_dir, old_path)
    if src.exists():
        src.rename(cover_path(covers_dir, new_path))
