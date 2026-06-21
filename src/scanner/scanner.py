import re
import unicodedata
from pathlib import Path

from src.database.db import Database
from src.scanner.mask import mask_to_regex, DEFAULT_MASK
from src.scanner.parser import parse_folder_name
from src.utils.logger import get_logger

log = get_logger()

_AUDIO_EXTENSIONS = {
    ".flac", ".mp3", ".wav", ".aiff", ".aif", ".m4a", ".alac",
    ".ogg", ".opus", ".ape", ".wv", ".wma", ".aac", ".dsf", ".dff",
}

_IGNORED_SUBDIR_NAMES = {"artwork", "cover", "media"}


def _norm(path: str) -> str:
    """Normalize a path to NFC Unicode form.

    On macOS HFS+/APFS the filesystem uses NFD; watchdog may deliver NFD paths
    while Python iterdir() produces NFC.  Normalising to a single form prevents
    spurious mismatches when comparing scanner results against stored paths.
    """
    return unicodedata.normalize("NFC", path)


def _load_pattern(db: Database) -> re.Pattern:
    mask = db.get_setting("folder_mask", DEFAULT_MASK)
    try:
        return mask_to_regex(mask)
    except Exception:
        log.warning("Invalid folder_mask in settings, falling back to default")
        return mask_to_regex(DEFAULT_MASK)


def _iter_release_dirs(root: Path, pattern: re.Pattern):
    """
    Yield all directories whose name matches the release pattern.

    The library may be structured as a flat list of release folders OR as
    a two-level hierarchy (artist folder → release folder).  We walk up to
    two levels deep: if a direct child matches, yield it; otherwise look
    one level deeper inside it.  This covers both layouts without
    accidentally descending into the audio/artwork contents of a release.
    """
    try:
        children = [e for e in root.iterdir() if e.is_dir()]
    except PermissionError:
        log.error("Permission denied: %s", root)
        return

    for entry in children:
        if parse_folder_name(entry.name, pattern):
            yield entry
        else:
            # Might be an artist/genre grouping folder — look one level deeper
            try:
                for sub in entry.iterdir():
                    if sub.is_dir() and parse_folder_name(sub.name, pattern):
                        yield sub
            except PermissionError:
                log.warning("Permission denied: %s", entry)


def _disc_subdirs(entry: Path) -> list[Path]:
    """Return sorted non-ignored subdirectories of a potential multi-disc container."""
    try:
        items = list(entry.iterdir())
    except PermissionError:
        return []
    has_audio = any(
        f.is_file() and f.suffix.lower() in _AUDIO_EXTENSIONS for f in items
    )
    if has_audio:
        return []
    return sorted(
        d for d in items
        if d.is_dir()
        and d.name.lower() not in _IGNORED_SUBDIR_NAMES
        and not d.name.startswith(".")
        and not d.name.startswith("_")
    )


def scan_source(db: Database, source_id: int, source_path: str) -> tuple[int, int, int]:
    """Scan one source directory. Returns (added, updated, removed) counts."""
    root = Path(source_path)
    if not root.exists():
        db.update_source_availability(source_id, False)
        if root.parent.exists():
            # Parent is still there → the source directory itself was deleted,
            # not a drive disconnection.  Remove releases whose folders are gone.
            removed = 0
            for path in db.get_release_paths_for_source(source_id):
                if not Path(_norm(path)).exists():
                    db.delete_release_by_path(path)
                    log.info("Removed release (source deleted): %s", path)
                    removed += 1
                else:
                    db.set_release_availability(path, False)
            log.warning("Source directory deleted: %s", source_path)
            return 0, 0, removed
        else:
            # Parent missing too → likely a drive/mount gone offline.
            # Keep releases as unavailable so they reappear when drive returns.
            db.set_releases_availability_by_source(source_id, False)
            log.warning("Source not available (drive offline?): %s", source_path)
            return 0, 0, 0

    db.update_source_availability(source_id, True)
    pattern = _load_pattern(db)

    # Normalise stored paths to NFC so comparisons are stable across NFD/NFC
    # variants that macOS / watchdog may produce.
    known_paths = {_norm(p) for p in db.get_release_paths_for_source(source_id)}
    found_paths: set[str] = set()
    added = updated = 0

    for entry in _iter_release_dirs(root, pattern):
        parsed = parse_folder_name(entry.name, pattern)
        if not parsed:
            continue

        path_str = _norm(str(entry))
        found_paths.add(path_str)

        disc_dirs = _disc_subdirs(entry)
        is_multi = bool(disc_dirs)
        existing = db.get_release_by_path(path_str)
        db.upsert_release(
            source_id=source_id,
            artist=parsed.artist,
            year_recorded=parsed.year_recorded,
            title=parsed.title,
            catalog_number=parsed.catalog_number,
            media=parsed.media,
            year_released=parsed.year_released,
            folder_path=path_str,
            extras=parsed.extras,
            disc_number=0 if is_multi else 1,
            is_multi_disc=is_multi,
            parent_path=None,
        )
        if is_multi:
            # Rebuild disc child entries from scratch on every scan
            db.delete_disc_entries_for_parent(path_str)
            for disc_num, disc_dir in enumerate(disc_dirs, 1):
                disc_path = _norm(str(disc_dir))
                db.upsert_release(
                    source_id=source_id,
                    artist=parsed.artist,
                    year_recorded=parsed.year_recorded,
                    title=parsed.title,
                    catalog_number=parsed.catalog_number,
                    media=parsed.media,
                    year_released=parsed.year_released,
                    folder_path=disc_path,
                    extras=parsed.extras,
                    disc_number=disc_num,
                    is_multi_disc=False,
                    parent_path=path_str,
                )
                log.debug("  disc %d: %s", disc_num, disc_dir.name)
        if existing is None:
            log.info("Added release%s: %s", " (multi-disc)" if is_multi else "", entry.name)
            added += 1
        else:
            updated += 1

    removed_paths = known_paths - found_paths
    removed = 0
    for path in removed_paths:
        truly_exists = False
        try:
            Path(path).resolve(strict=True)
            truly_exists = True
        except OSError:
            pass
        if not truly_exists:
            db.delete_release_by_path(path)
            log.info("Removed release (folder gone): %s", path)
            removed += 1
        else:
            # Folder exists but no longer matches the mask (e.g. renamed).
            db.set_release_availability(path, False)
            log.debug("Release no longer matched by scanner (folder exists): %s", path)

    db.update_source_last_scan(source_id)
    log.info(
        "Scan complete [source %d]: +%d updated=%d removed=%d",
        source_id, added, updated, removed,
    )
    return added, updated, removed


def scan_all(db: Database) -> tuple[int, int, int]:
    total_a = total_u = total_r = 0
    for source in db.get_sources():
        if not source["enabled"]:
            continue
        a, u, r = scan_source(db, source["id"], source["path"])
        total_a += a
        total_u += u
        total_r += r
    return total_a, total_u, total_r
