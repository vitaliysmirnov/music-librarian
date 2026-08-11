import json
from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaMetaData, QMediaPlayer

from src.utils.audio import AUDIO_EXTENSIONS


def _audio_paths(folder_path: str) -> list[str]:
    folder = Path(folder_path)
    if not folder.is_dir():
        return []
    return sorted(
        str(f) for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS
    )


def _read_track_tags(path: str) -> tuple[str, str, int]:
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


def _read_full_tags(path: str) -> tuple[str, str, str, int]:
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



def _row_for_path(path: str, release_row: dict | None) -> tuple[dict, str, str, int]:
    """Return (row, artist, track_title, duration_ms) for a single file.

    When release_row is provided it is used as-is.  When it is absent
    (e.g. a plain Finder drop) we read full tags so the album name from
    the file's metadata ends up in row["title"].
    """
    if release_row is not None:
        artist, title, duration_ms = _read_track_tags(path)
        return release_row, artist, title, duration_ms
    artist, title, album, duration_ms = _read_full_tags(path)
    row = {
        "folder_path":    str(Path(path).parent),
        "title":          album,
        "artist":         artist,
        "catalog_number": "",
    }
    return row, artist, title, duration_ms


@dataclass
class QueueTrack:
    row: dict
    path: str
    artist: str
    title: str
    duration_ms: int = 0


