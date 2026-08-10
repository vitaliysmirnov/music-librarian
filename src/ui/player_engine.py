from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

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


@dataclass
class QueueRelease:
    row: dict
    audio_paths: list[str]


class PlayerEngine(QObject):
    # row, file_path, track_idx (0-based), total_tracks_in_release
    track_changed    = Signal(dict, str, int, int)
    state_changed    = Signal(bool)   # True = playing
    position_changed = Signal(int)    # ms
    duration_changed = Signal(int)    # ms
    queue_changed    = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: list[QueueRelease] = []
        self._release_idx = -1
        self._track_idx   = -1

        self._player = QMediaPlayer(self)
        self._audio  = QAudioOutput(self)
        self._audio.setVolume(0.7)
        self._player.setAudioOutput(self._audio)

        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.playbackStateChanged.connect(self._on_state_changed)
        self._player.positionChanged.connect(lambda ms: self.position_changed.emit(int(ms)))
        self._player.durationChanged.connect(lambda ms: self.duration_changed.emit(int(ms)))

    # ── Read-only state ───────────────────────────────────────────────────

    @property
    def queue(self) -> list[QueueRelease]:
        return list(self._queue)

    @property
    def current_release_idx(self) -> int:
        return self._release_idx

    @property
    def current_track_idx(self) -> int:
        return self._track_idx

    def is_playing(self) -> bool:
        return self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def volume(self) -> float:
        return self._audio.volume()

    # ── Playback control ──────────────────────────────────────────────────

    def play_release(self, row: dict):
        """Replace queue with this release and start playback immediately."""
        self._queue.clear()
        self._release_idx = -1
        self._track_idx   = -1
        paths = _audio_paths(row["folder_path"])
        if not paths:
            return
        self._queue.append(QueueRelease(row=row, audio_paths=paths))
        self.queue_changed.emit()
        self._play_at(0, 0)

    def enqueue_release(self, row: dict):
        """Append release to queue; start playback if currently idle."""
        paths = _audio_paths(row["folder_path"])
        if not paths:
            return
        self._queue.append(QueueRelease(row=row, audio_paths=paths))
        self.queue_changed.emit()
        if self._release_idx < 0:
            self._play_at(0, 0)

    def remove_from_queue(self, release_idx: int):
        if not (0 <= release_idx < len(self._queue)):
            return
        self._queue.pop(release_idx)
        if release_idx < self._release_idx:
            self._release_idx -= 1
        elif release_idx == self._release_idx:
            self._player.stop()
            self._release_idx = -1
            self._track_idx   = -1
        self.queue_changed.emit()

    def play_pause(self):
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        elif self._release_idx >= 0:
            self._player.play()
        elif self._queue:
            self._play_at(0, 0)

    def prev(self):
        if self._track_idx > 0:
            self._play_at(self._release_idx, self._track_idx - 1)
        elif self._release_idx > 0:
            prev_rel = self._queue[self._release_idx - 1]
            self._play_at(self._release_idx - 1, len(prev_rel.audio_paths) - 1)

    def next(self):
        self._advance()

    def seek(self, ms: int):
        self._player.setPosition(ms)

    def set_volume(self, v: float):
        self._audio.setVolume(max(0.0, min(1.0, v)))

    # ── Internal ──────────────────────────────────────────────────────────

    def _play_at(self, rel_idx: int, track_idx: int):
        if not (0 <= rel_idx < len(self._queue)):
            return
        rel = self._queue[rel_idx]
        if not (0 <= track_idx < len(rel.audio_paths)):
            return
        self._release_idx = rel_idx
        self._track_idx   = track_idx
        self._player.setSource(QUrl.fromLocalFile(rel.audio_paths[track_idx]))
        self._player.play()
        self.track_changed.emit(
            rel.row, rel.audio_paths[track_idx], track_idx, len(rel.audio_paths)
        )

    def _advance(self):
        if not self._queue or self._release_idx < 0:
            return
        rel = self._queue[self._release_idx]
        if self._track_idx + 1 < len(rel.audio_paths):
            self._play_at(self._release_idx, self._track_idx + 1)
        elif self._release_idx + 1 < len(self._queue):
            self._play_at(self._release_idx + 1, 0)
        else:
            self._player.stop()

    def _on_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._advance()

    def _on_state_changed(self, state):
        self.state_changed.emit(state == QMediaPlayer.PlaybackState.PlayingState)
