import json
import math
import random
from dataclasses import dataclass
from pathlib import Path

from mutagen import File as MutagenFile
from mutagen.flac import FLAC
from mutagen.monkeysaudio import MonkeysAudio
from mutagen.mp3 import MP3
from mutagen.wave import WAVE
from mutagen.wavpack import WavPack
from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaDevices, QMediaMetaData, QMediaPlayer

from src.utils.audio import AUDIO_EXTENSIONS
from src.utils.cue import CueTrack, find_cue_for_file, find_cue_for_folder, parse_cue
from src.utils.normalizer import VolumeNormalizer


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


def _duration_from_file(path: str) -> int:
    """Total duration of an audio file in ms.

    MutagenFile() can return an object with info=None for certain FLAC
    files (e.g. when STREAMINFO is read in an unexpected order). Falls
    back to format-specific classes so we always get a real duration.
    """
    # Mutagen audio objects act as tag dicts, so bool(af) is False when
    # the file has no tags — use "is not None" to avoid skipping info.
    for easy in (False, True):
        try:
            af = MutagenFile(path, easy=easy)
            if af is not None and af.info is not None:
                ms = math.ceil(getattr(af.info, "length", 0) * 1000)
                if ms > 0:
                    return ms
        except Exception:
            pass
    # Format-specific fallbacks
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


def _cue_queue_tracks(
    cue_tracks: list[CueTrack],
    audio_file: str,
    album_artist: str,
    release_row: dict,
    is_library: bool,
) -> list["QueueTrack"]:
    """Convert parsed CueTrack list to QueueTrack list."""
    total_ms = _duration_from_file(audio_file)
    result = []
    for t in cue_tracks:
        dur = t.end_ms - t.start_ms if t.end_ms else max(0, total_ms - t.start_ms)
        result.append(QueueTrack(
            row=release_row,
            path=audio_file,
            artist=t.artist or album_artist,
            title=t.title,
            duration_ms=dur,
            is_library=is_library,
            start_ms=t.start_ms,
            end_ms=t.end_ms,
        ))
    return result


def _queue_entries_for_folder(folder_path: str, release_row: dict | None) -> list["QueueTrack"]:
    """Return QueueTrack list for a folder, expanding a CUE sheet if present."""
    folder = Path(folder_path)
    cue_path = find_cue_for_folder(folder)
    if cue_path:
        audio_file, album_artist, album_title, cue_tracks = parse_cue(cue_path)
        if audio_file and cue_tracks:
            row = release_row or {
                "folder_path": folder_path,
                "title":          album_title,
                "artist":         album_artist,
                "catalog_number": "",
            }
            return _cue_queue_tracks(
                cue_tracks, str(audio_file), album_artist,
                row, is_library=release_row is not None,
            )

    # No CUE — regular audio files
    is_library = release_row is not None
    bare = not (release_row or {}).get("title")
    result = []
    for p in _audio_paths(folder_path):
        track_row, artist, title, duration_ms = _row_for_path(p, None if bare else release_row)
        result.append(QueueTrack(
            row=track_row, path=p, artist=artist, title=title,
            duration_ms=duration_ms, is_library=is_library,
        ))
    return result


def _cue_entries_for_single_file(path: str) -> list["QueueTrack"] | None:
    """If a lone audio file has a matching CUE, return expanded entries; else None."""
    cue_path = find_cue_for_file(Path(path))
    if not cue_path:
        return None
    audio_file, album_artist, album_title, cue_tracks = parse_cue(cue_path)
    if not audio_file or not cue_tracks:
        return None
    row = {
        "folder_path": str(Path(path).parent),
        "title":          album_title,
        "artist":         album_artist,
        "catalog_number": "",
    }
    return _cue_queue_tracks(cue_tracks, str(audio_file), album_artist, row, is_library=False)


@dataclass
class QueueTrack:
    row: dict
    path: str
    artist: str
    title: str
    duration_ms: int = 0
    is_library: bool = False
    start_ms: int = 0  # non-zero for CUE virtual tracks
    end_ms:   int = 0  # 0 = play to end of file


