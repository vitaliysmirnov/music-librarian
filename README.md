# Music Librarian

A desktop music library manager for collections organised as folders on disk. Built with Python and PySide6.

---

## Features

- **Automatic indexing** — scans configured source folders and stores metadata in a local SQLite database; no external services required
- **Folder-name parsing** — a configurable mask extracts artist, year, title, catalog number, media type and any custom fields directly from folder names
- **Searchable, sortable table** — multi-column sort with tiebreakers; columns driven by the mask; per-column visibility and reorderable headers
- **Real-time watch** — monitors the filesystem via watchdog and reflects changes (added/removed/renamed folders) instantly without a full rescan
- **Drive awareness** — detects external drive connects/disconnects and marks releases as available/unavailable accordingly
- **Built-in player** — plays audio files via Qt Multimedia; supports queue reordering, drag-to-enqueue from the table, shuffle mode, and queue persistence across restarts
- **Tracklist popup** — shows all tracks in a release with artist, title and duration; play, enqueue, or like individual tracks; drag tracks to the queue or onto a playlist button
- **Liked Tracks** — like/unlike individual tracks from the tracklist popup or the player bar; dedicated Liked view with sortable columns (Track, Release, Cat. No., Date Liked, Duration), Play All, drag-to-queue, and Go to Release
- **Playlists** — create, delete, and drag-to-reorder playlists in the sidebar; add tracks via drag-and-drop onto a playlist button or from a tracklist popup context menu; playlist view with sortable columns (Track, Release, Cat. No., Date Added, Duration), drag-reorder, Play All, like column, and Go to Release
- **Go to Release** — navigate from the player bar, queue panel, Liked view, or playlist view directly to the playing track's release in the library; for tracks added from outside the library (e.g. Finder drag) opens the folder in Finder instead; multi-disc containers auto-expand
- **Truncated-text tooltips** — hovering over any clipped cell in the Releases, Liked, or playlist tables shows the full text after the standard tooltip delay
- **Volume normalisation** — optional ReplayGain-style peak normalisation; enabled per-session from Settings
- **Release editing** — edit artist, title and other fields; renames the folder on disk automatically
- **System tray** — runs in background with a tray icon; tooltip shows the currently playing track; main window can be hidden

---

## Supported audio formats

`.flac` `.mp3` `.wav` `.aiff` `.aif` `.m4a` `.alac` `.ogg` `.opus` `.ape` `.wv` `.wma` `.aac` `.dsf` `.dff`

---

## Folder name mask

The mask is the core configuration that tells Music Librarian how your folders are named. It uses `{token}` placeholders separated by literal text.

### Built-in tokens

| Token | Description |
|---|---|
| `{artist}` | Artist or group name (required) |
| `{title}` | Release title (required) |
| `{year_recorded}` | Recording year (required) |
| `{year_released}` | Release year |
| `{catalog_number}` | Label catalog number |
| `{media}` | Format — `CD`, `LP`, `WEB`, etc. |
| `{disc_number}` | Disc number for multi-disc sets |
| `{source}` | Provenance tag, e.g. `_фонотека` |

### Optional parts

Wrap a portion of the mask in `[` `]` to make it optional — the pattern will match with or without that segment:

```
{artist} - {year_recorded} - {title} [{catalog_number}] [{media}] ({year_released})
```

This matches both:
```
David Bowie - 1973 - Aladdin Sane [EMI – CDP 79 4768 2] [CD] (1990)
David Bowie - 1973 - Aladdin Sane
```

### Custom tokens

Any `{name}` not in the built-in set is treated as a custom token. Custom tokens appear as additional columns in the Releases table and are included in search.

### Multi-disc releases

If a folder contains no audio files but has subdirectories that do, it is treated as a multi-disc container. The subdirectories are disc entries. The container row can be expanded in the table to reveal individual discs. Disc children are visually distinguished by a subtle background tint. Multi-disc containers support drag-and-drop to the queue as well as **Play Now** and **Add to Queue** from the context menu — all tracks across all discs are collected automatically.

---

## Layout

Music Librarian supports two folder layouts:

