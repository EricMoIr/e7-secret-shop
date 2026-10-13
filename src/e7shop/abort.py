"""Stop signal shared by the hotkey, the GUI (later) and every step of a run."""

import threading
from collections.abc import Callable

from .errors import StopKind, StopRun


class AbortSignal:
    def __init__(self) -> None:
        self._event = threading.Event()

    def trigger(self) -> None:
        self._event.set()

    def reset(self) -> None:
        self._event.clear()

    @property
    def is_set(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise StopRun(StopKind.USER, "Stopped by the user")

    def sleep(self, seconds: float) -> None:
        """Sleep, but wake up and stop as soon as the signal is triggered."""
        self._event.wait(seconds)
        self.check()

    def register_hotkey(self, hotkey: str) -> Callable[[], None]:
        """Trigger on a global hotkey. Returns a function that unregisters it."""
        import keyboard

        handle = keyboard.add_hotkey(hotkey, self.trigger)
        return lambda: keyboard.remove_hotkey(handle)
