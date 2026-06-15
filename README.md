# Music Librarian

A desktop utility for managing a personal music collection stored as folders on disk.

> **Note:** The current app icons are placeholders (checkerboard pattern). Final icons will be added in a future release.

## What it does

- Scans configured source folders and indexes releases into a local SQLite database
- Parses folder names using a configurable mask (e.g. `{artist} - {year_recorded} - {title} [{catalog_number}]`)
- Displays the collection in a sortable, searchable table with custom columns driven by the mask
- Watches the filesystem for changes and updates the library automatically
- Detects when external drives are connected or disconnected and updates availability accordingly
- Runs in the background via a system tray icon

## Folder name mask

The mask defines how folder names are parsed into metadata fields. Required tokens: `{artist}`, `{year_recorded}`, `{title}`. Optional tokens can be wrapped in brackets:

```
{artist} - {year_recorded} - {title} [{catalog_number}] [{media}] ({year_released})
```

Custom tokens (any name not in the built-in set) are stored and displayed as additional columns in the Releases table.

## Installation

Download the latest build for your platform from the [Releases](../../releases) page:

- **macOS** — open the `.dmg`, drag the app to `/Applications`
- **Windows** — extract the `.zip`, run `Music Librarian.exe`

## Running from source

```bash
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Building

```bash
python assets/gen_icons.py      # generate placeholder icons
python build.py                 # produces dist/*.dmg (macOS) or dist/*.zip (Windows)
```

## Requirements

- Python 3.12+
- PySide6
- watchdog
- pyobjc-framework-Cocoa (macOS only)