```
# Flat — releases directly under the source root
/Music/_фонотека/David Bowie - 1973 - Aladdin Sane [CD]/

# Artist-organised — one level of artist folders
/Music/_фонотека/David Bowie/David Bowie - 1973 - Aladdin Sane [CD]/
```

Both layouts can coexist under the same source.

---

## Installation

Download the latest build from the [Releases](../../releases) page:

| Platform | File |
|---|---|
| macOS | `.dmg` — open, drag app to `/Applications` |
| Windows | `.zip` — extract, run `Music Librarian.exe` |

---

## Running from source

**Requirements:** Python 3.12+

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

On macOS, `pyobjc-framework-Cocoa` is required (already in `requirements.txt`) for Trash support, drive-mount notifications, and the tray icon tooltip.

---

## Updating icons

### Main app icon

Edit `assets/icon.svg`, then run:

```bash
# Requires: pip install cairosvg pillow
DYLD_LIBRARY_PATH="$(brew --prefix cairo)/lib" python -c \
  "import cairosvg; cairosvg.svg2png(url='assets/icon.svg', write_to='assets/icon.png', output_width=1024, output_height=1024)"
python assets/gen_icons.py   # produces icon.icns, icon.ico, tray.png
```

`gen_icons.py` reads `assets/icon.png` (1024×1024 RGBA master) and outputs:
- `assets/icon.icns` — macOS bundle icon
- `assets/icon.ico` — Windows multi-size icon
- `assets/icon.iconset/` — individual PNG sizes (gitignored)

### Tray icon

The tray icon (`assets/tray.png`, 44×44 RGBA) is generated automatically by `gen_icons.py` from the drawing code in `_make_tray()` at the bottom of that script. Edit that function to change the tray icon, then re-run `gen_icons.py`.

On macOS the tray icon is drawn in **white on transparent** so the system can apply a dark/light template tint automatically.

---

## Building a distributable

```bash
pip install -r requirements-dev.txt
python assets/gen_icons.py   # regenerate icons from assets/icon.png
python build.py              # produces dist/*.dmg (macOS) or dist/*.zip (Windows)
```

`build.py` calls PyInstaller with the spec in `music_librarian.spec`, then packages the output.

---

## Configuration

All settings are stored in the SQLite database (`music_librarian.db`) in the platform data directory:

| Platform | Path |
|---|---|
| macOS | `~/Library/Application Support/MusicLibrarian/` |
| Windows | `%APPDATA%\MusicLibrarian\` |
| Linux | `~/.local/share/MusicLibrarian/` |

### Scan modes

| Mode | Behaviour |
|---|---|
| Manual | Scan only when you click **Scan Now** |
| Automatic | Scan on startup and every N minutes (configurable) |

The filesystem watcher runs independently of scan mode and handles real-time changes.

### Playback settings

| Setting | Location | Description |
|---|---|---|
| Normalize volume | Settings → Playback | Enables peak normalisation for consistent loudness across tracks |

---

## Project structure

```
src/
├── database/
│   └── db.py              SQLite access — schema, migrations, all CRUD
├── scanner/
│   ├── mask.py            Compiles the folder-name mask string to a regex
│   ├── parser.py          ParsedRelease dataclass + parse_folder_name()
│   └── scanner.py         Filesystem walker; reads disk, writes DB
├── ui/
│   ├── main_window.py     Top-level QMainWindow; wires all subsystems
│   ├── releases_tab.py    Library tab — releases view, liked view, playlist view,
│   │                      sidebar; navigation and playlist CRUD
│   ├── player_bar.py      Transport controls, track/album labels, like button,
│   │                      Go to Release context menu
│   ├── player_engine.py   Queue management and QMediaPlayer wrapper;
│   │                      _read_track_tags / _read_full_tags helpers
│   ├── queue_panel.py     Floating queue panel with drag-reorder and
│   │                      Go to Release context menu
│   ├── tracklist_popup.py Per-release track list dialog; like buttons;
│   │                      drag tracks to queue or playlist buttons
│   ├── liked_view.py      Liked Tracks table — sort, Play All, drag-to-queue,
│   │                      Go to Release, add to playlist
│   ├── playlist_view.py   Playlist content table — sort, drag-reorder, Play All,
│   │                      URL drop, like column, Go to Release
│   ├── edit_release_dialog.py  Metadata edit + folder rename
│   ├── sources_tab.py     Add/remove/scan source directories
│   ├── settings_tab.py    App settings (scan mode, mask, theme, log)
│   ├── sidebar_panel.py   Left-column navigation; scrollable playlist list
│   │                      with drag-reorder, drop targets, and delete context menu
│   ├── style.py           Shared QSS constants; ElidedTooltipDelegate —
│   │                      character-level elision + tooltip for table cells
│   └── theme.py           Light / dark / system theme switching
├── utils/
│   ├── __init__.py        Shared helpers: fmt_ms(), open_path()
│   ├── audio.py           AUDIO_EXTENSIONS constant
│   ├── covers.py          Cover image save/load/rename
│   ├── drive_monitor.py   OS drive-mount notifications (macOS / stub)
│   └── logger.py          Logging setup + in-app log handler
└── watcher/
    └── watcher.py         watchdog observer + Qt-thread event draining
