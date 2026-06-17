import json
import re
import unicodedata
from pathlib import Path

from watchdog.events import (
    DirCreatedEvent,
    DirDeletedEvent,
    DirMovedEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from src.database.db import Database
from src.scanner.mask import mask_to_regex, DEFAULT_MASK
from src.scanner.parser import parse_folder_name
from src.utils.logger import get_logger

log = get_logger()


def _norm(path: str) -> str:
    """Normalise path to NFC Unicode form (watchdog on macOS may deliver NFD)."""
    return unicodedata.normalize("NFC", path)


def _load_pattern(db: Database) -> re.Pattern:
    mask = db.get_setting("folder_mask", DEFAULT_MASK)
    try:
        return mask_to_regex(mask)
    except Exception:
        return mask_to_regex(DEFAULT_MASK)


def _is_release_path(path: str, source_path: str, pattern: re.Pattern) -> bool:
    """
    True if `path` is a release folder at depth 1 or 2 under source_path.
    Depth 1: source/AlbumFolder
    Depth 2: source/ArtistFolder/AlbumFolder
    """
    p = Path(path)
    src = Path(source_path)
    try:
        rel = p.relative_to(src)
    except ValueError:
        return False
    return len(rel.parts) in (1, 2) and parse_folder_name(p.name, pattern) is not None


def _parent_is_source_or_artist(path: str, source_path: str) -> bool:
    """True if the path is at depth 1 or 2 — i.e. could be a release folder."""
    p = Path(path)
    src = Path(source_path)
    try:
        rel = p.relative_to(src)
    except ValueError:
        return False
    return len(rel.parts) in (1, 2)


class _ReleaseEventHandler(FileSystemEventHandler):
    def __init__(self, db: Database, source_id: int, source_path: str, on_change,
                 pattern: re.Pattern):
        super().__init__()
        self._db = db
        self._source_id = source_id
        self._source_path = source_path
        self._on_change = on_change
        self._pattern = pattern

    def on_created(self, event):
        if not isinstance(event, DirCreatedEvent):
            return
        src = _norm(event.src_path)
        if not _is_release_path(src, self._source_path, self._pattern):
            return
        parsed = parse_folder_name(Path(src).name, self._pattern)
        if not parsed:
            return
        self._db.upsert_release(
            source_id=self._source_id,
            artist=parsed.artist,
            year_recorded=parsed.year_recorded,
            title=parsed.title,
            catalog_number=parsed.catalog_number,
            media=parsed.media,
            year_released=parsed.year_released,
            folder_path=src,
            extras=parsed.extras,
        )
        log.info("Watcher: added release: %s", src)
        self._on_change()

    def on_deleted(self, event):
        if not isinstance(event, DirDeletedEvent):
            return
        src = _norm(event.src_path)
        if not _parent_is_source_or_artist(src, self._source_path):
            return
        self._db.delete_release_by_path(src)
        log.info("Watcher: deleted release: %s", src)
        self._on_change()

    def on_moved(self, event):
        if not isinstance(event, DirMovedEvent):
            return
        src = _norm(event.src_path)
        dst = _norm(event.dest_path)
        src_is_release = _is_release_path(src, self._source_path, self._pattern)
        dst_is_release = _is_release_path(dst, self._source_path, self._pattern)

        if src_is_release and dst_is_release:
            parsed = parse_folder_name(Path(dst).name, self._pattern)
            if parsed:
                self._db.rename_release(
                    src,
                    dst,
                    artist=parsed.artist,
                    year_recorded=parsed.year_recorded,
                    title=parsed.title,
                    catalog_number=parsed.catalog_number,
                    media=parsed.media,
                    year_released=parsed.year_released,
                    extras=json.dumps(parsed.extras, ensure_ascii=False),
                )
                log.info("Watcher: renamed: %s → %s", src, dst)
            else:
                self._db.delete_release_by_path(src)
                log.info("Watcher: renamed to unparseable, removed: %s", src)
            self._on_change()

        elif src_is_release:
            self._db.delete_release_by_path(src)
            log.info("Watcher: release moved out: %s", src)
            self._on_change()

        elif dst_is_release:
            parsed = parse_folder_name(Path(dst).name, self._pattern)
            if parsed:
                self._db.upsert_release(
                    source_id=self._source_id,
                    artist=parsed.artist,
                    year_recorded=parsed.year_recorded,
                    title=parsed.title,
                    catalog_number=parsed.catalog_number,
                    media=parsed.media,
                    year_released=parsed.year_released,
                    folder_path=dst,
                    extras=parsed.extras,
                )
                log.info("Watcher: release moved in: %s", dst)
                self._on_change()


class LibraryWatcher:
    def __init__(self, db: Database, on_change):
        self._db = db
        self._on_change = on_change
        self._observer = Observer()
        self._watches: dict[int, object] = {}

    def start(self):
        self._schedule_all()
        self._observer.start()
        log.info("FS watcher started")

    def stop(self):
        self._observer.stop()
        self._observer.join()
        log.info("FS watcher stopped")

    def _schedule_all(self):
        for source in self._db.get_sources():
            if source["enabled"] and source["is_available"]:
                self._add_watch(source["id"], source["path"])

    def _add_watch(self, source_id: int, path: str):
        if source_id in self._watches:
            return
        if not Path(path).exists():
            return
        pattern = _load_pattern(self._db)
        handler = _ReleaseEventHandler(self._db, source_id, path, self._on_change, pattern)
        # recursive=True so we catch renames inside artist subfolders
        watch = self._observer.schedule(handler, path, recursive=True)
        self._watches[source_id] = watch
        log.info("Watching source %d: %s", source_id, path)

    def refresh_watches(self):
        for source in self._db.get_sources():
            if source["enabled"] and source["is_available"]:
                self._add_watch(source["id"], source["path"])
            elif source["id"] in self._watches:
                self._observer.unschedule(self._watches.pop(source["id"]))
