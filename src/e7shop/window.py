"""Finding, focusing and measuring the game window (Windows only)."""

import ctypes
import logging
import time

from .errors import StopKind, StopRun
from .geometry import Rect

log = logging.getLogger(__name__)


def enable_dpi_awareness() -> None:
    """Use physical pixels everywhere, so display scaling doesn't skew coordinates."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor DPI aware
    except (AttributeError, OSError):
        ctypes.windll.user32.SetProcessDPIAware()


def find_pids(process_name: str) -> set[int]:
    import psutil

    name = process_name.lower()
    return {
        p.info["pid"]
        for p in psutil.process_iter(["pid", "name"])
        if (p.info["name"] or "").lower() == name
    }


class GameWindow:
    def __init__(self, hwnd: int):
        self.hwnd = hwnd

    @classmethod
    def find(cls, process_name: str) -> "GameWindow":
        import win32gui
        import win32process

        pids = find_pids(process_name)
        if not pids:
            raise StopRun(StopKind.ERROR, f"{process_name} isn't running. Start Epic Seven and try again.")

        candidates: list[tuple[int, int]] = []

        def collect(hwnd: int, _: object) -> bool:
            if win32gui.IsWindowVisible(hwnd):
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid in pids:
                    left, top, right, bottom = win32gui.GetClientRect(hwnd)
                    candidates.append(((right - left) * (bottom - top), hwnd))
            return True

        win32gui.EnumWindows(collect, None)
        if not candidates:
            raise StopRun(StopKind.ERROR, f"{process_name} is running but has no visible window")
        # The game's main window is the largest one the process owns.
        _, hwnd = max(candidates)
        log.debug("Game window: hwnd=%s", hwnd)
        return cls(hwnd)

    def focus(self, attempts: int = 5) -> None:
        import win32api
        import win32con
        import win32gui

        if win32gui.IsIconic(self.hwnd):
            win32gui.ShowWindow(self.hwnd, win32con.SW_RESTORE)
        for _ in range(attempts):
            try:
                win32gui.SetForegroundWindow(self.hwnd)
            except Exception:
                # Windows only lets the foreground process change focus. A synthetic Alt press
                # unlocks it for us.
                win32api.keybd_event(win32con.VK_MENU, 0, 0, 0)
                win32api.keybd_event(win32con.VK_MENU, 0, win32con.KEYEVENTF_KEYUP, 0)
            time.sleep(0.1)
            if self.is_foreground():
                return
        raise StopRun(StopKind.ERROR, "Couldn't bring the game window to the front")

    def is_foreground(self) -> bool:
        import win32gui

        return win32gui.GetForegroundWindow() == self.hwnd

    def client_size(self) -> tuple[int, int]:
        import win32gui

        left, top, right, bottom = win32gui.GetClientRect(self.hwnd)
        return right - left, bottom - top

    def client_origin(self) -> tuple[int, int]:
        import win32gui

        return win32gui.ClientToScreen(self.hwnd, (0, 0))

    def to_screen(self, rect: Rect) -> Rect:
        """Window-relative rectangle → screen coordinates. Re-reads the origin so a moved window still works."""
        x, y = self.client_origin()
        return rect.offset(x, y)