```

---

## Data model

A **release** corresponds to one folder on disk. Key fields:

| Field | Description |
|---|---|
| `folder_path` | Absolute, NFC-normalised path — primary key |
| `artist` | From folder name via mask |
| `title` | From folder name via mask |
| `year_recorded` | Recording year |
| `catalog_number` | Label catalog number |
| `media` | Format string |
| `is_multi_disc` | `1` if the folder has no direct audio but has disc subdirectories |
| `disc_number` | `0` for a multi-disc container; ≥1 for disc entries |
| `is_available` | `0` when the source drive is offline |
| `date_added` | ISO timestamp of first insert; never updated on re-scan |
| `extras` | JSON blob for custom tokens |

A **liked_track** stores per-file metadata at the time of liking (album is read from the file's ALBUM tag, falling back to the release DB entry):

| Field | Description |
|---|---|
| `path` | Absolute file path — primary key |
| `artist` / `title` / `album` | Read from file tags via mutagen |
| `folder_path` | Parent directory (used for Go to Release) |
| `duration_ms` | Track duration in milliseconds |
| `date_liked` | ISO timestamp |

**Playlists** are stored in two tables: `playlists` (id, name, date_created, position) and `playlist_tracks` (playlist_id, path, artist, title, album, folder_path, duration_ms, position). The `position` column on `playlists` preserves sidebar drag-reorder order.

---

## Development notes

### NFC path normalisation

macOS HFS+/APFS delivers paths in NFD form via watchdog while Python's `os.listdir` / `iterdir` produces NFC. All paths are normalised to NFC before storage and comparison to avoid spurious duplicate entries.

### Column layout in ReleasesModel

```
[COL_PLAY] [known tokens in mask order] [custom tokens] [Disc] [Source] [Available] [Path]
```

`COL_PLAY` (index 0) holds the play/expand button; it never participates in sort. The column count changes when the mask is edited, triggering a full `_apply_default_widths()` reset.

### Sort proxy

`_MultiSortProxy` uses a custom `lessThan` with a fixed tiebreaker chain: primary column → artist → year_recorded → title → disc_number. Blank values always sort to the end. Numeric strings sort numerically. Multi-disc containers (disc_number = −1) always sort before their disc children.

### Text elision in table cells

`ElidedTooltipDelegate` (in `style.py`) is set as the default delegate on all three table views. Its `paint()` method calls `QFontMetrics.elidedText()` and passes the pre-elided string directly to `style.drawControl(CE_ItemViewItem)`, bypassing `QStyledItemDelegate.paint()` which would call `initStyleOption()` a second time and overwrite the result. This is necessary because macOS `QMacStyle` routes text through CoreText which truncates at word boundaries; pre-eliding at character level prevents that. The tooltip threshold uses the same margin constant (`_MARGIN = 16 px`) as the elision.

### Queue persistence

On quit, `PlayerEngine.save_queue_state()` serialises the full queue (including the release `row` dict) to `queue_state.json`. On next launch, `restore_queue_state()` reconstructs the queue; the player is positioned at the last track but does not auto-play.
