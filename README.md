# Music Librarian

A desktop music library manager for collections organised as folders on disk. Built with Python and PySide6.

---

## Features

- **Automatic indexing** — scans configured source folders in a background thread; a live progress bar in the status bar shows how many releases have been processed; the UI stays fully responsive during scanning
- **Folder-name parsing** — a configurable mask extracts artist, year, title, catalog number, media type and any custom fields directly from folder names
- **Searchable, sortable table** — multi-column sort with tiebreakers; columns driven by the mask; per-column visibility and reorderable headers
- **Real-time watch** — monitors the filesystem via watchdog and reflects changes (added/removed/renamed folders) instantly without a full rescan
- **Drive awareness** — detects external drive connects/disconnects and marks releases as available/unavailable accordingly
- **Built-in player** — plays audio files via Qt Multimedia; supports queue reordering, drag-to-enqueue from the releases table to the queue panel, drag-to-player-bar to replace the queue and start playback immediately, shuffle mode, and queue persistence across restarts
- **Release Info** — double-click any release (including offline ones) to open its Release Info dialog; editable metadata fields (artist, title, years, catalog number, media, custom tokens) with **Apply** (keep open) and **Save** (close) buttons; inline tracklist with per-track like buttons, play/enqueue/add-to-playlist context menu, and drag-to-queue support; cover art area with Set Cover / Remove Cover; for offline releases all fields are read-only but the cover and like/playlist actions remain functional; tracklist for offline releases is read from the database (populated at scan time)
- **CUE virtual tracks** — releases stored as a single audio file with a `.cue` sheet are treated identically to multi-file releases: individual tracks can be liked, added to playlists, played, enqueued, and dragged anywhere that accepts tracks; per-track metadata (artist, title, duration, start/end offsets) is preserved through the full drag-and-drop path
- **Track search** — the main search box matches track titles in addition to artist, title, catalog number, and custom tokens; results show the containing release
- **Liked Tracks** — like/unlike individual tracks from the Release Info dialog or the player bar; dedicated Liked view with sortable, resizable, reorderable columns (Track, Release, Cat. No., Date Liked, Duration); right-click any column header to show/hide columns; Reset View button restores default column layout; Play All, drag-to-queue, and Go to Release; tracks whose source files are missing are shown in red with a restricted context menu (Remove only) and an info dialog on play attempt
- **Playlists** — create, delete, and drag-to-reorder playlists in the sidebar; add tracks via drag-and-drop onto a playlist button or from the Release Info context menu; adding a track that is already in the playlist shows a confirmation dialog before inserting the duplicate; playlist view with resizable, reorderable columns (Track, Release, Cat. No., Date Added, Duration); right-click any column header to show/hide columns; Reset View button; drag-reorder rows, Play All, like column, and Go to Release; tracks whose source files are missing are shown in red with a restricted context menu (Remove only) and an info dialog on play attempt
- **Go to Release** — navigate from the player bar, queue panel, Liked view, or playlist view directly to the playing track's release in the library; for tracks added from outside the library (e.g. Finder drag) opens the folder in Finder instead; multi-disc containers auto-expand
- **Truncated-text tooltips** — hovering over any clipped cell in the Releases, Liked, or playlist tables shows the full text after the standard tooltip delay
- **Volume normalisation** — optional ReplayGain-style peak normalisation; enabled per-session from Settings
- **Folder rename** — saving metadata in Release Info renames the folder on disk; liked tracks and playlist entries are updated atomically to reflect the new path
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

Music Librarian supports any number of grouping levels between the source root and a release folder — genre, label, artist, decade, or any other organising layer:

```
# Flat — releases directly under the source root
/Music/_фонотека/David Bowie - 1973 - Aladdin Sane [CD]/

# One grouping level (e.g. artist)
/Music/_фонотека/David Bowie/David Bowie - 1973 - Aladdin Sane [CD]/

# Multiple grouping levels (e.g. genre → label → release)
/Music/_фонотека/_HARDCORE CONTINUUM/Defective Records/1998 [DR 028] DJ Who – Level 3 EP/
```

The scanner recurses into non-matching subdirectories up to eight levels deep. A matching release folder is never descended into further, so audio content inside a release is never mistaken for a nested release. All layouts can coexist under the same source.

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

