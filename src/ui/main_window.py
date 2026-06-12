from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QIcon, QAction
from PySide6.QtWidgets import (
    QMainWindow, QTabWidget, QStatusBar,
    QLabel, QPushButton,
    QSystemTrayIcon, QMenu, QApplication, QMessageBox,
)

from src.database.db import Database
from src.scanner.scanner import scan_all, scan_source
from src.ui.releases_tab import ReleasesTab
from src.ui.settings_tab import SettingsTab, MODE_AUTO
from src.ui.sources_tab import SourcesTab
from src.utils.drive_monitor import DriveMonitor
from src.utils.logger import QtLogHandler, get_logger
from src.watcher.watcher import LibraryWatcher

log = get_logger()

_DRIVE_POLL_INTERVAL_MS = 20_000


class MainWindow(QMainWindow):
    def __init__(self, db: Database, qt_log_handler: QtLogHandler | None = None):
        super().__init__()
        self._db = db
        self._qt_log_handler = qt_log_handler
        self._watcher: LibraryWatcher | None = None

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
        self._setup_tray()
        self._apply_settings()
        self._check_drives()
        self._drive_timer.start()
        self._drive_monitor.start()

    # ── UI ────────────────────────────────────────────────────────────────

    def _setup_ui(self):
        self.setWindowTitle("Music Librarian")
        self.resize(1100, 680)

        self._tabs = QTabWidget()
        self.setCentralWidget(self._tabs)

        self._releases_tab = ReleasesTab(self._db)
        self._sources_tab = SourcesTab(self._db)
        self._settings_tab = SettingsTab(self._db, self._qt_log_handler)

        self._tabs.addTab(self._releases_tab, "Releases")
        self._tabs.addTab(self._sources_tab, "Sources")
        self._tabs.addTab(self._settings_tab, "Settings")

        self._sources_tab.sources_changed.connect(self._on_sources_changed)
        self._settings_tab.settings_changed.connect(self._apply_settings)
        self._settings_tab.mask_changed.connect(self._on_mask_changed)

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

    def _setup_tray(self):
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(QIcon.fromTheme("audio-x-generic", self.windowIcon()))
        self._tray.setToolTip("Music Librarian")

        menu = QMenu()
        show_action = QAction("Show", self)
        show_action.triggered.connect(self._show_window)
        scan_action = QAction("Scan Now", self)
        scan_action.triggered.connect(self._manual_scan)
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit)

        menu.addAction(show_action)
        menu.addAction(scan_action)
        menu.addSeparator()
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

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

    def _start_watcher(self):
        self._watcher = LibraryWatcher(self._db, self._on_fs_change)
        self._watcher.start()

    def _stop_watcher(self):
        if self._watcher:
            self._watcher.stop()
            self._watcher = None

    # ── Drive detection ───────────────────────────────────────────────────

    def _on_drive_mounted(self, mount_path: str):
        log.info("OS: drive mounted at %s", mount_path)
        triggered = False
        for source in self._db.get_sources():
            if source["path"].startswith(mount_path) and not source["is_available"]:
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
            if source["path"].startswith(mount_path) and source["is_available"]:
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
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
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

    def _show_window(self):
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self._show_window()

    def closeEvent(self, event):
        event.ignore()
        self.hide()
        self._tray.showMessage(
            "Music Librarian",
            "Music Librarian is running in the tray. Use the icon menu to quit.",
            QSystemTrayIcon.Information,
            3000,
        )

    def quit(self):
        self._check_drives()
        self._stop_watcher()
        self._drive_monitor.stop()
        QApplication.quit()
