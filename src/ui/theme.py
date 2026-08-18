import platform

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QApplication


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
    # Explicit QToolTip style prevents the black-rectangle rendering artefact
    # that appears on Windows when widgets have background:transparent.
    app.setStyleSheet(_TOOLTIP_STYLE_DARK if is_dark else _TOOLTIP_STYLE_LIGHT)
    _force_qt_refresh()


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
    return style.standardPalette() if style else QPalette()