class PlayerEngine(QObject):
    # row, file_path, track_idx (0-based in flat queue), total
    track_changed    = Signal(dict, str, int, int)
    metadata_changed = Signal(str, str)   # artist, title (from file tags)
    state_changed    = Signal(bool)       # True = playing
    position_changed = Signal(int)        # ms
    duration_changed = Signal(int)        # ms
    queue_changed    = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: list[QueueTrack] = []
        self._track_idx = -1

        self._player = QMediaPlayer(self)
        self._audio  = QAudioOutput(self)
        self._audio.setVolume(0.7)
        self._player.setAudioOutput(self._audio)

        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.playbackStateChanged.connect(self._on_state_changed)
        self._player.metaDataChanged.connect(self._on_metadata_changed)
        self._player.positionChanged.connect(lambda ms: self.position_changed.emit(int(ms)))
        self._player.durationChanged.connect(lambda ms: self.duration_changed.emit(int(ms)))

        self._media_devices = QMediaDevices(self)
        self._media_devices.audioOutputsChanged.connect(self._on_audio_device_changed)

    # ── Read-only state ───────────────────────────────────────────────────

    @property
    def queue(self) -> list[QueueTrack]:
        return list(self._queue)

    @property
    def current_track_idx(self) -> int:
        return self._track_idx

    def is_playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def _is_stopped(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.StoppedState

    def volume(self) -> float:
        return self._audio.volume()

    # ── Playback control ──────────────────────────────────────────────────

    def play_release(self, row: dict):
        """Replace queue with all tracks from release and start playback."""
        self._queue.clear()
        self._track_idx = -1
        bare = not row.get("title")
        for p in _audio_paths(row["folder_path"]):
            track_row, artist, title, duration_ms = _row_for_path(p, None if bare else row)
            self._queue.append(QueueTrack(row=track_row, path=p, artist=artist, title=title, duration_ms=duration_ms))
        self.queue_changed.emit()
        if self._queue:
            self._play_at(0)

    def enqueue_release(self, row: dict):
        """Append all tracks from release; start playback if currently idle."""
        bare = not row.get("title")
        for p in _audio_paths(row["folder_path"]):
            track_row, artist, title, duration_ms = _row_for_path(p, None if bare else row)
            self._queue.append(QueueTrack(row=track_row, path=p, artist=artist, title=title, duration_ms=duration_ms))
        self.queue_changed.emit()
        if self._track_idx < 0 and self._queue and self._is_stopped():
            self._play_at(0)

    def play_tracks(self, paths: list[str], release_row: dict | None = None):
        """Replace queue with given paths and start playback."""
        self._queue.clear()
        self._track_idx = -1
        for p in paths:
            row, artist, title, duration_ms = _row_for_path(p, release_row)
            self._queue.append(QueueTrack(row=row, path=p, artist=artist, title=title, duration_ms=duration_ms))
        self.queue_changed.emit()
        if self._queue:
            self._play_at(0)

    def enqueue_tracks(self, paths: list[str], release_row: dict | None = None):
        """Append specific audio files to the queue; start playback if idle."""
        added = False
        for p in paths:
            row, artist, title, duration_ms = _row_for_path(p, release_row)
            self._queue.append(QueueTrack(row=row, path=p, artist=artist, title=title, duration_ms=duration_ms))
            added = True
        if added:
            self.queue_changed.emit()
            if self._track_idx < 0 and self._is_stopped():
                self._play_at(0)

    def clear_queue(self):
        is_active = self._player.playbackState() != QMediaPlayer.PlaybackState.StoppedState
        self._queue.clear()
        self._track_idx = -1
        self.queue_changed.emit()
        if not is_active:
            self._player.stop()
            self.state_changed.emit(False)

    def play_track_at(self, idx: int):
        self._play_at(idx)

    def remove_track(self, idx: int):
        if not (0 <= idx < len(self._queue)):
            return
        self._queue.pop(idx)
        if idx < self._track_idx:
            self._track_idx -= 1
        elif idx == self._track_idx:
            self._track_idx = -1
        self.queue_changed.emit()

    def move_track(self, from_idx: int, to_idx: int):
        n = len(self._queue)
        if from_idx == to_idx or not (0 <= from_idx < n):
            return
        to_idx = max(0, min(to_idx, n - 1))
        track = self._queue.pop(from_idx)
        self._queue.insert(to_idx, track)
        if self._track_idx == from_idx:
            self._track_idx = to_idx
        elif from_idx < self._track_idx <= to_idx:
            self._track_idx -= 1
        elif to_idx <= self._track_idx < from_idx:
            self._track_idx += 1
        self.queue_changed.emit()

    def play_pause(self):
        state = self._player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        elif self._track_idx >= 0:
            self._player.play()
        elif self._queue:
            self._play_at(0)

    def prev(self):
        if self._track_idx > 0:
            self._play_at(self._track_idx - 1)

    def next(self):
        self._advance()

    def seek(self, ms: int):
        self._player.setPosition(ms)

    def set_volume(self, v: float):
        self._audio.setVolume(max(0.0, min(1.0, v)))

    # ── Internal ──────────────────────────────────────────────────────────

    def _play_at(self, idx: int):
        if not (0 <= idx < len(self._queue)):
            return
        self._track_idx = idx
        track = self._queue[idx]
        self._player.setSource(QUrl.fromLocalFile(track.path))
        self._player.play()
        self.track_changed.emit(track.row, track.path, idx, len(self._queue))
        if track.title:
            self.metadata_changed.emit(track.artist, track.title)

    def _advance(self):
        if not self._queue:
            return
        if self._track_idx < 0:
            self._play_at(0)
        elif self._track_idx + 1 < len(self._queue):
            self._play_at(self._track_idx + 1)
        else:
            self._player.stop()

    def _on_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._advance()

    def _on_state_changed(self, state):
        self.state_changed.emit(state == QMediaPlayer.PlaybackState.PlayingState)

    def _on_metadata_changed(self):
        meta   = self._player.metaData()
        artist = (
            meta.stringValue(QMediaMetaData.Key.ContributingArtist) or
            meta.stringValue(QMediaMetaData.Key.AlbumArtist) or
            meta.stringValue(QMediaMetaData.Key.Author) or
            ""
        ).strip()
        title  = meta.stringValue(QMediaMetaData.Key.Title).strip()
        if not title:
            return
        # Qt may not map a plain "artist" tag to any of the above keys;
        # fall back to the artist already read by mutagen for this track.
        if not artist and 0 <= self._track_idx < len(self._queue):
            artist = self._queue[self._track_idx].artist
        self.metadata_changed.emit(artist, title)

    def _on_audio_device_changed(self):
        self._audio.setDevice(QMediaDevices.defaultAudioOutput())

    # ── Queue persistence ─────────────────────────────────────────────────

    def save_queue_state(self, path: Path):
        state = {
            "current_idx": self._track_idx,
            "tracks": [
                {"path": t.path, "artist": t.artist, "title": t.title,
                 "duration_ms": t.duration_ms, "row": t.row}
                for t in self._queue
            ],
        }
        try:
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def restore_queue_state(self, path: Path):
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return

        saved_tracks = state.get("tracks", [])
        saved_idx    = state.get("current_idx", -1)
        current_path = (
            saved_tracks[saved_idx]["path"]
            if 0 <= saved_idx < len(saved_tracks) else None
        )

        for t in saved_tracks:
            p = t.get("path", "")
            if Path(p).is_file():
                self._queue.append(QueueTrack(
                    row=t.get("row", {}),
                    path=p,
                    artist=t.get("artist", ""),
                    title=t.get("title", ""),
                    duration_ms=t.get("duration_ms", 0),
                ))

        if not self._queue:
            return

        self._track_idx = 0
        if current_path:
            for i, t in enumerate(self._queue):
                if t.path == current_path:
                    self._track_idx = i
                    break

        track = self._queue[self._track_idx]
        self._player.setSource(QUrl.fromLocalFile(track.path))
        self.track_changed.emit(track.row, track.path, self._track_idx, len(self._queue))
        if track.title:
            self.metadata_changed.emit(track.artist, track.title)
        self.queue_changed.emit()
