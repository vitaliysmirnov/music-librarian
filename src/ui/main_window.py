import platform
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtGui import QIcon, QAction, QKeySequence, QPixmap, QPainter, QColor, QPen, QBrush
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
        self._setup_menu()
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
            "<b>Music Librarian</b><br><br>"
            "A personal music collection manager.<br>"
            "Scans folders, tracks releases, monitors changes.",
        )

    def _open_settings(self):
        self._show_window()
        self._tabs.setCurrentWidget(self._settings_tab)

    @staticmethod
    def _make_tray_icon() -> QIcon:
        """Draw a music-note icon using Qt — no external file needed."""
        pix = QPixmap(22, 22)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # Use black; macOS renders it correctly on both light and dark menu bars.
        color = QColor(0, 0, 0)
        p.setBrush(QBrush(color))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(2, 13, 9, 7)          # note head (filled oval)
        p.setPen(QPen(color, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawLine(11, 17, 11, 3)           # stem
        p.drawLine(11, 3, 19, 7)            # beam / flag
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
        self._check_drives()
        self._stop_watcher()
        self._drive_monitor.stop()
        QApplication.quit()
