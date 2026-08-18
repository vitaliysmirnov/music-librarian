import platform

from PySide6.QtCore import Qt, QEvent, QObject, QTimer
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QApplication, QWidget


# ── Windows dark titlebar ─────────────────────────────────────────────────────

_titlebar_filter: "QObject | None" = None

# Slightly lighter than the Window background (45,45,45) to match the macOS
# dark title bar shade.  COLORREF format is 0x00BBGGRR; for neutral gray R=G=B.
_DARK_CAPTION_COLOR = 0x3C3C3C
_DWMWA_COLOR_DEFAULT = 0xFFFFFFFF  # sentinel that resets caption colour to system default

try:
    import sys as _sys
    _IS_WIN11 = _sys.platform == "win32" and _sys.getwindowsversion().build >= 22000
except Exception:
    _IS_WIN11 = False


def _dwm_set_titlebar_dark(hwnd: int, dark: bool) -> None:
    """Apply dark or light title bar via DWM attributes."""
    import ctypes
    try:
        # DWMWA_USE_IMMERSIVE_DARK_MODE — dark min/max/close buttons and border.
        mode = ctypes.c_int(1 if dark else 0)
        r = ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(mode), ctypes.sizeof(mode))
        if r != 0:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(mode), ctypes.sizeof(mode))

        if _IS_WIN11:
            # DWMWA_CAPTION_COLOR (attr 35, Win 11+): softer gray matching the app palette.
            color = ctypes.c_uint(_DARK_CAPTION_COLOR if dark else _DWMWA_COLOR_DEFAULT)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(color), ctypes.sizeof(color))

    except Exception:
        pass