class PlayerEngine(QObject):
    # row, file_path, track_idx (0-based in flat queue), total
    track_changed    = Signal(dict, str, int, int)
    metadata_changed = Signal(str, str)   # artist, title (from file tags)
    state_changed    = Signal(bool)       # True = playing
    position_changed = Signal(int)        # ms
    duration_changed = Signal(int)        # ms
    queue_changed    = Signal()
    _norm_ready      = Signal(str, float) # path, gain_linear — internal use only

    def __init__(self, parent=None):
        super().__init__(parent)
        self._queue: list[QueueTrack] = []
        self._track_idx = -1
        self._track_removed = False  # current track was removed while playing; don't auto-advance after it ends
        self._shuffle_mode = False
        self._normalize    = False
        self._user_volume  = 0.7   # mirrors the initial setVolume call below
        self._norm_gain    = 1.0   # linear multiplier from normalizer
        self._normalizer   = VolumeNormalizer()
        self._norm_ready.connect(self._on_norm_ready)

        self._current_source: str = ""
        self._pending_seek:   int  = -1    # seek to apply after LoadedMedia
        self._pending_play:   bool = False  # whether to play after applying pending seek
        self._cue_advancing:  bool = False

        self._player = QMediaPlayer(self)
        self._audio  = QAudioOutput(self)
        self._audio.setVolume(0.7)
        self._player.setAudioOutput(self._audio)

        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.playbackStateChanged.connect(self._on_state_changed)
        self._player.metaDataChanged.connect(self._on_metadata_changed)
        self._player.positionChanged.connect(self._on_position_changed)
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
        self._queue.extend(_queue_entries_for_folder(row["folder_path"], None if bare else row))
        self.queue_changed.emit()
        if self._queue:
            self._play_at(0)

    def enqueue_release(self, row: dict):
        """Append all tracks from release; start playback if currently idle."""
        bare = not row.get("title")
        self._queue.extend(_queue_entries_for_folder(row["folder_path"], None if bare else row))
        self.queue_changed.emit()
        if self._track_idx < 0 and self._queue and self._is_stopped():
            self._play_at(0)

    def play_tracks(self, paths: list[str], release_row: dict | None = None):
        """Replace queue with given paths and start playback."""
        self._queue.clear()
        self._track_idx = -1
        if release_row is None and len(paths) == 1:
            expanded = _cue_entries_for_single_file(paths[0])
            if expanded:
                self._queue.extend(expanded)
                self.queue_changed.emit()
                self._play_at(0)
                return
        is_library = release_row is not None
        for p in paths:
            row, artist, title, duration_ms = _row_for_path(p, release_row)
            self._queue.append(QueueTrack(row=row, path=p, artist=artist, title=title, duration_ms=duration_ms, is_library=is_library))
        self.queue_changed.emit()
        if self._queue:
            self._play_at(0)

    def enqueue_tracks(self, paths: list[str], release_row: dict | None = None):
        """Append specific audio files to the queue; start playback if idle."""
        if release_row is None and len(paths) == 1:
            expanded = _cue_entries_for_single_file(paths[0])
            if expanded:
                self._queue.extend(expanded)
                self.queue_changed.emit()
                if self._track_idx < 0 and self._is_stopped():
                    self._play_at(0)
                return
        added = False
        is_library = release_row is not None
        for p in paths:
            row, artist, title, duration_ms = _row_for_path(p, release_row)
            self._queue.append(QueueTrack(row=row, path=p, artist=artist, title=title, duration_ms=duration_ms, is_library=is_library))
            added = True
        if added:
            self.queue_changed.emit()
            if self._track_idx < 0 and self._is_stopped():
                self._play_at(0)

    def clear_queue(self):
        is_active = self._player.playbackState() != QMediaPlayer.PlaybackState.StoppedState
        self._queue.clear()
        self._track_idx = -1
        self._current_source = ""
        self._pending_seek   = -1
        self._cue_advancing  = False
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
            self._track_removed = True
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

    def set_shuffle(self, enabled: bool):
        self._shuffle_mode = enabled

    def play_pause(self):
        state = self._player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        elif self._pending_seek >= 0:
            # Media is still loading — request play for after the pending seek lands
            self._pending_play = True
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
        self._audio.setVolume(0.0)
        self._player.setPosition(ms)
        QTimer.singleShot(80, self._apply_volume)

    def set_volume(self, v: float):
        self._user_volume = max(0.0, min(1.0, v))
        self._apply_volume()

    def set_normalize(self, enabled: bool):
        self._normalize = enabled
        if not enabled:
            self._norm_gain = 1.0
        self._apply_volume()
        # Re-analyse current track when toggling on
        if enabled and 0 <= self._track_idx < len(self._queue):
            self._trigger_normalization(self._queue[self._track_idx].path)

    def _apply_volume(self):
        effective = self._user_volume * self._norm_gain if self._normalize else self._user_volume
        self._audio.setVolume(max(0.0, min(1.0, effective)))

    def _trigger_normalization(self, path: str):
        cached = self._normalizer.gain_linear_cached(path)
        if cached is not None:
            self._norm_gain = cached
            self._apply_volume()
        else:
            self._normalizer.analyze_async(
                path,
                lambda p, g: self._norm_ready.emit(p, g),
            )

    def _on_norm_ready(self, path: str, gain: float):
        if (self._normalize
                and 0 <= self._track_idx < len(self._queue)
                and self._queue[self._track_idx].path == path):
            self._norm_gain = gain
            self._apply_volume()

    # ── Internal ──────────────────────────────────────────────────────────

    def _play_at(self, idx: int):
        if not (0 <= idx < len(self._queue)):
            return
        was_cue_advancing = self._cue_advancing
        self._track_removed = False
        self._cue_advancing  = False
        self._track_idx = idx
        track = self._queue[idx]
        self._norm_gain = 1.0
        self._apply_volume()
        if self._normalize:
            self._trigger_normalization(track.path)

        if track.path != self._current_source:
            self._current_source = track.path
            if track.start_ms > 0:
                # Delay play until LoadedMedia so we can seek before audio starts
                self._pending_seek = track.start_ms
                self._pending_play = True
                self._player.setSource(QUrl.fromLocalFile(track.path))
            else:
                self._pending_seek = -1
                self._pending_play = False
                self._player.setSource(QUrl.fromLocalFile(track.path))
                self._player.play()
        elif was_cue_advancing:
            # Natural CUE progression: end_ms[N] == start_ms[N+1], so the file is
            # already playing at the right position. Seeking would cause a click —
            # just update the track state and let the audio continue uninterrupted.
            pass
        else:
            # User-initiated jump within the same file — mute briefly to mask click.
            self._pending_seek = -1
            self._pending_play = False
            self._audio.setVolume(0.0)
            self._player.setPosition(track.start_ms)
            if not self.is_playing():
                self._player.play()
            QTimer.singleShot(80, self._apply_volume)

        self.track_changed.emit(track.row, track.path, idx, len(self._queue))
        if track.title:
            self.metadata_changed.emit(track.artist, track.title)

    def _advance(self):
        if not self._queue:
            return
        if self._track_idx < 0:
            if self._track_removed:
                self._track_removed = False
                self._player.stop()
                return
            self._play_at(0)
        elif self._shuffle_mode:
            candidates = [i for i in range(len(self._queue)) if i != self._track_idx]
            if candidates:
                self._play_at(random.choice(candidates))
            else:
                self._player.stop()
        elif self._track_idx + 1 < len(self._queue):
            self._play_at(self._track_idx + 1)
        else:
            self._player.stop()

    def _on_media_status(self, status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            if self._pending_seek >= 0:
                self._player.setPosition(self._pending_seek)
                self._pending_seek = -1
                if self._pending_play:
                    self._pending_play = False
                    self._player.play()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._advance()

    def _on_position_changed(self, ms: int):
        self.position_changed.emit(ms)
        if self._cue_advancing:
            return
        if 0 <= self._track_idx < len(self._queue):
            track = self._queue[self._track_idx]
            if track.end_ms > 0 and ms >= track.end_ms:
                self._cue_advancing = True
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
                 "duration_ms": t.duration_ms, "row": t.row, "is_library": t.is_library,
                 "start_ms": t.start_ms, "end_ms": t.end_ms}
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

        saved_tracks  = state.get("tracks", [])
        saved_idx     = state.get("current_idx", -1)
        current_path  = (
            saved_tracks[saved_idx]["path"]
            if 0 <= saved_idx < len(saved_tracks) else None
        )
        current_start = (
            saved_tracks[saved_idx].get("start_ms", 0)
            if 0 <= saved_idx < len(saved_tracks) else 0
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
                    is_library=t.get("is_library", False),
                    start_ms=t.get("start_ms", 0),
                    end_ms=t.get("end_ms", 0),
                ))

        if not self._queue:
            return

        self._track_idx = 0
        if current_path:
            # Match by path + start_ms so CUE tracks (same file, different offset) are identified correctly.
            # Fall back to path-only if no start_ms match (e.g. after format migration).
            for i, t in enumerate(self._queue):
                if t.path == current_path and t.start_ms == current_start:
                    self._track_idx = i
                    break
            else:
                for i, t in enumerate(self._queue):
                    if t.path == current_path:
                        self._track_idx = i
                        break

        track = self._queue[self._track_idx]
        # Mark the source so _play_at takes the same-file seek path instead of
        # calling setSource() again (which would interrupt the in-progress load
        # and make the first double-click after startup fail to play).
        self._current_source = track.path
        if track.start_ms > 0:
            # Seek to the CUE track's start once the file is loaded.
            # _pending_play stays False — restore does not auto-play.
            self._pending_seek = track.start_ms
        self._player.setSource(QUrl.fromLocalFile(track.path))
        self.track_changed.emit(track.row, track.path, self._track_idx, len(self._queue))
        if track.title:
            self.metadata_changed.emit(track.artist, track.title)
        self.queue_changed.emit()