All scans run in a background `QThread` via `_ScanWorker`. A `QProgressBar` and counter label in the status bar update in real time as releases are processed; the **Scan Now** button is disabled for the duration. The UI remains fully interactive during a scan.

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
│   └── scanner.py         Filesystem walker; reads disk, writes DB;
│                          ProgressCb type alias; _iter_release_dirs() recurses
│                          up to _MAX_SCAN_DEPTH=8 levels into grouping folders;
│                          _sync_tracks_if_changed() skips re-reading tags when
│                          folder mtime is unchanged
├── ui/
│   ├── main_window.py     Top-level QMainWindow; wires all subsystems;
│   │                      _ScanWorker(QObject) runs scan_all() on a QThread
│   │                      and emits progress/finished signals back to the UI
│   ├── releases_tab.py    Library tab — releases view, liked view, playlist view,
│   │                      sidebar; navigation and playlist CRUD; drag attaches the
│   │                      full release row (incl. catalog number) as
│   │                      application/x-release-meta mime data
│   ├── player_bar.py      Transport controls, track/album labels, like button,
│   │                      Go to Release context menu; accepts drag-and-drop —
│   │                      drop replaces the queue and starts playback immediately
│   ├── player_engine.py   Queue management and QMediaPlayer wrapper
│   ├── queue_panel.py     Floating queue panel with drag-reorder and
│   │                      Go to Release context menu
│   ├── tracklist_popup.py _confirm_add_duplicates helper (duplicate-track dialog)
│   ├── liked_view.py      Liked Tracks table — sort, Play All, drag-to-queue,
│   │                      Go to Release, add to playlist
│   ├── playlist_view.py   Playlist content table — sort, drag-reorder, Play All,
│   │                      URL drop, like column, Go to Release
│   ├── edit_release_dialog.py  Release Info dialog — metadata editing, Apply/Save
│   │                      split, inline tracklist with like/playlist/play/enqueue,
│   │                      cover art, offline read-only mode, CUE expansion
│   ├── sources_tab.py     Add/remove/scan source directories
│   ├── settings_tab.py    App settings (scan mode, mask, theme, log)
│   ├── sidebar_panel.py   Left-column navigation; scrollable playlist list
│   │                      with drag-reorder, drop targets, and delete context menu;
│   │                      _NavButton uses a custom paintEvent on Windows to centre
│   │                      text on ascent+descent (excluding leading) so it aligns
│   │                      with the icon; macOS falls through to super().paintEvent()
│   ├── style.py           Shared QSS constants and platform helpers;
│   │                      build_table_style() / build_tracklist_style() return
│   │                      platform-aware stylesheets (hardcoded hex on Windows
│   │                      dark to match macOS palette values);
│   │                      ElidedTooltipDelegate — character-level elision +
│   │                      tooltip for table cells
│   └── theme.py           Light / dark / system theme switching;
│                          _refresh_table_styles() re-applies build_table_style()
│                          on all live table views when the palette changes
├── utils/
│   ├── __init__.py        Shared helpers: fmt_ms(), open_path()
│   ├── audio.py           AUDIO_EXTENSIONS + audio_paths(), read_track_tags(),
│   │                      read_full_tags(), duration_from_file() — used by
│   │                      scanner, player engine, and Release Info dialog;
│   │                      all three guard with `is not None` (not truthiness) so
│   │                      tagless WAV files return correct duration
│   ├── covers.py          Cover image save/load/rename
│   ├── cue.py             CUE sheet parser — find_cue_for_folder(), parse_cue();
│   │                      returns per-track offsets, artist, title, duration
│   ├── drive_monitor.py   OS drive-mount notifications (macOS / stub)
│   ├── logger.py          Logging setup + in-app log handler
│   └── normalizer.py      ReplayGain-style peak normalisation helper
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
| `tracks_mtime` | `st_mtime` of the folder at the time tracks were last scanned; used to skip re-reading audio tags when the folder has not changed |
| `extras` | JSON blob for custom tokens |

A **release_track** stores the tracklist of each release folder as scanned. Populated during library scan (not on dialog open) so it is available when the drive goes offline. For multi-disc releases tracks are stored under each disc child's `folder_path`, not the parent container's.

