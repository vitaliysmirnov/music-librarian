import platform
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QEvent, QObject, Signal, QRectF, QPointF
from PySide6.QtGui import QIcon, QAction, QKeySequence, QPixmap, QPainter, QColor, QPen, QBrush
from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QStatusBar, QVBoxLayout,
    QLabel, QPushButton, QWidget,
    QSystemTrayIcon, QMenu, QApplication, QMessageBox,
)

from src._version import __version__
from src.database.db import Database
from src.scanner.scanner import scan_all, scan_source
from src.ui.player_bar import PlayerBar
from src.ui.player_engine import PlayerEngine
from src.ui.queue_panel import QueuePanel
from src.ui.releases_tab import ReleasesTab
from src.ui.settings_tab import SettingsTab, MODE_AUTO
from src.ui.sources_tab import SourcesTab
from src.updater import UpdateChecker
from src.utils.drive_monitor import DriveMonitor
from src.utils.logger import QtLogHandler, get_logger
from src.watcher.watcher import LibraryWatcher

log = get_logger()

_DRIVE_POLL_INTERVAL_MS = 20_000


def _set_tray_tooltip(text: str) -> None:
    try:
        from AppKit import NSStatusBar
        ptr_array = NSStatusBar.systemStatusBar().valueForKey_("_statusItems")
        if ptr_array is None:
            return
        for item in ptr_array.allObjects():
            try:
                btn = item.button()
                if btn is not None:
                    btn.setToolTip_(text)
            except Exception:
                pass
    except Exception:
        pass


def _apply_tray_template() -> None:
    """Set NSImage.template=YES on our status-bar icon.

    macOS colourises template images automatically — white on dark bars, black
    on light bars.  Qt (even 6.11) does not set this flag, so we do it via
    AppKit after the NSStatusItem has been created.

    NSStatusBar stores items in an NSConcretePointerArray; allObjects() is the
    safe way to iterate it without touching raw C pointers.
    """
    try:
        from AppKit import NSStatusBar, NSMakeSize  # pyobjc-framework-Cocoa
        bar = NSStatusBar.systemStatusBar()
        ptr_array = bar.valueForKey_("_statusItems")
        if ptr_array is None:
            return
        items = ptr_array.allObjects()
        for item in items:
            try:
                btn = item.button()
                if btn is None:
                    continue
                img = btn.image()
                if img is None:
                    continue
                img.setSize_(NSMakeSize(30, 30))
                if not img.isTemplate():
                    img.setTemplate_(True)
            except Exception:
                pass
    except Exception:
        pass


class _FsChangeSignal(QObject):
    """Bridges the watchdog thread to the Qt main thread."""
    triggered = Signal()


def _set_dock_visible(visible: bool) -> None:
    """Show or hide the macOS Dock icon. No-op on other platforms."""
    if platform.system() != "Darwin":
        return
    try:
        from AppKit import (
            NSApp,
            NSApplicationActivationPolicyRegular,
            NSApplicationActivationPolicyAccessory,
        )
        policy = (NSApplicationActivationPolicyRegular if visible
                  else NSApplicationActivationPolicyAccessory)
        NSApp.setActivationPolicy_(policy)
    except Exception:
        pass


