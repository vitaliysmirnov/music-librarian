"""Volume normalizer: reads ReplayGain tags or measures LUFS via ffmpeg."""
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

from mutagen import File as MutagenFile

_TARGET_LUFS = -14.0   # reference: Spotify / Apple Music
_MAX_GAIN_DB =  12.0   # cap to avoid excessive amplification of very quiet tracks
_MIN_GAIN_DB = -12.0


def _replaygain_from_tags(path: str) -> float | None:
    """Read REPLAYGAIN_TRACK_GAIN from file tags. Returns dB value or None."""
    try:
        audio = MutagenFile(path)
        if not audio or not audio.tags:
            return None
        tags = audio.tags
        # ID3 (MP3): stored as TXXX frame; Vorbis/FLAC: plain key
        for key in ("REPLAYGAIN_TRACK_GAIN", "replaygain_track_gain",
                    "TXXX:REPLAYGAIN_TRACK_GAIN"):
            val = tags.get(key)
            if val is None:
                continue
            text = str(val[0] if hasattr(val, "__iter__") and not isinstance(val, str) else val)
            m = re.search(r"[-+]?\d+\.?\d*", text)
            if m:
                return float(m.group())
    except Exception:
        pass
    return None


def _measure_lufs_ffmpeg(path: str) -> float | None:
    """Measure integrated loudness via ffmpeg ebur128. Returns LUFS or None."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return None
    try:
        r = subprocess.run(
            [ffmpeg, "-nostats", "-i", path,
             "-af", "ebur128=framelog=quiet",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=120,
        )
        # ebur128 summary is on stderr; grab the integrated loudness line
        m = re.search(r"I:\s+([-+]?\d+\.?\d*)\s+LUFS", r.stderr)
        if m:
            val = float(m.group(1))
            return None if val <= -70 else val
    except Exception:
        pass
    return None


def _gain_db(path: str) -> float:
    """Return gain in dB needed to reach target loudness (0.0 = no adjustment)."""
    # 1. Try tags first — instant
    rg = _replaygain_from_tags(path)
    if rg is not None:
        # ReplayGain gain is already relative to its own reference level (~-18 LUFS).
        # Adjust to our target (-14 LUFS) by adding the difference.
        adjusted = rg + (_TARGET_LUFS - (-18.0))
        return max(_MIN_GAIN_DB, min(_MAX_GAIN_DB, adjusted))

    # 2. Measure with ffmpeg
    lufs = _measure_lufs_ffmpeg(path)
    if lufs is not None:
        return max(_MIN_GAIN_DB, min(_MAX_GAIN_DB, _TARGET_LUFS - lufs))

    return 0.0


def db_to_linear(db: float) -> float:
    return 10 ** (db / 20)


class VolumeNormalizer:
    def __init__(self):
        self._cache: dict[str, float] = {}   # path → gain_db
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="normalizer")
        self._alive = True

    def gain_linear_cached(self, path: str) -> float | None:
        """Return cached gain as a linear multiplier, or None if not yet analysed."""
        if path in self._cache:
            return db_to_linear(self._cache[path])
        return None

    def shutdown(self) -> None:
        """Signal the pool to stop and wait for any running analysis to finish.

        Must be called before the owning QObject's C++ counterpart is destroyed,
        so the on_done callback is never invoked on a dead object.
        """
        self._alive = False
        self._pool.shutdown(wait=True)

    def analyze_async(self, path: str, on_done) -> None:
        """Analyse *path* in background. Calls on_done(path, gain_linear) when ready."""
        if not self._alive:
            return
        def _run():
            if not self._alive:
                return
            if path not in self._cache:
                self._cache[path] = _gain_db(path)
            if self._alive:
                on_done(path, db_to_linear(self._cache[path]))
        self._pool.submit(_run)
