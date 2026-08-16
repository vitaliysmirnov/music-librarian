"""Shared audio-file constants and utilities used across scanner, player, and UI."""

import math
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.monkeysaudio import MonkeysAudio
from mutagen.mp3 import MP3
from mutagen.wave import WAVE
from mutagen.wavpack import WavPack

AUDIO_EXTENSIONS: frozenset[str] = frozenset({
    ".flac", ".mp3", ".wav", ".aiff", ".aif", ".m4a", ".alac",
    ".ogg", ".opus", ".ape", ".wv", ".wma", ".aac", ".dsf", ".dff",
})


def audio_paths(folder_path: str) -> list[str]:
    """Return sorted list of audio file paths in a folder."""
    folder = Path(folder_path)
    if not folder.is_dir():
        return []
    return sorted(
        str(f) for f in folder.iterdir()
        if f.is_file()
        and not f.name.startswith("._")
        and f.suffix.lower() in AUDIO_EXTENSIONS
    )


def read_track_tags(path: str) -> tuple[str, str, int]:
    """Return (artist, title, duration_ms) from file tags; falls back to ("", stem, 0)."""
    try:
        audio = MutagenFile(path, easy=True)
        if audio:
            t = audio.tags or {}
            artist = next((t[k][0] for k in ("artist", "albumartist") if k in t), "")
            title  = t.get("title", [Path(path).stem])[0]
            duration_ms = int(getattr(audio.info, "length", 0) * 1000)
            return artist.strip(), title.strip(), duration_ms
    except Exception:
        pass
    return "", Path(path).stem, 0


def read_full_tags(path: str) -> tuple[str, str, str, int]:
    """Return (artist, title, album, duration_ms) from file tags."""
    try:
        audio = MutagenFile(path, easy=True)
        if audio:
            t = audio.tags or {}
            artist = next((t[k][0] for k in ("artist", "albumartist") if k in t), "")
            title  = t.get("title", [Path(path).stem])[0]
            album  = t.get("album", [""])[0]
            duration_ms = int(getattr(audio.info, "length", 0) * 1000)
            return artist.strip(), title.strip(), album.strip(), duration_ms
    except Exception:
        pass
    return "", Path(path).stem, "", 0


def duration_from_file(path: str) -> int:
    """Total duration of an audio file in ms."""
    for easy in (False, True):
        try:
            af = MutagenFile(path, easy=easy)
            if af is not None and af.info is not None:
                ms = math.ceil(getattr(af.info, "length", 0) * 1000)
                if ms > 0:
                    return ms
        except Exception:
            pass
    for cls in (FLAC, MP3, WAVE, WavPack, MonkeysAudio):
        try:
            af = cls(path)
            if af is not None and af.info is not None:
                ms = math.ceil(getattr(af.info, "length", 0) * 1000)
                if ms > 0:
                    return ms
        except Exception:
            pass
    return 0
