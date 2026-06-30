import json
import queue
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
    """Puts raw filesystem event tuples into a queue — does nothing else.

    All processing (NFC normalisation, DB writes, logging) happens on the
    main thread in LibraryWatcher.process_pending(), avoiding any macOS
    text-system (TSM) initialisation from the watchdog background thread.
    """

    def __init__(self, event_queue: queue.SimpleQueue):
        super().__init__()
        self._q = event_queue

    def on_created(self, event):
        if isinstance(event, DirCreatedEvent):
            self._q.put(("created", event.src_path, ""))

    def on_deleted(self, event):
        if isinstance(event, DirDeletedEvent):
            self._q.put(("deleted", event.src_path, ""))

    def on_moved(self, event):
        if isinstance(event, DirMovedEvent):
            self._q.put(("moved", event.src_path, event.dest_path))


class LibraryWatcher:
    def __init__(self, db: Database, on_change):
        self._db = db
        self._on_change = on_change
        self._observer = Observer()
        self._watches: dict[int, object] = {}
        self._queue: queue.SimpleQueue = queue.SimpleQueue()
        # source_id → (source_path, pattern) for use in process_pending
        self._source_info: dict[int, tuple[str, re.Pattern]] = {}

    def start(self):
        self._schedule_all()
        self._observer.start()
        log.info("FS watcher started")

    def stop(self):
        self._observer.stop()
        self._observer.join()
        log.info("FS watcher stopped")

    def process_pending(self):
        """Drain the event queue and process events on the caller's thread.

        Must be called from the main thread (e.g. via a QTimer).
        """
        changed = False
        while not self._queue.empty():
            try:
                kind, src, dst = self._queue.get_nowait()
            except queue.Empty:
                break
            changed |= self._handle(kind, _norm(src), _norm(dst))
        if changed:
            self._on_change()

    def _handle(self, kind: str, src: str, dst: str) -> bool:
        # Determine which source this path belongs to
        source_id, source_path, pattern = self._source_for(src) or (None, None, None)
        if source_id is None and kind == "moved":
            source_id, source_path, pattern = self._source_for(dst) or (None, None, None)
        if source_id is None:
            return False

        if kind == "created":
            if not _is_release_path(src, source_path, pattern):
                return False
            parsed = parse_folder_name(Path(src).name, pattern)
            if not parsed:
                return False
            self._db.upsert_release(
                source_id=source_id,
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
            return True

        if kind == "deleted":
            if not _parent_is_source_or_artist(src, source_path):
                return False
            # Skip if the folder still exists on disk — macOS FSEvents can fire a
            # spurious DirDeletedEvent for a path that was only briefly absent
            # during a rapid rename sequence.
            if Path(src).exists():
                return False
            self._db.delete_release_by_path(src)
            log.info("Watcher: deleted release: %s", src)
            return True

        if kind == "moved":
            src_ok = _is_release_path(src, source_path, pattern)
            dst_ok = _is_release_path(dst, source_path, pattern)

            if src_ok and dst_ok:
                parsed = parse_folder_name(Path(dst).name, pattern)
                if parsed:
                    found = self._db.rename_release(
                        src, dst,
                        artist=parsed.artist,
                        year_recorded=parsed.year_recorded,
                        title=parsed.title,
                        catalog_number=parsed.catalog_number,
                        media=parsed.media,
                        year_released=parsed.year_released,
                        extras=json.dumps(parsed.extras, ensure_ascii=False),
                    )
                    if found:
                        log.info("Watcher: renamed: %s → %s", src, dst)
                    else:
                        log.info("Watcher: rename no-op (src not in DB): %s → %s", src, dst)
                else:
                    self._db.delete_release_by_path(src)
                    log.info("Watcher: renamed to unparseable, removed: %s", src)
                return True

            if src_ok:
                self._db.delete_release_by_path(src)
                log.info("Watcher: release moved out: %s", src)
                return True

            if dst_ok:
                parsed = parse_folder_name(Path(dst).name, pattern)
                if parsed:
                    self._db.upsert_release(
                        source_id=source_id,
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
                    return True

        return False

    def _source_for(self, path: str) -> tuple[int, str, re.Pattern] | None:
        """Find which registered source contains `path`."""
        for sid, (sp, pat) in self._source_info.items():
            try:
                Path(path).relative_to(sp)
                return sid, sp, pat
            except ValueError:
                continue
        return None

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
        handler = _ReleaseEventHandler(self._queue)
        watch = self._observer.schedule(handler, path, recursive=True)
        self._watches[source_id] = watch
        self._source_info[source_id] = (path, pattern)
        log.info("Watching source %d: %s", source_id, path)

    def refresh_watches(self):
        for source in self._db.get_sources():
            if source["enabled"] and source["is_available"]:
                self._add_watch(source["id"], source["path"])
            elif source["id"] in self._watches:
                self._observer.unschedule(self._watches.pop(source["id"]))
                self._source_info.pop(source["id"], None)
