import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Signal

from src._version import __version__
from src.utils.logger import get_logger

log = get_logger()

_RELEASES_API = "https://api.github.com/repos/vitaliysmirnov/music-librarian/releases/latest"
_TIMEOUT = 8


def _fetch_latest() -> dict | None:
    if __version__ == "dev":
        return None  # no update checks for dev builds

    try:
        req = urllib.request.Request(
            _RELEASES_API,
            headers={"User-Agent": "MusicLibrarian-updater"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            data = json.loads(r.read())
        latest_tag = data["tag_name"]          # e.g. "v0.0.5"
        current_tag = f"v{__version__}"        # e.g. "v0.0.4"
        if latest_tag != current_tag:
            return {
                "version": latest_tag.lstrip("v"),
                "tag":     latest_tag,
                "notes":   (data.get("body") or "").strip(),
                "url":     data["html_url"],
            }
    except Exception as e:
        log.debug("Update check failed: %s", e)
    return None


class UpdateChecker(QObject):
    """Checks for updates in a background thread and emits update_available."""
    update_available = Signal(dict)  # payload: {version, tag, notes, url}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="updater")

    def check(self):
        """Fire-and-forget: result delivered via update_available signal."""
        future = self._pool.submit(_fetch_latest)
        future.add_done_callback(self._on_done)

    def _on_done(self, future):
        try:
            result = future.result()
        except Exception:
            return
        if result:
            self.update_available.emit(result)
