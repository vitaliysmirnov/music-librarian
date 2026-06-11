"""
Native drive mount/unmount notifications.

macOS  — NSWorkspace notifications via PyObjC (instant, no polling).
Windows — WM_DEVICECHANGE via Qt nativeEvent (requires HWND, wired in MainWindow).
Other  — no-op (fallback to polling timer).
"""
import platform
import threading
from typing import Callable

_SYSTEM = platform.system()


# ── macOS ──────────────────────────────────────────────────────────────────

if _SYSTEM == "Darwin":
    from AppKit import NSWorkspace
    from Foundation import NSObject, NSRunLoop, NSDate
    import objc

    class _Observer(NSObject):
        # PyObjC maps selector colons to argument count, so we use plain init
        # and set Python-side callbacks as regular attributes after alloc/init.

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

        def didMount_(self, notification):
            path = notification.userInfo().get("NSDevicePath", "")
            if self._on_mount:
                self._on_mount(str(path))

        def didUnmount_(self, notification):
            path = notification.userInfo().get("NSDevicePath", "")
            if self._on_unmount:
                self._on_unmount(str(path))

        def dealloc(self):
            NSWorkspace.sharedWorkspace().notificationCenter().removeObserver_(self)
            objc.super(_Observer, self).dealloc()

    class DriveMonitor:
        """Starts a background NSRunLoop thread to receive mount/unmount events."""

        def __init__(self, on_mount: Callable[[str], None], on_unmount: Callable[[str], None]):
            self._on_mount = on_mount
            self._on_unmount = on_unmount
            self._observer = None
            self._thread = None
            self._stop = threading.Event()

        def start(self):
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

        def stop(self):
            self._stop.set()

        def _run(self):
            self._observer = _Observer.alloc().init()
            self._observer._on_mount = self._on_mount
            self._observer._on_unmount = self._on_unmount
            self._observer.subscribe()
            loop = NSRunLoop.currentRunLoop()
            while not self._stop.is_set():
                loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.5))


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
