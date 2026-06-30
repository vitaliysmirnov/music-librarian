import json
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from src.utils.logger import get_logger

log = get_logger()

_DASH_CHARS = "‐‑‒–—―−﹘﹣－"


def _search_norm(s: str) -> str:
    """Lowercase and collapse all dash variants to a plain hyphen."""
    s = s.lower()
    for ch in _DASH_CHARS:
        s = s.replace(ch, "-")
    return s

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    path         TEXT    NOT NULL UNIQUE,
    is_available INTEGER NOT NULL DEFAULT 1,
    last_scan    TEXT,
    enabled      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS releases (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id      INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    artist         TEXT    NOT NULL,
    year_recorded  TEXT    NOT NULL,
    title          TEXT    NOT NULL,
    catalog_number TEXT,
    media          TEXT,
    year_released  TEXT,
    folder_path    TEXT    NOT NULL UNIQUE,
    last_seen_path TEXT    NOT NULL,
    is_available   INTEGER NOT NULL DEFAULT 1,
    modified_at    TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_releases_source  ON releases(source_id);
CREATE INDEX IF NOT EXISTS idx_releases_artist  ON releases(artist);
CREATE INDEX IF NOT EXISTS idx_releases_title   ON releases(title);
"""


class Database:
    def __init__(self, db_path: Path):
        self._path = str(db_path)
        self._init()

    @property
    def covers_dir(self) -> Path:
        return Path(self._path).parent / "covers"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        # Unicode-aware normalisation for search: lowercase + collapse all dash
        # variants (en-dash, em-dash, minus, etc.) to a plain hyphen so that
        # e.g. "Би-2" and "Би—2" match the same query term.
        conn.create_function("py_lower", 1, lambda s: _search_norm(s) if s else "")
        return conn

    def _init(self):
        with self._connect() as conn:
            conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        import unicodedata as _ud
        with self.conn() as c:
            cols = [r[1] for r in c.execute("PRAGMA table_info(releases)").fetchall()]
            if "extras" not in cols:
                c.execute("ALTER TABLE releases ADD COLUMN extras TEXT DEFAULT '{}'")
            if "disc_number" not in cols:
                c.execute("ALTER TABLE releases ADD COLUMN disc_number INTEGER NOT NULL DEFAULT 1")
            if "is_multi_disc" not in cols:
                c.execute("ALTER TABLE releases ADD COLUMN is_multi_disc INTEGER NOT NULL DEFAULT 0")
            if "parent_path" not in cols:
                c.execute("ALTER TABLE releases ADD COLUMN parent_path TEXT")

            # Normalise all stored paths to NFC — older entries may be NFD (macOS
            # filesystem) while the scanner now writes NFC, causing duplicates.
            # Runs on every startup so it catches entries created after the last run.
            path_rows = c.execute("SELECT id, folder_path FROM releases").fetchall()
            for row in path_rows:
                nfc = _ud.normalize("NFC", row["folder_path"])
                if nfc != row["folder_path"]:
                    existing = c.execute(
                        "SELECT id FROM releases WHERE folder_path=?", (nfc,)
                    ).fetchone()
                    if existing:
                        # NFC version already present — drop NFD entry and its disc children
                        c.execute("DELETE FROM releases WHERE parent_path=?", (row["folder_path"],))
                        c.execute("DELETE FROM releases WHERE id=?", (row["id"],))
                    else:
                        c.execute(
                            "UPDATE releases SET folder_path=?, last_seen_path=? WHERE id=?",
                            (nfc, nfc, row["id"]),
                        )
            parent_rows = c.execute(
                "SELECT id, parent_path FROM releases WHERE parent_path IS NOT NULL"
            ).fetchall()
            for row in parent_rows:
                nfc = _ud.normalize("NFC", row["parent_path"])
                if nfc != row["parent_path"]:
                    c.execute(
                        "UPDATE releases SET parent_path=? WHERE id=?", (nfc, row["id"])
                    )

            # Remove {country} that was mistakenly shipped as part of the default mask
            old = "{artist} - {year_recorded} - {title} [{catalog_number}] [{media}] ({year_released}) {country}"
            new = "{artist} - {year_recorded} - {title} [{catalog_number}] [{media}] ({year_released})"
            row = c.execute("SELECT value FROM settings WHERE key='folder_mask'").fetchone()
            if row and row["value"] == old:
                c.execute("UPDATE settings SET value=? WHERE key='folder_mask'", (new,))

        # Rename cover files from NFD-keyed to NFC-keyed names — runs after the
        # DB transaction commits so a filesystem error here cannot roll it back.
        try:
            from src.utils.covers import migrate_nfd_covers
            with self.conn() as c:
                all_paths = [r["folder_path"] for r in
                             c.execute("SELECT folder_path FROM releases").fetchall()]
            migrate_nfd_covers(self.covers_dir, all_paths)
        except Exception:
            log.warning("Cover NFD→NFC file migration failed (will retry on next startup)")

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = self._connect()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    # ── Settings ──────────────────────────────────────────────────────────

    def get_setting(self, key: str, default: str = "") -> str:
        with self.conn() as c:
            row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        with self.conn() as c:
            c.execute(
                "INSERT INTO settings(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # ── Sources ────────────────────────────────────────────────────────────

    def add_source(self, path: str) -> int:
        with self.conn() as c:
            cur = c.execute(
                "INSERT OR IGNORE INTO sources(path) VALUES(?)", (path,)
            )
            if cur.lastrowid:
                return cur.lastrowid
            row = c.execute("SELECT id FROM sources WHERE path=?", (path,)).fetchone()
            return row["id"]

    def get_sources(self) -> list[sqlite3.Row]:
        with self.conn() as c:
            return c.execute("SELECT * FROM sources ORDER BY id").fetchall()

    def update_source_availability(self, source_id: int, available: bool):
        with self.conn() as c:
            c.execute(
                "UPDATE sources SET is_available=? WHERE id=?",
                (int(available), source_id),
            )

    def update_source_last_scan(self, source_id: int):
        with self.conn() as c:
            c.execute(
                "UPDATE sources SET last_scan=? WHERE id=?",
                (datetime.now().isoformat(timespec="seconds"), source_id),
            )

    def set_source_enabled(self, source_id: int, enabled: bool):
        with self.conn() as c:
            c.execute(
                "UPDATE sources SET enabled=? WHERE id=?",
                (int(enabled), source_id),
            )

    def delete_source(self, source_id: int):
        with self.conn() as c:
            c.execute("DELETE FROM sources WHERE id=?", (source_id,))

    # ── Releases ───────────────────────────────────────────────────────────

    def upsert_release(
        self,
        source_id: int,
        artist: str,
        year_recorded: str,
        title: str,
        catalog_number: str | None,
        media: str | None,
        year_released: str | None,
        folder_path: str,
        extras: dict | None = None,
        disc_number: int = 1,
        is_multi_disc: bool = False,
        parent_path: str | None = None,
    ):
        now = datetime.now().isoformat(timespec="seconds")
        extras_json = json.dumps(extras or {}, ensure_ascii=False)
        with self.conn() as c:
            # Remove any stale NFD-keyed entry before inserting the NFC version so
            # macOS NFD/NFC path variance never creates duplicate rows.
            nfd = unicodedata.normalize("NFD", folder_path)
            if nfd != folder_path:
                c.execute("DELETE FROM releases WHERE parent_path=?", (nfd,))
                c.execute("DELETE FROM releases WHERE folder_path=?", (nfd,))
            c.execute(
                """
                INSERT INTO releases
                    (source_id, artist, year_recorded, title, catalog_number,
                     media, year_released, folder_path, last_seen_path,
                     is_available, modified_at, extras,
                     disc_number, is_multi_disc, parent_path)
                VALUES (?,?,?,?,?,?,?,?,?,1,?,?,?,?,?)
                ON CONFLICT(folder_path) DO UPDATE SET
                    artist=excluded.artist,
                    year_recorded=excluded.year_recorded,
                    title=excluded.title,
                    catalog_number=excluded.catalog_number,
                    media=excluded.media,
                    year_released=excluded.year_released,
                    last_seen_path=excluded.last_seen_path,
                    is_available=1,
                    modified_at=excluded.modified_at,
                    extras=excluded.extras,
                    is_multi_disc=excluded.is_multi_disc,
                    parent_path=excluded.parent_path
                """,
                (
                    source_id, artist, year_recorded, title,
                    catalog_number, media, year_released,
                    folder_path, folder_path, now, extras_json,
                    disc_number, int(is_multi_disc), parent_path,
                ),
            )

    def rename_release(self, old_path: str, new_path: str, **fields):
        now = datetime.now().isoformat(timespec="seconds")
        with self.conn() as c:
            row = c.execute(
                "SELECT id FROM releases WHERE folder_path=?", (old_path,)
            ).fetchone()
            if not row:
                return
            sets = ", ".join(f"{k}=?" for k in fields)
            vals = list(fields.values()) + [new_path, new_path, now, old_path]
            c.execute(
                f"UPDATE releases SET {sets}, folder_path=?, last_seen_path=?, "
                f"modified_at=? WHERE folder_path=?",
                vals,
            )
            # Update disc children: adjust their folder_path and parent_path
            children = c.execute(
                "SELECT id, folder_path FROM releases WHERE parent_path=?", (old_path,)
            ).fetchall()
            for child in children:
                old_child = child["folder_path"]
                new_child = new_path + old_child[len(old_path):]
                c.execute(
                    "UPDATE releases SET folder_path=?, last_seen_path=?, parent_path=?, modified_at=? WHERE id=?",
                    (new_child, new_child, new_path, now, child["id"]),
                )

    def delete_release_by_path(self, folder_path: str):
        with self.conn() as c:
            c.execute(
                "DELETE FROM releases WHERE folder_path=? OR parent_path=?",
                (folder_path, folder_path),
            )

    def set_release_availability(self, folder_path: str, available: bool):
        with self.conn() as c:
            c.execute(
                "UPDATE releases SET is_available=? WHERE folder_path=? OR parent_path=?",
                (int(available), folder_path, folder_path),
            )

    def set_releases_availability_by_source(self, source_id: int, available: bool):
        with self.conn() as c:
            c.execute(
                "UPDATE releases SET is_available=? WHERE source_id=?",
                (int(available), source_id),
            )

    def get_releases(self, search: str = "") -> list[sqlite3.Row]:
        query = """
            SELECT a.*, s.path AS source_path, s.is_available AS source_available
            FROM releases a
            JOIN sources s ON a.source_id = s.id
            WHERE a.parent_path IS NULL
        """
        params: list = []
        # Split into words so "david bowie pinups" matches artist + title together
        for word in search.split():
            w = f"%{_search_norm(word)}%"
            query += (
                " AND (py_lower(a.artist) LIKE ? OR py_lower(a.title) LIKE ?"
                " OR py_lower(a.year_recorded) LIKE ? OR py_lower(a.year_released) LIKE ?"
                " OR py_lower(a.catalog_number) LIKE ? OR py_lower(a.media) LIKE ?"
                " OR py_lower(a.extras) LIKE ?)"
            )
            params += [w] * 7
        query += " ORDER BY a.artist, a.year_recorded, a.title"
        with self.conn() as c:
            return c.execute(query, params).fetchall()

    def get_release_by_path(self, folder_path: str) -> sqlite3.Row | None:
        with self.conn() as c:
            return c.execute(
                "SELECT * FROM releases WHERE folder_path=?", (folder_path,)
            ).fetchone()

    def get_disc_entries(self, parent_path: str) -> list[sqlite3.Row]:
        with self.conn() as c:
            return c.execute(
                "SELECT a.*, s.path AS source_path, s.is_available AS source_available"
                " FROM releases a JOIN sources s ON a.source_id = s.id"
                " WHERE a.parent_path=? ORDER BY a.disc_number",
                (parent_path,),
            ).fetchall()

    def update_disc_children_metadata(self, parent_path: str, **fields):
        """Update metadata fields for all disc children of a container."""
        if not fields:
            return
        now = datetime.now().isoformat(timespec="seconds")
        sets = ", ".join(f"{k}=?" for k in fields) + ", modified_at=?"
        vals = list(fields.values()) + [now, parent_path]
        with self.conn() as c:
            c.execute(f"UPDATE releases SET {sets} WHERE parent_path=?", vals)

    def delete_disc_entries_for_parent(self, parent_path: str):
        with self.conn() as c:
            c.execute("DELETE FROM releases WHERE parent_path=?", (parent_path,))

    def get_release_paths_for_source(self, source_id: int) -> set[str]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT folder_path FROM releases WHERE source_id=? AND parent_path IS NULL",
                (source_id,),
            ).fetchall()
        return {r["folder_path"] for r in rows}

    def count_releases(self) -> int:
        with self.conn() as c:
            return c.execute(
                "SELECT COUNT(*) FROM releases WHERE parent_path IS NULL"
            ).fetchone()[0]

    def clear_releases(self):
        with self.conn() as c:
            c.execute("DELETE FROM releases")
