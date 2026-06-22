"""
Native drive mount/unmount notifications.

macOS  — NSWorkspace notifications via PyObjC (instant, no polling).
Windows — WM_DEVICECHANGE via Qt nativeEvent (requires HWND, wired in MainWindow).
Other  — no-op (fallback to polling timer).
"""
import platform
from typing import Callable

_SYSTEM = platform.system()


# ── macOS ──────────────────────────────────────────────────────────────────

if _SYSTEM == "Darwin":
    from AppKit import NSWorkspace
    from Foundation import NSObject
    import objc

    class _Observer(NSObject):
        def init(self):
            self = objc.super(_Observer, self).init()
            if self is None:
                return None
            self._on_mount = None
            self._on_unmount = None
            return self

        def subscribe(self):
            nc = NSWorkspace.sharedWorkspace().notificationCenter()
            nc.addObserver_selector_name_object_(
                self, "didMount:", "NSWorkspaceDidMountNotification", None
            )
            nc.addObserver_selector_name_object_(
                self, "didUnmount:", "NSWorkspaceDidUnmountNotification", None
            )

        def unsubscribe(self):
            NSWorkspace.sharedWorkspace().notificationCenter().removeObserver_(self)

        def didMount_(self, notification):
            path = notification.userInfo().get("NSDevicePath", "")
            if self._on_mount:
                self._on_mount(str(path))

        def didUnmount_(self, notification):
            path = notification.userInfo().get("NSDevicePath", "")
            if self._on_unmount:
                self._on_unmount(str(path))

    class DriveMonitor:
        """Receives mount/unmount events via NSWorkspace on the main run loop.

        Qt's macOS event loop is a Cocoa run loop, so NSWorkspace notifications
        subscribed on the main thread are delivered automatically — no background
        thread or separate NSRunLoop needed.
        """

        def __init__(self, on_mount: Callable[[str], None], on_unmount: Callable[[str], None]):
            self._on_mount = on_mount
            self._on_unmount = on_unmount
            self._observer: _Observer | None = None

        def start(self):
            self._observer = _Observer.alloc().init()
            self._observer._on_mount = self._on_mount
            self._observer._on_unmount = self._on_unmount
            self._observer.subscribe()

        def stop(self):
            if self._observer is not None:
                self._observer.unsubscribe()
                self._observer = None


# ── Windows ────────────────────────────────────────────────────────────────

elif _SYSTEM == "Windows":
    class DriveMonitor:
        """
        On Windows, mount/unmount events come via WM_DEVICECHANGE.
        DriveMonitor is a no-op here — MainWindow.nativeEvent() handles it directly.
        """
        def __init__(self, on_mount, on_unmount):
            pass

        def start(self):
            pass

        def stop(self):
            pass


# ── Fallback ───────────────────────────────────────────────────────────────

else:
    class DriveMonitor:
        def __init__(self, on_mount, on_unmount):
            pass

        def start(self):
            pass

        def stop(self):
            pass
