"""Shared utility functions."""

import os
import platform
import subprocess
from pathlib import Path


def fmt_ms(ms: int) -> str:
    """Format milliseconds as M:SS or H:MM:SS."""
    s = max(0, ms) // 1000
    m, sec = divmod(s, 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def open_path(path: str | Path) -> None:
    """Reveal path in the system file manager (Finder / Explorer / Nautilus)."""
    p = str(path)
    if platform.system() == "Darwin":
        subprocess.Popen(["open", p])
    elif platform.system() == "Windows":
        os.startfile(p)
    else:
        subprocess.Popen(["xdg-open", p])
