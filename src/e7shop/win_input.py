"""Mouse input through the Win32 SendInput API, the same path real hardware input takes."""

import ctypes
from ctypes import wintypes

INPUT_MOUSE = 0
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_VIRTUALDESK = 0x4000
MOUSEEVENTF_ABSOLUTE = 0x8000

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _INPUTUNION(ctypes.Union):
    # MOUSEINPUT is the largest member of the real union, so the struct size matches.
    _fields_ = [("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class SendInputBackend:
    def __init__(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self._user32.SendInput.restype = wintypes.UINT

    def position(self) -> tuple[int, int]:
        point = wintypes.POINT()
        self._user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    def move(self, x: int, y: int) -> None:
        # Absolute coordinates are normalized to 0..65535 across the whole virtual desktop.
        metric = self._user32.GetSystemMetrics
        left, top = metric(SM_XVIRTUALSCREEN), metric(SM_YVIRTUALSCREEN)
        width, height = metric(SM_CXVIRTUALSCREEN), metric(SM_CYVIRTUALSCREEN)
        nx = round((x - left) * 65535 / max(width - 1, 1))
        ny = round((y - top) * 65535 / max(height - 1, 1))
        self._send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK, nx, ny)

    def click(self) -> None:
        self._send(MOUSEEVENTF_LEFTDOWN)
        self._send(MOUSEEVENTF_LEFTUP)

    def wheel(self, delta: int) -> None:
        self._send(MOUSEEVENTF_WHEEL, data=delta & 0xFFFFFFFF)

    def _send(self, flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> None:
        event = INPUT(type=INPUT_MOUSE, u=_INPUTUNION(mi=MOUSEINPUT(dx, dy, data, flags, 0, 0)))
        if self._user32.SendInput(1, ctypes.byref(event), ctypes.sizeof(INPUT)) != 1:
            raise ctypes.WinError(ctypes.get_last_error())