class MainWindow(QMainWindow):
    def __init__(self, db: Database, qt_log_handler: QtLogHandler | None = None,
                 data_dir: Path | None = None):
        super().__init__()
        self._db = db
        self._qt_log_handler = qt_log_handler
        self._data_dir = data_dir
        self._watcher: LibraryWatcher | None = None

        self._fs_signal = _FsChangeSignal()
        self._fs_signal.triggered.connect(self._on_fs_change)

        self._watcher_poll_timer = QTimer(self)
        self._watcher_poll_timer.setInterval(500)
        self._watcher_poll_timer.timeout.connect(self._poll_watcher)

        self._auto_timer = QTimer(self)
        self._auto_timer.timeout.connect(self._auto_scan)

        self._drive_timer = QTimer(self)
        self._drive_timer.setInterval(_DRIVE_POLL_INTERVAL_MS)
        self._drive_timer.timeout.connect(self._check_drives)

        self._drive_monitor = DriveMonitor(
            on_mount=self._on_drive_mounted,
            on_unmount=self._on_drive_unmounted,
        )

        self._setup_ui()
        self._setup_menu()
        self._setup_tray()
        self._apply_settings()
        self._check_drives()
        self._drive_timer.start()
        self._drive_monitor.start()

        if self._data_dir:
            self._player_engine.restore_queue_state(self._data_dir / "queue_state.json")
            for pl in self._db.get_playlists():
                self._player_bar.on_playlist_renamed(pl["id"], pl["name"])

        self._player_engine.set_normalize(
            self._db.get_setting("normalize_volume", "0") == "1"
        )

        log.info("Application version: %s", "DEV" if __version__ == "dev" else f"v{__version__}")

        self._updater = UpdateChecker(self)
        self._updater.update_available.connect(self._on_update_available)
        QTimer.singleShot(3000, self._updater.check)

    # ── UI ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setWindowTitle("Music Librarian")
        self.resize(1100, 720)

        # ── Player engine (no UI) ─────────────────────────────────────────
        self._player_engine = PlayerEngine(self)

        # ── Central widget: player bar on top, tabs below ─────────────────
        container = QWidget(self)
        vbox = QVBoxLayout(container)
        vbox.setContentsMargins(0, 0, 0, 0)
        vbox.setSpacing(0)

        self._player_bar = PlayerBar(self._player_engine, container)
        vbox.addWidget(self._player_bar)

        self._tabs = QTabWidget()
        vbox.addWidget(self._tabs)

        self.setCentralWidget(container)

        # ── Queue panel (floating child of container) ─────────────────────
        self._queue_panel = QueuePanel(self._player_engine, container)
        self._player_bar.queue_toggled.connect(self._toggle_queue)

        # ── Tabs ──────────────────────────────────────────────────────────
        self._releases_tab = ReleasesTab(self._db)
        self._sources_tab = SourcesTab(self._db)
        self._settings_tab = SettingsTab(self._db, self._qt_log_handler)

        self._tabs.addTab(self._releases_tab, "Releases")
        self._tabs.addTab(self._sources_tab, "Sources")
        self._tabs.addTab(self._settings_tab, "Settings")

        self._sources_tab.sources_changed.connect(self._on_sources_changed)
        self._settings_tab.settings_changed.connect(self._apply_settings)
        self._settings_tab.mask_changed.connect(self._on_mask_changed)
        self._settings_tab.normalize_changed.connect(self._player_engine.set_normalize)
        self._tabs.currentChanged.connect(self._on_tab_changed)
        self._releases_tab.release_trashed.connect(self._update_info_label)
        self._releases_tab.release_trashed.connect(self._releases_tab.refresh_liked)
        self._releases_tab.play_requested.connect(self._player_engine.play_release)
        self._releases_tab.enqueue_requested.connect(self._player_engine.enqueue_release)
        self._releases_tab.play_track_requested.connect(self._player_engine.play_tracks)
        self._releases_tab.enqueue_track_requested.connect(self._player_engine.enqueue_tracks)
        self._releases_tab.liked_changed.connect(self._on_liked_changed)
        self._releases_tab.go_to_release.connect(lambda _: self._tabs.setCurrentWidget(self._releases_tab))
        self._player_bar.navigate_requested.connect(self._on_navigate_requested)
        self._player_bar.like_toggled.connect(self._on_like_toggled)
        self._player_bar.go_to_release_requested.connect(self._on_player_go_to_release)
        self._player_bar.add_to_playlist_requested.connect(self._on_player_add_to_playlist)
        self._releases_tab.playlists_changed.connect(self._player_bar.set_playlists)
        self._releases_tab.playlist_renamed.connect(self._player_bar.on_playlist_renamed)
        self._releases_tab.playlists_changed.connect(self._queue_panel.set_playlists)
        self._queue_panel.go_to_release.connect(self._on_player_go_to_release)
        self._queue_panel.add_to_playlist_requested.connect(self._on_queue_add_to_playlist)
        # initial sync — playlists_changed already fired during ReleasesTab construction
        initial_playlists = self._db.get_playlists()
        self._player_bar.set_playlists(initial_playlists)
        self._queue_panel.set_playlists(initial_playlists)
        self._player_engine.track_changed.connect(self._on_track_changed_liked)
        self._player_engine.metadata_changed.connect(self._on_tray_metadata)
        self._player_engine.state_changed.connect(self._on_tray_state)
        self._player_engine.track_not_found.connect(self._on_track_not_found)

        sb = QStatusBar()
        self.setStatusBar(sb)

        self._status_label = QLabel("")
        sb.addWidget(self._status_label)

        self._info_label = QLabel("")
        sb.addPermanentWidget(self._info_label)

        scan_btn = QPushButton("Scan Now")
        scan_btn.clicked.connect(self._manual_scan)
        sb.addPermanentWidget(scan_btn)

        self._refresh_all()

    def _on_liked_changed(self):
        self._releases_tab.refresh_liked()
        path = self._player_bar.current_path()
        if path:
            idx = self._player_engine.current_track_idx
            q   = self._player_engine.queue
            qt  = q[idx] if 0 <= idx < len(q) else None
            s_ms = qt.start_ms if qt and qt.path == path else 0
            self._player_bar.set_liked(self._db.is_track_liked(path, s_ms))

    def _on_like_toggled(self, path: str, row: dict, checked: bool):
        # Get start_ms/end_ms from the current QueueTrack (CUE virtual tracks).
        idx = self._player_engine.current_track_idx
        q   = self._player_engine.queue
        qt  = q[idx] if 0 <= idx < len(q) else None
        start_ms = qt.start_ms if qt and qt.path == path else 0
        end_ms   = qt.end_ms   if qt and qt.path == path else 0

        if checked:
            folder_path = (row.get("folder_path") or str(Path(path).parent))
            if start_ms:
                # CUE virtual track — use metadata from QueueTrack directly
                artist = qt.artist if qt else ""
                title  = qt.title  if qt else ""
                duration_ms = qt.duration_ms if qt else 0
                album_tag = (row or {}).get("title", "")
            else:
                from src.ui.player_engine import _read_full_tags
                artist, title, album_tag, duration_ms = _read_full_tags(path)
                if not album_tag:
                    release = self._db.get_release_by_path(str(Path(path).parent))
                    album_tag = dict(release).get("title", "") if release else ""
            self._db.like_track(path, artist, title, album_tag, folder_path, duration_ms,
                                start_ms, end_ms)
        else:
            self._db.unlike_track(path, start_ms)
        self._releases_tab.refresh_liked()

    def _on_track_changed_liked(self, row: dict, path: str, track_idx: int, total: int):
        q = self._player_engine.queue
        qt = q[track_idx] if 0 <= track_idx < len(q) else None
        start_ms = qt.start_ms if qt and qt.path == path else 0
        self._player_bar.set_liked(self._db.is_track_liked(path, start_ms))
        self._player_bar.set_is_library_track(qt.is_library if qt else False)

    def _on_tray_metadata(self, artist: str, title: str):
        tip = f"{artist} — {title}" if artist else title
        self._update_tray_tooltip(tip)

    def _on_tray_state(self, playing: bool):
        if not playing:
            self._update_tray_tooltip("Music Librarian")

    def _on_track_not_found(self, artist: str, title: str):
        label = f"«{artist} – {title}»" if artist else f"«{title}»"
        idx = self._player_engine.current_track_idx
        q   = self._player_engine.queue
        qt  = q[idx] if 0 <= idx < len(q) else None
        is_offline = False
        if qt:
            folder = (qt.row or {}).get("folder_path") or str(Path(qt.path).parent)
            rel = self._db.get_release_by_path(folder)
            if rel and not dict(rel).get("is_available", True):
                is_offline = True
        if is_offline:
            QMessageBox.information(
                self, "Source Disconnected",
                f"{label}\n\nThis track's source drive is currently disconnected.\n"
                "Reconnect it to play.",
            )
        else:
            QMessageBox.information(
                self, "Track Not Found",
                f"{label}\n\nThis track could not be found on disk.\n"
                "It may have been deleted.",
            )

    def _update_tray_tooltip(self, text: str):
        if sys.platform == "darwin":
            _set_tray_tooltip(text)
        else:
            self._tray.setToolTip(text)

    def _on_navigate_requested(self, kind: str, value: str):
        self._tabs.setCurrentWidget(self._releases_tab)
        self._releases_tab.navigate_to(kind, value)

    def _on_queue_add_to_playlist(self, track_idx: int, playlist_id: int):
        from PySide6.QtWidgets import QMessageBox
        queue = self._player_engine.queue
        if not (0 <= track_idx < len(queue)):
            return
        t = queue[track_idx]
        folder_path = (t.row or {}).get("folder_path") or str(Path(t.path).parent)
        album = (t.row or {}).get("title") or ""
        added = self._db.add_track_to_playlist(
            playlist_id, t.path, t.artist, t.title, album, folder_path, t.duration_ms,
            t.start_ms, t.end_ms,
        )
        if not added:
            label = f"«{t.artist} – {t.title}»" if t.artist else f"«{t.title}»"
            msg = f"{label} is already in this playlist.\n\nAdd it again?"
            reply = QMessageBox.question(
                self, "Already in Playlist", msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._db.add_track_to_playlist_again(
                    playlist_id, t.path, t.artist, t.title, album, folder_path, t.duration_ms,
                    t.start_ms, t.end_ms,
                )
        self._releases_tab.on_playlist_track_added(playlist_id)

    def _on_player_add_to_playlist(
        self,
        playlist_id: int, path: str, artist: str, title: str,
        album: str, folder_path: str, duration_ms: int,
        start_ms: int = 0, end_ms: int = 0,
    ):
        from PySide6.QtWidgets import QMessageBox
        added = self._db.add_track_to_playlist(
            playlist_id, path, artist, title, album, folder_path, duration_ms,
            start_ms, end_ms,
        )
        if not added:
            label = f"«{artist} – {title}»" if artist else f"«{title}»"
            msg = f"{label} is already in this playlist.\n\nAdd it again?"
            reply = QMessageBox.question(
                self, "Already in Playlist", msg,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self._db.add_track_to_playlist_again(
                    playlist_id, path, artist, title, album, folder_path, duration_ms,
                    start_ms, end_ms,
                )
        self._releases_tab.on_playlist_track_added(playlist_id)

    def _on_player_go_to_release(self, folder_path: str):
        if self._db.get_release_by_path(folder_path):
            self._show_window()
            self._releases_tab.navigate_to_release(folder_path)
        else:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder_path))

    def _toggle_queue(self):
        panel = self._queue_panel
        if panel.isVisible():
            panel.hide()
            self._player_bar.set_queue_checked(False)
            return
        self._reposition_queue_panel()
        panel.show()
        panel.raise_()
        self._player_bar.set_queue_checked(True)

    def _reposition_queue_panel(self):
        container = self.centralWidget()
        x = container.width() - self._queue_panel.width() - 4
        y = self._player_bar.height() + 4
        self._queue_panel.move(x, y)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "_queue_panel") and self._queue_panel.isVisible():
            self._reposition_queue_panel()

    def _setup_menu(self):
        """Native macOS menu bar with proper roles (About, Preferences, Quit).
        On other platforms these appear as a regular menu."""
        mb = self.menuBar()
        app_menu = mb.addMenu("Music Librarian")

        about_act = QAction("About Music Librarian", self)
        about_act.setMenuRole(QAction.MenuRole.AboutRole)
        about_act.triggered.connect(self._show_about)
        app_menu.addAction(about_act)

        app_menu.addSeparator()

        prefs_act = QAction("Preferences…", self)
        prefs_act.setMenuRole(QAction.MenuRole.PreferencesRole)
        prefs_act.setShortcut(QKeySequence("Ctrl+,"))
        prefs_act.triggered.connect(self._open_settings)
        app_menu.addAction(prefs_act)

        app_menu.addSeparator()

        quit_act = QAction("Quit Music Librarian", self)
        quit_act.setMenuRole(QAction.MenuRole.QuitRole)
        quit_act.setShortcut(QKeySequence.StandardKey.Quit)   # Cmd+Q / Ctrl+Q
        quit_act.triggered.connect(self.quit)
        app_menu.addAction(quit_act)

    def _show_about(self):
        QMessageBox.about(
            self,
            "About Music Librarian",
            f"<b>Music Librarian</b> v{__version__}<br><br>"
            "A personal music collection manager.<br>"
            "Scans folders, tracks releases, monitors changes.",
        )

    def _on_update_available(self, info: dict):
        import webbrowser
        version = info["version"]
        log.info("Update available: v%s", version)
        self._tray.showMessage(
            "Music Librarian update available",
            f"Version {version} is ready. Click to open the download page.",
            QSystemTrayIcon.MessageIcon.Information,
            8000,
        )
        self._update_info = info
        try:
            self._tray.messageClicked.disconnect(self._open_update_page)
        except RuntimeError:
            pass
        self._tray.messageClicked.connect(self._open_update_page)

    def _open_update_page(self):
        import webbrowser
        if hasattr(self, "_update_info"):
            webbrowser.open(self._update_info["url"])
            self._tray.messageClicked.disconnect(self._open_update_page)

    def _open_settings(self):
        self._show_window()
        self._tabs.setCurrentWidget(self._settings_tab)

    @staticmethod
    def _make_tray_icon() -> QIcon:
        """Draw vinyl+magnifier tray icon. Black on transparent = macOS template image."""
        # Render at the screen's device pixel ratio so the icon is crisp on Retina.
        app = QApplication.instance()
        dpr = app.devicePixelRatio() if app else 2.0
        phys = round(30 * dpr)
        pix = QPixmap(phys, phys)
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # White on transparent: visible on dark bars out of the box.
        # _apply_tray_template() also sets NSImage.template=YES so macOS
        # colourises the icon for light bars (renders black there).
        W  = QColor(255, 255, 255, 255)
        Wg = QColor(255, 255, 255, 110)  # semi-transparent for groove rings

        # Vinyl record (center-left)
        vx, vy, vr = 13.0, 17.0, 10.2
        p.setPen(QPen(W, 1.7))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(vx - vr, vy - vr, vr * 2, vr * 2))
        p.setPen(QPen(Wg, 1.1))
        for gr in (7.9, 5.4):
            p.drawEllipse(QRectF(vx - gr, vy - gr, gr * 2, gr * 2))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(W))
        p.drawEllipse(QRectF(vx - 3.0, vy - 3.0, 6.0, 6.0))

        # Magnifying glass (upper-right, overlays vinyl)
        mx, my, mr = 22.6, 8.2, 4.7
        p.setPen(QPen(W, 1.8))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QRectF(mx - mr, my - mr, mr * 2, mr * 2))
        hpen = QPen(W, 2.4)
        hpen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(hpen)
        p.drawLine(QPointF(mx + mr * 0.707, my + mr * 0.707), QPointF(28.3, 14.3))

        p.end()
        return QIcon(pix)

    def _setup_tray(self):
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.warning("System tray not available on this platform")
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(self._make_tray_icon())
        self._tray.setToolTip("Music Librarian")

        menu = QMenu()
        scan_action = QAction("Scan Now", self)
        scan_action.triggered.connect(self._manual_scan)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit)

        menu.addAction(scan_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

        # Qt does not set NSImage.template=YES automatically, so the icon stays
        # black on a dark menu bar instead of being colorised by macOS.
        # Apply the flag after a short delay to let Qt finish creating the NSStatusItem.
        if sys.platform == "darwin":
            QTimer.singleShot(300, _apply_tray_template)

    def _on_tab_changed(self, index: int):
        if self._tabs.widget(index) is not self._releases_tab:
            self._releases_tab.clear_releases_selection()
            self._releases_tab.collapse_releases()

    # ── Settings ──────────────────────────────────────────────────────────

    def _apply_settings(self):
        mode = self._db.get_setting("scan_mode", "manual")

        self._auto_timer.stop()

        if mode == MODE_AUTO:
            interval_min = int(self._db.get_setting("scan_interval_min", "60"))
            self._auto_timer.start(interval_min * 60 * 1000)
            if self._watcher is None:
                self._start_watcher()
        else:
            self._stop_watcher()

        self._update_info_label()

    # ── Watcher ───────────────────────────────────────────────────────────

    def _poll_watcher(self):
        if self._watcher:
            self._watcher.process_pending()

    def _start_watcher(self):
        self._watcher = LibraryWatcher(self._db, self._fs_signal.triggered.emit)
        self._watcher.start()
        self._watcher_poll_timer.start()

    def _stop_watcher(self):
        if self._watcher:
            self._watcher_poll_timer.stop()
            self._watcher.stop()
            self._watcher = None

    # ── Drive detection ───────────────────────────────────────────────────

    def _on_drive_mounted(self, mount_path: str):
        log.info("OS: drive mounted at %s", mount_path)
        triggered = False
        for source in self._db.get_sources():
            if Path(source["path"]).is_relative_to(Path(mount_path)) and not source["is_available"]:
                scan_source(self._db, source["id"], source["path"])
                if self._watcher:
                    self._watcher.refresh_watches()
                triggered = True
        if triggered:
            self._refresh_all()
            msg = "Drive connected, library updated"
            self._status_label.setText(msg)
            self._tray.showMessage("Music Librarian", msg, QSystemTrayIcon.Information, 4000)

    def _on_drive_unmounted(self, mount_path: str):
        log.info("OS: drive unmounted at %s", mount_path)
        triggered = False
        for source in self._db.get_sources():
            if Path(source["path"]).is_relative_to(Path(mount_path)) and source["is_available"]:
                self._db.update_source_availability(source["id"], False)
                self._db.set_releases_availability_by_source(source["id"], False)
                if self._watcher:
                    self._watcher.refresh_watches()
                triggered = True
        if triggered:
            self._refresh_all()
            msg = "Drive disconnected"
            self._status_label.setText(msg)
            self._tray.showMessage("Music Librarian", msg, QSystemTrayIcon.Warning, 4000)

    def _check_drives(self):
        newly_available, newly_gone = [], []
        for source in self._db.get_sources():
            path_exists = Path(source["path"]).exists()
            if not source["is_available"] and path_exists:
                newly_available.append(source)
            elif source["is_available"] and not path_exists:
                newly_gone.append(source)

        for source in newly_gone:
            src_path = Path(source["path"])
            if src_path.parent.exists():
                # Source directory itself was deleted — remove orphaned releases.
                log.info("Source directory deleted: %s", source["path"])
                self._db.update_source_availability(source["id"], False)
                for path in self._db.get_release_paths_for_source(source["id"]):
                    if not Path(path).exists():
                        self._db.delete_release_by_path(path)
                        log.info("Removed release (source deleted): %s", path)
                    else:
                        self._db.set_release_availability(path, False)
            else:
                # Parent also missing → drive likely offline.
                log.info("Drive gone offline: %s", source["path"])
                self._db.update_source_availability(source["id"], False)
                self._db.set_releases_availability_by_source(source["id"], False)
            if self._watcher:
                self._watcher.refresh_watches()

        for source in newly_available:
            log.info("Drive back online, scanning: %s", source["path"])
            scan_source(self._db, source["id"], source["path"])
            if self._watcher:
                self._watcher.refresh_watches()

        if newly_gone:
            self._refresh_all()
            names = ", ".join(Path(s["path"]).name for s in newly_gone)
            self._status_label.setText(f"Drive disconnected: {names}")
            self._tray.showMessage("Music Librarian", f"Drive disconnected: {names}", QSystemTrayIcon.Warning, 4000)

        if newly_available:
            self._refresh_all()
            names = ", ".join(Path(s["path"]).name for s in newly_available)
            self._status_label.setText(f"Drive connected, library updated: {names}")
            self._tray.showMessage("Music Librarian", f"Drive connected: {names}", QSystemTrayIcon.Information, 4000)

    # ── Scan ──────────────────────────────────────────────────────────────

    def _manual_scan(self):
        self._status_label.setText("Scanning…")
        a, u, r = scan_all(self._db)
        self._refresh_all()
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._status_label.setText(f"Scan {now} — added: {a}, updated: {u}, removed: {r}")
        if self._watcher:
            self._watcher.refresh_watches()

    def _auto_scan(self):
        log.info("Auto-scan triggered")
        self._manual_scan()

    def _on_mask_changed(self):
        count = self._db.count_releases()
        if count > 0:
            answer = QMessageBox.question(
                self,
                "Apply new mask",
                f"The library contains {count} release(s) indexed with the previous mask.\n"
                "They will be removed and the library will be re-scanned.\n\nContinue?",
                QMessageBox.Yes | QMessageBox.Cancel,
            )
            if answer != QMessageBox.Yes:
                return
        log.info("Mask changed — clearing releases and re-scanning")
        self._releases_tab.invalidate_header_state()
        self._db.clear_releases()
        self._stop_watcher()
        self._manual_scan()
        self._apply_settings()  # restart watcher with new pattern if auto mode

    # ── Refresh ───────────────────────────────────────────────────────────

    def _on_fs_change(self):
        self._releases_tab.refresh()
        self._update_info_label()

    def _on_sources_changed(self):
        self._sources_tab.refresh()
        if self._watcher:
            self._watcher.refresh_watches()
        self._refresh_all()

    def _refresh_all(self):
        self._db.cleanup_orphaned_tracks()
        self._releases_tab.refresh()
        self._sources_tab.refresh()
        self._update_info_label()

    def _update_info_label(self):
        sources = self._db.get_sources()
        available = sum(1 for s in sources if s["is_available"])
        total = len(sources)
        count = self._db.count_releases()
        mode = self._db.get_setting("scan_mode", "manual")
        mode_str = "automatic" if mode == MODE_AUTO else "manual"
        if mode == MODE_AUTO:
            interval = self._db.get_setting("scan_interval_min", "60")
            mode_str += f" · every {interval} min"
        self._info_label.setText(
            f"Monitoring: {mode_str}  |  Sources: {available}/{total}  |  Releases: {count}"
        )

    # ── Tray / window ─────────────────────────────────────────────────────


    def _on_tray_activated(self, reason):
        # Single click or double click both show the window
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self._show_window()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            if self.isMinimized():
                # Intercept minimize: hide to tray instead of going to Dock.
                QTimer.singleShot(0, self._intercept_minimize)
            elif event.oldState() & Qt.WindowState.WindowMinimized:
                # Restoring from minimized (e.g. Dock click after broken hide):
                # let _show_window handle proper activation.
                QTimer.singleShot(0, self._show_window)
        super().changeEvent(event)

    def _intercept_minimize(self):
        # Don't touch window state — just hide. showNormal() restores correctly on un-hide.
        self._hide_to_tray()

    def closeEvent(self, event):
        event.ignore()
        self._hide_to_tray()

    def _hide_to_tray(self):
        self.hide()
        if not self._db.get_setting("tray_hint_shown"):
            self._tray.showMessage(
                "Music Librarian",
                "Running in the menu bar. Use the icon to show or quit.",
                QSystemTrayIcon.MessageIcon.Information,
                3000,
            )
            self._db.set_setting("tray_hint_shown", "1")

    def _show_window(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()
        if platform.system() == "Darwin":
            try:
                from AppKit import NSApp
                NSApp.activateIgnoringOtherApps_(True)
            except Exception:
                pass

    def quit(self):
        if self._data_dir:
            self._player_engine.save_queue_state(self._data_dir / "queue_state.json")
        self._check_drives()
        self._stop_watcher()
        self._drive_monitor.stop()
        QApplication.quit()