class _TitlebarFilter(QObject):
    """Application-level event filter that keeps all top-level HWNDs in sync."""

    def __init__(self, dark: bool, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._dark = dark

    def set_dark(self, dark: bool) -> None:
        self._dark = dark

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if isinstance(obj, QWidget) and obj.isWindow():
            t = event.type()
            if t in (QEvent.Type.WinIdChange, QEvent.Type.Show):
                hwnd = obj.internalWinId()
                if hwnd:
                    _dwm_set_titlebar_dark(int(hwnd), self._dark)
        return False


def _apply_windows_titlebar_theme(dark: bool) -> None:
    if platform.system() != "Windows":
        return
    global _titlebar_filter
    app = QApplication.instance()
    if app is None:
        return
    if _titlebar_filter is None:
        _titlebar_filter = _TitlebarFilter(dark, app)
        app.installEventFilter(_titlebar_filter)
    else:
        _titlebar_filter.set_dark(dark)  # type: ignore[attr-defined]
    def _apply_all() -> None:
        a = QApplication.instance()
        if a is None:
            return
        for w in a.topLevelWidgets():
            if w.isWindow() and not w.isMinimized():
                hwnd = w.internalWinId()
                if hwnd:
                    _dwm_set_titlebar_dark(int(hwnd), dark)
                # DWM only re-reads DWMWA_USE_IMMERSIVE_DARK_MODE when it
                # allocates a new composition surface, which happens on resize.
                # A 1 px nudge forces that without any visible flicker: both
                # SetWindowPos calls complete before the next vsync (≤16 ms).
                _w = w.width()
                w.resize(_w + 1, w.height())
                w.resize(_w, w.height())
    # Defer to the next event-loop tick so Qt's own style-change messages from
    # _force_qt_refresh are processed before we touch the non-client area.
    QTimer.singleShot(0, _apply_all)


# ── Public API ────────────────────────────────────────────────────────────────

def apply_theme(theme: str) -> None:
    """Apply 'light', 'dark', or 'system' theme to the running QApplication."""
    app = QApplication.instance()
    if app is None:
        return

    if platform.system() == "Darwin":
        _apply_macos(theme)
    else:
        _apply_palette(theme, app)


def _apply_macos(theme: str) -> None:
    try:
        from AppKit import NSApp, NSAppearance
        if theme == "dark":
            appearance = NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua")
        elif theme == "light":
            appearance = NSAppearance.appearanceNamed_("NSAppearanceNameAqua")
        else:
            appearance = None
        NSApp.setAppearance_(appearance)
    except Exception:
        pass
    # NSAppearance propagates asynchronously; give the run loop one tick
    # to commit, then force Qt to re-read the platform palette and
    # re-resolve all palette() references in stylesheets.
    QTimer.singleShot(0, _force_qt_refresh)


def _force_qt_refresh() -> None:
    app = QApplication.instance()
    if app is None:
        return
    # Re-creating the style makes Qt re-query the platform palette.
    app.setStyle(app.style().objectName())
    # Re-polish every widget so stylesheet palette() tokens resolve fresh.
    for widget in app.allWidgets():
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()


def _system_is_dark(app: QApplication) -> bool:
    """Return True when the OS is in dark mode (Qt 6.5+ colorScheme API)."""
    try:
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:
        return False


_TOOLTIP_STYLE_DARK = (
    "QToolTip { background-color: #323232; color: #dcdcdc;"
    " border: 1px solid #505050; padding: 2px; }"
)
_TOOLTIP_STYLE_LIGHT = (
    "QToolTip { background-color: #ffffdc; color: #111111;"
    " border: 1px solid #b4b4b4; padding: 2px; }"
)


def _apply_palette(theme: str, app: QApplication) -> None:
    app.setStyle("Fusion")
    is_dark: bool
    if theme == "dark":
        app.setPalette(_dark_palette())
        is_dark = True
    elif theme == "light":
        app.setPalette(_light_palette())
        is_dark = False
    else:  # system — follow OS dark/light preference
        is_dark = _system_is_dark(app)
        app.setPalette(_dark_palette() if is_dark else _light_palette())
    # Call _force_qt_refresh() first: it calls app.setStyle() internally which can
    # reset the application stylesheet.  Setting the QToolTip stylesheet afterwards
    # ensures it survives the style re-creation.
    _force_qt_refresh()
    app.setStyleSheet(_TOOLTIP_STYLE_DARK if is_dark else _TOOLTIP_STYLE_LIGHT)
    _apply_windows_titlebar_theme(is_dark)


def _dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,           QColor(45,  45,  45))
    p.setColor(QPalette.ColorRole.WindowText,       QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Base,             QColor(30,  30,  30))
    p.setColor(QPalette.ColorRole.AlternateBase,    QColor(60,  60,  60))
    p.setColor(QPalette.ColorRole.ToolTipBase,      QColor(30,  30,  30))
    p.setColor(QPalette.ColorRole.ToolTipText,      QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Text,             QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Button,           QColor(60,  60,  60))
    p.setColor(QPalette.ColorRole.ButtonText,       QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.BrightText,       QColor(255, 80,  80))
    p.setColor(QPalette.ColorRole.Link,             QColor(42,  130, 218))
    p.setColor(QPalette.ColorRole.Highlight,        QColor(42,  130, 218))
    p.setColor(QPalette.ColorRole.HighlightedText,  QColor(0,   0,   0))
    # Roles used by Fusion-style borders, groove lines, and shadows
    p.setColor(QPalette.ColorRole.Mid,              QColor(80,  80,  80))
    p.setColor(QPalette.ColorRole.Midlight,         QColor(70,  70,  70))
    p.setColor(QPalette.ColorRole.Dark,             QColor(35,  35,  35))
    p.setColor(QPalette.ColorRole.Shadow,           QColor(20,  20,  20))
    # Used in stylesheet palette(placeholderText) tokens
    p.setColor(QPalette.ColorRole.PlaceholderText,  QColor(140, 140, 140))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,            QColor(120, 120, 120))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText,      QColor(120, 120, 120))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText,      QColor(120, 120, 120))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.PlaceholderText, QColor(90,  90,  90))
    return p


def _light_palette() -> QPalette:
    from PySide6.QtWidgets import QStyleFactory
    style = QStyleFactory.create("Fusion")
    p = style.standardPalette() if style else QPalette()
    # Fusion's standardPalette() on Windows derives ToolTipBase from the OS system
    # tooltip colour (COLOR_INFOBK), which can be black when the OS is in dark mode.
    # This makes tooltip text invisible on a light-theme app that the user has opened
    # while the OS is in dark mode.  Set both roles explicitly so the tooltip always
    # gets a readable light-yellow background with black text.
    p.setColor(QPalette.ColorRole.ToolTipBase, QColor(255, 255, 220))
    p.setColor(QPalette.ColorRole.ToolTipText, QColor(0, 0, 0))
    return p
