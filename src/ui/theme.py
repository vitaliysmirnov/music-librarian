import platform

from PySide6.QtCore import QTimer
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
    # Re-creating the style makes Qt re-query the macOS platform palette.
    app.setStyle(app.style().objectName())
    # Re-polish every widget so stylesheet palette() tokens resolve fresh.
    for widget in app.allWidgets():
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()


def _apply_palette(theme: str, app: QApplication) -> None:
    app.setStyle("Fusion")
    if theme == "dark":
        app.setPalette(_dark_palette())
    else:
        app.setPalette(_light_palette())


def _dark_palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window,          QColor(45,  45,  45))
    p.setColor(QPalette.ColorRole.WindowText,      QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Base,            QColor(30,  30,  30))
    p.setColor(QPalette.ColorRole.AlternateBase,   QColor(60,  60,  60))
    p.setColor(QPalette.ColorRole.ToolTipBase,     QColor(30,  30,  30))
    p.setColor(QPalette.ColorRole.ToolTipText,     QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Text,            QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.Button,          QColor(60,  60,  60))
    p.setColor(QPalette.ColorRole.ButtonText,      QColor(220, 220, 220))
    p.setColor(QPalette.ColorRole.BrightText,      QColor(255, 80,  80))
    p.setColor(QPalette.ColorRole.Link,            QColor(42,  130, 218))
    p.setColor(QPalette.ColorRole.Highlight,       QColor(42,  130, 218))
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(0,   0,   0))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text,       QColor(120, 120, 120))
    p.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor(120, 120, 120))
    return p


def _light_palette() -> QPalette:
    from PySide6.QtWidgets import QStyleFactory
    style = QStyleFactory.create("Fusion")
    return style.standardPalette() if style else QPalette()
