"""CUE sheet parsing for single-file albums (FLAC, WAV, APE, WV, MP3, TTA…)."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src.utils.audio import AUDIO_EXTENSIONS


@dataclass
class CueTrack:
    title: str
    artist: str
    start_ms: int
    end_ms: int  # 0 = play to end of file (last track)


def _decode(data: bytes) -> str:
    """Detect encoding and decode CUE bytes (UTF-8, CP1251, CP949, Shift-JIS…)."""
    for enc in ("utf-8-sig", "utf-8", "cp1251", "cp949", "shift_jis", "latin-1"):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("latin-1", errors="replace")


def _ts_to_ms(mm: str, ss: str, ff: str) -> int:
    """CUE timestamp MM:SS:FF → ms  (FF = 1/75 s frames)."""
    return (int(mm) * 60 + int(ss)) * 1000 + int(ff) * 1000 // 75


def _resolve_audio(cue_dir: Path, filename: str) -> Path | None:
    """Locate the audio file named in the CUE, with case-insensitive fallback."""
    direct = cue_dir / filename
    if direct.is_file():
        return direct
    fname_low = filename.lower()
    stem_low  = Path(filename).stem.lower()
    for f in cue_dir.iterdir():
        if not f.is_file():
            continue
        if f.name.lower() == fname_low:
            return f
        if f.stem.lower() == stem_low and f.suffix.lower() in AUDIO_EXTENSIONS:
            return f
    return None


def parse_cue(cue_path: Path) -> tuple[Path | None, str, str, list[CueTrack]]:
    """Parse a CUE sheet.

    Returns (audio_path, album_artist, album_title, tracks).
    audio_path is None when the referenced audio file can't be found.
    """
    text = _decode(cue_path.read_bytes())

    album_artist = ""
    album_title  = ""
    audio_file: Path | None = None
    raw: list[dict] = []
    cur: dict | None = None

    for line in text.splitlines():
        s = line.strip()

        m = re.match(r'^FILE\s+"(.+?)"\s+\w+', s, re.IGNORECASE)
        if m:
            audio_file = _resolve_audio(cue_path.parent, m.group(1))
            continue

        m = re.match(r'^PERFORMER\s+"(.*?)"', s, re.IGNORECASE)
        if m:
            if cur is None:
                album_artist = m.group(1)
            else:
                cur["artist"] = m.group(1)
            continue

        m = re.match(r'^TITLE\s+"(.*?)"', s, re.IGNORECASE)
        if m:
            if cur is None:
                album_title = m.group(1)
            else:
                cur["title"] = m.group(1)
            continue

        if re.match(r'^TRACK\s+\d+\s+AUDIO', s, re.IGNORECASE):
            cur = {"title": "", "artist": album_artist, "start": None}
            raw.append(cur)
            continue

        if cur is not None:
            m = re.match(r'^INDEX\s+01\s+(\d+):(\d+):(\d+)', s, re.IGNORECASE)
            if m:
                cur["start"] = _ts_to_ms(*m.groups())

    valid = [t for t in raw if t["start"] is not None]
    tracks: list[CueTrack] = []
    for i, t in enumerate(valid):
        end = valid[i + 1]["start"] if i + 1 < len(valid) else 0
        tracks.append(CueTrack(
            title=t["title"],
            artist=t["artist"],
            start_ms=t["start"],
            end_ms=end,
        ))

    return audio_file, album_artist, album_title, tracks


def find_cue_for_folder(folder: Path) -> Path | None:
    """Return the first .cue file found in folder, or None."""
    try:
        cues = sorted(
            f for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() == ".cue"
        )
        return cues[0] if cues else None
    except OSError:
        return None


def find_cue_for_file(audio: Path) -> Path | None:
    """Find a .cue file that matches this audio file.

    Priority: same-stem match → sole .cue in folder.
    """
    folder = audio.parent
    low_stem = audio.stem.lower()
    try:
        cues = [
            f for f in folder.iterdir()
            if f.is_file() and f.suffix.lower() == ".cue"
        ]
    except OSError:
        return None
    for c in cues:
        if c.stem.lower() == low_stem:
            return c
    return cues[0] if len(cues) == 1 else None