| Field | Description |
|---|---|
| `folder_path` | Folder the track belongs to (disc child path for multi-disc) |
| `path` | Absolute file path |
| `track_number` | 1-based position within the folder |
| `artist` / `title` | From file tags or CUE sheet |
| `duration_ms` | Track duration in milliseconds |
| `start_ms` / `end_ms` | CUE track offsets; `0` for whole-file tracks |

A **liked_track** stores per-track metadata at the time of liking. For CUE virtual tracks the primary key is the composite `(path, start_ms)` pair; regular file tracks have `start_ms = 0`. When a release folder is renamed, all matching paths in `liked_tracks` and `playlist_tracks` are updated atomically using a `SUBSTR`-based replacement query.

| Field | Description |
|---|---|
| `path` | Absolute file path |
| `start_ms` | CUE track start offset in ms; `0` for whole-file tracks |
| `end_ms` | CUE track end offset in ms; `0` for whole-file tracks |
| `artist` / `title` / `album` | Read from file tags or CUE sheet via mutagen |
| `folder_path` | Parent directory (used for Go to Release) |
| `duration_ms` | Track duration in milliseconds |
| `date_liked` | ISO timestamp |

**Playlists** are stored in two tables: `playlists` (id, name, date_created, position) and `playlist_tracks` (playlist_id, path, start_ms, end_ms, artist, title, album, folder_path, duration_ms, position). The `(path, start_ms)` pair identifies CUE virtual tracks within a playlist. The `position` column on `playlists` preserves sidebar drag-reorder order. Paths in `playlist_tracks` are updated on folder rename alongside `liked_tracks`.

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

### Background scanning

`_ScanWorker(QObject)` owns the scan state and runs on a dedicated `QThread`. Qt automatically uses queued connections for signals that cross thread boundaries, so `progress` and `finished` signals are safely delivered to the main-thread slots (`_on_scan_progress`, `_on_scan_finished`) without explicit locking.

The SQLite database is opened with WAL mode and `check_same_thread=False`; each `conn()` call creates a new connection from the pool, so the main thread can read (e.g. to refresh the table) concurrently with the scan thread writing.

Track tag reads are skipped via `_sync_tracks_if_changed()`: it compares the folder's current `st_mtime` against the stored `tracks_mtime` column, and only calls `_scan_folder_tracks()` (which reads mutagen tags) when the timestamp differs. The first scan after a schema migration reads everything; subsequent scans only call `os.stat()` per folder.

### Windows dark-mode rendering

Windows Fusion dark theme exposes several palette/metric differences from macOS that require explicit overrides.

**Table and list backgrounds** — `palette(base)` and `palette(alternateBase)` are lighter on Fusion than on macOS. `build_table_style()` and `build_tracklist_style()` in `style.py` substitute hardcoded hex colours (`#1e1e1e` / `#252525` for lists; gradient `#4c4c4c → #2b2b2b` for the header section) when `_IS_WIN and _is_dark_palette()` is true.

**Column header gradient** — Fusion's `palette(button)` is `#3c3c3c` and `palette(mid)` is `#505050`, producing a light-to-dark gradient that is the opposite of macOS. The override reverses this with a hardcoded two-stop gradient going darker top → lighter bottom in `_HEADER_SECTION_WIN_DARK`.

**Footer bar background** — Fusion does not auto-fill a plain `QWidget` child with `palette(window)`. All bottom-bar panels use `setObjectName("_BottomBar")` + `setAttribute(WA_StyledBackground, True)` + a scoped `#_BottomBar { background: palette(window); border-top: 1px solid palette(mid); }` stylesheet; a generic `QWidget { ... }` selector would inherit into child buttons.

**Sidebar icon/text vertical alignment** — `QFontMetrics.height()` includes leading which is larger on Fusion, causing Qt's default `CE_PushButtonLabel` to place the visual text centre below the icon centre. `_NavButton.paintEvent` on Windows centres text using `ascent + descent` (without leading): `ty = (h − (ascent + descent)) // 2`. On macOS `super().paintEvent()` is called instead; `setIcon()` is kept up-to-date via `set_current()` for that path.

### Queue persistence

On quit, `PlayerEngine.save_queue_state()` serialises the full queue (including the release `row` dict) to `queue_state.json`. On next launch, `restore_queue_state()` reconstructs the queue; the player is positioned at the last track but does not auto-play.
