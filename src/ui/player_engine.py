from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile
from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaMetaData, QMediaPlayer

_AUDIO_EXTENSIONS = {
    ".flac", ".mp3", ".wav", ".aiff", ".aif", ".m4a", ".alac",
    ".ogg", ".opus", ".ape", ".wv", ".wma", ".aac", ".dsf", ".dff",
}


def _audio_paths(folder_path: str) -> list[str]:
    folder = Path(folder_path)
    if not folder.is_dir():
        return []
    return sorted(
        str(f) for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in _AUDIO_EXTENSIONS
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

    # ── Read-only state ───────────────────────────────────────────────────

    @property
    def queue(self) -> list[QueueTrack]:
        return list(self._queue)

    @property
    def current_track_idx(self) -> int:
        return self._track_idx

    def is_playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def volume(self) -> float:
        return self._audio.volume()

    # ── Playback control ──────────────────────────────────────────────────

    def play_release(self, row: dict):
        """Replace queue with all tracks from release and start playback."""
        self._queue.clear()
        self._track_idx = -1
        for p in _audio_paths(row["folder_path"]):
            artist, title, duration_ms = _read_track_tags(p)
            self._queue.append(QueueTrack(row=row, path=p, artist=artist, title=title, duration_ms=duration_ms))
        self.queue_changed.emit()
        if self._queue:
            self._play_at(0)

    def enqueue_release(self, row: dict):
        """Append all tracks from release; start playback if currently idle."""
        for p in _audio_paths(row["folder_path"]):
            artist, title, duration_ms = _read_track_tags(p)
            self._queue.append(QueueTrack(row=row, path=p, artist=artist, title=title, duration_ms=duration_ms))
        self.queue_changed.emit()
        if self._track_idx < 0 and self._queue:
            self._play_at(0)

    def play_track_at(self, idx: int):
        self._play_at(idx)

    def remove_track(self, idx: int):
        if not (0 <= idx < len(self._queue)):
            return
        self._queue.pop(idx)
        if idx < self._track_idx:
            self._track_idx -= 1
        elif idx == self._track_idx:
            self._player.stop()
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
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
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
        if not self._queue or self._track_idx < 0:
            return
        if self._track_idx + 1 < len(self._queue):
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
        if title:
            self.metadata_changed.emit(artist, title)
