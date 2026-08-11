"""Shared audio-file constants used across scanner, player, and UI."""

AUDIO_EXTENSIONS: frozenset[str] = frozenset({
    ".flac", ".mp3", ".wav", ".aiff", ".aif", ".m4a", ".alac",
    ".ogg", ".opus", ".ape", ".wv", ".wma", ".aac", ".dsf", ".dff",
})
