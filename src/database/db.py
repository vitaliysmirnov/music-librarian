import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from src.utils.logger import get_logger

log = get_logger()

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

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init(self):
        with self._connect() as conn:
            conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        with self.conn() as c:
            # Add extras column for custom mask tokens if not present
            cols = [r[1] for r in c.execute("PRAGMA table_info(releases)").fetchall()]
            if "extras" not in cols:
                c.execute("ALTER TABLE releases ADD COLUMN extras TEXT DEFAULT '{}'")

            # Remove {country} that was mistakenly shipped as part of the default mask
            old = "{artist} - {year_recorded} - {title} [{catalog_number}] [{media}] ({year_released}) {country}"
            new = "{artist} - {year_recorded} - {title} [{catalog_number}] [{media}] ({year_released})"
            row = c.execute("SELECT value FROM settings WHERE key='folder_mask'").fetchone()
            if row and row["value"] == old:
                c.execute("UPDATE settings SET value=? WHERE key='folder_mask'", (new,))

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
    ):
        now = datetime.now().isoformat(timespec="seconds")
        extras_json = json.dumps(extras or {}, ensure_ascii=False)
        with self.conn() as c:
            c.execute(
                """
                INSERT INTO releases
                    (source_id, artist, year_recorded, title, catalog_number,
                     media, year_released, folder_path, last_seen_path,
                     is_available, modified_at, extras)
                VALUES (?,?,?,?,?,?,?,?,?,1,?,?)
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
                    extras=excluded.extras
                """,
                (
                    source_id, artist, year_recorded, title,
                    catalog_number, media, year_released,
                    folder_path, folder_path, now, extras_json,
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

    def delete_release_by_path(self, folder_path: str):
        with self.conn() as c:
            c.execute("DELETE FROM releases WHERE folder_path=?", (folder_path,))

    def set_release_availability(self, folder_path: str, available: bool):
        with self.conn() as c:
            c.execute(
                "UPDATE releases SET is_available=? WHERE folder_path=?",
                (int(available), folder_path),
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
            WHERE 1=1
        """
        params: list = []
        # Split into words so "david bowie pinups" matches artist + title together
        for word in search.split():
            query += (
                " AND (a.artist LIKE ? OR a.title LIKE ? OR a.year_recorded LIKE ?"
                " OR a.year_released LIKE ? OR a.catalog_number LIKE ? OR a.media LIKE ?"
                " OR a.extras LIKE ?)"
            )
            params += [f"%{word}%"] * 7
        query += " ORDER BY a.artist, a.year_recorded, a.title"
        with self.conn() as c:
            return c.execute(query, params).fetchall()

    def get_release_by_path(self, folder_path: str) -> sqlite3.Row | None:
        with self.conn() as c:
            return c.execute(
                "SELECT * FROM releases WHERE folder_path=?", (folder_path,)
            ).fetchone()

    def get_release_paths_for_source(self, source_id: int) -> set[str]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT folder_path FROM releases WHERE source_id=?", (source_id,)
            ).fetchall()
        return {r["folder_path"] for r in rows}

    def count_releases(self) -> int:
        with self.conn() as c:
            return c.execute("SELECT COUNT(*) FROM releases").fetchone()[0]

    def clear_releases(self):
        with self.conn() as c:
            c.execute("DELETE FROM releases")
